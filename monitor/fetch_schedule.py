"""Fetch Hokutetsu timetable data and save it as new_schedule.json."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode
from urllib.request import Request, build_opener

BASE_URL = "https://arj.hokutetsu.co.jp/timetable"
STOP_NAME = "金沢工業大学"
DEFAULT_OUTPUT = Path(__file__).with_name("new_schedule.json")

DAY_TYPES = {"weekday": "0", "weekend": "2"}


@dataclass(frozen=True)
class Source:
    stop: str
    checkdata: tuple[str, ...]


ROUTES: dict[str, tuple[Source, ...]] = {
    "to_uni": (Source("A", ("101",)), Source("C", ("101", "103", "104", "105"))),
    "to_station": (Source("B", ("201", "202", "203")), Source("D", ("104",))),
    "to_nakahashi": (Source("B", ("101", "102")), Source("D", ("101",))),
}


class HokutetsuClient:
    def __init__(self, base_url: str = BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self.opener = build_opener()

    def get_text(self, path: str) -> str:
        return self._request(path)

    def post_text(self, path: str, data: dict[str, str] | list[tuple[str, str]]) -> str:
        return self._request(path, data=urlencode(data).encode("utf-8"))

    def _request(self, path: str, data: bytes | None = None) -> str:
        request = Request(
            self.base_url + "/" + path.lstrip("/"),
            data=data,
            headers={
                "User-Agent": "bus-widget-api schedule monitor",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with self.opener.open(request, timeout=30) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")


class TimetableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_table = False
        self.in_row = False
        self.current_row_class = ""
        self.current_cell = -1
        self.current_hour = ""
        self.rows: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        tag = tag.lower()
        if tag == "table" and attrs_dict.get("id") == "busstop":
            self.in_table = True
            return
        if not self.in_table:
            return
        if tag == "tr":
            self.in_row = True
            self.current_row_class = attrs_dict.get("class", "")
            self.current_cell = -1
            self.current_hour = ""
            return
        if not self.in_row:
            return
        if tag == "th":
            self.current_cell = -1
        elif tag == "td":
            self.current_cell += 1
        elif tag == "a" and self.current_row_class == "busstop-time":
            value = attrs_dict.get("value") or ""
            self.rows.append((self.current_hour, value.split(",", 1)[0], ""))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "table" and self.in_table:
            self.in_table = False
        elif tag == "tr":
            self.in_row = False

    def handle_data(self, data: str) -> None:
        if not self.in_table or not self.in_row:
            return
        text = normalize_text(data)
        if not text:
            return
        if self.current_cell == -1:
            self.current_hour += text
        elif self.current_row_class == "busstop-time" and self.rows:
            hour, checkdata, minute = self.rows[-1]
            if not minute:
                self.rows[-1] = (hour, checkdata, text)


def normalize_text(value: str) -> str:
    value = html.unescape(value)
    value = value.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    value = re.sub(r"[\s\u3000]+", " ", value)
    return value.strip()


def discover_diaseq(client: HokutetsuClient) -> str:
    page = client.get_text("pathway.php")
    match = re.search(r'name="diaseq"\s+value="([^"]+)"\s+checked', page)
    if not match:
        match = re.search(r'name="diaseq"\s+value="([^"]+)"', page)
    if not match:
        raise RuntimeError("Could not find Hokutetsu diaseq")
    return match.group(1)


def extract_input_value(page: str, name_or_id: str) -> str:
    pattern = (
        r"<input[^>]+(?:name|id)=[\"']"
        + re.escape(name_or_id)
        + r"[\"'][^>]+value=[\"']([^\"']*)[\"']"
    )
    match = re.search(pattern, page, flags=re.IGNORECASE)
    return html.unescape(match.group(1)) if match else ""


def resolve_stop(client: HokutetsuClient, diaseq: str) -> str:
    page = client.post_text(
        "busstop_ikisaki.php",
        {"diaseq": diaseq, "type": "0", "tei_mei": STOP_NAME},
    )
    teicd = extract_input_value(page, "teicd")
    if not teicd:
        raise RuntimeError(f"Could not resolve stop code for {STOP_NAME}")
    return teicd


def fetch_destination_options(
    client: HokutetsuClient,
    *,
    diaseq_number: str,
    teicd: str,
    pole: str,
    weekday: str,
) -> tuple[str, dict[str, str]]:
    response = client.post_text(
        "phpscript/hpjpp0120.php",
        {"diaseq": diaseq_number, "teicd": teicd, "pole": pole, "weekday": weekday},
    )
    lines = [line for line in response.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"No destination data for pole {pole}, weekday {weekday}")
    labels: dict[str, str] = {}
    for line in lines[1:]:
        checkdata, _, label = line.partition(",")
        labels[checkdata] = format_line_label(label)
    return lines[0].strip(), labels


def format_line_label(raw_label: str) -> str:
    text = re.sub(r"<br\s*/?>", " ", raw_label, flags=re.IGNORECASE)
    text = normalize_text(text)
    match = re.match(r"^([0-9 ]+)\s+(.*)$", text)
    if not match:
        return text.replace(" ゆき", "行").replace(" 経由 ", "・")
    route_numbers = "/".join(match.group(1).split())
    route_text = match.group(2)
    route_text = route_text.replace("三丁目", "")
    route_text = route_text.replace(" 経由 ", "・")
    route_text = route_text.replace(" ゆき", "行")
    route_text = route_text.replace(" ", "")
    return f"({route_numbers}) {route_text}"


def fetch_timetable(
    client: HokutetsuClient,
    *,
    diaseq: str,
    teicd: str,
    pole: str,
    weekday: str,
    checkdata: Iterable[str],
    labels: dict[str, str],
    tjhseq: str,
) -> list[dict[str, str]]:
    selected = tuple(checkdata)
    body: list[tuple[str, str]] = [
        ("weekday", weekday),
        ("pole", pole),
        ("diaseq", diaseq),
        ("teicd", teicd),
        ("tei_mei", STOP_NAME),
        ("tjhseq", tjhseq),
    ]
    for value in selected:
        body.append(("checkdata[]", value))

    parser = TimetableParser()
    parser.feed(client.post_text("busstop_timetable.php", body))

    buses = []
    for hour, check, minute in parser.rows:
        if check in selected and minute:
            buses.append({"time": f"{int(hour):02d}:{int(minute):02d}", "line": labels.get(check, check), "stop": pole})
    return sorted(buses, key=lambda item: item["time"])


def fetch_schedule() -> dict[str, dict[str, list[dict[str, str]]]]:
    client = HokutetsuClient()
    diaseq = discover_diaseq(client)
    diaseq_number = diaseq.split(",", 1)[0]
    teicd = resolve_stop(client, diaseq)

    schedule: dict[str, dict[str, list[dict[str, str]]]] = {}
    for route_name, sources in ROUTES.items():
        schedule[route_name] = {}
        for day_name, weekday in DAY_TYPES.items():
            merged: list[dict[str, str]] = []
            for source in sources:
                tjhseq, labels = fetch_destination_options(
                    client,
                    diaseq_number=diaseq_number,
                    teicd=teicd,
                    pole=source.stop,
                    weekday=weekday,
                )
                merged.extend(
                    fetch_timetable(
                        client,
                        diaseq=diaseq,
                        teicd=teicd,
                        pole=source.stop,
                        weekday=weekday,
                        checkdata=source.checkdata,
                        labels=labels,
                        tjhseq=tjhseq,
                    )
                )
            schedule[route_name][day_name] = sorted(merged, key=lambda item: item["time"])
    return schedule


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Hokutetsu timetable data.")
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    schedule = fetch_schedule()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(schedule, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
