"""Read-only analysis of services that cannot be modelled for lack of status 40.

The audit (``audit_and_build_exam_target``) defines the wait as the last
status 40 before the first status 60. Services that reached status 60 without a
status 40 therefore have no target and are left out of the model. This module
does **not** change that rule, the V3 cohort, the features or any locked result.
It only answers, from the audited data and the frozen V3 holdout predictions:

1. How large is the gap and where is it? (funnel, room coverage, trend)
2. Is "no status 40" the same as "skipped the pre-exam step"? (which screen
   started the exam; 20 -> 60 time of the two groups inside the same room)
3. How much could the exclusion move the headline holdout MAE? (room-level
   post-stratification and a simple sensitivity table)
4. How many arrivals do the queue-load features miss in rooms with many
   missing status 40? (diagnostic only; the model is NOT retrained)

Everything is written under ``results/missing_status40_analysis``; nothing is
written to ``results/unified_pipeline_v3`` or to any ``*_EVALUATED.json`` lock.
"""

from __future__ import annotations

import argparse
import json
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.optimize_on_development import grouped_active_queue_count, grouped_prior_arrival_count
from pipeline.pipeline_config import PROJECT_ROOT, PipelineConfig
from pipeline.train_and_evaluate_model import TIME_COLUMN

ROOM = 'ma_phong_thuc_hien'
ROOM_NAME = 'ten_phong_thuc_hien'
START = 'exam_start_time'
STATUS20 = 'latest_status20_before60_time'
STATUS40 = 'latest_status40_before60_time'

HAS40 = 'has_status40'
MISSING40 = 'missing_status40'
STATUS20_BRANCH = 'status20_branch'
NO60 = 'no_status60'
GROUP_ORDER = (HAS40, MISSING40, STATUS20_BRANCH, NO60)

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / 'results' / 'missing_status40_analysis'
TARGET_COLUMNS = [
    'nb_dich_vu_id', ROOM, ROOM_NAME, 'ma_khoa_thuc_hien', START, STATUS20, STATUS40,
    'first_status20_time', 'has_status_40_any', 'has_status_60_any', 'has_status_40_before_60',
    'uses_status20_target', 'target_valid_v2', 'waiting_minutes',
]
TIME_COLUMNS = [START, STATUS20, STATUS40, 'first_status20_time']


def _minutes(later: pd.Series, earlier: pd.Series) -> pd.Series:
    return (later - earlier).dt.total_seconds() / 60.0


def _ns(series: pd.Series) -> pd.Series:
    return series.astype('datetime64[ns]')


# --------------------------------------------------------------------------- loading
def load_target(path: Path) -> pd.DataFrame:
    """Audited target table (all exam services) with the columns this analysis needs."""
    target = pd.read_csv(path, usecols=TARGET_COLUMNS, parse_dates=TIME_COLUMNS, low_memory=False,
                         encoding='utf-8-sig')
    for column in ('has_status_40_any', 'has_status_60_any', 'has_status_40_before_60',
                   'uses_status20_target', 'target_valid_v2'):
        target[column] = target[column].astype(bool)
    return prepare_target(target)


def prepare_target(target: pd.DataFrame) -> pd.DataFrame:
    """Add the service group and the derived waiting intervals (does not touch existing columns)."""
    target = target.copy()
    has60 = target['has_status_60_any'].astype(bool)
    uses20 = target['uses_status20_target'].astype(bool)
    has40 = target['has_status_40_before_60'].astype(bool)
    target['queue_group'] = np.select(
        [~has60, uses20, ~has40], [NO60, STATUS20_BRANCH, MISSING40], default=HAS40)
    target['wait_status20_to_60'] = _minutes(target[START], target[STATUS20])
    target['wait_status40_to_60'] = _minutes(target[START], target[STATUS40])
    # Best available queue entry for rows that never got a status 40: the last status 20.
    target['entry_proxy'] = target[STATUS40].fillna(target[STATUS20])
    return target


