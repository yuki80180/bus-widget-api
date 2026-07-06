"""Print route-search comparison candidates for human review.

This script reads monitor/debug/route_search_compare.json and prints a compact
review view. It does not write files or update schedule data.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

INPUT_PATH = Path("monitor/debug/route_search_compare.json")
REVIEWED_PATH = Path("monitor/route_search_reviewed_candidates.json")
ReviewedIndex = dict[str, set[tuple[str, ...]]]


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


def normalize_line_for_compare(line: object) -> str | None:
    if line is None:
        return None

    stripped = text(line).strip()
    bracket_match = re.match(r"^[^\dA-Za-z（(]*[（(]\s*([^）)]+?)\s*[）)]", stripped)
    if bracket_match:
        return bracket_match.group(1).strip()
    if stripped.isdigit():
        return stripped
    return stripped


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


def load_reviewed_index(path: Path) -> ReviewedIndex | None:
    if not path.exists():
        return None

    data = load_json(path)
    reviewed = data.get("reviewed", [])
    if not isinstance(reviewed, list):
        raise ValueError(f"reviewed must be a list: {path.as_posix()}")

    index: ReviewedIndex = {
        "direct": set(),
        "time_change_added": set(),
        "time_change_removed": set(),
    }
    for item in reviewed:
        if not isinstance(item, dict) or item.get("status") != "confirmed":
            continue
        key = item.get("key", {})
        if not isinstance(key, dict):
            continue

        route = text(item.get("route"))
        day_type = text(item.get("day_type"))
        candidate_type = text(item.get("type"))
        line_normalized = text(key.get("line_normalized"))
        stop = text(key.get("stop"))

        if candidate_type in {"added", "removed"}:
            index["direct"].add(
                (
                    route,
                    day_type,
                    candidate_type,
                    text(key.get("time")),
                    line_normalized,
                    stop,
                )
            )
        elif candidate_type == "time_change":
            old_time = text(key.get("old_time"))
            new_time = text(key.get("new_time"))
            index["direct"].add(
                (
                    route,
                    day_type,
                    candidate_type,
                    old_time,
                    new_time,
                    line_normalized,
                    stop,
                )
            )
            index["time_change_removed"].add(
                (route, day_type, "removed", old_time, line_normalized, stop)
            )
            index["time_change_added"].add(
                (route, day_type, "added", new_time, line_normalized, stop)
            )

    return index


def direct_review_key(
    route: str,
    day_type: str,
    candidate_type: str,
    item: dict[str, Any],
) -> tuple[str, ...]:
    return (
        route,
        day_type,
        candidate_type,
        text(item.get("time")),
        text(normalize_line_for_compare(item.get("line"))),
        text(item.get("stop")),
    )


def time_change_review_key(route: str, day_type: str, item: dict[str, Any]) -> tuple[str, ...]:
    return (
        route,
        day_type,
        "time_change",
        text(item.get("old_time")),
        text(item.get("new_time")),
        text(normalize_line_for_compare(item.get("line"))),
        text(item.get("stop")),
    )


def review_label(
    reviewed_index: ReviewedIndex | None,
    route: str,
    day_type: str,
    candidate_type: str,
    item: dict[str, Any],
) -> str:
    if reviewed_index is None:
        return ""

    if candidate_type == "time_change":
        key = time_change_review_key(route, day_type, item)
        return "[確認済み]" if key in reviewed_index["direct"] else "[未確認]"

    key = direct_review_key(route, day_type, candidate_type, item)
    if key in reviewed_index["direct"]:
        return "[確認済み]"
    if candidate_type == "added" and key in reviewed_index["time_change_added"]:
        return "[確認済み: 時刻変更対応]"
    if candidate_type == "removed" and key in reviewed_index["time_change_removed"]:
        return "[確認済み: 時刻変更対応]"
    return "[未確認]"


def label_prefix(label: str) -> str:
    return f"{label} " if label else ""


def print_time_change_candidates(
    route: str,
    day_type: str,
    items: list[dict[str, Any]],
    reviewed_index: ReviewedIndex | None,
) -> None:
    print("時刻変更候補:")
    for item in sorted(items, key=sort_time_change):
        label = review_label(reviewed_index, route, day_type, "time_change", item)
        print(
            f"- {label_prefix(label)}[{text(item.get('line'))} / {text(item.get('stop'))}] "
            f"{text(item.get('old_time'))} -> {text(item.get('new_time'))} "
            f"(+{int_value(item.get('difference_minutes'))}分)"
        )
    print()


def print_added(
    route: str,
    day_type: str,
    items: list[dict[str, Any]],
    reviewed_index: ReviewedIndex | None,
) -> None:
    print("追加候補:")
    for item in sorted(items, key=sort_by_time):
        label = review_label(reviewed_index, route, day_type, "added", item)
        print(
            f"- {label_prefix(label)}[{text(item.get('line'))} / {text(item.get('stop'))}] "
            f"{text(item.get('time'))}"
        )
    print()


def print_removed(
    route: str,
    day_type: str,
    items: list[dict[str, Any]],
    reviewed_index: ReviewedIndex | None,
) -> None:
    print("削除候補:")
    for item in sorted(items, key=sort_by_time):
        label = review_label(reviewed_index, route, day_type, "removed", item)
        print(
            f"- {label_prefix(label)}[{text(item.get('line'))} / {text(item.get('stop'))}] "
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


def print_route_day(
    route: str,
    day_type: str,
    detail: dict[str, Any],
    reviewed_index: ReviewedIndex | None,
) -> None:
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
        print_time_change_candidates(route, day_type, time_change_candidates, reviewed_index)
    if added:
        print_added(route, day_type, added, reviewed_index)
    if removed:
        print_removed(route, day_type, removed, reviewed_index)
    if line_only:
        print_line_only(line_only)


def print_summary(summary: dict[str, Any]) -> None:
    print("Summary")
    print(f"added: {int_value(summary.get('added_count'))}")
    print(f"removed: {int_value(summary.get('removed_count'))}")
    print(f"line_only: {int_value(summary.get('line_only_count'))}")
    print(f"time_change_candidates: {int_value(summary.get('time_change_candidate_count'))}")


def print_report(data: dict[str, Any], reviewed_index: ReviewedIndex | None) -> None:
    routes = data.get("routes", {})
    if not isinstance(routes, dict):
        raise ValueError("入力JSONの routes が不正です。")

    for route, days in routes.items():
        if not isinstance(days, dict):
            continue
        for day_type, detail in days.items():
            if isinstance(detail, dict):
                print_route_day(str(route), str(day_type), detail, reviewed_index)

    summary = data.get("summary", {})
    if not isinstance(summary, dict):
        summary = {}
    print_summary(summary)


def main() -> int:
    try:
        data = load_json(INPUT_PATH)
        reviewed_index = load_reviewed_index(REVIEWED_PATH)
        print_report(data, reviewed_index)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
