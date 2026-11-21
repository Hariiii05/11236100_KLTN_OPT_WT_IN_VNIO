"""Central configuration for the unified waiting-time pipeline.

Every stage (cohort, split, feature engineering, model search, calibration,
lunch-break warning, diagnostics, final evaluation) reads its parameters from a
single :class:`PipelineConfig` instance instead of scattering magic numbers
across modules. Build one with :func:`config_from_args` so the whole pipeline
can be reproduced from a single command line.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class PipelineConfig:
    input: Path = PROJECT_ROOT / 'data' / 'processed' / 'model_dataset.csv'
    output_dir: Path = PROJECT_ROOT / 'results' / 'unified_pipeline_v3'
    random_state: int = 42

    # --- modeling cohort (applied on top of the audited model dataset) ---
    # KB008 (khám nội, phòng H401/E1108) is a dependent service ordered during
    # another exam, so its status-20 -> status-60 interval is not a queue wait.
    # Waits above `max_wait_minutes` are patients who left and came back later.
    # Both rules use information known before/independent of model error.
    exclude_service_codes: tuple[str, ...] = ('KB008',)
    max_wait_minutes: float | None = 1440.0

    # --- time-based split (train -> validation folds -> holdout) ---
    # `holdout_start` fixes the boundary explicitly (V3 reuses the V2 boundary
    # so the three model generations are scored on the same period). When it
    # is None the boundary is derived from `holdout_days` counted back from the
    # last row. Either way the holdout is written to a lock file immediately
    # after the split and evaluated exactly once.
    holdout_start: str | None = '2026-07-15'
    holdout_days: int = 21
    validation_days: int = 7
    n_folds: int = 6
    sliding_train_days: int = 42
    # Days at the tail of the final training window withheld from model
    # fitting and used only to learn split-conformal calibration offsets.
    calibration_days: int = 7

    # --- lunch-break warning ("Cho qua gio nghi trua") ---
    lunch_start: str = '11:30'
    lunch_end: str = '13:00'
    # Rolling window for the causal per-room/per-department lunch-overflow
    # rate feature, consistent with the 42-day window already used for other
    # causal history features in pipeline/optimize_on_development.py.
    lunch_history_window_days: int = 42
    # F-beta used to pick the warning threshold on validation. beta=1 balances
    # false alarms and misses; beta>1 penalizes misses more (recommended if a
    # missed lunch-overrun is judged worse than an unnecessary warning).
    lunch_threshold_fbeta: float = 1.0

    # --- calibration grouping ---
    # Split-conformal offsets are learned per group (falling back to a single
    # global offset when a group has too few calibration rows) to address
    # uneven P80 coverage across departments noted in the prior diagnosis.
    calibration_group_column: str = 'ma_khoa_thuc_hien'
    calibration_min_group_rows: int = 40

    def with_output_dir(self, output_dir: Path) -> 'PipelineConfig':
        return replace(self, output_dir=output_dir)


def add_pipeline_cli_args(parser: argparse.ArgumentParser) -> None:
    defaults = PipelineConfig()
    parser.add_argument('--input', type=Path, default=defaults.input)
    parser.add_argument('--output-dir', type=Path, default=defaults.output_dir)
    parser.add_argument('--random-state', type=int, default=defaults.random_state)
    parser.add_argument(
        '--exclude-service-codes', nargs='*', default=list(defaults.exclude_service_codes),
        help='Service codes removed from the modeling cohort; pass no values to keep every service.',
    )
    parser.add_argument(
        '--max-wait-minutes', type=float, default=defaults.max_wait_minutes,
        help='Rows waiting longer than this are removed as patients who left; <=0 disables the rule.',
    )
    parser.add_argument(
        '--holdout-start', default=defaults.holdout_start,
        help="Explicit holdout boundary (YYYY-MM-DD); 'auto' derives it from --holdout-days.",
    )
    parser.add_argument('--holdout-days', type=int, default=defaults.holdout_days)
    parser.add_argument('--validation-days', type=int, default=defaults.validation_days)
    parser.add_argument('--n-folds', type=int, default=defaults.n_folds)
    parser.add_argument('--sliding-train-days', type=int, default=defaults.sliding_train_days)
    parser.add_argument('--calibration-days', type=int, default=defaults.calibration_days)
    parser.add_argument('--lunch-start', default=defaults.lunch_start)
    parser.add_argument('--lunch-end', default=defaults.lunch_end)
    parser.add_argument(
        '--lunch-history-window-days', type=int, default=defaults.lunch_history_window_days
    )
    parser.add_argument(
        '--lunch-threshold-fbeta', type=float, default=defaults.lunch_threshold_fbeta
    )
    parser.add_argument(
        '--calibration-group-column', default=defaults.calibration_group_column
    )
    parser.add_argument(
        '--calibration-min-group-rows', type=int, default=defaults.calibration_min_group_rows
    )


def config_from_args(args: argparse.Namespace) -> PipelineConfig:
    return PipelineConfig(
        input=args.input,
        output_dir=args.output_dir,
        random_state=args.random_state,
        exclude_service_codes=tuple(args.exclude_service_codes),
        max_wait_minutes=None if args.max_wait_minutes <= 0 else float(args.max_wait_minutes),
        holdout_start=None if args.holdout_start in (None, '', 'auto') else args.holdout_start,
        holdout_days=args.holdout_days,
        validation_days=args.validation_days,
        n_folds=args.n_folds,
        sliding_train_days=args.sliding_train_days,
        calibration_days=args.calibration_days,
        lunch_start=args.lunch_start,
        lunch_end=args.lunch_end,
        lunch_history_window_days=args.lunch_history_window_days,
        lunch_threshold_fbeta=args.lunch_threshold_fbeta,
        calibration_group_column=args.calibration_group_column,
        calibration_min_group_rows=args.calibration_min_group_rows,
    )
