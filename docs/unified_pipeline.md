# Pipeline hợp nhất V2: tối ưu, chẩn đoán, hiệu chỉnh và cảnh báo giờ nghỉ trưa

> **Tài liệu V2, giữ nguyên làm lịch sử.** Pipeline hiện hành là V3 (loại dịch vụ khám nội KB008 và ca chờ >24 giờ khỏi cohort): xem [`unified_pipeline_v3.md`](unified_pipeline_v3.md). Holdout V2 đã được đánh giá đúng một lần ngày 23/09/2026 (`results/unified_pipeline/HOLDOUT_EVALUATED.json`); kết quả dưới đây không được dùng để chọn lại feature, mô hình hay hyperparameter.
>
> **Đính chính:** holdout V2 ghi là "21 ngày" (`--holdout-days 21` tính lùi từ dòng cuối 04/08), nhưng dữ liệu thực tế kết thúc ngày 31/07 (ba dòng tháng 8 đều là KB008 xuất hiện lẻ tẻ). Holdout V2 vì vậy chỉ có **17 ngày lịch có dữ liệu (15/07–31/07)**. Kết quả và ranh giới không đổi, chỉ mô tả độ dài được sửa lại.

Tài liệu này mô tả pipeline hợp nhất `pipeline/run_unified_pipeline.py`, thay thế việc chạy tách rời `pipeline/optimize_on_development.py` và `pipeline/diagnose_and_calibrate.py`. Nó dùng kết quả audit và chẩn đoán hiện có (`docs/model_diagnosis_and_calibration.md`, `docs/development_optimization.md`) làm điểm xuất phát thay vì suy luận lại từ đầu.

Pipeline V1 (`pipeline/train_and_evaluate_model.py`, kết quả tại `results/final_model/`) được giữ nguyên làm dấu vết lịch sử. Không sửa, không chạy lại `--stage final-test` của V1.

## 1. Vì sao cần tái cấu trúc

Hai điểm rời rạc trong luồng cũ:

1. `optimize_on_development.py` và `diagnose_and_calibrate.py` là hai script riêng, không chia sẻ config, không lưu artifact có thể nạp lại để suy luận (inference) trên dữ liệu mới.
2. Final Test V1 (01/07–04/08/2026) đã được đánh giá và các số liệu tổng hợp của nó đã xuất hiện trong `docs/final_model_evaluation.md`. Theo đúng khuyến nghị ở cuối `docs/model_diagnosis_and_calibration.md` (mục 7.5): *"chỉ khi cấu hình mới được cố định trên development mới có thể quyết định dùng holdout mới, vì Final Test hiện tại đã được sử dụng một lần và không còn là dữ liệu chưa thấy"*. Vì vậy V2 không tinh chỉnh gì trên Final Test V1, mà gộp toàn bộ dữ liệu (development cũ + Final Test cũ) và chia lại một holdout hoàn toàn mới ở cuối trục thời gian.

## 2. Chia lại dữ liệu theo thời gian

`pipeline/data_split.py`:

- Gộp toàn bộ `data/processed/model_dataset.csv` (141.519 dòng, 01/04–04/08/2026), không phân biệt development/Final Test cũ.
- Holdout mới = `--holdout-days` ngày cuối cùng của toàn bộ dữ liệu (mặc định 21 ngày). Không xáo trộn ngẫu nhiên — chia thuần theo `queue_entry_time` để tránh rò rỉ thông tin tương lai.
- Ngay sau khi chia, ranh giới holdout được ghi vào `results/unified_pipeline/holdout_locked.json` (mốc thời gian, số dòng, hash SHA-256 của input) **trước khi** bất kỳ bước huấn luyện nào chạy. Các lần chạy `--stage dev` sau đó đọc lại đúng ranh giới đã khóa thay vì tính lại, miễn là input không đổi.
- Phần trước holdout (pre-holdout) được chia tiếp thành sáu validation fold trượt (walk-forward, mỗi fold 7 ngày, train sliding 42 ngày, purge nhãn chưa xuất hiện tại đầu fold) — tái sử dụng nguyên hàm `make_folds` đã có test của V1, không viết lại logic purge.
- Holdout chỉ được đọc đúng một lần, ở bước `--stage final-eval`, và bị khóa lại bằng `HOLDOUT_EVALUATED.json` ngay sau đó để không thể vô tình đánh giá lần hai.