# --------------------------------------------------------------------------- 1. size and place
def coverage_funnel(target: pd.DataFrame, max_wait_minutes: float | None) -> pd.DataFrame:
    """Where the services go between the audit table and the V3 modelling cohort."""
    total = len(target)
    counts = target['queue_group'].value_counts()
    has40 = target.loc[target['queue_group'].eq(HAS40)]
    over_limit = int(has40['waiting_minutes'].gt(max_wait_minutes).sum()) if max_wait_minutes else 0
    rows = [
        ('exam_services_total', total),
        ('no_status60', int(counts.get(NO60, 0))),
        ('status20_branch_kb008_h401', int(counts.get(STATUS20_BRANCH, 0))),
        ('missing_required_status40', int(counts.get(MISSING40, 0))),
        ('has_status40_and_status60', int(counts.get(HAS40, 0))),
        ('valid_target_audit', int(target['target_valid_v2'].sum())),
        ('removed_wait_over_limit_in_status40_branch', over_limit),
        ('v3_modelling_cohort', int(len(has40) - over_limit)),
    ]
    out = pd.DataFrame(rows, columns=['step', 'services'])
    out['pct_of_all_services'] = out['services'] / total if total else np.nan
    return out


def _is_glaucoma(name: object) -> bool:
    return 'glôcôm' in unicodedata.normalize('NFC', str(name)).lower()


def room_coverage(target: pd.DataFrame) -> pd.DataFrame:
    """Per-room gap. Two denominators on purpose.

    * ``no_valid_target_rate_of_all`` is the share of *all* services of the room without a
      valid target (the ``missing_rate`` of the audit workbook; it also counts no status 60).
    * ``missing_status40_rate_of_started`` is the share of the services that did start
      (status 60, status-40 branch) which lack status 40; this isolates the status-40 problem.
    """
    started = target['has_status_60_any'] & ~target['uses_status20_target']
    frame = target.assign(
        started_eligible=started,
        is_missing40=target['queue_group'].eq(MISSING40),
        no_valid=~target['target_valid_v2'],
    )
    out = (frame.groupby([ROOM, ROOM_NAME], dropna=False)
           .agg(services_all=('nb_dich_vu_id', 'size'), services_started=('started_eligible', 'sum'),
                missing_status40=('is_missing40', 'sum'), no_valid_target=('no_valid', 'sum'))
           .reset_index())
    out['missing_status40_rate_of_started'] = out['missing_status40'] / out['services_started'].where(
        out['services_started'] > 0)
    out['no_valid_target_rate_of_all'] = out['no_valid_target'] / out['services_all']
    out['is_glaucoma_room'] = out[ROOM_NAME].map(_is_glaucoma)
    out['share_of_all_missing_status40'] = out['missing_status40'] / max(int(out['missing_status40'].sum()), 1)
    return out.sort_values('services_all', ascending=False).reset_index(drop=True)


def missing_rate_trend(target: pd.DataFrame) -> pd.DataFrame:
    """Missing-status-40 rate (of started status-40-branch services) by week, weekday and hour."""
    started = target.loc[target['has_status_60_any'] & ~target['uses_status20_target']].copy()
    started['is_missing40'] = started['queue_group'].eq(MISSING40)
    keys = {
        'week': started[START].dt.to_period('W').astype(str),
        'weekday': started[START].dt.dayofweek,
        'hour_of_exam_start': started[START].dt.hour,
    }
    parts = []
    for dimension, key in keys.items():
        grouped = started.groupby(key)['is_missing40'].agg(['size', 'sum', 'mean'])
        grouped.columns = ['services_started', 'missing_status40', 'missing_status40_rate']
        grouped.insert(0, 'value', grouped.index.astype(str))
        grouped.insert(0, 'dimension', dimension)
        parts.append(grouped.reset_index(drop=True))
    return pd.concat(parts, ignore_index=True)


