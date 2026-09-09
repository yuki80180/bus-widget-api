"""Preview explicitly selected proposals; publish copies only under generated/."""

from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    from . import generate_update_proposals as generator
except ImportError:  # Direct CLI execution.
    import generate_update_proposals as generator


GENERATED_DIR = generator.GENERATED_DIR
DEFAULT_OUTPUT_DIR = GENERATED_DIR / "preview"
PREVIEW_VERSION = 1
ID_PATTERN = re.compile(r"[0-9a-f]{64}", re.ASCII)
BUS_KEYS = {"time", "line", "stop"}
CHANGE_TYPES = {"add": ("add", "added"), "remove": ("remove", "removed"),
                "time_change": ("replace", "time_change_candidates")}


class PreviewError(ValueError):
    """The complete selection was rejected before publication."""


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PreviewError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_json(data: bytes) -> Any:
    return json.loads(data.decode("utf-8-sig"), object_pairs_hook=unique_object,
                      parse_constant=generator.reject_json_constant)


def proposal_identity(proposal: dict[str, Any]) -> str:
    return generator.stable_id({
        "proposal_version": generator.PROPOSAL_VERSION,
        **{key: proposal[key] for key in ("direction", "day_type", "change_type", "changes")},
    })


def validate_report(report: object) -> dict[str, dict[str, Any]]:
    if (not isinstance(report, dict) or type(report.get("proposal_version")) is not int
            or report["proposal_version"] != generator.PROPOSAL_VERSION
            or report.get("apply_allowed") is not False):
        raise PreviewError("invalid proposal schema/version or apply_allowed must be false")
    if not isinstance(report.get("proposals"), list) or not isinstance(report.get("validation_errors"), list):
        raise PreviewError("proposals and validation_errors must be arrays")
    if not isinstance(report.get("simulation"), dict) or report["simulation"].get("status") != "passed":
        raise PreviewError("source proposal simulation must have passed")
    index = {}
    for proposal in report["proposals"]:
        identity = proposal.get("proposal_id") if isinstance(proposal, dict) else None
        if not isinstance(identity, str) or not ID_PATTERN.fullmatch(identity):
            raise PreviewError("invalid proposal ID in proposal file")
        if identity in index:
            raise PreviewError(f"duplicate proposal ID in proposal file: {identity}")
        index[identity] = proposal
    for error in report["validation_errors"]:
        if (not isinstance(error, dict)
                or not {"direction", "day_type", "candidate_type", "candidate", "codes"} <= error.keys()
                or not isinstance(error["codes"], list)):
            raise PreviewError("invalid validation_errors entry")
    return index


def error_components(value: object) -> set[tuple[str, str, str]]:
    """Find quarantined buses, including nested conflicting proposals/pairs."""
    components: set[tuple[str, str, str]] = set()
    if isinstance(value, dict):
        for time_key in ("time", "old_time", "new_time"):
            for line_key in ("line", "existing_line", "route_search_line"):
                if generator.is_valid_time(value.get(time_key)) and isinstance(value.get(line_key), str):
                    components.add((value[time_key], generator.normalize_candidate_line(value[line_key]),
                                    generator.text(value.get("stop"))))
        for child in value.values():
            components.update(error_components(child))
    elif isinstance(value, list):
        for child in value:
            components.update(error_components(child))
    return components