## 3. Luồng xử lý hợp nhất

```text
model_dataset.csv
   |
engineer_features()          # queue dynamics (tái dùng V1 ablation) + patient_payment_type + lunch features
   |
split_and_lock_holdout()     # khóa holdout ngay sau khi chia
   |
build_validation_folds()     # 6 fold trượt, sliding 42 ngày
   |
run_walk_forward_search()    # 9 candidate x 6 fold, chọn theo mean MAE + 0.25*std MAE
   |
diagnosis                    # segment/tail/drift trên out-of-fold predictions, trước (official) và sau (candidate chọn)
   |
lunch classifier + threshold # out-of-fold trên 6 fold, chọn ngưỡng theo F-beta trên validation
   |
final retrain + calibration  # fit trên (holdout_start - sliding_train_days .. holdout_start - calibration_days)
   |                         # hiệu chỉnh P50/P80 theo nhóm khoa trên (holdout_start - calibration_days .. holdout_start)
unified_model.joblib + dev_manifest.json
   |
--stage final-eval           # áp dụng đúng một lần lên holdout, khóa HOLDOUT_EVALUATED.json
```

Mỗi bước là một hàm thuần trong một module riêng (`feature_engineering.py`, `model_training.py`, `lunch_warning.py`, `diagnostics.py`), nhận `PipelineConfig` làm tham số, không đọc biến toàn cục ẩn. Random seed cố định (`--random-state`, mặc định 42) áp dụng cho mọi mô hình HistGradientBoosting. Toàn bộ artifact (fold metrics, candidate summary, calibration offsets, model đã huấn luyện, cảnh báo giờ trưa) được ghi ra `results/unified_pipeline/` để chạy lại tái lập bằng đúng hai lệnh:

```powershell
.\.venv\Scripts\python.exe -m pipeline.run_unified_pipeline --stage dev
.\.venv\Scripts\python.exe -m pipeline.run_unified_pipeline --stage final-eval
```

## 4. Feature engineering

Ba nhóm, đều chỉ dùng thông tin có trước `queue_entry_time`:

1. **Queue dynamics** (tái dùng nguyên từ `optimize_on_development.py`, đã kiểm chứng cải thiện MAE trong `docs/development_optimization.md`): số lượt vào hàng đợi/bắt đầu khám 15-60 phút trước, số ca đang chờ cùng phòng/khoa.
2. **Loại bệnh nhân** (`patient_payment_type`, mới): BHYT hay dịch vụ, suy từ `doi_tuong` tại `01_encounter.csv`. Đây là thuộc tính đợt điều trị, cố định từ lúc nhập viện — luôn có trước mọi lượt vào hàng đợi khám nên an toàn để dùng làm feature, không chỉ để chẩn đoán phân nhóm.
3. **Lunch features** (mới, xem mục 5): khoảng cách đến giờ nghỉ trưa, và tỷ lệ lịch sử vượt giờ trưa theo phòng/khoa (nhân quả — chỉ dùng ca đã biết kết quả trước thời điểm dự báo, cửa sổ trượt 42 ngày, cùng cơ chế với các feature lịch sử khác đã có).

Feature thời lượng khám/bác sĩ/trạng thái ca đang khám (roster, in-exam, flow) **không** được đưa vào tập mặc định — `docs/model_diagnosis_and_calibration.md` đã kiểm chứng đầy đủ (ablation nhiều nấc) rằng chúng không cải thiện MAE ngoài nhiễu giữa các fold. `pipeline/diagnose_and_calibrate.py` được giữ nguyên làm nơi lưu thử nghiệm đó.

## 5. Cảnh báo "Chờ quá giờ nghỉ trưa"

### 5.1. Vấn đề

`docs/model_diagnosis_and_calibration.md` (W5) đã ghi nhận: 46% ca chờ trên 120 phút vào hàng đợi lúc 05–07h, 34% bắt đầu khám lúc 13–15h — đây là các ca đăng ký sớm buổi sáng nhưng bị đẩy sang buổi chiều vì giờ nghỉ trưa, không phải do hàng đợi đông. Mô hình chính thức không có tín hiệu nào để nhận biết hiện tượng này.

