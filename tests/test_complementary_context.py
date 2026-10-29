"""Invented fixtures only; no observed queries, model, index or retrieval work."""
from copy import deepcopy
from fractions import Fraction
import hashlib
from unittest.mock import patch

import pytest

from semantic_search import complementary_context as candidate, context, selection
from semantic_search.cancellation import SearchCancelled
from semantic_search.corpus import Snippet
from semantic_search.source import physical_lines


QUERY = "load alpha payload and render beta template and persist gamma cache"


def snippet(number, words="ordinary value", *, symbol=None, start=None):
    start = start or number * 10 + 1
    symbol = symbol or f"f{number}"
    text = f"def {symbol.replace('.', '_')}():\n    return '{words}'\n"
    return Snippet(f"src/code.py:{symbol}:{start}-{start + 1}", "src/code.py", symbol,
                   start, start + 1, text, hashlib.sha256(text.encode()).hexdigest())


class Graph:
    def __init__(self, rows, edges=()):
        self.edges = dict(edges)
        self.rows = {}
        for row in sorted(rows, key=lambda row: (row.start_line, row.id)):
            self.rows.setdefault((row.path, row.symbol), row)

    def dependencies(self, row, limit):
        return [self.rows[key] for key in self.edges.get((row.path, row.symbol), ())][:limit]


def fixture(*, third=True, tail_words=None):
    rows = [snippet(n, "load alpha payload" if n == 1 else (tail_words or {}).get(n, "ordinary value"))
            for n in range(1, 9)]
    rows += [snippet(9, "render beta template")]
    if third:
        rows.append(snippet(10, "persist gamma cache"))
    return rows


def run(rows, *, keys=None, graph=None, query=QUERY, max_results=8, include_dependencies=True):
    pairs = [(row, 100. - index) for index, row in enumerate(rows)]
    graph = graph or Graph(rows)
    if keys is None:
        keys = {row.id: (row.path, row.symbol, row.start_line, row.end_line)
                for row in rows + list(graph.rows.values())}
    result = candidate.complementary_plan(pairs, query=query, max_results=max_results,
        include_dependencies=include_dependencies, references=graph, definition_keys=keys)
    baseline = selection.definition_context(pairs, query=query, max_results=max_results,
        include_dependencies=include_dependencies, references=graph, definition_keys=keys)
    return result, baseline, pairs, keys


def identifiers(pairs):
    return [row.id for row, _ in pairs]


def response(pairs, origins=None, *, token_budget=4096):
    rows = []
    for row, score in pairs:
        origin, owner = (origins or {}).get(row.id, ("retrieval", None))
        rows.append({"id": row.id, "path": row.path, "symbol": row.symbol,
                     "start_line": row.start_line, "end_line": row.end_line,
                     "window_end_line": row.end_line, "source": row.text,
                     "source_hash": row.content_hash, "content_hash": row.content_hash,
                     "retrieval_score": score, "h2o_score": None, "qwen_score": None,
                     "origin": origin, "related_to": owner, "truncated": False})
    value = {"query": QUERY, "scope": context.indexed_scope("src"), "match_status": "candidates",
             "results": rows, "total_source_chars": sum(len(row["source"]) for row in rows),
             "budget_exhausted": False}
    return context.apply_token_budget(value, token_budget)


def test_fixed_constants_and_production_aliases_are_unchanged():
    from semantic_search import backend
    from semantic_search.context_backfill import coalesce_context
    assert (candidate.MODE, candidate.MAX_RAW_WINDOWS_PER_DEFINITION,
            candidate.MAX_ADDITIONS, candidate.RANK_OFFSET) == ("complementary_v2", 2, 2, 8)
    assert backend.select_context is selection.definition_context
    assert backend.postprocess_context is coalesce_context


def test_normalization_and_exact_aspect_support():
    assert candidate.content_terms("the Bodies and retries, class paths parseHTTP") == {
        "body", "retry", "class", "path", "parse", "http"}
    assert candidate.aspect_scores({"alpha"}, [{"alpha", "beta"}], {"alpha": 2, "beta": 1}) == (Fraction(1, 3),)