def no_status60_profile(target: pd.DataFrame) -> pd.DataFrame:
    """Services that never started: only status 20, or reached status 40 and then left/cancelled."""
    never = target.loc[target['queue_group'].eq(NO60)]
    # A few stray rows fall after the real end of the extract; the window ends on the last busy day.
    per_day = target['first_status20_time'].dt.normalize().value_counts()
    last_day = per_day[per_day >= 1000].index.max() - pd.Timedelta(days=1)
    rows = [
        ('no_status60_total', len(never)),
        ('never_left_status20_only', int((~never['has_status_40_any']).sum())),
        ('reached_status40_but_never_started', int(never['has_status_40_any'].sum())),
        ('first_status20_in_last_two_days_of_data', int(never['first_status20_time'].ge(last_day).sum())),
    ]
    return pd.DataFrame(rows, columns=['item', 'services'])


# --------------------------------------------------------------------------- 2. is it a skipped step?
def first_status60_screen(status_log: pd.DataFrame) -> pd.DataFrame:
    """Browser screen (from the log's ``uri``) that triggered the first status 60 of each service."""
    log = status_log.loc[status_log['trang_thai'].eq(60), ['nb_dich_vu_id', 'thoi_gian_thay_doi_ban_ghi', 'uri']]
    log = log.sort_values('thoi_gian_thay_doi_ban_ghi', kind='stable').drop_duplicates('nb_dich_vu_id')
    screen = log['uri'].fillna('').str.extract(r'sakura\.vnio\.vn/([^/?]+)')[0].fillna('<none>')
    return pd.DataFrame({'nb_dich_vu_id': log['nb_dich_vu_id'].to_numpy(), 'start_screen': screen.to_numpy()})


def start_screen_by_group(target: pd.DataFrame, first_screen: pd.DataFrame) -> pd.DataFrame:
    """Share of each group whose exam start was triggered from each screen."""
    merged = target.loc[target['queue_group'].isin([HAS40, MISSING40]), ['nb_dich_vu_id', 'queue_group']].merge(
        first_screen, on='nb_dich_vu_id', how='inner')
    table = pd.crosstab(merged['start_screen'], merged['queue_group'])
    shares = table.div(table.sum(axis=0), axis=1)
    out = table.add_suffix('_services').join(shares.add_suffix('_share')).reset_index()
    sort_column = f'{MISSING40}_services'
    return out.sort_values(sort_column, ascending=False).reset_index(drop=True)


def status_trigger_screen(status_log: pd.DataFrame, status: int) -> pd.DataFrame:
    """Which screen created each status-``status`` event (all events of that status in the log)."""
    events = status_log.loc[status_log['trang_thai'].eq(status), 'uri']
    screen = events.fillna('').str.extract(r'sakura\.vnio\.vn/([^/?]+)')[0].fillna('<none>')
    counts = screen.value_counts()
    return pd.DataFrame({'screen': counts.index, 'events': counts.to_numpy(), 'share': counts.to_numpy() / counts.sum()})


def timing_by_group(target: pd.DataFrame) -> pd.DataFrame:
    """Quantiles of the 20->60 time for both groups and of 40->60 for the group that has it."""
    quantiles = [.1, .25, .5, .75, .9]
    parts = []
    for label, group, column in (
        ('wait_20_to_60', HAS40, 'wait_status20_to_60'),
        ('wait_20_to_60', MISSING40, 'wait_status20_to_60'),
        ('wait_40_to_60', HAS40, 'wait_status40_to_60'),
    ):
        values = target.loc[target['queue_group'].eq(group), column].dropna()
        row = {'interval': label, 'group': group, 'services': len(values), 'mean': values.mean()}
        row.update({f'p{int(q * 100)}': values.quantile(q) for q in quantiles})
        parts.append(row)
    has40 = target.loc[target['queue_group'].eq(HAS40)]
    gap = (has40['wait_status20_to_60'] - has40['wait_status40_to_60']).dropna()
    row = {'interval': 'gap_20_to_40_in_has40', 'group': HAS40, 'services': len(gap), 'mean': gap.mean()}
    row.update({f'p{int(q * 100)}': gap.quantile(q) for q in quantiles})
    parts.append(row)
    return pd.DataFrame(parts)


