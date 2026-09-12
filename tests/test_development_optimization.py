from __future__ import annotations

import unittest

import pandas as pd

from pipeline.optimize_on_development import add_causal_features


class CausalFeatureTests(unittest.TestCase):
    def test_queue_and_history_features_use_only_prior_events(self) -> None:
        data = pd.DataFrame(
            {
                'queue_entry_time': pd.to_datetime(
                    [
                        '2026-05-01 08:00:00',
                        '2026-05-01 08:10:00',
                        '2026-05-01 08:20:00',
                    ]
                ),
                'label_available_time': pd.to_datetime(
                    [
                        '2026-05-01 08:30:00',
                        '2026-05-01 08:15:00',
                        '2026-05-01 08:30:00',
                    ]
                ),
                'waiting_minutes': [30.0, 5.0, 10.0],
                'ma_phong_thuc_hien': ['P1', 'P1', 'P1'],
                'ma_khoa_thuc_hien': ['K1', 'K1', 'K1'],
                'ma_dich_vu': ['DV1', 'DV1', 'DV1'],
            }
        )
        featured = add_causal_features(data)
        self.assertEqual(featured['room_arrivals_last_15m'].tolist(), [0, 1, 1])
        self.assertEqual(featured['patients_ahead_same_room'].tolist(), [0, 1, 1])
        self.assertEqual(featured['room_starts_last_15m'].tolist(), [0, 0, 1])
        self.assertAlmostEqual(featured.loc[2, 'minutes_since_last_room_start'], 5.0)
        self.assertEqual(featured['room_completed_42d_count'].tolist(), [0, 0, 1])
        self.assertAlmostEqual(featured.loc[2, 'room_wait_logmean_42d'], 5.0)

    def test_equal_timestamps_do_not_count_each_other(self) -> None:
        data = pd.DataFrame(
            {
                'queue_entry_time': pd.to_datetime(['2026-05-01 08:00:00'] * 2),
                'label_available_time': pd.to_datetime(['2026-05-01 08:10:00'] * 2),
                'waiting_minutes': [10.0, 10.0],
                'ma_phong_thuc_hien': ['P1', 'P1'],
                'ma_khoa_thuc_hien': ['K1', 'K1'],
                'ma_dich_vu': ['DV1', 'DV1'],
            }
        )
        featured = add_causal_features(data)
        self.assertEqual(featured['room_arrivals_last_60m'].tolist(), [0, 0])
        self.assertEqual(featured['patients_ahead_same_room'].tolist(), [0, 0])


if __name__ == '__main__':
    unittest.main()
