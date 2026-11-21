"""Modeling-cohort rules applied on top of the audited model dataset.

The audit outputs (``exam_wait_target_*.csv``, ``model_dataset.csv``) stay
complete and untouched so the audit remains reproducible. The pipeline then
removes rows whose wait target is not a real queue wait:

* ``excluded_service`` -- services listed in ``exclude_service_codes``. KB008
  (khám nội, rooms H401/E1108) is ordered during another exam: its status 20
  is set while the parent exam is still running, so status 20 -> status 60 mixes
  the parent exam with waiting (verified: status 20 falls after the parent's
  start in 100% of rows and inside the parent exam in 81%).
* ``wait_over_limit`` -- waits longer than ``max_wait_minutes`` (default 24 h):
  patients who left and came back on a later day.

Both rules depend only on the service code and on the observed outcome of the
visit, never on model error. Rules are applied in that order, so a row matching
both is counted once, under ``excluded_service``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from pipeline.pipeline_config import PipelineConfig
from pipeline.train_and_evaluate_model import TARGET_COLUMN, TIME_COLUMN

SERVICE_COLUMN = 'ma_dich_vu'
LEGACY_COHORT_RULES: dict[str, Any] = {'exclude_service_codes': [], 'max_wait_minutes': None}


def cohort_rules(config: PipelineConfig) -> dict[str, Any]:
    """JSON-friendly snapshot of the active rules, stored in lock files and bundles."""
    return {
        'exclude_service_codes': sorted({code.strip().upper() for code in config.exclude_service_codes}),
        'max_wait_minutes': None if config.max_wait_minutes is None else float(config.max_wait_minutes),
    }


@dataclass(frozen=True)
class CohortResult:
    kept: pd.DataFrame
    removed: pd.DataFrame  # nb_dich_vu_id, queue_entry_time, removal_reason


def apply_cohort_rules(data: pd.DataFrame, config: PipelineConfig) -> CohortResult:
    rules = cohort_rules(config)
    reasons = np.full(len(data), '', dtype=object)

    if rules['exclude_service_codes']:
        if SERVICE_COLUMN not in data.columns:
            raise ValueError(f'Cohort rule needs the {SERVICE_COLUMN!r} column.')
        service = data[SERVICE_COLUMN].astype('string').str.strip().str.upper()
        excluded = service.isin(rules['exclude_service_codes']).fillna(False).to_numpy(dtype=bool)
        reasons[excluded] = 'excluded_service'

    if rules['max_wait_minutes'] is not None:
        too_long = data[TARGET_COLUMN].gt(rules['max_wait_minutes']).to_numpy(dtype=bool)
        reasons[too_long & (reasons == '')] = 'wait_over_limit'

    keep = reasons == ''
    kept = data.loc[keep].reset_index(drop=True)
    removed = data.loc[~keep, ['nb_dich_vu_id', TIME_COLUMN]].assign(removal_reason=reasons[~keep])
    if kept.empty:
        raise ValueError('Cohort rules removed every row.')
    return CohortResult(kept=kept, removed=removed)


def _partition_counts(times: pd.Series, holdout_start: pd.Timestamp) -> dict[str, int]:
    in_holdout = times.ge(holdout_start)
    return {
        'all': int(len(times)),
        'pre_holdout': int((~in_holdout).sum()),
        'holdout': int(in_holdout.sum()),
    }


def build_attrition_table(
    input_times: pd.Series, result: CohortResult, holdout_start: pd.Timestamp, config: PipelineConfig
) -> pd.DataFrame:
    rules = cohort_rules(config)
    steps = [('input_rows', input_times)]
    for reason, label in (
        ('excluded_service', f"removed_excluded_service:{'+'.join(rules['exclude_service_codes']) or '-'}"),
        ('wait_over_limit', f"removed_wait_over_limit:>{rules['max_wait_minutes']}min (additional to service rule)"),
    ):
        steps.append((label, result.removed.loc[result.removed['removal_reason'] == reason, TIME_COLUMN]))
    steps.append(('kept_rows', result.kept[TIME_COLUMN]))
    return pd.DataFrame(
        [{'step': label, **_partition_counts(times, holdout_start)} for label, times in steps]
    )
