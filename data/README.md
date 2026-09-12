# Dữ liệu cục bộ

Thư mục này không được đưa dữ liệu HIS lên Git.

```text
raw/           năm file trích xuất nguyên bản
intermediate/  CSV đã chuẩn hóa để audit
processed/     event log, target, feature và model dataset
```

Các file chính trong `processed/`:

- `event_log.csv`
- `exam_wait_target_all.csv`
- `exam_wait_target_valid.csv`
- `baseline_features.csv`
- `model_dataset.csv`

Xem điều kiện target và thứ tự tái tạo tại [`../docs/pipeline_overview.md`](../docs/pipeline_overview.md).
