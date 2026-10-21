"""Synthetic CPU contracts for the prospective three-arm native harness."""
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
import benchmark_windows as benchmark
from semantic_search import backend as backend_module, context
from semantic_search.corpus import Snippet
from semantic_search.source import physical_lines
from semantic_search.window_assembly import join_windows


def snippet(identifier, source, start=1, path="src/code.py", symbol="run"):
    return Snippet(identifier, path, symbol, start, start + len(physical_lines(source)) - 1,
                   source, benchmark.sha(source.encode()))


def source_row(item, origin="retrieval", related_to=None):
    row = benchmark.replay.source_row(item, 1., origin, related_to, item.text, physical_lines)
    if hasattr(item, "assembly"):
        row["assembly"] = copy.deepcopy(item.assembly)
    return row


def packed(query, items, origins=None):
    rows = [source_row(item, *(origins or {}).get(item.id, ("retrieval", None))) for item in items]
    return context.apply_token_budget({"query": query, "match_status": "candidates", "results": rows,
        "total_source_chars": sum(len(row["source"]) for row in rows), "budget_exhausted": False}, 4096)


def query(identifier="q", *, kind="present"):
    return {"id": identifier, "query": "Describe behavior " + identifier, "kind": kind,
            "targets": [] if kind == "absent" else [{"path": "src/code.py", "symbol": "run",
                "evidence_lines": [2, 2], "evidence_sha256": benchmark.sha(b"two\n")} ]}


def args(tmp_path):
    return SimpleNamespace(dataset=tmp_path / "dataset.json", corpus=tmp_path / "corpus",
        model=tmp_path / "model", output=tmp_path / "study", source_review=None,
        definition_inventory=None, development_study=None, role="development_warm_timing",
        candidate="connected_union", repetitions=3, tokenizer_cache=tmp_path / "tokenizers")


def test_schedule_has_three_arms_two_modes_three_real_rotated_repetitions():
    queries = [query("one"), query("two", kind="absent")]
    scheduled = list(benchmark.schedule(queries))
    assert len(scheduled) == 36
    expected = {(arm, retrieval) for arm in benchmark.ARMS for retrieval in benchmark.RETRIEVALS}
    for repetition in (1, 2, 3):
        for item in queries:
            assert {(arm, mode) for rep, q, arm, mode in scheduled if rep == repetition and q == item} == expected
    assert scheduled[0][2:] == ("legacy", "dense")
    assert scheduled[6][2:] == scheduled[12][2:] == ("coverage_control", "dense")


def test_fixed_conditions_and_hooks_match_development_without_exposing_control_as_candidate():
    import replay_windows
    assert benchmark.CONDITIONS == replay_windows.CONDITIONS
    assert benchmark.CANDIDATES == ("connected_union", "guarded_continuation", "guarded_connected_union")
    for name, declaration in benchmark.CONDITIONS.items():
        selector, postprocessor = benchmark.hooks(name)
        assert selector.__name__ == declaration["selector"]
        assert postprocessor.__name__ == declaration["postprocessor"]
    with pytest.raises(ValueError):
        benchmark.hooks("unknown")


def test_both_lazy_candidate_iterators_are_drained_only_after_the_native_call():
    item = snippet("item", "one\ntwo\n")
    definitions = {item.id: (item.path, item.symbol, 1, 2)}
    touched = []
    def candidate(name):
        touched.append(name)
        yield source_row(item)
    with benchmark.capture("legacy") as captured:
        backend_module.select_context([(item, .7)], query="literal", max_results=8,
            include_dependencies=False, references=None, definition_keys=definitions,
            ambiguous_symbols=frozenset())
        response = backend_module.postprocess_context(packed("literal", [item]), candidate("selected"),
            ranked_candidates=candidate("raw"), definition_keys=definitions,
            max_results=8, source_budget=12000, token_budget=4096)
    assert touched == []
    result = benchmark.receipt(query(), 1, "dense", "legacy", "legacy", response, captured,
                               .01, np.array([1., 0.], dtype=np.float32))
    assert touched == ["selected", "raw"]
    assert result["backfill_candidates"] == result["ranked_candidates"] == [source_row(item)]
    assert result["stages"]["ranked"] == [{"snippet": asdict(item), "score": .7}]
    assert result["actual_compact_tokens"] == response["token_usage"]["tokens"]
    assert result["delivered_ranks"] == [1]


