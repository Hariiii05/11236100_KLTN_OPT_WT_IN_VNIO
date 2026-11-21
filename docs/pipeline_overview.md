# Tổng quan pipeline dự báo thời gian chờ khám HIS

Tài liệu này là nguồn mô tả chính thức cho cấu trúc dự án, điều kiện target và thứ tự thực thi. Các tên `step1`, `step2`, `step3` và hậu tố file `v1`, `v2` trước đây đã được thay bằng tên mô tả chức năng để tránh nhầm giữa pipeline chính thức và nhánh thử nghiệm cũ.

> **Cập nhật:** mục 4.4–4.6 mô tả pipeline V1 (mô hình chính thức đã khóa tại `results/final_model/`, giữ nguyên làm dấu vết lịch sử, không sửa/không chạy lại). Mục 4.7 mô tả **pipeline hợp nhất V2/V3**, hợp nhất `optimize_on_development` và `diagnose_and_calibrate`, chia lại holdout theo thời gian và bổ sung cảnh báo giờ nghỉ trưa. **V3 là bản hiện hành** (loại dịch vụ khám nội KB008 và ca chờ >24 giờ khỏi cohort) — xem [`unified_pipeline_v3.md`](unified_pipeline_v3.md); V2 giữ làm lịch sử tại [`unified_pipeline.md`](unified_pipeline.md). V1 chỉ để đối chiếu.

## 1. Mục tiêu và phạm vi

- Đơn vị dự báo: một dịch vụ khám (`nb_dich_vu_id`).
- Thời điểm dự báo: lúc dịch vụ đi vào hàng đợi (`queue_entry_time`).
- Target: số phút từ thời điểm vào hàng đợi hợp lệ đến status `60` đầu tiên, đại diện thời điểm bắt đầu khám.
- Dữ liệu hiện tại: từ 01/04/2026 đến 04/08/2026.
- Final Test: các dòng từ 01/07/2026, đã đánh giá đúng một lần và được khóa.
- Pipeline V1 (mục 4.4) không có holdout lần hai. Pipeline V2 (mục 4.7) chia lại toàn bộ dữ liệu và có một holdout mới, khóa ngay sau khi chia và chỉ đánh giá một lần.

## 2. Cấu trúc thư mục

```text
Code/
├── data/
│   ├── raw/                         # 5 file trích xuất nguyên bản từ HIS
│   ├── intermediate/                # CSV đã chuẩn hóa để audit
│   └── processed/                   # event log, target và model dataset
├── pipeline/
│   ├── prepare_event_log.py
│   ├── audit_and_build_exam_target.py
│   ├── train_and_evaluate_model.py          # V1, đã khóa
│   ├── optimize_on_development.py           # V1 ablation, dùng lại làm hàm chung
│   ├── diagnose_and_calibrate.py            # V1 ablation, dùng lại làm hàm chung
│   ├── pipeline_config.py                   # V2/V3: config tập trung
│   ├── cohort.py                            # V3: quy tắc cohort (loại KB008, ca chờ >24h)
│   ├── data_split.py                        # V2: chia + khóa holdout
│   ├── feature_engineering.py               # V2: queue dynamics + patient type + lunch
│   ├── model_training.py                    # V2: candidate search, quantile, hiệu chỉnh theo nhóm
│   ├── lunch_warning.py                     # V2: cảnh báo giờ nghỉ trưa
│   ├── diagnostics.py                       # V2: segment/tail/drift/feature audit
│   ├── run_unified_pipeline.py              # V2: CLI hợp nhất (dev / final-eval / all)
│   └── legacy/                              # baseline cũ, không thuộc luồng chính
├── notebooks/
│   └── eda_and_build_model_dataset.ipynb
├── tests/
├── results/
│   ├── audit/
│   ├── eda/
│   ├── final_model/               # V1, đã khóa, không sửa
│   ├── development_experiments/   # V1 ablation
│   ├── model_diagnosis/           # V1 ablation
│   ├── unified_pipeline/          # V2 (đã đánh giá, giữ làm lịch sử), xem unified_pipeline.md
│   ├── unified_pipeline_v3/       # V3 (hiện hành), xem unified_pipeline_v3.md
│   └── archive/
├── docs/
│   └── sql/
├── logs/
├── requirements.txt
└── README.md
```

