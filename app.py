import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from jpholiday import JPHoliday

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / os.environ.get("BUS_DB_PATH", "bus.db")
VALID_DIRECTIONS = {"to_uni", "to_station", "to_nakahashi"}
STOP_NAMES = {
    "A": "正門向い",
    "B": "正門前",
    "C": "四十万方向",
    "D": "四十万から",
}
DIRECTION_DETAILS = {
    "to_uni": {
        "label": "KIT行き",
        "from": "金沢駅・中橋方面",
        "to": "KIT",
        "destination": "金沢工業大学行",
    },
    "to_station": {
        "label": "金沢駅行き",
        "from": "KIT",
        "to": "金沢駅",
        "destination": "金沢駅行",
    },
    "to_nakahashi": {
        "label": "中橋行き",
        "from": "KIT",
        "to": "中橋",
        "destination": "中橋方面行",
    },
}
LINE_NUMBER_PATTERN = re.compile(r"\((\d+)\)")
JST = timezone(timedelta(hours=9), name="JST")
JAPAN_HOLIDAYS = JPHoliday()
NEXT_SERVICE_SEARCH_DAYS = 7


def get_service_day_type(service_date):
    if service_date.weekday() >= 5 or JAPAN_HOLIDAYS.is_holiday(service_date):
        return "weekend"
    return "weekday"


def get_db_connection():
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database file was not found: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def minutes_until(departure_time, current_time):
    departure_hour, departure_minute = map(int, departure_time.split(":"))
    current_hour, current_minute = map(int, current_time.split(":"))
    return (departure_hour * 60 + departure_minute) - (current_hour * 60 + current_minute)


def extract_line_number(line):
    match = LINE_NUMBER_PATTERN.search(line)
    return match.group(1) if match else None


def serialize_bus(row, current_time=None):
    bus = {
        "time": row["time"],
        "line": row["line"],
        "line_number": extract_line_number(row["line"]),
        "stop": row["stop"],
        "stop_name": STOP_NAMES.get(row["stop"], row["stop"]),
    }
    if current_time is not None:
        bus["minutes_until"] = minutes_until(row["time"], current_time)
    return bus


def fetch_next_buses(direction, day_type, current_time):
    with closing(get_db_connection()) as conn:
        schedule_exists = conn.execute(
            """
            SELECT 1
            FROM bus_schedule
            WHERE direction = ? AND day_type = ?
            LIMIT 1
            """,
            (direction, day_type),
        ).fetchone()
        if schedule_exists is None:
            return None

        rows = conn.execute(
            """
            SELECT time, line, stop
            FROM bus_schedule
            WHERE direction = ? AND day_type = ? AND time > ?
            ORDER BY time ASC
            LIMIT 3
            """,
            (direction, day_type, current_time),
        ).fetchall()

    return [serialize_bus(row, current_time) for row in rows]


def fetch_first_bus(direction, day_type):
    with closing(get_db_connection()) as conn:
        row = conn.execute(
            """
            SELECT time, line, stop
            FROM bus_schedule
            WHERE direction = ? AND day_type = ?
            ORDER BY time ASC
            LIMIT 1
            """,
            (direction, day_type),
        ).fetchone()

    return serialize_bus(row) if row is not None else None


def fetch_timetable(direction, day_type):
    with closing(get_db_connection()) as conn:
        schedule_exists = conn.execute(
            "SELECT 1 FROM bus_schedule LIMIT 1"
        ).fetchone()
        if schedule_exists is None:
            return None

        rows = conn.execute(
            """
            SELECT time, line, stop
            FROM bus_schedule
            WHERE direction = ? AND day_type = ?
            ORDER BY time ASC, id ASC
            """,
            (direction, day_type),
        ).fetchall()

    return [serialize_bus(row) for row in rows]


def find_next_service(direction, service_date, max_days=NEXT_SERVICE_SEARCH_DAYS):
    for days_ahead in range(1, max_days + 1):
        candidate_date = service_date + timedelta(days=days_ahead)
        candidate_day_type = get_service_day_type(candidate_date)
        first_bus = fetch_first_bus(direction, candidate_day_type)
        if first_bus is not None:
            return {
                "date": candidate_date.isoformat(),
                "day_type": candidate_day_type,
                "days_ahead": days_ahead,
                "bus": first_bus,
            }
    return None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.after_request
def disable_schedule_api_cache(response):
    if request.path in {"/api/next_bus", "/api/timetable"}:
        response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/timetable")
def api_timetable():
    direction = request.args.get("dir", "to_uni")
    if direction not in VALID_DIRECTIONS:
        return jsonify({
            "status": "error",
            "message": "Unknown direction.",
            "valid_directions": sorted(VALID_DIRECTIONS),
        }), 400

    now = datetime.now(JST)
    current_time = now.strftime("%H:%M")
    service_date = now.date()
    day_type = get_service_day_type(service_date)

    try:
        buses = fetch_timetable(direction, day_type)
    except (FileNotFoundError, sqlite3.Error):
        app.logger.exception("Failed to load bus timetable")
        return jsonify({
            "status": "error",
            "code": "schedule_unavailable",
            "message": "Schedule data is currently unavailable.",
            "current_time": current_time,
        }), 503

    if buses is None:
        return jsonify({
            "status": "error",
            "code": "schedule_empty",
            "message": "Schedule data is currently unavailable.",
            "current_time": current_time,
            "date": service_date.isoformat(),
            "day_type": day_type,
            "direction": direction,
        }), 503

    return jsonify({
        "status": "success",
        "current_time": current_time,
        "date": service_date.isoformat(),
        "day_type": day_type,
        "direction": direction,
        "direction_detail": DIRECTION_DETAILS[direction],
        "buses": buses,
    })


@app.route("/api/next_bus")
def api_next_bus():
    direction = request.args.get("dir", "to_uni")
    if direction not in VALID_DIRECTIONS:
        return jsonify({
            "status": "error",
            "message": "Unknown direction.",
            "valid_directions": sorted(VALID_DIRECTIONS),
        }), 400

    now = datetime.now(JST)
    current_time = now.strftime("%H:%M")
    service_date = now.date()
    day_type = get_service_day_type(service_date)

    try:
        next_buses = fetch_next_buses(direction, day_type, current_time)
        next_service = None
        if next_buses == []:
            next_service = find_next_service(direction, service_date)
    except (FileNotFoundError, sqlite3.Error):
        app.logger.exception("Failed to load bus schedule")
        return jsonify({
            "status": "error",
            "message": "Schedule data is currently unavailable.",
            "current_time": current_time,
        }), 503

    if next_buses is None:
        return jsonify({
            "status": "error",
            "message": "Schedule data is currently unavailable.",
            "current_time": current_time,
            "day_type": day_type,
            "direction": direction,
        }), 503

    if next_buses:
        return jsonify({
            "status": "success",
            "current_time": current_time,
            "day_type": day_type,
            "direction": direction,
            "direction_detail": DIRECTION_DETAILS[direction],
            "buses": next_buses,
        })

    return jsonify({
        "status": "end",
        "current_time": current_time,
        "day_type": day_type,
        "direction": direction,
        "direction_detail": DIRECTION_DETAILS[direction],
        "next_service": next_service,
    })


if __name__ == "__main__":
    app.run(
        debug=os.environ.get("FLASK_DEBUG") == "1",
        host=os.environ.get("FLASK_RUN_HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
    )
