"""CPU-only native-profile contracts using invented repositories and query labels."""
from copy import deepcopy
from dataclasses import asdict
import gzip
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import benchmark_flow_allocation as native
from semantic_search import backend as backend_module, context, selection
from semantic_search.corpus import Snippet
from semantic_search.source import physical_lines


def options(tmp_path):
    return SimpleNamespace(candidate="shared_flow", development_study=tmp_path / "parent",
        dataset=tmp_path / "unread-data.json", corpus=tmp_path / "corpus", model=tmp_path / "model",
        output=tmp_path / "native", source_review=tmp_path / "review.json",
        definition_inventory=tmp_path / "definitions.json", repetitions=3,
        role="repository_holdout", tokenizer_cache=tmp_path / "tokenizers")


def parent_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(native, "ROOT", tmp_path)
    args = options(tmp_path)
    parent = args.development_study
    parent.mkdir()
    (tmp_path / "producer.py").write_text("frozen source\n")
    (tmp_path / "auditor.py").write_text("independent source\n")
    plan = {"study": "source_flow", "conditions": native.development.CONDITIONS, "settings": native.SETTINGS,
        "producer_sha256": {"producer.py": native.file_sha(tmp_path / "producer.py")},
        "selection": {"shared_flow": "Prequalified source-only flow rule"}}
    for name in native.development.FROZEN_FILES:
        native.write(parent / name, plan if name == "frozen-plan.json" else {})
    (parent / "replay.jsonl.gz").write_bytes(b"synthetic original trace bytes")
    (parent / "findings.md").write_text("All measured results retained.\n")
    chain = {"commit": "freeze", "bound_blob_count": 1, "committed_at_unix": 0}
    native.write(parent / "run-start.json", {"execution_commit": "execution", "prospective_freeze": chain})
    native.write(parent / "control-gate.json", {"exact_legacy_rows": 365, "exact_prior_coverage_rows": 337,
        "exact_parent_control_rows": 730, "new_variant_rows_before_gate": 0})
    q = {"qualified": True, "against_controls": {arm: {"lost_groups": 0, "lost_complete_rows": 0,
        "lost_single_file_first_hits": 0} for arm in native.ARMS[:-1]},
        "additional_distinct_complete_queries_vs_coverage_control": ["observed/new-behavior"],
        "gained_additional_distinct_complete_queries": 1}
    summary = {"study": "source_flow", "qualification": {"shared_flow": q}, "model_calls": 0,
        "retrieval_calls": 0, "network_calls": 0, "baseline_exact_rows": 365, "prior_coverage_exact_rows": 337,
        "exact_parent_control_rows": 730, "referenced_negative_rows": 1460,
        "plan_sha256": native.file_sha(parent / "frozen-plan.json"),
        "replay_sha256": native.file_sha(parent / "replay.jsonl.gz")}
    native.write(parent / "summary.json", summary)
    audit = {"study": "source_flow", "status": "passed", "checks_passed": True, "mode": "development",
        "candidate_planner_or_producer_modules_imported": False, "exact_legacy_controls": 365,
        "exact_prior_coverage_controls": 337, "verified_outcome_rows": 365, "referenced_control_rows": 730,
        "referenced_negative_rows": 1460, "independently_recomputed_summary": summary,
        "producer_execution": "execution", "producer_freeze": chain,
        "calls": {name: 0 for name in ("model", "index", "retrieval", "network")},
        "artifact_sha256": {name: native.file_sha(parent / name) for name in
                            ("summary.json", "frozen-plan.json", "replay.jsonl.gz")},
        "auditor_sha256": {"auditor.py": native.file_sha(tmp_path / "auditor.py")}}
    native.write(parent / "independent-audit.json", audit)
    monkeypatch.setattr(native, "committed", lambda paths: None)
    monkeypatch.setattr(native.subprocess, "check_call", lambda *a, **kw: None)
    return args


