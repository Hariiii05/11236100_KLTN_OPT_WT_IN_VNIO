"""Development-only optimization for the HIS waiting-time model.

The script never evaluates rows on or after ``--test-start``. It compares
causal queue-load and completed-history features on the same six sliding
walk-forward folds used during model development. Final Test artifacts are
read neither for fitting nor for model selection.
"""

from __future__ import annotations

import argparse
import heapq
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault('LOKY_MAX_CPU_COUNT', '1')

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder

from pipeline.train_and_evaluate_model import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    TARGET_COLUMN,
    TIME_COLUMN,
    calculate_metrics,
    dataset_sha256,
    load_dataset,
    make_folds,
)


QUEUE_FEATURES = [
    'room_arrivals_last_15m',
    'room_arrivals_last_30m',
    'room_arrivals_last_60m',
    'department_arrivals_last_30m',
    'department_arrivals_last_60m',
    'patients_ahead_same_room',
    'patients_ahead_same_department',
]

DYNAMICS_FEATURES = [
    'room_starts_last_15m',
    'room_starts_last_30m',
    'room_starts_last_60m',
    'department_starts_last_30m',
    'department_starts_last_60m',
    'minutes_since_last_room_start',
    'minutes_since_last_department_start',
]

HISTORY_FEATURES = [
    'room_completed_42d_count',
    'room_wait_logmean_42d',
    'service_completed_42d_count',
    'service_wait_logmean_42d',
    'global_completed_7d_count',
    'global_wait_logmean_7d',
]

CURRENT_PARAMS = {
    'loss': 'absolute_error',
    'learning_rate': 0.08,
    'max_iter': 120,
    'max_leaf_nodes': 31,
    'min_samples_leaf': 40,
    'l2_regularization': 1.0,
}

DEEPER_PARAMS = {
    'loss': 'absolute_error',
    'learning_rate': 0.06,
    'max_iter': 180,
    'max_leaf_nodes': 63,
    'min_samples_leaf': 30,
    'l2_regularization': 1.0,
}

LOG_PARAMS = {
    'loss': 'squared_error',
    'learning_rate': 0.08,
    'max_iter': 120,
    'max_leaf_nodes': 31,
    'min_samples_leaf': 40,
    'l2_regularization': 1.0,
}

QUANTILE_PARAMS = {
    'loss': 'quantile',
    'quantile': 0.60,
    'learning_rate': 0.08,
    'max_iter': 150,
    'max_leaf_nodes': 31,
    'min_samples_leaf': 40,
    'l2_regularization': 1.0,
}


@dataclass(frozen=True)
class Candidate:
    name: str
    feature_set: str
    target_transform: str
    params: dict[str, Any]


CANDIDATES = [
    Candidate('baseline_absolute', 'baseline', 'none', CURRENT_PARAMS),
    Candidate('queue_absolute', 'queue', 'none', CURRENT_PARAMS),
    Candidate('queue_dynamics_absolute', 'queue_dynamics', 'none', CURRENT_PARAMS),
    Candidate('queue_dynamics_deeper', 'queue_dynamics', 'none', DEEPER_PARAMS),
    Candidate(
        'queue_dynamics_quantile55',
        'queue_dynamics',
        'none',
        {**QUANTILE_PARAMS, 'quantile': 0.55},
    ),
    Candidate('queue_history_absolute', 'queue_history', 'none', CURRENT_PARAMS),
    Candidate('queue_history_deeper', 'queue_history', 'none', DEEPER_PARAMS),
    Candidate('queue_history_log', 'queue_history', 'log1p', LOG_PARAMS),
    Candidate('queue_history_quantile60', 'queue_history', 'none', QUANTILE_PARAMS),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Optimize waiting-time accuracy using development folds only.'
    )
    parser.add_argument(
        '--input',
        type=Path,
        default=Path(__file__).resolve().parents[1] / 'data' / 'processed' / 'model_dataset.csv',
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path(__file__).resolve().parents[1] / 'results' / 'development_experiments',
    )
    parser.add_argument('--test-start', default='2026-07-01')
    parser.add_argument('--validation-days', type=int, default=7)
    parser.add_argument('--n-folds', type=int, default=6)
    parser.add_argument('--sliding-train-days', type=int, default=42)
    parser.add_argument('--random-state', type=int, default=42)
    return parser.parse_args()


