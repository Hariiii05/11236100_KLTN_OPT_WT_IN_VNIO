# Pipeline V3: loại dịch vụ khám nội (KB008) và ca chờ >24 giờ khỏi cohort mô hình

> Holdout V3 đã được đánh giá đúng một lần ngày 25/09/2026 (`results/unified_pipeline_v3/HOLDOUT_EVALUATED.json`) và mô hình mới nhất đã được huấn luyện lại (`unified_model_latest.joblib`). Kết quả dưới đây không được dùng để chọn lại feature, mô hình hay hyperparameter.

V3 giữ nguyên toàn bộ thiết kế của V2 ([`unified_pipeline.md`](unified_pipeline.md): tách holdout theo thời gian, walk-forward CV, hiệu chỉnh theo khoa, classifier cảnh báo giờ trưa) và chỉ đổi **cohort mô hình**, thêm một bước huấn luyện lại trên dữ liệu mới nhất. V1 (`results/final_model/`) và V2 (`results/unified_pipeline/`) giữ nguyên làm lịch sử; V3 ghi vào `results/unified_pipeline_v3/`.

## 1. Vì sao đổi cohort

### 1.1. Dịch vụ khám nội KB008 không có mốc bắt đầu chờ hợp lệ

Nhận định nghiệp vụ (người dùng xác nhận): KB008 là dịch vụ khám phụ thuộc, được chỉ định trong quá trình của một dịch vụ khám trước nó, nên khoảng "status 20 → status 60" không phải thời gian chờ hàng đợi. Đã kiểm chứng lại bằng dữ liệu (2.898 ca KB008 có target hợp lệ):

| Kiểm tra | Kết quả |
|---|---|
| `chi_dinh_tu_loai_dich_vu` của KB008 | 100% bằng 10; các dịch vụ khác chỉ là 200 (134.987 ca) hoặc 230 (3.634 ca) |
| Dịch vụ "cha" đã chỉ định KB008 | KB001 (1.131), KB004 (969), KB006 (487), KB003 (249), KB005 (58) |
| Mốc status 20 so với lúc dịch vụ cha bắt đầu khám | **Sau lúc cha bắt đầu ở 100% ca** (trung vị +24 phút, phân vị 5–95%: +0,5 đến +1.022 phút) |
| Mốc status 20 nằm giữa lúc dịch vụ cha đang khám (60→150) | 81,3% ca |
| KB008 bắt đầu khám so với lúc dịch vụ cha kết luận | Trung vị sớm hơn 75 phút |
| Phòng thực hiện | Chỉ H401 (2.525 ca) và E1108 (373 ca); không có phòng nào chỉ một phần là KB008 |

Vậy khoảng 20→60 của KB008 lẫn thời gian khám của dịch vụ cha, không đo được hàng đợi. Đây cũng chính là toàn bộ "nhánh status 20" của V1/V2 (không ca nào có status 40), nên loại KB008 đồng nghĩa với loại nhánh này và loại luôn hai phòng H401, E1108. Quy tắc dựa trên mã dịch vụ, biết trước tại thời điểm dự báo, không dựa trên sai số của mô hình.

Lưu ý: kiểm tra thử nghiệm trên validation V2 cho thấy huấn luyện riêng/loại nhánh status 20 không cải thiện nhánh status 40 (MAE 12,15 so với 12,12) và mô hình riêng cho status 20 không thắng mô hình chung (176,3 so với 175,6; trung vị 181,2). Lý do loại bỏ vì vậy là **định nghĩa mục tiêu không hợp lệ**, không phải vì mô hình cải thiện nhờ loại.

### 1.2. Ca chờ >24 giờ là bệnh nhân bỏ về