def test_unqualified_parent_precedes_all_fresh_data_or_model_metadata(tmp_path, monkeypatch):
    args = parent_fixture(tmp_path, monkeypatch)
    summary = native.read(args.development_study / "summary.json")
    summary["qualification"]["shared_flow"]["qualified"] = False
    (args.development_study / "summary.json").write_bytes(native.canonical(summary))
    monkeypatch.setattr(native, "_identity_after_parent", lambda *a: pytest.fail("fresh data/model read before qualification"))
    with pytest.raises(ValueError, match="did not qualify"):
        native.identity(args)
    assert not args.dataset.exists() and not args.model.exists()


def test_parent_uses_actual_new_auditor_flag_without_forging_old_schema(tmp_path, monkeypatch):
    args = parent_fixture(tmp_path, monkeypatch)
    binding = native.parent_binding(args)
    assert binding["study"] == "source_flow" and binding["qualification"]["qualified"]
    path = args.development_study / "independent-audit.json"
    audit = native.read(path)
    audit.pop("candidate_planner_or_producer_modules_imported")
    audit["production_or_producer_modules_imported"] = False
    path.write_bytes(native.canonical(audit))
    with pytest.raises(ValueError, match="actual independent shared-flow audit schema"):
        native.parent_binding(args)


@pytest.mark.parametrize("fault", ["lost_group", "no_new_gain", "changed_source", "audit_changed", "missing_negative"])
def test_invalid_parent_cannot_advance(tmp_path, monkeypatch, fault):
    args = parent_fixture(tmp_path, monkeypatch)
    folder = args.development_study
    summary = native.read(folder / "summary.json")
    if fault == "lost_group":
        summary["qualification"]["shared_flow"]["against_controls"]["coverage_control"]["lost_groups"] = 1
    elif fault == "no_new_gain":
        summary["qualification"]["shared_flow"].update(additional_distinct_complete_queries_vs_coverage_control=[],
                                                         gained_additional_distinct_complete_queries=0)
    elif fault == "changed_source":
        (tmp_path / "producer.py").write_text("changed\n")
    elif fault == "missing_negative":
        summary["referenced_negative_rows"] = 730
    else:
        (folder / "replay.jsonl.gz").write_bytes(b"changed\n")
    (folder / "summary.json").write_bytes(native.canonical(summary))
    with pytest.raises(ValueError):
        native.parent_binding(args)


def test_valid_parent_is_required_before_native_inventory_or_source_preparation(tmp_path, monkeypatch):
    args = parent_fixture(tmp_path, monkeypatch)
    for name in native.NATIVE_SOURCES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic frozen code\n")
    seen = []
    original = native.parent_binding
    def parent(value):
        seen.append("parent")
        return original(value)
    monkeypatch.setattr(native, "parent_binding", parent)
    monkeypatch.setattr(native, "_identity_after_parent", lambda a, p: seen.append("fresh_preparation") or "fixture")
    assert native.identity(args) == "fixture"
    assert seen == ["parent", "fresh_preparation"]


def test_uncommitted_native_code_blocks_fresh_preparation_after_parent_qualification(tmp_path, monkeypatch):
    args = parent_fixture(tmp_path, monkeypatch)
    for name in native.NATIVE_SOURCES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("native code fixture\n")
    def committed(paths):
        if set(paths) == set(native.NATIVE_SOURCES):
            raise ValueError("native code uncommitted")
    monkeypatch.setattr(native, "committed", committed)
    monkeypatch.setattr(native, "_identity_after_parent", lambda *a: pytest.fail("fresh read before native code commit"))
    with pytest.raises(ValueError, match="native code uncommitted"):
        native.identity(args)


