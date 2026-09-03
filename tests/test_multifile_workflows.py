"""CPU-only checks of the new study's independent grading and observation paths."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import threading
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_multifile_workflows.py"
spec = importlib.util.spec_from_file_location("multifile_runner", SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class MultifileRunnerTests(unittest.TestCase):
    def test_schedule_has_eight_pairs_and_reverses_every_repetition(self):
        tasks = [{"id": f"task{i}"} for i in range(4)]
        trials = runner.schedule(tasks)
        self.assertEqual(len(trials), 16)
        firsts = []
        for task in tasks:
            one = [t["condition"] for t in trials if t["task_id"] == task["id"] and t["repetition"] == 1]
            two = [t["condition"] for t in trials if t["task_id"] == task["id"] and t["repetition"] == 2]
            self.assertEqual(two, list(reversed(one)))
            firsts.extend([one[0], two[0]])
        self.assertEqual(firsts.count("baseline"), 4)
        self.assertEqual(firsts.count("mcp"), 4)

    def test_pytest_outcome_counts_are_exact_and_do_not_count_warnings_as_tests(self):
        counts = runner.pytest_counts("progress\n247 passed, 13 skipped, 6 warnings in 0.21s\n")
        self.assertEqual(counts, {"passed": 247, "skipped": 13, "failed": 0,
                                 "errors": 0, "xfailed": 0, "xpassed": 0, "deselected": 0})
        failed = runner.pytest_counts("1 failed, 2 passed, 1 error, 3 xfailed, 1 xpassed, 4 deselected in 1s")
        self.assertEqual(failed["errors"], 1)
        self.assertEqual(failed["xfailed"], 3)
        self.assertIsNone(runner.pytest_counts("....... [100%]"))

    def test_common_model_prompt_and_normal_tools_are_identical_except_registered_mcp(self):
        config = {"cli_binary": "codex", "python": sys.executable, "model": "gpt-6.1-sol",
                  "common_config": runner.COMMON_CONFIG}
        workspace = Path("/tmp/workflow-fixture")
        user_prompt = runner.prompt({"prompt": "Implement a feature."}, sys.executable)
        commands = [runner.cli_command(config, workspace, workspace / "answer.txt", user_prompt,
                                       service_url=url, source_root="src") for url in (None, "http://127.0.0.1:9999")]
        def settings(command):
            return dict(command[i + 1].split("=", 1) for i, value in enumerate(command) if value == "-c")
        baseline, mcp = map(settings, commands)
        self.assertEqual(baseline, {key: value for key, value in mcp.items() if not key.startswith("mcp_servers.")})
        self.assertEqual(commands[0][-1], commands[1][-1])
        self.assertEqual(baseline["model_reasoning_effort"], '"ultra"')
        self.assertEqual(baseline["agents.enabled"], "false")
        self.assertEqual(baseline["tool_output_token_limit"], "4096")
        self.assertEqual(baseline["sandbox_workspace_write.network_access"], "true")
        self.assertIn("--agent-text-only", mcp["mcp_servers.semantic_search.args"])
        self.assertIn("new regression test file", user_prompt)

    def test_environment_removes_parent_bridges_without_modifying_auth_home(self):
        with patch.dict("os.environ", {"CODEX_THREAD_ID": "parent-thread", "CODEX_APP_TOOLS_PIPE_PATH": "bridge",
                                        "OPENAI_API_KEY": "fake-key"}):
            env = runner.environment()
        self.assertNotIn("CODEX_THREAD_ID", env)
        self.assertNotIn("CODEX_APP_TOOLS_PIPE_PATH", env)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertIn("cancellable=", env["PYTEST_ADDOPTS"])
        self.assertTrue(env["PATH"].startswith(str(Path(sys.executable).parent)))

    def test_actual_subprocess_capture_preserves_usage_and_excludes_public_reasoning(self):
        events = [{"type": "item.started", "item": {"id": "shell1", "type": "command_execution"}},
                  {"type": "item.completed", "item": {"id": "reason1", "type": "reasoning", "text": "PRIVATE_REASONING"}},
                  {"type": "item.completed", "item": {"id": "shell1", "type": "command_execution", "command": "true"}},
                  {"type": "turn.completed", "usage": {"input_tokens": 20, "output_tokens": 7,
                    "cached_input_tokens": 10, "cache_write_input_tokens": 0, "reasoning_output_tokens": 3}}]
        code = "import json; rows=" + repr(events) + "; [print(json.dumps(row),flush=True)for row in rows]"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw"
            raw.mkdir()
            record, received, observed = runner.run_agent([sys.executable, "-c", code], {"timeout_s": 2}, root, raw)
            self.assertEqual(record["usage"]["total_tokens"], 27)
            self.assertEqual(record["usage"]["uncached_input_tokens"], 10)
            self.assertEqual(record["usage"]["reasoning_output_tokens"], 3)
            self.assertIsNone(record["per_model_call_usage"])
            self.assertIsNotNone(record["tool_intervals"][0]["observed_duration_s"])
            hashes = runner.public_artifacts(raw, root / "public", received, observed, [])
            self.assertIn("PRIVATE_REASONING", (raw / "events.jsonl").read_text())
            self.assertNotIn("PRIVATE_REASONING", (root / "public/events.jsonl").read_text())
            self.assertNotIn("PRIVATE_REASONING", (root / "public/event-timestamps.json").read_text())
            self.assertIn("events.jsonl", hashes["raw"])

    def test_timeout_is_retained_without_invented_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record, _, _ = runner.run_agent([sys.executable, "-c", "import time; time.sleep(10)"],
                                           {"timeout_s": .05}, root, root)
            self.assertTrue(record["timed_out"])
            self.assertFalse(record["completed"])
            self.assertIsNone(record["usage"])

    def test_restored_original_tests_and_configs_prevent_weakening_the_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus = root / "corpus"
            (corpus / "src").mkdir(parents=True)
            (corpus / "tests").mkdir()
            (corpus / "src/package.py").write_text("def answer():\n    return 1\n")
            (corpus / "tests/test_answer.py").write_text("from package import answer\n\ndef test_answer():\n    assert answer() == 1\n")
            (corpus / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath=["src"]\n')
            subprocess.run(["git", "init", "-q", str(corpus)], check=True)
            runner.old.git(corpus, "add", ".")
            runner.old.git(corpus, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "original")
            base_files = runner.old.tree_hashes(corpus)
            workspace = root / "candidate"
            shutil.copytree(corpus, workspace)
            (workspace / "tests/test_answer.py").write_text("import pytest\n\n@pytest.mark.skip\ndef test_answer():\n    assert False\n")
            (workspace / "pyproject.toml").write_text('[tool.pytest.ini_options]\naddopts="-k no_matching_tests"\n')
            (workspace / "pytest.ini").write_text('[pytest]\naddopts=-k no_matching_tests\n')
            raw = root / "raw"
            raw.mkdir()
            expected = dict.fromkeys(runner.PYTEST_COUNT_FIELDS, 0)
            expected["passed"] = 1
            task = {"corpus": "corpus", "revision": runner.old.git(corpus, "rev-parse", "HEAD").stdout.decode().strip(),
                    "source_root": "src", "compatibility_commands": [{"args": ["-m", "pytest", "-q", "tests/test_answer.py"],
                    "timeout_s": 10, "expected_summary": expected}]}
            with patch.object(runner, "ROOT", root):
                grade = runner.compatibility(task, workspace, base_files, raw)
            self.assertTrue(grade["passed"])
            self.assertEqual(grade["commands"][0]["actual_summary"], expected)
            self.assertEqual({change["path"] for change in grade["changed_or_deleted_original_tests"]},
                             {"tests/test_answer.py", "pyproject.toml"})
            self.assertEqual(grade["added_configuration_removed_for_grading"], ["pytest.ini"])
            self.assertIn("@pytest.mark.skip", (workspace / "tests/test_answer.py").read_text())

    def test_skipped_all_outcome_cannot_pass_exact_expected_gate(self):
        counts = runner.pytest_counts("1 skipped in 0.01s")
        expected = dict.fromkeys(runner.PYTEST_COUNT_FIELDS, 0)
        expected["passed"] = 1
        self.assertNotEqual(counts, expected)

    def test_public_redaction_masks_credential_patterns_without_loading_auth(self):
        text = "/Users/example/project sk-ABCDEFGHIJKLMNOPQRST Bearer abcdefghijklmnopqrstuvwxyz123456"
        safe = runner.public_redact(text, [("/Users/example", "<HOME>")])
        self.assertNotIn("sk-", safe)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", safe)
        self.assertIn("<HOME>", safe)

    def test_real_lexical_http_receipt_links_exact_payload_and_source_snapshots(self):
        from semantic_search.client import SearchClient
        from semantic_search.server import create_server
        from semantic_search.context import compact_result, serialize_context
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, raw = root / "workspace", root / "raw"
            (workspace / "src").mkdir(parents=True)
            raw.mkdir()
            (workspace / "src/probe.py").write_text(runner.PROBE_SOURCE)
            subprocess.run(["git", "init", "-q", str(workspace)], check=True)
            runner.old.git(workspace, "add", ".")
            runner.old.git(workspace, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
            backend, guard = runner.recording_backend(workspace, "src", raw, model_rejecting=True)
            backend.record_requests = True
            server = create_server(backend, port=0)
            worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01})
            worker.start()
            try:
                client = SearchClient(f"http://127.0.0.1:{server.server_port}")
                response = client.search("workflow probe", retrieval="bm25", max_results=1,
                                         token_budget=512, include_dependencies=False)
            finally:
                server.shutdown()
                server.server_close()
                worker.join(2)
            receipt = json.loads((raw / "http-receipts.jsonl").read_text())
            event = {"type": "item.completed", "item": {"id": "mcp1", "type": "mcp_tool_call",
                     "server": "semantic_search", "tool": "search_code", "status": "completed",
                     "arguments": dict(runner.PROBE_ARGUMENTS),
                     "result": {"content": [{"type": "text", "text": serialize_context(compact_result(response))}]}}}
            self.assertEqual(len(runner.probe_matches([event], [receipt])), 1)
            self.assertEqual(guard.calls, 0)
            receipt["returned_source_checks"] = []
            self.assertEqual(runner.probe_matches([event], [receipt]), [])
            receipt = json.loads((raw / "http-receipts.jsonl").read_text())
            event["item"]["result"]["isError"] = True
            self.assertEqual(runner.probe_matches([event], [receipt]), [])
            event["item"]["result"].pop("isError")
            event["item"]["arguments"]["retrieval"] = "dense"
            self.assertEqual(runner.probe_matches([event], [receipt]), [])

    def test_loopback_probe_accepts_only_one_exact_script_after_mcp(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            file = workspace / "loopback_smoke.py"
            file.write_text(runner.LOOPBACK_SOURCE)
            result = subprocess.run([sys.executable, str(file)], capture_output=True, text=True, timeout=10)
            self.assertEqual((result.returncode, result.stdout.strip()), (0, "LOOPBACK_OK"))
            events = [{"type": "item.completed", "item": {"type": "mcp_tool_call", "id": "mcp1"}},
                      {"type": "item.started", "item": {"type": "command_execution", "id": "shell1"}},
                      {"type": "item.completed", "item": {"type": "command_execution", "id": "shell1",
                       "command": f'{sys.executable} loopback_smoke.py', "status": "completed",
                       "exit_code": 0, "aggregated_output": "LOOPBACK_OK\n"}}]
            self.assertEqual(len(runner.controlled_loopback_commands(events, sys.executable, workspace)), 1)
            events[-1]["item"]["command"] += "; echo unintended"
            self.assertEqual(runner.controlled_loopback_commands(events, sys.executable, workspace), [])


if __name__ == "__main__":
    unittest.main()
