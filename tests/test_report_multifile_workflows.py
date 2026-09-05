"""CPU-only checks of report denominators, receipts, and failure preservation."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/report_multifile_workflows.py"
spec = importlib.util.spec_from_file_location("multifile_report", SCRIPT)
reporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reporter)


class CharacterEncoder:
    def encode(self, text, **kwargs):
        return list(text)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def fixture(root, *, timeout=False):
    study, raw = root / "study", root / "private"
    schedule = [{"task_id": f"task{task}", "repetition": repetition, "condition": condition}
                for repetition in (1, 2) for task in range(4)
                for condition in (("baseline", "mcp") if (task + repetition) % 2 else ("mcp", "baseline"))]
    save(study / "frozen-config.json", {"schedule": schedule, "model": "gpt-6.1-sol",
         "common_config": {"model_reasoning_effort": "ultra"}, "work_root": str(raw)})
    source = "def probe():\n    return '\u2028'\n"
    content_hash = hashlib.sha256(source.encode()).hexdigest()
    compact = json.dumps({"query": "probe", "results": [{"path": "src/probe.py", "symbol": "probe", "start_line": 1,
       "end_line": 2, "source": source, "source_hash": content_hash, "truncated": False, "origin": "retrieval", "related_to": None}],
       "token_usage": {"encoding": "cl100k_base", "tokens": 120, "budget": 512, "truncated": False}}, separators=(",", ":"))
    snapshot = {"files": {"src/probe.py": content_hash}, "git_revision": "frozen", "manifest_sha256": "manifest"}
    records = []
    for number, slot in enumerate(schedule, 1):
        name = f"{number:02d}-r{slot['repetition']}-{slot['task_id']}-{slot['condition']}"
        directory = study / "runs" / name
        directory.mkdir(parents=True)
        failed = timeout and slot == {"task_id": "task0", "repetition": 1, "condition": "baseline"}
        mcp = slot["condition"] == "mcp"
        usage = {"input_tokens": 80 if mcp else 100, "cached_input_tokens": 10 if mcp else 40,
                 "cache_write_input_tokens": 5, "output_tokens": 20, "reasoning_output_tokens": 10}
        events = []
        if mcp:
            events += [{"type": "item.started", "item": {"id": "mcp1", "type": "mcp_tool_call"}},
                {"type": "item.completed", "item": {"id": "mcp1", "type": "mcp_tool_call", "status": "completed",
                 "server": "semantic_search", "tool": "search_code", "arguments": {"query": "probe", "token_budget": 512},
                 "result": {"content": [{"type": "text", "text": compact}]}}}]
        events += [{"type": "item.started", "item": {"id": "shell1", "type": "command_execution"}},
                   {"type": "item.completed", "item": {"id": "shell1", "type": "command_execution", "command": "cat src/probe.py",
                    "status": "completed", "exit_code": 0, "aggregated_output": source}}]
        if not failed:
            events.append({"type": "turn.completed", "usage": usage})
        (directory / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in events))
        raw_directory = raw / "raw" / name
        raw_directory.mkdir(parents=True)
        (raw_directory / "events.jsonl").write_text(json.dumps({"type": "item.completed", "item": {
            "type": "reasoning", "text": "PRIVATE_REASONING_MUST_NOT_APPEAR"}}) + "\n" + (directory / "events.jsonl").read_text())
        if mcp:
            receipt = {"number": 1, "query": "probe", "settings": {"token_budget": 512}, "success": True,
                "response": {"results": json.loads(compact)["results"]}, "snapshots": {"before": snapshot, "after": snapshot},
                "returned_source_checks": [{"id": "probe", "source_matches_snapshot": True, "physical_bounds_match": True, "source_hash_matches": True}],
                "compact_text_sha256": hashlib.sha256(compact.encode()).hexdigest(), "backend_call_s": .5,
                "snapshot_before_s": .1, "snapshot_after_s": .2,
                "start": {"monotonic_s": 14}, "end": {"monotonic_s": 14.8}}
            (directory / "http-receipts.jsonl").write_text(json.dumps(receipt) + "\n")
        summary = dict.fromkeys(reporter.COUNT_FIELDS, 0)
        summary["passed"] = 2
        stamp = lambda n: {"monotonic_s": n}
        phases = {"fixture_start": stamp(10), "fixture_end": stamp(12), "shutdown_start": stamp(21),
                  "shutdown_end": stamp(22), "grading_start": stamp(23), "grading_end": stamp(26), "trial_end": stamp(27)}
        if mcp:
            phases.update(service_setup_start=stamp(12), service_setup_end=stamp(14))
        full_usage = {**usage, "total_tokens": usage["input_tokens"] + usage["output_tokens"],
                      "uncached_input_tokens": usage["input_tokens"] - usage["cached_input_tokens"]} if not failed else None
        record = {**slot, "run_id": name, "usage": full_usage, "completed": not failed, "failed": False,
                  "timed_out": failed, "agent_exit_code": 0 if not failed else -15, "agent_elapsed_s": 3 if mcp else 5,
                  "correct": not failed, "completed_correct": not failed, "reported_turns": 0 if failed else 1,
                  "mcp_calls": int(mcp), "command_calls": 1, "base_tree_sha256": "same-fixture", "prompt_sha256": "same-prompt",
                  "phase_timestamps": phases, "tool_intervals": [{"observed_duration_s": .000021}],
                  "grade": {"passed": not failed, "exit_code": 0 if not failed else 1,
                            "detail": {"tests_run": 2, "failures": int(failed), "errors": 0, "skipped": 0, "passed": not failed},
                            "start": stamp(23), "end": stamp(24)},
                  "compatibility": {"passed": True, "commands": [{"passed": True, "exit_code": 0,
                    "expected_summary": summary, "actual_summary": summary, "start": stamp(24), "end": stamp(25)}],
                    "changed_or_deleted_original_tests": [], "added_configuration_removed_for_grading": [],
                    "original_test_and_config_hashes": {"tests/test_original.py": "original"}, "test_edits_require_review": False},
                  "changed_files": ["src/probe.py", "tests/test_new_feature.py"],
                  "artifact_sha256": {"published": {"events.jsonl": reporter.digest(directory / "events.jsonl")}}}
        save(directory / "result.json", record)
        records.append(record)
    save(study / "results.json", records)
    expected_counts = dict.fromkeys(reporter.COUNT_FIELDS, 0)
    expected_counts["passed"] = 2
    save(study / "tasks.json", {"tasks": [{"id": f"task{task}", "source_root": "src/probe",
        "seed_validation": {"reference": {"tests_run": 2, "failures": 0, "errors": 0, "skipped": 0, "passed": True}},
        "compatibility_commands": [{"expected_summary": expected_counts}],
        "upstream_test_sha256": {"tests/test_original.py": "original"}} for task in range(4)]})
    config = reporter.read_json(study / "frozen-config.json")
    config["manifest_sha256"] = reporter.digest(study / "tasks.json")
    save(study / "frozen-config.json", config)
    return study, raw


class MultifileReportTests(unittest.TestCase):
    def test_complete_inventory_keeps_token_subsets_separate_and_all_pairs(self):
        with tempfile.TemporaryDirectory() as temporary:
            study, raw = fixture(Path(temporary))
            result = reporter.build_report(study, encoder=CharacterEncoder())
            self.assertEqual((result["finalized_attempts"], len(result["pairs"])), (16, 8))
            baseline, mcp = (result["conditions"][condition]["usage"] for condition in reporter.CONDITIONS)
            self.assertEqual(baseline["total_tokens"]["complete_total"], 8 * 120)
            self.assertEqual(mcp["total_tokens"]["complete_total"], 8 * 100)
            self.assertEqual(mcp["uncached_input_tokens"]["complete_total"], 8 * 70)
            comparisons = result["all_attempt_pair_comparisons"]["metrics"]
            self.assertLess(comparisons["total_tokens"]["measured_sums_change_pct"], 0)
            self.assertGreater(comparisons["uncached_input_tokens"]["measured_sums_change_pct"], 0)
            self.assertEqual(result["correctness_qualified_pair_comparisons"]["declared_pairs"], 8)
            self.assertNotIn("PRIVATE_REASONING", json.dumps(result))
            self.assertNotIn(str(raw), json.dumps(result))

    def test_timeout_stays_in_all_attempts_and_does_not_become_zero_cost(self):
        with tempfile.TemporaryDirectory() as temporary:
            study, _ = fixture(Path(temporary), timeout=True)
            result = reporter.build_report(study, encoder=CharacterEncoder())
            baseline = result["conditions"]["baseline"]
            self.assertEqual(baseline["statuses"]["timeout"], 1)
            self.assertEqual(baseline["usage"]["total_tokens"]["observed"], 7)
            self.assertEqual(baseline["usage"]["total_tokens"]["measured_sum"], 7 * 120)
            self.assertIsNone(baseline["usage"]["total_tokens"]["complete_total"])
            self.assertEqual(result["all_attempt_pair_comparisons"]["declared_pairs"], 8)
            self.assertEqual(result["all_attempt_pair_comparisons"]["metrics"]["total_tokens"]["observed_pairs"], 7)
            self.assertEqual(result["correctness_qualified_pair_comparisons"]["declared_pairs"], 7)
            self.assertIn("A timeout can consume unreported", reporter.render_markdown(result))

    def test_partial_inventory_requires_explicit_flag_and_shows_every_missing_slot(self):
        with tempfile.TemporaryDirectory() as temporary:
            study, _ = fixture(Path(temporary))
            records = reporter.read_json(study / "results.json")[:2]
            save(study / "results.json", records)
            for directory in (study / "runs").iterdir():
                if directory.name not in {row["run_id"] for row in records}:
                    (directory / "result.json").unlink()
            with self.assertRaisesRegex(ValueError, "All 16"):
                reporter.build_report(study)
            result = reporter.build_report(study, allow_partial=True)
            self.assertEqual(len(result["runs"]), 16)
            self.assertEqual(len(result["pairs"]), 8)
            self.assertEqual(sum(row["status"] == "not_recorded" for row in result["runs"]), 14)
            self.assertEqual(result["conditions"]["baseline"]["usage"]["total_tokens"]["missing"], 7)

    def test_duplicate_retry_is_rejected_instead_of_averaged(self):
        with tempfile.TemporaryDirectory() as temporary:
            study, _ = fixture(Path(temporary))
            records = reporter.read_json(study / "results.json")
            save(study / "results.json", records + [records[0]])
            with self.assertRaisesRegex(ValueError, "duplicate"):
                reporter.build_report(study)

    def test_http_payloads_hash_bounds_unicode_and_rereads_are_independently_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            study, _ = fixture(Path(temporary))
            result = reporter.build_report(study, encoder=CharacterEncoder())
            mcp = next(row for row in result["runs"] if row["condition"] == "mcp")
            self.assertTrue(mcp["http"]["raw_link_validation_available"])
            self.assertEqual(mcp["http"]["calls"][0]["raw_payload_matching_mcp_items"], ["mcp1"])
            source = mcp["tools"]["mcp_calls"][0]["results"][0]
            self.assertTrue(source["delivered_hash_matches"])
            self.assertTrue(source["physical_bounds_match"])
            self.assertEqual(source["exact_full_excerpt_seen_in_later_stdout"], ["shell1"])
            self.assertEqual(mcp["event_observer_spans_s"], [.000021])
            self.assertEqual(mcp["http"]["calls"][0]["backend_call_s"], .5)
            self.assertNotIn("thinking", " ".join(mcp["timing_s"]))
            self.assertIn("batch/queue", mcp["event_observer_warning"])

    def test_usage_tampering_and_original_test_edits_are_explicit_flags(self):
        with tempfile.TemporaryDirectory() as temporary:
            study, _ = fixture(Path(temporary))
            records = reporter.read_json(study / "results.json")
            records[0]["usage"]["total_tokens"] += 9
            records[0]["compatibility"]["changed_or_deleted_original_tests"] = [{"path": "tests/test_original.py", "before": "a", "after": "b"}]
            save(study / "results.json", records)
            result = reporter.build_report(study)
            row = result["runs"][0]
            self.assertIn("Completed-turn usage and result metadata differ", row["flags"])
            self.assertEqual(len(row["test_integrity"]["changed_or_deleted_original_tests_and_configs"]), 1)
            self.assertEqual(row["usage"]["total_tokens"], 120)

    def test_no_tokenizer_marks_proxies_unavailable_but_counts_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            study, _ = fixture(Path(temporary))
            result = reporter.build_report(study)
            self.assertIsNone(result["runs"][0]["tools"]["total_output_cl100k_token_proxy"])
            self.assertGreater(result["runs"][0]["tools"]["total_output_utf8_bytes"], 0)

    def test_recorded_success_cannot_override_frozen_outcomes_or_counts(self):
        with tempfile.TemporaryDirectory() as temporary:
            study, _ = fixture(Path(temporary))
            records = reporter.read_json(study / "results.json")
            records[0]["grade"]["detail"]["skipped"] = 2
            records[0]["compatibility"]["commands"][0]["expected_summary"]["passed"] = 1
            records[0]["compatibility"]["commands"][0]["actual_summary"]["passed"] = 1
            save(study / "results.json", records)
            result = reporter.build_report(study)
            row = result["runs"][0]
            self.assertTrue(row["recorded_completed_correct"])
            self.assertFalse(row["completed_correct"])
            self.assertFalse(row["checks"]["selected"]["matches_frozen_reference"])
            self.assertFalse(row["checks"]["upstream_commands"][0]["actual_matches_frozen"])
            self.assertEqual(result["correctness_qualified_pair_comparisons"]["declared_pairs"], 7)

    def test_test_fixture_query_scope_is_reported_as_a_clue_not_a_causal_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            study, _ = fixture(Path(temporary))
            directory = next(path for path in (study / "runs").iterdir() if path.name.endswith("-mcp"))
            events = reporter.read_jsonl(directory / "events.jsonl")
            next(row["item"] for row in events if row.get("type") == "item.completed" and row.get("item",{}).get("type") == "mcp_tool_call")["arguments"]["query"] = "tests pytest fixtures"
            (directory / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in events))
            result = reporter.build_report(study)
            call = next(row for row in result["runs"] if row["run_id"] == directory.name)["tools"]["mcp_calls"][0]
            self.assertIn("heuristic", call["query_scope_clue"])
            self.assertIn("not proof", reporter.render_markdown(result))

    def test_nested_containment_and_later_same_path_context_are_descriptive_only(self):
        parent = "def parent():\n    def nested():\n        return 1\n    return nested()\n"
        child = "    def nested():\n        return 1\n"
        def excerpt(symbol, start, end, source):
            return {"path": "src/module.py", "symbol": symbol, "start_line": start, "end_line": end,
                    "source": source, "source_hash": hashlib.sha256(source.encode()).hexdigest()}
        def event(identifier, rows):
            return {"type": "item.completed", "item": {"type": "mcp_tool_call", "id": identifier,
                "server": "semantic_search", "tool": "search_code", "result": {"content": [{"type": "text",
                "text": json.dumps({"results": rows})}]}}}
        events = [event("first", [excerpt("parent", 1, 4, parent), excerpt("parent.nested", 2, 3, child)]),
                  event("later", [excerpt("parent", 7, 10, parent)])]
        result = reporter.event_tools(events, CharacterEncoder())
        self.assertEqual(result["same_call_source_containment_pairs"], 1)
        first = result["mcp_calls"][0]
        self.assertEqual(first["same_call_fully_contained_source"][0]["contained_symbol"], "parent.nested")
        repetition = first["results"][0]["exact_excerpt_present_in_later_mcp_results"]
        self.assertEqual(repetition[0]["item_id"], "later")
        self.assertFalse(repetition[0]["same_line_bounds"])
        self.assertEqual(result["source_excerpts_repeated_in_later_mcp"], 2)

    def test_unexpected_mcp_calls_are_not_counted_as_search_code_uptake(self):
        events = [{"type": "item.completed", "item": {"type": "mcp_tool_call", "id": "other",
            "server": "other_server", "tool": "read_file", "result": {"content": [{"type": "text", "text": "other data"}]}}}]
        result = reporter.event_tools(events, CharacterEncoder())
        self.assertEqual(result["completed_mcp_items"], 1)
        self.assertEqual(result["completed_search_code_items"], 0)
        self.assertEqual(result["phase_output_proxies"]["other_mcp"]["completed_calls"], 1)
        self.assertNotIn("mcp_search", result["phase_output_proxies"])

    def test_cli_normalized_null_field_is_not_a_structured_payload(self):
        event = {"type": "item.completed", "item": {"type": "mcp_tool_call", "id": "search", "server": "semantic_search",
            "tool": "search_code", "result": {"structured_content": None, "content": [{"type": "text", "text": "{}"}]}}}
        call = reporter.event_tools([event], CharacterEncoder())["mcp_calls"][0]
        self.assertTrue(call["structured_field_present"])
        self.assertFalse(call["structured_payload_present"])

    def test_normal_failed_compatibility_is_an_outcome_not_integrity_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            study, _ = fixture(Path(temporary))
            records = reporter.read_json(study / "results.json")
            record = records[0]
            record["compatibility"]["commands"][0]["actual_summary"] = {**record["compatibility"]["commands"][0]["actual_summary"], "passed": 1, "failed": 1}
            record["compatibility"]["commands"][0].update(passed=False, exit_code=1)
            record["compatibility"]["passed"] = False
            record.update(correct=False, completed_correct=False)
            save(study / "results.json", records)
            row = reporter.build_report(study)["runs"][0]
            self.assertEqual(row["status"], "incorrect_or_incomplete")
            self.assertEqual(row["flags"], [])

    def test_capacity_failure_keeps_failed_attempt_and_missing_usage(self):
        with tempfile.TemporaryDirectory() as temporary:
            study, _ = fixture(Path(temporary), timeout=True)
            records = reporter.read_json(study / "results.json")
            record = next(row for row in records if row["timed_out"])
            record.update(timed_out=False, failed=True, agent_exit_code=1, changed_files=[])
            directory = study / "runs" / record["run_id"]
            message = "Selected model is at capacity. Please try a different model."
            events = [{"type": "error", "message": message}, {"type": "turn.failed", "error": {"message": message}}]
            (directory / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
            record.update(command_calls=0, mcp_calls=0)
            record["artifact_sha256"]["published"]["events.jsonl"] = reporter.digest(directory / "events.jsonl")
            save(study / "results.json", records)
            result = reporter.build_report(study)
            row = next(row for row in result["runs"] if row.get("run_id") == record["run_id"])
            self.assertEqual(row["status"], "provider_capacity_failure")
            self.assertEqual(row["failure_messages"], [message])
            self.assertIsNone(row["usage"])
            self.assertFalse(row["completed_correct"])
            self.assertEqual(result["scheduled_attempts"], 16)
            self.assertEqual(len(result["pairs"]), 8)

    def test_supplemental_defects_do_not_rewrite_frozen_pair_qualifications(self):
        with tempfile.TemporaryDirectory() as temporary:
            study, _ = fixture(Path(temporary))
            record = reporter.read_json(study / "results.json")[0]
            save(study / "patch-review.json", {"runs": [{"run_id": record["run_id"], "summary": "A later probe found a defect.",
                "issues": [{"classification": "Confirmed supplemental contract defect", "case": "terminal_read", "shared_reference_failure": True}]}]})
            result = reporter.build_report(study)
            self.assertEqual(result["confirmed_supplemental_defects"]["affected_formally_completed_correct_attempts"], 1)
            self.assertEqual(result["confirmed_supplemental_defects"]["shared_reference_issue_instances"], 1)
            self.assertTrue(all(pair["correctness_qualified"] for pair in result["pairs"]))
            self.assertIn("Frozen green gates", reporter.render_markdown(result))


if __name__ == "__main__":
    unittest.main()