Nhận định nghiệp vụ (người dùng xác nhận): các ca chờ >24 giờ là người bệnh bỏ về, quay lại khám ở ngày sau. Dữ liệu phù hợp: 77 ca (0,054%), khoảng cách 1–33 ngày lịch giữa lúc vào hàng đợi và lúc khám (51 ca cách hơn 3 ngày), giờ bắt đầu khám tập trung 7–8 giờ sáng, và chỉ 1/77 ca có status 50 ("bỏ qua") trong khoảng chờ, tức HIS không tự đóng các lượt này. 47/77 ca trùng KB008, nên sau khi loại KB008 chỉ còn 30 ca bị loại thêm.

Không thuộc quy tắc này: 46 ca đăng ký buổi tối và khám sáng hôm sau với thời gian chờ dưới 24 giờ (khác ngày lịch nhưng ≤24 giờ). Người dùng chọn giữ luật ">24 giờ"; nhóm 46 ca này được giữ trong cohort.

### 1.3. Quy tắc cohort (`pipeline/cohort.py`)

Áp dụng ngay sau khi đọc `model_dataset.csv`, trước feature engineering, theo thứ tự: (1) loại `ma_dich_vu` thuộc `--exclude-service-codes` (mặc định `KB008`), (2) loại `waiting_minutes` > `--max-wait-minutes` (mặc định 1440). Một dòng thỏa cả hai chỉ được đếm một lần, ở quy tắc đầu. Các file audit và `model_dataset.csv` không bị sửa. Bảng hao hụt: `results/unified_pipeline_v3/cohort_attrition.csv`.

## 2. Quyết định thiết kế (đã được người dùng xác nhận)

1. **Giữ luật ">24 giờ"** (không dùng luật "khác ngày lịch").
2. **Lọc rồi mới tính feature.** Với KB008, `queue_entry_time` (status 20) không phải lúc đến hàng đợi thật, nên nếu giữ lại khi tính đếm lượt đến, lượt bắt đầu khám và "số người đứng trước" của khoa K30.7 thì các feature này bị nhiễu. Các ca chờ >24 giờ cũng bị loại khỏi phép đếm "đang chờ" thay vì bị coi là đang chờ nhiều ngày.
3. **Dùng lại mốc holdout `2026-07-15`** (`--holdout-start`), để V1, V2, V3 được chấm trên cùng giai đoạn. Đây là điểm cần nói rõ: giai đoạn này đã được V2 đánh giá một lần. Quy tắc cohort dựa trên nghiệp vụ và mã dịch vụ, không được chọn theo sai số holdout, nhưng holdout V3 **không phải dữ liệu hoàn toàn chưa từng thấy**. Dữ liệu chỉ tới 31/07 nên không có dữ liệu mới hơn; nếu bổ sung được dữ liệu tháng 8 từ HIS thì đó mới là holdout thực sự chưa thấy.
4. **Mốc holdout cố định tường minh thay vì tính từ `--holdout-days`.** Dữ liệu thực sự kết thúc 31/07 (ba dòng tháng 8 đều là KB008 lẻ tẻ và bị loại), nên tính lùi 21 ngày từ dòng cuối sau khi lọc sẽ ra `2026-07-11` thay vì `2026-07-15`. Holdout gồm **17 ngày lịch có dữ liệu (15/07–31/07)**.
5. **Khóa holdout kèm quy tắc cohort.** `holdout_locked.json` lưu quy tắc cohort; đổi quy tắc hoặc mốc sau khi khóa sẽ bị từ chối, vì làm thay đổi holdout gồm những dòng nào.
6. **`queue_entry_status` giữ trong danh sách feature nhưng là hằng số (chỉ còn 40).** Mô hình bỏ qua nó; giữ để V2 tái lập được bằng cùng mã. Segment chẩn đoán hằng số được tự động bỏ.
7. **Không làm hai đối chiếu tùy chọn** (tính-rồi-lọc so với lọc-rồi-tính, và bỏ khỏi "đang chờ" các ca vào hàng đợi từ ngày trước), vì quyết định 1–2 làm chúng không còn ý nghĩa.

## 3. Mô hình cuối: bản đã đánh giá và bản mới nhất