def validate_proposal(proposal: dict[str, Any], schedule: dict[str, Any],
                      validation_errors: list[dict[str, Any]]) -> None:
    identity = proposal["proposal_id"]

    def reject(reason: str) -> None:
        raise PreviewError(f"{identity}: {reason}")

    if proposal.get("status") not in ("pending", "approved"):
        reject("status must be pending or approved; selection is preview only")
    if proposal.get("review_reasons"):
        reject("proposal still has review_reasons")
    context = generator.context_errors(schedule, proposal.get("direction"), proposal.get("day_type"))
    if context:
        reject("invalid context: " + ", ".join(context))
    change_type = proposal.get("change_type")
    if not isinstance(change_type, str) or change_type not in CHANGE_TYPES:
        reject("invalid change_type")
    operation, category = CHANGE_TYPES[change_type]
    if proposal.get("source_category") != category:
        reject("source_category does not match change_type")
    changes = proposal.get("changes")
    if not isinstance(changes, list) or len(changes) != 1 or not isinstance(changes[0], dict):
        reject("changes must contain exactly one atomic change")
    change = changes[0]
    if set(change) != {"operation", "before", "after"} or change["operation"] != operation:
        reject("invalid change schema/operation")
    before, after = change["before"], change["after"]
    if ((operation == "add" and (before is not None or after is None))
            or (operation == "remove" and (before is None or after is not None))
            or (operation == "replace" and (before is None or after is None))):
        reject("inconsistent before/after buses")
    for bus in (before, after):
        if bus is not None and (not isinstance(bus, dict) or set(bus) != BUS_KEYS
                or generator.validate_schedule_structure({proposal["direction"]: {proposal["day_type"]: [bus]}})):
            reject("invalid bus: time, full line, stop and exact bus schema required")
    if proposal_identity(proposal) != identity:
        reject("proposal ID does not match its contents")
    current = schedule[proposal["direction"]][proposal["day_type"]]
    if before is not None:
        if (sum(bus == before for bus in current) != 1
                or sum(generator.component_key(bus) == generator.component_key(before) for bus in current) != 1):
            reject("stale/conflict: expected exactly one unambiguous before bus")
    if after is not None and any(generator.component_key(bus) == generator.component_key(after) for bus in current):
        reject("stale/conflict: after bus already exists")
    if operation == "replace":
        if before["line"] != after["line"] or before["stop"] != after["stop"]:
            reject("time_change must retain full line and stop")
        delta = abs(generator.time_to_minutes(after["time"]) - generator.time_to_minutes(before["time"]))
        if not 0 < delta <= generator.MAX_TIME_CHANGE_CANDIDATE_MINUTES:
            reject("time_change must be within 1..60 minutes")
        if generator.time_change_context(before, after, current)["crosses_existing_departure"]:
            reject("stale/conflict: time_change crosses an existing same-line departure")
    components = {generator.component_key(bus) for bus in (before, after) if bus is not None}
    for error in validation_errors:
        # An unscoped error cannot safely be attributed to another selection.
        if error["direction"] not in (proposal["direction"], "", None):
            continue
        if error["day_type"] not in (proposal["day_type"], "", None):
            continue
        blocked = error_components(error["candidate"])
        if (error["candidate_type"] in ("route", "day") or not blocked or components & blocked):
            reject("related validation error: " + str(error.get("validation_id", "unknown")))


def ordered_candidate(original: dict[str, Any], candidate: dict[str, Any]) -> None:
    """Keep unaffected row order; insert only new rows at their time positions."""
    for direction, days in candidate.items():
        for day_type, rows in days.items():
            remaining = list(rows)
            retained = []
            for old in original[direction][day_type]:
                if old in remaining:
                    retained.append(old.copy())
                    remaining.remove(old)
            for new in sorted(remaining, key=generator.bus_sort_key):
                position = next((i for i, row in enumerate(retained)
                                 if generator.bus_sort_key(row) > generator.bus_sort_key(new)), len(retained))
                retained.insert(position, new)
            rows[:] = retained


def json_spans(source: str) -> dict[tuple, tuple[int, int]]:
    """Locate JSON values without changing whitespace or reserializing the file."""
    decoder = json.JSONDecoder()
    spans: dict[tuple, tuple[int, int]] = {}

    def whitespace(position: int) -> int:
        while position < len(source) and source[position] in " \t\r\n":
            position += 1
        return position

    def visit(position: int, key: tuple) -> int:
        position = whitespace(position)
        start = position
        if source[position] in "[{":
            is_object = source[position] == "{"
            closing = "}" if is_object else "]"
            position = whitespace(position + 1)
            index = 0
            while source[position] != closing:
                if is_object:
                    name, position = decoder.raw_decode(source, position)
                    position = whitespace(position)
                    position = visit(position + 1, key + (name,))
                else:
                    position = visit(position, key + (index,))
                    index += 1
                position = whitespace(position)
                if source[position] == ",":
                    position = whitespace(position + 1)
                else:
                    break
            position += 1
        else:
            _, position = decoder.raw_decode(source, position)
        spans[key] = (start, position)
        return position

    visit(0, ())
    return spans


