# Web application: Tiếp đón → Dự báo thời gian chờ → QMS → Dashboard

Ứng dụng web vận hành cho phòng khám, dùng **mô hình V3 thật** (`results/unified_pipeline_v3/unified_model_latest.joblib`) để dự báo thời gian chờ ngay lúc tiếp đón. Gồm 3 phân hệ:

| Phân hệ | Đường dẫn | Chức năng |
|---|---|---|
| Quản lý tiếp đón | `#/tiep-don` | Màn hình thông tin hành chính (theo ảnh mẫu 1.1, `Lưu thông tin [F4]`) rồi màn hình chỉ định dịch vụ (theo ảnh 1.2): chọn dịch vụ `[F2]`, bảng *SL NB theo PK* có cột thời gian chờ dự kiến, dự báo, `Lưu & cấp số [F4]`, `NB tiếp theo [F1]` |
| QMS | `#/qms` | Màn hình TV phòng khám, màn hình khu vực chờ, bàn điều phối (gọi số, bắt đầu khám, hoàn thành, bỏ lượt), kiosk tra cứu |
| Dashboard | `#/dashboard` | KPI tổng quan, theo thời gian, theo phòng, dự báo so với thực tế, cảnh báo vận hành; bộ lọc: thời gian, chuyên khoa, phòng khám, đối tượng |

## 1. Chạy ứng dụng

Yêu cầu: đã có `.venv` và dữ liệu `data/processed/model_dataset.csv`, `data/intermediate/01_encounter.csv`, `data/intermediate/03_exam.csv` (xem README gốc, mục 2).

```powershell
.\.venv\Scripts\python.exe -m pip install -r webapp\requirements.txt   # thêm fastapi, uvicorn
.\.venv\Scripts\python.exe -m webapp                                 # http://127.0.0.1:8000
.\.venv\Scripts\python.exe -m webapp --host 0.0.0.0 --port 8080      # mở cho máy khác trong mạng nội bộ
```

Lần chạy đầu mất khoảng 20 giây để dựng cache (`webapp/cache/history.pkl`); các lần sau khởi động trong khoảng 1–2 giây. ECharts nằm sẵn trong `webapp/static/`; font Roboto (400/500/700, subset vietnamese) nạp từ Google Fonts, khi không có internet giao diện dùng Segoe UI/Arial. Tài liệu API tự sinh ở `http://127.0.0.1:8000/docs`.

Kiểm chứng và kiểm thử:

```powershell
.\.venv\Scripts\python.exe -m webapp.verify_parity            # đối chiếu với pipeline huấn luyện trên dữ liệu thật
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .  # 46 test, gồm tests/test_webapp_features.py
```

## 2. Vì sao có "đồng hồ mô phỏng" (phát lại dữ liệu HIS)

18/35 biến của mô hình mô tả **trạng thái hàng đợi và lịch sử của cả phòng và khoa** tại thời điểm vào hàng đợi: số người đang chờ trước, số lượt vào và số lượt bắt đầu khám trong 15/30/60 phút, số phút từ lượt khám gần nhất, tỷ lệ ca sáng bị đẩy qua trưa trong 42 ngày… Dữ liệu HIS chỉ có đến 31/07/2026. Nếu chạy theo giờ thật (sau 31/07), các biến này sẽ bằng 0, tức là mô hình bị gán giá trị giả.

Vì vậy web chạy trên một **đồng hồ mô phỏng** (góc phải thanh tiêu đề, mặc định 31/07/2026 07:30):

- Các lượt khám thật của ngày đó được **phát lại đúng giờ**: xuất hiện khi vào hàng đợi (status 40), chuyển sang "Đang khám" ở status 60, "Hoàn thành" ở status 150. Trên màn hình chúng mang nhãn `HIS`.
- Người bệnh tiếp đón trên web được chèn vào **cùng hàng đợi đó** (nhãn `WEB`, số thứ tự dạng `W72`). Feature của họ được tính từ tải thật của bệnh viện cộng với các phiếu web khác.
- Có thể tạm dừng, đổi ngày/giờ trong giai đoạn 01/04–31/07/2026, hoặc tăng tốc x5/x15/x60. Mọi màn hình dùng chung đồng hồ này.

Khi triển khai thật, chỉ cần thay nguồn phát lại (`webapp/history.py`, `Operations.tickets_at`) bằng luồng sự kiện từ HIS, ví dụ truy vấn `docs/sql/queue_snapshot_for_prediction.sql`. Phần tính feature và mô hình giữ nguyên.

