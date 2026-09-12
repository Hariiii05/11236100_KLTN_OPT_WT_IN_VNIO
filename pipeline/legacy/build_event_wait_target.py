"""Build the service-level waiting-time target from the normalized event log."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]


EVENT_COLUMNS = [
    "nb_dot_dieu_tri_id",
    "nb_dich_vu_id",
    "event_name",
    "event_time",
    "room_id",
    "department_id",
    "staff_id",
    "source_status",
]

WANTED_EVENTS = {
    "service_ordered",
    "service_received",
    "status_abandoned",
    "status_completed",
}


def _event_candidates(event_path: Path) -> pd.DataFrame:
    """Keep only per-service candidates needed for the final aggregations."""
    parts: list[pd.DataFrame] = []
    for chunk in pd.read_csv(
        event_path,
        usecols=EVENT_COLUMNS,
        chunksize=100_000,
        encoding="utf-8-sig",
        low_memory=False,
    ):
        filtered = chunk.loc[chunk["event_name"].isin(WANTED_EVENTS)].copy()
        if filtered.empty:
            continue
        filtered["event_time"] = pd.to_datetime(filtered["event_time"], errors="coerce")
        filtered = filtered.dropna(subset=["nb_dich_vu_id", "event_time"])
        if filtered.empty:
            continue

        # Earliest ordered/received and latest outcome are sufficient. Reducing each
        # chunk first substantially lowers peak memory for large event logs.
        starts = filtered[filtered["event_name"].isin({"service_ordered", "service_received"})]
        starts = starts.sort_values("event_time").drop_duplicates(
            ["nb_dich_vu_id", "event_name"], keep="first"
        )
        outcomes = filtered[filtered["event_name"].isin({"status_abandoned", "status_completed"})]
        outcomes = outcomes.sort_values("event_time").drop_duplicates("nb_dich_vu_id", keep="last")
        parts.extend(part for part in (starts, outcomes) if not part.empty)

    if not parts:
        empty = pd.DataFrame(columns=EVENT_COLUMNS)
        empty["event_time"] = pd.to_datetime(empty["event_time"])
        return empty
    return pd.concat(parts, ignore_index=True)


def build_target(event_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    events = _event_candidates(event_path)
    ordered = events[events["event_name"] == "service_ordered"].sort_values("event_time")
    received = events[events["event_name"] == "service_received"].sort_values("event_time")
    ordered = ordered.drop_duplicates("nb_dich_vu_id", keep="first").rename(
        columns={
            "event_time": "ordered_at",
            "room_id": "room_id_ordered",
            "department_id": "department_id_ordered",
            "staff_id": "staff_id_ordered",
        }
    )
    received = received.drop_duplicates("nb_dich_vu_id", keep="first").rename(
        columns={
            "event_time": "received_at",
            "room_id": "room_id_received",
            "department_id": "department_id_received",
            "staff_id": "staff_id_received",
        }
    )
    target = ordered[[
        "nb_dot_dieu_tri_id", "nb_dich_vu_id", "ordered_at", "room_id_ordered",
        "department_id_ordered", "staff_id_ordered",
    ]].merge(
        received[["nb_dich_vu_id", "received_at", "room_id_received",
                  "department_id_received", "staff_id_received"]],
        on="nb_dich_vu_id", how="left",
    )
    target["waiting_minutes"] = (
        target["received_at"] - target["ordered_at"]
    ).dt.total_seconds() / 60
    target["target_valid"] = target["waiting_minutes"].ge(0)
    target["target_invalid_reason"] = ""
    target.loc[target["received_at"].isna(), "target_invalid_reason"] = "missing_received_at"
    target.loc[target["waiting_minutes"].lt(0), "target_invalid_reason"] = "negative_waiting_time"
    target.loc[target["target_valid"], "target_invalid_reason"] = ""
    target = target.sort_values(["ordered_at", "nb_dich_vu_id"]).reset_index(drop=True)

    outcome = events[events["event_name"].isin({"status_abandoned", "status_completed"})]
    outcome = outcome.sort_values("event_time").drop_duplicates("nb_dich_vu_id", keep="last")
    outcome = outcome[["nb_dich_vu_id", "event_name"]].rename(columns={"event_name": "last_outcome"})
    target = target.merge(outcome, on="nb_dich_vu_id", how="left")
    summary = pd.DataFrame([
        {"metric": "ordered_services", "value": len(target)},
        {"metric": "valid_targets", "value": int(target["target_valid"].sum())},
        {"metric": "missing_received_at", "value": int(target["received_at"].isna().sum())},
        {"metric": "negative_waiting_time", "value": int(target["waiting_minutes"].lt(0).sum())},
        {"metric": "abandoned_or_completed_outcome", "value": int(target["last_outcome"].notna().sum())},
    ])
    return target, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=PROJECT_ROOT / "data" / "processed" / "event_log.csv",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "results" / "archive" / "legacy_event_target",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    target, summary = build_target(args.input)
    target.to_csv(args.output_dir / "waiting_target.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(args.output_dir / "waiting_target_summary.csv", index=False, encoding="utf-8-sig")
    print(summary.to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
