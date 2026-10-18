"""Model-free preparation, toy source proofs and owned-process lifecycle tests."""
import asyncio
from contextlib import asynccontextmanager, nullcontext
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest

SPEC = importlib.util.spec_from_file_location("portfolio_demo_mac", Path(__file__).resolve().parents[1] / "scripts/demo_mac.py")
demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demo)


def response(repo, query):
    rows = []
    for name, text in demo.SOURCES.items():
        rows.append({"path": name, "symbol": "toy", "start_line": 1, "end_line": len(text.splitlines()),
                     "source": text, "source_hash": hashlib.sha256(text.encode()).hexdigest()})
    return {"query": query, "scope": {"source_root": "src"}, "results": rows,
            "token_usage": {"encoding": "cl100k_base", "budget": 4096, "tokens": 200}}


def test_default_preparation_tracks_exact_toy_files_and_index_is_ignored(tmp_path, capsys):
    target = tmp_path / "demo with spaces"
    with patch.object(demo, "run_demo", side_effect=AssertionError("Preparation must never load a model")):
        demo.main(["--destination", str(target), "--python", str(tmp_path / "venv with spaces/bin/python")])
    output = capsys.readouterr().out
    tracked = subprocess.check_output(["git", "-C", str(target), "ls-files", "-z"]).decode().split("\0")
    assert set(tracked) - {""} == {".gitignore", *demo.SOURCES}
    for name, text in demo.SOURCES.items():
        assert (target / name).read_bytes() == text.encode("utf-8")
    cache, database = demo.cache_paths(target)
    assert database == cache / (hashlib.sha256((str(target.resolve()) + "\nsrc").encode()).hexdigest() + ".sqlite3")
    assert not cache.exists()
    assert subprocess.run(["git", "-C", str(target), "check-ignore", "--quiet", str(database)]).returncode == 0
    assert "--runtime-only" in output and "--source-root src" in output and "MCP stdio" in output
    assert "Include eligible dirty tracked buffers" in output
    for line in output.splitlines():
        if "semantic_search.cli serve" in line:
            args = shlex.split(line)
            assert args[0] == str(tmp_path / "venv with spaces/bin/python")
            assert args[args.index("--repo") + 1] == str(target.resolve())
            assert args[args.index("--index-cache") + 1] == str(cache)
        if line.startswith("curl "):
            args = shlex.split(line)
            payload = json.loads(args[args.index("--data-binary") + 1])
            assert payload["query"] == demo.QUERIES[0] and payload["rerank"] is False


@pytest.mark.parametrize("kind", ["directory", "file", "symlink", "dangling_symlink"])
def test_preparation_refuses_every_existing_path_without_mutating_it(tmp_path, kind):
    target, original = tmp_path / "target", tmp_path / "original"
    original.mkdir()
    (original / "sentinel").write_text("keep")
    if kind == "directory":
        target.mkdir()
    elif kind == "file":
        target.write_text("keep")
    else:
        target.symlink_to(original if kind == "symlink" else tmp_path / "missing", target_is_directory=True)
    with pytest.raises(ValueError, match="Refusing to overwrite"):
        demo.prepare(target)
    assert (original / "sentinel").read_text() == "keep"
    assert target.is_symlink() if "symlink" in kind else target.exists()


def test_project_destinations_must_be_ignored_and_missing_git_has_actionable_guidance(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "-C", str(project), "init", "--quiet"], check=True)
    (project / ".gitignore").write_text(".cache/\n")
    with patch.object(demo, "ROOT", project):
        with pytest.raises(ValueError, match="ignored destination"):
            demo.prepare(project / "tracked-folder")
        repo = demo.prepare(project / ".cache/My Demo")
        assert repo.is_dir()
    with patch.object(demo.shutil, "which", return_value=None):
        with pytest.raises(ValueError, match="xcode-select --install"):
            demo.prepare(tmp_path / "no-git")
    assert not (tmp_path / "no-git").exists()


