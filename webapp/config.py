"""Settings for the operations web app (reception -> model -> QMS -> dashboard).

Every value can be overridden with an environment variable so the app can be
pointed at another bundle or dataset without code changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from pipeline.pipeline_config import PROJECT_ROOT

WEBAPP_ROOT = Path(__file__).resolve().parent


def _path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else default


@dataclass(frozen=True)
class WebConfig:
    # Deployable V3 bundle (README section 3: "Để triển khai (mới nhất)").
    model_path: Path = _path(
        'WT_MODEL_PATH', PROJECT_ROOT / 'results' / 'unified_pipeline_v3' / 'unified_model_latest.joblib'
    )
    # Out-of-sample holdout outputs of the evaluated V3 bundle (dashboard: forecast vs actual).
    holdout_outputs_path: Path = _path(
        'WT_HOLDOUT_OUTPUTS', PROJECT_ROOT / 'results' / 'unified_pipeline_v3' / 'holdout_patient_outputs.csv'
    )
    holdout_metrics_path: Path = PROJECT_ROOT / 'results' / 'unified_pipeline_v3' / 'holdout_final_metrics.json'
    dataset_path: Path = _path('WT_DATASET', PROJECT_ROOT / 'data' / 'processed' / 'model_dataset.csv')
    exam_extract_path: Path = PROJECT_ROOT / 'data' / 'intermediate' / '03_exam.csv'
    cache_dir: Path = _path('WT_CACHE_DIR', WEBAPP_ROOT / 'cache')
    db_path: Path = _path('WT_DB_PATH', WEBAPP_ROOT / 'cache' / 'operations.sqlite3')
    static_dir: Path = WEBAPP_ROOT / 'static'
    hospital_name: str = os.environ.get('WT_HOSPITAL_NAME', 'Bệnh viện Mắt Trung ương')

    # Replay clock default: last full day of HIS data (data ends 31/07/2026).
    default_replay_start: str = os.environ.get('WT_REPLAY_START', '2026-07-31 07:30:00')

    # Operational alert thresholds (dashboard "Cảnh báo vận hành").
    alert_wait_minutes: float = float(os.environ.get('WT_ALERT_WAIT_MINUTES', 45))
    alert_queue_length: int = int(os.environ.get('WT_ALERT_QUEUE_LENGTH', 15))
    alert_overdue_minutes: float = float(os.environ.get('WT_ALERT_OVERDUE_MINUTES', 15))


CONFIG = WebConfig()