## 3. Tích hợp mô hình

### 3.1. Artifact và tiền xử lý

| Thành phần | Nguồn | Cách dùng lại |
|---|---|---|
| Mô hình P50 và P80 | `bundle['quantile_models']`: `Pipeline(ColumnTransformer(OrdinalEncoder, SimpleImputer(median)), HistGradientBoostingRegressor)` | Gọi thẳng `pipeline.model_training.predict_quantile_models`. Encoder và imputer đã fit nằm trong pipeline, không mã hóa lại bằng tay |
| Hiệu chỉnh khoảng theo khoa | `bundle['calibration_offsets']` | `apply_group_conformal_offsets` |
| Cảnh báo qua giờ trưa | `bundle['lunch_classifier']`, `lunch_threshold` = 0,229 | `predict_lunch_probability`, `build_patient_output` (cùng hàm tạo output holdout) |
| Feature | Định nghĩa trong `optimize_on_development.add_causal_features` và `feature_engineering.add_lunch_break_features` | Viết lại dưới dạng truy vấn tại một thời điểm (`webapp/features.py`); lịch và biến mã hóa chu kỳ dùng đúng biểu thức của `load_dataset` |

Giá trị phân loại lạ (phòng hoặc dịch vụ không có trong encoder) bị **từ chối ở bước kiểm tra đầu vào**, không để encoder âm thầm đổi thành NaN.

### 3.2. Danh sách 35 biến và nguồn

| Nguồn | Biến | Trên giao diện |
|---|---|---|
| Người dùng nhập (bắt buộc) | `ma_dich_vu`, `ma_phong_thuc_hien`, `patient_payment_type` (BHYT/dịch vụ), `uu_tien`, `cap_cuu` | Đối tượng có dấu `*`; Ưu tiên/Cấp cứu là ô tích. Thiếu thì không gọi mô hình và báo lỗi, ví dụ "Vui lòng chọn Phòng khám trước khi dự báo thời gian chờ." |
| Suy ra từ lựa chọn | `ma_khoa_thuc_hien` (mỗi phòng thuộc đúng một khoa) | Tự động |
| Hằng số hệ thống | `queue_entry_status` = 40 | Tự động |
| Tự động từ thời gian | giờ, thứ, cuối tuần, 4 biến sin/cos, `minutes_to_lunch_start` | Từ thời điểm vào hàng đợi, không bắt nhập |
| Từ dữ liệu hàng đợi | `stt` (số HIS sẽ cấp tiếp theo trong phòng), `order_to_queue_minutes` (từ lúc chọn dịch vụ đến lúc xác nhận), 7 biến lượt đến và người đứng trước, 7 biến lượt bắt đầu khám | Tính từ trạng thái hàng đợi (HIS phát lại + phiếu web) |
| Từ lịch sử 42 ngày | `room/department_lunch_overflow_rate`, `_count` | Tính từ các ca sáng đã hoàn tất; ca đăng ký buổi chiều có count = 0 và rate = NaN, đúng như lúc huấn luyện |

Các biến "trống (NaN)", ví dụ tỷ lệ qua trưa khi không có lịch sử, là **đúng định nghĩa lúc huấn luyện**: imputer trung vị của mô hình xử lý chúng giống hệt khi train. Giá trị 35 biến của mỗi lần dự báo được trả về trong `POST /api/predict` (trường `inputs`) và lưu cùng phiếu (`features_json`).

Không phải đầu vào mô hình: bác sĩ (chưa được gán tại lúc vào hàng đợi), loại khám, CCCD, địa chỉ… Các trường này vẫn có trên form để phục vụ nghiệp vụ và được ghi rõ là không dùng cho dự báo.

### 3.3. Đầu ra

- **Thời gian chờ dự kiến** = P50 đã hiệu chỉnh theo khoa (phút). **Dự kiến được khám lúc** = thời điểm vào hàng đợi + P50.
- **Khoảng 80%** = cận trên P80 (coverage 81,4% trên holdout).
- **Xác suất chờ qua giờ nghỉ trưa** (chỉ ca đăng ký trước 11:30) kèm khuyến nghị và giờ gợi ý quay lại.

