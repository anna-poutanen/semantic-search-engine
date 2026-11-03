"""Invented independent native audit contracts; no holdout/model/index reads."""
import ast
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("role_native_independent_test", SCRIPTS / "audit_role_native.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)
QUERY = "load alpha payload and render beta template"
SCOPE = {"source_root": "src", "membership": "saved implementations; Git-tracked files only in Git projects",
         "source_exclusions": [], "exclusions_omitted": 0}


class Counter:
    def encode(self, text, **kwargs):
        return list(text)


def metadata():
    ctx = audit.context
    return {"id": ctx.MODEL_ID, "revision": ctx.MODEL_REVISION, "dimensions": 1024,
        "query_prompt": ctx.QUERY_PROMPT, "normalization": "L2, float32", "weights_dtype": "bfloat16",
        "quantized": False, "document_prompt": None, "max_tokens": 8192, "max_padded_tokens": 8192,
        "batch_size": 4, "packages": {}}


def vector(axis=0):
    return [float(number == axis) for number in range(1024)]


def events(texts):
    if not texts:
        return []
    order = sorted(range(len(texts)), key=lambda i: (len(texts[i]), i))
    result = []
    for number, offset in enumerate(range(0, len(texts), 4)):
        indices = order[offset:offset+4]
        lengths = [len(texts[index]) for index in indices]
        identity = {"schema": "qwen_embedding_forward_v1", "forward_index": number,
            "input_count": len(texts), "batch_input_count": len(indices), "input_indices": indices,
            "token_lengths": lengths, "padded_tokens": len(indices) * max(lengths),
            "padded_shape": [len(indices), max(lengths)], "expected_output_shape": [len(indices), 1024],
            "input_sha256": [audit.sha(texts[index].encode()) for index in indices],
            "token_ids_sha256": [audit.sha(str(index).encode()) for index in indices]}
        result.extend([{**deepcopy(identity), "status": "started", "elapsed_seconds": float(number + .01)},
            {**deepcopy(identity), "status": "completed", "elapsed_seconds": float(number + .11),
             "duration_seconds": .05, "output_shape": [len(indices), 1024], "output_dtype": "float32"}])
    return result


def fixture():
    text = "def load():\n    return value\n"
    item = {"id": "src/load.py:load:1-2", "path": "src/load.py", "symbol": "load", "start_line": 1,
            "end_line": 2, "text": text, "content_hash": audit.sha(text.encode())}
    return {"inventory": {item["id"]: item}, "sources": {item["path"]: text},
            "definition_keys": {item["id"]: [item["path"], item["symbol"], 1, 2]}, "graph_edges": {}}


def trace(arm="candidate", *, query=QUERY, mode="dense", empty=False):
    frozen, meta = fixture(), metadata()
    item = next(iter(frozen["inventory"].values()))
    ranked = [] if empty else [{"snippet": deepcopy(item), "score": .5}]
    selected, eligible, origins = audit.native.baseline(ranked, frozen["inventory"], frozen["definition_keys"], {},
        canonical_definitions=arm != "legacy")
    before = audit.source.ordinary_pack(audit.native.native_template(query, SCOPE, ranked), eligible, origins, Counter())
    reference = deepcopy(before if arm == "legacy" else audit.source.coalesce_pack(before, audit.native.full_rows(selected), Counter()))
    reference["retrieval"] = mode
    raw, clauses = audit.native.full_rows(ranked), audit.context.literal_clauses(query)
    original_events = events([audit.context.QUERY_PROMPT + query])
    captured, reads, extra = None, [], []
    chosen = reference
    if arm == "candidate":
        features, receipts = None, []
        if clauses and raw and reference["results"]:
            full = {value["id"]: value for value in [*raw, *reference["results"]] if not audit.context.clipped(value)}
            windows = {identifier: {"binding": audit.context.row_binding(value), "vector": vector()}
                       for identifier, value in full.items()}
            fp = audit.semantic.model_fingerprint(meta)
            features = {"schema": audit.context.FEATURE_SCHEMA, "query": query, "facets": [query, *clauses],
                "query_prompt": audit.context.QUERY_PROMPT, "model_metadata": meta, "model_fingerprint": fp,
                "facet_vectors": [vector() for _ in range(len(clauses) + 1)], "windows": windows}
            extra = events([audit.context.QUERY_PROMPT + clause for clause in clauses])
            receipts = [{"schema": "semantic_context_live_provider_v1", "status": "active", "query": query,
                "retrieval": mode, "source_snapshot": "invented-snapshot", "model_metadata": meta, "model_fingerprint": fp,
                "facets": [query, *clauses], "extra_clause_prompts": [audit.context.QUERY_PROMPT + value for value in clauses],
                "original_query_vector": {"origin": "executing_retrieval_query_vector", "reencoded": False,
                    "dimensions": 1024, "float32_le_sha256": audit.vector_hash(vector())},
                "original_query_forward_events": original_events, "original_query_model_forward_calls": 1,
                "original_query_encoded_texts": 1, "extra_query_forward_events": extra, "extra_query_api_calls": 1,
                "extra_query_model_forward_calls": len(extra)//2, "extra_query_encoded_texts": len(clauses),
                "resident_vector_read_attempts": 1, "resident_vector_reads": 1, "document_inference_calls": 0,
                "extra_retrieval_calls": 0, "windows": {identifier: {"binding": value["binding"],
                    "float32_le_sha256": audit.vector_hash(value["vector"])} for identifier, value in windows.items()}}]
            reads = [{"requested_rows": list(full.values()), "model_metadata": meta, "index_path": "/invented/index.sqlite3",
                "returned_vectors": {identifier: {"vector": value["vector"], "float32_le_sha256": audit.vector_hash(value["vector"])}
                                     for identifier, value in windows.items()}}]
        elif clauses:
            receipts = [{"schema": "semantic_context_live_provider_v1", "status": "inactive", "reason": "empty_raw_pool",
                "query": query, "retrieval": mode, "extra_query_api_calls": 0, "extra_query_model_forward_calls": 0,
                "extra_query_encoded_texts": 0, "resident_vector_reads": 0, "document_inference_calls": 0, "extra_retrieval_calls": 0}]
        proposed, semantic = audit.context.reconstruct(reference, raw, features, definition_keys=frozen["definition_keys"],
            precoalesce_rows=before["results"], counter=Counter(), **audit.role.LIMITS)
        actual_semantic = deepcopy(semantic)
        if features is None and actual_semantic.get("fallback_reason") == "invalid_features":
            actual_semantic["feature_error"] = "Feature schema fields differ"
        chosen, role = audit.role.reconstruct(reference, proposed, definition_keys=frozen["definition_keys"])
        captured = {"reference_response": reference, "precoalesce_rows": before["results"], "ranked_candidates": raw,
            "definition_keys": frozen["definition_keys"], "features": features, "semantic_receipt": actual_semantic,
            "v1_response": proposed, "response": chosen, "role_guard_receipt": role, "provider_receipts": receipts,
            "limits": dict(audit.role.LIMITS), "calls": {"coverage_coalesce": 1, "semantic": 1, "role_guard": 1,
                "feature": int(bool(clauses)), "current_v1": 1, "extra_retrieval": 0, "document_inference": 0, "backfill_after_guard": 0},
            "request_binding": {"query": query, "retrieval": mode, "source_snapshot": "invented-snapshot",
                                "model_fingerprint": audit.semantic.model_fingerprint(meta)},
            "phase_timings": {name: .01 for name in ("coverage_context_s", "live_feature_provider_s",
                                                    "prepared_feature_allocation_s", "terminal_role_guard_s")}}
        captured.update(coverage_coalesce_calls=1, semantic_calls=1, feature_calls=int(bool(clauses)))
    payload = audit.context.compact_payload(chosen)
    empty, original, extras = (audit.successful_work(value) for value in ([], original_events, extra))
    counts = {"documents": empty, "retrieval_query": original, "extra_queries": extras, "unknown_external_events": 0,
        "extra_receipts_consistent": True, "reported_total_model_forward_calls": 1 + len(extra)//2,
        "reported_request_document_inference_calls": 0, "receipt_coverage_complete": True,
        "scope": "Reported execution receipts; started events alone do not prove a model forward"}
    return {"query_id": "invented", "kind": "present", "repetition": 1, "retrieval": mode, "arm": arm,
        "condition": audit.MODE if arm == "candidate" else arm, "elapsed_backend_s": 1.0, "multi_file": False,
        "candidate_ranks": [1], "delivered_ranks": [1], "stages": {"ranked": ranked, "selected": selected,
            "eligible": eligible, "origins": origins, "definition_keys": frozen["definition_keys"], "ambiguous_symbols": []},
        "pre_postprocess_response": before, "backfill_candidates": audit.native.full_rows(selected), "ranked_candidates": raw,
        "reference_response": deepcopy(captured["reference_response"]) if captured else None,
        "proposal_response": deepcopy(captured["v1_response"]) if captured else None,
        "response": chosen, "compact_payload": payload, "compact_sha256": audit.sha(payload.encode()),
        "actual_compact_tokens": len(payload), "query_vector": vector(), "query_vector_sha256": audit.vector_hash(vector()),
        "external_forward_events": [{"purpose": "retrieval_query", **event} for event in original_events],
        "candidate_capture": captured, "resident_reads": reads, "query_embedding_calls": 1,
        "document_embedding_calls": 0, "clause_query_calls": int(bool(extra) and arm == "candidate"),
        "clause_query_texts": len(clauses) if extra and arm == "candidate" else 0, "guard_calls": int(arm == "candidate"),
        "forward_counts": counts, "request_clock": {"started_perf_counter": 10.0, "ended_perf_counter": 11.0,
            "started_at_utc": "2026-01-01T00:00:00+00:00", "ended_at_utc": "2026-01-01T00:00:01+00:00"}}


def verify(value, query=QUERY):
    return audit.verify_trace(value, fixture(), Counter(), query=query, scope=SCOPE, model_metadata=metadata())


@pytest.mark.parametrize("arm", audit.ARMS)
@pytest.mark.parametrize("mode", audit.RETRIEVALS)
def test_independent_native_controls_features_guard_and_json_roundtrip(arm, mode):
    value = trace(arm, mode=mode)
    before = deepcopy(value)
    restored = json.loads(json.dumps(value, ensure_ascii=False))
    activity = verify(restored)
    audit.verify_forward_counts(restored)
    assert value == before
    assert activity["active"] is (arm == "candidate")
    assert activity["extra_texts"] == (2 if arm == "candidate" else 0)
    assert activity["diagnostic_normalization"] is None


@pytest.mark.parametrize("change", ["query_vector", "original_reencoded", "original_events", "clause_cache", "clause_texts",
    "clause_prompt", "clauses_failed", "resident_count", "resident_membership", "resident_vector", "resident_source",
    "metadata", "document_forward", "unknown_forward", "decision", "v1_source", "role_calls", "backfill",
    "raw_score", "scope_order", "payload", "tokens", "clock", "outside_timer", "bool_count", "forward_count"])
def test_native_audit_rejects_tampered_execution_source_and_accounting(change):
    value = trace()
    captured = value["candidate_capture"]
    provider = captured["provider_receipts"][0]
    if change == "query_vector":
        value["query_vector"] = vector(1)
        value["query_vector_sha256"] = audit.vector_hash(value["query_vector"])
    elif change == "original_reencoded":
        provider["original_query_vector"]["reencoded"] = True
    elif change == "original_events":
        provider["original_query_forward_events"] = []
    elif change == "clause_cache":
        value["clause_query_calls"] = 0
    elif change == "clause_texts":
        value["clause_query_texts"] += 1
    elif change == "clause_prompt":
        provider["extra_query_forward_events"][0]["input_sha256"][0] = "0" * 64
    elif change == "clauses_failed":
        provider["extra_query_forward_events"][1]["status"] = "failed"
    elif change == "resident_count":
        value["resident_reads"].append(deepcopy(value["resident_reads"][0]))
    elif change == "resident_membership":
        value["resident_reads"][0]["returned_vectors"] = {}
    elif change == "resident_vector":
        identifier = next(iter(value["resident_reads"][0]["returned_vectors"]))
        value["resident_reads"][0]["returned_vectors"][identifier] = {"vector": vector(1), "float32_le_sha256": audit.vector_hash(vector(1))}
    elif change == "resident_source":
        value["resident_reads"][0]["requested_rows"][0]["symbol"] = "different"
    elif change == "metadata":
        provider["model_metadata"]["batch_size"] = 8
    elif change in ("document_forward", "unknown_forward"):
        value["external_forward_events"][0]["purpose"] = "documents" if change == "document_forward" else "unknown"
    elif change == "decision":
        captured["role_guard_receipt"]["accepted"] = False
    elif change == "v1_source":
        captured["v1_response"]["results"][0]["source"] += "#changed\n"
    elif change == "role_calls":
        captured["calls"]["current_v1"] = 2
    elif change == "backfill":
        captured["calls"]["backfill_after_guard"] = 1
    elif change == "raw_score":
        value["ranked_candidates"][0]["retrieval_score"] += .1
    elif change == "scope_order":
        value["response"]["scope"] = dict(reversed(list(value["response"]["scope"].items())))
    elif change == "payload":
        value["compact_payload"] += " "
    elif change == "tokens":
        value["actual_compact_tokens"] += 1
    elif change == "clock":
        value["elapsed_backend_s"] = .9
    elif change == "outside_timer":
        value["request_clock"]["ended_perf_counter"] = 10.01
        value["elapsed_backend_s"] = value["request_clock"]["ended_perf_counter"] - 10.0
    elif change == "bool_count":
        value["document_embedding_calls"] = False
    else:
        value["forward_counts"]["reported_total_model_forward_calls"] = 1
    with pytest.raises(ValueError):
        verify(value)
        audit.verify_forward_counts(value)


def test_only_exact_published_inactive_none_feature_wording_is_normalized():
    value = trace(empty=True)
    activity = verify(value)
    assert activity["diagnostic_normalization"] == {"actual": "Feature schema fields differ", "independent": "Semantic feature schema"}
    assert activity["active"] is False and activity["extra_texts"] == 0
    for message in ("unrelated", "", None, 1):
        wrong = deepcopy(value)
        wrong["candidate_capture"]["semantic_receipt"]["feature_error"] = message
        with pytest.raises(ValueError):
            verify(wrong)


def test_diagnostic_normalization_cannot_conceal_another_receipt_change():
    value = trace(empty=True)
    value["candidate_capture"]["semantic_receipt"]["accepted"] = True
    with pytest.raises(ValueError):
        verify(value)


def test_python_ast_inventory_scopes_decorators_overloads_and_placeholders():
    source = '''from typing import overload
@overload
def overloaded(value): ...
def placeholder():
    "doc"
    raise NotImplementedError()
class Parent:
    @decorator
    async def method(self):
        "doc"
        def nested():
            return 1
        return nested()
def real():
    return value
'''
    result = audit.python_inventory({"src/code.py": source})
    assert result["definitions"] == 3
    assert [item["symbol"] for item in result["inventory"]] == ["Parent.method", "Parent.method.nested", "real"]
    assert result["definition_keys"]["src/code.py:Parent.method:8-13"] == ["src/code.py", "Parent.method", 8, 13]
    audit.verify_python_inventory(result, {"src/code.py": source})


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_python_inventory_preserves_nonascii_physical_windows(newline):
    source = newline.join(["def café():", "    return 'é'", ""])
    result = audit.python_inventory({"src/café.py": source})
    assert result["inventory"][0]["text"] == source
    assert result["definition_keys"]["src/café.py:café:1-2"] == ["src/café.py", "café", 1, 2]


def test_python_160_40_windows_skip_doc_only_prefix_and_cover_implementation():
    source = "def long():\n    \"\"\"\n" + "doc\n" * 180 + "    \"\"\"\n    return value\n"
    result = audit.python_inventory({"src/long.py": source})
    assert result["definitions"] == 1
    assert [(item["start_line"], item["end_line"]) for item in result["inventory"]] == [(121, 184)]
    assert next(iter(result["definition_keys"].values())) == ["src/long.py", "long", 1, 184]
    audit.verify_python_inventory(result, {"src/long.py": source})


@pytest.mark.parametrize("change", ["missing", "key", "source", "window", "extra"])
def test_complete_inventory_rejects_omissions_and_rewritten_boundaries(change):
    sources = {"src/code.py": "def one():\n    return 1\ndef two():\n    return 2\n"}
    result = audit.python_inventory(sources)
    if change == "missing":
        result["inventory"].pop()
    elif change == "key":
        next(iter(result["definition_keys"].values()))[3] += 1
    elif change == "source":
        result["inventory"][0]["text"] += "# changed\n"
    elif change == "window":
        result["inventory"][0]["start_line"] += 1
    else:
        result["inventory"].append(deepcopy(result["inventory"][0]))
    with pytest.raises(ValueError):
        audit.verify_python_inventory(result, sources)


def measured_rows():
    rows = []
    queries = [{"id": f"invented_{number}"} for number in range(14)]
    for repetition, query, arm, retrieval in audit.expected_schedule(queries):
        row = trace(arm, mode=retrieval)
        number = int(query.rsplit("_", 1)[1])
        row.update(query_id=query, repetition=repetition, kind="present" if number < 12 else "absent", multi_file=number < 8,
                   elapsed_backend_s=1.2 if arm == "candidate" else 1.0)
        row["delivered_ranks"] = ([1, 2] if arm == "candidate" else [1, None]) if number < 8 else [1] if number < 12 else []
        rows.append(row)
    return rows, queries


def test_complete_schedule_rotation_stability_and_mode_specific_gates():
    rows, queries = measured_rows()
    assert len(rows) == 252
    audit.verify_repeats(rows, queries)
    result = audit.native_metrics(rows)
    assert result["dense_promotion_gate_passed"] is True
    assert result["per_retrieval"]["dense"]["arms"]["candidate"]["measured_calls"] == 42
    assert result["per_retrieval"]["dense"]["arms"]["candidate"]["present_queries"] == 12
    assert result["per_retrieval"]["dense"]["median_wall_ratio_to_controls"] == {"legacy": 1.2, "coverage_control": 1.2}


@pytest.mark.parametrize("change", ["missing", "schedule", "vector", "raw_score", "payload"])
def test_native_repeat_proof_rejects_missing_order_or_bit_identity_changes(change):
    rows, queries = measured_rows()
    if change == "missing":
        rows.pop()
    elif change == "schedule":
        rows[0], rows[1] = rows[1], rows[0]
    elif change == "vector":
        rows[-1]["query_vector_sha256"] = "0" * 64
    elif change == "raw_score":
        rows[-1]["stages"]["ranked"][0]["score"] = .75
    else:
        rows[-1]["compact_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        audit.verify_repeats(rows, queries)


def test_hybrid_gain_or_latency_cannot_rescue_dense_failure():
    rows, _ = measured_rows()
    for row in rows:
        if row["retrieval"] == "dense" and row["arm"] == "candidate":
            row["elapsed_backend_s"] = 1.26
    result = audit.native_metrics(rows)
    assert result["dense_promotion_gate_passed"] is False
    assert result["per_retrieval"]["hybrid"]["passed"] is True
    # A group loss in an already incomplete query also fails independently.
    wrong = next(row for row in rows if row["retrieval"] == "hybrid" and row["arm"] == "candidate" and row["repetition"] == 1)
    wrong["delivered_ranks"] = [None, 2]
    assert audit.native_metrics(rows)["per_retrieval"]["hybrid"]["passed"] is False


def test_parent_rejection_happens_before_any_fresh_hash_or_first_source_read(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(audit, "read", lambda _: {"binding": {"implementation_sha256": {"fresh.json": "a" * 64}}, "frozen_at_utc": "invented"})

    def reject(*args, **kwargs):
        seen.append("parent")
        raise ValueError("unqualified parent")

    def forbidden(*args, **kwargs):
        raise AssertionError("Fresh input/source/tokenizer/model bytes read before the parent gate")

    monkeypatch.setattr(audit, "development_parent", reject)
    monkeypatch.setattr(audit, "file_sha", forbidden)
    monkeypatch.setattr(audit, "first_source_freeze", forbidden)
    with pytest.raises(ValueError, match="unqualified parent"):
        audit.preflight_authority(tmp_path, tmp_path)
    assert seen == ["parent"]


def test_source_freeze_rejection_happens_before_fresh_implementation_hash(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(audit, "read", lambda _: {"binding": {"implementation_sha256": {}}, "frozen_at_utc": "invented"})
    monkeypatch.setattr(audit, "development_parent", lambda *args, **kwargs: seen.append("parent"))

    def reject(*args, **kwargs):
        seen.append("source")
        raise ValueError("uncommitted source")

    monkeypatch.setattr(audit, "first_source_freeze", reject)
    monkeypatch.setattr(audit, "file_sha", lambda *_: pytest.fail("fresh hash before source-first freeze"))
    with pytest.raises(ValueError, match="uncommitted source"):
        audit.preflight_authority(tmp_path, tmp_path)
    assert seen == ["parent", "source"]


def test_native_auditor_imports_no_producer_or_model_runtime():
    tree = ast.parse((SCRIPTS / "audit_role_native.py").read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(item.name.split(".")[0] for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
    assert not names & {"semantic_search", "benchmark_role_native", "research_role_context", "numpy", "torch", "mlx", "transformers"}


def test_actual_fresh_cpu_cli_rejects_parent_before_touching_any_fresh_declared_path(tmp_path):
    folder = tmp_path / "invented-plan"
    folder.mkdir()
    value = {"binding": {"development_parent": {"publication_commit": "unqualified"},
                         "implementation_sha256": {"fresh-path-must-never-be-opened.json": "0" * 64}}}
    (folder / "plan.json").write_text(json.dumps(value))
    target = tmp_path / "input-audit.json"
    result = subprocess.run([sys.executable, str(SCRIPTS / "audit_role_native.py"), "--root", str(SCRIPTS.parent),
        "--corpus", str(tmp_path / "absent-invented-corpus"), "--results", str(folder), "--check-inputs", "--output", str(target)],
        capture_output=True, text=True, check=False)
    assert result.returncode != 0
    failure = json.loads(target.with_name("input-audit-failure.json").read_text())
    assert failure["error_type"] == "ValueError" and "Exact public development parent paths" in failure["error"]
    assert failure["source_sha256"] == {} and failure["calls"] == {"model": 0, "index": 0, "retrieval": 0, "network": 0}
    assert not target.exists()


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args]).decode().strip()


def test_first_source_freeze_split_counts_full_union_bytes_and_preparation_time(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "invented@example.test")
    git(tmp_path, "config", "user.name", "Invented")
    for name in (*audit.SOURCES, "src/semantic_search/nested/module.py", "pyproject.toml", "requirements-mac.lock", "old-parent.json"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# invented source\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "invented first source freeze")
    commit = git(tmp_path, "rev-parse", "HEAD")
    names = {name: audit.file_sha(tmp_path / name) for name in
             (*audit.SOURCES, "src/semantic_search/nested/module.py", "pyproject.toml", "requirements-mac.lock")}
    parent = {"old-parent.json": audit.file_sha(tmp_path / "old-parent.json")}
    frozen = {"commit": commit, "sha256": names, "bound_blob_count": len(names), "parent_bound_blob_count": 1,
              "committed_at_unix": int(git(tmp_path, "show", "-s", "--format=%ct", commit))}
    binding = {"native_source_freeze": frozen, "development_parent": {"artifacts": parent}}
    audit.first_source_freeze(tmp_path, binding, before=datetime.now(timezone.utc).isoformat())
    for wrong in ("count", "parent_count", "nested", "time"):
        changed = deepcopy(binding)
        if wrong == "count":
            changed["native_source_freeze"]["bound_blob_count"] += 1
        elif wrong == "parent_count":
            changed["native_source_freeze"]["parent_bound_blob_count"] = 0
        elif wrong == "nested":
            changed["native_source_freeze"]["sha256"].pop("src/semantic_search/nested/module.py")
        else:
            changed["native_source_freeze"]["committed_at_unix"] -= 1
        with pytest.raises(ValueError):
            audit.first_source_freeze(tmp_path, changed)
    (tmp_path / "old-parent.json").write_text("changed parent bytes\n")
    with pytest.raises(ValueError, match="Bound bytes differ"):
        audit.first_source_freeze(tmp_path, binding)


@pytest.mark.parametrize("change", ["missing", "negative", "too_long", "bool"])
def test_phase_receipts_are_disjoint_finite_actual_work_within_whole_search(change):
    value = trace()
    phases = value["candidate_capture"]["phase_timings"]
    if change == "missing":
        phases.pop("live_feature_provider_s")
    elif change == "negative":
        phases["live_feature_provider_s"] = -.1
    elif change == "too_long":
        phases["live_feature_provider_s"] = 1.1
    else:
        phases["live_feature_provider_s"] = False
    with pytest.raises(ValueError):
        verify(value)


def flat_review_fixture():
    frozen = fixture()
    rows = list(frozen["inventory"].values())
    windows = [{name: row[name] for name in ("id", "path", "symbol", "start_line", "end_line", "content_hash")} for row in rows]
    source_files = {name: {"bytes": len(text.encode()), "sha256": audit.sha(text.encode())} for name, text in frozen["sources"].items()}
    unsupported = {"src/py.typed": {"bytes": 0, "sha256": audit.sha(b"")}}
    counts = {"queries": 1, "present": 1, "multi_file_present": 0, "single_file_present": 1, "scoped_absent": 0,
              "required_positive_evidence_groups": 1, "target_rows": 1, "source_files": 1, "excluded_supported_files": 0,
              "unsupported_source_root_files": 1, "source_windows_160_40": 1, "extracted_definitions": 1,
              "source_files_with_extracted_implementations": 1, "canonical_definitions_with_multiple_windows": 0}
    author = {"version": 1, "status": "passed", "dataset_sha256": "d" * 64, "protocol_sha256": "b" * 64,
        "source_repository_code_executed": False, "source_only_precommit_receipt": True,
        "calls": {name: 0 for name in ("model", "index", "retrieval", "rule_activation", "network")}, "counts": deepcopy(counts),
        "source_files": source_files, "excluded_supported_files": {}, "unsupported_source_root_files": unsupported,
        "window_inventory": windows, "definition_keys": frozen["definition_keys"],
        "window_inventory_sha256": audit.sha(audit.canonical(windows) + b"\n"),
        "definition_keys_sha256": audit.sha(audit.canonical(frozen["definition_keys"]) + b"\n")}
    review = {"status": "passed", "dataset_sha256": "d" * 64, "protocol_sha256": "b" * 64,
        "revision": "c" * 40, "author_source_receipt_sha256": "a" * 64, "counts": deepcopy(counts),
        "calls": {name: 0 for name in ("index", "model", "network", "reranker", "retrieval", "selector_activation")},
        "dataset_changed": False, "blocking_findings": [], "upstream_repository_module_or_test_executed": False,
        "semantic_review": {name: True for name in audit.REVIEW_TRUE_FIELDS}}
    arguments = {"author_sha256": "a" * 64, "dataset_sha256": "d" * 64, "protocol_sha256": "b" * 64, "revision": "c" * 40,
                 "source_files": source_files, "excluded_files": {}, "unsupported_files": unsupported,
                 "inventory": rows, "definition_keys": frozen["definition_keys"], "counts": counts}
    return review, author, arguments


def test_original_flat_reviews_are_checked_without_projection_or_input_mutation():
    review, author, arguments = flat_review_fixture()
    before = deepcopy((review, author, arguments))
    value = audit.verify_flat_reviews(review, author, **arguments)
    assert (review, author, arguments) == before
    assert value["format"] == "attrs_flat_source_review_v1" and value["persisted_normalized_receipt"] is False
    assert "dataset" not in review and "corpus" not in review  # No fake legacy receipt persisted.


@pytest.mark.parametrize("change", ["status", "dataset", "protocol", "revision", "author_link", "model", "selector",
    "bool_call", "upstream", "author_executed", "precommit", "dataset_changed", "blocker", "semantic_flag", "counts",
    "source_files", "unsupported", "window", "definition", "hash_framing"])
def test_flat_review_compatibility_rejects_altered_original_evidence(change):
    review, author, arguments = deepcopy(flat_review_fixture())
    if change in ("status", "dataset", "protocol", "revision", "author_link"):
        key = {"status": "status", "dataset": "dataset_sha256", "protocol": "protocol_sha256",
               "revision": "revision", "author_link": "author_source_receipt_sha256"}[change]
        review[key] = "changed"
    elif change in ("model", "selector", "bool_call"):
        review["calls"]["selector_activation" if change == "selector" else "model"] = False if change == "bool_call" else 1
    elif change == "upstream":
        review["upstream_repository_module_or_test_executed"] = True
    elif change == "author_executed":
        author["source_repository_code_executed"] = True
    elif change == "precommit":
        author["source_only_precommit_receipt"] = False
    elif change == "dataset_changed":
        review["dataset_changed"] = True
    elif change == "blocker":
        review["blocking_findings"] = ["unresolved source issue"]
    elif change == "semantic_flag":
        review["semantic_review"][audit.REVIEW_TRUE_FIELDS[0]] = False
    elif change == "counts":
        review["counts"]["queries"] += 1
    elif change == "source_files":
        author["source_files"] = {}
    elif change == "unsupported":
        author["unsupported_source_root_files"] = {}
    elif change == "window":
        author["window_inventory"][0]["start_line"] += 1
    elif change == "definition":
        next(iter(author["definition_keys"].values()))[3] += 1
    else:
        author["window_inventory_sha256"] = audit.sha(audit.canonical(author["window_inventory"]))
    with pytest.raises(ValueError):
        audit.verify_flat_reviews(review, author, **arguments)


def test_compatibility_requires_preserved_failed_attempt_and_versioned_history(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "invented@example.test")
    git(tmp_path, "config", "user.name", "Invented")
    (tmp_path / "source.py").write_text("# initial source\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "invented original source freeze")
    original = git(tmp_path, "rev-parse", "HEAD")
    folder = tmp_path / "failed-attempt"
    folder.mkdir()
    (folder / "prepare.log").write_text("invented source-only interface failure\n")
    receipt = {"status": "failed_source_only_preparation", "source_freeze": original, "output_plan_created": False,
        "log_sha256": audit.file_sha(folder / "prepare.log"), **{name: 0 for name in
            ("model_calls", "index_calls", "retrieval_calls", "native_outcomes")}}
    (folder / "failure.json").write_text(json.dumps(receipt))
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "preserve invented failed source-only preparation")
    failed = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "source.py").write_text("# explicitly corrected interface\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "invented corrective source freeze")
    corrected = git(tmp_path, "rev-parse", "HEAD")
    monkeypatch.setattr(audit, "PRIOR_SOURCE_FREEZE", original)
    monkeypatch.setattr(audit, "FAILED_PREPARATION_COMMIT", failed)
    monkeypatch.setattr(audit, "FAILED_PREPARATION", "failed-attempt")
    hashes = {"failed-attempt/" + name: audit.file_sha(folder / name) for name in ("failure.json", "prepare.log")}
    compatibility = {"prior_source_freeze": original, "failed_preparation_commit": failed,
        "failed_preparation_artifacts": hashes, "projection_scope": "in_memory_legacy_helper_only"}
    binding = {"source_review_compatibility": compatibility, "native_source_freeze": {"commit": corrected},
               "implementation_sha256": hashes}
    audit.verify_review_compatibility(tmp_path, binding)
    wrong = deepcopy(binding)
    wrong["source_review_compatibility"]["failed_preparation_artifacts"].pop("failed-attempt/prepare.log")
    with pytest.raises(ValueError, match="Both original"):
        audit.verify_review_compatibility(tmp_path, wrong)
    receipt["model_calls"] = 1
    (folder / "failure.json").write_text(json.dumps(receipt))
    rebound = deepcopy(binding)
    rebound["source_review_compatibility"]["failed_preparation_artifacts"]["failed-attempt/failure.json"] = audit.file_sha(folder / "failure.json")
    rebound["implementation_sha256"]["failed-attempt/failure.json"] = audit.file_sha(folder / "failure.json")
    with pytest.raises(ValueError, match="Committed bound bytes"):
        audit.verify_review_compatibility(tmp_path, rebound)
