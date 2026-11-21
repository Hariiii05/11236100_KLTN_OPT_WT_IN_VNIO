"""Human-readable description of every model input and where it comes from."""

from __future__ import annotations

USER = 'Người dùng nhập'
DERIVED = 'Suy ra từ lựa chọn'
TIME = 'Tự động từ thời gian'
QUEUE = 'Từ dữ liệu hàng đợi'
HISTORY = 'Từ lịch sử 42 ngày'
CONSTANT = 'Hằng số hệ thống'

FEATURE_INFO: dict[str, tuple[str, str]] = {
    'ma_dich_vu': ('Dịch vụ khám', USER),
    'ma_phong_thuc_hien': ('Phòng khám', USER),
    'ma_khoa_thuc_hien': ('Chuyên khoa (khoa của phòng)', DERIVED),
    'queue_entry_status': ('Trạng thái vào hàng đợi (40 = chờ khám)', CONSTANT),
    'uu_tien': ('Ưu tiên', USER),
    'cap_cuu': ('Cấp cứu', USER),
    'patient_payment_type': ('Đối tượng (BHYT / dịch vụ)', USER),
    'stt': ('Số thứ tự trong phòng', QUEUE),
    'queue_entry_hour': ('Giờ vào hàng đợi', TIME),
    'queue_entry_day_of_week': ('Thứ trong tuần (0 = Thứ 2)', TIME),
    'queue_entry_is_weekend': ('Cuối tuần', TIME),
    'order_to_queue_minutes': ('Số phút từ chỉ định đến vào hàng đợi', QUEUE),
    'queue_hour_sin': ('Giờ (mã hóa chu kỳ, sin)', TIME),
    'queue_hour_cos': ('Giờ (mã hóa chu kỳ, cos)', TIME),
    'queue_dow_sin': ('Thứ (mã hóa chu kỳ, sin)', TIME),
    'queue_dow_cos': ('Thứ (mã hóa chu kỳ, cos)', TIME),
    'room_arrivals_last_15m': ('Lượt vào hàng đợi của phòng, 15 phút qua', QUEUE),
    'room_arrivals_last_30m': ('Lượt vào hàng đợi của phòng, 30 phút qua', QUEUE),
    'room_arrivals_last_60m': ('Lượt vào hàng đợi của phòng, 60 phút qua', QUEUE),
    'department_arrivals_last_30m': ('Lượt vào hàng đợi của khoa, 30 phút qua', QUEUE),
    'department_arrivals_last_60m': ('Lượt vào hàng đợi của khoa, 60 phút qua', QUEUE),
    'patients_ahead_same_room': ('Số người đang chờ trước trong phòng', QUEUE),
    'patients_ahead_same_department': ('Số người đang chờ trong khoa', QUEUE),
    'room_starts_last_15m': ('Lượt bắt đầu khám của phòng, 15 phút qua', QUEUE),
    'room_starts_last_30m': ('Lượt bắt đầu khám của phòng, 30 phút qua', QUEUE),
    'room_starts_last_60m': ('Lượt bắt đầu khám của phòng, 60 phút qua', QUEUE),
    'department_starts_last_30m': ('Lượt bắt đầu khám của khoa, 30 phút qua', QUEUE),
    'department_starts_last_60m': ('Lượt bắt đầu khám của khoa, 60 phút qua', QUEUE),
    'minutes_since_last_room_start': ('Số phút từ lượt khám gần nhất của phòng', QUEUE),
    'minutes_since_last_department_start': ('Số phút từ lượt khám gần nhất của khoa', QUEUE),
    'minutes_to_lunch_start': ('Số phút đến giờ nghỉ trưa', TIME),
    'room_lunch_overflow_rate': ('Tỷ lệ ca sáng bị đẩy qua trưa của phòng', HISTORY),
    'room_lunch_overflow_count': ('Số ca sáng đã hoàn tất của phòng', HISTORY),
    'department_lunch_overflow_rate': ('Tỷ lệ ca sáng bị đẩy qua trưa của khoa', HISTORY),
    'department_lunch_overflow_count': ('Số ca sáng đã hoàn tất của khoa', HISTORY),
}
