"""Invented-source adversarial checks for the independent native auditor."""
from copy import deepcopy
import hashlib
import importlib.util
import math
from pathlib import Path
import struct
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("independent_flow_native", ROOT / "scripts/audit_flow_native.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)
QUERY = "alpha beta gamma and delta epsilon zeta"
SCOPE = {"source_root": "src", "membership": "saved implementations; Git-tracked files only in Git projects",
         "source_exclusions": [], "exclusions_omitted": 0}


def fixture(size=10):
    rows, inventory, keys, sources = [], {}, {}, {}
    for number in range(1, size + 1):
        text = f"def unit_{number}():\n    return {number}\n"
        item = {"id": str(number), "path": f"src/unit{number}.py", "symbol": f"unit_{number}",
            "start_line": 1, "end_line": 2, "text": text, "content_hash": audit.sha(text.encode())}
        rows.append({"snippet": item, "score": 1 / number})
        inventory[item["id"]], keys[item["id"]], sources[item["path"]] = item, [item["path"], item["symbol"], 1, 2], text
    edges = {(inventory["9"]["path"], inventory["9"]["symbol"]): [(inventory["1"]["path"], inventory["1"]["symbol"])]} if size >= 9 else {}
    return rows, {"inventory": inventory, "definition_keys": keys, "sources": sources, "graph_edges": edges}


def identifiers(pairs):
    return [pair["snippet"]["id"] for pair in pairs]


def test_native_baseline_is_independent_of_any_captured_production_selection():
    ranked, fx = fixture()
    selected, eligible, origins = audit.baseline(ranked, fx["inventory"], fx["definition_keys"], fx["graph_edges"], canonical_definitions=True)
    assert identifiers(selected) == list(map(str, range(1, 11)))
    assert identifiers(eligible) == list(map(str, range(1, 9)))
    assert origins == {str(n): ["retrieval", None] for n in range(1, 9)}


def test_first_two_static_targets_use_earliest_windows_and_inherit_owner_score():
    ranked, fx = fixture()
    inventory, keys = fx["inventory"], fx["definition_keys"]
    helper = deepcopy(inventory["7"])
    helper.update(id="helper", start_line=3, end_line=4, text="    return 7\n    return 8\n")
    helper["content_hash"] = audit.sha(helper["text"].encode())
    inventory["helper"] = helper
    keys["helper"] = keys["7"]
    edges = {(inventory["1"]["path"], inventory["1"]["symbol"]): [
        (inventory[str(n)]["path"], inventory[str(n)]["symbol"]) for n in (7, 8, 9)]}
    _, eligible, origins = audit.baseline(ranked, inventory, keys, edges, canonical_definitions=True)
    assert identifiers(eligible) == ["1", "2", "3", "4", "5", "7", "8", "6"]
    assert next(pair["score"] for pair in eligible if pair["snippet"]["id"] == "7") == 1
    assert origins["7"] == origins["8"] == ["dependency", "1"]
    assert origins["6"] == ["retrieval", None]


def test_already_included_first_target_still_consumes_dependency_limit():
    ranked, fx = fixture()
    inv = fx["inventory"]
    edges = {(inv["1"]["path"], inv["1"]["symbol"]): [(inv[str(n)]["path"], inv[str(n)]["symbol"]) for n in (2, 7, 9)]}
    _, eligible, _ = audit.baseline(ranked, inv, fx["definition_keys"], edges, canonical_definitions=True)
    assert "7" in identifiers(eligible) and "9" not in identifiers(eligible)


def test_canonical_and_legacy_dedup_and_ambiguous_edges_are_distinct_controls():
    ranked, fx = fixture()
    clone = deepcopy(ranked[0]); clone["snippet"].update(id="shadow", start_line=3, end_line=4)
    fx["inventory"]["shadow"] = clone["snippet"]
    fx["definition_keys"]["shadow"] = [*fx["definition_keys"]["1"][:2], 3, 4]
    ranked.insert(1, clone)
    edge = {(ranked[0]["snippet"]["path"], ranked[0]["snippet"]["symbol"]): [
        (fx["inventory"]["9"]["path"], fx["inventory"]["9"]["symbol"])]}
    canonical = audit.baseline(ranked, fx["inventory"], fx["definition_keys"], edge, canonical_definitions=True)
    legacy = audit.baseline(ranked, fx["inventory"], fx["definition_keys"], edge, canonical_definitions=False)
    assert identifiers(canonical[0])[:2] == ["1", "shadow"]
    assert "shadow" not in identifiers(legacy[0])
    assert "9" not in identifiers(canonical[1]) and "9" in identifiers(legacy[1])


@pytest.fixture(scope="module")
def counter():
    return audit.win.offline_encoding(ROOT / ".cache/tokenizers")


def trace(counter, arm="candidate"):
    ranked, fx = fixture()
    selected, eligible, origins = audit.baseline(ranked, fx["inventory"], fx["definition_keys"], fx["graph_edges"], canonical_definitions=arm != "legacy")
    canonical = audit.baseline(ranked, fx["inventory"], fx["definition_keys"], fx["graph_edges"], canonical_definitions=True)
    outer = None
    if arm == "candidate":
        selected, eligible, origins, outer = audit.flow.planner(ranked, *canonical, fx["definition_keys"], fx["graph_edges"], [], query=QUERY)
    template = audit.native_template(QUERY, SCOPE, ranked)
    before = audit.source.ordinary_pack(template, eligible, origins, counter)
    response, reference, proposal, guard = before, None, None, None
    if arm != "legacy":
        proposal = audit.source.coalesce_pack(before, audit.full_rows(selected), counter)
        response = proposal
    if arm == "candidate":
        reference_before = audit.source.ordinary_pack(template, canonical[1], canonical[2], counter)
        reference = audit.source.coalesce_pack(reference_before, audit.full_rows(canonical[0]), counter)
        response, guard = audit.source.guard(reference, proposal, outer["guard_plan"])
    else:
        proposal = None
    compact = audit.win.compact_payload(response)
    row = {"arm": arm, "query_id": "invented", "retrieval": "dense", "repetition": 1,
        "condition": "shared_flow" if arm == "candidate" else arm,
        "stages": {"ranked": ranked, "selected": selected, "eligible": eligible, "origins": origins,
                   "definition_keys": fx["definition_keys"], "ambiguous_symbols": []},
        "flow_receipt": outer, "base_receipt": outer["base_plan"] if outer else None,
        "selection_plan": outer["guard_plan"] if outer else None,
        "actual_guard_plan": outer["guard_plan"] if outer else None, "source_guard_receipt": guard,
        "pre_postprocess_response": before, "backfill_candidates": audit.full_rows(selected), "ranked_candidates": audit.full_rows(ranked),
        "reference_response": reference, "proposal_response": proposal, "response": response,
        "compact_payload": compact, "compact_sha256": audit.sha(compact.encode()),
        "actual_compact_tokens": response["token_usage"]["tokens"], "query_embedding_calls": 1, "document_embedding_calls": 0}
    row.update({field: int(arm == "candidate") for field in audit.CALLS})
    return row, fx


@pytest.mark.parametrize("arm", audit.ARMS)
def test_all_three_native_arms_have_reconstructed_baselines_exact_source_and_counts(counter, arm):
    row, fx = trace(counter, arm)
    audit.verify_trace(row, fx, counter, query=QUERY, scope=SCOPE)


@pytest.mark.parametrize("forgery", ["baseline_axiom", "origin", "scalar", "early_trim", "source", "projection",
    "base", "calls", "query_calls", "doc_work", "key_map", "payload", "final_choice"])
def test_native_trace_rejects_self_consistent_but_unsupported_receipts(counter, forgery):
    row, fx = trace(counter)
    row = deepcopy(row)
    if forgery == "baseline_axiom": row["flow_receipt"]["base_plan"]["baseline_eligible_ids"].reverse()
    elif forgery == "origin": row["stages"]["origins"]["1"] = ["dependency", "2"]
    elif forgery == "scalar": row["stages"]["eligible"][0]["score"] = 123
    elif forgery == "early_trim": row["pre_postprocess_response"]["results"].pop()
    elif forgery == "source": row["ranked_candidates"][0]["source"] = "invented\n"
    elif forgery == "projection": row["actual_guard_plan"]["producer_schema"] = "source_graph_allocation_v1"
    elif forgery == "base": row["base_receipt"]["accepted"] = False
    elif forgery == "calls": row["base_planner_calls"] = 2
    elif forgery == "query_calls": row["query_embedding_calls"] = True
    elif forgery == "doc_work": row["document_embedding_calls"] = 1
    elif forgery == "key_map": row["stages"]["definition_keys"].pop("10")
    elif forgery == "payload": row["compact_payload"] += " "
    else: row["response"] = deepcopy(row["reference_response"])
    with pytest.raises(ValueError):
        audit.verify_trace(row, fx, counter, query=QUERY, scope=SCOPE)


def test_operational_timing_changes_do_not_relabel_guard_source(counter):
    row, fx = trace(counter)
    row["response"]["timings"] = {"packaging_s": 100, "total_s": 101}
    row["pre_postprocess_response"]["timings"] = {"packaging_s": .001, "total_s": .002}
    row["response"]["indexstats"] = {"elapsed_s": 12}
    audit.verify_trace(row, fx, counter, query=QUERY, scope=SCOPE)


@pytest.mark.parametrize("value", [[0.0, 0.0], [math.inf, 0.0], [True, 0.0], [1.0]])
def test_bad_vector_dimensions_norm_or_components_are_rejected(value):
    with pytest.raises(ValueError):
        audit.vector_receipt({"query_vector": value, "query_vector_sha256": "bad"}, dimensions=2)


def test_actual_float32_vector_hash_is_checked():
    vector = [1.0, 0.0]
    row = {"query_vector": vector, "query_vector_sha256": audit.sha(struct.pack("<ff", *vector))}
    assert audit.vector_receipt(row, dimensions=2) == row["query_vector_sha256"]
    row["query_vector_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="vector hash"):
        audit.vector_receipt(row, dimensions=2)


def test_fourteen_invented_queries_have_exact252_rotated_identities():
    queries = [{"id": str(i)} for i in range(14)]
    schedule = audit.expected_schedule(queries)
    assert len(schedule) == len(set(schedule)) == 252
    assert set(schedule) == {(r, query["id"], arm, mode) for r in range(1, 4) for query in queries
                             for arm in audit.ARMS for mode in audit.RETRIEVALS}


def warmups():
    rows = [{"condition": condition, "retrieval": retrieval,
             "query": "Find code that handles input and returns a result", "query_embedding_calls": 1,
             "document_embedding_calls": 0} for condition in ("legacy", "coverage_control", "shared_flow")
            for retrieval in audit.RETRIEVALS]
    rows[0]["document_embedding_calls"] = 2
    return {"warmups": rows, "query_calls": 6, "document_calls": 2, "index_model_and_explicit_warmup_s": 1.0}


def test_native_setup_separates_six_actual_warmups_and_initial_document_work():
    audit.verify_warmups(warmups())


@pytest.mark.parametrize("mutation", ["order", "count", "query_count", "repeat_docs", "total_docs", "query", "time"])
def test_warmup_inventory_and_actual_counter_forgeries_fail(mutation):
    setup = warmups()
    if mutation == "order": setup["warmups"].reverse()
    elif mutation == "count": setup["warmups"].pop()
    elif mutation == "query_count": setup["warmups"][1]["query_embedding_calls"] = True
    elif mutation == "repeat_docs": setup["warmups"][1]["document_embedding_calls"] = 1
    elif mutation == "total_docs": setup["document_calls"] = 3
    elif mutation == "query": setup["warmups"][0]["query"] = "gold query"
    else: setup["index_model_and_explicit_warmup_s"] = math.nan
    with pytest.raises(ValueError):
        audit.verify_warmups(setup)


def repeat_rows():
    queries = [{"id": str(i)} for i in range(14)]
    vector = [1.0] + [0.0] * 1023
    digest = audit.sha(struct.pack("<" + "f" * 1024, *vector))
    rows = [{"repetition": repetition, "query_id": query, "arm": arm, "retrieval": retrieval,
             "query_vector": vector, "query_vector_sha256": digest, "elapsed_backend_s": .1,
             "stages": {"ranked": []}, "compact_sha256": query + retrieval + arm}
            for repetition, query, arm, retrieval in audit.expected_schedule(queries)]
    return rows, queries


def test_actual_vector_rank_and_payload_stability_across_all252_requests():
    rows, queries = repeat_rows()
    audit.verify_repeats(rows, queries)


@pytest.mark.parametrize("mutation", ["vector", "rank", "payload", "missing", "schedule", "time"])
def test_repeat_forgeries_cannot_hide_one_different_request(mutation):
    rows, queries = repeat_rows()
    rows = deepcopy(rows)
    if mutation == "vector":
        rows[-1]["query_vector"] = [0.0, 1.0] + [0.0] * 1022
        rows[-1]["query_vector_sha256"] = audit.sha(struct.pack("<" + "f" * 1024, *rows[-1]["query_vector"]))
    elif mutation == "rank": rows[-1]["stages"]["ranked"] = [{"score": 1.0}]
    elif mutation == "payload": rows[-1]["compact_sha256"] = "different"
    elif mutation == "missing": rows.pop()
    elif mutation == "schedule": rows[0], rows[1] = rows[1], rows[0]
    else: rows[0]["elapsed_backend_s"] = math.nan
    with pytest.raises(ValueError):
        audit.verify_repeats(rows, queries)


def metric_rows():
    rows = []
    for mode in audit.RETRIEVALS:
        for rep in range(1, 4):
            for name, multi in (("gain", True), ("single", False)):
                for arm in audit.ARMS:
                    ranks = [1, 2] if multi and arm == "candidate" else [1, None] if multi else [1]
                    rows.append({"query_id": name, "kind": "present", "multi_file": multi, "repetition": rep,
                        "retrieval": mode, "arm": arm, "elapsed_backend_s": .8 if arm == "coverage_control" else 1.0,
                        "delivered_ranks": ranks, "response": {"results": []}, "stages": {"selected": []},
                        "compact_sha256": arm + name,
                        "flow_receipt": {"base_plan": {"activated": True}, "accepted": True},
                        "source_guard_receipt": {"accepted": True}})
    return rows


def test_native_dual_control_gate_passes_at_exact125percent_boundary():
    result = audit.native_metrics(metric_rows())
    assert result["dense_promotion_gate_passed"] is True
    for mode in audit.RETRIEVALS:
        assert result["per_retrieval"][mode]["median_wall_ratio_to_controls"]["coverage_control"] == 1.25
        assert result["per_retrieval"][mode]["against_controls"]["coverage_control"]["net_multi_file_complete_gain"] == 1


def test_faster_than_legacy_does_not_excuse_slower_than_coverage_control():
    rows = metric_rows()
    for row in rows:
        if row["arm"] == "coverage_control": row["elapsed_backend_s"] = .5
    result = audit.native_metrics(rows)
    assert result["per_retrieval"]["dense"]["against_controls"]["coverage_control"]["preserved"]
    assert result["dense_promotion_gate_passed"] is False


def test_hybrid_cannot_rescue_missing_dense_incremental_complete_gain():
    rows = metric_rows()
    for row in rows:
        if row["retrieval"] == "dense" and row["query_id"] == "gain" and row["arm"] == "candidate":
            row["delivered_ranks"] = [1, None]
    result = audit.native_metrics(rows)
    assert result["per_retrieval"]["hybrid"]["passed"] is True
    assert result["per_retrieval"]["dense"]["passed"] is False and not result["dense_promotion_gate_passed"]


def test_individual_group_loss_on_still_incomplete_query_rejects_another_complete_gain():
    rows = metric_rows()
    for mode in audit.RETRIEVALS:
        for rep in range(1, 4):
            for arm in audit.ARMS:
                item = deepcopy(next(row for row in rows if row["retrieval"] == mode and row["repetition"] == rep and row["arm"] == arm))
                item.update(query_id="partial", multi_file=True, delivered_ranks=[None, 2, None] if arm == "candidate" else [1, None, None])
                rows.append(item)
    result = audit.native_metrics(rows)
    assert result["per_retrieval"]["dense"]["against_controls"]["coverage_control"]["net_multi_file_complete_gain"] == 1
    assert result["per_retrieval"]["dense"]["against_controls"]["coverage_control"]["group_losses"]
    assert not result["dense_promotion_gate_passed"]


def test_fresh_source_scoring_requires_symbol_full_interval_and_exact_bytes():
    text = "start\nimplementation\nend\n"
    target = {"path": "a.py", "symbol": "expected", "evidence_lines": [2, 3],
              "evidence_sha256": audit.sha(b"implementation\nend\n")}
    row = {"path": "a.py", "symbol": "expected", "start_line": 1, "end_line": 3, "source": text}
    assert audit.strict_ranks([row], [[target]]) == [1]
    for changes in ({"symbol": "unrelated"}, {"end_line": 2}, {"source": "start\nimplementation\nwrong\n"}):
        assert audit.strict_ranks([{**row, **changes}], [[target]]) == [None]


@pytest.mark.parametrize("name", ["semantic_search.flow_allocation", "semantic_search.source_allocation", "semantic_search.selection",
    "semantic_search.backend", "benchmark_flow_allocation", "mlx", "numpy"])
def test_independent_native_import_guard_blocks_all_planners_and_numerical_runtime(name):
    with pytest.raises(RuntimeError, match="forbids"):
        audit.RefuseProduction().find_spec(name)


def test_cli_help_and_clean_import_do_not_read_holdout_or_load_models():
    command = """import importlib.util,sys
from pathlib import Path
p=Path('scripts/audit_flow_native.py').resolve()
s=importlib.util.spec_from_file_location('independent',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
assert not [n for n in sys.modules if m.forbidden_module(n)]
print('independent CPU only')
"""
    value = subprocess.run([sys.executable, "-c", command], cwd=ROOT, text=True, capture_output=True, check=True)
    assert value.stdout.strip() == "independent CPU only"
    help_result = subprocess.run([sys.executable, str(ROOT / "scripts/audit_flow_native.py"), "--help"], text=True, capture_output=True)
    assert help_result.returncode == 0 and "--corpus" in help_result.stdout


def test_existing_audit_output_is_preserved_before_any_holdout_read(tmp_path):
    target = tmp_path / "audit.json"
    target.write_text("preserved")
    value = subprocess.run([sys.executable, str(ROOT / "scripts/audit_flow_native.py"), "--corpus", str(tmp_path / "unread"),
        "--results", str(tmp_path / "unread-results"), "--output", str(target)], text=True, capture_output=True)
    assert value.returncode != 0 and "Preserve prior native audit" in value.stderr
    assert target.read_text() == "preserved"


def test_coverage_source_view_hash_includes_backfill_receipt_and_excludes_timings(counter):
    row, _ = trace(counter, "coverage_control")
    view = audit.context_view(row["response"])
    expected = {field: deepcopy(row["response"][field]) for field in audit.SOURCE_FIELDS}
    expected["_selection_backfill"] = deepcopy(row["response"]["_selection_backfill"])
    assert view == expected
    assert audit.sha(audit.old.canonical(view)) == audit.sha(audit.old.canonical(expected))
    without = {field: row["response"][field] for field in audit.SOURCE_FIELDS}
    assert audit.sha(audit.old.canonical(view)) != audit.sha(audit.old.canonical(without))
    row["response"]["timings"] = {"total_s": 100}
    assert audit.context_view(row["response"]) == view


def parent_fixture(tmp_path, monkeypatch):
    folder = Path("results/invented-qualified")
    qualification = {"qualified": True, "gained_additional_distinct_complete_queries": 1,
        "additional_distinct_complete_queries_vs_coverage_control": ["invented/gain"],
        "against_controls": {name: {"lost_groups": 0, "lost_complete_rows": 0, "lost_single_file_first_hits": 0}
                             for name in audit.ARMS[:2]}}
    summary = {"qualification": {"shared_flow": qualification}, "plan_sha256": "hash", "replay_sha256": "hash",
               "model_calls": 0, "retrieval_calls": 0, "network_calls": 0}
    freeze = {"commit": "freeze", "committed_at_unix": 1, "bound_blob_count": 2}
    names = ("frozen-plan.json", "run-start.json", "independent-audit.json", "summary.json", "replay.jsonl.gz")
    parent = {"study": "source_flow", "qualification": qualification, "freeze": freeze, "execution_commit": "execution",
        "artifacts": {(folder / name).as_posix(): "hash" for name in names}}
    parent["artifacts"].update({"producer.py": "hash", "auditor.py": "hash"})
    plan = {"producer_sha256": {"producer.py": "hash"}}
    started = {"execution_commit": "execution", "prospective_freeze": freeze}
    receipt = {"study": "source_flow", "status": "passed", "checks_passed": True,
        "candidate_planner_or_producer_modules_imported": False, "verified_outcome_rows": 365,
        "exact_legacy_controls": 365, "exact_prior_coverage_controls": 337, "referenced_control_rows": 730,
        "referenced_negative_rows": 1460, "independently_recomputed_summary": summary,
        "producer_execution": "execution", "producer_freeze": freeze, "artifact_sha256": {name: "hash" for name in names},
        "auditor_sha256": {"auditor.py": "hash"}, "calls": {name: 0 for name in ("model", "retrieval", "index", "network")}}
    documents = {name: value for name, value in zip(names[:4], (plan, started, receipt, summary))}
    documents.update({"historical-provenance.json": {"window": "proof"}, "negative-provenance.json": {"negative": "proof"},
                      "source-negative-provenance.json": {"source": "proof"}})
    monkeypatch.setattr(audit, "read", lambda path: documents[path.name])
    monkeypatch.setattr(audit, "file_sha", lambda path: "hash")
    monkeypatch.setattr(audit, "git_sha", lambda *args: "hash")
    monkeypatch.setattr(audit.source, "historical_proof", lambda *args: {"window": "proof"})
    monkeypatch.setattr(audit.source, "parent_allocation_proof", lambda *args: {"negative": "proof"})
    monkeypatch.setattr(audit.flow, "source_parent_proof", lambda *args: {"source": "proof"})
    monkeypatch.setattr(audit.flow, "new_freeze", lambda *args: (freeze, "execution"))
    return {"development_parent": parent}, qualification, receipt


def test_native_parent_gate_requires_its_independent_cpu_audit_schema(tmp_path, monkeypatch):
    binding, _, _ = parent_fixture(tmp_path, monkeypatch)
    assert audit.parent_proof(tmp_path, binding)["qualified"] is True


@pytest.mark.parametrize("mutation", ["missing_loss", "duplicate_gain", "gain_count", "model_calls", "boolean_calls", "omitted_auditor", "bad_history"])
def test_native_parent_gate_cannot_reinterpret_malformed_qualification_or_audit(tmp_path, monkeypatch, mutation):
    binding, quality, receipt = parent_fixture(tmp_path, monkeypatch)
    if mutation == "missing_loss": del quality["against_controls"]["legacy"]["lost_groups"]
    elif mutation == "duplicate_gain": quality["additional_distinct_complete_queries_vs_coverage_control"] *= 2
    elif mutation == "gain_count": quality["gained_additional_distinct_complete_queries"] = 2
    elif mutation == "model_calls": receipt["calls"]["model"] = 1
    elif mutation == "boolean_calls": receipt["calls"]["index"] = False
    elif mutation == "omitted_auditor": del binding["development_parent"]["artifacts"]["auditor.py"]
    else: monkeypatch.setattr(audit.source, "historical_proof", lambda *args: {"window": "substituted"})
    with pytest.raises(ValueError):
        audit.parent_proof(tmp_path, binding)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, stderr=subprocess.STDOUT).strip()


def native_freeze_fixture(tmp_path):
    from datetime import datetime, timezone, timedelta
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Invented native audit")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    names = {"scripts/audit_flow_native.py", "tests/test_flow_native_audit.py", "scripts/benchmark_flow_allocation.py",
        "tests/test_flow_allocation_benchmark.py", "docs/flow-allocation-native-protocol.md",
        "src/semantic_search/flow_allocation.py", *audit.source.GRAPH_SOURCES}
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("raise AssertionError('Do not execute frozen historical code')\n")
    output = tmp_path / "results/native-invented"
    output.mkdir(parents=True)
    (output / "source-inventory.json").write_text("{}\n")
    implementation = {name: audit.file_sha(tmp_path / name) for name in names}
    plan = {"binding": {"implementation_sha256": implementation, "development_parent": {"artifacts": {}}},
            "artifacts": {"source-inventory.json": audit.file_sha(output / "source-inventory.json")}}
    (output / "plan.json").write_text(__import__("json").dumps(plan))
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "Commit invented native freeze before execution")
    commit = git(tmp_path, "rev-parse", "HEAD")
    start = {"execution_commit": commit, "started_at_utc": (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat()}
    artifacts = {"plan.json": audit.file_sha(output / "plan.json")}
    return output, plan, start, artifacts


def test_native_freeze_requires_all_code_committed_before_inference_without_executing_it(tmp_path):
    output, plan, start, artifacts = native_freeze_fixture(tmp_path)
    chain, execution = audit.native_freeze(tmp_path, output, plan, start, artifacts)
    assert chain["commit"] == execution == start["execution_commit"]
    with (tmp_path / "scripts/audit_flow_native.py").open("a") as stream:
        stream.write("changed\n")
    with pytest.raises(ValueError, match="bytes changed"):
        audit.native_freeze(tmp_path, output, plan, start, artifacts)


def test_native_freeze_cannot_omit_auditor_or_tests_then_add_them_after_outputs(tmp_path):
    output, plan, start, artifacts = native_freeze_fixture(tmp_path)
    del plan["binding"]["implementation_sha256"]["tests/test_flow_native_audit.py"]
    with pytest.raises(ValueError, match="before any inference"):
        audit.native_freeze(tmp_path, output, plan, start, artifacts)
