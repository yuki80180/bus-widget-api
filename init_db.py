import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SCHEDULE_PATH = BASE_DIR / "schedule.json"
DB_PATH = BASE_DIR / "bus.db"

VALID_DIRECTIONS = {"to_uni", "to_station", "to_nakahashi"}
VALID_DAY_TYPES = {"weekday", "weekend"}
VALID_STOPS = {"A", "B", "C", "D"}
TIME_PATTERN = re.compile(r"^\d{2}:\d{2}$")


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
                if not TIME_PATTERN.match(bus_time):
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
                records.append(record)

    if not records:
        raise ValueError("schedule.json did not contain any bus records.")

    return records


def initialize_database(records):
    with sqlite3.connect(DB_PATH) as conn:
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
            INSERT INTO bus_schedule (direction, day_type, time, line, stop)
            VALUES (?, ?, ?, ?, ?)
            """,
            records,
        )


def main():
    schedule_data = load_schedule()
    records = validate_schedule(schedule_data)
    initialize_database(records)
    print(f"Initialized {DB_PATH.name} with {len(records)} bus records.")


if __name__ == "__main__":
    main()