def test_holdout_evidence_requires_qualified_symbol_and_literal_full_span_hash():
    item = snippet("item", "one\ntwo\n")
    targets = benchmark.replay.groups(query())
    assert benchmark.evidence_ranks([asdict(item)], targets) == [1]
    assert benchmark.evidence_ranks([{**asdict(item), "symbol": "other"}], targets) == [None]
    assert benchmark.evidence_ranks([{**asdict(item), "text": "one\nWRONG\n"}], targets) == [None]
    assert benchmark.evidence_ranks([asdict(snippet("early", "one\n"))], targets) == [None]
    assert benchmark.evidence_ranks([asdict(item)], []) == []


def test_composite_verifier_accepts_literal_union_and_rejects_forged_source_identity():
    first, second = snippet("a", "one\ntwo\n"), snippet("b", "two\nthree\n", 2)
    key = ("src/code.py", "run", 1, 3)
    joined = join_windows(first, second, definition_key=key, anchor_rank=1, companion_rank=2)
    inventory, definitions = [asdict(first), asdict(second)], {"a": key, "b": key}
    ranked = [{"snippet": asdict(item), "score": 1.} for item in (first, second)]
    sources = {first.path: "one\ntwo\nthree\n"}
    response = packed("query", [joined])
    benchmark.checked_response(response, sources, inventory, definitions, ranked)
    for mutate in (
        lambda row: row.update(id="a"),
        lambda row: row.update(content_hash="0" * 64),
        lambda row: row.update(window_end_line=5),
        lambda row: row.update(qwen_score=.1),
        lambda row: row.update(retrieval_score=.8),
        lambda row: row["assembly"].update(score_anchor_id="b"),
        lambda row: row["assembly"].update(companion_rank=9),
        lambda row: row["assembly"]["members"][1].update(id="unknown"),
        lambda row: row["assembly"]["members"][1].update(used_source_hash="0" * 64),
    ):
        changed = copy.deepcopy(response)
        mutate(changed["results"][0])
        context.count_context(changed)
        with pytest.raises(ValueError):
            benchmark.checked_response(changed, sources, inventory, definitions, ranked)


def test_stage_and_raw_iterator_validation_rejects_invented_index_membership():
    item = snippet("a", "one\ntwo\n")
    inventory = {"inventory": [asdict(item)], "definition_keys": {"a": (item.path, item.symbol, 1, 2)}}
    sources = {item.path: item.text}
    outcome = {"stages": {"ranked": [{"snippet": asdict(item), "score": 1.}]},
        "ranked_candidates": [source_row(item)], "backfill_candidates": [source_row(item)]}
    benchmark.checked_candidate_rows(outcome, sources, inventory)
    outcome["ranked_candidates"] = []
    with pytest.raises(ValueError, match="complete ranked order"):
        benchmark.checked_candidate_rows(outcome, sources, inventory)


def test_postprocessing_preserves_source_and_only_rewrites_verified_parent_ids():
    anchor = source_row(snippet("anchor", "one\ntwo\n"))
    helper = source_row(snippet("helper", "help()\n", path="src/helper.py", symbol="help"),
                        "dependency", "anchor")
    before = {"results": [anchor, helper]}
    after = copy.deepcopy(before)
    after["results"][0].update(id="composite", source="one\ntwo\nthree\n", end_line=3)
    after["results"][1]["related_to"] = "composite"
    after["_window_assembly"] = {"old_to_new_ids": {"anchor": "composite"}}
    benchmark.checked_preservation(before, after)
    after["results"][1]["origin"] = "retrieval"
    with pytest.raises(ValueError, match="retained row"):
        benchmark.checked_preservation(before, after)


