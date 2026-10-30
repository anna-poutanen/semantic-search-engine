"""Invented CPU fixtures for direct exchanges in actual final coverage rows."""
from copy import deepcopy
import hashlib

import pytest

from semantic_search import context, semantic_context as candidate, semantic_features
from semantic_search.cancellation import SearchCancelled
from semantic_search.retrieval import multifile_clauses
from semantic_search.source import physical_lines


QUERY = "load alpha payload and render beta template"
REAL_VALIDATED_SCORES = semantic_features.validated_scores


def row(identifier, source=None, *, start=1, path=None, **metadata):
    source = source if source is not None else f"def {identifier}():\n    return '{identifier}'\n"
    end = start + len(physical_lines(source)) - 1
    digest = hashlib.sha256(source.encode()).hexdigest()
    return {"id": identifier, "path": path or f"src/{identifier}.py", "symbol": identifier,
            "start_line": start, "end_line": end, "window_end_line": end,
            "source": source, "source_hash": digest, "content_hash": digest,
            "retrieval_score": 0.125, "h2o_score": None, "qwen_score": None,
            "origin": "retrieval", "related_to": None, "truncated": False, **metadata}


def response(rows, *, query=QUERY, token_budget=4096, **metadata):
    result = {"query": query, "results": deepcopy(rows), "match_status": "candidates",
              "total_source_chars": sum(len(value["source"]) for value in rows),
              "budget_exhausted": False, "timings": {"query_embedding_s": .123}, **metadata}
    if token_budget is not None:
        result["token_usage"] = {"encoding": context.ENCODING, "tokens": 0,
                                 "budget": token_budget, "truncated": False}
        context.count_context(result)
    return result


def features(raw, reference, updates=()):
    values = {item["id"]: (0, 0, 0) for item in [*raw, *reference["results"]]
              if not candidate.is_clipped(item)}
    values.update(updates)
    return {"facets": [reference["query"], *multifile_clauses(reference["query"])], "scores": values}


@pytest.fixture(autouse=True)
def injected_feature_boundary(monkeypatch):
    # This suite exercises allocation with invented integer scores. The separate
    # feature suite tests real vector/source binding and independent quantization.
    def validated(reference, raw, feature, **kwargs):
        return feature["facets"], feature["scores"]
    monkeypatch.setattr("semantic_search.semantic_features.validated_scores", validated)


@pytest.fixture
def fixture():
    raw = [row(f"window_{index}") for index in range(1, 11)]
    reference = response(raw[:8])
    return raw, reference


def run(raw, reference, updates=(), **kwargs):
    return candidate.semantic_final_context(reference, raw, features(raw, reference, updates), **kwargs)


def exact_count(result):
    actual = len(context.encoding().encode(context.serialize_context(context.compact_result(result)),
                                           disallowed_special=()))
    assert actual == result["token_usage"]["tokens"]
    assert actual <= result["token_usage"]["budget"]


def test_two_direct_exchanges_start_at_actual_final_rows_and_never_backfill(fixture):
    raw, reference = fixture
    before, before_raw = deepcopy(reference), deepcopy(raw)
    result, receipt = run(raw, reference, {"window_9": (100, 200, 0), "window_10": (100, 0, 300)})
    assert [item["id"] for item in result["results"]] == [
        "window_1", "window_2", "window_3", "window_4", "window_5", "window_6", "window_9", "window_10"]
    assert [exchange["victim_id"] for exchange in receipt["exchanges"]] == ["window_8", "window_7"]
    assert len(receipt["trials"]) == 2 and receipt["accepted"]
    assert receipt["reference_support"] == [0, 0, 0]
    assert receipt["final_support"] == [100, 200, 300]
    assert receipt["subsequent_backfill_calls"] == 0
    assert result["timings"] == reference["timings"]
    assert len(result["results"]) == 8
    assert reference == before and raw == before_raw
    assert not any(name.startswith("_semantic") for name in result)
    exact_count(result)


def test_no_positive_gain_is_exact_defensive_coverage_copy(fixture):
    raw, reference = fixture
    result, receipt = run(raw, reference)
    assert result == reference and result is not reference
    assert receipt["trials"] == [] and not receipt["accepted"]
    result["results"][0]["source"] = "changed"
    assert reference["results"][0]["source"] != "changed"


