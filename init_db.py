import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SCHEDULE_PATH = BASE_DIR / "schedule.json"
DB_PATH = BASE_DIR / "bus.db"

VALID_DIRECTIONS = {"to_uni", "to_station", "to_nakahashi"}
VALID_DAY_TYPES = {"weekday", "weekend"}
VALID_STOPS = {"A", "B", "C", "D"}
TIME_PATTERN = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", re.ASCII)


def validate_arrival_time(value):
    if value is not None and (not isinstance(value, str) or not TIME_PATTERN.fullmatch(value)):
        raise ValueError("arrival_time must be null or HH:MM.")
    return value


def require_text(bus, key, location):
    value = bus.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location}: '{key}' must be a non-empty string.")
    return value.strip()


def load_schedule():
    with SCHEDULE_PATH.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("schedule.json must contain an object at the top level.")

    return data


def validate_schedule(schedule_data):
    records = []
    seen = set()

    for direction, day_types in schedule_data.items():
        if direction not in VALID_DIRECTIONS:
            raise ValueError(f"Unknown direction: {direction}")
        if not isinstance(day_types, dict):
            raise ValueError(f"{direction}: day types must be an object.")

        for day_type, buses in day_types.items():
            if day_type not in VALID_DAY_TYPES:
                raise ValueError(f"{direction}: unknown day type: {day_type}")
            if not isinstance(buses, list):
                raise ValueError(f"{direction}/{day_type}: buses must be a list.")

            for index, bus in enumerate(buses, start=1):
                location = f"{direction}/{day_type}[{index}]"
                if not isinstance(bus, dict):
                    raise ValueError(f"{location}: bus entry must be an object.")

                bus_time = require_text(bus, "time", location)
                if not TIME_PATTERN.fullmatch(bus_time):
                    raise ValueError(f"{location}: time must be zero-padded HH:MM.")
                datetime.strptime(bus_time, "%H:%M")

                line = require_text(bus, "line", location)
                stop = require_text(bus, "stop", location)
                if stop not in VALID_STOPS:
                    raise ValueError(f"{location}: unknown stop: {stop}")

                record = (direction, day_type, bus_time, line, stop)
                if record in seen:
                    raise ValueError(f"{location}: duplicate bus record.")
                seen.add(record)
                records.append((*record, validate_arrival_time(bus.get("arrival_time"))))

    if not records:
        raise ValueError("schedule.json did not contain any bus records.")

    return records


def initialize_database(records):
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute("DROP TABLE IF EXISTS bus_schedule")
        conn.execute(
            """
            CREATE TABLE bus_schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                direction TEXT NOT NULL,
                day_type TEXT NOT NULL,
                time TEXT NOT NULL,
                line TEXT NOT NULL,
                stop TEXT NOT NULL,
                arrival_time TEXT NULL,
                UNIQUE(direction, day_type, time, line, stop)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX idx_bus_schedule_lookup
            ON bus_schedule(direction, day_type, time)
            """
        )
        conn.executemany(
            """
            INSERT INTO bus_schedule (direction, day_type, time, line, stop, arrival_time)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            records,
        )


def migrate_arrival_times(db_path, records):
    """Explicit, transactional migration. Preserve IDs and every departure field.

    Refuse to touch a DB that differs from the schedule. Run at deployment or
    manually, never on requests. Re-running this migration is safe.
    """
    from collections import Counter

    if not Path(db_path).is_file():
        raise FileNotFoundError(db_path)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT direction, day_type, time, line, stop FROM bus_schedule ORDER BY id"
        ).fetchall()
        if Counter(current) != Counter(record[:5] for record in records):
            raise ValueError("schedule/DB mismatch; migration aborted")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(bus_schedule)")}
        if "arrival_time" not in columns:
            conn.execute("ALTER TABLE bus_schedule ADD COLUMN arrival_time TEXT NULL")
        conn.executemany(
            "UPDATE bus_schedule SET arrival_time = ? "
            "WHERE direction = ? AND day_type = ? AND time = ? AND line = ? AND stop = ?",
            [(validate_arrival_time(r[5]), *r[:5]) for r in records],
        )


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Build or migrate the schedule database")
    parser.add_argument("--migrate-arrivals", action="store_true",
                        help="Preserve existing rows/IDs; update only nullable arrival_time")
    args = parser.parse_args()
    schedule_data = load_schedule()
    records = validate_schedule(schedule_data)
    if args.migrate_arrivals:
        migrate_arrival_times(DB_PATH, records)
    else:
        initialize_database(records)
    print(f"Initialized {DB_PATH.name} with {len(records)} bus records.")


if __name__ == "__main__":
    main()
