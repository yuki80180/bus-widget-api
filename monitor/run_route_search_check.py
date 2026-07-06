"""Run the route-search monitor investigation pipeline.

This is a read/compare workflow only: fetch saved research HTML, extract and
normalize records, compare with schedule.json, and print review candidates. It
does not update schedule.json, bus.db, or the production API.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MONITOR_DIR = Path(__file__).resolve().parent
DIFF_PATH = MONITOR_DIR / "debug" / "route_search_compare.json"


def script_path(name: str) -> str:
    return str(Path("monitor") / name)


def run_step(command: list[str]) -> int:
    display_command = " ".join(command)
    print(f"\n$ {display_command}", flush=True)
    completed = subprocess.run(command, cwd=PROJECT_ROOT)
    return completed.returncode


def run_pipeline(*, skip_fetch: bool) -> int:
    steps: list[list[str]] = []

    if not skip_fetch:
        steps.append([sys.executable, script_path("research_route_search.py")])

    steps.extend(
        [
            [sys.executable, script_path("extract_route_search_debug.py")],
            [sys.executable, script_path("convert_route_search_extracted.py")],
            [sys.executable, script_path("compare_route_search_normalized.py")],
            [sys.executable, script_path("print_route_search_candidates.py")],
        ]
    )

    for command in steps:
        return_code = run_step(command)
        if return_code != 0:
            return return_code

    return 0


def load_diff_summary(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def count_summary_value(summary: dict[str, object], key: str) -> int:
    value = summary.get(key, 0)
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def print_summary(diff: dict[str, object]) -> tuple[int, int, int, int]:
    summary = diff.get("summary", {})
    if not isinstance(summary, dict):
        summary = {}

    added_count = count_summary_value(summary, "added_count")
    removed_count = count_summary_value(summary, "removed_count")
    line_only_count = count_summary_value(summary, "line_only_count")
    time_change_candidate_count = count_summary_value(summary, "time_change_candidate_count")

    print("\n== Route search summary ==")
    if not (added_count or removed_count or line_only_count or time_change_candidate_count):
        print("差分なし")
        return added_count, removed_count, line_only_count, time_change_candidate_count

    print(f"added: {added_count}")
    print(f"removed: {removed_count}")
    print(f"line_only: {line_only_count}")
    print(f"time_change_candidates: {time_change_candidate_count}")

    return added_count, removed_count, line_only_count, time_change_candidate_count


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
        "--fail-on-diff",
        action="store_true",
        help="Exit with code 2 when any route-search difference or review candidate is present.",
    )
    args = parser.parse_args()

    return_code = run_pipeline(skip_fetch=args.skip_fetch)
    if return_code != 0:
        return return_code

    diff = load_diff_summary(DIFF_PATH)
    counts = print_summary(diff)

    if args.fail_on_diff and any(counts):
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
