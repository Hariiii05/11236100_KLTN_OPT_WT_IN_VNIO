"""Train and evaluate leakage-aware waiting-time models with walk-forward validation.

The workflow deliberately separates model selection from the final hold-out test:

1. ``--stage validate`` uses only rows before ``--test-start``. It compares
   expanding and sliding windows with walk-forward validation and writes the
   selected configuration.
2. ``--stage final-test`` retrains that frozen configuration, evaluates the
   hold-out test exactly once, and creates a lock file that prevents accidental
   repeated evaluation.

Training rows whose exam-start label was not yet available at a fold boundary
are purged even when their queue-entry timestamp is in the past.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault('LOKY_MAX_CPU_COUNT', '1')
warnings.filterwarnings('ignore', message='Could not find the number of physical cores')

import joblib
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error, median_absolute_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder


TIME_COLUMN = 'queue_entry_time'
TARGET_COLUMN = 'waiting_minutes'
GROUP_COLUMN = 'nb_dot_dieu_tri_id'

CATEGORICAL_FEATURES = [
    'ma_dich_vu',
    'ma_phong_thuc_hien',
    'ma_khoa_thuc_hien',
    'queue_entry_status',
    'uu_tien',
    'cap_cuu',
]

NUMERIC_FEATURES = [
    'stt',
    'queue_entry_hour',
    'queue_entry_day_of_week',
    'queue_entry_is_weekend',
    'order_to_queue_minutes',
    'queue_hour_sin',
    'queue_hour_cos',
    'queue_dow_sin',
    'queue_dow_cos',
]

FEATURE_COLUMNS = CATEGORICAL_FEATURES + NUMERIC_FEATURES
METRIC_COLUMNS = ['mae', 'rmse', 'median_ae', 'r2', 'p90_ae', 'bias', 'within_10m_rate', 'within_20m_rate']


@dataclass(frozen=True)
class ModelSpec:
    name: str
    target_transform: str
    params: dict[str, Any]


MODEL_SPECS = {
    'median_baseline': ModelSpec('median_baseline', 'none', {}),
    'hist_gb_absolute': ModelSpec(
        'hist_gb_absolute',
        'none',
        {
            'loss': 'absolute_error',
            'learning_rate': 0.08,
            'max_iter': 120,
            'max_leaf_nodes': 31,
            'min_samples_leaf': 40,
            'l2_regularization': 1.0,
        },
    ),
    'hist_gb_log_squared': ModelSpec(
        'hist_gb_log_squared',
        'log1p',
        {
            'loss': 'squared_error',
            'learning_rate': 0.08,
            'max_iter': 120,
            'max_leaf_nodes': 31,
            'min_samples_leaf': 40,
            'l2_regularization': 1.0,
        },
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Walk-forward modeling for HIS waiting time.')
    parser.add_argument('--stage', choices=['validate', 'final-test'], required=True)
    parser.add_argument('--input', type=Path, default=Path('03_output/step2_eda/step2_model_dataset.csv'))
    parser.add_argument('--output-dir', type=Path, default=Path('03_output/step3_modeling'))
    parser.add_argument('--test-start', default='2026-07-01')
    parser.add_argument('--validation-days', type=int, default=7)
    parser.add_argument('--n-folds', type=int, default=6)
    parser.add_argument('--sliding-train-days', type=int, default=42)
    parser.add_argument('--random-state', type=int, default=42)
    return parser.parse_args()


def dataset_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def json_dump(path: Path, value: Any) -> None:
    with path.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)


def load_dataset(path: Path) -> pd.DataFrame:
    data = pd.read_csv(path, low_memory=False)
    required = set(
        ['nb_dich_vu_id', GROUP_COLUMN, TIME_COLUMN, TARGET_COLUMN]
        + CATEGORICAL_FEATURES
        + [c for c in NUMERIC_FEATURES if not c.startswith('queue_') or c in data.columns]
    )
    missing = sorted(required - set(data.columns))
    if missing:
        raise ValueError(f'Missing required columns: {missing}')

    data[TIME_COLUMN] = pd.to_datetime(data[TIME_COLUMN], errors='coerce')
    data[TARGET_COLUMN] = pd.to_numeric(data[TARGET_COLUMN], errors='coerce')
    data = data.loc[data[TIME_COLUMN].notna() & data[TARGET_COLUMN].notna() & data[TARGET_COLUMN].ge(0)].copy()

    # The label becomes observable only when status 60 occurs.
    data['label_available_time'] = data[TIME_COLUMN] + pd.to_timedelta(data[TARGET_COLUMN], unit='m')
    data['queue_entry_hour'] = data[TIME_COLUMN].dt.hour.astype('int16')
    data['queue_entry_day_of_week'] = data[TIME_COLUMN].dt.dayofweek.astype('int8')
    data['queue_entry_is_weekend'] = data['queue_entry_day_of_week'].isin([5, 6]).astype('int8')
    data['queue_hour_sin'] = np.sin(2 * np.pi * data['queue_entry_hour'] / 24)
    data['queue_hour_cos'] = np.cos(2 * np.pi * data['queue_entry_hour'] / 24)
    data['queue_dow_sin'] = np.sin(2 * np.pi * data['queue_entry_day_of_week'] / 7)
    data['queue_dow_cos'] = np.cos(2 * np.pi * data['queue_entry_day_of_week'] / 7)

    for column in CATEGORICAL_FEATURES:
        data[column] = data[column].astype('string').fillna('__MISSING__')
    for column in NUMERIC_FEATURES:
        data[column] = pd.to_numeric(data[column], errors='coerce')

    data = data.sort_values([TIME_COLUMN, 'nb_dich_vu_id'], kind='mergesort').reset_index(drop=True)
    if data['nb_dich_vu_id'].duplicated().any():
        raise ValueError('nb_dich_vu_id must be unique in the model dataset.')
    return data


def feature_audit_table() -> pd.DataFrame:
    rows = [
        ('ma_dich_vu', 'Danh mục dịch vụ đã đăng ký', 'Có trước hoặc tại queue_entry_time', True, 'Giữ một mã; không dùng đồng thời ID/tên trùng thông tin.'),
        ('ma_phong_thuc_hien', 'Phòng thực hiện', 'Được xác định khi vào hàng đợi', True, 'Cần thiết cho điều kiện vận hành theo phòng.'),
        ('ma_khoa_thuc_hien', 'Khoa của phòng', 'Được xác định khi vào hàng đợi', True, 'Thông tin tổ chức đã biết tại thời điểm dự báo.'),
        ('queue_entry_status', 'Status 20 hoặc 40 dùng làm mốc', 'Chính là trigger dự báo', True, 'Phân biệt hai luồng nghiệp vụ target V2.'),
        ('uu_tien', 'Cờ ưu tiên', 'Đã có trong hàng đợi', True, 'Chỉ dùng nếu hệ thống vận hành giữ nguyên ý nghĩa hiện tại.'),
        ('cap_cuu', 'Cờ cấp cứu', 'Đã có trong hàng đợi', True, 'An toàn theo thời gian; có thể không có phương sai.'),
        ('stt', 'Số thứ tự hàng đợi', 'Đã cấp khi vào hàng đợi', True, 'Không dùng giá trị cập nhật sau queue_entry_time.'),
        ('queue_entry_hour/day_of_week/is_weekend', 'Lịch từ queue_entry_time', 'Biết tại thời điểm dự báo', True, 'Biến đổi xác định, không fit trên tương lai.'),
        ('queue_hour/dow_sin_cos', 'Mã hóa chu kỳ từ queue_entry_time', 'Biết tại thời điểm dự báo', True, 'Biến đổi xác định, không fit trên tương lai.'),
        ('order_to_queue_minutes', 'Khoảng chỉ định đến vào hàng đợi', 'Đã hoàn tất tại queue_entry_time', True, 'Không chứa sự kiện sau thời điểm dự báo.'),
        ('bac_si_kham_id/ma_bac_si_kham', 'Bác sĩ trong snapshot cuối', 'Chưa chứng minh được đã gán trước queue', False, 'Loại khỏi baseline để tránh temporal leakage.'),
        ('nb_dich_vu_id/nb_dot_dieu_tri_id', 'Khóa định danh', 'Có thể tồn tại nhưng không phải tín hiệu tổng quát', False, 'Chỉ dùng truy vết và kiểm tra overlap, không làm predictor.'),
        ('queue_entry_time/date/month', 'Timestamp hoặc nhãn thời gian thô', 'Biết tại thời điểm dự báo', False, 'Chỉ dùng chia tập; tránh học thuộc xu hướng tuyệt đối.'),
        ('thoi_gian_chi_dinh', 'Timestamp chỉ định thô', 'Có trước dự báo', False, 'Chỉ dùng qua order_to_queue_minutes.'),
        ('waiting_minutes/exam_start_time', 'Target và thời điểm kết quả', 'Chỉ có sau dự báo', False, 'Tuyệt đối không dùng làm predictor.'),
    ]
    return pd.DataFrame(rows, columns=['feature_or_group', 'source', 'availability', 'used_in_model', 'decision'])


def build_pipeline(spec: ModelSpec, random_state: int) -> Pipeline:
    categorical = OrdinalEncoder(
        handle_unknown='use_encoded_value',
        unknown_value=np.nan,
        encoded_missing_value=np.nan,
    )
    numeric = Pipeline([('imputer', SimpleImputer(strategy='median'))])
    preprocessing = ColumnTransformer(
        [
            ('categorical', categorical, CATEGORICAL_FEATURES),
            ('numeric', numeric, NUMERIC_FEATURES),
        ],
        remainder='drop',
        verbose_feature_names_out=False,
    )
    categorical_mask = [True] * len(CATEGORICAL_FEATURES) + [False] * len(NUMERIC_FEATURES)
    estimator = HistGradientBoostingRegressor(
        **spec.params,
        categorical_features=categorical_mask,
        early_stopping=False,
        random_state=random_state,
    )
    return Pipeline([('preprocessing', preprocessing), ('model', estimator)])


def fit_model(spec: ModelSpec, train: pd.DataFrame, random_state: int) -> dict[str, Any]:
    y = train[TARGET_COLUMN].to_numpy(dtype=float)
    if spec.name == 'median_baseline':
        return {'kind': 'median', 'value': float(np.median(y)), 'spec': spec}

    pipeline = build_pipeline(spec, random_state)
    fit_target = np.log1p(y) if spec.target_transform == 'log1p' else y
    pipeline.fit(train[FEATURE_COLUMNS], fit_target)
    return {'kind': 'pipeline', 'pipeline': pipeline, 'spec': spec}


def predict_model(fitted: dict[str, Any], data: pd.DataFrame) -> np.ndarray:
    if fitted['kind'] == 'median':
        prediction = np.full(len(data), fitted['value'], dtype=float)
    else:
        prediction = fitted['pipeline'].predict(data[FEATURE_COLUMNS])
        target_transform = fitted.get('target_transform')
        if target_transform is None:
            target_transform = fitted['spec'].target_transform
        if target_transform == 'log1p':
            prediction = np.expm1(prediction)
    return np.clip(np.asarray(prediction, dtype=float), 0, None)


def calculate_metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    error = predicted - actual
    absolute_error = np.abs(error)
    return {
        'mae': float(mean_absolute_error(actual, predicted)),
        'rmse': float(np.sqrt(mean_squared_error(actual, predicted))),
        'median_ae': float(median_absolute_error(actual, predicted)),
        'r2': float(r2_score(actual, predicted)),
        'p90_ae': float(np.quantile(absolute_error, 0.90)),
        'bias': float(np.mean(error)),
        'within_10m_rate': float(np.mean(absolute_error <= 10)),
        'within_20m_rate': float(np.mean(absolute_error <= 20)),
    }


def make_folds(
    development: pd.DataFrame,
    test_start: pd.Timestamp,
    validation_days: int,
    n_folds: int,
    sliding_train_days: int,
) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    horizon = timedelta(days=int(validation_days))
    fold_specs: list[dict[str, Any]] = []
    boundary_rows: list[dict[str, Any]] = []

    for window_type in ['expanding', 'sliding']:
        for fold_number in range(1, n_folds + 1):
            validation_start = test_start - horizon * (n_folds - fold_number + 1)
            validation_end = validation_start + horizon
            if window_type == 'expanding':
                train_start = development[TIME_COLUMN].min()
            else:
                train_start = validation_start - timedelta(days=int(sliding_train_days))

            time_candidate = development.loc[
                development[TIME_COLUMN].ge(train_start)
                & development[TIME_COLUMN].lt(validation_start)
            ]
            train = time_candidate.loc[time_candidate['label_available_time'].lt(validation_start)].copy()
            validation = development.loc[
                development[TIME_COLUMN].ge(validation_start)
                & development[TIME_COLUMN].lt(validation_end)
            ].copy()
            if train.empty or validation.empty:
                raise ValueError(
                    f'Empty train/validation for {window_type} fold {fold_number}: '
                    f'{validation_start} to {validation_end}'
                )
            if train[TIME_COLUMN].max() >= validation[TIME_COLUMN].min():
                raise AssertionError('Temporal ordering violation inside a fold.')
            if train['label_available_time'].max() >= validation_start:
                raise AssertionError('A training label was not available at validation start.')

            train_groups = set(train[GROUP_COLUMN].dropna())
            validation_groups = set(validation[GROUP_COLUMN].dropna())
            overlap_groups = train_groups.intersection(validation_groups)

            fold_specs.append(
                {
                    'window_type': window_type,
                    'fold': fold_number,
                    'validation_start': validation_start,
                    'validation_end': validation_end,
                    'train': train,
                    'validation': validation,
                }
            )
            boundary_rows.append(
                {
                    'window_type': window_type,
                    'fold': fold_number,
                    'train_start': train[TIME_COLUMN].min(),
                    'train_end': train[TIME_COLUMN].max(),
                    'train_rows': len(train),
                    'purged_unavailable_label_rows': len(time_candidate) - len(train),
                    'validation_start': validation[TIME_COLUMN].min(),
                    'validation_end': validation[TIME_COLUMN].max(),
                    'validation_rows': len(validation),
                    'overlapping_encounters': len(overlap_groups),
                }
            )
    return fold_specs, pd.DataFrame(boundary_rows)


def aggregate_validation_metrics(fold_metrics: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (window_type, model_name), group in fold_metrics.groupby(['window_type', 'model']):
        row: dict[str, Any] = {
            'window_type': window_type,
            'model': model_name,
            'folds': len(group),
        }
        for metric in METRIC_COLUMNS:
            values = group[metric].astype(float)
            row[f'{metric}_mean'] = float(values.mean())
            row[f'{metric}_median'] = float(values.median())
            row[f'{metric}_std'] = float(values.std(ddof=0))
            row[f'{metric}_min'] = float(values.min())
            row[f'{metric}_max'] = float(values.max())
        row['mae_trend_slope_per_fold'] = float(np.polyfit(group['fold'], group['mae'], 1)[0])
        row['selection_score'] = row['mae_mean'] + 0.25 * row['mae_std']
        rows.append(row)
    summary = pd.DataFrame(rows).sort_values(['selection_score', 'mae_mean', 'mae_std']).reset_index(drop=True)
    summary['selection_rank'] = np.arange(1, len(summary) + 1)
    return summary


def plot_timeline(boundaries: pd.DataFrame, test_start: pd.Timestamp, test_end: pd.Timestamp, output: Path) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(13, 7), sharex=True)
    colors = {'train': '#2F6690', 'validation': '#F28E2B', 'test': '#D1495B'}
    for axis, window_type in zip(axes, ['expanding', 'sliding']):
        subset = boundaries.loc[boundaries['window_type'].eq(window_type)].sort_values('fold')
        for y, row in enumerate(subset.itertuples(index=False), start=1):
            axis.barh(y, pd.Timestamp(row.train_end) - pd.Timestamp(row.train_start), left=pd.Timestamp(row.train_start), color=colors['train'], height=.55)
            axis.barh(y, pd.Timestamp(row.validation_end) - pd.Timestamp(row.validation_start), left=pd.Timestamp(row.validation_start), color=colors['validation'], height=.55)
        axis.axvspan(test_start, test_end, color=colors['test'], alpha=.18)
        axis.set_yticks(range(1, len(subset) + 1), [f'Fold {i}' for i in subset['fold']])
        axis.set_title(f'{window_type.title()} window', loc='left', fontweight='bold')
        axis.grid(axis='x', alpha=.2)
    handles = [plt.Rectangle((0, 0), 1, 1, color=colors[k], alpha=.8 if k != 'test' else .25) for k in ['train', 'validation', 'test']]
    fig.suptitle('Walk-forward validation và hold-out test', fontweight='bold', y=.995)
    fig.legend(
        handles,
        ['Train', 'Validation', 'Final Test'],
        loc='upper center',
        bbox_to_anchor=(.5, .965),
        ncol=3,
        frameon=False,
    )
    fig.tight_layout(rect=[0, 0, 1, .90])
    fig.savefig(output, dpi=220, bbox_inches='tight', facecolor='white')
    plt.close(fig)


def plot_metric_trend(fold_metrics: pd.DataFrame, output: Path) -> None:
    fig, ax = plt.subplots(figsize=(12, 6))
    for (window_type, model_name), group in fold_metrics.groupby(['window_type', 'model']):
        label = f'{window_type} / {model_name}'
        ax.plot(group['validation_start'], group['mae'], marker='o', linewidth=1.8, label=label)
    ax.set_title('Xu hướng MAE qua các validation fold', loc='left', fontweight='bold')
    ax.set_xlabel('Thời điểm bắt đầu validation')
    ax.set_ylabel('MAE (phút)')
    ax.grid(alpha=.2)
    ax.legend(frameon=False, ncol=2, fontsize=8)
    fig.tight_layout()
    fig.savefig(output, dpi=220, bbox_inches='tight', facecolor='white')
    plt.close(fig)


def run_validation(args: argparse.Namespace) -> None:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    test_lock = args.output_dir / 'FINAL_TEST_EVALUATED.json'
    if test_lock.exists():
        raise RuntimeError('Final Test is already locked. Validation outputs must not be changed afterward.')

    data = load_dataset(args.input)
    test_start = pd.Timestamp(args.test_start)
    development = data.loc[data[TIME_COLUMN].lt(test_start)].copy()
    final_test = data.loc[data[TIME_COLUMN].ge(test_start)].copy()
    if development.empty or final_test.empty:
        raise ValueError('Development or final test partition is empty.')
    if development[TIME_COLUMN].max() >= final_test[TIME_COLUMN].min():
        raise AssertionError('Development/Test time ordering violation.')

    fold_specs, boundaries = make_folds(
        development,
        test_start,
        args.validation_days,
        args.n_folds,
        args.sliding_train_days,
    )
    boundaries.to_csv(args.output_dir / 'fold_boundaries.csv', index=False, encoding='utf-8-sig')
    feature_audit_table().to_csv(args.output_dir / 'feature_audit.csv', index=False, encoding='utf-8-sig')

    metric_rows: list[dict[str, Any]] = []
    for fold_spec in fold_specs:
        train = fold_spec['train']
        validation = fold_spec['validation']
        for model_spec in MODEL_SPECS.values():
            fitted = fit_model(model_spec, train, args.random_state)
            predicted = predict_model(fitted, validation)
            metrics = calculate_metrics(validation[TARGET_COLUMN].to_numpy(dtype=float), predicted)
            metric_rows.append(
                {
                    'window_type': fold_spec['window_type'],
                    'fold': fold_spec['fold'],
                    'model': model_spec.name,
                    'train_rows': len(train),
                    'validation_rows': len(validation),
                    'validation_start': fold_spec['validation_start'],
                    'validation_end_exclusive': fold_spec['validation_end'],
                    **metrics,
                }
            )
            print(
                f"{fold_spec['window_type']} fold={fold_spec['fold']} "
                f"model={model_spec.name} MAE={metrics['mae']:.3f} RMSE={metrics['rmse']:.3f}"
            )

    fold_metrics = pd.DataFrame(metric_rows)
    fold_metrics.to_csv(args.output_dir / 'validation_fold_metrics.csv', index=False, encoding='utf-8-sig')
    validation_summary = aggregate_validation_metrics(fold_metrics)
    validation_summary.to_csv(args.output_dir / 'validation_metric_summary.csv', index=False, encoding='utf-8-sig')

    selected = validation_summary.iloc[0]
    selected_model = str(selected['model'])
    selected_window = str(selected['window_type'])
    selection = {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'input': str(args.input.resolve()),
        'dataset_sha256': dataset_sha256(args.input),
        'time_column': TIME_COLUMN,
        'target_column': TARGET_COLUMN,
        'test_start': str(test_start),
        'test_rows_locked_unseen': int(len(final_test)),
        'development_rows': int(len(development)),
        'validation_days': args.validation_days,
        'n_folds': args.n_folds,
        'sliding_train_days': args.sliding_train_days,
        'selection_rule': 'minimum(mean_validation_MAE + 0.25 * std_validation_MAE)',
        'selected_window': selected_window,
        'selected_model': selected_model,
        'selected_model_spec': {
            'target_transform': MODEL_SPECS[selected_model].target_transform,
            'params': MODEL_SPECS[selected_model].params,
        },
        'selected_validation_metrics': selected.to_dict(),
        'categorical_features': CATEGORICAL_FEATURES,
        'numeric_features': NUMERIC_FEATURES,
        'label_availability_rule': 'exam_start_time (= queue_entry_time + waiting_minutes) < fold validation_start',
    }
    json_dump(args.output_dir / 'selected_model.json', selection)

    plot_timeline(boundaries, test_start, data[TIME_COLUMN].max(), args.output_dir / 'walk_forward_timeline.png')
    plot_metric_trend(fold_metrics, args.output_dir / 'validation_mae_trend.png')
    print(f'Selected: window={selected_window}, model={selected_model}, score={selected["selection_score"]:.3f}')
    print('Final Test remains locked and has not been evaluated.')


def final_training_rows(data: pd.DataFrame, selection: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    test_start = pd.Timestamp(selection['test_start'])
    development = data.loc[data[TIME_COLUMN].lt(test_start)].copy()
    final_test = data.loc[data[TIME_COLUMN].ge(test_start)].copy()
    if selection['selected_window'] == 'sliding':
        train_start = test_start - timedelta(days=int(selection['sliding_train_days']))
        development = development.loc[development[TIME_COLUMN].ge(train_start)].copy()
    before_purge = len(development)
    development = development.loc[development['label_available_time'].lt(test_start)].copy()
    return development, final_test, before_purge - len(development)


def test_metric_rows(test: pd.DataFrame, predicted: np.ndarray) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    groups = [('overall', np.ones(len(test), dtype=bool))]
    queue_status_numeric = pd.to_numeric(test['queue_entry_status'], errors='coerce')
    groups.extend(
        [
            ('queue_status_20', queue_status_numeric.eq(20).to_numpy()),
            ('queue_status_40', queue_status_numeric.eq(40).to_numpy()),
        ]
    )
    actual = test[TARGET_COLUMN].to_numpy(dtype=float)
    for group_name, mask in groups:
        if not np.any(mask):
            continue
        rows.append({'group': group_name, 'rows': int(np.sum(mask)), **calculate_metrics(actual[mask], predicted[mask])})
    return pd.DataFrame(rows)


def plot_test_diagnostics(predictions: pd.DataFrame, output: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.2))
    display_cap = predictions['actual_minutes'].quantile(.99)
    shown = predictions.loc[predictions['actual_minutes'].le(display_cap)].copy()
    axes[0].scatter(shown['actual_minutes'], shown['predicted_minutes'], s=7, alpha=.18, color='#2F6690')
    axes[0].plot([0, display_cap], [0, display_cap], color='#D1495B', linestyle='--')
    axes[0].set_title('Dự báo và thực tế đến P99', loc='left', fontweight='bold')
    axes[0].set_xlabel('Thực tế (phút)')
    axes[0].set_ylabel('Dự báo (phút)')
    axes[0].grid(alpha=.2)
    axes[1].hist(shown['absolute_error_minutes'], bins=40, color='#F28E2B', edgecolor='white')
    axes[1].set_title('Phân phối sai số tuyệt đối', loc='left', fontweight='bold')
    axes[1].set_xlabel('Sai số tuyệt đối (phút)')
    axes[1].set_ylabel('Số ca')
    axes[1].grid(axis='y', alpha=.2)
    fig.tight_layout()
    fig.savefig(output, dpi=220, bbox_inches='tight', facecolor='white')
    plt.close(fig)


def run_final_test(args: argparse.Namespace) -> None:
    selection_path = args.output_dir / 'selected_model.json'
    test_lock = args.output_dir / 'FINAL_TEST_EVALUATED.json'
    if not selection_path.exists():
        raise FileNotFoundError('Run --stage validate before final-test.')
    if test_lock.exists():
        raise RuntimeError(f'Final Test was already evaluated. Lock: {test_lock}')

    with selection_path.open(encoding='utf-8') as stream:
        selection = json.load(stream)
    current_hash = dataset_sha256(args.input)
    if current_hash != selection['dataset_sha256']:
        raise RuntimeError('Input dataset changed after model selection. Re-validation is required before Test.')

    data = load_dataset(args.input)
    train, final_test, purged_rows = final_training_rows(data, selection)
    if train.empty or final_test.empty:
        raise ValueError('Final train or Test is empty.')
    test_start = pd.Timestamp(selection['test_start'])
    if train[TIME_COLUMN].max() >= final_test[TIME_COLUMN].min():
        raise AssertionError('Final Train/Test time ordering violation.')
    if train['label_available_time'].max() >= test_start:
        raise AssertionError('A final training label was not available at Test start.')

    model_spec = MODEL_SPECS[selection['selected_model']]
    fitted = fit_model(model_spec, train, args.random_state)
    predicted = predict_model(fitted, final_test)

    metrics = test_metric_rows(final_test, predicted)
    metrics.to_csv(args.output_dir / 'final_test_metrics.csv', index=False, encoding='utf-8-sig')
    prediction_table = final_test[
        ['nb_dich_vu_id', GROUP_COLUMN, TIME_COLUMN, 'queue_entry_status', 'ma_dich_vu', 'ma_phong_thuc_hien']
    ].copy()
    prediction_table['actual_minutes'] = final_test[TARGET_COLUMN].to_numpy(dtype=float)
    prediction_table['predicted_minutes'] = predicted
    prediction_table['error_minutes'] = predicted - prediction_table['actual_minutes']
    prediction_table['absolute_error_minutes'] = prediction_table['error_minutes'].abs()
    prediction_table.to_csv(args.output_dir / 'final_test_predictions.csv', index=False, encoding='utf-8-sig')

    serializable_fitted = dict(fitted)
    serializable_fitted['model_name'] = model_spec.name
    serializable_fitted['target_transform'] = model_spec.target_transform
    serializable_fitted.pop('spec', None)
    model_artifact = {
        'fitted_model': serializable_fitted,
        'selected_window': selection['selected_window'],
        'selected_model': selection['selected_model'],
        'features': FEATURE_COLUMNS,
        'test_start': selection['test_start'],
        'trained_at_utc': datetime.now(timezone.utc).isoformat(),
    }
    joblib.dump(model_artifact, args.output_dir / 'final_model.joblib')
    plot_test_diagnostics(prediction_table, args.output_dir / 'final_test_diagnostics.png')

    lock = {
        'evaluated_at_utc': datetime.now(timezone.utc).isoformat(),
        'dataset_sha256': current_hash,
        'selected_window': selection['selected_window'],
        'selected_model': selection['selected_model'],
        'train_rows': int(len(train)),
        'purged_unavailable_label_rows': int(purged_rows),
        'test_rows': int(len(final_test)),
        'test_start': selection['test_start'],
        'test_end': str(final_test[TIME_COLUMN].max()),
        'overall_metrics': metrics.loc[metrics['group'].eq('overall')].iloc[0].to_dict(),
        'warning': 'Hold-out  Test has been evaluated. Do not use these results for further model, feature, or hyperparameter selection.',
    }
    json_dump(test_lock, lock)
    print(json.dumps(lock, ensure_ascii=False, indent=2, default=str))


def main() -> None:
    args = parse_args()
    if args.validation_days <= 0 or args.n_folds <= 0 or args.sliding_train_days <= 0:
        raise ValueError('Window sizes and n_folds must be positive.')
    if args.stage == 'validate':
        run_validation(args)
    else:
        run_final_test(args)


if __name__ == '__main__':
    main()