def test_toy_languages_are_executable_and_source_proof_rejects_tampering(tmp_path):
    from semantic_search.corpus import extract_snippets
    repo = demo.prepare(tmp_path / "fixture")
    snippets = extract_snippets(repo, "src")
    assert {row.symbol for row in snippets} == {"normalize_label", "uniqueValues", "clamp"}
    value = response(repo, demo.QUERIES[0])
    assert len(demo.verify_response(value, demo.QUERIES[0], repo)["excerpts"]) == 3
    for edit in [{"path": "../escape.py"}, {"start_line": True}, {"end_line": 500},
                 {"source": "wrong"}, {"source_hash": "0" * 64}]:
        bad = json.loads(json.dumps(value))
        bad["results"][0].update(edit)
        with pytest.raises(ValueError):
            demo.verify_response(bad, demo.QUERIES[0], repo)
    with pytest.raises(ValueError):
        demo.verify_response(value, "different request", repo)


class OwnedProcess:
    def __init__(self):
        self.returncode = None
        self.terminated = self.killed = 0
        self.pid = 987654321
    def poll(self):
        return self.returncode
    def terminate(self):
        self.terminated += 1
    def kill(self):
        self.killed += 1
    def wait(self, timeout):
        if self.returncode is None:
            self.returncode = 0 if not self.killed else -9
        return self.returncode


def mock_group(owned):
    def send(pid, signum):
        assert pid == owned.pid
        if signum == signal.SIGTERM:
            owned.terminate()
        elif signum == signal.SIGKILL:
            owned.kill()
        elif signum == 0 and owned.returncode is not None:
            raise ProcessLookupError
    return patch.object(demo.os, "killpg", side_effect=send)


