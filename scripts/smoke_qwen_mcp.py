#!/usr/bin/env python3
"""Freeze, then explicitly run a native Qwen HTTP/MCP saved-edit smoke.

No model is loaded by --freeze or importing this module. The run is a toy
integration check, not retrieval quality, coding accuracy or token savings.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from semantic_search.backend import SearchBackend
from semantic_search.client import SearchClient
from semantic_search.context import encoding, serialize_context
from semantic_search.embeddings import QwenEmbedder
from semantic_search.qwen_reranker import QwenRerankerProcess
from semantic_search.server import create_server
from semantic_search.source import physical_lines

ROOT = Path(__file__).resolve().parents[1]
QUERY = "Calculate an exponential retry delay with a maximum wait."
SOURCE = '''import json

def normalize_path(path):
    return path.strip().rstrip("/")

def backoff_delay(attempt):
    return min(2 ** attempt, 60)

def decode_response(data):
    return json.loads(data)
''' + "".join(f"\ndef format_noise_{index}(value):\n    return str(value) + '-{index}'\n" for index in range(58))
EDITED = SOURCE.replace("attempt, 60", "attempt, 90")
DEFAULTS = {"retrieval": "hybrid", "rerank": True, "rerank_provider": "qwen", "candidates": 24}
CAPS = {"max_results": 8, "source_budget": 12000, "token_budget": 4096, "include_dependencies": True}
PHASES = ("http_before", "mcp_before", "http_after", "mcp_after", "mcp_explicit_disable")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition, detail):
    if not condition:
        raise AssertionError(detail)


def utc():
    return datetime.now(timezone.utc).isoformat()


def publication(value, work):
    if isinstance(value, dict):
        return {key: publication(item, work) for key, item in value.items()}
    if isinstance(value, list):
        return [publication(item, work) for item in value]
    if isinstance(value, str):
        return value.replace(str(work), "<WORK>").replace(str(ROOT), "<PROJECT>").replace(str(Path.home()), "<HOME>")
    return value


def write_new(path, value, work):
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(publication(value, work), ensure_ascii=False, sort_keys=True, indent=2,
                                allow_nan=False) + "\n")


def source_bindings():
    paths = [Path(__file__).resolve(), *sorted((ROOT / "src/semantic_search").glob("*.py"))]
    return {str(path.relative_to(ROOT)): sha(path.read_bytes()) for path in paths}


def committed_bindings(bindings):
    """Verify current frozen bytes are committed, without assuming HEAD stays fixed."""
    for relative, expected in bindings.items():
        require(not Path(relative).is_absolute() and ".." not in Path(relative).parts,
                "Unsafe committed source path")
        try:
            committed = subprocess.check_output(["git", "show", "HEAD:" + relative], cwd=ROOT,
                                                stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as error:
            raise AssertionError("Frozen file must be committed: " + relative) from error
        require(sha(committed) == expected, "Committed frozen bytes differ: " + relative)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def runtime_plan(python, checkpoint):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONDONTWRITEBYTECODE="1")
    command = [str(python), "-B", "-m", "semantic_search.qwen_worker", "--preflight", "--checkpoint", str(checkpoint)]
    result = json.loads(subprocess.check_output(command, cwd=ROOT, env=env, text=True, timeout=30))
    require(not result["runtime"]["errors"] and result["model_calls"] == 0
            and result["weights_loaded"] is False, "Native metadata preflight failed")
    return {"runtime": {key: result["runtime"][key] for key in
            ("packages", "qwen3_source_sha256", "supporting_source_sha256")},
            "model_id": result["checkpoint"]["model_id"], "revision": result["checkpoint"]["revision"],
            "checkpoint_files": result["checkpoint"]["files"],
            "embedding": QwenEmbedder(progress=False).metadata,
            "main_packages": {name: importlib.metadata.version(name) for name in
                              ("mcp", "mcp-types", "anyio", "tiktoken", "numpy", "sqlite-vec")}}


def freeze(output, work, python, checkpoint, *, retrieval="hybrid", runtime_override=None):
    require(not output.exists() and not work.exists(), "No overwrite/resume of an attempted smoke")
    require(retrieval in ("dense", "hybrid"), "Unknown smoke retrieval family")
    bindings = source_bindings()
    source_commit = committed_bindings(bindings) if runtime_override is None else None
    runtime = runtime_override if runtime_override is not None else runtime_plan(python, checkpoint)
    output.mkdir(parents=True)
    work.mkdir(parents=True, mode=0o700)
    repo = work / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src/sample.py").write_text(SOURCE)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "src/sample.py"], check=True)
    # Tracked fixture membership needs no commit; no project or fixture commit is created.
    defaults = dict(DEFAULTS, retrieval=retrieval)
    plan = {"kind": "qwen_native_http_mcp_saved_edit_smoke", "version": 1, "frozen_at_utc": utc(),
            "source_sha256": bindings, "source_commit": source_commit, "runtime": runtime,
            "fixture": {"source_root": "src", "path": "src/sample.py", "windows": 61,
                        "initial_sha256": sha(SOURCE.encode()), "edited_sha256": sha(EDITED.encode()),
                        "tracked_files": ["src/sample.py"], "query": QUERY, "target_symbol": "backoff_delay"},
            "defaults": defaults, "caps": CAPS, "phases": list(PHASES),
            "planned_native_forwards": {"warmup": 1, "http_before": 24, "mcp_before": 24,
                                        "http_after": 24, "mcp_after": 24, "mcp_explicit_disable": 0},
            "semantic_quality_measurement": False, "paid_token_measurement": False,
            "cpu_injected": runtime_override is not None}
    write_new(output / "frozen-plan.json", plan, work)
    return plan


class ObservedEmbedder:
    """Counts actual production API entries without caching query/document values."""
    def __init__(self, inner):
        self.inner = inner
        self.query_calls = self.document_calls = self.document_texts = 0
    @property
    def metadata(self):
        return self.inner.metadata
    def embed_query(self, query):
        self.query_calls += 1
        return self.inner.embed_query(query)
    def embed_documents(self, texts, *, cancel_check=None):
        self.document_calls += 1
        self.document_texts += len(texts)
        return self.inner.embed_documents(texts, cancel_check=cancel_check)
    def counts(self):
        return {"query_calls": self.query_calls, "document_calls": self.document_calls,
                "document_texts": self.document_texts}
    def close(self):
        self.inner.close()


def verify_context(envelope, text, source):
    require(envelope["query"] == QUERY and envelope["match_status"] == "candidates", "Wrong query/status")
    require(text == serialize_context(envelope), "MCP text differs from compact structured content")
    actual = len(encoding().encode(text, disallowed_special=()))
    require(envelope["token_usage"]["tokens"] == actual and actual <= CAPS["token_budget"]
            and envelope["token_usage"]["budget"] == CAPS["token_budget"], "Incorrect actual token cap/count")
    require(len(envelope["results"]) <= CAPS["max_results"]
            and sum(len(row["source"]) for row in envelope["results"]) <= CAPS["source_budget"], "Result/source cap exceeded")
    target = None
    lines = physical_lines(source)
    for row in envelope["results"]:
        require(row["path"] == "src/sample.py", "Fixture excerpt escaped source scope")
        require(row["source"] == "".join(lines[row["start_line"] - 1:row["end_line"]]), "Excerpt differs from saved source")
        require(row["source_hash"] == sha(row["source"].encode()), "Excerpt hash mismatch")
        if row["symbol"] == "backoff_delay":
            target = row
    require(target is not None and not target["truncated"], "Target toy implementation missing or clipped")
    return target


def verify_http(response, source):
    from semantic_search.context import compact_result
    require(response["rerank"] is True and response["rerank_provider"] == "qwen"
            and response["reranker_batch_size"] == 1 and response["candidate_windows"] == 24
            and response["retrieval_candidate_windows"] == 50, "Wrong production provider/prefix/full50 pool")
    receipts = response["reranker_receipts"]
    require(len(receipts) == 24, "Expected 24 actual singleton score receipts")
    for receipt in receipts.values():
        result = receipt["result"]
        require(receipt["batch_size"] == 1 and result["query_sha256"] == sha(QUERY.encode()), "Scorer request identity mismatch")
        require(all(type(result[key]) in (int, float) and math.isfinite(result[key])
                    for key in ("yes_logit", "no_logit", "logit_difference", "yes_rank_signal")), "Nonfinite score")
    context = compact_result(response)
    target = verify_context(context, serialize_context(context), source)
    return target


def native_delta(before, after):
    require(all(after[key] >= before[key] for key in before), "Native counters went backwards")
    return {key: after[key] - before[key] for key in before}


def clean_lifecycle(receipt, final_status, http_stopped):
    stop, worker = receipt.get("stop", {}), receipt.get("worker_stop", {})
    final_counts = (final_status or {}).get("counts", {})
    counters = ("requests", "attempted_pairs", "completed_pairs", "attempted_forwards", "completed_forwards")
    matching_counts = all(type(final_counts.get(key)) is int
        and type(worker.get("counts", {}).get(key)) is int
        and worker["counts"][key] == final_counts[key] for key in counters)
    return bool(stop.get("owned_group_stopped") is True and stop.get("exit_code") == 0
        and stop.get("forced_kill") is False and stop.get("errors") == []
        and receipt.get("auth_token_removed") is True and not receipt.get("private_cleanup_errors")
        and worker.get("clean_stop") is True and worker.get("listener_closed") is True
        and worker.get("auth_token_removed") is True and worker.get("auth_cleanup_error") is None
        and matching_counts and http_stopped is True)


async def exercise(service, backend, source_file, initial_status, owner, observations, timeout, defaults):
    from mcp import Client, StdioServerParameters
    settings = {**defaults, **CAPS}
    results = []
    before_count = owner.status()["counts"]
    before_http = await asyncio.to_thread(service.search, QUERY, **settings)
    before_target = verify_http(before_http, SOURCE)
    after_count = owner.status()["counts"]
    require(native_delta(before_count, after_count)["completed_forwards"] == 24, "HTTP search did not execute 24 forwards")
    results.append({"phase": "http_before", "response": before_http, "native_before": before_count, "native_after": after_count})
    parameters = StdioServerParameters(command=sys.executable,
        args=["-B", "-m", "semantic_search.mcp_server", "--server", service.url,
              "--default-retrieval", defaults["retrieval"], "--default-rerank",
              "--default-rerank-provider", "qwen", "--default-candidates", "24"],
        cwd=ROOT, env={"PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"})
    async with Client(parameters, mode="legacy", read_timeout_seconds=timeout) as client:
        tools = (await client.list_tools()).tools
        require(len(tools) == 1 and tools[0].name == "search_code", "Wrong MCP tool set")
        properties = tools[0].input_schema["properties"]
        for name, value in defaults.items():
            require(properties[name]["default"] == value, "MCP launch default differs: " + name)
        for name in ("max_results", "source_budget", "token_budget", "include_dependencies"):
            require(properties[name]["default"] == CAPS[name], "MCP budget default differs: " + name)

        async def mcp_search(phase, source, arguments):
            old = owner.status()["counts"]
            result = await client.call_tool("search_code", arguments)
            require(not result.is_error and isinstance(result.structured_content, dict), "MCP search failed")
            require(len(result.content) == 1 and result.content[0].type == "text", "Unexpected MCP content copies")
            target = verify_context(result.structured_content, result.content[0].text, source)
            current = owner.status()["counts"]
            expected = 0 if arguments.get("rerank") is False else 24
            require(native_delta(old, current)["completed_forwards"] == expected, "MCP native forward delta differs")
            observed = observations[-1]
            require(observed["settings"]["rerank_provider"] == "qwen" and observed["settings"]["candidates"] == 24,
                    "Actual MCP HTTP request did not use advertised Qwen/K24 defaults")
            require(observed["settings"]["rerank"] is (expected > 0), "Explicit rerank override was ignored")
            results.append({"phase": phase, "raw_tool_arguments": arguments,
                "context": result.structured_content, "native_before": old, "native_after": current,
                "effective_http_settings": observed["settings"]})
            return target

        await mcp_search("mcp_before", SOURCE, {"query": QUERY})
        source_file.write_text(EDITED)
        began = time.monotonic()
        polls = 0
        while time.monotonic() - began < timeout:
            refreshed = await asyncio.to_thread(service.status)
            polls += 1
            stats, watcher = refreshed.get("indexstats") or {}, refreshed.get("watcher") or {}
            if (refreshed["source_manifest"] != initial_status["source_manifest"]
                    and watcher.get("refresh_count", 0) >= 1
                    and stats.get("indexed_windows") == 1 and stats.get("reused_windows") == 60):
                break
            await asyncio.sleep(.1)
        else:
            raise TimeoutError("Saved edit was not independently refreshed by the watcher")
        require(watcher.get("last_error") is None and stats["total_windows"] == 61, "Saved-edit refresh failed")
        results.append({"phase": "watcher_saved_edit", "trigger_requests": "GET /status only",
                        "status_polls": polls, "status": refreshed})
        old = owner.status()["counts"]
        after_http = await asyncio.to_thread(service.search, QUERY, **settings)
        after_target = verify_http(after_http, EDITED)
        current = owner.status()["counts"]
        require(native_delta(old, current)["completed_forwards"] == 24, "Edited HTTP search forward count differs")
        require(after_target["source_hash"] != before_target["source_hash"] and "attempt, 90" in after_target["source"], "HTTP returned stale saved source")
        results.append({"phase": "http_after", "response": after_http, "native_before": old, "native_after": current})
        await mcp_search("mcp_after", EDITED, {"query": QUERY})
        await mcp_search("mcp_explicit_disable", EDITED, {"query": QUERY, "rerank": False})
        protocol = {"version": client.protocol_version,
                    "server": client.server_info.model_dump(mode="json", by_alias=True, exclude_none=True),
                    "schema": tools[0].input_schema}
    return results, protocol


def verify_ledger(directory, counts, observations):
    events = [json.loads(line) for line in (directory / "forwards.jsonl").read_text().splitlines()]
    started = [row for row in events if row["event"] == "started"]
    completed = [row for row in events if row["event"] == "completed"]
    require(len(started) == len(completed) == counts["completed_forwards"] == 97, "Native ledger/counters differ")
    require(not any(row["event"] == "failed" for row in events), "Native ledger recorded a failure")
    require(sum(row.get("warmup") is True for row in completed) == 1, "Warmup scope changed")
    expected = [receipt for observation in observations
        if observation["response"].get("rerank") is True
        and observation["response"].get("rerank_provider") == "qwen"
        for receipt in observation["response"]["reranker_receipts"].values()]
    measured = [row for row in completed if row.get("warmup") is False]
    require(len(expected) == len(measured) == 96, "Observed HTTP/native ledger logical pair scope differs")
    require([row["request_id"] for row in measured] == [row["request_id"] for row in expected],
            "Native request order differs from actual HTTP receipt order")
    for number, (row, receipt) in enumerate(zip(measured, expected), start=2):
        require(row["batch_size"] == 1 and row["forward_number"] == number
                and len(row["input_ids_sha256"]) == 64, "Native B1/input/forward receipt differs")
        for field in ("query_sha256", "source_sha256", "delivered_sha256", "total_tokens"):
            require(row[field] == receipt["result"][field], "Native receipt identity differs: " + field)
        require(row["score"] == {key: receipt["result"][key] for key in
                ("yes_logit", "no_logit", "logit_difference", "yes_rank_signal")}, "Native observed logits differ from HTTP response")
    return {"events": len(events), "started": len(started), "completed": len(completed),
            "warmup_forwards": 1, "measured_forwards": 96, "sha256": sha((directory / "forwards.jsonl").read_bytes())}


def run(output, work, python, checkpoint, *, timeout=180, owner_factory=QwenRerankerProcess,
        embedder_factory=lambda: QwenEmbedder(progress=False), native=True):
    require(not (output / "run-start.json").exists() and not (output / "results.json").exists(), "No retry/resume of an attempted smoke")
    raw_plan = (output / "frozen-plan.json").read_bytes()
    plan = json.loads(raw_plan)
    require(plan["source_sha256"] == source_bindings(), "Frozen production/harness source changed")
    require(plan["cpu_injected"] is not native, "CPU/native smoke mode differs from freeze")
    if native:
        require(output.is_relative_to(ROOT), "Public native smoke output must be inside the repository")
        committed_bindings(plan["source_sha256"])
        committed_bindings({str((output / "frozen-plan.json").relative_to(ROOT)): sha(raw_plan)})
        require(plan["runtime"] == runtime_plan(python, checkpoint), "Frozen runtime/checkpoint metadata changed")
    source_file = work / "repo/src/sample.py"
    require(source_file.read_text() == SOURCE and sha(source_file.read_bytes()) == plan["fixture"]["initial_sha256"], "Frozen fixture changed")
    require(subprocess.check_output(["git", "-C", str(work / "repo"), "ls-files"], text=True).splitlines()
            == plan["fixture"]["tracked_files"], "Tracked fixture scope changed")
    write_new(output / "run-start.json", {"started_at_utc": utc(), "frozen_plan_sha256": sha(raw_plan),
                                         "native_inference_authorized": native}, work)
    observations = []
    owner = backend = server = thread = embedder = None
    result = {"checks_passed": False, "native_inference": native, "steps": [], "semantic_quality_measurement": False}
    previous = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGINT)}
    interrupt_state = {"signals": 0, "cleaning": False, "by_signal": {}}
    def interrupted(number, *_):
        interrupt_state["signals"] += 1
        name = signal.Signals(number).name
        interrupt_state["by_signal"][name] = interrupt_state["by_signal"].get(name, 0) + 1
        if interrupt_state["signals"] == 1 and not interrupt_state["cleaning"]:
            raise KeyboardInterrupt
    for number in previous:
        signal.signal(number, interrupted)
    try:
        owner = owner_factory(python=python, checkpoint=checkpoint, log_dir=work / "worker-private")
        owner.start()
        embedder = ObservedEmbedder(embedder_factory())
        require(embedder.metadata == plan["runtime"]["embedding"], "Actual embedding metadata differs")
        class ObservedBackend(SearchBackend):
            def search(self, query, **settings):
                response = super().search(query, **settings)
                observations.append({"query": query, "settings": settings, "response": response})
                return response
        backend = ObservedBackend(work / "repo", "src", work / "index.sqlite3",
                                  embedder=embedder, qwen_client=owner.client)
        backend.search("Find the implementation that processes a value.", max_results=1)
        initial = backend.status()
        require(initial["windows"] == 61 and initial["indexstats"]["indexed_windows"] == 61,
                "Frozen toy AST/window count differs")
        backend.start_watching(poll_interval=.1, debounce=.1)
        server = create_server(backend, port=0)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .05})
        thread.start()
        service = SearchClient(f"http://127.0.0.1:{server.server_address[1]}", timeout=timeout)
        result["steps"], result["mcp"] = asyncio.run(exercise(service, backend, source_file, initial,
                                        owner, observations, timeout, plan["defaults"]))
        result["native_final_status"] = owner.status()
        result["embedding_counts"] = embedder.counts()
        require(embedder.counts()["query_calls"] == 6 and embedder.counts()["document_texts"] == 62,
                "Embedding entries do not match startup/search/edit schedule")
        result["native_ledger"] = verify_ledger(owner.directory, result["native_final_status"]["counts"], observations)
        result["checks_passed"] = True
    except BaseException as error:
        result["failure"] = {"error_type": type(error).__name__, "error": str(error)}
    finally:
        interrupt_state["cleaning"] = True
        cleanup_errors = []
        def cleanup(action, function):
            try:
                function()
            except BaseException as error:
                cleanup_errors.append({"action": action, "error_type": type(error).__name__, "error": str(error)})
        if server is not None:
            if thread is not None and thread.is_alive():
                cleanup("http_shutdown", server.shutdown)
            cleanup("http_backend_close", server.server_close)
            if thread is not None and thread.ident is not None:
                cleanup("http_thread_join", lambda: thread.join(timeout=5))
                result["http_thread_stopped"] = not thread.is_alive()
        elif backend is not None:
            cleanup("backend_close", backend.close)
        elif embedder is not None:
            cleanup("embedding_close", embedder.close)
        if owner is not None:
            cleanup("owner_close", owner.close)
            result["owner"] = owner.receipt
            directory = getattr(owner, "directory", None)
            ledger = Path(directory) / "forwards.jsonl" if directory is not None else None
            if ledger is not None and ledger.is_file():
                def preserve_ledger():
                    raw = ledger.read_bytes()
                    with (output / "native-forwards.jsonl").open("xb") as stream:
                        stream.write(raw)
                    result["native_ledger_preserved_sha256"] = sha(raw)
                cleanup("preserve_native_ledger", preserve_ledger)
        for number, handler in previous.items():
            cleanup("restore_" + signal.Signals(number).name, lambda number=number, handler=handler: signal.signal(number, handler))
        result["cleanup_errors"] = cleanup_errors
        result["interrupt_signals"] = interrupt_state["by_signal"]
    result["source_sha256"] = source_bindings()
    result["source_stable"] = result["source_sha256"] == plan["source_sha256"]
    result["observations"] = observations
    if owner is not None:
        result["lifecycle_passed"] = clean_lifecycle(owner.receipt, result.get("native_final_status"),
                                                    result.get("http_thread_stopped"))
    result["checks_passed"] = bool(result["checks_passed"] and result["source_stable"]
                                  and result.get("lifecycle_passed") and not result["cleanup_errors"]
                                  and not interrupt_state["signals"])
    write_new(output / "results.json", result, work)
    artifacts = {path.name: sha(path.read_bytes()) for path in sorted(output.iterdir()) if path.is_file()}
    certificate = {"checks_passed": result["checks_passed"] and native, "cpu_injected": not native,
                   "frozen_plan_sha256": sha(raw_plan), "source_sha256": plan["source_sha256"],
                   "artifact_sha256": artifacts, "completed_at_utc": utc(),
                   "scope": "Toy native HTTP/MCP/default/explicit override/saved-edit/index/lifecycle integration; not retrieval/coding quality or paid tokens"}
    write_new(output / "verification.json", certificate, work)
    return certificate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--reranker-python", type=Path, default=ROOT / ".cache/reranker-experiment/.venv/bin/python")
    parser.add_argument("--checkpoint", type=Path, default=ROOT / ".cache/models/qwen3-reranker-0.6b-original")
    parser.add_argument("--retrieval", choices=("dense", "hybrid"), default="hybrid")
    parser.add_argument("--timeout", type=float, default=180)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze", action="store_true")
    mode.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)
    require(math.isfinite(args.timeout) and 0 < args.timeout <= 600, "Invalid timeout")
    work, output = args.work_dir.resolve(), args.output.resolve()
    require(work.is_relative_to(ROOT / ".cache"), "Private smoke work directory must be inside ignored .cache")
    python, checkpoint = args.reranker_python.absolute(), args.checkpoint.resolve()
    if args.freeze:
        freeze(output, work, python, checkpoint, retrieval=args.retrieval)
        print("Frozen CPU-only preflight; no model inference. Run this exact new plan with --run.")
    else:
        certificate = run(output, work, python, checkpoint, timeout=args.timeout)
        print(json.dumps({"checks_passed": certificate["checks_passed"], "output": str(output)}))
        if not certificate["checks_passed"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
