"""Error diagnosis: segment breakdown, tail metrics, drift, feature audit.

Reuses ``interval_metrics``/``TAIL_MINUTES`` from ``diagnose_and_calibrate``
(the 120-minute tail threshold was derived there from evidence that 1.3-1.4%
of rows account for 45-47% of total absolute error) and
``feature_audit_table`` from ``train_and_evaluate_model``, extending both
with the new lunch and patient-type features instead of duplicating them.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from pipeline.diagnose_and_calibrate import TAIL_MINUTES, interval_metrics  # noqa: F401 (re-exported)
from pipeline.train_and_evaluate_model import TIME_COLUMN, feature_audit_table


def segments_of(frame: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        'queue_entry_status': frame['queue_entry_status'].astype(str),
        'actual_wait_bucket': pd.cut(
            frame['y'], [-1, 10, 20, 30, 60, 120, np.inf],
            labels=['<=10', '10-20', '20-30', '30-60', '60-120', '>120'],
        ),
        'patients_ahead_room': pd.cut(
            frame['patients_ahead_same_room'], [-1, 0, 2, 5, 10, np.inf],
            labels=['0', '1-2', '3-5', '6-10', '>10'],
        ),
        'hour': frame[TIME_COLUMN].dt.hour.astype(str).str.zfill(2),
        'day_of_week': frame[TIME_COLUMN].dt.dayofweek.astype(str),
        'room': frame['ma_phong_thuc_hien'].astype(str),
        'department': frame['ma_khoa_thuc_hien'].astype(str),
        'patient_payment_type': frame['patient_payment_type'].astype(str),
        'registered_before_lunch': frame['registered_before_lunch'].astype(str),
    }


def segment_table(frame: pd.DataFrame, segments: dict[str, pd.Series]) -> pd.DataFrame:
    rows = []
    total_abs = float(frame['abs_error'].sum())
    for name, labels in segments.items():
        if labels.nunique(dropna=True) < 2:
            continue  # a constant segment (e.g. queue_entry_status once KB008 is excluded) carries no information
        for label, part in frame.groupby(labels, observed=True):
            if len(part) == 0:
                continue
            rows.append({
                'segment': name,
                'value': str(label),
                'rows': len(part),
                'mae': part['abs_error'].mean(),
                'bias': part['error'].mean(),
                'within_20m': (part['abs_error'] <= 20).mean(),
                'coverage_p80': (part['y'] <= part['p80']).mean() if 'p80' in part else np.nan,
                'share_of_total_abs_error': part['abs_error'].sum() / total_abs if total_abs else np.nan,
            })
    return pd.DataFrame(rows)


def tail_summary(y: np.ndarray, predicted: np.ndarray, tail_minutes: float = TAIL_MINUTES) -> dict[str, float]:
    """Contrast error on the extreme-wait tail vs. the typical case.

    Priority target for weakness fixes: the model under-predicting the
    longest waits hurts patient experience the most (they are told to expect
    a much shorter wait than reality), so this is reported before/after every
    change that targets the tail.
    """
    error = np.asarray(predicted, dtype=float) - np.asarray(y, dtype=float)
    abs_error = np.abs(error)
    is_tail = np.asarray(y, dtype=float) > tail_minutes
    total_abs = float(abs_error.sum())
    return {
        'tail_threshold_minutes': float(tail_minutes),
        'tail_rows': int(is_tail.sum()),
        'tail_share_of_rows': float(is_tail.mean()) if len(is_tail) else float('nan'),
        'tail_share_of_total_abs_error': float(abs_error[is_tail].sum() / total_abs) if total_abs else float('nan'),
        'tail_mae': float(abs_error[is_tail].mean()) if is_tail.any() else float('nan'),
        'tail_bias': float(error[is_tail].mean()) if is_tail.any() else float('nan'),
        'non_tail_mae': float(abs_error[~is_tail].mean()) if (~is_tail).any() else float('nan'),
    }


def drift_slope_per_fold(fold_metrics: pd.DataFrame, metric: str = 'mae') -> float:
    """Linear trend of a metric across folds; the sign/magnitude flags drift."""
    if fold_metrics['fold'].nunique() < 2:
        return float('nan')
    return float(np.polyfit(fold_metrics['fold'], fold_metrics[metric].astype(float), 1)[0])


EXTENDED_FEATURE_AUDIT_ROWS = [
    (
        'patient_payment_type',
        'BHYT hay dịch vụ (doi_tuong tại 01_encounter)',
        'Có trước lúc nhập viện, trước mọi hàng đợi khám',
        True,
        'Thuộc tính đợt điều trị, có trước queue_entry_time; dùng làm feature và segment chẩn đoán.',
    ),
    (
        'minutes_to_lunch_start',
        'Khoảng cách đến giờ bắt đầu nghỉ trưa',
        'Tính trực tiếp từ queue_entry_time',
        True,
        'Biến đổi xác định từ thời điểm dự báo, không dùng tương lai.',
    ),
    (
        'room_lunch_overflow_rate/department_lunch_overflow_rate',
        'Tỷ lệ lịch sử vượt giờ trưa theo phòng/khoa',
        'Chỉ dùng ca có label_available_time trước queue_entry_time hiện tại',
        True,
        'Feature nhân quả theo cửa sổ trượt, cùng cơ chế với các feature lịch sử khác đã có.',
    ),
    (
        'service_minutes_60_to_150 (bác sĩ/roster/in-exam)',
        'Thời lượng và trạng thái ca đang khám',
        'Đã kiểm chứng trong model_diagnosis_and_calibration.md',
        False,
        'Đã thử nghiệm đầy đủ (ablation +duration/+roster/+in_exam/+flow/+shift); không cải '
        'thiện MAE ngoài nhiễu giữa các fold. Không đưa vào pipeline mặc định.',
    ),
    (
        'bac_si_kham_id/ma_bac_si_kham',
        'Bác sĩ trong snapshot cuối',
        'Chưa chứng minh được đã gán trước queue_entry_time',
        False,
        'Loại để tránh temporal leakage (xem W7); tài khoản thao tác được dùng thay thế trong '
        'nhánh nghiên cứu, không đưa vào pipeline mặc định.',
    ),
    (
        'thoi_gian_hen_kham',
        'Thời điểm hẹn khám',
        'Không chắc luôn được ghi trước queue_entry_time cho mọi dòng',
        False,
        'Chưa kiểm chứng đủ để đưa vào model; chỉ ghi nhận là hướng nghiên cứu thêm.',
    ),
    (
        'queue_entry_status (cohort V3)',
        'Status 20 hoặc 40 dùng làm mốc',
        'Sau khi loại KB008 chỉ còn status 40',
        True,
        'Hằng số trong cohort V3 nên không mang thông tin; giữ lại trong danh sách feature để V2 tái lập được, không ảnh hưởng mô hình.',
    ),
    (
        'KB008 / phòng H401, E1108 (cohort V3)',
        'Nhánh status 20: dịch vụ khám nội được chỉ định trong lúc khám dịch vụ khác',
        'Mốc status 20 xảy ra sau lúc bắt đầu khám của dịch vụ cha ở 100% ca',
        False,
        'Không phải thời gian chờ hàng đợi thật nên loại khỏi cohort mô hình (không dùng làm feature, không huấn luyện, không đánh giá).',
    ),
]


def extended_feature_audit() -> pd.DataFrame:
    base = feature_audit_table()
    extra = pd.DataFrame(EXTENDED_FEATURE_AUDIT_ROWS, columns=base.columns)
    return pd.concat([base, extra], ignore_index=True)