def test_two_complementary_definitions_replace_latest_unprotected_rows_with_original_scores():
    rows = fixture()
    (selected, eligible, origins, plan), baseline, pairs, _ = run(rows)
    assert plan["accepted"] and plan["termination"] == "max_additions"
    assert identifiers(selected) == identifiers(pairs)
    assert identifiers(eligible) == [row.id for row in rows[:5] + rows[8:10] + rows[5:6]]
    assert plan["evicted_ids"] == [row.id for row in rows[6:8]]
    assert eligible.source_plan == plan
    assert all(dict((row.id, score) for row, score in pairs)[row.id] == score for row, score in eligible)
    assert all(origins[row.id] == ("retrieval", None) for row, _ in eligible)
    assert len(plan["candidate_trials"]) == 2
    assert identifiers(baseline[1]) == [row.id for row in rows[:8]]


def test_receipt_is_defensive_and_wrapper_calls_actual_planner_once():
    rows = fixture()
    (_, eligible, _, plan), _, pairs, keys = run(rows)
    returned = eligible.source_plan
    returned["added_ids"].clear()
    assert eligible.source_plan == plan
    with patch.object(candidate, "complementary_plan", wraps=candidate.complementary_plan) as actual:
        wrapped = candidate.complementary_context(pairs, query=QUERY, max_results=8,
            include_dependencies=False, references=None, definition_keys=keys)
    assert actual.call_count == 1 and len(wrapped) == 3


def test_one_coherent_aspect_window_can_improve_over_union_of_incidental_terms():
    rows = [snippet(n, {1: "load alpha", 2: "payload", 3: "render beta template"}.get(n, "ordinary value"))
            for n in range(1, 9)] + [snippet(9, "load alpha payload")]
    (_, eligible, _, plan), _, _, _ = run(rows, query="load alpha payload and render beta template")
    assert plan["added_ids"] == [rows[8].id]
    assert rows[8].id in identifiers(eligible)
    assert plan["candidate_trials"][0]["gain"]["numerator"] > 0


def test_first_two_ranked_windows_survive_exact_definition_portfolio_but_third_does_not():
    rows = fixture(third=False)
    alternate = snippet(11, "persist gamma cache", symbol=rows[0].symbol, start=16)
    third = snippet(12, "render beta template persist gamma cache", symbol=rows[0].symbol, start=21)
    rows += [alternate, third]
    keys = {row.id: (row.path, row.symbol, row.start_line, row.end_line) for row in rows}
    for row in (rows[0], alternate, third):
        keys[row.id] = (row.path, row.symbol, 11, 25)
    (selected, eligible, _, plan), _, _, _ = run(rows, keys=keys)
    assert alternate.id in plan["portfolio_ids"] and third.id not in plan["portfolio_ids"]
    assert alternate.id in identifiers(eligible)
    assert rows[0].id in identifiers(eligible)
    assert third.id not in identifiers(selected)


def test_unknown_or_distinct_canonical_definitions_with_same_display_label_do_not_collapse():
    rows = fixture()
    extra = [snippet(11 + n, "render beta template", symbol="duplicate", start=200 + 10 * n) for n in range(3)]
    for keys in ({}, {row.id: (row.path, row.symbol, row.start_line, row.end_line) for row in rows + extra}):
        (_, _, _, plan), _, _, _ = run(rows + extra, keys=keys)
        assert all(row.id in plan["portfolio_ids"] for row in extra)


@pytest.mark.parametrize("reason", ["inactive", "no_gain", "tiny", "empty"])
def test_no_admission_returns_exact_original_canonical_pool_and_allocation(reason):
    rows = fixture()
    query, maximum = QUERY, 8
    if reason == "inactive":
        query = "simple implementation"
    elif reason == "no_gain":
        rows = fixture(third=False)[:8]
        rows[0] = snippet(1, "load alpha payload render beta template persist gamma cache")
    elif reason == "tiny":
        maximum = 2
    else:
        rows = []
    (selected, eligible, origins, plan), baseline, _, _ = run(rows, query=query, max_results=maximum)
    assert not plan["accepted"]
    assert selected == baseline[0] and eligible == baseline[1] and origins == baseline[2]


