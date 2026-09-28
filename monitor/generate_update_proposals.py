"""Generate dry-run schedule update proposals from route-search differences.

The generator reads existing monitor output and schedule.json, then writes a
machine-readable proposal report.  It never writes schedule.json, bus.db, or
production data.  Proposals are intentionally metadata for a future approval
flow; this module contains no apply operation.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:  # Support both ``python monitor/...`` and ``import monitor...``.
    from .compare_route_search_normalized import (
        MAX_TIME_CHANGE_CANDIDATE_MINUTES, normalize_line_for_compare,
    )
    from .run_automated_monitor import canonical_json, canonical_value
except ImportError:  # pragma: no cover - exercised by the CLI invocation.
    from compare_route_search_normalized import (
        MAX_TIME_CHANGE_CANDIDATE_MINUTES, normalize_line_for_compare,
    )
    from run_automated_monitor import canonical_json, canonical_value


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MONITOR_DIR = Path(__file__).resolve().parent

DEFAULT_COMPARISON_PATH = MONITOR_DIR / "debug" / "route_search_compare.json"
DEFAULT_SCHEDULE_PATH = PROJECT_ROOT / "schedule.json"
DEFAULT_REVIEWED_PATH = MONITOR_DIR / "route_search_reviewed_candidates.json"
GENERATED_DIR = MONITOR_DIR / "generated"
DEFAULT_OUTPUT_PATH = GENERATED_DIR / "update_proposals.json"

PROPOSAL_VERSION = 1
STATUS_VALUES = ("pending", "needs_review", "approved", "rejected")
VALID_DIRECTIONS = {"to_uni", "to_station", "to_nakahashi"}
DAY_TYPES = {"weekday", "weekend"}
VALID_STOPS = {"A", "B", "C", "D"}
ROUTE_CATEGORIES = ("added", "removed", "line_only", "time_change_candidates", "arrival_time_changes")

TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$", re.ASCII)
LINE_ID_PATTERN = re.compile(r"^\d+[A-Za-z]?$", re.ASCII)
SCHEDULE_LINE_PATTERN = re.compile(r"^\([0-9]+\) [^\r\n|]+$")

Bus = dict[str, str | None]
Schedule = dict[str, dict[str, list[Bus]]]
ReviewIndex = dict[tuple[str, ...], dict[str, Any]]


def stable_id(value: Any) -> str:
    """Create a process-independent SHA-256 identifier."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def generated_at_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_json_object(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        data = json.load(file, parse_constant=reject_json_constant)
    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return data


def reject_json_constant(value: str) -> Any:
    raise ValueError(f"non-standard JSON constant: {value}")


def display_source_path(path: Path) -> str:
    """Prefer a repository-relative source path for portable generated JSON."""

    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def validate_output_path(path: Path, input_paths: list[Path]) -> Path:
    """Allow report JSON only in generated/, never through an input alias."""
    root = GENERATED_DIR.absolute()
    if root.resolve() != root:
        raise ValueError("generated directory must not be a symlink or junction")
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or resolved.suffix.lower() != ".json":
        raise ValueError(f"output must be a .json file under {root}")
    protected = input_paths + [DEFAULT_SCHEDULE_PATH, PROJECT_ROOT / "bus.db", DEFAULT_REVIEWED_PATH]
    for source in protected:
        if resolved == source.resolve() or (
            resolved.exists() and source.exists() and resolved.samefile(source)
        ):
            raise ValueError(f"output must not overwrite an input or protected file: {source}")
    if resolved.exists() and resolved.stat().st_nlink > 1:
        raise ValueError("output must not be a hard link")
    return resolved


def write_report(path: Path, report: dict[str, Any], input_paths: list[Path]) -> None:
    output = validate_output_path(path, input_paths)
    serialized = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=".proposal-", suffix=".tmp", delete=False) as file:
            temporary_path = Path(file.name)
            file.write(serialized)
        validate_output_path(path, input_paths)
        os.replace(temporary_path, output)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def text(value: object) -> str:
    return value if isinstance(value, str) else ""


def bus_from(value: object) -> Bus | None:
    if not isinstance(value, dict):
        return None
    return {
        "time": text(value.get("time")),
        "line": text(value.get("line")),
        "stop": text(value.get("stop")),
        **({"arrival_time": value["arrival_time"]} if "arrival_time" in value else {}),
    }


