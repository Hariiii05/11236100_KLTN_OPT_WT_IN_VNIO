"""Inference with the frozen V3 bundle, reusing the pipeline's own functions.

Nothing here re-implements preprocessing: the bundle's sklearn pipelines hold
the fitted OrdinalEncoder / median imputer, and post-processing (quantile
clipping, per-department conformal offsets, lunch warning, suggested return
time) calls the same functions the pipeline used to score the holdout.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from pipeline.lunch_warning import build_patient_output, predict_lunch_probability
from pipeline.model_training import apply_group_conformal_offsets, predict_quantile_models
from pipeline.pipeline_config import PipelineConfig
from pipeline.train_and_evaluate_model import TIME_COLUMN


class ModelUnavailable(RuntimeError):
    pass


class WaitTimeModel:
    def __init__(self, path: Path):
        if not path.exists():
            raise ModelUnavailable(
                f'Không tìm thấy artifact mô hình {path}. Chạy '
                '`python -m pipeline.run_unified_pipeline --stage refit-final` để tạo lại.'
            )
        self.path = path
        self.bundle: dict[str, Any] = joblib.load(path)
        self.categorical: list[str] = list(self.bundle['quantile_categorical'])
        self.numeric: list[str] = list(self.bundle['quantile_numeric'])
        encoder = self.bundle['quantile_models'][0.5].named_steps['preprocessing'].named_transformers_['categorical']
        self.known_categories = {
            name: {str(value) for value in values} for name, values in zip(self.categorical, encoder.categories_)
        }
        self.pipeline_config = PipelineConfig(
            lunch_start=self.bundle.get('lunch_start', '11:30'), lunch_end=self.bundle.get('lunch_end', '13:00')
        )

    @property
    def feature_names(self) -> list[str]:
        return self.categorical + self.numeric

    def metadata(self) -> dict[str, Any]:
        selected = self.bundle.get('selected_candidate', {})
        return {
            'artifact': self.path.name,
            'model': selected.get('name'),
            'estimator': 'HistGradientBoostingRegressor (P50: absolute_error, P80: quantile 0.8)',
            'feature_set': selected.get('feature_set'),
            'n_features': len(self.feature_names),
            'trained_through_exclusive': self.bundle.get('trained_through_exclusive'),
            'created_at_utc': self.bundle.get('created_at_utc'),
            'cohort_rules': self.bundle.get('cohort_rules'),
            'calibration_group_column': self.bundle.get('calibration_group_column'),
            'lunch_threshold': self.bundle.get('lunch_threshold'),
            'lunch_start': self.pipeline_config.lunch_start,
            'lunch_end': self.pipeline_config.lunch_end,
        }

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        """``features`` must come from ``features.build_feature_frame`` and carry
        ``nb_dich_vu_id`` (any id) so the pipeline's output builder can key rows."""
        raw = predict_quantile_models(self.bundle['quantile_models'], self.categorical, self.numeric, features)
        calibrated = apply_group_conformal_offsets(
            features, raw, self.bundle['calibration_group_column'], self.bundle['calibration_offsets']
        )
        probability = predict_lunch_probability(self.bundle['lunch_classifier'], features)
        output = build_patient_output(
            features, calibrated[0.5], calibrated[0.8], probability, self.bundle['lunch_threshold'],
            self.pipeline_config,
        )
        output['raw_p50_minutes'] = raw[0.5]
        output['predicted_exam_time'] = (
            features[TIME_COLUMN].reset_index(drop=True) + pd.to_timedelta(calibrated[0.5], unit='m')
        )
        output['lunch_warning'] = output['recommendation'].ne('Chờ tiếp')
        output['probability_cross_lunch'] = output['probability_cross_lunch'].astype(float)
        return output

    def unknown_categories(self, row: dict[str, Any]) -> list[str]:
        return [
            name for name in self.categorical
            if name in row and str(row[name]) not in self.known_categories.get(name, set())
        ]

