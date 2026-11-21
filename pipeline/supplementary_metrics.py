"""Supplementary report metrics. No model is fitted and no artifact is modified.

1. PR-AUC / ROC-AUC (with bootstrap 95% CI) of the lunch-overflow classifier on
   the holdout, computed from the probabilities that ``--stage final-eval``
   already saved in ``holdout_patient_outputs.csv``. The numbers are descriptive
   and computed after the one-time holdout evaluation; they never feed back
   into model or threshold selection.
2. Spearman correlation between the numeric features and the wait target, and
   a correlation ratio (eta squared) for the categorical features, on the
   pre-holdout rows only (the holdout lock is read, never rewritten).

Run after the dev and final-eval stages:
    python -m pipeline.supplementary_metrics
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from pipeline.data_split import HOLDOUT_LOCK_FILENAME, split_and_lock_holdout
from pipeline.feature_engineering import feature_columns
from pipeline.pipeline_config import add_pipeline_cli_args, config_from_args
from pipeline.run_unified_pipeline import load_featured_data
from pipeline.train_and_evaluate_model import TARGET_COLUMN

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8', errors='replace')

FEATURE_SET = 'queue_dynamics_lunch'   # the V3 feature set (35 features)
BOOTSTRAP_RESAMPLES = 1000


def lunch_auc_report(y_true, probability, n_boot: int = BOOTSTRAP_RESAMPLES, seed: int = 42) -> dict:
    y = np.asarray(y_true, dtype=int)
    p = np.asarray(probability, dtype=float)
    rng = np.random.default_rng(seed)
    pr_values, roc_values = [], []
    for _ in range(n_boot):
        idx = rng.integers(0, len(y), len(y))
        if y[idx].min() == y[idx].max():
            continue
        pr_values.append(average_precision_score(y[idx], p[idx]))
        roc_values.append(roc_auc_score(y[idx], p[idx]))
    return {
        'rows': int(len(y)),
        'positives': int(y.sum()),
        'prevalence': float(y.mean()),
        'pr_auc': float(average_precision_score(y, p)),
        'pr_auc_ci95': [float(v) for v in np.percentile(pr_values, [2.5, 97.5])],
        'roc_auc': float(roc_auc_score(y, p)),
        'roc_auc_ci95': [float(v) for v in np.percentile(roc_values, [2.5, 97.5])],
        'bootstrap_resamples': int(len(pr_values)),
    }


def eta_squared(values: pd.Series, groups: pd.Series) -> float:
    """Share of the rank variance of ``values`` explained by ``groups`` (0..1)."""
    ranks = values.rank()
    grand_mean = ranks.mean()
    total = float(((ranks - grand_mean) ** 2).sum())
    stats = ranks.groupby(groups, observed=True).agg(['count', 'mean'])
    between = float((stats['count'] * (stats['mean'] - grand_mean) ** 2).sum())
    return between / total if total else float('nan')


def plot_heatmap(matrix: pd.DataFrame, path) -> None:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    labels = list(matrix.columns)
    fig, ax = plt.subplots(figsize=(6.4, 5.9))
    fig.subplots_adjust(left=0.33, right=0.86, top=0.98, bottom=0.30)
    image = ax.imshow(matrix.to_numpy(), cmap='RdBu_r', vmin=-1, vmax=1)
    ax.set_xticks(range(len(labels)), labels, rotation=90, fontsize=6.5)
    ax.set_yticks(range(len(labels)), labels, fontsize=6.5)
    fig.colorbar(image, ax=ax, fraction=0.046, pad=0.02).set_label('Hệ số tương quan Spearman', fontsize=7)
    fig.savefig(path, dpi=220, bbox_inches='tight')
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description='PR-AUC/ROC-AUC on the holdout and feature correlation (no model fitting).')
    add_pipeline_cli_args(parser)
    config = config_from_args(parser.parse_args())
    out = config.output_dir

    patient_path = out / 'holdout_patient_outputs.csv'
    if not (out / HOLDOUT_LOCK_FILENAME).exists() or not patient_path.exists():
        raise SystemExit(f'Run the dev and final-eval stages first: {patient_path} or the holdout lock is missing.')

    data, _, _ = load_featured_data(config)
    split = split_and_lock_holdout(data, config)

    # ---- 1. PR-AUC / ROC-AUC of the lunch-overflow classifier on the holdout
    saved = pd.read_csv(patient_path, encoding='utf-8-sig')[['nb_dich_vu_id', 'probability_cross_lunch']]
    holdout = split.holdout.merge(saved, on='nb_dich_vu_id', how='left')
    morning = holdout[holdout['registered_before_lunch']]
    if morning['probability_cross_lunch'].isna().any():
        raise SystemExit('Saved holdout outputs do not cover every morning holdout row.')
    report = lunch_auc_report(morning['crosses_lunch'], morning['probability_cross_lunch'])
    (out / 'holdout_lunch_auc.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)

    # ---- 2. feature correlation on pre-holdout rows
    categorical, numeric = feature_columns(FEATURE_SET)
    pre = split.pre_holdout
    matrix = pre[numeric + [TARGET_COLUMN]].astype(float).corr(method='spearman')
    matrix.to_csv(out / 'feature_correlation_spearman.csv', encoding='utf-8-sig')
    plot_heatmap(matrix, out / 'feature_correlation_heatmap.png')

    target = pre[TARGET_COLUMN].astype(float)
    association = [
        {'feature': name, 'kind': 'numeric', 'statistic': 'spearman_rho', 'value': matrix.loc[name, TARGET_COLUMN]}
        for name in numeric
    ] + [
        {'feature': name, 'kind': 'categorical', 'statistic': 'eta_squared',
         'value': eta_squared(target, pre[name].astype(str))}
        for name in categorical
    ]
    association = pd.DataFrame(association)
    association.to_csv(out / 'feature_target_association.csv', index=False, encoding='utf-8-sig')
    print(f'pre-holdout rows used: {len(pre):,}', flush=True)
    print(association.sort_values('value', key=abs, ascending=False).round(3).to_string(index=False), flush=True)

    features = matrix.loc[numeric, numeric].abs().where(np.triu(np.ones((len(numeric),) * 2, dtype=bool), k=1))
    pairs = features.stack().sort_values(ascending=False)
    print('pairs with |rho| >= 0.8:', flush=True)
    print(pairs[pairs >= 0.8].round(2).to_string(), flush=True)


if __name__ == '__main__':
    main()