def test_first_capacity_blocked_choice_stops_without_skipping_to_lower_utility_candidate():
    rows = fixture()
    parent = snippet(11)
    edges = [((parent.path, parent.symbol), [(row.path, row.symbol) for row in [rows[8], *rows[5:8]]])]
    (selected, eligible, _, plan), baseline, _, _ = run(rows, graph=Graph(rows + [parent], edges))
    assert not plan["accepted"] and plan["termination"] == "capacity_blocked"
    assert len(plan["candidate_trials"]) == 1
    assert plan["candidate_trials"][0]["candidate_id"] == rows[8].id
    assert plan["protected_baseline_ids"] == [row.id for row in rows[:5]]
    assert selected == baseline[0] and eligible == baseline[1]


def test_second_capacity_block_rolls_back_trial_protection_and_keeps_first_admission():
    rows = fixture()
    parent = snippet(12)
    edges = [((parent.path, parent.symbol), [(row.path, row.symbol) for row in [rows[9], rows[6], rows[7]]])]
    (_, eligible, _, plan), _, _, _ = run(rows, graph=Graph(rows + [parent], edges))
    assert plan["accepted"] and plan["termination"] == "capacity_blocked"
    assert plan["added_ids"] == [rows[8].id]
    assert [trial["accepted"] for trial in plan["candidate_trials"]] == [True, False]
    assert plan["protected_baseline_ids"] == [row.id for row in rows[:5]]
    assert rows[6].id in identifiers(eligible) and rows[7].id not in identifiers(eligible)
    assert not plan["protected_keys"]


def test_static_dependencies_keep_owner_score_and_outside_raw_window_is_exempt_from_raw_cap():
    rows = fixture(third=False)[:8]
    dependency = snippet(20, symbol="helper", start=100)
    first = snippet(21, "render beta template", symbol="helper", start=120)
    second = snippet(22, "persist gamma cache", symbol="helper", start=140)
    rows += [first, second]
    keys = {row.id: (row.path, row.symbol, row.start_line, row.end_line) for row in rows + [dependency]}
    for row in (dependency, first, second):
        keys[row.id] = (row.path, row.symbol, 100, 160)
    graph = Graph(rows + [dependency], [((rows[0].path, rows[0].symbol), [(dependency.path, dependency.symbol)])])
    (_, eligible, origins, plan), _, pairs, _ = run(rows, keys=keys, graph=graph)
    assert plan["raw_windows_per_definition"] == 2 and plan["dependency_windows_outside_raw_exempt"]
    assert plan["added_ids"] == [first.id, second.id]
    assert [row.symbol for row, _ in eligible].count("helper") == 3
    assert origins[dependency.id] == ("dependency", rows[0].id)
    assert dict((row.id, score) for row, score in eligible)[dependency.id] == pairs[0][1]


def test_ambiguous_graph_labels_create_no_invented_neighbor_or_shared_flow_witnesses():
    rows = fixture()
    other = snippet(11, symbol=rows[0].symbol, start=200)
    graph = Graph(rows + [other], [((rows[0].path, rows[0].symbol), [(rows[8].path, rows[8].symbol)])])
    keys = {row.id: (row.path, row.symbol, row.start_line, row.end_line) for row in rows + [other]}
    (_, _, _, plan), _, _, _ = run(rows, graph=graph, keys=keys)
    assert not plan["graph_witnesses"]
    assert all(not trial["flow_witnesses"] for trial in plan["candidate_trials"])


def test_unknown_window_cannot_borrow_another_same_label_definition_graph_identity():
    rows = fixture()
    known = snippet(20, symbol=rows[8].symbol, start=300)
    parent = snippet(21, symbol="parent")
    keys = {row.id: (row.path, row.symbol, row.start_line, row.end_line)
            for row in rows + [known, parent] if row.id != rows[8].id}
    graph = Graph(rows + [known, parent], [((parent.path, parent.symbol),
        [(known.path, known.symbol), (rows[7].path, rows[7].symbol)])])
    (_, _, _, plan), _, _, _ = run(rows, graph=graph, keys=keys)
    trial = next(value for value in plan["candidate_trials"] if value["candidate_id"] == rows[8].id)
    assert rows[8].id not in plan["canonical_keys"]
    assert not trial["flow_witnesses"] and not trial["flow_keys"] and not trial["orchestrator_keys"]


