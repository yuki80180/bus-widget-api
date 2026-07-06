"""Compare schedule.json with normalized route-search results.

This script is investigation-only. It reads schedule.json and
monitor/debug/route_search_normalized.json, then writes a comparison report to
monitor/debug/route_search_compare.json. It does not update schedule.json,
bus.db, or the production API.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MONITOR_DIR = Path(__file__).resolve().parent
DEBUG_DIR = MONITOR_DIR / "debug"

DEFAULT_SCHEDULE_PATH = PROJECT_ROOT / "schedule.json"
DEFAULT_ROUTE_SEARCH_PATH = DEBUG_DIR / "route_search_normalized.json"
DEFAULT_OUTPUT_PATH = DEBUG_DIR / "route_search_compare.json"

TARGETS = (
    ("to_uni", "weekday"),
    ("to_station", "weekday"),
    ("to_nakahashi", "weekday"),
)
EXCLUDED_NORMALIZED_LINES = {
    ("to_station", "weekday"): {"49"},
}
MAX_TIME_CHANGE_CANDIDATE_MINUTES = 60

Bus = dict[str, str | None]
GroupedByKey = dict[tuple[str, str], list[Bus]]


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def normalize_text(value: object) -> str:
    return " ".join(str(value or "").split())


def normalize_optional_text(value: object) -> str | None:
    if value is None:
        return None
    return normalize_text(value)


def normalize_line_for_compare(line: str | None) -> str | None:
    if line is None:
        return None

    stripped = line.strip()
    bracket_match = re.match(r"^[^\dA-Za-z（(]*[（(]\s*([^）)]+?)\s*[）)]", stripped)
    if bracket_match:
        return bracket_match.group(1).strip()
    if stripped.isdigit():
        return stripped
    return stripped


def normalize_bus(record: dict[str, object]) -> Bus:
    return {
        "time": normalize_text(record.get("time")),
        "line": normalize_optional_text(record.get("line")),
        "stop": normalize_text(record.get("stop")),
    }


def sort_buses(items: list[Bus]) -> list[Bus]:
    return sorted(items, key=lambda item: (item["time"], item["stop"], item["line"] or ""))


def group_by_time_stop(items: list[Bus], *, label: str) -> GroupedByKey:
    grouped: defaultdict[tuple[str, str], list[Bus]] = defaultdict(list)
    for item in items:
        if not item["time"]:
            raise ValueError(f"{label} record is missing time: {item}")
        if not item["stop"]:
            raise ValueError(f"{label} record is missing stop: {item}")
        grouped[(item["time"], item["stop"])].append(item)
    return dict(grouped)


def sorted_line_values(values: set[str | None]) -> list[str | None]:
    return sorted(values, key=lambda value: "" if value is None else value)


def line_summary_from_values(values: list[str | None]) -> str | None:
    if len(values) == 1:
        return values[0]
    return " | ".join("" if value is None else value for value in values)


def line_values(items: list[Bus]) -> list[str | None]:
    return sorted_line_values({item["line"] for item in items})


def normalized_line_values(items: list[Bus]) -> list[str | None]:
    return sorted_line_values({normalize_line_for_compare(item["line"]) for item in items})


def line_summary(items: list[Bus]) -> str | None:
    return line_summary_from_values(line_values(items))


def normalized_line_summary(items: list[Bus]) -> str | None:
    return line_summary_from_values(normalized_line_values(items))


def get_schedule_records(schedule_data: dict[str, Any], route: str, day_type: str) -> list[Bus]:
    records = schedule_data.get(route, {}).get(day_type, [])
    if not isinstance(records, list):
        raise ValueError(f"schedule.json {route}/{day_type} must be a list")
    return sort_buses([normalize_bus(record) for record in records])


def get_route_search_records(route_search_data: dict[str, Any], route: str, day_type: str) -> list[Bus]:
    grouped = route_search_data.get("grouped", {})
    records = grouped.get(route, {}).get(day_type)
    if records is None:
        records = [
            item
            for item in route_search_data.get("items", [])
            if item.get("route") == route and item.get("day_type") == day_type
        ]
    if not isinstance(records, list):
        raise ValueError(f"route_search_normalized.json {route}/{day_type} must be a list")
    return sort_buses([normalize_bus(record) for record in records])


def filter_excluded_records(records: list[Bus], route: str, day_type: str) -> list[Bus]:
    excluded_lines = EXCLUDED_NORMALIZED_LINES.get((route, day_type), set())
    if not excluded_lines:
        return records
    return [
        record
        for record in records
        if normalize_line_for_compare(record["line"]) not in excluded_lines
    ]


def time_to_minutes(time: str | None) -> int | None:
    if not time:
        return None
    parts = time.split(":")
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    return hour * 60 + minute


def build_time_change_candidates(removed: list[Bus], added: list[Bus]) -> list[dict[str, Any]]:
    candidate_pairs = []
    for removed_index, existing_item in enumerate(removed):
        existing_minutes = time_to_minutes(existing_item["time"])
        if existing_minutes is None:
            continue
        existing_line = normalize_line_for_compare(existing_item["line"])
        for added_index, route_search_item in enumerate(added):
            route_search_minutes = time_to_minutes(route_search_item["time"])
            if route_search_minutes is None:
                continue
            route_search_line = normalize_line_for_compare(route_search_item["line"])
            if existing_line != route_search_line or existing_item["stop"] != route_search_item["stop"]:
                continue

            difference_minutes = abs(route_search_minutes - existing_minutes)
            if difference_minutes > MAX_TIME_CHANGE_CANDIDATE_MINUTES:
                continue
            candidate_pairs.append(
                (
                    difference_minutes,
                    existing_item["time"] or "",
                    route_search_item["time"] or "",
                    existing_item["stop"] or "",
                    existing_line or "",
                    removed_index,
                    added_index,
                    existing_line,
                    existing_item,
                    route_search_item,
                )
            )

    candidate_pairs.sort()
    used_removed = set()
    used_added = set()
    time_change_candidates = []

    for (
        difference_minutes,
        old_time,
        new_time,
        stop,
        _line_sort,
        removed_index,
        added_index,
        line,
        existing_item,
        route_search_item,
    ) in candidate_pairs:
        if removed_index in used_removed or added_index in used_added:
            continue
        used_removed.add(removed_index)
        used_added.add(added_index)
        time_change_candidates.append(
            {
                "line": line,
                "stop": stop,
                "old_time": old_time,
                "new_time": new_time,
                "difference_minutes": difference_minutes,
                "existing_item": existing_item,
                "route_search_item": route_search_item,
            }
        )

    return time_change_candidates


def compare_day(existing_records: list[Bus], route_search_records: list[Bus]) -> dict[str, Any]:
    existing_groups = group_by_time_stop(existing_records, label="schedule.json")
    route_search_groups = group_by_time_stop(
        route_search_records,
        label="route_search_normalized.json",
    )

    existing_keys = set(existing_groups)
    route_search_keys = set(route_search_groups)
    added_keys = sorted(route_search_keys - existing_keys)
    removed_keys = sorted(existing_keys - route_search_keys)
    common_keys = sorted(existing_keys & route_search_keys)

    added = [
        bus
        for key in added_keys
        for bus in sort_buses(route_search_groups[key])
    ]
    removed = [
        bus
        for key in removed_keys
        for bus in sort_buses(existing_groups[key])
    ]
    line_only = []
    time_change_candidates = build_time_change_candidates(removed, added)

    for time, stop in common_keys:
        existing_lines = line_summary(existing_groups[(time, stop)])
        route_search_lines = line_summary(route_search_groups[(time, stop)])
        existing_lines_normalized = normalized_line_values(existing_groups[(time, stop)])
        route_search_lines_normalized = normalized_line_values(route_search_groups[(time, stop)])
        if existing_lines_normalized == route_search_lines_normalized:
            continue
        line_only.append(
            {
                "time": time,
                "stop": stop,
                "existing_line": existing_lines,
                "route_search_line": route_search_lines,
                "existing_line_normalized": line_summary_from_values(existing_lines_normalized),
                "route_search_line_normalized": line_summary_from_values(route_search_lines_normalized),
            }
        )

    return {
        "existing_count": len(existing_records),
        "route_search_count": len(route_search_records),
        "added": added,
        "removed": removed,
        "line_only": line_only,
        "time_change_candidates": time_change_candidates,
        "added_count": len(added),
        "removed_count": len(removed),
        "line_only_count": len(line_only),
        "time_change_candidate_count": len(time_change_candidates),
    }


def build_comparison(schedule_path: Path, route_search_path: Path) -> dict[str, Any]:
    schedule_data = load_json(schedule_path)
    route_search_data = load_json(route_search_path)

    result: dict[str, Any] = {
        "comparison_key": ["time", "stop"],
        "line_comparison": "line_only when time and stop match but line differs",
        "routes": {},
        "summary": {
            "added_count": 0,
            "removed_count": 0,
            "line_only_count": 0,
            "time_change_candidate_count": 0,
        },
    }

    for route, day_type in TARGETS:
        existing_records = get_schedule_records(schedule_data, route, day_type)
        route_search_records = get_route_search_records(route_search_data, route, day_type)
        existing_records = filter_excluded_records(existing_records, route, day_type)
        route_search_records = filter_excluded_records(route_search_records, route, day_type)
        day_result = compare_day(existing_records, route_search_records)

        result["routes"].setdefault(route, {})[day_type] = day_result
        result["summary"]["added_count"] += day_result["added_count"]
        result["summary"]["removed_count"] += day_result["removed_count"]
        result["summary"]["line_only_count"] += day_result["line_only_count"]
        result["summary"]["time_change_candidate_count"] += day_result["time_change_candidate_count"]

    return result


def print_summary(comparison: dict[str, Any]) -> None:
    for route, day_type in TARGETS:
        day_result = comparison["routes"][route][day_type]
        print(
            f"{route}/{day_type}: "
            f"existing={day_result['existing_count']} "
            f"route_search={day_result['route_search_count']} "
            f"added={day_result['added_count']} "
            f"removed={day_result['removed_count']} "
            f"line_only={day_result['line_only_count']} "
            f"time_change_candidates={day_result['time_change_candidate_count']}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare schedule.json with normalized route-search results."
    )
    parser.add_argument("--schedule", type=Path, default=DEFAULT_SCHEDULE_PATH)
    parser.add_argument("--route-search", type=Path, default=DEFAULT_ROUTE_SEARCH_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()

    try:
        comparison = build_comparison(args.schedule, args.route_search)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print_summary(comparison)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