**Ngoài phạm vi, không dự báo và nêu rõ lý do**: dịch vụ KB008 (dịch vụ phụ thuộc, bị loại khỏi cohort V3) và ca cấp cứu (không có ca nào trong dữ liệu huấn luyện). Ca cấp cứu vẫn được tiếp đón và cấp số, nhưng không có thời gian chờ dự kiến.

### 3.4. Kiểm chứng tiền xử lý khớp lúc huấn luyện

`python -m webapp.verify_parity` (kết quả chạy ngày 26/09/2026):

1. **Feature**: 20.000 dòng ngẫu nhiên của cohort V3. Feature do web tính (truy vấn tại thời điểm) so với `run_unified_pipeline.load_featured_data` (đường huấn luyện): **35/35 biến trùng khớp, sai khác tối đa 0**, NaN cùng vị trí.
2. **Dự báo đầu cuối**: chấm lại 23.080 dòng holdout bằng `unified_model.joblib` qua đường của web tái tạo đúng `holdout_patient_outputs.csv`. P50, P80, xác suất qua trưa và khuyến nghị **trùng 100%**; MAE = 9,52 phút, đúng như báo cáo.

### 3.5. Nhãn cảnh báo "Chờ quá giờ nghỉ trưa"

Chỉ áp dụng cho người bệnh vào hàng đợi trước 11:30. Khi xác suất ≥ ngưỡng 0,229, nhãn **Quá giờ trưa** (màu vàng) hiện ở: bảng *SL NB theo PK*, cột *Cảnh báo* của dịch vụ đã chọn, thanh kết quả dự báo (kèm xác suất và giờ gợi ý quay lại 13:00), phiếu số thứ tự, màn hình QMS phòng khám và khu vực chờ, bàn điều phối, kiosk và tab *Vận hành & cảnh báo*. Người bệnh buổi sáng không bị cảnh báo sẽ thấy dòng "Không có nguy cơ chờ quá giờ nghỉ trưa".

Cảnh báo hiếm theo thiết kế (khoảng 0,7% ca buổi sáng bị đẩy qua trưa). Ngày 31/07/2026 có 39 lượt được cảnh báo, chủ yếu ở phòng PK-37 và khung 11:00–11:30. Để xem nhanh: đặt đồng hồ **31/07/2026 11:15**, chọn dịch vụ KB004 ở màn hình chỉ định dịch vụ, các phòng như PK-22, PK-20, PK-30 sẽ có nhãn.

## 4. Luồng dữ liệu

```text
Tiếp đón (form hành chính)            → đối tượng, ưu tiên, cấp cứu
  → Chọn dịch vụ                       → ghi thời điểm chỉ định
  → Bảng phòng: dự báo cho từng phòng  → POST /api/rooms/options
  → Chọn phòng: dự báo chi tiết        → POST /api/predict   (kiểm tra đủ đầu vào, tính 35 biến, gọi bundle)
  → Xác nhận                           → POST /api/reception (dự báo lại tại lúc vào hàng đợi, cấp STT, lưu SQLite)
QMS: TV / khu vực / điều phối / kiosk  → GET /api/queue/...
  → Gọi, Bắt đầu khám, Hoàn thành, Bỏ lượt → POST /api/tickets/{id}/{action}
  → start_time − queue_entry_time = thời gian chờ thực tế
Dashboard                               → GET /api/dashboard (lịch sử + holdout), /api/dashboard/live (hiện tại + phiếu web)
```

Mỗi phiếu lưu: mã phiếu, STT, phòng, dịch vụ, giờ chỉ định, giờ vào hàng đợi, P50, P80, giờ dự kiến, xác suất qua trưa, trạng thái, giờ gọi, giờ bắt đầu khám, giờ kết thúc, giờ bỏ lượt, artifact mô hình, và toàn bộ 35 giá trị feature (`features_json`) để đánh giá lại mô hình sau này.

## 5. Nguồn dữ liệu của từng màn hình

| Màn hình | Dữ liệu thật | Dữ liệu demo |
|---|---|---|
| Hàng đợi, QMS | Lượt khám thật của ngày phát lại (không có tên người bệnh, hiển thị "Người bệnh (HIS)") | Người bệnh nhập trên web |
| Dashboard: Tổng quan, Theo thời gian, Theo phòng | Cohort V3: 138.591 lượt, 01/04–31/07/2026 | – |
| Dashboard: Dự báo & thực tế | Holdout 15/07–31/07 của `unified_model.joblib` (out-of-sample, đúng số liệu khóa luận) | Bảng "Theo dõi trong vận hành" tính từ phiếu web đã bắt đầu khám |
| Dashboard: Vận hành & cảnh báo | Trạng thái hàng đợi tại đồng hồ mô phỏng | Phiếu web |