def within_room_timing(target: pd.DataFrame, min_services: int = 100) -> pd.DataFrame:
    """20->60 of both groups inside the same room (rooms with enough services in each group)."""
    pair = target.loc[target['queue_group'].isin([HAS40, MISSING40])]
    rows = []
    for (room, name), part in pair.groupby([ROOM, ROOM_NAME], dropna=False):
        has40 = part.loc[part['queue_group'].eq(HAS40)]
        missing = part.loc[part['queue_group'].eq(MISSING40)]
        if len(has40) < min_services or len(missing) < min_services:
            continue
        rows.append({
            ROOM: room, ROOM_NAME: name, 'services_has_status40': len(has40), 'services_missing_status40': len(missing),
            'missing_status40_rate_of_started': len(missing) / len(part),
            'median_20_to_60_missing': missing['wait_status20_to_60'].median(),
            'median_20_to_60_has_status40': has40['wait_status20_to_60'].median(),
            'median_40_to_60_has_status40': has40['wait_status40_to_60'].median(),
        })
    out = pd.DataFrame(rows)
    if not out.empty:
        out['ratio_missing_vs_has40_20_to_60'] = (
            out['median_20_to_60_missing'] / out['median_20_to_60_has_status40'])
        out['ratio_40_to_60_vs_20_to_60_in_has40'] = (
            out['median_40_to_60_has_status40'] / out['median_20_to_60_has_status40'])
        # "Skipped the pre-exam step" predicts 20 ~ 40, i.e. 20->60 of the missing group close to the
        # 40->60 of the group that has status 40. A ratio clearly above 1 argues against that.
        out['ratio_missing_20_to_60_vs_has40_40_to_60'] = (
            out['median_20_to_60_missing'] / out['median_40_to_60_has_status40'])
        out = out.sort_values('services_missing_status40', ascending=False).reset_index(drop=True)
    return out


# --------------------------------------------------------------------------- 3. headline MAE
def holdout_population(target: pd.DataFrame, holdout_start: str, max_wait_minutes: float | None) -> pd.DataFrame:
    """Services that WOULD be in the V3 holdout if status 40 had been recorded for every one.

    Rows that do have status 40 enter at it (exactly the V3 rule); rows without it enter at their
    last status 20, the closest observable proxy. KB008/H401 and waits above the limit are excluded
    as in V3 (for the proxy rows the limit is applied to 20 -> 60).
    """
    started = target.loc[target['has_status_60_any'] & ~target['uses_status20_target']
                         & target['entry_proxy'].notna()].copy()
    wait = _minutes(started[START], started['entry_proxy'])
    keep = started['entry_proxy'].ge(pd.Timestamp(holdout_start))
    if max_wait_minutes is not None:
        keep &= wait.le(max_wait_minutes)
    return started.loc[keep]


def post_stratified_mae(
    observed: pd.DataFrame,
    population: pd.DataFrame,
    min_observed: int = 30,
    multipliers: tuple[float, ...] = (1.0, 1.25, 1.5, 2.0),
) -> tuple[pd.DataFrame, dict]:
    """Re-weight the holdout MAE from the observed room mix to the real room mix.

    ``observed``: nb_dich_vu_id, ma_phong_thuc_hien, abs_error (V3 holdout rows, status 40 present).
    ``population``: all started services of the holdout window (with and without status 40).
    Assumption (stated, not tested): inside a room the services without status 40 are as
    predictable as those with it. ``multipliers`` relaxes it: the MAE of the missing rows is the
    room MAE times the multiplier.
    """
    obs = observed.groupby(ROOM)['abs_error'].agg(n_observed='size', mae_observed='mean')
    pop = population.groupby(ROOM).agg(
        n_population=('nb_dich_vu_id', 'size'),
        n_missing_status40=('queue_group', lambda s: int(s.eq(MISSING40).sum())),
    )
    per_room = pop.join(obs, how='outer').fillna({'n_observed': 0, 'n_population': 0, 'n_missing_status40': 0})
    per_room['n_observed'] = per_room['n_observed'].astype(int)
    per_room['represented'] = per_room['n_observed'] >= min_observed
    total_population = float(per_room['n_population'].sum())
    per_room['share_of_population'] = per_room['n_population'] / total_population
    per_room = per_room.reset_index()

    rep = per_room.loc[per_room['represented']]
    observed_mae = float(observed['abs_error'].mean())
    weights = rep['n_population'] / rep['n_population'].sum()
    reweighted = float((weights * rep['mae_observed']).sum())
    scenarios = []
    for multiplier in multipliers:
        loss = rep['n_observed'] * rep['mae_observed'] + rep['n_missing_status40'] * multiplier * rep['mae_observed']
        scenarios.append({
            'mae_multiplier_on_missing_status40_rows': multiplier,
            'overall_mae': float(loss.sum() / (rep['n_observed'] + rep['n_missing_status40']).sum()),
        })
    summary = {
        'observed_holdout_rows': int(len(observed)),
        'observed_mae': observed_mae,
        'post_stratified_mae': reweighted,
        'difference_minutes': reweighted - observed_mae,
        'population_rows': int(total_population),
        'population_rows_missing_status40': int(per_room['n_missing_status40'].sum()),
        'share_population_in_unrepresented_rooms': float(
            per_room.loc[~per_room['represented'], 'n_population'].sum() / total_population),
        'min_observed_per_room': min_observed,
        'scenarios': scenarios,
    }
    return per_room, summary


