"""Run bus timetable monitors and notify Discord when logical diffs change."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = PROJECT_ROOT / ".monitor_state" / "last_diff_hash.txt"
UPDATE_CANDIDATES_PATH = PROJECT_ROOT / "monitor" / "update_candidates.json"
ROUTE_SEARCH_COMPARE_PATH = PROJECT_ROOT / "monitor" / "debug" / "route_search_compare.json"

NORMAL_SUMMARY_KEYS = ("added_count", "removed_count")
ROUTE_SUMMARY_KEYS = (
    "added_count",
    "removed_count",
    "line_only_count",
    "time_change_candidate_count",
)
ROUTE_SEARCH_DIFF_KEYS = (
    "added",
    "removed",
    "line_only",
    "time_change_candidates",
)


class MonitorError(RuntimeError):
    pass


def run_step(label: str, command: list[str]) -> None:
    print(f"\n== {label} ==")
    print("$ " + " ".join(command), flush=True)
    completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    if completed.returncode != 0:
        raise MonitorError(f"{label} failed with exit code {completed.returncode}")


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise MonitorError(f"required JSON file was not created: {path.relative_to(PROJECT_ROOT).as_posix()}")
    try:
        with path.open(encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError as exc:
        raise MonitorError(f"invalid JSON file: {path.relative_to(PROJECT_ROOT).as_posix()}") from exc
    if not isinstance(data, dict):
        raise MonitorError(f"JSON root must be an object: {path.relative_to(PROJECT_ROOT).as_posix()}")
    return data


def required_summary_counts(data: dict[str, Any], keys: tuple[str, ...], *, label: str) -> dict[str, int]:
    summary = data.get("summary")
    if not isinstance(summary, dict):
        raise MonitorError(f"{label} JSON is missing summary object")

    counts: dict[str, int] = {}
    for key in keys:
        if key not in summary:
            raise MonitorError(f"{label} summary is missing required key: {key}")
        try:
            counts[key] = int(summary[key])
        except (TypeError, ValueError) as exc:
            raise MonitorError(f"{label} summary key is not an integer: {key}") from exc
    return counts


def require_routes(data: dict[str, Any], *, label: str) -> dict[str, Any]:
    routes = data.get("routes")
    if not isinstance(routes, dict):
        raise MonitorError(f"{label} JSON is missing routes object")
    return routes


def canonical_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): canonical_value(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [canonical_value(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(canonical_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normal_diff_payload(update_candidates: dict[str, Any]) -> dict[str, Any]:
    routes = require_routes(update_candidates, label="update_candidates")
    return canonical_value(routes)


def route_search_diff_payload(route_search_compare: dict[str, Any]) -> dict[str, Any]:
    routes = require_routes(route_search_compare, label="route_search_compare")
    payload: dict[str, Any] = {}
    for route in sorted(routes):
        days = routes.get(route)
        if not isinstance(days, dict):
            continue
        route_payload: dict[str, Any] = {}
        for day_type in sorted(days):
            detail = days.get(day_type)
            if not isinstance(detail, dict):
                continue
            route_payload[day_type] = {
                key: canonical_value(detail.get(key, []))
                for key in ROUTE_SEARCH_DIFF_KEYS
            }
        if route_payload:
            payload[route] = route_payload
    return payload


def build_status(update_candidates: dict[str, Any], route_search_compare: dict[str, Any]) -> dict[str, Any]:
    normal_counts = required_summary_counts(update_candidates, NORMAL_SUMMARY_KEYS, label="update_candidates")
    route_counts = required_summary_counts(route_search_compare, ROUTE_SUMMARY_KEYS, label="route_search_compare")

    has_diff = any(normal_counts.values()) or any(route_counts.values())
    logical_diff = {
        "normal": normal_diff_payload(update_candidates),
        "route_search": route_search_diff_payload(route_search_compare),
    }
    fingerprint = hashlib.sha256(canonical_json(logical_diff).encode("utf-8")).hexdigest()

    return {
        "has_diff": has_diff,
        "fingerprint": fingerprint,
        "normal_counts": normal_counts,
        "route_counts": route_counts,
    }


def read_previous_fingerprint(path: Path = STATE_PATH) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return value or None


def save_fingerprint(fingerprint: str, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fingerprint + "\n", encoding="utf-8")


def build_run_url() -> str:
    server_url = os.environ.get("GITHUB_SERVER_URL")
    repository = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if server_url and repository and run_id:
        return f"{server_url}/{repository}/actions/runs/{run_id}"
    return "(GitHub Actions run URL unavailable)"


def build_discord_message(status: dict[str, Any]) -> str:
    normal_counts = status["normal_counts"]
    route_counts = status["route_counts"]
    return "\n".join(
        [
            "【バス時刻表変更候補】",
            "",
            "前回の監視結果から変更を検出しました。",
            "",
            "通常monitor",
            f"追加: {normal_counts['added_count']}",
            f"削除: {normal_counts['removed_count']}",
            "",
            "route search",
            f"追加: {route_counts['added_count']}",
            f"削除: {route_counts['removed_count']}",
            f"系統差: {route_counts['line_only_count']}",
            f"時刻変更候補: {route_counts['time_change_candidate_count']}",
            "",
            "schedule.json / bus.db は自動更新されていません。",
            "人間による確認が必要です。",
            "",
            "GitHub Actions:",
            build_run_url(),
        ]
    )


def send_discord_notification(message: str) -> None:
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        raise MonitorError("DISCORD_WEBHOOK_URL is not set; Discord notification was not sent")

    body = json.dumps({"content": message}, ensure_ascii=False).encode("utf-8")
    request = Request(
        webhook_url,
        data=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "DiscordBot (https://github.com/yuki80180/bus-widget-api, 1.0)",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            status_code = response.getcode()
            if not 200 <= status_code < 300:
                raise MonitorError(f"Discord notification failed with HTTP status {status_code}")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise MonitorError(f"Discord notification failed with HTTP status {exc.code}: {detail}") from exc
    except URLError as exc:
        raise MonitorError(f"Discord notification failed: {exc.reason}") from exc


def run_monitors() -> None:
    python = sys.executable
    run_step("Run schedule monitor", [python, "monitor/run_check.py"])
    run_step("Run route search monitor", [python, "monitor/run_route_search_check.py"])


def main() -> int:
    try:
        run_monitors()
        update_candidates = load_json(UPDATE_CANDIDATES_PATH)
        route_search_compare = load_json(ROUTE_SEARCH_COMPARE_PATH)
        status = build_status(update_candidates, route_search_compare)

        previous_fingerprint = read_previous_fingerprint()
        current_fingerprint = status["fingerprint"]
        has_diff = bool(status["has_diff"])
        changed_from_previous = previous_fingerprint != current_fingerprint

        print("\n== Automated monitor summary ==")
        print(f"has_diff={str(has_diff).lower()}")
        print(f"fingerprint={current_fingerprint}")
        print(f"previous_fingerprint={previous_fingerprint or '(none)'}")

        if not changed_from_previous:
            print("Diff fingerprint is unchanged. No Discord notification.")
            save_fingerprint(current_fingerprint)
            print("State saved.")
            return 0

        if has_diff:
            print("Diff fingerprint changed and current diff exists. Sending Discord notification.")
            send_discord_notification(build_discord_message(status))
            save_fingerprint(current_fingerprint)
            print("Discord notification sent. State saved.")
            return 0

        print("Diff fingerprint changed to no-diff state. No Discord notification.")
        save_fingerprint(current_fingerprint)
        print("State saved.")
        return 0

    except MonitorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
