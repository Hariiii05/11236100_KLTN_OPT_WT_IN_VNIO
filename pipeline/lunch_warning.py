"""'Cho qua gio nghi trua' (past-lunch) warning: classifier, threshold, output.

Patients who queue before the configured lunch start sometimes are not seen
until the clinic reopens in the afternoon (see feature_engineering.py and
weakness W5 in docs/model_diagnosis_and_calibration.md). This module trains a
dedicated probability-of-crossing-lunch classifier (rather than relying on
the wait-time regressor's point forecast, which is optimized for the typical
case and systematically under-predicts this specific tail), selects a
decision threshold on validation only, and assembles the final per-patient
recommendation.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import f1_score, precision_recall_curve, precision_score, recall_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder

from pipeline.feature_engineering import feature_columns, parse_time_of_day
from pipeline.pipeline_config import PipelineConfig
from pipeline.train_and_evaluate_model import TIME_COLUMN

LUNCH_FEATURE_SET = 'queue_dynamics_lunch'
CLASSIFIER_PARAMS = {
    'learning_rate': 0.08,
    'max_iter': 150,
    'max_leaf_nodes': 31,
    'min_samples_leaf': 30,
    'l2_regularization': 1.0,
}

RECOMMEND_AFTERNOON = 'Chờ quá giờ nghỉ trưa / Đề xuất quay lại vào buổi chiều'
RECOMMEND_KEEP_WAITING = 'Chờ tiếp'


def build_classifier_pipeline(categorical: list[str], numeric: list[str], random_state: int) -> Pipeline:
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
    estimator = HistGradientBoostingClassifier(
        **CLASSIFIER_PARAMS, categorical_features=categorical_mask, random_state=random_state
    )
    return Pipeline([('preprocessing', preprocessing), ('model', estimator)])


def morning_subset(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.loc[frame['registered_before_lunch']].copy()


def fit_lunch_classifier(train: pd.DataFrame, random_state: int) -> dict[str, Any]:
    categorical, numeric = feature_columns(LUNCH_FEATURE_SET)
    train_morning = morning_subset(train)
    pipeline = build_classifier_pipeline(categorical, numeric, random_state)
    pipeline.fit(train_morning[categorical + numeric], train_morning['crosses_lunch'].astype(int))
    return {'pipeline': pipeline, 'categorical': categorical, 'numeric': numeric}


def predict_lunch_probability(fitted: dict[str, Any], frame: pd.DataFrame) -> np.ndarray:
    columns = fitted['categorical'] + fitted['numeric']
    return fitted['pipeline'].predict_proba(frame[columns])[:, 1]


def out_of_fold_lunch_predictions(fold_specs: list[dict[str, Any]], random_state: int) -> pd.DataFrame:
    """Fit the classifier on each fold's train split and predict on its own
    validation split, so the pooled result is out-of-fold and safe to use for
    threshold selection (never touches the holdout)."""
    frames = []
    for fold in fold_specs:
        fitted = fit_lunch_classifier(fold['train'], random_state)
        validation_morning = morning_subset(fold['validation'])
        if validation_morning.empty:
            continue
        proba = predict_lunch_probability(fitted, validation_morning)
        frames.append(validation_morning.assign(lunch_probability=proba, fold=fold['fold']))
    return pd.concat(frames, ignore_index=True)


def select_threshold(y_true: np.ndarray, proba: np.ndarray, fbeta: float) -> dict[str, float]:
    """Pick the probability threshold maximizing F-beta on (out-of-fold)
    validation predictions. beta=1 balances false alarms and misses; beta>1
    favors recall (fewer missed lunch-overruns) at the cost of more false
    alarms -- the right trade-off is a business call, so beta is exposed via
    PipelineConfig.lunch_threshold_fbeta rather than hard-coded."""
    if len(y_true) == 0:
        return {'threshold': 0.5, 'precision': 0.0, 'recall': 0.0, 'f_beta': 0.0}
    precision, recall, thresholds = precision_recall_curve(y_true, proba)
    precision, recall = precision[:-1], recall[:-1]
    beta2 = fbeta ** 2
    denominator = beta2 * precision + recall
    f_beta = np.divide(
        (1 + beta2) * precision * recall, denominator, out=np.zeros_like(denominator), where=denominator > 0
    )
    if len(f_beta) == 0 or not np.any(np.isfinite(f_beta)):
        return {'threshold': 0.5, 'precision': 0.0, 'recall': 0.0, 'f_beta': 0.0}
    best = int(np.nanargmax(f_beta))
    return {
        'threshold': float(thresholds[best]),
        'precision': float(precision[best]),
        'recall': float(recall[best]),
        'f_beta': float(f_beta[best]),
    }


def classification_report_at_threshold(
    y_true: np.ndarray, proba: np.ndarray, threshold: float
) -> dict[str, float]:
    predicted = (proba >= threshold).astype(int)
    return {
        'threshold': float(threshold),
        'precision': float(precision_score(y_true, predicted, zero_division=0)),
        'recall': float(recall_score(y_true, predicted, zero_division=0)),
        'f1': float(f1_score(y_true, predicted, zero_division=0)),
        'positives_true': int(np.sum(y_true)),
        'positives_predicted': int(np.sum(predicted)),
        'rows': int(len(y_true)),
    }


def build_patient_output(
    frame: pd.DataFrame,
    predicted_p50: np.ndarray,
    predicted_p80: np.ndarray,
    lunch_probability: np.ndarray,
    threshold: float,
    config: PipelineConfig,
) -> pd.DataFrame:
    """Assemble the final per-patient record: predicted wait, interval,
    P(crosses lunch), and a recommendation with a suggested return time."""
    lunch_end = parse_time_of_day(config.lunch_end)
    queue_entry_time = frame[TIME_COLUMN].reset_index(drop=True)
    predicted_exam_time = queue_entry_time + pd.to_timedelta(predicted_p50, unit='m')
    lunch_end_clock = queue_entry_time.dt.normalize() + lunch_end
    suggested_time = predicted_exam_time.where(predicted_exam_time.ge(lunch_end_clock), lunch_end_clock)

    registered_before_lunch = frame['registered_before_lunch'].to_numpy(dtype=bool)
    warn = registered_before_lunch & (lunch_probability >= threshold)
    probability_out = np.where(registered_before_lunch, lunch_probability, np.nan)
    recommendation = np.where(warn, RECOMMEND_AFTERNOON, RECOMMEND_KEEP_WAITING)

    output = pd.DataFrame({
        'nb_dich_vu_id': frame['nb_dich_vu_id'].to_numpy(),
        'queue_entry_time': queue_entry_time.to_numpy(),
        'predicted_wait_minutes': np.round(predicted_p50, 1),
        'predicted_wait_p80_minutes': np.round(predicted_p80, 1),
        'probability_cross_lunch': probability_out,
        'recommendation': recommendation,
    })
    output['suggested_return_time'] = np.where(
        warn, suggested_time.dt.strftime('%Y-%m-%d %H:%M'), pd.NA
    )
    return output
