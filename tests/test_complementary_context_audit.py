"""Invented adversarial checks for the new independent reconstruction."""
from copy import deepcopy
from dataclasses import asdict
from fractions import Fraction
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import audit_complementary_context as audit
import complementary_inputs as data

QUERY = "load alpha payload and render beta template and persist gamma cache"
SCOPE = {"source_root": "src", "membership": "saved implementations; Git-tracked files only in Git projects",
         "source_exclusions": [], "exclusions_omitted": 0}


def invented(*, active=True, count=10):
    inventory, keys, pairs = {}, {}, []
    for number in range(1, count + 1):
        words = {1: "load alpha payload", 9: "render beta template", 10: "persist gamma cache"}.get(number, "ordinary value")
        text = f"def f{number}():\n    return '{words}'\n"
        row = data.Window(str(number), f"src/unit{number}.py", f"f{number}", 1, 2, text, data.sha(text.encode()))
        inventory[row.id], keys[row.id] = row, (row.path, row.symbol, 1, 2)
        pairs.append((row, 1. / number))
    case = {"subset": "invented", "dataset": "invented", "query_id": "one", "condition": "dense",
        "kind": "present", "multi_file": True, "qualified": True, "groups": [],
        "pairs": pairs, "inventory": inventory, "definition_keys": keys,
        "graph": data.FrozenGraph(inventory, []), "ambiguous_symbols": set(),
        "original": {"query": QUERY if active else "find ordinary value", "scope": SCOPE, "match_status": "candidates",
                     "results": [{"qwen_score": None}]}}
    return case


def plan(case):
    ranked = audit.pairs(case)
    baseline = audit.native.baseline(ranked, {key: asdict(value) for key, value in case["inventory"].items()},
        case["definition_keys"], case["graph"].edges, canonical_definitions=True)
    return audit.planner(ranked, *baseline, case["definition_keys"], case["graph"].edges,
                         case["ambiguous_symbols"], query=case["original"]["query"])


@pytest.fixture(scope="module")
def counter():
    return audit.old.offline_encoding(ROOT / ".cache/tokenizers")


def trace(case, counter):
    selected, eligible, origins, receipt = plan(case)
    controls = {}
    for name in data.CONTROLS:
        response = audit.reconstruct_control(case, name, counter)[0]
        controls[name, data.case_key(case)] = {"response": response, "reference": {"path": name, "line_number": 1}}
    before = audit.base.ordinary_pack(case["original"], eligible, origins, counter)
    proposal = audit.base.coalesce_pack(before, audit.native.full_rows(selected), counter)
    reference = controls["coverage_control", data.case_key(case)]["response"] if receipt["accepted"] else proposal
    response, guard = audit.guard(reference, proposal, receipt, self_reference=not receipt["accepted"])
    compact = audit.win.compact_payload(response)
    row = {**{name: case[name] for name in ("subset", "dataset", "query_id", "condition", "kind", "multi_file", "qualified")},
        "evidence_groups": case["groups"], "selection_plan": receipt, "complementary_plan_receipt": deepcopy(receipt),
        "stages": {"ranked": audit.pairs(case), "selected": selected, "eligible": eligible, "origins": origins,
                   "definition_keys": case["definition_keys"], "ambiguous_symbols": []}, "pre_postprocess_response": before,
        "backfill_candidates": audit.full_rows(case, selected), "ranked_candidates": audit.full_rows(case, audit.pairs(case)), "proposal_response": proposal,
        "reference_response": reference, "response": response, "complementary_guard_receipt": guard,
        "planner_calls": 1, "guard_calls": 1, "coalesce_calls": 1,
        "reference_calls": int(receipt["accepted"]), "self_reference_identity": not receipt["accepted"],
        "compact_payload": compact, "compact_sha256": data.sha(compact.encode()),
        "actual_compact_tokens": len(counter.encode(compact, disallowed_special=())),
        "control_references": {name: deepcopy(controls[name, data.case_key(case)]["reference"]) for name in data.CONTROLS}}
    row.update(audit.reported_score(case, response), selector=audit.MODE, comparisons=audit.differences(case, response, controls))
    return row, controls


