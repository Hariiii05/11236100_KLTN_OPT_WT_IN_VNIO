"""Ingest HIS extracts, run quality checks, and build a normalized event log."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]


INPUT_FILES = {
    "encounter": "01_encounter.csv",
    "reception": "02_reception.csv",
    "exam": "03_exam.csv",
    "journey": "04_service_journey.csv",
    "status_log": "05_exam_status_log.csv",
}

TIME_COLUMNS = [
    "thoi_gian_vao_vien",
    "thoi_gian_ra_vien",
    "thoi_gian_lay_so",
    "thoi_gian_hoan_thanh",
    "thoi_gian_chi_dinh",
    "thoi_gian_tiep_nhan",
    "thoi_gian_thuc_hien",
    "thoi_gian_co_ket_qua",
    "thoi_gian_ket_luan",
    "thoi_gian_xac_nhan_khong_thuc_hien",
    "thoi_gian_thay_doi_ban_ghi",
]

STATUS_GROUPS = {
    20: "waiting",
    30: "waiting",
    40: "waiting",
    50: "abandoned",
    60: "in_service",
    70: "in_service",
    100: "pending_conclusion",
    110: "pending_conclusion",
    120: "pending_conclusion",
    130: "abandoned",
    140: "concluding",
    150: "completed",
}

KHOA_SCOPE = {152, 153, 155, 156, 157, 158, 159, 160, 161}


def _read_prefix(path: Path, size: int = 2_000_000) -> bytes:
    """Read only the bytes needed for format/encoding detection."""
    with path.open("rb") as stream:
        return stream.read(size)


def detect_encoding(path: Path) -> str:
    """Choose a Vietnamese-compatible encoding using a strict decode probe."""
    sample = _read_prefix(path)
    for encoding in ("utf-8-sig", "utf-8", "cp1258", "cp1252"):
        try:
            sample.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    # Preserve row structure for exports with isolated invalid bytes in text fields.
    return "utf-8"


def read_extract(
    path: Path, *, usecols: list[str] | None = None, **read_kwargs: Any
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Read both real CSVs and XLSX workbooks with a .csv extension."""
    signature = _read_prefix(path, 2)
    if signature == b"PK":
        frame = pd.read_excel(path, engine="openpyxl", usecols=usecols, **read_kwargs)
        metadata = {"format": "xlsx", "encoding": "not_applicable"}
    else:
        encoding = detect_encoding(path)
        frame = pd.read_csv(
            path,
            encoding=encoding,
            encoding_errors="replace",
            low_memory=False,
            usecols=usecols,
            **read_kwargs,
        )
        metadata = {"format": "csv", "encoding": encoding}

    frame.columns = [str(column).strip() for column in frame.columns]
    for column in TIME_COLUMNS:
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], errors="coerce")
    return frame, metadata


def key_quality(frame: pd.DataFrame, key: str) -> dict[str, int]:
    if key not in frame.columns:
        return {"null": -1, "duplicate_rows": -1}
    return {
        "null": int(frame[key].isna().sum()),
        "duplicate_rows": int(frame[key].duplicated().sum()),
    }


def add_event(events: list[pd.DataFrame], source: pd.DataFrame, event_name: str,
              time_column: str, source_table: str) -> None:
    if time_column not in source.columns:
        return
    candidates = {
        "nb_dot_dieu_tri_id": ("nb_dot_dieu_tri_id",),
        "nb_dich_vu_id": ("nb_dich_vu_id",),
        "room_id": ("phong_thuc_hien_id", "phong_id"),
        "department_id": ("khoa_thuc_hien_id", "khoa_id"),
        "staff_id": ("bac_si_kham_id", "bac_si_ket_luan_id"),
        "source_status": ("trang_thai",),
    }
    available = {
        output_name: next((name for name in names if name in source.columns), None)
        for output_name, names in candidates.items()
    }
    selected_columns = [name for name in available.values() if name is not None]
    selected = source[selected_columns + [time_column]].copy()
    selected = selected.dropna(subset=[time_column])
    selected = selected.rename(
        columns={name: output for output, name in available.items() if name is not None}
    ).rename(columns={time_column: "event_time"})
    selected["event_name"] = event_name
    selected["source_table"] = source_table
    for column in ("nb_dot_dieu_tri_id", "nb_dich_vu_id", "room_id", "department_id",
                   "staff_id", "source_status"):
        if column not in selected:
            selected[column] = pd.NA
    events.append(selected)