| File | Huấn luyện trên | Dùng để |
|---|---|---|
| `unified_model.joblib` | Dữ liệu **trước 15/07** (fit 42 ngày trừ 7 ngày cuối, hiệu chỉnh 7 ngày cuối) | Báo cáo chỉ số: các chỉ số holdout thuộc bản này |
| `unified_model_latest.joblib` | Dữ liệu đến hết **31/07**, gồm cả giai đoạn holdout: fit 20/06–24/07 (44.557 dòng), hiệu chỉnh 25/07–31/07 (8.968 dòng) | **Triển khai**: mô hình mới nhất |

`--stage refit-final` chỉ chạy được sau khi holdout đã đánh giá xong (`HOLDOUT_EVALUATED.json`) và **không chọn lại gì**: cùng feature, cùng hyperparameter, cùng ngưỡng cảnh báo giờ trưa đã chọn ở giai đoạn dev; chỉ dữ liệu huấn luyện và hệ số hiệu chỉnh được cập nhật theo cửa sổ mới nhất. Vì các dòng holdout là dữ liệu gần nhất và dữ liệu gần đây có giá trị cao (W10), mô hình triển khai cần học từ chúng; làm trước khi đánh giá sẽ làm hỏng holdout. Bản refit **không có đánh giá độc lập riêng**. Thông tin cửa sổ và số dòng nằm ở `refit_manifest.json`.

Lưu ý khi triển khai thực tế:

- **KB008 (khám nội) không có dự báo**: các dịch vụ thuộc nhánh này nằm ngoài phạm vi mô hình.
- **Khi tính "số người đứng trước" và các phép đếm lượt vào hàng đợi, cần bỏ các lượt vào hàng đợi đã quá 24 giờ.** Mô hình được huấn luyện trên cohort đã loại các ca bỏ về; nếu hệ thống sống vẫn đếm các lượt đó (chúng vẫn ở trạng thái "chờ khám" cho tới khi bệnh nhân quay lại), feature triển khai sẽ lệch so với lúc huấn luyện.
- **Đánh giá chỉ phản ánh bệnh nhân ở lại chờ** (vì loại theo nhãn >24 giờ). Khi triển khai mô hình vẫn dự báo cho mọi bệnh nhân, kể cả người sau đó bỏ về.

## 4. Kết quả chọn mô hình (validation, out-of-fold)

Cohort mô hình: 138.591 dòng (loại 2.898 ca KB008 và thêm 30 ca chờ >24 giờ, tổng −2,07%). Trước holdout: 115.511 dòng, chia thành 6 fold validation trượt (sliding 42 ngày). Holdout: 23.080 dòng, 17 ngày lịch có dữ liệu (15/07–31/07).

| Hạng | Candidate | Cửa sổ | Mean MAE | Std MAE | Mean ≤20 phút | Selection score |
|---:|---|---|---:|---:|---:|---:|
| 1 | **queue_dynamics_lunch_deeper** | sliding | **9,35** | 0,37 | **90,7%** | **9,45** |
| 2 | queue_dynamics_lunch_deeper | expanding | 9,39 | 0,29 | 90,8% | 9,47 |
| 3 | queue_dynamics_deeper | sliding | 9,41 | 0,42 | 90,5% | 9,51 |
| 4 | queue_dynamics_patient_type_deeper | sliding | 9,42 | 0,42 | 90,6% | 9,53 |
| 5 | queue_dynamics_lunch_current | sliding | 9,48 | 0,41 | 90,5% | 9,58 |
| 8 | official_deeper | sliding | 10,08 | 0,29 | 89,1% | 10,15 |
| 9 | official_current | sliding | 10,14 | 0,38 | 89,2% | 10,23 |
| 10 | median_baseline | sliding | 14,70 | 0,58 | 84,1% | 14,84 |

Candidate được chọn vẫn là **`queue_dynamics_lunch_deeper`**, cùng cấu hình như V2. Ba điểm đáng chú ý:

