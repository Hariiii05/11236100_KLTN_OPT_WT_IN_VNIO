"""Unified waiting-time pipeline: cohort -> features -> walk-forward
hyperparameter search -> diagnosis -> calibration -> lunch-break warning ->
single-use holdout evaluation -> refit on the latest data.

Replaces the separate ``optimize_on_development`` / ``diagnose_and_calibrate``
scripts with one reproducible flow driven by :class:`PipelineConfig`. It reads
the existing audit and diagnosis results as its starting point (queue-dynamics
features help, extra service-duration/doctor features do not -- see
docs/model_diagnosis_and_calibration.md) rather than re-deriving them.

Stages, matching the safety pattern already used by
``train_and_evaluate_model.py``:

``--stage dev`` (freely rerunnable): loads the audited model dataset, applies
the modeling-cohort rules (see ``cohort.py``), engineers features once on the
whole filtered timeline, splits by time into pre-holdout and a holdout that is
locked immediately, runs the walk-forward candidate search, diagnoses errors,
fits the lunch-break classifier and threshold on validation, retrains the
final artifacts on data before the holdout and calibrates them, and writes a
single bundle (``unified_model.joblib``).

``--stage final-eval`` (single-use, locked by HOLDOUT_EVALUATED.json):
reloads the dev-stage bundle, scores the holdout exactly once, and writes the
final metrics, segment tables, lunch-warning precision/recall/F1, per-patient
outputs, and a same-rows comparison against the frozen V1 and V2 models.

``--stage refit-final`` (after final-eval only): refits the frozen
configuration on the most recent data, holdout period included, and writes
``unified_model_latest.joblib`` -- the up-to-date deployable model. It has no
independent evaluation; the holdout metrics belong to the model evaluated in
``final-eval``.

``--stage all`` (default) runs all three in one command for a first,
from-scratch reproduction.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any

import joblib
import numpy as np
import pandas as pd

# The project path itself contains Vietnamese diacritics (e.g. "Khoa luan"),
# which Windows' default console codepage (cp1252) cannot encode. Reconfigure
# stdout/stderr to UTF-8 so any printed path or Vietnamese text (recommendation
# labels, etc.) does not crash a redirected/non-interactive run.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8', errors='replace')

from pipeline.cohort import apply_cohort_rules, build_attrition_table, cohort_rules
from pipeline.data_split import (
    HOLDOUT_EVALUATED_FILENAME,
    build_validation_folds,
    check_holdout_not_evaluated,
    lock_holdout_evaluated,
    split_and_lock_holdout,
)
from pipeline.diagnostics import (
    TAIL_MINUTES,
    drift_slope_per_fold,
    extended_feature_audit,
    segment_table,
    segments_of,
    tail_summary,
)
from pipeline.feature_engineering import engineer_features, feature_columns
from pipeline.lunch_warning import (
    build_patient_output,
    classification_report_at_threshold,
    fit_lunch_classifier,
    out_of_fold_lunch_predictions,
    predict_lunch_probability,
    select_threshold,
)
from pipeline.model_training import (
    CANDIDATES,
    apply_group_conformal_offsets,
    final_training_window,
    fit_group_conformal_offsets,
    fit_quantile_models,
    pooled_out_of_fold_quantile_predictions,
    predict_quantile_models,
    run_walk_forward_search,
    summarize_candidates,
)
from pipeline.pipeline_config import PROJECT_ROOT, PipelineConfig, add_pipeline_cli_args, config_from_args
from pipeline.train_and_evaluate_model import (
    TARGET_COLUMN,
    TIME_COLUMN,
    calculate_metrics,
    dataset_sha256,
    json_dump,
    load_dataset,
    predict_model as predict_official_model,
)

BUNDLE_FILENAME = 'unified_model.joblib'
LATEST_BUNDLE_FILENAME = 'unified_model_latest.joblib'
OLD_MODEL_PATH = PROJECT_ROOT / 'results' / 'final_model' / 'final_model.joblib'
OLD_LOCK_PATH = PROJECT_ROOT / 'results' / 'final_model' / 'FINAL_TEST_EVALUATED.json'
V2_BUNDLE_PATH = PROJECT_ROOT / 'results' / 'unified_pipeline' / BUNDLE_FILENAME


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Unified waiting-time pipeline (cohort, features, CV, diagnosis, calibration, lunch warning).'
    )
    parser.add_argument('--stage', choices=['dev', 'final-eval', 'refit-final', 'all'], default='all')
    add_pipeline_cli_args(parser)
    return parser.parse_args()


def load_featured_data(config: PipelineConfig):
    """Audited dataset -> cohort rules -> causal features, computed once on the filtered timeline."""
    raw = load_dataset(config.input)
    cohort = apply_cohort_rules(raw, config)
    input_times = raw[TIME_COLUMN].copy()
    del raw
    data = engineer_features(cohort.kept, config)
    return data, cohort, input_times


def subgroup_errors(y: np.ndarray, predicted: np.ndarray, crosses_lunch: np.ndarray) -> dict[str, dict[str, float]]:
    error = np.asarray(predicted, dtype=float) - y
    abs_error = np.abs(error)
    tail = y > TAIL_MINUTES
    result: dict[str, dict[str, float]] = {}
    for name, mask in (('crosses_lunch', crosses_lunch), ('tail_gt_120', tail), ('non_tail_le_120', ~tail)):
        result[name] = {
            'rows': int(mask.sum()),
            'mae': float(abs_error[mask].mean()) if mask.any() else float('nan'),
            'bias': float(error[mask].mean()) if mask.any() else float('nan'),
        }
    return result


def run_dev_stage(config: PipelineConfig) -> dict[str, Any]:
    config.output_dir.mkdir(parents=True, exist_ok=True)

    print('[dev] loading dataset, applying cohort rules, engineering features...', flush=True)
    data, cohort, input_times = load_featured_data(config)

    split = split_and_lock_holdout(data, config)
    attrition = build_attrition_table(input_times, cohort, split.holdout_start, config)
    attrition.to_csv(config.output_dir / 'cohort_attrition.csv', index=False, encoding='utf-8-sig')
    print(attrition.to_string(index=False), flush=True)
    holdout_days_with_data = int(split.holdout[TIME_COLUMN].dt.normalize().nunique())
    print(
        f'[dev] holdout locked at {split.holdout_start} '
        f'({len(split.pre_holdout):,} pre-holdout rows, {len(split.holdout):,} holdout rows reserved, '
        f'{holdout_days_with_data} calendar days with data)',
        flush=True,
    )

    fold_specs, boundaries = build_validation_folds(
        split.pre_holdout, split.holdout_start, config, window_types=('sliding',)
    )
    boundaries.to_csv(config.output_dir / 'fold_boundaries.csv', index=False, encoding='utf-8-sig')

    print('[dev] walk-forward candidate search (sliding window, time-based CV)...', flush=True)
    fold_metrics = run_walk_forward_search(fold_specs, CANDIDATES, config.random_state)

    provisional_best_name = summarize_candidates(fold_metrics).iloc[0]['candidate']
    provisional_best = next(c for c in CANDIDATES if c.name == provisional_best_name)
    print(f'[dev] confirmatory check: {provisional_best_name} on expanding window...', flush=True)
    expanding_folds, _ = build_validation_folds(
        split.pre_holdout, split.holdout_start, config, window_types=('expanding',)
    )
    expanding_metrics = run_walk_forward_search(expanding_folds, [provisional_best], config.random_state)
    fold_metrics = pd.concat([fold_metrics, expanding_metrics], ignore_index=True)

    summary = summarize_candidates(fold_metrics)
    fold_metrics.to_csv(config.output_dir / 'candidate_fold_metrics.csv', index=False, encoding='utf-8-sig')
    summary.to_csv(config.output_dir / 'candidate_summary.csv', index=False, encoding='utf-8-sig')

    selected_row = summary.loc[summary['window_type'].eq('sliding')].iloc[0]
    selected_name = str(selected_row['candidate'])
    selected_candidate = next(c for c in CANDIDATES if c.name == selected_name)
    print(
        f"[dev] selected candidate: {selected_name} (sliding window, "
        f"MAE={selected_row['mae_mean']:.3f}+/-{selected_row['mae_std']:.3f})",
        flush=True,
    )

    print('[dev] diagnosis: pooled out-of-fold validation predictions (before vs after)...', flush=True)
    baseline_candidate = next(c for c in CANDIDATES if c.name == 'official_current')
    before_frame = pooled_out_of_fold_quantile_predictions(baseline_candidate, fold_specs, config.random_state)
    after_frame = pooled_out_of_fold_quantile_predictions(selected_candidate, fold_specs, config.random_state)

    segment_table(before_frame, segments_of(before_frame)).assign(model='before_official_current').to_csv(
        config.output_dir / 'segment_diagnostics_before.csv', index=False, encoding='utf-8-sig'
    )
    segment_table(after_frame, segments_of(after_frame)).assign(model=f'after_{selected_name}').to_csv(
        config.output_dir / 'segment_diagnostics_after.csv', index=False, encoding='utf-8-sig'
    )

    def _lunch_subgroup_metrics(frame: pd.DataFrame) -> dict[str, float]:
        mask = frame['crosses_lunch'].to_numpy(dtype=bool)
        if not mask.any():
            return {'crosses_lunch_mae': float('nan'), 'crosses_lunch_bias': float('nan'), 'crosses_lunch_rows': 0}
        return {
            'crosses_lunch_mae': float(frame.loc[mask, 'abs_error'].mean()),
            'crosses_lunch_bias': float(frame.loc[mask, 'error'].mean()),
            'crosses_lunch_rows': int(mask.sum()),
        }

    tail_before = {**tail_summary(before_frame['y'].to_numpy(), before_frame['p50'].to_numpy()),
                   **_lunch_subgroup_metrics(before_frame)}
    tail_after = {**tail_summary(after_frame['y'].to_numpy(), after_frame['p50'].to_numpy()),
                  **_lunch_subgroup_metrics(after_frame)}
    json_dump(
        config.output_dir / 'tail_and_lunch_before_after.json',
        {'before_official_current': tail_before, f'after_{selected_name}': tail_after},
    )

    drift = {
        'before_official_current': drift_slope_per_fold(
            fold_metrics.loc[fold_metrics['candidate'].eq('official_current') & fold_metrics['window_type'].eq('sliding')]
        ),
        f'after_{selected_name}': drift_slope_per_fold(
            fold_metrics.loc[fold_metrics['candidate'].eq(selected_name) & fold_metrics['window_type'].eq('sliding')]
        ),
    }
    extended_feature_audit().to_csv(config.output_dir / 'feature_audit.csv', index=False, encoding='utf-8-sig')

    print('[dev] lunch-break classifier: out-of-fold validation + threshold selection...', flush=True)
    oof_lunch = out_of_fold_lunch_predictions(fold_specs, config.random_state)
    y_lunch = oof_lunch['crosses_lunch'].to_numpy(dtype=int)
    proba_lunch = oof_lunch['lunch_probability'].to_numpy()
    threshold_info = select_threshold(y_lunch, proba_lunch, config.lunch_threshold_fbeta)
    validation_lunch_report = classification_report_at_threshold(y_lunch, proba_lunch, threshold_info['threshold'])
    json_dump(
        config.output_dir / 'lunch_threshold_and_validation_report.json',
        {
            'threshold_selection': threshold_info,
            'validation_report': validation_lunch_report,
            'base_rate_positive': float(np.mean(y_lunch)),
            'morning_validation_rows': int(len(oof_lunch)),
            'fbeta_used': config.lunch_threshold_fbeta,
        },
    )

    print('[dev] final retrain (fit window) + split-conformal calibration (calibration window)...', flush=True)
    fit_rows, calibration_rows, purged = final_training_window(split.pre_holdout, split.holdout_start, config)
    categorical, numeric = feature_columns(selected_candidate.feature_set)
    final_quantile_models = fit_quantile_models(
        categorical, numeric, fit_rows, selected_candidate.params, config.random_state
    )
    calibration_predictions = predict_quantile_models(final_quantile_models, categorical, numeric, calibration_rows)
    calibration_offsets = fit_group_conformal_offsets(
        calibration_rows, calibration_predictions, config.calibration_group_column, config.calibration_min_group_rows
    )
    json_dump(config.output_dir / 'calibration_offsets.json', calibration_offsets)

    final_lunch_classifier = fit_lunch_classifier(fit_rows, config.random_state)

    bundle = {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'dataset_sha256': dataset_sha256(config.input),
        'cohort_rules': cohort_rules(config),
        'holdout_start': str(split.holdout_start),
        'selected_candidate': {
            'name': selected_candidate.name,
            'feature_set': selected_candidate.feature_set,
            'params': selected_candidate.params,
        },
        'quantile_models': final_quantile_models,
        'quantile_categorical': categorical,
        'quantile_numeric': numeric,
        'calibration_offsets': calibration_offsets,
        'calibration_group_column': config.calibration_group_column,
        'lunch_classifier': final_lunch_classifier,
        'lunch_threshold': threshold_info['threshold'],
        'lunch_start': config.lunch_start,
        'lunch_end': config.lunch_end,
    }
    joblib.dump(bundle, config.output_dir / BUNDLE_FILENAME)

    dev_manifest = {
        'completed_at_utc': datetime.now(timezone.utc).isoformat(),
        'dataset_sha256': dataset_sha256(config.input),
        'cohort_rules': cohort_rules(config),
        'cohort_attrition': attrition.to_dict(orient='records'),
        'holdout_start': str(split.holdout_start),
        'holdout_calendar_days_with_data': holdout_days_with_data,
        'pre_holdout_rows': int(len(split.pre_holdout)),
        'holdout_rows_reserved': int(len(split.holdout)),
        'selected_candidate': selected_name,
        'selected_feature_set': selected_candidate.feature_set,
        'selected_params': selected_candidate.params,
        'selected_validation_metrics': selected_row.to_dict(),
        'window_confirmation': summary.loc[summary['candidate'].eq(selected_name), ['window_type', 'mae_mean', 'mae_std']]
        .to_dict(orient='records'),
        'tail_and_lunch_before_after': {'before_official_current': tail_before, f'after_{selected_name}': tail_after},
        'drift_mae_slope_per_fold': drift,
        'lunch_threshold': threshold_info,
        'lunch_validation_report': validation_lunch_report,
        'purged_final_training_rows': int(purged),
        'config': {
            'holdout_start': config.holdout_start,
            'holdout_days': config.holdout_days,
            'validation_days': config.validation_days,
            'n_folds': config.n_folds,
            'sliding_train_days': config.sliding_train_days,
            'calibration_days': config.calibration_days,
            'lunch_start': config.lunch_start,
            'lunch_end': config.lunch_end,
            'lunch_history_window_days': config.lunch_history_window_days,
            'lunch_threshold_fbeta': config.lunch_threshold_fbeta,
            'calibration_group_column': config.calibration_group_column,
            'calibration_min_group_rows': config.calibration_min_group_rows,
            'random_state': config.random_state,
        },
    }
    json_dump(config.output_dir / 'dev_manifest.json', dev_manifest)
    print(f'[dev] done. Artifacts in {config.output_dir}', flush=True)
    return dev_manifest


def score_frozen_v2(holdout: pd.DataFrame) -> dict[str, Any] | None:
    """Score the frozen V2 bundle (trained with KB008 in its data) on the same holdout rows."""
    if not V2_BUNDLE_PATH.exists():
        return None
    v2 = joblib.load(V2_BUNDLE_PATH)
    raw = predict_quantile_models(v2['quantile_models'], v2['quantile_categorical'], v2['quantile_numeric'], holdout)
    calibrated = apply_group_conformal_offsets(holdout, raw, v2['calibration_group_column'], v2['calibration_offsets'])
    return {
        'p50': calibrated[0.5],
        'lunch_proba': predict_lunch_probability(v2['lunch_classifier'], holdout),
        'lunch_threshold': v2['lunch_threshold'],
    }


def evaluate_frozen_bundle(config: PipelineConfig, bundle: dict[str, Any], holdout: pd.DataFrame) -> dict[str, Any]:
    """Score a holdout-like frame with a frozen bundle, alongside the frozen V1 and V2 models.

    Pure: writes nothing. Kept separate from ``run_final_eval_stage`` so the exact
    scoring code can be smoke-tested on a validation slice before the single-use
    holdout evaluation.
    """
    categorical, numeric = bundle['quantile_categorical'], bundle['quantile_numeric']
    raw_predictions = predict_quantile_models(bundle['quantile_models'], categorical, numeric, holdout)
    calibrated_predictions = apply_group_conformal_offsets(
        holdout, raw_predictions, bundle['calibration_group_column'], bundle['calibration_offsets']
    )
    lunch_proba_all = predict_lunch_probability(bundle['lunch_classifier'], holdout)
    patient_output = build_patient_output(
        holdout, calibrated_predictions[0.5], calibrated_predictions[0.8], lunch_proba_all, bundle['lunch_threshold'], config
    )

    y = holdout[TARGET_COLUMN].to_numpy(dtype=float)
    overall_raw = calculate_metrics(y, raw_predictions[0.5])
    overall_calibrated = calculate_metrics(y, calibrated_predictions[0.5])
    coverage_raw = {
        'p50': float(np.mean(y <= raw_predictions[0.5])),
        'p80': float(np.mean(y <= raw_predictions[0.8])),
    }
    coverage_calibrated = {
        'p50': float(np.mean(y <= calibrated_predictions[0.5])),
        'p80': float(np.mean(y <= calibrated_predictions[0.8])),
    }

    frame = holdout.assign(y=y, p50=calibrated_predictions[0.5], p80=calibrated_predictions[0.8])
    frame['error'] = frame['p50'] - frame['y']
    frame['abs_error'] = frame['error'].abs()
    segments = segment_table(frame, segments_of(frame))

    morning_mask = holdout['registered_before_lunch'].to_numpy(dtype=bool)
    crosses_lunch = holdout['crosses_lunch'].to_numpy(dtype=bool)
    lunch_report = classification_report_at_threshold(
        crosses_lunch[morning_mask].astype(int), lunch_proba_all[morning_mask], bundle['lunch_threshold']
    )

    evaluated_on = 'v3_cohort_holdout'
    comparison_rows: list[dict[str, Any]] = []
    subgroups = {'v3_calibrated': subgroup_errors(y, calibrated_predictions[0.5], crosses_lunch)}
    if OLD_MODEL_PATH.exists():
        old_artifact = joblib.load(OLD_MODEL_PATH)
        v1_predicted = predict_official_model(old_artifact['fitted_model'], holdout)
        comparison_rows.append(
            {'model': 'v1_official_frozen', 'evaluated_on': evaluated_on, 'rows': len(holdout),
             **calculate_metrics(y, v1_predicted)}
        )
        subgroups['v1_frozen'] = subgroup_errors(y, v1_predicted, crosses_lunch)
    if OLD_LOCK_PATH.exists():
        with OLD_LOCK_PATH.open(encoding='utf-8') as stream:
            old_lock = json.load(stream)
        comparison_rows.append({
            'model': 'v1_official_frozen',
            'evaluated_on': f"old_final_test_{old_lock['test_start']}_to_{old_lock['test_end']} (different cohort)",
            'rows': old_lock['test_rows'],
            **old_lock['overall_metrics'],
        })
    v2_scored = score_frozen_v2(holdout)
    v2_lunch_report = None
    if v2_scored is not None:
        comparison_rows.append(
            {'model': 'v2_unified_frozen_calibrated', 'evaluated_on': evaluated_on, 'rows': len(holdout),
             **calculate_metrics(y, v2_scored['p50'])}
        )
        subgroups['v2_frozen'] = subgroup_errors(y, v2_scored['p50'], crosses_lunch)
        v2_lunch_report = classification_report_at_threshold(
            crosses_lunch[morning_mask].astype(int), v2_scored['lunch_proba'][morning_mask], v2_scored['lunch_threshold']
        )
    comparison_rows.append(
        {'model': 'v3_unified_raw', 'evaluated_on': evaluated_on, 'rows': len(holdout), **overall_raw}
    )
    comparison_rows.append(
        {'model': 'v3_unified_calibrated', 'evaluated_on': evaluated_on, 'rows': len(holdout), **overall_calibrated}
    )
    return {
        'patient_output': patient_output,
        'segment_table': segments,
        'comparison': pd.DataFrame(comparison_rows),
        'summary': {
            'overall_metrics_raw': overall_raw,
            'overall_metrics_calibrated': overall_calibrated,
            'coverage_raw': coverage_raw,
            'coverage_calibrated': coverage_calibrated,
            'tail_summary_calibrated': tail_summary(y, calibrated_predictions[0.5]),
            'subgroup_errors': subgroups,
            'lunch_warning_holdout_report': lunch_report,
            'lunch_warning_v2_frozen_same_rows': v2_lunch_report,
        },
    }


def run_final_eval_stage(config: PipelineConfig) -> dict[str, Any]:
    check_holdout_not_evaluated(config.output_dir)
    bundle_path = config.output_dir / BUNDLE_FILENAME
    manifest_path = config.output_dir / 'dev_manifest.json'
    if not bundle_path.exists() or not manifest_path.exists():
        raise FileNotFoundError('Run --stage dev before final-eval.')
    bundle = joblib.load(bundle_path)
    current_hash = dataset_sha256(config.input)
    if current_hash != bundle['dataset_sha256']:
        raise RuntimeError('Input dataset changed after the dev stage. Re-run --stage dev before final-eval.')
    if bundle['cohort_rules'] != cohort_rules(config):
        raise RuntimeError('Cohort rules changed after the dev stage. Re-run --stage dev before final-eval.')

    print('[final-eval] loading dataset, applying cohort rules, engineering features...', flush=True)
    data, _, _ = load_featured_data(config)
    split = split_and_lock_holdout(data, config)
    if str(split.holdout_start) != bundle['holdout_start']:
        raise AssertionError('Holdout boundary mismatch between the dev bundle and the current lock file.')
    holdout = split.holdout

    print(f'[final-eval] evaluating {len(holdout):,} holdout rows exactly once...', flush=True)
    scored = evaluate_frozen_bundle(config, bundle, holdout)
    scored['patient_output'].to_csv(config.output_dir / 'holdout_patient_outputs.csv', index=False, encoding='utf-8-sig')
    scored['segment_table'].to_csv(config.output_dir / 'holdout_segment_diagnostics.csv', index=False, encoding='utf-8-sig')
    scored['comparison'].to_csv(config.output_dir / 'old_vs_new_comparison.csv', index=False, encoding='utf-8-sig')

    final_summary = {
        'evaluated_at_utc': datetime.now(timezone.utc).isoformat(),
        'dataset_sha256': current_hash,
        'cohort_rules': cohort_rules(config),
        'holdout_start': str(split.holdout_start),
        'holdout_end': str(holdout[TIME_COLUMN].max()),
        'holdout_calendar_days_with_data': int(holdout[TIME_COLUMN].dt.normalize().nunique()),
        'holdout_rows': int(len(holdout)),
        'selected_candidate': bundle['selected_candidate'],
        **scored['summary'],
        'lunch_threshold': bundle['lunch_threshold'],
        'warning': (
            'Holdout has been evaluated. Do not use these results for further '
            'feature, model, or hyperparameter selection.'
        ),
    }
    lock_holdout_evaluated(config.output_dir, final_summary)
    json_dump(config.output_dir / 'holdout_final_metrics.json', final_summary)
    print(json.dumps(final_summary, ensure_ascii=False, indent=2, default=str))
    return final_summary


def run_refit_final_stage(config: PipelineConfig) -> dict[str, Any]:
    """Refit the frozen recipe on the latest data (holdout period included).

    Only allowed after the single holdout evaluation is locked: the holdout
    rows are the most recent data, so a deployable model should learn from
    them, but doing so before the evaluation would contaminate it. No
    hyperparameter, feature or threshold is re-chosen here.
    """
    if not (config.output_dir / HOLDOUT_EVALUATED_FILENAME).exists():
        raise RuntimeError('Run --stage final-eval first: the refit uses holdout rows and must follow the evaluation.')
    bundle = joblib.load(config.output_dir / BUNDLE_FILENAME)
    if bundle['cohort_rules'] != cohort_rules(config):
        raise RuntimeError('Cohort rules changed after the dev stage; the frozen recipe no longer applies.')

    print('[refit-final] loading dataset, applying cohort rules, engineering features...', flush=True)
    data, _, _ = load_featured_data(config)
    refit_end = data[TIME_COLUMN].max().normalize() + pd.Timedelta(1, unit='D')
    fit_rows, calibration_rows, purged = final_training_window(data, refit_end, config)

    selected = bundle['selected_candidate']
    categorical, numeric = feature_columns(selected['feature_set'])
    models = fit_quantile_models(categorical, numeric, fit_rows, selected['params'], config.random_state)
    calibration_predictions = predict_quantile_models(models, categorical, numeric, calibration_rows)
    offsets = fit_group_conformal_offsets(
        calibration_rows, calibration_predictions, config.calibration_group_column, config.calibration_min_group_rows
    )
    lunch_classifier = fit_lunch_classifier(fit_rows, config.random_state)

    latest = {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'dataset_sha256': dataset_sha256(config.input),
        'cohort_rules': cohort_rules(config),
        'trained_through_exclusive': str(refit_end),
        'selected_candidate': selected,
        'quantile_models': models,
        'quantile_categorical': categorical,
        'quantile_numeric': numeric,
        'calibration_offsets': offsets,
        'calibration_group_column': config.calibration_group_column,
        'lunch_classifier': lunch_classifier,
        'lunch_threshold': bundle['lunch_threshold'],
        'lunch_start': config.lunch_start,
        'lunch_end': config.lunch_end,
    }
    joblib.dump(latest, config.output_dir / LATEST_BUNDLE_FILENAME)

    manifest = {
        'created_at_utc': latest['created_at_utc'],
        'purpose': 'Deployable model refit on the latest data with the frozen recipe (no re-selection).',
        'trained_through_exclusive': str(refit_end),
        'last_row_time': str(data[TIME_COLUMN].max()),
        'fit_window': [
            str(refit_end - pd.Timedelta(config.sliding_train_days, unit='D')),
            str(refit_end - pd.Timedelta(config.calibration_days, unit='D')),
        ],
        'calibration_window': [str(refit_end - pd.Timedelta(config.calibration_days, unit='D')), str(refit_end)],
        'fit_rows': int(len(fit_rows)),
        'calibration_rows': int(len(calibration_rows)),
        'purged_fit_rows': int(purged),
        'selected_candidate': selected,
        'lunch_threshold_frozen_from_dev': bundle['lunch_threshold'],
        'evaluation_note': (
            'No independent evaluation exists for this refit. The reported holdout metrics belong to '
            f'{BUNDLE_FILENAME}, trained only on data before the holdout.'
        ),
    }
    json_dump(config.output_dir / 'refit_manifest.json', manifest)
    print(f'[refit-final] done: {LATEST_BUNDLE_FILENAME} trained through {refit_end}', flush=True)
    return manifest


def main() -> None:
    args = parse_args()
    config = config_from_args(args)
    if args.stage == 'all':
        check_holdout_not_evaluated(config.output_dir)
    if args.stage in ('dev', 'all'):
        run_dev_stage(config)
    if args.stage in ('final-eval', 'all'):
        run_final_eval_stage(config)
    if args.stage in ('refit-final', 'all'):
        run_refit_final_stage(config)


if __name__ == '__main__':
    main()
