"""Independent synthetic adversarial checks; no observed outcome runs."""
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
SPEC = importlib.util.spec_from_file_location("independent_source_allocation_audit", ROOT / "scripts/audit_source_allocation.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)
QUERY = "alpha beta gamma and delta epsilon zeta"


def pool(size=11):
    pairs, keys = [], {}
    for number in range(1, size + 1):
        text = f"def unit_{number}(): return {number}\n"
        item = {"id": str(number), "path": f"unit_{number}.py", "symbol": f"unit_{number}",
                "start_line": 1, "end_line": 1, "text": text, "content_hash": audit.sha(text.encode())}
        pairs.append({"snippet": item, "score": 1 / number})
        keys[item["id"]] = [item["path"], item["symbol"], 1, 1]
    origins = {str(number): ["retrieval", None] for number in range(1, 9)}
    return pairs, keys, origins


def label(pair):
    return tuple(pair["snippet"][field] for field in ("path", "symbol"))


def plan(*, mode="source_graph", edges=None, query=QUERY):
    pairs, keys, origins = pool()
    if edges is None:
        edges = {label(pairs[0]): [label(pairs[8]), label(pairs[9]), label(pairs[10])]}
    return audit.planner(pairs, pairs, pairs[:8], origins, keys, edges, [], mode=mode, query=query)


@pytest.mark.parametrize("mode", audit.CANDIDATES)
def test_plan_uses_raw_order_maximum_two_neighbors_and_evicts_last_unprotected(mode):
    selected, eligible, origins, receipt = plan(mode=mode)
    assert [pair["snippet"]["id"] for pair in selected] == list(map(str, range(1, 12)))
    assert [pair["snippet"]["id"] for pair in eligible] == ["1", "2", "3", "4", "5", "9", "10", "6"]
    assert receipt["available_candidate_ids"] == ["9", "10", "11"]
    assert receipt["added_ids"] == ["9", "10"] and receipt["evicted_ids"] == ["7", "8"]
    assert receipt["accepted"] is True and receipt["fallback_reason"] is None
    assert origins == pool()[2]


def test_both_modes_have_identical_prepacking_proposal_despite_guard_scope():
    left, right = plan(), plan(mode="source_graph_all")
    assert left[:3] == right[:3]
    lhs, rhs = deepcopy(left[3]), deepcopy(right[3])
    lhs.pop("mode"); rhs.pop("mode")
    assert lhs == rhs


def test_reverse_call_neighbor_protects_baseline_caller_and_adds_new_caller():
    pairs, keys, origins = pool()
    edges = {label(pairs[7]): [label(pairs[0])], label(pairs[8]): [label(pairs[0])]}
    _, eligible, _, receipt = audit.planner(pairs, pairs, pairs[:8], origins, keys, edges, [],
                                           mode="source_graph", query=QUERY)
    assert receipt["protected_baseline_ids"] == ["1", "2", "3", "4", "5", "8"]
    assert [pair["snippet"]["id"] for pair in eligible] == ["1", "2", "3", "4", "5", "8", "9", "6"]
    assert all(w["direction"] == "caller" for w in receipt["graph_witnesses"])


@pytest.mark.parametrize("mutation", ["unknown", "malformed", "ambiguous", "explicit_ambiguous"])
def test_unresolved_or_ambiguous_canonical_labels_cannot_supply_edges(mutation):
    pairs, keys, origins = pool()
    edges = {label(pairs[0]): [label(pairs[8])]}
    ambiguous = []
    if mutation == "unknown":
        keys.pop("9")
    elif mutation == "malformed":
        keys["9"] = [*keys["9"][:2], True, 1]
    elif mutation == "ambiguous":
        keys["shadow"] = [*keys["9"][:2], 2, 2]
    else:
        ambiguous = [label(pairs[8])]
    _, eligible, _, receipt = audit.planner(pairs, pairs, pairs[:8], origins, keys, edges, ambiguous,
                                           mode="source_graph", query=QUERY)
    assert eligible == pairs[:8]
    assert receipt["fallback_reason"] == "no_graph_neighbors"


def test_unknown_anchor_key_cannot_invent_graph_neighbor():
    pairs, keys, origins = pool()
    keys.pop("1")
    edges = {label(pairs[0]): [label(pairs[8])]}
    _, eligible, _, receipt = audit.planner(pairs, pairs, pairs[:8], origins, keys, edges, [],
                                           mode="source_graph", query=QUERY)
    assert eligible == pairs[:8] and receipt["graph_witnesses"] == []


def test_dependencies_and_owners_remain_in_original_protected_order():
    pairs, keys, origins = pool()
    origins["7"] = ["dependency", "6"]
    edges = {label(pairs[0]): [label(pairs[8])]}
    _, eligible, result_origins, receipt = audit.planner(pairs, pairs, pairs[:8], origins, keys, edges, [],
                                           mode="source_graph", query=QUERY)
    assert receipt["protected_baseline_ids"] == list(map(str, range(1, 8)))
    assert receipt["dependency_owners"] == {"7": "6"}
    assert [pair["snippet"]["id"] for pair in eligible] == list(map(str, range(1, 8))) + ["9"]
    assert result_origins["7"] == ["dependency", "6"]


@pytest.mark.parametrize("query", ["one implementation only", "alpha beta gamma and it"])
def test_inactive_literal_query_keeps_exact_reference_eligible(query):
    selected, eligible, origins, receipt = plan(query=query)
    assert eligible == selected[:8]
    assert receipt["fallback_reason"] == "no_literal_clauses"
    assert receipt["graph_witnesses"] == [] and receipt["activated"] is False


def test_no_new_neighbor_keeps_baseline_even_when_known_edges_exist():
    pairs, _, _ = pool()
    _, eligible, _, receipt = plan(edges={label(pairs[0]): [label(pairs[1])]})
    assert eligible == pairs[:8] and receipt["fallback_reason"] == "no_new_graph_neighbor"


def test_planning_receipt_or_scalar_forgery_is_rejected():
    pairs, keys, origins = pool()
    edges = {label(pairs[0]): [label(pairs[8])]}
    selected, eligible, result_origins, receipt = audit.planner(pairs, pairs, pairs[:8], origins, keys,
        edges, [], mode="source_graph", query=QUERY)
    baseline = {"stages": {"ranked": pairs, "selected": pairs, "eligible": pairs[:8], "origins": origins}}
    row = {"selector": "source_graph", "response": {"query": QUERY}, "stages": {"ranked": pairs,
        "selected": selected, "eligible": eligible, "origins": result_origins, "ambiguous_symbols": []},
        "source_allocation_receipt": receipt, "selection_plan": deepcopy(receipt)}
    fixture = {"definition_keys": keys, "graph_edges": edges}
    assert audit.verify_planning(row, baseline, fixture) == receipt
    row["source_allocation_receipt"]["graph_witnesses"][0]["direction"] = "caller"
    with pytest.raises(ValueError, match="allocation receipt"):
        audit.verify_planning(row, baseline, fixture)


def source_row(identifier="1", *, path="a.py", start=1, text="alpha\nbeta\n", origin="retrieval", owner=None):
    return {"id": identifier, "path": path, "start_line": start,
            "end_line": start + len(audit.win.physical_lines(text)) - 1, "source": text,
            "source_hash": audit.sha(text.encode()), "origin": origin, "related_to": owner}


def guard_plan(mode="source_graph", *, active=True):
    return {"schema": "source_graph_allocation_v1", "mode": mode, "accepted": active,
            "protected_baseline_ids": ["1"], "neighbor_keys": [["backfill.py", "caller", 1, 1]],
            "canonical_keys": {"later": ["backfill.py", "caller", 1, 1]}, "protected_windows": []}


def response(*rows):
    return {"results": list(rows)}


def test_postpack_guard_rejects_preserved_id_with_shortened_actual_span():
    original = source_row()
    short = source_row(text="alpha\n")
    reference, proposal = response(original), response(short)
    chosen, receipt = audit.guard(reference, proposal, guard_plan())
    assert chosen == reference
    assert receipt["accepted"] is False and receipt["source_preserved"] is False
    assert receipt["fallback_reason"] == "protected_source_not_preserved"


def test_graph_neighbor_backfill_row_and_dependency_rows_are_protected():
    original, caller = source_row(), source_row("later", path="backfill.py", text="wire\n")
    dep = source_row("dep", path="dep.py", text="callee\n", origin="dependency", owner="1")
    reference = response(original, caller, dep, source_row("unprotected", path="other.py"))
    assert [row["id"] for row in audit.protected_rows(reference, guard_plan())] == ["1", "later", "dep"]


def test_all_source_guard_protects_unrelated_original_rows_and_inactive_plan_does_too():
    reference = response(source_row(), source_row("unprotected", path="other.py"))
    proposal = response(source_row())
    assert audit.guard(reference, proposal, guard_plan())[1]["accepted"] is True
    assert audit.guard(reference, proposal, guard_plan("source_graph_all"))[0] == reference
    chosen, receipt = audit.guard(reference, reference, guard_plan(active=False))
    assert chosen == reference and receipt["source_preserved"] is True and receipt["accepted"] is False
    assert receipt["fallback_reason"] == "inactive_plan"


@pytest.mark.parametrize("change", ["source_hash", "origin", "owner", "provenance_presence", "provenance_value", "bytes"])
def test_guard_rejects_hash_origin_owner_overlay_or_literal_byte_forgery(change):
    old = source_row(origin="dependency", owner="parent")
    old["source_origin"], old["buffer_version"] = "buffer", 3
    current = deepcopy(old)
    if change == "source_hash": current["source_hash"] = "0" * 64
    elif change == "origin": current["origin"] = "retrieval"
    elif change == "owner": current["related_to"] = "another"
    elif change == "provenance_presence": current.pop("buffer_version")
    elif change == "provenance_value": current["buffer_version"] = 4
    else:
        current["source"] = "alpha\nwrong\n"; current["source_hash"] = audit.sha(current["source"].encode())
    assert audit.source_covers(current, old) is False


def test_guard_accepts_verified_single_enclosing_literal_source_and_unicode_crlf():
    old = source_row(start=2, text="β\r\nγ\r\n")
    outer = source_row("outer", text="α\r\nβ\r\nγ\r\nδ\r\n")
    assert audit.source_covers(outer, old)
    one, two = source_row("one", start=2, text="β\r\n"), source_row("two", start=3, text="γ\r\n")
    assert audit.guard(response(old), response(one, two), guard_plan())[1]["accepted"] is False


def test_shared_graph_extractor_frozen_edges_are_reconstructed_from_literal_source():
    text = "def callee():\n    return 1\n\ndef caller():\n    return callee()\n"
    inv = {}
    for symbol, start, end in (("callee", 1, 2), ("caller", 4, 5)):
        body = "".join(text.splitlines(keepends=True)[start-1:end])
        inv[symbol] = {"id": symbol, "path": "a.py", "symbol": symbol, "start_line": start,
                       "end_line": end, "text": body, "content_hash": audit.sha(body.encode())}
    records = [{"caller": ["a.py", "caller"], "targets": [["a.py", "callee"]]}]
    assert audit.verify_graph_records(records, {"a.py": text}, inv) == {("a.py", "caller"): [("a.py", "callee")]}
    with pytest.raises(ValueError, match="graph edge inventory"):
        audit.verify_graph_records([], {"a.py": text}, inv)


def test_shared_graph_does_not_invent_factory_or_parameter_receivers():
    text = "def callee():\n    return 1\n\ndef caller(receiver):\n    return receiver.callee()\n"
    inv = {name: {"id": name, "path": "a.py", "symbol": name} for name in ("callee", "caller")}
    assert audit.graph_records({"a.py": text}, inv) == []


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
    for name in audit.FROZEN_FILES:
        (output / name).write_text("{}\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "Commit synthetic freeze as data")
    commit = git(tmp_path, "rev-parse", "HEAD")
    stamp = int(git(tmp_path, "show", "-s", "--format=%ct", commit))
    plan = {"producer_sha256": {"producer.py": audit.file_sha(code)}, "input_sha256": {},
            "historical_parent": {"artifacts": {}}, "negative_parent": {"artifacts": {}}}
    start = {"created_at_utc": (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat(), "execution_commit": commit,
             "prospective_freeze": {"commit": commit, "committed_at_unix": stamp, "bound_blob_count": len(audit.FROZEN_FILES) + 1}}
    return output, plan, start


def test_new_strict_freeze_reuses_committed_bytes_without_executing_source(tmp_path):
    output, plan, start = committed_fixture(tmp_path)
    assert audit.new_freeze(tmp_path, output, plan, start)[1] == start["execution_commit"]
    (tmp_path / "producer.py").write_text("print('changed')\n")
    with pytest.raises(ValueError, match="Prospective source/input"):
        audit.new_freeze(tmp_path, output, plan, start)


def test_new_execution_must_descend_from_its_own_freeze(tmp_path):
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


def test_clean_process_allows_only_documented_source_graph_modules():
    command = """import importlib.util,sys
from pathlib import Path
p=Path('scripts/audit_source_allocation.py').resolve()
s=importlib.util.spec_from_file_location('independent',p); m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
f=m.RefuseProduction(); sys.meta_path.insert(0,f)
m.graph_records({'a.py':'def caller():\\n    return 1\\n'}, {'a':{'path':'a.py','symbol':'caller'}})
for forbidden in ('semantic_search.source_allocation','semantic_search.backend','semantic_search.selection','numpy','replay_source_allocation'):
 try: __import__(forbidden)
 except RuntimeError: pass
 else: raise AssertionError(forbidden)
assert not [n for n in sys.modules if m.forbidden_module(n)]
print('shared source graph only; independent planning/guards')
"""
    result = subprocess.run([sys.executable, "-c", command], cwd=ROOT, check=True, capture_output=True, text=True,
                            env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")})
    assert result.stdout.strip() == "shared source graph only; independent planning/guards"


def test_postpack_dependency_owner_must_really_be_delivered_for_acceptance():
    dep = source_row("dependency", origin="dependency", owner="owner")
    reference = response(dep)
    chosen, receipt = audit.guard(reference, reference, guard_plan("source_graph_all"))
    assert chosen == reference and receipt["accepted"] is False
    assert receipt["fallback_reason"] == "protected_source_not_preserved"
    with_owner = response(dep, source_row("owner", path="owner.py"))
    assert audit.guard(reference, with_owner, guard_plan("source_graph_all"))[1]["accepted"] is True


def test_structural_guard_protects_full_actual_container_when_anchor_id_was_coalesced():
    container = source_row("container", text="before\nanchor\nafter\n")
    plan = guard_plan()
    plan["protected_windows"] = [{"id": "anchor", "path": "a.py", "start_line": 2, "end_line": 2,
                                  "content_hash": audit.sha(b"anchor\n")}]
    protected = audit.protected_rows(response(container), plan)
    assert protected == [container]
    replacement = source_row("replacement", start=2, text="anchor\n")
    assert audit.guard(response(container), response(replacement), plan)[1]["accepted"] is False


@pytest.mark.parametrize("change", ["related_to_presence", "retrieval_owner", "content_hash"])
def test_guard_strict_metadata_does_not_admit_same_id_spoofing(change):
    old = source_row()
    old["content_hash"] = old["source_hash"]
    now = deepcopy(old)
    if change == "related_to_presence": now.pop("related_to")
    elif change == "retrieval_owner": now["related_to"] = "invented"
    else: now["content_hash"] = "0" * 64
    assert audit.source_covers(now, old) is False


def summary_row(query, selector, condition, lines, *, multi=True):
    groups = [[{"path": "a.py", "evidence_lines": [number, number]}] for number in (1, 2, 3)]
    rows = [source_row(str(number), start=number, text="x\n") for number in lines]
    result = {"results": rows, "total_source_chars": 2 * len(rows), "match_status": "candidates",
              "token_usage": {"tokens": 30, "truncated": False}}
    return {"subset": "synthetic", "dataset": "example", "query_id": query, "condition": condition,
        "selector": selector, "kind": "present", "multi_file": multi, "evidence_groups": groups,
        "evidence_ranks": audit.win.evidence_ranks(rows, groups), "response": result}


def comparisons(rows):
    controls = {(row["selector"], *audit.win.case_key(row)): row for row in rows if row["selector"] in audit.CONTROLS}
    for row in rows:
        row["comparisons"] = {name: audit.win.comparison(controls[(name, *audit.win.case_key(row))]["response"],
            row["response"], row["evidence_groups"]) for name in audit.CONTROLS}


def test_summary_keeps_negatives_read_only_and_counts_repeated_gain_as_one_unit():
    rows = [summary_row("gain", name, order, [1, 2, 3] if name in audit.CANDIDATES else [1, 2])
            for order in ("dense", "hybrid") for name in audit.CONDITIONS]
    comparisons(rows)
    result = audit.summary(rows, {"observed_rows": 2, "unique_queries": 1})
    assert result["stored_new_candidate_rows"] == result["referenced_negative_rows"] == result["referenced_control_rows"] == 4
    assert set(result["qualification"]) == set(audit.CANDIDATES)
    for name in audit.CANDIDATES:
        assert result["qualification"][name]["qualified"] is True
        assert result["qualification"][name]["gained_additional_distinct_complete_queries"] == 1
        assert len(result["all_changes"][name]["coverage_control"]["complete_gains"]) == 2


def test_any_partial_query_loss_rejects_an_additional_complete_gain_against_both_controls():
    rows = [summary_row("gain", name, "dense", [1, 2, 3] if name in audit.CANDIDATES else [1, 2])
            for name in audit.CONDITIONS]
    rows += [summary_row("still_incomplete", name, "dense", [2] if name in audit.CANDIDATES else [1])
             for name in audit.CONDITIONS]
    comparisons(rows)
    result = audit.summary(rows, {"observed_rows": 2, "unique_queries": 2})
    for name in audit.CANDIDATES:
        assert result["qualification"][name]["qualified"] is False
        assert result["qualification"][name]["gained_additional_distinct_complete_queries"] == 1
        for control in audit.CONTROLS:
            assert result["qualification"][name]["against_controls"][control]["lost_groups"] == 1


def test_old_coverage_control_gain_cannot_qualify_as_new():
    rows = [summary_row("old", name, "dense", [1, 2] if name == "legacy" else [1, 2, 3])
            for name in audit.CONDITIONS]
    comparisons(rows)
    result = audit.summary(rows, {"observed_rows": 1, "unique_queries": 1})
    assert all(value["gained_additional_distinct_complete_queries"] == 0 and value["qualified"] is False
               for value in result["qualification"].values())


@pytest.mark.parametrize("mutation", ["plan", "receipt", "guard_count", "planner_count", "reference_count", "final", "reference"])
def test_guard_capture_forgery_cannot_change_actual_fallback_or_once_counts(mutation):
    reference = response(source_row())
    proposal = response(source_row(text="alpha\n"))
    plan = guard_plan()
    chosen, receipt = audit.guard(reference, proposal, plan)
    row = {"reference_response": reference, "proposal_response": proposal, "selection_plan": plan,
           "guard_plan": deepcopy(plan), "source_guard_receipt": receipt, "response": chosen,
           "planner_calls": 1, "guard_calls": 1, "reference_calls": 1}
    assert audit.verify_guard(row, {"response": reference}) == receipt
    if mutation == "plan": row["guard_plan"]["mode"] = "source_graph_all"
    elif mutation == "receipt": row["source_guard_receipt"]["protected_rows"][0]["end_line"] = 1
    elif mutation == "guard_count": row["guard_calls"] = 2
    elif mutation == "planner_count": row["planner_calls"] = True
    elif mutation == "reference_count": row["reference_calls"] = 0
    elif mutation == "final": row["response"] = proposal
    else: row["reference_response"] = proposal
    with pytest.raises(ValueError):
        audit.verify_guard(row, {"response": reference})


@pytest.fixture(scope="module")
def counter():
    return audit.win.offline_encoding(ROOT / ".cache/tokenizers")


def packing_fixture(counter):
    pairs, keys, origins = pool(9)
    parent = {"id": "parent", "path": "a.py", "symbol": "parent", "start_line": 1, "end_line": 3,
              "text": "first\nchild\nlast\n", "content_hash": audit.sha(b"first\nchild\nlast\n")}
    child = {"id": "child", "path": "a.py", "symbol": "parent.child", "start_line": 2, "end_line": 2,
             "text": "child\n", "content_hash": audit.sha(b"child\n")}
    selected = [{"snippet": parent, "score": 1.0}, {"snippet": child, "score": .9}] + pairs[:7]
    template = {"query": QUERY, "scope": {"source_root": "."}, "match_status": "candidates", "results": []}
    origins = {pair["snippet"]["id"]: ["retrieval", None] for pair in selected[:8]}
    before = audit.ordinary_pack(template, selected[:8], origins, counter)
    candidates = [audit.ordinary_pack(template, [pair], {}, counter)["results"][0] for pair in selected]
    proposal = audit.coalesce_pack(before, candidates, counter)
    row = {"stages": {"eligible": selected[:8], "origins": origins},
           "pre_postprocess_response": before, "backfill_candidates": candidates, "proposal_response": proposal}
    return row, {"original": template}


def test_independent_pack_reconstructs_whole_character_prefixes_and_backfill_debug(counter):
    row, fixture = packing_fixture(counter)
    audit.verify_packing(row, fixture, counter)
    result = row["proposal_response"]
    assert result["_selection_backfill"]["coalesced_ids"] == ["child"]
    assert result["_selection_backfill"]["appended_ids"] == ["7"]
    assert [item["id"] for item in result["results"]] == ["parent", "1", "2", "3", "4", "5", "6", "7"]
    pair = row["stages"]["eligible"][0]
    limited = audit.ordinary_pack(fixture["original"], [pair], {}, counter, chars=7)
    assert limited["results"][0]["source"] == "first\n"
    assert limited["results"][0]["end_line"] == 1 and limited["results"][0]["truncated"] is True


@pytest.mark.parametrize("forgery", ["early_trim", "eligible_omission", "backfill_order", "backfill_diagnostic"])
def test_independent_pack_rejects_legal_but_nondeterministic_packing_and_backfill(counter, forgery):
    row, fixture = packing_fixture(counter)
    if forgery == "early_trim":
        item = row["pre_postprocess_response"]["results"][0]
        item.update(source="first\n", end_line=1, source_hash=audit.sha(b"first\n"), truncated=True)
        row["pre_postprocess_response"]["total_source_chars"] = sum(len(r["source"]) for r in row["pre_postprocess_response"]["results"])
        audit.count_compact(row["pre_postprocess_response"], counter)
    elif forgery == "eligible_omission":
        row["pre_postprocess_response"]["results"].pop()
        row["pre_postprocess_response"]["total_source_chars"] = sum(len(r["source"]) for r in row["pre_postprocess_response"]["results"])
        audit.count_compact(row["pre_postprocess_response"], counter)
    elif forgery == "backfill_order":
        row["proposal_response"]["results"][-1], row["proposal_response"]["results"][-2] = row["proposal_response"]["results"][-2:]
    else:
        row["proposal_response"]["_selection_backfill"]["trials"] += 1
    with pytest.raises(ValueError, match="packing differs|backfill proposal differs"):
        audit.verify_packing(row, fixture, counter)


def test_independent_token_pass_clips_new_prefix_and_recounts_exact_envelope(counter):
    pairs, _, _ = pool(1)
    text = "".join(f"row {i} unique text value\n" for i in range(80))
    pairs[0]["snippet"].update(text=text, end_line=80, content_hash=audit.sha(text.encode()))
    template = {"query": QUERY, "scope": {"source_root": "."}, "match_status": "candidates", "results": []}
    clipped = audit.ordinary_pack(template, pairs, {}, counter, tokens=230)
    assert clipped["token_usage"]["tokens"] <= 230 and clipped["token_usage"]["truncated"] is True
    assert clipped["results"][0]["end_line"] < 80
    assert len(counter.encode(audit.win.compact_payload(clipped), disallowed_special=())) == clipped["token_usage"]["tokens"]