def test_run_lifecycle_uses_real_http_with_fake_service_and_disposes_only_owned_child(tmp_path):
    repo = demo.prepare(tmp_path / "fixture")
    bodies = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            bodies.append(body)
            data = json.dumps(response(repo, body["query"])).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    owned, calls = OwnedProcess(), []
    def popen(arguments, **kwargs):
        calls.append(arguments)
        assert kwargs["start_new_session"] is True
        kwargs["stdout"].write(f"Ready: http://127.0.0.1:{server.server_port}\n".encode())
        kwargs["stdout"].flush()
        return owned
    try:
        with patch.object(demo.subprocess, "Popen", side_effect=popen), patch.object(demo, "run_mcp", return_value={"protocol_version": "cpu-mock", "searches": []}) as client, mock_group(owned):
            report = demo.run_demo(repo, Path("/fake installed runtime/bin/python"), timeout=2)
        assert report["shutdown"] == {"returncode": 0, "forced_kill": False}
        assert len(bodies) == 3 and {body["query"] for body in bodies} == set(demo.QUERIES)
        assert all(body["retrieval"] == "dense" and body["rerank"] is False for body in bodies)
        assert calls[0][calls[0].index("--source-root") + 1] == "src" and "--no-watch" in calls[0]
        assert client.call_args.args[0] == Path("/fake installed runtime/bin/python")
        assert owned.terminated == 1 and owned.killed == 0
        receipt = json.loads((repo / ".semantic-search/demo.json").read_text())
        assert receipt["database"] == str(demo.cache_paths(repo)[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("phase", ["startup", "http", "mcp"])
def test_failure_stops_owned_service_and_does_not_write_success_receipt(tmp_path, phase):
    repo, owned = demo.prepare(tmp_path / "fixture"), OwnedProcess()
    def popen(arguments, **kwargs):
        if phase == "startup":
            owned.returncode = 1
        else:
            kwargs["stdout"].write(b"Ready: http://127.0.0.1:12345\n")
            kwargs["stdout"].flush()
        return owned
    with patch.object(demo.subprocess, "Popen", side_effect=popen), patch.object(demo, "exercise_http", side_effect=ValueError("HTTP failed") if phase == "http" else None, return_value=[]), patch.object(demo, "run_mcp", side_effect=RuntimeError("MCP failed")), mock_group(owned):
        with pytest.raises((RuntimeError, ValueError)):
            demo.run_demo(repo, Path("/fake/bin/python"), timeout=2)
    assert owned.returncode is not None
    assert owned.terminated == 1
    assert not (repo / ".semantic-search/demo.json").exists()


def test_readiness_timeout_and_unresponsive_owned_child_are_cleaned_up(tmp_path):
    repo, owned = demo.prepare(tmp_path / "fixture"), OwnedProcess()
    with patch.object(demo.subprocess, "Popen", return_value=owned), patch.object(demo.time, "monotonic", side_effect=[0, 3, 3]), mock_group(owned):
        with pytest.raises(RuntimeError, match="timed out"):
            demo.run_demo(repo, Path("/fake/bin/python"), timeout=2)
    assert owned.terminated == 1
    waits = iter([subprocess.TimeoutExpired("owned", 15), -9])
    def wait(timeout):
        result = next(waits)
        if isinstance(result, BaseException):
            raise result
        owned.returncode = result
        return result
    owned.returncode = None
    owned.wait = wait
    with mock_group(owned):
        assert demo.stop_owned(owned) == {"returncode": -9, "forced_kill": True}
    assert owned.killed == 1


def test_official_mcp_worker_lifecycle_uses_only_shared_service_and_calls_each_toy_query(tmp_path):
    from semantic_search.context import encoding, serialize_context
    repo, seen = demo.prepare(tmp_path / "fixture"), []
    class FakeClient:
        protocol_version = "cpu-mock"
        def __init__(self, transport, **kwargs):
            assert kwargs == {"mode": "legacy", "read_timeout_seconds": 2}
            self.transport = transport
        async def __aenter__(self):
            assert await self.transport.__aenter__() == ("reader", "writer")
            return self
        async def __aexit__(self, *args):
            await self.transport.__aexit__(*args)
            seen.append("closed")
        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(name="search_code")])
        async def call_tool(self, name, arguments):
            assert name == "search_code" and arguments["rerank"] is False
            seen.append(arguments["query"])
            value = response(repo, arguments["query"])
            for _ in range(10):
                tokens = len(encoding().encode(serialize_context(value), disallowed_special=()))
                if tokens == value["token_usage"]["tokens"]:
                    break
                value["token_usage"]["tokens"] = tokens
            return SimpleNamespace(is_error=False, structured_content=value,
                                   content=[SimpleNamespace(type="text", text=serialize_context(value))])
    @asynccontextmanager
    async def fake_stdio(parameters):
        seen.append(parameters)
        assert parameters.args == ["-m", "semantic_search.mcp_server", "--server", "http://127.0.0.1:12345"]
        yield "reader", "writer"

    sdk = SimpleNamespace(Client=FakeClient, StdioServerParameters=lambda **kwargs: SimpleNamespace(**kwargs),
                          stdio_client=fake_stdio)
    with patch.dict(sys.modules, {"mcp": sdk}):
        result = asyncio.run(demo.exercise_mcp("/fake/bin/python", "http://127.0.0.1:12345", repo, 2))
    assert result["protocol_version"] == "cpu-mock" and len(result["searches"]) == 3
    assert seen[1:-1] == list(demo.QUERIES) and seen[-1] == "closed"


def test_mcp_spawn_failure_preserves_error_and_restores_signal_handlers(tmp_path):
    sdk = pytest.importorskip("mcp")
    repo = demo.prepare(tmp_path / "fixture")
    handlers = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}

    @asynccontextmanager
    async def failed_spawn(parameters):
        raise OSError("owned adapter could not start")
        yield  # pragma: no cover

    with patch.object(sdk, "stdio_client", side_effect=failed_spawn):
        with pytest.raises(OSError, match="owned adapter could not start"):
            demo.run_mcp(sys.executable, "http://127.0.0.1:12345", repo, 2)
    assert {signum: signal.getsignal(signum) for signum in handlers} == handlers