Số liệu đo lại trên toàn bộ dữ liệu hiện tại xác nhận phát hiện trên: trong số các ca vào hàng đợi trước 11:30 (80,8% tổng số ca), 1,3% thực sự bị đẩy qua giờ nghỉ trưa (bắt đầu khám lúc/sau 11:30, kể cả trường hợp qua ngày hôm sau). Nhóm này có thời gian chờ trung bình 556 phút (trung vị 103 phút) so với 23,6 phút của nhóm không bị đẩy, và chiếm 36,8% tổng số ca có thời gian chờ trên 120 phút — gần như đồng nhất với hiện tượng "đuôi chờ cực đoan" (W4). Phòng `H401` (16,7% ca sáng bị đẩy) và `PK-37` (9,3%) là hai điểm nóng, đúng như hai phòng bị nêu tên trong `docs/model_diagnosis_and_calibration.md`.

### 5.2. Vì sao dùng classifier riêng thay vì suy ra từ quantile

Mô hình hồi quy tối ưu MAE (kể cả sau khi thêm feature giờ trưa) hầu như không cải thiện MAE trên chính nhóm bị đẩy qua trưa (xem bảng ở mục 8) — điều này *đúng như kỳ vọng*: một mô hình point-forecast tối ưu MAE sẽ luôn dự báo gần trung vị của toàn bộ phân phối, không thể và không nên dự báo 400-500 phút cho một nhóm mà đa phần thành viên chỉ chờ 20-100 phút, nếu không sẽ làm hỏng sai số của các ca thông thường. Vì vậy pipeline dùng một **classifier nhị phân riêng** ước lượng P(bị đẩy qua giờ trưa), tách hoàn toàn khỏi điểm dự báo thời gian chờ — đúng theo hướng "mô hình phân loại riêng" mà yêu cầu đề ra, thay vì suy ra P(vượt giờ trưa) từ khoảng quantile P50–P80 (khoảng đó quá hẹp so với biên độ thực tế của hiện tượng này nên sẽ ước lượng xác suất rất kém).

### 5.3. Nhãn và điều kiện

- `registered_before_lunch`: `queue_entry_time` có giờ trong ngày < `lunch_start` (mặc định 11:30).
- `crosses_lunch` (nhãn lịch sử, chỉ dùng để huấn luyện/đánh giá classifier, không bao giờ là feature): đúng khi `registered_before_lunch` **và** thời điểm bắt đầu khám thực tế (`queue_entry_time + waiting_minutes`) rơi vào lúc/sau `lunch_start`, kể cả khi qua ngày hôm sau.
- Nhãn dùng `lunch_start` (không phải `lunch_end`) làm ranh giới vì đây là thời điểm phòng khám ngừng gọi số; nếu bệnh nhân chưa được gọi trước mốc này thì trên thực tế họ sẽ phải chờ đến `lunch_end`.

### 5.4. Feature và huấn luyện

Classifier `HistGradientBoostingClassifier` dùng đúng tập feature `queue_dynamics_lunch` (mục 4), chỉ huấn luyện/dự báo trên các dòng `registered_before_lunch = True`. Ngưỡng cảnh báo được chọn trên **out-of-fold validation** (dự báo của mỗi fold trên đúng phần validation của fold đó, không đụng vào holdout), tối đa hóa F-beta (mặc định beta=1, cân bằng báo nhầm và bỏ sót; có thể tăng qua `--lunch-threshold-fbeta` nếu muốn ưu tiên giảm bỏ sót).

### 5.5. Output cho mỗi bệnh nhân

`pipeline/lunch_warning.py::build_patient_output` trả về, cho mỗi dòng:

| Cột | Ý nghĩa |
|---|---|
| `predicted_wait_minutes` | Thời gian chờ dự báo, đã hiệu chỉnh (P50 calibrated) |
| `predicted_wait_p80_minutes` | Cận trên khoảng dự báo, đã hiệu chỉnh (P80 calibrated) |
| `probability_cross_lunch` | P(bị đẩy qua giờ trưa); `NaN` nếu đăng ký buổi chiều (không áp dụng) |
| `recommendation` | "Chờ tiếp" hoặc "Chờ quá giờ nghỉ trưa / Đề xuất quay lại vào buổi chiều" |
| `suggested_return_time` | Chỉ có khi cảnh báo bật: giờ khám dự báo, nhưng không bao giờ sớm hơn `lunch_end` |

