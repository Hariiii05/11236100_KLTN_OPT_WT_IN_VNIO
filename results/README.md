# Kết quả pipeline

```text
audit/                    báo cáo chất lượng dữ liệu và target
eda/                      biểu đồ, bảng và tóm tắt EDA
final_model/              validation, model và Final Test đã khóa
development_experiments/  thử nghiệm chỉ trên development folds
archive/                  artifact legacy, không dùng trong luồng chính
```

Không chạy lại Final Test: khóa nằm tại `final_model/FINAL_TEST_EVALUATED.json`.

`final_model/selected_model.json` và `eda/eda_summary.json` giữ đường dẫn input tuyệt đối tại thời điểm artifact được tạo. Đây là provenance lịch sử; đường dẫn runtime hiện tại đã được chuẩn hóa trong code thành `data/processed/model_dataset.csv` và `data/processed/exam_wait_target_all.csv`. Các file lịch sử này không được sửa trong lần refactor để bảo toàn checksum.