def test_recorded_k24_pool_never_gains_an_unscored_tail_or_new_scalar():
    rows = fixture() + [snippet(n) for n in range(11, 25)]
    (selected, eligible, _, plan), _, pairs, _ = run(rows)
    raw_ids = {row.id for row, _ in pairs}
    assert len(plan["raw_ranks"]) == 24 and set(plan["portfolio_ids"]) <= raw_ids
    assert {row.id for row, _ in selected + eligible} <= raw_ids


def test_guard_accepts_actual_safe_complementary_source_and_preserves_input_objects():
    (_, eligible, origins, plan), baseline, _, _ = run(fixture())
    reference, proposal = response(baseline[1], baseline[2]), response(eligible, origins)
    before = deepcopy((reference, proposal))
    chosen, receipt = candidate.guard_complementary_plan(reference, proposal, plan)
    assert receipt["accepted"] and receipt["reference_kind"] == "coverage_factory"
    assert chosen == proposal and chosen is not proposal
    assert (reference, proposal) == before


@pytest.mark.parametrize("replacement", [True, 1])
def test_protected_original_float_score_cannot_be_replaced_by_equal_boolean_or_integer(replacement):
    (_, eligible, origins, plan), baseline, _, _ = run(fixture())
    reference, proposal = response(baseline[1], baseline[2]), response(eligible, origins)
    reference["results"][0]["retrieval_score"] = 1.0
    proposal["results"][0]["retrieval_score"] = replacement
    chosen, receipt = candidate.guard_complementary_plan(reference, proposal, plan)
    assert not receipt["source_preserved"] and not receipt["accepted"]
    assert chosen == reference


@pytest.mark.parametrize("tamper", ["omit", "character_clip", "token_clip", "source_hash", "content_hash",
                                   "source_origin", "buffer_version", "buffer_hash", "key_presence",
                                   "retrieval_score", "h2o_score", "qwen_score"])
def test_actual_protected_source_provenance_and_scores_are_checked_after_packing(tamper):
    (_, eligible, origins, plan), baseline, _, _ = run(fixture())
    reference, proposal = response(baseline[1], baseline[2]), response(eligible, origins)
    row = proposal["results"][0]
    if tamper == "omit":
        proposal["results"].pop(0)
    elif tamper in ("character_clip", "token_clip"):
        row["source"] = physical_lines(row["source"])[0]
        row["end_line"] = row["start_line"]
        row["source_hash"] = hashlib.sha256(row["source"].encode()).hexdigest()
        row["truncated"] = True
        proposal["budget_exhausted"] = True
        proposal["token_usage"]["truncated"] = tamper == "token_clip"
        context.count_context(proposal)
    elif tamper == "key_presence":
        row["source_origin"] = None
    else:
        row[tamper] = "tampered"
    chosen, receipt = candidate.guard_complementary_plan(reference, proposal, plan)
    assert not receipt["accepted"] and not receipt["source_preserved"]
    assert chosen == reference


def test_actual_delivered_aspect_floor_rejects_lost_unprotected_support_despite_full_plan_features():
    rows = fixture(tail_words={7: "persist gamma cache"})
    (_, eligible, origins, plan), baseline, _, _ = run(rows)
    reference, proposal = response(baseline[1], baseline[2]), response(eligible, origins)
    row = next(row for row in proposal["results"] if row["id"] == rows[9].id)
    row["source"] = physical_lines(row["source"])[0]
    row["end_line"] = row["start_line"]
    row["source_hash"] = hashlib.sha256(row["source"].encode()).hexdigest()
    row["truncated"] = True
    chosen, receipt = candidate.guard_complementary_plan(reference, proposal, plan)
    assert receipt["source_preserved"] and not receipt["aspect_preserved"]
    assert receipt["fallback_reason"] == "delivered_aspect_support_decreased" and chosen == reference


def test_guard_protects_reference_neighbor_outside_original_eligible_ids():
    rows = fixture()
    graph = Graph(rows, [((rows[0].path, rows[0].symbol), [(rows[8].path, rows[8].symbol)])])
    (_, eligible, origins, plan), baseline, pairs, _ = run(rows, graph=graph, include_dependencies=False)
    assert rows[8].id not in plan["baseline_eligible_ids"]
    reference = response(baseline[1][:-1] + [pairs[8]], baseline[2])
    proposal = response(eligible, origins)
    proposal["results"] = [row for row in proposal["results"] if row["id"] != rows[8].id]
    chosen, receipt = candidate.guard_complementary_plan(reference, proposal, plan)
    assert rows[8].id in receipt["protected_ids"]
    assert not receipt["accepted"] and chosen == reference


