# Chẩn đoán điểm yếu và hiệu chỉnh mô hình thời gian chờ

> Toàn bộ kết quả dưới đây chỉ dùng dữ liệu trước `2026-07-01` (sáu development fold, sliding 42 ngày). Final Test đã khóa không được đọc, không được dùng để chọn hay chỉnh gì. Mô hình chính thức trong `results/final_model/` giữ nguyên.

Chạy lại: `.\.venv\Scripts\python.exe -m pipeline.diagnose_and_calibrate` (khoảng 25 phút). Đầu ra ở `results/model_diagnosis/`.

## 1. Kết luận ngắn

1. **Thêm thời lượng khám, bác sĩ, trạng thái ca đang khám và lưu lượng 15/30/60 phút không làm mô hình tốt hơn** so với nhóm queue dynamics đã có (MAE 16,89 → 16,95, nằm trong nhiễu giữa các fold). Kết quả này giống nhau khi đo thời lượng theo 60→150 và 60→140.
2. **Hiệu chỉnh có tác dụng**: độ phủ của P80 tăng từ 77,6% lên 80,1%, P50 từ 52,5% xuống 51,0%, chỉ bằng một độ lệch học từ tuần cuối của cửa sổ train.
3. **MAE bị chi phối bởi đuôi**: 1,3–1,4% ca chờ trên 120 phút tạo ra 45–47% tổng sai số tuyệt đối. Vì vậy cần báo cáo song song MAE trên ca ≤120 phút và sai số log.
4. Mô hình chính thức nên gọi là **baseline**. Chưa đủ cơ sở để khẳng định giá trị triển khai.

## 2. Điểm yếu đã xác định (kèm bằng chứng)

| # | Điểm yếu | Bằng chứng (development) |
|---|---|---|
| W1 | Mô hình chính thức chỉ dùng lịch, danh mục và số thứ tự, không có trạng thái hàng đợi, lưu lượng, thời lượng khám | Queue dynamics đã giảm MAE 17,52 → 16,89 và MAE ca ≤120 phút 9,69 → 9,03 |
| W2 | Ngưỡng "sai số ≤20 phút" quá dễ với ca chờ ngắn | Dự báo hằng số (median) đã đạt 83,2%; mô hình chính thức 88,2%. Nên dùng MAE ca ≤120 phút và sai số log |
| W3 | Dự báo thấp có hệ thống | Bias −9 đến −10 phút ở mọi cấu hình. Một con số đơn lẻ không biểu diễn được đuôi |
| W4 | Đuôi chờ dài quyết định MAE | Ca >120 phút: 1,3–1,4% số dòng, 45–47% tổng sai số; hai phòng PK-13 và H401 chiếm ~40% (H401 là phòng thuộc nhánh status 20, riêng nhánh này chiếm 19%) |
| W5 | Đuôi có cấu trúc theo ca làm việc, mô hình không thấy | 46% ca >120 phút vào hàng đợi lúc 05–07h; 34% bắt đầu khám lúc 13–15h. Đó là ca đăng ký sớm chờ sang buổi chiều, không phải hàng đợi đông |
| W6 | 60→150 không phải thời gian chiếm dụng bác sĩ | 78,7% ca bắt đầu khám (60) trước khi bác sĩ đó đóng ca trước (150). Median 60→150 là 34,7 phút, nhưng khoảng cách hai lần bắt đầu liên tiếp của cùng bác sĩ chỉ 5,3 phút. P99 của 60→150 là 1.065 phút |
| W7 | Không quan sát được bác sĩ phục vụ ca đang chờ | Status 40 được tạo bởi nhiều API: `dang-kham` (55.719 lần) và thanh toán `nb-phieu-thu/thanh-toan` (54.367 lần) là hai nguồn chính trong 140.124 lần, nên không chứng minh được bác sĩ đã gán lúc vào hàng đợi. Tài khoản ở status 60 chỉ khớp `bac_si_kham_id` 37,4% |
| W8 | 15% ca bị loại khỏi mô hình | 25.787/170.216 dịch vụ thiếu status 40 (đã báo trong audit). Khi triển khai các ca này sẽ không có dự báo |
| W9 | Không có khoảng dự báo, chưa hiệu chỉnh | P80 thô chỉ phủ 77,6–78,5% |
| W10 | Có dấu hiệu dữ liệu gần nhất rất có giá trị (chưa tách được khỏi hiệu ứng ít dữ liệu hơn) | Train trên 35 ngày thay vì 42 ngày (bỏ 7 ngày cuối) làm MAE xấu đi ~0,7 phút (16,89 → 17,58). Cần thử riêng tần suất retrain trước khi kết luận |