`data/`, các bảng EDA theo từng dòng, prediction Final Test và archive dữ liệu không được commit lên Git vì có thể lớn hoặc chứa dữ liệu HIS.

Hai metadata lịch sử `results/final_model/selected_model.json` và `results/eda/eda_summary.json` vẫn ghi đường dẫn tuyệt đối cũ tại thời điểm chúng được tạo. Đây chỉ là provenance của lần chạy đã khóa; code hiện tại không dùng các chuỗi đường dẫn đó làm default. Chúng được giữ nguyên byte để không làm thay đổi dấu vết kết quả.

## 3. Nguồn dữ liệu

Năm file nguồn nằm trong `data/raw/`:

| File | Grain chính | Số dòng hiện tại |
|---|---|---:|
| `01_encounter.csv` | một đợt khám | 167.518 |
| `02_reception.csv` | một lượt tiếp đón | 145.951 |
| `03_exam.csv` | một dịch vụ khám | 170.216 |
| `04_service_journey.csv` | một dịch vụ trong hành trình | 676.727 |
| `05_exam_status_log.csv` | một lần đổi trạng thái | 1.017.080 |

Các file có thể mang đuôi `.csv` nhưng thực tế là workbook XLSX. Pipeline nhận diện định dạng bằng bytes, sau đó dùng bản CSV đã chuẩn hóa trong `data/intermediate/` khi có sẵn. SQL nguồn và điều kiện cohort nằm tại [`his_source_queries.md`](his_source_queries.md).

## 4. Luồng xử lý chính thức

### 4.1. Chuẩn hóa event log

Script `pipeline/prepare_event_log.py`:

- đọc năm nguồn và chuẩn hóa datetime;
- kiểm tra khóa, thời gian âm và chất lượng join;
- chuyển các mốc HIS thành event thống nhất;
- chỉ loại các event trùng hoàn toàn, không gộp hai encounter khác nhau chỉ vì cùng timestamp.

Đầu ra:

| File | Vai trò |
|---|---|
| `data/processed/event_log.csv` | event log chuẩn hóa, hiện có 3.344.324 event |
| `results/audit/data_quality_report.csv` | kiểm tra chất lượng cơ bản |
| `results/audit/ingestion_metadata.json` | định dạng và encoding nguồn |

Lệnh chạy:

```powershell
.\.venv\Scripts\python.exe -m pipeline.prepare_event_log
```

### 4.2. Audit và tạo target khám

Script `pipeline/audit_and_build_exam_target.py` vừa audit đầy đủ năm nguồn, vừa tạo target theo quy tắc nghiệp vụ dưới đây.

Với mỗi `nb_dich_vu_id`:

1. Lấy status `60` đầu tiên làm `exam_start_time`.
2. Nếu mã dịch vụ là `KB008` hoặc phòng là `H401`, lấy status `20` cuối cùng xuất hiện trước status `60` làm `queue_entry_time`.
3. Các trường hợp còn lại lấy status `40` cuối cùng xuất hiện trước status `60` làm `queue_entry_time`.
4. Tính `waiting_minutes = exam_start_time - queue_entry_time`.
5. Target chỉ hợp lệ khi có đúng status hàng đợi bắt buộc trước status `60` và `waiting_minutes >= 0`.

Các ca dài hơn 2 giờ, 4 giờ, 24 giờ hoặc qua ngày được giữ nguyên và gắn cờ để audit; pipeline không tự động xóa outlier.

Đầu ra:

| File | Vai trò |
|---|---|
| `data/processed/exam_wait_target_all.csv` | 170.216 dịch vụ khám, gồm cả target không hợp lệ |
| `data/processed/exam_wait_target_valid.csv` | 141.519 target hợp lệ |
| `results/audit/HIS_data_audit_report.xlsx` | báo cáo audit nhiều sheet |
| `results/audit/target_build_summary.json` | tổng hợp machine-readable |

Lệnh chạy:

```powershell
.\.venv\Scripts\python.exe -m pipeline.audit_and_build_exam_target
```

### 4.3. EDA và tạo model dataset

Notebook `notebooks/eda_and_build_model_dataset.ipynb` đọc `exam_wait_target_all.csv`, thực hiện EDA và xuất:

- `data/processed/baseline_features.csv`;
- `data/processed/model_dataset.csv`;
- bảng, biểu đồ và `eda_summary.json` trong `results/eda/`.

Mở notebook bằng kernel của `.venv`, sau đó chạy toàn bộ cell từ đầu. Notebook tự nhận diện project root khi được mở từ thư mục gốc hoặc từ `notebooks/`.

### 4.4. Chọn và đánh giá mô hình chính thức

Script `pipeline/train_and_evaluate_model.py` sử dụng walk-forward validation trên development data, có purge các nhãn chưa xuất hiện tại đầu mỗi fold. Cấu hình chính thức là HistGradientBoosting với absolute loss và sliding window 42 ngày.

```powershell
.\.venv\Scripts\python.exe -m pipeline.train_and_evaluate_model --stage validate
```

Final Test đã được đánh giá và khóa trong `results/final_model/FINAL_TEST_EVALUATED.json`. Không chạy lại `--stage final-test`. Kết quả chính thức:

| Chỉ số | Kết quả Final Test |
|---|---:|
| Số dòng | 41.305 |
| MAE | 15,69 phút |
| Median AE | 6,54 phút |
| Sai số ≤10 phút | 67,52% |
| Sai số ≤20 phút | 88,47% |

Chi tiết chia tập, feature và metric theo status nằm tại [`final_model_evaluation.md`](final_model_evaluation.md).

### 4.5. Thử nghiệm tối ưu trên development

`pipeline/optimize_on_development.py` là nhánh nghiên cứu riêng, chỉ dùng dữ liệu trước 01/07/2026. Nhóm feature queue dynamics tăng tỷ lệ sai số ≤20 phút trung bình từ 88,23% lên 89,45% trên sáu development folds.

```powershell
.\.venv\Scripts\python.exe -m pipeline.optimize_on_development
```

Kết quả này là development ablation, không thay thế Final Test hoặc mô hình chính thức. Xem [`development_optimization.md`](development_optimization.md).

### 4.6. Chẩn đoán điểm yếu và hiệu chỉnh khoảng dự báo

`pipeline/diagnose_and_calibrate.py` chỉ dùng development data. Script chẩn đoán sai số theo phân đoạn, thử thêm feature thời lượng khám/bác sĩ/trạng thái ca đang khám, và hiệu chỉnh khoảng dự báo P50–P80 bằng split-conformal. Kết luận và cách trình bày mô hình nằm tại [`model_diagnosis_and_calibration.md`](model_diagnosis_and_calibration.md).

```powershell
.\.venv\Scripts\python.exe -m pipeline.diagnose_and_calibrate
```

### 4.7. Pipeline hợp nhất V3 (hiện hành)

`pipeline/run_unified_pipeline.py` hợp nhất mục 4.5 và 4.6 thành một luồng duy nhất chạy được bằng một lệnh: cohort → feature engineering → huấn luyện/tối ưu siêu tham số (walk-forward CV theo thời gian) → chẩn đoán lỗi → hiệu chỉnh khoảng dự báo theo nhóm → cảnh báo "Chờ quá giờ nghỉ trưa" → đánh giá cuối trên holdout → huấn luyện lại trên dữ liệu mới nhất. Toàn bộ dữ liệu (development cũ + Final Test cũ) được gộp lại và chia lại theo thời gian; holdout bị khóa ngay sau khi chia (`results/unified_pipeline_v3/holdout_locked.json`, kèm quy tắc cohort) và chỉ được đánh giá đúng một lần (`HOLDOUT_EVALUATED.json`), cùng cơ chế khóa với V1.

**Cohort mô hình** (`pipeline/cohort.py`, áp dụng sau khi đọc `model_dataset.csv` và trước feature engineering; các file audit giữ nguyên đầy đủ): loại dịch vụ **KB008** (khám nội, phòng H401/E1108 — dịch vụ phụ thuộc, được chỉ định trong lúc khám dịch vụ khác nên mốc status 20 không phải mốc bắt đầu chờ) và các ca chờ **>24 giờ** (bệnh nhân bỏ về, quay lại ngày sau). Bảng hao hụt nằm tại `results/unified_pipeline_v3/cohort_attrition.csv`.

