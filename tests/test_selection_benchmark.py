"""Synthetic CPU contracts; never index, retrieve or load embedding weights."""
import copy
from dataclasses import asdict
import gzip
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import benchmark_selection as benchmark
from semantic_search import backend as backend_module, context
from semantic_search.corpus import Snippet
from semantic_search.source import physical_lines


def snippet(identifier, source, start=1, path="src/code.py"):
    return Snippet(identifier, path, identifier, start,
                   start + len(physical_lines(source)) - 1, source, benchmark.sha(source.encode()))


def source_row(item, origin="retrieval", related_to=None):
    return benchmark.replay.source_row(item, 1.0, origin, related_to, item.text, physical_lines)


def packed(query, items):
    rows = [source_row(item) for item in items]
    return context.apply_token_budget({"query": query, "match_status": "candidates",
        "results": rows, "total_source_chars": sum(len(row["source"]) for row in rows),
        "budget_exhausted": False}, 4096)


def query(identifier="q", *, kind="present"):
    return {"id": identifier, "query": "Describe behavior " + identifier, "kind": kind,
            "targets": [] if kind == "absent" else [
                {"path": "src/code.py", "symbol": "navigation only", "evidence_lines": [2, 2]}]}


def args(tmp_path, **values):
    return SimpleNamespace(dataset=tmp_path / "dataset.json", corpus=tmp_path / "corpus",
        model=tmp_path / "model", output=tmp_path / "study", source_review=None,
        development_study=None, role="development_warm_timing", candidate="definition_coverage_backfill",
        repetitions=3, tokenizer_cache=tmp_path / "tokenizers", **values)


def test_schedule_covers_every_query_arm_mode_three_times_and_rotates():
    queries = [query("one"), query("two", kind="absent")]
    schedule = list(benchmark.schedule(queries))
    assert len(schedule) == 24
    for repetition in (1, 2, 3):
        for item in queries:
            assert {(arm, retrieval) for rep, q, arm, retrieval in schedule
                    if rep == repetition and q == item} == {
                ("baseline", "dense"), ("candidate", "dense"),
                ("baseline", "hybrid"), ("candidate", "hybrid")}
    assert schedule[0][2:] == ("baseline", "dense")
    assert schedule[4][2:] == schedule[8][2:] == ("candidate", "dense")


def test_six_frozen_hook_conditions_keep_legacy_default_and_only_two_postprocess():
    assert benchmark.CONDITIONS == benchmark.replay.CONDITIONS
    assert len(benchmark.CONDITIONS) == 6
    for condition in benchmark.CONDITIONS:
        selector, postprocessor = benchmark.hooks(condition)
        assert selector.__name__ == benchmark.CONDITIONS[condition]
        assert (postprocessor is benchmark.coalesce_context) == condition.endswith("_coverage_backfill")
    with pytest.raises(ValueError):
        benchmark.hooks("not_a_condition")


def test_capture_records_native_stages_and_drains_unused_candidates_after_timing():
    item = snippet("item", "one\ntwo\n")
    definition_keys = {item.id: (item.path, item.symbol, 1, 2)}
    touched = []
    def candidates():
        touched.append(True)
        yield source_row(item)
    class Graph:
        def dependencies(self, item, limit):
            return []
    with benchmark.capture("legacy") as captured:
        selected, eligible, origins = backend_module.select_context([(item, .7)],
            query="literal query", max_results=8, include_dependencies=True,
            references=Graph(), definition_keys=definition_keys, ambiguous_symbols=frozenset())
        response = backend_module.postprocess_context(packed("literal query", [item]), candidates(),
            max_results=8, source_budget=12000, token_budget=4096)
    assert touched == []  # Baseline hook's lazy behavior is preserved inside the request.
    result = benchmark.receipt(query(), 1, "dense", "baseline", "legacy", response, captured,
                               .01, np.array([1., 0.], dtype=np.float32))
    assert touched == [True]
    assert result["stages"]["ranked"] == [{"snippet": asdict(item), "score": .7}]
    assert result["stages"]["selected"] == result["stages"]["eligible"]
    assert result["stages"]["origins"] == origins
    assert result["stages"]["definition_keys"] == definition_keys
    assert result["stages"]["ambiguous_symbols"] == []
    assert result["backfill_candidates"] == [source_row(item)]
    assert result["pre_postprocess_response"] == response
    assert result["actual_compact_tokens"] == response["token_usage"]["tokens"]
    assert result["delivered_ranks"] == [1]
    assert result["query_vector_sha256"] == benchmark.sha(np.array([1., 0.], dtype=np.float32).tobytes())


