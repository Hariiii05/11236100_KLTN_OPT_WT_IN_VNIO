"""Aggregations for the management dashboard.

Sources:
* history  - the V3 modeling cohort (real waits, 01/04 - 31/07/2026)
* forecast - out-of-sample holdout predictions of the evaluated V3 bundle
             (15/07 - 31/07/2026), i.e. the numbers reported in the thesis
* live     - the replayed day at the clock's ``now`` plus web tickets
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from webapp.config import WebConfig
from webapp.history import HistoricalData
from webapp.operations import Operations, web_number

SHIFTS = {'sang': 'Sáng (05:00–11:30)', 'trua': 'Trưa (11:30–13:00)', 'chieu': 'Chiều (13:00–17:00)',
          'ngoai_gio': 'Ngoài giờ'}
WEEKDAYS = ['Thứ 2', 'Thứ 3', 'Thứ 4', 'Thứ 5', 'Thứ 6', 'Thứ 7', 'Chủ nhật']
MIN_GROUP_ROWS = 20
SHORT_NAMES = (('Khám bệnh và điều trị', 'KB&ĐT'), ('Tạo hình - Thẩm mỹ mắt và vùng mặt', 'Tạo hình – Thẩm mỹ'))


def short_department(name: str) -> str:
    for long, short in SHORT_NAMES:
        name = name.replace(long, short)
    return name


def _enrich(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    entry = frame['queue_entry_time']
    frame['date'] = entry.dt.normalize()
    frame['hour'] = entry.dt.hour
    frame['dow'] = entry.dt.dayofweek
    minutes = entry.dt.hour * 60 + entry.dt.minute
    frame['shift'] = np.select(
        [(minutes >= 300) & (minutes < 690), (minutes >= 690) & (minutes < 780), (minutes >= 780) & (minutes < 1020)],
        ['sang', 'trua', 'chieu'], default='ngoai_gio')
    frame['uu_tien_bool'] = frame['uu_tien'].astype(str).eq('True')
    return frame


def _r(value: Any, digits: int = 1) -> float | None:
    return None if value is None or pd.isna(value) else round(float(value), digits)


def _stats(wait: pd.Series, threshold: float) -> dict[str, Any]:
    if wait.empty:
        return {'n': 0, 'mean': None, 'median': None, 'p80': None, 'p90': None, 'over': None}
    return {'n': int(len(wait)), 'mean': _r(wait.mean()), 'median': _r(wait.median()),
            'p80': _r(wait.quantile(0.8)), 'p90': _r(wait.quantile(0.9)),
            'over': _r(100 * (wait > threshold).mean())}


class Dashboard:
    def __init__(self, config: WebConfig, history: HistoricalData, operations: Operations):
        self.config = config
        self.ops = operations
        self.history = _enrich(history.cohort)
        self.holdout = _enrich(history.holdout) if history.holdout is not None else None

    # ---------------------------------------------------------------- filters
    def options(self) -> dict[str, Any]:
        data = self.history
        return {
            'date_min': str(data['date'].min().date()), 'date_max': str(data['date'].max().date()),
            'holdout_min': str(self.holdout['date'].min().date()) if self.holdout is not None else None,
            'holdout_max': str(self.holdout['date'].max().date()) if self.holdout is not None else None,
            'shifts': [{'ma': k, 'ten': v} for k, v in SHIFTS.items()],
            'doctors': sorted(data['ma_bac_si_kham'].dropna().unique().tolist()),
        }

    @staticmethod
    def _filter(frame: pd.DataFrame, f: dict[str, Any]) -> pd.DataFrame:
        mask = pd.Series(True, index=frame.index)
        if f.get('start'):
            mask &= frame['date'] >= pd.Timestamp(f['start'])
        if f.get('end'):
            mask &= frame['date'] <= pd.Timestamp(f['end'])
        for key, column in (('department', 'ma_khoa_thuc_hien'), ('room', 'ma_phong_thuc_hien'),
                            ('service', 'ma_dich_vu'), ('doctor', 'ma_bac_si_kham'),
                            ('payment', 'patient_payment_type'), ('shift', 'shift')):
            if f.get(key):
                mask &= frame[column].astype(str) == str(f[key])
        return frame.loc[mask]

    # --------------------------------------------------------------- sections
    def summary(self, f: dict[str, Any]) -> dict[str, Any]:
        threshold = float(f.get('threshold') or 30)
        data = self._filter(self.history, f)
        result: dict[str, Any] = {'threshold': threshold, 'rows': int(len(data))}
        if data.empty:
            return result | {'empty': True}
        wait = data['waiting_minutes']
        days = data['date'].nunique()
        duration = data['service_minutes_60_to_150']
        result['overview'] = _stats(wait, threshold) | {
            'services': int(len(data)), 'encounters': int(data['nb_dot_dieu_tri_id'].nunique()),
            'days': int(days), 'per_day': _r(len(data) / max(days, 1), 0),
            'priority_share': _r(100 * data['uu_tien_bool'].mean()),
            'bhyt_share': _r(100 * data['patient_payment_type'].eq('bhyt').mean()),
            'median_exam_minutes': _r(duration[(duration > 0) & (duration < 600)].median()),
            'rooms': int(data['ma_phong_thuc_hien'].nunique()),
            'start': str(data['date'].min().date()), 'end': str(data['date'].max().date()),
        }
        result['by_hour'] = self._by_hour(data, days, threshold)
        result['heatmap'] = self._heatmap(data)
        result['daily'] = [
            {'date': str(d.date()), 'n': int(len(g)), 'median': _r(g['waiting_minutes'].median()),
             'mean': _r(g['waiting_minutes'].mean()), 'p90': _r(g['waiting_minutes'].quantile(0.9)),
             'over': _r(100 * (g['waiting_minutes'] > threshold).mean())}
            for d, g in data.groupby('date')
        ]
        result['by_weekday'] = []
        for dow, g in data.groupby('dow'):
            n_days = g['date'].nunique()
            result['by_weekday'].append({'dow': int(dow), 'label': WEEKDAYS[int(dow)],
                                         'per_day': _r(len(g) / max(n_days, 1), 0)} | _stats(g['waiting_minutes'],
                                                                                             threshold))
        result['rooms'] = self._rooms(data, threshold)
        result['departments'] = [
            {'ma': k, 'ten': short_department(self.ops.departments.get(k, k)), 'ten_day_du': self.ops.departments.get(k, k)}
            | _stats(g['waiting_minutes'], threshold)
            for k, g in data.groupby('ma_khoa_thuc_hien')
        ]
        result['services'] = [
            {'ma': k, 'ten': self.ops.services.get(k, k)} | _stats(g['waiting_minutes'], threshold)
            for k, g in data.groupby('ma_dich_vu')
        ]
        result['payment'] = [
            {'ma': k, 'ten': 'BHYT' if k == 'bhyt' else 'Dịch vụ'} | _stats(g['waiting_minutes'], threshold)
            for k, g in data.groupby('patient_payment_type')
        ]
        result['shifts'] = [
            {'ma': k, 'ten': SHIFTS[k]} | _stats(g['waiting_minutes'], threshold)
            for k, g in data.groupby('shift')
        ]
        result['distribution'] = self._distribution(wait)
        result['forecast'] = self.forecast(f, threshold)
        return result

    @staticmethod
    def _by_hour(data: pd.DataFrame, days: int, threshold: float) -> list[dict[str, Any]]:
        rows = []
        for hour, g in data.groupby('hour'):
            item = {'hour': int(hour), 'per_day': _r(len(g) / max(days, 1), 1)} | _stats(g['waiting_minutes'],
                                                                                        threshold)
            if len(g) < MIN_GROUP_ROWS:
                item |= {'mean': None, 'median': None, 'p90': None}
            rows.append(item)
        return rows

    @staticmethod
    def _heatmap(data: pd.DataFrame) -> dict[str, Any]:
        grouped = data.groupby(['dow', 'hour'])['waiting_minutes']
        median, count = grouped.median(), grouped.size()
        cells = [[int(h), int(d), _r(median[(d, h)]), int(count[(d, h)])]
                 for (d, h) in median.index if count[(d, h)] >= MIN_GROUP_ROWS]
        return {'weekdays': WEEKDAYS, 'cells': cells}

    @staticmethod
    def _distribution(wait: pd.Series) -> list[dict[str, Any]]:
        edges = [0, 5, 10, 15, 20, 30, 45, 60, 90, 120, np.inf]
        labels = ['<5', '5–10', '10–15', '15–20', '20–30', '30–45', '45–60', '60–90', '90–120', '≥120']
        counts = pd.cut(wait, edges, right=False, labels=labels).value_counts().reindex(labels)
        return [{'bin': label, 'n': int(counts[label]), 'share': _r(100 * counts[label] / len(wait))}
                for label in labels]

    def _rooms(self, data: pd.DataFrame, threshold: float) -> list[dict[str, Any]]:
        rows = []
        for room, g in data.groupby('ma_phong_thuc_hien'):
            starts = g['label_available_time']
            active_hours = starts.dt.floor('h').nunique()
            duration = g['service_minutes_60_to_150']
            info = self.ops.room_info(room)
            rows.append({
                'ma_phong': room, 'ten_phong': info['ten_phong'], 'ma_khoa': info['ma_khoa'],
                'ten_khoa': info['ten_khoa'], 'days': int(g['date'].nunique()),
                'per_hour': _r(len(g) / max(active_hours, 1)),
                'median_exam_minutes': _r(duration[(duration > 0) & (duration < 600)].median()),
            } | _stats(g['waiting_minutes'], threshold))
        rows.sort(key=lambda r: -r['n'])
        return rows

    def forecast(self, f: dict[str, Any], threshold: float) -> dict[str, Any]:
        if self.holdout is None:
            return {'available': False, 'reason': 'Thiếu results/unified_pipeline_v3/holdout_patient_outputs.csv.'}
        data = self._filter(self.holdout, f)
        if data.empty:
            return {'available': False, 'reason': 'Không có dòng holdout (15/07–31/07/2026) trong phạm vi lọc.'}
        actual = data['waiting_minutes'].to_numpy(dtype=float)
        predicted = data['predicted_wait_minutes'].to_numpy(dtype=float)
        p80 = data['predicted_wait_p80_minutes'].to_numpy(dtype=float)
        error = predicted - actual
        tolerance = float(f.get('tolerance') or 10)
        result = {
            'available': True, 'n': int(len(data)), 'tolerance': tolerance,
            'mae': _r(np.abs(error).mean(), 2), 'rmse': _r(np.sqrt((error ** 2).mean()), 2),
            'median_ae': _r(np.median(np.abs(error)), 2), 'bias': _r(error.mean(), 2),
            'within_tolerance': _r(100 * (np.abs(error) <= tolerance).mean()),
            'within_10': _r(100 * (np.abs(error) <= 10).mean()), 'within_20': _r(100 * (np.abs(error) <= 20).mean()),
            'coverage_p80': _r(100 * (actual <= p80).mean()),
            'mean_actual': _r(actual.mean()), 'mean_predicted': _r(predicted.mean()),
        }
        frame = data.assign(abs_error=np.abs(error), error=error)
        result['daily'] = [
            {'date': str(d.date()), 'n': int(len(g)), 'mae': _r(g['abs_error'].mean(), 2),
             'actual': _r(g['waiting_minutes'].mean()), 'predicted': _r(g['predicted_wait_minutes'].mean())}
            for d, g in frame.groupby('date')
        ]
        result['by_hour'] = [
            {'hour': int(h), 'n': int(len(g)), 'mae': _r(g['abs_error'].mean(), 2),
             'actual': _r(g['waiting_minutes'].mean()), 'predicted': _r(g['predicted_wait_minutes'].mean())}
            for h, g in frame.groupby('hour') if len(g) >= MIN_GROUP_ROWS
        ]
        result['by_department'] = sorted([
            {'ma': k, 'ten': short_department(self.ops.departments.get(k, k)), 'n': int(len(g)),
             'mae': _r(g['abs_error'].mean(), 2),
             'bias': _r(g['error'].mean(), 2)}
            for k, g in frame.groupby('ma_khoa_thuc_hien') if len(g) >= MIN_GROUP_ROWS
        ], key=lambda r: -r['n'])
        deciles = pd.qcut(frame['predicted_wait_minutes'], 10, duplicates='drop')
        result['calibration'] = [
            {'predicted': _r(g['predicted_wait_minutes'].mean()), 'actual': _r(g['waiting_minutes'].mean()),
             'actual_p80': _r(g['waiting_minutes'].quantile(0.8)), 'n': int(len(g))}
            for _, g in frame.groupby(deciles, observed=True)
        ]
        edges = [-np.inf, -30, -20, -10, -5, 0, 5, 10, 20, 30, np.inf]
        labels = ['<-30', '-30…-20', '-20…-10', '-10…-5', '-5…0', '0…5', '5…10', '10…20', '20…30', '>30']
        hist = pd.cut(frame['error'], edges, labels=labels).value_counts().reindex(labels)
        result['error_hist'] = [{'bin': b, 'n': int(hist[b])} for b in labels]
        morning = frame.loc[frame['queue_entry_time'].dt.hour * 60 + frame['queue_entry_time'].dt.minute < 690]
        if not morning.empty:
            start = morning['label_available_time']
            crossed = (start.dt.normalize() > morning['date']) | (start.dt.hour * 60 + start.dt.minute >= 690)
            warned = morning['recommendation'].ne('Chờ tiếp')
            tp = int((crossed & warned).sum())
            result['lunch'] = {
                'morning': int(len(morning)), 'crossed': int(crossed.sum()), 'warned': int(warned.sum()),
                'precision': _r(tp / warned.sum(), 3) if warned.sum() else None,
                'recall': _r(tp / crossed.sum(), 3) if crossed.sum() else None,
            }
        return result

    # ------------------------------------------------------------------ live
    def live(self, f: dict[str, Any]) -> dict[str, Any]:
        now = self.ops.clock.now()
        tickets = self.ops.tickets_at(now, room=f.get('room') or None, department=f.get('department') or None)
        rooms: dict[str, dict[str, Any]] = {}
        overdue = []
        for t in tickets:
            room = rooms.setdefault(t['ma_phong'], {'waiting': 0, 'in_exam': 0, 'done': 0, 'arrivals_30m': 0,
                                                    'starts_30m': 0, 'pred': [], 'waited': [], 'lunch': 0,
                                                    'actual': []})
            if t['queue_entry_time'] >= now - pd.Timedelta(minutes=30):
                room['arrivals_30m'] += 1
            start = pd.Timestamp(t['start_time']) if t.get('start_time') else None
            if start is not None and start >= now - pd.Timedelta(minutes=30):
                room['starts_30m'] += 1
            if start is not None:
                room['actual'].append((start - t['queue_entry_time']).total_seconds() / 60)
            if t['status'] in {'waiting', 'called'}:
                room['waiting'] += 1
                waited = (now - t['queue_entry_time']).total_seconds() / 60
                room['waited'].append(waited)
                if t['predicted_wait_minutes'] is not None:
                    room['pred'].append(t['predicted_wait_minutes'])
                if t.get('lunch_warning'):
                    room['lunch'] += 1
                p80 = t.get('predicted_wait_p80_minutes')
                if p80 is not None and waited - p80 > self.config.alert_overdue_minutes:
                    overdue.append(self.ops._serialize(t, now) | {'room': self.ops.room_info(t['ma_phong'])})
            elif t['status'] == 'in_exam':
                room['in_exam'] += 1
            elif t['status'] == 'done':
                room['done'] += 1

        room_rows, alerts = [], []
        for code, r in rooms.items():
            info = self.ops.room_info(code)
            median_pred = float(np.median(r['pred'])) if r['pred'] else None
            row = info | {
                'waiting': r['waiting'], 'in_exam': r['in_exam'], 'done': r['done'],
                'arrivals_30m': r['arrivals_30m'], 'starts_30m': r['starts_30m'],
                'median_predicted_wait': _r(median_pred), 'max_waited': _r(max(r['waited'], default=None)),
                'avg_actual_wait_today': _r(np.mean(r['actual'])) if r['actual'] else None,
                'lunch_risk': r['lunch'],
            }
            room_rows.append(row)
            name = f"{info['ma_phong']} – {info['ten_phong']}"
            if median_pred is not None and median_pred > self.config.alert_wait_minutes:
                alerts.append({'level': 'high', 'type': 'Thời gian chờ cao', 'room': code,
                               'message': f'{name}: thời gian chờ dự kiến trung vị {median_pred:.0f} phút '
                                          f'(ngưỡng {self.config.alert_wait_minutes:.0f}).'})
            if r['waiting'] > self.config.alert_queue_length:
                alerts.append({'level': 'high', 'type': 'Hàng đợi dài', 'room': code,
                               'message': f'{name}: {r["waiting"]} người đang chờ '
                                          f'(ngưỡng {self.config.alert_queue_length}).'})
            if r['arrivals_30m'] - r['starts_30m'] >= 5 and r['waiting'] >= 8:
                alerts.append({'level': 'medium', 'type': 'Nguy cơ ùn tắc', 'room': code,
                               'message': f'{name}: 30 phút qua có {r["arrivals_30m"]} lượt vào nhưng chỉ '
                                          f'{r["starts_30m"]} lượt được khám.'})
            if r['lunch']:
                alerts.append({'level': 'medium', 'type': 'Nguy cơ qua giờ nghỉ trưa', 'room': code,
                               'message': f'{name}: {r["lunch"]} người bệnh buổi sáng được cảnh báo có thể phải '
                                          f'chờ qua giờ nghỉ trưa.'})
        by_room_overdue: dict[str, int] = {}
        for t in overdue:
            by_room_overdue[t['ma_phong']] = by_room_overdue.get(t['ma_phong'], 0) + 1
        for code, n in by_room_overdue.items():
            info = self.ops.room_info(code)
            alerts.append({'level': 'medium', 'type': 'Chờ vượt dự báo', 'room': code,
                           'message': f"{info['ma_phong']} – {info['ten_phong']}: {n} người đã chờ lâu hơn cận "
                                      f"trên P80 quá {self.config.alert_overdue_minutes:.0f} phút."})
        alerts.extend(self._upcoming_risk(now, f))
        room_rows.sort(key=lambda r: -r['waiting'])
        order = {'high': 0, 'medium': 1, 'info': 2}
        alerts.sort(key=lambda a: order[a['level']])
        waiting_total = sum(r['waiting'] for r in room_rows)
        started_today = [t for t in tickets if t.get('start_time')]
        actual = [(pd.Timestamp(t['start_time']) - t['queue_entry_time']).total_seconds() / 60 for t in started_today]
        return {
            'now': str(now), 'rooms': room_rows, 'alerts': alerts,
            'overdue': sorted(overdue, key=lambda t: -(t['overdue_minutes'] or 0))[:20],
            'kpi': {'arrivals_today': len(tickets), 'waiting': waiting_total,
                    'in_exam': sum(r['in_exam'] for r in room_rows), 'done': sum(r['done'] for r in room_rows),
                    'avg_actual_wait_today': _r(np.mean(actual)) if actual else None,
                    'active_rooms': sum(1 for r in room_rows if r['waiting'] or r['in_exam'])},
            'thresholds': {'wait': self.config.alert_wait_minutes, 'queue': self.config.alert_queue_length,
                           'overdue': self.config.alert_overdue_minutes},
            'web_forecast': self._web_forecast(),
        }

    def _upcoming_risk(self, now: pd.Timestamp, f: dict[str, Any]) -> list[dict[str, Any]]:
        """Hours in the next 3 h that were historically overloaded on this weekday
        (8 previous weeks, data strictly before today)."""
        past = self.history.loc[(self.history['date'] < now.normalize())
                                & (self.history['date'] >= now.normalize() - pd.Timedelta(weeks=8))
                                & (self.history['dow'] == now.dayofweek)]
        past = self._filter(past, {k: f.get(k) for k in ('department', 'room')})
        if past.empty:
            return []
        days = past['date'].nunique()
        per_hour = past.groupby('hour').agg(n=('waiting_minutes', 'size'), median=('waiting_minutes', 'median'))
        per_hour['per_day'] = per_hour['n'] / days
        busy = per_hour['per_day'].quantile(0.75)
        alerts = []
        for hour in range(now.hour + 1, min(now.hour + 4, 24)):
            if hour not in per_hour.index:
                continue
            row = per_hour.loc[hour]
            if row['per_day'] >= busy and row['median'] >= 20:
                alerts.append({'level': 'info', 'type': 'Khung giờ cao điểm sắp tới', 'room': None,
                               'message': f'{hour:02d}:00–{hour:02d}:59 {WEEKDAYS[now.dayofweek]}: trung bình '
                                          f'{row["per_day"]:.0f} lượt/ngày, thời gian chờ trung vị '
                                          f'{row["median"]:.0f} phút (8 tuần gần nhất).'})
        return alerts

    def _web_forecast(self) -> dict[str, Any]:
        tickets = [t for t in self.ops.store.tickets("t.start_time IS NOT NULL AND t.prediction_status = 'ok'")]
        rows = []
        for t in tickets:
            actual = (pd.Timestamp(t['start_time']) - pd.Timestamp(t['queue_entry_time'])).total_seconds() / 60
            rows.append({'id': t['id'], 'stt': t['stt'], 'display_stt': web_number(t['stt']), 'room': t['ma_phong_thuc_hien'],
                         'queue_entry_time': t['queue_entry_time'], 'predicted': _r(t['predicted_wait_minutes']),
                         'p80': _r(t['predicted_wait_p80_minutes']), 'actual': _r(actual),
                         'error': _r(t['predicted_wait_minutes'] - actual)})
        errors = np.array([abs(r['error']) for r in rows]) if rows else np.array([])
        return {'n': len(rows), 'mae': _r(errors.mean(), 2) if len(errors) else None,
                'within_10': _r(100 * (errors <= 10).mean()) if len(errors) else None,
                'rows': rows[-30:][::-1]}
