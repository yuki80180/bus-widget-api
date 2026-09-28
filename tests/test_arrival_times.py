import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import app
import init_db
from monitor import audit_arrival_times as audit
from monitor import compare_route_search_normalized as compare
from monitor import convert_route_search_extracted as convert
from monitor import extract_route_search_debug as extract
from monitor import generate_update_proposals as generator
from monitor import preview_update_proposals as preview
from monitor import run_automated_monitor as automated
from monitor import run_route_search_check as runner


ROOT = Path(__file__).resolve().parents[1]


def bus(time="08:00", arrival="08:34", line="(39) 泉野", stop="A"):
    return {"time": time, "arrival_time": arrival, "line": line, "stop": stop}


def schedule():
    return {direction: {day: [bus(stop="A" if direction == "to_uni" else "B"),
                                   bus("09:00", None, stop="A" if direction == "to_uni" else "B")]
                         for day in ("weekday", "weekend")}
            for direction in ("to_uni", "to_station", "to_nakahashi")}


def report(data, detail):
    return generator.build_update_proposals(
        {"comparison_key": ["time", "stop"], "routes": {"to_uni": {"weekday": detail}}}, data,
        generated_at="2026-09-25T00:00:00Z")


def arrival_report():
    data = schedule()
    old = data["to_uni"]["weekday"]
    new = [dict(old[0], arrival_time="08:39"), old[1]]
    return data, report(data, compare.compare_day(old, new))


class ArrivalDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "bus.db"

    def test_valid_null_and_legacy_arrivals(self):
        for value in ("00:00", "23:59", None):
            self.assertEqual(init_db.validate_arrival_time(value), value)
        data = schedule()
        del data["to_uni"]["weekday"][0]["arrival_time"]
        self.assertIsNone(init_db.validate_schedule(data)[0][-1])

    def test_invalid_arrivals_rejected(self):
        for value in ("", "8:34", "24:00", "12:60", "０８:３４", "08:34\n", 834, [], {}, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                init_db.validate_arrival_time(value)

    def test_overnight_format_is_not_rejected_by_time_order(self):
        self.assertEqual(init_db.validate_schedule({"to_uni": {"weekday": [bus("23:50", "00:20")]}})[0][-1], "00:20")

    def test_rebuild_round_trip(self):
        records = init_db.validate_schedule(schedule())
        with patch.object(init_db, "DB_PATH", self.db):
            init_db.initialize_database(records)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            self.assertEqual(conn.execute("SELECT direction,day_type,time,line,stop,arrival_time FROM bus_schedule ORDER BY id").fetchall(), records)

    def create_legacy(self):
        records = init_db.validate_schedule(schedule())
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("CREATE TABLE bus_schedule (id INTEGER PRIMARY KEY, direction TEXT, day_type TEXT, time TEXT, line TEXT, stop TEXT)")
            conn.executemany("INSERT INTO bus_schedule VALUES (?,?,?,?,?,?)", [(n+41, *r[:5]) for n, r in enumerate(records)])
        return records

    def test_migration_is_idempotent_and_preserves_ids(self):
        records = self.create_legacy()
        for _ in range(2):
            init_db.migrate_arrival_times(self.db, records)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            self.assertEqual(conn.execute("SELECT id FROM bus_schedule ORDER BY id").fetchall(), [(n+41,) for n in range(len(records))])
            self.assertEqual(conn.execute("SELECT arrival_time FROM bus_schedule ORDER BY id").fetchall(), [(r[-1],) for r in records])
            self.assertEqual([r for r in conn.execute("PRAGMA table_info(bus_schedule)") if r[1] == "arrival_time"][0][3], 0)

    def test_migration_refuses_mismatch_without_changing_schema_or_rows(self):
        records = self.create_legacy()
        before = self.db.read_bytes()
        with self.assertRaisesRegex(ValueError, "mismatch"):
            init_db.migrate_arrival_times(self.db, records[:-1])
        self.assertEqual(self.db.read_bytes(), before)

    def test_migration_invalid_arrival_rolls_back_alter(self):
        records = self.create_legacy()
        records[-1] = (*records[-1][:5], "bad")
        with self.assertRaises(ValueError):
            init_db.migrate_arrival_times(self.db, records)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            self.assertNotIn("arrival_time", [r[1] for r in conn.execute("PRAGMA table_info(bus_schedule)")])

    def test_production_evidence_integrity_and_unchanged_departures(self):
        data = json.loads((ROOT / "schedule.json").read_text(encoding="utf-8-sig"))
        evidence = json.loads((ROOT / "monitor/arrival_time_evidence.json").read_text(encoding="utf-8"))
        result = audit.audit(data, ROOT / "bus.db", evidence)
        self.assertEqual(result["errors"], [])
        self.assertEqual((result["total"], result["arrival_time_present"], result["arrival_time_null"]), (214, 207, 7))
        self.assertEqual(result["overnight_buses"], [])
        self.assertEqual(sum(c["ambiguous"] for c in result["summary"].values()), 0)
        projection = {r: {d: [{k: v for k, v in b.items() if k != "arrival_time"} for b in rows]
                          for d, rows in days.items()} for r, days in data.items()}
        digest = lambda value: hashlib.sha256(generator.canonical_json(value).encode()).hexdigest()
        self.assertEqual(digest(projection), evidence["baseline_departures_sha256"])
        with closing(sqlite3.connect(ROOT / "bus.db")) as conn:
            rows = conn.execute("SELECT id,direction,day_type,time,line,stop FROM bus_schedule ORDER BY id").fetchall()
        self.assertEqual(digest(rows), evidence["baseline_db_rows_sha256"])

    def test_evidence_ambiguous_and_weekend_disagreement_are_null(self):
        row = {"depart_time": "08:00", "arrive_time": "08:34", "line": "39", "arrive_pole": "A",
               "depart_stop": "金沢駅", "arrive_stop": "金沢工業大学", "page_index": 1}
        evidence = {"sources": {"kanazawa_station_to_uni_"+d: {"rows": [copy.deepcopy(row)]}
                                for d in ("weekday", "saturday", "weekend")}}
        self.assertEqual(audit.classify_bus("to_uni", "weekend", bus(), evidence)[0], "08:34")
        evidence["sources"]["kanazawa_station_to_uni_weekend"]["rows"][0]["arrive_time"] = "08:35"
        self.assertEqual(audit.classify_bus("to_uni", "weekend", bus(), evidence)[1], "ambiguous")
        evidence["sources"]["kanazawa_station_to_uni_weekday"]["rows"].append(row)
        self.assertEqual(audit.classify_bus("to_uni", "weekday", bus(), evidence)[1], "ambiguous")


class ArrivalApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "bus.db"
        with patch.object(init_db, "DB_PATH", self.db):
            init_db.initialize_database(init_db.validate_schedule(schedule()))
        self.db_patch = patch.object(app, "DB_PATH", self.db)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.client = app.app.test_client()

    def get(self, endpoint, direction, hour=7):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 9, 25, hour, 0, tzinfo=tz)
        with patch.object(app, "datetime", Clock):
            return self.client.get(endpoint + "?dir=" + direction)

    def test_next_bus_arrival_null_legacy_fields_and_countdown_all_directions(self):
        for direction in app.VALID_DIRECTIONS:
            data = self.get("/api/next_bus", direction).get_json()
            self.assertEqual([b["arrival_time"] for b in data["buses"]], ["08:34", None])
            self.assertEqual([b["minutes_until"] for b in data["buses"]], [60, 120])
            self.assertTrue({"time", "line", "line_number", "stop", "stop_name", "minutes_until"} <= data["buses"][0].keys())

    def test_timetable_keeps_equal_departure_order(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("INSERT INTO bus_schedule (direction,day_type,time,line,stop,arrival_time) VALUES ('to_uni','weekday','08:00','(33) 寺地','C','08:32')")
        response = self.get("/api/timetable", "to_uni")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual([b["arrival_time"] for b in response.get_json()["buses"]], ["08:34", "08:32", None])

    def test_next_service_arrival_and_null_all_directions(self):
        for arrival in ("08:34", None):
            with closing(sqlite3.connect(self.db)) as conn, conn:
                conn.execute("UPDATE bus_schedule SET arrival_time=? WHERE time='08:00'", (arrival,))
            for direction in app.VALID_DIRECTIONS:
                data = self.get("/api/next_bus", direction, hour=23).get_json()["next_service"]
                self.assertEqual(data["bus"]["arrival_time"], arrival)
                self.assertEqual((data["date"], data["day_type"], data["days_ahead"]), ("2026-09-26", "weekend", 1))

    def test_existing_db_serves_null_without_request_time_migration(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("ALTER TABLE bus_schedule DROP COLUMN arrival_time")
        before = self.db.read_bytes()
        for endpoint in ("/api/next_bus", "/api/timetable"):
            data = self.get(endpoint, "to_uni").get_json()
            self.assertTrue(all(b["arrival_time"] is None for b in data["buses"]))
        self.assertEqual(before, self.db.read_bytes())

    def test_invalid_direction_missing_db_empty_db(self):
        self.assertEqual(self.get("/api/next_bus", "invalid").status_code, 400)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("DELETE FROM bus_schedule")
        self.assertEqual(self.get("/api/timetable", "to_uni").status_code, 503)
        self.db.unlink()
        self.assertEqual(self.get("/api/next_bus", "to_uni").status_code, 503)


class ArrivalMonitorTests(unittest.TestCase):
    def test_extract_official_endpoint_times(self):
        html = '<table class="pathway"><tr class="pathway-times"><td name="time_arr[]" value="15:41">15:41</td></tr><tr class="pathway-times"><td name="time_dep[]" value="16:12">16:12</td></tr></table>'
        parser = extract.RouteSearchParser()
        parser.feed(html)
        row = extract.table_to_item(parser.tables[0], "fixture.html", "kanazawa_station_to_uni_weekday", 1)
        self.assertEqual((row["depart_time"], row["arrive_time"], row["arrival_time"]), ("15:41", "16:12", "16:12"))

    def test_normalize_old_new_null_invalid_and_wrong_endpoint(self):
        row = {"route_key": "kanazawa_station_to_uni_weekday", "depart_time": "08:00", "line": "39",
               "depart_stop": "金沢駅［東口１０］", "arrive_stop": "金沢工業大学", "arrive_pole": "A"}
        self.assertIsNone(convert.normalize_item(row)["arrival_time"])
        self.assertEqual(convert.normalize_item(dict(row, arrive_time="08:34"))["arrival_time"], "08:34")
        self.assertIsNone(convert.normalize_item(dict(row, arrive_time="08:34", arrive_stop="中橋"))["arrival_time"])
        with self.assertRaises(ValueError):
            convert.normalize_item(dict(row, arrive_time="wrong"))

    def test_unchanged_arrival_and_old_source_never_erase(self):
        old = bus()
        legacy = {k: v for k, v in old.items() if k != "arrival_time"}
        for new in (old, legacy, dict(old, arrival_time=None)):
            self.assertEqual(compare.compare_day([old], [new])["arrival_time_changes"], [])

    def test_arrival_only_compare_and_proposal_require_review(self):
        data, result = arrival_report()
        p = result["proposals"][0]
        self.assertEqual((p["change_type"], p["status"]), ("arrival_time_change", "needs_review"))
        self.assertEqual(p["changes"][0]["before"], data["to_uni"]["weekday"][0])
        self.assertEqual(p["changes"][0]["after"]["arrival_time"], "08:39")
        self.assertEqual(result["simulation"]["status"], "passed")
        self.assertFalse(result["apply_allowed"])

    def test_departure_and_arrival_change_preserved_and_unknown_cleared(self):
        data = schedule()
        for arrival in ("08:39", None):
            detail = compare.compare_day(data["to_uni"]["weekday"], [bus("08:05", arrival), bus("09:00", None)])
            result = report(data, detail)
            self.assertEqual(len(result["proposals"]), 1)
            self.assertEqual(result["proposals"][0]["changes"][0]["after"], bus("08:05", arrival))

    def test_added_and_removed_keep_arrival(self):
        data = schedule()
        detail = compare.compare_day(data["to_uni"]["weekday"], [bus("12:00", "12:34"), bus("09:00", None)])
        proposals = report(data, detail)["proposals"]
        self.assertEqual({p["change_type"] for p in proposals}, {"add", "remove"})
        for p in proposals:
            change = p["changes"][0]
            self.assertEqual((change["after"] or change["before"])["arrival_time"], "12:34" if p["change_type"] == "add" else "08:34")

    def test_ambiguous_arrival_is_quarantined(self):
        data = schedule()
        detail = compare.compare_day([bus()], [bus(arrival="08:35"), bus(arrival="08:36")])
        result = report(data, detail)
        self.assertEqual(result["proposals"], [])
        self.assertIn("ambiguous_arrival_time_change", result["validation_errors"][0]["codes"])
        reversed_detail = compare.compare_day([bus()], [bus(arrival="08:36"), bus(arrival="08:35")])
        self.assertEqual(detail, reversed_detail)

    def test_mismatched_embedded_context_and_trip_are_rejected(self):
        data = schedule()
        for field, value in (("direction", "to_station"), ("day_type", "weekend"), ("stop", "C")):
            detail = compare.compare_day([bus()], [bus(arrival="08:39")])
            detail["arrival_time_changes"][0]["route_search_item"][field] = value
            result = report(data, detail)
            self.assertEqual(result["proposals"], [])
            self.assertTrue(result["validation_errors"])

    def test_old_added_candidate_gets_null_in_arrival_schedule(self):
        data = schedule()
        new = bus("12:00")
        del new["arrival_time"]
        result = report(data, compare.compare_day(data["to_uni"]["weekday"], [*data["to_uni"]["weekday"], new]))
        self.assertIsNone(result["proposals"][0]["changes"][0]["after"]["arrival_time"])

    def test_invalid_arrival_is_not_coerced_to_null(self):
        with self.assertRaises(ValueError):
            compare.normalize_bus(bus(arrival="08:99"))
        data = schedule()
        detail = compare.compare_day([bus()], [bus(arrival="bad")])
        result = report(data, detail)
        self.assertEqual(result["proposals"], [])
        self.assertTrue(result["validation_errors"])

    def test_proposal_id_includes_arrival_and_is_deterministic(self):
        data, first = arrival_report()
        second = report(data, compare.compare_day(data["to_uni"]["weekday"], [bus(arrival="08:40"), bus("09:00", None)]))
        self.assertNotEqual(first["proposals"][0]["proposal_id"], second["proposals"][0]["proposal_id"])
        self.assertEqual(first, arrival_report()[1])

    def test_preview_arrival_and_legacy_schedule_is_read_only(self):
        for legacy in (False, True):
            data, result = arrival_report()
            if legacy:
                data["to_uni"]["weekday"][0].pop("arrival_time")
                result = report(data, compare.compare_day(data["to_uni"]["weekday"], [bus(arrival="08:39"), bus("09:00", None)]))
            p = result["proposals"][0]
            with self.assertRaises(preview.PreviewError):
                preview.build_preview(result, [p["proposal_id"]], json.dumps(data).encode())
            p["status"] = "approved"  # Synthetic human approval, preview only.
            original = json.dumps(data, ensure_ascii=False).encode()
            artifacts = preview.build_preview(result, [p["proposal_id"]], original)
            self.assertEqual(artifacts, preview.build_preview(result, [p["proposal_id"]], original))
            candidate = json.loads(artifacts["schedule.preview.json"])
            self.assertEqual(candidate["to_uni"]["weekday"][0]["arrival_time"], "08:39")
            self.assertIn("[到着時刻変更]", artifacts["summary.txt"].decode())
            self.assertEqual(original, json.dumps(data, ensure_ascii=False).encode())

    def test_preview_rejects_stale_arrival_and_conflicts(self):
        data, result = arrival_report()
        p = result["proposals"][0]
        p["status"] = "approved"
        data["to_uni"]["weekday"][0]["arrival_time"] = "08:38"
        with self.assertRaisesRegex(preview.PreviewError, "stale/conflict"):
            preview.build_preview(result, [p["proposal_id"]], json.dumps(data).encode())
        data, result = arrival_report()
        p = result["proposals"][0]
        p["status"] = "approved"
        q = copy.deepcopy(p)
        q["changes"][0]["after"]["arrival_time"] = "08:40"
        q["proposal_id"] = preview.proposal_identity(q)
        result["proposals"].append(q)
        with self.assertRaisesRegex(preview.PreviewError, "conflicting"):
            preview.build_preview(result, [p["proposal_id"], q["proposal_id"]], json.dumps(data).encode())

    def test_simulation_rejects_stale_arrival(self):
        data, result = arrival_report()
        data["to_uni"]["weekday"][0]["arrival_time"] = "08:38"
        simulation = generator.simulate_proposals(data, result["proposals"], include_candidate=True)
        self.assertEqual(simulation["status"], "failed")
        self.assertNotIn("candidate_schedule", simulation)

    def test_notify_fingerprint_and_fail_on_diff_include_arrival(self):
        normal = {"routes": {}, "summary": {"added_count": 0, "removed_count": 0}}
        data = schedule()
        def comparison_for(arrival):
            detail = compare.compare_day([bus()], [bus(arrival=arrival)])
            return {"routes": {"to_uni": {"weekday": detail}},
                    "summary": {key: detail[key] for key in automated.ROUTE_SUMMARY_KEYS}}
        original = automated.build_status(normal, comparison_for("08:34"))
        changed = automated.build_status(normal, comparison_for("08:39"))
        self.assertFalse(original["has_diff"])
        self.assertTrue(changed["has_diff"])
        self.assertNotEqual(original["fingerprint"], changed["fingerprint"])
        self.assertTrue(any(runner.print_summary(comparison_for("08:39"))))
        old = comparison_for("08:34")
        del old["summary"]["arrival_time_change_count"]
        del old["routes"]["to_uni"]["weekday"]["arrival_time_changes"]
        self.assertFalse(automated.build_status(normal, old)["has_diff"])


if __name__ == "__main__":
    unittest.main()
