from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from pipeline.model_training import (
    apply_group_conformal_offsets,
    final_training_window,
    fit_group_conformal_offsets,
)
from pipeline.pipeline_config import PipelineConfig


class GroupConformalCalibrationTests(unittest.TestCase):
    def test_group_with_enough_rows_gets_its_own_offset_small_group_falls_back(self) -> None:
        rng = np.random.default_rng(0)
        n_big, n_small = 200, 5
        actual = np.concatenate([rng.normal(100, 5, n_big), rng.normal(20, 2, n_small)])
        predicted_p50 = np.concatenate([np.full(n_big, 90.0), np.full(n_small, 15.0)])
        predicted_p80 = predicted_p50 + 5
        groups = np.array(['G_BIG'] * n_big + ['G_SMALL'] * n_small)
        frame = pd.DataFrame({'waiting_minutes': actual, 'grp': groups})

        offsets = fit_group_conformal_offsets(
            frame, {0.5: predicted_p50, 0.8: predicted_p80}, 'grp', min_group_rows=30
        )
        self.assertIn('G_BIG', offsets)
        self.assertNotIn('G_SMALL', offsets)
        self.assertIn('__global__', offsets)
        # The big group's own offset should correct most of its ~10-unit bias.
        self.assertGreater(offsets['G_BIG'][0.5], 5.0)

    def test_apply_uses_global_fallback_for_unseen_group_and_keeps_p80_above_p50(self) -> None:
        offsets = {'__global__': {0.5: 10.0, 0.8: 20.0}, 'G1': {0.5: 2.0, 0.8: 3.0}}
        frame = pd.DataFrame({'grp': ['G1', 'G2']})
        predictions = {0.5: np.array([50.0, 50.0]), 0.8: np.array([51.0, 51.0])}
        calibrated = apply_group_conformal_offsets(frame, predictions, 'grp', offsets)
        self.assertAlmostEqual(calibrated[0.5][0], 52.0)
        self.assertAlmostEqual(calibrated[0.5][1], 60.0)
        self.assertTrue(np.all(calibrated[0.8] >= calibrated[0.5]))

    def test_conformal_offsets_reach_target_coverage_per_group(self) -> None:
        rng = np.random.default_rng(2)
        n = 300
        actual = rng.normal(50, 10, n)
        predicted_p80 = np.full(n, 45.0)  # deliberately under-shooting P80
        groups = np.array(['G'] * n)
        frame = pd.DataFrame({'waiting_minutes': actual, 'grp': groups})
        offsets = fit_group_conformal_offsets(
            frame, {0.5: predicted_p80, 0.8: predicted_p80}, 'grp', min_group_rows=30, levels=(0.8,)
        )
        calibrated = apply_group_conformal_offsets(
            frame, {0.8: predicted_p80}, 'grp', offsets, levels=(0.8,)
        )
        self.assertGreaterEqual(np.mean(actual <= calibrated[0.8]), 0.79)


class FinalTrainingWindowTests(unittest.TestCase):
    def test_calibration_window_is_the_tail_and_fit_window_is_purged(self) -> None:
        days = pd.date_range('2026-01-01', periods=63, freq='D')
        waiting_minutes = np.zeros(63)
        # These rows complete far in the future, so their label is not yet
        # available at calibration_start and must be purged from the fit window.
        waiting_minutes[-10:-7] = 60 * 24 * 10
        data = pd.DataFrame({'queue_entry_time': days, 'waiting_minutes': waiting_minutes})
        data['label_available_time'] = data['queue_entry_time'] + pd.to_timedelta(
            data['waiting_minutes'], unit='m'
        )
        holdout_start = days[-1] + pd.Timedelta(1, unit='D')
        config = PipelineConfig(sliding_train_days=42, calibration_days=7)

        fit_rows, calibration_rows, purged = final_training_window(data, holdout_start, config)
        calibration_start = holdout_start - pd.Timedelta(7, unit='D')

        self.assertTrue((fit_rows['queue_entry_time'] < calibration_start).all())
        self.assertTrue((calibration_rows['queue_entry_time'] >= calibration_start).all())
        self.assertTrue((calibration_rows['queue_entry_time'] < holdout_start).all())
        self.assertTrue((fit_rows['label_available_time'] < calibration_start).all())
        self.assertGreater(purged, 0)

    def test_raises_when_a_window_is_empty(self) -> None:
        data = pd.DataFrame({
            'queue_entry_time': pd.to_datetime(['2026-01-01']),
            'waiting_minutes': [5.0],
        })
        data['label_available_time'] = data['queue_entry_time'] + pd.Timedelta(5, unit='m')
        config = PipelineConfig(sliding_train_days=42, calibration_days=7)
        with self.assertRaises(ValueError):
            final_training_window(data, pd.Timestamp('2026-06-01'), config)


if __name__ == '__main__':
    unittest.main()