def test_invalid_cli_limits_and_run_runtime_guidance_create_no_fixture(tmp_path, capsys):
    target = tmp_path / "fixture"
    for argv in [["--port", "0"], ["--timeout", "nan"], ["--timeout", "0"]]:
        with pytest.raises(SystemExit):
            demo.main(["--destination", str(target), *argv])
    with patch.object(demo.platform, "system", return_value="Darwin"), patch.object(demo.platform, "machine", return_value="arm64"):
        with pytest.raises(SystemExit):
            demo.main(["--run", "--destination", str(target), "--python", str(tmp_path / "missing")])
    assert not target.exists()
    assert "Runtime Python missing" in capsys.readouterr().err


def test_preparation_runs_with_site_packages_and_pythonpath_disabled(tmp_path):
    target = tmp_path / "stdlib only demo"
    script = Path(demo.__file__).resolve()
    completed = subprocess.run([sys.executable, "-I", "-S", str(script), "--destination", str(target)],
                               check=True, capture_output=True, text=True, timeout=10)
    assert target.is_dir() and "Terminal 1" in completed.stdout and "MCP stdio" in completed.stdout
    assert "Automatic demo" in completed.stdout and not (target / ".semantic-search").exists()


def test_external_parent_alias_cannot_create_nested_repo_in_unignored_project_directory(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "-C", str(project), "init", "--quiet"], check=True)
    (project / ".gitignore").write_text(".cache/\n")
    alias = tmp_path / "external-alias"
    alias.symlink_to(project, target_is_directory=True)
    with patch.object(demo, "ROOT", project):
        with pytest.raises(ValueError, match="ignored destination"):
            demo.prepare(alias / "tracked child")
        assert not (project / "tracked child").exists()
        assert demo.prepare(alias / ".cache/ignored child") == project / ".cache/ignored child"


def process_alive(pid):
    completed = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True)
    state = completed.stdout.strip()
    return bool(state) and not state.startswith("Z")


@pytest.mark.skipif(os.name != "posix", reason="Mac/POSIX process-tree proof")
@pytest.mark.parametrize("mode", ["timeout", "sigint", "sigint_during_spawn"])
def test_actual_model_free_sdk_adapter_and_descendant_stop_on_timeout_or_sigint(tmp_path, mode):
    sdk = pytest.importorskip("mcp")
    repo = demo.prepare(tmp_path / "fixture")
    script, pids = tmp_path / "fake_adapter.py", tmp_path / "pids.json"
    script.write_text(
        'import json, os, subprocess, sys\n'
        'child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])\n'
        'temporary = sys.argv[1] + ".pending"\n'
        'with open(temporary, "w") as stream: json.dump([os.getpid(), child.pid], stream)\n'
        'os.replace(temporary, sys.argv[1])\n'
        'try:\n'
        ' for line in sys.stdin: pass\n'
        'finally:\n'
        ' child.terminate(); child.wait(timeout=2)\n'
    )
    parameters = sdk.StdioServerParameters
    spawn_handoff = []
    spawn_patch = nullcontext()
    if mode == "sigint_during_spawn":
        import anyio
        import mcp.client.stdio as stdio

        spawn = stdio._create_platform_compatible_process

        async def delayed_handoff(**kwargs):
            process = await spawn(**kwargs)
            spawn_handoff.append("spawned")
            deadline = time.monotonic() + 5
            while not pids.exists() and time.monotonic() < deadline:
                await anyio.sleep(.01)
            # Keep a real spawned process in the handoff window until SIGINT is
            # delivered. Cancellation here must wait until SDK cleanup is armed.
            await anyio.sleep(.1)
            spawn_handoff.append("returned")
            return process

        spawn_patch = patch.object(stdio, "_create_platform_compatible_process", side_effect=delayed_handoff)
    trigger = None
    def send_interrupt():
        deadline = time.monotonic() + 5
        while not pids.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        if pids.exists():
            os.kill(os.getpid(), signal.SIGINT)
    if mode.startswith("sigint"):
        trigger = threading.Thread(target=send_interrupt, daemon=True)
        trigger.start()
    try:
        with spawn_patch, patch.object(sdk, "StdioServerParameters", side_effect=lambda **kwargs: parameters(command=sys.executable, args=[str(script), str(pids)], cwd=tmp_path)):
            if mode.startswith("sigint"):
                with pytest.raises(KeyboardInterrupt):
                    demo.run_mcp(sys.executable, "http://127.0.0.1:12345", repo, 10)
            else:
                with pytest.raises(Exception):
                    demo.run_mcp(sys.executable, "http://127.0.0.1:12345", repo, .2)
        assert pids.exists()
        owned = json.loads(pids.read_text())
        deadline = time.monotonic() + 3
        while any(process_alive(pid) for pid in owned) and time.monotonic() < deadline:
            time.sleep(.02)
        assert not any(process_alive(pid) for pid in owned)
        if mode == "sigint_during_spawn":
            assert spawn_handoff == ["spawned", "returned"]
    finally:
        if trigger:
            trigger.join(timeout=5)
        if pids.exists():
            for pid in json.loads(pids.read_text()):
                if process_alive(pid):
                    os.kill(pid, signal.SIGKILL)


