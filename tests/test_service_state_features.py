from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from pipeline.diagnose_and_calibrate import (
    MINUTE_NS,
    conformal_offset,
    room_state,
    rolling_quantiles,
)


def ts(text: str) -> int:
    return pd.Timestamp(f'2026-05-01 {text}').value


class ServiceStateFeatureTests(unittest.TestCase):
    def test_rolling_quantiles_use_only_durations_ended_strictly_before_query(self) -> None:
        ends = np.array([ts('08:00'), ts('08:10'), ts('08:20'), ts('08:30'), ts('08:40'), ts('08:50')])
        durations = np.array([10.0, 20.0, 30.0, 40.0, 50.0, 999.0])
        stats = rolling_quantiles(
            np.zeros(2, dtype=int), np.array([ts('08:50'), ts('09:00')]),
            np.zeros(6, dtype=int), ends, durations,
        )
        self.assertEqual(stats[0, 2], 5)  # the 08:50 completion is not before an 08:50 query
        self.assertAlmostEqual(stats[0, 0], 30.0)
        self.assertEqual(stats[1, 2], 6)

    def test_room_state_counts_open_cases_and_ignores_events_at_query_time(self) -> None:
        events = pd.DataFrame({
            'ma_phong_thuc_hien': ['P1'] * 3,
            'exam_start_time': pd.to_datetime(['2026-05-01 08:00', '2026-05-01 08:20', '2026-05-01 08:30']),
            'exam_complete_time': pd.to_datetime(['2026-05-01 08:25', '2026-05-01 09:10', pd.NaT]),
            'conclusion_start_time': pd.to_datetime([pd.NaT] * 3),
            'bac_si_kham_id': [1.0, 2.0, 2.0],
            'tai_khoan': ['a', 'b', 'a'],
        })
        data = pd.DataFrame({
            'queue_entry_time': pd.to_datetime(['2026-05-01 08:30', '2026-05-01 08:40']),
            'ma_phong_thuc_hien': ['P1', 'P1'],
        })
        state = room_state(data, events, 'e150_', np.array([30.0, 30.0]))
        # 08:30: the 08:30 start does not exist yet; only the 08:20 case is open (elapsed 10)
        self.assertEqual(state.loc[0, 'e150_in_exam_n'], 1)
        self.assertAlmostEqual(state.loc[0, 'e150_in_exam_max_elapsed'], 10.0)
        self.assertAlmostEqual(state.loc[0, 'e150_in_exam_remaining_sum'], 20.0)
        self.assertEqual(state.loc[0, 'e150_starts_30m'], 2)
        self.assertEqual(state.loc[0, 'e150_completed_30m'], 1)
        # 08:40: the 08:20 and 08:30 cases are open
        self.assertEqual(state.loc[0, 'e150_starts_today'], 2)
        self.assertAlmostEqual(state.loc[0, 'e150_minutes_since_room_open'], 30.0)
        self.assertEqual(state.loc[1, 'e150_in_exam_n'], 2)
        self.assertEqual(state.loc[1, 'e150_accounts_30m'], 2)

    def test_conformal_offset_reaches_target_coverage(self) -> None:
        residual = np.arange(1, 101, dtype=float)
        offset = conformal_offset(residual, 0.8)
        self.assertGreaterEqual(np.mean(residual <= offset), 0.8)


if __name__ == '__main__':
    unittest.main()
