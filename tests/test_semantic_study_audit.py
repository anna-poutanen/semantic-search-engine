"""Invented stdlib audit contracts; no observed corpus, feature or model reads."""
from copy import deepcopy
import importlib.util
import gzip
import json
from pathlib import Path
import sys
from datetime import datetime, timezone
import subprocess

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("semantic_study_independent_test", SCRIPTS / "audit_semantic_study.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)
ctx = audit.context


def metadata():
    return {"id": ctx.MODEL_ID, "revision": ctx.MODEL_REVISION, "dimensions": ctx.DIMENSIONS,
            "query_prompt": ctx.QUERY_PROMPT, "normalization": "L2, float32",
            "weights_dtype": "bfloat16", "quantized": False, "document_prompt": None,
            "max_tokens": 8192, "max_padded_tokens": 8192, "batch_size": 4, "packages": {}}


def vector(axis=0):
    return [float(i == axis) for i in range(ctx.DIMENSIONS)]


def events(texts, groups=None):
    groups = groups or [sorted(range(len(texts)), key=lambda i: (min(len(texts[i]), 8192), i))]
    output = []
    for index, indices in enumerate(groups):
        lengths = [min(len(texts[i]), 8192) for i in indices]
        event = {"schema": "qwen_embedding_forward_v1", "forward_index": index,
                 "input_count": len(texts), "batch_input_count": len(indices), "input_indices": indices,
                 "token_lengths": lengths, "padded_tokens": len(indices) * max(lengths),
                 "padded_shape": [len(indices), max(lengths)], "expected_output_shape": [len(indices), 1024],
                 "input_sha256": [audit.sha(texts[i].encode()) for i in indices],
                 "token_ids_sha256": [audit.sha(str(i).encode()) for i in indices]}
        output += [{**deepcopy(event), "status": "started", "elapsed_seconds": index + 0.1},
                   {**deepcopy(event), "status": "completed", "elapsed_seconds": index + 0.3,
                    "duration_seconds": 0.05, "output_shape": [len(indices), 1024], "output_dtype": "float32"}]
    return output


def full_row():
    source = "def load():\n    return payload\n"
    return {"id": "invented", "path": "src/load.py", "symbol": "load", "start_line": 1,
            "end_line": 2, "window_end_line": 2, "source": source, "source_hash": audit.sha(source.encode()),
            "content_hash": audit.sha(source.encode()), "truncated": False,
            "retrieval_score": 0.5, "h2o_score": None, "qwen_score": None,
            "origin": "retrieval", "related_to": None}


class Counter:
    def encode(self, text, **kwargs):
        return list(text.encode())


def capture_fixture():
    query = "load alpha payload and render beta template"
    facets = [query, *ctx.literal_clauses(query)]
    row = full_row()
    text = ctx.document_text(row)
    doc_id, request_id = "doc_" + audit.sha(text.encode()), "query_" + audit.sha(audit.canonical(
        {"query": query, "facets": facets, "query_prompt": ctx.QUERY_PROMPT}))
    requests = {"document_requests": [{"document_id": doc_id, "document_sha256": audit.sha(text.encode()), "text": text}],
                "query_requests": [{"request_id": request_id, "query": query, "facets": facets, "case_ids": ["case"]}],
                "cases": [{"case_id": "case", "active": True, "reason": None, "query_request_id": request_id,
                           "window_documents": {row["id"]: doc_id}, "window_bindings": {row["id"]: ctx.row_binding(row)}}]}
    doc_events = events([text])
    full_events = events([ctx.QUERY_PROMPT + query])
    clause_events = events([ctx.QUERY_PROMPT + clause for clause in facets[1:]])
    records = []
    for stage, identity, values in (("documents", "documents", doc_events),
                                    ("query_full", request_id, full_events), ("query_clauses", request_id, clause_events)):
        records += [{"sequence": len(records) + i, "stage": stage, "request_id": identity, "event": value}
                    for i, value in enumerate(values)]
    doc = {"document_id": doc_id, "document_sha256": audit.sha(text.encode()), "vector": vector(),
           "float32_le_sha256": audit.vector_hash(vector())}
    vectors = [vector(i) for i in range(len(facets))]
    queries = {request_id: {"query": query, "facets": facets, "vectors": vectors,
                           "vector_hashes": [audit.vector_hash(v) for v in vectors],
                           "literal_prompts_sha256": [audit.sha((ctx.QUERY_PROMPT + facet).encode()) for facet in facets],
                           "origin": audit.FRESH_ORIGIN, "historical_query_vector_present": False,
                           "full_query_forward_events": full_events, "clause_forward_events": clause_events}}
    summary = {"documents": {"api_calls": 1, "model_forward_calls": 1, "encoded_texts": 1},
               "query_full": {"api_calls": 1, "model_forward_calls": 1, "encoded_texts": 1},
               "query_clauses": {"api_calls": 1, "model_forward_calls": 1, "encoded_texts": len(facets) - 1},
               "total_model_forward_calls": 3, "total_encoded_texts": len(facets) + 1}
    capture = {"status": "passed", "model_metadata": metadata(), "model_fingerprint": audit.model_fingerprint(metadata()),
               "documents": {doc_id: doc}, "queries": queries, "forward_summary": summary,
               "calls": {"document_api_calls": 1, "full_query_api_calls": 1, "clause_api_calls": 1,
                         "retrieval_calls": 0, "index_calls": 0, "cache_hits": 0}}
    doc_checkpoints = [{"input_indices": [0], "documents": [deepcopy(doc)]}]
    query_checkpoints = [{"stage": stage, "request_id": request_id, "vectors": values,
                          "float32_le_sha256": [audit.vector_hash(v) for v in values]}
                         for stage, values in (("query_full", vectors[:1]), ("query_clauses", vectors[1:]))]
    return capture, requests, records, doc_checkpoints, query_checkpoints


def verify_fixture(value):
    capture, requests, forwards, documents, queries = value
    return audit.verify_capture(capture, requests, forward_events=forwards,
                                document_checkpoints=documents, query_checkpoints=queries)


def test_invented_capture_is_exact_and_independent():
    fixture = capture_fixture()
    assert verify_fixture(fixture) == fixture[0]["forward_summary"]


@pytest.mark.parametrize("mutation", [
    "status", "model_fp", "query_origin", "historical_reuse", "facet_order", "document_wrapper",
    "document_vector", "document_checkpoint", "document_id", "query_checkpoint", "query_events",
    "global_sequence", "stage_order", "forward_count", "encoded_count", "extra_document", "missing_query",
])
def test_capture_tampering_is_rejected(mutation):
    fixture = capture_fixture()
    capture, requests, forwards, documents, queries = fixture
    query_id, doc_id = next(iter(capture["queries"])), next(iter(capture["documents"]))
    if mutation == "status":
        capture["status"] = "failed"
    elif mutation == "model_fp":
        capture["model_fingerprint"] = "wrong"
    elif mutation == "query_origin":
        capture["queries"][query_id]["origin"] = "original_retrieval_vector"
    elif mutation == "historical_reuse":
        capture["queries"][query_id]["historical_query_vector_present"] = True
    elif mutation == "facet_order":
        capture["queries"][query_id]["facets"].reverse()
    elif mutation == "document_wrapper":
        requests["document_requests"][0]["text"] += "changed"
    elif mutation == "document_vector":
        capture["documents"][doc_id]["vector"][0] = 0.1
    elif mutation == "document_checkpoint":
        documents[0]["documents"][0]["vector"] = vector(7)
    elif mutation == "document_id":
        documents[0]["documents"][0]["document_id"] = "other"
    elif mutation == "query_checkpoint":
        queries[0]["vectors"] = [vector(9)]
    elif mutation == "query_events":
        capture["queries"][query_id]["full_query_forward_events"] = []
    elif mutation == "global_sequence":
        forwards[-1]["sequence"] = 999
    elif mutation == "stage_order":
        forwards.reverse()
        for i, row in enumerate(forwards):
            row["sequence"] = i
    elif mutation == "forward_count":
        capture["forward_summary"]["total_model_forward_calls"] = 0
    elif mutation == "encoded_count":
        capture["forward_summary"]["documents"]["encoded_texts"] = 2
    elif mutation == "extra_document":
        capture["documents"]["extra"] = capture["documents"][doc_id]
    elif mutation == "missing_query":
        capture["queries"].clear()
    with pytest.raises((ValueError, KeyError)):
        verify_fixture(fixture)


@pytest.mark.parametrize("mutation", ["missing", "started_only", "failed", "order", "indices", "tokens",
                                      "padded", "shape", "dtype", "hash", "time", "duration", "duplicate", "batch_order"])
def test_forward_proofs_fail_closed(mutation):
    texts = ["longer", "a"]
    ledger = events(texts)
    if mutation == "missing":
        ledger.clear()
    elif mutation == "started_only":
        ledger.pop()
    elif mutation == "failed":
        ledger[1]["status"] = "failed"
    elif mutation == "order":
        ledger.reverse()
    elif mutation == "indices":
        for event in ledger:
            event["input_indices"] = [0, 0]
    elif mutation == "tokens":
        for event in ledger:
            event["token_lengths"][0] = True
    elif mutation == "padded":
        for event in ledger:
            event["padded_tokens"] = 99999
    elif mutation == "shape":
        ledger[1]["output_shape"] = [2, 2]
    elif mutation == "dtype":
        ledger[1]["output_dtype"] = "float64"
    elif mutation == "hash":
        for event in ledger:
            event["input_sha256"][0] = "a" * 64
    elif mutation == "time":
        ledger[1]["elapsed_seconds"] = -1.
    elif mutation == "duration":
        ledger[1]["duration_seconds"] = 100.
    elif mutation == "duplicate":
        ledger += deepcopy(ledger)
    elif mutation == "batch_order":
        ledger = events(texts, [[0, 1]])
    with pytest.raises(ValueError):
        audit.verify_forward_events(ledger, texts, metadata())


def test_short_and_long_actual_batches_are_counted_without_extra_forwards():
    texts = ["a" * 8192, "b" * 100, "c" * 4097]
    ledger = events(texts, [[1], [2], [0]])
    result = audit.verify_forward_events(ledger, texts, metadata())
    assert result == {"api_calls": 1, "model_forward_calls": 3, "encoded_texts": 3}
    assert audit.verify_forward_events([], [], metadata()) == {
        "api_calls": 0, "model_forward_calls": 0, "encoded_texts": 0}


def test_full_query_must_be_a_singleton():
    with pytest.raises(ValueError, match="singleton"):
        audit.verify_forward_events(events(["one", "two"]), ["one", "two"], metadata(), singleton=True)


def case_fixture(*, active=True):
    capture, requests, *_ = capture_fixture()
    request = requests["cases"][0]
    query = next(iter(capture["queries"].values()))["query"] if active else "find the implementation"
    response = {"query": query, "match_status": "candidates", "scope": {"source": "invented"},
                "results": [full_row()], "total_source_chars": len(full_row()["source"]), "budget_exhausted": False,
                "token_usage": {"encoding": "cl100k_base", "budget": 4096, "tokens": 0, "truncated": False}}
    ctx.recount(response, 4096, Counter())
    case = {"case_id": "case", "key": ["invented", "toy", "q", "dense"], "subset": "invented", "dataset": "toy",
            "query_id": "q", "condition": "dense", "retrieval": "dense", "kind": "present", "multi_file": False, "qualified": False,
            "groups": [[{"path": "src/load.py", "evidence_lines": [1, 2]}]], "query": query,
            "raw_rows": [full_row()], "precoalesce_rows": [full_row()], "reference_response": response,
            "legacy_control": deepcopy(response), "definition_keys": {"invented": ["src/load.py", "load", 1, 2]},
            "limits": dict(audit.LIMITS), "feature_request_id": request["query_request_id"] if active else None}
    if not active:
        requests["cases"][0] = {"case_id": "case", "active": False, "reason": "ineligible_query",
                                "query_request_id": None, "window_documents": {}, "window_bindings": {}}
    features = audit.features_for_case(case, capture, requests["cases"][0])
    result, receipt = ctx.reconstruct(response, case["raw_rows"], features, definition_keys=case["definition_keys"],
                                       precoalesce_rows=case["precoalesce_rows"], counter=Counter(), **audit.LIMITS)
    payload = ctx.compact_payload(result)
    outcome = {"case_id": "case", "key": case["key"], "limits": dict(audit.LIMITS),
               **{name: case[name] for name in ("subset", "dataset", "query_id", "condition", "kind", "multi_file", "qualified")},
               "reference_response": response, "ranked_candidates": case["raw_rows"], "precoalesce_rows": case["precoalesce_rows"],
               "definition_keys": case["definition_keys"], "features_sha256": audit.sha(audit.canonical(features)),
               "response": result, "semantic_receipt": receipt,
               "compact_payload": payload, "compact_sha256": audit.sha(payload.encode()), "actual_compact_tokens": len(payload.encode()),
               "coverage_coalesce_calls": 0, "reference_coverage_coalesce_calls": 1, "semantic_calls": 1,
               "feature_calls": int(active), "subsequent_backfill_calls": 0,
               "query_request_id": case["feature_request_id"], "window_documents": requests["cases"][0]["window_documents"],
               "calls": {name: 0 for name in ("model", "retrieval", "index", "network")},
               "evidence_ranks": [1], "covered_groups": 1, "complete": True, "single_file_first_hit": True,
               "comparisons": {name: {"group_gains": [], "group_losses": [], "complete_gain": False,
                                       "complete_loss": False, "single_file_first_hit_loss": False}
                               for name in audit.CONTROLS}}
    return case, outcome, capture, requests


@pytest.mark.parametrize("active", [True, False])
def test_exact_outcome_reconstruction_and_honest_zero_replay_coalesce(active):
    case, outcome, capture, requests = case_fixture(active=active)
    result = audit.audit_outcomes([case], [outcome], capture, requests, counter=Counter(), expected_rows=1, expected_units=1)
    assert not result["qualified"] and result["occurrences"] == result["distinct_dataset_query_units"] == 1


@pytest.mark.parametrize("field", ["features_sha256", "ranked_candidates", "definition_keys", "precoalesce_rows", "reference_response",
                                  "compact_payload", "compact_sha256", "actual_compact_tokens", "coverage_coalesce_calls",
                                  "reference_coverage_coalesce_calls", "subsequent_backfill_calls", "feature_calls", "query_request_id"])
def test_each_operand_payload_and_actual_call_count_is_bound(field):
    case, outcome, capture, requests = case_fixture()
    outcome[field] = None
    with pytest.raises(ValueError):
        audit.audit_outcomes([case], [outcome], capture, requests, counter=Counter(), expected_rows=1, expected_units=1)


def test_source_provenance_cannot_be_added_to_a_captured_window():
    case, _, capture, requests = case_fixture()
    requests["cases"][0]["window_bindings"]["invented"]["source_origin"] = "overlay"
    with pytest.raises(ValueError, match="provenance"):
        audit.features_for_case(case, capture, requests["cases"][0])


def quality_case(number, *, multi=True):
    row = full_row()
    return {"case_id": str(number), "subset": "toy", "dataset": "toy", "query_id": str(number), "condition": "dense",
            "kind": "present", "qualified": False, "multi_file": multi,
            "groups": [[{"path": row["path"], "evidence_lines": [1, 2]}]],
            "legacy_control": {"results": [row]}, "reference_response": {"results": [row]}}


def test_a_new_gain_cannot_hide_an_individual_group_and_complete_loss():
    gain, loss = quality_case(1), quality_case(2)
    gain["legacy_control"] = gain["reference_response"] = {"results": []}
    result = audit.quality_gate([gain, loss], {"1": {"results": [full_row()]}, "2": {"results": []}})
    assert not result["qualified"]
    assert result["additional_distinct_complete_multi_queries_vs_coverage"] == [["toy", "1"]]
    for comparison in result["against_controls"].values():
        assert len(comparison["group_losses"]) == len(comparison["complete_losses"]) == 1


def test_both_controls_and_incomplete_behaviors_must_preserve_each_group():
    case = quality_case(1, multi=False)
    case["groups"].append([{"path": "missing.py", "evidence_lines": [1, 1]}])
    case["reference_response"] = {"results": []}
    result = audit.quality_gate([case], {"1": {"results": []}})
    assert not result["qualified"]
    assert result["against_controls"]["legacy"]["group_losses"][0]["groups"] == [1]
    assert result["against_controls"]["legacy"]["single_file_first_hit_losses"]
    assert not result["against_controls"]["legacy"]["complete_losses"]


def test_repeated_modes_count_as_one_distinct_complete_gain():
    cases = [quality_case(1), quality_case(1)]
    cases[1]["case_id"], cases[1]["condition"] = "two", "hybrid"
    for case in cases:
        case["legacy_control"] = case["reference_response"] = {"results": []}
    result = audit.quality_gate(cases, {"1": {"results": [full_row()]}, "two": {"results": [full_row()]}})
    assert result["qualified"] and result["occurrences"] == 2 and result["distinct_dataset_query_units"] == 1
    assert len(result["additional_distinct_complete_multi_queries_vs_coverage"]) == 1


def test_qualified_groups_require_exact_symbol_and_literal_evidence_hash():
    case = quality_case(1)
    case["qualified"] = True
    target = case["groups"][0][0]
    target.update(symbol="load", evidence_sha256=audit.sha(full_row()["source"].encode()))
    assert audit.quality(case, {"results": [full_row()]})["complete"]
    target["symbol"] = "other"
    assert not audit.quality(case, {"results": [full_row()]})["complete"]


def test_actual_model_byte_binding_detects_changed_file(tmp_path):
    weights = b"invented checkpoint bytes, never loaded"
    digest = audit.sha(weights)
    files = {"model.safetensors": weights, "config.json": json.dumps({"model_type": "qwen3", "hidden_size": 1024}).encode(),
             "tokenizer.json": b"{}", "tokenizer_config.json": b"{}",
             "semantic_search_checkpoint.json": json.dumps({"id": ctx.MODEL_ID, "revision": ctx.MODEL_REVISION,
                 "weights_etag": digest, "weights_dtype": "bfloat16", "quantized": False}).encode(),
             ".cache/huggingface/download/model.safetensors.metadata": (ctx.MODEL_REVISION + "\n" + digest + "\n").encode()}
    for name, raw in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    binding = {"metadata": metadata(), "fingerprint": audit.model_fingerprint(metadata()),
               "checkpoint": {"path": str(tmp_path), "files": {name: {"sha256": audit.sha(raw), "bytes": len(raw)}
                                                                for name, raw in files.items()}},
               "runtime": {"packages": {}}}
    assert audit.verify_model_binding(binding, verify_runtime=False)["actual_checkpoint_verified"]
    (tmp_path / "model.safetensors").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checkpoint bytes"):
        audit.verify_model_binding(binding, verify_runtime=False)


def test_auditor_source_imports_no_producer_or_numerical_runtime():
    source = (SCRIPTS / "audit_semantic_study.py").read_text()
    assert "import semantic_search" not in source and "from semantic_search" not in source
    assert "import semantic_model_study" not in source and "import semantic_study_inputs" not in source
    assert "import numpy" not in source and "import mlx" not in source


def prepared_fixture(tmp_path, monkeypatch):
    """Actual CPU control producer versus the independent old-control auditor."""
    import importlib
    import complementary_inputs as data

    path = Path(__file__).with_name("test_semantic_study_inputs.py")
    spec = importlib.util.spec_from_file_location("invented_control_fixture", path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    modules = {name: importlib.import_module("semantic_search." + name) for name in ("context", "corpus", "selection")}
    monkeypatch.setattr(modules["context"], "encoding", lambda: Counter())
    cases = [fixture.make_case(modules, condition="hybrid"), fixture.make_case(modules, condition="bm25")]
    controls = fixture.controls_for(cases, modules)
    monkeypatch.setattr(data, "load", lambda root: (cases, controls, [], {}))
    values = fixture.study.build(cases, controls, [], {}, modules)
    folder = tmp_path / "prepared"
    folder.mkdir()
    for name, value in values.items():
        (folder / name).write_text(json.dumps(value))
    declarations = {}
    for i, name in enumerate(audit.MAPPING_SOURCES):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        value = {"conditions": ["bm25", "dense", "hybrid"]} if i == 0 else {
            "conditions": [{"id": "hybrid_k24_b1", "retrieval": "hybrid", "shortlist": 24, "batch_size": 1}]}
        path.write_text(json.dumps(value))
        declarations[name] = {mode: mode for mode in value["conditions"]} if i == 0 else value["conditions"][0]
    plan = {"schema": "semantic_study_inputs_v1", "settings": dict(audit.LIMITS), "feature_policy": audit.POLICY,
            "artifact_sha256": {name: audit.file_sha(folder / name) for name in audit.ARTIFACTS},
            "counts": data.counts(cases), "historical_parents": {},
            "retrieval_conditions": {"mapping": audit.RETRIEVALS, "source_declarations": declarations,
                "source_sha256": {name: audit.file_sha(tmp_path / name) for name in audit.MAPPING_SOURCES},
                "case_counts": {"bm25": 1, "hybrid": 1}},
            "barriers": {"exact_control_rows": 4, "referenced_negative_rows": 0, "new_candidate_outcomes_computed": 0,
                         "model_calls": 0, "retrieval_calls": 0, "index_calls": 0, "network_calls": 0}}
    (folder / "frozen-plan.json").write_text(json.dumps(plan))
    return folder


def verify_prepared_fixture(tmp_path, folder):
    return audit.audit_preparation(tmp_path, folder, Counter(), expected_rows=2, expected_units=1,
                                   expected_negatives=0, expected_kinds={"present": 2})


def test_actual_invented_controls_and_inactive_bm25_independently_reconstruct(tmp_path, monkeypatch):
    folder = prepared_fixture(tmp_path, monkeypatch)
    result = verify_prepared_fixture(tmp_path, folder)
    assert result["independent_controls"] == 4
    assert result["feature_requests"]["counts"]["active_cases"] == 1
    assert result["feature_requests"]["cases"][0]["reason"] == "lexical_only"


@pytest.mark.parametrize("artifact", ["operands.json", "feature-requests.json", "control-parity.json",
                                     "control-references.json", "negative-references.json", "input-manifest.json"])
def test_changed_prepared_artifacts_cannot_bypass_independent_controls(tmp_path, monkeypatch, artifact):
    folder = prepared_fixture(tmp_path, monkeypatch)
    value = json.loads((folder / artifact).read_bytes())
    if artifact == "operands.json":
        value["cases"][0]["groups"][0][0]["evidence_lines"] = [1, 1]
    elif artifact == "feature-requests.json":
        value["cases"][0]["active"] = True
    elif artifact == "control-parity.json":
        value["cases"][0]["compact_sha256"] = "0" * 64
    elif artifact == "control-references.json":
        value[0]["controls"]["legacy"]["line_number"] = 999
    elif artifact == "negative-references.json":
        value.append({"invented": "new negative"})
    else:
        value["cases"][0]["raw_pairs_sha256"] = "0" * 64
    (folder / artifact).write_text(json.dumps(value))
    # Updating an outer artifact checksum cannot conceal changed trusted inputs.
    plan = json.loads((folder / "frozen-plan.json").read_bytes())
    plan["artifact_sha256"][artifact] = audit.file_sha(folder / artifact)
    (folder / "frozen-plan.json").write_text(json.dumps(plan))
    with pytest.raises(ValueError):
        verify_prepared_fixture(tmp_path, folder)


def freeze_fixture(tmp_path):
    closure = ("audit_semantic_study", "audit_semantic_context", "audit_complementary_context", "audit_flow_native",
               "audit_flow_allocation", "audit_source_allocation", "audit_windows", "audit_selection", "complementary_inputs",
               "semantic_study_inputs", "semantic_model_study")
    names = ["scripts/" + name + ".py" for name in closure] + ["src/semantic_search/invented.py"]
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# invented source, never executed\n")
    for name in ["model-binding.json", "retrieval-declaration.json", "prepared/operands.json"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n")
    plan = {"source_sha256": {name: audit.file_sha(tmp_path / name) for name in names}, "historical_artifact_sha256": {},
            "model_binding": {"path": "model-binding.json", "sha256": audit.file_sha(tmp_path / "model-binding.json")},
            "retrieval_conditions": {"source_sha256": {"retrieval-declaration.json": audit.file_sha(tmp_path / "retrieval-declaration.json")}},
            "artifact_sha256": {"operands.json": audit.file_sha(tmp_path / "prepared/operands.json")}}
    (tmp_path / "prepared/frozen-plan.json").write_text(json.dumps(plan))
    for arguments in (["init", "-q"], ["config", "user.email", "invented@example.invalid"],
                      ["config", "user.name", "Invented Fixture"], ["add", "."], ["commit", "-qm", "invented freeze"]):
        subprocess.run(["git", "-C", str(tmp_path), *arguments], check=True, capture_output=True)
    commit = audit.git(tmp_path, "rev-parse", "HEAD").decode().strip()
    binding = {"execution_commit": commit, "freeze": {"commit": commit, "bound_blob_count": len(names) + 4,
        "plan_sha256": audit.file_sha(tmp_path / "prepared/frozen-plan.json"), "new_candidate_outcomes_before_freeze": 0}}
    return {"plan": plan}, binding, datetime.now(timezone.utc).isoformat()


def test_frozen_source_input_model_and_exact_commit_ancestry_are_checked(tmp_path):
    prepared, binding, start = freeze_fixture(tmp_path)
    assert audit.verify_freeze(tmp_path, tmp_path / "prepared", prepared, binding, start)["commit"] == binding["freeze"]["commit"]


@pytest.mark.parametrize("name", ["scripts/audit_semantic_study.py", "src/semantic_search/invented.py",
                                  "model-binding.json", "retrieval-declaration.json", "prepared/operands.json", "prepared/frozen-plan.json"])
def test_any_frozen_source_input_or_model_manifest_change_invalidates_attempt(tmp_path, name):
    prepared, binding, start = freeze_fixture(tmp_path)
    (tmp_path / name).write_text("changed after prospective freeze\n")
    with pytest.raises(ValueError):
        audit.verify_freeze(tmp_path, tmp_path / "prepared", prepared, binding, start)


def test_outcome_execution_cannot_predate_the_committed_freeze(tmp_path):
    prepared, binding, _ = freeze_fixture(tmp_path)
    with pytest.raises(ValueError, match="precede"):
        audit.verify_freeze(tmp_path, tmp_path / "prepared", prepared, binding, "2000-01-01T00:00:00+00:00")


@pytest.mark.parametrize("active", [True, False])
def test_feature_digest_is_rebuilt_and_inactive_none_has_literal_null_digest(active):
    case, outcome, capture, requests = case_fixture(active=active)
    features = audit.features_for_case(case, capture, requests["cases"][0])
    assert outcome["features_sha256"] == audit.sha(audit.canonical(features))
    assert "features" not in outcome
    if not active:
        assert outcome["features_sha256"] == audit.sha(b"null")
    # Dictionary insertion order does not change this canonical vector binding.
    if features is not None:
        assert audit.sha(audit.canonical(dict(reversed(list(features.items()))))) == outcome["features_sha256"]
    outcome["features"] = features
    with pytest.raises(ValueError, match="feature hash"):
        audit.audit_outcomes([case], [outcome], capture, requests, counter=Counter(), expected_rows=1, expected_units=1)


def test_lossless_gzip_outcomes_preserve_actual_envelope_and_feature_digest(tmp_path):
    case, outcome, capture, requests = case_fixture()
    raw = (json.dumps(outcome, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode()
    path = tmp_path / "outcomes.jsonl.gz"
    path.write_bytes(gzip.compress(raw, mtime=0))
    restored = audit.jsonl(path)
    assert restored == [outcome]
    result = audit.audit_outcomes([case], restored, capture, requests, counter=Counter(), expected_rows=1, expected_units=1)
    assert result["occurrences"] == 1


def test_truncated_or_changed_gzip_trailer_is_rejected(tmp_path):
    path = tmp_path / "outcomes.jsonl.gz"
    compressed = gzip.compress(b'{"invented":"complete row"}\n', mtime=0)
    path.write_bytes(compressed[:-8])
    with pytest.raises(EOFError):
        audit.jsonl(path)
    path.write_bytes(compressed[:-8] + b"changed!")
    with pytest.raises(gzip.BadGzipFile):
        audit.jsonl(path)


def test_replay_artifact_hash_binds_final_gzip_bytes_and_requires_exact_closure(tmp_path):
    (tmp_path / "outcomes.jsonl.gz").write_bytes(gzip.compress(b"{}\n", mtime=0))
    (tmp_path / "replay-start.json").write_bytes(b"{}\n")
    hashes = {name: audit.file_sha(tmp_path / name) for name in ("outcomes.jsonl.gz", "replay-start.json")}
    audit.verify_replay_artifacts(tmp_path, hashes)
    for changed in ({"outcomes.jsonl": hashes["outcomes.jsonl.gz"], "replay-start.json": hashes["replay-start.json"]},
                    {"outcomes.jsonl.gz": hashes["outcomes.jsonl.gz"]}, {**hashes, "extra.json": "0" * 64}):
        with pytest.raises(ValueError, match="closure"):
            audit.verify_replay_artifacts(tmp_path, changed)
    (tmp_path / "outcomes.jsonl.gz").write_bytes(gzip.compress(b'{"changed":true}\n', mtime=0))
    with pytest.raises(ValueError, match="artifact changed"):
        audit.verify_replay_artifacts(tmp_path, hashes)