def render_candidate(original_bytes: bytes, original: dict[str, Any], candidate: dict[str, Any]) -> bytes:
    bom = original_bytes.startswith(b"\xef\xbb\xbf")
    source = original_bytes.decode("utf-8-sig")
    spans = json_spans(source)
    replacements = []
    for direction, days in candidate.items():
        for day_type, rows in days.items():
            old_rows = original[direction][day_type]
            if old_rows == rows:
                continue
            key = (direction, day_type)
            start, end = spans[key]
            if not rows:
                rendered = "[]"
            elif not old_rows:
                # Only an originally empty array needs a fresh local layout.
                rendered = "[" + ", ".join(json.dumps(row, ensure_ascii=source.isascii()) for row in rows) + "]"
            else:
                first_start, first_end = spans[key + (0,)]
                last_end = spans[key + (len(old_rows) - 1,)][1]
                prefix, suffix = source[start:first_start], source[last_end:end]
                separator = (source[first_end:spans[key + (1,)][0]] if len(old_rows) > 1
                             else "," + (prefix[1:] or " "))
                rendered_rows = []
                for row in rows:
                    index = next((i for i, old in enumerate(old_rows) if old == row), None)
                    if index is None:
                        index = next((i for i, old in enumerate(old_rows)
                                      if old["line"] == row["line"] and old["stop"] == row["stop"]), 0)
                    row_key = key + (index,)
                    row_start, row_end = spans[row_key]
                    rendered_row = source[row_start:row_end]
                    for name in sorted(BUS_KEYS, key=lambda name: spans[row_key + (name,)][0], reverse=True):
                        if row[name] == old_rows[index][name]:
                            continue
                        value_start, value_end = spans[row_key + (name,)]
                        rendered_row = (rendered_row[:value_start - row_start]
                                        + json.dumps(row[name], ensure_ascii=source.isascii())
                                        + rendered_row[value_end - row_start:])
                    rendered_rows.append(rendered_row)
                rendered = prefix + separator.join(rendered_rows) + suffix
            replacements.append((start, end, rendered))
    for start, end, rendered in sorted(replacements, reverse=True):
        source = source[:start] + rendered + source[end:]
    result = (b"\xef\xbb\xbf" if bom else b"") + source.encode("utf-8")
    if parse_json(result) != candidate:
        raise PreviewError("candidate serialization does not match simulation")
    return result


