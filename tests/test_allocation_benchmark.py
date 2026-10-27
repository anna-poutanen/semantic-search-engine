"""Synthetic CPU contracts for the scoped allocation native benchmark."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import benchmark_allocation as benchmark
import benchmark_windows as native
from semantic_search import allocation, backend as backend_module, context, embeddings, selection
from semantic_search.corpus import Snippet
from semantic_search.source import physical_lines


SPEC = importlib.util.spec_from_file_location("allocation_native_fixture_helpers",
    Path(__file__).with_name("test_window_benchmark.py"))
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)


QUERY = "alpha beta gamma and delta epsilon zeta"


def items():
    result = []
    for number in range(1, 10):
        text = "one\n" + ("delta epsilon zeta\n" if number == 9 else "alpha beta gamma\n")
        result.append(Snippet(str(number), f"src/unit{number}.py", f"unit{number}", 1, 2,
                              text, native.sha(text.encode())))
    return result


def source_row(item, score=1., origin="retrieval", related_to=None):
    return native.replay.source_row(item, score, origin, related_to, item.text, physical_lines)


def packed(query, eligible, origins):
    rows = [source_row(item, score, *origins.get(item.id, ("retrieval", None)))
            for item, score in eligible]
    return context.apply_token_budget({"query": query, "match_status": "candidates", "results": rows,
        "total_source_chars": sum(len(row["source"]) for row in rows), "budget_exhausted": False}, 4096)


def options(tmp_path, candidate="aspect_fill"):
    value = fixtures.args(tmp_path)
    value.candidate = candidate
    return value


def test_profile_is_scoped_matches_replay_and_keeps_production_aliases_unchanged():
    import replay_allocation
    old_conditions, old_candidates = native.CONDITIONS, native.CANDIDATES
    old_selector, old_postprocessor = selection.select_context, selection.postprocess_context
    assert benchmark.CONDITIONS == replay_allocation.CONDITIONS
    with benchmark.profile():
        assert native.CANDIDATES == ("aspect_fill", "aspect_balanced")
        for condition, spec in benchmark.CONDITIONS.items():
            selector, postprocessor = native.hooks(condition)
            assert selector.__name__ == spec["selector"]
            assert postprocessor.__name__ == spec["postprocessor"]
    assert native.CONDITIONS is old_conditions and native.CANDIDATES is old_candidates
    assert selection.select_context is old_selector and selection.postprocess_context is old_postprocessor
    assert not hasattr(selection, "aspect_fill_context")
    assert not hasattr(selection, "aspect_balanced_context")


def test_fourteen_queries_still_schedule_all_252_rotated_native_calls():
    queries = [{"id": f"synthetic-{number}"} for number in range(14)]
    schedule = list(native.schedule(queries))
    assert len(schedule) == 252
    assert {(rep, item["id"], arm, retrieval) for rep, item, arm, retrieval in schedule} == {
        (rep, item["id"], arm, retrieval) for rep in (1, 2, 3) for item in queries
        for arm in native.ARMS for retrieval in native.RETRIEVALS}


def test_actual_planner_receipt_is_captured_once_by_reference_then_copied_after_timer(monkeypatch):
    snippets = items()
    pairs = [(item, 1. / rank) for rank, item in enumerate(snippets, 1)]
    keys = {item.id: (item.path, item.symbol, 1, 2) for item in snippets}
    original, actual, copied, inside_timer = allocation.allocation_plan, [], [], [False]
    def live(*args, **kwargs):
        result = original(*args, **kwargs)
        actual.append(result[3])
        return result
    def copy_after(value):
        assert not inside_timer[0]
        copied.append(value)
        return deepcopy(value)
    monkeypatch.setattr(allocation, "allocation_plan", live)
    monkeypatch.setattr(benchmark, "deepcopy", copy_after)
    with benchmark.profile(), benchmark.capture("aspect_fill") as captured:
        inside_timer[0] = True
        selected, eligible, origins = backend_module.select_context(pairs, query=QUERY,
            max_results=8, include_dependencies=False, references=None, definition_keys=keys,
            ambiguous_symbols=frozenset())
        response = backend_module.postprocess_context(packed(QUERY, eligible, origins),
            (source_row(item, score) for item, score in selected),
            ranked_candidates=(source_row(item, score) for item, score in pairs),
            definition_keys=keys, max_results=8, source_budget=12000, token_budget=4096)
        assert copied == [] and len(actual) == 1
        inside_timer[0] = False
    assert copied == [actual[0]] and copied[0] is actual[0]
    assert captured["allocation_receipt"] == actual[0]
    assert captured["allocation_receipt"] is not actual[0]
    outcome = benchmark.receipt({"id": "synthetic", "kind": "absent", "query": QUERY, "targets": []},
        1, "dense", "candidate", "aspect_fill", response, captured, .01, np.array([1., 0.], dtype=np.float32))
    assert outcome["allocation_planner_calls"] == 1 and outcome["allocation_receipt"]["accepted"]


@pytest.mark.parametrize("condition", ["legacy", "coverage_control"])
def test_controls_never_call_allocation_planner(condition, monkeypatch):
    item = items()[0]
    monkeypatch.setattr(allocation, "allocation_plan", lambda *a, **kw: pytest.fail("control invoked planner"))
    with benchmark.profile(), benchmark.capture(condition) as captured:
        backend_module.select_context([(item, 1.)], query=QUERY, max_results=8,
            include_dependencies=False, references=None, definition_keys={}, ambiguous_symbols=frozenset())
    assert captured["allocation_planner_calls"] == 0 and captured["allocation_receipt"] is None


def test_duplicate_planning_is_rejected_instead_of_recomputed_for_trace(monkeypatch):
    original = allocation.aspect_fill_context
    def twice(*args, **kwargs):
        original(*args, **kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(allocation, "aspect_fill_context", twice)
    with pytest.raises(ValueError, match="exactly one"), benchmark.profile(), benchmark.capture("aspect_fill"):
        backend_module.select_context([(items()[0], 1.)], query=QUERY, max_results=8,
            include_dependencies=False, references=None, definition_keys={}, ambiguous_symbols=frozenset())


@pytest.mark.parametrize("role", ["repository_holdout", "development_warm_timing"])
def test_every_native_role_requires_qualified_allocation_parent_before_source_or_model(tmp_path, role):
    args = options(tmp_path)
    args.role = role
    with pytest.raises(ValueError, match="qualified development parent"):
        benchmark.identity(args)
    with pytest.raises(ValueError, match="qualified development parent"):
        benchmark.parent_binding(args)


def test_parent_rejects_old_study_even_if_it_has_qualification_flags(tmp_path):
    args = options(tmp_path)
    args.development_study = tmp_path / "parent"
    args.development_study.mkdir()
    for name in ("summary.json", "frozen-plan.json", "independent-audit.json"):
        native.write(args.development_study / name, {"study": "windows"})
    with pytest.raises(ValueError, match="own allocation development study"):
        benchmark.parent_binding(args)


def test_parent_includes_history_manifests_and_validates_current_freeze_ancestry(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark, "ROOT", tmp_path)
    args = options(tmp_path)
    args.development_study = folder = tmp_path / "parent"
    folder.mkdir()
    for name in (*benchmark.PARENT_EXTRAS, "baseline-gate.json", "prior-coverage-gate.json", "source-fixtures.json"):
        native.write(folder / name, {})
    historical = tmp_path / "original.json"
    historical.write_text("immutable historical input\n")
    plan = {"study": "allocation", "producer_sha256": {}, "input_sha256": {},
        "historical_parent": {"artifacts": {"original.json": native.file_sha(historical)}},
        "artifacts": {name: native.file_sha(folder / name) for name in benchmark.PARENT_EXTRAS}}
    native.write(folder / "frozen-plan.json", plan)
    native.write(folder / "summary.json", {"study": "allocation"})
    native.write(folder / "independent-audit.json", {"study": "allocation"})
    native.write(folder / "control-gate.json", {"exact_parent_control_rows": 730})
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "CPU fixture"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "fixture@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "Prospective fixture"], check=True)
    revision = subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()
    stamp = int(subprocess.check_output(["git", "-C", str(tmp_path), "show", "-s", "--format=%ct", revision], text=True))
    proof = {"commit": revision, "committed_at_unix": stamp, "bound_blob_count": 7}
    native.write(folder / "run-start.json", {"prospective_freeze": proof,
        "execution_commit": revision, "created_at_utc": datetime.now(timezone.utc).isoformat()})
    (folder / "independent-audit.json").write_bytes(native.canonical({
        "study": "allocation", "producer_execution": revision, "producer_freeze": proof}))
    monkeypatch.setattr(benchmark, "_base_parent", lambda args: {"artifacts": {}, "qualification": {"qualified": True}})
    with benchmark.profile():
        bound = benchmark.parent_binding(args)
    assert set(bound["artifacts"]) == {"parent/" + name for name in benchmark.PARENT_EXTRAS} | {"original.json"}
    assert bound["prospective_freeze"]["commit"] == revision
    plan["input_sha256"] = {"original.json": "wrong digest"}
    (folder / "frozen-plan.json").write_bytes(native.canonical(plan))
    with benchmark.profile(), pytest.raises(ValueError, match="freeze blob changed"):
        benchmark.parent_binding(args)


def test_identity_adds_all_allocation_source_test_and_protocol_bindings(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark, "ROOT", tmp_path)
    args = options(tmp_path)
    args.development_study = tmp_path / "qualified"
    for name in benchmark.EXTRA_BINDINGS:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("prospective allocation fixture\n")
    binding = {"implementation_sha256": {}, "development_parent": {"qualification": {"qualified": True}}}
    monkeypatch.setattr(benchmark, "_base_identity", lambda args: ({}, {}, {}, binding))
    assert benchmark.identity(args)[3]["study"] == "allocation"
    assert binding["implementation_sha256"] == {name: native.file_sha(tmp_path / name) for name in benchmark.EXTRA_BINDINGS}


def test_exclusive_writer_sets_accurate_study_before_writing_without_overwriting(tmp_path):
    path = tmp_path / "plan.json"
    benchmark.write(path, {"candidate_reason": "old window label"})
    assert native.read(path)["study"] == "allocation"
    assert "complementary-definition allocation" in native.read(path)["candidate_reason"]
    with pytest.raises(FileExistsError):
        benchmark.write(path, {})


def test_source_only_freeze_uses_profile_and_exclusive_allocation_metadata(tmp_path, monkeypatch):
    args = options(tmp_path)
    item = items()[0]
    inventory = {"inventory": [asdict(item)], "definition_keys": {item.id: (item.path, item.symbol, 1, 2)}}
    monkeypatch.setattr(benchmark, "identity", lambda args: (
        {"queries": [{"id": "synthetic", "query": QUERY, "kind": "absent"}]}, {}, inventory, {"gate": native.GATE}))
    monkeypatch.setattr(backend_module, "SearchBackend", lambda *a, **kw: pytest.fail("backend during freeze"))
    plan = benchmark.freeze(args)
    saved = native.read(args.output / "plan.json")
    assert saved["study"] == "allocation"
    assert "complementary-definition allocation" in saved["candidate_reason"]
    assert plan["binding"] == saved["binding"] and plan["quality_repetition"] == 1
    assert sorted(path.name for path in args.output.iterdir()) == ["plan.json", "source-inventory.json"]
    with pytest.raises(ValueError, match="new freeze directory"):
        benchmark.freeze(args)


def test_native_verification_checks_frozen_blobs_beyond_current_head(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark, "ROOT", tmp_path)
    args = options(tmp_path)
    args.output.mkdir()
    producer = tmp_path / "producer.py"
    producer.write_text("original prospective implementation\n")
    binding = {"implementation_sha256": {"producer.py": native.file_sha(producer)},
               "development_parent": {"artifacts": {}}}
    plan = {"study": "allocation", "binding": binding, "artifacts": {}}
    native.write(args.output / "plan.json", plan)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "CPU fixture"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "fixture@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "Freeze native fixture"], check=True)
    monkeypatch.setattr(benchmark, "_base_verify", lambda args, current: plan)
    with benchmark.profile():
        assert benchmark.verify_plan(args, binding) == plan
    producer.write_text("later implementation cannot impersonate frozen code\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "producer.py"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "Later fixture"], check=True)
    binding["implementation_sha256"]["producer.py"] = native.file_sha(producer)
    with benchmark.profile(), pytest.raises(ValueError, match="freeze blob changed"):
        benchmark.verify_plan(args, binding)


@pytest.mark.parametrize("candidate,fault", [
    ("aspect_fill", None), ("aspect_balanced", None),
    ("aspect_fill", "extra_embedding"), ("aspect_fill", "changed_vector"),
])
def test_complete_252_request_native_workflow_with_cpu_fakes_preserves_receipts_or_failures(
        tmp_path, monkeypatch, candidate, fault):
    args = options(tmp_path, candidate)
    args.output.mkdir()
    args.tokenizer_cache.mkdir()
    (args.tokenizer_cache / native.replay.CACHE_KEY).write_bytes(b"synthetic local ranks")
    monkeypatch.setattr(native.replay, "CACHE_SHA256", native.sha(b"synthetic local ranks"))
    snippets = items()
    definitions = {item.id: (item.path, item.symbol, 1, 2) for item in snippets}
    inventory = {"inventory": [asdict(item) for item in snippets], "definition_keys": definitions}
    sources = {item.path: item.text for item in snippets}
    targets = [{"path": snippets[number].path, "symbol": snippets[number].symbol,
                "evidence_lines": [2, 2],
                "evidence_sha256": native.sha(physical_lines(snippets[number].text)[1].encode())}
               for number in (0, 8)]
    queries = [{"id": f"synthetic-{number}", "query": QUERY + f" number {number}",
                "kind": "present" if number < 12 else "absent",
                "targets": targets if number < 8 else targets[:1] if number < 12 else []}
               for number in range(14)]
    data, binding = {"source_root": "src", "queries": queries}, {"gate": native.GATE}
    monkeypatch.setattr(benchmark, "identity", lambda args: (data, sources, inventory, binding))
    monkeypatch.setattr(benchmark, "verify_plan", lambda args, current: {"binding": current})
    monkeypatch.setattr(native, "relative", lambda path: "synthetic")
    benchmark.write(args.output / "plan.json", {"binding": binding})
    class FakeEmbedder:
        def __init__(self, *args, **kwargs):
            self.calls = 0
        def embed_query(self, query):
            self.calls += 1
            return np.array([1., float(self.calls) if fault == "changed_vector" else 0.], dtype=np.float32)
    class Graph:
        def dependencies(self, item, limit):
            return []
    class FakeBackend:
        def __init__(self, *args, embedder, **kwargs):
            self.embedder, self._definition_keys, self.calls = embedder, definitions, 0
        def status(self):
            return {"fixture": True}
        def close(self):
            pass
        def search(self, query_text, retrieval, **kwargs):
            self.calls += 1
            self.embedder.embed_query(query_text)
            if fault == "extra_embedding" and self.calls > 6:
                self.embedder.embed_query(query_text)
            pairs = [(item, 1. / rank) for rank, item in enumerate(snippets, 1)]
            selected, eligible, origins = backend_module.select_context(pairs, query=query_text,
                max_results=8, include_dependencies=True, references=Graph(), definition_keys=definitions,
                ambiguous_symbols=frozenset())
            return backend_module.postprocess_context(packed(query_text, eligible, origins),
                (source_row(item, score) for item, score in selected),
                ranked_candidates=(source_row(item, score) for item, score in pairs), definition_keys=definitions,
                max_results=8, source_budget=12000, token_budget=4096, cancel_check=lambda: None)
    monkeypatch.setattr(embeddings, "QwenEmbedder", FakeEmbedder)
    monkeypatch.setattr(backend_module, "SearchBackend", FakeBackend)
    if fault:
        with pytest.raises(ValueError, match="exactly one fresh query embedding" if fault == "extra_embedding"
                           else "query vectors changed"):
            benchmark.run(args)
        assert native.read(args.output / "failure.json")["phase"] == "measured_calls"
        assert native.read(args.output / "failure.json")["study"] == "allocation"
        assert not (args.output / "summary.json").exists()
        with pytest.raises(ValueError, match="Attempt already exists"):
            benchmark.run(args)
        return
    benchmark.run(args)
    summary = native.read(args.output / "summary.json")
    assert summary["study"] == "allocation"
    assert summary["requests"] == summary["expected_requests"] == summary["measured_query_embeddings"] == 252
    assert summary["setup"]["query_calls"] == 6 and summary["total_query_embeddings"] == 258
    with gzip.open(args.output / "searches.jsonl.gz", "rt") as stream:
        outcomes = [json.loads(line) for line in stream]
    assert len(outcomes) == 252
    assert all(row["allocation_planner_calls"] == int(row["arm"] == "candidate") for row in outcomes)
    assert all((row["allocation_receipt"] is None) == (row["arm"] != "candidate") for row in outcomes)
    assert all(row["allocation_receipt"]["accepted"] for row in outcomes if row["arm"] == "candidate")
    assert all(row["compact_payload"] == context.serialize_context(context.compact_result(row["response"]))
               for row in outcomes)
    assert all(metrics["allocation_activity"]["changed_allocation_calls"] == 42
               for metrics in summary["metrics"]["per_retrieval"].values())
    with pytest.raises(ValueError, match="Attempt already exists"):
        benchmark.run(args)
