# Quy trình xử lý dữ liệu HIS

> Cập nhật theo mã nguồn và kết quả hiện có ngày 31/08/2026.

## 1. Mục tiêu và phạm vi

Mục tiêu hiện tại là xây dựng dữ liệu phục vụ phân tích và dự báo thời gian chờ khám ngoại trú ở cấp **dịch vụ khám** (`nb_dich_vu_id`). Quy trình tập trung vào:

1. kiểm tra chất lượng năm nguồn dữ liệu HIS;
2. chuẩn hóa các mốc thời gian thành event log;
3. tạo target thời gian chờ;
4. tạo bộ đặc trưng baseline không sử dụng thông tin xảy ra sau thời điểm dự báo.

Phạm vi dữ liệu được trích xuất:

- thời gian vào viện từ `2026-04-01 00:00:00` đến trước `2026-08-01 00:00:00`;
- `doi_tuong_kcb = 1`;
- encounter còn hiệu lực (`active IS TRUE`);
- các khoa `152, 153, 155, 156, 157, 158, 159, 160, 161` trong tập encounter;
- tập khám `03_exam` chỉ gồm `loai_dich_vu = 10`.

Đơn vị phân tích chính là một dịch vụ khám, không phải một bệnh nhân hoặc một lượt điều trị. Một lượt điều trị (`nb_dot_dieu_tri_id`) có thể chứa nhiều dịch vụ.

## 2. Cấu trúc thư mục và script

| Thư mục | Vai trò |
|---|---|
| `01_data_input/` | Năm file trích xuất từ HIS |
| `02_processing/` | Các script kiểm tra và xử lý dữ liệu |
| `03_work/csv/` | CSV trung gian dùng cho audit theo chunk |
| `03_output/` | Event log, báo cáo chất lượng và target chính |
| `docs/` | Tài liệu quy trình |

| Script | Chức năng |
|---|---|
| `prepare_his_events.py` | Nạp dữ liệu, kiểm tra chất lượng và tạo event log chuẩn hóa |
| `his_step1_audit.py` | Audit năm nguồn và tạo target khám V2 theo luồng `20 → 60` hoặc `40 → 60` |
| `build_waiting_target.py` | Tạo target baseline từ `service_ordered → service_received` |
| `build_features.py` | Tạo đặc trưng baseline từ target hợp lệ |
| `train_waiting_models.py` | Walk-forward validation, chọn mô hình và đánh giá Final Test có khóa |

## 3. Dữ liệu đầu vào

Năm truy vấn trích xuất đầy đủ được lưu riêng tại [Nguồn SQL.md](Nguồn%20SQL.md). File này chỉ mô tả vai trò, quy trình xử lý và kết quả sau trích xuất.

| File | Grain | Khóa chính | Số dòng | Vai trò |
|---|---|---|---:|---|
| `01_encounter.csv` | Một lượt điều trị | `nb_dot_dieu_tri_id` | 167.518 | Cohort, nhân khẩu học, đối tượng và thời gian vào viện |
| `02_reception.csv` | Một lần lấy số/tiếp đón | `lay_so_tiep_don_id` | 145.951 | Mốc lấy số và hoàn thành tiếp đón |
| `03_exam.csv` | Một dịch vụ khám | `nb_dich_vu_id` | 170.216 | Thông tin khám, phòng, khoa, bác sĩ và ưu tiên |
| `04_service_journey.csv` | Một dịch vụ trong hành trình | `nb_dich_vu_id` | 676.727 | Mốc chỉ định, tiếp nhận, thực hiện và có kết quả |
| `05_exam_status_log.csv` | Một lần đổi trạng thái khám | Dịch vụ + thời gian + trạng thái | 1.017.080 | Lịch sử chuyển trạng thái dịch vụ khám |

Tất cả file hiện tại là CSV thật. Encoding được nhận diện như sau:

| Tập | Encoding |
|---|---|
| encounter | `cp1258` |
| reception | `utf-8-sig` |
| exam | `utf-8-sig` |
| journey | `utf-8` |
| status log | `cp1258` |

