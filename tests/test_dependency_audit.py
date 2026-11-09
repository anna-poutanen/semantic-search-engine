"""Independent auditor contracts using invented sources/outcomes only."""
import ast
from copy import deepcopy
import json
from pathlib import Path

import pytest

from experiments.dependency_context import audit, study_inputs as inputs
from experiments.dependency_context.annotated_dependencies import AnnotatedDependencies
from experiments.dependency_context import study
from semantic_search.corpus import Snippet


class InventedCounter:
    def encode(self, text, disallowed_special=()):
        return [0] * 85


def fixture(tmp_path):
    text = ("class Writer:\n    def finish(self):\n        return 'ok'\n\n"
            "def render(value: Writer):\n    return value.finish()\n\n"
            "def spare():\n    return 'spare'\n")
    path = "module.py"
    lines = inputs.physical_lines(text)
    inventory, keys, values = [], {}, []
    for symbol, low, high in (("Writer.finish", 2, 3), ("render", 5, 6), ("spare", 8, 9)):
        source = "".join(lines[low-1:high])
        identifier = f"{path}:{symbol}:{low}-{high}"
        inventory.append({"id": identifier, "path": path, "symbol": symbol, "start_line": low,
                          "end_line": high, "text": source, "content_hash": inputs.sha(source.encode())})
        keys[identifier] = [path, symbol, low, high]
        values.append({"id": identifier, "path": path, "symbol": symbol, "start_line": low,
                       "end_line": high, "window_end_line": high, "source": source,
                       "source_hash": inputs.sha(source.encode()), "content_hash": inputs.sha(source.encode()),
                       "truncated": False, "origin": "retrieval", "related_to": None, "retrieval_score": 0.4,
                       "h2o_score": None, "qwen_score": None})
    corpus = tmp_path / "corpus"
    (corpus / "toy").mkdir(parents=True)
    (corpus / "toy/module.py").write_bytes(text.encode())
    spec = {"source_sha256": {path: inputs.sha(text.encode())}, "inventory": inventory,
            "definition_keys": keys, "graph_edges": []}
    bundles = inputs.build_bundles({"toy": spec}, corpus)
    bundle = next(iter(bundles.values()))

    def response(rows):
        return {"query": "finish rendering", "scope": {"include": ["module.py"], "exclude": []},
                "match_status": "candidates", "results": deepcopy(rows),
                "total_source_chars": sum(len(row["source"]) for row in rows), "budget_exhausted": False,
                "token_usage": {"encoding": "cl100k_base", "tokens": 85, "budget": 4096, "truncated": False}}

    callee, caller, spare = values
    reference, proposal = response([caller, callee]), response([caller, spare])
    graph = AnnotatedDependencies(bundle["sources"], [Snippet(**row) for row in inventory], definition_keys=keys).witnesses()
    key = ["invented", "toy", "toy_render", "hybrid"]
    record = {"id": inputs.opaque_id("historical_role", key), "dataset": "toy", "query_id": "toy_render",
              "mode": "hybrid", "repetition": 1, "quality": True, "reference": reference, "proposal": proposal,
              "legacy": deepcopy(reference), "definition_keys": keys, "raw_rows": values, "precoalesce_rows": values,
              "limits": deepcopy(inputs.LIMITS), "source_bundle_id": bundle["id"],
              "scoring": {"kind": "present", "multi_file": True, "qualified": True,
                          "groups": [[{"path": path, "symbol": "render", "evidence_lines": [6, 6],
                                       "evidence_sha256": inputs.sha(lines[5].encode())}],
                                     [{"path": path, "symbol": "Writer.finish", "evidence_lines": [3, 3],
                                       "evidence_sha256": inputs.sha(lines[2].encode())}]]}}
    operand = {name: deepcopy(record[name]) for name in inputs.OPERAND_FIELDS}
    prepared = {"records": [record], "operands": [operand], "source_bundles": bundles}
    return prepared, record, operand, bundle, graph


def real_invented_replay(tmp_path):
    prepared, record, operand, bundle, graph = fixture(tmp_path)
    output = tmp_path / "replay"
    summary = study.replay(prepared, output, binding={"invented": True}, counter=InventedCounter())
    row = list(inputs.rows(output / "outcomes.jsonl.gz"))[0][1]
    return prepared, record, operand, bundle, graph, row, summary


def test_independent_graph_ast_and_literal_proof_accepts_real_invented_resolver(tmp_path):
    _, _, _, bundle, graph = fixture(tmp_path)
    assert len(graph["accepted"]) == 1
    proof = audit.verify_graph_receipt(bundle, graph)
    assert proof["accepted_edges"] == 1
    assert proof["independent_accepted_ast_and_literal_witnesses"] is True


