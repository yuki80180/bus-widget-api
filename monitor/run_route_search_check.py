"""Run the route-search monitor investigation pipeline.

This is a read/compare workflow only: fetch saved research HTML, extract records,
compare with schedule.json, and print the diff summary. It does not update
schedule.json, bus.db, or the production API.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MONITOR_DIR = Path(__file__).resolve().parent
DIFF_PATH = MONITOR_DIR / "route_search_diff.json"


def script_path(name: str) -> str:
    return str(Path("monitor") / name)


def run_step(command: list[str]) -> int:
    display_command = " ".join(command)
    print(f"\n$ {display_command}")
    completed = subprocess.run(command, cwd=PROJECT_ROOT)
    return completed.returncode


def run_pipeline(*, skip_fetch: bool, pretty: bool) -> int:
    steps: list[list[str]] = []

    if not skip_fetch:
        steps.append([sys.executable, script_path("research_route_search.py")])

    extract_command = [sys.executable, script_path("extract_route_search_results.py")]
    if pretty:
        extract_command.append("--pretty")
    steps.append(extract_command)

    steps.append([sys.executable, script_path("compare_route_search_results.py")])

    for command in steps:
        return_code = run_step(command)
        if return_code != 0:
            return return_code

    return 0


def load_diff_summary(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def print_summary(diff: dict[str, object]) -> tuple[int, int, int]:
    added_count = len(diff.get("added", []))
    removed_count = len(diff.get("removed", []))
    changed_count = len(diff.get("changed", []))

    print("\nroute_search_diff summary")
    print(f"existing_count: {diff.get('existing_count')}")
    print(f"extracted_count: {diff.get('extracted_count')}")
    print(f"added: {added_count}")
    print(f"removed: {removed_count}")
    print(f"changed: {changed_count}")
    print(f"unchanged_count: {diff.get('unchanged_count')}")

    return added_count, removed_count, changed_count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run route-search fetch/extract/compare checks without updating schedule data."
    )
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Skip research_route_search.py and use already saved HTML files.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pass --pretty to extract_route_search_results.py.",
    )
    parser.add_argument(
        "--fail-on-diff",
        action="store_true",
        help="Exit with code 2 when added, removed, or changed records are present.",
    )
    args = parser.parse_args()

    return_code = run_pipeline(skip_fetch=args.skip_fetch, pretty=args.pretty)
    if return_code != 0:
        return return_code

    diff = load_diff_summary(DIFF_PATH)
    added_count, removed_count, changed_count = print_summary(diff)

    if args.fail_on_diff and (added_count or removed_count or changed_count):
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
