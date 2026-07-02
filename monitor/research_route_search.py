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


SEARCH_CASES: tuple[SearchCase, ...] = (
    SearchCase("kanazawa_station_to_uni_weekday", "金沢駅", "金沢工業大学"),
    SearchCase("uni_to_kanazawa_station_weekday", "金沢工業大学", "金沢駅"),
    SearchCase("uni_to_nakahashi_weekday", "金沢工業大学", "中橋"),
)


class RouteSearchClient:
    def __init__(self, base_url: str = BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self.opener = build_opener()

    def get_text(self, path: str) -> str:
        return self._request(path)

    def post_text(self, path: str, data: dict[str, str]) -> str:
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
                },
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