@pytest.mark.parametrize("field", ["call", "annotation", "resolution", "endpoint", "definition", "source_hash"])
def test_graph_or_witness_tampering_is_rejected(tmp_path, field):
    _, _, _, bundle, graph = fixture(tmp_path)
    fact = graph["accepted"][0]
    if field in ("call", "annotation"):
        fact[field]["start_byte"] += 1
    elif field == "resolution":
        fact["resolution"][0]["source"] = "class Other:"
    elif field == "endpoint":
        fact["callee"]["symbol"] = "Other.finish"
    elif field == "definition":
        fact["callee"]["definition_key"][2] += 1
    else:
        graph["source_sha256"]["module.py"] = "0" * 64
    with pytest.raises(ValueError):
        audit.verify_graph_receipt(bundle, graph)


def test_witness_byte_boundaries_and_physical_lines_are_independent(tmp_path):
    _, _, _, bundle, graph = fixture(tmp_path)
    witness = deepcopy(graph["accepted"][0]["call"])
    audit.verify_witness(witness, bundle)
    witness["end_line"] -= 1
    with pytest.raises(ValueError):
        audit.verify_witness(witness, bundle)


def test_independent_whole_operand_decision_restores_both_exact_endpoints(tmp_path):
    _, _, operand, _, graph = fixture(tmp_path)
    response, receipt = audit.reconstruct_guard(operand, graph)
    assert response == operand["reference"]
    assert receipt["accepted"] is False
    assert receipt["protected_ids"] == [row["id"] for row in operand["reference"]["results"]]
    assert receipt["violated_ids"] == [operand["reference"]["results"][1]["id"]]
    assert receipt["calls"] == audit.NO_WORK


def test_changed_scalar_on_retained_endpoint_forces_exact_fallback(tmp_path):
    _, _, operand, _, graph = fixture(tmp_path)
    operand["proposal"] = deepcopy(operand["reference"])
    operand["proposal"]["results"][0]["retrieval_score"] = 0.5
    response, receipt = audit.reconstruct_guard(operand, graph)
    assert response == operand["reference"]
    assert receipt["violated_ids"] == [operand["reference"]["results"][0]["id"]]


def test_unrelated_saved_proposal_is_accepted_exactly(tmp_path):
    _, _, operand, _, graph = fixture(tmp_path)
    graph["accepted"] = []
    response, receipt = audit.reconstruct_guard(operand, graph)
    assert response == operand["proposal"]
    assert receipt["accepted"] is True


def test_actual_producer_roundtrip_independent_decision_scores_summary_interoperate(tmp_path):
    prepared, record, operand, bundle, graph, row, summary = real_invented_replay(tmp_path)
    assert audit.verify_record(record, operand, bundle, graph, row, InventedCounter()) == row
    recomputed = audit.audit_outcomes(prepared, [row], {bundle["id"]: graph}, InventedCounter())
    assert all(inputs.same(summary[name], value) for name, value in recomputed.items())
    assert recomputed["quality_gate"]["qualified"] is True
    assert recomputed["quality_gate"]["distinct_complete_multi_gains_vs_prior_role"] == [["toy", "toy_render"]]


@pytest.mark.parametrize("change", ["response", "score", "provenance", "decision", "calls", "protected", "graphhash",
                                   "certificate", "quality", "identity", "payload", "token_count", "cap", "extra",
                                   "scoring", "comparison", "typed", "nested_order"])
def test_independent_record_rejects_material_tampering(tmp_path, change):
    _, record, operand, bundle, graph, row, _ = real_invented_replay(tmp_path)
    if change == "response":
        row["response"] = deepcopy(operand["proposal"])
    elif change == "score":
        row["response"]["results"][0]["retrieval_score"] = 0.75
    elif change == "provenance":
        row["response"]["results"][0]["source_origin"] = "saved"
    elif change == "decision":
        row["guard_receipt"]["accepted"] = True
    elif change == "calls":
        row["guard_receipt"]["calls"]["model"] = 1
    elif change == "protected":
        row["guard_receipt"]["protected_ids"] = []
    elif change == "graphhash":
        row["graph_receipt_sha256"] = "0" * 64
    elif change == "certificate":
        row["skip_certificate"]["eligible"] = True
    elif change == "quality":
        row["quality"] = False
    elif change == "identity":
        row["id"] = "record_" + "0" * 64
    elif change == "payload":
        row["compact_payload"] += " "
    elif change == "token_count":
        row["actual_compact_tokens"] = 84
    elif change == "cap":
        row["response"]["token_usage"]["tokens"] = 4097
    elif change == "extra":
        row["unknown"] = "unbound"
    elif change == "scoring":
        row["scores"]["candidate"]["complete"] = False
    elif change == "comparison":
        row["comparisons"]["prior_role"]["group_gains"] = []
    elif change == "typed":
        row["actual_compact_tokens"] = 85.0
    else:
        row["response"]["scope"] = {"exclude": [], "include": ["module.py"]}
    with pytest.raises(ValueError):
        audit.verify_record(record, operand, bundle, graph, row, InventedCounter())


