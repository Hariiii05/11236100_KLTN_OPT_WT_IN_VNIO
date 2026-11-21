"""Model features at an arbitrary prediction time, from the current queue state.

Training computes these features column-wise over the whole dataset
(``optimize_on_development.add_causal_features`` and
``feature_engineering.add_lunch_break_features``). At inference time a single
new patient must be featurized against the queue as it is *now*, so this
module re-expresses the same definitions as point queries on sorted per-group
event arrays. ``webapp/verify_parity.py`` checks the two implementations give
identical values on the real cohort, and ``tests/test_webapp_features.py``
does the same on synthetic data.

Definitions (T = queue-entry time of the row being predicted):

* arrivals_last_w  : entries e with T - w <= e < T in the group
* patients_ahead   : entries e < T whose exam has not started by T (start > T).
                     Entries older than 24 h are ignored, matching the V3
                     cohort rule (waits > 24 h were patients who left).
* starts_last_w    : exam starts s with T - w <= s < T
* minutes_since_last_start : T - max{s < T}, NaN when none
* lunch overflow   : only for T before lunch start; among morning entries in the
                     group whose exam started in [T - 42 d, T), count and share
                     that crossed lunch. Afternoon rows get count 0 / rate NaN.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from pipeline.feature_engineering import feature_columns, parse_time_of_day
from pipeline.train_and_evaluate_model import TIME_COLUMN

NOT_YET = np.iinfo(np.int64).max  # exam not started / patient still waiting
MINUTE_NS = float(pd.Timedelta(1, unit='m').value)
DAY_NS = int(pd.Timedelta(1, unit='D').value)
ACTIVE_LOOKBACK_NS = int(pd.Timedelta(24, unit='h').value)
GROUP_COLUMNS = {'room': 'ma_phong_thuc_hien', 'department': 'ma_khoa_thuc_hien'}
MODEL_FEATURE_SET = 'queue_dynamics_lunch'


@dataclass
class _GroupArrays:
    entry: np.ndarray            # entry times, sorted
    leave_by_entry: np.ndarray   # time the entry stopped waiting (start/skip), aligned with `entry`
    start: np.ndarray            # known exam starts, sorted
    morning_start: np.ndarray    # exam starts of morning entries, sorted
    morning_cross_cumsum: np.ndarray  # prefix sums of crosses_lunch aligned with morning_start


class EventIndex:
    """Per-group sorted event arrays for rooms and departments."""

    def __init__(self, events: pd.DataFrame, lunch_start: pd.Timedelta):
        """``events`` columns: ma_phong_thuc_hien, ma_khoa_thuc_hien, entry_ns,
        start_ns (NOT_YET if unknown), leave_ns (NOT_YET if still waiting)."""
        entry = events['entry_ns'].to_numpy(dtype=np.int64)
        start = events['start_ns'].to_numpy(dtype=np.int64)
        leave = events['leave_ns'].to_numpy(dtype=np.int64)
        started = start != NOT_YET
        entry_tod = entry % DAY_NS
        lunch_ns = int(lunch_start.value)
        morning = entry_tod < lunch_ns
        start_safe = np.where(started, start, 0)
        crosses = morning & started & (
            (start_safe // DAY_NS > entry // DAY_NS) | (start_safe % DAY_NS >= lunch_ns)
        )
        self.groups: dict[str, dict[str, _GroupArrays]] = {}
        for kind, column in GROUP_COLUMNS.items():
            per_group: dict[str, _GroupArrays] = {}
            keys = events[column].astype('string').fillna('__MISSING__').to_numpy()
            for key, positions in pd.Series(np.arange(len(events))).groupby(keys).indices.items():
                positions = np.asarray(positions)
                order = positions[np.argsort(entry[positions], kind='stable')]
                known = positions[started[positions]]
                morning_known = known[morning[known]]
                morning_order = morning_known[np.argsort(start[morning_known], kind='stable')]
                per_group[str(key)] = _GroupArrays(
                    entry=entry[order],
                    leave_by_entry=leave[order],
                    start=np.sort(start[known], kind='stable'),
                    morning_start=start[morning_order],
                    morning_cross_cumsum=np.concatenate([[0], np.cumsum(crosses[morning_order])]),
                )
            self.groups[kind] = per_group

    @classmethod
    def from_history(cls, cohort: pd.DataFrame, lunch_start: pd.Timedelta) -> 'EventIndex':
        start_ns = cohort['label_available_time'].astype('int64').to_numpy()
        return cls(
            pd.DataFrame({
                'ma_phong_thuc_hien': cohort['ma_phong_thuc_hien'].to_numpy(),
                'ma_khoa_thuc_hien': cohort['ma_khoa_thuc_hien'].to_numpy(),
                'entry_ns': cohort[TIME_COLUMN].astype('int64').to_numpy(),
                'start_ns': start_ns,
                'leave_ns': start_ns,
            }),
            lunch_start,
        )

    def aggregates(self, kind: str, key: str, t: int, windows: tuple[int, ...], lunch_window_ns: int) -> dict:
        arrays = self.groups[kind].get(key)
        if arrays is None:
            return {'arrivals': {w: 0 for w in windows}, 'starts': {w: 0 for w in windows}, 'ahead': 0,
                    'last_start': None, 'lunch_count': 0, 'lunch_sum': 0}
        entry, start = arrays.entry, arrays.start
        right = np.searchsorted(entry, t, side='left')
        arrivals = {w: int(right - np.searchsorted(entry, t - w * 60 * 10**9, side='left')) for w in windows}
        active_left = np.searchsorted(entry, t - ACTIVE_LOOKBACK_NS, side='left')
        ahead = int(np.count_nonzero(arrays.leave_by_entry[active_left:right] > t))
        start_right = np.searchsorted(start, t, side='left')
        starts = {w: int(start_right - np.searchsorted(start, t - w * 60 * 10**9, side='left')) for w in windows}
        last_start = int(start[start_right - 1]) if start_right > 0 else None
        lo = np.searchsorted(arrays.morning_start, t - lunch_window_ns, side='left')
        hi = np.searchsorted(arrays.morning_start, t, side='left')
        return {
            'arrivals': arrivals, 'starts': starts, 'ahead': ahead, 'last_start': last_start,
            'lunch_count': int(hi - lo),
            'lunch_sum': int(arrays.morning_cross_cumsum[hi] - arrays.morning_cross_cumsum[lo]),
        }


def _combined(indexes: list[EventIndex], kind: str, key: str, t: int, windows: tuple[int, ...],
              lunch_window_ns: int) -> dict:
    parts = [index.aggregates(kind, key, t, windows, lunch_window_ns) for index in indexes]
    last = [p['last_start'] for p in parts if p['last_start'] is not None]
    return {
        'arrivals': {w: sum(p['arrivals'][w] for p in parts) for w in windows},
        'starts': {w: sum(p['starts'][w] for p in parts) for w in windows},
        'ahead': sum(p['ahead'] for p in parts),
        'last_start': max(last) if last else None,
        'lunch_count': sum(p['lunch_count'] for p in parts),
        'lunch_sum': sum(p['lunch_sum'] for p in parts),
    }


def build_feature_frame(
    queries: pd.DataFrame,
    indexes: list[EventIndex],
    lunch_start: str,
    lunch_window_days: int,
) -> pd.DataFrame:
    """Return ``queries`` with every model feature column added.

    ``queries`` needs: queue_entry_time, ma_dich_vu, ma_phong_thuc_hien,
    ma_khoa_thuc_hien, queue_entry_status, uu_tien, cap_cuu,
    patient_payment_type, stt, order_to_queue_minutes.
    """
    frame = queries.copy().reset_index(drop=True)
    frame[TIME_COLUMN] = pd.to_datetime(frame[TIME_COLUMN])
    lunch_delta = parse_time_of_day(lunch_start)
    lunch_window_ns = int(pd.Timedelta(lunch_window_days, unit='D').value)
    times = frame[TIME_COLUMN].astype('int64').to_numpy()

    # Calendar features: same expressions as train_and_evaluate_model.load_dataset.
    frame['queue_entry_hour'] = frame[TIME_COLUMN].dt.hour.astype('int16')
    frame['queue_entry_day_of_week'] = frame[TIME_COLUMN].dt.dayofweek.astype('int8')
    frame['queue_entry_is_weekend'] = frame['queue_entry_day_of_week'].isin([5, 6]).astype('int8')
    frame['queue_hour_sin'] = np.sin(2 * np.pi * frame['queue_entry_hour'] / 24)
    frame['queue_hour_cos'] = np.cos(2 * np.pi * frame['queue_entry_hour'] / 24)
    frame['queue_dow_sin'] = np.sin(2 * np.pi * frame['queue_entry_day_of_week'] / 7)
    frame['queue_dow_cos'] = np.cos(2 * np.pi * frame['queue_entry_day_of_week'] / 7)

    time_of_day = frame[TIME_COLUMN] - frame[TIME_COLUMN].dt.normalize()
    frame['minutes_to_lunch_start'] = (lunch_delta - time_of_day).dt.total_seconds() / 60.0
    frame['registered_before_lunch'] = time_of_day.lt(lunch_delta)

    columns: dict[str, list] = {name: [] for name in (
        'room_arrivals_last_15m', 'room_arrivals_last_30m', 'room_arrivals_last_60m',
        'department_arrivals_last_30m', 'department_arrivals_last_60m',
        'patients_ahead_same_room', 'patients_ahead_same_department',
        'room_starts_last_15m', 'room_starts_last_30m', 'room_starts_last_60m',
        'department_starts_last_30m', 'department_starts_last_60m',
        'minutes_since_last_room_start', 'minutes_since_last_department_start',
        'room_lunch_overflow_rate', 'room_lunch_overflow_count',
        'department_lunch_overflow_rate', 'department_lunch_overflow_count',
    )}
    rooms = frame['ma_phong_thuc_hien'].astype('string').fillna('__MISSING__').to_numpy()
    departments = frame['ma_khoa_thuc_hien'].astype('string').fillna('__MISSING__').to_numpy()
    morning = frame['registered_before_lunch'].to_numpy(dtype=bool)
    for position, t in enumerate(times):
        t = int(t)
        room = _combined(indexes, 'room', str(rooms[position]), t, (15, 30, 60), lunch_window_ns)
        dept = _combined(indexes, 'department', str(departments[position]), t, (30, 60), lunch_window_ns)
        for w in (15, 30, 60):
            columns[f'room_arrivals_last_{w}m'].append(room['arrivals'][w])
            columns[f'room_starts_last_{w}m'].append(room['starts'][w])
        for w in (30, 60):
            columns[f'department_arrivals_last_{w}m'].append(dept['arrivals'][w])
            columns[f'department_starts_last_{w}m'].append(dept['starts'][w])
        columns['patients_ahead_same_room'].append(room['ahead'])
        columns['patients_ahead_same_department'].append(dept['ahead'])
        for prefix, agg in (('room', room), ('department', dept)):
            columns[f'minutes_since_last_{prefix}_start'].append(
                np.nan if agg['last_start'] is None else (t - agg['last_start']) / MINUTE_NS
            )
            count = agg['lunch_count'] if morning[position] else 0
            columns[f'{prefix}_lunch_overflow_count'].append(count)
            columns[f'{prefix}_lunch_overflow_rate'].append(agg['lunch_sum'] / count if count else np.nan)
    for name, values in columns.items():
        frame[name] = np.asarray(values, dtype=float if 'rate' in name or 'minutes' in name else np.int32)

    categorical, numeric = feature_columns(MODEL_FEATURE_SET)
    for column in categorical:
        frame[column] = frame[column].astype('string').fillna('__MISSING__')
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors='coerce')
    return frame


def web_events_frame(tickets: list[dict]) -> pd.DataFrame:
    """Queue events of tickets created in the web app (ns timestamps)."""
    rows = []
    for ticket in tickets:
        entry = pd.Timestamp(ticket['queue_entry_time']).value
        start = pd.Timestamp(ticket['start_time']).value if ticket.get('start_time') else NOT_YET
        left = ticket.get('skipped_time') if ticket.get('status') in ('skipped', 'cancelled') else None
        leave = start if start != NOT_YET else (pd.Timestamp(left).value if left else NOT_YET)
        rows.append({
            'ma_phong_thuc_hien': ticket['ma_phong_thuc_hien'],
            'ma_khoa_thuc_hien': ticket['ma_khoa_thuc_hien'],
            'entry_ns': entry, 'start_ns': start, 'leave_ns': leave,
        })
    columns = ['ma_phong_thuc_hien', 'ma_khoa_thuc_hien', 'entry_ns', 'start_ns', 'leave_ns']
    return pd.DataFrame(rows, columns=columns)