def test_native_plan_requires_exact_committed_freeze_blobs(tmp_path, monkeypatch):
    monkeypatch.setattr(native, "ROOT", tmp_path)
    args = options(tmp_path)
    args.output.mkdir()
    (tmp_path / "producer.py").write_text("original source\n")
    native.write(args.output / "source-inventory.json", {})
    binding = {"implementation_sha256": {"producer.py": native.file_sha(tmp_path / "producer.py")},
               "development_parent": {"artifacts": {}}, "definition_preflight": None}
    native.write(args.output / "plan.json", {"study": "source_flow_native", "version": 1, "binding": binding,
        "artifacts": {"source-inventory.json": native.file_sha(args.output / "source-inventory.json")}})
    for command in (["git", "init", "-q"], ["git", "config", "user.name", "Native CPU fixture"],
                    ["git", "config", "user.email", "fixture@example.invalid"],
                    ["git", "add", "."], ["git", "commit", "-qm", "frozen fixture"]):
        subprocess.run(command, cwd=tmp_path, check=True)
    _, chain = native.verify_plan(args, binding)
    assert chain["bound_blob_count"] == 3
    (tmp_path / "producer.py").write_text("changed source\n")
    with pytest.raises(ValueError, match="changed frozen file"):
        native.verify_plan(args, binding)


def snippet(number, *, path="src/code.py", body=None):
    text = f"def unit_{number}():\n    return {body or number}\n"
    return Snippet(str(number), path, f"unit_{number}", number * 10 + 1, number * 10 + 2,
                   text, native.sha(text.encode()))


class Graph:
    def __init__(self, items, edges):
        self.windows = {(item.path, item.symbol): [item] for item in items}
        self.edges = {}
        for a, b in edges:
            self.edges.setdefault((a.path, a.symbol), []).append((b.path, b.symbol))
    def dependencies(self, item, limit):
        return [self.windows[key][0] for key in self.edges.get((item.path, item.symbol), ())[:limit]]


def source_row(item, score=1., origin="retrieval", related_to=None):
    result = native.replay.source_row(item, score, origin, related_to, item.text, physical_lines)
    result["qwen_score"] = None
    return result


def packed(query, pairs, origins):
    rows = [source_row(item, score, *origins.get(item.id, ("retrieval", None))) for item, score in pairs]
    return context.apply_token_budget({"query": query, "scope": context.indexed_scope("src"), "match_status": "candidates", "results": rows,
        "total_source_chars": sum(len(row["source"]) for row in rows), "budget_exhausted": False,
        "timings": {"packaging_s": .001, "total_s": .002}}, 4096)


