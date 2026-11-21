"""Time-based train / validation / holdout split for the unified pipeline.

The whole dataset (old development rows plus the previously-locked Final
Test rows) is combined and re-split by time only -- no shuffling, so no
future information can leak backward. The holdout is everything from
``holdout_start`` onward (or the last ``holdout_days`` days when no explicit
boundary is configured); it is written to a lock file immediately after the
split, before any model fitting happens, and later stages must treat it as
read-only until the single final evaluation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from pipeline.cohort import LEGACY_COHORT_RULES, cohort_rules
from pipeline.pipeline_config import PipelineConfig
from pipeline.train_and_evaluate_model import (
    GROUP_COLUMN,
    TIME_COLUMN,
    dataset_sha256,
    json_dump,
    make_folds,
)

HOLDOUT_LOCK_FILENAME = 'holdout_locked.json'
HOLDOUT_EVALUATED_FILENAME = 'HOLDOUT_EVALUATED.json'


def compute_holdout_start(data: pd.DataFrame, holdout_days: int) -> pd.Timestamp:
    last_day = data[TIME_COLUMN].max().normalize()
    return last_day + pd.Timedelta(1, unit='D') - pd.Timedelta(holdout_days, unit='D')


def resolve_holdout_start(data: pd.DataFrame, config: PipelineConfig) -> pd.Timestamp:
    if config.holdout_start:
        return pd.Timestamp(config.holdout_start)
    return compute_holdout_start(data, config.holdout_days)


@dataclass(frozen=True)
class DatasetSplit:
    pre_holdout: pd.DataFrame
    holdout: pd.DataFrame
    holdout_start: pd.Timestamp


def split_and_lock_holdout(data: pd.DataFrame, config: PipelineConfig) -> DatasetSplit:
    """Split an already-loaded dataset by time and lock the holdout immediately.

    ``data`` should already be cohort-filtered and carry every engineered
    feature, computed once on the full timeline before splitting. This is not a
    leakage risk: causal features (rolling counts, historical rates) for a
    holdout row are only ever built from events strictly before that row's own
    ``queue_entry_time`` -- exactly what a deployed model would see in
    production on that date, including the true pre-holdout history. Isolating
    holdout rows before computing rolling history would only reset those
    windows to empty at the holdout boundary for no methodological reason.

    If a lock already exists from a previous dev-stage run, its boundary is
    reused and verified against the current input hash, cohort rules and any
    explicit ``holdout_start`` instead of being recomputed, so the holdout
    cannot silently drift across reruns while the dev stage is iterated on.
    """
    lock_path = config.output_dir / HOLDOUT_LOCK_FILENAME
    dataset_hash = dataset_sha256(config.input)
    rules = cohort_rules(config)

    if lock_path.exists():
        with lock_path.open(encoding='utf-8') as stream:
            lock = json.load(stream)
        if lock['dataset_sha256'] != dataset_hash:
            raise RuntimeError(
                f'{config.input} changed since the holdout was locked at {lock_path}. '
                'Restore the original input, or delete the output directory to '
                'intentionally re-split (this discards the existing lock and must '
                'only be done before any final evaluation has happened).'
            )
        if lock.get('cohort_rules', LEGACY_COHORT_RULES) != rules:
            raise RuntimeError(
                f'Cohort rules changed since the holdout was locked at {lock_path} '
                f'(locked: {lock.get("cohort_rules", LEGACY_COHORT_RULES)}, current: {rules}). '
                'Changing the cohort changes which rows are in the holdout; use a new '
                'output directory instead.'
            )
        holdout_start = pd.Timestamp(lock['holdout_start'])
        if config.holdout_start and pd.Timestamp(config.holdout_start) != holdout_start:
            raise RuntimeError(
                f'--holdout-start {config.holdout_start} conflicts with the locked boundary '
                f'{holdout_start} in {lock_path}.'
            )
    else:
        holdout_start = resolve_holdout_start(data, config)

    pre_holdout = data.loc[data[TIME_COLUMN].lt(holdout_start)].copy()
    holdout = data.loc[data[TIME_COLUMN].ge(holdout_start)].copy()
    if pre_holdout.empty or holdout.empty:
        raise ValueError('Pre-holdout or holdout partition is empty; adjust the holdout boundary.')
    if pre_holdout[TIME_COLUMN].max() >= holdout[TIME_COLUMN].min():
        raise AssertionError('Pre-holdout/holdout time ordering violation.')

    if not lock_path.exists():
        config.output_dir.mkdir(parents=True, exist_ok=True)
        train_groups = set(pre_holdout[GROUP_COLUMN].dropna())
        holdout_groups = set(holdout[GROUP_COLUMN].dropna())
        json_dump(lock_path, {
            'locked_at_utc': datetime.now(timezone.utc).isoformat(),
            'input': str(config.input.resolve()),
            'dataset_sha256': dataset_hash,
            'cohort_rules': rules,
            'holdout_start_source': 'config' if config.holdout_start else 'holdout_days',
            'holdout_days_configured': config.holdout_days,
            'holdout_start': str(holdout_start),
            'holdout_end': str(holdout[TIME_COLUMN].max()),
            'holdout_calendar_days_with_data': int(holdout[TIME_COLUMN].dt.normalize().nunique()),
            'pre_holdout_rows': int(len(pre_holdout)),
            'holdout_rows': int(len(holdout)),
            'overlapping_encounters': len(train_groups & holdout_groups),
            'note': (
                'Holdout locked immediately after the time-based split. It must be '
                'used only once, at the final evaluation stage, and never for feature '
                'selection, hyperparameter search, or calibration fitting.'
            ),
        })

    return DatasetSplit(pre_holdout=pre_holdout, holdout=holdout, holdout_start=holdout_start)


def build_validation_folds(
    pre_holdout: pd.DataFrame,
    holdout_start: pd.Timestamp,
    config: PipelineConfig,
    window_types: tuple[str, ...] = ('sliding',),
) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    """Walk-forward folds inside the pre-holdout data, purging unavailable labels.

    Thin wrapper around the already-tested ``make_folds`` from
    ``train_and_evaluate_model`` so the unified pipeline reuses the exact same
    causal purge logic instead of re-implementing it.
    """
    fold_specs, boundaries = make_folds(
        pre_holdout, holdout_start, config.validation_days, config.n_folds, config.sliding_train_days
    )
    if window_types:
        fold_specs = [fold for fold in fold_specs if fold['window_type'] in window_types]
        boundaries = boundaries.loc[boundaries['window_type'].isin(window_types)].copy()
    return fold_specs, boundaries


def check_holdout_not_evaluated(output_dir: Path) -> None:
    evaluated_path = output_dir / HOLDOUT_EVALUATED_FILENAME
    if evaluated_path.exists():
        raise RuntimeError(
            f'Holdout was already evaluated (see {evaluated_path}). '
            'Do not re-run the final evaluation stage.'
        )


def lock_holdout_evaluated(output_dir: Path, summary: dict[str, Any]) -> None:
    json_dump(output_dir / HOLDOUT_EVALUATED_FILENAME, summary)