def same_bus(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return bus_key(left) == bus_key(right) and left.get("arrival_time") == right.get("arrival_time")


def bus_key(bus: dict[str, Any]) -> tuple[str, str, str]:
    return (text(bus.get("time")), text(bus.get("line")), text(bus.get("stop")))


def bus_sort_key(bus: dict[str, Any]) -> tuple[str, str, str]:
    return (text(bus.get("time")), text(bus.get("stop")), text(bus.get("line")))


def is_valid_time(value: object) -> bool:
    return isinstance(value, str) and TIME_PATTERN.fullmatch(value) is not None


def time_to_minutes(value: str) -> int:
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def normalize_candidate_line(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return normalize_line_for_compare(value) or ""


def is_valid_line(value: object) -> bool:
    return isinstance(value, str) and (
        LINE_ID_PATTERN.fullmatch(value) is not None
        or SCHEDULE_LINE_PATTERN.fullmatch(value) is not None
    )


def record_context_errors(item: object, direction: str, day_type: str) -> list[str]:
    if not isinstance(item, dict):
        return []
    errors = []
    for key in ("direction", "route"):
        if key in item and item[key] != direction:
            errors.append(f"{key}_context_mismatch")
    if "day_type" in item and item["day_type"] != day_type:
        errors.append("day_type_context_mismatch")
    return errors


def validate_schedule_structure(schedule: object) -> list[str]:
    """Validate the baseline before any proposal can hide a malformed row."""
    if not isinstance(schedule, dict):
        return ["schedule must be an object"]
    errors = []
    for direction, days in schedule.items():
        if direction not in VALID_DIRECTIONS:
            errors.append(f"{direction}: unknown direction")
        if not isinstance(days, dict):
            errors.append(f"{direction}: day types must be an object")
            continue
        for day_type, buses in days.items():
            label = f"{direction}/{day_type}"
            if day_type not in DAY_TYPES:
                errors.append(f"{label}: invalid day_type")
            if not isinstance(buses, list):
                errors.append(f"{label}: buses must be an array")
                continue
            seen = set()
            for index, bus in enumerate(buses):
                if not isinstance(bus, dict):
                    errors.append(f"{label}[{index}]: bus must be an object")
                    continue
                codes = candidate_bus_errors(bus, stops=VALID_STOPS)
                if not isinstance(bus.get("line"), str) or not SCHEDULE_LINE_PATTERN.fullmatch(bus["line"]):
                    codes.append("invalid_schedule_line")
                errors.extend(f"{label}[{index}]: {code}" for code in codes)
                if bus_key(bus) in seen:
                    errors.append(f"{label}[{index}]: duplicate bus")
                seen.add(bus_key(bus))
    if schedule_bus_count(schedule) == 0:
        errors.append("schedule must contain at least one bus")
    return sorted(set(errors))


def schedule_bus_count(schedule: dict[str, Any]) -> int:
    total = 0
    for days in schedule.values():
        if not isinstance(days, dict):
            continue
        for buses in days.values():
            if isinstance(buses, list):
                total += len(buses)
    return total


def context_errors(
    schedule: dict[str, Any],
    direction: object,
    day_type: object,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(direction, str) or direction not in VALID_DIRECTIONS:
        errors.append("unknown_direction")
    elif direction not in schedule:
        errors.append("direction_missing_from_schedule")
    if not isinstance(day_type, str) or day_type not in DAY_TYPES:
        errors.append("invalid_day_type")
    elif isinstance(direction, str) and direction in schedule:
        days = schedule.get(direction)
        if not isinstance(days, dict) or day_type not in days:
            errors.append("day_type_missing_from_schedule")
    return errors


def candidate_bus_errors(
    item: object,
    *,
    stops: set[str],
    time_field: str = "time",
    line_field: str = "line",
    stop_field: str = "stop",
) -> list[str]:
    if not isinstance(item, dict):
        return ["candidate_must_be_object"]

    errors: list[str] = []
    if not is_valid_time(item.get(time_field)):
        errors.append(f"invalid_{time_field}")
    if item.get("arrival_time") is not None and not is_valid_time(item["arrival_time"]):
        errors.append("invalid_arrival_time")
    if not is_valid_line(item.get(line_field)):
        errors.append(f"invalid_{line_field}")
    stop = item.get(stop_field)
    if not isinstance(stop, str) or stop not in stops:
        errors.append(f"unknown_{stop_field}")
    return errors


def schedule_items(
    schedule: dict[str, Any],
    direction: str,
    day_type: str,
) -> list[dict[str, Any]]:
    days = schedule.get(direction)
    if not isinstance(days, dict):
        return []
    buses = days.get(day_type)
    if not isinstance(buses, list):
        return []
    return [bus for bus in buses if isinstance(bus, dict)]


def direct_review_key(
    direction: str,
    day_type: str,
    candidate_type: str,
    item: dict[str, Any],
) -> tuple[str, ...]:
    return (
        direction,
        day_type,
        candidate_type,
        text(item.get("time")),
        normalize_candidate_line(item.get("line")),
        text(item.get("stop")),
    )


def time_change_review_key(
    direction: str,
    day_type: str,
    item: dict[str, Any],
) -> tuple[str, ...]:
    return (
        direction,
        day_type,
        "time_change",
        text(item.get("old_time")),
        text(item.get("new_time")),
        normalize_candidate_line(item.get("line")),
        text(item.get("stop")),
    )


def build_review_index(reviewed_data: dict[str, Any] | None) -> ReviewIndex:
    if reviewed_data is None:
        return {}
    if type(reviewed_data.get("schema_version")) is not int or reviewed_data["schema_version"] != 1:
        raise ValueError("reviewed candidates JSON must use schema_version 1")
    if reviewed_data.get("scope") != "route_search":
        raise ValueError("reviewed candidates JSON scope must be 'route_search'")
    reviewed = reviewed_data.get("reviewed", [])
    if not isinstance(reviewed, list):
        raise ValueError("reviewed candidates JSON field 'reviewed' must be an array")

    index: ReviewIndex = {}
    for item in sorted(reviewed, key=canonical_json):
        if not isinstance(item, dict):
            continue
        key_data = item.get("key")
        if not isinstance(key_data, dict):
            continue
        direction = text(item.get("route"))
        day_type = text(item.get("day_type"))
        candidate_type = text(item.get("type"))
        line = text(key_data.get("line_normalized"))
        stop = text(key_data.get("stop"))
        if candidate_type in {"added", "removed"}:
            key = (
                direction,
                day_type,
                candidate_type,
                text(key_data.get("time")),
                line,
                stop,
            )
        elif candidate_type == "time_change":
            key = (
                direction,
                day_type,
                candidate_type,
                text(key_data.get("old_time")),
                text(key_data.get("new_time")),
                line,
                stop,
            )
        else:
            continue
        if key in index and canonical_json(index[key]) != canonical_json(item):
            raise ValueError(f"conflicting reviewed candidate entries for {key}")
        index[key] = item
    return index


def review_snapshot(reviewed_item: dict[str, Any] | None) -> dict[str, Any]:
    if reviewed_item is None:
        return {"matched": False, "candidate_status": "unreviewed"}

    result: dict[str, Any] = {
        "matched": True,
        "candidate_status": text(reviewed_item.get("status")) or "unknown",
    }
    if isinstance(reviewed_item.get("source"), str):
        result["source"] = reviewed_item["source"]
    if isinstance(reviewed_item.get("reviewed_note"), str):
        result["reviewed_note"] = reviewed_item["reviewed_note"]
    return result


def proposal_status(reviewed_item: dict[str, Any] | None) -> str:
    # A confirmed source candidate is ready for proposal review, not approved.
    if reviewed_item is not None and reviewed_item.get("status") == "confirmed":
        return "pending"
    return "needs_review"


def make_proposal(
    *,
    direction: str,
    day_type: str,
    change_type: str,
    changes: list[dict[str, Any]],
    source_path: str,
    reviewed_item: dict[str, Any] | None,
) -> dict[str, Any]:
    identity = {
        "proposal_version": PROPOSAL_VERSION,
        "direction": direction,
        "day_type": day_type,
        "change_type": change_type,
        "changes": changes,
    }
    return {
        "proposal_id": stable_id(identity),
        "direction": direction,
        "day_type": day_type,
        "change_type": change_type,
        "source_category": {
            "add": "added",
            "remove": "removed",
            "time_change": "time_change_candidates",
            "arrival_time_change": "arrival_time_changes",
        }.get(change_type, change_type),
        "status": proposal_status(reviewed_item),
        "source": source_path,
        "review": review_snapshot(reviewed_item),
        "changes": changes,
    }


def make_validation_error(
    *,
    direction: object,
    day_type: object,
    candidate_type: str,
    candidate: object,
    codes: Iterable[str],
) -> dict[str, Any]:
    unique_codes = sorted(set(codes))
    identity = {
        "direction": direction,
        "day_type": day_type,
        "candidate_type": candidate_type,
        "candidate": canonical_value(candidate),
        "codes": unique_codes,
    }
    return {
        "validation_id": stable_id(identity),
        "direction": direction,
        "day_type": day_type,
        "candidate_type": candidate_type,
        "status": "needs_review",
        "candidate": canonical_value(candidate),
        "codes": unique_codes,
    }


def proposal_sort_key(proposal: dict[str, Any]) -> tuple[str, ...]:
    changes = proposal.get("changes", [])
    first = changes[0] if isinstance(changes, list) and changes else {}
    before = first.get("before") if isinstance(first, dict) else None
    after = first.get("after") if isinstance(first, dict) else None
    before_bus = before if isinstance(before, dict) else {}
    after_bus = after if isinstance(after, dict) else {}
    return (
        text(proposal.get("direction")),
        text(proposal.get("day_type")),
        min(text(before_bus.get("time")) or "99:99", text(after_bus.get("time")) or "99:99"),
        text(proposal.get("change_type")),
        text(before_bus.get("time")),
        text(after_bus.get("time")),
        text(proposal.get("proposal_id")),
    )


def validation_sort_key(error: dict[str, Any]) -> tuple[str, ...]:
    candidate = error.get("candidate")
    candidate_dict = candidate if isinstance(candidate, dict) else {}
    return (
        text(error.get("direction")),
        text(error.get("day_type")),
        text(error.get("candidate_type")),
        text(candidate_dict.get("time")) or text(candidate_dict.get("old_time")),
        text(error.get("validation_id")),
    )


def conflicting_proposal_ids(proposals: list[dict[str, Any]]) -> set[str]:
    owners: dict[tuple, set[str]] = defaultdict(set)
    for proposal in proposals:
        change = proposal["changes"][0]
        for side in ("before", "after"):
            bus = change[side]
            if bus is not None:
                # Different full labels of the same line/time/stop remain
                # separate candidates, but cannot be simulated together safely.
                key = (proposal["direction"], proposal["day_type"], side, component_key(bus))
                owners[key].add(proposal["proposal_id"])
    return {identity for identities in owners.values() if len(identities) > 1 for identity in identities}


def resolve_added_line(
    candidate_line: str,
    *,
    existing: list[dict[str, Any]],
    stop: str,
) -> tuple[str | None, str | None]:
    if SCHEDULE_LINE_PATTERN.match(candidate_line):
        return candidate_line, None

    normalized = normalize_candidate_line(candidate_line)
    matching_lines = sorted(
        {
            text(bus.get("line"))
            for bus in existing
            if text(bus.get("stop")) == stop
            and normalize_candidate_line(bus.get("line")) == normalized
            and text(bus.get("line"))
        }
    )
    if len(matching_lines) == 1:
        return matching_lines[0], None
    if not matching_lines:
        return None, "added_line_unresolved"
    return None, "added_line_ambiguous"


def component_key(item: object, *, time_field: str = "time") -> tuple[str, str, str]:
    if not isinstance(item, dict):
        return ("", "", "")
    return (
        text(item.get(time_field)),
        normalize_candidate_line(item.get("line")),
        text(item.get("stop")),
    )


def prepare_time_changes(
    candidates: list[Any], added: list[Any], removed: list[Any],
    schedule: dict[str, Any], direction: str, day_type: str,
) -> tuple[list[tuple[Any, list[str], Bus | None, Bus | None]], set[tuple], set[tuple]]:
    """Quarantine every ambiguous pairing before accepting any contender."""
    added_owners: dict[tuple, set[str]] = defaultdict(set)
    removed_owners: dict[tuple, set[str]] = defaultdict(set)
    prepared = []
    raw_added = {bus_key(item) for item in added if isinstance(item, dict)}
    raw_removed = {bus_key(item) for item in removed if isinstance(item, dict)}
    for candidate in candidates:
        codes, before, after = validate_time_change(
            candidate, schedule=schedule, direction=direction, day_type=day_type, stops=VALID_STOPS,
        )
        if isinstance(candidate, dict):
            old = candidate.get("existing_item")
            new = candidate.get("route_search_item")
            if not isinstance(old, dict) or bus_key(old) not in raw_removed:
                codes.append("time_change_removed_component_missing_or_mismatched")
            if not isinstance(new, dict) or bus_key(new) not in raw_added:
                codes.append("time_change_added_component_missing_or_mismatched")
            for raw, nested in ((added, new), (removed, old)):
                if isinstance(nested, dict):
                    for item in raw:
                        if isinstance(item, dict) and bus_key(item) == bus_key(nested):
                            codes.extend(record_context_errors(item, direction, day_type))
                            codes.extend(candidate_bus_errors(item, stops=VALID_STOPS))
                            if item.get("arrival_time") != nested.get("arrival_time"):
                                codes.append("time_change_component_arrival_mismatch")
            identity = canonical_json([bus_from(old), bus_from(new)])
            removed_owners[component_key(old)].add(identity)
            added_owners[component_key(new)].add(identity)
        prepared.append((candidate, codes, before, after))

    blocked_added: set[tuple] = set()
    blocked_removed: set[tuple] = set()
    for candidate, codes, _, _ in prepared:
        if not isinstance(candidate, dict):
            continue
        old_key = component_key(candidate.get("existing_item"))
        new_key = component_key(candidate.get("route_search_item"))
        if len(removed_owners[old_key]) > 1 or len(added_owners[new_key]) > 1:
            codes.append("time_change_component_has_multiple_pairings")
        if codes:
            blocked_removed.update([old_key, component_key(candidate, time_field="old_time")])
            blocked_added.update([new_key, component_key(candidate, time_field="new_time")])
    # An invalid duplicate also blocks its otherwise-valid counterpart.
    for candidate, codes, _, _ in prepared:
        if isinstance(candidate, dict) and not codes and (
            component_key(candidate.get("existing_item")) in blocked_removed
            or component_key(candidate.get("route_search_item")) in blocked_added
        ):
            codes.append("time_change_component_has_invalid_pairing")
    return prepared, blocked_added, blocked_removed


def candidate_list(
    detail: dict[str, Any],
    category: str,
    *,
    direction: str,
    day_type: str,
    add_error: Any,
) -> list[Any]:
    value = detail.get(category, [] if category == "arrival_time_changes" else None)
    if isinstance(value, list):
        return sorted(value, key=canonical_json)
    add_error(
        make_validation_error(
            direction=direction,
            day_type=day_type,
            candidate_type=category,
            candidate=value,
            codes=["candidate_list_must_be_array"],
        )
    )
    return []


def time_change_context(before: Bus, after: Bus, current: list[dict[str, Any]]) -> dict[str, Any]:
    """Expose neighbouring same-line departures without guessing a pairing."""
    peers = sorted([
        bus for bus in current
        if bus["stop"] == before["stop"]
        and normalize_candidate_line(bus["line"]) == normalize_candidate_line(before["line"])
        and bus_key(bus) != bus_key(before)
    ], key=bus_sort_key)
    previous = [bus for bus in peers if bus["time"] < before["time"]]
    following = [bus for bus in peers if bus["time"] > before["time"]]
    lower, upper = sorted([before["time"], after["time"]])
    return {
        "signed_difference_minutes": time_to_minutes(after["time"]) - time_to_minutes(before["time"]),
        "previous_bus": copy.deepcopy(previous[-1]) if previous else None,
        "next_bus": copy.deepcopy(following[0]) if following else None,
        "crosses_existing_departure": any(lower <= bus["time"] <= upper for bus in peers),
    }


def validate_time_change(
    candidate: object,
    *,
    schedule: dict[str, Any],
    direction: str,
    day_type: str,
    stops: set[str],
) -> tuple[list[str], Bus | None, Bus | None]:
    errors = context_errors(schedule, direction, day_type)
    if not isinstance(candidate, dict):
        return errors + ["candidate_must_be_object"], None, None
    errors.extend(record_context_errors(candidate, direction, day_type))
    errors.extend(record_context_errors(candidate.get("existing_item"), direction, day_type))
    errors.extend(record_context_errors(candidate.get("route_search_item"), direction, day_type))

    if not is_valid_time(candidate.get("old_time")):
        errors.append("invalid_old_time")
    if not is_valid_time(candidate.get("new_time")):
        errors.append("invalid_new_time")
    if not is_valid_line(candidate.get("line")):
        errors.append("invalid_line")
    stop = candidate.get("stop")
    if not isinstance(stop, str) or stop not in stops:
        errors.append("unknown_stop")

    existing_item = bus_from(candidate.get("existing_item"))
    route_search_item = bus_from(candidate.get("route_search_item"))
    if existing_item is None:
        errors.append("missing_existing_item")
    if route_search_item is None:
        errors.append("missing_route_search_item")

    old_time = text(candidate.get("old_time"))
    new_time = text(candidate.get("new_time"))
    line = normalize_candidate_line(candidate.get("line"))
    stop_text = text(stop)

    if existing_item is not None:
        if existing_item["time"] != old_time:
            errors.append("existing_item_old_time_mismatch")
        if existing_item["stop"] != stop_text:
            errors.append("existing_item_stop_mismatch")
        if normalize_candidate_line(existing_item["line"]) != line:
            errors.append("existing_item_line_mismatch")
        errors.extend(
            candidate_bus_errors(existing_item, stops=stops)
        )

    if route_search_item is not None:
        if route_search_item["time"] != new_time:
            errors.append("route_search_item_new_time_mismatch")
        if route_search_item["stop"] != stop_text:
            errors.append("route_search_item_stop_mismatch")
        if normalize_candidate_line(route_search_item["line"]) != line:
            errors.append("route_search_item_line_mismatch")
        if (existing_item is not None and SCHEDULE_LINE_PATTERN.fullmatch(route_search_item["line"])
                and route_search_item["line"] != existing_item["line"]):
            errors.append("time_change_full_line_changed")
        errors.extend(
            candidate_bus_errors(route_search_item, stops=stops)
        )

    if is_valid_time(old_time) and is_valid_time(new_time):
        if old_time == new_time:
            errors.append("time_change_has_same_time")
        expected_difference = abs(time_to_minutes(new_time) - time_to_minutes(old_time))
        if expected_difference > MAX_TIME_CHANGE_CANDIDATE_MINUTES:
            errors.append("time_change_exceeds_comparison_window")
        difference = candidate.get("difference_minutes")
        if type(difference) is not int or difference != expected_difference:
            errors.append("difference_minutes_mismatch")

    if not errors and existing_item is not None:
        current = schedule_items(schedule, direction, day_type)
        matches = [bus for bus in current if bus_key(bus) == bus_key(existing_item)]
        if not matches:
            errors.append("time_change_current_bus_missing_from_schedule")
        elif len(matches) > 1:
            errors.append("time_change_current_bus_duplicated_in_schedule")
        elif "arrival_time" in existing_item and not same_bus(matches[0], existing_item):
            errors.append("time_change_stale_arrival_time")
        else:
            existing_item = copy.deepcopy(matches[0])

    proposed_item: Bus | None = None
    if existing_item is not None and is_valid_time(new_time):
        proposed_item = dict(existing_item)
        proposed_item["time"] = new_time
        # A different departure is a different trip. Never carry its old arrival.
        if "arrival_time" in existing_item or (route_search_item and "arrival_time" in route_search_item):
            proposed_item["arrival_time"] = route_search_item.get("arrival_time") if route_search_item else None
        if not errors:
            current = schedule_items(schedule, direction, day_type)
            if any(component_key(bus) == component_key(proposed_item) for bus in current):
                errors.append("time_change_target_already_exists")

    return sorted(set(errors)), existing_item, proposed_item


def validate_arrival_change(candidate, schedule, direction, day_type):
    codes = context_errors(schedule, direction, day_type)
    if not isinstance(candidate, dict) or candidate.get("ambiguous"):
        return codes + ["ambiguous_arrival_time_change"], None, None
    old, new = bus_from(candidate.get("existing_item")), bus_from(candidate.get("route_search_item"))
    for item in (candidate, candidate.get("existing_item"), candidate.get("route_search_item")):
        codes.extend(record_context_errors(item, direction, day_type))
    for item in (old, new):
        codes.extend(candidate_bus_errors(item, stops=VALID_STOPS))
    if codes or old is None or new is None:
        return sorted(set(codes)), None, None
    if component_key(old) != component_key(new) or component_key(candidate) != component_key(old):
        codes.append("arrival_change_trip_mismatch")
    if SCHEDULE_LINE_PATTERN.fullmatch(new["line"]) and new["line"] != old["line"]:
        codes.append("arrival_change_full_line_changed")
    if new.get("arrival_time") is None or old.get("arrival_time") == new.get("arrival_time"):
        codes.append("arrival_change_requires_new_confirmed_time")
    current = schedule_items(schedule, direction, day_type)
    matches = [bus for bus in current if component_key(bus) == component_key(old)]
    if len(matches) != 1 or not same_bus(matches[0], old):
        codes.append("arrival_change_stale_or_ambiguous")
    if codes:
        return sorted(set(codes)), None, None
    before = copy.deepcopy(matches[0])
    return [], before, dict(before, arrival_time=new["arrival_time"])


def simulate_proposals(
    schedule: dict[str, Any],
    proposals: list[dict[str, Any]],
    *,
    include_candidate: bool = False,
) -> dict[str, Any]:
    """Apply proposals to an in-memory copy and validate the resulting schedule."""

    simulated = copy.deepcopy(schedule)
    errors = validate_schedule_structure(schedule)
    before_count = schedule_bus_count(schedule)
    simulated_count = 0

    for proposal in sorted(proposals, key=proposal_sort_key):
        proposal_id = text(proposal.get("proposal_id")) or "(missing-id)"
        direction = text(proposal.get("direction"))
        day_type = text(proposal.get("day_type"))
        days = simulated.get(direction)
        if (direction not in VALID_DIRECTIONS or day_type not in DAY_TYPES
                or not isinstance(days, dict) or not isinstance(days.get(day_type), list)):
            errors.append(f"{proposal_id}: simulation target route/day is missing")
            continue
        target = days[day_type]
        changes = proposal.get("changes")
        if not isinstance(changes, list) or len(changes) != 1:
            errors.append(f"{proposal_id}: changes must contain one atomic change")
            continue

        for change in changes:
            if not isinstance(change, dict):
                errors.append(f"{proposal_id}: change must be an object")
                continue
            operation = change.get("operation")
            before = bus_from(change.get("before"))
            after = bus_from(change.get("after"))
            expected_type = {"add": ("add",), "remove": ("remove",),
                             "replace": ("time_change", "arrival_time_change")}
            if not isinstance(operation, str) or operation not in expected_type:
                errors.append(f"{proposal_id}: unknown operation {operation!r}")
                continue
            if proposal.get("change_type") not in expected_type[operation]:
                errors.append(f"{proposal_id}: change type does not match operation")
                continue
            if proposal.get("change_type") == "arrival_time_change" and (
                    before is None or after is None or bus_key(before) != bus_key(after)
                    or before.get("arrival_time") == after.get("arrival_time")):
                errors.append(f"{proposal_id}: arrival change must change only arrival_time")
                continue
            if ((operation == "add" and change.get("before") is not None)
                    or (operation == "remove" and change.get("after") is not None)
                    or (operation in {"remove", "replace"} and before is None)
                    or (operation in {"add", "replace"} and after is None)):
                errors.append(f"{proposal_id}: operation has inconsistent before/after buses")
                continue
            change_errors = len(errors)
            for side, bus in (("before", before), ("after", after)):
                if bus is not None:
                    bus_errors = validate_schedule_structure({direction: {day_type: [bus]}})
                    errors.extend(
                        f"{proposal_id}: invalid {side} bus: {error}" for error in bus_errors
                    )
            if len(errors) != change_errors:
                continue
            original_target = copy.deepcopy(target)

            if operation in {"remove", "replace"}:
                if before is None:
                    errors.append(f"{proposal_id}: {operation} is missing before bus")
                else:
                    matching_indexes = [
                        index
                        for index, bus in enumerate(target)
                        if isinstance(bus, dict) and same_bus(bus, before)
                    ]
                    if len(matching_indexes) != 1:
                        errors.append(
                            f"{proposal_id}: {operation} expected one before bus, "
                            f"found {len(matching_indexes)}"
                        )
                    else:
                        target.pop(matching_indexes[0])

            if operation in {"add", "replace"}:
                if after is None:
                    errors.append(f"{proposal_id}: {operation} is missing after bus")
                else:
                    if any(
                        isinstance(bus, dict) and bus_key(bus) == bus_key(after)
                        for bus in target
                    ):
                        errors.append(f"{proposal_id}: {operation} creates a duplicate bus")
                    target.append(after)

            if len(errors) != change_errors:
                target[:] = original_target
            else:
                simulated_count += 1

    errors.extend(validate_schedule_structure(simulated))

    try:
        json.dumps(simulated, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        errors.append(f"simulated schedule is not JSON serializable: {exc}")

    after_count = schedule_bus_count(simulated)
    unique_errors = sorted(set(errors))
    result = {
        "status": "passed" if not unique_errors else "failed",
        "proposals_simulated": len(proposals),
        "successful_changes": simulated_count,
        "before_bus_count": before_count,
        "after_bus_count": after_count,
        "bus_count_delta": after_count - before_count,
        "errors": unique_errors,
    }
    # Preview may consume the validated copy; the default report stays unchanged.
    # A failed simulation must never expose a partially changed candidate.
    if include_candidate and not unique_errors:
        result["candidate_schedule"] = simulated
    return result


def build_update_proposals(
    comparison: dict[str, Any],
    schedule: dict[str, Any],
    reviewed_data: dict[str, Any] | None = None,
    *,
    generated_at: str | None = None,
    source_path: str = "monitor/debug/route_search_compare.json",
) -> dict[str, Any]:
    """Build a deterministic proposal report from route-search comparison data."""

    if not isinstance(comparison, dict):
        raise ValueError("comparison must be an object")
    if comparison.get("comparison_key") != ["time", "stop"]:
        raise ValueError("comparison_key must be ['time', 'stop']")
    if not isinstance(schedule, dict):
        raise ValueError("schedule must be an object")
    schedule_errors = validate_schedule_structure(schedule)
    if schedule_errors:
        raise ValueError("invalid schedule: " + "; ".join(schedule_errors))
    routes = comparison.get("routes")
    if not isinstance(routes, dict):
        raise ValueError("comparison is missing routes object")

    reviews = build_review_index(reviewed_data)
    stops = VALID_STOPS
    proposals_by_id: dict[str, dict[str, Any]] = {}
    errors_by_id: dict[str, dict[str, Any]] = {}
    input_counts = {category: 0 for category in ROUTE_CATEGORIES}
    duplicates_suppressed = 0
    paired_components_suppressed = 0
    blocked_components_suppressed = 0

    def add_proposal(proposal: dict[str, Any]) -> None:
        nonlocal duplicates_suppressed
        proposal_id = proposal["proposal_id"]
        if proposal_id in proposals_by_id:
            duplicates_suppressed += 1
            # Prefer a confirmed-candidate pending state if duplicate evidence differs.
            if proposal["status"] == "pending":
                proposals_by_id[proposal_id] = proposal
            return
        proposals_by_id[proposal_id] = proposal

    def add_error(error: dict[str, Any]) -> None:
        nonlocal duplicates_suppressed
        validation_id = error["validation_id"]
        if validation_id in errors_by_id:
            duplicates_suppressed += 1
            return
        errors_by_id[validation_id] = error

    for direction in sorted(routes, key=str):
        days = routes.get(direction)
        if not isinstance(days, dict):
            add_error(
                make_validation_error(
                    direction=direction,
                    day_type="",
                    candidate_type="route",
                    candidate=days,
                    codes=["route_days_must_be_object"],
                )
            )
            continue

        for day_type in sorted(days, key=str):
            detail = days.get(day_type)
            if not isinstance(detail, dict):
                add_error(
                    make_validation_error(
                        direction=direction,
                        day_type=day_type,
                        candidate_type="day",
                        candidate=detail,
                        codes=["route_day_detail_must_be_object"],
                    )
                )
                continue

            day_errors = context_errors(schedule, direction, day_type)
            unknown = set(detail) - set(ROUTE_CATEGORIES) - {
                "existing_count", "route_search_count", "added_count", "removed_count",
                "line_only_count", "time_change_candidate_count", "arrival_time_change_count",
            }
            if unknown:
                day_errors.append("unknown_comparison_fields: " + ", ".join(sorted(unknown)))
            if day_errors:
                add_error(make_validation_error(
                    direction=direction, day_type=day_type, candidate_type="day",
                    candidate=detail, codes=day_errors,
                ))
                continue

            categorized: dict[str, list[Any]] = {}
            for category in ROUTE_CATEGORIES:
                categorized[category] = candidate_list(
                    detail,
                    category,
                    direction=str(direction),
                    day_type=str(day_type),
                    add_error=add_error,
                )
                input_counts[category] += len(categorized[category])
            if any(not isinstance(detail.get(category, [] if category == "arrival_time_changes" else None), list)
                   for category in ROUTE_CATEGORIES):
                continue

            consumed_added: set[tuple[str, str, str]] = set()
            consumed_removed: set[tuple[str, str, str]] = set()
            prepared, blocked_added, blocked_removed = prepare_time_changes(
                categorized["time_change_candidates"], categorized["added"], categorized["removed"],
                schedule, str(direction), str(day_type),
            )
            for candidate, validation_codes, before, after in prepared:
                if validation_codes or before is None or after is None or not isinstance(candidate, dict):
                    add_error(
                        make_validation_error(
                            direction=direction,
                            day_type=day_type,
                            candidate_type="time_change",
                            candidate=candidate,
                            codes=validation_codes or ["invalid_time_change_candidate"],
                        )
                    )
                    continue

                review = reviews.get(time_change_review_key(str(direction), str(day_type), candidate))
                change = {"operation": "replace", "before": before, "after": after}
                proposal = make_proposal(
                        direction=str(direction),
                        day_type=str(day_type),
                        change_type="time_change",
                        changes=[change],
                        source_path=source_path,
                        reviewed_item=review,
                    )
                proposal["time_context"] = time_change_context(
                    before, after, schedule_items(schedule, str(direction), str(day_type)),
                )
                if proposal["time_context"]["crosses_existing_departure"]:
                    proposal["status"] = "needs_review"
                    proposal["review_reasons"] = ["time_change_crosses_existing_departure"]
                elif proposal["status"] == "needs_review":
                    proposal["review_reasons"] = ["unconfirmed_time_change_pairing"]
                if after.get("arrival_time") is not None:
                    proposal["status"] = "needs_review"
                    proposal["review_reasons"] = sorted(set(proposal.get("review_reasons", []) + ["arrival_requires_review"]))
                add_proposal(proposal)
                consumed_removed.add(bus_key(candidate["existing_item"]))
                consumed_added.add(bus_key(candidate["route_search_item"]))

            for candidate in categorized["added"]:
                if component_key(candidate) in blocked_added:
                    blocked_components_suppressed += 1
                    add_error(make_validation_error(
                        direction=direction, day_type=day_type, candidate_type="added",
                        candidate=candidate, codes=["blocked_by_invalid_time_change"],
                    ))
                    continue
                if isinstance(candidate, dict) and bus_key(candidate) in consumed_added:
                    paired_components_suppressed += 1
                    continue

                validation_codes = context_errors(schedule, direction, day_type)
                validation_codes.extend(record_context_errors(candidate, str(direction), str(day_type)))
                validation_codes.extend(candidate_bus_errors(candidate, stops=stops))
                candidate_bus = bus_from(candidate)
                existing = schedule_items(schedule, str(direction), str(day_type))
                if not validation_codes and candidate_bus is not None:
                    semantic_key = (
                        candidate_bus["time"],
                        normalize_candidate_line(candidate_bus["line"]),
                        candidate_bus["stop"],
                    )
                    if any(
                        (
                            text(bus.get("time")),
                            normalize_candidate_line(bus.get("line")),
                            text(bus.get("stop")),
                        )
                        == semantic_key
                        for bus in existing
                    ):
                        validation_codes.append("added_bus_already_exists_in_schedule")

                resolved_line: str | None = None
                if not validation_codes and candidate_bus is not None:
                    resolved_line, resolution_error = resolve_added_line(
                        candidate_bus["line"],
                        existing=existing,
                        stop=candidate_bus["stop"],
                    )
                    if resolution_error:
                        validation_codes.append(resolution_error)

                if validation_codes or candidate_bus is None or resolved_line is None:
                    add_error(
                        make_validation_error(
                            direction=direction,
                            day_type=day_type,
                            candidate_type="added",
                            candidate=candidate,
                            codes=validation_codes or ["invalid_added_candidate"],
                        )
                    )
                    continue

                after = dict(candidate_bus)
                after["line"] = resolved_line
                if any("arrival_time" in item for item in existing):
                    after.setdefault("arrival_time", None)
                review = reviews.get(direct_review_key(str(direction), str(day_type), "added", candidate))
                add_proposal(
                    make_proposal(
                        direction=str(direction),
                        day_type=str(day_type),
                        change_type="add",
                        changes=[{"operation": "add", "before": None, "after": after}],
                        source_path=source_path,
                        reviewed_item=review,
                    )
                )

            for candidate in categorized["removed"]:
                if component_key(candidate) in blocked_removed:
                    blocked_components_suppressed += 1
                    add_error(make_validation_error(
                        direction=direction, day_type=day_type, candidate_type="removed",
                        candidate=candidate, codes=["blocked_by_invalid_time_change"],
                    ))
                    continue
                if isinstance(candidate, dict) and bus_key(candidate) in consumed_removed:
                    paired_components_suppressed += 1
                    continue

                validation_codes = context_errors(schedule, direction, day_type)
                validation_codes.extend(record_context_errors(candidate, str(direction), str(day_type)))
                validation_codes.extend(candidate_bus_errors(candidate, stops=stops))
                before = bus_from(candidate)
                if not validation_codes and before is not None:
                    existing = schedule_items(schedule, str(direction), str(day_type))
                    match_count = sum(bus_key(bus) == bus_key(before) for bus in existing)
                    if match_count == 0:
                        validation_codes.append("removed_bus_missing_from_schedule")
                    elif match_count > 1:
                        validation_codes.append("removed_bus_duplicated_in_schedule")
                    else:
                        current_bus = next(bus for bus in existing if bus_key(bus) == bus_key(before))
                        if "arrival_time" in before and not same_bus(before, current_bus):
                            validation_codes.append("removed_stale_arrival_time")
                        else:
                            before = copy.deepcopy(current_bus)

                if validation_codes or before is None:
                    add_error(
                        make_validation_error(
                            direction=direction,
                            day_type=day_type,
                            candidate_type="removed",
                            candidate=candidate,
                            codes=validation_codes or ["invalid_removed_candidate"],
                        )
                    )
                    continue

                review = reviews.get(direct_review_key(str(direction), str(day_type), "removed", candidate))
                add_proposal(
                    make_proposal(
                        direction=str(direction),
                        day_type=str(day_type),
                        change_type="remove",
                        changes=[{"operation": "remove", "before": before, "after": None}],
                        source_path=source_path,
                        reviewed_item=review,
                    )
                )

            for candidate in categorized["arrival_time_changes"]:
                codes, before, after = validate_arrival_change(candidate, schedule, str(direction), str(day_type))
                if codes:
                    add_error(make_validation_error(
                        direction=direction, day_type=day_type, candidate_type="arrival_time_changes",
                        candidate=candidate, codes=codes,
                    ))
                else:
                    add_proposal(make_proposal(
                        direction=str(direction), day_type=str(day_type), change_type="arrival_time_change",
                        changes=[{"operation": "replace", "before": before, "after": after}],
                        source_path=source_path, reviewed_item=None,
                    ))

            for candidate in categorized["line_only"]:
                validation_codes = context_errors(schedule, direction, day_type)
                if not isinstance(candidate, dict):
                    validation_codes.append("candidate_must_be_object")
                else:
                    if not is_valid_time(candidate.get("time")):
                        validation_codes.append("invalid_time")
                    stop = candidate.get("stop")
                    if not isinstance(stop, str) or stop not in stops:
                        validation_codes.append("unknown_stop")
                    if not is_valid_line(candidate.get("existing_line")):
                        validation_codes.append("invalid_existing_line")
                    if not is_valid_line(candidate.get("route_search_line")):
                        validation_codes.append("invalid_route_search_line")
                validation_codes.append("line_only_not_actionable_without_exact_schedule_line")
                add_error(
                    make_validation_error(
                        direction=direction,
                        day_type=day_type,
                        candidate_type="line_only",
                        candidate=candidate,
                        codes=validation_codes,
                    )
                )

    proposals = sorted(proposals_by_id.values(), key=proposal_sort_key)
    conflicts = conflicting_proposal_ids(proposals)
    for proposal in proposals:
        if proposal["proposal_id"] in conflicts:
            add_error(make_validation_error(
                direction=proposal["direction"], day_type=proposal["day_type"],
                candidate_type=proposal["source_category"], candidate=proposal,
                codes=["conflicting_proposal_bus"],
            ))
    proposals = [proposal for proposal in proposals if proposal["proposal_id"] not in conflicts]
    validation_errors = sorted(errors_by_id.values(), key=validation_sort_key)
    simulation = simulate_proposals(schedule, proposals)
    if simulation["status"] == "failed":
        for proposal in proposals:
            proposal["status"] = "needs_review"
            proposal["review_reasons"] = sorted(set(proposal.get("review_reasons", []) + ["simulation_failed"]))

    by_status = {status: 0 for status in STATUS_VALUES}
    by_change_type = {"add": 0, "remove": 0, "line_only": 0, "time_change": 0, "arrival_time_change": 0}
    for proposal in proposals:
        by_status[proposal["status"]] += 1
        by_change_type[proposal["change_type"]] += 1

    return {
        "proposal_version": PROPOSAL_VERSION,
        "generated_at": generated_at or generated_at_now(),
        "source": source_path,
        "apply_allowed": False,
        "status_values": list(STATUS_VALUES),
        "proposals": proposals,
        "validation_errors": validation_errors,
        "simulation": simulation,
        "summary": {
            "total": len(proposals),
            "by_status": by_status,
            "by_change_type": by_change_type,
            "input_candidates": input_counts,
            "validation_error_count": len(validation_errors),
            "duplicates_suppressed": duplicates_suppressed,
            "paired_components_suppressed": paired_components_suppressed,
            "blocked_components_suppressed": blocked_components_suppressed,
        },
    }


def format_bus(bus: object) -> str:
    if not isinstance(bus, dict):
        return "-"
    arrival = f" → {bus['arrival_time']} 着" if bus.get("arrival_time") else ""
    return f"{text(bus.get('time'))} 発{arrival} {text(bus.get('line'))} {text(bus.get('stop'))}".strip()


def human_change_label(change_type: str) -> str:
    return {
        "add": "追加候補",
        "remove": "削除候補",
        "time_change": "時刻変更候補",
        "arrival_time_change": "到着時刻変更候補",
    }.get(change_type, change_type)


def human_review_label(proposal: dict[str, Any]) -> str:
    review = proposal.get("review")
    if isinstance(review, dict) and review.get("candidate_status") == "confirmed":
        return "確認済み候補"
    return "未確認候補"


def print_report(report: dict[str, Any]) -> None:
    proposals = report.get("proposals", [])
    for proposal in proposals if isinstance(proposals, list) else []:
        if not isinstance(proposal, dict):
            continue
        changes = proposal.get("changes")
        change = changes[0] if isinstance(changes, list) and changes else {}
        if not isinstance(change, dict):
            change = {}
        print(
            f"[{human_review_label(proposal)} / {text(proposal.get('status'))}] "
            f"{human_change_label(text(proposal.get('change_type')))}"
        )
        print(f"方向: {text(proposal.get('direction'))}")
        print(f"日種別: {text(proposal.get('day_type'))}")
        print(f"現在: {format_bus(change.get('before'))}")
        print(f"候補: {format_bus(change.get('after'))}")
        print(f"Proposal: {text(proposal.get('proposal_id'))}")
        print(f"状態: {text(proposal.get('status'))}")
        print()

    validation_errors = report.get("validation_errors", [])
    for error in validation_errors if isinstance(validation_errors, list) else []:
        if not isinstance(error, dict):
            continue
        print(
            f"[更新案生成不可] {text(error.get('candidate_type'))} "
            f"{text(error.get('direction'))}/{text(error.get('day_type'))}"
        )
        codes = error.get("codes", [])
        print("候補: " + json.dumps(error.get("candidate"), ensure_ascii=False, sort_keys=True))
        print("理由: " + ", ".join(str(code) for code in codes))
        print(f"Validation: {text(error.get('validation_id'))}")
        print()

    summary = report.get("summary", {})
    if not isinstance(summary, dict):
        summary = {}
    by_status = summary.get("by_status", {})
    if not isinstance(by_status, dict):
        by_status = {}
    by_change = summary.get("by_change_type", {})
    if not isinstance(by_change, dict):
        by_change = {}

    print("Update Proposal Summary")
    print("-----------------------")
    print(f"total: {summary.get('total', 0)}")
    for status in STATUS_VALUES:
        print(f"{status}: {by_status.get(status, 0)}")
    print()
    print(f"added: {by_change.get('add', 0)}")
    print(f"removed: {by_change.get('remove', 0)}")
    print(f"line_only: {by_change.get('line_only', 0)}")
    print(f"time_change: {by_change.get('time_change', 0)}")
    print(f"arrival_time_change: {by_change.get('arrival_time_change', 0)}")
    print(f"validation_errors: {summary.get('validation_error_count', 0)}")
    simulation = report.get("simulation", {})
    if isinstance(simulation, dict):
        print(f"simulation: {simulation.get('status', 'unknown')}")
        for error in simulation.get("errors", []):
            print(f"simulation_error: {error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate dry-run schedule update proposals from saved monitor results."
    )
    parser.add_argument("--comparison", type=Path, default=DEFAULT_COMPARISON_PATH)
    parser.add_argument("--schedule", type=Path, default=DEFAULT_SCHEDULE_PATH)
    parser.add_argument("--reviewed", type=Path, default=DEFAULT_REVIEWED_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args(argv)

    try:
        inputs = [args.comparison, args.schedule, args.reviewed]
        validate_output_path(args.output, inputs)
        comparison = load_json_object(args.comparison)
        schedule = load_json_object(args.schedule)
        reviewed = load_json_object(args.reviewed) if args.reviewed.exists() else None
        report = build_update_proposals(
            comparison,
            schedule,
            reviewed,
            source_path=display_source_path(args.comparison),
        )
        write_report(args.output, report, inputs)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print_report(report)
    print(f"\nSaved {args.output}")
    if report.get("simulation", {}).get("status") != "passed":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