`suggested_return_time` lấy `max(giờ khám dự báo, lunch_end)` — dùng P50 (không phải P80) làm cơ sở, vì hướng lệch an toàn hơn ở đây là khuyên bệnh nhân quay lại hơi sớm (chờ thêm một chút tại viện) thay vì hơi trễ (rủi ro bỏ lỡ lượt gọi số).

## 6. Hiệu chỉnh khoảng dự báo theo nhóm

`docs/model_diagnosis_and_calibration.md` ghi nhận độ phủ P80 lệch nhau nhiều giữa các phòng sau khi hiệu chỉnh bằng một hệ số chung (ví dụ PK-37 chỉ 72,7%). V2 hiệu chỉnh split-conformal **theo từng khoa** (`--calibration-group-column`, mặc định `ma_khoa_thuc_hien`) thay vì một hệ số toàn cục: nhóm có đủ số dòng hiệu chỉnh (`--calibration-min-group-rows`, mặc định 40) được tính hệ số riêng; nhóm quá ít dòng rơi về hệ số chung. Cửa sổ hiệu chỉnh là `--calibration-days` ngày cuối (mặc định 7) của cửa sổ huấn luyện cuối cùng, tách biệt hoàn toàn khỏi dữ liệu fit mô hình điểm — không hiệu chỉnh trên holdout.

## 7. Kết quả chọn mô hình (validation, out-of-fold)

Holdout mới: từ `2026-07-15 00:00:00`, 23.450 dòng, thực chất 17 ngày lịch có dữ liệu (15/07–31/07; `--holdout-days 21` tính lùi từ dòng cuối 04/08 nhưng dữ liệu kết thúc 31/07). Phần trước holdout: 118.069 dòng, chia thành 6 fold validation trượt (sliding 42 ngày) dùng để chọn mô hình.

| Hạng | Candidate | Cửa sổ | Mean MAE | Std MAE | Mean ≤20 phút | Selection score |
|---:|---|---|---:|---:|---:|---:|
| 1 | **queue_dynamics_lunch_deeper** | sliding | **15,32** | 2,64 | **89,81%** | **15,98** |
| 2 | queue_dynamics_deeper | sliding | 15,42 | 2,67 | 89,67% | 16,09 |
| 3 | queue_dynamics_lunch_deeper | expanding | 15,46 | 2,61 | 89,76% | 16,11 |
| 4 | queue_dynamics_patient_type_deeper | sliding | 15,44 | 2,71 | 89,62% | 16,12 |
| 5 | queue_dynamics_lunch_current | sliding | 15,47 | 2,66 | 89,59% | 16,14 |
| 8 | official_deeper | sliding | 16,12 | 2,66 | 88,19% | 16,78 |
| 9 | official_current | sliding | 16,14 | 2,69 | 88,21% | 16,81 |
| 10 | median_baseline | sliding | 20,95 | 2,78 | 83,34% | 21,65 |

(Bảng đầy đủ 10 dòng x 2 cửa sổ tại `results/unified_pipeline/candidate_summary.csv`.)

Candidate được chọn: **`queue_dynamics_lunch_deeper`** — feature set `queue_dynamics_lunch` (official + queue dynamics + `patient_payment_type` + `minutes_to_lunch_start` + tỷ lệ vượt giờ trưa theo phòng/khoa), hyperparameter "deeper" (`learning_rate=0,06`, `max_iter=180`, `max_leaf_nodes=63`, `min_samples_leaf=30`). Cửa sổ sliding 42 ngày tiếp tục thắng expanding (15,32 so với 15,46), xác nhận lại lựa chọn đã có từ V1. Xu hướng MAE giảm dần qua các fold cho cả trước/sau (`drift_mae_slope_per_fold`: −0,686 và −0,672 phút/fold) — không có dấu hiệu drift xấu đi.

## 8. Điểm yếu: bằng chứng, đã sửa, còn tồn đọng

