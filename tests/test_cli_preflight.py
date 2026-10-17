"""The source preflight command works before any index or model is started."""

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from semantic_search import cli


SOURCE = Path(__file__).resolve().parents[1] / "src"
IMPORT_AND_WRITE_GUARD = '''\
import os
import sys

class ImportGuard:
    def find_spec(self, fullname, path=None, target=None):
        forbidden = (
            "semantic_search.embeddings", "semantic_search.backend",
            "semantic_search.store", "semantic_search.model", "semantic_search.context",
            "semantic_search.experiment", "semantic_search.server", "semantic_search.client",
            "semantic_search.mcp_server", "semantic_search.qwen_reranker",
            "mlx", "mlx_embeddings", "torch", "transformers", "huggingface_hub", "tiktoken", "sqlite3",
        )
        if any(fullname == name or fullname.startswith(name + ".") for name in forbidden):
            raise AssertionError("Preflight imported forbidden module: " + fullname)

sys.meta_path.insert(0, ImportGuard())

def guard(event, args):
    if event.startswith("socket.") or event == "sqlite3.connect":
        raise AssertionError("Preflight attempted network or database work: " + event)
    if event == "open":
        flags = args[2]
        if isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            raise AssertionError("Preflight attempted a file write")
    if event in ("os.mkdir", "os.remove", "os.rmdir", "os.rename", "os.link", "os.symlink", "os.truncate"):
        raise AssertionError("Preflight attempted a filesystem mutation: " + event)
    if event == "subprocess.Popen":
        command = args[1]
        if not isinstance(command, (list, tuple)) or os.path.basename(str(command[0])) != "git":
            raise AssertionError("Preflight attempted a non-Git subprocess")

sys.addaudithook(guard)
'''


class CLIPreflightTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="source preflight cli ")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.repo = self.directory / "repository with spaces"
        (self.repo / "src").mkdir(parents=True)
        (self.repo / "src" / "good.py").write_text("def find_answer():\n    return 42\n")
        subprocess.run(["git", "init", "--quiet", str(self.repo)], check=True,
                       capture_output=True, text=True)
        subprocess.run(["git", "-C", str(self.repo), "add", "--", "src"], check=True,
                       capture_output=True, text=True)
        guard = self.directory / "import guard"
        guard.mkdir()
        (guard / "sitecustomize.py").write_text(IMPORT_AND_WRITE_GUARD)
        self.environment = dict(os.environ, PYTHONPATH=os.pathsep.join((str(guard), str(SOURCE))),
                                PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1",
                                TRANSFORMERS_OFFLINE="1")

    def run_cli(self, *arguments):
        return subprocess.run([sys.executable, "-m", "semantic_search.cli", "inspect", *arguments],
                              cwd=self.directory, env=self.environment,
                              capture_output=True, text=True, timeout=30)

    def call_main(self, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = cli.main(["inspect", *arguments])
        return result, output.getvalue()

    def test_defaults_are_forwarded_and_ready_report_returns_zero(self):
        report = {"status": "ready", "stable_snapshot": True}
        with patch("semantic_search.preflight.inspect_source", return_value=report) as inspect, \
                patch("semantic_search.preflight.format_preflight", return_value="Saved source is ready.") as format_report:
            result, output = self.call_main("--repo", str(self.repo))
        inspect.assert_called_once_with(self.repo, "src", source_exclusions=[], limit=10)
        format_report.assert_called_once_with(report)
        self.assertEqual((result, output), (0, "Saved source is ready.\n"))

    def test_json_and_repeated_exclusions_forward_without_text_formatting(self):
        report = {"status": "blocked", "stable_snapshot": True, "errors": 1}
        with patch("semantic_search.preflight.inspect_source", return_value=report) as inspect, \
                patch("semantic_search.preflight.format_preflight") as format_report:
            result, output = self.call_main("--repo", str(self.repo), "--source-root", "lib",
                "--exclude-source", "*/fixtures/*", "--exclude-source", "*.test.ts", "--limit", "3", "--json")
        inspect.assert_called_once_with(self.repo, "lib",
            source_exclusions=["*/fixtures/*", "*.test.ts"], limit=3)
        format_report.assert_not_called()
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(output), report)

    def test_invalid_inspection_input_uses_parser_error_without_traceback(self):
        for error in (ValueError("invalid source root"), OSError("source is unreadable")):
            with self.subTest(error=error), patch("semantic_search.preflight.inspect_source", side_effect=error), \
                    contextlib.redirect_stderr(io.StringIO()) as output, self.assertRaises(SystemExit) as raised:
                self.call_main("--repo", str(self.repo))
            self.assertEqual(raised.exception.code, 2)
            self.assertIn(str(error), output.getvalue())
            self.assertNotIn("Traceback", output.getvalue())

    def test_real_ready_repo_is_read_only_without_model_network_or_database_imports(self):
        before = sorted(str(path.relative_to(self.repo)) for path in self.repo.rglob("*"))
        completed = self.run_cli("--repo", str(self.repo), "--json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["status"], "ready")
        self.assertTrue(report["stable_snapshot"])
        self.assertEqual(completed.stderr, "")
        self.assertEqual(sorted(str(path.relative_to(self.repo)) for path in self.repo.rglob("*")), before)
        self.assertFalse((self.directory / ".cache").exists())
        self.assertFalse((self.repo / ".semantic-search").exists())

    def test_real_parse_failure_returns_one_and_explicit_exclusion_restores_readiness(self):
        (self.repo / "src" / "broken.py").write_text("def broken(:\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "--", "src/broken.py"], check=True,
                       capture_output=True, text=True)
        completed = self.run_cli("--repo", str(self.repo), "--json")
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["status"], "blocked")
        self.assertEqual(completed.stderr, "")
        excluded = self.run_cli("--repo", str(self.repo), "--exclude-source", "src/broken.py", "--json")
        self.assertEqual(excluded.returncode, 0, excluded.stderr)
        self.assertEqual(json.loads(excluded.stdout)["status"], "ready")

    def test_real_text_mode_has_no_json_envelope_and_keeps_blocked_exit_status(self):
        (self.repo / "src" / "good.py").write_text("def broken(:\n")
        completed = self.run_cli("--repo", str(self.repo))
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertTrue(completed.stdout.strip())
        self.assertFalse(completed.stdout.lstrip().startswith("{"))
        self.assertNotIn("Traceback", completed.stderr)

    def test_bad_cli_arguments_exit_two_without_loading_services(self):
        invalid = [(), ("--repo", str(self.repo), "--limit", "nonsense"),
                   ("--repo", str(self.repo), "--limit", "0"),
                   ("--repo", str(self.repo), "--source-root", "../outside"),
                   ("--repo", str(self.repo), "--exclude-source", "../outside/*")]
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                completed = self.run_cli(*arguments)
                self.assertEqual(completed.returncode, 2, completed.stderr)
                self.assertIn("error:", completed.stderr)
                self.assertNotIn("Traceback", completed.stderr)


if __name__ == "__main__":
    unittest.main()
