from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from pipeline.feature_engineering import (
    add_lunch_break_features,
    causal_group_rate_and_count,
    feature_columns,
    parse_time_of_day,
)
from pipeline.pipeline_config import PipelineConfig


def frame(rows: list[dict]) -> pd.DataFrame:
    data = pd.DataFrame(rows)
    data['queue_entry_time'] = pd.to_datetime(data['queue_entry_time'])
    data['label_available_time'] = pd.to_datetime(data['label_available_time'])
    return data


class LunchLabelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = PipelineConfig()

    def test_crosses_lunch_requires_morning_registration_and_late_start(self) -> None:
        data = frame([
            # queued before 11:30, seen before 11:30 -> not crossing
            {'queue_entry_time': '2026-05-01 08:00', 'label_available_time': '2026-05-01 08:20',
             'ma_phong_thuc_hien': 'P1', 'ma_khoa_thuc_hien': 'K1'},
            # queued before 11:30, seen at/after 11:30 -> crossing
            {'queue_entry_time': '2026-05-01 09:00', 'label_available_time': '2026-05-01 11:40',
             'ma_phong_thuc_hien': 'P1', 'ma_khoa_thuc_hien': 'K1'},
            # queued after 11:30 -> never counts as crossing, regardless of exam time
            {'queue_entry_time': '2026-05-01 12:00', 'label_available_time': '2026-05-01 15:00',
             'ma_phong_thuc_hien': 'P1', 'ma_khoa_thuc_hien': 'K1'},
            # queued before 11:30, wait rolls into the next day -> crossing
            {'queue_entry_time': '2026-05-01 10:00', 'label_available_time': '2026-05-02 08:00',
             'ma_phong_thuc_hien': 'P1', 'ma_khoa_thuc_hien': 'K1'},
        ])
        featured = add_lunch_break_features(data, self.config)
        self.assertEqual(
            featured['registered_before_lunch'].tolist(), [True, True, False, True]
        )
        self.assertEqual(featured['crosses_lunch'].tolist(), [False, True, False, True])
        # minutes_to_lunch_start is positive before 11:30, negative after.
        self.assertAlmostEqual(featured.loc[0, 'minutes_to_lunch_start'], 210.0)
        self.assertAlmostEqual(featured.loc[2, 'minutes_to_lunch_start'], -30.0)

    def test_configured_lunch_start_is_respected(self) -> None:
        config = PipelineConfig(lunch_start='12:00', lunch_end='13:30')
        data = frame([
            {'queue_entry_time': '2026-05-01 11:45', 'label_available_time': '2026-05-01 12:05',
             'ma_phong_thuc_hien': 'P1', 'ma_khoa_thuc_hien': 'K1'},
        ])
        featured = add_lunch_break_features(data, config)
        self.assertTrue(bool(featured.loc[0, 'registered_before_lunch']))
        self.assertTrue(bool(featured.loc[0, 'crosses_lunch']))


class CausalGroupRateTests(unittest.TestCase):
    def test_only_counts_history_with_label_available_strictly_before_query(self) -> None:
        data = frame([
            {'queue_entry_time': '2026-05-01 08:00', 'label_available_time': '2026-05-01 08:20',
             'ma_phong_thuc_hien': 'P1', 'ma_khoa_thuc_hien': 'K1'},  # crosses=False
            {'queue_entry_time': '2026-05-01 09:00', 'label_available_time': '2026-05-01 11:40',
             'ma_phong_thuc_hien': 'P1', 'ma_khoa_thuc_hien': 'K1'},  # crosses=True, revealed 11:40
            # Next day, before its own lunch start, but after row 1 above was revealed.
            {'queue_entry_time': '2026-05-02 08:00', 'label_available_time': '2026-05-02 08:15',
             'ma_phong_thuc_hien': 'P1', 'ma_khoa_thuc_hien': 'K1'},
            # Same day/time but a different room: must not pollute P1's rate.
            {'queue_entry_time': '2026-05-02 08:00', 'label_available_time': '2026-05-02 08:10',
             'ma_phong_thuc_hien': 'P2', 'ma_khoa_thuc_hien': 'K1'},
        ])
        config = PipelineConfig()
        featured = add_lunch_break_features(data, config)
        mask = featured['registered_before_lunch'].to_numpy(dtype=bool)
        outcome = featured['crosses_lunch'].to_numpy(dtype=float)
        counts, rates = causal_group_rate_and_count(featured, mask, outcome, 'ma_phong_thuc_hien', window_days=42)
        # Row 2 (index 2): both P1 rows before it are already revealed (08:20 and 11:40 < 2026-05-02 08:00).
        self.assertEqual(counts[2], 2)
        self.assertAlmostEqual(rates[2], 0.5)
        # Row 3 (index 3, room P2) has no prior P2 history.
        self.assertEqual(counts[3], 0)
        self.assertTrue(np.isnan(rates[3]))
        # Row 0 has no prior history at all.
        self.assertEqual(counts[0], 0)

    def test_window_truncates_old_history(self) -> None:
        data = frame([
            {'queue_entry_time': '2026-01-01 08:00', 'label_available_time': '2026-01-01 08:20',
             'ma_phong_thuc_hien': 'P1', 'ma_khoa_thuc_hien': 'K1'},
            {'queue_entry_time': '2026-03-01 08:00', 'label_available_time': '2026-03-01 08:20',
             'ma_phong_thuc_hien': 'P1', 'ma_khoa_thuc_hien': 'K1'},
        ])
        mask = np.array([True, True])
        outcome = np.array([1.0, 0.0])
        counts, rates = causal_group_rate_and_count(data, mask, outcome, 'ma_phong_thuc_hien', window_days=1)
        self.assertEqual(counts[1], 0)
        self.assertTrue(np.isnan(rates[1]))


class FeatureColumnsTests(unittest.TestCase):
    def test_ladder_is_additive(self) -> None:
        official_cat, official_num = feature_columns('official')
        _, queue_num = feature_columns('queue_dynamics')
        patient_cat, patient_num = feature_columns('queue_dynamics_patient_type')
        lunch_cat, lunch_num = feature_columns('queue_dynamics_lunch')

        self.assertTrue(set(official_num).issubset(queue_num))
        self.assertIn('patient_payment_type', patient_cat)
        self.assertNotIn('patient_payment_type', official_cat)
        self.assertIn('minutes_to_lunch_start', lunch_num)
        self.assertNotIn('minutes_to_lunch_start', patient_num)

    def test_unknown_feature_set_rejected(self) -> None:
        with self.assertRaises(ValueError):
            feature_columns('does_not_exist')


class ParseTimeOfDayTests(unittest.TestCase):
    def test_accepts_hh_mm(self) -> None:
        self.assertEqual(parse_time_of_day('11:30'), pd.Timedelta(11, unit='h') + pd.Timedelta(30, unit='m'))


if __name__ == '__main__':
    unittest.main()
