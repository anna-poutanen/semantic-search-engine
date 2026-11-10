"""Invented native receipts only; no observed corpus, models or outcomes."""
import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from experiments.provider_native import audit


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("invented_role_native_audit_fixtures", ROOT / "tests/test_role_native_audit.py")
toy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(toy)


def trace(arm="original_role", *, mode="dense", query=toy.QUERY, empty=False):
    value = toy.trace("candidate" if arm in audit.ROLES else arm, query=query, mode=mode, empty=empty)
    value["arm"] = arm
    calls = value["candidate_capture"]["feature_calls"] if arm in audit.ROLES else 0
    value["provider_integration"] = {"schema": "provider_native_integration_v1", "arm": arm,
        "implementation": {"original_role": "original", "fast_role": "fast"}.get(arm),
        "factory_attempts": int(arm in audit.ROLES), "factory_completed": int(arm in audit.ROLES), "factory_failed": 0,
        "provider_attempts": calls, "provider_completed": calls, "provider_failed": 0,
        "factory_elapsed_s": .002 if arm in audit.ROLES else 0.0, "provider_elapsed_s": .005 if calls else 0.0}
    return value


def invented_matrix():
    rows, warmups = [], []
    queries = [{"id": "invented", "query": toy.QUERY, "kind": "present"}]
    for repeat, identifier, arm, mode in audit.expected_schedule(queries):
        value = trace(arm, mode=mode)
        value.update(repetition=repeat, query_id=identifier)
        rows.append(value)
    for arm in audit.ARMS:
        for mode in audit.MODES:
            value = trace(arm, mode=mode, query="Find code that handles input and returns a result")
            value.update(query_id="__generic_warmup__", repetition=0, kind="absent", multi_file=False,
                         candidate_ranks=[], delivered_ranks=[])
            warmups.append(value)
    for index, value in enumerate([*warmups, *rows]):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=index*1.5)
        value["request_clock"] = {"started_perf_counter": 10.0+index*1.5, "ended_perf_counter": 11.0+index*1.5,
                                   "started_at_utc": start.isoformat(), "ended_at_utc": (start+timedelta(seconds=1)).isoformat()}
    fixture = toy.fixture()
    text = next(iter(fixture["inventory"].values()))["text"]
    prepared = {"fixture": fixture, "legacy_plan": {"binding": {"embedding": toy.metadata()}},
                "data": {"queries": queries, "source_root": "src", "source_exclusions": []},
                "queries": {"invented": queries[0]}, "multi": {"invented": False},
                "groups": {"invented": [[{"path": "src/load.py", "symbol": "load", "evidence_lines": [1, 2],
                                          "evidence_sha256": audit.sha(text.encode())}]]}}
    return rows, warmups, prepared


@pytest.mark.parametrize("arm", audit.ARMS)
@pytest.mark.parametrize("mode", audit.MODES)
def test_projection_reuses_unchanged_independent_full_trace_and_real_work_receipts(arm, mode):
    value = trace(arm, mode=mode)
    projected = audit.project_row(value)
    before = deepcopy(value)
    result = audit.old.verify_trace(projected, toy.fixture(), toy.Counter(), query=toy.QUERY,
                                   scope=toy.SCOPE, model_metadata=toy.metadata())
    audit.old.verify_forward_counts(value)
    audit.verify_integration(value)
    assert result["active"] is (arm in audit.ROLES)
    assert value == before


def test_complete_actual_matrix_and_two_independent_projections_interoperate():
    rows, warmups, prepared = invented_matrix()
    rows, warmups = json.loads(json.dumps((rows, warmups), ensure_ascii=False))
    before = deepcopy((rows, warmups, prepared))
    result = audit.audit_rows(rows, warmups, prepared, toy.Counter())
    assert result["verified_requests"] == 24
    assert result["verified_warmups"] == 8
    assert result["verified_role_pairs"] == 6
    assert result["exact_fast_original_parity"] is True
    assert result["metrics"]["provider_comparison"]["dense"]["timed_requests_per_arm"] == 3
    assert (rows, warmups, prepared) == before
    for arm in audit.ROLES:
        projected = audit.project_rows(rows, arm, prepared["data"]["queries"])
        audit.old.verify_repeats(projected, prepared["data"]["queries"])