Phân tích lỗi theo phân nhóm dùng out-of-fold validation predictions (`segment_diagnostics_before.csv` = feature chính thức V1, `segment_diagnostics_after.csv` = candidate được chọn); số liệu holdout tương ứng nằm ở mục 9.

### 8.1. Đã sửa (có bằng chứng trước/sau)

| # | Điểm yếu | Bằng chứng trước | Sau khi sửa | Cách sửa |
|---|---|---|---|---|
| W1 | Mô hình chính thức không có tín hiệu tải hàng đợi | official MAE 16,12–16,14 | queue_dynamics_lunch MAE 15,32 (−0,8 phút, −5%) | Đưa queue dynamics (đã kiểm chứng ở `optimize_on_development.py`) vào tập feature mặc định của pipeline hợp nhất, không còn là nhánh nghiên cứu tách rời |
| W5 | Ca đăng ký sớm buổi sáng bị đẩy qua giờ nghỉ trưa, mô hình không thấy | Trên toàn bộ dữ liệu: 1,3% ca vào hàng đợi trước 11:30 bị đẩy qua trưa, chờ trung bình 556 phút, chiếm 36,8% tổng số ca chờ >120 phút; phòng H401 (16,7%) và PK-37 (9,3%) là điểm nóng | Classifier riêng đạt precision 0,44 / recall 0,79 / F1 0,56 trên **holdout thật** (xem mục 9) — bắt được khoảng 4/5 ca thực sự bị đẩy qua trưa | Feature `minutes_to_lunch_start` + tỷ lệ lịch sử vượt giờ trưa theo phòng/khoa (nhân quả) đưa vào classifier riêng; xem mục 5 |
| W9 | Không có khoảng dự báo, chưa hiệu chỉnh | Coverage P80 thô trên holdout: 77,6% (nên thấp hơn mục tiêu 80%) | Coverage P80 sau hiệu chỉnh theo khoa: 81,3% trên holdout | Split-conformal theo khoa (mục 6), là bước thường trực của pipeline thay vì một thử nghiệm ablation riêng |
| — | Đánh giá công bằng mô hình cũ/mới | V1 đóng băng đánh giá trên chính holdout mới: MAE 16,25 | V2 trên cùng holdout: MAE 15,18 (−1,07 phút, −6,6%); ≤20 phút 88,5% → 89,8% | Chấm lại V1 (không sửa V1) trên đúng 23.450 dòng holdout mới để so sánh ngang hàng — xem mục 10 |

### 8.2. Đã kiểm chứng lại nhưng không có tác dụng (giữ nguyên kết luận cũ)

- **Thời lượng khám/bác sĩ/trạng thái ca đang khám** (`docs/model_diagnosis_and_calibration.md`): không lặp lại ablation đầy đủ trong V2 vì đã được kiểm chứng kỹ; giữ ngoài tập feature mặc định.
- **Thêm `patient_payment_type` vào mô hình hồi quy**: cải thiện rất nhỏ và không ổn định (candidate `queue_dynamics_patient_type_deeper` xếp hạng 4, MAE 15,44 — kém hơn `queue_dynamics_lunch_deeper` và xấp xỉ `queue_dynamics_deeper`). Vẫn giữ trong tập feature vì có cơ sở nhân quả và rẻ để tính, nhưng không phải nguồn cải thiện chính.
- **Thêm feature giờ trưa vào chính mô hình hồi quy điểm dự báo**: hầu như không cải thiện MAE trên chính nhóm bị đẩy qua trưa (out-of-fold validation: 429,8 → 427,3 phút; holdout: mô hình cũ 503,3 → mô hình mới 482,6 phút, chỉ giảm 4,1%). Đây là kỳ vọng đúng, không phải thất bại: một mô hình tối ưu MAE không nên dự báo 500 phút cho một nhóm mà phần lớn chỉ chờ 20–100 phút vì sẽ làm hỏng sai số ca thông thường. Đây chính là lý do cần classifier riêng (mục 5.2) thay vì kỳ vọng con số dự báo tự sửa vấn đề này. *(Đính chính ở V3: mức "hầu như không cải thiện" chủ yếu do các ca KB008 và ca chờ nhiều ngày chi phối nhóm này. Trên cohort sạch, cùng so sánh cho thấy MAE nhóm vượt giờ trưa giảm 15–27%; xem [`unified_pipeline_v3.md`](unified_pipeline_v3.md) mục 6. Classifier riêng vẫn cần thiết.)*