Pipeline vẫn hỗ trợ workbook XLSX mang phần mở rộng `.csv` bằng cách kiểm tra chữ ký nhị phân của file.

## 4. Luồng nghiệp vụ và ánh xạ trạng thái

```text
Vào viện
  → Lấy số tiếp đón
  → Hoàn thành tiếp đón
  → Chỉ định dịch vụ
  → Vào hàng đợi khám
  → Bắt đầu khám
  → Kết luận
  → Hoàn thành hoặc bỏ qua
```

Một encounter có thể rẽ nhánh sang nhiều dịch vụ khám, xét nghiệm hoặc chẩn đoán hình ảnh. Vì vậy mọi phép tính thời gian chờ khám phải giữ đúng grain `nb_dich_vu_id`.

| Mã | Nhóm chuẩn hóa | Diễn giải |
|---:|---|---|
| 20, 30, 40 | `waiting` | Chờ khám |
| 50 | `abandoned` | Bỏ qua |
| 60, 70 | `in_service` | Đang khám/thực hiện dịch vụ |
| 100, 110, 120 | `pending_conclusion` | Chờ kết luận |
| 130 | `abandoned` | Bỏ qua kết luận khám |
| 140 | `concluding` | Đang kết luận |
| 150 | `completed` | Đã kết luận |
| Mã khác | `needs_mapping` | Chưa có ánh xạ nghiệp vụ |

## 5. Bước 1 – Nạp dữ liệu và tạo event log

Script: `02_processing/prepare_his_events.py`

### Xử lý

1. Kiểm tra file và nhận diện CSV/XLSX bằng chữ ký file.
2. Dò encoding trên phần đầu file.
3. Chuẩn hóa tên cột và chuyển các cột `thoi_gian_*` sang `datetime`.
4. Kiểm tra số dòng, khóa null/trùng, timestamp thiếu, duration âm và khả năng nối khóa.
5. Chuyển timestamp nguồn thành event có schema thống nhất.
6. Loại event trùng hoàn toàn và sắp xếp theo dịch vụ, thời gian, tên event.

Schema event chuẩn:

```text
nb_dot_dieu_tri_id, nb_dich_vu_id, event_name, event_time,
room_id, department_id, staff_id, source_status, source_table
```

### Output

| File | Nội dung |
|---|---|
| `03_output/data_quality_report.csv` | Báo cáo chất lượng cơ bản |
| `03_output/ingestion_metadata.json` | Định dạng và encoding |
| `03_output/event_log_normalized.csv` | Event log chuẩn hóa |

### Kết quả hiện tại

Event log đã được tái tạo ngày 06/09/2026 bằng code hiện tại và có **3.344.324 event**. Lần tái tạo này cũng sửa lỗi khử trùng từng làm gộp nhầm các encounter/reception khác nhau có cùng timestamp.

| Event | Số dòng |
|---|---:|
| `encounter_arrived` | 167.518 |
| `reception_number_taken` | 145.951 |
| `reception_completed` | 145.951 |
| `service_ordered` | 676.727 |
| `service_received` | 259.354 |
| `service_started` | 676.727 |
| `service_result_available` | 255.016 |
| `status_waiting` | 311.830 |
| `status_in_service` | 227.714 |
| `status_pending_conclusion` | 110.731 |
| `status_concluding` | 184.071 |
| `status_completed` | 181.244 |
| `status_abandoned` | 1.490 |

Các điểm chất lượng đáng chú ý:

- khóa chính của encounter, reception, exam và journey không null, không trùng;
- status log có nhiều dòng trên một `nb_dich_vu_id` là đúng grain lịch sử;
- `thoi_gian_ra_vien` thiếu 10.415 dòng;
- exam thiếu 3.209 `thoi_gian_tiep_nhan` và 7.260 `thoi_gian_ket_luan`;
- journey thiếu 417.373 `thoi_gian_tiep_nhan` và 421.711 `thoi_gian_co_ket_qua`;
- có 201 trường hợp `thoi_gian_tiep_nhan < thoi_gian_chi_dinh` trong journey;
- có 27.268 trường hợp `thoi_gian_thuc_hien < thoi_gian_tiep_nhan`, cần kiểm tra lại ý nghĩa trường hoặc dữ liệu nguồn.