def test_absent_queries_are_not_vacuously_counted_as_complete_positives():
    item = snippet("item", "one\ntwo\n")
    response = packed("absent", [item])
    with benchmark.capture("legacy") as captured:
        backend_module.select_context([(item, .7)], query="absent", max_results=8,
            include_dependencies=False, references=None, definition_keys={item.id: (item.path, item.symbol, 1, 2)})
        backend_module.postprocess_context(response, iter([source_row(item)]),
            max_results=8, source_budget=12000, token_budget=4096)
    outcome = benchmark.receipt(query(kind="absent"), 1, "dense", "baseline", "legacy",
                                 response, captured, .01, np.array([1.], dtype=np.float32))
    assert outcome["candidate_ranks"] == outcome["delivered_ranks"] == []
    assert not outcome["multi_file"]


def test_source_hash_full_window_identity_and_exact_token_caps_are_checked():
    item = snippet("item", "one\ntwo\n")
    response = packed("query", [item])
    inventory, sources = [asdict(item)], {item.path: item.text}
    benchmark.checked_response(response, sources, inventory)
    for key, value in (("content_hash", "wrong"), ("window_end_line", 30),
                       ("source_hash", "wrong"), ("truncated", True),
                       ("origin", "dependency"), ("end_line", 1)):
        changed = copy.deepcopy(response)
        changed["results"][0][key] = value
        context.count_context(changed)
        with pytest.raises(ValueError):
            benchmark.checked_response(changed, sources, inventory)
    for key, value in (("budget", 9999), ("encoding", "other"), ("tokens", 1)):
        changed = copy.deepcopy(response)
        changed["token_usage"][key] = value
        with pytest.raises(ValueError):
            benchmark.checked_response(changed, sources, inventory)


def test_parent_qualification_rejects_missing_or_unqualified_fresh_candidate(tmp_path):
    options = args(tmp_path)
    options.role = "repository_holdout"
    with pytest.raises(ValueError, match="qualified committed development parent"):
        benchmark.parent_binding(options)
    options.development_study = tmp_path / "parent"
    options.development_study.mkdir()
    benchmark.write(options.development_study / "summary.json", {"qualification": {
        options.candidate: {"qualified": False}}})
    with pytest.raises(ValueError, match="did not qualify"):
        benchmark.parent_binding(options)


def test_qualified_parent_binds_original_plan_control_gate_raw_and_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark, "ROOT", tmp_path)
    options = args(tmp_path)
    options.role = "repository_holdout"
    options.development_study = tmp_path / "parent"
    options.development_study.mkdir()
    parent = options.development_study
    benchmark.write(parent / "frozen-plan.json", {"conditions": benchmark.CONDITIONS,
        "settings": benchmark.SETTINGS, "selection": {options.candidate: "Declared condition before fresh outcomes"}})
    for name in ("baseline-gate.json", "control-gate.json"):
        benchmark.write(parent / name, {"exact_rows": 337})
    (parent / "replay.jsonl.gz").write_bytes(b"frozen observed raw bytes")
    benchmark.write(parent / "summary.json", {"qualification": {options.candidate: {"qualified": True}},
        "model_calls": 0, "retrieval_calls": 0, "baseline_exact_rows": 337,
        "plan_sha256": benchmark.file_sha(parent / "frozen-plan.json"),
        "replay_sha256": benchmark.file_sha(parent / "replay.jsonl.gz")})
    binding = benchmark.parent_binding(options)
    assert len(binding["artifacts"]) == 5
    assert binding["qualification"]["qualified"]
    assert binding["condition_description"] == "Declared condition before fresh outcomes"
    (parent / "replay.jsonl.gz").write_bytes(b"changed")
    with pytest.raises(ValueError, match="artifact/configuration mismatch"):
        benchmark.parent_binding(options)


def test_checkpoint_binding_hashes_actual_weights_and_config_without_loading_them(tmp_path):
    from semantic_search.embeddings import DIMENSIONS, MODEL_ID, MODEL_REVISION, PROVENANCE_FILE
    benchmark.write(tmp_path / PROVENANCE_FILE, {"id": MODEL_ID, "revision": MODEL_REVISION})
    benchmark.write(tmp_path / "config.json", {"model_type": "qwen3", "hidden_size": DIMENSIONS})
    (tmp_path / "model.safetensors").write_bytes(b"synthetic weights; never loaded")
    original = benchmark.checkpoint_binding(tmp_path)
    assert original["model.safetensors"] == benchmark.sha(b"synthetic weights; never loaded")
    (tmp_path / "model.safetensors").write_bytes(b"changed synthetic weights")
    assert benchmark.checkpoint_binding(tmp_path) != original
    (tmp_path / "config.json").write_text(json.dumps({"model_type": "qwen3", "hidden_size": DIMENSIONS,
                                                     "quantization_config": {"bits": 4}}))
    with pytest.raises(ValueError, match="unquantized"):
        benchmark.checkpoint_binding(tmp_path)