### 8.3. Phát hiện mới, chưa đủ bằng chứng để kết luận

- **Loại bệnh nhân (BHYT/dịch vụ)**: trên validation, nhóm BHYT có MAE cao hơn rõ rệt (20,4 so với 15,0 phút, within_20m 80,9% so với 90,3%); nhưng trên holdout mối quan hệ **đảo ngược** (BHYT MAE 14,3 tốt hơn dịch vụ 15,2). Cỡ mẫu BHYT nhỏ (2.476 dòng validation, 1.001 dòng holdout) nên chênh lệch này có thể do nhiễu ngẫu nhiên, không phải hiệu ứng thật. Cần thêm dữ liệu hoặc kiểm định thống kê trước khi kết luận.

### 8.4. Còn tồn đọng (chưa sửa trong lần này)

| # | Điểm yếu | Bằng chứng (holdout) | Vì sao chưa sửa |
|---|---|---|---|
| W-status20 | Nhánh status 20 (KB008/H401) kém hơn hẳn nhánh status 40 | MAE 230,4 so với 11,7 phút; ≤20 phút chỉ 57,1% so với 90,4% (368 dòng) | Cần mô hình/feature riêng cho nhánh này — đã ghi nhận từ V1 (`final_model_evaluation.md`), ngoài phạm vi lần sửa này. *(V3: nhánh này bị loại khỏi cohort vì KB008 là dịch vụ phụ thuộc, mốc status 20 không phải mốc bắt đầu chờ; không còn là điểm yếu cần sửa.)* |
| W-tail | Đuôi chờ cực đoan (>120 phút) vẫn sai số rất lớn | 1,02% số dòng, MAE 608 phút, chiếm 41% tổng sai số tuyệt đối, coverage P80 chỉ 25,8% cho riêng nhóm này | Đặc điểm cố hữu của mô hình tối ưu MAE trên phân phối lệch phải cực mạnh; cảnh báo giờ trưa xử lý được một phần nguyên nhân (ca đăng ký sớm), nhưng không xử lý được toàn bộ đuôi (ví dụ ca chờ lâu vì lý do khác). *(V3: phần lớn đuôi này là KB008 và ca chờ nhiều ngày; sau khi loại, đuôi >120 phút chỉ còn 7,7% tổng sai số trên holdout.)* |
| W7 | Không quan sát được bác sĩ phục vụ ca đang chờ | Không đo lại trong V2; xem `model_diagnosis_and_calibration.md` W6/W7 | Cần dữ liệu thời điểm gán bác sĩ, ngoài phạm vi 5 file nguồn hiện có |
| W8 | Ca thiếu status 40 bị loại khỏi mô hình | Không đo lại trong V2; theo audit gốc: 25.787/170.216 dịch vụ | Cần quyết định nghiệp vụ, ngoài phạm vi kỹ thuật |
| W10 | Chưa tách được hiệu ứng "dữ liệu gần đây tốt hơn" khỏi hiệu ứng "ít dữ liệu hơn" | Không đo lại trong V2 | Cần thử nghiệm riêng nhiều độ dài cửa sổ train, chưa thực hiện trong lần này |

## 9. Kết quả holdout (đánh giá cuối, một lần)

Holdout: 23.450 dòng, `2026-07-15 00:00:00` đến 31/07/2026 (dòng cuối cùng theo thời gian là 04/08 10:19, nhưng ba dòng tháng 8 đều là KB008 lẻ tẻ; 17 ngày lịch có dữ liệu).

| Chỉ số | Raw | Calibrated theo khoa |
|---|---:|---:|
| MAE | 15,18 | 15,18 |
| RMSE | 322,55 | 322,54 |
| Median AE | 5,99 | 6,01 |
| P90 AE | 20,27 | 20,15 |
| Bias | −7,66 | −7,46 |
| Sai số ≤10 phút | 70,68% | 70,72% |
| Sai số ≤20 phút | 89,76% | 89,83% |
| Coverage P50 | 50,48% | 51,47% |
| Coverage P80 | **77,56%** | **81,29%** |