def synthetic_native(tmp_path, monkeypatch, *, fault=None, blocked=False):
    args = options(tmp_path)
    args.output.mkdir()
    args.tokenizer_cache.mkdir()
    (args.tokenizer_cache / native.replay.CACHE_KEY).write_bytes(b"fixture ranks")
    monkeypatch.setattr(native.replay, "CACHE_SHA256", native.sha(b"fixture ranks"))
    items = [snippet(number) for number in range(1, 9)] + [snippet(9, path="src/caller.py", body="unit_1()")]
    raw = [(item, 1 / rank) for rank, item in enumerate(items, 1)]
    graph_items, edges = list(items), [(items[8], items[0])]
    if blocked:
        a, b, parent = snippet(20), snippet(21), snippet(30)
        graph_items += [a, b, parent]
        edges += [(items[0], a), (items[0], b), (parent, items[8]), (parent, items[5])]
    graph = Graph(graph_items, edges)
    definitions = {item.id: (item.path, item.symbol, item.start_line, item.end_line) for item in graph_items}
    sources = {}
    for item in graph_items:
        lines = sources.setdefault(item.path, ["\n"] * max(400, item.end_line))
        lines[item.start_line - 1:item.end_line] = physical_lines(item.text)
    sources = {path: "".join(lines) for path, lines in sources.items()}
    def target(item):
        return {"path": item.path, "symbol": item.symbol, "evidence_lines": [item.start_line, item.end_line],
                "evidence_sha256": item.content_hash}
    queries = []
    for number in range(14):
        query = {"id": f"q{number}", "query": f"alpha beta gamma and delta epsilon zeta q{number}",
                 "kind": "absent" if number >= 12 else "present"}
        if number < 8:
            query["target_groups"] = [[target(items[0])], [target(items[8])]]
        elif number < 12:
            query["targets"] = [target(items[0])]
        else:
            query["targets"] = []
        queries.append(query)
    data = {"source_root": "src", "queries": queries}
    inventory = {"inventory": [asdict(item) for item in graph_items], "definition_keys": definitions,
        "graph_edges": [{"caller": list(caller), "targets": [list(target) for target in targets]}
                        for caller, targets in sorted(graph.edges.items())]}
    monkeypatch.setattr(native, "identity", lambda args: (data, sources, inventory, {"gate": native.GATE}))
    monkeypatch.setattr(native, "verify_plan", lambda *a: ({"binding": {"gate": native.GATE}},
        {"commit": "fixture", "committed_at_unix": 0, "bound_blob_count": 1}))
    monkeypatch.setattr(native, "relative", lambda path: "fixture")
    native.write(args.output / "plan.json", {"binding": {"gate": native.GATE}})
    # Initialize the actual local tokenizer before the intentionally fake loader
    # bytes are used to exercise runner bookkeeping; no network is permitted.
    context.encoding()
    class Embedder:
        def __init__(self, *a, **kw):
            self.query_calls = self.document_calls = 0
        def embed_query(self, query):
            self.query_calls += 1
            self.last_query = np.zeros(1024, dtype=np.float32)
            self.last_query[0] = 1.
            self.last_query[1] = float(self.query_calls) if fault == "vector" else 0.
            return self.last_query
    class Backend:
        def __init__(self, *a, **kw):
            self.embedder = kw["embedder"]
            self._definition_keys, self._references = definitions, graph
            self.calls = 0
        def status(self):
            return {"fixture": True}
        def close(self):
            pass
        def search(self, text, retrieval, **settings):
            self.calls += 1
            if fault == "second" and self.calls == 8:
                raise RuntimeError("fixture second measured request")
            self.embedder.embed_query(text)
            if self.calls == 1:
                self.embedder.document_calls += 1
            if fault == "extra" and self.calls > 6:
                self.embedder.embed_query(text)
            selected, eligible, origins = backend_module.select_context(raw, query=text, max_results=8,
                include_dependencies=True, references=graph, definition_keys=definitions, ambiguous_symbols=frozenset())
            before = packed(text, eligible, origins)
            def reference():
                a, b, c = selection.definition_context(raw, query=text, max_results=8, include_dependencies=True,
                    references=graph, definition_keys=definitions, ambiguous_symbols=frozenset())
                return native.coalesce_context(packed(text, b, c),
                    (source_row(item, score) for item, score in a), max_results=8,
                    source_budget=12000, token_budget=4096)
            plan = getattr(eligible, "source_plan", None)
            result = backend_module.postprocess_context(before, (source_row(item, score) for item, score in selected),
                max_results=8, source_budget=12000, token_budget=4096, reference_factory=reference,
                ranked_candidates=(source_row(item, score) for item, score in raw), definition_keys=definitions,
                **({"selection_plan": plan} if plan is not None else {}))
            result["timings"] = {"packaging_s": float(self.calls), "total_s": float(self.calls) + .1}
            return result
    monkeypatch.setattr(native, "RecordingEmbedder", Embedder)
    monkeypatch.setattr(backend_module, "SearchBackend", Backend)
    args.fixture = data, sources, inventory
    return args


