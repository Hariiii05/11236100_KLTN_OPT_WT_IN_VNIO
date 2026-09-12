# Bước 3 – Triển khai mô hình thời gian chờ khám

> Kết quả được khóa ngày 31/08/2026. Final Test đã được đánh giá một lần và không được dùng để lựa chọn lại mô hình, feature hoặc hyperparameter.

## 1. Bài toán và dữ liệu

- Đơn vị dự báo: một dịch vụ khám (`nb_dich_vu_id`).
- Thời điểm dự báo: `queue_entry_time` theo quy tắc target V2.
- Target: `waiting_minutes` từ status hàng đợi bắt buộc đến status `60` đầu tiên.
- Tổng dữ liệu model: 141.519 dòng, từ 01/04/2026 đến 04/08/2026.
- Development: 100.214 dòng có `queue_entry_time < 01/07/2026`.
- Final Test: 41.305 dòng có `queue_entry_time >= 01/07/2026`; gồm ba dòng đầu tháng 8 và vẫn được giữ trong hold-out cuối.
- Metric lựa chọn chính: MAE. RMSE, Median AE, P90 AE, R², bias và tỷ lệ sai số trong 10/20 phút được báo cáo bổ sung.

Đây là dự báo thời gian chờ cho từng bệnh nhân tại thời điểm vào hàng đợi, không phải dự báo tổng lượng bệnh nhân theo ngày. Validation horizon một tuần phản ánh giả định vận hành retrain/giám sát hàng tuần.

## 2. Chia tập và chống temporal leakage

Final Test bắt đầu từ `2026-07-01 00:00:00` và hoàn toàn không tham gia chọn cửa sổ, mô hình, feature hoặc hyperparameter. Phần development được đánh giá bằng sáu validation fold liên tiếp, mỗi fold dài bảy ngày.

Mỗi fold còn áp dụng quy tắc label availability:

```text
exam_start_time = queue_entry_time + waiting_minutes
chỉ dùng dòng train khi exam_start_time < validation_start
```

Quy tắc này loại những ca đã vào hàng đợi nhưng chưa bắt đầu khám tại thời điểm mô phỏng retrain. Nếu chỉ lọc bằng `queue_entry_time`, target tương lai có thể lọt vào train.

| Fold | Validation | Validation rows | Expanding train | Sliding 42 ngày | Nhãn bị purge |
|---:|---|---:|---:|---:|---:|
| 1 | 20/05–26/05 | 6.916 | 51.182 | 43.189 | 3 |
| 2 | 27/05–02/06 | 7.162 | 58.096 | 41.992 | 5 |
| 3 | 03/06–09/06 | 8.389 | 65.259 | 41.351 | 4 |
| 4 | 10/06–16/06 | 8.887 | 73.644 | 42.903 | 8 |
| 5 | 17/06–23/06 | 8.942 | 82.533 | 46.702 | 6 |
| 6 | 24/06–30/06 | 8.733 | 91.476 | 47.695 | 5 |

Có 1–9 encounter xuất hiện ở cả train và validation tùy fold. Pipeline không dùng `nb_dot_dieu_tri_id`, `nb_dich_vu_id` hoặc thông tin định danh bệnh nhân làm predictor; thứ tự thời gian vẫn được ưu tiên và số overlap được lưu để audit.

## 3. Feature và preprocessing

Feature sử dụng:

- dịch vụ, phòng, khoa;
- status bắt đầu hàng đợi `20` hoặc `40`;
- ưu tiên, cấp cứu, số thứ tự;
- giờ, thứ trong tuần, cuối tuần và mã hóa chu kỳ sin/cos;
- thời gian từ chỉ định đến khi vào hàng đợi.

Không dùng khóa định danh, timestamp thô, target, thời điểm bắt đầu khám hoặc bác sĩ trong snapshot cuối. Bác sĩ bị loại vì chưa chứng minh được đã được phân công trước `queue_entry_time`.

Trong từng fold, ordinal encoder cho category chưa gặp và median imputer cho số được fit riêng trên train. Validation/Test không tham gia fit preprocessing. Pipeline hiện không dùng lag, rolling target, target encoding hoặc scaling.

## 4. So sánh mô hình và cửa sổ

Ba mô hình được so sánh trên cả expanding và sliding:

1. median baseline;
2. HistGradientBoosting với `absolute_error`;
3. HistGradientBoosting dự báo `log1p(waiting_minutes)` với squared error.