Hiệu chỉnh theo khoa gần như không đổi MAE (đúng như thiết kế — chỉ sửa độ phủ) nhưng đưa coverage P80 từ 77,6% lên 81,3%, sát mục tiêu 80% hơn hẳn so với hiệu chỉnh bằng một hệ số chung của V1 (P80 thô 78,5%, xem `docs/model_diagnosis_and_calibration.md`).

**Đuôi chờ cực đoan** (>120 phút, 240/23.450 dòng = 1,02%): MAE 608,17 phút, bias −597,51, chiếm 41,0% tổng sai số tuyệt đối toàn holdout — xác nhận lại đúng quy mô đã thấy ở V1 (1,3–1,4% số dòng, 45–47% tổng sai số).

**Cảnh báo "Chờ quá giờ nghỉ trưa" trên holdout** (ngưỡng 0,2371 chọn từ validation, không đổi khi áp lên holdout):

| Chỉ số | Giá trị |
|---|---:|
| Số ca đăng ký buổi sáng trong holdout | 18.989 |
| Số ca thực sự bị đẩy qua trưa | 159 (0,84%) |
| Số ca được cảnh báo | 288 |
| Precision | 0,4375 |
| Recall | 0,7925 |
| F1 | 0,5638 |

Recall 79% nghĩa là pipeline bắt được khoảng 4/5 ca thực sự bị đẩy qua giờ trưa trên dữ liệu hoàn toàn chưa từng thấy. Precision giảm so với validation (0,69 → 0,44) trong khi recall tăng (0,70 → 0,79) — biến động tự nhiên của một sự kiện hiếm (526 ca dương trên validation, chỉ 159 ca trên holdout), không phải dấu hiệu rò rỉ hay lỗi thiết kế: ngưỡng được chọn cố định từ validation và không được chỉnh lại sau khi thấy holdout. Nếu muốn ưu tiên giảm báo nhầm hơn, tăng `--lunch-threshold-fbeta` xuống dưới 1 sẽ đánh đổi theo hướng ngược lại (xem mục 11).

## 10. So sánh mô hình cũ (V1) và mới (V2)

So sánh công bằng: mô hình V1 đã đóng băng (`results/final_model/final_model.joblib`, không huấn luyện lại) được chấm điểm trên **đúng cùng 23.450 dòng holdout mới** như V2, ngoài ra vẫn giữ số liệu V1 trên Final Test gốc của nó để tham khảo lịch sử.

| Mô hình | Đánh giá trên | Rows | MAE | RMSE | P90 AE | Bias | ≤10 phút | ≤20 phút |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| V1 (đóng băng) | Final Test gốc (01/07–04/08/2026) | 41.305 | 15,69 | 319,66 | 21,80 | −6,75 | 67,52% | 88,47% |
| **V1 (đóng băng)** | **Holdout mới (15/07–31/07/2026)** | 23.450 | **16,25** | 322,66 | 21,86 | −6,67 | 67,72% | 88,53% |
| **V2 raw** | **Holdout mới** | 23.450 | **15,18** | 322,55 | 20,27 | −7,66 | 70,68% | 89,76% |
| **V2 calibrated** | **Holdout mới** | 23.450 | **15,18** | 322,54 | 20,15 | −7,46 | 70,72% | 89,83% |

So trên cùng một holdout (hai dòng in đậm ở giữa): **MAE giảm 16,25 → 15,18 phút (−1,07 phút, −6,6%)**, P90 AE giảm 21,86 → 20,15 phút (−7,8%), sai số ≤10 phút tăng 67,7% → 70,7% (+3,0 điểm), sai số ≤20 phút tăng 88,5% → 89,8% (+1,3 điểm). Trên nhóm bị đẩy qua giờ trưa riêng (159 dòng trong holdout): MAE giảm 503,3 → 482,6 phút (−4,1%); trên đuôi >120 phút (240 dòng): MAE giảm 626,8 → 608,2 phút (−3,0%); trên phần còn lại (23.210 dòng, "ca thông thường"): MAE giảm 9,94 → 9,05 phút (−9,0%) — cải thiện tập trung chủ yếu ở ca thông thường, đúng như phân tích ở mục 8.2.

