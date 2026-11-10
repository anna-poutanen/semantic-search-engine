"""Frozen observed native comparison of exact vector validation implementations."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import json
import importlib
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

old = importlib.import_module("benchmark_role_native")
DurableLedger = importlib.import_module("experiments.dependency_context.study").Ledger
integration = importlib.import_module("experiments.provider_native.integration")

SCHEMA = "provider_native_study_v1"
CLASSIFICATION = "observed_native_provider_comparison"
ARMS = ("legacy", "coverage_control", "original_role", "fast_role")
MODES = ("dense", "hybrid")
PARENT = "470451f571408cb54c99307d3b2aed5f56dadb1f"
PUBLICATION = "b08c784162c4a89192c1cd0a1bf94f6d35edddcb"
LEGACY = "results/role-native-holdout-v2-2026-11-03"
CPU = "results/dependency-provider-cpu-2026-11-09"
DEPENDENCY = "results/dependency-context-continuation-2026-11-09"
GATE = {"max_median_ratio_vs_controls": 1.25,
        "zero_individual_losses_vs_original_role": True, "exact_fast_original_parity": True}
require, canonical, sha, file_sha, read = old.require, old.canonical, old.sha, old.file_sha, old.read


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def utc():
    return datetime.now(timezone.utc).isoformat()


def sync(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write(path, value):
    with Path(path).open("xb") as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    sync(Path(path).parent)


def native_json(value):
    """Native tuple operands become JSON arrays without changing mapping order."""
    if type(value) is dict:
        require(all(type(key) is str for key in value), "Native archive requires string mapping keys")
        return {key: native_json(item) for key, item in value.items()}
    if type(value) in (list, tuple):
        return [native_json(item) for item in value]
    return value  # The durable writer still rejects every other nonliteral type.


class Ledger(DurableLedger):
    """Match original native tuple transport, retaining strict durable writes."""
    def append(self, row):
        try:
            super().append(native_json(row))
        except BaseException as error:
            try:
                write(self.path.parent / "archive-failure.json", {
                    "archive_file": self.path.name, "error_type": type(error).__name__, "error": str(error),
                    "source_row_snapshot": old.capture_helpers._safe_snapshot(row),
                    "snapshot_policy": "Tuples become arrays; unsupported or cyclic values have explicit type markers",
                    "resume_allowed": False})
            except BaseException as recorder_error:
                raise error from recorder_error
            raise


def source_hashes():
    paths = set()
    for folder in ("src", "scripts", "experiments/dependency_context", "experiments/provider_native"):
        paths.update((ROOT / folder).rglob("*.py"))
    paths.update((ROOT / "tests").glob("test_provider_native*.py"))
    paths.update(ROOT / name for name in ("experiments/__init__.py",
        "tests/test_dependency_fast_provider.py", "tests/test_dependency_provider_profile.py",
        "docs/provider-native-protocol.md", "pyproject.toml", "requirements-mac.lock",
        read(ROOT / LEGACY / "plan.json")["binding"]["runtime_manifest"]["manifest_path"]))
    return {path.relative_to(ROOT).as_posix(): file_sha(path) for path in sorted(paths)}


def committed(hashes, commit):
    for name, expected in hashes.items():
        require(file_sha(ROOT / name) == expected == sha(subprocess.check_output(
            ["git", "-C", str(ROOT), "show", commit + ":" + name])), "Bound blob differs: " + name)


def source_authority():
    old.source_authority()
    for name, module in tuple(sys.modules.items()):
        if name == "experiments" or name.startswith("experiments."):
            path = getattr(module, "__file__", None)
            require(path is not None and Path(path).resolve().is_relative_to(ROOT / "experiments"),
                    "Experiment module escapes this checkout: " + name)


def parent_hashes():
    plan = read(ROOT / LEGACY / "plan.json")
    binding = plan["binding"]
    summary = read(ROOT / LEGACY / "attempt/summary.json")
    audit = read(ROOT / LEGACY / "independent-audit.json")
    cpu = read(ROOT / CPU / "independent-review.json")
    require(summary["complete"] is True and audit["checks_passed"] is True
            and audit["verified_requests"] == 252 and cpu["checks_passed"] is True,
            "Complete audited original native and CPU evidence required")
    dependency = read(ROOT / DEPENDENCY / "replay/summary.json")
    require(dependency["quality_gate"]["qualified"] is False,
            "Retain failed dependency quality; this experiment does not qualify it")
    dependency_freeze = read(ROOT / DEPENDENCY / "study-freeze.json")
    failure_authority = read(ROOT / DEPENDENCY / "preflight/failure-authority.json")
    paths = {**binding["implementation_sha256"], **binding["development_parent"]["artifacts"],
             **cpu["input_artifact_sha256"],
             **dependency_freeze["sources"], **dependency_freeze["artifacts"],
             **failure_authority["original_failure_artifact_sha256"],
             **{DEPENDENCY + "/replay/" + name: value for name, value in dependency["artifacts"].items()},
             **{LEGACY + "/attempt/" + name: value for name, value in summary["artifact_sha256"].items()}}
    for name in (LEGACY + "/plan.json", LEGACY + "/source-inventory.json",
        LEGACY + "/independent-audit.json", LEGACY + "/input-audit.json", LEGACY + "/attempt/summary.json",
        CPU + "/independent-review.json", CPU + "/independent-review-script.py",
        CPU + "/independent-review-notes.md", DEPENDENCY + "/study-freeze.json",
        DEPENDENCY + "/prepared/frozen-plan.json", DEPENDENCY + "/replay/summary.json",
        DEPENDENCY + "/preflight/failure-authority.json",
        DEPENDENCY + "/independent-audit.json", DEPENDENCY + "/findings.md"):
        paths[name] = file_sha(ROOT / name)
    committed(paths, PARENT)
    for name in ("plan.json", "source-inventory.json", "independent-audit.json", "attempt/summary.json"):
        committed({LEGACY + "/" + name: paths[LEGACY + "/" + name]}, PUBLICATION)
    require(subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", PARENT, "HEAD"],
        capture_output=True).returncode == 0, "Published parent must remain an ancestor")
    return {"commit": PARENT, "artifacts": paths}


def inventory(corpus, legacy):
    from semantic_search.corpus import _source_files, extract_snippets
    from semantic_search.languages import decode_source
    from semantic_search.relations import ReferenceGraph

    binding = legacy["binding"]
    data = read(ROOT / binding["dataset_path"])
    require(file_sha(ROOT / binding["dataset_path"]) == binding["dataset_sha256"], "Fixed dataset changed")
    require(subprocess.check_output(["git", "-C", str(corpus), "rev-parse", "HEAD"], text=True).strip()
            == binding["revision"], "Observed corpus revision changed")
    files = _source_files(corpus, corpus / data["source_root"], data.get("source_exclusions", ()))
    sources = {path.relative_to(corpus).as_posix(): decode_source(path, path.read_bytes()) for path in files}
    require({path.relative_to(corpus).as_posix(): file_sha(path) for path in files} == binding["source_sha256"],
            "Complete observed corpus source bytes changed")
    keys = {}
    snippets = extract_snippets(corpus, data["source_root"], 160, 40, sources=sources,
                                source_exclusions=data.get("source_exclusions", ()), definition_keys=keys)
    graph = ReferenceGraph(sources, snippets)
    document = {"inventory": [asdict(item) for item in snippets],
        "definition_keys": {key: list(value) for key, value in keys.items()},
        "graph_edges": [{"caller": list(caller), "targets": [list(target) for target in targets]}
                        for caller, targets in sorted(graph.edges.items())]}
    expected = read(ROOT / LEGACY / "source-inventory.json")
    require(canonical(document) == canonical(expected), "Actual extraction/graph differs from original source inventory")
    return data, sources, expected


def freeze(args):
    source_authority()
    require(type(args.source_freeze) is str and re.fullmatch(r"[0-9a-f]{40}", args.source_freeze),
            "Explicit full source-freeze SHA required")
    require(not args.output.exists(), "Use an exclusive new plan directory")
    hashes = source_hashes()
    committed(hashes, args.source_freeze)
    committed(hashes, "HEAD")
    require(subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", args.source_freeze, "HEAD"],
            capture_output=True).returncode == 0, "Source freeze must precede preparation")
    parent = parent_hashes()
    legacy = read(ROOT / LEGACY / "plan.json")
    data, _, _ = inventory(args.corpus, legacy)
    runtime = old.transport.verify_runtime(ROOT, ROOT / legacy["binding"]["runtime_manifest"]["manifest_path"],
                                           "HEAD", args.model)
    require(runtime == legacy["binding"]["runtime_manifest"], "Original complete model/runtime binding changed")
    args.output.mkdir(exist_ok=False)
    sync(args.output.parent)
    # Preserve original transport bytes and all original schema/authoring flags.
    with (args.output / "source-inventory.json").open("xb") as stream:
        stream.write((ROOT / LEGACY / "source-inventory.json").read_bytes())
        stream.flush()
        os.fsync(stream.fileno())
    plan = {"schema": SCHEMA, "version": 1, "classification": CLASSIFICATION,
        "frozen_at_utc": utc(), "frozen_from_commit": git("rev-parse", "HEAD"),
        "source_freeze": {"commit": args.source_freeze, "sha256": hashes, "bound_blob_count": len(hashes),
            "committed_at_unix": int(git("show", "-s", "--format=%ct", args.source_freeze))},
        "parent": parent, "legacy_plan": {"path": LEGACY + "/plan.json",
            "sha256": file_sha(ROOT / LEGACY / "plan.json"), "publication_commit": PUBLICATION},
        "runtime_manifest": runtime, "queries": legacy["queries"], "arms": list(ARMS), "retrievals": list(MODES),
        "repetitions": 3, "quality_repetition": 1, "expected_requests": 336, "expected_warmups": 8,
        "gate": GATE, "settings": old.SETTINGS,
        "artifacts": {"source-inventory.json": file_sha(args.output / "source-inventory.json")},
        "corpus": str(args.corpus), "model": str(args.model),
        "scope": "Observed performance replication; no new quality rule, skip wiring, fresh or automatic promotion claim"}
    write(args.output / "plan.json", plan)
    return plan


def verify_plan(args):
    source_authority()
    plan = read(args.output / "plan.json")
    require(plan["schema"] == SCHEMA and plan["classification"] == CLASSIFICATION and type(plan["version"]) is int
            and plan["version"] == 1 and plan["gate"] == GATE and plan["arms"] == list(ARMS)
            and plan["retrievals"] == list(MODES) and plan["settings"] == old.SETTINGS
            and all(type(plan[key]) is int and plan[key] == value for key, value in
                (("repetitions", 3), ("quality_repetition", 1), ("expected_requests", 336), ("expected_warmups", 8))),
            "Native fixed plan differs")
    frozen = plan["source_freeze"]
    require(type(frozen["commit"]) is str and re.fullmatch(r"[0-9a-f]{40}", frozen["commit"])
            and type(frozen["bound_blob_count"]) is int and frozen["bound_blob_count"] == len(frozen["sha256"])
            and frozen["sha256"] == source_hashes(), "Actual source-first closure differs")
    committed(frozen["sha256"], frozen["commit"])
    committed(frozen["sha256"], "HEAD")
    for first, last in ((PARENT, frozen["commit"]), (frozen["commit"], "HEAD")):
        require(subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", first, last],
            capture_output=True).returncode == 0, "Source-first ancestry differs")
    require(type(frozen["committed_at_unix"]) is int
            and frozen["committed_at_unix"] == int(git("show", "-s", "--format=%ct", frozen["commit"]))
            <= datetime.fromisoformat(plan["frozen_at_utc"]).timestamp(), "Source-first chronology differs")
    require(plan["parent"] == parent_hashes(),
            "Frozen source/parent closure changed")
    require(plan["legacy_plan"] == {"path": LEGACY + "/plan.json", "publication_commit": PUBLICATION,
        "sha256": file_sha(ROOT / LEGACY / "plan.json")}, "Immutable original plan pointer differs")
    legacy = read(ROOT / LEGACY / "plan.json")
    require(plan["runtime_manifest"] == legacy["binding"]["runtime_manifest"] and plan["queries"] == legacy["queries"]
            and set(plan["artifacts"]) == {"source-inventory.json"}
            and plan["artifacts"]["source-inventory.json"] == file_sha(args.output / "source-inventory.json")
            == file_sha(ROOT / LEGACY / "source-inventory.json"), "Original runtime/queries/inventory changed")
    input_path = args.output / "input-audit.json"
    audit = read(input_path)
    verify_input_barrier(audit, plan, file_sha(args.output / "plan.json"))
    hashes = {**plan["source_freeze"]["sha256"], **plan["parent"]["artifacts"],
        (args.output / "plan.json").relative_to(ROOT).as_posix(): file_sha(args.output / "plan.json"),
        input_path.relative_to(ROOT).as_posix(): file_sha(input_path),
        (args.output / "source-inventory.json").relative_to(ROOT).as_posix(): plan["artifacts"]["source-inventory.json"]}
    committed(hashes, "HEAD")
    commit = git("log", "-1", "--format=%H", "--", str(input_path.relative_to(ROOT)))
    committed(hashes, commit)
    for first, last in ((frozen["commit"], audit["execution_commit"]),
                        (audit["execution_commit"], commit), (commit, "HEAD")):
        require(subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", first, last],
            capture_output=True).returncode == 0, "Source/input freeze ancestry differs")
    require(not git("status", "--porcelain", "--untracked-files=no"), "Tracked worktree must be clean")
    require(str(args.corpus) == plan["corpus"] and str(args.model) == plan["model"], "Execution paths differ")
    data, sources, document = inventory(args.corpus, legacy)
    require(old.transport.verify_runtime(ROOT, ROOT / plan["runtime_manifest"]["manifest_path"], "HEAD", args.model)
            == plan["runtime_manifest"], "Actual runtime/model changed")
    return plan, data, sources, document, {"commit": commit, "bound_blob_count": len(hashes),
        "committed_at_unix": int(git("show", "-s", "--format=%ct", commit))}


def verify_input_barrier(audit, plan, plan_sha):
    zero = {key: 0 for key in ("model", "index", "retrieval", "selector", "postprocessor",
                              "rule_activation", "gold_coverage_scoring", "new_native_outcomes")}
    expected_parent = {"commit": PARENT, "bound_artifacts": len(plan["parent"]["artifacts"]),
        "old_native_publication": PUBLICATION, "old_native_requests": 252,
        "cpu_parity_integrity_passed": True, "dependency_quality_qualified": False}
    require(audit.get("schema") == "provider_native_input_audit_v1" and audit.get("status") == "passed"
            and audit.get("checks_passed") is True and audit.get("classification") == CLASSIFICATION
            and type(audit.get("new_native_outcomes_computed")) is int and audit["new_native_outcomes_computed"] == 0
            and audit.get("plan_sha256") == plan_sha
            and audit.get("source_inventory_sha256") == plan["artifacts"]["source-inventory.json"]
            and audit.get("source_freeze") == plan["source_freeze"]
            and canonical(audit.get("parent")) == canonical(plan["parent"])
            and canonical(audit.get("parent_verification")) == canonical(expected_parent)
            and audit.get("source_sha256") == plan["source_freeze"]["sha256"]
            and type(audit.get("source_only_calls")) is dict and audit["source_only_calls"].keys() == zero.keys()
            and all(type(audit["source_only_calls"][key]) is int and audit["source_only_calls"][key] == 0 for key in zero)
            and type(audit.get("execution_commit")) is str and re.fullmatch(r"[0-9a-f]{40}", audit["execution_commit"])
            and audit.get("original_native_input_audit_sha256") == file_sha(ROOT / LEGACY / "input-audit.json"),
            "Committed independent source-only input barrier required")


def schedule(queries):
    order = [(arm, mode) for mode in MODES for arm in ARMS]
    for repetition in range(1, 4):
        for position, query in enumerate(queries):
            offset = (position + repetition - 1) % len(order)
            for arm, mode in order[offset:] + order[:offset]:
                yield repetition, query, arm, mode


def project(rows, arm):
    result = []
    for row in rows:
        if row["arm"] not in ("legacy", "coverage_control", arm):
            continue
        value = dict(row)
        if value["arm"] == arm:
            value.update(arm="candidate", condition=old.CANDIDATE)
        result.append(value)
    return result


def metrics(rows):
    result = {"role_arms": {arm: old.summarize(project(rows, arm)) for arm in ARMS[2:]},
              "provider_comparison": {}, "phase_medians": {}}
    for mode in MODES:
        by_arm = {arm: [row for row in rows if row["arm"] == arm and row["retrieval"] == mode] for arm in ARMS[2:]}
        times = {arm: sorted(row["elapsed_backend_s"] for row in group) for arm, group in by_arm.items()}
        original = {(row["query_id"], row["repetition"]): row for row in by_arm["original_role"]}
        medians = {arm: statistics.median(values) for arm, values in times.items()}
        result["provider_comparison"][mode] = {
            "original_median_s": medians["original_role"], "fast_median_s": medians["fast_role"],
            "original_p95_s": times["original_role"][math.ceil(.95 * len(times["original_role"]))-1],
            "fast_p95_s": times["fast_role"][math.ceil(.95 * len(times["fast_role"]))-1],
            "fast_over_original_median_ratio": medians["fast_role"] / medians["original_role"],
            "median_paired_delta_s": statistics.median(row["elapsed_backend_s"]
                - original[(row["query_id"], row["repetition"])]["elapsed_backend_s"] for row in by_arm["fast_role"]),
            "timed_requests_per_arm": len(original)}
        result["phase_medians"][mode] = {arm: {name: statistics.median(row["provider_integration"][field]
            for row in group) for name, field in (("factory_setup_s", "factory_elapsed_s"),
                ("provider_call_s", "provider_elapsed_s"))} for arm, group in by_arm.items()}
    return result


def run(args):
    attempt = args.output / "attempt"
    require(not attempt.exists(), "No overwrite, retry or resume of a native attempt")
    plan, data, sources, document, chain = verify_plan(args)
    require(chain["committed_at_unix"] <= datetime.now(timezone.utc).timestamp(), "Freeze cannot follow execution")
    attempt.mkdir(exist_ok=False)
    sync(attempt.parent)
    rows, warmups, phase, backend, embedder, lease, attempted = [], [], "claim", None, None, None, 0
    setup_events = []
    try:
        write(attempt / "run-started.json", {"schema": SCHEMA, "started_at_utc": utc(),
            "execution_commit": git("rev-parse", "HEAD"), "prospective_freeze": chain,
            "source_freeze": plan["source_freeze"], "plan_sha256": file_sha(args.output / "plan.json"),
            "resume_allowed": False, "expected_measured_calls": 336, "expected_warmups": 8})
        with ExitStack() as streams:
            ledgers = {repetition: streams.enter_context(Ledger(attempt / f"searches-r{repetition}.jsonl.gz"))
                       for repetition in range(1, 4)}
            warm_ledger = streams.enter_context(Ledger(attempt / "warmups.jsonl.gz"))
            phase = "gpu_lease"
            with old.transport.gpu_lease(ROOT) as lease:
                write(attempt / "gpu-lease-acquired.json", deepcopy(lease))
                try:
                    phase = "setup"
                    rank_bytes = (args.tokenizer_cache / old.base.replay.CACHE_KEY).read_bytes()
                    require(sha(rank_bytes) == old.base.replay.CACHE_SHA256, "Exact offline tokenizer required")
                    import tiktoken.load

                    def rank_reader(url, expected_hash=None):
                        require(url == old.base.replay.CACHE_URL and expected_hash == old.base.replay.CACHE_SHA256,
                                "Unexpected tokenizer resource")
                        return rank_bytes

                    with patch.object(tiktoken.load, "read_file_cached", rank_reader):
                        old.context.encoding()
                    embedder = old.RecordingEmbedder(args.model, progress=True)
                    require(embedder.metadata == plan["runtime_manifest"]["model_binding"]["metadata"],
                            "Actual embedder metadata differs")
                    db = ROOT / ".cache/indexes" / ("provider-native-" + sha(attempt.relative_to(ROOT).as_posix().encode())[:20] + ".sqlite3")
                    require(not any(Path(str(db) + suffix).exists() for suffix in ("", "-wal", "-shm")), "Own a new index")
                    began = time.perf_counter()
                    backend = old.make_backend(args, data, db, embedder)
                    with patch.object(backend, "_research_forward_observer", setup_events.append):
                        backend.refresh()
                        status = backend.status()
                    require(backend._definition_keys == {key: tuple(value) for key, value in document["definition_keys"].items()},
                            "Live definitions differ from input freeze")
                    graph = [{"caller": list(caller), "targets": [list(target) for target in targets]}
                             for caller, targets in sorted(backend._references.edges.items())]
                    require(graph == document["graph_edges"], "Live graph differs")
                    warm_query = {"id": "__generic_warmup__", "query": old.WARM_QUERY, "kind": "absent"}
                    for arm in ARMS:
                        for mode in MODES:
                            row = integration.measure(backend, embedder, warm_query, 0, arm, mode,
                                on_failure=lambda value: write(attempt / "request-failure.json", value))
                            warm_ledger.append(row)
                            warmups.append(row)
                    setup = {"index_model_and_explicit_warmup_s": time.perf_counter()-began, "status": status,
                        "warmup_calls": len(warmups), "query_api_calls": embedder.query_calls,
                        "clause_api_calls": embedder.clause_query_calls, "clause_encoded_texts": embedder.clause_query_texts,
                        "document_api_calls": embedder.document_calls, "external_forward_events": setup_events,
                        "db_path": str(db), "document_calls": deepcopy(embedder.document_api_records),
                        "machine_state": old.machine_state()}
                    require(len(warmups) == embedder.query_calls == 8, "Eight fixed warmups required")
                    write(attempt / "setup.json", setup)
                    phase = "measured_calls"
                    ranked, vectors, payloads, last_clock = {}, {}, {}, warmups[-1]["request_clock"]
                    for repetition, query, arm, mode in schedule(data["queries"]):
                        attempted += 1
                        row = integration.measure(backend, embedder, query, repetition, arm, mode,
                            on_failure=lambda value: write(attempt / "request-failure.json", value))
                        clock = row["request_clock"]
                        low, high = (datetime.fromisoformat(clock[name]) for name in ("started_at_utc", "ended_at_utc"))
                        gap = (low-datetime.fromisoformat(last_clock["ended_at_utc"])).total_seconds()
                        if gap > 30 or abs((high-low).total_seconds()-row["elapsed_backend_s"]) > 2:
                            write(attempt / "clock-interruption.json", {"gap_s": gap, "request": row})
                            raise ValueError("Native timing interrupted; preserve entire attempt")
                        last_clock = clock
                        raw = row["stages"]["ranked"]
                        for stage in ("ranked", "selected", "eligible"):
                            for pair in row["stages"][stage]:
                                old.base.checked_snippet(pair["snippet"], sources, document["inventory"], document["definition_keys"], raw)
                        for field in ("response", "pre_postprocess_response", "reference_response", "proposal_response"):
                            if row.get(field) is not None:
                                old.base.checked_response(row[field], sources, document["inventory"], document["definition_keys"], raw)
                        old.base.checked_candidate_rows(row, sources, document)
                        key = (query["id"], mode)
                        require(ranked.setdefault(key, sha(canonical(raw))) == sha(canonical(raw)), "Raw ranks changed")
                        require(vectors.setdefault(query["id"], row["query_vector_sha256"]) == row["query_vector_sha256"], "Query vector changed")
                        require(payloads.setdefault((*key, arm), row["compact_sha256"]) == row["compact_sha256"], "Repeated compact response changed")
                        ledgers[repetition].append(row)
                        rows.append(row)
                        print(f"r{repetition} {query['id']} {mode} {arm} {row['elapsed_backend_s']:.3f}s", flush=True)
                    require(attempted == len(rows) == 336, "Complete fixed native matrix required")
                finally:
                    previous, primary = phase, sys.exception()
                    phase = "close_backend"
                    try:
                        if backend is not None:
                            backend.close()
                        elif embedder is not None:
                            old.transport.close_preserving_error(embedder)
                        write(attempt / "model-closed.json", {"closed_at": utc(), "phase": previous})
                        phase = previous
                    except BaseException as error:
                        if primary is not None:
                            raise primary from error
                        raise
            write(attempt / "gpu-lease-released.json", deepcopy(lease))
        phase = "final_bindings"
        require(verify_plan(args)[0] == plan, "Frozen plan changed during native execution")
        summary = {"schema": SCHEMA, "classification": CLASSIFICATION, "complete": True,
            "requests": len(rows), "unique_queries": len(data["queries"]), "quality_repetition": 1,
            "setup": setup, "gate": GATE, "metrics": metrics(rows), "plan_sha256": file_sha(args.output / "plan.json"),
            "post_run_machine_state": old.machine_state(), "ranked_repeat_changes": 0,
            "compact_repeat_changes": 0, "query_vector_repeat_changes": 0,
            "measured_query_embeddings": sum(row["query_embedding_calls"] for row in rows),
            "measured_clause_query_calls": sum(row["clause_query_calls"] for row in rows),
            "measured_clause_encoded_texts": sum(row["clause_query_texts"] for row in rows),
            "measured_document_embedding_calls": sum(row["document_embedding_calls"] for row in rows),
            "artifact_sha256": {path.name: file_sha(path) for path in attempt.iterdir() if path.is_file()}}
        write(attempt / "summary.json", summary)
        return summary
    except BaseException as error:
        try:
            if lease is not None and lease.get("released_at") is not None and not (attempt / "gpu-lease-released.json").exists():
                write(attempt / "gpu-lease-released.json", deepcopy(lease))
            if embedder is not None and not (attempt / "setup.json").exists():
                write(attempt / "setup-partial.json", old.capture_helpers._safe_snapshot({
                    "document_calls": embedder.document_api_records, "external_forward_events": setup_events,
                    "query_api_calls": embedder.query_calls, "clause_api_calls": embedder.clause_query_calls,
                    "clause_encoded_texts": embedder.clause_query_texts, "document_api_calls": embedder.document_calls}))
            write(attempt / "failure.json", {"schema": SCHEMA, "phase": phase, "completed_requests": len(rows),
                "attempted_requests": attempted, "error_type": type(error).__name__, "error": str(error), "resume_allowed": False,
                "artifacts": {path.name: file_sha(path) for path in attempt.iterdir() if path.is_file()}})
        except BaseException as recorder_error:
            raise error from recorder_error
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--tokenizer-cache", type=Path, required=True)
    parser.add_argument("--source-freeze")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--freeze", action="store_true")
    action.add_argument("--run", action="store_true")
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    require(args.output.parent.is_dir() and args.output.is_relative_to(ROOT), "Exclusive attempt parent must exist inside project")
    if args.freeze:
        require(args.source_freeze is not None, "Freeze requires an explicit committed source SHA")
    result = freeze(args) if args.freeze else run(args)
    print(json.dumps({key: result[key] for key in ("schema", "classification", "complete", "requests") if key in result}))


if __name__ == "__main__":
    main()
