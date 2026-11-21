"""Train vs validation error per walk-forward fold, to check over/under-fitting.

Refits the median baseline, the previous-generation reference model and the
candidate selected by the dev stage on every sliding-window fold, then scores
each fit on its own training rows and on its validation rows. A train MAE far
below the validation MAE would point to over-fitting; a train MAE close to the
baseline would point to under-fitting.

Read-only with respect to the holdout: only pre-holdout rows are used, the lock
file written by ``run_unified_pipeline.py --stage dev`` is reused and never
rewritten, and no model artifact is modified. The numbers are diagnostic and do
not feed back into candidate selection.
"""

from __future__ import annotations

import argparse
import json
import sys

import pandas as pd

from pipeline.data_split import HOLDOUT_LOCK_FILENAME, build_validation_folds, split_and_lock_holdout
from pipeline.model_training import CANDIDATES, fit_candidate, predict_candidate
from pipeline.pipeline_config import add_pipeline_cli_args, config_from_args
from pipeline.run_unified_pipeline import load_featured_data
from pipeline.train_and_evaluate_model import TARGET_COLUMN, calculate_metrics

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8', errors='replace')

REFERENCE_CANDIDATES = ('median_baseline', 'official_current')
SUMMARY_COLUMNS = ['train_mae', 'validation_mae', 'mae_gap', 'mae_ratio', 'train_r2', 'validation_r2']


def fold_error_row(candidate: str, fold: int, train_metrics: dict, validation_metrics: dict,
                   train_rows: int, validation_rows: int) -> dict:
    return {
        'candidate': candidate,
        'fold': fold,
        'train_rows': train_rows,
        'validation_rows': validation_rows,
        'train_mae': train_metrics['mae'],
        'validation_mae': validation_metrics['mae'],
        'mae_gap': validation_metrics['mae'] - train_metrics['mae'],
        'mae_ratio': validation_metrics['mae'] / train_metrics['mae'],
        'train_r2': train_metrics['r2'],
        'validation_r2': validation_metrics['r2'],
    }


def summarize_fold_errors(fold_table: pd.DataFrame) -> pd.DataFrame:
    return fold_table.groupby('candidate', sort=False)[SUMMARY_COLUMNS].mean().reset_index()


def main() -> None:
    parser = argparse.ArgumentParser(description='Train vs validation MAE per walk-forward fold.')
    add_pipeline_cli_args(parser)
    config = config_from_args(parser.parse_args())

    lock_path = config.output_dir / HOLDOUT_LOCK_FILENAME
    manifest_path = config.output_dir / 'dev_manifest.json'
    if not lock_path.exists() or not manifest_path.exists():
        raise SystemExit(f'Run `run_unified_pipeline.py --stage dev` first: {lock_path} or {manifest_path} is missing.')
    selected_name = json.loads(manifest_path.read_text(encoding='utf-8'))['selected_candidate']

    data, _, _ = load_featured_data(config)
    split = split_and_lock_holdout(data, config)
    folds, _ = build_validation_folds(split.pre_holdout, split.holdout_start, config, window_types=('sliding',))

    rows = []
    for name in (*REFERENCE_CANDIDATES, selected_name):
        candidate = next(c for c in CANDIDATES if c.name == name)
        for fold in folds:
            fitted = fit_candidate(candidate, fold['train'], config.random_state)
            train_y = fold['train'][TARGET_COLUMN].to_numpy(dtype=float)
            validation_y = fold['validation'][TARGET_COLUMN].to_numpy(dtype=float)
            row = fold_error_row(
                name, fold['fold'],
                calculate_metrics(train_y, predict_candidate(fitted, fold['train'])),
                calculate_metrics(validation_y, predict_candidate(fitted, fold['validation'])),
                len(train_y), len(validation_y),
            )
            rows.append(row)
            print(
                f"{name} fold={row['fold']} train MAE={row['train_mae']:.3f} "
                f"validation MAE={row['validation_mae']:.3f}",
                flush=True,
            )

    fold_table = pd.DataFrame(rows)
    summary = summarize_fold_errors(fold_table)
    fold_table.to_csv(config.output_dir / 'train_vs_validation_mae_folds.csv', index=False, encoding='utf-8-sig')
    summary.to_csv(config.output_dir / 'train_vs_validation_mae_summary.csv', index=False, encoding='utf-8-sig')
    print(summary.round(3).to_string(index=False), flush=True)


if __name__ == '__main__':
    main()
