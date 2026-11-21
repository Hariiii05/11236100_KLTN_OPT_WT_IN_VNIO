from __future__ import annotations

import unittest

import pandas as pd

from pipeline.train_vs_validation_mae import fold_error_row, summarize_fold_errors


class TrainVsValidationTests(unittest.TestCase):
    def test_gap_and_ratio_are_validation_relative_to_train(self) -> None:
        row = fold_error_row('m', 1, {'mae': 8.0, 'r2': 0.7}, {'mae': 10.0, 'r2': 0.6}, 100, 20)
        self.assertEqual(row['mae_gap'], 2.0)
        self.assertEqual(row['mae_ratio'], 1.25)
        self.assertEqual((row['train_rows'], row['validation_rows']), (100, 20))

    def test_summary_averages_per_candidate_in_input_order(self) -> None:
        table = pd.DataFrame([
            fold_error_row('b', 1, {'mae': 14.0, 'r2': 0.0}, {'mae': 15.0, 'r2': 0.0}, 10, 5),
            fold_error_row('a', 1, {'mae': 8.0, 'r2': 0.7}, {'mae': 10.0, 'r2': 0.6}, 10, 5),
            fold_error_row('a', 2, {'mae': 6.0, 'r2': 0.8}, {'mae': 10.0, 'r2': 0.6}, 10, 5),
        ])
        summary = summarize_fold_errors(table)
        self.assertEqual(summary['candidate'].tolist(), ['b', 'a'])
        self.assertEqual(summary.loc[1, 'train_mae'], 7.0)
        self.assertEqual(summary.loc[1, 'mae_gap'], 3.0)


if __name__ == '__main__':
    unittest.main()
