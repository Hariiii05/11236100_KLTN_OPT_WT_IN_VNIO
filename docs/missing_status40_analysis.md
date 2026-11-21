# Phân tích bổ sung: dịch vụ thiếu status 40

Tài liệu này giải thích phân tích chỉ-đọc về các dịch vụ **có status 60 nhưng thiếu status 40**, nhóm làm mô hình mất phạm vi lớn nhất (25.787 dịch vụ, 15,15%). Phân tích **không thay đổi** quy tắc tạo target, cohort, đặc trưng hay mô hình V3, và **không đánh giá lại holdout**: holdout V3 đã khóa (`HOLDOUT_EVALUATED.json`), nên mọi thay đổi về đặc trưng hoặc target phải là một phiên bản mới (V4) với holdout mới.

## Chạy

```powershell
.\.venv\Scripts\python.exe -m pipeline.analyze_missing_queue_status      # ~1 phút
.\.venv\Scripts\python.exe -m unittest tests.test_missing_queue_status
```

Đầu vào: `data/processed/exam_wait_target_all.csv`, `data/intermediate/05_exam_status_log.csv`, `results/unified_pipeline_v3/holdout_patient_outputs.csv` (chỉ đọc). Đầu ra: `results/missing_status40_analysis/` (không ghi vào thư mục V3).

## Mốc thời gian (để tránh nhầm)

`waiting_minutes` = status 60 **đầu tiên** − status 40 **cuối cùng** trước đó (code: `audit_and_build_exam_target.py`). Chỉ KB008/phòng H401 dùng status 20, và V3 đã loại nhóm này. Khoảng **20→60** không phải target; nó gồm cả thanh toán và các bước tiền khám.

## Kết quả chính (dữ liệu 01/04–31/07/2026)

| Câu hỏi | Kết quả | File |
|---|---|---|
| Phạm vi | 170.216 dịch vụ → 141.519 có target (83,14%) → cohort V3 138.591 (**81,42%**) sau khi loại KB008 (2.898) và ca >24 giờ (30) | `coverage_funnel.csv` |
| Tập trung ở đâu | 5 phòng chiếm 50,9% và 10 phòng chiếm 68,9% số ca thiếu; PK-22 50,9%, C502 94,1%, PK-37 37,7%. Phòng Glôcôm không đồng nhất: PK-3 4,2%, PK-4 1,7%, PK-38 29,9%, C502 94,1% | `room_coverage.csv` |
| Xu hướng | Thiếu tăng từ 10,1% (tuần đầu) lên khoảng 16–19% từ tháng 6; thứ Hai 20,6%, cuối tuần 1,7–3,1% | `missing_rate_trend.csv` |
| Có phải "bỏ qua đo thị lực"? | **Không được ủng hộ**: 88,0% ca thiếu có status 60 ghi từ màn hình `do-thi-luc` (nhóm có 40: 90,3%); 30/31 phòng đủ cỡ mẫu có trung vị 20→60 của nhóm thiếu lớn hơn trung vị 40→60 của nhóm có; status 40 do nhiều màn hình tạo (đo thị lực 41,0%, thu ngân 38,9%, khám bệnh 10,5%) | `start_screen_by_group.csv`, `within_room_timing.csv`, `status40_trigger_screen.csv` |
| Ảnh hưởng đến MAE holdout | 9,52 → **9,96** phút (+0,44) khi trọng số lại theo cơ cấu phòng thực tế; 10,45 / 10,94 / 11,93 nếu sai số trên ca thiếu gấp 1,25 / 1,5 / 2 lần | `holdout_mae_by_room.csv`, `missing_status40_summary.json` |
| Ảnh hưởng đến đặc trưng tải | Số lượt đến 30 phút bị đếm thiếu 10,1%, số người đứng trước thiếu 24,5% (ước lượng gần đúng, dùng status 20 làm mốc vào của ca thiếu) | `load_feature_undercount_by_room.csv` |

Số theo phòng trong luận văn (C502 94,1%, PK-22 51,3%, PK-19 48,7%, PK-40 47,2%) là **tỷ lệ dịch vụ không có target hợp lệ trên mọi dịch vụ của phòng** (kể cả không có status 60). Tính riêng thiếu status 40 trên các ca đã khám thì là 94,1%, 50,9%, 47,4%, 41,8%. `room_coverage.csv` có cả hai cột.

## Giả định và giới hạn

- Trọng số lại MAE giả định **trong cùng một phòng** ca thiếu status 40 dự báo được như ca có; bảng độ nhạy nới giả định này bằng hệ số nhân. Đây không phải đánh giá mô hình trên ca thiếu (các ca đó không có nhãn).
- Mốc vào hàng đợi của ca thiếu được lấy gần đúng bằng status 20 cuối cùng. Status 20 sớm hơn mốc thật, nên số người đứng trước có xu hướng bị thổi phồng còn số lượt đến chỉ là xấp xỉ; các tỷ lệ "đếm thiếu" là ước lượng cỡ độ lớn, không phải giá trị chính xác.
- Tên màn hình lấy từ đường dẫn `uri` trong log (`do-thi-luc`, `kham-benh`, `thu-ngan`); ý nghĩa nghiệp vụ là suy luận từ tên, **chưa được bệnh viện xác nhận**. Cơ chế thiếu status 40 cần bộ phận quản trị HIS xác nhận.
- 2.910 dịch vụ không có status 60 (2.119 chỉ có status 20; 791 đã có status 40 nhưng không được khám) vẫn bị loại; đó là quan sát bị chặn (người bệnh bỏ về/hủy), làm đuôi chờ dài bị ước lượng thấp.

## Hướng cho V4 (chưa thực hiện, để không đụng V3)

1. Tính đặc trưng tải hàng đợi từ **mọi** dịch vụ có status 20 (mốc vào = status 40 nếu có, ngược lại status 20); chỉ nhãn mới loại ca.
2. Nếu cần dự báo cho các phòng thiếu status 40: huấn luyện **mô hình riêng** cho target 20→60 ("từ tiếp đón đến bắt đầu khám"), có tên và đánh giá riêng, không trộn vào target 40→60.
3. Xác nhận với IT/HIS vì sao status 40 không được ghi ở các phòng nêu trên.
4. Đánh giá V4 trên holdout mới (dữ liệu từ 08/2026).
