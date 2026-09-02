"""CPU-only bookkeeping contracts for paired, provider-metered coding trials."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import tomllib
import unittest

from semantic_search import workflows


PROJECT = Path(__file__).resolve().parents[1]


PROVIDER_USAGE = {
    "input_tokens": 1000, "cached_input_tokens": 700,
    "cache_write_input_tokens": 100, "output_tokens": 200,
    "reasoning_output_tokens": 150,
}


class WorkflowUsageTests(unittest.TestCase):
    def test_provider_usage_keeps_subcategories_without_double_counting(self):
        original = copy.deepcopy(PROVIDER_USAGE)
        summary = workflows.usage_summary(PROVIDER_USAGE)
        self.assertEqual(summary["total_tokens"], 1200)
        self.assertEqual(summary["uncached_input_tokens"], 300)
        for key, value in PROVIDER_USAGE.items():
            self.assertEqual(summary[key], value)
        self.assertEqual(PROVIDER_USAGE, original)

    def test_unavailable_usage_and_unavailable_subfields_remain_null(self):
        self.assertIsNone(workflows.usage_summary(None))
        summary = workflows.usage_summary({"input_tokens": 10})
        self.assertEqual(summary["input_tokens"], 10)
        for field in ("cached_input_tokens", "cache_write_input_tokens", "output_tokens",
                      "reasoning_output_tokens", "total_tokens", "uncached_input_tokens"):
            self.assertIsNone(summary[field])
        zero = workflows.usage_summary({key: 0 for key in workflows.USAGE_FIELDS})
        self.assertEqual(zero["total_tokens"], 0)
        self.assertEqual(zero["uncached_input_tokens"], 0)

    def test_invalid_provider_counts_are_rejected(self):
        for field in workflows.USAGE_FIELDS:
            for invalid in (-1, True, 1.5, "200"):
                with self.subTest(field=field, invalid=invalid), self.assertRaises(ValueError):
                    workflows.usage_summary({**PROVIDER_USAGE, field: invalid})
        with self.assertRaises(ValueError):
            workflows.usage_summary({**PROVIDER_USAGE, "cached_input_tokens": 1001})

    def test_completed_turn_uses_reported_usage_without_local_token_estimates(self):
        events = [{"type": "thread.started", "thread_id": "test"}, {"type": "turn.started"},
                  {"type": "item.completed", "item": {"id": "answer", "type": "agent_message",
                                                         "text": "A long answer does not set usage."}},
                  {"type": "turn.completed", "usage": PROVIDER_USAGE}]
        parsed = workflows.parse_events(events)
        self.assertTrue(parsed["completed"])
        self.assertFalse(parsed["failed"])
        self.assertEqual(parsed["reported_turns"], 1)
        self.assertEqual(parsed["usage"], workflows.usage_summary(PROVIDER_USAGE))
        self.assertEqual(parsed["mcp_calls"], 0)

    def test_completed_turn_with_no_reported_usage_is_not_zero_usage(self):
        parsed = workflows.parse_events([{"type": "turn.completed"}])
        self.assertTrue(parsed["completed"])
        self.assertIsNone(parsed["usage"])
        self.assertEqual(parsed["reported_turns"], 0)

    def test_multiple_reported_turns_sum_each_category_and_preserve_missing_fields(self):
        first = {**PROVIDER_USAGE}
        second = {"input_tokens": 50, "cached_input_tokens": 20, "output_tokens": 10}
        parsed = workflows.parse_events([{"type": "turn.completed", "usage": first},
                                         {"type": "turn.completed", "usage": second}])
        self.assertEqual(parsed["reported_turns"], 2)
        self.assertEqual(parsed["usage"]["input_tokens"], 1050)
        self.assertEqual(parsed["usage"]["cached_input_tokens"], 720)
        self.assertEqual(parsed["usage"]["output_tokens"], 210)
        self.assertEqual(parsed["usage"]["total_tokens"], 1260)
        self.assertIsNone(parsed["usage"]["reasoning_output_tokens"])
        self.assertIsNone(parsed["usage"]["cache_write_input_tokens"])

    def test_completed_tool_calls_count_once_and_failed_invocations_are_retained(self):
        call = {"id": "mcp-1", "type": "mcp_tool_call", "server": "semantic_search",
                "tool": "search_code", "arguments": {"query": "callback order"},
                "status": "completed", "result": {"content": []}}
        failed = {"id": "mcp-2", "type": "mcp_tool_call", "server": "semantic_search",
                  "tool": "search_code", "status": "failed", "error": "Backend unavailable"}
        command = {"id": "command-1", "type": "command_execution", "command": "rg callback",
                   "status": "completed", "exit_code": 0}
        events = [{"type": "item.started", "item": call},
                  {"type": "item.completed", "item": call},
                  {"type": "item.completed", "item": call},
                  {"type": "item.completed", "item": failed},
                  {"type": "item.started", "item": command},
                  {"type": "item.completed", "item": command},
                  {"type": "item.completed", "item": command}]
        parsed = workflows.parse_events(events)
        self.assertEqual(parsed["mcp_calls"], 2)
        self.assertEqual(parsed["command_calls"], 1)
        self.assertEqual(parsed["mcp_items"], [call, failed])
        self.assertFalse(parsed["completed"])

    def test_cli_warning_item_does_not_make_a_completed_turn_fail(self):
        warning = {"type": "item.completed", "item": {"id": "warning", "type": "error",
                   "message": "Under-development features enabled: skip_host_skill_discovery"}}
        parsed = workflows.parse_events([warning, {"type": "turn.completed", "usage": PROVIDER_USAGE}])
        self.assertTrue(parsed["completed"])
        self.assertFalse(parsed["failed"])
        self.assertEqual(parsed["errors"], [])

    def test_turn_failure_remains_failure_even_if_other_turn_completed(self):
        failure = {"type": "turn.failed", "error": {"message": "Provider disconnected"}}
        parsed = workflows.parse_events([{"type": "turn.completed", "usage": PROVIDER_USAGE}, failure])
        self.assertTrue(parsed["failed"])
        self.assertEqual(parsed["errors"], [failure])


class WorkflowManifestTests(unittest.TestCase):
    def test_checked_in_task_artifacts_match_their_frozen_digests(self):
        manifest = json.loads((PROJECT / "benchmarks/coding_workflows.json").read_text())
        self.assertEqual(workflows.validate_manifest(manifest, root=PROJECT), manifest)

    def fixture(self, directory):
        files = {"seed.patch": b"seed data\n", "check.py": b"oracle data\n",
                 "common.py": b"helper data\n"}
        for name, contents in files.items():
            (directory / name).write_bytes(contents)
        task = {"id": "test", "preparation_patch": "seed.patch",
                "preparation_patch_sha256": hashlib.sha256(files["seed.patch"]).hexdigest(),
                "verification": {"script": "check.py", "sha256": hashlib.sha256(files["check.py"]).hexdigest(),
                                 "shared_helper": "common.py",
                                 "shared_helper_sha256": hashlib.sha256(files["common.py"]).hexdigest()}}
        return {"tasks": [task]}

    def test_frozen_seed_oracle_and_shared_helper_are_all_required(self):
        for name in ("seed.patch", "check.py", "common.py"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                manifest = self.fixture(directory)
                self.assertEqual(workflows.validate_manifest(manifest, root=directory), manifest)
                (directory / name).write_bytes(b"changed after freeze\n")
                with self.assertRaises(ValueError):
                    workflows.validate_manifest(manifest, root=directory)

    def test_duplicate_and_empty_task_lists_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = self.fixture(directory)
            manifest["tasks"].append(copy.deepcopy(manifest["tasks"][0]))
            with self.assertRaises(ValueError):
                workflows.validate_manifest(manifest, root=directory)
            with self.assertRaises(ValueError):
                workflows.validate_manifest({"tasks": []}, root=directory)

    def test_evaluator_artifacts_cannot_escape_declared_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            root = directory / "inside"
            root.mkdir()
            manifest = self.fixture(root)
            outside = directory / "outside.patch"
            outside.write_bytes(b"outside\n")
            manifest["tasks"][0].update(preparation_patch="../outside.patch",
                                       preparation_patch_sha256=workflows.digest(outside))
            with self.assertRaises(ValueError):
                workflows.validate_manifest(manifest, root=root)


class WorkflowCommandTests(unittest.TestCase):
    def test_conditions_share_model_prompt_and_config_except_declared_mcp_server(self):
        config = {"cli_binary": "/bin/codex", "model": "gpt-6.1-sol",
                  "python": "/tmp/trial environment/bin/python", "common_config": workflows.COMMON_CONFIG}
        prompt = "Exact common task prompt."
        baseline = workflows.cli_command(config, Path("/tmp/checkout"), Path("/tmp/final"), prompt)
        mcp = workflows.cli_command(config, Path("/tmp/checkout"), Path("/tmp/final"), prompt,
                                    service_url="http://127.0.0.1:8767")
        self.assertEqual(baseline[-1], prompt)
        self.assertEqual(mcp[-1], prompt)
        for command in (baseline, mcp):
            for flag in ("--no-daemon", "--ignore-user-config", "--ignore-rules", "--strict-config",
                         "--ephemeral", "--json"):
                self.assertIn(flag, command)
            self.assertEqual(command[command.index("-m") + 1], "gpt-6.1-sol")
        def settings(command):
            return dict(command[index + 1].split("=", 1) for index, value in enumerate(command) if value == "-c")
        plain, enhanced = settings(baseline), settings(mcp)
        self.assertEqual(plain, {key: value for key, value in enhanced.items()
                                 if not key.startswith("mcp_servers.")})
        parsed = tomllib.loads("\n".join(f"{key}={value}" for key, value in enhanced.items()))
        server = parsed["mcp_servers"]["semantic_search"]
        self.assertEqual(server["enabled_tools"], ["search_code"])
        self.assertTrue(server["required"])
        self.assertEqual(server["command"], config["python"])
        self.assertEqual(server["args"], ["-m", "semantic_search.mcp_server", "--server", "http://127.0.0.1:8767"])
        self.assertEqual(parsed["model_reasoning_effort"], "ultra")
        self.assertEqual(parsed["service_tier"], "default")
        self.assertEqual(parsed["web_search"], "disabled")
        self.assertFalse(parsed["skills"]["bundled"]["enabled"])
        self.assertFalse(parsed["skills"]["include_instructions"])
        self.assertTrue(parsed["features"]["skip_host_skill_discovery"])
        self.assertTrue(all(not value for key, value in parsed["features"].items()
                            if key != "skip_host_skill_discovery"))


class WorkflowSchedulingTests(unittest.TestCase):
    def test_every_task_runs_in_both_conditions_with_balanced_first_condition(self):
        tasks = [{"id": str(index), "prompt": f"Fix regression {index}."} for index in range(6)]
        original = copy.deepcopy(tasks)
        schedule = workflows.paired_schedule(tasks)
        self.assertEqual(len(schedule), 12)
        first_conditions = []
        conditions = None
        for index, task in enumerate(tasks):
            first, second = schedule[2 * index:2 * index + 2]
            self.assertEqual(first[0], task)
            self.assertEqual(second[0], task)
            self.assertNotEqual(first[1], second[1])
            pair = {first[1], second[1]}
            conditions = pair if conditions is None else conditions
            self.assertEqual(pair, conditions)
            first_conditions.append(first[1])
        self.assertEqual(first_conditions[::2], [first_conditions[0]] * 3)
        self.assertEqual(first_conditions[1::2], [first_conditions[1]] * 3)
        self.assertNotEqual(first_conditions[0], first_conditions[1])
        self.assertEqual(tasks, original)
        self.assertEqual(workflows.paired_schedule([]), [])

    def test_common_prompt_is_deterministic_and_excludes_evaluator_answers(self):
        task = {
            "id": "fixture",
            "prompt": "Correct callback registration order and add a regression test.",
            "verification": {"script": "SECRET_HIDDEN_CHECKER.py"},
            "reference_patch_sha256": "SECRET_REFERENCE_DIGEST",
            "preparation_patch": "SECRET_SEED_PATCH.patch",
        }
        python = "/tmp/trial environment/bin/python"
        original = copy.deepcopy(task)
        first = workflows.common_prompt(task, python)
        self.assertEqual(first, workflows.common_prompt(task, python))
        self.assertIn(task["prompt"], first)
        self.assertIn(python, first)
        for hidden in ("SECRET_HIDDEN_CHECKER", "SECRET_REFERENCE_DIGEST", "SECRET_SEED_PATCH"):
            self.assertNotIn(hidden, first)
        self.assertEqual(task, original)


if __name__ == "__main__":
    unittest.main()