Quy tắc chọn đã được xác định trước:

```text
selection_score = mean validation MAE + 0,25 × std validation MAE
```

| Hạng | Cửa sổ | Mô hình | Mean MAE | Median MAE | Std | Min | Max | Selection score |
|---:|---|---|---:|---:|---:|---:|---:|---:|
| 1 | Sliding 42 ngày | HistGB absolute | 17,55 | 16,53 | 4,15 | 13,23 | 24,90 | 18,59 |
| 2 | Sliding 42 ngày | HistGB log | 17,59 | 16,61 | 4,07 | 13,26 | 24,70 | 18,61 |
| 3 | Expanding | HistGB absolute | 17,70 | 16,85 | 4,12 | 13,38 | 24,96 | 18,73 |
| 4 | Expanding | HistGB log | 17,83 | 16,96 | 4,03 | 13,62 | 24,84 | 18,84 |
| 5 | Sliding 42 ngày | Median baseline | 22,29 | 21,14 | 4,06 | 17,55 | 29,17 | 23,30 |
| 6 | Expanding | Median baseline | 22,32 | 21,19 | 4,06 | 17,58 | 29,23 | 23,34 |

Sliding 42 ngày được chọn dựa hoàn toàn trên development data. Dữ liệu chỉ có bốn tháng nhưng phân phối thay đổi theo thời gian; cửa sổ gần nhất cho kết quả validation tốt hơn expanding, đặc biệt ở fold cuối. MAE của cấu hình được chọn có xu hướng giảm khoảng 0,60 phút qua mỗi fold, dù độ biến động giữa các tuần vẫn đáng kể.

## 5. Final Test đã khóa

Cấu hình được chọn được retrain trên 49.017 dòng trong 42 ngày trước 01/07; 12 nhãn chưa xuất hiện tại Test start bị purge. Sau đó Final Test được đánh giá đúng một lần.

| Nhóm | Rows | MAE | RMSE | Median AE | P90 AE | Bias | ≤10 phút | ≤20 phút |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Toàn bộ | 41.305 | 15,69 | 319,66 | 6,54 | 21,80 | -6,75 | 67,52% | 88,47% |
| Status 20 | 702 | 190,56 | 1.354,19 | 24,12 | 95,20 | -162,44 | 21,79% | 42,31% |
| Status 40 | 40.603 | 12,67 | 268,78 | 6,44 | 20,81 | -4,06 | 68,31% | 89,27% |

RMSE và R² bị chi phối mạnh bởi các ca chờ thực tế rất dài; mô hình tối ưu MAE có xu hướng dự báo thấp ở đuôi phân phối. Các ca dài không bị xóa.

Hiệu năng status `20` kém rõ rệt và có bias âm lớn. Vì phát hiện này xuất phát từ Final Test, không được dùng để chỉnh mô hình hiện tại. Thiết kế mô hình riêng cho status `20` được ghi nhận là hướng nghiên cứu ngoài phạm vi của kết quả hiện tại.

## 6. Artifact và cách chạy

Script: `02_processing/train_waiting_models.py`

```powershell
.\.venv\Scripts\python.exe 02_processing\train_waiting_models.py --stage validate
.\.venv\Scripts\python.exe 02_processing\train_waiting_models.py --stage final-test
```

Sau lần chạy hiện tại, lệnh `final-test` thứ hai sẽ bị từ chối bởi `FINAL_TEST_EVALUATED.json`.

| File | Nội dung |
|---|---|
| `fold_boundaries.csv` | Mốc thời gian, số dòng và label purge từng fold |
| `validation_fold_metrics.csv` | Metric của từng fold |
| `validation_metric_summary.csv` | Mean, median, std, min, max và trend |
| `selected_model.json` | Cấu hình được chọn khi Test còn khóa |
| `walk_forward_timeline.png` | Timeline expanding/sliding/Test |
| `validation_mae_trend.png` | Xu hướng MAE qua các fold |
| `feature_audit.csv` | Quyết định dùng/loại feature theo thời điểm dự báo |
| `final_model.joblib` | Pipeline cuối có thể load ở tiến trình khác |
| `final_test_metrics.csv` | Metric Test tổng thể và theo status |
| `final_test_predictions.csv` | Dự báo từng dòng Test để audit |
| `FINAL_TEST_EVALUATED.json` | Khóa và dấu vết lần đánh giá Test duy nhất |
