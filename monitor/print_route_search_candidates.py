"""Print route-search comparison candidates for human review.

This script reads monitor/debug/route_search_compare.json and prints a compact
review view. It does not write files or update schedule data.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

INPUT_PATH = Path("monitor/debug/route_search_compare.json")


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"入力ファイルが見つかりません: {path.as_posix()}\n"
            "先に python monitor/compare_route_search_normalized.py を実行してください。"
        )

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"入力JSONの形式が不正です: {path.as_posix()}")
    return data


def as_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def text(value: object) -> str:
    return "" if value is None else str(value)


def int_value(value: object) -> int:
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def sort_by_time(item: dict[str, Any]) -> tuple[str, str, str]:
    return (text(item.get("time")), text(item.get("stop")), text(item.get("line")))


def sort_line_only(item: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        text(item.get("time")),
        text(item.get("stop")),
        text(item.get("existing_line")),
        text(item.get("route_search_line")),
    )


def sort_time_change(item: dict[str, Any]) -> tuple[int, str, str, str, str]:
    return (
        int_value(item.get("difference_minutes")),
        text(item.get("old_time")),
        text(item.get("new_time")),
        text(item.get("stop")),
        text(item.get("line")),
    )


def print_time_change_candidates(items: list[dict[str, Any]]) -> None:
    print("時刻変更候補:")
    for item in sorted(items, key=sort_time_change):
        print(
            f"- [{text(item.get('line'))} / {text(item.get('stop'))}] "
            f"{text(item.get('old_time'))} -> {text(item.get('new_time'))} "
            f"(+{int_value(item.get('difference_minutes'))}分)"
        )
    print()


def print_added(items: list[dict[str, Any]]) -> None:
    print("追加候補:")
    for item in sorted(items, key=sort_by_time):
        print(
            f"- [{text(item.get('line'))} / {text(item.get('stop'))}] "
            f"{text(item.get('time'))}"
        )
    print()


def print_removed(items: list[dict[str, Any]]) -> None:
    print("削除候補:")
    for item in sorted(items, key=sort_by_time):
        print(
            f"- [{text(item.get('line'))} / {text(item.get('stop'))}] "
            f"{text(item.get('time'))}"
        )
    print()


def print_line_only(items: list[dict[str, Any]]) -> None:
    print("系統表記差分:")
    for item in sorted(items, key=sort_line_only):
        print(
            f"- [{text(item.get('time'))} / {text(item.get('stop'))}] "
            f"{text(item.get('existing_line'))} -> {text(item.get('route_search_line'))}"
        )
    print()


def print_route_day(route: str, day_type: str, detail: dict[str, Any]) -> None:
    time_change_candidates = as_list(detail.get("time_change_candidates"))
    added = as_list(detail.get("added"))
    removed = as_list(detail.get("removed"))
    line_only = as_list(detail.get("line_only"))

    print(f"Route: {route} / {day_type}")
    print()

    if not (time_change_candidates or added or removed or line_only):
        print("差分なし")
        print()
        return

    if time_change_candidates:
        print_time_change_candidates(time_change_candidates)
    if added:
        print_added(added)
    if removed:
        print_removed(removed)
    if line_only:
        print_line_only(line_only)


def print_summary(summary: dict[str, Any]) -> None:
    print("Summary")
    print(f"added: {int_value(summary.get('added_count'))}")
    print(f"removed: {int_value(summary.get('removed_count'))}")
    print(f"line_only: {int_value(summary.get('line_only_count'))}")
    print(f"time_change_candidates: {int_value(summary.get('time_change_candidate_count'))}")


def print_report(data: dict[str, Any]) -> None:
    routes = data.get("routes", {})
    if not isinstance(routes, dict):
        raise ValueError("入力JSONの routes が不正です。")

    for route, days in routes.items():
        if not isinstance(days, dict):
            continue
        for day_type, detail in days.items():
            if isinstance(detail, dict):
                print_route_day(str(route), str(day_type), detail)

    summary = data.get("summary", {})
    if not isinstance(summary, dict):
        summary = {}
    print_summary(summary)


def main() -> int:
    try:
        data = load_json(INPUT_PATH)
        print_report(data)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
