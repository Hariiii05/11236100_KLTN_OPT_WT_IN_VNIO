"""Diagnose weaknesses of the waiting-time model and calibrate P50/P80 forecasts.

Development-only (rows before ``--test-start``); the locked Final Test is never read.

Scenario
1. Diagnose  - residuals of the official feature set by segment (status, wait bucket,
               queue load, hour, room), plus a constant-median reference.
2. Features  - causal service-duration and queue-state features built from ALL exam
               events (also cases whose wait target is invalid): P50/P75 duration by
               room/service/doctor roster, active accounts, in-exam cases and their
               elapsed time, remaining-time estimate, starts/completions in 15/30/60m.
               Duration is measured 60->150 (business rule) and, as a robustness check,
               60->140.
3. Ablation  - feature ladder, each rung fit twice (P50 absolute loss, P80 quantile).
4. Calibrate - split-conformal offset learned on the last days of each training window.

Every feature only uses events strictly before ``queue_entry_time``.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Any

os.environ.setdefault('LOKY_MAX_CPU_COUNT', '1')

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder

from pipeline.optimize_on_development import (
    DEEPER_PARAMS,
    DYNAMICS_FEATURES,
    QUEUE_FEATURES,
    add_causal_features,
)
from pipeline.prepare_event_log import read_extract
from pipeline.train_and_evaluate_model import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    PROJECT_ROOT,
    TARGET_COLUMN,
    TIME_COLUMN,
    calculate_metrics,
    dataset_sha256,
    load_dataset,
    make_folds,
)

MINUTE_NS = 60_000_000_000
DAY_NS = 24 * 60 * MINUTE_NS
NO_END = np.iinfo(np.int64).max  # case still open (no end status yet)
IN_EXAM_CAP_MIN = 240  # older open cases are treated as never-closed records, not as live exams
END_COLUMNS = {'e150_': 'exam_complete_time', 'e140_': 'conclusion_start_time'}


# --------------------------------------------------------------------------- events

def load_events(root: Path, test_start: pd.Timestamp) -> pd.DataFrame:
    """One row per exam service: start (60), conclusion start (140), close (150), doctor, account."""
    cols = ['nb_dich_vu_id', 'ma_dich_vu', 'ma_phong_thuc_hien', 'bac_si_kham_id',
            'exam_start_time', 'exam_complete_time']
    events = pd.read_csv(
        root / 'data' / 'processed' / 'exam_wait_target_all.csv',
        usecols=cols, parse_dates=['exam_start_time', 'exam_complete_time'], low_memory=False,
    )
    log, _ = read_extract(
        root / 'data' / 'intermediate' / '05_exam_status_log.csv',
        usecols=['nb_dich_vu_id', 'thoi_gian_thay_doi_ban_ghi', 'trang_thai', 'tai_khoan'],
    )
    log = log.rename(columns={'thoi_gian_thay_doi_ban_ghi': 'time'})
    first60 = (log.loc[log['trang_thai'].eq(60)].sort_values('time')
               .drop_duplicates('nb_dich_vu_id')[['nb_dich_vu_id', 'time', 'tai_khoan']]
               .rename(columns={'time': 'time60'}))
    first140 = (log.loc[log['trang_thai'].eq(140)].merge(first60[['nb_dich_vu_id', 'time60']])
                .query('time >= time60').sort_values('time')
                .drop_duplicates('nb_dich_vu_id')[['nb_dich_vu_id', 'time']]
                .rename(columns={'time': 'conclusion_start_time'}))
    events = (events.merge(first60[['nb_dich_vu_id', 'tai_khoan']], how='left')
              .merge(first140, how='left'))
    events = events.loc[events['exam_start_time'].notna() & events['exam_start_time'].lt(test_start)]
    return events.reset_index(drop=True)


def ns(series: pd.Series, missing: int) -> np.ndarray:
    values = series.astype('int64').to_numpy().copy()
    values[series.isna().to_numpy()] = missing
    return values


# ------------------------------------------------------------- rolling quantiles

def rolling_quantiles(q_key, q_ns, e_key, e_end_ns, e_dur, k=200, days=28) -> np.ndarray:
    """P50, P75 and count of the last ``k`` durations that ended strictly before each query.

    Only durations ended within ``days`` count; fewer than 5 observations gives NaN quantiles.
    """
    out = np.full((len(q_ns), 3), np.nan)
    out[:, 2] = 0
    window = int(pd.Timedelta(days, unit='D').value)
    e_groups = pd.Series(np.arange(len(e_key))).groupby(np.asarray(e_key)).indices
    q_groups = pd.Series(np.arange(len(q_key))).groupby(np.asarray(q_key)).indices
    for key, q_pos in q_groups.items():
        e_pos = e_groups.get(key)
        if e_pos is None:
            continue
        order = np.argsort(e_end_ns[e_pos], kind='stable')
        ends, durations = e_end_ns[e_pos][order], e_dur[e_pos][order]
        times = q_ns[q_pos]
        hi = np.searchsorted(ends, times, side='left')
        lo = np.maximum(hi - k, np.searchsorted(ends, times - window, side='left'))
        for position, left, right in zip(q_pos, lo, hi):
            out[position, 2] = right - left
            if right - left >= 5:
                out[position, :2] = np.percentile(durations[left:right], [50, 75])
    return out


# ------------------------------------------------------------------ room state

def room_state(data: pd.DataFrame, events: pd.DataFrame, prefix: str,
               room_p50: np.ndarray) -> pd.DataFrame:
    """Queue state of each query's room from events strictly before the query time."""
    end_col = END_COLUMNS[prefix]
    q_t = data[TIME_COLUMN].astype('int64').to_numpy()
    codes, _ = pd.factorize(pd.concat([data['ma_phong_thuc_hien'], events['ma_phong_thuc_hien']]))
    q_room, e_room = codes[:len(data)], codes[len(data):]
    start, end = ns(events['exam_start_time'], NO_END), ns(events[end_col], NO_END)
    duration = np.where(end < NO_END, (end - start) / MINUTE_NS, np.nan)
    duration[duration < 0] = np.nan
    doctor = pd.factorize(events['bac_si_kham_id'])[0]
    account = pd.factorize(events['tai_khoan'])[0]

    # rolling doctor speed, attached to each completion as of its own end time
    done = np.flatnonzero(np.isfinite(duration) & (doctor >= 0))
    doctor_stats = np.full((len(events), 2), np.nan)
    doctor_stats[done] = rolling_quantiles(
        doctor[done], end[done] + 1, doctor[done], end[done], duration[done], k=100, days=42
    )[:, :2]

    names = ['starts_15m', 'starts_30m', 'starts_60m', 'accounts_30m', 'accounts_60m',
             'start_gap_median20', 'in_exam_n', 'in_exam_mean_elapsed', 'in_exam_max_elapsed',
             'in_exam_remaining_sum', 'completed_15m', 'completed_30m', 'completed_60m',
             'roster_size', 'roster_p50', 'roster_p75', 'starts_today', 'minutes_since_room_open']
    out = np.full((len(data), len(names)), np.nan)
    out[:, [0, 1, 2, 3, 4, 6, 10, 11, 12, 13, 16]] = 0
    e_groups = pd.Series(np.arange(len(events))).groupby(e_room).indices
    q_groups = pd.Series(np.arange(len(data))).groupby(q_room).indices
    for room, q_pos in q_groups.items():
        e_pos = e_groups.get(room)
        if e_pos is None:
            continue
        order = np.argsort(start[e_pos], kind='stable')
        e_pos = e_pos[order]
        s, e, acc = start[e_pos], end[e_pos], account[e_pos]
        finished = np.flatnonzero(e < NO_END)
        finished = finished[np.argsort(e[finished], kind='stable')]
        ce, cdoc = e[finished], doctor[e_pos][finished]
        cstats = doctor_stats[e_pos][finished]
        for position in q_pos:
            t = q_t[position]
            hi = np.searchsorted(s, t, side='left')
            row = out[position]
            for column, minutes in ((0, 15), (1, 30), (2, 60)):
                row[column] = hi - np.searchsorted(s, t - minutes * MINUTE_NS, side='left')
            for column, minutes in ((3, 30), (4, 60)):
                lo = np.searchsorted(s, t - minutes * MINUTE_NS, side='left')
                row[column] = len(np.unique(acc[lo:hi][acc[lo:hi] >= 0]))
            if hi >= 6:
                row[5] = np.median(np.diff(s[max(0, hi - 21):hi])) / MINUTE_NS
            lo = np.searchsorted(s, t - IN_EXAM_CAP_MIN * MINUTE_NS, side='left')
            open_start = s[lo:hi][e[lo:hi] >= t]
            elapsed = (t - open_start) / MINUTE_NS
            row[6] = len(elapsed)
            if len(elapsed):
                row[7], row[8] = elapsed.mean(), elapsed.max()
                row[9] = np.maximum(room_p50[position] - elapsed, 0).sum()
            hc = np.searchsorted(ce, t, side='left')
            for column, minutes in ((10, 15), (11, 30), (12, 60)):
                row[column] = hc - np.searchsorted(ce, t - minutes * MINUTE_NS, side='left')
            lo = max(hc - 20, np.searchsorted(ce, t - 180 * MINUTE_NS, side='left'))
            docs = cdoc[lo:hc]
            row[13] = len(np.unique(docs[docs >= 0]))
            if hc > lo and np.isfinite(cstats[lo:hc]).any():
                row[14], row[15] = np.nanmean(cstats[lo:hc], axis=0)
            first_today = np.searchsorted(s, t - t % DAY_NS, side='left')  # room opening within the day
            row[16] = hi - first_today
            if hi > first_today:
                row[17] = (t - s[first_today]) / MINUTE_NS
    return pd.DataFrame(out, columns=[prefix + name for name in names], index=data.index)


