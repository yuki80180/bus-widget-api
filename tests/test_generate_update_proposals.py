import copy
import io
import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from monitor import generate_update_proposals as proposal_module


FIXED_GENERATED_AT = "2026-09-04T00:00:00Z"


class UpdateProposalTestCase(unittest.TestCase):
    def setUp(self):
        self.removed_bus = {
            "time": "08:00",
            "line": "(34) 久安",
            "stop": "A",
        }
        self.line_source_bus = {
            "time": "09:00",
            "line": "(39) 泉野",
            "stop": "A",
        }
        self.time_change_bus = {
            "time": "10:00",
            "line": "(33) 寺地・四十万行",
            "stop": "C",
        }
        self.station_bus = {
            "time": "07:00",
            "line": "(60) 寺地・金石行",
            "stop": "D",
        }
        self.schedule = {
            "to_uni": {
                "weekday": [
                    copy.deepcopy(self.removed_bus),
                    copy.deepcopy(self.line_source_bus),
                    copy.deepcopy(self.time_change_bus),
                ],
                "weekend": [],
            },
            "to_station": {
                "weekday": [copy.deepcopy(self.station_bus)],
                "weekend": [],
            },
            "to_nakahashi": {
                "weekday": [],
                "weekend": [],
            },
        }

    @staticmethod
    def day_detail(
        *,
        added=None,
        removed=None,
        line_only=None,
        time_change_candidates=None,
    ):
        added = copy.deepcopy(added or [])
        removed = copy.deepcopy(removed or [])
        line_only = copy.deepcopy(line_only or [])
        time_change_candidates = copy.deepcopy(time_change_candidates or [])
        return {
            "existing_count": 0,
            "route_search_count": 0,
            "added": added,
            "removed": removed,
            "line_only": line_only,
            "time_change_candidates": time_change_candidates,
            "added_count": len(added),
            "removed_count": len(removed),
            "line_only_count": len(line_only),
            "time_change_candidate_count": len(time_change_candidates),
        }

    @staticmethod
    def comparison(routes):
        summary = {
            "added_count": 0,
            "removed_count": 0,
            "line_only_count": 0,
            "time_change_candidate_count": 0,
        }
        for days in routes.values():
            for detail in days.values():
                for key in summary:
                    summary[key] += int(detail.get(key, 0))
        return {
            "comparison_key": ["time", "stop"],
            "line_comparison": "line_only when time and stop match but line differs",
            "routes": copy.deepcopy(routes),
            "summary": summary,
        }

    @staticmethod
    def added_candidate(time="09:30", line="39", stop="A"):
        return {"time": time, "line": line, "stop": stop}

    def time_change_candidate(self, old_time="10:00", new_time="10:05"):
        existing_item = copy.deepcopy(self.time_change_bus)
        existing_item["time"] = old_time
        return {
            "line": "33",
            "stop": "C",
            "old_time": old_time,
            "new_time": new_time,
            "difference_minutes": 5,
            "existing_item": existing_item,
            "route_search_item": {
                "time": new_time,
                "line": "33",
                "stop": "C",
            },
        }

    @staticmethod
    def reviewed_entry(
        *,
        direction="to_uni",
        day_type="weekday",
        candidate_type,
        key,
        source="official_test_source",
        note="fixture confirmed",
    ):
        return {
            "route": direction,
            "day_type": day_type,
            "type": candidate_type,
            "key": copy.deepcopy(key),
            "status": "confirmed",
            "source": source,
            "reviewed_note": note,
        }

    @staticmethod
    def reviewed_data(*entries):
        return {
            "schema_version": 1,
            "scope": "route_search",
            "reviewed": [copy.deepcopy(entry) for entry in entries],
        }

    def build_report(
        self,
        detail,
        *,
        schedule=None,
        reviewed_data=None,
        generated_at=FIXED_GENERATED_AT,
        direction="to_uni",
        day_type="weekday",
    ):
        comparison = self.comparison({direction: {day_type: detail}})
        return proposal_module.build_update_proposals(
            comparison,
            copy.deepcopy(self.schedule if schedule is None else schedule),
            reviewed_data=copy.deepcopy(reviewed_data),
            generated_at=generated_at,
            source_path="test/route_search_compare.json",
        )

    def assert_error_code_contains(self, report, expected_fragment):
        codes = [
            str(code)
            for error in report["validation_errors"]
            for code in error.get("codes", [])
        ]
        self.assertTrue(
            any(expected_fragment in code for code in codes),
            f"expected an error code containing {expected_fragment!r}, got {codes!r}",
        )

    def test_added_candidate_resolves_unique_schedule_line_and_creates_add_change(self):
        candidate = self.added_candidate()

        report = self.build_report(self.day_detail(added=[candidate]))

        self.assertFalse(report["apply_allowed"])
        self.assertEqual(report["proposal_version"], 1)
        self.assertEqual(len(report["proposals"]), 1)
        proposal = report["proposals"][0]
        self.assertEqual(proposal["change_type"], "add")
        self.assertEqual(proposal["status"], "needs_review")
        self.assertEqual(proposal["direction"], "to_uni")
        self.assertEqual(proposal["day_type"], "weekday")
        self.assertEqual(
            proposal["changes"],
            [
                {
                    "operation": "add",
                    "before": None,
                    "after": {
                        "time": "09:30",
                        "line": "(39) 泉野",
                        "stop": "A",
                    },
                }
            ],
        )
        self.assertEqual(report["simulation"]["status"], "passed")

    def test_removed_candidate_creates_remove_change_for_exact_schedule_bus(self):
        report = self.build_report(
            self.day_detail(removed=[self.removed_bus])
        )

        self.assertEqual(len(report["proposals"]), 1)
        proposal = report["proposals"][0]
        self.assertEqual(proposal["change_type"], "remove")
        self.assertEqual(
            proposal["changes"],
            [
                {
                    "operation": "remove",
                    "before": self.removed_bus,
                    "after": None,
                }
            ],
        )

    def test_line_only_is_not_actionable_and_is_reported_as_validation_error(self):
        line_only = {
            "time": "09:00",
            "stop": "A",
            "existing_line": "(39) 泉野",
            "route_search_line": "49",
            "existing_line_normalized": "39",
            "route_search_line_normalized": "49",
        }

        report = self.build_report(self.day_detail(line_only=[line_only]))

        self.assertEqual(report["proposals"], [])
        self.assert_error_code_contains(report, "line_only")
        self.assertEqual(report["summary"]["validation_error_count"], 1)

    def test_time_change_creates_replace_and_suppresses_component_add_remove(self):
        candidate = self.time_change_candidate()
        new_item = candidate["route_search_item"]
        old_item = candidate["existing_item"]
        detail = self.day_detail(
            added=[new_item],
            removed=[old_item],
            time_change_candidates=[candidate],
        )

        report = self.build_report(detail)

        self.assertEqual(len(report["proposals"]), 1)
        proposal = report["proposals"][0]
        self.assertEqual(proposal["change_type"], "time_change")
        self.assertEqual(proposal["status"], "needs_review")
        self.assertEqual(
            proposal["changes"],
            [
                {
                    "operation": "replace",
                    "before": self.time_change_bus,
                    "after": {
                        "time": "10:05",
                        "line": "(33) 寺地・四十万行",
                        "stop": "C",
                    },
                }
            ],
        )
        self.assertEqual(
            report["summary"].get("paired_components_suppressed"),
            2,
        )

    def test_exact_duplicate_candidates_create_one_proposal(self):
        candidate = self.added_candidate()
        report = self.build_report(
            self.day_detail(added=[candidate, candidate])
        )

        self.assertEqual(len(report["proposals"]), 1)
        self.assertEqual(report["summary"].get("duplicates_suppressed"), 1)

    def test_invalid_direction_is_rejected_without_proposal(self):
        report = self.build_report(
            self.day_detail(added=[self.added_candidate()]),
            direction="unknown",
        )

        self.assertEqual(report["proposals"], [])
        self.assert_error_code_contains(report, "direction")

    def test_invalid_day_type_is_rejected_without_proposal(self):
        report = self.build_report(
            self.day_detail(added=[self.added_candidate()]),
            day_type="holiday",
        )

        self.assertEqual(report["proposals"], [])
        self.assert_error_code_contains(report, "day_type")

    def test_invalid_time_is_rejected_without_proposal(self):
        for invalid_time in ("7:05", "24:00", "12:60", "not-a-time"):
            with self.subTest(invalid_time=invalid_time):
                report = self.build_report(
                    self.day_detail(
                        added=[self.added_candidate(time=invalid_time)]
                    )
                )

                self.assertEqual(report["proposals"], [])
                self.assert_error_code_contains(report, "time")

    def test_removed_candidate_absent_from_schedule_is_validation_error(self):
        missing = {
            "time": "12:00",
            "line": "(34) 久安",
            "stop": "A",
        }

        report = self.build_report(self.day_detail(removed=[missing]))

        self.assertEqual(report["proposals"], [])
        self.assert_error_code_contains(report, "removed")

    def test_added_candidate_already_present_in_schedule_is_validation_error(self):
        already_present = self.added_candidate(time="09:00")

        report = self.build_report(
            self.day_detail(added=[already_present])
        )

        self.assertEqual(report["proposals"], [])
        self.assert_error_code_contains(report, "added")

    def test_proposal_id_is_stable_sha256_and_excludes_generated_at_and_review(self):
        candidate = self.added_candidate()
        detail = self.day_detail(added=[candidate])
        review = self.reviewed_entry(
            candidate_type="added",
            key={"time": "09:30", "line_normalized": "39", "stop": "A"},
        )

        unreviewed = self.build_report(
            detail,
            generated_at="2026-09-04T00:00:00Z",
        )["proposals"][0]
        confirmed = self.build_report(
            detail,
            reviewed_data=self.reviewed_data(review),
            generated_at="2026-09-05T00:00:00Z",
        )["proposals"][0]

        self.assertRegex(unreviewed["proposal_id"], re.compile(r"^[0-9a-f]{64}$"))
        self.assertEqual(unreviewed["proposal_id"], confirmed["proposal_id"])
        self.assertEqual(unreviewed["status"], "needs_review")
        self.assertEqual(confirmed["status"], "pending")

    def test_proposal_order_is_identical_for_shuffled_routes_and_candidates(self):
        early_add = self.added_candidate(time="09:15")
        late_add = self.added_candidate(time="09:45")
        uni_forward = self.day_detail(
            added=[late_add, early_add],
            removed=[self.removed_bus],
        )
        uni_reverse = self.day_detail(
            added=[early_add, late_add],
            removed=[self.removed_bus],
        )
        station = self.day_detail(removed=[self.station_bus])
        comparison_a = self.comparison(
            {
                "to_station": {"weekday": station},
                "to_uni": {"weekday": uni_forward},
            }
        )
        comparison_b = self.comparison(
            {
                "to_uni": {"weekday": uni_reverse},
                "to_station": {"weekday": station},
            }
        )

        report_a = proposal_module.build_update_proposals(
            comparison_a,
            copy.deepcopy(self.schedule),
            generated_at=FIXED_GENERATED_AT,
            source_path="test/route_search_compare.json",
        )
        report_b = proposal_module.build_update_proposals(
            comparison_b,
            copy.deepcopy(self.schedule),
            generated_at=FIXED_GENERATED_AT,
            source_path="test/route_search_compare.json",
        )

        self.assertEqual(report_a["proposals"], report_b["proposals"])
        self.assertEqual(report_a["validation_errors"], report_b["validation_errors"])

    def test_confirmed_review_is_reflected_as_pending_never_approved(self):
        review = self.reviewed_entry(
            candidate_type="removed",
            key={"time": "08:00", "line_normalized": "34", "stop": "A"},
            source="official_bus_stop_timetable",
            note="official source checked",
        )

        report = self.build_report(
            self.day_detail(removed=[self.removed_bus]),
            reviewed_data=self.reviewed_data(review),
        )

        proposal = report["proposals"][0]
        self.assertEqual(proposal["status"], "pending")
        self.assertNotEqual(proposal["status"], "approved")
        self.assertEqual(
            proposal["review"],
            {
                "matched": True,
                "candidate_status": "confirmed",
                "source": "official_bus_stop_timetable",
                "reviewed_note": "official source checked",
            },
        )
        self.assertEqual(report["summary"]["by_status"]["approved"], 0)
        self.assertEqual(report["summary"]["by_status"]["rejected"], 0)

    def test_simulation_succeeds_on_copy_without_mutating_input_schedule(self):
        candidate = self.added_candidate()
        report = self.build_report(
            self.day_detail(
                added=[candidate],
                removed=[self.removed_bus],
            )
        )
        original = copy.deepcopy(self.schedule)

        simulation = proposal_module.simulate_proposals(
            self.schedule,
            report["proposals"],
        )

        self.assertEqual(simulation["status"], "passed")
        self.assertEqual(simulation["proposals_simulated"], 2)
        self.assertEqual(simulation["errors"], [])
        self.assertEqual(simulation["before_bus_count"], 4)
        self.assertEqual(simulation["after_bus_count"], 4)
        self.assertEqual(simulation["bus_count_delta"], 0)
        self.assertEqual(self.schedule, original)
        json.dumps(simulation, ensure_ascii=False)

    def test_simulation_reports_duplicate_bus_after_apply(self):
        duplicate_add = {
            "proposal_id": "test-duplicate",
            "source": "route_search",
            "direction": "to_uni",
            "day_type": "weekday",
            "change_type": "add",
            "status": "needs_review",
            "review": {"candidate_status": "unreviewed"},
            "changes": [
                {
                    "operation": "add",
                    "path": ["to_uni", "weekday"],
                    "before": None,
                    "after": copy.deepcopy(self.line_source_bus),
                }
            ],
        }

        simulation = proposal_module.simulate_proposals(
            copy.deepcopy(self.schedule),
            [duplicate_add],
        )

        self.assertEqual(simulation["status"], "failed")
        self.assertIn("duplicate", json.dumps(simulation["errors"]).lower())

    def test_ambiguous_or_unresolved_added_line_is_validation_error(self):
        cases = []

        unresolved_schedule = copy.deepcopy(self.schedule)
        unresolved = self.added_candidate(time="12:00", line="49")
        cases.append(("unresolved", unresolved_schedule, unresolved))

        ambiguous_schedule = copy.deepcopy(self.schedule)
        ambiguous_schedule["to_uni"]["weekday"].extend(
            [
                {"time": "11:00", "line": "(49) 経路A", "stop": "A"},
                {"time": "11:30", "line": "(49) 経路B", "stop": "A"},
            ]
        )
        ambiguous = self.added_candidate(time="12:00", line="49")
        cases.append(("ambiguous", ambiguous_schedule, ambiguous))

        for expected_code, schedule, candidate in cases:
            with self.subTest(expected_code=expected_code):
                report = self.build_report(
                    self.day_detail(added=[candidate]),
                    schedule=schedule,
                )

                self.assertEqual(report["proposals"], [])
                self.assert_error_code_contains(report, expected_code)

    def test_invalid_line_and_stop_are_rejected(self):
        for field, value in (
            ("line", "not-a-route"),
            ("line", ""),
            ("line", None),
            ("stop", "Z"),
            ("stop", None),
        ):
            with self.subTest(field=field, value=value):
                candidate = self.added_candidate()
                candidate[field] = value

                report = self.build_report(self.day_detail(added=[candidate]))

                self.assertEqual(report["proposals"], [])
                self.assert_error_code_contains(report, field)

    def test_conflicting_time_changes_quarantine_every_shared_old_bus(self):
        earlier = self.time_change_candidate(new_time="10:05")
        later = self.time_change_candidate(new_time="10:10")
        later["difference_minutes"] = 10
        detail = self.day_detail(
            added=[earlier["route_search_item"], later["route_search_item"]],
            removed=[earlier["existing_item"]],
            time_change_candidates=[earlier, later],
        )

        report = self.build_report(detail)
        detail["time_change_candidates"].reverse()
        reversed_report = self.build_report(detail)

        self.assertEqual(report["proposals"], [])
        self.assertTrue(report["validation_errors"])
        self.assertEqual(report, reversed_report)

    def test_conflicting_time_changes_quarantine_every_shared_new_bus(self):
        earlier = self.time_change_candidate(old_time="09:55", new_time="10:05")
        earlier["difference_minutes"] = 10
        later = self.time_change_candidate(new_time="10:05")
        schedule = copy.deepcopy(self.schedule)
        schedule["to_uni"]["weekday"].append(earlier["existing_item"])

        report = self.build_report(
            self.day_detail(
                added=[later["route_search_item"]],
                removed=[earlier["existing_item"], later["existing_item"]],
                time_change_candidates=[earlier, later],
            ),
            schedule=schedule,
        )

        self.assertEqual(report["proposals"], [])
        self.assertTrue(report["validation_errors"])

    def test_duplicate_time_change_emits_one_replace_without_validation_errors(self):
        candidate = self.time_change_candidate()

        report = self.build_report(
            self.day_detail(
                added=[candidate["route_search_item"]],
                removed=[candidate["existing_item"]],
                time_change_candidates=[candidate, copy.deepcopy(candidate)],
            )
        )

        self.assertEqual(len(report["proposals"]), 1)
        self.assertEqual(report["proposals"][0]["change_type"], "time_change")
        self.assertEqual(report["validation_errors"], [])
        self.assertEqual(report["summary"]["duplicates_suppressed"], 1)

    def test_duplicate_pair_with_different_metadata_has_one_stable_proposal(self):
        candidate = self.time_change_candidate()
        annotated = copy.deepcopy(candidate)
        annotated["source_note"] = "additional saved source evidence"
        annotated["existing_item"]["source_note"] = "existing bus evidence"
        annotated["route_search_item"]["source_note"] = "new bus evidence"
        detail = self.day_detail(
            added=[candidate["route_search_item"]],
            removed=[candidate["existing_item"]],
            time_change_candidates=[candidate],
        )
        original_report = self.build_report(detail)
        detail["time_change_candidates"].append(annotated)

        report = self.build_report(detail)

        self.assertEqual(report["proposals"], original_report["proposals"])
        self.assertEqual(report["validation_errors"], [])
        self.assertEqual(report["summary"]["duplicates_suppressed"], 1)

    def test_invalid_pair_does_not_fall_back_to_independent_add_and_remove(self):
        candidate = self.time_change_candidate()
        candidate["difference_minutes"] = 30

        report = self.build_report(
            self.day_detail(
                added=[candidate["route_search_item"]],
                removed=[candidate["existing_item"]],
                time_change_candidates=[candidate],
            )
        )

        self.assertEqual(report["proposals"], [])
        self.assert_error_code_contains(report, "difference_minutes")

    def test_missing_pair_component_does_not_emit_the_remaining_component(self):
        candidate = self.time_change_candidate()
        for missing in ("added", "removed"):
            with self.subTest(missing=missing):
                detail = self.day_detail(
                    added=[candidate["route_search_item"]],
                    removed=[candidate["existing_item"]],
                    time_change_candidates=[candidate],
                )
                detail[missing] = []

                report = self.build_report(detail)

                self.assertEqual(report["proposals"], [])
                self.assert_error_code_contains(report, "component")

    def test_pair_components_must_match_exact_line_not_only_normalized_id(self):
        candidate = self.time_change_candidate()
        for changed in ("added", "removed"):
            with self.subTest(changed=changed):
                detail = self.day_detail(
                    added=[candidate["route_search_item"]],
                    removed=[candidate["existing_item"]],
                    time_change_candidates=[candidate],
                )
                detail[changed][0]["line"] = "(33) 別経路"

                report = self.build_report(detail)

                self.assertEqual(report["proposals"], [])
                self.assert_error_code_contains(report, "component")

    def test_time_change_beyond_comparison_window_is_rejected(self):
        candidate = self.time_change_candidate(new_time="11:01")
        candidate["difference_minutes"] = 61

        report = self.build_report(
            self.day_detail(
                added=[candidate["route_search_item"]],
                removed=[candidate["existing_item"]],
                time_change_candidates=[candidate],
            )
        )

        self.assertEqual(report["proposals"], [])
        self.assertTrue(report["validation_errors"])

    def test_boolean_difference_minutes_is_not_accepted_as_integer_one(self):
        candidate = self.time_change_candidate(new_time="10:01")
        candidate["difference_minutes"] = True

        report = self.build_report(
            self.day_detail(
                added=[candidate["route_search_item"]],
                removed=[candidate["existing_item"]],
                time_change_candidates=[candidate],
            )
        )

        self.assertEqual(report["proposals"], [])
        self.assert_error_code_contains(report, "difference_minutes")

    def test_unicode_digits_do_not_pass_ascii_hh_mm_validation(self):
        for timestamp in ("1０:0０", "1١:0٢", "１０:０５"):
            with self.subTest(timestamp=timestamp):
                report = self.build_report(
                    self.day_detail(added=[self.added_candidate(time=timestamp)])
                )

                self.assertEqual(report["proposals"], [])
                self.assert_error_code_contains(report, "time")

    def test_time_change_does_not_discard_different_full_new_line(self):
        candidate = self.time_change_candidate()
        candidate["route_search_item"]["line"] = "(33) 別の経路・行先"

        report = self.build_report(
            self.day_detail(
                added=[candidate["route_search_item"]],
                removed=[candidate["existing_item"]],
                time_change_candidates=[candidate],
            )
        )

        self.assertEqual(report["proposals"], [])
        self.assert_error_code_contains(report, "line")

    def test_time_change_rejects_contradictory_embedded_route_and_day_context(self):
        for location in (None, "existing_item", "route_search_item"):
            for field, value in (
                ("route", "to_station"),
                ("direction", "to_station"),
                ("day_type", "weekend"),
            ):
                with self.subTest(location=location, field=field):
                    candidate = self.time_change_candidate()
                    target = candidate if location is None else candidate[location]
                    target[field] = value

                    report = self.build_report(
                        self.day_detail(
                            added=[candidate["route_search_item"]],
                            removed=[candidate["existing_item"]],
                            time_change_candidates=[candidate],
                        )
                    )

                    self.assertEqual(report["proposals"], [])
                    self.assertTrue(report["validation_errors"])

    def test_simulation_reports_malformed_bus_without_raising_or_mutating(self):
        for invalid_bus in (None, "not-a-bus", 3, ["10:00", "33", "C"]):
            with self.subTest(invalid_bus=invalid_bus):
                schedule = copy.deepcopy(self.schedule)
                schedule["to_uni"]["weekday"].append(invalid_bus)
                original = copy.deepcopy(schedule)

                simulation = proposal_module.simulate_proposals(schedule, [])

                self.assertEqual(simulation["status"], "failed")
                self.assertTrue(simulation["errors"])
                self.assertEqual(schedule, original)

    def test_simulation_rejects_malformed_initial_schedule_even_if_removed(self):
        schedule = copy.deepcopy(self.schedule)
        invalid_bus = {"time": "25:00", "line": "(33) 寺地", "stop": "C"}
        schedule["to_uni"]["weekday"].append(invalid_bus)
        proposal = {
            "proposal_id": "remove-invalid-input",
            "direction": "to_uni",
            "day_type": "weekday",
            "change_type": "remove",
            "changes": [{"operation": "remove", "before": invalid_bus, "after": None}],
        }

        simulation = proposal_module.simulate_proposals(schedule, [proposal])

        self.assertEqual(simulation["status"], "failed")
        self.assertTrue(simulation["errors"])

    def test_simulation_rejects_malformed_operation_or_change_type_atomically(self):
        valid_proposal = self.build_report(
            self.day_detail(added=[self.added_candidate()])
        )["proposals"][0]
        for field, value in (
            ("operation", "delete"),
            ("operation", None),
            ("operation", ["add"]),
            ("operation", {"name": "add"}),
            ("change_type", "remove"),
            ("change_type", "unknown"),
            ("change_type", None),
            ("change_type", ["add"]),
        ):
            with self.subTest(field=field, value=value):
                proposal = copy.deepcopy(valid_proposal)
                target = proposal["changes"][0] if field == "operation" else proposal
                target[field] = value
                original_schedule = copy.deepcopy(self.schedule)

                simulation = proposal_module.simulate_proposals(self.schedule, [proposal])

                self.assertEqual(simulation["status"], "failed")
                self.assertTrue(simulation["errors"])
                self.assertEqual(simulation["successful_changes"], 0)
                self.assertEqual(simulation["bus_count_delta"], 0)
                self.assertEqual(self.schedule, original_schedule)

    def test_simulation_rejects_invalid_after_bus_without_counting_or_applying_it(self):
        candidate = self.time_change_candidate()
        replace = self.build_report(self.day_detail(
            added=[candidate["route_search_item"]],
            removed=[candidate["existing_item"]],
            time_change_candidates=[candidate],
        ))["proposals"][0]
        add = self.build_report(self.day_detail(added=[self.added_candidate()]))["proposals"][0]
        original = copy.deepcopy(self.schedule)
        for valid_proposal in (add, replace):
            for field, value in (("time", "25:00"), ("line", "39"), ("stop", "Z")):
                with self.subTest(operation=valid_proposal["change_type"], field=field):
                    proposal = copy.deepcopy(valid_proposal)
                    proposal["changes"][0]["after"][field] = value

                    simulation = proposal_module.simulate_proposals(self.schedule, [proposal])

                    self.assertEqual(simulation["status"], "failed")
                    self.assertEqual(simulation["successful_changes"], 0)
                    self.assertEqual(simulation["bus_count_delta"], 0)
                    self.assertEqual(self.schedule, original)

    def test_simulation_rejects_missing_remove_target_without_mutating_schedule(self):
        proposal = self.build_report(
            self.day_detail(removed=[self.removed_bus])
        )["proposals"][0]
        proposal["changes"][0]["before"]["time"] = "12:00"
        original = copy.deepcopy(self.schedule)

        simulation = proposal_module.simulate_proposals(self.schedule, [proposal])

        self.assertEqual(simulation["status"], "failed")
        self.assertEqual(simulation["successful_changes"], 0)
        self.assertEqual(simulation["bus_count_delta"], 0)
        self.assertEqual(self.schedule, original)

    def test_review_status_never_becomes_proposal_approval_or_rejection(self):
        for status in ("confirmed", "unreviewed", "approved", "rejected", "unknown", None):
            with self.subTest(status=status):
                review = self.reviewed_entry(
                    candidate_type="added",
                    key={"time": "09:30", "line_normalized": "39", "stop": "A"},
                )
                review["status"] = status
                report = self.build_report(
                    self.day_detail(added=[self.added_candidate()]),
                    reviewed_data=self.reviewed_data(review),
                )
                proposal = report["proposals"][0]
                self.assertEqual(proposal["status"], "pending" if status == "confirmed" else "needs_review")
                self.assertEqual(proposal["review"]["candidate_status"], status or "unknown")
                self.assertFalse(report["apply_allowed"])

    def test_conflicting_review_entries_are_input_error_in_either_order(self):
        review = self.reviewed_entry(
            candidate_type="added",
            key={"time": "09:30", "line_normalized": "39", "stop": "A"},
        )
        conflicting = copy.deepcopy(review)
        conflicting["status"] = "rejected"
        for entries in ((review, conflicting), (conflicting, review)):
            with self.subTest(entries=entries), self.assertRaisesRegex(ValueError, "conflicting reviewed"):
                self.build_report(
                    self.day_detail(added=[self.added_candidate()]),
                    reviewed_data=self.reviewed_data(*entries),
                )

    def test_confirmed_time_change_crossing_neighbor_still_needs_review(self):
        candidate = self.time_change_candidate(new_time="10:10")
        candidate["difference_minutes"] = 10
        schedule = copy.deepcopy(self.schedule)
        for time in ("09:55", "10:03", "10:20"):
            schedule["to_uni"]["weekday"].append(dict(self.time_change_bus, time=time))
        review = self.reviewed_entry(
            candidate_type="time_change",
            key={"old_time": "10:00", "new_time": "10:10", "line_normalized": "33", "stop": "C"},
        )
        report = self.build_report(self.day_detail(
            added=[candidate["route_search_item"]],
            removed=[candidate["existing_item"]],
            time_change_candidates=[candidate],
        ), schedule=schedule, reviewed_data=self.reviewed_data(review))

        proposal = report["proposals"][0]
        self.assertEqual(proposal["status"], "needs_review")
        self.assertEqual(proposal["review_reasons"], ["time_change_crosses_existing_departure"])
        self.assertEqual(proposal["time_context"]["previous_bus"]["time"], "09:55")
        self.assertEqual(proposal["time_context"]["next_bus"]["time"], "10:03")
        self.assertTrue(proposal["time_context"]["crosses_existing_departure"])

    def test_cli_rejects_outputs_that_alias_any_input(self):
        comparison = self.comparison({"to_uni": {"weekday": self.day_detail()}})
        with tempfile.TemporaryDirectory() as temp_directory:
            directory = Path(temp_directory)
            paths = {
                "comparison": directory / "comparison.json",
                "schedule": directory / "schedule.json",
                "reviewed": directory / "reviewed.json",
            }
            for key, document in (
                ("comparison", comparison),
                ("schedule", self.schedule),
                ("reviewed", self.reviewed_data()),
            ):
                paths[key].write_text(json.dumps(document), encoding="utf-8")
            original = {key: path.read_bytes() for key, path in paths.items()}
            base_args = [argument for key, path in paths.items() for argument in (f"--{key}", str(path))]

            for key, output in paths.items():
                with self.subTest(input=key), patch.object(proposal_module, "GENERATED_DIR", directory):
                    for input_key, path in paths.items():
                        path.write_bytes(original[input_key])
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        result = proposal_module.main(base_args + ["--output", str(output)])

                    self.assertNotEqual(result, 0)
                    for input_key, path in paths.items():
                        self.assertEqual(path.read_bytes(), original[input_key])

    def test_cli_rejects_output_outside_generated_directory_and_non_json(self):
        comparison = self.comparison({"to_uni": {"weekday": self.day_detail()}})
        with tempfile.TemporaryDirectory() as temp_directory:
            directory = Path(temp_directory)
            generated = directory / "generated"
            comparison_path = directory / "comparison.json"
            schedule_path = directory / "schedule.json"
            comparison_path.write_text(json.dumps(comparison), encoding="utf-8")
            schedule_path.write_text(json.dumps(self.schedule), encoding="utf-8")
            base_args = [
                "--comparison", str(comparison_path),
                "--schedule", str(schedule_path),
                "--reviewed", str(directory / "absent-reviews.json"),
            ]

            for output in (
                directory / "outside.json",
                generated / ".." / "escaped.json",
                generated / "bus.db",
            ):
                with self.subTest(output=output), patch.object(proposal_module, "GENERATED_DIR", generated):
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        result = proposal_module.main(base_args + ["--output", str(output)])

                    self.assertNotEqual(result, 0)
                    self.assertFalse(output.exists())

    def test_cli_rejects_hard_link_to_schedule_inside_generated_directory(self):
        comparison = self.comparison({"to_uni": {"weekday": self.day_detail()}})
        with tempfile.TemporaryDirectory() as temp_directory:
            directory = Path(temp_directory)
            generated = directory / "generated"
            generated.mkdir()
            comparison_path = directory / "comparison.json"
            schedule_path = directory / "schedule.json"
            output_path = generated / "aliased-output.json"
            comparison_path.write_text(json.dumps(comparison), encoding="utf-8")
            schedule_path.write_text(json.dumps(self.schedule), encoding="utf-8")
            original = schedule_path.read_bytes()
            try:
                os.link(schedule_path, output_path)
            except OSError as exc:
                self.skipTest(f"hard links unavailable: {exc}")

            with patch.object(proposal_module, "GENERATED_DIR", generated):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    result = proposal_module.main([
                        "--comparison", str(comparison_path),
                        "--schedule", str(schedule_path),
                        "--reviewed", str(directory / "absent-reviews.json"),
                        "--output", str(output_path),
                    ])

            self.assertNotEqual(result, 0)
            self.assertEqual(schedule_path.read_bytes(), original)
            self.assertEqual(output_path.read_bytes(), original)

    def test_output_directory_link_cannot_overwrite_protected_schedule(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            directory = Path(temp_directory)
            generated = directory / "generated"
            generated.mkdir()
            protected = directory / "protected"
            protected.mkdir()
            schedule_path = protected / "schedule.json"
            schedule_path.write_text(json.dumps(self.schedule), encoding="utf-8")
            original = schedule_path.read_bytes()
            alias = generated / "alias"
            if os.name == "nt":
                import _winapi
                _winapi.CreateJunction(str(protected), str(alias))
            else:
                alias.symlink_to(protected, target_is_directory=True)
            output = alias / "schedule.json"

            for generated_root in (generated, alias):
                with self.subTest(generated_root=generated_root):
                    with patch.object(proposal_module, "GENERATED_DIR", generated_root):
                        with self.assertRaises(ValueError):
                            proposal_module.write_report(output, {}, [schedule_path])

            self.assertEqual(schedule_path.read_bytes(), original)
            self.assertEqual(output.read_bytes(), original)

    def test_failed_report_replace_preserves_existing_output_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            generated = Path(temp_directory)
            output = generated / "report.json"
            original = b'{"previous_report":true}\n'
            output.write_bytes(original)
            with patch.object(proposal_module, "GENERATED_DIR", generated):
                with patch.object(proposal_module.os, "replace", side_effect=OSError("replace failed")):
                    with self.assertRaises(OSError):
                        proposal_module.write_report(output, {"new_report": True}, [])

            self.assertEqual(output.read_bytes(), original)
            self.assertEqual(list(generated.iterdir()), [output])

    def test_cli_writes_only_requested_output_and_preserves_all_input_bytes(self):
        candidate = self.added_candidate()
        comparison = self.comparison(
            {"to_uni": {"weekday": self.day_detail(added=[candidate])}}
        )
        reviews = self.reviewed_data()

        with tempfile.TemporaryDirectory() as temp_directory:
            directory = Path(temp_directory)
            comparison_path = directory / "comparison.json"
            schedule_path = directory / "schedule.json"
            reviewed_path = directory / "reviewed.json"
            output_path = directory / "generated" / "update_proposals.json"
            input_documents = {
                comparison_path: comparison,
                schedule_path: self.schedule,
                reviewed_path: reviews,
            }
            for path, value in input_documents.items():
                path.write_text(
                    json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            original_bytes = {
                path: path.read_bytes()
                for path in input_documents
            }

            stdout = io.StringIO()
            with patch.object(proposal_module, "GENERATED_DIR", output_path.parent), redirect_stdout(stdout):
                return_code = proposal_module.main(
                    [
                        "--comparison",
                        str(comparison_path),
                        "--schedule",
                        str(schedule_path),
                        "--reviewed",
                        str(reviewed_path),
                        "--output",
                        str(output_path),
                    ]
                )

            self.assertEqual(return_code, 0)
            self.assertTrue(output_path.is_file())
            output = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertFalse(output["apply_allowed"])
            self.assertEqual(len(output["proposals"]), 1)
            for path, before in original_bytes.items():
                self.assertEqual(path.read_bytes(), before)
            rendered = stdout.getvalue()
            self.assertIn("Update Proposal Summary", rendered)
            self.assertIn(output["proposals"][0]["proposal_id"], rendered)


if __name__ == "__main__":
    unittest.main()
