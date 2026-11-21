from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from pipeline.lunch_warning import (
    RECOMMEND_AFTERNOON,
    RECOMMEND_KEEP_WAITING,
    build_patient_output,
    classification_report_at_threshold,
    select_threshold,
)
from pipeline.pipeline_config import PipelineConfig


class SelectThresholdTests(unittest.TestCase):
    def test_finds_a_separating_threshold_on_clean_data(self) -> None:
        rng = np.random.default_rng(0)
        negatives = rng.uniform(0.0, 0.4, size=200)
        positives = rng.uniform(0.6, 1.0, size=50)
        y_true = np.concatenate([np.zeros_like(negatives), np.ones_like(positives)])
        proba = np.concatenate([negatives, positives])
        result = select_threshold(y_true, proba, fbeta=1.0)
        self.assertGreater(result['threshold'], 0.4)
        self.assertLessEqual(result['threshold'], 1.0)
        self.assertGreaterEqual(result['f_beta'], 0.95)

    def test_higher_beta_favors_recall(self) -> None:
        rng = np.random.default_rng(1)
        # Overlapping distributions so precision/recall trade off meaningfully.
        negatives = rng.uniform(0.0, 0.7, size=300)
        positives = rng.uniform(0.3, 1.0, size=60)
        y_true = np.concatenate([np.zeros_like(negatives), np.ones_like(positives)])
        proba = np.concatenate([negatives, positives])
        balanced = select_threshold(y_true, proba, fbeta=1.0)
        recall_favoring = select_threshold(y_true, proba, fbeta=2.0)
        self.assertGreaterEqual(recall_favoring['recall'], balanced['recall'] - 1e-9)

    def test_empty_input_falls_back_safely(self) -> None:
        result = select_threshold(np.array([]), np.array([]), fbeta=1.0)
        self.assertEqual(result['threshold'], 0.5)


class ClassificationReportTests(unittest.TestCase):
    def test_precision_recall_f1_at_fixed_threshold(self) -> None:
        y_true = np.array([1, 1, 0, 0, 1])
        proba = np.array([0.9, 0.4, 0.6, 0.1, 0.2])
        report = classification_report_at_threshold(y_true, proba, threshold=0.5)
        # Predicted positive: index 0 (0.9) and index 2 (0.6, a false positive).
        self.assertEqual(report['positives_predicted'], 2)
        self.assertEqual(report['positives_true'], 3)
        self.assertAlmostEqual(report['precision'], 0.5)
        self.assertAlmostEqual(report['recall'], 1 / 3)


class BuildPatientOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = PipelineConfig(lunch_start='11:30', lunch_end='13:00')
        self.frame = pd.DataFrame({
            'nb_dich_vu_id': [1, 2, 3],
            'queue_entry_time': pd.to_datetime([
                '2026-05-01 09:00',  # morning, will warn (predicted wait pushes past lunch)
                '2026-05-01 09:00',  # morning, low probability -> no warning
                '2026-05-01 13:30',  # afternoon -> warning never applies
            ]),
            'registered_before_lunch': [True, True, False],
        })

    def test_warning_only_for_morning_rows_over_threshold(self) -> None:
        predicted_p50 = np.array([60.0, 30.0, 15.0])  # minutes
        predicted_p80 = predicted_p50 + 10
        lunch_probability = np.array([0.8, 0.1, 0.5])
        output = build_patient_output(self.frame, predicted_p50, predicted_p80, lunch_probability, threshold=0.5, config=self.config)

        self.assertEqual(output.loc[0, 'recommendation'], RECOMMEND_AFTERNOON)
        self.assertEqual(output.loc[1, 'recommendation'], RECOMMEND_KEEP_WAITING)
        self.assertEqual(output.loc[2, 'recommendation'], RECOMMEND_KEEP_WAITING)

        self.assertTrue(pd.notna(output.loc[0, 'suggested_return_time']))
        self.assertTrue(pd.isna(output.loc[1, 'suggested_return_time']))
        self.assertTrue(pd.isna(output.loc[2, 'suggested_return_time']))

        # Afternoon row's probability is not a meaningful lunch-crossing estimate.
        self.assertTrue(pd.isna(output.loc[2, 'probability_cross_lunch']))
        self.assertAlmostEqual(output.loc[0, 'probability_cross_lunch'], 0.8)

    def test_suggested_return_time_never_precedes_lunch_end(self) -> None:
        # Predicted exam time (09:00 + 45min = 09:45) is well before lunch ends;
        # the recommendation must still not send the patient back before 13:00.
        predicted_p50 = np.array([45.0, 45.0, 45.0])
        predicted_p80 = predicted_p50 + 5
        lunch_probability = np.array([0.9, 0.9, 0.9])
        output = build_patient_output(self.frame, predicted_p50, predicted_p80, lunch_probability, threshold=0.5, config=self.config)
        suggested = pd.to_datetime(output.loc[0, 'suggested_return_time'])
        self.assertGreaterEqual(suggested, pd.Timestamp('2026-05-01 13:00'))

    def test_suggested_return_time_uses_prediction_when_later_than_lunch_end(self) -> None:
        predicted_p50 = np.array([240.0, 240.0, 240.0])  # 09:00 + 4h = 13:00 exactly
        predicted_p80 = predicted_p50 + 30  # -> 13:30, later than lunch end
        lunch_probability = np.array([0.9, 0.9, 0.9])
        output = build_patient_output(self.frame, predicted_p50, predicted_p80, lunch_probability, threshold=0.5, config=self.config)
        suggested = pd.to_datetime(output.loc[0, 'suggested_return_time'])
        self.assertEqual(suggested, pd.Timestamp('2026-05-01 13:00'))


if __name__ == '__main__':
    unittest.main()