def parent_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark, "ROOT", tmp_path)
    options = args(tmp_path)
    options.role, options.development_study = "repository_holdout", tmp_path / "parent"
    folder = options.development_study
    folder.mkdir()
    (tmp_path / "producer.py").write_text("frozen implementation\n")
    (tmp_path / "auditor.py").write_text("independent verifier\n")
    benchmark.write(folder / "frozen-plan.json", {"conditions": benchmark.CONDITIONS, "settings": benchmark.SETTINGS,
        "producer_sha256": {"producer.py": benchmark.file_sha(tmp_path / "producer.py")},
        "selection": {options.candidate: "One declared condition before fresh outcomes"}})
    for name in ("baseline-gate.json", "prior-coverage-gate.json", "source-fixtures.json", "run-start.json"):
        benchmark.write(folder / name, {})
    benchmark.write(folder / "control-gate.json", {"exact_legacy_rows": 365, "exact_prior_coverage_rows": 337,
                                                  "new_variant_rows_before_gate": 0})
    (folder / "replay.jsonl.gz").write_bytes(b"synthetic historical result bytes")
    (folder / "findings.md").write_text("All controls and rejected variants retained.\n")
    qualification = {"qualified": True, "against_controls": {name: {"lost_groups": 0,
        "lost_complete_rows": 0, "lost_single_file_first_hits": 0} for name in ("legacy", "coverage_control")},
        "additional_distinct_complete_queries_vs_coverage_control": ["observed/new-query"],
        "gained_additional_distinct_complete_queries": 1}
    summary = {"qualification": {options.candidate: qualification}, "model_calls": 0, "retrieval_calls": 0,
        "network_calls": 0, "baseline_exact_rows": 365, "prior_coverage_exact_rows": 337,
        "plan_sha256": benchmark.file_sha(folder / "frozen-plan.json"),
        "replay_sha256": benchmark.file_sha(folder / "replay.jsonl.gz")}
    benchmark.write(folder / "summary.json", summary)
    audit = {"status": "passed", "checks_passed": True, "mode": "development",
        "production_or_producer_modules_imported": False, "exact_legacy_controls": 365,
        "exact_prior_coverage_controls": 337, "independently_recomputed_summary": summary,
        "calls": {name: 0 for name in ("model", "index", "retrieval", "network")},
        "artifact_sha256": {name: benchmark.file_sha(folder / name) for name in
                            ("frozen-plan.json", "replay.jsonl.gz", "summary.json")},
        "auditor_sha256": {"auditor.py": benchmark.file_sha(tmp_path / "auditor.py")}}
    benchmark.write(folder / "independent-audit.json", audit)
    return options


def test_parent_requires_dual_controls_new_incremental_gain_and_independent_audit(tmp_path, monkeypatch):
    options = parent_fixture(tmp_path, monkeypatch)
    binding = benchmark.parent_binding(options)
    assert binding["qualification"]["qualified"]
    assert len(binding["artifacts"]) == 12
    summary_path = options.development_study / "summary.json"
    summary = benchmark.read(summary_path)
    summary["qualification"][options.candidate]["against_controls"]["coverage_control"]["lost_groups"] = 1
    summary_path.write_bytes(benchmark.canonical(summary))
    with pytest.raises(ValueError, match="loses evidence"):
        benchmark.parent_binding(options)


def test_parent_known_gains_against_legacy_do_not_count_as_new_coverage_improvement(tmp_path, monkeypatch):
    options = parent_fixture(tmp_path, monkeypatch)
    path = options.development_study / "summary.json"
    summary = benchmark.read(path)
    q = summary["qualification"][options.candidate]
    q["additional_distinct_complete_queries_vs_coverage_control"] = []
    q["gained_additional_distinct_complete_queries"] = 0
    path.write_bytes(benchmark.canonical(summary))
    with pytest.raises(ValueError, match="new distinct complete"):
        benchmark.parent_binding(options)