def test_caps_and_tokenizer_checked_on_actual_saved_payload(tmp_path):
    _, record, operand, bundle, graph, row, _ = real_invented_replay(tmp_path)

    class WrongCounter:
        def encode(self, text, disallowed_special=()):
            return [0] * 4097

    with pytest.raises(ValueError, match="token count/cap"):
        audit.verify_record(record, operand, bundle, graph, row, WrongCounter())


@pytest.mark.parametrize("change", ["missing_record", "duplicate_record", "missing_graph", "missing_operand"])
def test_complete_matrix_membership_is_required(tmp_path, change):
    prepared, _, _, bundle, graph, row, _ = real_invented_replay(tmp_path)
    outcomes, graphs = [row], {bundle["id"]: graph}
    if change == "missing_record":
        outcomes = []
    elif change == "duplicate_record":
        outcomes.append(deepcopy(row))
    elif change == "missing_graph":
        graphs = {}
    else:
        prepared["operands"] = []
    with pytest.raises(ValueError):
        audit.audit_outcomes(prepared, outcomes, graphs, InventedCounter())


def certificate_operand():
    rows, keys = [], {}
    for index in range(8):
        source = f"def f{index}():\n    return {index}\n"
        identifier = f"toy.py:f{index}:{index*3+1}-{index*3+2}"
        row = {"id": identifier, "path": "toy.py", "symbol": f"f{index}", "start_line": index*3+1,
               "end_line": index*3+2, "window_end_line": index*3+2, "source": source,
               "source_hash": inputs.sha(source.encode()), "content_hash": inputs.sha(source.encode()), "truncated": False}
        rows.append(row)
        keys[identifier] = [row[name] for name in ("path", "symbol", "start_line", "end_line")]
    reference = {"results": rows}
    return {"reference": reference, "proposal": reference, "definition_keys": keys,
            "raw_rows": rows, "precoalesce_rows": [], "limits": deepcopy(inputs.LIMITS)}


def test_independent_skip_union_protects_full_context_and_declines_unprotected_victim():
    operand = certificate_operand()
    graph = {"accepted": []}
    def role(row, **_):
        return {"reasons": ["explicit_write"] if row["symbol"] in ("f5", "f6", "f7") else []}
    receipt = audit.reconstruct_certificate(operand, graph, role_classifier=role)
    assert receipt["eligible"] is True
    assert receipt["semantic_protected_ids"] == [row["id"] for row in operand["reference"]["results"][:5]]
    assert receipt["role_protected_ids"] == [row["id"] for row in operand["reference"]["results"][5:]]
    empty = audit.reconstruct_certificate(operand, graph, role_classifier=lambda *_, **__: {"reasons": []})
    assert empty["eligible"] is False
    assert len(empty["unprotected_ids"]) == 3


def test_independent_skip_declines_free_slot_before_role_classification():
    operand = certificate_operand()
    operand["reference"]["results"].pop()

    def forbidden(*_, **__):
        raise AssertionError("Unneeded classifier work")

    receipt = audit.reconstruct_certificate(operand, {"accepted": []}, role_classifier=forbidden)
    assert receipt["eligible"] is False
    assert receipt["reason"] == "context_not_full"


def test_full_context_skip_matches_frozen_independent_role_classifier():
    operand = certificate_operand()
    receipt = audit.reconstruct_certificate(operand, {"accepted": []})
    assert receipt["eligible"] is False
    assert receipt["role_protected_ids"] == []


def test_qualified_group_requires_symbol_exact_evidence_bytes_and_physical_span(tmp_path):
    _, record, operand, _, _ = fixture(tmp_path)
    scoring = record["scoring"]
    assert audit.score(scoring, operand["reference"])["complete"] is True
    response = deepcopy(operand["reference"])
    response["results"][1]["symbol"] = "other"
    assert audit.score(scoring, response)["evidence_ranks"] == [1, None]
    response["results"][1]["symbol"] = "Writer.finish"
    response["results"][1]["source"] = "    def finish(self):\n        return 'changed'\n"
    assert audit.score(scoring, response)["evidence_ranks"] == [1, None]