@pytest.mark.skipif(os.name != "posix", reason="Mac/POSIX process-tree proof")
def test_owned_service_group_reaps_a_real_model_free_child(tmp_path):
    script, pids = tmp_path / "fake_service.py", tmp_path / "pids.json"
    script.write_text(
        'import json, os, signal, subprocess, sys, time\n'
        'child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])\n'
        'def stop(*args):\n'
        ' child.terminate(); child.wait(timeout=2); sys.exit(0)\n'
        'signal.signal(signal.SIGTERM, stop)\n'
        'with open(sys.argv[1], "w") as stream: json.dump([os.getpid(), child.pid], stream)\n'
        'while True: time.sleep(.01)\n'
    )
    process = subprocess.Popen([sys.executable, str(script), str(pids)], start_new_session=True)
    try:
        deadline = time.monotonic() + 3
        while not pids.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert pids.exists()
        assert demo.stop_owned(process) == {"returncode": 0, "forced_kill": False}
        assert not any(process_alive(pid) for pid in json.loads(pids.read_text()))
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)


def test_source_zip_without_git_allows_only_designated_demo_cache(tmp_path):
    project = tmp_path / "Downloaded App"
    project.mkdir()
    with patch.object(demo, "ROOT", project):
        with pytest.raises(ValueError, match="ignored destination"):
            demo.prepare(project / "src/not-a-demo")
        repo = demo.prepare(project / ".cache/demos/My Demo")
    assert (repo / ".git").is_dir()
    assert set(subprocess.check_output(["git", "-C", str(repo), "ls-files"]).decode().splitlines()) == {".gitignore", *demo.SOURCES}


def test_demo_does_not_inherit_user_git_templates_or_repository_environment(tmp_path):
    project = tmp_path / "App"
    project.mkdir()
    template = tmp_path / "user-template"
    template.mkdir()
    (template / "sentinel").write_text("Do not copy this")
    config = tmp_path / "userconfig"
    config.write_text(f'[init]\n  templateDir = "{template}"\n')
    unrelated = tmp_path / "other-repo"
    unrelated.mkdir()
    subprocess.run(["git", "-C", str(unrelated), "init", "--quiet"], check=True)
    with patch.object(demo, "ROOT", project), patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config), "GIT_DIR": str(unrelated / ".git")}):
        repo = demo.prepare(tmp_path / "toy")
    assert not (repo / ".git/sentinel").exists()
    assert subprocess.check_output(["git", "-C", str(unrelated), "ls-files"]) == b""
    assert set(subprocess.check_output(["git", "-C", str(repo), "ls-files"]).decode().splitlines()) == {".gitignore", *demo.SOURCES}


def test_quiet_demo_keeps_fresh_toy_and_skips_manual_setup_instructions(tmp_path, capsys):
    target = tmp_path / "quiet toy"
    demo.main(["--destination", str(target), "--quiet"])
    output = capsys.readouterr().out
    assert output.strip() == f"Toy repository: {target.resolve()}"
    assert (target / "src/labels.py").is_file()
