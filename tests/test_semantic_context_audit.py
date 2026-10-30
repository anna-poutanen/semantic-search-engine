"""Invented, model-free checks of the independent final-context auditor."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import struct

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("independent_semantic_context_audit",
    ROOT / "scripts/audit_semantic_context.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def f32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def row(identifier, source="return value\n", *, path=None, start=1, **extras):
    end = start + len(audit.physical_lines(source)) - 1
    return {"id": identifier, "path": path or f"src/{identifier}.py", "symbol": identifier,
            "start_line": start, "end_line": end, "window_end_line": end,
            "source": source, "source_hash": audit.digest(source), "content_hash": audit.digest(source),
            "truncated": False, "origin": "retrieval", "related_to": None,
            "retrieval_score": 0.125, "h2o_score": None, "qwen_score": None, **extras}


def test_independent_exact_ppm_cosine():
    assert audit.cosine_ppm([1.0, 0.0], [1.0, 0.0]) == 1_000_000
    assert audit.cosine_ppm([1.0, 0.0], [0.0, 1.0]) == 0
    assert audit.cosine_ppm([1.0, 0.0], [-1.0, 0.0]) == -1_000_000
    assert audit.cosine_ppm([1.0, 1.0], [1.0, 0.0]) == 707_107
    assert audit.cosine_ppm([f32(0.1), f32(0.3)], [f32(0.9), f32(0.7)]) == 832_050


@pytest.mark.parametrize("left,right", [
    ([], [1.0]), ([0.0], [1.0]), ([1.0], [0.0]), ([1.0], [1.0, 2.0]),
    ([True], [1.0]), ([1], [1.0]), ([float("nan")], [1.0]),
    ([float("inf")], [1.0]), ([0.1], [1.0]), ([1e100], [1.0]),
])
def test_unbound_nonfinite_nonfloat32_or_inconsistent_vector_rejected(left, right):
    with pytest.raises(ValueError):
        audit.cosine_ppm(left, right)


def test_typed_equality_rejects_truthy_numeric_substitution():
    assert audit.same({"a": [1, None, 0.0, True]}, {"a": [1, None, 0.0, True]})
    assert not audit.same({"a": [1]}, {"a": [True]})
    assert not audit.same({"a": [1]}, {"a": [1.0]})
    assert not audit.same({"a": None}, {})


def test_literal_and_covered_physical_spans_keep_cr_and_unicode_bytes():
    outer = row("outer", 'a\r\nb = "é🦉\u2028inside"\rc\n')
    inner = row("inner", audit.physical_lines(outer["source"])[1], start=2, path=outer["path"])
    assert audit.covers(outer, inner)
    changed = deepcopy(inner)
    changed["source"] = changed["source"].replace("\r", "\n")
    changed["source_hash"] = audit.digest(changed["source"])
    assert not audit.covers(outer, changed)


def test_hidden_full_window_bounds_cannot_cover_delivered_tail():
    outer = row("outer", "one\ntwo\n", window_end_line=99, truncated=True)
    inner = row("inner", "three\n", start=3, path=outer["path"])
    assert not audit.covers(outer, inner)


@pytest.mark.parametrize("field,value", [
    ("buffer_version", 3), ("buffer_hash", "buffer"), ("source_origin", "open_buffer"),
])
def test_same_literal_bytes_do_not_erase_overlay_provenance(field, value):
    outer = row("outer", **{field: value})
    inner = row("inner", path=outer["path"])
    assert not audit.covers(outer, inner)
    inner[field] = value
    assert audit.covers(outer, inner)


@pytest.mark.parametrize("field", audit.SCORE_FIELDS)
def test_same_id_must_preserve_original_scalar_key_presence_type_and_value(field):
    original = row("same")
    changed = deepcopy(original)
    changed[field] = 1.0
    assert not audit.covers(changed, original)
    changed = deepcopy(original)
    del changed[field]
    assert not audit.covers(changed, original)


def test_dependency_requires_original_source_owner_identifier():
    original = row("dep", origin="dependency", related_to="original-window")
    assert audit.covers(original, original)
    changed = deepcopy(original)
    changed["related_to"] = "containing-delivered-window"
    assert not audit.covers(changed, original)
    changed = row("larger", path=original["path"])
    assert not audit.covers(changed, original)


@pytest.mark.parametrize("change", ["hash", "bounds", "empty", "boolean_bound"])
def test_malformed_source_rejected(change):
    value = row("one")
    if change == "hash":
        value["source_hash"] = "forged"
    elif change == "bounds":
        value["end_line"] += 1
    elif change == "empty":
        value["source"] = ""
    else:
        value["start_line"] = True
    with pytest.raises(ValueError):
        audit.literal(value)


QUERY = "load alpha payload and render beta template"


class Counter:
    """Deterministic invented token boundary; no tokenizer/model work."""
    def encode(self, text, **kwargs):
        return list(range((len(text.encode("utf-8")) + 7) // 8))


def vector(*parts):
    return [*[f32(value) for value in parts], *([0.0] * (audit.DIMENSIONS-len(parts)))]


def invented(*, query=QUERY, candidate_count=2, source="return value\n"):
    raw = [row(str(index), source) for index in range(1, 9 + candidate_count)]
    reference = {"query": query, "match_status": "candidates", "results": deepcopy(raw[:8]),
                 "budget_exhausted": False,
                 "token_usage": {"encoding": "cl100k_base", "tokens": 0, "budget": 4096, "truncated": False}}
    audit.recount(reference, 4096, Counter())
    metadata = {"id": audit.MODEL_ID, "revision": audit.MODEL_REVISION,
                "dimensions": audit.DIMENSIONS, "query_prompt": audit.QUERY_PROMPT,
                "normalization": "L2, float32"}
    features = {"schema": audit.FEATURE_SCHEMA, "query": query,
                "facets": [query, *audit.literal_clauses(query)],
                "query_prompt": audit.QUERY_PROMPT, "model_metadata": metadata,
                "model_fingerprint": __import__("hashlib").sha256(
                    __import__("json").dumps(metadata, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "facet_vectors": [vector(1.0, 0.0, 0.0), vector(0.0, 1.0, 0.0), vector(0.0, 0.0, 1.0)],
                "windows": {}}
    for item in raw:
        index = int(item["id"])
        embedding = vector(1.0, float(index == 9), float(index == 10))
        features["windows"][item["id"]] = {"binding": audit.row_binding(item), "vector": embedding}
    keys = {item["id"]: [item["path"], item["symbol"], item["start_line"], item["end_line"]] for item in raw}
    return reference, raw, features, keys


def captured(reference, raw, features, keys, *, source_budget=12000, token_budget=4096,
             precoalesce_rows=()):
    limits = {"max_results": 8, "source_budget": source_budget, "token_budget": token_budget}
    final, receipt = audit.reconstruct(reference, raw, features, definition_keys=keys, counter=Counter(),
                                       precoalesce_rows=precoalesce_rows, **limits)
    payload = audit.compact_payload(final)
    return {"reference_response": reference, "ranked_candidates": raw, "features": features,
            "definition_keys": keys, "precoalesce_rows": list(precoalesce_rows),
            "limits": limits, "response": final, "semantic_receipt": receipt,
            "compact_payload": payload, "compact_sha256": audit.digest(payload),
            "actual_compact_tokens": len(Counter().encode(payload)),
            "coverage_coalesce_calls": 1, "semantic_calls": 1,
            "feature_calls": int(bool(audit.literal_clauses(reference["query"]))), "subsequent_backfill_calls": 0}


def test_reconstruction_exchanges_actual_final_slots_with_no_second_backfill():
    reference, raw, features, keys = invented()
    record = captured(reference, raw, features, keys)
    assert audit.verify_record(record, Counter()) == record["response"]
    assert [item["id"] for item in record["response"]["results"]] == ["1", "2", "3", "4", "5", "6", "10", "9"]
    receipt = record["semantic_receipt"]
    assert receipt["accepted"] and len(receipt["exchanges"]) == 2
    assert [(item["candidate_id"], item["victim_id"]) for item in receipt["exchanges"]] == [("9", "8"), ("10", "7")]
    assert receipt["reference_support"] == [1_000_000, 0, 0]
    assert receipt["final_support"] == [1_000_000, 707_107, 707_107]
    assert receipt["subsequent_backfill_calls"] == 0
    assert record["response"]["results"][7] == raw[8]
    assert record["response"]["results"][6] == raw[9]


def test_inactive_request_is_exact_defensive_baseline_without_features():
    reference, raw, _, keys = invented(query="find one implementation")
    record = captured(reference, raw, None, keys)
    assert record["response"] == reference and record["response"] is not reference
    assert not record["semantic_receipt"]["activated"]
    assert record["semantic_receipt"]["fallback_reason"] == "no_literal_clauses"
    assert record["feature_calls"] == 0
    assert audit.verify_record(record, Counter()) == reference


@pytest.mark.parametrize("mutation", ["query", "facet", "prompt", "model", "fingerprint", "normalization",
    "missing_window", "extra_window", "source_binding", "document_binding", "provenance", "dimension",
    "component", "zero", "typed_component", "cached_scores"])
def test_invalid_semantic_capture_fails_closed(mutation):
    reference, raw, features, keys = invented()
    window = features["windows"]["9"]
    if mutation == "query":
        features["query"] += " extra"
    elif mutation == "facet":
        features["facets"][1] += " extra"
    elif mutation == "prompt":
        features["query_prompt"] += " "
    elif mutation == "model":
        features["model_metadata"]["revision"] = "unbound"
    elif mutation == "fingerprint":
        features["model_fingerprint"] = "unbound"
    elif mutation == "normalization":
        features["model_metadata"]["normalization"] = "unbound"
    elif mutation == "missing_window":
        del features["windows"]["9"]
    elif mutation == "extra_window":
        features["windows"]["51"] = deepcopy(window)
    elif mutation == "source_binding":
        window["binding"]["source_hash"] = "forged"
    elif mutation == "document_binding":
        window["binding"]["document_sha256"] = "forged"
    elif mutation == "provenance":
        window["binding"]["buffer_version"] = 1
    elif mutation == "dimension":
        window["vector"].pop()
    elif mutation == "component":
        window["vector"][0] = float("nan")
    elif mutation == "zero":
        window["vector"] = vector(0.0)
    elif mutation == "typed_component":
        window["vector"][0] = 1
    else:
        window["scores"] = [1_000_000] * 3
    record = captured(reference, raw, features, keys)
    assert record["response"] == reference
    assert record["semantic_receipt"]["fallback_reason"] == "invalid_features"
    assert audit.verify_record(record, Counter()) == reference


def test_clipped_reference_cannot_claim_semantic_support_from_full_raw_vector():
    reference, raw, features, keys = invented()
    raw[7] = row("8", "one\ntwo\n")
    reference["results"][7] = row("8", "one\n", window_end_line=2,
                                   content_hash=audit.digest(raw[7]["source"]), truncated=True)
    features["windows"]["8"] = {"binding": audit.row_binding(raw[7]), "vector": vector(0.0, 0.0, 1.0)}
    keys["8"][-1] = 2
    record = captured(reference, raw, features, keys)
    assert record["semantic_receipt"]["reference_support"] == [1_000_000, 0, 0]
    assert "clipped" in record["semantic_receipt"]["protected_rows"]["8"]
    assert record["response"]["results"][7] == reference["results"][7]
    assert record["semantic_receipt"]["accepted"]


@pytest.mark.parametrize("change", ["source", "content_hash", "full_end", "provenance", "symbol"])
def test_clipped_reference_requires_exact_identity_and_literal_raw_prefix(change):
    reference, raw, features, keys = invented()
    raw[7] = row("8", "one\ntwo\n")
    reference["results"][7] = row("8", "one\n", window_end_line=2,
                                   content_hash=audit.digest(raw[7]["source"]), truncated=True)
    features["windows"]["8"] = {"binding": audit.row_binding(raw[7]), "vector": vector(1.0)}
    old = reference["results"][7]
    if change == "source":
        old["source"] = "bad\n"
        old["source_hash"] = audit.digest(old["source"])
    elif change == "full_end":
        old["window_end_line"] += 1
    elif change == "provenance":
        old["buffer_version"] = 42
    else:
        old[change] = "changed"
    record = captured(reference, raw, features, keys)
    assert record["response"] == reference
    assert record["semantic_receipt"]["fallback_reason"] == "invalid_features"


@pytest.mark.parametrize("name", ["coverage_coalesce_calls", "semantic_calls", "feature_calls", "subsequent_backfill_calls"])
def test_omitted_or_fabricated_actual_call_counts_rejected(name):
    record = captured(*invented())
    record[name] += 1
    with pytest.raises(ValueError, match="call|backfill"):
        audit.verify_record(record, Counter())
    del record[name]
    if name != "subsequent_backfill_calls":
        with pytest.raises(ValueError):
            audit.verify_record(record, Counter())


@pytest.mark.parametrize("mutation", ["source", "scalar", "owner", "receipt", "tokens", "compact", "hash", "typed_acceptance"])
def test_false_final_payload_and_audit_receipts_rejected(mutation):
    record = captured(*invented())
    if mutation == "source":
        record["response"]["results"][0]["source"] += "forged\n"
    elif mutation == "scalar":
        record["response"]["results"][7]["retrieval_score"] = True
    elif mutation == "owner":
        record["response"]["results"][7]["related_to"] = "fabricated-owner"
    elif mutation == "receipt":
        record["semantic_receipt"]["exchanges"][0]["victim_id"] = "6"
    elif mutation == "tokens":
        record["actual_compact_tokens"] += 1
    elif mutation == "compact":
        record["compact_payload"] += " "
    elif mutation == "hash":
        record["compact_sha256"] = "forged"
    else:
        record["semantic_receipt"]["accepted"] = 1
    with pytest.raises(ValueError):
        audit.verify_record(record, Counter())


def test_backend_may_refresh_only_top_level_operational_fields_after_source_capture():
    record = captured(*invented())
    record["reference_response"]["timings"] = {"total_s": 0.1, "packaging_s": 0.01}
    record["reference_response"]["indexstats"] = {"elapsed_s": 0.05}
    record["response"]["timings"] = {"total_s": 0.2, "packaging_s": 0.1}
    record["response"]["indexstats"] = {"elapsed_s": 0.05, "status": "cached"}
    audited = audit.verify_record(record, Counter())
    assert audited == record["response"] and audited is not record["response"]
    assert audited["timings"]["total_s"] == 0.2
    # Operational maps may be absent from an earlier immutable historical
    # snapshot or added by the live backend. No source field is exempted.
    del record["response"]["indexstats"]
    assert audit.verify_record(record, Counter()) == record["response"]


@pytest.mark.parametrize("mutation", ["query", "scope", "match_evidence", "row_timings", "source_total", "raw_score"])
def test_operational_exemption_does_not_hide_other_top_level_or_nested_changes(mutation):
    record = captured(*invented())
    record["response"]["timings"] = {"total_s": 999.0}
    if mutation == "query":
        record["response"]["query"] += " rewritten"
    elif mutation == "scope":
        record["response"]["scope"] = {"source_root": "different"}
    elif mutation == "match_evidence":
        record["response"]["match_evidence"] = {"top_cosine": 1.0}
    elif mutation == "row_timings":
        record["response"]["results"][0]["timings"] = {"forged": True}
    elif mutation == "source_total":
        record["response"]["total_source_chars"] += 1
    else:
        record["response"]["results"][7]["retrieval_score"] += 0.001
    with pytest.raises(ValueError, match="Actual final response"):
        audit.verify_record(record, Counter())


def test_trusted_operands_cannot_be_rewritten_with_self_consistent_capture():
    reference, raw, features, keys = invented()
    record = captured(reference, raw, features, keys)
    trusted = deepcopy(raw)
    record["ranked_candidates"][9]["retrieval_score"] = 0.999
    changed = captured(record["reference_response"], record["ranked_candidates"], features, keys)
    with pytest.raises(ValueError, match="trusted input"):
        audit.verify_record(changed, Counter(), raw_rows=trusted)


def test_no_whole_window_fit_uses_bounded_trials_and_preserves_original_flags():
    reference, raw, features, keys = invented()
    for item in raw[8:]:
        item["source"] = "x" * 5000 + "\n"
        item["source_hash"] = item["content_hash"] = audit.digest(item["source"])
        features["windows"][item["id"]]["binding"] = audit.row_binding(item)
    reference["budget_exhausted"] = True
    record = captured(reference, raw, features, keys, source_budget=200)
    assert record["response"] == reference
    assert len(record["semantic_receipt"]["trials"]) == 6
    assert all(item["reason"] == "budget_exceeded" for item in record["semantic_receipt"]["trials"])
    assert record["semantic_receipt"]["termination"] == "no_feasible_positive_exchange"


def test_actual_coverage_backfilled_row_participates_in_semantic_floor():
    reference, raw, features, keys = invented(candidate_count=1)
    features["windows"]["8"]["vector"] = vector(1.0, 0.0, 1.0)
    features["windows"]["9"]["vector"] = vector(1.0, 1.0, 0.0)
    reference["_selection_backfill"] = {"appended_ids": ["8"], "coalesced_ids": []}
    record = captured(reference, raw, features, keys)
    assert record["semantic_receipt"]["exchanges"][0]["victim_id"] == "7"
    assert record["response"]["results"][7] == reference["results"][7]
    assert "_selection_backfill" not in record["response"]
    assert record["semantic_receipt"]["reference_support"][2] == 707_107


def test_no_candidate_surviving_eight_full_budget_trials_stops_without_clipping():
    reference, raw, features, keys = invented(candidate_count=3)
    features["windows"]["11"]["vector"] = vector(1.0, 0.5, 0.5)
    for item in raw[8:]:
        item["source"] = "x" * 5000 + "\n"
        item["source_hash"] = item["content_hash"] = audit.digest(item["source"])
        features["windows"][item["id"]]["binding"] = audit.row_binding(item)
    record = captured(reference, raw, features, keys, source_budget=200)
    assert len(record["semantic_receipt"]["trials"]) == 8
    assert record["semantic_receipt"]["termination"] == "budget_trial_limit"
    assert record["response"] == reference
    assert all(item["source_chars"] > 200 for item in record["semantic_receipt"]["trials"])


def test_precoalesced_dependency_preserves_its_actual_containing_final_row_and_source_owner():
    reference, raw, features, keys = invented()
    # This dependency was physically contained and removed during ordinary
    # coalescing. Its owner source is contained in a different delivered row.
    raw[5] = row("6", "owner-prefix\nowner-body\n", path="src/owner.py")
    raw[6] = row("7", "dependency-prefix\ndependency-body\n", path="src/dep.py")
    reference["results"][5:7] = deepcopy(raw[5:7])
    owner = row("owner-original", "owner-body\n", path="src/owner.py", start=2)
    dependency = row("dependency-original", "dependency-body\n", path="src/dep.py", start=2,
                     origin="dependency", related_to=owner["id"])
    pre = [*deepcopy(reference["results"][:5]), owner, dependency]
    reference["_selection_backfill"] = {"coalesced_ids": [dependency["id"], owner["id"]], "appended_ids": ["8"]}
    for item in raw[5:7]:
        features["windows"][item["id"]]["binding"] = audit.row_binding(item)
    record = captured(reference, raw, features, keys, precoalesce_rows=pre)
    assert record["semantic_receipt"]["protected_rows"]["6"] == ["protected_source_overlap"]
    assert record["semantic_receipt"]["protected_rows"]["7"] == ["protected_source_overlap"]
    assert record["response"]["results"][5:7] == reference["results"][5:7]
    assert record["semantic_receipt"]["exchanges"][0]["victim_id"] == "8"
    assert audit.verify_record(record, Counter(), precoalesce_rows=pre) == record["response"]


@pytest.mark.parametrize("mutation", ["missing", "source", "provenance", "duplicate", "cap"])
def test_invalid_precoalesced_source_fails_before_feature_validation(mutation):
    reference, raw, features, keys = invented()
    pre = deepcopy(reference["results"])
    if mutation == "missing":
        pre[-1]["path"] = "src/missing.py"
    elif mutation == "source":
        pre[-1]["source"] = "changed bytes\n"
    elif mutation == "provenance":
        pre[-1]["buffer_version"] = 42
    elif mutation == "duplicate":
        pre[-1] = deepcopy(pre[0])
    else:
        pre.append(row("extra"))
    record = captured(reference, raw, None, keys, precoalesce_rows=pre)
    assert record["response"] == reference
    assert record["semantic_receipt"]["fallback_reason"] == "invalid_precoalesce_source"


def test_missing_removed_dependency_source_fails_closed():
    reference, raw, features, keys = invented()
    reference["_selection_backfill"] = {"coalesced_ids": ["unknown-dependency"]}
    record = captured(reference, raw, features, keys)
    assert record["response"] == reference
    assert record["semantic_receipt"]["fallback_reason"] == "unobservable_coalesced_source"


def test_free_final_slot_is_appended_before_an_equal_gain_swap():
    reference, raw, features, keys = invented()
    reference["results"] = reference["results"][:6]
    audit.recount(reference, 4096, Counter())
    record = captured(reference, raw, features, keys)
    assert [(item["candidate_id"], item["victim_id"], item["victim_position"])
            for item in record["semantic_receipt"]["exchanges"]] == [("9", None, 7), ("10", None, 8)]
    assert record["response"]["results"][:6] == reference["results"]
    assert len(record["response"]["results"]) == 8


def test_failed_append_budget_can_use_a_whole_feasible_swap():
    reference, raw, features, keys = invented(candidate_count=1)
    # Both possible final plans have identical semantic improvement. The append
    # loses on characters, while replacing the long unprotected tail fits.
    raw[5] = row("6", "long" * 30 + "\n")
    reference["results"] = [*deepcopy(raw[:6])]
    features["windows"]["6"]["binding"] = audit.row_binding(raw[5])
    audit.recount(reference, 4096, Counter())
    record = captured(reference, raw, features, keys, source_budget=reference["total_source_chars"])
    trials = record["semantic_receipt"]["trials"]
    assert trials[0]["victim_id"] is None and not trials[0]["accepted"]
    assert trials[1]["victim_id"] == "6" and trials[1]["accepted"]
    assert record["response"]["results"][-1] == raw[8]
    assert len(record["response"]["results"]) == 6


@pytest.mark.parametrize("owner", [None, 42, "unknown-source-id"])
def test_unresolved_dependency_source_owner_fails_before_semantic_features(owner):
    reference, raw, features, keys = invented()
    reference["results"][6]["origin"] = "dependency"
    reference["results"][6]["related_to"] = owner
    record = captured(reference, raw, None, keys)
    assert record["response"] == reference
    assert record["semantic_receipt"]["fallback_reason"] == "unresolved_dependency_owner"


@pytest.mark.parametrize("mutation", ["protected", "scalar", "owner", "provenance", "source", "floor", "budget"])
def test_final_guard_rejects_actual_source_scalar_origin_provenance_floor_or_budget_loss(mutation):
    reference, raw, features, keys = invented()
    facets, scores = audit.audit_features(reference, raw, features)
    protected, _ = audit.protected_rows(reference, raw, keys, 8)
    proposal = deepcopy(reference)
    proposal["results"][-1] = deepcopy(raw[8])
    limits = {"max_results": 8, "source_budget": 12000, "token_budget": 4096}
    if mutation == "protected":
        proposal["results"][0] = deepcopy(raw[9])
    elif mutation == "scalar":
        proposal["results"][7]["retrieval_score"] = True
    elif mutation == "owner":
        proposal["results"][7]["related_to"] = "fabricated-owner"
    elif mutation == "provenance":
        proposal["results"][7]["buffer_hash"] = "fabricated-buffer"
    elif mutation == "source":
        proposal["results"][7]["source"] += "fabricated source\n"
    elif mutation == "floor":
        scores["8"] = (1_000_000, 0, 1_000_000)
    else:
        limits["source_budget"] = 1
    final, receipt = audit.guard(reference, proposal, protected, scores, len(facets),
        allowed_rows={item["id"]: item for item in [*raw, *reference["results"]]},
        counter=Counter(), **limits)
    assert not receipt["accepted"] and final == reference


def test_exact_feature_schema_rejects_precomputed_scores_or_extra_top_level_data():
    reference, raw, features, keys = invented()
    features["scores"] = {"9": [1_000_000] * 3}
    record = captured(reference, raw, features, keys)
    assert record["semantic_receipt"]["fallback_reason"] == "invalid_features"


def test_auditor_module_import_and_helpers_do_not_load_production_or_numerical_runtimes():
    import subprocess
    import sys
    code = """