def test_inactive_query_needs_no_feature_object(fixture, monkeypatch):
    raw, reference = fixture
    reference["query"] = "find implementation"
    monkeypatch.setattr("semantic_search.semantic_features.validated_scores", lambda *a, **k: pytest.fail("inactive"))
    result, receipt = candidate.semantic_final_context(reference, raw, None)
    assert result == reference and receipt["fallback_reason"] == "no_literal_clauses"


@pytest.mark.parametrize("error", [ValueError("wrong source"), TypeError("bad vector"), KeyError("missing")])
def test_failed_feature_binding_returns_exact_actual_coverage(fixture, monkeypatch, error):
    raw, reference = fixture
    def failed(*args, **kwargs):
        raise error
    monkeypatch.setattr("semantic_search.semantic_features.validated_scores", failed)
    result, receipt = candidate.semantic_final_context(reference, raw, {})
    assert result == reference and receipt["fallback_reason"] == "invalid_features"
    assert not receipt["trials"]


@pytest.mark.parametrize("bad", [None, (0, 0), (0., 0, 0), (True, 0, 0), (1_000_001, 0, 0)])
def test_every_full_window_requires_valid_integer_scores(fixture, bad):
    raw, reference = fixture
    feature = features(raw, reference)
    if bad is None:
        del feature["scores"]["window_10"]
    else:
        feature["scores"]["window_10"] = bad
    result, receipt = candidate.semantic_final_context(reference, raw, feature)
    assert result == reference and receipt["fallback_reason"] == "invalid_features"


def test_facet_maxima_cannot_trade_one_behavior_for_another(fixture):
    raw, reference = fixture
    result, receipt = run(raw, reference, {"window_8": (100, 100, 100), "window_9": (1000, 0, 0)})
    # The candidate can replace an irrelevant row while preserving window_8,
    # even though the latest-row tiebreak would otherwise remove window_8.
    assert "window_8" in [item["id"] for item in result["results"]]
    assert receipt["final_support"] == [1000, 100, 100]
    assert receipt["exchanges"][0]["victim_id"] == "window_7"


def test_full_query_is_a_facet_not_only_literal_clauses(fixture):
    raw, reference = fixture
    for item in reference["results"][:5]:
        item["truncated"] = True
    result, receipt = run(raw, reference, {"window_6": (500, 0, 0), "window_7": (500, 0, 0),
        "window_8": (500, 0, 0), "window_9": (499, 1000, 1000), "window_10": (499, 1000, 1000)})
    assert receipt["reference_support"][0] == 500
    assert receipt["final_support"][0] == 500
    assert len([item for item in result["results"] if item["id"] in ("window_6", "window_7", "window_8")]) >= 1


def test_ties_use_raw_rank_then_latest_victim(fixture):
    raw, reference = fixture
    result, receipt = run(raw, reference, {"window_9": (1, 2, 3), "window_10": (1, 2, 3)})
    assert receipt["exchanges"][0]["candidate_id"] == "window_9"
    assert receipt["exchanges"][0]["victim_id"] == "window_8"
    assert len(receipt["exchanges"]) == 1


def test_existing_actual_backfill_contribution_is_scored_before_exchange(fixture):
    raw, reference = fixture
    # window_8 was a real delivered backfill result, not an eligible planner row.
    reference["_selection_backfill"] = {"coalesced_ids": ["window_1"], "appended_ids": ["window_8"]}
    result, receipt = run(raw, reference, {"window_8": (500, 500, 500), "window_9": (400, 1000, 400)})
    assert "window_8" in [item["id"] for item in result["results"]]
    assert receipt["reference_support"] == [500, 500, 500]
    assert "_selection_backfill" not in result
    assert reference["_selection_backfill"]["appended_ids"] == ["window_8"]


def test_unknown_coalesced_away_source_fails_closed(fixture):
    raw, reference = fixture
    reference["_selection_backfill"] = {"coalesced_ids": ["outside_raw_dependency"]}
    result, receipt = run(raw, reference, {"window_9": (100, 100, 100)})
    assert result == reference and receipt["fallback_reason"] == "unobservable_coalesced_source"


