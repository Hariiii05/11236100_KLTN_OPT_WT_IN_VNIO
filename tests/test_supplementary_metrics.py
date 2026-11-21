from __future__ import annotations

import unittest

import pandas as pd

from pipeline.supplementary_metrics import eta_squared, lunch_auc_report


class SupplementaryMetricsTests(unittest.TestCase):
    def test_perfect_ranking_gives_auc_one_and_reports_prevalence(self) -> None:
        report = lunch_auc_report([0, 0, 0, 1, 1], [0.1, 0.2, 0.3, 0.8, 0.9], n_boot=50)
        self.assertEqual(report['pr_auc'], 1.0)
        self.assertEqual(report['roc_auc'], 1.0)
        self.assertEqual((report['rows'], report['positives']), (5, 2))
        self.assertAlmostEqual(report['prevalence'], 0.4)

    def test_eta_squared_is_one_when_every_row_is_its_own_group_and_near_zero_when_groups_interleave(self) -> None:
        values = pd.Series([1.0, 2.0, 3.0, 10.0, 11.0, 12.0])
        self.assertEqual(eta_squared(values, pd.Series(list('abcdef'))), 1.0)
        self.assertAlmostEqual(eta_squared(values, pd.Series(list('abbaab'))), 0.0, delta=0.02)


if __name__ == '__main__':
    unittest.main()