def build_preview(report: dict[str, Any], selected_ids: list[str], schedule_bytes: bytes) -> dict[str, bytes]:
    """Validate the whole selection and return all artifacts, without any I/O."""
    index = validate_report(report)
    if not selected_ids:
        raise PreviewError("at least one explicit --proposal-id is required")
    if any(not isinstance(identity, str) or not ID_PATTERN.fullmatch(identity) for identity in selected_ids):
        raise PreviewError("proposal IDs must be full lowercase SHA-256 values")
    if len(set(selected_ids)) != len(selected_ids):
        raise PreviewError("duplicate selected proposal ID")
    missing = sorted(set(selected_ids) - index.keys())
    if missing:
        raise PreviewError("unknown proposal ID: " + ", ".join(missing))
    schedule = parse_json(schedule_bytes)
    errors = generator.validate_schedule_structure(schedule)
    if errors:
        raise PreviewError("invalid schedule: " + "; ".join(errors))
    if any(set(bus) != BUS_KEYS for days in schedule.values() for rows in days.values() for bus in rows):
        raise PreviewError("schedule buses must contain exactly time, line and stop")
    selected = [copy.deepcopy(index[identity]) for identity in selected_ids]
    for proposal in selected:
        validate_proposal(proposal, schedule, report["validation_errors"])
    selected.sort(key=generator.proposal_sort_key)
    conflicts = generator.conflicting_proposal_ids(selected)
    # Cross-side overlap is also forbidden: selected changes cannot create each
    # other's prerequisites, even if sequential simulation could succeed.
    owners: dict[tuple, str] = {}
    for proposal in selected:
        for bus in (proposal["changes"][0]["before"], proposal["changes"][0]["after"]):
            if bus is None:
                continue
            key = (proposal["direction"], proposal["day_type"], generator.component_key(bus))
            if key in owners and owners[key] != proposal["proposal_id"]:
                conflicts.update((owners[key], proposal["proposal_id"]))
            owners[key] = proposal["proposal_id"]
    if conflicts:
        raise PreviewError("conflicting proposals: " + ", ".join(sorted(conflicts)))
    simulation = generator.simulate_proposals(schedule, selected, include_candidate=True)
    if simulation["status"] != "passed":
        raise PreviewError("simulation failed: " + "; ".join(simulation["errors"]))
    candidate = simulation.pop("candidate_schedule")
    ordered_candidate(schedule, candidate)
    candidate_bytes = render_candidate(schedule_bytes, schedule, candidate)
    before_text = schedule_bytes.decode("utf-8-sig")
    after_text = candidate_bytes.decode("utf-8-sig")
    diff = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                   for line in difflib.unified_diff(before_text.splitlines(keepends=True),
                                                   after_text.splitlines(keepends=True),
                                                   fromfile="schedule.json (input)",
                                                   tofile="schedule.preview.json (copy)"))
    counts = {kind: sum(p["change_type"] == kind for p in selected) for kind in CHANGE_TYPES}
    summary = {
        "preview_version": PREVIEW_VERSION, "status": "passed",
        "selection_meaning": "preview_only", "apply_allowed": False, "apply_performed": False,
        "selected_proposal_ids": [p["proposal_id"] for p in selected],
        "selected_proposals": selected, "by_change_type": counts,
        "before_bus_count": simulation["before_bus_count"], "after_bus_count": simulation["after_bus_count"],
        "conflicts": 0, "validation_errors": 0,
        "source_validation_error_count": len(report["validation_errors"]),
        "schedule_modified": False, "bus_db_modified": False,
        "schedule_sha256": hashlib.sha256(schedule_bytes).hexdigest(),
        "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
        "simulation": simulation,
    }
    lines = ["Update Proposal Preview", "-----------------------", ""]
    for proposal in selected:
        change = proposal["changes"][0]
        label = {"add": "追加", "remove": "削除", "time_change": "時刻変更"}[proposal["change_type"]]
        lines.extend([f"[{label}] {proposal['direction']} / {proposal['day_type']}",
                      "変更前: " + generator.format_bus(change["before"]),
                      "変更後: " + generator.format_bus(change["after"]),
                      "Proposal: " + proposal["proposal_id"], ""])
    lines.extend([f"selected proposals: {len(selected)}", f"added: {counts['add']}",
                  f"removed: {counts['remove']}", f"time_change: {counts['time_change']}",
                  f"before buses: {summary['before_bus_count']}", f"after buses: {summary['after_bus_count']}",
                  "conflicts: 0", "validation errors: 0",
                  f"unselected source validation errors: {len(report['validation_errors'])}",
                  "schedule.json modified: NO", "bus.db modified: NO", "apply performed: NO",
                  "selection means preview only; approval/status unchanged", ""])
    return {"schedule.preview.json": candidate_bytes, "schedule.diff": diff.encode("utf-8"),
            "summary.json": (json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8"),
            "summary.txt": "\n".join(lines).encode("utf-8")}


def safe_directory(path: Path) -> Path:
    root = GENERATED_DIR.absolute()
    absolute = path.absolute()
    if ".." in path.parts or root.resolve() != root or absolute.resolve() != absolute:
        raise PreviewError("output directory must not use traversal, symlinks or junctions")
    if not absolute.is_relative_to(root):
        raise PreviewError(f"output directory must be under {root}")
    for part in absolute.relative_to(root).parts:
        if ":" in part or part.rstrip(" .") != part:
            raise PreviewError("invalid output directory component")
    if absolute.exists() and not absolute.is_dir():
        raise PreviewError("output directory is not a directory")
    return absolute


def validate_artifact(path: Path, protected: list[Path]) -> None:
    safe_directory(path.parent)
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise PreviewError("artifact must not be a symlink or junction")
    for source in protected:
        if path.resolve() == source.resolve() or (path.exists() and source.exists() and path.samefile(source)):
            raise PreviewError("artifact must not alias an input or protected file")
    if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        raise PreviewError("artifact must be a regular file without hard links")


def publish_preview(artifacts: dict[str, bytes], output_dir: Path, inputs: dict[Path, bytes]) -> Path:
    """Publish an immutable artifact directory; never replace an existing file."""
    expected_names = {"schedule.preview.json", "schedule.diff", "summary.json", "summary.txt"}
    if set(artifacts) != expected_names or any(not isinstance(value, bytes) for value in artifacts.values()):
        raise PreviewError("invalid artifact bundle")
    root = safe_directory(output_dir)
    identity = generator.stable_id({name: hashlib.sha256(data).hexdigest() for name, data in artifacts.items()})
    destination = root / identity
    protected = list(inputs) + [generator.DEFAULT_SCHEDULE_PATH, generator.PROJECT_ROOT / "bus.db",
                               generator.DEFAULT_REVIEWED_PATH, generator.DEFAULT_OUTPUT_PATH]

    def recheck() -> None:
        safe_directory(root)
        safe_directory(destination)
        for name in expected_names:
            validate_artifact(destination / name, protected)
        for source, data in inputs.items():
            if source.read_bytes() != data:
                raise PreviewError(f"input changed during preview: {source}")

    recheck()
    if destination.exists():
        if (set(p.name for p in destination.iterdir()) != expected_names
                or any((destination / name).read_bytes() != data for name, data in artifacts.items())):
            raise PreviewError("existing preview differs from expected artifacts; refusing overwrite")
        return destination
    root.mkdir(parents=True, exist_ok=True)
    safe_directory(root)
    staging = Path(tempfile.mkdtemp(prefix=".preview-", dir=root))
    try:
        for name, data in artifacts.items():
            validate_artifact(staging / name, protected)
            with (staging / name).open("xb") as file:
                file.write(data)
        recheck()
        # Only a new generated/<digest> directory is published. No input file is
        # opened for writing, renamed, replaced or used as a destination.
        os.rename(staging, destination)
    finally:
        if staging.exists():
            safe_directory(staging)
            for name in expected_names:
                validate_artifact(staging / name, protected)
                (staging / name).unlink(missing_ok=True)
            staging.rmdir()
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposal-file", type=Path, default=generator.DEFAULT_OUTPUT_PATH)
    parser.add_argument("--proposal-id", action="append", required=True, help="full ID; repeat to select multiple")
    parser.add_argument("--schedule", type=Path, default=generator.DEFAULT_SCHEDULE_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    try:
        inputs = {path: path.read_bytes() for path in (args.proposal_file, args.schedule)}
        report = parse_json(inputs[args.proposal_file])
        artifacts = build_preview(report, args.proposal_id, inputs[args.schedule])
        destination = publish_preview(artifacts, args.output_dir, inputs)
    except (OSError, ValueError, RecursionError) as exc:
        print(f"Preview rejected: {exc}\napply performed: NO", file=sys.stderr)
        return 1
    print(artifacts["summary.txt"].decode("utf-8"), end="")
    print("preview directory: " + str(destination))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