def test_original_packed_dependency_is_protected_after_containment_coalescing(fixture):
    raw, reference = fixture
    dependency = row("outside_raw_dependency", "nested\n", path="src/outer.py", start=2,
                     origin="dependency", related_to="window_6")
    outer = row("outer", "before\nnested\nafter\n", path="src/outer.py")
    reference["results"][7] = outer
    reference["_selection_backfill"] = {"coalesced_ids": [dependency["id"]]}
    precoalesce = [*reference["results"][:7], dependency]
    result, receipt = run(raw, reference, {"window_9": (100, 100, 100)}, precoalesce_rows=precoalesce)
    assert receipt["accepted"]
    assert outer in result["results"] and raw[5] in result["results"]
    assert {"outer", "window_6"} <= receipt["protected_rows"].keys()
    assert "_selection_backfill" not in result
    assert reference["_selection_backfill"]["coalesced_ids"] == [dependency["id"]]


@pytest.mark.parametrize("change", ["source", "provenance", "hash"])
def test_precoalescing_snapshot_must_be_literally_covered_with_actual_provenance(fixture, change):
    raw, reference = fixture
    precoalesce = deepcopy(reference["results"])
    if change == "source":
        precoalesce[0]["source"] = "forged\n"
        precoalesce[0]["source_hash"] = hashlib.sha256(b"forged\n").hexdigest()
        precoalesce[0]["end_line"] = 1
    elif change == "provenance":
        precoalesce[0].update(source_origin="open_buffer", buffer_version=1, buffer_hash="other")
    else:
        precoalesce[0]["source_hash"] = "wrong"
    result, receipt = run(raw, reference, {"window_9": (100, 100, 100)}, precoalesce_rows=precoalesce)
    assert result == reference and receipt["fallback_reason"] == "invalid_precoalesce_source"


def test_clipped_rows_are_exactly_preserved_and_never_semantically_scored(fixture):
    raw, reference = fixture
    old = reference["results"][7]
    old.update(source="def window_8():\n", end_line=1, truncated=True,
               source_hash=hashlib.sha256(b"def window_8():\n").hexdigest())
    feature = features(raw, reference, {"window_9": (100, 100, 100)})
    # Raw window_8 still requires a score, but the delivered prefix must not use it.
    feature["scores"]["window_8"] = (1_000_000, 1_000_000, 1_000_000)
    result, receipt = candidate.semantic_final_context(reference, raw, feature)
    assert result["results"][7] == old
    assert receipt["reference_support"] == [0, 0, 0]
    assert receipt["protected_rows"]["window_8"][0] == "clipped"


def test_clipped_only_reference_needs_no_prefix_vectors(fixture):
    raw, reference = fixture
    raw = raw[:5]
    for item in reference["results"]:
        item["truncated"] = True
    feature = features(raw, reference)
    result, receipt = candidate.semantic_final_context(reference, raw, feature)
    assert result == reference
    assert receipt["reference_support"] == [-1_000_000] * 3


def test_first_five_canonical_anchors_protect_actual_overlapping_rows(fixture):
    raw, reference = fixture
    original = raw[0]
    # A containing delivered row protects original anchor source even though its
    # ID differs and the original anchor was coalesced away.
    outer = row("outer", "before\n" + original["source"] + "after\n", path=original["path"], start=1)
    original.update(start_line=2, end_line=3, window_end_line=3)
    reference["results"][0] = outer
    result, receipt = run(raw, reference, {"outer": (-1000, -1000, -1000), "window_9": (100, 100, 100)})
    assert result["results"][0] == outer
    assert "outer" in receipt["protected_rows"]


def test_dependencies_and_source_owner_containing_row_are_protected(fixture):
    raw, reference = fixture
    owner = raw[6]
    dependency = row("dependency", origin="dependency", related_to=owner["id"],
                     retrieval_score=owner["retrieval_score"], source_origin="open_buffer",
                     buffer_version=7, buffer_hash="recorded-buffer")
    reference["results"][7] = dependency
    result, receipt = run(raw, reference, {"window_9": (100, 100, 100)})
    assert dependency in result["results"] and owner in result["results"]
    assert {"dependency", owner["id"]} <= receipt["protected_rows"].keys()


