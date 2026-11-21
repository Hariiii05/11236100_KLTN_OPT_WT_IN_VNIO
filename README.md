# Opt-WT-in-VNIO

Dự báo thời gian chờ khám của bệnh nhân từ dữ liệu HIS (hệ thống thông tin bệnh viện). Tại thời điểm bệnh nhân vào hàng đợi khám, mô hình đưa ra:

1. **Thời gian chờ dự báo** (phút) và **khoảng dự báo** P50–P80 đã hiệu chỉnh.
2. **Xác suất "chờ quá giờ nghỉ trưa"** đối với bệnh nhân đăng ký buổi sáng, kèm khuyến nghị *chờ tiếp* hoặc *quay lại buổi chiều* với giờ gợi ý.

Dữ liệu hiện có: từ 01/04/2026 đến 31/07/2026 (141.519 lượt khám có target hợp lệ; 138.591 lượt sau khi lọc cohort mô hình).

## 1. Bắt đầu nhanh

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .          # 60 test
```

Chạy pipeline hiện hành (V3) khi đã có `data/processed/model_dataset.csv`:

```powershell
.\.venv\Scripts\python.exe -m pipeline.run_unified_pipeline --stage dev          # tối ưu, chẩn đoán, hiệu chỉnh (chạy lại được)
.\.venv\Scripts\python.exe -m pipeline.run_unified_pipeline --stage final-eval   # đánh giá holdout, chỉ chạy đúng một lần
.\.venv\Scripts\python.exe -m pipeline.run_unified_pipeline --stage refit-final  # huấn luyện lại trên dữ liệu mới nhất
```

`--stage all` chạy cả ba bước liên tiếp cho lần tái lập đầu tiên. Kết quả V3 đã có sẵn trong `results/unified_pipeline_v3/`; vì `final-eval` đã chạy và bị khóa nên `--stage all` bị chặn. Muốn tái lập từ đầu, thêm `--output-dir <thư mục mới>`.

## 2. Quy trình tổng quan

```text
data/raw  (5 file trích xuất từ HIS)
   │  pipeline.prepare_event_log
   ▼
data/intermediate + data/processed/event_log.csv + results/audit/data_quality_report.csv
   │  pipeline.audit_and_build_exam_target
   ▼
data/processed/exam_wait_target_all.csv, exam_wait_target_valid.csv + results/audit/HIS_data_audit_report.xlsx
   │  notebooks/eda_and_build_model_dataset.ipynb
   ▼
data/processed/model_dataset.csv + results/eda
   │  pipeline.run_unified_pipeline   (V3, hiện hành)
   ▼