def build_events(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    events: list[pd.DataFrame] = []
    add_event(events, frames["encounter"], "encounter_arrived", "thoi_gian_vao_vien", "encounter")
    add_event(events, frames["reception"], "reception_number_taken", "thoi_gian_lay_so", "reception")
    add_event(events, frames["reception"], "reception_completed", "thoi_gian_hoan_thanh", "reception")
    add_event(events, frames["journey"], "service_ordered", "thoi_gian_chi_dinh", "journey")
    add_event(events, frames["journey"], "service_received", "thoi_gian_tiep_nhan", "journey")
    add_event(events, frames["journey"], "service_started", "thoi_gian_thuc_hien", "journey")
    add_event(events, frames["journey"], "service_result_available", "thoi_gian_co_ket_qua", "journey")
    add_event(events, frames["journey"], "exam_concluded", "thoi_gian_ket_luan", "journey")

    status_log = frames["status_log"].copy()
    if "trang_thai" in status_log.columns:
        status_log["status_group"] = status_log["trang_thai"].map(STATUS_GROUPS).fillna("needs_mapping")
        status_log["event_name"] = "status_" + status_log["status_group"]
        status_log["source_table"] = "status_log"
        status_log = status_log.rename(columns={
            "thoi_gian_thay_doi_ban_ghi": "event_time",
            "trang_thai": "source_status",
        })
        for column in (
            "nb_dot_dieu_tri_id",
            "nb_dich_vu_id",
            "room_id",
            "department_id",
            "staff_id",
        ):
            if column not in status_log:
                status_log[column] = pd.NA
        events.append(status_log[["nb_dot_dieu_tri_id", "nb_dich_vu_id", "event_name",
                                  "event_time", "room_id", "department_id", "staff_id",
                                  "source_status", "source_table"]].dropna(subset=["event_time"]))

    result = pd.concat(events, ignore_index=True) if events else pd.DataFrame()
    if result.empty:
        return result
    result["event_time"] = pd.to_datetime(result["event_time"], errors="coerce")
    # Deduplicate exact events only. Encounter/reception events do not have a
    # service ID, so a service-level subset would incorrectly merge distinct
    # encounters that happen to share the same timestamp.
    result = result.drop_duplicates()
    return result.sort_values(["nb_dich_vu_id", "event_time", "event_name"], na_position="last").reset_index(drop=True)


def quality_report(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    report: list[dict] = []
    keys = {
        "encounter": "nb_dot_dieu_tri_id",
        "reception": "lay_so_tiep_don_id",
        "exam": "nb_dich_vu_id",
        "journey": "nb_dich_vu_id",
        "status_log": "nb_dich_vu_id",
    }
    for name, frame in frames.items():
        key = keys[name]
        quality = key_quality(frame, key)
        report.append({"check": "row_count", "dataset": name, "value": len(frame), "detail": ""})
        report.append({"check": "key_null", "dataset": name, "value": quality["null"], "detail": key})
        report.append({"check": "key_duplicate_rows", "dataset": name, "value": quality["duplicate_rows"], "detail": key})
        for column in TIME_COLUMNS:
            if column in frame:
                report.append({"check": "time_null", "dataset": name, "value": int(frame[column].isna().sum()), "detail": column})

    time_pairs = {
        "encounter_arrival_discharge": ("encounter", "thoi_gian_vao_vien", "thoi_gian_ra_vien"),
        "reception_arrival_number": ("reception", "thoi_gian_vao_vien", "thoi_gian_lay_so"),
        "reception_number_complete": ("reception", "thoi_gian_lay_so", "thoi_gian_hoan_thanh"),
        "service_order_receive": ("journey", "thoi_gian_chi_dinh", "thoi_gian_tiep_nhan"),
        "service_receive_start": ("journey", "thoi_gian_tiep_nhan", "thoi_gian_thuc_hien"),
    }
    for check, (dataset, start, end) in time_pairs.items():
        frame = frames[dataset]
        if start in frame and end in frame:
            invalid = int((frame[end].notna() & frame[start].notna() & (frame[end] < frame[start])).sum())
            report.append({"check": "negative_duration", "dataset": dataset, "value": invalid, "detail": f"{start} -> {end}"})

    encounter_ids = set(frames["encounter"].get("nb_dot_dieu_tri_id", pd.Series(dtype="object")).dropna())
    for name in ("reception", "exam", "journey", "status_log"):
        values = frames[name].get("nb_dot_dieu_tri_id", pd.Series(dtype="object")).dropna()
        matched = int(values.isin(encounter_ids).sum())
        report.append({"check": "encounter_join", "dataset": name, "value": matched,
                       "detail": f"{len(values)} non-null keys; unmatched={len(values) - matched}"})

    exam_ids = set(frames["exam"].get("nb_dich_vu_id", pd.Series(dtype="object")).dropna())
    for name in ("journey", "status_log"):
        values = frames[name].get("nb_dich_vu_id", pd.Series(dtype="object")).dropna()
        matched = int(values.isin(exam_ids).sum())
        report.append({"check": "exam_join", "dataset": name, "value": matched,
                       "detail": f"{len(values)} non-null keys; unmatched={len(values) - matched}"})

    for name, frame in frames.items():
        if "doi_tuong_kcb" in frame:
            values = frame["doi_tuong_kcb"].dropna()
            report.append({"check": "doi_tuong_kcb_values", "dataset": name, "value": values.nunique(),
                           "detail": json.dumps(sorted(values.astype(str).unique().tolist()), ensure_ascii=False)})
        if "khoa_id" in frame:
            values = set(pd.to_numeric(frame["khoa_id"], errors="coerce").dropna().astype(int))
            report.append({"check": "khoa_scope", "dataset": name, "value": len(values & KHOA_SCOPE),
                           "detail": f"in_scope={sorted(values & KHOA_SCOPE)}; out_of_scope={sorted(values - KHOA_SCOPE)[:20]}"})
    return pd.DataFrame(report)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=PROJECT_ROOT / "data" / "raw")
    parser.add_argument(
        "--processed-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "processed",
    )
    parser.add_argument(
        "--audit-dir",
        type=Path,
        default=PROJECT_ROOT / "results" / "audit",
    )
    args = parser.parse_args()
    args.processed_dir.mkdir(parents=True, exist_ok=True)
    args.audit_dir.mkdir(parents=True, exist_ok=True)

    frames: dict[str, pd.DataFrame] = {}
    metadata = {}
    for name, filename in INPUT_FILES.items():
        print(f"[ingest] reading {name}: {filename}", flush=True)
        frame, file_metadata = read_extract(args.input_dir / filename)
        frames[name] = frame
        metadata[name] = file_metadata
        print(f"[ingest] loaded {name}: {len(frame):,} rows", flush=True)

    print("[quality] creating report", flush=True)
    quality_report(frames).to_csv(
        args.audit_dir / "data_quality_report.csv",
        index=False,
        encoding="utf-8-sig",
    )
    print("[events] creating normalized event log", flush=True)
    event_log_path = args.processed_dir / "event_log.csv"
    build_events(frames).to_csv(
        event_log_path,
        index=False,
        encoding="utf-8-sig",
    )
    metadata_path = args.audit_dir / "ingestion_metadata.json"
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[done] event log: {event_log_path}", flush=True)
    print(f"[done] audit outputs: {args.audit_dir}", flush=True)


if __name__ == "__main__":
    main()