def test_raw_portfolio_keeps_first_two_exact_definition_windows(fixture):
    raw, reference = fixture
    raw.extend([row("window_11")])
    key = ("src/shared.py", "duplicate", 1, 20)
    keys = {"window_9": key, "window_10": key, "window_11": key}
    result, receipt = run(raw, reference, {"window_9": (10, 0, 0), "window_10": (0, 10, 0),
                                         "window_11": (1000, 1000, 1000)}, definition_keys=keys)
    assert "window_11" not in receipt["portfolio_ids"]
    assert "window_11" not in [item["id"] for item in result["results"]]
    assert {"window_9", "window_10"} <= set(receipt["portfolio_ids"])


def test_unknown_same_display_label_does_not_collapse_anchors_or_portfolio(fixture):
    raw, reference = fixture
    for item in raw:
        item["symbol"] = "same"
    reference = response(raw[:8])
    _, receipt = run(raw, reference, {"window_9": (1, 1, 1)})
    assert receipt["anchor_ids"] == [f"window_{index}" for index in range(1, 6)]
    assert len(receipt["portfolio_ids"]) == len(raw)


def test_already_covered_literal_candidate_is_not_added(fixture):
    raw, reference = fixture
    raw[8] = deepcopy(raw[7])
    raw[8]["id"] = "covered"
    result, receipt = run(raw, reference, {"covered": (1000, 1000, 1000)})
    assert result == reference and not receipt["trials"]


def test_raw_dependency_metadata_cannot_be_fabricated_into_retrieval_candidate(fixture):
    raw, reference = fixture
    raw[8].update(origin="dependency", related_to="window_1")
    result, receipt = run(raw, reference, {"window_9": (1000, 1000, 1000)})
    assert result == reference and not receipt["trials"]


def test_oversized_candidate_is_rejected_whole_then_next_candidate_is_tried(fixture):
    raw, reference = fixture
    raw[8] = row("window_9", "large line " * 1000 + "\n")
    result, receipt = run(raw, reference, {"window_9": (1000, 1000, 1000), "window_10": (1, 1, 1)},
                          source_budget=1000)
    assert receipt["trials"][0]["candidate_id"] == "window_9"
    assert not receipt["trials"][0]["accepted"]
    assert "window_9" not in [item["id"] for item in result["results"]]
    assert "window_10" in [item["id"] for item in result["results"]]
    assert all(not item["truncated"] for item in result["results"])


def test_eight_budget_trials_are_shared_across_exchanges_and_never_clip(fixture):
    raw, reference = fixture
    raw.extend(row(f"window_{index}", "huge line " * 1000 + "\n") for index in range(11, 15))
    for index in (8, 9):
        raw[index] = row(f"window_{index + 1}", "huge line " * 1000 + "\n")
    update = {item["id"]: (100, 100, 100) for item in raw[8:]}
    result, receipt = run(raw, reference, update, source_budget=1000)
    assert result == reference
    assert len(receipt["trials"]) == 8 and receipt["termination"] == "budget_trial_limit"
    assert all(not trial["accepted"] for trial in receipt["trials"])


def test_exact_token_budget_rejects_full_candidate_instead_of_clipping(fixture):
    raw, reference = fixture
    raw[8] = row("window_9", "alpha beta gamma delta " * 1000 + "\n")
    result, receipt = run(raw, reference, {"window_9": (100, 100, 100)}, source_budget=100000, token_budget=700)
    assert result == reference
    assert receipt["trials"] and all(not trial["accepted"] for trial in receipt["trials"])


@pytest.mark.parametrize("token_budget", [None, 4096])
def test_source_flags_and_optional_budget_metadata_remain_truthful(fixture, token_budget):
    raw, reference = fixture
    reference = response(raw[:8], token_budget=token_budget)
    reference["budget_exhausted"] = True
    result, receipt = run(raw, reference, {"window_9": (1, 1, 1)}, token_budget=token_budget)
    assert receipt["accepted"] and result["budget_exhausted"]
    assert result["total_source_chars"] == sum(len(item["source"]) for item in result["results"])
    if token_budget is None:
        assert "token_usage" not in result
    else:
        exact_count(result)


@pytest.mark.parametrize("name,value", [("max_results", True), ("max_results", -1),
    ("source_budget", -1), ("source_budget", 1.0), ("token_budget", -1), ("token_budget", False)])
def test_invalid_limits_raise_before_allocation(fixture, name, value):
    raw, reference = fixture
    with pytest.raises(ValueError):
        run(raw, reference, **{name: value})


