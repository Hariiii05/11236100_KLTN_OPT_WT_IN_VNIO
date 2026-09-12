"""Create leakage-aware baseline features for valid waiting-time targets."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from prepare_his_events import read_extract


def events_before(values: np.ndarray, times: np.ndarray, window_minutes: int) -> np.ndarray:
    left = np.searchsorted(times, values - np.timedelta64(window_minutes, "m"), side="left")
    right = np.searchsorted(times, values, side="left")
    return right - left


def active_queue_size(frame: pd.DataFrame) -> pd.Series:
    """Count earlier orders in the same room that have not started yet."""
    result = pd.Series(0, index=frame.index, dtype="int64")
    for _, group in frame.dropna(subset=["room_id_ordered"]).groupby("room_id_ordered"):
        group = group.sort_values("ordered_at")
        ordered = group["ordered_at"].to_numpy(dtype="datetime64[ns]")
        received = group["received_at"].dropna().sort_values().to_numpy(dtype="datetime64[ns]")
        prior_orders = np.searchsorted(ordered, ordered, side="left")
        prior_starts = np.searchsorted(received, ordered, side="right")
        result.loc[group.index] = np.maximum(prior_orders - prior_starts, 0)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=Path("03_output"))
    parser.add_argument("--data-dir", type=Path, default=Path("01_data_input"))
    parser.add_argument("--output-dir", type=Path, default=Path("03_output"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    target = pd.read_csv(args.input_dir / "waiting_target.csv", encoding="utf-8-sig",
                         parse_dates=["ordered_at", "received_at"])
    target = target[target["target_valid"]].copy()
    target = target.sort_values("ordered_at").reset_index(drop=True)

    encounter_columns = [
        "nb_dot_dieu_tri_id", "gioi_tinh", "ngay_sinh", "chi_nam_sinh",
        "doi_tuong", "loai_doi_tuong_id", "cap_cuu", "kham_suc_khoe",
        "arrival_hour", "day_of_week", "arrival_month", "is_weekend",
    ]
    encounter, _ = read_extract(args.data_dir / "01_encounter.csv", usecols=encounter_columns)
    target = target.merge(encounter, on="nb_dot_dieu_tri_id", how="left")
    print("[features] reading exam extract", flush=True)
    exam_columns = ["nb_dich_vu_id", "bac_si_kham_id", "uu_tien", "cap_cuu"]
    exam, _ = read_extract(args.data_dir / "03_exam.csv", usecols=exam_columns)
    print(f"[features] loaded exam extract: {len(exam):,} rows", flush=True)
    exam_columns = [column for column in exam_columns if column in exam.columns]
    exam = exam[exam_columns].drop_duplicates("nb_dich_vu_id", keep="first")
    target = target.merge(exam, on="nb_dich_vu_id", how="left", suffixes=("", "_exam"))
    print("[features] merged exam fields", flush=True)
    if "cap_cuu_exam" in target:
        target["cap_cuu"] = target["cap_cuu_exam"]
        target = target.drop(columns=["cap_cuu_exam"])
    target["waiting_minutes"] = target["waiting_minutes"].astype(float)

    order_times = target["ordered_at"].dropna().sort_values().values.astype("datetime64[ns]")
    values = target["ordered_at"].values.astype("datetime64[ns]")
    for minutes in (15, 30, 60):
        target[f"arrivals_last_{minutes}m"] = events_before(values, order_times, minutes)

    target["patients_ahead_same_room"] = active_queue_size(target)

    target["target_date"] = target["ordered_at"].dt.date.astype(str)
    target["target_hour"] = target["ordered_at"].dt.hour
    target = target.drop(columns=["target_date"])
    target = target.rename(columns={
        "room_id_ordered": "phong_thuc_hien_id",
        "department_id_ordered": "khoa_thuc_hien_id",
    })
    if "staff_id_ordered" in target:
        exam_doctor = target.get(
            "bac_si_kham_id", pd.Series(pd.NA, index=target.index, dtype="object")
        )
        target["bac_si_kham_id"] = target["staff_id_ordered"].combine_first(
            exam_doctor
        )
    feature_columns = [
        "nb_dot_dieu_tri_id", "nb_dich_vu_id", "ordered_at", "received_at",
        "waiting_minutes", "target_valid", "phong_thuc_hien_id",
        "khoa_thuc_hien_id", "bac_si_kham_id", "gioi_tinh", "ngay_sinh",
        "chi_nam_sinh", "doi_tuong", "loai_doi_tuong_id", "cap_cuu",
        "kham_suc_khoe", "arrival_hour", "day_of_week", "arrival_month",
        "is_weekend", "target_hour", "arrivals_last_15m", "arrivals_last_30m",
        "arrivals_last_60m", "patients_ahead_same_room",
        "uu_tien",
    ]
    target[feature_columns].to_csv(args.output_dir / "model_features_baseline.csv",
                                    index=False, encoding="utf-8-sig")
    summary = pd.DataFrame([
        {"metric": "feature_rows", "value": len(target)},
        {"metric": "missing_room", "value": int(target["phong_thuc_hien_id"].isna().sum())},
        {"metric": "missing_department", "value": int(target["khoa_thuc_hien_id"].isna().sum())},
        {"metric": "missing_doctor", "value": int(target["bac_si_kham_id"].isna().sum())},
        {"metric": "missing_encounter_match", "value": int(target["gioi_tinh"].isna().sum())},
    ])
    summary.to_csv(args.output_dir / "model_features_summary.csv", index=False, encoding="utf-8-sig")
    print(summary.to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