- **Chênh lệch giữa các ứng viên tốt nhất rất nhỏ.** Sliding thắng expanding chỉ 0,04 phút (9,35 so với 9,39), và tập feature có giờ trưa hơn `queue_dynamics` thuần chỉ 0,05 phút. Các chênh lệch này nằm trong biên nhiễu giữa fold; so với V2 thì V3 không còn bằng chứng mạnh cho "sliding tốt hơn expanding". Khoảng cách lớn nhất vẫn là giữa `official` và `queue_dynamics` (≈0,5–0,7 phút, 5–7%).
- **Độ lệch giữa các fold giảm từ 2,64 (V2) xuống 0,37**, và xu hướng MAE theo fold từ −0,67 xuống −0,12 phút/fold. Điều này phù hợp với việc V2 bị chi phối bởi các ca KB008 và ca bỏ về; đọc lại các kết luận cũ về "dữ liệu gần đây tốt hơn" (W10) cần thận trọng, vì xu hướng cải thiện theo thời gian nay yếu hơn nhiều.
- **Cảnh báo giờ trưa trên validation** (out-of-fold, 42.002 ca đăng ký sáng, 359 ca dương tính = 0,85%): ngưỡng 0,229, precision 0,717, recall 0,713, F1 0,715.

### 4.1. Sai số train so với validation (kiểm tra overfit/underfit)

`pipeline/train_vs_validation_mae.py` huấn luyện lại ứng viên được chọn và hai mô hình tham chiếu trên từng fold sliding, rồi chấm trên chính dòng train và dòng validation của fold đó. Chỉ dùng dữ liệu trước holdout, không đổi lựa chọn mô hình.

| Mô hình | MAE train | MAE validation | Chênh lệch | R² train | R² validation |
|---|---:|---:|---:|---:|---:|
| median_baseline | 14,40 | 14,70 | 0,30 | −0,06 | −0,05 |
| official_current | 8,99 | 10,14 | 1,15 | 0,672 | 0,687 |
| **queue_dynamics_lunch_deeper** | **7,59** | **9,35** | **1,77** | **0,723** | **0,722** |

MAE train thấp hơn validation 1,77 phút (tỉ lệ 1,23), còn R² gần như bằng nhau, và MAE holdout (9,52) sát MAE validation: không có dấu hiệu overfit nặng. MAE train thấp hơn nhiều so với baseline nên cũng không underfit. Mô hình sâu hơn có khoảng cách lớn hơn mô hình `official_current` nhưng vẫn có MAE validation tốt hơn. Ở baseline, ba fold cuối có MAE validation thấp hơn MAE train, cho thấy một phần chênh lệch đến từ thay đổi phân phối theo thời gian chứ không chỉ do overfit. Kết quả từng fold nằm ở `train_vs_validation_mae_folds.csv`.

## 5. Kết quả holdout (đánh giá cuối, một lần)

Holdout: 23.080 dòng, `2026-07-15 00:00:00` đến `2026-07-31 22:49`, 17 ngày lịch có dữ liệu.

| Chỉ số | Raw | Calibrated theo khoa |
|---|---:|---:|
| MAE | 9,52 | 9,52 |
| RMSE | 17,49 | 17,50 |
| R² | 0,629 | 0,629 |
| Median AE | 5,94 | 5,94 |
| P90 AE | 19,45 | 19,47 |
| Bias | −1,79 | −1,84 |
| Sai số ≤10 phút | 71,3% | 71,4% |
| Sai số ≤20 phút | 90,5% | 90,5% |
| Coverage P50 | 52,1% | 52,1% |
| Coverage P80 | **78,0%** | **81,4%** |

Hiệu chỉnh theo khoa gần như không đổi MAE nhưng đưa coverage P80 từ 78,0% lên 81,4%. RMSE và R² nay có ý nghĩa (RMSE 17,5 phút, R² 0,63), khác hẳn V2 (RMSE 322, R² 0,005) vì các ca chờ nhiều ngày không còn chi phối chúng.

**Đuôi chờ dài** (>120 phút): 199 ca (0,86%), MAE 84,70 phút, bias −71,58, chiếm **7,7%** tổng sai số tuyệt đối (V2: 41%); MAE các ca ≤120 phút là 8,86.