def test_cancellation_propagates_and_never_mutates_input(fixture):
    raw, reference = fixture
    before = deepcopy(reference)
    calls = 0
    def cancel():
        nonlocal calls
        calls += 1
        if calls == 4:
            raise SearchCancelled("cancelled")
    with pytest.raises(SearchCancelled):
        run(raw, reference, {"window_9": (1, 1, 1)}, cancel_check=cancel)
    assert reference == before


@pytest.mark.parametrize("change", ["source", "scalar_type", "scalar_presence", "owner", "buffer"])
def test_final_guard_rejects_forged_source_scalars_owners_or_provenance(fixture, change):
    raw, reference = fixture
    proposal = deepcopy(reference)
    target = proposal["results"][0]
    if change == "source":
        target["source"] += "forged\n"
    elif change == "scalar_type":
        reference["results"][0]["retrieval_score"] = 1.0
        target["retrieval_score"] = 1
    elif change == "scalar_presence":
        del target["qwen_score"]
    elif change == "owner":
        target["related_to"] = "rewritten-owner"
    else:
        target.update(source_origin="open_buffer", buffer_version=1, buffer_hash="different")
    score = features(raw, reference)["scores"]
    result, receipt = candidate.guard_semantic_final(reference, proposal, protected_ids={target["id"]},
        scores=score, facet_count=3, max_results=8, source_budget=12000, token_budget=4096,
        allowed_rows={item["id"]: item for item in [*raw, *reference["results"]]})
    assert result == reference and not receipt["accepted"]


def test_final_guard_checks_original_feature_floor_not_only_planned_changes(fixture):
    raw, reference = fixture
    proposal = deepcopy(reference)
    proposal["results"][7] = deepcopy(raw[8])
    scores = features(raw, reference, {"window_8": (100, 100, 100)})["scores"]
    result, receipt = candidate.guard_semantic_final(reference, proposal, protected_ids=set(),
        scores=scores, facet_count=3, max_results=8, source_budget=12000, token_budget=4096,
        allowed_rows={item["id"]: item for item in [*raw, *reference["results"]]})
    assert result == reference and receipt["fallback_reason"] == "semantic_floor_decreased"


def test_real_vectors_only_feature_boundary_and_selector_agree_without_model_work(fixture, monkeypatch):
    raw, reference = fixture
    monkeypatch.setattr(semantic_features, "validated_scores", REAL_VALIDATED_SCORES)
    def basis(index):
        return [float(position == index) for position in range(1024)]
    vectors = {item["id"]: basis(3) for item in raw}
    vectors.update(window_9=basis(0), window_10=basis(1))
    metadata = {"id": semantic_features.MODEL_ID, "revision": semantic_features.MODEL_REVISION,
                "dimensions": 1024, "query_prompt": semantic_features.QUERY_PROMPT,
                "normalization": "L2, float32"}
    feature = semantic_features.make_features(QUERY, raw, reference,
        [basis(0), basis(1), basis(2)], vectors, metadata)
    result, receipt = candidate.semantic_final_context(reference, raw, feature)
    assert receipt["accepted"] and len(receipt["exchanges"]) == 2
    assert receipt["final_support"] == [1_000_000, 1_000_000, 0]
    assert {"window_9", "window_10"} <= {item["id"] for item in result["results"]}
    exact_count(result)


def test_budget_trial_limit_does_not_reset_after_first_accepted_exchange(fixture):
    raw, reference = fixture
    raw[8] = row("window_9", "huge line " * 1000 + "\n")
    raw.extend(row(f"window_{index}", "huge line " * 1000 + "\n") for index in range(11, 15))
    updates = {item["id"]: (0, 100, 0) for item in [raw[8], *raw[10:]]}
    updates["window_10"] = (1000, 0, 0)
    result, receipt = run(raw, reference, updates, source_budget=1000)
    assert len(receipt["exchanges"]) == 1
    assert receipt["exchanges"][0]["candidate_id"] == "window_10"
    assert len(receipt["trials"]) == 8 and receipt["termination"] == "budget_trial_limit"
    assert "window_10" in {item["id"] for item in result["results"]}
    assert all(not item["truncated"] for item in result["results"])


