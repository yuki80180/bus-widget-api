"""Compare extracted route-search results with schedule.json.

This script is investigation-only. It reads local JSON files and writes a diff
summary; it never updates schedule.json, bus.db, or the production API.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROUTE_KEY = "to_nakahashi"
DAY_TYPE = "weekday"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MONITOR_DIR = Path(__file__).resolve().parent
DEFAULT_EXTRACTED_PATH = MONITOR_DIR / "debug" / "04_uni_to_nakahashi_weekday_extracted.json"
DEFAULT_SCHEDULE_PATH = PROJECT_ROOT / "schedule.json"
DEFAULT_OUTPUT_PATH = MONITOR_DIR / "route_search_diff.json"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def normalize_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_via(value: object) -> str:
    via = normalize_text(value)
    via = via.replace(" 経由", "").replace("経由", "").strip()
    if via == "久安三丁目":
        return "久安"
    return via


def normalize_destination(value: object) -> str:
    destination = normalize_text(value)
    destination = destination.replace(" ゆき", "").replace("ゆき", "").strip()
    if destination.endswith("行"):
        return destination
    return destination + "行"


def build_schedule_line(record: dict[str, object]) -> str:
    route_no = normalize_text(record.get("route_no"))
    via = normalize_via(record.get("via"))
    destination = normalize_destination(record.get("destination"))

    if via and destination:
        body = f"{via}・{destination}"
    else:
        body = via or destination
    return f"({route_no}) {body}"


def normalize_extracted_record(record: dict[str, object]) -> dict[str, object]:
    return {
        "time": normalize_text(record.get("departure_time")),
        "line": build_schedule_line(record),
        "stop": normalize_text(record.get("departure_stop_code")),
        "source": record,
    }


def normalize_schedule_record(record: dict[str, object]) -> dict[str, str]:
    return {
        "time": normalize_text(record.get("time")),
        "line": normalize_text(record.get("line")),
        "stop": normalize_text(record.get("stop")),
    }


def index_by_time(records: list[dict[str, object]], *, label: str) -> dict[str, dict[str, object]]:
    indexed: dict[str, dict[str, object]] = {}
    duplicates: list[str] = []
    for record in records:
        time = normalize_text(record.get("time"))
        if not time:
            raise ValueError(f"{label} record is missing time: {record}")
        if time in indexed:
            duplicates.append(time)
        indexed[time] = record
    if duplicates:
        raise ValueError(f"{label} has duplicate time values: {', '.join(sorted(set(duplicates)))}")
    return indexed


def compare_records(
    existing_records: list[dict[str, object]], extracted_records: list[dict[str, object]]
) -> dict[str, object]:
    existing_by_time = index_by_time(existing_records, label="schedule.json")
    extracted_by_time = index_by_time(extracted_records, label="extracted.json")

    added: list[dict[str, object]] = []
    removed: list[dict[str, object]] = []
    changed: list[dict[str, object]] = []
    unchanged_count = 0

    for time in sorted(extracted_by_time):
        extracted = extracted_by_time[time]
        existing = existing_by_time.get(time)
        if existing is None:
            added.append({"time": time, "record": extracted})
            continue

        if existing["line"] == extracted["line"] and existing["stop"] == extracted["stop"]:
            unchanged_count += 1
        else:
            changed.append(
                {
                    "time": time,
                    "existing": existing,
                    "extracted": extracted,
                }
            )

    for time in sorted(existing_by_time):
        if time not in extracted_by_time:
            removed.append({"time": time, "record": existing_by_time[time]})

    return {
        "route_key": ROUTE_KEY,
        "day_type": DAY_TYPE,
        "existing_count": len(existing_records),
        "extracted_count": len(extracted_records),
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged_count": unchanged_count,
    }


def build_diff(extracted_path: Path, schedule_path: Path) -> dict[str, object]:
    extracted_json = load_json(extracted_path)
    schedule_json = load_json(schedule_path)

    extracted_records = [
        normalize_extracted_record(record)
        for record in extracted_json.get("records", [])
    ]
    existing_records = [
        normalize_schedule_record(record)
        for record in schedule_json[ROUTE_KEY][DAY_TYPE]
    ]

    return compare_records(existing_records, extracted_records)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare extracted route-search results with schedule.json without updating either."
    )
    parser.add_argument("--extracted", type=Path, default=DEFAULT_EXTRACTED_PATH)
    parser.add_argument("--schedule", type=Path, default=DEFAULT_SCHEDULE_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()

    diff = build_diff(args.extracted, args.schedule)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(diff, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"existing_count: {diff['existing_count']}")
    print(f"extracted_count: {diff['extracted_count']}")
    print(f"added: {len(diff['added'])}")
    print(f"removed: {len(diff['removed'])}")
    print(f"changed: {len(diff['changed'])}")
    print(f"unchanged_count: {diff['unchanged_count']}")
    print(f"wrote {args.output.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