def test_parent_qualified_flag_alone_and_changed_implementation_cannot_advance(tmp_path, monkeypatch):
    options = parent_fixture(tmp_path, monkeypatch)
    (tmp_path / "producer.py").write_text("changed variant\n")
    with pytest.raises(ValueError, match="producer changed"):
        benchmark.parent_binding(options)
    options.development_study = None
    with pytest.raises(ValueError, match="qualified committed development parent"):
        benchmark.parent_binding(options)


def test_parent_failed_or_changed_independent_audit_is_rejected(tmp_path, monkeypatch):
    options = parent_fixture(tmp_path, monkeypatch)
    path = options.development_study / "independent-audit.json"
    audit = benchmark.read(path)
    audit["checks_passed"] = False
    path.write_bytes(benchmark.canonical(audit))
    with pytest.raises(ValueError, match="successful independent audit"):
        benchmark.parent_binding(options)


def test_freeze_has_no_backend_or_model_calls_and_refuses_existing_directory(tmp_path, monkeypatch):
    options = args(tmp_path)
    inventory = {"inventory": [asdict(snippet("item", "one\ntwo\n"))],
                 "definition_keys": {"item": ["src/code.py", "run", 1, 2]}}
    monkeypatch.setattr(benchmark, "identity", lambda args: ({"queries": [query()]}, {}, inventory, {"gate": benchmark.GATE}))
    monkeypatch.setattr(backend_module, "SearchBackend", lambda *a, **kw: pytest.fail("backend called during freeze"))
    plan = benchmark.freeze(options)
    assert sorted(path.name for path in options.output.iterdir()) == ["plan.json", "source-inventory.json"]
    assert "%6" in plan["schedule"] and "six arm/mode warmups" in plan["timing"]
    with pytest.raises(ValueError, match="new freeze directory"):
        benchmark.freeze(options)


def test_plan_verifier_requires_preflight_parent_and_plan_files_committed_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark, "ROOT", tmp_path)
    options = args(tmp_path)
    options.output.mkdir()
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "CPU fixture"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "fixture@example.invalid"], check=True)
    for name in ("producer.py", "parent.json", "preflight.json"):
        (tmp_path / name).write_text("frozen bytes\n")
    binding = {"implementation_sha256": {"producer.py": benchmark.file_sha(tmp_path / "producer.py")},
        "development_parent": {"artifacts": {"parent.json": benchmark.file_sha(tmp_path / "parent.json")}},
        "definition_preflight": {"preflight.json": benchmark.file_sha(tmp_path / "preflight.json")}}
    benchmark.write(options.output / "plan.json", {"version": 1, "binding": binding, "artifacts": {}})
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "CPU fixture"], check=True)
    benchmark.verify_plan(options, binding)
    (tmp_path / "preflight.json").write_text("changed\n")
    with pytest.raises(ValueError, match="Uncommitted or changed"):
        benchmark.verify_plan(options, binding)


