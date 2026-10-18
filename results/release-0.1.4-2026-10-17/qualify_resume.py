"""Interrupt and resume own-repo indexing in the unmodified exact release ZIP.

Run after qualify_setup.py. All model work belongs to the extracted release's
launcher/runtime. The parent observes SQLite, stops only owned process groups,
and exercises the generated MCP config before and after a cache-only restart.
This is one-device release integration evidence, not a retrieval benchmark.
"""
from __future__ import annotations

import argparse
from contextlib import closing
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
import sqlite3
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
VERSION = "0.1.4"
SOURCE_ROOT = "src/flask"
QUERY = "Open and decode a signed cookie session, returning an empty session when its signature is invalid."
QUERIES = [QUERY, "Dispatch a request to its view function and handle exceptions during request processing."]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(database):
    """A separate read connection sees only complete commits; export no source."""
    tables = {}
    with closing(sqlite3.connect(database, timeout=10)) as connection:
        connection.execute("BEGIN")
        for name, order in (("snippets", "position"), ("snippets_fts", "id"),
                            ("active_vectors", "snippet_id"), ("metadata", "key")):
            rows = connection.execute(f"SELECT * FROM {name} ORDER BY {order}").fetchall()
            safe = [[{"blob_sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
                     if isinstance(value, bytes) else value for value in row] for row in rows]
            tables[name] = {"rows": len(rows), "sha256": hashlib.sha256(
                json.dumps(safe, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
        cache = [(fingerprint, document, dimensions, len(blob), hashlib.sha256(blob).hexdigest())
                 for fingerprint, document, dimensions, blob in connection.execute(
                     "SELECT model_fingerprint,document_hash,dimensions,vector FROM embeddings "
                     "ORDER BY model_fingerprint,document_hash")]
    return {"active_tables": tables, "cached_documents": len(cache),
            "embedding_cache_entries": {
                f"{fingerprint}:{document}": hashlib.sha256(json.dumps(
                    [dimensions, size, vector_hash], separators=(",", ":")).encode()).hexdigest()
                for fingerprint, document, dimensions, size, vector_hash in cache},
            "embedding_cache_sha256": hashlib.sha256(json.dumps(
                cache, separators=(",", ":")).encode()).hexdigest()}


def cache_preserved_subset(observed, current):
    """Allow later commits while requiring every witnessed vector to survive."""
    return all(current["embedding_cache_entries"].get(key) == value
               for key, value in observed["embedding_cache_entries"].items())


def process_rows():
    output = subprocess.check_output(["/bin/ps", "-axo", "pid=,ppid=,pgid=,stat=,command="], text=True)
    rows = []
    for line in output.splitlines():
        parts = line.strip().split(None, 4)
        if len(parts) == 5:
            rows.append({"pid": int(parts[0]), "ppid": int(parts[1]), "pgid": int(parts[2]),
                         "state": parts[3], "command": parts[4]})
    return rows


class TerminalLauncher:
    def __init__(self, app, repo, port, environment, output, stage):
        self.app, self.repo, self.stage = app, repo, stage
        self.sessions = app / ".cache/quickstart/sessions"
        self.before = set(self.sessions.iterdir()) if self.sessions.exists() else set()
        self.master, slave = pty.openpty()
        self.log = (output / f"{stage}.log").open("xb")
        self.transcript = bytearray()
        self.service = None
        self.session_directory = None
        self.started = time.perf_counter()
        self.process = subprocess.Popen(
            ["/bin/bash", "Get Started.command", "--repo", str(repo), "--source-root", SOURCE_ROOT,
             "--port", str(port)], cwd=app, env=environment, stdin=slave, stdout=slave,
            stderr=slave, start_new_session=True)
        os.close(slave)

    def drain(self, timeout=.05):
        if not select.select([self.master], [], [], timeout)[0]:
            return
        try:
            data = os.read(self.master, 65536)
        except OSError as error:
            if error.errno == errno.EIO:
                return
            raise
        self.transcript.extend(data)
        self.log.write(data)
        self.log.flush()

    def session(self):
        if self.session_directory is None and self.sessions.exists():
            added = set(self.sessions.iterdir()) - self.before
            require(len(added) <= 1, "Unexpected concurrent session in the owned extracted app")
            if added:
                self.session_directory = added.pop()
        return self.session_directory

    def discover_service(self):
        if self.service is None:
            candidates = [row for row in process_rows()
                          if row["ppid"] == self.process.pid
                          and "-m semantic_search.cli serve " in row["command"]
                          and str(self.repo) in row["command"]
                          and str(self.app / ".cache/quickstart/venv/bin/python") in row["command"]]
            require(len(candidates) <= 1, "Unexpected concurrent owned service")
            if candidates:
                self.service = candidates[0]
                require(self.service["pgid"] == self.service["pid"], "Service does not own its process group")
        return self.service

    def signal_service(self, signum):
        service = self.discover_service()
        if service is None:
            return
        current = next((row for row in process_rows() if row["pid"] == service["pid"]), None)
        if current is None or current["state"].startswith("Z"):
            return
        require(current["command"] == service["command"] and current["pgid"] == service["pid"],
                "Owned service identity changed; refusing to signal another process")
        os.killpg(service["pgid"], signum)

    def prompt(self, after=0, timeout=1800):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.drain()
            if b"Search> " in self.transcript[after:]:
                self.discover_service()
                require(self.session() is not None, "Ready launcher has no session artifacts")
                return
            if self.process.poll() is not None:
                raise RuntimeError(f"{self.stage}: launcher exited before prompt; inspect log")
        raise TimeoutError(f"{self.stage}: launcher prompt deadline exceeded")

    def stop_for_kill(self):
        require(self.discover_service() is not None, "Cannot freeze an unknown service")
        self.signal_service(signal.SIGSTOP)
        os.killpg(self.process.pid, signal.SIGSTOP)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            rows = {row["pid"]: row for row in process_rows()}
            if all(rows.get(pid, {}).get("state", "").startswith("T")
                   for pid in (self.process.pid, self.service["pid"])):
                return
            self.drain(.02)
        raise TimeoutError("Owned launcher/service did not acknowledge SIGSTOP")

    def kill_frozen(self):
        self.signal_service(signal.SIGKILL)
        os.killpg(self.process.pid, signal.SIGKILL)
        require(self.process.wait(timeout=10) == -signal.SIGKILL, "Launcher was not deliberately SIGKILLed")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            current = next((row for row in process_rows() if row["pid"] == self.service["pid"]), None)
            if current is None or current["state"].startswith("Z"):
                self.drain(0)
                return "absent" if current is None else "zombie; no running worker"
            self.drain(.02)
        raise TimeoutError("Owned native service survived SIGKILL")

    def quit(self):
        os.write(self.master, b"q\n")
        deadline = time.monotonic() + 30
        while self.process.poll() is None and time.monotonic() < deadline:
            self.drain()
        require(self.process.wait(timeout=5) == 0, "Launcher did not quit cleanly")
        self.drain(0)
        saved = json.loads((self.session() / "session.json").read_text())
        require(saved["shutdown"] == {"returncode": 0, "forced_kill": False}, "Service shutdown was not clean")
        return saved

    def close(self):
        try:
            if self.process.poll() is None:
                # A failed proof may leave the groups stopped. Continue only our
                # known groups before bounded normal cleanup, then force if needed.
                self.signal_service(signal.SIGCONT)
                os.killpg(self.process.pid, signal.SIGCONT)
                self.signal_service(signal.SIGTERM)
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=25)
                except subprocess.TimeoutExpired:
                    self.signal_service(signal.SIGKILL)
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=5)
            self.signal_service(signal.SIGKILL)
        finally:
            os.close(self.master)
            self.log.close()


def progress_events(session):
    return [json.loads(line[len("Progress: "):])
            for line in (session / "service.log").read_text().splitlines()
            if line.startswith("Progress: ")]


def copy_artifacts(launcher, output, stage):
    directory = launcher.session()
    for name in ("service.log", "session.json", "mcp.json", "vscode-mcp.json"):
        if (directory / name).is_file():
            label = "mcp-config.json" if name == "mcp.json" else name
            (output / f"{stage}-{label}").write_bytes((directory / name).read_bytes())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-directory", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True, help="Existing clean public Flask checkout; never modified")
    args = parser.parse_args(argv)
    output = args.output_directory.resolve()
    output.mkdir(parents=True, exist_ok=True)
    state_path = args.asset_directory.resolve() / "qualification-state.json"
    state = json.loads(state_path.read_text())
    require(state["version"] == VERSION and state.get("cold_setup_passed"), "Fresh release setup must pass first")
    owner, app = Path(state["owner"]), Path(state["app"])
    require((owner / "OWNED_RELEASE_QUALIFICATION").is_file() and app.is_relative_to(owner), "Unrecognized extraction ownership")
    require(json.loads((app / "BUNDLE_MANIFEST.json").read_text())["version"] == VERSION,
            "Extracted app version differs from the release")
    python = app / ".cache/quickstart/venv/bin/python"
    source_repo = args.repo.resolve()
    revision = subprocess.check_output(["git", "-C", str(source_repo), "rev-parse", "HEAD"], text=True).strip()
    require(not subprocess.check_output(["git", "-C", str(source_repo), "status", "--porcelain"], text=True).strip(),
            "Public source checkout must be clean")
    fixture = owner / "Public Flask Resume With Spaces"
    require(not fixture.exists(), "Choose an extraction without an earlier resume fixture")
    subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", str(source_repo), str(fixture)], check=True, timeout=60)
    require((fixture / "src/flask/sessions.py").is_file(), "Expected saved public Flask sessions.py")
    license_file = fixture / "LICENSE.txt"
    if license_file.is_file():
        (output / "FLASK_LICENSE.txt").write_bytes(license_file.read_bytes())
    environment = dict(os.environ, PATH="/usr/bin:/bin:/usr/sbin:/sbin",
                       GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    for name in state["child_inherited_python_and_auth_settings_removed"]:
        environment.pop(name, None)
    for name in tuple(environment):
        if name.startswith("GIT_") and name not in ("GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL"):
            environment.pop(name)
    # Direct CLI inspection must share the launcher's warmed encoding cache.
    # Leaving this unset creates the application's fallback cache and invalidates
    # the later generated-MCP check's intentional no-fallback precondition.
    environment["TIKTOKEN_CACHE_DIR"] = str(app / ".cache/quickstart/tiktoken")
    # Preserve HEAD and physical bytes; vary only the explicitly tracked source
    # membership so both runs have exactly the same repo/source-root cache identity.
    subprocess.run(["git", "-C", str(fixture), "rm", "--cached", "-r", "--quiet", "--", SOURCE_ROOT], check=True)
    subprocess.run(["git", "-C", str(fixture), "add", "--", "src/flask/sessions.py"], check=True)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    state["resume_qualification"] = {"fixture": str(fixture), "port": port,
                                     "public_revision": revision, "stages": []}
    write_json(state_path, state)
    launchers, runs = [], []

    def start(stage):
        launcher = TerminalLauncher(app, fixture, port, environment, output, stage)
        launchers.append(launcher)
        state["resume_qualification"]["stages"].append({"stage": stage, "launcher_pid": launcher.process.pid})
        write_json(state_path, state)
        return launcher

    try:
        seed = start("seed")
        seed.prompt()
        seed_session = seed.quit()
        copy_artifacts(seed, output, "seed")
        databases = list((app / ".cache/quickstart/indexes").rglob("*.sqlite3"))
        require(len(databases) == 1, "Expected one own-repo index in the fresh extracted app")
        database = databases[0]
        old = snapshot(database)
        require(old["cached_documents"] > 0 and old["active_tables"]["snippets"]["rows"] > 0,
                "Seed did not establish a nonempty active snapshot")
        subprocess.run(["git", "-C", str(fixture), "add", "--", SOURCE_ROOT], check=True)
        require(not subprocess.check_output(["git", "-C", str(fixture), "status", "--porcelain"], text=True).strip(),
                "Expanded fixture should exactly match original saved public source")
        preflight = subprocess.run([str(python), "-m", "semantic_search.cli", "inspect", "--repo", str(fixture),
                                    "--source-root", SOURCE_ROOT, "--json"], cwd=app, env=environment,
                                   capture_output=True, check=True, timeout=120)
        (output / "full-source-preflight.json").write_bytes(preflight.stdout)
        scope = json.loads(preflight.stdout)
        total_documents, total_windows = scope["windows"]["unique_documents"], scope["windows"]["total"]
        require(total_documents > old["cached_documents"] + 8, "Fixture lacks enough unfinished work")
        interrupted = start("interrupted")
        deadline = time.monotonic() + 1800
        observed = None
        while time.monotonic() < deadline:
            interrupted.drain(.025)
            require(interrupted.process.poll() is None, "Launcher exited before durable indexing progress")
            if interrupted.session() is not None and interrupted.discover_service() is not None:
                candidate = snapshot(database)
                if candidate["cached_documents"] > old["cached_documents"]:
                    require(candidate["active_tables"] == old["active_tables"],
                            "Partial work replaced the active snapshot before interruption")
                    require(candidate["cached_documents"] < total_documents
                            and cache_preserved_subset(old, candidate),
                            "Live cache witness is not a valid durable proper subset")
                    observed = candidate
                    # Never open SQLite while a writer is SIGSTOPped: it may
                    # hold a WAL coordination lock between protocol steps.
                    interrupted.stop_for_kill()
                    break
        require(observed is not None, "No independently committed new batch observed before deadline")
        worker_state = interrupted.kill_frozen()
        after_kill = snapshot(database)
        require(after_kill["active_tables"] == old["active_tables"],
                "Interrupted work replaced the active snapshot")
        require(old["cached_documents"] < after_kill["cached_documents"] < total_documents,
                "Interruption did not leave a durable proper subset")
        require(cache_preserved_subset(observed, after_kill),
                "SIGKILL lost or changed a previously witnessed committed vector")
        interruption = {
            "method": "Observe committed cache while writer runs; SIGSTOP owned service and launcher PGs; immediately SIGKILL both without SQLite reads while paused; inspect after termination",
            "last_observation_before_stop": observed, "after_sigkill": after_kill,
            "active_tables_unchanged": True, "committed_vectors_survived": True,
            "observed_cache_preserved_as_subset": True,
            "committed_documents_after_last_observation": after_kill["cached_documents"] - observed["cached_documents"],
            "service_state_after_kill": worker_state, "launcher_returncode": interrupted.process.returncode,
            "additional_durable_documents": after_kill["cached_documents"] - old["cached_documents"]}
        interruption_receipt = {
            "version": VERSION, "captured_at": datetime.now(timezone.utc).isoformat(),
            "zip_sha256": state["zip_sha256"], "revision": revision, "source_root": SOURCE_ROOT,
            "seed": {"tracked_source_files": ["src/flask/sessions.py"], "session": seed_session, "snapshot": old},
            "scope": {"total_documents": total_documents, "total_windows": total_windows},
            "interruption": interruption,
            "qualification_complete": False,
            "limits": "This receipt proves only the interruption checkpoint; resume and MCP checks follow."}
        # Preserve the independent durability witness before any optional client
        # verification, artifact copy, or subsequent launcher run can fail.
        write_json(output / "interruption.json", interruption_receipt)
        copy_artifacts(interrupted, output, "interrupted")
        interrupted_cache = next(event for event in progress_events(interrupted.session())
                                 if event["phase"] == "cache")
        require(interrupted_cache["cached_documents"] == old["cached_documents"]
                and interrupted_cache["total"] == total_documents,
                "Expanded launch did not begin from the seeded production cache")
        interruption["initial_progress_cache"] = interrupted_cache
        write_json(output / "interruption.json", interruption_receipt)
        state["resume_qualification"].update(index_database=str(database),
            killed_launcher_pid=interrupted.process.pid, killed_service_pid=interrupted.service["pid"],
            cached_after_interruption=after_kill["cached_documents"])
        write_json(state_path, state)
        reused_config = None
        for stage in ("resumed", "restart"):
            launcher = start(stage)
            launcher.prompt()
            startup = time.perf_counter() - launcher.started
            session = launcher.session()
            config = session / "mcp.json"
            if reused_config is None:
                reused_config = config
            mcp_output = output / f"{stage}-mcp-verification.json"
            command = [str(python), str(ROOT / "scripts/verify_beta_mcp.py"), "--config", str(reused_config),
                       "--repo", str(fixture), "--source-root", SOURCE_ROOT, "--output", str(mcp_output)]
            for query in QUERIES:
                command.extend(("--query", query))
            with (output / f"{stage}-mcp.log").open("xb") as stream:
                subprocess.run(command, cwd=app, env=environment, stdout=stream, stderr=subprocess.STDOUT,
                               check=True, timeout=300)
            mcp = json.loads(mcp_output.read_text())
            require(mcp["packages"]["semantic-search-engine"] == VERSION
                    and mcp["network_guard"]["outbound_attempts"] == 0,
                    "Generated MCP config failed version or guarded socket checks")
            marker = len(launcher.transcript)
            os.write(launcher.master, (QUERY + "\n").encode())
            launcher.prompt(marker, 120)
            require(b"open_session" in launcher.transcript[marker:]
                    and b"src/flask/sessions.py" in launcher.transcript[marker:], "Terminal query omitted expected saved implementation")
            marker = len(launcher.transcript)
            os.write(launcher.master, b":mcp\n")
            launcher.prompt(marker, 30)
            require(b'"mcpServers"' in launcher.transcript[marker:]
                    and b'"TIKTOKEN_CACHE_DIR"' in launcher.transcript[marker:], "Generated MCP config not shown")
            marker = len(launcher.transcript)
            os.write(launcher.master, b":doctor\n")
            launcher.prompt(marker, 30)
            saved = launcher.quit()
            require(saved["searches"] == 1 and saved["errors"] == 0 and saved["windows"] == total_windows,
                    "Unexpected completed terminal session counts")
            progress = progress_events(session)
            cache = next(event for event in progress if event["phase"] == "cache")
            cached = after_kill["cached_documents"] if stage == "resumed" else total_documents
            require(cache["cached_documents"] == cached and cache["pending_documents"] == total_documents - cached,
                    "Actual launcher cache reuse differs from independently observed durable work")
            embedding = [event for event in progress if event["phase"] == "embed"]
            if stage == "resumed":
                require(embedding and embedding[-1]["completed"] == total_documents - cached,
                        "Resume did not complete exactly the remaining document count")
            else:
                require(not embedding, "Restart re-inferred already committed source documents")
            copy_artifacts(launcher, output, stage)
            runs.append({"stage": stage, "startup_wall_s": startup, "cache": cache,
                         "session": saved, "generated_config_reused_after_restart": stage == "restart",
                         "terminal_query_verified": True, "mcp_source_and_budget_checks_passed": True})
            print(json.dumps({"stage": stage, "cache": cache, "shutdown": saved["shutdown"]}), flush=True)
        for name, arguments in (
            ("doctor-after-resume", ["--doctor", "--repo", str(fixture), "--source-root", SOURCE_ROOT]),
            ("feedback-after-resume", ["--feedback", "--output", str(output / "scripted-feedback.json"),
                                       "--attempts", "2", "--full", "2", "--interface", "mcp", "--workflow", "service_restart"]),
        ):
            with (output / f"{name}.log").open("xb") as stream:
                subprocess.run(["/bin/bash", "Get Started.command", *arguments], env=environment, cwd=app,
                               stdout=stream, stderr=subprocess.STDOUT, timeout=60, check=True)
        require(subprocess.check_output(["git", "-C", str(fixture), "rev-parse", "HEAD"], text=True).strip() == revision
                and not subprocess.check_output(["git", "-C", str(fixture), "status", "--porcelain"], text=True).strip(),
                "Saved public fixture changed during integration qualification")
        report = {"passed": True, "version": VERSION, "captured_at": datetime.now(timezone.utc).isoformat(),
                  "zip_sha256": state["zip_sha256"], "public_repo": "https://github.com/pallets/flask",
                  "revision": revision, "source_root": SOURCE_ROOT,
                  "unmodified_extracted_launcher_and_service": True, "inference_patches": False,
                  "seed": {"tracked_source_files": ["src/flask/sessions.py"], "session": seed_session, "snapshot": old},
                  "interruption": interruption,
                  "scope": {"total_documents": total_documents, "total_windows": total_windows},
                  "runs": runs, "support_commands_passed": True, "scripted_external_testers": 0,
                  "limits": ["Existing Mac; not an independent machine", "Existing known public queries; not new retrieval evidence",
                             "Only launcher-owned process groups were signalled", "MCP guard covers Python client/adapter, not resident backend",
                             "Descriptive wall times; no throughput or latency claim"]}
        write_json(output / "resume-integration.json", report)
        state["resume_qualification"]["passed"] = True
        write_json(state_path, state)
    finally:
        for launcher in reversed(launchers):
            launcher.close()


if __name__ == "__main__":
    main()