def test_schedule_has_eight_rotating_conditions_and_exact_336_requests():
    queries = [{"id": "q"+str(index)} for index in range(14)]
    schedule = audit.expected_schedule(queries)
    assert len(schedule) == 336
    assert len(set(schedule)) == 336
    expected = [(arm, mode) for mode in audit.MODES for arm in audit.ARMS]
    assert [(arm, mode) for _, _, arm, mode in schedule[:8]] == expected
    assert [(arm, mode) for _, _, arm, mode in schedule[8:16]] == expected[1:]+expected[:1]
    assert [(arm, mode) for _, _, arm, mode in schedule[112:120]] == expected[1:]+expected[:1]


@pytest.mark.parametrize("change", [None, "missing_row", "wrong_rep", "float_rep"])
def test_native_transport_keeps_all336_full_rows_in_three_fixed_repetition_shards(tmp_path, change):
    for repetition, name in enumerate(audit.SEARCH_SHARDS, 1):
        values = [{"repetition": repetition, "position": position} for position in range(112)]
        if repetition == 2:
            if change == "missing_row":
                values.pop()
            elif change == "wrong_rep":
                values[0]["repetition"] = 1
            elif change == "float_rep":
                values[0]["repetition"] = 2.0
        with gzip.open(tmp_path / name, "wt", encoding="utf-8") as stream:
            for value in values:
                stream.write(json.dumps(value)+"\n")
    if change is None:
        result = audit.measured_rows(tmp_path)
        assert len(result) == 336
        assert [row["repetition"] for row in result] == [1]*112+[2]*112+[3]*112
        assert len(audit.ATTEMPT_ARTIFACTS) == 9
        assert len(audit.LEGACY_ARTIFACTS) == 7
        assert "searches.jsonl.gz" in audit.LEGACY_ARTIFACTS
        assert "searches.jsonl.gz" not in audit.ATTEMPT_ARTIFACTS
    else:
        with pytest.raises(ValueError, match="repetition shard"):
            audit.measured_rows(tmp_path)


@pytest.mark.parametrize("change", ["missing", "duplicate", "order", "mode", "arm", "query_vector", "raw", "repeat_payload", "snapshot", "bool_rep", "float_rep"])
def test_matrix_rejects_missing_aliases_order_vector_raw_or_repetition_changes(change):
    rows, _, prepared = invented_matrix()
    if change == "missing":
        rows.pop()
    elif change == "duplicate":
        rows[-1] = deepcopy(rows[0])
    elif change == "order":
        rows[0], rows[1] = rows[1], rows[0]
    elif change == "mode":
        rows[0]["retrieval"] = "hybrid"
    elif change == "arm":
        rows[0]["arm"] = "candidate"
    elif change == "query_vector":
        rows[-1]["query_vector_sha256"] = "0"*64
    elif change == "raw":
        rows[-1]["stages"]["ranked"][0]["score"] = .75
    elif change == "repeat_payload":
        rows[-1]["compact_sha256"] = "0"*64
    elif change == "bool_rep":
        rows[0]["repetition"] = True
    elif change == "float_rep":
        rows[0]["repetition"] = 1.0
    else:
        next(row for row in rows if row["candidate_capture"])["candidate_capture"]["request_binding"]["source_snapshot"] = "changed"
    with pytest.raises(ValueError):
        audit.verify_matrix(rows, prepared["data"]["queries"])


@pytest.mark.parametrize("change", ["factory_outside", "provider_outside", "phase_outside", "bool", "wrong_impl", "missing_factory",
                                   "failure", "zero_factory_timer", "zero_provider_timer", "nonfinite", "inactive_timer", "extra"])
def test_actual_integration_accounts_for_factory_clone_and_every_provider_attempt(change):
    row = trace()
    value = row["provider_integration"]
    if change == "factory_outside":
        value["factory_elapsed_s"] = 1.0
    elif change == "provider_outside":
        value["provider_elapsed_s"] = .02
    elif change == "phase_outside":
        value["factory_elapsed_s"] = .97
    elif change == "bool":
        value["factory_attempts"] = True
    elif change == "wrong_impl":
        value["implementation"] = "fast"
    elif change == "missing_factory":
        value["factory_attempts"] = 0
    elif change == "failure":
        value["provider_failed"] = 1
    elif change == "zero_factory_timer":
        value["factory_elapsed_s"] = 0.0
    elif change == "zero_provider_timer":
        value["provider_elapsed_s"] = 0.0
    elif change == "nonfinite":
        value["provider_elapsed_s"] = float("nan")
    elif change == "inactive_timer":
        row = trace("coverage_control")
        row["provider_integration"]["factory_elapsed_s"] = .01
    else:
        value["unbound"] = 1
    with pytest.raises(ValueError):
        audit.verify_integration(row)


