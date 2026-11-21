"""Check that web-app inference reproduces the training pipeline exactly.

1. Feature parity: features built by ``webapp.features`` (point queries on the
   queue state) versus ``pipeline.feature_engineering.engineer_features``
   (column-wise, as in training) on the real V3 cohort.
2. End-to-end parity: scoring the holdout with the *evaluated* bundle
   (``unified_model.joblib``) through the web-app path must reproduce
   ``holdout_patient_outputs.csv`` written by ``--stage final-eval``.

Usage: python -m webapp.verify_parity [--sample 20000]
"""

from __future__ import annotations

import argparse
import time

import numpy as np
import pandas as pd

from pipeline.feature_engineering import parse_time_of_day
from pipeline.pipeline_config import PROJECT_ROOT, PipelineConfig
from pipeline.run_unified_pipeline import load_featured_data
from webapp.config import CONFIG
from webapp.features import EventIndex, build_feature_frame
from webapp.history import load_history
from webapp.model_service import WaitTimeModel

QUERY_COLUMNS = ['nb_dich_vu_id', 'queue_entry_time', 'ma_dich_vu', 'ma_phong_thuc_hien', 'ma_khoa_thuc_hien',
                 'queue_entry_status', 'uu_tien', 'cap_cuu', 'patient_payment_type', 'stt', 'order_to_queue_minutes']


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--sample', type=int, default=20000, help='rows for the feature-parity check')
    args = parser.parse_args()
    config = PipelineConfig()
    model = WaitTimeModel(PROJECT_ROOT / 'results' / 'unified_pipeline_v3' / 'unified_model.joblib')
    history = load_history(CONFIG)
    cohort = history.cohort
    index = EventIndex.from_history(cohort, parse_time_of_day(config.lunch_start))

    print(f'[1/2] training feature path (run_unified_pipeline.load_featured_data) on {len(cohort):,} rows...',
          flush=True)
    started = time.time()
    training, _, _ = load_featured_data(config)
    print(f'      done in {time.time() - started:.0f}s', flush=True)

    sample = training.sample(n=min(args.sample, len(training)), random_state=0).reset_index(drop=True)
    # Queries come from the web app's own history loader, not from the training frame.
    queries = cohort.set_index('nb_dich_vu_id').loc[sample['nb_dich_vu_id']].reset_index()[QUERY_COLUMNS]
    web = build_feature_frame(queries, [index], config.lunch_start, config.lunch_history_window_days)
    failed = []
    for name in model.categorical:
        mismatches = int((sample[name].astype(str).to_numpy() != web[name].astype(str).to_numpy()).sum())
        print(f'      {name:40s} categorical mismatches={mismatches}')
        if mismatches:
            failed.append(name)
    for name in model.numeric:
        expected = sample[name].to_numpy(dtype=float)
        actual = web[name].to_numpy(dtype=float)
        nan_ok = bool((np.isnan(expected) == np.isnan(actual)).all())
        diff = float(np.nanmax(np.abs(expected - actual))) if np.any(~np.isnan(expected)) else 0.0
        print(f'      {name:40s} NaN-pattern {"OK" if nan_ok else "DIFF"}  max|diff|={diff:.2e}')
        if not nan_ok or diff > 1e-6:
            failed.append(name)
    print(f'      feature parity on {len(sample):,} rows: {"PASS" if not failed else "FAIL " + str(failed)}')

    print('[2/2] holdout end-to-end parity with the evaluated bundle...', flush=True)
    expected = pd.read_csv(CONFIG.holdout_outputs_path)
    holdout = cohort.loc[cohort['nb_dich_vu_id'].isin(expected['nb_dich_vu_id'])]
    features = build_feature_frame(holdout[QUERY_COLUMNS], [index], config.lunch_start,
                                   config.lunch_history_window_days)
    scored = model.predict(features).merge(expected, on='nb_dich_vu_id', suffixes=('', '_expected'))
    p50_diff = np.abs(scored['predicted_wait_minutes'] - scored['predicted_wait_minutes_expected']).max()
    p80_diff = np.abs(scored['predicted_wait_p80_minutes'] - scored['predicted_wait_p80_minutes_expected']).max()
    proba = scored['probability_cross_lunch'].to_numpy(dtype=float)
    proba_expected = scored['probability_cross_lunch_expected'].to_numpy(dtype=float)
    proba_ok = np.allclose(proba, proba_expected, equal_nan=True, atol=1e-9)
    rec_ok = bool((scored['recommendation'] == scored['recommendation_expected']).all())
    mae = np.abs(holdout.set_index('nb_dich_vu_id').loc[scored['nb_dich_vu_id'], 'waiting_minutes'].to_numpy()
                 - scored['predicted_wait_minutes'].to_numpy()).mean()
    print(f'      rows={len(scored):,}/{len(expected):,}  max|ΔP50|={p50_diff:.3f}  max|ΔP80|={p80_diff:.3f}  '
          f'P(lunch) equal={proba_ok}  recommendation equal={rec_ok}  MAE={mae:.2f}')
    ok = not failed and len(scored) == len(expected) and p50_diff < 0.051 and p80_diff < 0.051 and proba_ok and rec_ok
    print('RESULT:', 'PASS' if ok else 'FAIL')
    raise SystemExit(0 if ok else 1)


if __name__ == '__main__':
    main()
