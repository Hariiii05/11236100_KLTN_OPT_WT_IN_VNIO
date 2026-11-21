"""Feature engineering for the unified pipeline.

Combines three sources, all causal (only using information available at or
before ``queue_entry_time``):

1. The official calendar/category features and the validated queue-dynamics
   features, reused unchanged from ``train_and_evaluate_model`` and
   ``optimize_on_development`` -- the prior diagnosis already established that
   queue dynamics help and extra service-duration/doctor features do not
   (see docs/model_diagnosis_and_calibration.md), so that conclusion is the
   starting point here rather than being re-litigated feature by feature.
2. Patient payment type (BHYT vs fee-for-service), joined from the encounter
   extract. It is fixed at admission time, strictly before any exam queue
   entry, so it is safe to use as a predictor and as a segment for diagnosis.
3. New lunch-break features: minutes until the configured lunch start, and a
   causal per-room/per-department historical rate of morning patients who
   were pushed past lunch. These directly target weakness W5 from the prior
   diagnosis (structural, shift-related tail waits the model could not see).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.optimize_on_development import (
    DYNAMICS_FEATURES,
    QUEUE_FEATURES,
    add_causal_features,
)
from pipeline.pipeline_config import PROJECT_ROOT, PipelineConfig
from pipeline.train_and_evaluate_model import CATEGORICAL_FEATURES, NUMERIC_FEATURES, TIME_COLUMN

LUNCH_NUMERIC_FEATURES = [
    'minutes_to_lunch_start',
    'room_lunch_overflow_rate',
    'room_lunch_overflow_count',
    'department_lunch_overflow_rate',
    'department_lunch_overflow_count',
]
PATIENT_TYPE_CATEGORICAL_FEATURES = ['patient_payment_type']

# Feature-set ladder for the walk-forward model search in model_training.py.
FEATURE_SET_NAMES = (
    'official',
    'queue_dynamics',
    'queue_dynamics_patient_type',
    'queue_dynamics_lunch',
)


def feature_columns(feature_set: str) -> tuple[list[str], list[str]]:
    """Categorical/numeric column lists for a named feature-set rung."""
    if feature_set not in FEATURE_SET_NAMES:
        raise ValueError(f'Unknown feature set: {feature_set!r}')
    categorical = list(CATEGORICAL_FEATURES)
    numeric = list(NUMERIC_FEATURES)
    if feature_set in {'queue_dynamics', 'queue_dynamics_patient_type', 'queue_dynamics_lunch'}:
        numeric = numeric + QUEUE_FEATURES + DYNAMICS_FEATURES
    if feature_set in {'queue_dynamics_patient_type', 'queue_dynamics_lunch'}:
        categorical = categorical + PATIENT_TYPE_CATEGORICAL_FEATURES
    if feature_set == 'queue_dynamics_lunch':
        numeric = numeric + LUNCH_NUMERIC_FEATURES
    return categorical, numeric


def parse_time_of_day(value: str) -> pd.Timedelta:
    parts = [int(part) for part in value.split(':')]
    while len(parts) < 3:
        parts.append(0)
    hours, minutes, seconds = parts[:3]
    return (
        pd.Timedelta(hours, unit='h')
        + pd.Timedelta(minutes, unit='m')
        + pd.Timedelta(seconds, unit='s')
    )


def load_patient_payment_type(root: Path = PROJECT_ROOT) -> pd.DataFrame:
    """One row per encounter: BHYT (insurance) vs fee-for-service (dich_vu).

    Derived from ``doi_tuong`` (1 = fee-for-service ["Benh nhan TYC"/"thuong"],
    2 = BHYT and its sub-categories -- see docs/his_source_queries.md). This
    is a visit-level attribute fixed at admission, well before any exam queue
    entry, so it cannot leak future information.
    """
    from pipeline.prepare_event_log import read_extract

    frame, _ = read_extract(
        root / 'data' / 'intermediate' / '01_encounter.csv',
        usecols=['nb_dot_dieu_tri_id', 'doi_tuong'],
    )
    frame['nb_dot_dieu_tri_id'] = pd.to_numeric(frame['nb_dot_dieu_tri_id'], errors='coerce')
    frame = frame.dropna(subset=['nb_dot_dieu_tri_id']).drop_duplicates('nb_dot_dieu_tri_id').copy()
    frame['nb_dot_dieu_tri_id'] = frame['nb_dot_dieu_tri_id'].astype('int64')
    doi_tuong = pd.to_numeric(frame['doi_tuong'], errors='coerce')
    frame['patient_payment_type'] = np.select(
        [doi_tuong.eq(1), doi_tuong.eq(2)], ['dich_vu', 'bhyt'], default='khac'
    )
    return frame[['nb_dot_dieu_tri_id', 'patient_payment_type']]


def add_patient_payment_type(data: pd.DataFrame, root: Path = PROJECT_ROOT) -> pd.DataFrame:
    mapping = load_patient_payment_type(root)
    data = data.copy()
    data['nb_dot_dieu_tri_id'] = pd.to_numeric(data['nb_dot_dieu_tri_id'], errors='coerce').astype('int64')
    merged = data.merge(mapping, on='nb_dot_dieu_tri_id', how='left')
    merged['patient_payment_type'] = merged['patient_payment_type'].fillna('unknown').astype('string')
    return merged


def causal_group_rate_and_count(
    data: pd.DataFrame,
    mask: np.ndarray,
    outcome: np.ndarray,
    group_column: str,
    window_days: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Causal rolling mean of a binary outcome within a group.

    For each row where ``mask`` is True, uses only other ``mask``-eligible
    rows in the same group whose label became available strictly before this
    row's ``queue_entry_time``, within a trailing ``window_days`` window.
    Rows outside ``mask`` (and masked rows with no prior history) get NaN,
    left for the model's median imputer, matching the convention used by the
    other causal history features in ``optimize_on_development.py``.
    """
    counts = np.zeros(len(data), dtype=np.int32)
    rates = np.full(len(data), np.nan, dtype=float)
    query_ns = data[TIME_COLUMN].astype('int64').to_numpy()
    completed_ns = data['label_available_time'].astype('int64').to_numpy()
    window_ns = int(pd.Timedelta(window_days, unit='D').value)
    groups = data[group_column].astype('string').fillna('__MISSING__').to_numpy()

    eligible = np.flatnonzero(mask)
    for group_value in np.unique(groups[eligible]):
        group_positions = eligible[groups[eligible] == group_value]
        query_order = group_positions[np.argsort(query_ns[group_positions], kind='stable')]
        completion_order = group_positions[np.argsort(completed_ns[group_positions], kind='stable')]
        completion_times = completed_ns[completion_order]
        completion_values = outcome[completion_order].astype(float)
        left = 0
        right = 0
        running_sum = 0.0
        for position in query_order:
            query_time = query_ns[position]
            while right < len(completion_order) and completion_times[right] < query_time:
                running_sum += completion_values[right]
                right += 1
            cutoff = query_time - window_ns
            while left < right and completion_times[left] < cutoff:
                running_sum -= completion_values[left]
                left += 1
            count = right - left
            counts[position] = count
            if count > 0:
                rates[position] = running_sum / count
    return counts, rates