def test_factory_is_required_even_when_ineligible_query_never_invokes_provider():
    row = trace(query="short")
    assert row["candidate_capture"]["feature_calls"] == 0
    assert row["provider_integration"]["factory_attempts"] == 1
    assert row["provider_integration"]["provider_attempts"] == 0
    audit.verify_integration(row)


@pytest.mark.parametrize("value", [False, 0.0])
def test_warmup_repetition_count_requires_exact_integer(value):
    rows, warmups, prepared = invented_matrix()
    warmups[0]["repetition"] = value
    with pytest.raises(ValueError, match="Warmups"):
        audit.audit_rows(rows, warmups, prepared, toy.Counter())


def test_inactive_provider_may_be_invoked_without_any_clause_inference():
    row = trace(empty=True)
    assert row["candidate_capture"]["feature_calls"] == 1
    assert row["clause_query_calls"] == 0
    assert row["provider_integration"]["provider_attempts"] == 1
    audit.verify_integration(row)


def pair():
    return trace("original_role"), trace("fast_role")


def test_parity_permits_only_actual_enumerated_native_clock_differences():
    original, fast = pair()
    for name in ("response", "pre_postprocess_response", "reference_response", "proposal_response"):
        original[name]["timings"] = {"total_s": .1}
        fast[name]["timings"] = {"total_s": .2}
        original[name]["indexstats"] = {"elapsed_s": .003, "rows": 1}
        fast[name]["indexstats"] = {"elapsed_s": .004, "rows": 1}
    for name in ("response", "reference_response", "v1_response"):
        original["candidate_capture"][name]["timings"] = {"total_s": .1}
        fast["candidate_capture"][name]["timings"] = {"total_s": .2}
    for event in fast["external_forward_events"]:
        event["elapsed_seconds"] += .001
    receipt = fast["candidate_capture"]["provider_receipts"][0]
    for name in ("original_query_forward_events", "extra_query_forward_events"):
        for event in receipt[name]:
            event["elapsed_seconds"] += .001
            if "duration_seconds" in event:
                event["duration_seconds"] += .001
    fast["candidate_capture"]["phase_timings"]["live_feature_provider_s"] = .03
    before = deepcopy((original, fast))
    audit.verify_pair(original, fast)
    assert (original, fast) == before


@pytest.mark.parametrize("change", ["vector", "features", "binding", "receipt_counter", "receipt_prompt", "event_presence", "event_status",
    "event_tokens", "event_shape", "indexstats", "indexstats_presence", "elapsed_presence", "nested_timing", "scalar", "source",
    "provenance", "semantic", "role", "scope_order", "payload", "ranks", "extra_capture", "timings_presence", "timings_type",
    "timings_key_missing", "timings_key_extra", "timings_negative", "timings_nan", "timings_int", "timings_bool"])
