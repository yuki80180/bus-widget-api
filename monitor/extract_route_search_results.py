"""Extract route-search results from saved Hokutetsu HTML pages."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from inspect_route_search_html import html_to_text_lines

DEFAULT_DEBUG_DIR = Path(__file__).resolve().parent / "debug"
DEFAULT_PATTERN = "04_uni_to_nakahashi_weekday_route_search_page_*_response.html"
DEFAULT_OUTPUT = DEFAULT_DEBUG_DIR / "04_uni_to_nakahashi_weekday_extracted.json"

TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
PAGE_RANGE_RE = re.compile(r"^（(?P<start>\d+)～(?P<end>\d+)／(?P<total>\d+)件）$")
DEPARTURE_STOP_RE = re.compile(
    r"^(?P<stop>金沢工業大学)（(?P<detail>[^）]+)）\s+(?P<index>\d+),(?P<code>[A-Z])$"
)
ARRIVAL_STOP_RE = re.compile(
    r"^(?P<stop>中橋)-(?P<detail>[A-ZＡ-Ｚ]+)\s+(?P<index>\d+),(?P<code>[A-Z])$"
)
FARE_RE = re.compile(r"^\d+円$")
ROUTE_NO_RE = re.compile(r"^\d{1,3}$")


def read_lines(path: Path) -> list[str]:
    content = path.read_text(encoding="utf-8", errors="replace")
    return html_to_text_lines(content)


def parse_page_range(lines: list[str]) -> dict[str, int] | None:
    for line in lines:
        match = PAGE_RANGE_RE.match(line)
        if match:
            return {
                "start": int(match.group("start")),
                "end": int(match.group("end")),
                "total": int(match.group("total")),
            }
    return None


def find_record_starts(lines: list[str]) -> list[int]:
    starts: list[int] = []

    for i in range(len(lines) - 1):
        if not TIME_RE.match(lines[i]):
            continue

        if DEPARTURE_STOP_RE.match(lines[i + 1]):
            starts.append(i)

    return starts


def first_matching_line(lines: list[str], pattern: re.Pattern[str]) -> str | None:
    for line in lines:
        if pattern.match(line):
            return line
    return None


def first_line_containing(lines: list[str], keyword: str) -> str | None:
    for line in lines:
        if keyword in line:
            return line
    return None


def parse_record(
    *,
    source_file: Path,
    page_range: dict[str, int] | None,
    lines: list[str],
    start_index: int,
    end_index: int,
) -> dict[str, Any]:
    block = lines[start_index:end_index]

    departure_time = block[0]
    departure_match = DEPARTURE_STOP_RE.match(block[1])
    if not departure_match:
        raise ValueError(f"departure stop line could not be parsed: {block[1]!r}")

    local_index = int(departure_match.group("index"))

    result_no: int | None = None
    if page_range is not None:
        result_no = page_range["start"] + local_index

    fare = first_matching_line(block, FARE_RE)

    route_no: str | None = None
    route_index: int | None = None
    for i, line in enumerate(block):
        if ROUTE_NO_RE.match(line):
            route_no = line
            route_index = i
            break

    via: str | None = None
    destination: str | None = None
    if route_index is not None:
        if route_index + 1 < len(block):
            via_candidate = block[route_index + 1]
            if "経由" in via_candidate:
                via = via_candidate

        if route_index + 2 < len(block):
            destination_candidate = block[route_index + 2]
            if "ゆき" in destination_candidate:
                destination = destination_candidate

    arrival_time: str | None = None
    arrival_line: str | None = None
    arrival_match: re.Match[str] | None = None

    search_from = route_index + 1 if route_index is not None else 2
    for i in range(search_from, len(block) - 1):
        if not TIME_RE.match(block[i]):
            continue

        maybe_arrival = ARRIVAL_STOP_RE.match(block[i + 1])
        if maybe_arrival:
            arrival_time = block[i]
            arrival_line = block[i + 1]
            arrival_match = maybe_arrival
            break

    if arrival_time is None or arrival_match is None:
        raise ValueError(f"arrival could not be parsed in {source_file.name}: {block!r}")

    return {
        "result_no": result_no,
        "page_range": page_range,
        "local_index": local_index,
        "departure_time": departure_time,
        "departure_stop": departure_match.group("stop"),
        "departure_stop_detail": departure_match.group("detail"),
        "departure_stop_code": departure_match.group("code"),
        "arrival_time": arrival_time,
        "arrival_stop": arrival_match.group("stop"),
        "arrival_stop_detail": arrival_match.group("detail"),
        "arrival_stop_code": arrival_match.group("code"),
        "fare": fare,
        "route_no": route_no,
        "via": via,
        "destination": destination,
        "source_file": source_file.name,
        "raw_text": "\n".join(block),
    }


def extract_file(path: Path) -> list[dict[str, Any]]:
    lines = read_lines(path)
    page_range = parse_page_range(lines)
    starts = find_record_starts(lines)

    records: list[dict[str, Any]] = []

    for i, start_index in enumerate(starts):
        end_index = starts[i + 1] if i + 1 < len(starts) else len(lines)

        record = parse_record(
            source_file=path,
            page_range=page_range,
            lines=lines,
            start_index=start_index,
            end_index=end_index,
        )
        records.append(record)

    if page_range is not None:
        expected_count = page_range["end"] - page_range["start"] + 1
        if len(records) != expected_count:
            print(
                f"warning: {path.name}: expected {expected_count} records, got {len(records)}",
                file=sys.stderr,
            )

    return records


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract route-search results from saved HTML files."
    )
    parser.add_argument("--debug-dir", type=Path, default=DEFAULT_DEBUG_DIR)
    parser.add_argument("--pattern", default=DEFAULT_PATTERN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    html_files = sorted(path for path in args.debug_dir.glob(args.pattern) if path.is_file())

    if not html_files:
        print(f"No HTML files matched {args.pattern!r} under {args.debug_dir}", file=sys.stderr)
        return 1

    all_records: list[dict[str, Any]] = []

    for html_file in html_files:
        records = extract_file(html_file)
        print(f"{html_file.name}: {len(records)} records")
        all_records.extend(records)

    result = {
        "route_key": "uni_to_nakahashi_weekday",
        "source_pattern": args.pattern,
        "record_count": len(all_records),
        "records": all_records,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"wrote {args.output.as_posix()}")
    print(f"total records: {len(all_records)}")

    if args.pretty:
        for record in all_records:
            print(
                f"{record['result_no']:>2}: "
                f"{record['departure_time']} "
                f"{record['departure_stop']}({record['departure_stop_detail']})"
                f" -> {record['arrival_time']} {record['arrival_stop']}-{record['arrival_stop_detail']} "
                f"[{record['route_no']}] {record['via']} / {record['destination']}"
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())