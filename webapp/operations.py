"""Queue operations: reception, prediction, ticketing and room boards.

The queue a room sees at the replay clock's ``now`` is the union of
* real HIS exams of the replayed day (status derived from their recorded
  queue-entry, exam-start and conclusion times), and
* tickets created through the web app (status changed by room staff).
Model features always use both, so a patient registered here is predicted
against the real load of the hospital at that moment.
"""

from __future__ import annotations

import json
import threading
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from pipeline.feature_engineering import parse_time_of_day
from pipeline.pipeline_config import PipelineConfig
from webapp.config import WebConfig
from webapp.feature_catalog import FEATURE_INFO
from webapp.features import EventIndex, build_feature_frame, web_events_frame
from webapp.history import HistoricalData
from webapp.model_service import WaitTimeModel
from webapp.store import ReplayClock, Store, fmt

OUT_OF_SCOPE_SERVICES = {'KB008': 'Dịch vụ khám nội KB008 là dịch vụ phụ thuộc, nằm ngoài phạm vi mô hình (cohort V3).'}
EMERGENCY_REASON = ('Ca cấp cứu nằm ngoài phạm vi mô hình: dữ liệu huấn luyện không có ca cấp cứu nào '
                    '(100% cap_cuu = False). Chuyển luồng cấp cứu, không dự báo thời gian chờ.')
STATUS_LABELS = {
    'waiting': 'Đang chờ', 'soon': 'Sắp tới lượt', 'called': 'Đang gọi', 'in_exam': 'Đang khám',
    'done': 'Hoàn thành', 'skipped': 'Bỏ lượt', 'cancelled': 'Đã hủy',
}
ACTIONS = {
    'call': ({'waiting', 'skipped'}, 'called'),
    'recall': ({'called'}, 'called'),
    'start': ({'waiting', 'called'}, 'in_exam'),
    'finish': ({'in_exam'}, 'done'),
    'skip': ({'waiting', 'called'}, 'skipped'),
    'restore': ({'skipped'}, 'waiting'),
    'cancel': ({'waiting', 'skipped'}, 'cancelled'),
}
SOON_COUNT = 3
ROOM_WINDOW_DAYS = 42


class ValidationError(ValueError):
    def __init__(self, errors: dict[str, str]):
        super().__init__('; '.join(errors.values()))
        self.errors = errors


def mask_name(name: str | None) -> str:
    if not name:
        return 'Người bệnh'
    parts = name.strip().upper().split()
    if len(parts) <= 2:
        return ' '.join(parts)
    return ' '.join([parts[0], *[p[0] + '.' for p in parts[1:-1]], parts[-1]])


def web_number(stt: int) -> str:
    """Queue number shown for tickets created in the app (distinct from replayed HIS numbers)."""
    return f'W{stt}'


def _num(value: Any) -> float | None:
    return None if value is None or pd.isna(value) else float(value)