def test_whole_source_feature_receipt_and_work_parity_never_relaxes_material_fields(change):
    original, fast = pair()
    capture = fast["candidate_capture"]
    if change == "vector":
        fast["query_vector"] = toy.vector(1)
    elif change == "features":
        capture["features"]["facet_vectors"][1] = toy.vector(1)
    elif change == "binding":
        capture["request_binding"]["source_snapshot"] = "changed"
    elif change == "receipt_counter":
        capture["provider_receipts"][0]["extra_query_api_calls"] = 0
    elif change == "receipt_prompt":
        capture["provider_receipts"][0]["extra_clause_prompts"][0] = "changed"
    elif change == "event_presence":
        capture["provider_receipts"][0]["extra_query_forward_events"][0]["duration_seconds"] = .1
    elif change == "event_status":
        fast["external_forward_events"][1]["status"] = "failed"
    elif change == "event_tokens":
        fast["external_forward_events"][0]["token_ids_sha256"][0] = "0"*64
    elif change == "event_shape":
        fast["external_forward_events"][0]["padded_shape"][1] += 1
    elif change in {"indexstats", "indexstats_presence", "elapsed_presence"}:
        original["response"]["indexstats"] = {"elapsed_s": .01, "rows": 1}
        fast["response"]["indexstats"] = {"elapsed_s": .02, "rows": 1}
        if change == "indexstats":
            fast["response"]["indexstats"]["rows"] = 2
        elif change == "indexstats_presence":
            del fast["response"]["indexstats"]
        else:
            del fast["response"]["indexstats"]["elapsed_s"]
    elif change == "nested_timing":
        fast["response"]["results"][0]["timings"] = {"unexpected": .1}
    elif change == "scalar":
        fast["response"]["results"][0]["retrieval_score"] = .75
    elif change == "source":
        fast["response"]["results"][0]["source"] += "# changed\n"
    elif change == "provenance":
        fast["response"]["results"][0]["source_origin"] = "saved"
    elif change == "semantic":
        capture["semantic_receipt"]["accepted"] = not capture["semantic_receipt"]["accepted"]
    elif change == "role":
        capture["role_guard_receipt"]["accepted"] = False
    elif change == "scope_order":
        fast["response"]["scope"] = dict(reversed(list(fast["response"]["scope"].items())))
    elif change == "payload":
        fast["compact_payload"] += " "
    elif change == "ranks":
        fast["delivered_ranks"] = [None]
    elif change == "extra_capture":
        capture["unknown"] = 0
    elif change == "timings_presence":
        fast["response"]["timings"] = {"total_s": .1}
    elif change in {"timings_key_missing", "timings_key_extra", "timings_negative", "timings_nan", "timings_int", "timings_bool"}:
        original["response"]["timings"] = {"total_s": .1, "packaging_s": .01}
        fast["response"]["timings"] = {"total_s": .2, "packaging_s": .02}
        if change == "timings_key_missing":
            del fast["response"]["timings"]["packaging_s"]
        elif change == "timings_key_extra":
            fast["response"]["timings"]["unreported_s"] = .001
        else:
            fast["response"]["timings"]["total_s"] = {"timings_negative": -.1, "timings_nan": float("nan"),
                                                      "timings_int": 1, "timings_bool": True}[change]
    else:
        original["response"]["timings"] = {}
        fast["response"]["timings"] = []
    with pytest.raises(ValueError):
        audit.verify_pair(original, fast)


def test_nearest_rank_p95_pair_deltas_and_all_three_arm_quality_metrics():
    rows, _, prepared = invented_matrix()
    for row in rows:
        row["elapsed_backend_s"] = float({"legacy": 2, "coverage_control": 2, "original_role": 4, "fast_role": 1}[row["arm"]] + row["repetition"])
    result = audit.metrics(rows, prepared["data"]["queries"])
    dense = result["provider_comparison"]["dense"]
    assert dense == {"original_median_s": 6.0, "fast_median_s": 3.0, "original_p95_s": 7.0, "fast_p95_s": 4.0,
                     "fast_over_original_median_ratio": .5, "median_paired_delta_s": -3.0, "timed_requests_per_arm": 3}
    assert result["role_arms"]["fast_role"]["per_retrieval"]["dense"]["median_wall_ratio_to_controls"] == {"legacy": .75, "coverage_control": .75}
    assert audit.paired_diagnostics(rows)["dense"]["median_paired_fast_over_original_ratio"] == .5


