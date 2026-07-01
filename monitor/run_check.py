"""Run the schedule monitor fetch, compare, and candidate print steps."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

MONITOR_DIR = Path(__file__).resolve().parent
ROOT_DIR = MONITOR_DIR.parent
FETCH_SCRIPT = MONITOR_DIR / "fetch_schedule.py"
COMPARE_SCRIPT = MONITOR_DIR / "compare_schedule.py"
PRINT_SCRIPT = MONITOR_DIR / "print_update_candidates.py"
UPDATE_CANDIDATES = MONITOR_DIR / "update_candidates.json"


class StepError(RuntimeError):
    pass


def run_step(label: str, command: list[str], *, allow_codes: set[int] | None = None) -> int:
    allowed = allow_codes or {0}
    print(f"\n== {label} ==")
    try:
        result = subprocess.run(
            command,
            cwd=ROOT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise StepError(f"{label} failed to start: {exc}") from exc

    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)

    if result.returncode not in allowed:
        raise StepError(f"{label} failed with exit code {result.returncode}.")
    return result.returncode


def load_update_candidates() -> dict:
    try:
        with UPDATE_CANDIDATES.open(encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError as exc:
        raise StepError(f"Update candidates file was not created: {UPDATE_CANDIDATES}") from exc
    except json.JSONDecodeError as exc:
        raise StepError(f"Update candidates file is not valid JSON: {UPDATE_CANDIDATES}") from exc


def print_candidate_summary(candidates: dict) -> None:
    summary = candidates.get("summary", {})
    added_count = int(summary.get("added_count", 0))
    removed_count = int(summary.get("removed_count", 0))

    print("\n== Summary ==")
    if added_count == 0 and removed_count == 0:
        print("更新候補なし")
        return

    print(f"更新候補あり: added={added_count}, removed={removed_count}")


def main() -> int:
    python = sys.executable

    try:
        run_step("Fetch schedule", [python, str(FETCH_SCRIPT)])
        # compare_schedule.py returns 1 when differences exist. That is a normal
        # monitor result, not a runner failure.
        run_step("Compare schedule", [python, str(COMPARE_SCRIPT)], allow_codes={0, 1})
        run_step("Print update candidates", [python, str(PRINT_SCRIPT)])
        print_candidate_summary(load_update_candidates())
    except StepError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
