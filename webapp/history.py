"""Historical HIS data used by the web app, loaded exactly like the pipeline.

The modeling cohort is rebuilt with the pipeline's own functions
(``load_dataset`` -> ``apply_cohort_rules`` -> ``add_patient_payment_type``),
so the replayed queue state and the dashboard use the same rows, dtypes and
``label_available_time`` arithmetic the model was trained on. The result is
cached as a pickle keyed on the source files' size and mtime.
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from pipeline.cohort import apply_cohort_rules
from pipeline.feature_engineering import add_patient_payment_type
from pipeline.pipeline_config import PipelineConfig
from pipeline.prepare_event_log import read_extract
from pipeline.train_and_evaluate_model import load_dataset
from webapp.config import WebConfig

CACHE_VERSION = 3
HISTORY_COLUMNS = [
    'nb_dich_vu_id', 'nb_dot_dieu_tri_id', 'ma_dich_vu', 'ma_phong_thuc_hien', 'ma_khoa_thuc_hien',
    'stt', 'uu_tien', 'cap_cuu', 'queue_entry_status', 'queue_entry_time', 'label_available_time',
    'waiting_minutes', 'order_to_queue_minutes', 'exam_complete_time', 'service_minutes_60_to_150',
    'patient_payment_type',
]


@dataclass
class HistoricalData:
    cohort: pd.DataFrame          # one row per exam service in the V3 modeling cohort
    rooms: pd.DataFrame           # ma_phong_thuc_hien, ten_phong, dia_diem, ma_khoa
    departments: pd.DataFrame     # ma_khoa_thuc_hien, ten_khoa
    services: pd.DataFrame        # ma_dich_vu, ten_dich_vu
    holdout: pd.DataFrame | None  # out-of-sample predictions joined to the cohort


def _signature(paths: list[Path]) -> tuple:
    return tuple((str(p), p.stat().st_size, int(p.stat().st_mtime)) for p in paths if p.exists())


def _load_catalog(config: WebConfig) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    exam, _ = read_extract(
        config.exam_extract_path,
        usecols=['ma_phong_thuc_hien', 'ten_phong_thuc_hien', 'dia_diem_phong',
                 'ma_khoa_thuc_hien', 'ten_khoa_thuc_hien', 'ma_dich_vu', 'ten_dich_vu'],
    )
    rooms = (
        exam.dropna(subset=['ma_phong_thuc_hien'])
        .drop_duplicates('ma_phong_thuc_hien', keep='last')
        .rename(columns={'ten_phong_thuc_hien': 'ten_phong', 'dia_diem_phong': 'dia_diem'})
        [['ma_phong_thuc_hien', 'ten_phong', 'dia_diem', 'ma_khoa_thuc_hien']]
    )
    departments = (
        exam.dropna(subset=['ma_khoa_thuc_hien'])
        .drop_duplicates('ma_khoa_thuc_hien', keep='last')
        .rename(columns={'ten_khoa_thuc_hien': 'ten_khoa'})[['ma_khoa_thuc_hien', 'ten_khoa']]
    )
    services = exam.dropna(subset=['ma_dich_vu']).drop_duplicates('ma_dich_vu', keep='last')[
        ['ma_dich_vu', 'ten_dich_vu']
    ]
    return (
        rooms.reset_index(drop=True).astype('string'),
        departments.reset_index(drop=True).astype('string'),
        services.reset_index(drop=True).astype('string'),
    )


def _load_cohort(config: WebConfig) -> pd.DataFrame:
    pipeline_config = PipelineConfig(input=config.dataset_path)
    raw = load_dataset(config.dataset_path)
    cohort = apply_cohort_rules(raw, pipeline_config).kept
    del raw
    cohort = add_patient_payment_type(cohort)
    cohort = cohort[HISTORY_COLUMNS].copy()
    cohort['exam_complete_time'] = pd.to_datetime(cohort['exam_complete_time'], errors='coerce')
    # Doctor codes have leading zeros; load_dataset parses them as numbers, so re-read as text.
    doctors = pd.read_csv(
        config.dataset_path, usecols=['nb_dich_vu_id', 'ma_bac_si_kham'], dtype={'ma_bac_si_kham': 'string'}
    )
    cohort = cohort.merge(doctors, on='nb_dich_vu_id', how='left')
    cohort = cohort.sort_values(['queue_entry_time', 'nb_dich_vu_id'], kind='mergesort').reset_index(drop=True)
    return cohort


def _load_holdout(config: WebConfig, cohort: pd.DataFrame) -> pd.DataFrame | None:
    if not config.holdout_outputs_path.exists():
        return None
    outputs = pd.read_csv(config.holdout_outputs_path)
    outputs = outputs[['nb_dich_vu_id', 'predicted_wait_minutes', 'predicted_wait_p80_minutes',
                       'probability_cross_lunch', 'recommendation']]
    return outputs.merge(cohort, on='nb_dich_vu_id', how='inner')


def load_history(config: WebConfig) -> HistoricalData:
    sources = [config.dataset_path, config.exam_extract_path, config.holdout_outputs_path,
               config.dataset_path.parents[1] / 'intermediate' / '01_encounter.csv']
    if not config.dataset_path.exists():
        raise FileNotFoundError(
            f'Thiếu dữ liệu {config.dataset_path}. Chạy pipeline chuẩn bị dữ liệu trước (README mục 2).'
        )
    key = (CACHE_VERSION, _signature(sources))
    cache_path = config.cache_dir / 'history.pkl'
    if cache_path.exists():
        with cache_path.open('rb') as stream:
            cached = pickle.load(stream)
        if cached.get('key') == key:
            return cached['data']

    cohort = _load_cohort(config)
    rooms, departments, services = _load_catalog(config)
    data = HistoricalData(cohort, rooms, departments, services, _load_holdout(config, cohort))
    config.cache_dir.mkdir(parents=True, exist_ok=True)
    with cache_path.open('wb') as stream:
        pickle.dump({'key': key, 'data': data}, stream, protocol=pickle.HIGHEST_PROTOCOL)
    return data