@pytest.mark.parametrize("change", [None, "gap", "overlap", "wall_perf"])
def test_clock_guards_reject_sleep_gaps_overlap_and_wall_perf_discontinuity(change):
    rows, warmups, _ = invented_matrix()
    zero = datetime(2026, 1, 1, tzinfo=timezone.utc)
    common = Path(audit.old.git(ROOT, "rev-parse", "--git-common-dir").decode().strip())
    if not common.is_absolute():
        common = ROOT / common
    acquired = {"pid": 12345, "path": str(common.resolve()/"semantic-search-gpu.lock"), "released_at": None,
                "acquired_at": (zero-timedelta(seconds=1)).isoformat()}
    released = {**acquired, "released_at": (zero+timedelta(seconds=49)).isoformat()}
    state = {"power": "invented receipt", "pid": 12345, "platform": "InventedOS", "machine": "arm64", "python": "invented"}
    setup = {"machine_state": {**state, "recorded_at_utc": (zero+timedelta(seconds=11.6)).isoformat()}}
    summary = {"post_run_machine_state": {**state, "recorded_at_utc": (zero+timedelta(seconds=50)).isoformat()}}
    start = {"started_at_utc": (zero-timedelta(seconds=2)).isoformat()}
    closed = {"phase": "measured_calls", "closed_at": (zero+timedelta(seconds=48)).isoformat()}
    plan = {"runtime_manifest": {"model_binding": {"runtime": {"platform": "InventedOS"}}}}
    if change == "gap":
        for key in ("started_at_utc", "ended_at_utc"):
            rows[1]["request_clock"][key] = (datetime.fromisoformat(rows[1]["request_clock"][key])+timedelta(seconds=31)).isoformat()
    elif change == "overlap":
        rows[1]["request_clock"]["started_perf_counter"] = rows[0]["request_clock"]["started_perf_counter"]
        rows[1]["request_clock"]["ended_perf_counter"] = rows[0]["request_clock"]["ended_perf_counter"]
    elif change == "wall_perf":
        rows[0]["request_clock"]["ended_at_utc"] = (datetime.fromisoformat(rows[0]["request_clock"]["ended_at_utc"])+timedelta(seconds=3)).isoformat()
    if change is None:
        audit.verify_chronology(start, setup, summary, acquired, closed, released, warmups, rows, plan, root=ROOT)
    else:
        with pytest.raises(ValueError, match="interrupted|overlapped|discontinuity"):
            audit.verify_chronology(start, setup, summary, acquired, closed, released, warmups, rows, plan, root=ROOT)


@pytest.mark.parametrize("name", ["numpy", "torch", "mlx.core", "transformers", "semantic_search.backend",
                                  "benchmark_role_native", "experiments.provider_native.study",
                                  "experiments.provider_native.integration", "experiments.dependency_context.fast_provider"])
def test_independent_cli_blocks_producer_model_backend_and_numeric_runtime_imports(name):
    assert audit.forbidden_module(name)


def test_auditor_imports_only_independent_helpers_and_never_native_producer_or_provider():
    tree = ast.parse(Path(audit.__file__).read_text())
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imports.update(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
    assert not {"numpy", "semantic_search.backend", "semantic_search.embeddings", "benchmark_role_native",
                "experiments.provider_native.study", "experiments.provider_native.integration",
                "experiments.dependency_context.fast_provider"} & imports


def test_fresh_independent_process_refuses_numpy_and_network():
    code = ("from experiments.provider_native.audit import independent_cpu_only\n"
            "import socket\n"
            "with independent_cpu_only():\n"
            " try: import numpy\n"
            " except RuntimeError: pass\n"
            " else: raise AssertionError('numpy admitted')\n"
            " try: socket.create_connection(('example.invalid',80))\n"
            " except RuntimeError: pass\n"
            " else: raise AssertionError('network admitted')\n")
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_source_first_barrier_rejects_changed_source_before_model_or_corpus_read(monkeypatch):
    legacy = {"binding": {"runtime_manifest": {"manifest_path": "model.json"}}}
    plan = {"runtime_manifest": {"manifest_path": "model.json"}, "source_freeze": {"sha256": {"source.py": "0"*64}, "bound_blob_count": 1}}
    monkeypatch.setattr(audit, "source_hashes", lambda *_: {"source.py": "1"*64})
    with pytest.raises(ValueError, match="source-first byte closure"):
        audit.first_source_freeze(ROOT, plan, legacy)


def test_self_rebound_parent_file_cannot_replace_immutable_git_blob(tmp_path):
    root = tmp_path / "git"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "evidence.json").write_text("original\n")
    subprocess.run(["git", "-C", str(root), "add", "evidence.json"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Invented", "-c", "user.email=invented@example.invalid",
                    "commit", "-qm", "invented authority"], check=True)
    commit = audit.old.git(root, "rev-parse", "HEAD").decode().strip()
    audit.verify_blobs(root, {"evidence.json": audit.file_sha(root / "evidence.json")}, (commit,))
    (root / "evidence.json").write_text("self-rebound\n")
    with pytest.raises(ValueError):
        audit.verify_blobs(root, {"evidence.json": audit.file_sha(root / "evidence.json")}, (commit,))


