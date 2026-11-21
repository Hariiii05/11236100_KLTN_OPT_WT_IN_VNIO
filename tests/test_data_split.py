from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from pipeline.data_split import (
    build_validation_folds,
    check_holdout_not_evaluated,
    compute_holdout_start,
    lock_holdout_evaluated,
    split_and_lock_holdout,
)
from pipeline.pipeline_config import PipelineConfig


def synthetic_dataset(n_days: int = 100) -> pd.DataFrame:
    days = pd.date_range('2026-01-01', periods=n_days, freq='D')
    data = pd.DataFrame({
        'nb_dich_vu_id': range(n_days),
        'nb_dot_dieu_tri_id': range(n_days),
        'queue_entry_time': days,
        'waiting_minutes': [10.0] * n_days,
    })
    data['label_available_time'] = data['queue_entry_time'] + pd.Timedelta(10, unit='m')
    return data


class ComputeHoldoutStartTests(unittest.TestCase):
    def test_holdout_is_the_trailing_window(self) -> None:
        data = synthetic_dataset(100)
        start = compute_holdout_start(data, holdout_days=21)
        expected = data['queue_entry_time'].max().normalize() + pd.Timedelta(1, unit='D') - pd.Timedelta(21, unit='D')
        self.assertEqual(start, expected)


class SplitAndLockHoldoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.input_path = root / 'model_dataset.csv'
        self.input_path.write_text('placeholder\n1\n', encoding='utf-8')
        self.output_dir = root / 'unified_pipeline'
        self.config = PipelineConfig(
            input=self.input_path, output_dir=self.output_dir, holdout_days=21,
            holdout_start=None, exclude_service_codes=(), max_wait_minutes=None,
        )

    def test_lock_is_created_and_reused(self) -> None:
        data = synthetic_dataset(100)
        split = split_and_lock_holdout(data, self.config)
        lock_path = self.output_dir / 'holdout_locked.json'
        self.assertTrue(lock_path.exists())
        with lock_path.open(encoding='utf-8') as stream:
            lock = json.load(stream)
        self.assertEqual(lock['holdout_start'], str(split.holdout_start))

        # A second call must reuse the same locked boundary, not recompute it.
        second = split_and_lock_holdout(data, self.config)
        self.assertEqual(second.holdout_start, split.holdout_start)

    def test_time_ordering_is_enforced_and_no_rows_are_lost(self) -> None:
        data = synthetic_dataset(100)
        split = split_and_lock_holdout(data, self.config)
        self.assertLess(split.pre_holdout['queue_entry_time'].max(), split.holdout['queue_entry_time'].min())
        self.assertEqual(len(split.pre_holdout) + len(split.holdout), len(data))

    def test_changed_input_after_lock_raises(self) -> None:
        data = synthetic_dataset(100)
        split_and_lock_holdout(data, self.config)
        self.input_path.write_text('placeholder\n2\n', encoding='utf-8')
        with self.assertRaises(RuntimeError):
            split_and_lock_holdout(data, self.config)


    def test_explicit_holdout_start_overrides_holdout_days(self) -> None:
        data = synthetic_dataset(100)
        config = PipelineConfig(
            input=self.input_path, output_dir=self.output_dir, holdout_days=21,
            holdout_start='2026-03-15', exclude_service_codes=(), max_wait_minutes=None,
        )
        split = split_and_lock_holdout(data, config)
        self.assertEqual(split.holdout_start, pd.Timestamp('2026-03-15'))
        self.assertGreaterEqual(split.holdout['queue_entry_time'].min(), pd.Timestamp('2026-03-15'))

    def test_changing_cohort_rules_after_lock_raises(self) -> None:
        data = synthetic_dataset(100)
        split_and_lock_holdout(data, self.config)
        changed = PipelineConfig(
            input=self.input_path, output_dir=self.output_dir, holdout_days=21,
            holdout_start=None, exclude_service_codes=('KB008',), max_wait_minutes=None,
        )
        with self.assertRaises(RuntimeError):
            split_and_lock_holdout(data, changed)

    def test_conflicting_explicit_holdout_start_after_lock_raises(self) -> None:
        data = synthetic_dataset(100)
        split_and_lock_holdout(data, self.config)
        conflicting = PipelineConfig(
            input=self.input_path, output_dir=self.output_dir, holdout_days=21,
            holdout_start='2026-03-01', exclude_service_codes=(), max_wait_minutes=None,
        )
        with self.assertRaises(RuntimeError):
            split_and_lock_holdout(data, conflicting)

    def test_lock_without_cohort_rules_is_treated_as_rules_off(self) -> None:
        data = synthetic_dataset(100)
        split_and_lock_holdout(data, self.config)
        lock_path = self.output_dir / 'holdout_locked.json'
        lock = json.loads(lock_path.read_text(encoding='utf-8'))
        lock.pop('cohort_rules')
        lock_path.write_text(json.dumps(lock), encoding='utf-8')
        split_and_lock_holdout(data, self.config)  # rules off == legacy lock, must not raise
        with_rules = PipelineConfig(
            input=self.input_path, output_dir=self.output_dir, holdout_days=21,
            holdout_start=None, exclude_service_codes=('KB008',), max_wait_minutes=None,
        )
        with self.assertRaises(RuntimeError):
            split_and_lock_holdout(data, with_rules)


class HoldoutEvaluatedLockTests(unittest.TestCase):
    def test_check_passes_when_absent_and_fails_after_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            check_holdout_not_evaluated(output_dir)  # should not raise
            lock_holdout_evaluated(output_dir, {'ok': True})
            with self.assertRaises(RuntimeError):
                check_holdout_not_evaluated(output_dir)


class BuildValidationFoldsTests(unittest.TestCase):
    def test_filters_to_requested_window_types(self) -> None:
        data = synthetic_dataset(200)
        holdout_start = data['queue_entry_time'].max() + pd.Timedelta(1, unit='D')
        config = PipelineConfig(validation_days=7, n_folds=3, sliding_train_days=42)
        fold_specs, boundaries = build_validation_folds(data, holdout_start, config, window_types=('sliding',))
        self.assertTrue(all(f['window_type'] == 'sliding' for f in fold_specs))
        self.assertTrue((boundaries['window_type'] == 'sliding').all())
        self.assertEqual(len(fold_specs), 3)


if __name__ == '__main__':
    unittest.main()