def write_json(path: Path, value: dict[str, Any]) -> None:
    with path.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)


def grouped_prior_arrival_count(
    data: pd.DataFrame,
    group_column: str,
    window_minutes: int,
) -> np.ndarray:
    """Count earlier queue entries in a group; equal timestamps do not interact."""
    result = np.zeros(len(data), dtype=np.int32)
    time_ns = data[TIME_COLUMN].astype('int64').to_numpy()
    window_ns = int(pd.Timedelta(window_minutes, unit='m').value)
    groups = data[group_column].astype('string').fillna('__MISSING__')
    for positions in data.groupby(groups, sort=False).indices.values():
        positions = np.asarray(positions, dtype=np.int64)
        order = np.argsort(time_ns[positions], kind='stable')
        positions = positions[order]
        times = time_ns[positions]
        left = np.searchsorted(times, times - window_ns, side='left')
        right = np.searchsorted(times, times, side='left')
        result[positions] = right - left
    return result


def grouped_active_queue_count(data: pd.DataFrame, group_column: str) -> np.ndarray:
    """Reconstruct the number of earlier entries still waiting at each event time."""
    result = np.zeros(len(data), dtype=np.int32)
    queue_ns = data[TIME_COLUMN].astype('int64').to_numpy()
    start_ns = data['label_available_time'].astype('int64').to_numpy()
    groups = data[group_column].astype('string').fillna('__MISSING__')
    for positions in data.groupby(groups, sort=False).indices.values():
        positions = np.asarray(positions, dtype=np.int64)
        order = np.argsort(queue_ns[positions], kind='stable')
        positions = positions[order]
        index = 0
        active_starts: list[int] = []
        while index < len(positions):
            time_value = queue_ns[positions[index]]
            block_end = index + 1
            while block_end < len(positions) and queue_ns[positions[block_end]] == time_value:
                block_end += 1
            while active_starts and active_starts[0] <= time_value:
                heapq.heappop(active_starts)
            result[positions[index:block_end]] = len(active_starts)
            for position in positions[index:block_end]:
                if start_ns[position] > time_value:
                    heapq.heappush(active_starts, int(start_ns[position]))
            index = block_end
    return result


def grouped_prior_event_count(
    data: pd.DataFrame,
    group_column: str,
    event_time_column: str,
    window_minutes: int,
) -> np.ndarray:
    """Count group events in [query-window, query), using only observable events."""
    result = np.zeros(len(data), dtype=np.int32)
    query_ns = data[TIME_COLUMN].astype('int64').to_numpy()
    event_ns = data[event_time_column].astype('int64').to_numpy()
    window_ns = int(pd.Timedelta(window_minutes, unit='m').value)
    groups = data[group_column].astype('string').fillna('__MISSING__')
    for positions in data.groupby(groups, sort=False).indices.values():
        positions = np.asarray(positions, dtype=np.int64)
        sorted_events = np.sort(event_ns[positions], kind='stable')
        queries = query_ns[positions]
        left = np.searchsorted(sorted_events, queries - window_ns, side='left')
        right = np.searchsorted(sorted_events, queries, side='left')
        result[positions] = right - left
    return result


def grouped_minutes_since_last_event(
    data: pd.DataFrame,
    group_column: str,
    event_time_column: str,
) -> np.ndarray:
    result = np.full(len(data), np.nan, dtype=float)
    query_ns = data[TIME_COLUMN].astype('int64').to_numpy()
    event_ns = data[event_time_column].astype('int64').to_numpy()
    groups = data[group_column].astype('string').fillna('__MISSING__')
    for positions in data.groupby(groups, sort=False).indices.values():
        positions = np.asarray(positions, dtype=np.int64)
        sorted_events = np.sort(event_ns[positions], kind='stable')
        queries = query_ns[positions]
        previous = np.searchsorted(sorted_events, queries, side='left') - 1
        has_previous = previous >= 0
        result[positions[has_previous]] = (
            queries[has_previous] - sorted_events[previous[has_previous]]
        ) / float(pd.Timedelta(1, unit='m').value)
    return result


