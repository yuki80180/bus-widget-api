from __future__ import annotations

import json
import re
import sys
import unicodedata
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

DEBUG_DIR = Path(__file__).resolve().parent / "debug"
INPUT_PATTERN = "*_route_search_page_*_response.html"
OUTPUT_FILE = DEBUG_DIR / "route_search_extracted.json"

FILENAME_RE = re.compile(
    r"^\d+_(?P<route_key>.+)_route_search_page_(?P<page_index>\d+)_response\.html$"
)
TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    text = unescape(value).replace("\xa0", " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    return "\n".join(line.strip() for line in text.split("\n") if line.strip())


def compact_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", normalize_text(value)).strip()


def attrs_to_dict(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
    return {name.lower(): value or "" for name, value in attrs}


def class_tokens(attrs: dict[str, str]) -> set[str]:
    return set(attrs.get("class", "").split())


def to_ascii_width(value: str | None) -> str | None:
    if value is None:
        return None
    return unicodedata.normalize("NFKC", value).strip() or None


class RouteSearchParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[dict] = []
        self.table_depth = 0
        self.table: dict | None = None
        self.row: dict | None = None
        self.cell: dict | None = None

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = attrs_to_dict(attrs_list)
        classes = class_tokens(attrs)

        if tag == "table":
            if self.table is not None:
                self.table_depth += 1
            elif "pathway" in classes:
                self.table = {"rows": [], "text_parts": []}
                self.table_depth = 1
            return

        if self.table is None:
            return

        if tag == "tr" and self.row is None:
            self.row = {"attrs": attrs, "classes": classes, "cells": [], "text_parts": []}
            return

        if tag in {"td", "th"} and self.row is not None and self.cell is None:
            self.cell = {
                "attrs": attrs,
                "classes": classes,
                "inputs": [],
                "text_parts": [],
                "pre_input_parts": [],
                "input_seen": False,
            }
            return

        if tag == "input" and self.cell is not None:
            self.cell["inputs"].append(attrs)
            self.cell["input_seen"] = True
            return

        if tag == "br":
            self.add_data("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.table is None:
            return

        if tag in {"td", "th"} and self.cell is not None and self.row is not None:
            self.cell["text"] = normalize_text("".join(self.cell["text_parts"]))
            self.cell["pre_input_text"] = normalize_text("".join(self.cell["pre_input_parts"]))
            self.row["cells"].append(self.cell)
            self.cell = None
            return

        if tag == "tr" and self.row is not None:
            self.row["text"] = normalize_text("".join(self.row["text_parts"]))
            self.table["rows"].append(self.row)
            self.row = None
            return

        if tag == "table":
            self.table_depth -= 1
            if self.table_depth <= 0:
                self.table["text"] = normalize_text("".join(self.table["text_parts"]))
                self.tables.append(self.table)
                self.table = None
                self.row = None
                self.cell = None
                self.table_depth = 0

    def handle_data(self, data: str) -> None:
        self.add_data(data)

    def add_data(self, data: str) -> None:
        if self.table is not None:
            self.table["text_parts"].append(data)
        if self.row is not None:
            self.row["text_parts"].append(data)
        if self.cell is not None:
            self.cell["text_parts"].append(data)
            if not self.cell["input_seen"]:
                self.cell["pre_input_parts"].append(data)


def read_html(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp932", "shift_jis", "euc_jp"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


def parse_filename(path: Path) -> tuple[str | None, int | None]:
    match = FILENAME_RE.match(path.name)
    if not match:
        return None, None
    return match.group("route_key"), int(match.group("page_index"))


def first_cell_with_class(row: dict | None, class_name: str) -> dict | None:
    if not row:
        return None
    return next((cell for cell in row.get("cells", []) if class_name in cell.get("classes", set())), None)


def time_from_text(value: str | None) -> str | None:
    match = TIME_RE.search(value or "")
    return match.group(0) if match else None


def time_from_row(row: dict | None, name: str) -> str | None:
    if not row:
        return None
    for cell in row.get("cells", []):
        if cell.get("attrs", {}).get("name") == name:
            return cell.get("attrs", {}).get("value") or time_from_text(cell.get("text"))
    return time_from_text(row.get("text"))


def input_value(cell: dict | None, name: str) -> str | None:
    if not cell:
        return None
    for attrs in cell.get("inputs", []):
        if attrs.get("name") == name:
            return attrs.get("value") or None
    return None


def pole_from_input(value: str | None) -> str | None:
    if not value:
        return None
    return to_ascii_width(value.split(",")[-1])


def split_stop_and_pole(raw_stop: str | None, pole_value: str | None) -> tuple[str | None, str | None]:
    text = compact_text(raw_stop)
    pole = pole_from_input(pole_value)

    if not pole:
        paren = re.search(r"[（(]([^）)]+)[）)]\s*$", text)
        suffix = re.search(r"[-－ー]([A-Za-zＡ-Ｚａ-ｚ0-9０-９]+)\s*$", text)
        if paren:
            pole = to_ascii_width(re.split(r"[-－ー]", paren.group(1), maxsplit=1)[0])
        elif suffix:
            pole = to_ascii_width(suffix.group(1))

    stop = re.sub(r"\s*[（(][^）)]*[）)]\s*$", "", text).strip()
    if pole:
        stop = re.sub(rf"\s*[-－ー]\s*{re.escape(pole)}\s*$", "", stop, flags=re.IGNORECASE)
    return (stop or None), pole


def extract_stop(row: dict | None, pole_input_name: str) -> tuple[str | None, str | None]:
    cell = first_cell_with_class(row, "pathway-links")
    raw_stop = cell.get("pre_input_text") if cell else None
    if not raw_stop and cell:
        raw_stop = cell.get("text")
    return split_stop_and_pole(raw_stop, input_value(cell, pole_input_name))


def extract_fare(row: dict | None) -> str | None:
    if not row:
        return None
    match = re.search(r"(\d+)\s*円", compact_text(row.get("text")))
    return match.group(1) if match else None


def extract_line(row: dict | None) -> str | None:
    cell = first_cell_with_class(row, "pathway-keiro-maku")
    if not cell:
        return None
    text = compact_text(cell.get("text"))
    match = re.search(r"[0-9A-Za-z]+", to_ascii_width(text) or "")
    return match.group(0) if match else (text or None)


def extract_via(row: dict | None) -> list[str] | None:
    if not row:
        return None
    via_cell = None
    for cell in row.get("cells", []):
        classes = cell.get("classes", set())
        if "pathway-time" not in classes and "pathway-keiro-maku" not in classes:
            via_cell = cell
            break
    if not via_cell:
        return None

    values = []
    for line in normalize_text(via_cell.get("text")).split("\n"):
        line = compact_text(line).replace("経由", "").replace("ゆき", "").replace("行き", "")
        line = line.strip(" 　-－ー")
        if line and line not in values:
            values.append(line)
    return values or None


def table_to_item(table: dict, source_file: str, route_key: str | None, page_index: int | None) -> dict:
    depart_row = arrive_row = keiro_row = None

    for row in table.get("rows", []):
        classes = row.get("classes", set())
        if "pathway-times" in classes:
            if time_from_row(row, "time_arr[]") is not None and depart_row is None:
                depart_row = row
            elif time_from_row(row, "time_dep[]") is not None and arrive_row is None:
                arrive_row = row
        elif "pathway-keiro" in classes and keiro_row is None:
            keiro_row = row

    depart_stop, depart_pole = extract_stop(depart_row, "pole_arr[]")
    arrive_stop, arrive_pole = extract_stop(arrive_row, "pole_dep[]")

    return {
        "source_file": source_file,
        "route_key": route_key,
        "page_index": page_index,
        "depart_time": time_from_row(depart_row, "time_arr[]"),
        "depart_stop": depart_stop,
        "depart_pole": depart_pole,
        "arrive_time": time_from_row(arrive_row, "time_dep[]"),
        "arrive_stop": arrive_stop,
        "arrive_pole": arrive_pole,
        "fare": extract_fare(keiro_row),
        "line": extract_line(keiro_row),
        "via": extract_via(keiro_row),
        "raw_text": compact_text(table.get("text")) or None,
    }


def extract_file(path: Path) -> list[dict]:
    route_key, page_index = parse_filename(path)
    try:
        source_file = path.relative_to(Path.cwd()).as_posix()
    except ValueError:
        source_file = path.as_posix()

    parser = RouteSearchParser()
    parser.feed(read_html(path))
    parser.close()

    return [
        table_to_item(table, source_file, route_key, page_index)
        for table in parser.tables
    ]


def main() -> int:
    items = []
    for path in sorted(DEBUG_DIR.glob(INPUT_PATTERN)):
        try:
            items.extend(extract_file(path))
        except Exception as exc:
            print(f"warning: skipped {path}: {exc}", file=sys.stderr)

    OUTPUT_FILE.write_text(
        json.dumps({"items": items}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    counts = {}
    for item in items:
        key = item.get("route_key") or "(unknown)"
        counts[key] = counts.get(key, 0) + 1

    print(f"wrote {OUTPUT_FILE.relative_to(Path.cwd()).as_posix()}")
    print(f"items: {len(items)}")
    for key in sorted(counts):
        print(f"{key}: {counts[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())