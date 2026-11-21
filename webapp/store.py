"""SQLite storage for patients, receptions and queue tickets created in the app,
plus the shared replay clock."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS patients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ma_nb TEXT UNIQUE NOT NULL,
    ho_ten TEXT NOT NULL,
    ngay_sinh TEXT NOT NULL,
    gioi_tinh TEXT NOT NULL,
    so_dien_thoai TEXT,
    cccd TEXT,
    dia_chi TEXT,
    tinh_xa TEXT NOT NULL,
    nghe_nghiep TEXT,
    dan_toc TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ma_ho_so TEXT UNIQUE NOT NULL,
    patient_id INTEGER NOT NULL REFERENCES patients(id),
    reception_time TEXT NOT NULL,
    doi_tuong TEXT NOT NULL,
    loai_doi_tuong TEXT NOT NULL,
    so_bhyt TEXT,
    bhyt_tu_ngay TEXT,
    bhyt_den_ngay TEXT,
    noi_dang_ky_bhyt TEXT,
    uu_tien INTEGER NOT NULL,
    cap_cuu INTEGER NOT NULL,
    loai_kham TEXT,
    ly_do_kham TEXT
);
CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    visit_id INTEGER NOT NULL REFERENCES visits(id),
    ma_dich_vu TEXT NOT NULL,
    ma_phong_thuc_hien TEXT NOT NULL,
    ma_khoa_thuc_hien TEXT NOT NULL,
    bac_si TEXT,
    stt INTEGER NOT NULL,
    ordered_time TEXT NOT NULL,
    queue_entry_time TEXT NOT NULL,
    status TEXT NOT NULL,
    called_time TEXT,
    call_count INTEGER NOT NULL DEFAULT 0,
    start_time TEXT,
    end_time TEXT,
    skipped_time TEXT,
    prediction_status TEXT NOT NULL,
    out_of_scope_reason TEXT,
    predicted_wait_minutes REAL,
    predicted_wait_p80_minutes REAL,
    predicted_exam_time TEXT,
    probability_cross_lunch REAL,
    lunch_warning INTEGER,
    suggested_return_time TEXT,
    model_artifact TEXT,
    features_json TEXT
);
CREATE INDEX IF NOT EXISTS tickets_room ON tickets(ma_phong_thuc_hien, queue_entry_time);
"""

TIME_FORMAT = '%Y-%m-%d %H:%M:%S'


def fmt(ts: pd.Timestamp | None) -> str | None:
    return None if ts is None or pd.isna(ts) else pd.Timestamp(ts).strftime(TIME_FORMAT)


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        with self.connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            db = sqlite3.connect(self.path)
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            finally:
                db.close()

    # --- settings -------------------------------------------------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
        return default if row is None else json.loads(row['value'])

    def set_setting(self, key: str, value: Any) -> None:
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)', (key, json.dumps(value)))

    # --- rows -------------------------------------------------------------
    def insert(self, db: sqlite3.Connection, table: str, row: dict[str, Any]) -> int:
        columns = ', '.join(row)
        marks = ', '.join('?' for _ in row)
        cursor = db.execute(f'INSERT INTO {table} ({columns}) VALUES ({marks})', tuple(row.values()))
        return int(cursor.lastrowid)

    def tickets(self, where: str = '1=1', params: tuple = ()) -> list[dict[str, Any]]:
        query = f"""
            SELECT t.*, v.ma_ho_so, v.doi_tuong, v.loai_doi_tuong, v.uu_tien, v.cap_cuu, v.loai_kham,
                   v.reception_time, p.ma_nb, p.ho_ten, p.ngay_sinh, p.gioi_tinh, p.so_dien_thoai
            FROM tickets t JOIN visits v ON v.id = t.visit_id JOIN patients p ON p.id = v.patient_id
            WHERE {where} ORDER BY t.queue_entry_time"""
        with self.connect() as db:
            return [dict(row) for row in db.execute(query, params).fetchall()]

    def update_ticket(self, ticket_id: str, **values: Any) -> None:
        assignments = ', '.join(f'{key} = ?' for key in values)
        with self.connect() as db:
            db.execute(f'UPDATE tickets SET {assignments} WHERE id = ?', (*values.values(), ticket_id))

    def count(self, table: str, where: str = '1=1', params: tuple = ()) -> int:
        with self.connect() as db:
            return int(db.execute(f'SELECT COUNT(*) FROM {table} WHERE {where}', params).fetchone()[0])

    def reset_operations(self) -> None:
        with self.connect() as db:
            db.executescript('DELETE FROM tickets; DELETE FROM visits; DELETE FROM patients;')


class ReplayClock:
    """Simulated hospital clock shared by every screen.

    The web app replays a real HIS day so queue features come from real
    events; ``now()`` advances with wall time multiplied by ``speed`` and can
    be paused or moved (only inside the period covered by the data).
    """

    def __init__(self, store: Store, default_start: str, min_date: pd.Timestamp, max_date: pd.Timestamp):
        self.store = store
        self.min_date = min_date.normalize()
        self.max_date = max_date.normalize() + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
        clock = store.get_setting('clock')
        # First run, or a running clock that has already played past the last day of data.
        if clock is None or (not clock['paused'] and self.now() >= self.max_date):
            self._save(pd.Timestamp(default_start), 1.0, False)

    def _save(self, sim: pd.Timestamp, speed: float, paused: bool) -> None:
        self.store.set_setting('clock', {'anchor_real': time.time(), 'anchor_sim': fmt(sim),
                                         'speed': speed, 'paused': paused})

    def state(self) -> dict[str, Any]:
        clock = self.store.get_setting('clock')
        return {'now': fmt(self.now()), 'speed': clock['speed'], 'paused': clock['paused'],
                'min': fmt(self.min_date), 'max': fmt(self.max_date)}

    def now(self) -> pd.Timestamp:
        clock = self.store.get_setting('clock')
        sim = pd.Timestamp(clock['anchor_sim'])
        if not clock['paused']:
            sim += pd.Timedelta(seconds=(time.time() - clock['anchor_real']) * clock['speed'])
        return min(sim.floor('s'), self.max_date)

    def update(self, sim: str | None = None, speed: float | None = None, paused: bool | None = None) -> None:
        clock = self.store.get_setting('clock')
        target = pd.Timestamp(sim) if sim else self.now()
        if not (self.min_date <= target <= self.max_date):
            raise ValueError(
                f'Chỉ phát lại được trong giai đoạn có dữ liệu HIS: {self.min_date:%d/%m/%Y} – {self.max_date:%d/%m/%Y}.'
            )
        self._save(target, float(speed if speed is not None else clock['speed']),
                   bool(paused if paused is not None else clock['paused']))
