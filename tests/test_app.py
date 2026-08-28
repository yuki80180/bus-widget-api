import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import app as app_module


class FixedDateTime(datetime):
    current = (2026, 8, 24, 7, 30)
    requested_timezone = None

    @classmethod
    def now(cls, tz=None):
        cls.requested_timezone = tz
        return cls(*cls.current, tzinfo=tz)


class BusApiTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_directory.name) / "bus.db"
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE bus_schedule (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    direction TEXT NOT NULL,
                    day_type TEXT NOT NULL,
                    time TEXT NOT NULL,
                    line TEXT NOT NULL,
                    stop TEXT NOT NULL
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO bus_schedule (direction, day_type, time, line, stop)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    ("to_uni", "weekday", "07:31", "錦町B線 (32)", "A"),
                    ("to_uni", "weekday", "07:42", "錦町B線 (32)", "A"),
                    ("to_uni", "weekday", "08:00", "錦町B線 (32)", "A"),
                    ("to_uni", "weekday", "09:00", "錦町B線 (32)", "A"),
                    ("to_uni", "weekend", "00:10", "錦町B線 (32)", "A"),
                    ("to_station", "weekday", "06:00", "野々市線 (33)", "B"),
                    ("to_nakahashi", "weekday", "07:35", "野々市線 (33)", "B"),
                ],
            )
            conn.commit()

        app_module.app.config.update(TESTING=True)
        FixedDateTime.current = (2026, 8, 24, 7, 30)
        FixedDateTime.requested_timezone = None
        self.client = app_module.app.test_client()
        self.db_patch = patch.object(app_module, "DB_PATH", self.db_path)
        self.datetime_patch = patch.object(app_module, "datetime", FixedDateTime)
        self.db_patch.start()
        self.datetime_patch.start()

    def tearDown(self):
        self.datetime_patch.stop()
        self.db_patch.stop()
        self.temp_directory.cleanup()

    def test_health_check(self):
        response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    def test_web_app_and_manifest_are_served(self):
        page_response = self.client.get("/")
        manifest_response = self.client.get("/static/manifest.webmanifest")

        try:
            self.assertEqual(page_response.status_code, 200)
            page_html = page_response.get_data(as_text=True)
            self.assertIn("KIT Bus", page_html)
            self.assertIn("manifest.webmanifest", page_html)
            self.assertIn("apple-touch-icon.png", page_html)
            self.assertEqual(manifest_response.status_code, 200)
            manifest = manifest_response.get_json()
            self.assertEqual(manifest["display"], "standalone")
            self.assertEqual(
                {icon["sizes"] for icon in manifest["icons"]},
                {"192x192", "512x512", "any"},
            )
        finally:
            page_response.close()
            manifest_response.close()

    def test_next_bus_keeps_existing_fields_and_adds_display_fields(self):
        response = self.client.get("/api/next_bus?dir=to_uni")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["current_time"], "07:30")
        self.assertEqual(data["direction"], "to_uni")
        self.assertEqual(len(data["buses"]), 3)
        self.assertEqual(
            {"time", "line", "stop"}.difference(data["buses"][0]),
            set(),
        )
        self.assertEqual(data["buses"][0]["line_number"], "32")
        self.assertEqual(data["buses"][0]["stop_name"], "正門向い")
        self.assertEqual(
            [bus["minutes_until"] for bus in data["buses"]],
            [1, 12, 30],
        )
        self.assertIsInstance(data["status"], str)
        self.assertIsInstance(data["current_time"], str)
        self.assertIsInstance(data["day_type"], str)
        self.assertTrue(all(isinstance(bus["time"], str) for bus in data["buses"]))
        self.assertTrue(all(isinstance(bus["line"], str) for bus in data["buses"]))
        self.assertTrue(all(isinstance(bus["stop"], str) for bus in data["buses"]))
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_end_of_service_response_contains_direction_details(self):
        response = self.client.get("/api/next_bus?dir=to_station")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["status"], "end")
        self.assertEqual(data["direction_detail"]["to"], "金沢駅")

    def test_invalid_direction_is_rejected(self):
        response = self.client.get("/api/next_bus?dir=unknown")
        data = response.get_json()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(data["status"], "error")
        self.assertEqual(
            data["valid_directions"],
            ["to_nakahashi", "to_station", "to_uni"],
        )
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_empty_schedule_is_an_api_error_not_end_of_service(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("DELETE FROM bus_schedule")
            conn.commit()

        response = self.client.get("/api/next_bus?dir=to_uni")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["status"], "error")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_jst_and_new_day_are_used_for_schedule_selection(self):
        FixedDateTime.current = (2026, 8, 30, 0, 5)

        response = self.client.get("/api/next_bus?dir=to_uni")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(FixedDateTime.requested_timezone, app_module.JST)
        self.assertEqual(data["current_time"], "00:05")
        self.assertEqual(data["day_type"], "weekend")
        self.assertEqual(data["buses"][0]["minutes_until"], 5)


if __name__ == "__main__":
    unittest.main()