def test_definition_preflight_binds_complete_source_map_review_and_validator(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark, "ROOT", tmp_path)
    options = args(tmp_path)
    options.corpus.mkdir()
    (options.corpus / "src").mkdir()
    (options.corpus / "src/code.py").write_text("one\ntwo\n")
    options.dataset.write_text("fixture labels\n")
    options.source_review = tmp_path / "review.json"
    options.source_review.write_text("fixture independent source review\n")
    (tmp_path / "protocol.md").write_text("source-only protocol\n")
    options.definition_inventory = tmp_path / "definition-inventory.json"
    data = {"revision": "fixture", "source_root": "src", "source_review_protocol": "protocol.md"}
    item = snippet("a", "one\ntwo\n")
    inventory = [asdict(item)]
    definitions = {"a": (item.path, item.symbol, 1, 2)}
    source_files = {item.path: {"bytes": len(item.text.encode()), "sha256": benchmark.sha(item.text.encode())}}
    preflight = {"format_version": 1, "status": "passed_source_only_definition_inventory",
        "dataset": {"sha256": benchmark.file_sha(options.dataset)},
        "protocol": {"sha256": benchmark.file_sha(tmp_path / "protocol.md")},
        "source_review": {"sha256": benchmark.file_sha(options.source_review)},
        "corpus": {"revision": "fixture", "source_root": "src", "source_exclusions": []},
        "settings": {"max_lines": 160, "overlap_lines": 40}, "inventory": inventory,
        "definition_keys": definitions, "source_files": source_files, "excluded_supported_files": {},
        "calls": {"model": 0, "retrieval": 0, "index": 0}, "source_implementations_sha256": {}}
    benchmark.write(options.definition_inventory, preflight)
    validator = tmp_path / "definition-inventory-validator.py.txt"
    validator.write_text("independent source-only fixture validator\n")
    benchmark.write(tmp_path / "definition-inventory-receipt.json", {"status": preflight["status"],
        "inventory": {"sha256": benchmark.file_sha(options.definition_inventory)},
        "validator": {"sha256": benchmark.file_sha(validator)}, "all_source_ast_boundary_checks_passed": True})
    binding = benchmark.definition_preflight(options, data, {item.path: item.text}, inventory, definitions, source_files)
    assert set(binding) == {"definition-inventory.json", "definition-inventory-receipt.json", "definition-inventory-validator.py.txt"}
    preflight["definition_keys"]["a"] = [item.path, item.symbol, 1, 3]
    options.definition_inventory.write_bytes(benchmark.canonical(preflight))
    with pytest.raises(ValueError, match="does not match frozen extraction"):
        benchmark.definition_preflight(options, data, {item.path: item.text}, inventory, definitions, source_files)


def test_fresh_role_cannot_omit_complete_source_only_definition_inventory(tmp_path):
    options = args(tmp_path)
    options.role = "repository_holdout"
    with pytest.raises(ValueError, match="complete source-only definition inventory"):
        benchmark.definition_preflight(options, {}, {}, [], {}, {})