## 6. Bước 2 – Audit và target khám V2

Script: `02_processing/his_step1_audit.py`

Đây là nhánh audit chính cho bài toán **thời gian chờ khám**.

### Xử lý

1. Đọc năm nguồn theo chunk để giới hạn bộ nhớ.
2. Thống kê chất lượng từng cột, khóa, timestamp và phân phối trạng thái.
3. Kiểm tra tỷ lệ nối giữa encounter, exam, journey và status log.
4. Với mỗi dịch vụ trong `03_exam`:
   - lấy status `60` đầu tiên làm `exam_start_time`;
   - nếu `ma_dich_vu = KB008` hoặc `ma_phong_thuc_hien = H401`, lấy status `20` cuối cùng trước hoặc đúng status `60` làm `queue_entry_time`;
   - với các trường hợp còn lại, lấy status `40` cuối cùng trước hoặc đúng status `60` làm `queue_entry_time`;
   - tính `waiting_minutes = exam_start_time - queue_entry_time`;
   - target hợp lệ khi có đúng trạng thái hàng đợi bắt buộc theo nhánh nghiệp vụ và `waiting_minutes >= 0`.
5. Gắn cờ chờ trên 2 giờ, 4 giờ, 24 giờ và chờ qua ngày.
6. Xuất báo cáo Excel, CSV target và JSON tổng hợp.

Status `20` biểu diễn chờ khám sau đăng ký. Riêng `KB008` và phòng `H401` không cần đo thị lực nên dùng `20 → 60`; các trường hợp còn lại dùng thời gian chờ sau đo thị lực `40 → 60`. Pipeline không tự động fallback sang status khác khi thiếu trạng thái bắt buộc.

### Output

| File | Nội dung |
|---|---|
| `03_output/HIS_data_audit_report.xlsx` | Báo cáo audit nhiều sheet |
| `03_output/exam_wait_target_v2.csv` | Toàn bộ dịch vụ khám, gồm quy tắc target và các dòng không hợp lệ |
| `03_output/exam_wait_target_v2_valid.csv` | Chỉ target V2 hợp lệ |
| `03_output/step1_summary.json` | Kết quả tổng hợp dạng machine-readable |

### Kết quả target V2

| Chỉ số | Kết quả |
|---|---:|
| Tổng dịch vụ khám | 170.216 |
| Có status `60` | 167.306 |
| Thuộc nhánh target status `20` | 3.101 |
| Thuộc nhánh target status `40` | 167.115 |
| Target hợp lệ | 141.519 |
| Tỷ lệ hợp lệ | 83,14% |
| Median | 15,82 phút |
| P90 | 48,10 phút |
| P95 | 66,18 phút |
| P99 | 135,09 phút |
| Chờ trên 2 giờ | 1.804 |
| Chờ trên 4 giờ | 506 |
| Chờ trên 24 giờ | 77 |
| Chờ qua ngày | 123 |
| Thiếu status `20` bắt buộc | 0 |
| Thiếu status `40` bắt buộc | 25.787 |

Các ca trên 24 giờ và qua ngày là trường hợp thực tế, được giữ nguyên trong target và chỉ gắn cờ để báo cáo.

### Kết quả nối khóa

| Quan hệ | Khớp | Không khớp | Tỷ lệ khớp |
|---|---:|---:|---:|
| exam → encounter | 170.213 | 3 | 99,998% |
| reception → encounter | 145.507 | 444 | 99,696% |
| journey → encounter | 636.672 | 40.055 | 94,081% |
| status log → exam | 1.017.080 | 0 | 100% |
| exam → journey | 170.216 | 0 | 100% |

Ba exam không nối được encounter và 40.055 journey không nối được encounter cần được kiểm tra trước khi dùng thuộc tính ở cấp lượt điều trị.

## 7. Bước 3 – Target baseline từ event log

Script: `02_processing/build_waiting_target.py`

