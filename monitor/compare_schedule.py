"""Compare schedule.json with monitor/new_schedule.json."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OLD = ROOT_DIR / "schedule.json"
DEFAULT_NEW = Path(__file__).with_name("new_schedule.json")
DEFAULT_DIFF = Path(__file__).with_name("schedule_diff.json")
DEFAULT_UPDATE_CANDIDATES = Path(__file__).with_name("update_candidates.json")

Bus = dict[str, str]
Schedule = dict[str, dict[str, list[Bus]]]
UPDATE_TARGET_ROUTES = {"to_station", "to_nakahashi"}
INVESTIGATION_ONLY_ROUTES = {"to_uni"}


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def time_stop_key(bus: Bus) -> tuple[str, str]:
    return (bus.get("time", ""), bus.get("stop", ""))


def group_by_time_stop(items: list[Bus]) -> dict[tuple[str, str], list[Bus]]:
    grouped: dict[tuple[str, str], list[Bus]] = defaultdict(list)
    for item in items:
        grouped[time_stop_key(item)].append(item)
    return dict(grouped)


def sorted_buses(items: list[Bus]) -> list[Bus]:
    return sorted(items, key=lambda item: (item.get("time", ""), item.get("stop", ""), item.get("line", "")))


def line_values(items: list[Bus]) -> list[str]:
    return sorted(item.get("line", "") for item in items)


def compare_target_day(old_items: list[Bus], new_items: list[Bus]) -> dict[str, Any] | None:
    old_groups = group_by_time_stop(old_items)
    new_groups = group_by_time_stop(new_items)
    old_keys = set(old_groups)
    new_keys = set(new_groups)

    added_keys = sorted(set(new_groups) - set(old_groups))
    removed_keys = sorted(set(old_groups) - set(new_groups))
    common_keys = sorted(old_keys & new_keys)

    line_differences = []
    for key in common_keys:
        old_lines = line_values(old_groups[key])
        new_lines = line_values(new_groups[key])
        if old_lines == new_lines:
            continue
        time, stop = key
        line_differences.append(
            {
                "time": time,
                "stop": stop,
                "old_lines": old_lines,
                "new_lines": new_lines,
                "old": sorted_buses(old_groups[key]),
                "new": sorted_buses(new_groups[key]),
            }
        )

    if not added_keys and not removed_keys and not line_differences:
        return None

    return {
        "old_count": len(old_items),
        "new_count": len(new_items),
        "matched_time_stop_count": len(common_keys),
        "added": [bus for key in added_keys for bus in sorted_buses(new_groups[key])],
        "removed": [bus for key in removed_keys for bus in sorted_buses(old_groups[key])],
        "line_differences": line_differences,
    }


def summarize_investigation_day(old_items: list[Bus], new_items: list[Bus]) -> dict[str, Any]:
    old_groups = group_by_time_stop(old_items)
    new_groups = group_by_time_stop(new_items)
    old_times = {item.get("time", "") for item in old_items}
    new_times = {item.get("time", "") for item in new_items}

    return {
        "old_count": len(old_items),
        "new_count": len(new_items),
        "matched_time_stop_count": len(set(old_groups) & set(new_groups)),
        "matched_time_count": len(old_times & new_times),
        "old_only_time_count": len(old_times - new_times),
        "new_only_time_count": len(new_times - old_times),
        "old_stops": sorted({item.get("stop", "") for item in old_items}),
        "new_stops": sorted({item.get("stop", "") for item in new_items}),
        "note": "to_uni is investigation-only because the current fetch source appears to differ from the existing schedule.json source.",
    }


def compare(old: Schedule, new: Schedule) -> dict[str, Any]:
    route_names = sorted(set(old) | set(new))
    result: dict[str, Any] = {
        "changed": False,
        "comparison_key": ["time", "stop"],
        "update_target_routes": sorted(UPDATE_TARGET_ROUTES),
        "investigation_only_routes": sorted(INVESTIGATION_ONLY_ROUTES),
        "routes": {},
        "investigation_only": {},
    }

    for route in route_names:
        day_names = sorted(set(old.get(route, {})) | set(new.get(route, {})))

        if route in INVESTIGATION_ONLY_ROUTES:
            result["investigation_only"][route] = {}
            for day in day_names:
                old_items = old.get(route, {}).get(day, [])
                new_items = new.get(route, {}).get(day, [])
                result["investigation_only"][route][day] = summarize_investigation_day(old_items, new_items)
            continue

        route_diff: dict[str, Any] = {}
        for day in day_names:
            old_items = old.get(route, {}).get(day, [])
            new_items = new.get(route, {}).get(day, [])
            day_diff = compare_target_day(old_items, new_items)
            if day_diff is None:
                continue
            result["changed"] = True
            route_diff[day] = day_diff
        if route_diff:
            result["routes"][route] = route_diff

    return result


def build_update_candidates(diff: dict[str, Any]) -> dict[str, Any]:
    candidates: dict[str, Any] = {
        "update_target_routes": sorted(UPDATE_TARGET_ROUTES),
        "comparison_key": diff.get("comparison_key", ["time", "stop"]),
        "routes": {},
        "summary": {
            "added_count": 0,
            "removed_count": 0,
        },
    }

    for route in sorted(UPDATE_TARGET_ROUTES):
        route_diff = diff.get("routes", {}).get(route, {})
        route_candidates: dict[str, Any] = {}
        for day, day_diff in route_diff.items():
            added = day_diff.get("added", [])
            removed = day_diff.get("removed", [])
            if not added and not removed:
                continue
            route_candidates[day] = {
                "added": added,
                "removed": removed,
            }
            candidates["summary"]["added_count"] += len(added)
            candidates["summary"]["removed_count"] += len(removed)
        if route_candidates:
            candidates["routes"][route] = route_candidates

    return candidates


def print_summary(diff: dict[str, Any], update_candidates: dict[str, Any]) -> None:
    if not diff["changed"]:
        print("No update-target schedule differences found.")
    else:
        print("Schedule differences found for update-target routes:")
        for route, days in diff["routes"].items():
            for day, detail in days.items():
                print(
                    f"- {route}/{day}: "
                    f"{detail['old_count']} -> {detail['new_count']} "
                    f"(+{len(detail['added'])}, -{len(detail['removed'])}, "
                    f"line-only {len(detail['line_differences'])})"
                )

    if diff["investigation_only"]:
        print("Investigation-only routes:")
        for route, days in diff["investigation_only"].items():
            for day, detail in days.items():
                print(
                    f"- {route}/{day}: "
                    f"{detail['old_count']} -> {detail['new_count']}, "
                    f"time matches {detail['matched_time_count']}"
                )

    summary = update_candidates["summary"]
    print(
        "Update candidates: "
        f"+{summary['added_count']}, -{summary['removed_count']}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare bus schedules.")
    parser.add_argument("--old", type=Path, default=DEFAULT_OLD)
    parser.add_argument("--new", type=Path, default=DEFAULT_NEW)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_DIFF)
    parser.add_argument("--candidates-output", type=Path, default=DEFAULT_UPDATE_CANDIDATES)
    args = parser.parse_args()

    old_schedule = load_json(args.old)
    new_schedule = load_json(args.new)
    diff = compare(old_schedule, new_schedule)
    update_candidates = build_update_candidates(diff)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(diff, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.candidates_output.parent.mkdir(parents=True, exist_ok=True)
    args.candidates_output.write_text(
        json.dumps(update_candidates, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print_summary(diff, update_candidates)
    print(f"Saved {args.output}")
    print(f"Saved {args.candidates_output}")
    return 1 if diff["changed"] else 0


if __name__ == "__main__":
    sys.exit(main())
