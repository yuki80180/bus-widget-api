import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request

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


def get_day_type(now):
    return "weekend" if now.weekday() >= 5 else "weekday"


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


def fetch_next_buses(direction, day_type, current_time):
    with closing(get_db_connection()) as conn:
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

    return [
        {
            "time": row["time"],
            "line": row["line"],
            "line_number": extract_line_number(row["line"]),
            "stop": row["stop"],
            "stop_name": STOP_NAMES.get(row["stop"], row["stop"]),
            "minutes_until": minutes_until(row["time"], current_time),
        }
        for row in rows
    ]


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.route("/api/next_bus")
def api_next_bus():
    direction = request.args.get("dir", "to_uni")
    if direction not in VALID_DIRECTIONS:
        return jsonify({
            "status": "error",
            "message": "Unknown direction.",
            "valid_directions": sorted(VALID_DIRECTIONS),
        }), 400

    now = datetime.now()
    current_time = now.strftime("%H:%M")
    day_type = get_day_type(now)

    try:
        next_buses = fetch_next_buses(direction, day_type, current_time)
    except (FileNotFoundError, sqlite3.Error):
        app.logger.exception("Failed to load bus schedule")
        return jsonify({
            "status": "error",
            "message": "Schedule data is currently unavailable.",
            "current_time": current_time,
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
    })


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
