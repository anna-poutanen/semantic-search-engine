#!/usr/bin/env python3
"""Freeze, then run a tiny native Qwen VS Code settings integration check.

Freeze requires completed coding/install/parity/MCP gates and reads metadata.
It never starts an editor, model, service, installer or hosted coding agent.
"""
from __future__ import annotations

import argparse
import contextvars
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import signal
import socket
import subprocess
import threading
import time
import uuid
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/vscode"
COUNTERS = ("requests", "attempted_pairs", "completed_pairs", "attempted_forwards", "completed_forwards")
CANCEL_SUFFIX = " Cancellation transport smoke."
DEFAULT_PYTHON = ROOT / ".cache/qwen-install-validation-2026-10-14/.venv/bin/python"
DEFAULT_CHECKPOINT = ROOT / ".cache/qwen-install-validation-2026-10-14/model"


def helper(name):
    spec = importlib.util.spec_from_file_location("vscode_native_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat()


def write_new(path, value, work):
    def public(item):
        if isinstance(item, str):
            return item.replace(str(work), "<WORK>").replace(str(ROOT), "<PROJECT>").replace(str(Path.home()), "<HOME>")
        if isinstance(item, dict):
            return {key: public(value) for key, value in item.items()}
        if isinstance(item, list):
            return [public(value) for value in item]
        return item
    with path.open("x") as stream:
        stream.write(json.dumps(public(value), indent=2, sort_keys=True, allow_nan=False) + "\n")


def publication_qualified(output, certificate):
    """A late signal invalidates its hash-bound journal, even after JSON writes."""
    journal = output / "signals.jsonl"
    return (certificate.get("checks_passed") is True
            and certificate.get("publication_rule") == "Empty actual signals.jsonl with exact artifact hash required"
            and journal.is_file() and journal.read_bytes() == b""
            and certificate.get("artifact_sha256", {}).get(journal.name) == sha(b""))


def coding_gate(path, *, driver_file=None):
    audit = json.loads(path.read_text())
    require(audit.get("checks_passed") is True and audit.get("inventory_complete") is True
            and audit.get("final_completion_receipt") is True and audit.get("recorded_attempts") == 18
            and audit.get("missing_attempts") == [], "Full18 independent coding certificate required before native editor work")
    if driver_file is not None:
        driver = json.loads(driver_file.read_text())["driver"]
        for identity, probe in ((driver["pid"], os.kill), (driver["process_group"], os.killpg)):
            try:
                probe(identity, 0)
            except ProcessLookupError:
                continue
            raise AssertionError("Owned coding driver must be gone before native editor work")
    return audit


def prerequisite_gates(paths, *, driver_file=None, python=DEFAULT_PYTHON, checkpoint=DEFAULT_CHECKPOINT):
    coding_gate(paths["coding"], driver_file=driver_file)
    install = json.loads(paths["install"].read_text())
    require(install.get("checks_passed") is True and install.get("model_calls") == 0
            and install.get("weights_loaded") is False and install.get("after", {}).get("ready") is True
            and install["after"].get("package_count") == 34
            and len(install["after"]["runtime"]["metadata"]["packages"]) == 34
            and install["after"]["checkpoint"]["weight_identity_verified"] is True,
            "Verified fresh isolated installation receipt required")
    invocation_path = install["after"]["runtime"]["python_invocation"].replace("<PROJECT>", str(ROOT)).replace("<HOME>", str(Path.home()))
    require(Path(invocation_path) == python,
            "Installation receipt must describe this exact fresh interpreter")
    parity = json.loads(paths["parity"].read_text())
    require(parity.get("checks_passed") is True and parity.get("exact_original_pairs") == 416
            and parity.get("tokenizer_input_hashes_exact") is True
            and parity.get("original_scalar_equality_exact") is True
            and parity.get("owned_group_gone") is True and parity.get("token_absent") is True,
            "Independent installed all416 parity/cleanup certificate required")
    mcp = json.loads(paths["mcp"].read_text())
    require(mcp.get("checks_passed") is True and mcp.get("study_qualified") is True
            and mcp.get("process_cleanup", {}).get("actual_auth_file_absent") is True
            and mcp["process_cleanup"].get("current_pid_and_group_absent") is True,
            "Independent installed native HTTP/MCP certificate required")
    def local(value):
        return Path(value.replace("<PROJECT>", str(ROOT)).replace("<HOME>", str(Path.home())))
    require(local(mcp["explicit_paths"]["reranker_python"]) == python
            and local(mcp["explicit_paths"]["checkpoint"]) == checkpoint, "Installed MCP validation paths differ")
    # Bind each certificate's named study artifacts, including its runtime plan.
    result = {}
    for name, path in paths.items():
        row = json.loads(path.read_text())
        bound = {str(path.relative_to(ROOT)): sha(path.read_bytes())}
        for relative, expected in row.get("artifact_sha256", {}).items():
            base = ROOT / "results/qwen-coding-workflows-2026-10-14" if name == "coding" else path.parent
            snapshot = row.get("global_input_snapshots", {}).get(relative)
            artifact = ((path.parent / snapshot["file"]) if snapshot else (base / relative)).resolve()
            require(not Path(relative).is_absolute() and ".." not in Path(relative).parts
                    and artifact.is_relative_to(ROOT) and artifact.is_file()
                    and sha(artifact.read_bytes()) == expected, "Prerequisite artifact changed: " + name + "/" + relative)
            bound[str(artifact.relative_to(ROOT))] = expected
        result[name] = bound
    installer_directory = paths["install"].parent
    outer = json.loads((installer_directory / "verification.json").read_text())
    require(outer.get("checks_passed") is True and all(outer["checks"].values()),
            "Completed full fresh installer preservation verification required")
    installer_plan = json.loads((installer_directory / "frozen-plan.json").read_text())
    require(ROOT / installer_plan["destinations"]["venv"] / "bin/python" == python
            and ROOT / installer_plan["destinations"]["checkpoint"] == checkpoint,
            "Fresh installer frozen destinations differ")
    for name in ("verification.json", "frozen-plan.json", "install-command.json", "verify-command.json", "verify.stdout.txt"):
        path = installer_directory / name
        require(path.is_file(), "Completed installer artifact missing: " + name)
        if name.endswith("-command.json"):
            command = json.loads(path.read_text())
            require(command.get("exit_code") == 0 and command.get("interrupted") in (None, False)
                    and command.get("timeout") is False, "Installer/verification command did not complete cleanly")
        result["install"][str(path.relative_to(ROOT))] = sha(path.read_bytes())
    for name in ("parity", "mcp"):
        plan_path = paths[name].parent / "frozen-plan.json"
        require(plan_path.is_file(), "Installed native gate's frozen plan required")
        require(sha(plan_path.read_bytes()) == json.loads(paths[name].read_text())["frozen_plan_sha256"],
                "Native gate frozen plan hash differs")
        # The native smoke records model file witnesses, which bind the installed
        # directory; parity's immutable planned invocation names its interpreter.
        invocation = paths[name].parent / "planned-invocation.json"
        if name == "parity":
            require(invocation.is_file(), "Installed parity invocation required")
            command = json.loads(invocation.read_text())["run_command"]
            require(command.count("--python") == command.count("--checkpoint") == 1
                    and ROOT / command[command.index("--python") + 1] == python
                    and ROOT / command[command.index("--checkpoint") + 1] == checkpoint,
                    "Installed parity invocation paths differ")
            verified = json.loads((paths[name].parent / "verification.json").read_text())
            for field in ("owner_start", "owner_stop"):
                actual = verified[field]
                require(local(actual["interpreter"]["invocation_path"]) == python
                        and local(actual["ready"]["provenance"]["model"]["directory"]) == checkpoint,
                        "Actual parity owner interpreter/checkpoint differ from fresh installation")
            result[name][str(invocation.relative_to(ROOT))] = sha(invocation.read_bytes())
    return result


def source_bindings():
    files = [Path(__file__), Path(__file__).with_name("smoke_qwen_mcp.py"),
             Path(__file__).with_name("qwen_workflow_process.py"), Path(__file__).with_name("qwen_workflow_evidence.py"),
             ROOT / "requirements-qwen-reranker.lock", *sorted((ROOT / "src/semantic_search").glob("*.py"))]
    for directory, pattern in (("src", "*.ts"), ("test/host", "*.ts"), ("scripts", "*.cjs")):
        files.extend(sorted((EXTENSION / directory).glob(pattern)))
    files.extend(EXTENSION / name for name in ("package.json", "package-lock.json", "tsconfig.json"))
    return {str(path.relative_to(ROOT)): sha(path.read_bytes()) for path in files}


def host_runtime():
    script = "const c=require('./scripts/host-config.cjs');process.stdout.write(JSON.stringify(c));"
    config = json.loads(subprocess.check_output(["node", "-e", script], cwd=EXTENSION, text=True))
    base = EXTENSION / ".cache/vscode-host" / ("vscode-darwin-arm64-" + config["version"])
    executable = base / "Visual Studio Code.app/Contents/MacOS/Code"
    require((base / "is-complete").is_file(), "Pinned editor must already be prepared; this wrapper installs nothing")
    require(sha(executable.read_bytes()) == config["expected_executable_sha256"], "Prepared editor executable differs")
    files = [executable, base / "is-complete", base / "Visual Studio Code.app/Contents/Resources/app/product.json",
             base / "Visual Studio Code.app/Contents/Resources/app/package.json"]
    files.extend(sorted((EXTENSION / "dist/src").glob("*.js*")))
    files.extend(sorted((EXTENSION / "dist/test/host").glob("*.js*")))
    files.extend(sorted((EXTENSION / "node_modules/@vscode/test-electron/out").glob("*.js")))
    files.extend([EXTENSION / "node_modules/@vscode/test-electron/package.json",
                  EXTENSION / "node_modules/typescript/package.json", EXTENSION / "node_modules/typescript/lib/_tsc.js"])
    product = json.loads(files[2].read_text())
    require(product["commit"] == config["commit"], "Prepared editor product commit differs")
    require(json.loads(files[3].read_text())["version"] == config["version"], "Prepared editor version differs")
    return {"files": {str(path.relative_to(ROOT)): sha(path.read_bytes()) for path in files},
            "node": subprocess.check_output(["node", "--version"], text=True).strip(),
            "npm": subprocess.check_output(["npm", "--version"], text=True).strip(), "config": config}


def verify_files(bindings):
    for name, expected in bindings.items():
        path = ROOT / name
        require(not Path(name).is_absolute() and ".." not in Path(name).parts
                and path.is_file() and sha(path.read_bytes()) == expected, "Frozen file changed: " + name)


def freeze(output, work, python, checkpoint, gates):
    require(not output.exists() and not work.exists(), "Fresh editor output/work paths required; no overwrite/resume")
    bound_gates = prerequisite_gates(gates, driver_file=ROOT / ".cache/qwen-coding-workflows-2026-10-14/driver-observed.json",
                                    python=python, checkpoint=checkpoint)
    smoke = helper("smoke_qwen_mcp")
    bindings = source_bindings()
    committed = smoke.committed_bindings(bindings)
    runtime = smoke.runtime_plan(python, checkpoint)
    host = host_runtime()
    work.mkdir(parents=True, mode=0o700)
    fixture = json.loads(subprocess.check_output(["node", "scripts/host-fixture.cjs", str(work / "repo"), "--mixed"], cwd=EXTENSION, text=True))
    require(fixture["functions"] == 5 and fixture["model_loaded"] is False and fixture["service_started"] is False, "Mixed authored fixture differs")
    output.mkdir(parents=True)
    plan = {"version": 1, "classification": "Tiny actual Development Host Qwen workspace-settings integration; no quality/performance/billing claim",
            "frozen_at_utc": utc(), "source_commit": committed, "source_sha256": bindings,
            "gates": bound_gates, "gate_paths": {key: str(path.relative_to(ROOT)) for key, path in gates.items()},
            "runtime": runtime, "host_runtime": host, "fixture": fixture,
            "paths": {"python": str(python), "checkpoint": str(checkpoint), "work": str(work)},
            "native_schedule": {"warmup": 1, "saved_qwen": 5, "dirty_qwen": 5, "all_other_searches": 0},
            "expected_observed_embedding_entries": {"initial_document_texts": 5, "dirty_document_texts": 1,
                "deterministic_successful_search_queries": 7, "cancelled_search_query_entries": [0, 1],
                "cancelled_search_successes": [0, 1], "search_attempts": 8},
            "settings": {"retrieval": "hybrid", "rerank": True, "rerank_provider": "qwen", "candidates": 24,
                         "max_results": 5, "source_budget": 12000, "token_budget": 2000, "include_dependencies": False},
            "model_calls_in_freeze": 0, "editor_launched_in_freeze": False}
    write_new(output / "frozen-plan.json", plan, work)
    return plan


def verify_native(rows, observations, counts, *, builder):
    """Reconstruct literal tokenizer inputs and reconcile every actual event."""
    expected = []
    from semantic_search.qwen_reranker_core import binary_score
    from semantic_search.qwen_worker import WARMUP
    qwen = [item for item in observations if item["response"].get("rerank") is True]
    require(len(qwen) == 2 and any(item["settings"].get("overlays") for item in qwen)
            and any(not item["settings"].get("overlays") for item in qwen), "One saved and one dirty Qwen search required")
    for item in qwen:
        response = item["response"]
        require(response["rerank_provider"] == "qwen" and response["candidate_windows"] == 5
                and response["retrieval_candidate_windows"] == 5 and response["reranker_batch_size"] == 1,
                "Actual available fixture pool/provider differs")
        sources = {row["id"]: row["source"] for row in response["results"]}
        require(len(sources) == len(response["reranker_receipts"]) == 5
                and all(not row["truncated"] for row in response["results"]), "All five literal fixture windows required")
        require(all(row["qwen_score"] == response["reranker_receipts"][row["id"]]["result"]["logit_difference"]
                    and row["h2o_score"] is None for row in response["results"]), "Returned window scores differ from actual Qwen receipts")
        for identifier, receipt in response["reranker_receipts"].items():
            built = builder.build(item["query"], sources[identifier])
            result = receipt["result"]
            require(all(type(result.get(key)) is float and math.isfinite(result[key]) for key in
                ("yes_logit", "no_logit", "logit_difference", "yes_rank_signal")), "Invalid actual native score scalars")
            require({key: result[key] for key in ("yes_logit", "no_logit", "logit_difference", "yes_rank_signal")}
                    == binary_score(result["yes_logit"], result["no_logit"]), "Actual native binary arithmetic differs")
            for key, value in built.items():
                if key not in ("input_ids", "source"):
                    require(result[key] == value, "Literal scoring input differs: " + key)
            expected.append((receipt, sha(json.dumps(built["input_ids"], separators=(",", ":")).encode())))
    expected.sort(key=lambda item: item[0]["counts"]["completed_forwards"])
    require(all(type(counts.get(key)) is int and counts[key] == 11 for key in COUNTERS), "Actual native counters must reconcile 10 measured plus one warmup")
    require(len(rows) == 22 and [(row.get("event"), row.get("forward_number")) for row in rows]
            == [(event, number) for number in range(1, 12) for event in ("started", "completed")], "Native event inventory/order differs")
    require(all(row.get("warmup") is (index < 2) for index, row in enumerate(rows)), "Warmup attribution differs")
    warmup = builder.build(WARMUP["query"], WARMUP["source"])
    warmup_hash = sha(json.dumps(warmup["input_ids"], separators=(",", ":")).encode())
    for row in rows[:2]:
        require(row.get("request_id") == "generic-warmup" and row.get("batch_size") == 1
                and row.get("input_ids_sha256") == warmup_hash, "Actual warmup tokenizer/request differs")
        require(all(row.get(key) == warmup[key] for key in ("query_sha256", "source_sha256", "delivered_sha256", "total_tokens")),
                "Actual warmup literal input differs")
    for number, (receipt, input_hash) in enumerate(expected, start=2):
        pair = rows[(number - 1) * 2:number * 2]
        require(all(type(receipt["counts"].get(key)) is int and receipt["counts"][key] == number for key in COUNTERS),
                "Returned cumulative native counters differ")
        result = receipt["result"]
        for row in pair:
            require(row["request_id"] == receipt["request_id"] and row["batch_size"] == receipt["batch_size"] == 1
                    and row["input_ids_sha256"] == input_hash, "Actual native request/tokenizer identity differs")
            for key in ("query_sha256", "source_sha256", "delivered_sha256", "total_tokens"):
                require(row[key] == result[key], "Actual ledger/input identity differs: " + key)
        require(pair[1]["score"] == {key: result[key] for key in ("yes_logit", "no_logit", "logit_difference", "yes_rank_signal")}, "Actual native score differs from HTTP receipt")
    return {"started": 11, "completed": 11, "measured": 10, "warmup": 1, "literal_pairs_reconstructed": 10, "tokenizer_input_hashes_exact": True}


def verify_response(query, settings, response, repo):
    from semantic_search.context import compact_result, encoding, serialize_context
    from semantic_search.source import physical_lines
    require(response["query"] == query and response["retrieval"] == settings.get("retrieval", "dense")
            and response["rerank"] is settings.get("rerank", False), "Actual HTTP request settings echo differs")
    token_count = len(encoding().encode(serialize_context(compact_result(response)), disallowed_special=()))
    require(token_count == response["token_usage"]["tokens"] and token_count <= settings["token_budget"]
            and response["token_usage"]["budget"] == settings["token_budget"], "Actual compact token count/cap differs")
    require(len(response["results"]) <= settings["max_results"] and sum(len(row["source"]) for row in response["results"])
            == response["total_source_chars"] <= settings["source_budget"], "Actual result/source cap differs")
    overrides = {row["path"]: row["text"] for row in settings.get("overlays", [])}
    for row in response["results"]:
        require(row["path"] in ("src/inventory.py", "src/decorate.js", "src/delta.ts"), "Unexpected fixture source path")
        literal = overrides.get(row["path"])
        if literal is None:
            literal = (repo / row["path"]).read_bytes().decode("utf-8")
        require(row["source"] == "".join(physical_lines(literal)[row["start_line"] - 1:row["end_line"]])
                and row["source_hash"] == sha(row["source"].encode()), "Actual literal saved/dirty source differs")
    return token_count


class RequestObservedEmbedder:
    """Observe actual API entry/exit and bind it to the active request context."""
    def __init__(self, inner, request_context, entries):
        self.inner, self.request_context, self.entries = inner, request_context, entries
        self.query_calls = self.document_calls = self.document_texts = 0

    @property
    def metadata(self):
        return self.inner.metadata

    def _entry(self, kind, payload):
        row = {"number": len(self.entries) + 1, "kind": kind,
               "request_id": self.request_context.get(), "status": "entered", "entered_at_utc": utc(), **payload}
        self.entries.append(row)
        return row

    def _call(self, row, action):
        try:
            value = action()
            row["status"] = "completed"
            return value
        except BaseException as error:
            row.update(status="failed", error_type=type(error).__name__, error=str(error))
            raise
        finally:
            row["finished_at_utc"] = utc()

    def embed_query(self, query):
        self.query_calls += 1
        row = self._entry("query", {"query_sha256": sha(query.encode())})
        return self._call(row, lambda: self.inner.embed_query(query))

    def embed_documents(self, texts, *, cancel_check=None):
        self.document_calls += 1
        self.document_texts += len(texts)
        row = self._entry("documents", {"document_texts": len(texts),
            "document_input_sha256": [sha(text.encode()) for text in texts]})
        return self._call(row, lambda: self.inner.embed_documents(texts, cancel_check=cancel_check))

    def counts(self):
        return {"query_calls": self.query_calls, "document_calls": self.document_calls,
                "document_texts": self.document_texts}

    def close(self):
        self.inner.close()


def verify_embedding_schedule(attempts, entries, observations, counts):
    """Reconcile observed cancellation stage without requiring one race outcome."""
    require(len(attempts) == 8 and len({item["request_id"] for item in attempts}) == 8,
            "Eight distinct actual search attempts required")
    require([item["number"] for item in entries] == list(range(1, len(entries) + 1)),
            "Ordered actual embedding API entries required")
    require(all(item["status"] == "completed" for item in entries), "Embedding API entries did not complete cleanly")
    cancelled = [item for item in attempts if item["query"].endswith(CANCEL_SUFFIX)]
    deterministic = [item for item in attempts if not item["query"].endswith(CANCEL_SUFFIX)]
    require(len(cancelled) == 1 and len(deterministic) == 7
            and all(item["status"] == "completed" for item in deterministic),
            "Seven deterministic successful searches and one cancellation attempt required")
    query_entries = [item for item in entries if item["kind"] == "query"]
    by_request = {item["request_id"]: [] for item in attempts}
    for entry in query_entries:
        require(entry["request_id"] in by_request, "Query entry is outside the observed search inventory")
        by_request[entry["request_id"]].append(entry)
    for attempt in attempts:
        actual = by_request[attempt["request_id"]]
        require(all(entry["query_sha256"] == sha(attempt["query"].encode()) for entry in actual),
                "Actual embedding query differs from that search")
        if attempt in deterministic:
            require(len(actual) == 1, "Deterministic search did not enter query embedding exactly once")
    cancel = cancelled[0]
    cancel_entries = len(by_request[cancel["request_id"]])
    require(cancel_entries in (0, 1) and cancel["status"] in ("completed", "cancelled")
            and (cancel["status"] != "completed" or cancel_entries == 1),
            "Cancellation must reflect its observed before-entry/after-entry/completed stage")
    completed = {item["request_id"] for item in attempts if item["status"] == "completed"}
    require(len(observations) == len(completed) and {item["request_id"] for item in observations} == completed,
            "Successful response observations differ from actual completed requests")
    documents = [entry for entry in entries if entry["kind"] == "documents"]
    initial = [entry for entry in documents if entry["request_id"] is None]
    dirty = [item for item in attempts if item["settings"].get("overlays")]
    require(len(initial) == 1 and initial[0]["document_texts"] == 5
            and len(dirty) == 1 and dirty[0]["status"] == "completed", "Initial and dirty indexing inventory differs")
    changed = [entry for entry in documents if entry["request_id"] == dirty[0]["request_id"]]
    require(len(documents) == 2 and len(changed) == 1 and changed[0]["document_texts"] == 1,
            "Dirty search did not enter exactly one changed document embedding")
    require(counts == {"query_calls": len(query_entries), "document_calls": len(documents), "document_texts": 6},
            "Actual embedding entries and API counters differ")
    return {"search_attempts": 8, "deterministic_successful_searches": 7,
            "cancelled_request_status": cancel["status"], "cancelled_request_query_entries": cancel_entries,
            "cancelled_request_successful_responses": int(cancel["status"] == "completed"),
            "observed_query_entries": len(query_entries), "successful_responses": len(observations),
            "observed_document_texts": 6, "count_kind": "Actual API entries; no claim of GPU-forward cancellation"}


def resolve_public(value, work):
    return value.replace("<WORK>", str(work)).replace("<PROJECT>", str(ROOT)).replace("<HOME>", str(Path.home()))


def group_gone(pid):
    try:
        os.killpg(pid, 0)
        return False
    except ProcessLookupError:
        return True


def listener_closed(url):
    parsed = urlsplit(url)
    require(parsed.hostname in ("localhost", "127.0.0.1") and parsed.port is not None, "Only known local listeners are probed")
    try:
        with socket.create_connection(("127.0.0.1", parsed.port), timeout=.3):
            return False
    except ConnectionRefusedError:
        return True


def stop_editor(ownership_file, nonce, repo):
    """Only the durably nonce-bound detached group created by this invocation."""
    record = json.loads(ownership_file.read_text())
    require(record.get("nonce") == nonce and type(record.get("pid")) is int and record["pid"] > 1
            and record.get("process_group") == record["pid"] and record.get("detached") is True,
            "Editor ownership identity differs; no unowned group is signalled")
    require(str(repo) in record.get("argv", []) and any(arg == "--extensionTestsPath=" + str(EXTENSION / "dist/test/host/index.js")
            for arg in record["argv"]), "Editor invocation is not this known fixture/test")
    config = json.loads(subprocess.check_output(["node", "-e", "process.stdout.write(JSON.stringify(require('./scripts/host-config.cjs')));"], cwd=EXTENSION, text=True))
    expected = EXTENSION / ".cache/vscode-host" / ("vscode-darwin-arm64-" + config["version"]) / "Visual Studio Code.app/Contents/MacOS/Code"
    require(record["executable"] == str(expected), "Editor executable identity differs; no unowned group is signalled")
    receipt = {"owned_group": record["pid"], "owned_group_stopped": False, "forced_kill": False, "cleanup_clean": False}

    def alive():
        try:
            os.killpg(record["pid"], 0)
            return True
        except ProcessLookupError:
            return False

    def wait(seconds):
        end = time.monotonic() + seconds
        while alive() and time.monotonic() < end:
            time.sleep(.05)
        return not alive()

    if alive():
        try:
            os.killpg(record["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass
        if not wait(5):
            receipt["forced_kill"] = True
            try:
                os.killpg(record["pid"], signal.SIGKILL)
            except ProcessLookupError:
                pass
            wait(5)
    receipt["owned_group_stopped"] = not alive()
    stop = Path(str(ownership_file) + ".stop.json")
    if stop.is_file():
        observed = json.loads(stop.read_text())
        require(observed.get("pid") == record["pid"], "Editor stop receipt identity differs")
        receipt["launcher_stop"] = observed
        receipt["cleanup_clean"] = bool(receipt["owned_group_stopped"] and not receipt["forced_kill"]
            and observed.get("completed_cleanly") is True and observed.get("cleanup_clean") is True)
    profile = next((Path(arg.split("=", 1)[1]).parent for arg in record["argv"] if arg.startswith("--user-data-dir=")), None)
    receipt["owned_profile_removed"] = profile is not None and not profile.exists()
    receipt["cleanup_clean"] = receipt["cleanup_clean"] and receipt["owned_profile_removed"]
    return receipt


def run(output, work, *, timeout=360):
    from semantic_search.backend import SearchBackend
    from semantic_search.embeddings import QwenEmbedder
    from semantic_search.qwen_reranker import QwenRerankerProcess
    from semantic_search.qwen_reranker_core import PairBuilder
    from semantic_search.server import create_server
    from tokenizers import Tokenizer

    smoke, process = helper("smoke_qwen_mcp"), helper("qwen_workflow_process")
    require(not (output / "run-start.json").exists(), "No editor smoke retry/resume")
    require({path.name for path in output.iterdir()} == {"frozen-plan.json"}, "Fresh frozen editor output must contain only its plan")
    raw_plan = (output / "frozen-plan.json").read_bytes()
    plan = json.loads(raw_plan)
    verify_files(plan["source_sha256"])
    for bound in plan["gates"].values():
        verify_files(bound)
    coding = plan["gate_paths"]["coding"]
    coding_gate(ROOT / coding, driver_file=ROOT / ".cache/qwen-coding-workflows-2026-10-14/driver-observed.json")
    smoke.committed_bindings({str((output / "frozen-plan.json").relative_to(ROOT)): sha(raw_plan), **plan["source_sha256"]})
    python = Path(resolve_public(plan["paths"]["python"], work))
    checkpoint = Path(resolve_public(plan["paths"]["checkpoint"], work))
    require(smoke.runtime_plan(python, checkpoint) == plan["runtime"]
            and smoke.publication(host_runtime(), work) == plan["host_runtime"], "Frozen native/editor runtime differs")
    require(work == Path(resolve_public(plan["paths"]["work"], work)), "Frozen work directory differs")
    for name, expected in plan["fixture"]["source_files"].items():
        require(sha((work / "repo" / name).read_bytes()) == expected, "Frozen literal fixture changed")
    write_new(output / "run-start.json", {"started_at_utc": utc(), "frozen_plan_sha256": sha(raw_plan)}, work)
    observations, search_attempts, embedding_entries = [], [], []
    request_context = contextvars.ContextVar("native-editor-search-request", default=None)
    result = {"checks_passed": False, "native_inference": True, "editor_activation": True, "started_at_utc": utc()}
    owner = embedder = backend = server = thread = child = None
    previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    nonce = uuid.uuid4().hex
    ownership_file = work / "editor-ownership.json"
    interrupts = []
    cleaning = False
    launching = False
    signal_fd = os.open(output / "signals.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND, 0o600)

    def interrupt(number, frame):
        os.write(signal_fd, (json.dumps({"signal": signal.Signals(number).name, "received_at_utc": utc()}, separators=(",", ":")) + "\n").encode())
        os.fsync(signal_fd)
        interrupts.append(signal.Signals(number).name)
        result["checks_passed"] = False
        if not cleaning and not launching and len(interrupts) == 1:
            raise KeyboardInterrupt

    for number in previous:
        signal.signal(number, interrupt)
    try:
        owner = QwenRerankerProcess(python=python, checkpoint=checkpoint, log_dir=work / "qwen")
        owner.start()
        embedder = RequestObservedEmbedder(QwenEmbedder(progress=False), request_context, embedding_entries)

        class ObservedBackend(SearchBackend):
            def search(self, query, **settings):
                before = embedder.counts()
                request_id = settings.get("request_id")
                require(isinstance(request_id, str) and request_id, "Observed editor searches require a request identity")
                attempt = {"request_id": request_id, "query": query, "settings": settings,
                           "status": "started", "started_at_utc": utc(), "embedding_before": before}
                search_attempts.append(attempt)
                context = request_context.set(request_id)
                try:
                    response = super().search(query, **settings)
                    actual_tokens = verify_response(query, settings, response, work / "repo")
                    observations.append({"request_id": request_id, "query": query, "settings": settings, "response": response,
                        "embedding_before": before, "embedding_after": embedder.counts(), "actual_compact_tokens": actual_tokens})
                    attempt["status"] = "completed"
                    return response
                except BaseException as error:
                    from semantic_search.cancellation import SearchCancelled
                    attempt.update(status="cancelled" if isinstance(error, SearchCancelled) else "failed",
                                   error_type=type(error).__name__, error=str(error))
                    raise
                finally:
                    attempt["embedding_after"] = embedder.counts()
                    attempt["finished_at_utc"] = utc()
                    request_context.reset(context)

        backend = ObservedBackend(work / "repo", "src", work / "index.sqlite3", embedder=embedder, qwen_client=owner.client)
        backend.refresh()
        result["initial_status"] = backend.status()
        result["initial_embedding_counts"] = embedder.counts()
        require(result["initial_embedding_counts"]["document_texts"] == 5 and backend.status()["windows"] == 5, "Initial mixed fixture indexing differs")
        server = create_server(backend, port=0)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .05})
        thread.start()
        host_path = output / "host.json"
        command = ["npm", "run", "host:smoke", "--", "--run", "--qwen-reranker", "--repo", str(work / "repo"),
                   "--url", f"http://127.0.0.1:{server.server_address[1]}", "--output", str(host_path)]
        result["host_command"] = command
        environment = {key: value for key, value in os.environ.items() if not key.startswith("VSCODE_")
                       and key not in ("NODE_OPTIONS", "ELECTRON_RUN_AS_NODE")}
        environment.update(SEMANTIC_SEARCH_HOST_OWNERSHIP_FILE=str(ownership_file), SEMANTIC_SEARCH_HOST_OWNERSHIP_NONCE=nonce)
        with (work / "host.log").open("xb") as log:
            launching = True
            try:
                child = subprocess.Popen(command, cwd=EXTENSION, env=environment, start_new_session=True, stdout=log, stderr=subprocess.STDOUT)
            finally:
                launching = False
            if interrupts:
                raise KeyboardInterrupt
            result["host_exit_code"] = child.wait(timeout=timeout)
        require(result["host_exit_code"] == 0, "Actual editor fixture failed; retained host receipt/private log")
        host = json.loads(host_path.read_text())
        require(host.get("passed") is True and host.get("host", {}).get("qwen_reranker", {}).get("passed") is True,
                "Actual editor workspace-settings checks did not pass")
        result["final_status"] = backend.status()
        result["embedding_counts"] = embedder.counts()
        result["embedding_schedule"] = verify_embedding_schedule(search_attempts, embedding_entries, observations, embedder.counts())
        dirty = [item for item in observations if item["settings"].get("overlays")]
        require(len(dirty) == 1 and dirty[0]["embedding_after"]["document_texts"] - dirty[0]["embedding_before"]["document_texts"] == 1,
                "Dirty request did not embed exactly one actual changed window")
        require(result["initial_status"]["source_manifest"] == result["final_status"]["source_manifest"]
                and result["final_status"]["windows"] == 5, "Saved persistent index scope changed during dirty editor search")
        result["native_final_status"] = owner.status()
        ledger = (owner.directory / "forwards.jsonl").read_bytes()
        result["native_ledger_sha256"] = sha(ledger)
        result["native_ledger"] = verify_native([json.loads(line) for line in ledger.splitlines()], observations,
            result["native_final_status"]["counts"], builder=PairBuilder(Tokenizer.from_file(str(checkpoint / "tokenizer.json"))))
        result["checks_passed"] = True
    except BaseException as error:
        result["failure"] = {"error_type": type(error).__name__, "error": str(error)}
    finally:
        cleaning = True
        errors = []

        def cleanup(name, action):
            try:
                action()
            except BaseException as error:
                errors.append({"action": name, "error_type": type(error).__name__, "error": str(error)})

        if child is not None:
            result["launcher_cleanup"] = process.stop_owned(child)
        if ownership_file.is_file():
            cleanup("detached_editor_cleanup", lambda: result.update(editor_cleanup=stop_editor(ownership_file, nonce, work / "repo")))
        if server is not None:
            if thread is not None and thread.is_alive():
                cleanup("http_shutdown", server.shutdown)
            cleanup("http_close", server.server_close)
            if thread is not None:
                cleanup("http_join", lambda: thread.join(timeout=5))
                result["http_thread_stopped"] = not thread.is_alive()
            cleanup("actual_http_listener", lambda: result.update(actual_http_listener_closed=listener_closed(f"http://127.0.0.1:{server.server_address[1]}")))
        elif backend is not None:
            cleanup("backend_close", backend.close)
        elif embedder is not None:
            cleanup("embedder_close", embedder.close)
        if owner is not None:
            cleanup("owner_close", owner.close)
            result["owner"] = owner.receipt
            if owner.directory is not None and (owner.directory / "forwards.jsonl").is_file():
                def preserve_ledger():
                    final_raw = (owner.directory / "forwards.jsonl").read_bytes()
                    result["native_private_public_ledger_identical"] = sha(final_raw) == result.get("native_ledger_sha256")
                    (output / "native-forwards.jsonl.gz").write_bytes(gzip.compress(final_raw, mtime=0))
                cleanup("public_ledger", preserve_ledger)
                cleanup("actual_owner_group", lambda: result.update(actual_owner_group_gone=group_gone(owner.receipt["owned_group"])))
                result["actual_auth_token_absent"] = not (owner.directory / "token.secret").exists()
                if owner.receipt.get("ready", {}).get("url"):
                    cleanup("actual_owner_listener", lambda: result.update(actual_owner_listener_closed=listener_closed(owner.receipt["ready"]["url"])))
        result["cleanup_errors"], result["interrupts"] = errors, interrupts
    try:
        result["observations"] = observations
        result["search_attempts"] = search_attempts
        result["embedding_entries"] = embedding_entries
        result["source_stable"] = plan["source_sha256"] == source_bindings()
        result["host_runtime_stable"] = smoke.publication(host_runtime(), work) == plan["host_runtime"]
        result["lifecycle_passed"] = owner is not None and smoke.clean_lifecycle(owner.receipt, result.get("native_final_status"), result.get("http_thread_stopped"))
        result["checks_passed"] = bool(result["checks_passed"] and result["source_stable"] and result["host_runtime_stable"] and result["lifecycle_passed"]
            and result.get("launcher_cleanup", {}).get("owned_group_stopped") is True
            and result.get("editor_cleanup", {}).get("cleanup_clean") is True
            and result.get("actual_owner_group_gone") is True and result.get("actual_auth_token_absent") is True
            and result.get("actual_owner_listener_closed") is True and result.get("native_private_public_ledger_identical") is True
            and result.get("actual_http_listener_closed") is True
            and not result["cleanup_errors"] and not interrupts)
        result["finished_at_utc"] = utc()
        write_new(output / "results.json", result, work)
        artifacts = {path.name: sha(path.read_bytes()) for path in output.iterdir() if path.is_file()}
        certificate = {"checks_passed": result["checks_passed"] and not interrupts, "classification": plan["classification"],
                       "source_sha256": plan["source_sha256"], "artifact_sha256": artifacts, "finished_at_utc": utc(),
                       "publication_rule": "Empty actual signals.jsonl with exact artifact hash required"}
        write_new(output / "verification.json", certificate, work)
        if not publication_qualified(output, certificate):
            certificate["checks_passed"] = False
        return certificate
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
        os.close(signal_fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--reranker-python", type=Path, default=DEFAULT_PYTHON)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--coding-audit", type=Path)
    parser.add_argument("--setup-receipt", type=Path)
    parser.add_argument("--parity-audit", type=Path)
    parser.add_argument("--mcp-audit", type=Path)
    parser.add_argument("--timeout", type=float, default=360)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze", action="store_true")
    mode.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)
    output, work = args.output.resolve(), args.work_dir.resolve()
    require(output.is_relative_to(ROOT / "results") and work.is_relative_to(ROOT / ".cache"), "Output/work must be in project results/ignored .cache")
    require(math.isfinite(args.timeout) and 0 < args.timeout <= 600, "Invalid owned host timeout")
    if args.freeze:
        paths = {"coding": args.coding_audit, "install": args.setup_receipt, "parity": args.parity_audit, "mcp": args.mcp_audit}
        require(all(path is not None for path in paths.values()), "All four completed gate paths are required for freeze")
        freeze(output, work, args.reranker_python.absolute(), args.checkpoint.resolve(), {key: path.resolve() for key, path in paths.items()})
        print("Frozen native editor plan; no models or editor started. Commit it before --run.")
    else:
        certificate = run(output, work, timeout=args.timeout)
        print(json.dumps({"checks_passed": certificate["checks_passed"], "output": str(output)}))
        return 0 if certificate["checks_passed"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