Kết quả dự báo không bao giờ bị giả lập: nếu thiếu artifact hoặc dữ liệu thì server báo lỗi rõ ràng khi khởi động (ví dụ "Không tìm thấy artifact mô hình… chạy `--stage refit-final`").

## 6. Giới hạn và giả định

- **Ngày phát lại nằm trong giai đoạn huấn luyện** của `unified_model_latest.joblib` (huấn luyện đến hết 31/07). Vì vậy dự báo cho lượt HIS phát lại là in-sample, và dashboard **không** dùng chúng để đo độ chính xác. Độ chính xác chỉ lấy từ holdout của `unified_model.joblib`. Muốn chạy web bằng bản đã đánh giá: đặt `WT_MODEL_PATH=results\unified_pipeline_v3\unified_model.joblib` và chọn ngày trong 15/07–31/07.
- **STT**: biến `stt` là số HIS sẽ cấp tiếp theo trong phòng. Các lượt HIS phát lại về sau có thể dùng lại số đó, nên phiếu web hiển thị tiền tố `W` (ví dụ `W72`) để không nhầm.
- **Phiếu web bị bỏ lượt hoặc hủy**: vẫn được tính vào "số lượt vào hàng đợi" tại thời điểm trước đó, vì khi ấy chưa biết họ sẽ bỏ. Trong khi đó dữ liệu huấn luyện chỉ gồm lượt đã được khám. Đây là lệch train/serve vốn có khi triển khai, đã nêu trong `docs/unified_pipeline_v3.md`. Người vào hàng đợi quá 24 giờ không được tính là "đang chờ", theo đúng quy tắc cohort.
- **"Đang khám"** = từ status 60 đến 150 (đã kết luận), gồm cả thời gian làm cận lâm sàng. Đây cũng là nghĩa của cột "Đang khám" trong HIS (ảnh tham khảo 1.2).
- Không có đăng nhập/phân quyền. SQLite dùng cho một máy chủ nhỏ. Nút đặt lại dữ liệu demo: `POST /api/demo/reset` với `{"confirm": true}`.

## 7. Cấu hình (biến môi trường)

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `WT_MODEL_PATH` | `results/unified_pipeline_v3/unified_model_latest.joblib` | Bundle mô hình |
| `WT_DATASET` | `data/processed/model_dataset.csv` | Dữ liệu phát lại và dashboard |
| `WT_HOSPITAL_NAME` | `Bệnh viện Mắt Trung ương` | Tên hiển thị trên thanh tiêu đề và màn hình QMS |
| `WT_REPLAY_START` | `2026-07-31 07:30:00` | Đồng hồ mô phỏng khi khởi tạo lần đầu |
| `WT_ALERT_WAIT_MINUTES` / `WT_ALERT_QUEUE_LENGTH` / `WT_ALERT_OVERDUE_MINUTES` | 45 / 15 / 15 | Ngưỡng cảnh báo vận hành |
| `WT_DB_PATH`, `WT_CACHE_DIR` | `webapp/cache/` | SQLite và cache (không commit) |

## 8. Cấu trúc

```text
webapp/
├── __main__.py        python -m webapp (uvicorn)
├── server.py          FastAPI: API + phục vụ giao diện
├── config.py          đường dẫn, ngưỡng, biến môi trường
├── history.py         dựng cohort V3 bằng đúng hàm của pipeline (load_dataset → cohort → payment type), cache
├── features.py        35 biến tại một thời điểm bất kỳ từ trạng thái hàng đợi
├── model_service.py   nạp bundle, dự báo bằng các hàm hậu xử lý của pipeline
├── feature_catalog.py nhãn tiếng Việt và nguồn của từng biến
├── operations.py      đồng hồ phát lại, hàng đợi, kiểm tra đầu vào, tiếp đón, cấp số, thao tác QMS
├── dashboard.py       tổng hợp KPI, biểu đồ, dự báo vs thực tế, cảnh báo
├── store.py           SQLite (người bệnh, lượt tiếp đón, phiếu) và đồng hồ
├── verify_parity.py   kiểm chứng feature/dự báo khớp pipeline huấn luyện
└── static/            giao diện (HTML/CSS/JS thuần, ECharts, font nội bộ)
```