results/unified_pipeline_v3   (mô hình, chỉ số holdout, cảnh báo giờ nghỉ trưa)
```

| Bước | Thực hiện bởi | Làm gì |
|---|---|---|
| 1. Chuẩn hóa và kiểm tra dữ liệu | `pipeline/prepare_event_log.py` | Đọc 5 file nguồn (nhận diện cả file đuôi `.csv` thực chất là XLSX), kiểm tra khóa/thời gian/join, gộp thành event log thống nhất |
| 2. Audit và tạo target | `pipeline/audit_and_build_exam_target.py` | Audit đầy đủ 5 nguồn, tính thời gian chờ cho từng dịch vụ khám, gắn cờ ca bất thường |
| 3. EDA và tạo model dataset | `notebooks/eda_and_build_model_dataset.ipynb` | Khám phá dữ liệu, xuất bảng/biểu đồ và `model_dataset.csv` (đầu vào của mô hình) |
| 4. Mô hình hóa | `pipeline/run_unified_pipeline.py` | Pipeline hợp nhất, gồm các bước nhỏ ở bảng dưới |

Các bước bên trong `run_unified_pipeline`:

| Thứ tự | Bước | Module |
|---:|---|---|
| 1 | Lọc cohort: loại dịch vụ khám nội KB008 và ca chờ >24 giờ | `cohort.py` |
| 2 | Feature engineering: lịch, tải hàng đợi, loại bệnh nhân, đặc trưng giờ nghỉ trưa | `feature_engineering.py` |
| 3 | Chia theo thời gian và khóa holdout ngay sau khi chia | `data_split.py` |
| 4 | Tìm mô hình/siêu tham số bằng walk-forward CV theo thời gian | `model_training.py` |
| 5 | Chẩn đoán lỗi theo phân nhóm (giờ, ngày, phòng, khoa, mức đông, loại bệnh nhân, đuôi chờ dài) | `diagnostics.py` |
| 6 | Classifier "chờ quá giờ nghỉ trưa" và chọn ngưỡng trên validation | `lunch_warning.py` |
| 7 | Huấn luyện lại và hiệu chỉnh khoảng dự báo theo khoa (split-conformal) | `model_training.py` |
| 8 | Đánh giá holdout đúng một lần, so sánh V1/V2/V3 trên cùng dòng dữ liệu | `run_unified_pipeline.py` |
| 9 | Huấn luyện lại công thức đã đóng băng trên dữ liệu mới nhất để triển khai | `run_unified_pipeline.py` |

Toàn bộ tham số (mốc holdout, độ dài fold, giờ nghỉ trưa, quy tắc cohort, random seed) nằm ở `pipeline/pipeline_config.py` và chỉnh được qua tham số dòng lệnh.

## 3. Khái niệm chính

- **Target**: `waiting_minutes` = từ status 40 **cuối cùng** trước status 60 **đầu tiên** ("đang khám"); chỉ KB008/phòng H401 dùng status 20 (V3 đã loại nhóm này). Mã 20, 30, 40 cùng mang tên "chờ khám" nhưng **không thay thế được cho nhau**: khoảng 20→60 gồm cả thanh toán và bước tiền khám (trung vị 31,3 phút so với 15,6 phút của 40→60). Dịch vụ có status 60 mà thiếu status 40 (25.787 dịch vụ, 15,15%) không có target và bị loại, không fallback sang status 20; phân tích chi tiết ở [`docs/missing_status40_analysis.md`](docs/missing_status40_analysis.md).
- **Cohort mô hình (V3)**: loại dịch vụ khám nội **KB008** (phòng H401, E1108) vì đây là dịch vụ được chỉ định trong lúc khám dịch vụ khác nên status 20 không phải mốc bắt đầu chờ; loại các ca chờ **>24 giờ** vì là bệnh nhân bỏ về. Các file audit vẫn giữ đầy đủ, việc lọc chỉ áp dụng trong pipeline mô hình.
- **Chia dữ liệu**: theo thời gian, không xáo trộn. Train trượt 42 ngày, validation gồm 6 fold walk-forward mỗi fold 7 ngày, holdout từ 15/07/2026 (17 ngày lịch có dữ liệu). Nhãn chưa xuất hiện tại đầu fold bị loại (purge) để không rò rỉ tương lai.
- **Feature**: chỉ dùng thông tin có trước lúc vào hàng đợi. Gồm lịch (giờ, thứ), danh mục dịch vụ/phòng/khoa, tải hàng đợi (số lượt đến, số lượt bắt đầu khám, số người đứng trước), loại bệnh nhân (BHYT hay dịch vụ) và khoảng cách đến giờ nghỉ trưa.
- **Cảnh báo giờ nghỉ trưa**: bệnh nhân đăng ký trước 11:30 mà thực tế khám từ 11:30 trở đi. Giờ nghỉ trưa mặc định [11:30–13:00], chỉnh được.
- **Khóa holdout**: holdout được ghi vào file khóa ngay sau khi chia và chỉ được đánh giá một lần; sau đó file `*_EVALUATED.json` chặn mọi lần chạy lại.

### Ba thế hệ mô hình

| Thế hệ | Nội dung | Thư mục kết quả | Trạng thái |
|---|---|---|---|
| **V1** | Mô hình chính thức đầu tiên: HistGradientBoosting, walk-forward, Final Test từ 01/07/2026 | `results/final_model/` | Đã khóa, giữ làm lịch sử |
| **V2** | Pipeline hợp nhất, holdout mới từ 15/07/2026, cảnh báo giờ trưa, hiệu chỉnh theo khoa; cohort đầy đủ | `results/unified_pipeline/` | Đã đánh giá, giữ làm lịch sử |
| **V3** | Như V2 nhưng loại KB008 và ca >24 giờ; thêm bước huấn luyện lại trên dữ liệu mới nhất | `results/unified_pipeline_v3/` | **Hiện hành** |

### Mô hình hiện hành

- **Để triển khai (mới nhất)**: `results/unified_pipeline_v3/unified_model_latest.joblib`, huấn luyện đến hết 31/07/2026 (gồm cả giai đoạn holdout). Không có đánh giá độc lập riêng.
- **Để báo cáo chỉ số**: `results/unified_pipeline_v3/unified_model.joblib`, chỉ huấn luyện trên dữ liệu trước 15/07/2026; các chỉ số trong `holdout_final_metrics.json` thuộc mô hình này.

## 4. Cấu trúc thư mục

```text
Code/
├── data/          dữ liệu (không commit)
├── pipeline/      mã nguồn xử lý và mô hình
├── notebooks/     EDA và tạo model dataset
├── tests/         kiểm thử tự động
├── webapp/        ứng dụng web: Tiếp đón → dự báo → QMS → Dashboard
├── results/       kết quả từng bước và từng thế hệ mô hình
├── docs/          tài liệu, truy vấn SQL
├── logs/          log lưu trữ
├── requirements.txt   thư viện Python cần cài
└── README.md      tài liệu này
```

### 4.1. `data/` (loại khỏi Git vì lớn và chứa dữ liệu bệnh nhân)

| Thư mục | Nội dung |
|---|---|
| `raw/` | 5 file trích xuất nguyên bản từ HIS: `01_encounter` (đợt khám), `02_reception` (tiếp đón), `03_exam` (dịch vụ khám), `04_service_journey` (hành trình dịch vụ), `05_exam_status_log` (log đổi trạng thái) |
| `intermediate/` | Bản CSV đã chuẩn hóa của 5 file trên, dùng cho audit và các bước sau |
| `processed/` | `event_log.csv` (event log chuẩn hóa), `exam_wait_target_all.csv` (mọi dịch vụ khám, kể cả target không hợp lệ), `exam_wait_target_valid.csv` (target hợp lệ), `baseline_features.csv`, `model_dataset.csv` (đầu vào mô hình) |

### 4.2. `pipeline/`

**Chuẩn bị dữ liệu**

| File | Vai trò |
|---|---|
| `prepare_event_log.py` | Đọc và kiểm tra 5 file nguồn, tạo event log và báo cáo chất lượng |
| `audit_and_build_exam_target.py` | Audit 5 nguồn, xác định mốc chờ và tạo target khám |

**Pipeline hợp nhất V2/V3 (hiện hành)**

| File | Vai trò |
|---|---|
| `run_unified_pipeline.py` | Điểm vào duy nhất, các giai đoạn `dev`, `final-eval`, `refit-final`, `all` |
| `pipeline_config.py` | Cấu hình tập trung và tham số dòng lệnh |
| `cohort.py` | Quy tắc cohort (loại KB008, ca >24 giờ) và bảng hao hụt |
| `data_split.py` | Chia train/validation/holdout theo thời gian, khóa holdout, tạo fold |
| `feature_engineering.py` | Feature tải hàng đợi, loại bệnh nhân, giờ nghỉ trưa |
| `model_training.py` | Danh sách mô hình thử, walk-forward CV, mô hình quantile, hiệu chỉnh theo khoa |
| `lunch_warning.py` | Classifier "chờ quá giờ nghỉ trưa", chọn ngưỡng, tạo khuyến nghị cho từng bệnh nhân |
| `diagnostics.py` | Bảng sai số theo phân nhóm, đuôi chờ dài, drift, bảng audit feature |

**Phân tích bổ sung (chỉ đọc, không đổi mô hình V3)**

| File | Vai trò |
|---|---|
| `analyze_missing_queue_status.py` | Phân tích dịch vụ thiếu status 40: phạm vi, phân bố theo phòng, có phải bỏ qua bước tiền khám không, ảnh hưởng đến MAE holdout (trọng số lại theo phòng) và đến feature tải. Ghi vào `results/missing_status40_analysis/`, không ghi vào thư mục V3 |

**V1 và các thử nghiệm nền tảng** (giữ nguyên; một số hàm vẫn được V2/V3 import lại)

| File | Vai trò |
|---|---|
| `train_and_evaluate_model.py` | Pipeline V1: walk-forward validation và Final Test có khóa; chứa các hàm dùng chung (đọc dataset, tạo fold, tính metric) |
| `optimize_on_development.py` | Thử nghiệm tối ưu trên development: nhóm feature tải hàng đợi |
| `diagnose_and_calibrate.py` | Thử nghiệm chẩn đoán điểm yếu, feature thời lượng khám/bác sĩ, hiệu chỉnh P50–P80 |
| `legacy/` | Baseline cũ dựa trên `service_ordered → service_received` (`build_event_wait_target.py`, `build_event_features.py`), không thuộc luồng chính |

### 4.3. `notebooks/`

| File | Vai trò |
|---|---|
| `eda_and_build_model_dataset.ipynb` | EDA, xuất bảng/biểu đồ vào `results/eda/`, tạo `baseline_features.csv` và `model_dataset.csv` |

### 4.4. `tests/`

| File | Bảo vệ điều kiện nào |
|---|---|
| `test_event_log.py` | Event log không gộp nhầm hai đợt khám khác nhau có cùng timestamp |
| `test_exam_service_duration.py` | Cách tính thời lượng khám (status 60 → 150) |
| `test_development_optimization.py` | Feature tải hàng đợi chỉ dùng sự kiện xảy ra trước thời điểm dự báo |
| `test_service_state_features.py` | Feature trạng thái ca đang khám và hàm hiệu chỉnh conformal |
| `test_cohort.py` | Quy tắc cohort, bảng hao hụt, giá trị mặc định dòng lệnh |
| `test_data_split.py` | Khóa holdout, mốc holdout tường minh, từ chối khi quy tắc cohort/dữ liệu đổi |
| `test_feature_engineering.py` | Nhãn và feature giờ nghỉ trưa chỉ dùng lịch sử đã biết |
| `test_model_training.py` | Hiệu chỉnh theo nhóm, tách cửa sổ huấn luyện và hiệu chỉnh |
| `test_lunch_warning.py` | Chọn ngưỡng, precision/recall/F1, khuyến nghị và giờ quay lại gợi ý |
| `test_missing_queue_status.py` | Phân nhóm dịch vụ theo status 40/60, hai mẫu số của tỷ lệ thiếu theo phòng, trọng số lại MAE theo phòng, đếm tải khi tính thêm ca thiếu |
| `test_webapp_features.py` | Feature của web app (truy vấn tại một thời điểm) khớp feature lúc huấn luyện; phiếu đang chờ/bỏ lượt/quá 24 giờ được đếm đúng |

### 4.5. `results/`

| Thư mục | Nội dung |
|---|---|
| `audit/` | Báo cáo chất lượng dữ liệu (`HIS_data_audit_report.xlsx`, `data_quality_report.csv`), tổng hợp target (`target_build_summary.json`), metadata nguồn |
| `eda/` | Biểu đồ (`figures/`), bảng (`tables/`, không commit) và tóm tắt EDA |
| `final_model/` | **V1**: kết quả validation, mô hình, Final Test đã khóa (`FINAL_TEST_EVALUATED.json`) |
| `development_experiments/` | V1: kết quả thử nghiệm feature tải hàng đợi trên development folds |
| `model_diagnosis/` | V1: chẩn đoán điểm yếu, ablation feature và hiệu chỉnh P50–P80 |
| `unified_pipeline/` | **V2**: kết quả pipeline hợp nhất trên cohort đầy đủ |
| `unified_pipeline_v3/` | **V3 (hiện hành)**: xem bảng dưới |
| `missing_status40_analysis/` | Kết quả phân tích bổ sung về dịch vụ thiếu status 40 (bảng CSV, `missing_status40_summary.json`, biểu đồ theo phòng); không thuộc V1–V3 |
| `archive/` | Target V1 cũ của nhánh legacy, không dùng để huấn luyện hay báo cáo |

File chính trong `unified_pipeline_v3/` (V2 có bộ file tương tự, trừ `cohort_attrition.csv`, `refit_manifest.json` và `unified_model_latest.joblib`):

| File | Nội dung |
|---|---|
| `holdout_locked.json` | Ranh giới holdout và quy tắc cohort, ghi ngay sau khi chia |
| `HOLDOUT_EVALUATED.json` | Khóa: holdout đã được đánh giá, không chạy lại `final-eval` |
| `cohort_attrition.csv` | Số dòng vào, bị loại theo từng quy tắc, còn lại |
| `fold_boundaries.csv`, `candidate_fold_metrics.csv`, `candidate_summary.csv` | Mốc thời gian fold, kết quả và xếp hạng các mô hình thử |
| `segment_diagnostics_before.csv`, `segment_diagnostics_after.csv`, `tail_and_lunch_before_after.json` | Chẩn đoán lỗi theo phân nhóm trước/sau, đuôi chờ dài, nhóm vượt giờ trưa |
| `lunch_threshold_and_validation_report.json` | Ngưỡng cảnh báo giờ trưa và độ chính xác trên validation |
| `calibration_offsets.json` | Hệ số hiệu chỉnh khoảng dự báo theo khoa |
| `dev_manifest.json` | Tóm tắt mọi quyết định của giai đoạn `dev` để tái lập |
| `unified_model.joblib` | Mô hình đã đánh giá (huấn luyện trước holdout) |
| `holdout_final_metrics.json`, `holdout_segment_diagnostics.csv`, `old_vs_new_comparison.csv` | Kết quả holdout, sai số theo phân nhóm, so sánh V1/V2/V3 trên cùng dòng dữ liệu |
| `holdout_patient_outputs.csv` | Output cho từng bệnh nhân trên holdout (thời gian chờ, khoảng dự báo, xác suất, khuyến nghị); không commit |
| `unified_model_latest.joblib`, `refit_manifest.json` | Mô hình mới nhất để triển khai và thông tin cửa sổ huấn luyện |

### 4.6. `docs/`

| File | Nội dung |
|---|---|
| `pipeline_overview.md` | Mô tả chính thức cấu trúc dự án, điều kiện target và thứ tự thực thi |
| `his_source_queries.md` | Truy vấn SQL nguồn và điều kiện cohort của 5 file trích xuất |
| `final_model_evaluation.md` | Đánh giá mô hình V1 và Final Test đã khóa |
| `development_optimization.md` | Thử nghiệm tối ưu feature tải hàng đợi (V1 ablation) |
| `model_diagnosis_and_calibration.md` | Chẩn đoán điểm yếu, ablation feature và hiệu chỉnh khoảng dự báo (V1 ablation) |
| `unified_pipeline.md` | Pipeline hợp nhất V2 (lịch sử), kèm đính chính đã cập nhật ở V3 |
| `unified_pipeline_v3.md` | Pipeline V3 hiện hành: lý do đổi cohort, kết quả, điểm yếu còn tồn đọng, giả định |
| `missing_status40_analysis.md` | Phân tích bổ sung về 25.787 dịch vụ thiếu status 40: phạm vi, nguyên nhân khả dĩ, ảnh hưởng đến chỉ số và feature, hướng cho V4 |
| `sql/queue_snapshot_for_prediction.sql` | Truy vấn tùy chọn lấy snapshot hàng đợi để tích hợp dự báo trực tiếp |
| `project_description_and_proposed_pipeline.docx` | Tài liệu Word mô tả dự án và pipeline đề xuất (định hướng đề tài, phạm vi rộng hơn phần đã thực hiện) |
| `bao_cao_du_bao_thoi_gian_cho.docx` | Báo cáo Data Analytics / Data Science đầy đủ (22 mục): dữ liệu, audit, EDA, target, feature, các phiên bản mô hình V1–V3, đánh giá, hạn chế và đề xuất |

### 4.7. Các file khác

| File/thư mục | Nội dung |
|---|---|
| `logs/archive/` | Log lưu trữ của lần chạy audit trước khi refactor |
| `requirements.txt` | Thư viện Python (pandas, numpy, scikit-learn, matplotlib, joblib, openpyxl, ...) |
| `.gitignore` | Loại khỏi Git: `.venv/`, dữ liệu trong `data/`, bảng theo từng dòng, prediction theo từng dòng, `results/archive/` |
| `.venv/`, `.jupyter-config/` | Môi trường Python và cấu hình Jupyter cục bộ (không commit) |

## Ứng dụng web vận hành

`webapp/` là web app dùng mô hình V3 thật: tiếp đón người bệnh, dự báo thời gian chờ và giờ dự kiến được khám cho từng phòng, cấp số, QMS (TV phòng khám, khu vực chờ, điều phối, kiosk) và dashboard quản trị.

```powershell
.\.venv\Scripts\python.exe -m pip install -r webapp\requirements.txt
.\.venv\Scripts\python.exe -m webapp          # http://127.0.0.1:8000
```

Web phát lại dữ liệu HIS thật theo một đồng hồ mô phỏng để các biến hàng đợi của mô hình được tính từ tải thực tế; feature của web đã được kiểm chứng trùng khớp tuyệt đối với pipeline huấn luyện (`python -m webapp.verify_parity`). Chi tiết: [`webapp/README.md`](webapp/README.md).

## 5. Kết quả tóm tắt

Chấm trên cùng 23.080 dòng holdout (15/07–31/07/2026, cohort V3). V1 và V2 là mô hình đóng băng, không huấn luyện lại.

| Mô hình | MAE (phút) | Sai số ≤10 phút | Sai số ≤20 phút |
|---|---:|---:|---:|
| V1 | 10,57 | 68,4% | 89,2% |
| V2 | 9,50 | 71,8% | 90,3% |
| **V3** | **9,52** | 71,4% | **90,5%** |

Cảnh báo "chờ quá giờ nghỉ trưa" của V3 trên holdout: precision 0,47, recall 0,81, F1 0,60. V3 hơn V1 rõ rệt nhưng **ngang V2** trên cùng cohort; MAE toàn cục giảm từ 15,18 (V2 trên cohort cũ) xuống 9,52 chỉ do đổi cohort, không phải mô hình tốt lên. Chi tiết và điểm yếu còn tồn đọng ở [`docs/unified_pipeline_v3.md`](docs/unified_pipeline_v3.md).

## 6. Lưu ý quan trọng

- **Không chạy lại phần đã khóa**: `results/final_model/FINAL_TEST_EVALUATED.json` (V1), `results/unified_pipeline/HOLDOUT_EVALUATED.json` (V2) và `results/unified_pipeline_v3/HOLDOUT_EVALUATED.json` (V3). Các file này ngăn đánh giá holdout lần thứ hai.
- **V1 và V2 giữ nguyên** làm dấu vết lịch sử; kết quả mới luôn ghi vào thư mục riêng. `results/final_model/selected_model.json` và `results/eda/eda_summary.json` còn ghi đường dẫn tuyệt đối cũ, đó là provenance lịch sử và không được sửa để bảo toàn dấu vết.
- **Khi triển khai**: KB008 (khám nội) không có dự báo, và khi đếm "số người đứng trước" phải bỏ các lượt vào hàng đợi đã quá 24 giờ để khớp với dữ liệu huấn luyện.
- **Dữ liệu**: không đưa dữ liệu HIS, bảng theo từng lượt khám hay prediction theo từng dòng lên repository.

Tài liệu chi tiết từng phần nằm trong `docs/` (mục 4.6).