def test_fraction_support_prefers_one_coherent_window():
    assert audit.support({"alpha"}, [{"alpha", "beta"}], {"alpha": 2, "beta": 1}) == (Fraction(1, 3),)
    assert audit.best(("a", "b"), {"a": (Fraction(1, 3),), "b": (Fraction(2, 3),)}, 1) == (Fraction(2, 3),)


def test_fixed_rule_admits_two_complements_and_keeps_original_scalars():
    case = invented()
    selected, eligible, origins, receipt = plan(case)
    assert receipt["added_ids"] == ["9", "10"]
    assert receipt["evicted_ids"] == ["7", "8"]
    assert [row["snippet"]["id"] for row in eligible] == ["1", "2", "3", "4", "5", "9", "10", "6"]
    assert all(row["score"] == dict((item.id, score) for item, score in case["pairs"])[row["snippet"]["id"]] for row in eligible)


def test_same_definition_second_window_is_retained_third_is_not():
    case = invented()
    first = case["inventory"]["1"]
    for number in (9, 10):
        old = case["inventory"][str(number)]
        alternate = data.Window(old.id, first.path, first.symbol, number, number + 1, old.text, old.content_hash)
        case["inventory"][old.id] = alternate
        case["pairs"][number - 1] = alternate, case["pairs"][number - 1][1]
        case["definition_keys"][old.id] = case["definition_keys"][first.id]
    receipt = plan(case)[3]
    assert "9" in receipt["portfolio_ids"] and "10" not in receipt["portfolio_ids"]


def test_unknown_winner_does_not_inherit_another_same_name_definition_graph_edges():
    case = invented()
    unknown = case["inventory"]["9"]
    del case["definition_keys"][unknown.id]
    case["definition_keys"]["known_other"] = (unknown.path, unknown.symbol, 300, 301)
    case["definition_keys"]["parent"] = ("src/parent.py", "parent", 1, 2)
    case["graph"].edges = {("src/parent.py", "parent"): [(unknown.path, unknown.symbol),
        (case["inventory"]["8"].path, case["inventory"]["8"].symbol)]}
    receipt = plan(case)[3]
    trial = next(value for value in receipt["candidate_trials"] if value["candidate_id"] == "9")
    assert not trial["flow_witnesses"] and not trial["flow_keys"] and not trial["orchestrator_keys"]


@pytest.mark.parametrize("active", [True, False])
def test_complete_trace_is_reconstructed_without_production(active, counter):
    case = invented(active=active)
    row, controls = trace(case, counter)
    assert audit.verify_row(row, case, controls, counter) == row["response"]
    assert row["reference_calls"] == int(active)
    assert row["self_reference_identity"] is (not active)


@pytest.mark.parametrize("field", ["planner_calls", "guard_calls", "coalesce_calls", "reference_calls", "self_reference_identity"])
def test_lie_about_actual_calls_is_rejected(field, counter):
    case = invented()
    row, controls = trace(case, counter)
    row[field] = False if field == "self_reference_identity" else 0
    if field == "self_reference_identity":
        row[field] = True
    with pytest.raises(ValueError):
        audit.verify_row(row, case, controls, counter)