@pytest.mark.parametrize("provider", ["h2o_score", "qwen_score"])
def test_optional_original_reranker_scalar_and_overlay_metadata_are_kept_verbatim(fixture, provider):
    raw, reference = fixture
    raw[8].update({provider: -12.75, "retrieval_score": .0625,
                  "source_origin": "open_buffer", "buffer_version": 3, "buffer_hash": "literal-buffer"})
    result, receipt = run(raw, reference, {"window_9": (100, 100, 100)})
    assert receipt["accepted"]
    delivered = next(item for item in result["results"] if item["id"] == "window_9")
    assert delivered == raw[8] and type(delivered[provider]) is float


def test_final_guard_checks_literal_identity_for_unprotected_rows_too(fixture):
    raw, reference = fixture
    proposal = deepcopy(reference)
    proposal["results"][7]["qwen_score"] = 123.0
    scores = features(raw, reference)["scores"]
    result, receipt = candidate.guard_semantic_final(reference, proposal, protected_ids=set(),
        scores=scores, facet_count=3, max_results=8, source_budget=12000, token_budget=4096,
        allowed_rows={item["id"]: item for item in [*raw, *reference["results"]]})
    assert result == reference and receipt["fallback_reason"] == "literal_row_changed"


def test_free_excerpt_capacity_appends_even_when_every_delivered_row_is_protected(fixture):
    raw, _ = fixture
    reference = response(raw[:5])
    result, receipt = run(raw, reference, {"window_9": (100, 200, 0), "window_10": (100, 0, 300)})
    assert receipt["accepted"] and len(receipt["exchanges"]) == 2
    assert all(trial["victim_id"] is None for trial in receipt["trials"])
    assert [trial["victim_position"] for trial in receipt["trials"]] == [6, 7]
    assert result["results"][:5] == reference["results"]
    assert len(result["results"]) == 7
    exact_count(result)


def test_free_capacity_wins_equal_utility_without_evicting_unprotected_source(fixture):
    raw, _ = fixture
    reference = response(raw[:7])
    result, receipt = run(raw, reference, {"window_9": (100, 100, 100)})
    assert receipt["exchanges"][0]["victim_id"] is None
    assert receipt["exchanges"][0]["victim_position"] == 8
    assert result["results"][:7] == reference["results"]
    assert result["results"][7] == raw[8]


def test_failed_append_budget_can_then_accept_whole_window_swap(fixture):
    raw, _ = fixture
    raw[5] = row("window_6", "old large source " * 200 + "\n")
    raw[8] = row("window_9", "new source " * 100 + "\n")
    reference = response(raw[:6])
    result, receipt = run(raw, reference, {"window_9": (100, 100, 100)},
                          source_budget=reference["total_source_chars"] + 50)
    assert receipt["trials"][0]["victim_id"] is None and not receipt["trials"][0]["accepted"]
    assert receipt["trials"][1]["victim_id"] == "window_6" and receipt["trials"][1]["accepted"]
    assert len(result["results"]) == 6 and result["results"][5] == raw[8]
    assert all(not item["truncated"] for item in result["results"])
    exact_count(result)


@pytest.mark.parametrize("owner", ["unknown_source_owner", None, []])
def test_unresolved_dependency_source_owner_fails_closed(fixture, owner):
    raw, reference = fixture
    reference["results"][7].update(origin="dependency", related_to=owner)
    result, receipt = run(raw, reference, {"window_9": (100, 100, 100)})
    assert result == reference and receipt["fallback_reason"] == "unresolved_dependency_owner"
    assert receipt["trials"] == []


def test_coalesced_away_dependency_also_requires_known_original_owner_source(fixture):
    raw, reference = fixture
    dependency = row("outside_dependency", "nested\n", path="src/outer.py", start=2,
                     origin="dependency", related_to="unknown_original_owner")
    outer = row("outer", "before\nnested\nafter\n", path="src/outer.py")
    reference["results"][7] = outer
    reference["_selection_backfill"] = {"coalesced_ids": [dependency["id"]]}
    precoalesce = [*reference["results"][:7], dependency]
    result, receipt = run(raw, reference, {"window_9": (100, 100, 100)}, precoalesce_rows=precoalesce)
    assert result == reference and receipt["fallback_reason"] == "unresolved_dependency_owner"