def test_individual_loss_cannot_hide_behind_equal_aggregate_or_new_gain(tmp_path):
    _, _, _, _, _, row, _ = real_invented_replay(tmp_path)
    loss = deepcopy(row)
    loss["id"] = "other"
    loss["query_id"] = "other"
    loss["comparisons"]["coverage"]["group_losses"] = [2]
    result = audit.aggregate([row, loss])
    assert result["quality_gate"]["qualified"] is False
    assert result["quality_gate"]["individual_losses"]["coverage"][0]["query_id"] == "other"


def test_repetitions_are_integrity_only_and_gains_count_distinct_units(tmp_path):
    _, _, _, _, _, row, _ = real_invented_replay(tmp_path)
    duplicate = deepcopy(row)
    duplicate["id"] = "other_mode"
    duplicate["mode"] = "dense"
    repeat = deepcopy(row)
    repeat["id"] = "repeat2"
    repeat["repetition"] = 2
    repeat["quality"] = False
    result = audit.aggregate([row, duplicate, repeat])
    assert result["decisions"] == 3
    assert result["quality_occurrences"] == 2
    assert result["distinct_units"] == 1
    assert result["quality_gate"]["distinct_complete_multi_gains_vs_prior_role"] == [["toy", "toy_render"]]


def test_old_coverage_gain_without_incremental_prior_role_gain_cannot_qualify(tmp_path):
    _, _, _, _, _, row, _ = real_invented_replay(tmp_path)
    row["comparisons"]["prior_role"]["complete_gain"] = False
    assert audit.aggregate([row])["quality_gate"]["qualified"] is False


@pytest.mark.parametrize("change", [None, "missing", "quality", "response", "receipt", "raw", "payload"])
def test_native_repetitions_prove_decision_and_source_stability(tmp_path, change):
    _, record, _, _, _, row, _ = real_invented_replay(tmp_path)
    records, outcomes = {}, {}
    for repeat in (1, 2, 3):
        identifier = "invented_repeat_" + str(repeat)
        value = {**deepcopy(record), "id": identifier, "repetition": repeat, "quality": repeat == 1,
                 "original_identity": {"origin": "attrs_native_observed"}}
        records[identifier] = value
        outcomes[identifier] = {**deepcopy(row), "id": identifier, "repetition": repeat, "quality": repeat == 1}
    identifier = "invented_repeat_3"
    if change == "missing":
        del records[identifier]
    elif change == "quality":
        records[identifier]["quality"] = True
    elif change == "response":
        outcomes[identifier]["response"]["results"][0]["retrieval_score"] = 0.8
    elif change == "receipt":
        outcomes[identifier]["guard_receipt"]["pairs"] = []
    elif change == "raw":
        records[identifier]["raw_rows"][0]["retrieval_score"] = 0.8
    elif change == "payload":
        outcomes[identifier]["compact_payload"] += " "
    if change is None:
        audit.verify_repetitions(records, outcomes)
    else:
        with pytest.raises(ValueError, match="repetit|repeated"):
            audit.verify_repetitions(records, outcomes)


def test_auditor_source_never_imports_guard_skip_study_or_quality_producer():
    tree = ast.parse(Path(audit.__file__).read_text())
    names = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not {"experiments.dependency_context.guard", "experiments.dependency_context.skip",
                "experiments.dependency_context.study", "scripts.semantic_model_study"} & names
    assert "apply_dependency_guard" not in Path(audit.__file__).read_text()


def test_freeze_source_mutation_fails_before_git_or_outcome_reads(tmp_path, monkeypatch):
    folder = tmp_path / "prepared"
    folder.mkdir()
    freeze = {"schema": audit.SCHEMA, "new_guard_outcomes": 0, "native_calls": 0, "default_change": False,
              "sources": {"source.py": "0" * 64}, "runtime": {"invented": True}}
    (tmp_path / "study-freeze.json").write_text(json.dumps(freeze))
    monkeypatch.setattr(inputs, "source_hashes", lambda _: {"source.py": "1" * 64})
    monkeypatch.setattr(audit, "runtime", lambda _: {"invented": True})
    with pytest.raises(ValueError, match="source/runtime changed"):
        audit.verify_freeze(folder, "0" * 40, root=tmp_path)