@pytest.mark.parametrize("mutation", ["score", "source", "plan", "origin", "tokens", "reference", "guard", "backfill", "labels", "evidence_type", "comparison_type"])
def test_false_source_scalar_plan_and_evidence_receipts_are_rejected(mutation, counter):
    case = invented()
    row, controls = trace(case, counter)
    if mutation == "score":
        row["stages"]["ranked"][0]["score"] += 0.001
    elif mutation == "source":
        row["response"]["results"][0]["source"] += "# fabricated\n"
    elif mutation == "plan":
        row["selection_plan"]["term_df"]["alpha"] += 1
    elif mutation == "origin":
        row["stages"]["origins"]["9"] = ["dependency", "1"]
    elif mutation == "tokens":
        row["actual_compact_tokens"] += 1
    elif mutation == "reference":
        row["control_references"]["legacy"]["line_number"] = 2
    elif mutation == "guard":
        row["complementary_guard_receipt"]["accepted"] = False
    elif mutation == "backfill":
        row["backfill_candidates"].pop()
    elif mutation == "evidence_type":
        row["complete"] = 0
    elif mutation == "comparison_type":
        row["comparisons"]["legacy"]["complete_gain"] = 0
    else:
        row["qualified"] = False
    with pytest.raises(ValueError):
        audit.verify_row(row, case, controls, counter)


def test_actual_delivered_guard_rejects_loss_of_protected_source(counter):
    case = invented()
    row, _ = trace(case, counter)
    proposal = deepcopy(row["proposal_response"])
    proposal["results"] = [value for value in proposal["results"] if value["id"] != "1"]
    response, receipt = audit.guard(row["reference_response"], proposal, row["selection_plan"])
    assert response == row["reference_response"] and not receipt["source_preserved"]


def test_lexical_tail_that_does_not_survive_packing_cannot_support_guard(counter):
    case = invented()
    row, _ = trace(case, counter)
    proposal = deepcopy(row["proposal_response"])
    for value in proposal["results"]:
        if value["id"] == "1":
            value["source"] = "def f1():\n"
            value["end_line"] = 1
            value["source_hash"] = data.sha(value["source"].encode())
    _, receipt = audit.guard(row["reference_response"], proposal, row["selection_plan"])
    assert not receipt["aspect_preserved"]


def test_qualified_evidence_needs_symbol_and_literal_whole_span_hash(counter):
    case = invented()
    row, _ = trace(case, counter)
    first = row["response"]["results"][0]
    case["groups"] = [[{"path": first["path"], "symbol": first["symbol"], "evidence_lines": [1, 2],
                        "evidence_sha256": data.sha(first["source"].encode())}]]
    assert audit.quality(case, row["response"])["complete"]
    case["groups"][0][0]["symbol"] = "wrong"
    assert not audit.quality(case, row["response"])["complete"]
    case["qualified"] = False
    assert audit.quality(case, row["response"])["complete"]


def test_no_gain_does_not_qualify_even_with_zero_losses(counter):
    case = invented()
    row, controls = trace(case, counter)
    gate = audit.quality_gate([case], {data.case_key(case): row["response"]}, controls)
    assert not gate["qualified"] and gate["additional_distinct_complete_multi_queries_vs_coverage"] == []


def gold(case, identifier):
    row = case["inventory"][identifier]
    return {"path": row.path, "symbol": row.symbol, "evidence_lines": [row.start_line, row.end_line],
            "evidence_sha256": row.content_hash}


def test_complete_multi_gain_is_deduplicated_across_repeated_modes(counter):
    cases, controls, responses = [], {}, {}
    for mode in ("dense", "hybrid"):
        case = invented()
        case["condition"] = mode
        case["groups"] = [[gold(case, "1")], [gold(case, "9")]]
        row, local = trace(case, counter)
        cases.append(case)
        controls.update(local)
        responses[data.case_key(case)] = row["response"]
    result = audit.quality_gate(cases, responses, controls)
    assert result["qualified"]
    assert result["additional_distinct_complete_multi_queries_vs_coverage"] == [["invented", "one"]]


