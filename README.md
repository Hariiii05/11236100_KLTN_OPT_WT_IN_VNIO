# Opt-WT-in-VNIO

Pipeline nghiên cứu dự báo thời gian chờ khám từ dữ liệu HIS. Repository tách rõ dữ liệu, mã xử lý, notebook, kiểm thử, kết quả chính thức và thử nghiệm development.

## Bắt đầu nhanh

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

Luồng xử lý và điều kiện target được mô tả tại [`docs/pipeline_overview.md`](docs/pipeline_overview.md). Kết quả mô hình chính thức nằm trong [`docs/final_model_evaluation.md`](docs/final_model_evaluation.md).

## Cấu trúc

```text
data/       dữ liệu raw, intermediate và processed (không commit)
pipeline/   mã nguồn pipeline chính thức và nhánh legacy
notebooks/  EDA và tạo model dataset
tests/      regression tests
results/    audit, EDA, Final Test đã khóa và thử nghiệm development
docs/       tài liệu và truy vấn SQL
logs/       log cục bộ/lưu trữ
```

## Lưu ý dữ liệu

Dữ liệu HIS, bảng theo từng lượt khám và prediction theo từng dòng được loại khỏi Git bằng `.gitignore`. Không đưa dữ liệu định danh hoặc dữ liệu bệnh nhân lên repository.