Nhánh này tạo target kỹ thuật từ event log và **không đồng nhất** với target khám V1 ở Bước 2.

### Định nghĩa

```text
ordered_at       = service_ordered đầu tiên
received_at      = service_received đầu tiên
waiting_minutes  = received_at - ordered_at
target_valid     = received_at tồn tại và waiting_minutes >= 0
```

Script giữ metadata phòng, khoa, nhân viên tại thời điểm chỉ định và tiếp nhận. Khi tạo feature, chỉ metadata tại thời điểm chỉ định được dùng để hạn chế leakage. Event log được đọc theo chunk và rút gọn trước khi ghép nhằm giảm RAM.

### Output

| File | Nội dung |
|---|---|
| `03_output/waiting_target.csv` | Target cho mọi dịch vụ có `service_ordered` |
| `03_output/waiting_target_summary.csv` | Tổng hợp target hợp lệ, thiếu và âm |

### Kết quả lần kiểm thử gần nhất

| Chỉ số | Kết quả |
|---|---:|
| Dịch vụ có `service_ordered` | 359.524 |
| Target hợp lệ | 137.693 |
| Thiếu `received_at` | 221.719 |
| Waiting time âm | 112 |
| Có outcome bỏ qua/hoàn thành | 84.035 |

Các file kiểm thử này không được giữ trong `03_output`; cần chạy lại script nếu chọn nhánh target baseline.

### So sánh hai target

| Target khám V2 | Target event baseline |
|---|---|
| Chỉ 170.216 dịch vụ khám trong `03_exam` | 359.524 dịch vụ có event chỉ định trong journey |
| `KB008`/`H401` dùng `20 → 60`, ca khác dùng `40 → 60` | Dùng `service_ordered → service_received` |
| Đo thời gian ở hàng đợi khám theo status | Đo khoảng từ chỉ định đến tiếp nhận dịch vụ |
| 141.519 target hợp lệ | 137.693 target hợp lệ trong lần kiểm thử |

Không gộp hoặc so sánh trực tiếp hai target nếu chưa thống nhất định nghĩa nghiệp vụ.

## 8. Bước 4 – Tạo đặc trưng baseline

Script: `02_processing/build_features.py`

### Input

- `03_output/waiting_target.csv` từ Bước 3;
- `01_data_input/01_encounter.csv`;
- `01_data_input/03_exam.csv`.

Chỉ các dòng `target_valid = TRUE` được giữ lại.

### Nhóm đặc trưng

| Nhóm | Biến |
|---|---|
| Thời gian | `arrival_hour`, `day_of_week`, `arrival_month`, `is_weekend`, `target_hour` |
| Người bệnh | `gioi_tinh`, `ngay_sinh`, `chi_nam_sinh`, `doi_tuong`, `loai_doi_tuong_id`, `cap_cuu`, `kham_suc_khoe` |
| Tổ chức khám | `phong_thuc_hien_id`, `khoa_thuc_hien_id`, `bac_si_kham_id` |
| Ưu tiên | `uu_tien` |
| Tải toàn hệ thống | `arrivals_last_15m`, `arrivals_last_30m`, `arrivals_last_60m` |
| Hàng đợi cùng phòng | `patients_ahead_same_room` |

`patients_ahead_same_room` là số dịch vụ cùng phòng đã được chỉ định trước đó nhưng chưa bắt đầu tại thời điểm hiện tại. Các dòng cùng timestamp không tự đếm lẫn nhau.

Phòng, khoa và bác sĩ ưu tiên lấy từ metadata tại `service_ordered`; bác sĩ từ `03_exam` là nguồn bổ sung khi giá trị đó còn thiếu.

### Output và kết quả kiểm thử

| File | Nội dung |
|---|---|
| `03_output/model_features_baseline.csv` | Dữ liệu feature và target baseline |
| `03_output/model_features_summary.csv` | Thống kê số dòng và mức thiếu |

| Chỉ số | Kết quả |
|---|---:|
| Số dòng feature | 137.693 |
| Thiếu phòng | 0 |
| Thiếu khoa | 0 |
| Thiếu bác sĩ | 51.589 |
| Không nối được thuộc tính encounter | 8.249 |

