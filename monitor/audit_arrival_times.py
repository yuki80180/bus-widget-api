"""Read-only, offline verification of the reviewed arrival evidence and live data."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

try:
    from .convert_route_search_extracted import confirmed_arrival
    from .compare_route_search_normalized import normalize_line_for_compare
except ImportError:
    from convert_route_search_extracted import confirmed_arrival
    from compare_route_search_normalized import normalize_line_for_compare

ROOT = Path(__file__).resolve().parents[1]
PREFIXES = {"to_uni": "kanazawa_station_to_uni", "to_station": "uni_to_kanazawa_station",
            "to_nakahashi": "uni_to_nakahashi"}


def classify_bus(direction, day_type, bus, evidence):
    """Exact departure/line/KIT pole and endpoints; no duration/nearby-trip inference."""
    kinds = ["weekday"] if day_type == "weekday" else ["saturday", "weekend"]
    arrivals, references = [], []
    for kind in kinds:
        source_key = PREFIXES[direction] + "_" + kind
        source = evidence["sources"].get(source_key, {})
        matches = [row for row in source.get("rows", []) if
                   row["depart_time"] == bus["time"] and
                   row["line"] == normalize_line_for_compare(bus["line"]) and
                   row["arrive_pole" if direction == "to_uni" else "depart_pole"] == bus["stop"]]
        if not matches:
            return None, "unconfirmed", f"no_exact_trip:{source_key}", references
        if len(matches) != 1:
            return None, "ambiguous", f"multiple_trips:{source_key}", references
        row = matches[0]
        arrival = confirmed_arrival(row, direction)
        if arrival is None:
            return None, "unconfirmed", f"missing_arrival_or_endpoint_mismatch:{source_key}", references
        arrivals.append(arrival)
        references.append({"source": source_key, "page": row["page_index"],
                           "arrival_time": arrival})
    if len(set(arrivals)) != 1:
        return None, "ambiguous", "saturday_sunday_arrival_disagreement", references
    return arrivals[0], "confirmed", "exact_official_trip", references


def audit(schedule, db_path, evidence):
    with closing(sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True)) as conn:
        rows = conn.execute("SELECT direction, day_type, time, arrival_time, line, stop FROM bus_schedule ORDER BY id").fetchall()
    db_counts = Counter(rows)
    report = {"summary": {}, "null_buses": [], "errors": [], "overnight_buses": []}
    expected = []
    for direction, days in schedule.items():
        for day_type, buses in days.items():
            counts = {"total": len(buses), "confirmed": 0, "unconfirmed": 0, "ambiguous": 0,
                      "arrival_time_present": 0, "arrival_time_null": 0, "invalid_arrival": 0,
                      "duplicate": 0, "db_mismatch": 0}
            seen = set()
            for bus in buses:
                arrival, classification, reason, refs = classify_bus(direction, day_type, bus, evidence)
                counts[classification] += 1
                value = bus.get("arrival_time")
                counts["arrival_time_null" if value is None else "arrival_time_present"] += 1
                if "arrival_time" not in bus or (value is not None and (not isinstance(value, str) or
                        not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value))):
                    counts["invalid_arrival"] += 1
                if value != arrival:
                    report["errors"].append(f"{direction}/{day_type}/{bus['time']}: official evidence mismatch")
                key = (bus["time"], bus["line"], bus["stop"])
                counts["duplicate"] += int(key in seen)
                seen.add(key)
                record = (direction, day_type, bus["time"], value, bus["line"], bus["stop"])
                expected.append(record)
                counts["db_mismatch"] += int(db_counts[record] != 1)
                if value is None:
                    report["null_buses"].append({"direction": direction, "day_type": day_type, **bus,
                                                "classification": classification, "reason": reason})
                if value is not None and value < bus["time"]:
                    report["overnight_buses"].append({"direction": direction, "day_type": day_type, **bus})
            report["summary"][f"{direction}/{day_type}"] = counts
    if Counter(expected) != db_counts:
        report["errors"].append("schedule/DB records differ")
    for key, counts in report["summary"].items():
        if any(counts[name] for name in ("invalid_arrival", "duplicate", "db_mismatch")):
            report["errors"].append(f"{key}: invalid/duplicate/mismatch")
    report["total"] = len(expected)
    report["arrival_time_present"] = sum(c["arrival_time_present"] for c in report["summary"].values())
    report["arrival_time_null"] = len(report["null_buses"])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedule", type=Path, default=ROOT / "schedule.json")
    parser.add_argument("--db", type=Path, default=ROOT / "bus.db")
    parser.add_argument("--evidence", type=Path, default=Path(__file__).with_name("arrival_time_evidence.json"))
    args = parser.parse_args()
    report = audit(json.loads(args.schedule.read_text(encoding="utf-8-sig")), args.db,
                   json.loads(args.evidence.read_text(encoding="utf-8")))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