def test_missing_required_old_native_and_cpu_proofs_fail_before_source_or_model_reads():
    plan = {"parent": {"commit": audit.PARENT_COMMIT, "artifacts": {}},
            "legacy_plan": {"path": audit.NATIVE+"/plan.json", "sha256": "0"*64,
                            "publication_commit": audit.NATIVE_PUBLICATION}}
    with pytest.raises(ValueError, match="parent closure omitted"):
        audit.parent_authority(ROOT, plan)


def test_actual_source_only_receipt_interoperates_with_producer_pre_model_barrier(tmp_path, monkeypatch):
    # Only the expensive frozen authorities are invented. Exercise the actual
    # receipt assembly and the distinct producer's complete barrier contract.
    from experiments.provider_native import study

    results = tmp_path / "results/invented"
    results.mkdir(parents=True)
    old_folder = tmp_path / audit.NATIVE
    old_folder.mkdir(parents=True)
    raw_inventory = b'{"inventory": [], "definition_keys": {}, "graph_edges": []}\n'
    (results / "source-inventory.json").write_bytes(raw_inventory)
    (old_folder / "source-inventory.json").write_bytes(raw_inventory)
    (old_folder / "input-audit.json").write_text('{"invented_original_input_authority":true}')
    metadata = toy.metadata()
    model_binding = {"metadata": metadata, "checkpoint": {"path": "/invented/never-opened", "files": {}}, "runtime": {}}
    manifest = {"manifest_path": "invented-manifest.json", "model_binding": model_binding}
    (tmp_path / manifest["manifest_path"]).write_text(json.dumps({"model_binding": model_binding}))
    manifest["manifest_sha256"] = audit.file_sha(tmp_path / manifest["manifest_path"])
    parent = {"commit": audit.PARENT_COMMIT, "artifacts": {"invented-original.json": "0"*64}}
    verification = {"commit": audit.PARENT_COMMIT, "bound_artifacts": 1,
                    "old_native_publication": audit.NATIVE_PUBLICATION, "old_native_requests": 252,
                    "cpu_parity_integrity_passed": True, "dependency_quality_qualified": False}
    frozen = {"commit": "1"*40, "sha256": {"source.py": "2"*64}, "bound_blob_count": 1, "committed_at_unix": 1}
    queries = [{"id": "q"+str(index), "query": "invented request", "kind": "present"} for index in range(14)]
    legacy = {"binding": {"embedding": metadata, "runtime_manifest": manifest}, "queries": queries}
    plan = {"schema": audit.SCHEMA, "version": 1, "classification": audit.CLASSIFICATION,
            "source_freeze": frozen, "runtime_manifest": manifest, "parent": parent, "queries": queries,
            "expected_requests": 336, "expected_warmups": 8, "repetitions": 3, "quality_repetition": 1,
            "arms": list(audit.ARMS), "retrievals": list(audit.MODES), "gate": audit.GATE,
            "settings": audit.old.SETTINGS, "artifacts": {"source-inventory.json": audit.sha(raw_inventory)}}
    (results / "plan.json").write_text(json.dumps(plan))
    monkeypatch.setattr(audit, "parent_authority", lambda *_: (legacy, verification))
    monkeypatch.setattr(audit, "first_source_freeze", lambda *_: frozen)
    monkeypatch.setattr(audit, "source_authority", lambda *_: None)
    monkeypatch.setattr(audit.old.semantic, "verify_model_binding", lambda *_: {"invented": True})
    monkeypatch.setattr(audit.old, "fresh_fixture", lambda *_: ({"queries": queries}, toy.fixture(), {"invented_definitions": 1}))
    monkeypatch.setattr(audit.old, "verify_queries", lambda *_: ({row["id"]: row for row in queries}, {}, {}))
    monkeypatch.setattr(audit.old, "git", lambda *_: b"3"*40+b"\n")
    receipt, _ = audit.audit_inputs(tmp_path, tmp_path / "unopened-corpus", results)
    assert receipt["parent"] == parent
    assert receipt["parent_verification"] == verification
    assert receipt["source_only_calls"] == audit.SOURCE_ONLY_CALLS
    monkeypatch.setattr(study, "ROOT", tmp_path)
    study.verify_input_barrier(receipt, plan, audit.file_sha(results / "plan.json"))
    receipt["parent_verification"]["cpu_parity_integrity_passed"] = False
    with pytest.raises(ValueError):
        study.verify_input_barrier(receipt, plan, audit.file_sha(results / "plan.json"))
