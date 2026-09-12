# Thử nghiệm tăng độ chính xác trên development data

> Thử nghiệm ngày 12/09/2026 chỉ sử dụng 100.214 dòng trước `2026-07-01`. Final Test đã khóa không được đọc để fit, chọn cấu hình hoặc đánh giá lại.

## 1. Mục tiêu

Mục tiêu chính là tăng tỷ lệ dự báo có sai số không quá 20 phút. Để tránh đổi một metric lấy sự suy giảm lớn ở metric khác, cấu hình chỉ đủ điều kiện lựa chọn khi MAE trung bình không cao hơn baseline quá một phút.

Quy tắc xếp hạng:

```text
maximize mean(within_20m_rate) - 0,25 × std(within_20m_rate)
subject to mean MAE <= baseline mean MAE + 1 phút
```

Đánh giá dùng sáu validation fold liên tiếp, mỗi fold bảy ngày, với sliding training window 42 ngày và quy tắc purge label giống pipeline chính thức.

## 2. Feature bổ sung

Nhóm được chọn mô tả trạng thái hàng đợi và công suất phục vụ tại đúng `queue_entry_time`:

- số lượt vào hàng đợi cùng phòng trong 15, 30 và 60 phút trước đó;
- số lượt vào hàng đợi cùng khoa trong 30 và 60 phút trước đó;
- số bệnh nhân vào trước và vẫn đang chờ tại cùng phòng/khoa;
- số lượt bắt đầu khám cùng phòng trong 15, 30 và 60 phút trước đó;
- số lượt bắt đầu khám cùng khoa trong 30 và 60 phút trước đó;
- số phút từ lần bắt đầu khám gần nhất tại phòng/khoa.

Mọi phép đếm chỉ dùng event có timestamp nhỏ hơn thời điểm dự báo. Các dòng cùng timestamp không đếm lẫn nhau. Trạng thái “vẫn đang chờ” có thể quan sát trực tiếp từ hàng đợi tại thời điểm dự báo; trong backtest nó được tái dựng bằng việc chưa có status `60` trước thời điểm đó.

Thống kê waiting lịch sử 7/42 ngày cũng được thử với điều kiện label đã xuất hiện trước dự báo, nhưng không cải thiện bằng nhóm queue dynamics. Quantile loss 55–60% và log target cũng không thắng cấu hình absolute loss.

## 3. Kết quả development walk-forward

| Chỉ số | Baseline chính thức | Queue dynamics deeper | Thay đổi |
|---|---:|---:|---:|
| MAE trung bình | 17,55 phút | **16,89 phút** | **−0,66 phút** |
| Median AE trung bình | 6,67 phút | **6,07 phút** | **−0,60 phút** |
| P90 AE trung bình | 21,95 phút | **20,62 phút** | **−1,33 phút** |
| Sai số ≤10 phút | 66,19% | **69,14%** | **+2,95 điểm %** |
| Sai số ≤20 phút | 88,23% | **89,45%** | **+1,22 điểm %** |
| Bias | −9,13 phút | −9,14 phút | gần như không đổi |

Tỷ lệ ≤20 phút tăng ở cả sáu fold, từ `+0,62` đến `+1,51` điểm phần trăm. Trung bình chênh lệch là `+1,22` điểm phần trăm; khoảng tin cậy 95% theo sáu chênh lệch fold là khoảng `+0,87` đến `+1,57` điểm phần trăm. Trên tổng 49.029 lượt validation, số ca sai quá 20 phút giảm từ khoảng 5.785 xuống 5.173, tương đương giảm 612 ca hoặc khoảng 10,6% số lỗi loại này.

Cấu hình được chọn:

```text
HistGradientBoostingRegressor
loss = absolute_error
learning_rate = 0,06
max_iter = 180
max_leaf_nodes = 63
min_samples_leaf = 30
l2_regularization = 1,0
```

## 4. Diễn giải đúng phạm vi

Kết quả cho thấy queue dynamics cải thiện ổn định trên development folds. Đây là thử nghiệm phát triển, không thay thế kết quả Final Test chính thức và không được diễn giải là Final Test đã tăng từ 88,47% lên một con số mới.

Trong luận văn có thể trình bày phần này như một ablation hoặc hướng cải tiến đã kiểm chứng bằng walk-forward development validation. Kết quả Final Test và mô hình chính thức trong `03_output/step3_modeling/` vẫn giữ nguyên.

## 5. Artifact

- Script: `02_processing/optimize_development_model.py`
- Metric từng fold: `03_output/development_optimization/development_fold_metrics.csv`
- Bảng tổng hợp: `03_output/development_optimization/development_metric_summary.csv`
- Cấu hình được chọn: `03_output/development_optimization/selected_development_candidate.json`
- Biểu đồ so sánh: `03_output/development_optimization/development_candidate_comparison.png`