def add_service_state_features(data: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Add duration / roster / in-exam / throughput / drain features for 60->150 and 60->140."""
    data = data.copy()
    q_t = data[TIME_COLUMN].astype('int64').to_numpy()
    for prefix, end_col in END_COLUMNS.items():
        end = ns(events[end_col], NO_END)
        duration = np.where(end < NO_END, (end - ns(events['exam_start_time'], NO_END)) / MINUTE_NS, np.nan)
        ok = np.flatnonzero(np.isfinite(duration) & (duration >= 0))
        for key, label in (('ma_phong_thuc_hien', 'room'), ('ma_dich_vu', 'svc')):
            codes, _ = pd.factorize(pd.concat([data[key], events[key]]))
            stats = rolling_quantiles(codes[:len(data)], q_t, codes[len(data):][ok],
                                      end[ok], duration[ok])
            data[f'{prefix}{label}_dur_p50'] = stats[:, 0]
            data[f'{prefix}{label}_dur_p75'] = stats[:, 1]
            data[f'{prefix}{label}_dur_n'] = stats[:, 2]
        state = room_state(data, events, prefix, data[f'{prefix}room_dur_p50'].to_numpy())
        data = data.join(state)
        servers = data[f'{prefix}accounts_30m'].clip(lower=1)
        data[f'{prefix}ahead_work_per_server'] = (
            data['patients_ahead_same_room'] * data[f'{prefix}room_dur_p50'] / servers
        )
    data['ahead_per_start_rate'] = (
        data['patients_ahead_same_room'] / data['e150_starts_60m'].clip(lower=1) * 60
    )
    return data


# ----------------------------------------------------------------- feature sets

def group(prefix: str) -> dict[str, list[str]]:
    return {
        'duration': [f'{prefix}{n}' for n in (
            'room_dur_p50', 'room_dur_p75', 'room_dur_n', 'svc_dur_p50', 'svc_dur_p75', 'svc_dur_n')],
        'roster': [f'{prefix}{n}' for n in ('roster_size', 'roster_p50', 'roster_p75',
                                            'accounts_30m', 'accounts_60m')],
        'in_exam': [f'{prefix}{n}' for n in ('in_exam_n', 'in_exam_mean_elapsed',
                                             'in_exam_max_elapsed', 'in_exam_remaining_sum')],
        'shift': [f'{prefix}{n}' for n in ('starts_today', 'minutes_since_room_open')],
        'flow': [f'{prefix}{n}' for n in ('starts_15m', 'starts_30m', 'starts_60m',
                                          'start_gap_median20', 'completed_15m', 'completed_30m',
                                          'completed_60m', 'ahead_work_per_server')]
                + (['ahead_per_start_rate'] if prefix == 'e150_' else []),
    }


def feature_sets() -> dict[str, list[str]]:
    base = list(NUMERIC_FEATURES)
    queue = base + QUEUE_FEATURES + DYNAMICS_FEATURES
    g150, g140 = group('e150_'), group('e140_')
    ladder = {'official': base, 'queue_dynamics': queue}
    current = queue
    for name in ('duration', 'roster', 'in_exam', 'flow', 'shift'):
        current = current + g150[name]
        ladder[f'+{name}_150'] = current
    ladder['full_140'] = queue + sum(g140.values(), [])
    return ladder


# ---------------------------------------------------------------------- models

def make_model(numeric: list[str], params: dict[str, Any], seed: int) -> Pipeline:
    pre = ColumnTransformer(
        [('cat', OrdinalEncoder(handle_unknown='use_encoded_value', unknown_value=np.nan,
                                encoded_missing_value=np.nan), CATEGORICAL_FEATURES),
         ('num', SimpleImputer(strategy='median'), numeric)],
        remainder='drop', verbose_feature_names_out=False,
    )
    mask = [True] * len(CATEGORICAL_FEATURES) + [False] * len(numeric)
    return Pipeline([('pre', pre), ('model', HistGradientBoostingRegressor(
        **params, categorical_features=mask, early_stopping=False, random_state=seed))])


def fit_quantiles(numeric, train, seed, levels=(0.5, 0.8)) -> dict[float, Pipeline]:
    models = {}
    for level in levels:
        params = DEEPER_PARAMS if level == 0.5 else {**DEEPER_PARAMS, 'loss': 'quantile', 'quantile': level}
        models[level] = make_model(numeric, params, seed).fit(
            train[CATEGORICAL_FEATURES + numeric], train[TARGET_COLUMN])
    return models


def predict_quantiles(models, numeric, frame) -> tuple[np.ndarray, np.ndarray]:
    x = frame[CATEGORICAL_FEATURES + numeric]
    p50 = np.clip(models[0.5].predict(x), 0, None)
    p80 = np.maximum(np.clip(models[0.8].predict(x), 0, None), p50)
    return p50, p80


def pinball(y: np.ndarray, p: np.ndarray, level: float) -> float:
    d = y - p
    return float(np.mean(np.maximum(level * d, (level - 1) * d)))


TAIL_MINUTES = 120  # <2% of rows but ~45% of total absolute error; see segment diagnostics


def interval_metrics(y, p50, p80) -> dict[str, float]:
    typical = y <= TAIL_MINUTES
    return {**calculate_metrics(y, p50),
            'mae_wait_le_120': float(np.mean(np.abs(p50 - y)[typical])),
            'within_10m_wait_le_120': float(np.mean((np.abs(p50 - y) <= 10)[typical])),
            'mean_abs_log_error': float(np.mean(np.abs(np.log1p(p50) - np.log1p(y)))),
            'coverage_p50': float(np.mean(y <= p50)), 'coverage_p80': float(np.mean(y <= p80)),
            'pinball_p50': pinball(y, p50, 0.5), 'pinball_p80': pinball(y, p80, 0.8),
            'width_p80_minus_p50': float(np.mean(p80 - p50))}


def conformal_offset(residual: np.ndarray, level: float) -> float:
    n = len(residual)
    return float(np.quantile(residual, min(1.0, np.ceil((n + 1) * level) / n)))


# -------------------------------------------------------------------- diagnosis

def segment_table(frame: pd.DataFrame, segments: dict[str, pd.Series]) -> pd.DataFrame:
    rows = []
    total_abs = float(frame['abs_error'].sum())
    for name, labels in segments.items():
        for label, part in frame.groupby(labels, observed=True):
            rows.append({'segment': name, 'value': str(label), 'rows': len(part),
                         'mae': part['abs_error'].mean(), 'bias': part['error'].mean(),
                         'within_20m': (part['abs_error'] <= 20).mean(),
                         'coverage_p80': (part['y'] <= part['p80']).mean(),
                         'share_of_total_abs_error': part['abs_error'].sum() / total_abs})
    return pd.DataFrame(rows)


def segments_of(frame: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        'queue_entry_status': frame['queue_entry_status'].astype(str),
        'actual_wait_bucket': pd.cut(frame['y'], [-1, 10, 20, 30, 60, 120, np.inf],
                                     labels=['<=10', '10-20', '20-30', '30-60', '60-120', '>120']),
        'patients_ahead_room': pd.cut(frame['patients_ahead_same_room'], [-1, 0, 2, 5, 10, np.inf],
                                      labels=['0', '1-2', '3-5', '6-10', '>10']),
        'hour': frame[TIME_COLUMN].dt.hour.astype(str).str.zfill(2),
        'room': frame['ma_phong_thuc_hien'].astype(str),
    }


# ------------------------------------------------------------------------ main

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--input', type=Path, default=PROJECT_ROOT / 'data' / 'processed' / 'model_dataset.csv')
    p.add_argument('--output-dir', type=Path, default=PROJECT_ROOT / 'results' / 'model_diagnosis')
    p.add_argument('--test-start', default='2026-07-01')
    p.add_argument('--validation-days', type=int, default=7)
    p.add_argument('--n-folds', type=int, default=6)
    p.add_argument('--sliding-train-days', type=int, default=42)
    p.add_argument('--calibration-days', type=int, default=7)
    p.add_argument('--random-state', type=int, default=42)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    test_start = pd.Timestamp(args.test_start)
    data = load_dataset(args.input)
    development = data.loc[data[TIME_COLUMN].lt(test_start)].copy()
    del data
    print(f'Development rows: {len(development):,}', flush=True)
    development = add_causal_features(development)
    events = load_events(PROJECT_ROOT, test_start)
    print(f'Events (all exam services before test start): {len(events):,}', flush=True)
    development = add_service_state_features(development, events)

    folds, _ = make_folds(development, test_start, args.validation_days,
                          args.n_folds, args.sliding_train_days)
    folds = [f for f in folds if f['window_type'] == 'sliding']
    sets = feature_sets()

    metric_rows, kept = [], {}
    for name, numeric in sets.items():
        for fold in folds:
            train, val = fold['train'], fold['validation']
            models = fit_quantiles(numeric, train, args.random_state)
            p50, p80 = predict_quantiles(models, numeric, val)
            y = val[TARGET_COLUMN].to_numpy(dtype=float)
            metric_rows.append({'candidate': name, 'fold': fold['fold'], 'rows': len(val),
                                **interval_metrics(y, p50, p80)})
            kept.setdefault(name, []).append(val.assign(y=y, p50=p50, p80=p80))
            print(f'{name} fold={fold["fold"]} MAE={metric_rows[-1]["mae"]:.3f} '
                  f'cov80={metric_rows[-1]["coverage_p80"]:.3f}', flush=True)
    for fold in folds:  # reference: constant train median
        y = fold['validation'][TARGET_COLUMN].to_numpy(dtype=float)
        constant = np.full(len(y), fold['train'][TARGET_COLUMN].median())
        metric_rows.append({'candidate': 'constant_median', 'fold': fold['fold'], 'rows': len(y),
                            **interval_metrics(y, constant, np.full(len(y), fold['train'][TARGET_COLUMN].quantile(.8)))})

    fold_metrics = pd.DataFrame(metric_rows)
    summary = fold_metrics.drop(columns=['fold', 'rows']).groupby('candidate', sort=False).mean().reset_index()
    summary = summary.sort_values('mae').reset_index(drop=True)

    # Calibration: fit on the early part of each training window, learn offsets on the last days.
    best = summary.loc[summary['candidate'].ne('constant_median'), 'candidate'].iloc[0]
    calibration_rows, calibrated_frames = [], []
    for name in ('official', best):
        numeric = sets[name]
        for fold in folds:
            train, val = fold['train'], fold['validation']
            cal_start = fold['validation_start'] - timedelta(days=args.calibration_days)
            fit = train.loc[train[TIME_COLUMN].lt(cal_start) & train['label_available_time'].lt(cal_start)]
            cal = train.loc[train[TIME_COLUMN].ge(cal_start)]
            models = fit_quantiles(numeric, fit, args.random_state)
            c50, c80 = predict_quantiles(models, numeric, cal)
            v50, v80 = predict_quantiles(models, numeric, val)
            y_cal, y = cal[TARGET_COLUMN].to_numpy(float), val[TARGET_COLUMN].to_numpy(float)
            o50, o80 = conformal_offset(y_cal - c50, 0.5), conformal_offset(y_cal - c80, 0.8)
            k50 = np.clip(v50 + o50, 0, None)
            k80 = np.maximum(np.clip(v80 + o80, 0, None), k50)
            for version, a, b in (('raw', v50, v80), ('calibrated', k50, k80)):
                calibration_rows.append({'candidate': name, 'version': version, 'fold': fold['fold'],
                                         'offset_p50': o50, 'offset_p80': o80, **interval_metrics(y, a, b)})
            if name == best:
                calibrated_frames.append(val.assign(y=y, p50=k50, p80=k80))

    calibration = pd.DataFrame(calibration_rows)
    calibration_summary = (calibration.drop(columns='fold').groupby(['candidate', 'version'], sort=False)
                           .mean().reset_index())

    def with_errors(frame: pd.DataFrame) -> pd.DataFrame:
        frame = frame.copy()
        frame['error'] = frame['p50'] - frame['y']
        frame['abs_error'] = frame['error'].abs()
        return frame

    args.output_dir.mkdir(parents=True, exist_ok=True)
    official_frame = with_errors(pd.concat(kept['official']))
    best_frame = with_errors(pd.concat(calibrated_frames))
    segment_table(official_frame, segments_of(official_frame)).assign(model='official').to_csv(
        args.output_dir / 'segment_diagnostics_official.csv', index=False, encoding='utf-8-sig')
    segment_table(best_frame, segments_of(best_frame)).assign(model=f'{best}_calibrated').to_csv(
        args.output_dir / 'segment_diagnostics_best_calibrated.csv', index=False, encoding='utf-8-sig')
    fold_metrics.to_csv(args.output_dir / 'candidate_fold_metrics.csv', index=False, encoding='utf-8-sig')
    summary.to_csv(args.output_dir / 'candidate_summary.csv', index=False, encoding='utf-8-sig')
    calibration.to_csv(args.output_dir / 'calibration_fold_metrics.csv', index=False, encoding='utf-8-sig')
    calibration_summary.to_csv(args.output_dir / 'calibration_summary.csv', index=False, encoding='utf-8-sig')
    (args.output_dir / 'diagnosis.json').write_text(json.dumps({
        'scope': 'development_only_no_final_test_evaluation',
        'input_sha256': dataset_sha256(args.input),
        'test_start_exclusive_boundary': str(test_start),
        'development_rows': len(development),
        'best_uncalibrated_candidate': best,
        'in_exam_cap_minutes': IN_EXAM_CAP_MIN,
        'note': 'Doctor identity is used only through completed cases (known at status 150); '
                'live roster uses status-60 audit-log accounts.',
    }, ensure_ascii=False, indent=2), encoding='utf-8')

    pd.set_option('display.width', 250)
    show = ['candidate', 'mae', 'mae_wait_le_120', 'mean_abs_log_error', 'within_20m_rate', 'bias',
            'coverage_p50', 'coverage_p80', 'pinball_p80', 'width_p80_minus_p50']
    print('\n=== ablation (mean of 6 development folds) ===')
    print(summary[show].round(4).to_string(index=False))
    print('\n=== calibration (mean of 6 development folds) ===')
    print(calibration_summary[['candidate', 'version'] + show[1:]].round(4).to_string(index=False))


if __name__ == '__main__':
    main()