**Cảnh báo "Chờ quá giờ nghỉ trưa"** (ngưỡng 0,2292 chọn từ validation, không đổi khi áp lên holdout):

| Chỉ số | Giá trị | Khoảng tin cậy 95% |
|---|---:|---:|
| Số ca đăng ký buổi sáng | 18.664 | |
| Số ca thực sự bị đẩy qua trưa | 134 (0,72%) | |
| Số ca được cảnh báo | 230 | |
| Precision | 0,474 | 0,41–0,54 |
| Recall | 0,813 | 0,74–0,87 |
| F1 | 0,599 | |

Recall 81% (bắt được khoảng 4/5 ca). Precision trên holdout (0,47) thấp hơn validation (0,72) trong khi recall cao hơn (0,81 so với 0,71). Mô hình V2 cũng có đúng hiện tượng này (precision 0,69 → 0,44), tức không phải ngẫu nhiên riêng của V3: ngưỡng chọn trên validation có xu hướng cảnh báo dư ở giai đoạn sau, một phần vì tỷ lệ dương trên holdout thấp hơn (0,72% so với 0,85%). Với 134 ca dương tính, khoảng tin cậy còn rộng. Nếu ưu tiên ít báo nhầm hơn, có thể chọn ngưỡng cao hơn hoặc chọn ngưỡng theo `--lunch-threshold-fbeta` nhỏ hơn 1.

## 6. So sánh V1, V2, V3 trên cùng 23.080 dòng holdout

Cả ba mô hình được chấm trên đúng cùng các dòng. V1 và V2 là mô hình đóng băng, không huấn luyện lại; feature của V2 được tính lại trên cohort đã lọc khi chấm.

| Mô hình | MAE | RMSE | R² | P90 AE | Bias | ≤10 phút | ≤20 phút |
|---|---:|---:|---:|---:|---:|---:|---:|
| V1 (đóng băng) | 10,57 | 20,30 | 0,501 | 20,94 | −1,29 | 68,4% | 89,2% |
| V2 (đóng băng, calibrated) | 9,50 | 17,55 | 0,627 | 19,51 | −2,08 | 71,8% | 90,3% |
| **V3 (calibrated)** | **9,52** | **17,50** | **0,629** | **19,47** | −1,84 | 71,4% | **90,5%** |
| *Tham khảo: V1 trên Final Test gốc (khác cohort, khác giai đoạn)* | *15,69* | *319,66* | *0,006* | *21,80* | *−6,75* | *67,5%* | *88,5%* |

Sai số theo nhóm (MAE, phút):

| Nhóm | Số dòng | V1 | V2 | V3 |
|---|---:|---:|---:|---:|
| Ca vượt giờ nghỉ trưa | 134 | 87,1 | 61,4 | 63,5 |
| Đuôi chờ >120 phút | 199 | 105,0 | 81,2 | 84,7 |
| Ca ≤120 phút | 22.881 | 9,75 | 8,87 | 8,86 |

Cảnh báo giờ trưa trên cùng 18.664 ca sáng: V3 precision 0,474 / recall 0,813 / F1 0,599; V2 đóng băng (ngưỡng 0,237) precision 0,459 / recall 0,836 / F1 0,593.

Diễn giải:

1. **V3 so với V1**: MAE −9,9% (10,57 → 9,52), P90 AE −7,0%, RMSE −13,8%, ≤10 phút +3,0 điểm, ≤20 phút +1,3 điểm. Cải thiện tập trung ở các ca thông thường (−9,1%) và nhóm vượt giờ trưa (−27%).
2. **V3 không tốt hơn V2 trên cùng cohort.** MAE chênh 0,02 phút, rất nhỏ so với sai số chuẩn của bản thân MAE (≈0,10 phút với 23.080 dòng), nên coi là ngang nhau; ≤20 phút, P90, bias và F1 cảnh báo cũng gần như trùng nhau. Vì vậy việc loại KB008 và ca bỏ về **không làm dự báo chính xác hơn trên các ca hợp lệ**. Giả thuyết ban đầu rằng các ca này làm nhiễu feature hàng đợi không được thấy trên holdout dưới dạng cải thiện độ chính xác. Giá trị của V3 nằm ở việc định nghĩa mục tiêu hợp lệ và các chỉ số trung thực, không ở độ chính xác.
3. **MAE toàn cục giảm từ 15,18 (V2 trên cohort cũ) xuống 9,52 hoàn toàn là hiệu ứng của cohort**, không phải mô hình tốt lên: V2 đóng băng chấm trên cohort mới đã cho 9,50. Không được trình bày mức giảm này như một cải tiến.
4. **Đính chính một kết luận của V2**: V2 kết luận rằng feature giờ trưa gần như không giảm MAE cho nhóm vượt giờ trưa (validation 429,8 → 427,3 phút; holdout 503,3 → 482,6 phút) và cho rằng đó là giới hạn cố hữu của mô hình điểm. Trên cohort sạch, cùng so sánh cho thấy giảm rõ (validation 63,7 → 53,9 phút, −15%; holdout V1 87,1 → V3 63,5 phút, −27%). Vậy kết luận cũ chủ yếu phản ánh các ca chờ nhiều ngày và KB008, không phải giới hạn của mô hình. Classifier riêng vẫn cần thiết vì MAE nhóm này còn 63,5 phút và mô hình vẫn dự báo thấp (bias −59,7).

## 7. Điểm yếu: đã được giải quyết bằng cohort, và còn tồn đọng

### 7.1. Điểm yếu của V2 hóa ra là hiệu ứng của KB008 và ca bỏ về

| Điểm yếu ở V2 | Thấy gì ở V3 |
|---|---|
| Nhánh status 20 kém hơn hẳn (MAE 230 so với 12) | Nằm ngoài phạm vi: mốc chờ không hợp lệ (mục 1.1), không phải điểm yếu của mô hình |
| Phòng PK-13 có MAE 225–272 phút, ~15,7% tổng sai số | Không còn trong nhóm phòng sai số cao nhất. Trong 21 ca chờ >120 phút của phòng này, 9 ca là chờ >24 giờ |
| Nhóm "hơn 10 người đứng trước" (268 ca validation, MAE 102) | Nhóm này biến mất trên validation V3; nhóm 6–10 người (472 ca) có MAE 9,2. Phù hợp với việc ca KB008/bỏ về bị đếm là đang chờ; đây là quan sát sau khi lọc, chưa kiểm chứng riêng |
| Đuôi >120 phút chiếm 41% tổng sai số | 7,7% (holdout V3) |

### 7.2. Còn tồn đọng

| # | Điểm yếu | Bằng chứng (holdout V3, trừ khi ghi khác) |
|---|---|---|
| T1 | Ca chờ dài vẫn bị dự báo thấp | Đuôi >120 phút: 199 ca, MAE 84,7, bias −71,6; nhóm 60–120 phút bias −22,8; coverage P80 riêng nhóm >120 phút chỉ 29,6% (coverage theo nhóm kết quả bị thiên lệch, chỉ tham khảo) |
| T2 | Giờ 05 và 11 chưa được nắm hết | Validation: 05h MAE 19,3 (1.845 dòng), 11h MAE 17,4 và bias −12,4 (836 dòng); holdout: 05h 13,2, 11h 15,9 và bias −8,8 (397 dòng). Cấu trúc giờ mở cửa/nghỉ trưa còn phần dư |
| T3 | PK-37 và PK-38 sai số cao và dự báo cao hơn thực tế | MAE 30,7 và 23,4; bias +3,4 và +7,3; chiếm 4,9% và 4,0% tổng sai số |
| T4 | Coverage P80 theo khoa chưa đều | K30.6 chỉ 70,8% (408 dòng), 14.30.1 là 78,5%, so với 81,4% toàn cục |
| T5 | Ngưỡng cảnh báo giờ trưa cảnh báo dư ở giai đoạn sau | Precision holdout 0,47 so với validation 0,72; lặp lại ở V2 |
| T6 | Bằng chứng "dữ liệu gần đây tốt hơn" (W10) cần đo lại | Xu hướng MAE theo fold nay chỉ −0,12 phút/fold (V2: −0,67), có thể V2 bị chi phối bởi ca ngoại lai; chưa có thí nghiệm riêng về độ dài cửa sổ và tần suất huấn luyện lại |
| T7 | Không quan sát được bác sĩ phục vụ ca đang chờ; ca thiếu status 40 vẫn bị loại | Cần dữ liệu thời điểm gán bác sĩ và quyết định nghiệp vụ (W7, W8 của `model_diagnosis_and_calibration.md`). Phân tích định lượng phần thiếu status 40 (tập trung theo phòng, ảnh hưởng đến MAE holdout và feature tải): `missing_status40_analysis.md` |