@pytest.mark.parametrize("blocked", [False, True])
def test_full_252_request_synthetic_loop_captures_actual_calls_and_guard_fallback(tmp_path, monkeypatch, blocked):
    args = synthetic_native(tmp_path, monkeypatch, blocked=blocked)
    native.run(args)
    summary = native.read(args.output / "summary.json")
    assert summary["requests"] == summary["expected_requests"] == summary["measured_query_embeddings"] == 252
    assert summary["measured_document_embedding_calls"] == 0 and summary["total_query_embeddings"] == 258
    assert summary["setup"]["query_calls"] == 6
    assert len(summary["setup"]["warmups"]) == 6
    assert all(row["query_embedding_calls"] == 1 for row in summary["setup"]["warmups"])
    with gzip.open(args.output / "searches.jsonl.gz", "rt") as stream:
        rows = [json.loads(line) for line in stream]
    assert len(rows) == 252 and sum(row["arm"] == "candidate" for row in rows) == 84
    for row in rows:
        assert row["document_embedding_calls"] == 0 and row["query_embedding_calls"] == 1
        assert row["compact_payload"] == context.serialize_context(context.compact_result(row["response"]))
        expected = 1 if row["arm"] == "candidate" else 0
        assert row["flow_planner_calls"] == row["base_planner_calls"] == row["guard_calls"] == row["reference_calls"] == expected
        if expected:
            assert row["flow_receipt"]["accepted"] is (not blocked)
            if blocked:
                assert native.source_view(row["response"]) == native.source_view(row["reference_response"])
    # Feed the actual complete fake producer ledger through the independently
    # implemented trace/packing/guard/schedule/vector/metrics verifier. This
    # deliberately shares no producer-generated replacement trace or metrics.
    spec = importlib.util.spec_from_file_location("fake_flow_native_independent_audit",
        Path(__file__).resolve().parents[1] / "scripts/audit_flow_native.py")
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    data, sources, inventory = args.fixture
    fixture = {"inventory": {item["id"]: item for item in inventory["inventory"]},
        "definition_keys": inventory["definition_keys"], "sources": sources,
        "graph_edges": {tuple(item["caller"]): [tuple(target) for target in item["targets"]]
                        for item in inventory["graph_edges"]}}
    queries = {query["id"]: query for query in data["queries"]}
    for row in rows:
        audit.verify_trace(row, fixture, context.encoding(), query=queries[row["query_id"]]["query"],
                           scope=context.indexed_scope("src"))
        groups = native.replay.groups(queries[row["query_id"]])
        assert audit.strict_ranks(row["response"]["results"], groups) == row["delivered_ranks"]
    audit.verify_repeats(rows, data["queries"])
    audit.verify_warmups(summary["setup"])
    assert audit.native_metrics(rows) == summary["metrics"]
    with pytest.raises(ValueError, match="never overwrite, resume or retry"):
        native.run(args)


@pytest.mark.parametrize("fault", ["second", "extra", "vector"])
def test_partial_native_attempt_preserves_readable_completed_traces_and_refuses_retry(tmp_path, monkeypatch, fault):
    args = synthetic_native(tmp_path, monkeypatch, fault=fault)
    with pytest.raises((ValueError, RuntimeError)):
        native.run(args)
    failure = native.read(args.output / "failure.json")
    assert failure["phase"] == "measured_calls" and not failure["resume_allowed"]
    with gzip.open(args.output / "searches.jsonl.gz", "rt") as stream:
        rows = [json.loads(line) for line in stream]
    assert len(rows) == failure["completed_requests"] == (0 if fault == "extra" else 1)
    assert not (args.output / "summary.json").exists()
    with pytest.raises(ValueError, match="never overwrite, resume or retry"):
        native.run(args)


def test_setup_failure_is_claimed_and_cannot_retry(tmp_path, monkeypatch):
    args = options(tmp_path)
    args.output.mkdir()
    native.write(args.output / "plan.json", {})
    monkeypatch.setattr(native, "identity", lambda *a: ({}, {}, {}, {}))
    monkeypatch.setattr(native, "verify_plan", lambda *a: ({}, {"committed_at_unix": 0}))
    with pytest.raises(FileNotFoundError):
        native.run(args)
    failure = native.read(args.output / "failure.json")
    assert failure["phase"] == "setup" and failure["completed_requests"] == failure["attempted_requests"] == 0
    assert native.read(args.output / "run-started.json")["resume_allowed"] is False
    with pytest.raises(ValueError, match="never overwrite, resume or retry"):
        native.run(args)