```powershell
.\.venv\Scripts\python.exe -m pipeline.run_unified_pipeline --stage dev           # có thể chạy lại nhiều lần
.\.venv\Scripts\python.exe -m pipeline.run_unified_pipeline --stage final-eval    # chỉ chạy đúng một lần
.\.venv\Scripts\python.exe -m pipeline.run_unified_pipeline --stage refit-final   # sau final-eval: mô hình mới nhất để triển khai
# hoặc chạy cả ba bằng một lệnh cho lần tái lập đầu tiên:
.\.venv\Scripts\python.exe -m pipeline.run_unified_pipeline --stage all
```

Tái lập V2 bằng cùng mã: `--output-dir results/unified_pipeline --exclude-service-codes --max-wait-minutes 0 --holdout-start auto`.

Chi tiết thiết kế, kết quả, so sánh V1/V2/V3 và điểm yếu đã sửa/còn tồn đọng nằm tại [`unified_pipeline_v3.md`](unified_pipeline_v3.md). `pipeline/optimize_on_development.py` và `pipeline/diagnose_and_calibrate.py` được giữ nguyên vì vẫn là nơi lưu các thử nghiệm ablation gốc và được import lại làm hàm dùng chung; luồng chạy chuẩn khi cần tái lập hoặc mở rộng là mục 4.7, không phải 4.5/4.6.

## 5. Thứ tự chạy khi tái tạo từ đầu

```text
data/raw
   ↓
pipeline.prepare_event_log
   ↓
data/intermediate + data/processed/event_log.csv + results/audit
   ↓
pipeline.audit_and_build_exam_target
   ↓
data/processed/exam_wait_target_*.csv
   ↓
notebooks/eda_and_build_model_dataset.ipynb
   ↓
data/processed/model_dataset.csv + results/eda
   ↓
pipeline.train_and_evaluate_model --stage validate   (V1, lịch sử)
   hoặc
pipeline.run_unified_pipeline --stage dev            (V3, khuyến nghị)
```

Chỉ tái tạo validation khi cần kiểm tra pipeline. Không tái đánh giá Final Test V1 đã khóa, và không chạy `run_unified_pipeline --stage final-eval` nhiều hơn một lần trên cùng một lần chia holdout (V2 và V3 đã đánh giá xong).

## 6. Kiểm thử

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

Test hiện tại bảo vệ các điều kiện quan trọng:

- event log không gộp nhầm hai encounter khác nhau có cùng timestamp;
- feature tải hàng đợi chỉ sử dụng event xảy ra trước thời điểm dự báo, kể cả trường hợp timestamp bằng nhau;
- feature thời lượng khám và trạng thái ca đang khám cũng chỉ dùng event trước thời điểm dự báo;
- nhãn "chờ quá giờ nghỉ trưa" và feature nhân quả theo phòng/khoa chỉ dùng lịch sử đã biết trước `queue_entry_time` (`tests/test_feature_engineering.py`);
- quy tắc cohort (loại KB008, ca chờ >24 giờ) và bảng hao hụt (`tests/test_cohort.py`);
- holdout bị khóa ngay sau khi chia, không đổi qua các lần chạy lại `--stage dev`, và bị từ chối nếu quy tắc cohort hay mốc holdout đổi sau khi khóa (`tests/test_data_split.py`);
- hiệu chỉnh theo nhóm rơi về hệ số chung khi nhóm quá ít dòng, và cửa sổ huấn luyện cuối luôn tách rời cửa sổ hiệu chỉnh (`tests/test_model_training.py`, `tests/test_lunch_warning.py`).

## 7. Nhánh legacy

`pipeline/legacy/` giữ lại baseline cũ dựa trên `service_ordered → service_received` để tái lập lịch sử nghiên cứu. Nhánh này không tạo target khám chính thức và không nằm trong lệnh chạy chuẩn.

Artifact target V1 cũ được lưu tại `results/archive/legacy_target_v1/`. Không dùng các file này để huấn luyện hoặc báo cáo kết quả chính thức.

## 8. Truy vấn dự báo trực tiếp tùy chọn

[`sql/queue_snapshot_for_prediction.sql`](sql/queue_snapshot_for_prediction.sql) lấy snapshot các ca vừa vào hàng đợi để phục vụ tích hợp dự báo. Đây là input vận hành tùy chọn, không phải một tập holdout hoặc bước đánh giá bổ sung.