RMSE gần như không đổi vì bị chi phối hoàn toàn bởi vài trăm ca chờ cực đoan (giống nhận định đã có ở V1); MAE, sai số ≤10/≤20 phút và P90 AE là các chỉ số phản ánh đúng hơn cải thiện thực tế cho phần lớn bệnh nhân.

## 11. Giả định cần điều chỉnh

1. **Giờ nghỉ trưa mặc định [11:30–13:00]** (`--lunch-start`, `--lunch-end`): lấy theo mô tả của người dùng, chưa xác nhận với lịch vận hành thực tế của từng phòng/khoa. Nếu một số khoa nghỉ trưa giờ khác, nên truyền `--lunch-start`/`--lunch-end` riêng hoặc mở rộng pipeline để cấu hình theo khoa.
2. **Ranh giới nhãn `crosses_lunch` dùng `lunch_start`, không phải `lunch_end`**: giả định phòng khám ngừng gọi số tại `lunch_start` và không khám trong suốt giờ nghỉ. Nếu một số phòng vẫn khám xuyên trưa, nhãn này sẽ đánh giá quá tay cho phòng đó.
3. **Ngưỡng cảnh báo chọn theo F1 (beta=1)**: cân bằng báo nhầm và bỏ sót theo mặc định toán học, chưa phản ánh chi phí vận hành thực tế (ví dụ bỏ sót một ca chờ 5 tiếng có lẽ tệ hơn nhiều một lần báo nhầm). Điều chỉnh qua `--lunch-threshold-fbeta` (>1 để ưu tiên giảm bỏ sót).
4. **Holdout mới** (`--holdout-days 21`, thực chất 17 ngày lịch có dữ liệu vì dữ liệu kết thúc 31/07): chọn để vừa đủ lớn cho ước lượng ổn định vừa để lại đủ dữ liệu cho 6 fold validation x 7 ngày + 42 ngày train trượt. V3 cố định thẳng mốc `2026-07-15` bằng `--holdout-start`.
5. **`patient_payment_type` suy từ `doi_tuong`** (1 = dịch vụ, 2 = BHYT và các biến thể): suy luận từ tỷ lệ khớp với `ten_loai_doi_tuong`, chưa được xác nhận trực tiếp với đội nghiệp vụ HIS.
6. **`thoi_gian_hen_kham` (giờ hẹn khám) chưa được dùng làm feature** vì chưa kiểm chứng được nó luôn được ghi nhận trước `queue_entry_time` cho mọi dòng — ghi nhận là hướng nghiên cứu thêm, không phải giả định đang áp dụng.

## 12. Artifact

| File | Nội dung |
|---|---|
| `holdout_locked.json` | Ranh giới holdout, khóa ngay sau khi chia |
| `fold_boundaries.csv` | Mốc thời gian 6 fold validation trượt |
| `candidate_fold_metrics.csv` / `candidate_summary.csv` | Kết quả và xếp hạng 9 candidate x cửa sổ |
| `segment_diagnostics_before.csv` / `_after.csv` | Sai số theo phân đoạn, trước/sau khi áp dụng candidate chọn |
| `tail_and_lunch_before_after.json` | So sánh MAE/bias trên đuôi chờ dài và nhóm vượt giờ trưa |
| `feature_audit.csv` | Bảng feature mở rộng, gồm feature mới và feature đã thử nhưng loại |
| `lunch_threshold_and_validation_report.json` | Ngưỡng cảnh báo và precision/recall/F1 trên validation |
| `calibration_offsets.json` | Hệ số hiệu chỉnh P50/P80 theo khoa (và hệ số chung) |
| `unified_model.joblib` | Bundle triển khai: model P50/P80, hệ số hiệu chỉnh, classifier giờ trưa, ngưỡng |
| `dev_manifest.json` | Tổng hợp toàn bộ quyết định của `--stage dev`, dùng để tái lập |
| `holdout_patient_outputs.csv` | Output cuối cho từng bệnh nhân trên holdout (không commit, xem `.gitignore`) |
| `holdout_segment_diagnostics.csv` | Sai số theo phân đoạn trên holdout |
| `old_vs_new_comparison.csv` | So sánh V1 (đóng băng) và V2 trên cùng holdout mới |
| `holdout_final_metrics.json` / `HOLDOUT_EVALUATED.json` | Kết quả cuối và khóa đánh giá một lần |
