"""Adversarial synthetic checks of the independent allocation auditor."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("independent_allocation_audit", ROOT / "scripts/audit_allocation.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)
QUERY = "alpha beta gamma and delta epsilon zeta"


def pool():
    pairs, keys = [], {}
    for number in range(1, 10):
        text = "alpha beta gamma\n" if number < 9 else "delta epsilon zeta\n"
        item = {"id": str(number), "path": "src/example.py", "symbol": f"unit_{number}",
                "start_line": number, "end_line": number, "text": text,
                "content_hash": audit.sha(text.encode())}
        pairs.append({"snippet": item, "score": 1 / number})
        keys[item["id"]] = [item["path"], item["symbol"], number, number]
    origins = {pair["snippet"]["id"]: ["retrieval", None] for pair in pairs[:8]}
    return pairs, keys, origins


def planned(*, mode="aspect_fill", query=QUERY):
    ranked, keys, origins = pool()
    return audit.planner(ranked, ranked, ranked[:8], origins, keys, mode=mode, query=query)


@pytest.mark.parametrize("mode,expected", [
    ("aspect_fill", ["1", "2", "3", "4", "5", "9", "6", "7"]),
    ("aspect_balanced", ["1", "2", "3", "9", "4", "5", "6", "7"]),
])
def test_literal_independent_plan_and_exact_rational_rank_tie_break(mode, expected):
    selected, eligible, origins, receipt = planned(mode=mode)
    assert [p["snippet"]["id"] for p in eligible] == expected
    assert [p["snippet"]["id"] for p in selected] == list(map(str, range(1, 10)))
    assert receipt["picks"][0] == {"id": "9", "rank": 9,
        "novel_terms": ["delta", "epsilon", "zeta"], "weighted_gain": {"numerator": 3, "denominator": 1},
        "utility": {"numerator": 1, "denominator": 3}, "reason": "novel_query_terms"}
    assert receipt["accepted"] is True and origins["9"] == ["retrieval", None]


def test_actual_first_window_features_and_full_definition_df_are_separate():
    ranked, keys, origins = pool()
    later = deepcopy(ranked[0])
    later["snippet"].update(id="later", text="delta epsilon zeta\n")
    keys["later"] = keys["1"]
    ranked.append(later)
    result = audit.planner(ranked, ranked[:-1], ranked[:8], origins, keys, mode="aspect_fill", query=QUERY)
    receipt = result[3]
    assert receipt["term_df"]["delta"] == 2
    assert receipt["candidate_terms"]["1"] == ["alpha", "beta", "gamma"]
    assert receipt["candidate_terms"]["later"] == ["delta", "epsilon", "zeta"]
    assert all(p["id"] != "later" for p in receipt["picks"])
    assert receipt["picks"][0]["weighted_gain"] == {"numerator": 3, "denominator": 2}


def test_unknown_keys_remain_distinct_opaque_windows():
    ranked, keys, origins = pool()
    ranked[1]["snippet"]["symbol"] = ranked[0]["snippet"]["symbol"]
    keys.pop("1")
    keys.pop("2")
    assert len(audit.unique_definitions(ranked, keys)) == 9


def test_original_dependencies_and_fifth_owner_survive_balanced_three_anchors():
    ranked, keys, origins = pool()
    helper = deepcopy(ranked[0])
    helper["snippet"].update(id="helper", path="helpers.py", symbol="helper")
    helper["score"] = ranked[4]["score"]
    keys["helper"] = ["helpers.py", "helper", 1, 1]
    baseline = ranked[:5] + [helper] + ranked[5:7]
    origins["helper"] = ["dependency", "5"]
    selected, eligible, new_origins, receipt = audit.planner(ranked, ranked, baseline, origins, keys,
        mode="aspect_balanced", query=QUERY)
    assert receipt["protected_ids"] == ["1", "2", "3", "5", "helper"]
    assert receipt["protected_owner_ids"] == ["5"]
    assert new_origins["helper"] == ["dependency", "5"]
    assert next(p["score"] for p in eligible if p["snippet"]["id"] == "helper") == ranked[4]["score"]


@pytest.mark.parametrize("query", ["one implementation only", "alpha beta gamma and it", "cookie cookie cookie and weak weak weak"])
def test_nonactivating_queries_retain_exact_baseline_and_empty_receipt_fields(query):
    ranked, keys, origins = pool()
    actual = audit.planner(ranked, ranked, ranked[:8], origins, keys, mode="aspect_fill", query=query)
    assert actual[:3] == (ranked, ranked[:8], origins)
    assert actual[3]["fallback_reason"] == "no_literal_clauses"
    assert actual[3]["raw_ranks"] == actual[3]["term_df"] == actual[3]["candidate_terms"] == {}


def test_all_protected_slots_trigger_exact_baseline_no_free_slots():
    ranked, keys, origins = pool()
    result = audit.planner(ranked, ranked, ranked[:5], origins, keys,
                           mode="aspect_fill", query=QUERY, max_results=5)
    assert result[:3] == (ranked, ranked[:5], origins)
    assert result[3]["fallback_reason"] == "no_free_slots"
    assert result[3]["picks"] == result[3]["proposed_eligible_ids"] == []
    assert result[3]["query_terms"]


def test_no_positive_novelty_rejects_even_if_rank_fill_completed():
    ranked, keys, origins = pool()
    ranked[-1]["snippet"]["text"] = "alpha beta gamma\n"
    result = audit.planner(ranked, ranked, ranked[:8], origins, keys, mode="aspect_fill", query=QUERY)
    assert result[:3] == (ranked, ranked[:8], origins)
    assert result[3]["fallback_reason"] == "no_novel_query_terms"
    assert all(p["reason"] == "rank_fill" for p in result[3]["picks"])


def test_dropping_baseline_query_term_rejects_higher_novelty_proposal():
    ranked, keys, origins = pool()
    content = ["alpha beta gamma\n"] * 3 + ["delta\n", "epsilon\n", "zeta theta iota kappa\n", "delta\n", "delta\n", "delta\n"]
    for pair, text in zip(ranked, content):
        pair["snippet"]["text"] = text
    query = "alpha beta gamma and delta epsilon zeta theta iota kappa"
    result = audit.planner(ranked, ranked, ranked[:5], origins, keys,
                           mode="aspect_balanced", query=query, max_results=5)
    assert result[:3] == (ranked, ranked[:5], origins)
    assert result[3]["fallback_reason"] == "query_terms_not_preserved"
    assert set(result[3]["baseline_query_terms"]) - set(result[3]["proposed_query_terms"]) == {"delta"}


def test_normalization_matches_declared_existing_literal_rules():
    assert audit.words("Where are registryEntries and registries, HTTPRetryPolicies? class Class") == {
        "registry", "entry", "http", "retry", "policy", "class"}
    assert audit.literal_clauses("alpha beta gamma, delta epsilon zeta then red green blue while gold silver bronze and one two three") == [
        "alpha beta gamma", "delta epsilon zeta", "red green blue", "gold silver bronze"]


def planning_fixture():
    ranked, keys, origins = pool()
    selected, eligible, new_origins, receipt = planned()
    baseline = {"stages": {"ranked": ranked, "selected": ranked, "eligible": ranked[:8], "origins": origins}}
    row = {"selector": "aspect_fill", "stages": {"ranked": ranked, "selected": selected,
        "eligible": eligible, "origins": new_origins}, "response": {"query": QUERY}, "allocation_receipt": receipt}
    return row, baseline, {"definition_keys": keys}


@pytest.mark.parametrize("mutate", [
    lambda r: r["allocation_receipt"]["raw_ranks"].update({"9": 8}),
    lambda r: r["allocation_receipt"]["term_df"].update(delta=2),
    lambda r: r["allocation_receipt"]["picks"][0]["utility"].update(numerator=2),
    lambda r: r["allocation_receipt"]["picks"][0].update(reason="rank_fill"),
    lambda r: r["allocation_receipt"].update(accepted=False),
    lambda r: r["allocation_receipt"]["protected_anchor_ids"].pop(),
    lambda r: r["allocation_receipt"]["candidate_terms"]["9"].clear(),
    lambda r: r["allocation_receipt"]["proposed_eligible_ids"].reverse(),
])
def test_forged_planner_receipts_rejected(mutate):
    row, baseline, fixture = planning_fixture()
    mutate(row)
    with pytest.raises(ValueError, match="deterministic allocation receipt"):
        audit.verify_planning(row, baseline, fixture)


@pytest.mark.parametrize("mutate", [
    lambda r: r["stages"]["eligible"][5].update(score=0.123),
    lambda r: r["stages"]["eligible"].reverse(),
    lambda r: r["stages"]["origins"].update({"9": ["dependency", "1"]}),
])
def test_forged_actual_stage_scores_order_or_provenance_rejected(mutate):
    row, baseline, fixture = planning_fixture()
    # Independently deep-copy actual stages so a forged row cannot alter inputs.
    row = deepcopy(row)
    mutate(row)
    with pytest.raises(ValueError, match="stage/order/score/provenance"):
        audit.verify_planning(row, baseline, fixture)


def test_input_representative_dedup_cannot_be_substituted():
    ranked, keys, origins = pool()
    with pytest.raises(ValueError, match="canonical representative"):
        audit.planner(ranked, ranked[:-1], ranked[:8], origins, keys, mode="aspect_fill", query=QUERY)


def test_streamed_immutable_control_hash_binds_line_complete_record_and_response(tmp_path):
    raw = {"subset": "s", "dataset": "d", "query_id": "q", "condition": "dense", "selector": "legacy",
        "kind": "present", "multi_file": False, "evidence_groups": [], "evidence_ranks": [], "stages": {},
        "response": {"query": "q", "results": [], "total_source_chars": 0, "budget_exhausted": False}}
    raw["compact_payload"] = audit.win.compact_payload(raw["response"])
    raw["compact_sha256"] = audit.sha(raw["compact_payload"].encode())
    path = tmp_path / "old.jsonl.gz"
    with gzip.open(path, "wt") as stream:
        stream.write(json.dumps({"selector": "unrelated"}) + "\n")
        stream.write(json.dumps(raw) + "\n")
    controls = audit.read_controls(path, expected_total=2, expected_controls=1)
    reference = next(iter(controls.values()))["reference"]
    assert reference["line_number"] == 2 and reference["record_sha256"] == audit.sha(audit.canonical(raw))
    assert reference["response_sha256"] == audit.sha(audit.canonical(raw["response"]))
    with pytest.raises(ValueError, match="Complete immutable"):
        audit.read_controls(path, expected_total=3, expected_controls=1)


@pytest.mark.parametrize("field,value", [("line_number", 99), ("record_sha256", "bad"),
                                         ("response_sha256", "bad"), ("compact_sha256", "bad")])
def test_control_reference_forgery_rejected(field, value):
    identity = {"subset": "s", "dataset": "d", "query_id": "q", "condition": "dense"}
    references = {name: {"line_number": i, "record_sha256": "record", "response_sha256": "response", "compact_sha256": "compact"}
                  for i, name in enumerate(audit.CONDITIONS[:2], 1)}
    controls = {(name, *audit.win.case_key(identity)): {"reference": reference} for name, reference in references.items()}
    row = {**identity, "control_references": deepcopy(references)}
    row["control_references"]["legacy"][field] = value
    with pytest.raises(ValueError, match="control record references"):
        audit.verify_control_references(row, controls)


def summary_row(query, name, condition, lines, *, multi=True):
    groups = [[{"path": "a.py", "evidence_lines": [number, number]}] for number in (1, 2, 3)]
    results = [{"id": str(number), "path": "a.py", "start_line": number, "end_line": number,
                "source": "x\n", "origin": "retrieval", "related_to": None} for number in lines]
    response = {"results": results, "total_source_chars": 2 * len(results), "match_status": "candidates",
                "token_usage": {"tokens": 30, "truncated": False}}
    return {"subset": "synthetic", "dataset": "example", "query_id": query, "condition": condition,
        "selector": name, "kind": "present", "multi_file": multi, "evidence_groups": groups,
        "evidence_ranks": audit.win.evidence_ranks(results, groups), "response": response}


def attach_comparisons(rows):
    controls = {(row["selector"], *audit.win.case_key(row)): row for row in rows if row["selector"] in audit.CONDITIONS[:2]}
    for row in rows:
        row["comparisons"] = {name: audit.win.comparison(controls[(name, *audit.win.case_key(row))]["response"],
                             row["response"], row["evidence_groups"]) for name in audit.CONDITIONS[:2]}


def test_summary_counts_reused_conditions_as_one_new_complete_unit():
    rows = [summary_row("gain", name, condition, [1, 2] if name in audit.CONDITIONS[:2] else [1, 2, 3])
            for condition in ("dense", "hybrid") for name in audit.CONDITIONS]
    attach_comparisons(rows)
    value = audit.summary(rows, {"observed_rows": 2, "unique_queries": 1})
    for name in audit.CANDIDATES:
        verdict = value["qualification"][name]
        assert verdict["qualified"] is True
        assert verdict["gained_additional_distinct_complete_queries"] == 1
        assert len(value["all_changes"][name]["coverage_control"]["complete_gains"]) == 2


def test_partial_query_group_loss_rejects_candidate_even_with_complete_gain():
    rows = [summary_row("gain", name, "dense", [1, 2] if name in audit.CONDITIONS[:2] else [1, 2, 3])
            for name in audit.CONDITIONS]
    rows += [summary_row("still_incomplete", name, "dense", [1] if name in audit.CONDITIONS[:2] else [2])
             for name in audit.CONDITIONS]
    attach_comparisons(rows)
    value = audit.summary(rows, {"observed_rows": 2, "unique_queries": 2})
    for name in audit.CANDIDATES:
        verdict = value["qualification"][name]
        assert verdict["qualified"] is False
        for control in audit.CONDITIONS[:2]:
            assert verdict["against_controls"][control] == {
                "lost_groups": 1, "lost_complete_rows": 0, "lost_single_file_first_hits": 0}


def test_previously_known_coverage_gain_does_not_count_as_new_candidate_gain():
    rows = [summary_row("old_gain", name, "dense", [1, 2] if name == "legacy" else [1, 2, 3])
            for name in audit.CONDITIONS]
    attach_comparisons(rows)
    value = audit.summary(rows, {"observed_rows": 1, "unique_queries": 1})
    for name in audit.CANDIDATES:
        assert value["all_changes"][name]["legacy"]["unique_complete_gains"] == ["example/old_gain"]
        assert value["qualification"][name]["gained_additional_distinct_complete_queries"] == 0
        assert value["qualification"][name]["qualified"] is False


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.STDOUT, text=True).strip()


def committed_fixture(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Synthetic audit test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    output = tmp_path / "results/example"
    output.mkdir(parents=True)
    code = tmp_path / "producer.py"
    code.write_text("raise AssertionError('Historical code must never execute')\n")
    for name in ("frozen-plan.json", "baseline-gate.json", "prior-coverage-gate.json", "source-fixtures.json",
                 "control-references.json", "historical-provenance.json"):
        (output / name).write_text("{}\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "Commit synthetic freeze as data")
    commit = git(tmp_path, "rev-parse", "HEAD")
    stamp = int(git(tmp_path, "show", "-s", "--format=%ct", commit))
    plan = {"producer_sha256": {"producer.py": audit.file_sha(code)}, "input_sha256": {},
            "historical_parent": {"artifacts": {}}}
    start = {"created_at_utc": (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat(), "execution_commit": commit,
             "prospective_freeze": {"commit": commit, "committed_at_unix": stamp, "bound_blob_count": 7}}
    return output, plan, start


def test_new_freeze_uses_committed_bytes_without_executing_any_code(tmp_path):
    output, plan, start = committed_fixture(tmp_path)
    chain, execution = audit.new_freeze(tmp_path, output, plan, start)
    assert execution == chain["commit"] == start["execution_commit"]


def test_changed_source_cannot_pass_new_freeze(tmp_path):
    output, plan, start = committed_fixture(tmp_path)
    (tmp_path / "producer.py").write_text("print('substituted')\n")
    with pytest.raises(ValueError, match="Prospective source/input"):
        audit.new_freeze(tmp_path, output, plan, start)


def test_new_execution_must_descend_from_new_freeze(tmp_path):
    output, plan, start = committed_fixture(tmp_path)
    git(tmp_path, "checkout", "--orphan", "unrelated")
    git(tmp_path, "rm", "-rf", ".")
    (tmp_path / "unrelated.txt").write_text("unrelated\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "Unrelated history")
    unrelated = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "checkout", "--detach", start["execution_commit"])
    start["execution_commit"] = unrelated
    with pytest.raises(subprocess.CalledProcessError):
        audit.new_freeze(tmp_path, output, plan, start)


def test_clean_independent_process_imports_no_producer_or_numerical_runtime():
    command = """import importlib.util,sys
from pathlib import Path
p=Path('scripts/audit_allocation.py').resolve()
s=importlib.util.spec_from_file_location('independent',p); m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
assert not [n for n in sys.modules if any(n==p or n.startswith(p+'.') for p in m.FORBIDDEN)]
print('independent CPU imports only')
"""
    result = subprocess.run([sys.executable, "-c", command], cwd=ROOT, check=True, capture_output=True, text=True)
    assert result.stdout.strip() == "independent CPU imports only"