Thiếu bác sĩ cao vì target baseline chứa nhiều loại dịch vụ hơn tập `03_exam`. Đây là một lý do cần thống nhất target chính trước khi huấn luyện.

## 9. Thứ tự chạy

Từ thư mục gốc dự án trên Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe 02_processing\prepare_his_events.py
.\.venv\Scripts\python.exe 02_processing\his_step1_audit.py
```

Hai lệnh trên tạo event log, báo cáo chất lượng và target khám V1 chính.

Chỉ chạy hai bước sau nếu tiếp tục với target event baseline:

```powershell
.\.venv\Scripts\python.exe 02_processing\build_waiting_target.py
.\.venv\Scripts\python.exe 02_processing\build_features.py
```

Có thể truyền đường dẫn rõ ràng:

```powershell
.\.venv\Scripts\python.exe 02_processing\prepare_his_events.py `
  --input-dir 01_data_input `
  --output-dir 03_output

.\.venv\Scripts\python.exe 02_processing\his_step1_audit.py `
  --raw-dir 01_data_input `
  --normalized-dir 03_work\csv `
  --out-dir 03_output
```

## 10. Quy tắc chống target leakage

- Không dùng `received_at`, `exam_start_time`, `waiting_minutes`, trạng thái hoàn thành hoặc kết luận làm input dự báo.
- Không dùng `thoi_gian_ket_luan`, `thoi_gian_co_ket_qua`, `ket_qua_dieu_tri` hoặc `thoi_gian_hen_kham`.
- Phòng, khoa và bác sĩ chỉ được dùng nếu đã biết tại thời điểm dự báo.
- Các biến cửa sổ chỉ được đếm từ event xảy ra trước thời điểm dự báo.
- Chia train/validation/test theo thời gian, không chia ngẫu nhiên toàn bộ dữ liệu.
- Mọi phép nối phải giữ grain một dòng trên một `nb_dich_vu_id`.

## 11. Trạng thái hiện tại và bước tiếp theo

Target chính cho bài toán **dự báo thời gian chờ khám** hiện tại là `exam_wait_target_v2_valid.csv`. Quy tắc nghiệp vụ đã được chốt: `KB008` hoặc phòng `H401` dùng `20 → 60`; các trường hợp khác dùng `40 → 60`.

Notebook `02_processing/step2_eda_features.ipynb` đã được chạy lại với 141.519 target hợp lệ và đã xuất tập model V2. Pipeline mô hình tại `02_processing/train_waiting_models.py` đã hoàn tất walk-forward validation, chọn sliding window 42 ngày với HistGradientBoosting absolute loss và đánh giá Final Test đúng một lần. Kết quả Test tổng thể: MAE 15,69 phút và Median AE 6,54 phút. Chi tiết xem [`Buoc_3_trien_khai_mo_hinh.md`](Buoc_3_trien_khai_mo_hinh.md).

Notebook EDA chính thức hiện tại là [`step2_eda_features.ipynb`](../02_processing/step2_eda_features.ipynb). Notebook xuất biểu đồ, bảng tổng hợp, danh sách ngoại lệ và tập feature vào `03_output/step2_eda/`. Script mô hình chính thức xuất kết quả vào `03_output/step3_modeling/`; Final Test hiện đã khóa và không được dùng để tiếp tục lựa chọn mô hình. Các số liệu EDA và target 259.153 dòng trong bản tài liệu cũ không thuộc pipeline hiện tại nên đã được loại bỏ.

Một thử nghiệm cải thiện riêng trên development data đã bổ sung feature tải hàng đợi và công suất phục vụ theo thời gian. Cấu hình tốt nhất nâng tỷ lệ sai số không quá 20 phút trung bình từ 88,23% lên 89,45% trên sáu development folds, nhưng không thay thế kết quả Final Test chính thức. Chi tiết xem [`Toi_uu_do_chinh_xac_development.md`](Toi_uu_do_chinh_xac_development.md).
