"""Research Hokutetsu route-search form requests and responses.

This script saves raw request payloads and response bodies for manual review.
It does not update schedule.json or bus.db.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, build_opener

BASE_URL = "https://arj.hokutetsu.co.jp/timetable"
DEFAULT_DEBUG_DIR = Path(__file__).resolve().parent / "debug"
PATHWAY_PATH = "pathway.php"
STOP_RESOLVE_PATH = "phpscript/hpjpp0500.php"
ROUTE_SEARCH_PATH = "pathway_timetable.php"


@dataclass(frozen=True)
class SearchCase:
    key: str
    departure: str
    arrival: str
    weekday: str = "0"
    pathway: str = "0"


@dataclass(frozen=True)
class RouteSearchFormState:
    fields: list[tuple[str, str]]
    next_page: str | None
    previous_page: str | None


SEARCH_CASES: tuple[SearchCase, ...] = (
    SearchCase("kanazawa_station_to_uni_weekday", "金沢駅", "金沢工業大学"),
    SearchCase("uni_to_kanazawa_station_weekday", "金沢工業大学", "金沢駅"),
    SearchCase("uni_to_nakahashi_weekday", "金沢工業大学", "中橋"),
)


class RouteSearchFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.fields: list[tuple[str, str]] = []
        self.next_page: str | None = None
        self.previous_page: str | None = None
        self.found_form = False
        self.in_form = False
        self.form_depth = 0
        self.current_select_name: str | None = None
        self.current_select_selected_value: str | None = None
        self.current_select_first_value: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr_map = {name.lower(): value or "" for name, value in attrs}

        if tag == "form" and attr_map.get("name") == "form1":
            self.found_form = True
            self.in_form = True
            self.form_depth = 1
            return

        if not self.in_form:
            return

        if tag == "form":
            self.form_depth += 1
        elif tag == "input":
            self._add_input(attr_map)
        elif tag == "select":
            self._start_select(attr_map)
        elif tag == "option":
            self._handle_option(attr_map)
        elif tag == "a":
            self._capture_page_links(attr_map)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if not self.in_form:
            return

        if tag == "select":
            self._finish_select()
        elif tag == "form":
            self.form_depth -= 1
            if self.form_depth <= 0:
                self.in_form = False

    def _add_input(self, attr_map: dict[str, str]) -> None:
        name = attr_map.get("name")
        if not name or "disabled" in attr_map:
            return

        input_type = attr_map.get("type", "text").lower()
        if input_type in {"button", "submit", "image", "reset", "file"}:
            return
        if input_type in {"checkbox", "radio"} and "checked" not in attr_map:
            return

        value = attr_map.get("value", "on" if input_type in {"checkbox", "radio"} else "")
        self.fields.append((html.unescape(name), html.unescape(value)))

    def _start_select(self, attr_map: dict[str, str]) -> None:
        name = attr_map.get("name")
        if not name or "disabled" in attr_map:
            self.current_select_name = None
            return

        self.current_select_name = html.unescape(name)
        self.current_select_selected_value = None
        self.current_select_first_value = None

    def _handle_option(self, attr_map: dict[str, str]) -> None:
        if self.current_select_name is None or "disabled" in attr_map:
            return

        value = html.unescape(attr_map.get("value", ""))
        if self.current_select_first_value is None:
            self.current_select_first_value = value
        if "selected" in attr_map:
            self.current_select_selected_value = value

    def _finish_select(self) -> None:
        if self.current_select_name is not None:
            value = self.current_select_selected_value
            if value is None:
                value = self.current_select_first_value or ""
            self.fields.append((self.current_select_name, value))

        self.current_select_name = None
        self.current_select_selected_value = None
        self.current_select_first_value = None

    def _capture_page_links(self, attr_map: dict[str, str]) -> None:
        link_id = attr_map.get("id")
        href = attr_map.get("href", "")
        normalized_href = href[2:] if href.startswith("./") else href
        page_value = attr_map.get("value")

        if normalized_href != ROUTE_SEARCH_PATH or not page_value:
            return

        if self.next_page is None and link_id in {"next", "next2"}:
            self.next_page = html.unescape(page_value)
        elif self.previous_page is None and link_id in {"back", "back2"}:
            self.previous_page = html.unescape(page_value)


class RouteSearchClient:
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
                "User-Agent": "bus-widget-api route search research",
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": self.base_url + "/" + PATHWAY_PATH,
            },
        )
        with self.opener.open(request, timeout=30) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")


def save_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"saved {path.as_posix()}")


def save_json(path: Path, data: object) -> None:
    save_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def find_checked_diaseq(page: str) -> str:
    match = re.search(r'name="diaseq"\s+value="([^"]+)"\s+checked', page)
    if not match:
        match = re.search(r'name="diaseq"\s+value="([^"]+)"', page)
    if not match:
        raise RuntimeError("Could not find diaseq in pathway.php")
    return html.unescape(match.group(1))


def build_form_data(case: SearchCase, *, diaseq: str, hour: str, minute: str) -> dict[str, str]:
    # The Hokutetsu form uses arr_mei for the departure field and dep_mei for arrival.
    return {
        "diaseq": diaseq,
        "landmark1": "0",
        "arr_mei": case.departure,
        "landmark2": "0",
        "dep_mei": case.arrival,
        "weekday": case.weekday,
        "pathway": case.pathway,
        "hour": hour,
        "min": minute,
    }


def normalize_form_data_for_json(data: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"name": name, "value": value} for name, value in data]


def find_result_range(page: str) -> dict[str, int | str] | None:
    match = re.search(r"[（(]\s*(\d+)\s*[～~\-]\s*(\d+)\s*[／/]\s*(\d+)\s*件\s*[）)]", page)
    if not match:
        return None

    return {
        "text": match.group(0),
        "start": int(match.group(1)),
        "end": int(match.group(2)),
        "total": int(match.group(3)),
    }


def parse_route_search_form(page: str) -> RouteSearchFormState:
    parser = RouteSearchFormParser()
    parser.feed(page)

    if not parser.found_form:
        raise RuntimeError("Could not find form1 in route search response")

    return RouteSearchFormState(
        fields=parser.fields,
        next_page=parser.next_page,
        previous_page=parser.previous_page,
    )


def build_page_form_data(
    form_data: list[tuple[str, str]], *, arrow: str, page: str
) -> list[tuple[str, str]]:
    page_data = [(name, value) for name, value in form_data if name not in {"arrow", "page"}]
    page_data.append(("arrow", arrow))
    page_data.append(("page", page))
    return page_data


def build_next_page_form_data(form_data: list[tuple[str, str]], *, page: str) -> list[tuple[str, str]]:
    return build_page_form_data(form_data, arrow="next", page=page)


def run(debug_dir: Path, *, hour: str, minute: str) -> None:
    client = RouteSearchClient()
    debug_dir.mkdir(parents=True, exist_ok=True)

    summary: dict[str, object] = {
        "base_url": BASE_URL,
        "note": "Research only. This script does not update schedule.json or bus.db.",
        "endpoints": {
            "pathway_form": PATHWAY_PATH,
            "stop_resolver": STOP_RESOLVE_PATH,
            "route_search": ROUTE_SEARCH_PATH,
        },
        "cases": [],
    }

    print(f"[1] GET {PATHWAY_PATH}")
    pathway_html = client.get_text(PATHWAY_PATH)
    pathway_file = debug_dir / "01_pathway.html"
    save_text(pathway_file, pathway_html)
    diaseq = find_checked_diaseq(pathway_html)
    summary["diaseq"] = diaseq

    for index, case in enumerate(SEARCH_CASES, start=2):
        form_data = build_form_data(case, diaseq=diaseq, hour=hour, minute=minute)
        prefix = f"{index:02d}_{case.key}"

        resolve_request_file = debug_dir / f"{prefix}_stop_resolver_request.json"
        resolve_response_file = debug_dir / f"{prefix}_stop_resolver_response.txt"
        route_request_file = debug_dir / f"{prefix}_route_search_request.json"
        route_response_file = debug_dir / f"{prefix}_route_search_response.html"
        next_page_request_file = debug_dir / f"{prefix}_route_search_next_page_request.json"
        next_page_response_file = debug_dir / f"{prefix}_route_search_next_page_response.html"
        page_01_response_file = debug_dir / f"{prefix}_route_search_page_01_response.html"

        print(
            f"[{index}] POST {STOP_RESOLVE_PATH} "
            f"departure={case.departure} arrival={case.arrival} weekday={case.weekday}"
        )
        save_json(
            resolve_request_file,
            {
                "url": BASE_URL + "/" + STOP_RESOLVE_PATH,
                "method": "POST",
                "data": form_data,
            },
        )
        resolve_response = client.post_text(STOP_RESOLVE_PATH, form_data)
        save_text(resolve_response_file, resolve_response)

        print(
            f"[{index}] POST {ROUTE_SEARCH_PATH} "
            f"departure={case.departure} arrival={case.arrival} weekday={case.weekday}"
        )
        save_json(
            route_request_file,
            {
                "url": BASE_URL + "/" + ROUTE_SEARCH_PATH,
                "method": "POST",
                "data": form_data,
            },
        )
        route_response = client.post_text(ROUTE_SEARCH_PATH, form_data)
        save_text(route_response_file, route_response)

        first_page_response = route_response
        try:
            form_state = parse_route_search_form(first_page_response)
            if form_state.previous_page:
                previous_page_data = build_page_form_data(
                    form_state.fields,
                    arrow="back",
                    page=form_state.previous_page,
                )
                first_page_response = client.post_text(ROUTE_SEARCH_PATH, previous_page_data)
        except Exception as exc:
            print(f"[{index}] WARNING: first page rewind failed: {exc}", file=sys.stderr)

        save_text(page_01_response_file, first_page_response)

        max_pages = 20

        def result_range_key(result_range: dict[str, int | str] | None) -> tuple[int, int, int] | None:
            if result_range is None:
                return None
            return (
                int(result_range["start"]),
                int(result_range["end"]),
                int(result_range["total"]),
            )

        first_result_range = find_result_range(first_page_response)
        if first_result_range:
            print(f"[{index}] page_01 result_range={first_result_range['text']}")
        pages: list[dict[str, object]] = [
            {
                "page_index": 1,
                "request_page_value": None,
                "result_range": first_result_range,
                "response_file": str(page_01_response_file),
                "request_file": str(route_request_file),
                "advanced": True,
            }
        ]

        seen_result_ranges: set[tuple[int, int, int]] = set()
        first_key = result_range_key(first_result_range)
        if first_key is not None:
            seen_result_ranges.add(first_key)

        next_page_summary: dict[str, object] = {
            "found": False,
            "first_result_range": first_result_range,
        }

        current_response = first_page_response
        previous_result_range = first_result_range
        pagination_stop_reason = "max_pages_reached"

        for page_index in range(2, max_pages + 1):
            try:
                form_state = parse_route_search_form(current_response)
                next_page = form_state.next_page

                if not next_page:
                    pagination_stop_reason = "next_page_not_found"
                    print(f"[{index}] Next page link not found; stopped")
                    break

                page_request_file = debug_dir / f"{prefix}_route_search_page_{page_index:02d}_request.json"
                page_response_file = debug_dir / f"{prefix}_route_search_page_{page_index:02d}_response.html"

                next_page_data = build_next_page_form_data(form_state.fields, page=next_page)
                request_payload = {
                    "url": BASE_URL + "/" + ROUTE_SEARCH_PATH,
                    "method": "POST",
                    "data_format": "ordered name/value pairs; duplicate names are preserved",
                    "data": normalize_form_data_for_json(next_page_data),
                }

                save_json(page_request_file, request_payload)
                if page_index == 2:
                    save_json(next_page_request_file, request_payload)

                page_response = client.post_text(ROUTE_SEARCH_PATH, next_page_data)
                save_text(page_response_file, page_response)
                if page_index == 2:
                    save_text(next_page_response_file, page_response)

                result_range = find_result_range(page_response)
                if result_range:
                    print(f"[{index}] page_{page_index:02d} result_range={result_range['text']}")
                previous_key = result_range_key(previous_result_range)
                current_key = result_range_key(result_range)
                duplicate = current_key is not None and current_key in seen_result_ranges
                advanced = current_key is not None and current_key != previous_key and not duplicate

                page_record: dict[str, object] = {
                    "page_index": page_index,
                    "request_page_value": next_page,
                    "result_range": result_range,
                    "response_file": str(page_response_file),
                    "request_file": str(page_request_file),
                    "advanced": advanced,
                }

                if current_key is None:
                    pagination_stop_reason = "result_range_not_found"
                    page_record["warning"] = "Could not find result range in response."
                elif duplicate:
                    pagination_stop_reason = "duplicate_result_range"
                    page_record["warning"] = "Result range was already seen."
                elif current_key == previous_key:
                    pagination_stop_reason = "result_range_not_advanced"
                    page_record["warning"] = "Result range did not advance from previous page."

                pages.append(page_record)

                if page_index == 2:
                    next_page_summary = {
                        "found": True,
                        "page": next_page,
                        "restored_form_field_count": len(form_state.fields),
                        "request_data": normalize_form_data_for_json(next_page_data),
                        "first_result_range": first_result_range,
                        "next_result_range": result_range,
                        "advanced": advanced,
                        "files": {
                            "route_search_next_page_request": str(next_page_request_file),
                            "route_search_next_page_response": str(next_page_response_file),
                        },
                    }

                if not advanced:
                    print(f"[{index}] Pagination stopped: {pagination_stop_reason}")
                    break

                seen_result_ranges.add(current_key)
                current_response = page_response
                previous_result_range = result_range

            except Exception as exc:
                pagination_stop_reason = "page_fetch_failed"
                next_page_summary["error"] = str(exc)
                print(f"[{index}] WARNING: page fetch failed: {exc}", file=sys.stderr)
                break

        next_page_summary["stop_reason"] = pagination_stop_reason

        summary["cases"].append(
            {
                "key": case.key,
                "departure": case.departure,
                "arrival": case.arrival,
                "weekday": case.weekday,
                "pathway": case.pathway,
                "condition": "departure",
                "time": f"{hour}:{minute}",
                "request_data": form_data,
                "files": {
                    "stop_resolver_request": str(resolve_request_file),
                    "stop_resolver_response": str(resolve_response_file),
                    "route_search_request": str(route_request_file),
                    "route_search_response": str(route_response_file),
                    "route_search_page_01_response": str(page_01_response_file),
                },
                "next_page": next_page_summary,
                "pages": pages,
                "pagination_stop_reason": pagination_stop_reason,
            }
        )

    summary_file = debug_dir / "00_route_search_summary.json"
    save_json(summary_file, summary)
    print(f"Saved {summary_file.as_posix()}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Research Hokutetsu route-search form responses.")
    parser.add_argument("--debug-dir", type=Path, default=DEFAULT_DEBUG_DIR)
    parser.add_argument("--hour", default="07")
    parser.add_argument("--minute", default="00")
    args = parser.parse_args()

    try:
        run(args.debug_dir, hour=args.hour.zfill(2), minute=args.minute.zfill(2))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())