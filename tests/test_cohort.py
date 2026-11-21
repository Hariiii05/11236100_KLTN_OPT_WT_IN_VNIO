from __future__ import annotations

import argparse
import unittest

import pandas as pd

from pipeline.cohort import apply_cohort_rules, build_attrition_table, cohort_rules
from pipeline.pipeline_config import PipelineConfig, add_pipeline_cli_args, config_from_args


def frame() -> pd.DataFrame:
    return pd.DataFrame({
        'nb_dich_vu_id': [1, 2, 3, 4, 5, 6],
        'ma_dich_vu': ['KB004', 'KB008', 'KB008', 'KB001', 'KB004', ' kb008 '],
        'queue_entry_time': pd.to_datetime([
            '2026-07-01 08:00', '2026-07-02 08:00', '2026-07-16 08:00',
            '2026-07-17 08:00', '2026-07-18 08:00', '2026-07-20 08:00',
        ]),
        'waiting_minutes': [10.0, 20.0, 3000.0, 3000.0, 1440.0, 15.0],
    })


class ApplyCohortRulesTests(unittest.TestCase):
    def test_service_rule_takes_precedence_and_wait_rule_is_strictly_greater(self) -> None:
        result = apply_cohort_rules(frame(), PipelineConfig())
        self.assertEqual(result.kept['nb_dich_vu_id'].tolist(), [1, 5])  # exactly 1440 min is kept
        reasons = result.removed.set_index('nb_dich_vu_id')['removal_reason'].to_dict()
        self.assertEqual(reasons[2], 'excluded_service')
        self.assertEqual(reasons[3], 'excluded_service')  # also >24h, counted once under the first rule
        self.assertEqual(reasons[4], 'wait_over_limit')
        self.assertEqual(reasons[6], 'excluded_service')  # code matching ignores case and whitespace

    def test_disabled_rules_keep_every_row(self) -> None:
        config = PipelineConfig(exclude_service_codes=(), max_wait_minutes=None)
        result = apply_cohort_rules(frame(), config)
        self.assertEqual(len(result.kept), 6)
        self.assertTrue(result.removed.empty)

    def test_removing_everything_is_an_error(self) -> None:
        data = frame().assign(ma_dich_vu='KB008')
        with self.assertRaises(ValueError):
            apply_cohort_rules(data, PipelineConfig())

    def test_rules_snapshot_is_normalized(self) -> None:
        config = PipelineConfig(exclude_service_codes=('kb008', 'KB008 '), max_wait_minutes=1440)
        self.assertEqual(cohort_rules(config), {'exclude_service_codes': ['KB008'], 'max_wait_minutes': 1440.0})


class AttritionTableTests(unittest.TestCase):
    def test_counts_by_partition(self) -> None:
        data = frame()
        result = apply_cohort_rules(data, PipelineConfig())
        table = build_attrition_table(data['queue_entry_time'], result, pd.Timestamp('2026-07-15'), PipelineConfig())
        by_step = table.set_index('step')
        self.assertEqual(by_step.loc['input_rows', 'all'], 6)
        self.assertEqual(by_step.loc['input_rows', 'holdout'], 4)
        service_step = [s for s in by_step.index if s.startswith('removed_excluded_service')][0]
        wait_step = [s for s in by_step.index if s.startswith('removed_wait_over_limit')][0]
        self.assertEqual(by_step.loc[service_step, 'all'], 3)
        self.assertEqual(by_step.loc[wait_step, 'all'], 1)
        self.assertEqual(by_step.loc['kept_rows', 'all'], 2)
        # partitions are consistent: removed + kept == input
        for column in ('all', 'pre_holdout', 'holdout'):
            self.assertEqual(
                by_step.loc[service_step, column] + by_step.loc[wait_step, column] + by_step.loc['kept_rows', column],
                by_step.loc['input_rows', column],
            )


class CliConfigTests(unittest.TestCase):
    def parse(self, *argv: str) -> PipelineConfig:
        parser = argparse.ArgumentParser()
        add_pipeline_cli_args(parser)
        return config_from_args(parser.parse_args(list(argv)))

    def test_defaults_encode_the_v3_decisions(self) -> None:
        config = self.parse()
        self.assertEqual(config.exclude_service_codes, ('KB008',))
        self.assertEqual(config.max_wait_minutes, 1440.0)
        self.assertEqual(config.holdout_start, '2026-07-15')
        self.assertEqual(config.output_dir.name, 'unified_pipeline_v3')

    def test_rules_can_be_disabled_from_the_command_line(self) -> None:
        config = self.parse('--exclude-service-codes', '--max-wait-minutes', '0', '--holdout-start', 'auto')
        self.assertEqual(config.exclude_service_codes, ())
        self.assertIsNone(config.max_wait_minutes)
        self.assertIsNone(config.holdout_start)


if __name__ == '__main__':
    unittest.main()