def test_capture_defers_unused_iterator_drains_and_diagnostic_deepcopy(tmp_path, monkeypatch):
    item = snippet(1)
    definitions = {item.id: (item.path, item.symbol, item.start_line, item.end_line)}
    touched = []
    def candidates(name):
        touched.append(name)
        yield source_row(item)
    timed = [True]
    original = native.base.deepcopy
    monkeypatch.setattr(native.base, "deepcopy", lambda value: pytest.fail("diagnostic copy in timer") if timed[0] else original(value))
    with native.capture("legacy") as state:
        backend_module.select_context([(item, 1.)], query=QUERY, max_results=8, include_dependencies=False,
            references=None, definition_keys=definitions, ambiguous_symbols=frozenset())
        result = backend_module.postprocess_context(packed(QUERY, [(item, 1.)], {}), candidates("selected"),
            ranked_candidates=candidates("ranked"), definition_keys=definitions,
            max_results=8, source_budget=12000, token_budget=4096)
    assert touched == []
    timed[0] = False
    query = {"id": "fake", "query": QUERY, "kind": "absent", "targets": []}
    native.receipt(query, 1, "dense", "legacy", "legacy", result, state, .01, np.array([1., 0.]))
    assert touched == ["selected", "ranked"]


QUERY = "alpha beta gamma and delta epsilon zeta"


def metric_row(mode, arm, ranks, *, elapsed=1., multi=True, identifier="q"):
    return {"query_id": identifier, "retrieval": mode, "arm": arm, "repetition": 1,
        "kind": "present", "multi_file": multi, "delivered_ranks": ranks, "elapsed_backend_s": elapsed,
        "stages": {"selected": []}, "response": {"match_status": "candidates", "results": []},
        "flow_receipt": {"accepted": True, "base_plan": {"activated": True}},
        "source_guard_receipt": {"accepted": True}, "compact_sha256": "same"}


@pytest.mark.parametrize("legacy,coverage,candidate,passed", [(1., .8, 1.1, False), (.8, 1., 1.1, False),
                                                            (1., 2., 1.25, True), (2., 1., 1.25, True)])
def test_native_latency_and_incremental_quality_gate_use_both_controls(legacy, coverage, candidate, passed):
    times = {"legacy": legacy, "coverage_control": coverage, "candidate": candidate}
    rows = [metric_row(mode, arm, [1, 2] if arm == "candidate" else [1, None], elapsed=times[arm])
            for mode in native.RETRIEVALS for arm in native.ARMS]
    value = native.summarize(rows)
    assert value["dense_promotion_gate_passed"] is passed
    assert all(mode["median_wall_ratio_to_controls"] == {"legacy": candidate / legacy, "coverage_control": candidate / coverage}
               for mode in value["per_retrieval"].values())


def test_partial_group_loss_fails_dense_while_hybrid_cannot_rescue():
    rows = []
    for mode in native.RETRIEVALS:
        for arm in native.ARMS:
            rows.append(metric_row(mode, arm, [1, 2] if arm == "candidate" else [1, None]))
            rows.append(metric_row(mode, arm, [None, 2, None] if mode == "dense" and arm == "candidate" else [1, None, None], identifier="partial"))
    value = native.summarize(rows)
    assert not value["dense_promotion_gate_passed"] and value["per_retrieval"]["hybrid"]["passed"]
    assert value["per_retrieval"]["dense"]["against_controls"]["coverage_control"]["group_losses"]


def test_freeze_is_source_only_and_requires_new_directory(tmp_path, monkeypatch):
    args = options(tmp_path)
    monkeypatch.setattr(native, "identity", lambda args: ({"queries": []}, {}, {"inventory": [], "definition_keys": {}, "graph_edges": []}, {}))
    monkeypatch.setattr(native, "RecordingEmbedder", lambda *a, **k: pytest.fail("model during freeze"))
    plan = native.freeze(args)
    assert plan["study"] == "source_flow_native" and plan["quality_repetition"] == 1
    assert sorted(path.name for path in args.output.iterdir()) == ["plan.json", "source-inventory.json"]
    with pytest.raises(ValueError, match="new native freeze directory"):
        native.freeze(args)