# --------------------------------------------------------------------------- 4. load features
def load_feature_undercount(
    target: pd.DataFrame, max_wait_minutes: float | None, window_minutes: int = 30,
) -> tuple[pd.DataFrame, dict]:
    """Queue-load features with and without the services that lack status 40 (diagnostic only).

    ``visible`` rebuilds what the V3 model saw: the cohort rows only. ``all`` adds the services
    without status 40 at their last status 20. The same primitives as the model are used, so the
    only difference is which services are counted.
    """
    frame = target.loc[target['has_status_60_any'] & ~target['uses_status20_target']
                       & target['entry_proxy'].notna()].copy()
    if max_wait_minutes is not None:
        frame = frame.loc[_minutes(frame[START], frame['entry_proxy']).le(max_wait_minutes)]
    frame[TIME_COLUMN] = _ns(frame['entry_proxy'])
    frame['label_available_time'] = _ns(frame[START])
    frame = frame.sort_values(TIME_COLUMN, kind='stable').reset_index(drop=True)
    frame['arrivals_all'] = grouped_prior_arrival_count(frame, ROOM, window_minutes)
    frame['ahead_all'] = grouped_active_queue_count(frame, ROOM)

    visible_mask = frame['queue_group'].eq(HAS40).to_numpy()
    visible = frame.loc[visible_mask].reset_index(drop=True)
    visible['arrivals_visible'] = grouped_prior_arrival_count(visible, ROOM, window_minutes)
    visible['ahead_visible'] = grouped_active_queue_count(visible, ROOM)
    visible['arrivals_all'] = frame.loc[visible_mask, 'arrivals_all'].to_numpy()
    visible['ahead_all'] = frame.loc[visible_mask, 'ahead_all'].to_numpy()

    def aggregate(part: pd.DataFrame) -> pd.Series:
        return pd.Series({
            'cohort_rows': len(part),
            f'mean_arrivals_{window_minutes}m_visible': part['arrivals_visible'].mean(),
            f'mean_arrivals_{window_minutes}m_all': part['arrivals_all'].mean(),
            'arrivals_undercount_pct': _undercount(part['arrivals_visible'], part['arrivals_all']),
            'mean_patients_ahead_visible': part['ahead_visible'].mean(),
            'mean_patients_ahead_all': part['ahead_all'].mean(),
            'patients_ahead_undercount_pct': _undercount(part['ahead_visible'], part['ahead_all']),
        })

    per_room = visible.groupby([ROOM, ROOM_NAME], dropna=False).apply(aggregate, include_groups=False).reset_index()
    per_room = per_room.sort_values('cohort_rows', ascending=False).reset_index(drop=True)
    summary = {
        'cohort_rows_rebuilt': int(len(visible)),
        'window_minutes': window_minutes,
        'overall_arrivals_undercount_pct': _undercount(visible['arrivals_visible'], visible['arrivals_all']),
        'overall_patients_ahead_undercount_pct': _undercount(visible['ahead_visible'], visible['ahead_all']),
    }
    return per_room, summary


