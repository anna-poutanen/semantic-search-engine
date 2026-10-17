"""Drive the real packaged launcher through a terminal and generated MCP config."""
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import pty
import select
import signal
import socket
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).resolve().parent
QUERY = "Open and decode a signed cookie session, returning an empty session when its signature is invalid."
QUERIES = [QUERY, "Dispatch a request to its view function and handle exceptions during request processing."]


def main():
    state = json.loads((ROOT / ".cache/releases/v0.1.3/qualification-state.json").read_text())
    app = Path(state["app"])
    python = app / ".cache/quickstart/venv/bin/python"
    repo = Path("/Users/annapoutanen/personal-projects/semantic-search-engine/.cache/corpora/flask")
    revision = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    assert not subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"], text=True).strip()
    environment = dict(os.environ, PATH="/usr/bin:/bin:/usr/sbin:/sbin")
    for name in state["child_inherited_python_and_auth_settings_removed"]:
        environment.pop(name, None)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    sessions = app / ".cache/quickstart/sessions"
    reused_config = None
    runs = []
    # The initial cold index is retained in before-mcp-cache-isolation/.
    # These sessions reopen it after isolating the toy adapter's fallback cache.
    for stage in ("reopen", "restart"):
        before = set(sessions.iterdir()) if sessions.exists() else set()
        master, slave = pty.openpty()
        log = OUTPUT / ("ownrepo-" + stage + ".log")
        transcript = bytearray()
        process = subprocess.Popen(["/bin/bash", "Get Started.command", "--repo", str(repo),
                                    "--source-root", "src/flask", "--port", str(port)],
                                   cwd=app, env=environment, stdin=slave, stdout=slave, stderr=slave,
                                   start_new_session=True)
        os.close(slave)
        started = time.perf_counter()
        with log.open("xb") as stream:
            def drain():
                if not select.select([master], [], [], .1)[0]:
                    return
                try:
                    data = os.read(master, 65536)
                except OSError as error:
                    if error.errno == errno.EIO:
                        return
                    raise
                transcript.extend(data)
                stream.write(data)
                stream.flush()

            def prompt(after=0, timeout=300):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    drain()
                    if b"Search> " in transcript[after:]:
                        return
                    if process.poll() is not None:
                        raise RuntimeError(f"Launcher stopped before prompt; see {log}")
                raise TimeoutError(f"Launcher prompt timeout; see {log}")

            try:
                prompt()
                startup = time.perf_counter() - started
                added = set(sessions.iterdir()) - before
                assert len(added) == 1
                session = added.pop()
                config = session / "mcp.json"
                if reused_config is None:
                    reused_config = config
                mcp_output = OUTPUT / ("ownrepo-" + stage + "-mcp-verification.json")
                command = [str(python), str(ROOT / "scripts/verify_beta_mcp.py"), "--config", str(reused_config),
                           "--repo", str(repo), "--source-root", "src/flask", "--output", str(mcp_output)]
                for query in QUERIES:
                    command.extend(("--query", query))
                with (OUTPUT / ("ownrepo-" + stage + "-mcp.log")).open("xb") as mcp_log:
                    subprocess.run(command, env=environment, cwd=app, stdout=mcp_log,
                                   stderr=subprocess.STDOUT, timeout=300, check=True)
                mcp = json.loads(mcp_output.read_text())
                assert mcp["packages"]["semantic-search-engine"] == "0.1.3"
                assert mcp["network_guard"]["outbound_attempts"] == 0
                marker = len(transcript)
                os.write(master, (QUERY + "\n").encode())
                prompt(marker, 120)
                assert b"open_session" in transcript[marker:] and b"src/flask/sessions.py" in transcript[marker:]
                marker = len(transcript)
                os.write(master, b":mcp\n")
                prompt(marker, 30)
                assert b'"mcpServers"' in transcript[marker:] and b'"TIKTOKEN_CACHE_DIR"' in transcript[marker:]
                marker = len(transcript)
                os.write(master, b":doctor\n")
                prompt(marker, 30)
                os.write(master, b"q\n")
                deadline = time.monotonic() + 30
                while process.poll() is None and time.monotonic() < deadline:
                    drain()
                assert process.wait(timeout=5) == 0
                drain()
                saved = json.loads((session / "session.json").read_text())
                assert saved["searches"] == 1 and saved["errors"] == 0
                assert saved["shutdown"] == {"returncode": 0, "forced_kill": False}
                assert saved["settings"] == {"retrieval": "dense", "rerank": False, "candidates": 4,
                    "max_results": 8, "source_budget": 12000, "token_budget": 4096, "include_dependencies": True}
                service_bytes = (session / "service.log").read_bytes()
                (OUTPUT / ("ownrepo-" + stage + "-service.log")).write_bytes(service_bytes)
                progress = [json.loads(line[len("Progress: "):]) for line in service_bytes.decode().splitlines()
                            if line.startswith("Progress: ")]
                cache = next(item for item in progress if item["phase"] == "cache")
                assert cache["pending_documents"] == 0 and cache["cached_documents"] == cache["total"] > 0
                for name in ("mcp.json", "vscode-mcp.json", "session.json"):
                    saved_name = "mcp-config.json" if name == "mcp.json" else name
                    (OUTPUT / ("ownrepo-" + stage + "-" + saved_name)).write_bytes((session / name).read_bytes())
                runs.append({"stage": stage, "startup_wall_s": startup, "cache": cache,
                             "session": saved, "generated_config_reused_after_restart": stage == "restart",
                             "typed_terminal_query_has_open_session_excerpt": True,
                             "mcp_source_and_budget_checks_passed": True,
                             "terminal_mcp_and_doctor_commands_passed": True})
                print(json.dumps({"stage": stage, "cache": cache, "shutdown": saved["shutdown"]}), flush=True)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=25)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
                os.close(master)
    for name, arguments in (
        ("doctor-after-setup", ["--doctor", "--repo", str(repo), "--source-root", "src/flask"]),
        ("feedback", ["--feedback", "--output", str(OUTPUT / "scripted-feedback.json"),
                      "--attempts", "1", "--full", "1", "--interface", "mcp", "--workflow", "saved_search"]),
    ):
        with (OUTPUT / (name + ".log")).open("xb") as stream:
            subprocess.run(["/bin/bash", "Get Started.command", *arguments], env=environment, cwd=app,
                           stdout=stream, stderr=subprocess.STDOUT, timeout=60, check=True)
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "version": "0.1.3",
              "zip_sha256": state["zip_sha256"], "public_repo": "https://github.com/pallets/flask",
              "revision": revision, "source_root": "src/flask", "queries": QUERIES, "runs": runs,
              "support_commands_passed": True, "feedback_is_scripted_external_testers": 0,
              "earlier_validation_precondition": "before-mcp-cache-isolation/failure.json",
              "earlier_receipt_filename_correction": "before-mcp-receipt-name-correction/correction.json",
              "limits": ["Existing development Mac; not an independent machine.",
                         "Source and integration checks on previously observed public queries, not a new retrieval benchmark.",
                         "One-run battery-power wall times are not a latency benchmark.",
                         "MCP guard covers Python client/adapter sockets; resident backend is separate."]}
    (OUTPUT / "ownrepo.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