def test_one_individual_group_loss_blocks_other_complete_multi_gains(counter):
    good = invented()
    good["groups"] = [[gold(good, "1")], [gold(good, "9")]]
    bad = invented()
    bad["query_id"] = "loss"
    bad["groups"] = [[gold(bad, "8")]]
    controls, responses = {}, {}
    for case in (good, bad):
        row, local = trace(case, counter)
        controls.update(local)
        responses[data.case_key(case)] = row["response"]
    result = audit.quality_gate([good, bad], responses, controls)
    assert not result["qualified"]
    assert result["additional_distinct_complete_multi_queries_vs_coverage"]
    assert result["against_controls"]["coverage_control"]["group_losses"][0]["groups"] == [1]


def test_single_file_gain_cannot_satisfy_multi_file_incremental_gate(counter):
    case = invented()
    case["multi_file"] = False
    case["groups"] = [[gold(case, "9")]]
    row, controls = trace(case, counter)
    result = audit.quality_gate([case], {data.case_key(case): row["response"]}, controls)
    assert not result["qualified"] and not result["additional_distinct_complete_multi_queries_vs_coverage"]


def test_optional_qwen_score_key_absence_is_preserved_in_old_template(counter):
    case = invented()
    case["original"]["results"] = [{"h2o_score": None}]
    row, controls = trace(case, counter)
    assert all("qwen_score" not in item for item in row["backfill_candidates"])
    assert audit.verify_row(row, case, controls, counter) == row["response"]


@pytest.mark.parametrize("field", ["retrieval_score", "h2o_score", "qwen_score"])
def test_guard_cannot_silently_change_protected_original_score_fields(field, counter):
    case = invented()
    row, _ = trace(case, counter)
    proposal = deepcopy(row["proposal_response"])
    proposal["results"][0][field] = 17
    _, receipt = audit.guard(row["reference_response"], proposal, row["selection_plan"])
    assert not receipt["source_preserved"] and not receipt["accepted"]


@pytest.mark.parametrize("replacement", [True, 1])
def test_equal_boolean_or_integer_is_not_the_same_original_float_scalar(replacement, counter):
    case = invented()
    row, _ = trace(case, counter)
    proposal = deepcopy(row["proposal_response"])
    assert type(proposal["results"][0]["retrieval_score"]) is float
    proposal["results"][0]["retrieval_score"] = replacement
    _, receipt = audit.guard(row["reference_response"], proposal, row["selection_plan"])
    assert not receipt["source_preserved"] and not receipt["accepted"]


def test_fresh_auditor_process_has_no_production_or_numeric_stack():
    result = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0,'scripts'); import audit_complementary_context; "
                             "assert not any(n=='semantic_search' or n.startswith('semantic_search.') or n=='numpy' for n in sys.modules)"],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", ["semantic_search.complementary_context", "replay_complementary_context", "mlx.core", "numpy", "transformers"])
def test_candidate_model_and_producer_imports_are_blocked(name):
    with pytest.raises(RuntimeError):
        audit.RefuseProduction().find_spec(name)


@pytest.mark.parametrize("mutation", ["case", "parity", "negative_count", "plan", "none"])
def test_full_control_barrier_receipts_are_verified_not_just_summary_counts(mutation):
    receipts = [{"id": "invented", "source_view_sha256": "original"}]
    parity = {"exact_control_rows": 786, "new_variant_outcomes_computed": 0, "cases": deepcopy(receipts)}
    barrier = {"exact_control_rows": 786, "new_variant_rows_before_gate": 0,
               "referenced_negative_rows": 2948, "cases": deepcopy(receipts), "plan_sha256": "plan"}
    if mutation == "case":
        barrier["cases"][0]["source_view_sha256"] = "forged"
    elif mutation == "parity":
        parity["cases"] = []
    elif mutation == "negative_count":
        barrier["referenced_negative_rows"] -= 1
    elif mutation == "plan":
        barrier["plan_sha256"] = "different"
    if mutation == "none":
        audit.verify_control_gate(barrier, parity, receipts, "plan", 2948)
    else:
        with pytest.raises(ValueError):
            audit.verify_control_gate(barrier, parity, receipts, "plan", 2948)
