import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timezone
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


class FixedUtcInstantDateTime(datetime):
    current_utc = datetime(2026, 9, 23, 15, 5, tzinfo=timezone.utc)
    requested_timezone = None

    @classmethod
    def now(cls, tz=None):
        cls.requested_timezone = tz
        if tz is None:
            return cls.current_utc.replace(tzinfo=None)
        return cls.current_utc.astimezone(tz)


class ServiceDayTypeTestCase(unittest.TestCase):
    def test_service_day_type_uses_weekends_and_japanese_holidays(self):
        cases = [
            ("ordinary Monday", date(2026, 8, 24), "weekday"),
            ("ordinary Saturday", date(2026, 8, 29), "weekend"),
            ("ordinary Sunday", date(2026, 8, 30), "weekend"),
            ("Respect for the Aged Day", date(2026, 9, 21), "weekend"),
            ("substitute holiday", date(2026, 5, 6), "weekend"),
            ("citizen's holiday", date(2026, 9, 22), "weekend"),
            ("Vernal Equinox Day", date(2026, 3, 20), "weekend"),
            ("Autumnal Equinox Day", date(2026, 9, 23), "weekend"),
            ("weekday after the holidays", date(2026, 9, 24), "weekday"),
        ]

        for label, service_date, expected in cases:
            with self.subTest(label=label, service_date=service_date):
                self.assertEqual(
                    app_module.get_service_day_type(service_date),
                    expected,
                )


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
                    ("to_station", "weekend", "07:15", "野々市線 (33)", "D"),
                    ("to_nakahashi", "weekday", "07:35", "野々市線 (33)", "B"),
                    ("to_nakahashi", "weekend", "07:05", "野々市線 (33)", "B"),
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

    def assert_next_service(
            self,
            data,
            expected_date,
            expected_day_type,
            expected_time,
            expected_days_ahead=1):
        next_service = data["next_service"]
        self.assertEqual(next_service["date"], expected_date)
        self.assertEqual(next_service["day_type"], expected_day_type)
        self.assertEqual(next_service["days_ahead"], expected_days_ahead)
        self.assertEqual(next_service["bus"]["time"], expected_time)
        self.assertEqual(
            {"time", "line", "line_number", "stop", "stop_name"},
            set(next_service["bus"]),
        )
        self.assertNotIn("minutes_until", next_service["bus"])

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
        with patch.object(app_module, "find_next_service") as next_service_mock:
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
        self.assertNotIn("next_service", data)
        next_service_mock.assert_not_called()
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_end_of_service_response_contains_direction_details(self):
        response = self.client.get("/api/next_bus?dir=to_station")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["status"], "end")
        self.assertEqual(data["direction_detail"]["to"], "金沢駅")
        self.assertNotIn("buses", data)
        self.assert_next_service(
            data,
            "2026-08-25",
            "weekday",
            "06:00",
        )

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

    def test_weekday_holiday_uses_weekend_schedule(self):
        FixedDateTime.current = (2026, 9, 21, 0, 5)

        response = self.client.get("/api/next_bus?dir=to_uni")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["current_time"], "00:05")
        self.assertEqual(data["day_type"], "weekend")
        self.assertEqual(data["buses"][0]["time"], "00:10")
        self.assertEqual(data["buses"][0]["minutes_until"], 5)

    def test_utc_instant_uses_the_jst_service_date(self):
        FixedUtcInstantDateTime.requested_timezone = None

        with patch.object(app_module, "datetime", FixedUtcInstantDateTime):
            response = self.client.get("/api/next_bus?dir=to_uni")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            FixedUtcInstantDateTime.requested_timezone,
            app_module.JST,
        )
        self.assertEqual(data["current_time"], "00:05")
        self.assertEqual(data["day_type"], "weekday")
        self.assertEqual(data["buses"][0]["time"], "07:31")

    def test_end_of_service_finds_expected_day_type_transitions(self):
        cases = [
            ("weekday to weekday", (2026, 8, 24), "2026-08-25", "weekday", "07:31"),
            ("Friday to Saturday", (2026, 8, 28), "2026-08-29", "weekend", "00:10"),
            ("Saturday to Sunday", (2026, 8, 29), "2026-08-30", "weekend", "00:10"),
            ("Sunday to Monday", (2026, 8, 30), "2026-08-31", "weekday", "07:31"),
            ("ordinary day to holiday", (2026, 2, 10), "2026-02-11", "weekend", "00:10"),
            ("Sunday to holiday", (2026, 9, 20), "2026-09-21", "weekend", "00:10"),
            ("holiday to citizen's holiday", (2026, 9, 21), "2026-09-22", "weekend", "00:10"),
            ("citizen's holiday to equinox", (2026, 9, 22), "2026-09-23", "weekend", "00:10"),
            ("holiday to weekday", (2026, 9, 23), "2026-09-24", "weekday", "07:31"),
        ]

        for label, current_date, expected_date, day_type, time in cases:
            with self.subTest(label=label):
                FixedDateTime.current = (*current_date, 23, 59)
                response = self.client.get("/api/next_bus?dir=to_uni")
                data = response.get_json()

                self.assertEqual(response.status_code, 200)
                self.assertEqual(data["status"], "end")
                self.assert_next_service(data, expected_date, day_type, time)

    def test_end_of_service_finds_first_bus_for_all_directions(self):
        FixedDateTime.current = (2026, 8, 24, 23, 59)
        expected = {
            "to_uni": "07:31",
            "to_station": "06:00",
            "to_nakahashi": "07:35",
        }

        for direction, expected_time in expected.items():
            with self.subTest(direction=direction):
                response = self.client.get(f"/api/next_bus?dir={direction}")
                data = response.get_json()

                self.assertEqual(response.status_code, 200)
                self.assertEqual(data["status"], "end")
                self.assertEqual(data["direction"], direction)
                self.assert_next_service(
                    data,
                    "2026-08-25",
                    "weekday",
                    expected_time,
                )

    def test_missing_tomorrow_schedule_skips_to_next_operating_day(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                "DELETE FROM bus_schedule WHERE direction = ? AND day_type = ?",
                ("to_uni", "weekend"),
            )
            conn.commit()
        FixedDateTime.current = (2026, 8, 28, 23, 59)

        response = self.client.get("/api/next_bus?dir=to_uni")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["status"], "end")
        self.assert_next_service(
            data,
            "2026-08-31",
            "weekday",
            "07:31",
            expected_days_ahead=3,
        )

    def test_next_service_search_is_bounded_to_seven_days(self):
        with patch.object(app_module, "fetch_first_bus", return_value=None) as fetch_mock:
            result = app_module.find_next_service("to_uni", date(2026, 8, 24))

        self.assertIsNone(result)
        self.assertEqual(fetch_mock.call_count, app_module.NEXT_SERVICE_SEARCH_DAYS)

    def test_end_response_handles_no_next_service(self):
        FixedDateTime.current = (2026, 8, 24, 23, 59)

        with patch.object(app_module, "find_next_service", return_value=None):
            response = self.client.get("/api/next_bus?dir=to_uni")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["status"], "end")
        self.assertIsNone(data["next_service"])

    def test_next_service_database_error_is_not_end_of_service(self):
        FixedDateTime.current = (2026, 8, 24, 23, 59)

        with patch.object(
                app_module,
                "fetch_first_bus",
                side_effect=sqlite3.OperationalError("database error")):
            response = self.client.get("/api/next_bus?dir=to_uni")
        data = response.get_json()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(data["status"], "error")
        self.assertNotEqual(data.get("status"), "end")

    def test_first_bus_lookup_includes_midnight_departure(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO bus_schedule (direction, day_type, time, line, stop)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("to_uni", "weekend", "00:00", "始発線 (99)", "A"),
            )
            conn.commit()

        bus = app_module.fetch_first_bus("to_uni", "weekend")

        self.assertEqual(bus["time"], "00:00")
        self.assertEqual(bus["line_number"], "99")
        self.assertNotIn("minutes_until", bus)

    def test_jst_midnight_switches_preview_to_today_service(self):
        FixedDateTime.current = (2026, 8, 29, 23, 59)

        before_midnight = self.client.get("/api/next_bus?dir=to_uni").get_json()

        self.assertEqual(before_midnight["status"], "end")
        self.assert_next_service(
            before_midnight,
            "2026-08-30",
            "weekend",
            "00:10",
        )

        FixedDateTime.current = (2026, 8, 30, 0, 0)
        after_midnight = self.client.get("/api/next_bus?dir=to_uni").get_json()

        self.assertEqual(after_midnight["status"], "success")
        self.assertEqual(after_midnight["day_type"], "weekend")
        self.assertEqual(after_midnight["buses"][0]["time"], "00:10")
        self.assertEqual(after_midnight["buses"][0]["minutes_until"], 10)
        self.assertNotIn("next_service", after_midnight)


if __name__ == "__main__":
    unittest.main()
