import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from monitor import generate_update_proposals as g
from monitor import preview_update_proposals as p


class PreviewTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.generated = self.root / "generated"
        self.output = self.generated / "preview"
        self.schedule_path = self.root / "schedule.json"
        self.proposal_path = self.root / "proposals.json"
        self.db_path = self.root / "bus.db"
        self.db_path.write_bytes(b"fixture database; must never be changed")
        for module, name, value in (
            (p, "GENERATED_DIR", self.generated), (g, "PROJECT_ROOT", self.root),
            (g, "DEFAULT_SCHEDULE_PATH", self.schedule_path),
            (g, "DEFAULT_OUTPUT_PATH", self.proposal_path),
            (g, "DEFAULT_REVIEWED_PATH", self.root / "reviewed.json"),
        ):
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.removed = {"time": "08:00", "line": "(34) 久安", "stop": "A"}
        self.peer = {"time": "09:00", "line": "(39) 泉野", "stop": "A"}
        self.old_time = {"time": "10:00", "line": "(33) 寺地・四十万行", "stop": "C"}
        self.new_time = dict(self.old_time, time="10:05")
        self.added = dict(self.peer, time="09:30")
        self.schedule = {
            "to_uni": {"weekday": [self.removed.copy(), self.peer.copy(), self.old_time.copy()], "weekend": []},
            "to_station": {"weekday": [{"time": "07:00", "line": "(60) 金石", "stop": "D"}], "weekend": []},
            "to_nakahashi": {"weekday": [], "weekend": []},
        }
        self.source = self.encode_schedule(self.schedule)
        self.schedule_path.write_bytes(self.source)

    @staticmethod
    def encode_schedule(schedule):
        lines = ["{"]
        for direction, days in schedule.items():
            lines.append('  "' + direction + '": {')
            for day_type, rows in days.items():
                lines.append('    "' + day_type + '": [')
                lines.extend("      " + json.dumps(row, ensure_ascii=False) + "," for row in rows)
                if rows:
                    lines[-1] = lines[-1][:-1]
                lines.append("    ],")
            lines[-1] = lines[-1][:-1]
            lines.append("  },")
        lines[-1] = lines[-1][:-1]
        return ("\n".join(lines + ["}"]) + "\n").encode("utf-8")

    def proposal(self, kind="add", *, before=None, after=None, **overrides):
        if kind == "add":
            after = copy.deepcopy(after if after is not None else self.added)
        elif kind == "remove":
            before = copy.deepcopy(before if before is not None else self.removed)
        else:
            before = copy.deepcopy(before if before is not None else self.old_time)
            after = copy.deepcopy(after if after is not None else self.new_time)
        proposal = g.make_proposal(
            direction="to_uni", day_type="weekday", change_type=kind,
            changes=[{"operation": {"add": "add", "remove": "remove", "time_change": "replace"}[kind],
                      "before": before, "after": after}], source_path="fixture/comparison.json",
            reviewed_item={"status": "confirmed"},
        )
        proposal.update(overrides)
        proposal["proposal_id"] = p.proposal_identity(proposal)
        return proposal

    @staticmethod
    def report(proposals, errors=None):
        return {"proposal_version": 1, "apply_allowed": False, "proposals": copy.deepcopy(proposals),
                "validation_errors": errors or [], "simulation": {"status": "passed"}}

    def preview(self, proposals, *, source=None, ids=None, errors=None):
        return p.build_preview(self.report(proposals, errors),
                               ids if ids is not None else [item["proposal_id"] for item in proposals],
                               self.source if source is None else source)

    @staticmethod
    def candidate(bundle):
        return json.loads(bundle["schedule.preview.json"])

    def run_cli(self, report, ids, *, output=None):
        self.proposal_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        args = ["--proposal-file", str(self.proposal_path), "--schedule", str(self.schedule_path),
                "--output-dir", str(output or self.output)]
        for identity in ids:
            args += ["--proposal-id", identity]
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = p.main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_single_added_preview_keeps_other_rows_and_inputs(self):
        before = copy.deepcopy(self.schedule)
        bundle = self.preview([self.proposal()])
        rows = self.candidate(bundle)["to_uni"]["weekday"]
        self.assertEqual(rows, [self.removed, self.peer, self.added, self.old_time])
        self.assertEqual(self.schedule, before)

    def test_single_removed_preview(self):
        rows = self.candidate(self.preview([self.proposal("remove")]))["to_uni"]["weekday"]
        self.assertEqual(rows, [self.peer, self.old_time])

    def test_single_time_change_preview(self):
        rows = self.candidate(self.preview([self.proposal("time_change")]))["to_uni"]["weekday"]
        self.assertEqual(rows, [self.removed, self.peer, self.new_time])

    def test_multiple_proposals_apply_only_the_explicit_selection(self):
        proposals = [self.proposal(), self.proposal("remove"), self.proposal("time_change")]
        bundle = self.preview(proposals, ids=[proposals[0]["proposal_id"], proposals[2]["proposal_id"]])
        rows = self.candidate(bundle)["to_uni"]["weekday"]
        self.assertEqual(rows, [self.removed, self.peer, self.added, self.new_time])

    def test_unknown_id_is_an_explicit_error(self):
        with self.assertRaisesRegex(p.PreviewError, "unknown proposal ID"):
            self.preview([self.proposal()], ids=["0" * 64])

    def test_duplicate_selected_id_is_rejected(self):
        proposal = self.proposal()
        with self.assertRaisesRegex(p.PreviewError, "duplicate selected"):
            self.preview([proposal], ids=[proposal["proposal_id"]] * 2)

    def test_duplicate_report_ids_are_rejected_even_if_only_selected_once(self):
        proposal = self.proposal()
        with self.assertRaisesRegex(p.PreviewError, "duplicate proposal ID"):
            self.preview([proposal, proposal], ids=[proposal["proposal_id"]])

    def test_selection_is_required(self):
        with self.assertRaisesRegex(p.PreviewError, "explicit"):
            self.preview([self.proposal()], ids=[])

    def test_short_or_malformed_ids_are_rejected(self):
        for identity in ("abc123", "../schedule.json", "A" * 64, 42):
            with self.subTest(identity=identity), self.assertRaises(p.PreviewError):
                self.preview([self.proposal()], ids=[identity])

    def test_rejected_status_is_refused(self):
        with self.assertRaisesRegex(p.PreviewError, "status"):
            self.preview([self.proposal(status="rejected")])

    def test_needs_review_status_is_refused(self):
        with self.assertRaisesRegex(p.PreviewError, "status"):
            self.preview([self.proposal(status="needs_review")])

    def test_unknown_status_is_refused(self):
        with self.assertRaisesRegex(p.PreviewError, "status"):
            self.preview([self.proposal(status="ready")])

    def test_pending_and_approved_remain_preview_only_without_status_mutation(self):
        for status in ("pending", "approved"):
            proposal = self.proposal(status=status)
            original = copy.deepcopy(proposal)
            summary = json.loads(self.preview([proposal])["summary.json"])
            self.assertEqual(proposal, original)
            self.assertEqual(summary["selection_meaning"], "preview_only")
            self.assertFalse(summary["apply_allowed"])
            self.assertFalse(summary["apply_performed"])
            self.assertEqual(summary["selected_proposals"][0]["status"], status)

    def test_review_reasons_are_not_bypassed_by_changing_status(self):
        with self.assertRaisesRegex(p.PreviewError, "review_reasons"):
            self.preview([self.proposal(status="approved", review_reasons=["ambiguous"])])

    def test_stale_removed_is_refused(self):
        changed = copy.deepcopy(self.schedule)
        changed["to_uni"]["weekday"].remove(self.removed)
        with self.assertRaisesRegex(p.PreviewError, "stale/conflict"):
            self.preview([self.proposal("remove")], source=self.encode_schedule(changed))

    def test_stale_added_is_refused(self):
        changed = copy.deepcopy(self.schedule)
        changed["to_uni"]["weekday"].append(self.added)
        with self.assertRaisesRegex(p.PreviewError, "stale/conflict"):
            self.preview([self.proposal()], source=self.encode_schedule(changed))

    def test_stale_time_change_missing_old_or_existing_new_is_refused(self):
        for missing_old in (True, False):
            changed = copy.deepcopy(self.schedule)
            if missing_old:
                changed["to_uni"]["weekday"].remove(self.old_time)
            else:
                changed["to_uni"]["weekday"].append(self.new_time)
            with self.subTest(missing_old=missing_old), self.assertRaisesRegex(p.PreviewError, "stale/conflict"):
                self.preview([self.proposal("time_change")], source=self.encode_schedule(changed))

    def test_stale_added_detects_normalized_line_alias(self):
        changed = copy.deepcopy(self.schedule)
        changed["to_uni"]["weekday"].append(dict(self.added, line="(39) different destination"))
        with self.assertRaisesRegex(p.PreviewError, "stale/conflict"):
            self.preview([self.proposal()], source=self.encode_schedule(changed))

    def test_ambiguous_before_with_same_normalized_line_is_refused(self):
        changed = copy.deepcopy(self.schedule)
        changed["to_uni"]["weekday"].append(dict(self.removed, line="(34) another destination"))
        with self.assertRaisesRegex(p.PreviewError, "unambiguous"):
            self.preview([self.proposal("remove")], source=self.encode_schedule(changed))

    def test_same_bus_double_remove_is_refused(self):
        proposal = self.proposal("remove")
        with self.assertRaisesRegex(p.PreviewError, "duplicate"):
            self.preview([proposal, copy.deepcopy(proposal)])

    def test_duplicate_add_target_is_refused(self):
        proposals = [self.proposal(), self.proposal(after=dict(self.added, line="(39) other destination"))]
        with self.assertRaisesRegex(p.PreviewError, "conflicting proposals"):
            self.preview(proposals)

    def test_remove_and_time_change_conflict_is_refused(self):
        proposals = [self.proposal("remove", before=self.old_time), self.proposal("time_change")]
        with self.assertRaisesRegex(p.PreviewError, "conflicting proposals"):
            self.preview(proposals)

    def test_add_and_time_change_conflict_is_refused(self):
        with self.assertRaisesRegex(p.PreviewError, "conflicting proposals"):
            self.preview([self.proposal(after=self.new_time), self.proposal("time_change")])

    def test_time_changes_sharing_old_bus_are_refused(self):
        with self.assertRaisesRegex(p.PreviewError, "conflicting proposals"):
            self.preview([self.proposal("time_change"), self.proposal("time_change", after=dict(self.new_time, time="10:10"))])

    def test_remove_cannot_make_an_existing_add_target_available(self):
        with self.assertRaisesRegex(p.PreviewError, "stale/conflict"):
            self.preview([self.proposal("remove"), self.proposal(after=self.removed)])

    def test_invalid_direction_is_refused(self):
        with self.assertRaisesRegex(p.PreviewError, "context"):
            self.preview([self.proposal(direction="unknown")])

    def test_invalid_day_type_is_refused(self):
        with self.assertRaisesRegex(p.PreviewError, "context"):
            self.preview([self.proposal(day_type="holiday")])

    def test_invalid_times_are_refused(self):
        for time in ("24:00", "12:60", "１２:００", "7:05", None):
            with self.subTest(time=time), self.assertRaisesRegex(p.PreviewError, "invalid bus"):
                self.preview([self.proposal(after=dict(self.added, time=time))])

    def test_invalid_stop_is_refused(self):
        with self.assertRaisesRegex(p.PreviewError, "invalid bus"):
            self.preview([self.proposal(after=dict(self.added, stop="Z"))])

    def test_line_number_alone_is_not_a_schedule_line(self):
        with self.assertRaisesRegex(p.PreviewError, "invalid bus"):
            self.preview([self.proposal(after=dict(self.added, line="39"))])

    def test_time_change_must_keep_line_and_stop_and_valid_delta(self):
        for after in (dict(self.new_time, line="(34) 久安"), dict(self.new_time, stop="A"),
                      self.old_time, dict(self.new_time, time="11:01")):
            with self.subTest(after=after), self.assertRaises(p.PreviewError):
                self.preview([self.proposal("time_change", after=after)])

    def test_new_same_line_departure_crossing_is_refused(self):
        changed = copy.deepcopy(self.schedule)
        changed["to_uni"]["weekday"].append(dict(self.old_time, time="10:03"))
        with self.assertRaisesRegex(p.PreviewError, "crosses"):
            self.preview([self.proposal("time_change")], source=self.encode_schedule(changed))

    def test_tampered_proposal_id_is_rejected(self):
        proposal = self.proposal()
        proposal["changes"][0]["after"]["time"] = "09:35"
        with self.assertRaisesRegex(p.PreviewError, "ID does not match"):
            self.preview([proposal])

    def test_invalid_report_schema_is_rejected(self):
        proposal = self.proposal()
        for key, value in (("proposal_version", True), ("proposal_version", 2), ("apply_allowed", True),
                           ("validation_errors", {}), ("simulation", {"status": "failed"}), ("proposals", None)):
            report = self.report([proposal])
            report[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(p.PreviewError):
                p.build_preview(report, [proposal["proposal_id"]], self.source)

    def test_invalid_change_shape_operation_and_extra_bus_fields_are_rejected(self):
        original = self.proposal()
        variants = []
        for changes in ([], [None], [original["changes"][0]] * 2,
                        [{**original["changes"][0], "operation": "replace"}],
                        [{**original["changes"][0], "after": {**self.added, "direction": "to_station"}}]):
            proposal = copy.deepcopy(original)
            proposal["changes"] = changes
            variants.append(proposal)
        for proposal in variants:
            with self.subTest(changes=proposal["changes"]), self.assertRaises(p.PreviewError):
                self.preview([proposal])

    def test_duplicate_bus_or_invalid_baseline_cannot_be_hidden_by_removal(self):
        for bus in (self.removed.copy(), dict(self.removed, time="bad")):
            changed = copy.deepcopy(self.schedule)
            changed["to_uni"]["weekday"].append(bus)
            with self.subTest(bus=bus), self.assertRaisesRegex(p.PreviewError, "invalid schedule"):
                self.preview([self.proposal("remove")], source=self.encode_schedule(changed))

    def test_related_quarantined_candidate_cannot_be_restored(self):
        errors = [g.make_validation_error(direction="to_uni", day_type="weekday", candidate_type="added",
                                          candidate=dict(self.added, line="39"), codes=["added_line_ambiguous"])]
        with self.assertRaisesRegex(p.PreviewError, "related validation error"):
            self.preview([self.proposal()], errors=errors)

    def test_nested_quarantined_time_change_blocks_both_components(self):
        candidate = {"old_time": "10:00", "new_time": "10:05", "line": "33", "stop": "C",
                     "existing_item": self.old_time, "route_search_item": self.new_time}
        error = g.make_validation_error(direction="to_uni", day_type="weekday", candidate_type="time_change",
                                        candidate=candidate, codes=["ambiguous"])
        for proposal in (self.proposal("remove", before=self.old_time), self.proposal(after=self.new_time)):
            with self.subTest(kind=proposal["change_type"]), self.assertRaisesRegex(p.PreviewError, "related validation error"):
                self.preview([proposal], errors=[error])

    def test_quarantined_conflicting_proposal_and_day_error_are_refused(self):
        proposal = self.proposal()
        for category, candidate in (("added", proposal), ("day", {})):
            error = g.make_validation_error(direction="to_uni", day_type="weekday", candidate_type=category,
                                            candidate=candidate, codes=["conflict"])
            with self.subTest(category=category), self.assertRaisesRegex(p.PreviewError, "related validation error"):
                self.preview([proposal], errors=[error])

    def test_unrelated_validation_errors_do_not_block_selected_safe_proposal(self):
        error = g.make_validation_error(direction="to_uni", day_type="weekday", candidate_type="added",
                                        candidate={"time": "22:11", "line": "33", "stop": "C"}, codes=["ambiguous"])
        summary = json.loads(self.preview([self.proposal()], errors=[error])["summary.json"])
        self.assertEqual(summary["source_validation_error_count"], 1)
        self.assertEqual(summary["validation_errors"], 0)

    def test_candidate_json_and_summary_counts_match_simulation(self):
        bundle = self.preview([self.proposal(), self.proposal("remove"), self.proposal("time_change")])
        summary = json.loads(bundle["summary.json"])
        candidate = self.candidate(bundle)
        self.assertFalse(g.validate_schedule_structure(candidate))
        self.assertEqual(summary["before_bus_count"], 4)
        self.assertEqual(summary["after_bus_count"], 4)
        self.assertEqual(summary["by_change_type"], {"add": 1, "remove": 1, "time_change": 1, "arrival_time_change": 0})

    def test_deterministic_output_and_selection_report_order(self):
        proposals = [self.proposal(), self.proposal("remove"), self.proposal("time_change")]
        before = copy.deepcopy(proposals)
        first = self.preview(proposals)
        second = self.preview(list(reversed(proposals)))
        reordered = json.loads(json.dumps(proposals, sort_keys=True))
        self.assertEqual(first, second)
        self.assertEqual(first, self.preview(reordered))
        self.assertEqual(proposals, before)

    def test_unified_diff_has_only_changed_rows_for_compact_bus_format(self):
        bundle = self.preview([self.proposal("time_change")])
        diff = bundle["schedule.diff"].decode("utf-8")
        changed = [line for line in diff.splitlines() if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
        self.assertEqual(len(changed), 2)
        self.assertIn('"10:00"', changed[0])
        self.assertIn('"10:05"', changed[1])
        self.assertIn("@@", diff)

    def test_semantic_summary_identifies_each_change_and_full_ids(self):
        proposals = [self.proposal(), self.proposal("remove"), self.proposal("time_change")]
        text = self.preview(proposals)["summary.txt"].decode("utf-8")
        for expected in ("[追加]", "[削除]", "[時刻変更]", "to_uni / weekday", "変更前:", "変更後:",
                         "schedule.json modified: NO", "bus.db modified: NO", "apply performed: NO"):
            self.assertIn(expected, text)
        for proposal in proposals:
            self.assertIn(proposal["proposal_id"], text)

    def test_formatting_preserves_crlf_bom_keys_and_no_trailing_newline(self):
        source = b"\xef\xbb\xbf" + self.source.replace(b"\n", b"\r\n").rstrip(b"\r\n")
        output = self.preview([self.proposal("time_change")], source=source)["schedule.preview.json"]
        self.assertEqual(output, source.replace(b'"10:00"', b'"10:05"'))

    def test_pretty_multiline_objects_and_empty_array_can_be_rendered(self):
        source = (json.dumps(self.schedule, ensure_ascii=False, indent=4) + "\n").encode("utf-8")
        bundle = self.preview([self.proposal("time_change")], source=source)
        self.assertEqual(bundle["schedule.preview.json"], source.replace(b'"10:00"', b'"10:05"'))
        proposal = self.proposal(direction="to_nakahashi", day_type="weekend")
        candidate = self.candidate(self.preview([proposal]))
        self.assertEqual(candidate["to_nakahashi"]["weekend"], [self.added])

    def test_unchanged_string_escapes_and_bus_key_order_are_preserved(self):
        source = self.source.replace("泉野".encode("utf-8"), b"\\u6cc9\\u91ce")
        reordered = {"stop": "C", "line": self.old_time["line"], "time": "10:00"}
        source = source.replace(json.dumps(self.old_time, ensure_ascii=False).encode("utf-8"),
                                json.dumps(reordered, ensure_ascii=False).encode("utf-8"))
        output = self.preview([self.proposal("time_change")], source=source)["schedule.preview.json"]
        self.assertEqual(output, source.replace(b'"10:00"', b'"10:05"'))

    def test_removing_last_row_in_one_day_keeps_other_routes(self):
        station = self.schedule["to_station"]["weekday"][0]
        proposal = self.proposal("remove", before=station, direction="to_station")
        candidate = self.candidate(self.preview([proposal]))
        self.assertEqual(candidate["to_station"]["weekday"], [])
        self.assertEqual(candidate["to_uni"], self.schedule["to_uni"])

    def test_unsorted_unaffected_rows_keep_their_relative_order(self):
        schedule = copy.deepcopy(self.schedule)
        schedule["to_uni"]["weekday"].reverse()
        output = self.candidate(self.preview([self.proposal()], source=self.encode_schedule(schedule)))
        unaffected = [row for row in output["to_uni"]["weekday"] if row != self.added]
        self.assertEqual(unaffected, schedule["to_uni"]["weekday"])

    def test_one_line_without_final_newline_produces_valid_diff_markers(self):
        source = json.dumps(self.schedule, ensure_ascii=False).encode("utf-8")
        bundle = self.preview([self.proposal()], source=source)
        self.assertFalse(bundle["schedule.preview.json"].endswith(b"\n"))
        self.assertEqual(bundle["schedule.diff"].count(b"\\ No newline at end of file"), 2)

    def test_duplicate_json_keys_and_nonstandard_constants_are_refused(self):
        for data in (b'{"to_uni":{},"to_uni":{}}', b'{"to_uni":NaN}'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.preview([self.proposal()], source=data)

    def test_cli_protects_schedule_database_and_proposal_hashes(self):
        proposal = self.proposal()
        schedule_hash = hashlib.sha256(self.schedule_path.read_bytes()).hexdigest()
        db_hash = hashlib.sha256(self.db_path.read_bytes()).hexdigest()
        report = self.report([proposal])
        code, stdout, stderr = self.run_cli(report, [proposal["proposal_id"]])
        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("apply performed: NO", stdout)
        self.assertEqual(hashlib.sha256(self.schedule_path.read_bytes()).hexdigest(), schedule_hash)
        self.assertEqual(hashlib.sha256(self.db_path.read_bytes()).hexdigest(), db_hash)
        self.assertEqual(json.loads(self.proposal_path.read_bytes()), report)
        directory, = self.output.iterdir()
        self.assertEqual({file.name for file in directory.iterdir()},
                         {"schedule.preview.json", "schedule.diff", "summary.json", "summary.txt"})

    def test_validation_failure_is_atomic_and_does_not_create_output(self):
        good, invalid = self.proposal(), self.proposal("remove", before=dict(self.removed, time="08:01"))
        code, stdout, stderr = self.run_cli(self.report([good, invalid]), [good["proposal_id"], invalid["proposal_id"]])
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("stale/conflict", stderr)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.schedule_path.read_bytes(), self.source)

    def test_repeat_preview_reuses_identical_bundle_without_overwriting(self):
        proposal = self.proposal()
        report = self.report([proposal])
        self.assertEqual(self.run_cli(report, [proposal["proposal_id"]])[0], 0)
        directory, = self.output.iterdir()
        times = {}
        for file in directory.iterdir():
            os.utime(file, (1000, 1000))
            times[file.name] = file.stat().st_mtime_ns
        self.assertEqual(self.run_cli(report, [proposal["proposal_id"]])[0], 0)
        self.assertEqual(list(self.output.iterdir()), [directory])
        self.assertEqual({file.name: file.stat().st_mtime_ns for file in directory.iterdir()}, times)

    def test_existing_different_artifact_is_never_overwritten(self):
        proposal = self.proposal()
        report = self.report([proposal])
        self.assertEqual(self.run_cli(report, [proposal["proposal_id"]])[0], 0)
        directory, = self.output.iterdir()
        target = directory / "summary.txt"
        target.write_bytes(b"manually saved evidence")
        self.assertEqual(self.run_cli(report, [proposal["proposal_id"]])[0], 1)
        self.assertEqual(target.read_bytes(), b"manually saved evidence")

    def test_path_traversal_and_outside_directory_are_rejected(self):
        proposal = self.proposal()
        for output in (self.generated / ".." / "escape", self.root / "escape", self.schedule_path, self.db_path):
            with self.subTest(output=output):
                code, _, stderr = self.run_cli(self.report([proposal]), [proposal["proposal_id"]], output=output)
                self.assertEqual(code, 1)
                self.assertIn("output directory", stderr)
        self.assertEqual(self.schedule_path.read_bytes(), self.source)

    @staticmethod
    def directory_link(target, alias):
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(target), str(alias))
        else:
            alias.symlink_to(target, target_is_directory=True)

    def test_output_junction_or_symlink_to_protected_directory_is_rejected(self):
        self.generated.mkdir()
        alias = self.generated / "alias"
        self.directory_link(self.root, alias)
        try:
            proposal = self.proposal()
            code, _, _ = self.run_cli(self.report([proposal]), [proposal["proposal_id"]], output=alias)
            self.assertEqual(code, 1)
            self.assertEqual(self.schedule_path.read_bytes(), self.source)
        finally:
            if os.name == "nt":
                alias.rmdir()
            else:
                alias.unlink()

    def test_generated_root_junction_is_rejected(self):
        target = self.root / "actual"
        target.mkdir()
        self.directory_link(target, self.generated)
        try:
            proposal = self.proposal()
            self.assertEqual(self.run_cli(self.report([proposal]), [proposal["proposal_id"]])[0], 1)
            self.assertEqual(list(target.iterdir()), [])
        finally:
            if os.name == "nt":
                self.generated.rmdir()
            else:
                self.generated.unlink()

    def test_existing_artifact_hard_link_to_input_is_rejected(self):
        proposal = self.proposal()
        artifacts = self.preview([proposal])
        digest = g.stable_id({name: hashlib.sha256(data).hexdigest() for name, data in artifacts.items()})
        destination = self.output / digest
        destination.mkdir(parents=True)
        os.link(self.schedule_path, destination / "schedule.preview.json")
        with self.assertRaisesRegex(p.PreviewError, "alias"):
            p.publish_preview(artifacts, self.output, {self.schedule_path: self.source})
        self.assertEqual(self.schedule_path.read_bytes(), self.source)

    def test_existing_artifact_hard_link_to_unrelated_file_is_rejected(self):
        proposal = self.proposal()
        artifacts = self.preview([proposal])
        digest = g.stable_id({name: hashlib.sha256(data).hexdigest() for name, data in artifacts.items()})
        destination = self.output / digest
        destination.mkdir(parents=True)
        unrelated = self.root / "evidence.txt"
        unrelated.write_bytes(b"keep")
        os.link(unrelated, destination / "summary.txt")
        with self.assertRaisesRegex(p.PreviewError, "hard links"):
            p.publish_preview(artifacts, self.output, {self.schedule_path: self.source})
        self.assertEqual(unrelated.read_bytes(), b"keep")

    def test_input_change_during_preview_is_refused(self):
        artifacts = self.preview([self.proposal()])
        self.schedule_path.write_bytes(self.source + b"\n")
        with self.assertRaisesRegex(p.PreviewError, "input changed"):
            p.publish_preview(artifacts, self.output, {self.schedule_path: self.source})
        self.assertFalse(self.output.exists())

    def test_publish_failure_cleans_staging_and_exposes_no_partial_bundle(self):
        artifacts = self.preview([self.proposal()])
        with patch.object(p.os, "rename", side_effect=OSError("publish failed")):
            with self.assertRaises(OSError):
                p.publish_preview(artifacts, self.output, {self.schedule_path: self.source})
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertEqual(self.schedule_path.read_bytes(), self.source)

    def test_write_failure_cleans_staging_and_exposes_no_partial_bundle(self):
        artifacts = self.preview([self.proposal()])
        original_open = Path.open

        def fail_diff_write(path, mode="r", *args, **kwargs):
            if path.name == "schedule.diff" and mode == "xb":
                raise OSError("disk full")
            return original_open(path, mode, *args, **kwargs)

        with patch.object(Path, "open", fail_diff_write), self.assertRaises(OSError):
            p.publish_preview(artifacts, self.output, {self.schedule_path: self.source})
        self.assertEqual(list(self.output.iterdir()), [])

    def test_invalid_selection_keeps_previous_successful_preview(self):
        good = self.proposal()
        self.assertEqual(self.run_cli(self.report([good]), [good["proposal_id"]])[0], 0)
        before = {str(file): file.read_bytes() for file in self.output.rglob("*") if file.is_file()}
        self.assertEqual(self.run_cli(self.report([good]), ["0" * 64])[0], 1)
        self.assertEqual({str(file): file.read_bytes() for file in self.output.rglob("*") if file.is_file()}, before)

    def test_artifact_name_traversal_is_rejected_before_writing(self):
        artifacts = self.preview([self.proposal()])
        artifacts["../../schedule.json"] = self.source
        with self.assertRaisesRegex(p.PreviewError, "artifact bundle"):
            p.publish_preview(artifacts, self.output, {self.schedule_path: self.source})
        self.assertFalse(self.output.exists())

    def test_failed_simulation_does_not_return_partial_candidate(self):
        invalid = self.proposal("remove", before=dict(self.removed, time="08:01"))
        result = g.simulate_proposals(self.schedule, [self.proposal(), invalid], include_candidate=True)
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("candidate_schedule", result)
        self.assertNotIn(self.added, self.schedule["to_uni"]["weekday"])

    def test_simulation_default_schema_is_unchanged(self):
        proposals = [self.proposal()]
        before = copy.deepcopy(self.schedule)
        default = g.simulate_proposals(self.schedule, proposals)
        extended = g.simulate_proposals(self.schedule, proposals, include_candidate=True)
        self.assertEqual(set(extended) - set(default), {"candidate_schedule"})
        extended.pop("candidate_schedule")
        self.assertEqual(default, extended)
        self.assertEqual(self.schedule, before)

    def test_actual_generator_output_is_accepted_without_schema_adapter(self):
        candidate = dict(self.added, line="39")
        comparison = {"comparison_key": ["time", "stop"], "routes": {"to_uni": {"weekday": {
            "added": [candidate], "removed": [], "line_only": [], "time_change_candidates": []}}}}
        reviewed = {"schema_version": 1, "scope": "route_search", "reviewed": [{
            "route": "to_uni", "day_type": "weekday", "type": "added",
            "key": {"time": "09:30", "line_normalized": "39", "stop": "A"},
            "status": "confirmed", "source": "fixture"}]}
        report = g.build_update_proposals(comparison, self.schedule, reviewed, generated_at="2026-09-09T00:00:00Z")
        self.assertEqual(len(report["proposals"]), 1)
        bundle = p.build_preview(report, [report["proposals"][0]["proposal_id"]], self.source)
        self.assertIn(self.added, self.candidate(bundle)["to_uni"]["weekday"])


if __name__ == "__main__":
    unittest.main()