def causal_completed_history(
    data: pd.DataFrame,
    group_column: str | None,
    window_days: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Count and log-mean waits whose labels were observable before each queue entry."""
    counts = np.zeros(len(data), dtype=np.int32)
    means = np.full(len(data), np.nan, dtype=float)
    queue_ns = data[TIME_COLUMN].astype('int64').to_numpy()
    completed_ns = data['label_available_time'].astype('int64').to_numpy()
    values = np.log1p(data[TARGET_COLUMN].clip(lower=0).to_numpy(dtype=float))
    window_ns = int(pd.Timedelta(window_days, unit='D').value)

    if group_column is None:
        grouped_positions = [np.arange(len(data), dtype=np.int64)]
    else:
        groups = data[group_column].astype('string').fillna('__MISSING__')
        grouped_positions = [
            np.asarray(positions, dtype=np.int64)
            for positions in data.groupby(groups, sort=False).indices.values()
        ]

    for query_positions in grouped_positions:
        query_positions = query_positions[
            np.argsort(queue_ns[query_positions], kind='stable')
        ]
        completion_positions = query_positions[
            np.argsort(completed_ns[query_positions], kind='stable')
        ]
        completion_times = completed_ns[completion_positions]
        completion_values = values[completion_positions]
        left = 0
        right = 0
        running_sum = 0.0
        for position in query_positions:
            query_time = queue_ns[position]
            while right < len(completion_positions) and completion_times[right] < query_time:
                running_sum += float(completion_values[right])
                right += 1
            cutoff = query_time - window_ns
            while left < right and completion_times[left] < cutoff:
                running_sum -= float(completion_values[left])
                left += 1
            count = right - left
            counts[position] = count
            if count:
                means[position] = float(np.expm1(running_sum / count))
    return counts, means


def add_causal_features(development: pd.DataFrame) -> pd.DataFrame:
    data = development.copy().reset_index(drop=True)
    for minutes in (15, 30, 60):
        data[f'room_arrivals_last_{minutes}m'] = grouped_prior_arrival_count(
            data, 'ma_phong_thuc_hien', minutes
        )
    for minutes in (30, 60):
        data[f'department_arrivals_last_{minutes}m'] = grouped_prior_arrival_count(
            data, 'ma_khoa_thuc_hien', minutes
        )
    data['patients_ahead_same_room'] = grouped_active_queue_count(
        data, 'ma_phong_thuc_hien'
    )
    data['patients_ahead_same_department'] = grouped_active_queue_count(
        data, 'ma_khoa_thuc_hien'
    )
    for minutes in (15, 30, 60):
        data[f'room_starts_last_{minutes}m'] = grouped_prior_event_count(
            data, 'ma_phong_thuc_hien', 'label_available_time', minutes
        )
    for minutes in (30, 60):
        data[f'department_starts_last_{minutes}m'] = grouped_prior_event_count(
            data, 'ma_khoa_thuc_hien', 'label_available_time', minutes
        )
    data['minutes_since_last_room_start'] = grouped_minutes_since_last_event(
        data, 'ma_phong_thuc_hien', 'label_available_time'
    )
    data['minutes_since_last_department_start'] = grouped_minutes_since_last_event(
        data, 'ma_khoa_thuc_hien', 'label_available_time'
    )

    history_specs = [
        ('ma_phong_thuc_hien', 42, 'room'),
        ('ma_dich_vu', 42, 'service'),
        (None, 7, 'global'),
    ]
    for group_column, days, prefix in history_specs:
        counts, means = causal_completed_history(data, group_column, days)
        data[f'{prefix}_completed_{days}d_count'] = counts
        data[f'{prefix}_wait_logmean_{days}d'] = means
    return data


def feature_columns(feature_set: str) -> tuple[list[str], list[str]]:
    numeric = list(NUMERIC_FEATURES)
    if feature_set in {'queue', 'queue_dynamics', 'queue_history'}:
        numeric.extend(QUEUE_FEATURES)
    if feature_set == 'queue_dynamics':
        numeric.extend(DYNAMICS_FEATURES)
    if feature_set == 'queue_history':
        numeric.extend(HISTORY_FEATURES)
    return list(CATEGORICAL_FEATURES), numeric


def build_pipeline(candidate: Candidate, random_state: int) -> tuple[Pipeline, list[str]]:
    categorical_features, numeric_features = feature_columns(candidate.feature_set)
    categorical = OrdinalEncoder(
        handle_unknown='use_encoded_value',
        unknown_value=np.nan,
        encoded_missing_value=np.nan,
    )
    numeric = Pipeline([('imputer', SimpleImputer(strategy='median'))])
    preprocessing = ColumnTransformer(
        [
            ('categorical', categorical, categorical_features),
            ('numeric', numeric, numeric_features),
        ],
        remainder='drop',
        verbose_feature_names_out=False,
    )
    all_features = categorical_features + numeric_features
    categorical_mask = [True] * len(categorical_features) + [False] * len(numeric_features)
    estimator = HistGradientBoostingRegressor(
        **candidate.params,
        categorical_features=categorical_mask,
        early_stopping=False,
        random_state=random_state,
    )
    return Pipeline([('preprocessing', preprocessing), ('model', estimator)]), all_features


def predict_candidate(
    candidate: Candidate,
    train: pd.DataFrame,
    validation: pd.DataFrame,
    random_state: int,
) -> np.ndarray:
    pipeline, features = build_pipeline(candidate, random_state)
    target = train[TARGET_COLUMN].to_numpy(dtype=float)
    fit_target = np.log1p(target) if candidate.target_transform == 'log1p' else target
    pipeline.fit(train[features], fit_target)
    prediction = pipeline.predict(validation[features])
    if candidate.target_transform == 'log1p':
        prediction = np.expm1(prediction)
    return np.clip(np.asarray(prediction, dtype=float), 0, None)


def summarize_metrics(fold_metrics: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    metric_names = [
        'mae', 'rmse', 'median_ae', 'r2', 'p90_ae', 'bias',
        'within_10m_rate', 'within_20m_rate',
    ]
    for candidate, group in fold_metrics.groupby('candidate', sort=False):
        row: dict[str, Any] = {'candidate': candidate, 'folds': int(len(group))}
        for metric in metric_names:
            values = group[metric].astype(float)
            row[f'{metric}_mean'] = float(values.mean())
            row[f'{metric}_std'] = float(values.std(ddof=0))
            row[f'{metric}_min'] = float(values.min())
            row[f'{metric}_max'] = float(values.max())
        row['within_20m_stability_score'] = (
            row['within_20m_rate_mean'] - 0.25 * row['within_20m_rate_std']
        )
        rows.append(row)
    return pd.DataFrame(rows).sort_values(
        ['within_20m_stability_score', 'mae_mean'], ascending=[False, True]
    ).reset_index(drop=True)


def plot_comparison(summary: pd.DataFrame, output: Path) -> None:
    shown = summary.sort_values('within_20m_rate_mean')
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.2))
    axes[0].barh(shown['candidate'], shown['within_20m_rate_mean'] * 100, color='#2F6690')
    axes[0].set_xlabel('Tỷ lệ sai số ≤20 phút (%)')
    axes[0].set_title('Development walk-forward', loc='left', fontweight='bold')
    axes[0].grid(axis='x', alpha=.2)
    axes[1].barh(shown['candidate'], shown['mae_mean'], color='#F28E2B')
    axes[1].set_xlabel('MAE trung bình (phút)')
    axes[1].set_title('MAE theo cấu hình', loc='left', fontweight='bold')
    axes[1].grid(axis='x', alpha=.2)
    fig.tight_layout()
    fig.savefig(output, dpi=220, bbox_inches='tight', facecolor='white')
    plt.close(fig)


def main() -> None:
    args = parse_args()
    test_start = pd.Timestamp(args.test_start)
    all_data = load_dataset(args.input)
    development = all_data.loc[all_data[TIME_COLUMN].lt(test_start)].copy()
    if development.empty or development[TIME_COLUMN].max() >= test_start:
        raise AssertionError('Development-only boundary violation.')
    del all_data

    print(f'Building causal features for {len(development):,} development rows...', flush=True)
    development = add_causal_features(development)
    fold_specs, boundaries = make_folds(
        development,
        test_start,
        args.validation_days,
        args.n_folds,
        args.sliding_train_days,
    )
    fold_specs = [fold for fold in fold_specs if fold['window_type'] == 'sliding']
    boundaries = boundaries.loc[boundaries['window_type'].eq('sliding')].copy()

    metric_rows: list[dict[str, Any]] = []
    for candidate in CANDIDATES:
        for fold in fold_specs:
            prediction = predict_candidate(
                candidate, fold['train'], fold['validation'], args.random_state
            )
            metrics = calculate_metrics(
                fold['validation'][TARGET_COLUMN].to_numpy(dtype=float), prediction
            )
            metric_rows.append(
                {
                    'candidate': candidate.name,
                    'feature_set': candidate.feature_set,
                    'target_transform': candidate.target_transform,
                    'fold': fold['fold'],
                    'train_rows': int(len(fold['train'])),
                    'validation_rows': int(len(fold['validation'])),
                    'validation_start': fold['validation_start'],
                    'validation_end_exclusive': fold['validation_end'],
                    **metrics,
                }
            )
            print(
                f'{candidate.name} fold={fold["fold"]} '
                f'MAE={metrics["mae"]:.3f} '
                f'within20={metrics["within_20m_rate"]:.3%}',
                flush=True,
            )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    fold_metrics = pd.DataFrame(metric_rows)
    summary = summarize_metrics(fold_metrics)
    baseline_mae = float(
        summary.loc[summary['candidate'].eq('baseline_absolute'), 'mae_mean'].iloc[0]
    )
    eligible = summary.loc[summary['mae_mean'].le(baseline_mae + 1.0)].copy()
    selected = eligible.iloc[0]
    selected_candidate = next(
        candidate for candidate in CANDIDATES if candidate.name == selected['candidate']
    )

    fold_metrics.to_csv(
        args.output_dir / 'development_fold_metrics.csv', index=False, encoding='utf-8-sig'
    )
    summary.to_csv(
        args.output_dir / 'development_metric_summary.csv', index=False, encoding='utf-8-sig'
    )
    boundaries.to_csv(
        args.output_dir / 'development_fold_boundaries.csv', index=False, encoding='utf-8-sig'
    )
    plot_comparison(summary, args.output_dir / 'development_candidate_comparison.png')

    result = {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'scope': 'development_only_no_final_test_evaluation',
        'input_sha256': dataset_sha256(args.input),
        'test_start_exclusive_boundary': str(test_start),
        'development_rows': int(len(development)),
        'validation_folds': int(args.n_folds),
        'validation_days': int(args.validation_days),
        'sliding_train_days': int(args.sliding_train_days),
        'selection_objective': (
            'maximize mean(within_20m_rate) - 0.25*std(within_20m_rate), '
            'subject to mean MAE <= baseline mean MAE + 1 minute'
        ),
        'selected_candidate': selected_candidate.name,
        'selected_feature_set': selected_candidate.feature_set,
        'selected_target_transform': selected_candidate.target_transform,
        'selected_params': selected_candidate.params,
        'selected_development_metrics': selected.to_dict(),
        'baseline_development_metrics': summary.loc[
            summary['candidate'].eq('baseline_absolute')
        ].iloc[0].to_dict(),
        'feature_availability': {
            'queue_features': (
                'Counts use only queue entries strictly before each prediction time. '
                'Active queue size is reconstructable from current queue state.'
            ),
            'history_features': (
                'Only waits with label_available_time strictly before each prediction '
                'time contribute to rolling statistics.'
            ),
        },
        'warning': (
            'This experiment does not establish improved Final Test performance. '
            'The locked Final Test was not evaluated or overwritten.'
        ),
    }
    write_json(args.output_dir / 'selected_development_candidate.json', result)
    print(json.dumps(result, ensure_ascii=True, indent=2, default=str), flush=True)


if __name__ == '__main__':
    main()
