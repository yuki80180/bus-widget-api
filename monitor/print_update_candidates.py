"""Print monitor/update_candidates.json in a human-readable form."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DEFAULT_CANDIDATES = Path(__file__).with_name("update_candidates.json")


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def format_bus(bus: dict[str, str]) -> str:
    return (
        f"time={bus.get('time', '-')} "
        f"stop={bus.get('stop', '-')} "
        f"line={bus.get('line', '')}"
    )


def print_candidates(candidates: dict[str, Any]) -> bool:
    routes = candidates.get("routes", {})
    printed = False

    for route in sorted(routes):
        days = routes.get(route, {})
        for day in sorted(days):
            detail = days.get(day, {})
            added = detail.get("added", [])
            removed = detail.get("removed", [])

            if added:
                printed = True
                print(f"[{route}/{day}] added")
                for bus in added:
                    print(f"  + {format_bus(bus)}")

            if removed:
                printed = True
                print(f"[{route}/{day}] removed")
                for bus in removed:
                    print(f"  - {format_bus(bus)}")

    if not printed:
        print("No update candidates.")
    return printed


def main() -> int:
    parser = argparse.ArgumentParser(description="Print update candidates for manual review.")
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=DEFAULT_CANDIDATES,
        help="Path to update_candidates.json. Defaults to monitor/update_candidates.json.",
    )
    args = parser.parse_args()

    candidates = load_json(args.path)
    print_candidates(candidates)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