## 3. Kịch bản chẩn đoán và hiệu chỉnh

1. **Chẩn đoán.** Sai số của mô hình chính thức theo status, nhóm thời gian chờ thực tế, số ca đứng trước, giờ, phòng (`segment_diagnostics_*.csv`). Có mốc tham chiếu là dự báo hằng số.
2. **Feature nhân quả.** Chỉ dùng sự kiện xảy ra **trước** `queue_entry_time`, lấy từ toàn bộ 117.412 ca có status 60 (kể cả ca target không hợp lệ):
   - median/P75 thời lượng 60→150 theo phòng và dịch vụ (200 ca gần nhất, trong 28 ngày);
   - median/P75 theo bác sĩ, gắn vào lúc ca hoàn tất rồi lấy trung bình trên nhóm bác sĩ vừa hoàn tất trong phòng (chỉ dùng bác sĩ của ca đã đóng, vì lúc đó bác sĩ chắc chắn đã biết);
   - số tài khoản đang thao tác trong phòng 30/60 phút (thay cho "số bác sĩ đang phục vụ", vì danh tính bác sĩ chưa chứng minh được);
   - số ca đang khám, thời gian đã khám (trung bình/lớn nhất), ước lượng thời lượng còn lại;
   - số lượt bắt đầu và hoàn tất trong 15/30/60 phút, khoảng cách median giữa các lần bắt đầu;
   - trạng thái mở phòng trong ngày (số ca đã bắt đầu hôm nay, số phút từ ca đầu tiên);
   - ước lượng xả hàng đợi: số ca đứng trước × median thời lượng ÷ số người phục vụ.
3. **Ablation** theo thang cộng dồn, mỗi bước huấn luyện hai mô hình: P50 (absolute error) và P80 (quantile 0,8).
4. **Hiệu chỉnh** split-conformal: huấn luyện trên 35 ngày đầu của cửa sổ, học độ lệch P50 và P80 trên 7 ngày cuối, áp lên tuần validation.

## 4. Kết quả (trung bình sáu fold development)

| Cấu hình | MAE | MAE ca ≤120′ | Sai số log | Phủ P50 | Phủ P80 |
|---|---:|---:|---:|---:|---:|
| Hằng số (median) | 22,29 | 12,99 | 0,741 | 51,2% | 80,7% |
| Chính thức (baseline) | 17,52 | 9,69 | 0,559 | 52,8% | 79,3% |
| Queue dynamics | **16,89** | 9,03 | 0,521 | 52,4% | 78,2% |
| + thời lượng 60→150 | 16,95 | 9,04 | 0,522 | 52,0% | 77,4% |
| + bác sĩ/tài khoản | 16,97 | 9,05 | 0,523 | 52,0% | 77,3% |
| + ca đang khám | 16,95 | 9,03 | 0,522 | 52,0% | 77,2% |
| + lưu lượng, xả hàng đợi | 16,96 | 9,02 | 0,522 | 51,9% | 77,3% |
| + mở phòng trong ngày | 16,95 | 9,02 | 0,522 | 51,8% | 77,1% |
| Toàn bộ, thời lượng 60→140 | 16,95 | 9,01 | 0,521 | 51,8% | 77,0% |

Chênh lệch MAE của mọi cấu hình so với queue dynamics không quá +0,08 phút (đều xấu hơn nhẹ, không tốt hơn), nhỏ hơn dao động giữa các fold (ví dụ chênh lệch theo fold của "+ mở phòng" so với queue dynamics: +0,04, +0,11, +0,25, +0,02, −0,11, +0,07). Không có cơ sở để nói các feature này giúp ích.