def _undercount(visible: pd.Series, everything: pd.Series) -> float:
    total = float(everything.sum())
    return float(100.0 * (1.0 - visible.sum() / total)) if total > 0 else float('nan')


# --------------------------------------------------------------------------- figure + orchestration
def plot_room_gap(room_table: pd.DataFrame, path: Path, top_n: int = 20) -> None:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    top = room_table.nlargest(top_n, 'services_all').sort_values('missing_status40_rate_of_started')
    fig, ax = plt.subplots(figsize=(9, 7))
    bars = ax.barh(top[ROOM], top['missing_status40_rate_of_started'] * 100, color='#2F6690')
    ax.bar_label(bars, labels=[f'{v:.1f}%' for v in top['missing_status40_rate_of_started'] * 100], padding=3, fontsize=8)
    ax.set_xlabel('Tỷ lệ dịch vụ đã khám nhưng thiếu status 40 (%)')
    ax.set_title(f'Thiếu status 40 theo phòng ({top_n} phòng nhiều dịch vụ nhất)', loc='left', fontweight='bold')
    ax.spines[['top', 'right']].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=200, facecolor='white')
    plt.close(fig)


def run(
    target_path: Path, status_log_path: Path, holdout_outputs_path: Path, output_dir: Path,
    config: PipelineConfig | None = None,
) -> dict:
    config = config or PipelineConfig()
    output_dir.mkdir(parents=True, exist_ok=True)
    target = load_target(target_path)
    summary: dict = {'inputs': {'target': str(target_path), 'status_log': str(status_log_path),
                                'holdout_outputs': str(holdout_outputs_path)}}

    funnel = coverage_funnel(target, config.max_wait_minutes)
    rooms = room_coverage(target)
    funnel.to_csv(output_dir / 'coverage_funnel.csv', index=False, encoding='utf-8-sig')
    rooms.to_csv(output_dir / 'room_coverage.csv', index=False, encoding='utf-8-sig')
    missing_rate_trend(target).to_csv(output_dir / 'missing_rate_trend.csv', index=False, encoding='utf-8-sig')
    no_status60_profile(target).to_csv(output_dir / 'no_status60_profile.csv', index=False, encoding='utf-8-sig')
    plot_room_gap(rooms, output_dir / 'missing_status40_by_room.png')
    funnel_map = dict(zip(funnel['step'], funnel['services']))
    summary['funnel'] = funnel_map
    summary['coverage'] = {
        'valid_target_audit_pct': funnel_map['valid_target_audit'] / funnel_map['exam_services_total'],
        'v3_cohort_pct': funnel_map['v3_modelling_cohort'] / funnel_map['exam_services_total'],
        'missing_status40_pct_of_all': funnel_map['missing_required_status40'] / funnel_map['exam_services_total'],
        'top5_rooms_share_of_missing_status40': float(
            rooms['missing_status40'].nlargest(5).sum() / max(rooms['missing_status40'].sum(), 1)),
        'top10_rooms_share_of_missing_status40': float(
            rooms['missing_status40'].nlargest(10).sum() / max(rooms['missing_status40'].sum(), 1)),
    }
    glaucoma = rooms.loc[rooms['is_glaucoma_room']]
    summary['glaucoma_rooms'] = {
        'rooms': int(len(glaucoma)),
        'missing_status40_rate_of_started': float(
            glaucoma['missing_status40'].sum() / max(glaucoma['services_started'].sum(), 1)),
        'by_room': {row[ROOM]: float(row['missing_status40_rate_of_started']) for _, row in glaucoma.iterrows()
                    if row['services_started'] >= 500},
    }

    timing_by_group(target).to_csv(output_dir / 'timing_by_group.csv', index=False, encoding='utf-8-sig')
    within = within_room_timing(target)
    within.to_csv(output_dir / 'within_room_timing.csv', index=False, encoding='utf-8-sig')
    skip_ratio = within['ratio_missing_20_to_60_vs_has40_40_to_60']
    summary['within_room_timing'] = {
        'rooms_compared': int(len(within)),
        'rooms_missing_20_to_60_above_has40_40_to_60': int(skip_ratio.gt(1).sum()),
        'rooms_missing_20_to_60_within_20pct_of_has40_20_to_60': int(
            within['ratio_missing_vs_has40_20_to_60'].between(.8, 1.2).sum()),
    }

    log = pd.read_csv(status_log_path, usecols=['nb_dich_vu_id', 'thoi_gian_thay_doi_ban_ghi', 'trang_thai', 'uri'],
                      parse_dates=['thoi_gian_thay_doi_ban_ghi'], low_memory=False,
                      encoding='utf-8', encoding_errors='replace')
    screens = start_screen_by_group(target, first_status60_screen(log))
    screens.to_csv(output_dir / 'start_screen_by_group.csv', index=False, encoding='utf-8-sig')
    status_trigger_screen(log, 40).to_csv(
        output_dir / 'status40_trigger_screen.csv', index=False, encoding='utf-8-sig')
    summary['start_screen_share_do_thi_luc'] = {
        group: float(screens.loc[screens['start_screen'].eq('do-thi-luc'), f'{group}_share'].sum())
        for group in (HAS40, MISSING40)}
    del log

    outputs = pd.read_csv(holdout_outputs_path, usecols=['nb_dich_vu_id', 'predicted_wait_minutes'], encoding='utf-8-sig')
    outputs = outputs.merge(target[['nb_dich_vu_id', ROOM, 'waiting_minutes']], on='nb_dich_vu_id', how='left')
    outputs['abs_error'] = (outputs['predicted_wait_minutes'] - outputs['waiting_minutes']).abs()
    population = holdout_population(target, config.holdout_start, config.max_wait_minutes)
    per_room, mae_summary = post_stratified_mae(outputs, population)
    per_room = per_room.merge(rooms[[ROOM, ROOM_NAME]], on=ROOM, how='left')
    per_room.to_csv(output_dir / 'holdout_mae_by_room.csv', index=False, encoding='utf-8-sig')
    mae_summary['observed_rows_match_population_with_status40'] = bool(
        int(population['queue_group'].eq(HAS40).sum()) == len(outputs))
    summary['holdout_mae'] = mae_summary

    load_table, load_summary = load_feature_undercount(target, config.max_wait_minutes)
    load_table.to_csv(output_dir / 'load_feature_undercount_by_room.csv', index=False, encoding='utf-8-sig')
    cohort_rows = funnel_map['v3_modelling_cohort']
    load_summary['rebuilt_rows_match_v3_cohort'] = bool(load_summary['cohort_rows_rebuilt'] == cohort_rows)
    room_missing = rooms.set_index(ROOM)['missing_status40_rate_of_started']
    cohort_room_rate = load_table[ROOM].map(room_missing)
    load_summary['cohort_share_in_rooms_missing_ge_30pct'] = float(
        load_table.loc[cohort_room_rate.ge(.30), 'cohort_rows'].sum() / load_table['cohort_rows'].sum())
    load_summary['cohort_share_in_rooms_missing_ge_10pct'] = float(
        load_table.loc[cohort_room_rate.ge(.10), 'cohort_rows'].sum() / load_table['cohort_rows'].sum())
    summary['load_features'] = load_summary

    (output_dir / 'missing_status40_summary.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=float), encoding='utf-8')
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--target', type=Path, default=PROJECT_ROOT / 'data' / 'processed' / 'exam_wait_target_all.csv')
    parser.add_argument('--status-log', type=Path,
                        default=PROJECT_ROOT / 'data' / 'intermediate' / '05_exam_status_log.csv')
    parser.add_argument('--holdout-outputs', type=Path,
                        default=PROJECT_ROOT / 'results' / 'unified_pipeline_v3' / 'holdout_patient_outputs.csv')
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    result = run(args.target, args.status_log, args.holdout_outputs, args.output_dir)
    print(json.dumps(result, ensure_ascii=True, indent=2, default=float))


if __name__ == '__main__':
    main()