class Operations:
    def __init__(self, config: WebConfig, history: HistoricalData, model: WaitTimeModel, store: Store):
        self.config = config
        self.history = history
        self.model = model
        self.store = store
        self.lunch_start = model.pipeline_config.lunch_start
        self.lunch_window_days = PipelineConfig().lunch_history_window_days
        cohort = history.cohort
        self.his_index = EventIndex.from_history(cohort, parse_time_of_day(self.lunch_start))
        self.clock = ReplayClock(store, config.default_replay_start,
                                 cohort['queue_entry_time'].min(), cohort['queue_entry_time'].max())
        self.rooms = {row.ma_phong_thuc_hien: row._asdict() for row in history.rooms.itertuples(index=False)}
        self.departments = dict(zip(history.departments['ma_khoa_thuc_hien'], history.departments['ten_khoa']))
        self.services = dict(zip(history.services['ma_dich_vu'], history.services['ten_dich_vu']))
        self.model_rooms = self.model.known_categories['ma_phong_thuc_hien']
        self.model_services = self.model.known_categories['ma_dich_vu']
        self._entry_dates = cohort['queue_entry_time'].dt.date.to_numpy()
        self._day_cache: dict[date, pd.DataFrame] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ catalog
    def room_info(self, code: str) -> dict[str, Any]:
        room = self.rooms.get(code, {})
        dept = room.get('ma_khoa_thuc_hien')
        return {'ma_phong': code, 'ten_phong': room.get('ten_phong') or code, 'dia_diem': room.get('dia_diem'),
                'ma_khoa': dept, 'ten_khoa': self.departments.get(dept, dept)}

    def catalog(self) -> dict[str, Any]:
        services = [
            {'ma': code, 'ten': self.services.get(code, code),
             'in_scope': code in self.model_services and code not in OUT_OF_SCOPE_SERVICES,
             'note': OUT_OF_SCOPE_SERVICES.get(code)}
            for code in sorted(set(self.services) | self.model_services)
        ]
        rooms = [self.room_info(code) | {'in_model': code in self.model_rooms} for code in sorted(self.rooms)]
        departments = [{'ma': code, 'ten': name} for code, name in sorted(self.departments.items())]
        doctors = sorted(self.history.cohort['ma_bac_si_kham'].dropna().unique().tolist())
        return {'services': services, 'rooms': rooms, 'departments': departments, 'doctors': doctors}

    # -------------------------------------------------------------- web state
    def web_tickets(self, until: pd.Timestamp | None = None) -> list[dict[str, Any]]:
        if until is None:
            return self.store.tickets()
        return self.store.tickets('t.queue_entry_time <= ?', (fmt(until),))

    def _indexes(self, now: pd.Timestamp) -> list[EventIndex]:
        web = [t for t in self.web_tickets(now) if t['status'] != 'cancelled']
        if not web:
            return [self.his_index]
        # A staff action recorded after `now` (clock moved back) is not known yet at `now`.
        visible = []
        for ticket in web:
            ticket = dict(ticket)
            for key in ('start_time', 'skipped_time'):
                if ticket.get(key) and pd.Timestamp(ticket[key]) > now:
                    ticket[key] = None
            visible.append(ticket)
        return [self.his_index, EventIndex(web_events_frame(visible), parse_time_of_day(self.lunch_start))]

    # ------------------------------------------------------------ replay day
    def replay_day(self, day: date) -> pd.DataFrame:
        """Real HIS exams of ``day`` with the model's prediction at their queue entry."""
        with self._lock:
            if day in self._day_cache:
                return self._day_cache[day]
            rows = self.history.cohort.loc[self._entry_dates == day].reset_index(drop=True)
            if rows.empty:
                frame = rows.assign(predicted_wait_minutes=[], predicted_wait_p80_minutes=[],
                                    predicted_exam_time=[], probability_cross_lunch=[], lunch_warning=[],
                                    suggested_return_time=[])
            else:
                features = build_feature_frame(rows, [self.his_index], self.lunch_start, self.lunch_window_days)
                output = self.model.predict(features)
                frame = rows.assign(
                    predicted_wait_minutes=output['predicted_wait_minutes'].to_numpy(),
                    predicted_wait_p80_minutes=output['predicted_wait_p80_minutes'].to_numpy(),
                    predicted_exam_time=output['predicted_exam_time'].to_numpy(),
                    probability_cross_lunch=output['probability_cross_lunch'].to_numpy(),
                    lunch_warning=output['lunch_warning'].to_numpy(),
                    suggested_return_time=output['suggested_return_time'].to_numpy(),
                )
            if len(self._day_cache) >= 3:
                self._day_cache.pop(next(iter(self._day_cache)))
            self._day_cache[day] = frame
            return frame

    def _his_ticket(self, row: Any, now: pd.Timestamp) -> dict[str, Any]:
        start, complete = row.label_available_time, row.exam_complete_time
        if start > now:
            status = 'waiting'
        elif pd.isna(complete) or complete > now:
            status = 'in_exam'
        else:
            status = 'done'
        stt = None if pd.isna(row.stt) else int(row.stt)
        return {
            'id': f'HIS-{row.nb_dich_vu_id}', 'source': 'his', 'stt': stt, 'display_stt': '–' if stt is None else str(stt),
            'ma_phong': row.ma_phong_thuc_hien, 'ma_khoa': row.ma_khoa_thuc_hien, 'ma_dich_vu': row.ma_dich_vu,
            'patient': 'Người bệnh (HIS)', 'uu_tien': str(row.uu_tien) == 'True',
            'doi_tuong': row.patient_payment_type, 'queue_entry_time': row.queue_entry_time, 'status': status,
            'called_time': None, 'start_time': start if start <= now else None,
            'end_time': complete if status == 'done' else None,
            'predicted_wait_minutes': _num(row.predicted_wait_minutes),
            'predicted_wait_p80_minutes': _num(row.predicted_wait_p80_minutes),
            'predicted_exam_time': row.predicted_exam_time, 'lunch_warning': bool(row.lunch_warning),
            'suggested_return_time': row.suggested_return_time if isinstance(row.suggested_return_time, str) else None,
            'prediction_status': 'ok', 'call_count': 0,
        }

    def _web_ticket(self, t: dict[str, Any], now: pd.Timestamp) -> dict[str, Any]:
        status = t['status']
        return {
            'id': t['id'], 'source': 'web', 'stt': t['stt'], 'display_stt': web_number(t['stt']),
            'ma_phong': t['ma_phong_thuc_hien'],
            'ma_khoa': t['ma_khoa_thuc_hien'], 'ma_dich_vu': t['ma_dich_vu'], 'patient': mask_name(t['ho_ten']),
            'ho_ten': t['ho_ten'], 'ma_nb': t['ma_nb'], 'uu_tien': bool(t['uu_tien']), 'doi_tuong': t['doi_tuong'],
            'queue_entry_time': pd.Timestamp(t['queue_entry_time']), 'status': status,
            'called_time': t['called_time'], 'start_time': t['start_time'], 'end_time': t['end_time'],
            'skipped_time': t['skipped_time'],
            'predicted_wait_minutes': t['predicted_wait_minutes'],
            'predicted_wait_p80_minutes': t['predicted_wait_p80_minutes'],
            'predicted_exam_time': pd.Timestamp(t['predicted_exam_time']) if t['predicted_exam_time'] else None,
            'lunch_warning': bool(t['lunch_warning']), 'suggested_return_time': t['suggested_return_time'],
            'prediction_status': t['prediction_status'], 'out_of_scope_reason': t['out_of_scope_reason'],
            'call_count': t['call_count'],
        }

    def tickets_at(self, now: pd.Timestamp, room: str | None = None, department: str | None = None
                   ) -> list[dict[str, Any]]:
        day = self.replay_day(now.date())
        visible = day.loc[day['queue_entry_time'] <= now]
        if room:
            visible = visible.loc[visible['ma_phong_thuc_hien'] == room]
        if department:
            visible = visible.loc[visible['ma_khoa_thuc_hien'] == department]
        tickets = [self._his_ticket(row, now) for row in visible.itertuples(index=False)]
        day_start = fmt(now.normalize())
        for t in self.store.tickets('t.queue_entry_time >= ? AND t.queue_entry_time <= ?', (day_start, fmt(now))):
            if (room and t['ma_phong_thuc_hien'] != room) or (department and t['ma_khoa_thuc_hien'] != department):
                continue
            tickets.append(self._web_ticket(t, now))
        return tickets

    @staticmethod
    def _serialize(ticket: dict[str, Any], now: pd.Timestamp) -> dict[str, Any]:
        out = dict(ticket)
        entry = ticket['queue_entry_time']
        start = pd.Timestamp(ticket['start_time']) if ticket.get('start_time') else None
        waited_until = start if start is not None else now
        out['waited_minutes'] = round((waited_until - entry).total_seconds() / 60, 1)
        out['actual_wait_minutes'] = out['waited_minutes'] if start is not None else None
        exam_time = ticket.get('predicted_exam_time')
        out['remaining_minutes'] = (
            round((exam_time - now).total_seconds() / 60, 1)
            if exam_time is not None and not pd.isna(exam_time) and ticket['status'] in {'waiting', 'soon', 'called'}
            else None
        )
        out['overdue_minutes'] = (
            round(out['waited_minutes'] - ticket['predicted_wait_p80_minutes'], 1)
            if ticket.get('predicted_wait_p80_minutes') is not None and ticket['status'] in {'waiting', 'soon'}
            else None
        )
        out['status_label'] = STATUS_LABELS.get(ticket['status'], ticket['status'])
        for key in ('queue_entry_time', 'predicted_exam_time', 'start_time', 'end_time', 'called_time',
                    'skipped_time'):
            if key in out:
                value = out[key]
                out[key] = None if value is None or (not isinstance(value, str) and pd.isna(value)) else fmt(
                    pd.Timestamp(value))
        return out

    # -------------------------------------------------------------- boards
    def room_board(self, room: str) -> dict[str, Any]:
        now = self.clock.now()
        tickets = self.tickets_at(now, room=room)
        waiting = sorted([t for t in tickets if t['status'] == 'waiting'], key=lambda t: (t['stt'] or 0))
        for position, ticket in enumerate(waiting):
            ticket['status'] = 'soon' if position < SOON_COUNT else 'waiting'
            ticket['position'] = position + 1
        called = sorted([t for t in tickets if t['status'] == 'called'], key=lambda t: t['called_time'] or '')
        in_exam = sorted([t for t in tickets if t['status'] == 'in_exam'], key=lambda t: str(t['start_time']))
        done = [t for t in tickets if t['status'] == 'done']
        skipped = [t for t in tickets if t['status'] == 'skipped']
        started = [t for t in tickets if t.get('start_time')]
        waits = [(pd.Timestamp(t['start_time']) - t['queue_entry_time']).total_seconds() / 60 for t in started]
        recent_pred = [t['predicted_wait_minutes'] for t in tickets
                       if t['predicted_wait_minutes'] is not None
                       and t['queue_entry_time'] >= now - pd.Timedelta(minutes=60)]
        ser = lambda items: [self._serialize(t, now) for t in items]  # noqa: E731
        return {
            'now': fmt(now), 'room': self.room_info(room),
            'called': ser(called[-3:]), 'in_exam': ser(in_exam[-6:]), 'waiting': ser(waiting),
            'skipped': ser(skipped), 'done': ser(sorted(done, key=lambda t: str(t['end_time']))[-10:]),
            'stats': {
                'waiting': len(waiting), 'in_exam': len(in_exam), 'called': len(called), 'done': len(done),
                'skipped': len(skipped), 'total_today': len(tickets),
                'avg_actual_wait_today': round(float(np.mean(waits)), 1) if waits else None,
                'avg_predicted_wait_60m': round(float(np.mean(recent_pred)), 1) if recent_pred else None,
            },
        }

    def area_board(self, department: str | None) -> dict[str, Any]:
        now = self.clock.now()
        tickets = self.tickets_at(now, department=department)
        by_room: dict[str, list[dict[str, Any]]] = {}
        for ticket in tickets:
            by_room.setdefault(ticket['ma_phong'], []).append(ticket)
        rooms = []
        for code, items in by_room.items():
            waiting = sorted([t for t in items if t['status'] == 'waiting'], key=lambda t: t['stt'] or 0)
            in_exam = sorted([t for t in items if t['status'] in {'in_exam', 'called'}],
                             key=lambda t: str(t['called_time'] or t['start_time']))
            if not waiting and not in_exam:
                continue
            next_times = [t['predicted_exam_time'] for t in waiting if t['predicted_exam_time'] is not None]
            rooms.append({
                'room': self.room_info(code),
                'current': [t['display_stt'] for t in in_exam[-3:]],
                'calling': [t['display_stt'] for t in items if t['status'] == 'called'],
                'next': [t['display_stt'] for t in waiting[:5]],
                'waiting': len(waiting),
                'lunch_warnings': sum(bool(t.get('lunch_warning')) for t in waiting),
                'median_predicted_wait': round(float(np.median([t['predicted_wait_minutes'] for t in waiting
                                                                if t['predicted_wait_minutes'] is not None])), 0)
                if any(t['predicted_wait_minutes'] is not None for t in waiting) else None,
                'last_expected_time': fmt(max(next_times)) if next_times else None,
            })
        rooms.sort(key=lambda r: r['room']['ma_phong'])
        return {'now': fmt(now), 'department': department, 'rooms': rooms,
                'department_name': self.departments.get(department) if department else 'Toàn bệnh viện'}

    # ----------------------------------------------------------- prediction
    def next_stt(self, room: str, now: pd.Timestamp, tickets: list[dict[str, Any]] | None = None) -> int:
        tickets = tickets if tickets is not None else self.tickets_at(now, room=room)
        numbers = [t['stt'] for t in tickets if t['stt'] is not None and t['ma_phong'] == room]
        # The next number HIS would issue now: this is also the model's `stt` input.
        # Replayed HIS rows may later reuse it, so web tickets are *displayed* as W<stt>.
        return int(max(numbers, default=0)) + 1

    def validate_model_inputs(self, data: dict[str, Any]) -> dict[str, str]:
        errors: dict[str, str] = {}
        service = data.get('ma_dich_vu')
        if not service:
            errors['ma_dich_vu'] = 'Vui lòng chọn Dịch vụ khám trước khi dự báo thời gian chờ.'
        elif service not in self.model_services and service not in OUT_OF_SCOPE_SERVICES:
            errors['ma_dich_vu'] = f'Dịch vụ {service} không có trong danh mục của mô hình.'
        room = data.get('ma_phong_thuc_hien')
        if not room:
            errors['ma_phong_thuc_hien'] = 'Vui lòng chọn Phòng khám trước khi dự báo thời gian chờ.'
        elif room not in self.model_rooms:
            errors['ma_phong_thuc_hien'] = f'Phòng {room} không có trong danh mục phòng của mô hình.'
        if data.get('patient_payment_type') not in {'bhyt', 'dich_vu'}:
            errors['patient_payment_type'] = ('Vui lòng chọn Đối tượng người bệnh (BHYT / Không bảo hiểm) '
                                              'ở bước Thông tin hành chính.')
        for key, label in (('uu_tien', 'Ưu tiên'), ('cap_cuu', 'Cấp cứu')):
            if not isinstance(data.get(key), bool):
                errors[key] = f'Thiếu thông tin "{label}" (có/không).'
        return errors

    def out_of_scope_reason(self, data: dict[str, Any]) -> str | None:
        if data.get('ma_dich_vu') in OUT_OF_SCOPE_SERVICES:
            return OUT_OF_SCOPE_SERVICES[data['ma_dich_vu']]
        if data.get('cap_cuu'):
            return EMERGENCY_REASON
        return None

    def _query_rows(self, data: dict[str, Any], rooms: list[str], now: pd.Timestamp, ordered_time: pd.Timestamp,
                    tickets: list[dict[str, Any]]) -> pd.DataFrame:
        rows = []
        for room in rooms:
            rows.append({
                'nb_dich_vu_id': room, 'queue_entry_time': now, 'ma_dich_vu': data['ma_dich_vu'],
                'ma_phong_thuc_hien': room, 'ma_khoa_thuc_hien': self.rooms[room]['ma_khoa_thuc_hien'],
                'queue_entry_status': '40', 'uu_tien': str(bool(data['uu_tien'])),
                'cap_cuu': str(bool(data['cap_cuu'])), 'patient_payment_type': data['patient_payment_type'],
                'stt': float(self.next_stt(room, now, [t for t in tickets if t['ma_phong'] == room])),
                'order_to_queue_minutes': (now - ordered_time).total_seconds() / 60.0,
            })
        return pd.DataFrame(rows)

    def _ordered_time(self, data: dict[str, Any], now: pd.Timestamp) -> pd.Timestamp:
        value = data.get('ordered_time')
        if not value:
            return now
        ordered = pd.Timestamp(value)
        if ordered > now or now - ordered > pd.Timedelta(hours=24):
            raise ValidationError({'ordered_time': 'Thời điểm chỉ định không hợp lệ so với đồng hồ hệ thống.'})
        return ordered

    def predict(self, data: dict[str, Any]) -> dict[str, Any]:
        errors = self.validate_model_inputs(data)
        if errors:
            raise ValidationError(errors)
        now = self.clock.now()
        room = data['ma_phong_thuc_hien']
        reason = self.out_of_scope_reason(data)
        tickets = self.tickets_at(now, room=room)
        stt = self.next_stt(room, now, tickets)
        base = {'now': fmt(now), 'queue_entry_time': fmt(now), 'stt': stt, 'display_stt': web_number(stt),
                'room': self.room_info(room),
                'model': self.model.metadata()}
        if reason:
            return base | {'status': 'out_of_scope', 'reason': reason}
        ordered_time = self._ordered_time(data, now)
        features = build_feature_frame(self._query_rows(data, [room], now, ordered_time, tickets),
                                       self._indexes(now), self.lunch_start, self.lunch_window_days)
        output = self.model.predict(features).iloc[0]
        row = features.iloc[0]
        inputs = []
        for name in self.model.feature_names:
            label, source = FEATURE_INFO.get(name, (name, ''))
            value = row[name]
            if not isinstance(value, str):
                value = None if pd.isna(value) else float(value)
            inputs.append({'name': name, 'label': label, 'source': source, 'value': value})
        return base | {
            'status': 'ok',
            'ordered_time': fmt(ordered_time),
            'prediction': {
                'wait_minutes': float(output['predicted_wait_minutes']),
                'wait_p80_minutes': float(output['predicted_wait_p80_minutes']),
                'predicted_exam_time': fmt(output['predicted_exam_time']),
                'probability_cross_lunch': _num(output['probability_cross_lunch']),
                'lunch_warning': bool(output['lunch_warning']),
                'recommendation': output['recommendation'],
                'suggested_return_time': output['suggested_return_time'] if isinstance(
                    output['suggested_return_time'], str) else None,
            },
            'inputs': inputs,
        }

    def room_options(self, data: dict[str, Any]) -> dict[str, Any]:
        """Rooms that performed the service in the last 42 days, with live load and
        the model's prediction for *this* patient if sent to each room."""
        now = self.clock.now()
        service = data.get('ma_dich_vu')
        cohort = self.history.cohort
        window = cohort.loc[(cohort['ma_dich_vu'] == service)
                            & (cohort['queue_entry_time'] >= now - pd.Timedelta(days=ROOM_WINDOW_DAYS))
                            & (cohort['queue_entry_time'] < now)]
        department = data.get('ma_khoa')
        rooms = sorted(set(window['ma_phong_thuc_hien']) & self.model_rooms)
        if department:
            rooms = [r for r in rooms if self.rooms.get(r, {}).get('ma_khoa_thuc_hien') == department]
        tickets = self.tickets_at(now)
        recent = now - pd.Timedelta(hours=2)
        result = []
        for room in rooms:
            items = [t for t in tickets if t['ma_phong'] == room]
            waiting = [t for t in items if t['status'] in {'waiting', 'called'}]
            active = bool(waiting) or any(
                t['queue_entry_time'] >= recent or (t.get('start_time') and pd.Timestamp(t['start_time']) >= recent)
                for t in items)
            result.append(self.room_info(room) | {
                'waiting_normal': sum(not t['uu_tien'] for t in waiting),
                'waiting_priority': sum(t['uu_tien'] for t in waiting),
                'in_exam': sum(t['status'] == 'in_exam' for t in items),
                'done': sum(t['status'] == 'done' for t in items),
                'total': len(items), 'active': active,
                'service_volume_42d': int((window['ma_phong_thuc_hien'] == room).sum()),
            })
        errors = self.validate_model_inputs(data)
        errors.pop('ma_phong_thuc_hien', None)  # choosing the room is what this table is for
        reason = self.out_of_scope_reason(data) if not errors else None
        if rooms and not errors and not reason:
            ordered_time = self._ordered_time(data, now)
            features = build_feature_frame(self._query_rows(data, rooms, now, ordered_time, tickets),
                                           self._indexes(now), self.lunch_start, self.lunch_window_days)
            output = self.model.predict(features)
            for item, (_, prediction) in zip(result, output.iterrows()):
                item['predicted_wait_minutes'] = float(prediction['predicted_wait_minutes'])
                item['predicted_wait_p80_minutes'] = float(prediction['predicted_wait_p80_minutes'])
                item['predicted_exam_time'] = fmt(prediction['predicted_exam_time'])
                item['lunch_warning'] = bool(prediction['lunch_warning'])
                item['probability_cross_lunch'] = _num(prediction['probability_cross_lunch'])
                item['suggested_return_time'] = (prediction['suggested_return_time']
                                                 if isinstance(prediction['suggested_return_time'], str) else None)
        result.sort(key=lambda r: (not r['active'], r.get('predicted_wait_minutes') or 1e9))
        return {'now': fmt(now), 'rooms': result, 'missing': errors, 'out_of_scope_reason': reason}

    # ------------------------------------------------------------ reception
    def register(self, admin: dict[str, Any], service: dict[str, Any]) -> dict[str, Any]:
        # Patient-level model inputs are captured once, on the administrative form.
        service = service | {'patient_payment_type': admin.get('doi_tuong'), 'uu_tien': admin.get('uu_tien'),
                             'cap_cuu': admin.get('cap_cuu')}
        errors = self.validate_admin(admin) | self.validate_model_inputs(service)
        if errors:
            raise ValidationError(errors)
        prediction = self.predict(service)
        now = pd.Timestamp(prediction['now'])
        room = service['ma_phong_thuc_hien']
        with self.store.connect() as db:
            patient_id = None
            if admin.get('ma_nb'):
                found = db.execute('SELECT id FROM patients WHERE ma_nb = ?', (admin['ma_nb'],)).fetchone()
                patient_id = found['id'] if found else None
            patient = {k: admin.get(k) for k in ('ho_ten', 'ngay_sinh', 'gioi_tinh', 'so_dien_thoai', 'cccd',
                                                 'dia_chi', 'tinh_xa', 'nghe_nghiep', 'dan_toc')}
            if patient_id is None:
                seq = db.execute('SELECT COUNT(*) FROM patients').fetchone()[0] + 1
                ma_nb = admin.get('ma_nb') or f'NB{now:%y%m%d}{seq:05d}'
                patient_id = self.store.insert(db, 'patients', patient | {'ma_nb': ma_nb, 'created_at': fmt(now)})
            else:
                db.execute(f"UPDATE patients SET {', '.join(f'{k} = ?' for k in patient)} WHERE id = ?",
                           (*patient.values(), patient_id))
            visit_seq = db.execute('SELECT COUNT(*) FROM visits').fetchone()[0] + 1
            visit_id = self.store.insert(db, 'visits', {
                'ma_ho_so': f'HS{now:%y%m%d}{visit_seq:05d}', 'patient_id': patient_id, 'reception_time': fmt(now),
                'doi_tuong': service['patient_payment_type'], 'loai_doi_tuong': admin['loai_doi_tuong'],
                'so_bhyt': admin.get('so_bhyt'), 'bhyt_tu_ngay': admin.get('bhyt_tu_ngay'),
                'bhyt_den_ngay': admin.get('bhyt_den_ngay'), 'noi_dang_ky_bhyt': admin.get('noi_dang_ky_bhyt'),
                'uu_tien': int(service['uu_tien']), 'cap_cuu': int(service['cap_cuu']),
                'loai_kham': admin.get('loai_kham'), 'ly_do_kham': admin.get('ly_do_kham'),
            })
            ticket_seq = db.execute('SELECT COUNT(*) FROM tickets WHERE queue_entry_time >= ?',
                                    (fmt(now.normalize()),)).fetchone()[0] + 1
            ticket_id = f'Q{now:%y%m%d}-{ticket_seq:04d}'
            ok = prediction['status'] == 'ok'
            p = prediction.get('prediction', {})
            self.store.insert(db, 'tickets', {
                'id': ticket_id, 'visit_id': visit_id, 'ma_dich_vu': service['ma_dich_vu'],
                'ma_phong_thuc_hien': room, 'ma_khoa_thuc_hien': self.rooms[room]['ma_khoa_thuc_hien'],
                'bac_si': service.get('bac_si'), 'stt': prediction['stt'],
                'ordered_time': prediction.get('ordered_time') or fmt(now), 'queue_entry_time': fmt(now),
                'status': 'waiting', 'prediction_status': prediction['status'],
                'out_of_scope_reason': prediction.get('reason'),
                'predicted_wait_minutes': p.get('wait_minutes'), 'predicted_wait_p80_minutes': p.get('wait_p80_minutes'),
                'predicted_exam_time': p.get('predicted_exam_time'),
                'probability_cross_lunch': p.get('probability_cross_lunch'),
                'lunch_warning': int(bool(p.get('lunch_warning'))) if ok else None,
                'suggested_return_time': p.get('suggested_return_time'),
                'model_artifact': self.model.path.name if ok else None,
                'features_json': json.dumps({i['name']: i['value'] for i in prediction.get('inputs', [])}),
            })
        ticket = self.ticket(ticket_id)
        return {'ticket': ticket, 'prediction': prediction}

    @staticmethod
    def validate_admin(admin: dict[str, Any]) -> dict[str, str]:
        required = {
            'ho_ten': 'Vui lòng nhập Họ và tên.', 'ngay_sinh': 'Vui lòng nhập Ngày sinh.',
            'gioi_tinh': 'Vui lòng chọn Giới tính.', 'tinh_xa': 'Vui lòng nhập Phường/Xã, Tỉnh/TP.',
            'loai_doi_tuong': 'Vui lòng chọn Loại đối tượng.',
        }
        errors = {key: message for key, message in required.items() if not str(admin.get(key) or '').strip()}
        if admin.get('doi_tuong') == 'bhyt' and not str(admin.get('so_bhyt') or '').strip():
            errors['so_bhyt'] = 'Người bệnh BHYT phải có Số thẻ BHYT.'
        phone = str(admin.get('so_dien_thoai') or '').strip()
        if phone and (not phone.isdigit() or not 9 <= len(phone) <= 11):
            errors['so_dien_thoai'] = 'Số điện thoại gồm 9–11 chữ số.'
        cccd = str(admin.get('cccd') or '').strip()
        if cccd and (not cccd.isdigit() or len(cccd) not in (9, 12)):
            errors['cccd'] = 'CCCD/CMND gồm 12 (hoặc 9) chữ số.'
        return errors

    # -------------------------------------------------------------- tickets
    def ticket(self, ticket_id: str) -> dict[str, Any]:
        rows = self.store.tickets('t.id = ?', (ticket_id,))
        if not rows:
            raise KeyError(ticket_id)
        now = self.clock.now()
        out = self._serialize(self._web_ticket(rows[0], now), now)
        return out | {'room': self.room_info(rows[0]['ma_phong_thuc_hien']),
                      'service_name': self.services.get(rows[0]['ma_dich_vu']),
                      'ma_ho_so': rows[0]['ma_ho_so'], 'loai_doi_tuong': rows[0]['loai_doi_tuong'],
                      'ngay_sinh': rows[0]['ngay_sinh'], 'gioi_tinh': rows[0]['gioi_tinh']}

    def act(self, ticket_id: str, action: str) -> dict[str, Any]:
        if action not in ACTIONS:
            raise ValidationError({'action': f'Thao tác không hợp lệ: {action}'})
        rows = self.store.tickets('t.id = ?', (ticket_id,))
        if not rows:
            raise KeyError(ticket_id)
        allowed, target = ACTIONS[action]
        status = rows[0]['status']
        if status not in allowed:
            raise ValidationError({'status': f'Không thể "{action}" khi phiếu đang ở trạng thái '
                                             f'"{STATUS_LABELS.get(status, status)}".'})
        now = fmt(self.clock.now())
        values: dict[str, Any] = {'status': target}
        if action in {'call', 'recall'}:
            values |= {'called_time': now, 'call_count': rows[0]['call_count'] + 1}
        elif action == 'start':
            values['start_time'] = now
        elif action == 'finish':
            values['end_time'] = now
        elif action in {'skip', 'cancel'}:
            values['skipped_time'] = now
        elif action == 'restore':
            values['skipped_time'] = None
        self.store.update_ticket(ticket_id, **values)
        return self.ticket(ticket_id)

    def call_next(self, room: str) -> dict[str, Any]:
        board = self.room_board(room)
        candidates = [t for t in board['waiting'] if t['source'] == 'web']
        if not candidates:
            raise ValidationError({'room': 'Không còn người bệnh (tạo trên hệ thống) đang chờ trong phòng này.'})
        return self.act(candidates[0]['id'], 'call')

    def lookup(self, query: str) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return []
        rows = self.store.tickets(
            't.id = ? OR p.ma_nb = ? OR p.so_dien_thoai = ? OR p.cccd = ? OR v.ma_ho_so = ?',
            (query, query, query, query, query))
        now = self.clock.now()
        return [self._serialize(self._web_ticket(r, now), now) | {'room': self.room_info(r['ma_phong_thuc_hien'])}
                for r in rows if pd.Timestamp(r['queue_entry_time']) <= now][-5:]

    def search_patients(self, query: str) -> list[dict[str, Any]]:
        like = f'%{query.strip()}%'
        with self.store.connect() as db:
            rows = db.execute(
                'SELECT * FROM patients WHERE ma_nb LIKE ? OR ho_ten LIKE ? OR so_dien_thoai LIKE ? OR cccd LIKE ? '
                'ORDER BY id DESC LIMIT 10', (like, like, like, like)).fetchall()
        return [dict(r) for r in rows]
