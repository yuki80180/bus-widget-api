"""Compare schedule.json with monitor/new_schedule.json."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OLD = ROOT_DIR / "schedule.json"
DEFAULT_NEW = Path(__file__).with_name("new_schedule.json")
DEFAULT_DIFF = Path(__file__).with_name("schedule_diff.json")

Bus = dict[str, str]
Schedule = dict[str, dict[str, list[Bus]]]


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def bus_key(bus: Bus) -> tuple[str, str, str]:
    return (bus.get("time", ""), bus.get("line", ""), bus.get("stop", ""))


def compare(old: Schedule, new: Schedule) -> dict[str, Any]:
    route_names = sorted(set(old) | set(new))
    result: dict[str, Any] = {"changed": False, "routes": {}}

    for route in route_names:
        day_names = sorted(set(old.get(route, {})) | set(new.get(route, {})))
        route_diff: dict[str, Any] = {}
        for day in day_names:
            old_items = old.get(route, {}).get(day, [])
            new_items = new.get(route, {}).get(day, [])
            old_map = {bus_key(item): item for item in old_items}
            new_map = {bus_key(item): item for item in new_items}

            added_keys = sorted(set(new_map) - set(old_map))
            removed_keys = sorted(set(old_map) - set(new_map))
            if not added_keys and not removed_keys:
                continue

            result["changed"] = True
            route_diff[day] = {
                "old_count": len(old_items),
                "new_count": len(new_items),
                "added": [new_map[key] for key in added_keys],
                "removed": [old_map[key] for key in removed_keys],
            }
        if route_diff:
            result["routes"][route] = route_diff

    return result


def print_summary(diff: dict[str, Any]) -> None:
    if not diff["changed"]:
        print("No schedule differences found.")
        return

    print("Schedule differences found:")
    for route, days in diff["routes"].items():
        for day, detail in days.items():
            print(
                f"- {route}/{day}: "
                f"{detail['old_count']} -> {detail['new_count']} "
                f"(+{len(detail['added'])}, -{len(detail['removed'])})"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare bus schedules.")
    parser.add_argument("--old", type=Path, default=DEFAULT_OLD)
    parser.add_argument("--new", type=Path, default=DEFAULT_NEW)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_DIFF)
    args = parser.parse_args()

    old_schedule = load_json(args.old)
    new_schedule = load_json(args.new)
    diff = compare(old_schedule, new_schedule)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(diff, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print_summary(diff)
    print(f"Saved {args.output}")
    return 1 if diff["changed"] else 0


if __name__ == "__main__":
    sys.exit(main())