def test_contained_original_owner_identifier_does_not_require_returned_row_membership():
    inner = snippet(1)
    outer = {"id": "outer", "path": inner.path, "symbol": "outer", "start_line": inner.start_line - 1,
             "end_line": inner.end_line, "source": "def outer():\n" + inner.text,
             "origin": "retrieval", "related_to": None}
    outer["source_hash"] = hashlib.sha256(outer["source"].encode()).hexdigest()
    original = response([(inner, 1.)])["results"][0]
    assert candidate.protected_covered(outer, original)
    dependency = response([(snippet(20), 1.)])["results"][0]
    dependency.update(origin="dependency", related_to=inner.id)
    assert candidate.protected_covered(dependency, deepcopy(dependency))
    # The source pointer remains the original direct caller, even when another
    # returned excerpt contains its bytes. Never rewrite it to outer's ID.
    assert inner.id not in {outer["id"], dependency["id"]}
    assert dependency["related_to"] == inner.id


def test_inactive_postprocessor_coalesces_once_and_never_calls_lazy_reference_factory():
    (selected, eligible, origins, plan), baseline, _, _ = run(fixture(), query="simple implementation")
    packed = response(eligible, origins)
    def forbidden():
        raise AssertionError("inactive reference factory")
    with patch.object(candidate, "coalesce_context", wraps=candidate.coalesce_context) as coalesce, \
            patch.object(candidate, "guard_complementary_plan", wraps=candidate.guard_complementary_plan) as guard:
        chosen = candidate.guard_complementary_context(packed, response(selected)["results"],
            selection_plan=plan, max_results=8, source_budget=12000, token_budget=4096,
            reference_factory=forbidden)
    assert coalesce.call_count == guard.call_count == 1
    assert guard.call_args.args[0] is guard.call_args.args[1]
    assert chosen == candidate.coalesce_context(response(baseline[1], baseline[2]),
        response(baseline[0])["results"], max_results=8, source_budget=12000, token_budget=4096)
    assert not {"source_plan", "complementary_plan", "_complementary_guard"} & chosen.keys()


def test_admitted_postprocessor_packs_one_actual_reference_and_checks_cancellation():
    (selected, eligible, origins, plan), baseline, _, _ = run(fixture())
    reference = response(baseline[1], baseline[2])
    calls = []
    def factory():
        calls.append("reference")
        return reference
    with patch.object(candidate, "guard_complementary_plan", wraps=candidate.guard_complementary_plan) as guard:
        chosen = candidate.guard_complementary_context(response(eligible, origins), response(selected)["results"],
            selection_plan=plan, max_results=8, source_budget=12000, token_budget=4096, reference_factory=factory)
    assert calls == ["reference"] and guard.call_count == 1
    assert chosen["results"] == response(eligible, origins)["results"]
    def cancelled():
        raise SearchCancelled("synthetic cancel")
    with pytest.raises(SearchCancelled):
        candidate.guard_complementary_context(response(eligible, origins), (), selection_plan=plan,
            max_results=8, source_budget=12000, reference_factory=factory, cancel_check=cancelled)
    assert calls == ["reference"]


def test_invalid_mode_plan_factory_and_inactive_reference_fail_closed():
    with pytest.raises(ValueError, match="mode"):
        candidate.complementary_plan([], mode="unfrozen", query=QUERY, max_results=8,
            include_dependencies=False, references=None)
    with pytest.raises(ValueError, match="receipt"):
        candidate.guard_complementary_plan({}, {}, {})
    (_, eligible, origins, plan), _, _, _ = run(fixture())
    with pytest.raises(ValueError, match="reference factory"):
        candidate.guard_complementary_context(response(eligible, origins), (), selection_plan=plan,
            max_results=8, source_budget=12000)
    (_, eligible, origins, plan), _, _, _ = run(fixture(), query="simple implementation")
    packed = response(eligible, origins)
    with pytest.raises(ValueError, match="self reference"):
        candidate.guard_complementary_plan(packed, deepcopy(packed), plan)
