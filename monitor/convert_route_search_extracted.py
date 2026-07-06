from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


DEBUG_DIR = Path(__file__).resolve().parent / "debug"
INPUT_FILE = DEBUG_DIR / "route_search_extracted.json"
OUTPUT_FILE = DEBUG_DIR / "route_search_normalized.json"

ROUTE_MAP = {
    "kanazawa_station_to_uni_weekday": {
        "route": "to_uni",
        "day_type": "weekday",
        "time_field": "depart_time",
        "stop_field": "arrive_stop",
        "pole_field": "arrive_pole",
    },
    "uni_to_kanazawa_station_weekday": {
        "route": "to_station",
        "day_type": "weekday",
        "time_field": "depart_time",
        "stop_field": "depart_stop",
        "pole_field": "depart_pole",
    },
    "uni_to_nakahashi_weekday": {
        "route": "to_nakahashi",
        "day_type": "weekday",
        "time_field": "depart_time",
        "stop_field": "depart_stop",
        "pole_field": "depart_pole",
    },
}
ROUTE_ORDER = {mapping["route"]: index for index, mapping in enumerate(ROUTE_MAP.values())}


def load_items(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(
            f"input file not found: {path.as_posix()}\n"
            "Run python monitor/extract_route_search_debug.py first."
        )

    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError(f"input JSON must contain an items list: {path.as_posix()}")
    return items


def normalize_item(item: dict[str, Any]) -> dict[str, Any] | None:
    source_route_key = item.get("route_key")
    mapping = ROUTE_MAP.get(source_route_key)
    if mapping is None:
        print(f"warning: unsupported route_key skipped: {source_route_key}", file=sys.stderr)
        return None

    return {
        "route": mapping["route"],
        "day_type": mapping["day_type"],
        "time": item.get(mapping["time_field"]),
        "line": item.get("line"),
        "stop": item.get(mapping["pole_field"]),
        "pole": item.get(mapping["pole_field"]),
        "depart_time": item.get("depart_time"),
        "depart_stop": item.get("depart_stop"),
        "depart_pole": item.get("depart_pole"),
        "arrive_time": item.get("arrive_time"),
        "arrive_stop": item.get("arrive_stop"),
        "arrive_pole": item.get("arrive_pole"),
        "fare": item.get("fare"),
        "via": item.get("via"),
        "source_file": item.get("source_file"),
        "source_route_key": source_route_key,
        "page_index": item.get("page_index"),
    }


def sort_key(item: dict[str, Any]) -> tuple[int, str, str, str, int]:
    page_index = item.get("page_index")
    route = str(item.get("route") or "")
    return (
        ROUTE_ORDER.get(route, len(ROUTE_ORDER)),
        str(item.get("day_type") or ""),
        str(item.get("time") or ""),
        str(item.get("source_file") or ""),
        page_index if isinstance(page_index, int) else -1,
    )


def build_output(items: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = []
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    counts: dict[str, int] = {}

    for item in items:
        normalized_item = normalize_item(item)
        if normalized_item is None:
            continue

        normalized.append(normalized_item)
        route = normalized_item["route"]
        day_type = normalized_item["day_type"]
        grouped.setdefault(route, {}).setdefault(day_type, []).append(normalized_item)

    normalized.sort(key=sort_key)
    for route_groups in grouped.values():
        for route_items in route_groups.values():
            route_items.sort(key=sort_key)

    for mapping in ROUTE_MAP.values():
        route = mapping["route"]
        day_type = mapping["day_type"]
        if route in grouped and day_type in grouped[route]:
            counts[f"{route}/{day_type}"] = len(grouped[route][day_type])

    return {
        "items": normalized,
        "grouped": grouped,
        "counts": counts,
    }


def main() -> int:
    try:
        items = load_items(INPUT_FILE)
        output = build_output(items)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    OUTPUT_FILE.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"wrote {OUTPUT_FILE.relative_to(Path.cwd()).as_posix()}")
    for key, count in output["counts"].items():
        print(f"{key}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