def add_lunch_break_features(data: pd.DataFrame, config: PipelineConfig) -> pd.DataFrame:
    """Add lunch-timing features and the historical ``crosses_lunch`` label.

    ``crosses_lunch`` (ground truth, for training/evaluating the warning
    classifier only -- never a model feature) is True when a patient who
    queued before ``lunch_start`` actually started their exam at or after
    ``lunch_start`` (including cases that rolled into the next day).
    """
    data = data.copy()
    lunch_start = parse_time_of_day(config.lunch_start)
    time_of_day_queue = data[TIME_COLUMN] - data[TIME_COLUMN].dt.normalize()
    time_of_day_exam = data['label_available_time'] - data['label_available_time'].dt.normalize()
    exam_next_day = data['label_available_time'].dt.normalize() > data[TIME_COLUMN].dt.normalize()

    data['minutes_to_lunch_start'] = (lunch_start - time_of_day_queue).dt.total_seconds() / 60.0
    data['registered_before_lunch'] = time_of_day_queue.lt(lunch_start)
    data['crosses_lunch'] = data['registered_before_lunch'] & (
        exam_next_day | time_of_day_exam.ge(lunch_start)
    )

    outcome = data['crosses_lunch'].to_numpy(dtype=float)
    mask = data['registered_before_lunch'].to_numpy(dtype=bool)
    for group_column, prefix in (('ma_phong_thuc_hien', 'room'), ('ma_khoa_thuc_hien', 'department')):
        counts, rates = causal_group_rate_and_count(
            data, mask, outcome, group_column, config.lunch_history_window_days
        )
        data[f'{prefix}_lunch_overflow_count'] = counts
        data[f'{prefix}_lunch_overflow_rate'] = rates
    return data


def engineer_features(data: pd.DataFrame, config: PipelineConfig, root: Path = PROJECT_ROOT) -> pd.DataFrame:
    """Single entry point: queue dynamics + patient type + lunch features."""
    data = add_causal_features(data)
    data = add_patient_payment_type(data, root)
    data = add_lunch_break_features(data, config)
    return data