**Hiệu chỉnh** (mô hình train trên 35 ngày nên MAE tuyệt đối cao hơn bảng trên; so sánh nội bộ raw với calibrated):

| Mô hình | Phiên bản | Phủ P50 | Phủ P80 | Độ rộng P80−P50 |
|---|---|---:|---:|---:|
| Chính thức | raw | 52,9% | 78,5% | 11,3′ |
| Chính thức | calibrated | 51,0% | 79,7% | 12,4′ |
| Queue dynamics | raw | 52,5% | 77,6% | 10,2′ |
| Queue dynamics | calibrated | 51,0% | **80,1%** | 11,6′ |

MAE gần như không đổi (17,58 → 17,57), tức hiệu chỉnh sửa độ phủ mà không làm hỏng điểm dự báo.

## 5. Vì sao feature mới không giúp

Đây là giả thuyết được hỗ trợ bởi dữ liệu, chưa phải kết luận đã kiểm chứng:

- Feature hợp lệ (không suy biến, tương quan Spearman với thời gian chờ từ −0,27 đến +0,35; mạnh nhất là số ca đứng trước ÷ tốc độ bắt đầu). Phần thông tin này đã có trong queue dynamics (số lượt bắt đầu, số ca đứng trước), nên GBM không học thêm được.
- 60→150 không đo thời gian chiếm dụng bác sĩ (W6), và 60→140 gần như trùng (median 32,0 so với 32,5 phút). Bác sĩ mở nhiều ca cùng lúc nên khó suy ra "còn bao lâu nữa đến lượt".
- Phần sai số còn lại nằm ở đuôi có cấu trúc ca (W4, W5), cần thông tin lịch chứ không phải thời lượng.

## 6. Cách trình bày mô hình

Nên viết: *"Ước lượng thời gian chờ tại thời điểm bệnh nhân vào hàng đợi bằng thông tin quan sát được lúc đó và phân phối thời lượng phục vụ lịch sử."* Không viết "dự báo chính xác thời gian chờ".

Cách đọc khoảng dự báo: **trung vị P50 là ước lượng trung tâm; P80 là cận trên sao cho khoảng 80% ca chờ không quá mức đó.** Không nên diễn giải "P50–P80" như khoảng chứa 30% ca. Về tên gọi, mô hình hiện tại là **baseline**; nhóm queue dynamics là cải tiến đã kiểm chứng trên development, chưa có kết quả Final Test.

Cảnh báo khi đọc bảng segment: độ phủ theo nhóm *thời gian chờ thực tế* bị thiên lệch vì nhóm được chọn theo kết quả. Chỉ dùng độ phủ theo phòng, giờ và số ca đứng trước (chọn theo thông tin biết trước). Theo phòng, độ phủ P80 sau hiệu chỉnh dao động 73–83%, ví dụ PK-37 chỉ 72,7%, nên hiệu chỉnh theo phòng là bước tiếp theo hợp lý.

## 7. Việc còn lại

Cần dữ liệu ngoài ba file hiện có để xử lý đuôi và bác sĩ:

1. **Lịch hẹn/ca làm việc theo phòng** (giờ mở, nghỉ trưa, ca chiều, thời điểm hẹn). Đây là ứng viên mạnh nhất cho W5.
2. **Thời điểm gán `bac_si_kham_id`** trong log `nb_dv_kham`. Nếu chứng minh được bác sĩ đã gán trước lúc vào hàng đợi, có thể dùng danh tính bác sĩ thật thay cho tài khoản.
3. Quyết định nghiệp vụ cho 25.787 ca thiếu status 40 (W8).
4. **Đo bằng nhịp bắt đầu của bác sĩ** thay vì 60→150 nếu muốn tiếp tục hướng thời lượng phục vụ (W6).
5. Đánh giá lần cuối: chỉ khi cấu hình mới được cố định trên development mới có thể quyết định dùng holdout mới, vì Final Test hiện tại đã được sử dụng một lần và không còn là dữ liệu chưa thấy.
