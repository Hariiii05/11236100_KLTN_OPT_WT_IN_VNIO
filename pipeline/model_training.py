"""Model search, quantile fitting, and per-group calibration for the unified pipeline.

Reuses the already-tested building blocks from ``train_and_evaluate_model``
(metrics, walk-forward folds) and ``diagnose_and_calibrate`` (the
split-conformal offset function) instead of redefining them, and adds only
what the unified pipeline needs on top: a feature-set-aware candidate grid
and per-group (instead of single global) conformal calibration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder

from pipeline.diagnose_and_calibrate import conformal_offset
from pipeline.feature_engineering import feature_columns
from pipeline.optimize_on_development import CURRENT_PARAMS, DEEPER_PARAMS
from pipeline.pipeline_config import PipelineConfig
from pipeline.train_and_evaluate_model import (
    METRIC_COLUMNS,
    TARGET_COLUMN,
    TIME_COLUMN,
    calculate_metrics,
)


@dataclass(frozen=True)
class ModelCandidate:
    name: str
    feature_set: str
    params: dict[str, Any] | None  # None marks the median-baseline reference


CANDIDATES: list[ModelCandidate] = [
    ModelCandidate('median_baseline', 'official', None),
    ModelCandidate('official_current', 'official', CURRENT_PARAMS),
    ModelCandidate('official_deeper', 'official', DEEPER_PARAMS),
    ModelCandidate('queue_dynamics_current', 'queue_dynamics', CURRENT_PARAMS),
    ModelCandidate('queue_dynamics_deeper', 'queue_dynamics', DEEPER_PARAMS),
    ModelCandidate('queue_dynamics_patient_type_current', 'queue_dynamics_patient_type', CURRENT_PARAMS),
    ModelCandidate('queue_dynamics_patient_type_deeper', 'queue_dynamics_patient_type', DEEPER_PARAMS),
    ModelCandidate('queue_dynamics_lunch_current', 'queue_dynamics_lunch', CURRENT_PARAMS),
    ModelCandidate('queue_dynamics_lunch_deeper', 'queue_dynamics_lunch', DEEPER_PARAMS),
]


def build_pipeline(
    categorical: list[str], numeric: list[str], params: dict[str, Any], random_state: int
) -> Pipeline:
    categorical_encoder = OrdinalEncoder(
        handle_unknown='use_encoded_value', unknown_value=np.nan, encoded_missing_value=np.nan
    )
    numeric_pipeline = Pipeline([('imputer', SimpleImputer(strategy='median'))])
    preprocessing = ColumnTransformer(
        [('categorical', categorical_encoder, categorical), ('numeric', numeric_pipeline, numeric)],
        remainder='drop',
        verbose_feature_names_out=False,
    )
    categorical_mask = [True] * len(categorical) + [False] * len(numeric)
    estimator = HistGradientBoostingRegressor(
        **params, categorical_features=categorical_mask, early_stopping=False, random_state=random_state
    )
    return Pipeline([('preprocessing', preprocessing), ('model', estimator)])


def fit_candidate(candidate: ModelCandidate, train: pd.DataFrame, random_state: int) -> dict[str, Any]:
    if candidate.params is None:
        return {'kind': 'median', 'value': float(train[TARGET_COLUMN].median())}
    categorical, numeric = feature_columns(candidate.feature_set)
    pipeline = build_pipeline(categorical, numeric, candidate.params, random_state)
    pipeline.fit(train[categorical + numeric], train[TARGET_COLUMN].to_numpy(dtype=float))
    return {'kind': 'pipeline', 'pipeline': pipeline, 'categorical': categorical, 'numeric': numeric}


def predict_candidate(fitted: dict[str, Any], frame: pd.DataFrame) -> np.ndarray:
    if fitted['kind'] == 'median':
        prediction = np.full(len(frame), fitted['value'], dtype=float)
    else:
        columns = fitted['categorical'] + fitted['numeric']
        prediction = fitted['pipeline'].predict(frame[columns])
    return np.clip(np.asarray(prediction, dtype=float), 0, None)


def run_walk_forward_search(
    fold_specs: list[dict[str, Any]], candidates: list[ModelCandidate], random_state: int
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        for fold in fold_specs:
            fitted = fit_candidate(candidate, fold['train'], random_state)
            predicted = predict_candidate(fitted, fold['validation'])
            metrics = calculate_metrics(fold['validation'][TARGET_COLUMN].to_numpy(dtype=float), predicted)
            rows.append({
                'candidate': candidate.name,
                'feature_set': candidate.feature_set,
                'window_type': fold['window_type'],
                'fold': fold['fold'],
                'train_rows': len(fold['train']),
                'validation_rows': len(fold['validation']),
                'validation_start': fold['validation_start'],
                **metrics,
            })
            print(
                f"{candidate.name} window={fold['window_type']} fold={fold['fold']} "
                f"MAE={metrics['mae']:.3f} within20={metrics['within_20m_rate']:.3%}",
                flush=True,
            )
    return pd.DataFrame(rows)


def summarize_candidates(fold_metrics: pd.DataFrame) -> pd.DataFrame:
    """Rank candidates by minimum(mean validation MAE + 0.25*std), the same
    selection rule documented as the project's primary metric in
    docs/final_model_evaluation.md."""
    rows: list[dict[str, Any]] = []
    for (candidate, window_type), group in fold_metrics.groupby(['candidate', 'window_type'], sort=False):
        row: dict[str, Any] = {
            'candidate': candidate,
            'window_type': window_type,
            'feature_set': group['feature_set'].iloc[0],
            'folds': len(group),
        }
        for metric in METRIC_COLUMNS:
            values = group[metric].astype(float)
            row[f'{metric}_mean'] = float(values.mean())
            row[f'{metric}_std'] = float(values.std(ddof=0))
        row['selection_score'] = row['mae_mean'] + 0.25 * row['mae_std']
        rows.append(row)
    summary = pd.DataFrame(rows).sort_values(['selection_score', 'mae_mean']).reset_index(drop=True)
    summary['selection_rank'] = np.arange(1, len(summary) + 1)
    return summary


def fit_quantile_models(
    categorical: list[str],
    numeric: list[str],
    train: pd.DataFrame,
    base_params: dict[str, Any],
    random_state: int,
    levels: tuple[float, ...] = (0.5, 0.8),
) -> dict[float, Pipeline]:
    models: dict[float, Pipeline] = {}
    for level in levels:
        params = base_params if level == 0.5 else {**base_params, 'loss': 'quantile', 'quantile': level}
        models[level] = build_pipeline(categorical, numeric, params, random_state).fit(
            train[categorical + numeric], train[TARGET_COLUMN].to_numpy(dtype=float)
        )
    return models


def predict_quantile_models(
    models: dict[float, Pipeline], categorical: list[str], numeric: list[str], frame: pd.DataFrame
) -> dict[float, np.ndarray]:
    columns = categorical + numeric
    predictions = {level: np.clip(model.predict(frame[columns]), 0, None) for level, model in models.items()}
    if 0.5 in predictions and 0.8 in predictions:
        predictions[0.8] = np.maximum(predictions[0.8], predictions[0.5])
    return predictions


def fit_group_conformal_offsets(
    calibration_frame: pd.DataFrame,
    predictions: dict[float, np.ndarray],
    group_column: str,
    min_group_rows: int,
    levels: tuple[float, ...] = (0.5, 0.8),
) -> dict[str, dict[float, float]]:
    """Split-conformal offsets per group, falling back to a global offset.

    Addresses the uneven P80 coverage across departments/rooms noted in the
    prior diagnosis (e.g. one room's P80 coverage was ~10 points below the
    overall average) by calibrating each group separately whenever it has
    enough calibration rows, instead of applying one offset everywhere.
    """
    actual = calibration_frame[TARGET_COLUMN].to_numpy(dtype=float)
    groups = calibration_frame[group_column].astype('string').fillna('__MISSING__').to_numpy()
    offsets: dict[str, dict[float, float]] = {
        '__global__': {level: conformal_offset(actual - predictions[level], level) for level in levels}
    }
    for group_value in np.unique(groups):
        group_mask = groups == group_value
        if group_mask.sum() < min_group_rows:
            continue
        offsets[str(group_value)] = {
            level: conformal_offset(actual[group_mask] - predictions[level][group_mask], level)
            for level in levels
        }
    return offsets


def apply_group_conformal_offsets(
    frame: pd.DataFrame,
    predictions: dict[float, np.ndarray],
    group_column: str,
    offsets: dict[str, dict[float, float]],
    levels: tuple[float, ...] = (0.5, 0.8),
) -> dict[float, np.ndarray]:
    groups = frame[group_column].astype('string').fillna('__MISSING__').to_numpy()
    calibrated: dict[float, np.ndarray] = {}
    for level in levels:
        group_offset = np.array([offsets.get(str(g), offsets['__global__'])[level] for g in groups])
        calibrated[level] = np.clip(predictions[level] + group_offset, 0, None)
    if 0.5 in levels and 0.8 in levels:
        calibrated[0.8] = np.maximum(calibrated[0.8], calibrated[0.5])
    return calibrated


def pooled_out_of_fold_quantile_predictions(
    candidate: ModelCandidate,
    fold_specs: list[dict[str, Any]],
    random_state: int,
    extra_columns: tuple[str, ...] = (),
) -> pd.DataFrame:
    """Fit P50/P80 for one candidate per fold and pool out-of-fold results.

    Used for segment/tail diagnosis: predictions are always made on a fold's
    own validation split (never on data the fold trained on), so the pooled
    frame is a fair "what would validation-time scoring have looked like"
    view without needing a second holdout.
    """
    if candidate.params is None:
        raise ValueError('pooled_out_of_fold_quantile_predictions needs a fitted-model candidate.')
    categorical, numeric = feature_columns(candidate.feature_set)
    keep_columns = list(dict.fromkeys(
        ['nb_dich_vu_id', TIME_COLUMN, TARGET_COLUMN, 'ma_phong_thuc_hien', 'ma_khoa_thuc_hien',
         'queue_entry_status', 'patients_ahead_same_room', 'patient_payment_type',
         'registered_before_lunch', 'crosses_lunch']
        + list(extra_columns)
    ))
    frames = []
    for fold in fold_specs:
        models = fit_quantile_models(categorical, numeric, fold['train'], candidate.params, random_state)
        predictions = predict_quantile_models(models, categorical, numeric, fold['validation'])
        available_columns = [c for c in keep_columns if c in fold['validation'].columns]
        frame = fold['validation'][available_columns].copy()
        frame['y'] = frame[TARGET_COLUMN].to_numpy(dtype=float)
        frame['p50'] = predictions[0.5]
        frame['p80'] = predictions[0.8]
        frame['error'] = frame['p50'] - frame['y']
        frame['abs_error'] = frame['error'].abs()
        frame['fold'] = fold['fold']
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def final_training_window(
    pre_holdout: pd.DataFrame, holdout_start: pd.Timestamp, config: PipelineConfig
) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """Split the pre-holdout tail into a model-fit window and a calibration
    window. The calibration window is never used to fit the point-forecast
    model, and the fit window is purged the same way every training fold is
    (a row only counts if its label would already be known at
    ``calibration_start``). The calibration rows keep their true observed
    target regardless of label-availability timing, the same way validation
    rows already do in ``make_folds`` -- this is a backtest, not a live
    retrain, so the historical outcome is simply known.
    """
    train_start = holdout_start - pd.Timedelta(config.sliding_train_days, unit='D')
    calibration_start = holdout_start - pd.Timedelta(config.calibration_days, unit='D')
    candidate_rows = pre_holdout.loc[
        pre_holdout[TIME_COLUMN].ge(train_start) & pre_holdout[TIME_COLUMN].lt(calibration_start)
    ]
    fit_rows = candidate_rows.loc[candidate_rows['label_available_time'].lt(calibration_start)].copy()
    calibration_rows = pre_holdout.loc[
        pre_holdout[TIME_COLUMN].ge(calibration_start) & pre_holdout[TIME_COLUMN].lt(holdout_start)
    ].copy()
    if fit_rows.empty or calibration_rows.empty:
        raise ValueError('Final fit or calibration window is empty; adjust sliding/calibration days.')
    if fit_rows[TIME_COLUMN].max() >= calibration_rows[TIME_COLUMN].min():
        raise AssertionError('Final fit/calibration time ordering violation.')
    if fit_rows['label_available_time'].max() >= calibration_start:
        raise AssertionError('A final training label was not available at calibration start.')
    purged = len(candidate_rows) - len(fit_rows)
    return fit_rows, calibration_rows, purged
