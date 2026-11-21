from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from pipeline.feature_engineering import add_lunch_break_features, feature_columns, parse_time_of_day
from pipeline.optimize_on_development import add_causal_features
from pipeline.pipeline_config import PipelineConfig
from webapp.features import EventIndex, build_feature_frame, web_events_frame

CONFIG = PipelineConfig()
QUERY_COLUMNS = ['nb_dich_vu_id', 'queue_entry_time', 'ma_dich_vu', 'ma_phong_thuc_hien', 'ma_khoa_thuc_hien',
                 'queue_entry_status', 'uu_tien', 'cap_cuu', 'patient_payment_type', 'stt', 'order_to_queue_minutes']


def synthetic_history(rows: int = 400, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = pd.to_datetime(['2026-05-04', '2026-05-05', '2026-05-06', '2026-05-07'])
    entry = days[rng.integers(0, len(days), rows)] + pd.to_timedelta(rng.integers(6 * 60, 16 * 60, rows), unit='m')
    wait = rng.gamma(2.0, 12.0, rows)
    rooms = rng.choice(['P1', 'P2', 'P3'], rows)
    data = pd.DataFrame({
        'nb_dich_vu_id': np.arange(rows), 'queue_entry_time': entry, 'waiting_minutes': wait,
        'ma_phong_thuc_hien': rooms, 'ma_khoa_thuc_hien': np.where(rooms == 'P3', 'K2', 'K1'),
        'ma_dich_vu': rng.choice(['KB001', 'KB004'], rows), 'queue_entry_status': '40',
        'uu_tien': rng.choice(['False', 'True'], rows), 'cap_cuu': 'False', 'patient_payment_type': 'dich_vu',
        'stt': rng.integers(1, 60, rows).astype(float), 'order_to_queue_minutes': rng.exponential(5, rows),
        'service_minutes_60_to_150': rng.gamma(2.0, 10.0, rows),
    })
    data['label_available_time'] = data['queue_entry_time'] + pd.to_timedelta(data['waiting_minutes'], unit='m')
    data['exam_complete_time'] = data['label_available_time'] + pd.to_timedelta(data['service_minutes_60_to_150'], unit='m')
    for column, values in (('queue_entry_hour', data['queue_entry_time'].dt.hour),
                           ('queue_entry_day_of_week', data['queue_entry_time'].dt.dayofweek)):
        data[column] = values
    data['queue_entry_is_weekend'] = data['queue_entry_day_of_week'].isin([5, 6]).astype('int8')
    data['queue_hour_sin'] = np.sin(2 * np.pi * data['queue_entry_hour'] / 24)
    data['queue_hour_cos'] = np.cos(2 * np.pi * data['queue_entry_hour'] / 24)
    data['queue_dow_sin'] = np.sin(2 * np.pi * data['queue_entry_day_of_week'] / 7)
    data['queue_dow_cos'] = np.cos(2 * np.pi * data['queue_entry_day_of_week'] / 7)
    return data.sort_values('queue_entry_time', kind='mergesort').reset_index(drop=True)


class WebFeatureParityTests(unittest.TestCase):
    def test_point_queries_match_training_features(self) -> None:
        history = synthetic_history()
        training = add_lunch_break_features(add_causal_features(history), CONFIG)
        index = EventIndex.from_history(history, parse_time_of_day(CONFIG.lunch_start))
        web = build_feature_frame(history[QUERY_COLUMNS], [index], CONFIG.lunch_start,
                                  CONFIG.lunch_history_window_days)
        _, numeric = feature_columns('queue_dynamics_lunch')
        for name in numeric:
            np.testing.assert_allclose(web[name].to_numpy(dtype=float), training[name].to_numpy(dtype=float),
                                       rtol=0, atol=1e-9, equal_nan=True, err_msg=name)


class WebQueueStateTests(unittest.TestCase):
    def query(self, indexes: list[EventIndex], time: str) -> pd.Series:
        row = pd.DataFrame([{
            'nb_dich_vu_id': -1, 'queue_entry_time': pd.Timestamp(time), 'ma_dich_vu': 'KB004',
            'ma_phong_thuc_hien': 'P1', 'ma_khoa_thuc_hien': 'K1', 'queue_entry_status': '40', 'uu_tien': 'False',
            'cap_cuu': 'False', 'patient_payment_type': 'bhyt', 'stt': 5.0, 'order_to_queue_minutes': 1.0,
        }])
        return build_feature_frame(row, indexes, CONFIG.lunch_start, CONFIG.lunch_history_window_days).iloc[0]

    def test_waiting_skipped_and_stale_web_tickets(self) -> None:
        tickets = [
            {'ma_phong_thuc_hien': 'P1', 'ma_khoa_thuc_hien': 'K1', 'queue_entry_time': '2026-05-04 08:00:00',
             'status': 'waiting'},
            {'ma_phong_thuc_hien': 'P1', 'ma_khoa_thuc_hien': 'K1', 'queue_entry_time': '2026-05-04 08:05:00',
             'status': 'skipped', 'skipped_time': '2026-05-04 08:20:00'},
            {'ma_phong_thuc_hien': 'P1', 'ma_khoa_thuc_hien': 'K1', 'queue_entry_time': '2026-05-04 08:10:00',
             'status': 'in_exam', 'start_time': '2026-05-04 08:25:00'},
            {'ma_phong_thuc_hien': 'P1', 'ma_khoa_thuc_hien': 'K1', 'queue_entry_time': '2026-05-03 07:00:00',
             'status': 'waiting'},  # older than 24 h: treated as a patient who left
        ]
        index = EventIndex(web_events_frame(tickets), parse_time_of_day(CONFIG.lunch_start))
        features = self.query([index], '2026-05-04 08:30:00')
        self.assertEqual(features['patients_ahead_same_room'], 1)
        self.assertEqual(features['room_arrivals_last_30m'], 3)
        self.assertEqual(features['room_starts_last_15m'], 1)
        self.assertAlmostEqual(features['minutes_since_last_room_start'], 5.0)
        before_skip = self.query([index], '2026-05-04 08:15:00')
        self.assertEqual(before_skip['patients_ahead_same_room'], 3)

    def test_afternoon_rows_have_no_lunch_history(self) -> None:
        history = synthetic_history()
        index = EventIndex.from_history(history, parse_time_of_day(CONFIG.lunch_start))
        afternoon = self.query([index], '2026-05-07 14:00:00')
        self.assertEqual(afternoon['room_lunch_overflow_count'], 0)
        self.assertTrue(np.isnan(afternoon['room_lunch_overflow_rate']))
        morning = self.query([index], '2026-05-07 09:00:00')
        self.assertGreater(morning['room_lunch_overflow_count'], 0)


if __name__ == '__main__':
    unittest.main()