def test_freeze_writes_only_source_and_plan_not_outcomes_or_native_calls(tmp_path, monkeypatch):
    options = args(tmp_path)
    data = {"queries": [query()]}
    inventory = {"inventory": [asdict(snippet("item", "one\ntwo\n"))],
                 "definition_keys": {"item": ["src/code.py", "item", 1, 2]}}
    binding = {"gate": benchmark.GATE}
    monkeypatch.setattr(benchmark, "identity", lambda args: (data, {}, inventory, binding))
    monkeypatch.setattr(backend_module, "SearchBackend", lambda *a, **kw: pytest.fail("Native backend during freeze"))
    plan = benchmark.freeze(options)
    assert sorted(path.name for path in options.output.iterdir()) == ["plan.json", "source-inventory.json"]
    assert plan["artifacts"]["source-inventory.json"] == benchmark.file_sha(options.output / "source-inventory.json")
    assert plan["binding"]["gate"] == benchmark.GATE
    assert "query" in plan["queries"][0] and plan["quality_repetition"] == 1
    with pytest.raises(ValueError, match="new freeze directory"):
        benchmark.freeze(options)


def test_unchanged_committed_bindings_are_required_for_plan_and_producer(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark, "ROOT", tmp_path)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "CPU fixture"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "fixture@example.invalid"], check=True)
    source = tmp_path / "producer.py"
    source.write_text("original\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "producer.py"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "CPU fixture"], check=True)
    bindings = {"producer.py": benchmark.file_sha(source)}
    benchmark.committed(bindings)
    source.write_text("changed\n")
    with pytest.raises(ValueError, match="Uncommitted or changed"):
        benchmark.committed(bindings)
    subprocess.run(["git", "-C", str(tmp_path), "add", "producer.py"], check=True)
    with pytest.raises(ValueError, match="Uncommitted or changed"):
        benchmark.committed({"producer.py": benchmark.file_sha(source)})


def metric_row(identifier, retrieval, arm, ranks, *, multi=True, kind="present", elapsed=1.):
    return {"query_id": identifier, "retrieval": retrieval, "arm": arm, "repetition": 1,
        "kind": kind, "multi_file": multi, "delivered_ranks": ranks, "elapsed_backend_s": elapsed,
        "response": {"match_status": "candidates", "results": []}}


def test_per_mode_gate_keeps_partial_group_regressions_and_hybrid_cannot_rescue_dense():
    rows = []
    for retrieval in benchmark.RETRIEVALS:
        rows.extend([metric_row("gain", retrieval, "baseline", [1, None]),
                     metric_row("gain", retrieval, "candidate", [1, 2], elapsed=1.1),
                     metric_row("partial", retrieval, "baseline", [1, None, None]),
                     metric_row("partial", retrieval, "candidate", [None, 2, None] if retrieval == "dense" else [1, None, None], elapsed=1.1),
                     metric_row("absent", retrieval, "baseline", [], kind="absent"),
                     metric_row("absent", retrieval, "candidate", [], kind="absent", elapsed=1.1)])
    result = benchmark.summarize(rows)
    assert not result["dense_promotion_gate_passed"]
    assert result["per_retrieval"]["hybrid"]["passed"]
    assert result["per_retrieval"]["dense"]["group_losses"] == [
        {"query_id": "partial", "group": 1, "baseline_rank": 1, "candidate_rank": None}]
    assert result["per_retrieval"]["dense"]["arms"]["baseline"]["complete_positives"] == 0
    assert len(result["per_retrieval"]["dense"]["arms"]["baseline"]["absent_inventory"]) == 1


def test_wall_gate_uses_actual_same_run_mode_medians_and_single_first_hit_losses():
    rows = []
    for retrieval in benchmark.RETRIEVALS:
        rows.extend([metric_row("gain", retrieval, "baseline", [1, None]),
                     metric_row("gain", retrieval, "candidate", [1, 2], elapsed=1.3),
                     metric_row("single", retrieval, "baseline", [1], multi=False),
                     metric_row("single", retrieval, "candidate", [None], multi=False, elapsed=1.3)])
    result = benchmark.summarize(rows)
    for mode in result["per_retrieval"].values():
        assert mode["median_wall_ratio"] == 1.3
        assert mode["single_file_first_hit_losses"] == ["single"]
        assert mode["complete_losses"] == ["single"]
        assert not mode["passed"]


def test_native_runner_can_be_exercised_with_cpu_fakes_and_retains_full_receipts(tmp_path, monkeypatch):
    from semantic_search import embeddings
    options = args(tmp_path)
    options.output.mkdir()
    options.tokenizer_cache.mkdir()
    (options.tokenizer_cache / benchmark.replay.CACHE_KEY).write_bytes(b"fixture ranks")
    monkeypatch.setattr(benchmark.replay, "CACHE_SHA256", benchmark.sha(b"fixture ranks"))
    parent = snippet("parent", "one\ntwo\n")
    child = snippet("child", "two\n", start=2)
    items = [parent, child]
    definition_keys = {item.id: (item.path, item.symbol, item.start_line, item.end_line) for item in items}
    inventory = {"inventory": [asdict(item) for item in items], "definition_keys": definition_keys}
    sources = {parent.path: parent.text}
    data = {"source_root": "src", "queries": [query(), query("absent", kind="absent")]}
    binding = {"gate": benchmark.GATE}
    monkeypatch.setattr(benchmark, "identity", lambda args: (data, sources, inventory, binding))
    monkeypatch.setattr(benchmark, "verify_plan", lambda args, current: {"binding": current})
    monkeypatch.setattr(benchmark, "relative", lambda path: "fixture")
    benchmark.write(options.output / "plan.json", {"binding": binding})
    class FakeEmbedder:
        def __init__(self, *args, **kwargs):
            pass
        def embed_query(self, query):
            return np.array([1., 0.], dtype=np.float32)
    class Graph:
        def dependencies(self, item, limit):
            return []
    class FakeBackend:
        def __init__(self, *args, embedder, **kwargs):
            self.embedder, self._definition_keys = embedder, definition_keys
        def status(self):
            return {"fixture": True}
        def close(self):
            pass
        def search(self, query_text, retrieval, **kwargs):
            self.embedder.embed_query(query_text)
            selected, eligible, origins = backend_module.select_context([(item, 1.) for item in items],
                query=query_text, max_results=8, include_dependencies=True,
                references=Graph(), definition_keys=definition_keys)
            response = packed(query_text, [item for item, score in eligible])
            candidates = (source_row(item) for item, score in selected)
            return backend_module.postprocess_context(response, candidates, max_results=8,
                source_budget=12000, token_budget=4096, cancel_check=lambda: None)
    monkeypatch.setattr(embeddings, "QwenEmbedder", FakeEmbedder)
    monkeypatch.setattr(backend_module, "SearchBackend", FakeBackend)
    result = benchmark.run(options)
    assert not result["dense_promotion_gate_passed"]
    summary = benchmark.read(options.output / "summary.json")
    assert summary["requests"] == summary["measured_query_embeddings"] == 24
    assert summary["setup"]["query_calls"] == 4
    with gzip.open(options.output / "searches.jsonl.gz", "rt") as stream:
        outcomes = [json.loads(line) for line in stream]
    assert len(outcomes) == 24
    assert all(len(outcome["stages"]["ranked"]) == 2 for outcome in outcomes)
    assert all(len(outcome["pre_postprocess_response"]["results"]) == 2 for outcome in outcomes)
    assert all(outcome["compact_payload"] == context.serialize_context(context.compact_result(outcome["response"])) for outcome in outcomes)
    assert any(len(outcome["response"]["results"]) == 1 for outcome in outcomes if outcome["arm"] == "candidate")
    with pytest.raises(ValueError, match="Attempt already exists"):
        benchmark.run(options)


def test_failed_native_setup_is_claimed_once_and_keeps_failure_receipt(tmp_path, monkeypatch):
    options = args(tmp_path)
    options.output.mkdir()
    benchmark.write(options.output / "plan.json", {"fixture": True})
    monkeypatch.setattr(benchmark, "identity", lambda args: ({}, {}, {}, {}))
    monkeypatch.setattr(benchmark, "verify_plan", lambda args, binding: {})
    # A missing tokenizer fails setup before any embedder/backend is constructed.
    with pytest.raises(FileNotFoundError):
        benchmark.run(options)
    assert (options.output / "run-started.json").is_file()
    failure = benchmark.read(options.output / "failure.json")
    assert failure["phase"] == "setup" and failure["completed_requests"] == 0
    with pytest.raises(ValueError, match="Attempt already exists"):
        benchmark.run(options)