def test_source_only_identity_extracts_fixture_without_loading_model_or_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark, "ROOT", tmp_path)
    options = args(tmp_path)
    (options.corpus / "src").mkdir(parents=True)
    source = "def run():\n    return 42\n"
    (options.corpus / "src/code.py").write_text(source)
    subprocess.run(["git", "init", "-q", str(options.corpus)], check=True)
    subprocess.run(["git", "-C", str(options.corpus), "config", "user.name", "CPU fixture"], check=True)
    subprocess.run(["git", "-C", str(options.corpus), "config", "user.email", "fixture@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(options.corpus), "add", "."], check=True)
    subprocess.run(["git", "-C", str(options.corpus), "commit", "-qm", "CPU fixture"], check=True)
    revision = subprocess.check_output(["git", "-C", str(options.corpus), "rev-parse", "HEAD"], text=True).strip()
    target = {"path": "src/code.py", "symbol": "run", "evidence_lines": [2, 2],
        "evidence_sha256": benchmark.sha(b"    return 42\n"), "source_file_sha256": benchmark.sha(source.encode())}
    benchmark.write(options.dataset, {"revision": revision, "source_root": "src", "queries": [
        {"id": "fixture", "query": "Return a number", "kind": "present", "targets": [target]}]})
    extras = ["scripts/benchmark_windows.py", "tests/test_window_benchmark.py", "scripts/replay_windows.py",
        "scripts/benchmark_multifile.py", "scripts/replay_selection.py", "scripts/diagnose_multifile.py",
        "scripts/report_language_quality.py", "tests/test_window_assembly.py", "tests/test_window_replay.py",
        "docs/window-native-protocol.md", "docs/window-development-protocol.md", "pyproject.toml", "requirements-mac.lock"]
    for name in extras:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("frozen fixture\n")
    monkeypatch.setattr(benchmark, "checkpoint_binding", lambda model: {"model.safetensors": "fixture weights digest"})
    monkeypatch.setattr(benchmark.replay, "runtime", lambda root: ({"encoding": "cl100k_base"}, b"fixture ranks"))
    class MetadataOnly:
        metadata = {"fixture": "source metadata only"}
        def __init__(self, *args, **kwargs):
            pass
    monkeypatch.setattr(benchmark, "QwenEmbedder", MetadataOnly)
    monkeypatch.setattr(backend_module, "SearchBackend", lambda *args, **kwargs: pytest.fail("backend during identity"))
    data, sources, inventory, binding = benchmark.identity(options)
    assert sources == {"src/code.py": source}
    assert len(inventory["inventory"]) == len(inventory["definition_keys"]) == 1
    assert binding["arms"]["coverage_control"] == "coverage_control"
    assert binding["embedding"] == MetadataOnly.metadata


def metric_row(identifier, mode, arm, ranks, *, multi=True, kind="present", elapsed=1., active=False):
    return {"query_id": identifier, "retrieval": mode, "arm": arm, "repetition": 1,
        "kind": kind, "multi_file": multi, "delivered_ranks": ranks, "elapsed_backend_s": elapsed,
        "stages": {"selected": [{"snippet": {"assembly": {}}, "score": 1.}] if active else []},
        "response": {"match_status": "candidates", "results": []}}


def test_gate_requires_incremental_multi_file_gain_over_prior_coverage_not_legacy_only():
    rows = [metric_row("old-gain", mode, arm, [1, None] if arm == "legacy" else [1, 2])
            for mode in benchmark.RETRIEVALS for arm in benchmark.ARMS]
    result = benchmark.summarize(rows)
    assert not result["dense_promotion_gate_passed"]
    for mode in result["per_retrieval"].values():
        assert mode["against_controls"]["legacy"]["complete_gains"] == ["old-gain"]
        assert not mode["against_controls"]["coverage_control"]["complete_gains"]


def test_gate_retains_every_partial_group_loss_against_either_control_and_separates_modes():
    rows = []
    for mode in benchmark.RETRIEVALS:
        for arm in benchmark.ARMS:
            rows.append(metric_row("gain", mode, arm, [1, 2] if arm == "candidate" else [1, None],
                                   elapsed=1.1 if arm == "candidate" else 1., active=arm == "candidate"))
            rows.append(metric_row("partial", mode, arm, [None, 2, None] if mode == "dense" and arm == "candidate" else [1, None, None]))
            rows.append(metric_row("absent", mode, arm, [], kind="absent"))
    result = benchmark.summarize(rows)
    assert not result["dense_promotion_gate_passed"]
    assert result["per_retrieval"]["hybrid"]["passed"]
    dense = result["per_retrieval"]["dense"]
    assert all(change["group_losses"] == [{"query_id": "partial", "group": 1,
        "control_rank": 1, "candidate_rank": None}] for change in dense["against_controls"].values())
    assert dense["active_composite_overhead"]["samples"] == 1
    assert dense["active_composite_overhead"]["median_wall_ratio_to_legacy"] == 1.1
    assert dense["arms"]["legacy"]["present_queries"] == 2


def test_wall_gate_uses_actual_legacy_mode_median_and_keeps_single_file_losses():
    rows = []
    for mode in benchmark.RETRIEVALS:
        for arm in benchmark.ARMS:
            rows.extend([metric_row("gain", mode, arm, [1, 2] if arm == "candidate" else [1, None], elapsed=1.3 if arm == "candidate" else 1.),
                         metric_row("single", mode, arm, [None] if arm == "candidate" else [1], multi=False,
                                    elapsed=1.3 if arm == "candidate" else 1.)])
    result = benchmark.summarize(rows)
    for mode in result["per_retrieval"].values():
        assert mode["median_wall_ratio_to_legacy"] == 1.3 and not mode["passed"]
        assert all(item["single_file_first_hit_losses"] == ["single"] for item in mode["against_controls"].values())


@pytest.mark.parametrize("legacy,coverage,candidate,passed", [
    (1., .8, 1.1, False),  # Legacy allowance passes; faster coverage control fails.
    (.8, 1., 1.1, False),  # Coverage allowance passes; faster legacy control fails.
    (1., 2., 1.25, True),  # Exact allowance against the faster control passes.
    (2., 1., 1.25, True),
])
def test_latency_gate_requires_same_run_median_allowance_against_both_controls(legacy, coverage, candidate, passed):
    times = {"legacy": legacy, "coverage_control": coverage, "candidate": candidate}
    rows = [metric_row("new-gain", mode, arm, [1, 2] if arm == "candidate" else [1, None],
                       elapsed=times[arm]) for mode in benchmark.RETRIEVALS for arm in benchmark.ARMS]
    result = benchmark.summarize(rows)
    assert result["dense_promotion_gate_passed"] is passed
    for mode in result["per_retrieval"].values():
        assert mode["passed"] is passed
        assert mode["median_wall_ratio_to_controls"] == {
            "legacy": candidate / legacy, "coverage_control": candidate / coverage}
        assert mode["median_wall_ratio_to_legacy"] == candidate / legacy


@pytest.mark.parametrize("candidate,fault", [*( (name, None) for name in benchmark.CANDIDATES),
                                            ("connected_union", "extra_embedding"),
                                            ("connected_union", "changed_vector")])
def test_complete_native_runner_with_cpu_fakes_keeps_requests_or_failure_receipt(tmp_path, monkeypatch, candidate, fault):
    from semantic_search import embeddings
    options = args(tmp_path)
    options.candidate = candidate
    options.output.mkdir()
    options.tokenizer_cache.mkdir()
    (options.tokenizer_cache / benchmark.replay.CACHE_KEY).write_bytes(b"fixture ranks")
    monkeypatch.setattr(benchmark.replay, "CACHE_SHA256", benchmark.sha(b"fixture ranks"))
    first, second = snippet("a", "one\ntwo\n"), snippet("b", "two\nthree\n", 2)
    helper = snippet("helper", "help()\n", path="src/helper.py", symbol="help")
    items = [first, second, helper]
    definitions = {"a": (first.path, first.symbol, 1, 3), "b": (second.path, second.symbol, 1, 3),
                   "helper": (helper.path, helper.symbol, 1, 1)}
    inventory = {"inventory": [asdict(item) for item in items], "definition_keys": definitions}
    sources = {first.path: "one\ntwo\nthree\n", helper.path: helper.text}
    data = {"source_root": "src", "queries": [query(), query("absent", kind="absent")]}
    binding = {"gate": benchmark.GATE}
    monkeypatch.setattr(benchmark, "identity", lambda args: (data, sources, inventory, binding))
    monkeypatch.setattr(benchmark, "verify_plan", lambda args, current: {"binding": current})
    monkeypatch.setattr(benchmark, "relative", lambda path: "fixture")
    benchmark.write(options.output / "plan.json", {"binding": binding})
    class FakeEmbedder:
        def __init__(self, *args, **kwargs):
            self.calls = 0
        def embed_query(self, query):
            self.calls += 1
            return np.array([1., float(self.calls) if fault == "changed_vector" else 0.], dtype=np.float32)
    class Graph:
        def dependencies(self, item, limit):
            return [helper] if item.symbol == "run" else []
    class FakeBackend:
        def __init__(self, *args, embedder, **kwargs):
            self.embedder, self._definition_keys = embedder, definitions
            self.calls = 0
        def status(self):
            return {"fixture": True}
        def close(self):
            pass
        def search(self, query_text, retrieval, **kwargs):
            self.calls += 1
            self.embedder.embed_query(query_text)
            if fault == "extra_embedding" and self.calls > 6:
                self.embedder.embed_query(query_text)
            pairs = [(item, 1.) for item in (first, second)]
            selected, eligible, origins = backend_module.select_context(pairs, query=query_text,
                max_results=8, include_dependencies=True, references=Graph(), definition_keys=definitions,
                ambiguous_symbols=frozenset())
            response = packed(query_text, [item for item, score in eligible], origins)
            def reference_factory():
                from semantic_search import selection
                selected, eligible, origins = selection.definition_context(pairs, query=query_text,
                    max_results=8, include_dependencies=True, references=Graph(), definition_keys=definitions,
                    ambiguous_symbols=frozenset())
                return benchmark.coalesce_context(packed(query_text, [item for item, score in eligible], origins),
                    (source_row(item) for item, score in selected), max_results=8, source_budget=12000,
                    token_budget=4096, cancel_check=lambda: None)
            return backend_module.postprocess_context(response, (source_row(item) for item, score in selected),
                ranked_candidates=(source_row(item) for item, score in pairs), definition_keys=definitions,
                reference_factory=reference_factory,
                max_results=8, source_budget=12000, token_budget=4096, cancel_check=lambda: None)
    monkeypatch.setattr(embeddings, "QwenEmbedder", FakeEmbedder)
    monkeypatch.setattr(backend_module, "SearchBackend", FakeBackend)
    if fault:
        with pytest.raises(ValueError, match="exactly one fresh query embedding" if fault == "extra_embedding" else "query vectors changed"):
            benchmark.run(options)
        assert benchmark.read(options.output / "failure.json")["phase"] == "measured_calls"
        assert not (options.output / "summary.json").exists()
        with pytest.raises(ValueError, match="Attempt already exists"):
            benchmark.run(options)
        return
    result = benchmark.run(options)
    assert not result["dense_promotion_gate_passed"]
    summary = benchmark.read(options.output / "summary.json")
    assert summary["requests"] == summary["expected_requests"] == summary["measured_query_embeddings"] == 36
    assert summary["setup"]["query_calls"] == 6 and summary["total_query_embeddings"] == 42
    with gzip.open(options.output / "searches.jsonl.gz", "rt") as stream:
        outcomes = [json.loads(line) for line in stream]
    assert len(outcomes) == 36
    assert all(len(outcome["ranked_candidates"]) == len(outcome["stages"]["ranked"]) == 2 for outcome in outcomes)
    assert all(outcome["compact_payload"] == context.serialize_context(context.compact_result(outcome["response"])) for outcome in outcomes)
    assert all(any("assembly" in row for row in outcome["response"]["results"])
               for outcome in outcomes if outcome["arm"] == "candidate")
    with pytest.raises(ValueError, match="Attempt already exists"):
        benchmark.run(options)


def test_setup_failure_is_claimed_once_with_no_ability_to_resume(tmp_path, monkeypatch):
    options = args(tmp_path)
    options.output.mkdir()
    benchmark.write(options.output / "plan.json", {"fixture": True})
    monkeypatch.setattr(benchmark, "identity", lambda args: ({"queries": [query()]}, {}, {}, {}))
    monkeypatch.setattr(benchmark, "verify_plan", lambda args, binding: {})
    with pytest.raises(FileNotFoundError):
        benchmark.run(options)
    failure = benchmark.read(options.output / "failure.json")
    assert failure["phase"] == "setup" and failure["completed_requests"] == 0
    assert benchmark.read(options.output / "run-started.json")["resume_allowed"] is False
    with pytest.raises(ValueError, match="Attempt already exists"):
        benchmark.run(options)