BHYT so với dịch vụ: V3 cho MAE của BHYT cao hơn dịch vụ ở cả hai giai đoạn (validation 10,38 và 9,31; holdout 9,79 và 9,51), chênh lệch nhỏ (0,3–1,1 phút) và cùng chiều, không đảo chiều như ở V2 (nơi validation BHYT tệ hơn hẳn còn holdout tốt hơn). Cỡ mẫu BHYT nhỏ (885 dòng holdout) nên chưa đủ để kết luận đây là điểm yếu thật.

## 8. Giả định cần điều chỉnh

1. Các giả định của V2 về giờ nghỉ trưa [11:30–13:00], ranh giới nhãn `crosses_lunch` dùng `lunch_start`, ngưỡng cảnh báo theo F1 (beta=1) và cách suy `patient_payment_type` từ `doi_tuong` vẫn áp dụng (xem mục 11 của [`unified_pipeline.md`](unified_pipeline.md)).
2. **Giả định nghiệp vụ mới**: mọi ca KB008 đều là dịch vụ phụ thuộc và mọi ca chờ >24 giờ đều là bệnh nhân bỏ về; dữ liệu ủng hộ nhưng chưa được đối chiếu từng ca với HIS.
3. **Holdout V3 không hoàn toàn chưa từng thấy** (mục 2, điểm 3).
4. **Lượt vào hàng đợi "qua đêm" dưới 24 giờ (46 ca) được giữ** theo luật ">24 giờ".

## 9. Lệnh chạy và artifact

```powershell
.\.venv\Scripts\python.exe -m pipeline.run_unified_pipeline --stage dev          # có thể chạy lại
.\.venv\Scripts\python.exe -m pipeline.run_unified_pipeline --stage final-eval   # một lần
.\.venv\Scripts\python.exe -m pipeline.run_unified_pipeline --stage refit-final  # sau final-eval
```

Tái lập V2 bằng cùng mã: thêm `--output-dir results/unified_pipeline --exclude-service-codes --max-wait-minutes 0 --holdout-start auto`.

Artifact mới so với V2 (các file còn lại giống V2, xem mục 12 của [`unified_pipeline.md`](unified_pipeline.md)):

| File | Nội dung |
|---|---|
| `cohort_attrition.csv` | Số dòng vào, bị loại theo từng quy tắc, còn lại; tách theo toàn bộ / trước holdout / holdout |
| `holdout_locked.json` | Ranh giới holdout, quy tắc cohort, số ngày lịch có dữ liệu trong holdout |
| `unified_model_latest.joblib` / `refit_manifest.json` | Mô hình huấn luyện lại trên dữ liệu mới nhất và thông tin cửa sổ |
| `old_vs_new_comparison.csv` | V1, V2, V3 chấm trên đúng cùng các dòng holdout V3 |
| `train_vs_validation_mae_folds.csv` / `train_vs_validation_mae_summary.csv` | MAE, R² train và validation theo từng fold (`python -m pipeline.train_vs_validation_mae`, chạy sau stage dev) |