import importlib.abc, importlib.util, sys
class Blocked(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'semantic_search', 'numpy', 'mlx', 'torch', 'transformers', 'jax'}:
            raise AssertionError('Forbidden import: ' + fullname)
sys.meta_path.insert(0, Blocked())
spec = importlib.util.spec_from_file_location('independent', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert module.cosine_ppm([1.0], [1.0]) == 1000000
assert len(module.literal_clauses('load alpha payload and render beta template')) == 2
"""
    completed = subprocess.run([sys.executable, "-c", code,
        str(ROOT / "scripts/audit_semantic_context.py")], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.fixture(scope="module")
def offline_counter():
    spec = importlib.util.spec_from_file_location("semantic_offline_tokenizer_audit",
        ROOT / "scripts/audit_selection.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.offline_encoding(ROOT / ".cache/tokenizers")


@pytest.mark.parametrize("variant", ["basic", "append", "inactive", "invalid_features",
    "no_positive_gain", "clipped", "unresolved_owner", "unobservable_dependency", "invalid_precoalesce"])
def test_actual_selector_receipt_and_payload_reconstructed_independently(variant, offline_counter):
    # Importing the implementation is allowed only here, in the invented test.
    # The independent auditor module above imports no producer or implementation.
    from semantic_search import semantic_context as implementation

    reference, raw, features, keys = invented()
    pre = []
    if variant == "append":
        reference["results"] = reference["results"][:6]
    elif variant == "inactive":
        reference["query"] = "find one implementation"
        features = None
    elif variant == "invalid_features":
        features["windows"]["9"]["vector"][0] = 0.1
    elif variant == "no_positive_gain":
        for window in features["windows"].values():
            window["vector"] = vector(1.0, 0.0, 0.0)
    elif variant == "clipped":
        raw[7] = row("8", "one\ntwo\n")
        reference["results"][7] = row("8", "one\n", window_end_line=2,
                                       content_hash=audit.digest(raw[7]["source"]), truncated=True)
        features["windows"]["8"] = {"binding": audit.row_binding(raw[7]), "vector": vector(0.0, 0.0, 1.0)}
    elif variant == "unresolved_owner":
        reference["results"][6].update(origin="dependency", related_to="missing-owner")
    elif variant == "unobservable_dependency":
        reference["_selection_backfill"] = {"coalesced_ids": ["unknown-dependency"]}
    elif variant == "invalid_precoalesce":
        pre = [row("missing", path="src/missing.py")]
    audit.recount(reference, 4096, offline_counter)
    final, receipt = implementation.semantic_final_context(reference, raw, features,
        definition_keys=keys, precoalesce_rows=pre)
    payload = audit.compact_payload(final)
    record = {"reference_response": reference, "ranked_candidates": raw, "features": features,
        "precoalesce_rows": pre, "definition_keys": keys, "response": final, "semantic_receipt": receipt,
        "compact_payload": payload, "compact_sha256": audit.digest(payload),
        "actual_compact_tokens": len(offline_counter.encode(payload, disallowed_special=())),
        "coverage_coalesce_calls": 1, "semantic_calls": 1,
        "feature_calls": int(bool(audit.literal_clauses(reference["query"]))), "subsequent_backfill_calls": 0}
    assert audit.verify_record(record, offline_counter) == final


@pytest.mark.parametrize("seed", range(6))
def test_invented_float32_vector_roundtrip_matches_exact_ppm_and_trial_order(seed, offline_counter):
    import random
    from semantic_search import semantic_context as implementation

    randomizer = random.Random(seed)
    reference, raw, features, keys = invented(candidate_count=6)
    features["facet_vectors"] = [vector(*[randomizer.uniform(-1, 1) for _ in range(12)]) for _ in range(3)]
    for window in features["windows"].values():
        window["vector"] = vector(*[randomizer.uniform(-1, 1) for _ in range(12)])
    if seed % 2:
        reference["results"] = reference["results"][:6]
    audit.recount(reference, 4096, offline_counter)
    expected, receipt = audit.reconstruct(reference, raw, features, definition_keys=keys, counter=offline_counter)
    actual, actual_receipt = implementation.semantic_final_context(reference, raw, features, definition_keys=keys)
    assert audit.same(actual_receipt, receipt)
    assert audit.same(actual, expected)
