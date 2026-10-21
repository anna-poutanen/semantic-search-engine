"""Adversarial checks of the independent window auditor, without producers."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("window_audit", ROOT / "scripts/audit_windows.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class ByteCounter:
    def encode(self, text, **kwargs):
        return text.encode()


def fixture():
    text = "one\ntwo\nthree\nfour\nfive\nsix\nseven\n"
    lines = audit.physical_lines(text)
    sources = {"a.py": text}
    def snippet(identifier, start, end):
        source = "".join(lines[start - 1:end])
        return {"id": identifier, "path": "a.py", "symbol": "f", "start_line": start,
                "end_line": end, "text": source, "content_hash": audit.sha(source.encode())}
    inventory = {"left": snippet("left", 1, 4), "right": snippet("right", 3, 7)}
    keys = {identifier: ["a.py", "f", 1, 7] for identifier in inventory}
    stage = [{"snippet": inventory[identifier], "score": score}
             for identifier, score in (("left", 0.9), ("right", 0.7))]
    ranked = audit.raw_ranked(stage, inventory)
    def member(item):
        return {name: item[name] for name in
                ("id", "path", "symbol", "start_line", "end_line", "content_hash")} | {
                "used_start_line": item["start_line"], "used_end_line": item["end_line"],
                "used_source_hash": item["content_hash"]}
    members = [member(inventory["left"]), member(inventory["right"])]
    assembly = {"kind": "connected_windows", "definition_key": keys["left"],
                "members": members, "score_anchor_id": "left", "anchor_rank": 1, "companion_rank": 2}
    composite = {"id": audit.composite_id(members, keys["left"], text, 1, 7), "path": "a.py", "symbol": "f",
                 "start_line": 1, "end_line": 7, "text": text,
                 "content_hash": audit.sha(text.encode()), "assembly": assembly}
    return sources, inventory, keys, ranked, composite


def row(item, score=0.9):
    result = {name: deepcopy(value) for name, value in item.items() if name != "text"}
    result.update(source=item["text"], source_hash=item["content_hash"],
                  window_end_line=item["end_line"], retrieval_score=score,
                  h2o_score=None, qwen_score=None, origin="retrieval", related_to=None, truncated=False)
    return result


def response(rows):
    result = {"query": "find f", "results": rows, "total_source_chars": sum(len(r["source"]) for r in rows),
              "budget_exhausted": False,
              "token_usage": {"encoding": "cl100k_base", "tokens": 0, "budget": 4096, "truncated": False}}
    for _ in range(10):
        result["token_usage"]["tokens"] = len(audit.compact_payload(result).encode())
    return result


def test_canonical_raw_source_and_composite_full_literal_union():
    sources, inventory, keys, ranked, composite = fixture()
    audit.verify_inventory(inventory, sources, keys)
    assert audit.verify_composite(composite, inventory, sources, keys, ranked, score=0.9) == composite
    final = response([row(composite)])
    assert audit.verify_response(final, inventory, sources, keys, ranked, ByteCounter()) == audit.compact_payload(final)


@pytest.mark.parametrize("mutate,reason", [
    (lambda c: c.update(id="not-indexed-but-fake"), "ephemeral"),
    (lambda c: c["assembly"]["members"].pop(), "exactly two"),
    (lambda c: c["assembly"]["members"][0].update(id="missing"), "canonical raw"),
    (lambda c: c["assembly"]["members"][0].update(content_hash="bad"), "original identity"),
    (lambda c: c["assembly"]["members"][0].update(used_start_line=0), "used interval"),
    (lambda c: c["assembly"]["members"][0].update(used_source_hash="bad"), "used source hash"),
    (lambda c: c.update(content_hash="bad"), "full bounds/hash"),
    (lambda c: c.update(id="context:connected_windows:" + "0" * 64), "identity digest"),
    (lambda c: c["assembly"].update(anchor_rank=2), "raw rank"),
    (lambda c: c["assembly"].update(score_anchor_id="missing"), "actual raw retrieved"),
    (lambda c: c["assembly"].update(invented=True), "Unknown composite"),
])
def test_forged_composite_receipts_rejected(mutate, reason):
    sources, inventory, keys, ranked, composite = fixture()
    mutate(composite)
    with pytest.raises(ValueError, match=reason):
        audit.verify_composite(composite, inventory, sources, keys, ranked, score=0.9)


def test_composite_requires_exact_canonical_js_definition_including_byte_offset():
    sources, inventory, keys, ranked, composite = fixture()
    keys["left"] = ["a.py", "f", 1, 7, 0]
    keys["right"] = ["a.py", "f", 1, 7, 1]
    composite["assembly"]["definition_key"] = keys["left"]
    with pytest.raises(ValueError, match="different canonical definitions"):
        audit.verify_composite(composite, inventory, sources, keys, ranked)


def test_composite_cannot_bridge_gap_even_with_truthful_member_hashes():
    sources, inventory, keys, ranked, composite = fixture()
    # The two individual saved members remain truthful, but lines 3 and 4 are absent.
    for identifier, low, high in (("left", 1, 2), ("right", 5, 7)):
        text = audit.literal(sources, "a.py", low, high)
        inventory[identifier].update(start_line=low, end_line=high, text=text, content_hash=audit.sha(text.encode()))
    for member in composite["assembly"]["members"]:
        full = inventory[member["id"]]
        member.update({name: full[name] for name in ("start_line", "end_line", "content_hash")})
        member.update(used_start_line=full["start_line"], used_end_line=full["end_line"], used_source_hash=full["content_hash"])
    with pytest.raises(ValueError, match="uncovered source gap"):
        audit.verify_composite(composite, inventory, sources, keys, ranked)


def test_same_member_id_requires_a_clipped_original_prefix_and_full_remainder():
    sources, inventory, keys, ranked, composite = fixture()
    full = inventory["left"]
    original = deepcopy(composite["assembly"]["members"][0])
    clipped = deepcopy(original)
    clipped.update(used_end_line=2, used_source_hash=audit.sha("one\ntwo\n".encode()))
    members = [clipped, original]
    composite.update(start_line=1, end_line=4, text=full["text"], content_hash=full["content_hash"])
    composite["assembly"].update(members=members, companion_rank=1)
    composite["id"] = audit.composite_id(members, keys["left"], full["text"], 1, 4)
    audit.verify_composite(composite, inventory, sources, keys, ranked)
    members[0] = deepcopy(original)
    composite["id"] = audit.composite_id(members, keys["left"], full["text"], 1, 4)
    with pytest.raises(ValueError, match="does not add any source|clipped-own-window"):
        audit.verify_composite(composite, inventory, sources, keys, ranked)


def test_inherited_score_is_not_a_new_model_score():
    sources, inventory, keys, ranked, composite = fixture()
    with pytest.raises(ValueError, match="inherited anchor score"):
        audit.verify_composite(composite, inventory, sources, keys, ranked, score=0.95)
    delivered = row(composite)
    delivered["qwen_score"] = 0.95
    with pytest.raises(ValueError, match="direct model"):
        audit.verify_composite(delivered, inventory, sources, keys, ranked)


def test_actual_composite_source_can_be_clipped_but_full_union_stays_auditable():
    sources, inventory, keys, ranked, composite = fixture()
    delivered = row(composite)
    delivered.update(end_line=5, source="one\ntwo\nthree\nfour\nfive\n", truncated=True)
    delivered["source_hash"] = audit.sha(delivered["source"].encode())
    final = response([delivered])
    audit.verify_response(final, inventory, sources, keys, ranked, ByteCounter())
    final["results"][0]["truncated"] = False
    with pytest.raises(ValueError, match="Full source/hash/truncation"):
        audit.verify_response(final, inventory, sources, keys, ranked, ByteCounter())


def test_exact_token_count_and_overlay_provenance_are_not_trusted():
    sources, inventory, keys, ranked, composite = fixture()
    final = response([row(composite)])
    final["token_usage"]["tokens"] += 1
    with pytest.raises(ValueError, match="Exact compact"):
        audit.verify_response(final, inventory, sources, keys, ranked, ByteCounter())
    final = response([row(composite)])
    with pytest.raises(ValueError, match="overlay provenance"):
        audit.verify_response(final, inventory, sources, keys, ranked, ByteCounter(),
                              raw_rows={"left": {"source_origin": "buffer", "buffer_version": 3, "buffer_hash": "saved"}})


def test_fresh_evidence_requires_qualified_symbol_and_exact_complete_bytes():
    group = [[{"path": "a.py", "symbol": "f", "evidence_lines": [2, 3],
               "evidence_sha256": audit.sha(b"two\nthree\n")}]]
    wrong = {"path": "a.py", "symbol": "other", "start_line": 1, "end_line": 4,
             "source": "one\ntwo\nthree\nfour\n"}
    assert audit.evidence_ranks([wrong], group) == [1]  # Preserve the historical scoring contract.
    assert audit.evidence_ranks([wrong], group, qualified=True) == [None]
    correct = dict(wrong, symbol="f")
    assert audit.evidence_ranks([correct], group, qualified=True) == [1]
    correct["source"] = "one\ntwo\nchanged\nfour\n"
    assert audit.evidence_ranks([correct], group, qualified=True) == [None]


def test_guarded_continuation_requires_byte_witness_and_only_truthful_owner_rewrites():
    sources, inventory, keys, ranked, composite = fixture()
    original = row(inventory["left"])
    extended = row(composite)
    extended["assembly"]["inherited_scores"] = {name: original.get(name) for name in
                                               ("retrieval_score", "h2o_score", "qwen_score")}
    dependency = row(inventory["right"], score=0.9)
    dependency.update(origin="dependency", related_to="left")
    before = response([original, dependency])
    after = response([extended, dict(dependency, related_to=extended["id"])])
    after["_window_assembly"] = {"old_to_new_ids": {"left": extended["id"]}, "coverage_preserved": True,
                                 "trials": 1, "trial_limit": 8,
                                 "applied": [{"anchor_id": "left", "id": extended["id"]}]}
    audit.verify_guarded_preservation(before, after)
    bad = deepcopy(after)
    bad["results"][1]["related_to"] = "left"
    with pytest.raises(ValueError, match="strict owner rewrite"):
        audit.verify_guarded_preservation(before, bad)
    bad = deepcopy(after)
    bad["results"][0]["source"] = "wrong\n" + bad["results"][0]["source"][4:]
    with pytest.raises(ValueError, match="old actual source bytes"):
        audit.verify_guarded_preservation(before, bad)


def test_clean_subprocess_guard_blocks_producer_and_numerical_imports():
    code = """import importlib.util,sys
from pathlib import Path
root=Path(sys.argv[1]);spec=importlib.util.spec_from_file_location('audit',root/'scripts/audit_windows.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
sys.meta_path.insert(0,mod.RefuseProduction())
for name in ('semantic_search.selection','replay_windows','benchmark_windows','numpy','mlx'):
 try: __import__(name)
 except RuntimeError: pass
 else: raise AssertionError(name)
"""
    subprocess.run([sys.executable, "-c", code, str(ROOT)], check=True)


@pytest.mark.parametrize("accepted", [False, True])
def test_connected_guard_recomputes_actual_proposal_decision_and_exact_fallback(accepted):
    _, inventory, _, _, composite = fixture()
    control = response([row(inventory["left"])])
    proposal = response([row(composite) if accepted else row(inventory["right"], score=0.7)])
    final = deepcopy(proposal if accepted else control)
    final["_window_guard"] = {"accepted": accepted,
        "fallback_reason": None if accepted else "coverage_control_source_not_preserved",
        "reference_compact_sha256": audit.sha(audit.compact_payload(control).encode()),
        "proposed_composites": int(accepted), "accepted_composites": int(accepted)}
    recorded = {"reference_response": deepcopy(control), "proposal_response": proposal,
                "response": final, "guard_calls": 1}
    audit.verify_guarded_union(recorded, control)
    recorded["response"]["_window_guard"]["accepted"] = not accepted
    with pytest.raises(ValueError, match="decision/necessity"):
        audit.verify_guarded_union(recorded, control)


def development_rows(*, additional=False, incomplete_loss=False):
    _, inventory, _, _, _ = fixture()
    target = {"path": "a.py", "evidence_lines": [1, 2]}
    groups = [[target]]
    rows = []
    for query in (["old_gain", "new_gain"] if additional else ["old_gain"]):
        conditions = {}
        for name in audit.CONDITIONS:
            complete = name != "legacy" if query == "old_gain" else name == "guarded_connected_union"
            conditions[name] = response([row(inventory["left"])] if complete else [])
        for name in audit.CONDITIONS:
            rows.append({"selector": name, "subset": "observed", "dataset": "example", "query_id": query,
                "condition": "hybrid", "kind": "present", "multi_file": True, "evidence_groups": groups,
                "evidence_ranks": audit.evidence_ranks(conditions[name]["results"], groups),
                "response": conditions[name],
                "comparisons": {control: audit.comparison(conditions[control], conditions[name], groups)
                                for control in ("legacy", "coverage_control")}})
    if incomplete_loss:
        # This unit is incomplete in both arms, but loses an already covered group.
        groups = [[target], [{"path": "missing.py", "evidence_lines": [1, 2]}]]
        controls = {"legacy": response([row(inventory["left"])]),
                    "coverage_control": response([row(inventory["left"])])}
        after = response([])
        for name in audit.CONDITIONS:
            value = controls.get(name, after)
            rows.append({"selector": name, "subset": "observed", "dataset": "example", "query_id": "partial_loss",
                "condition": "hybrid", "kind": "present", "multi_file": True, "evidence_groups": groups,
                "evidence_ranks": audit.evidence_ranks(value["results"], groups), "response": value,
                "comparisons": {control: audit.comparison(controls[control], value, groups) for control in controls}})
    return rows


def test_development_gate_does_not_rebrand_previous_coverage_gains_as_new():
    summary = audit.development_summary(development_rows(), {"observed_rows": 1})
    verdict = summary["qualification"]["guarded_connected_union"]
    assert verdict["qualified"] is False
    assert verdict["gained_additional_distinct_complete_queries"] == 0
    summary = audit.development_summary(development_rows(additional=True), {"observed_rows": 2})
    assert summary["qualification"]["guarded_connected_union"]["qualified"] is True
    assert summary["qualification"]["guarded_connected_union"]["additional_distinct_complete_queries_vs_coverage_control"] == ["example/new_gain"]


def test_development_partial_group_loss_blocks_incremental_complete_gain():
    summary = audit.development_summary(development_rows(additional=True, incomplete_loss=True), {})
    verdict = summary["qualification"]["guarded_connected_union"]
    assert verdict["qualified"] is False
    assert verdict["gained_additional_distinct_complete_queries"] == 1
    assert verdict["against_controls"]["coverage_control"] == {
        "lost_groups": 1, "lost_complete_rows": 0, "lost_single_file_first_hits": 0}


def native_rows(*, new_gain):
    result = []
    for mode in ("dense", "hybrid"):
        for repetition in (1, 2, 3):
            for query in ("known", "additional"):
                for arm in ("legacy", "coverage_control", "candidate"):
                    complete = (query == "known" and arm != "legacy") or (query == "additional" and arm == "candidate" and new_gain)
                    result.append({"query_id": query, "retrieval": mode, "repetition": repetition,
                        "arm": arm, "kind": "present", "multi_file": True,
                        "elapsed_backend_s": 1.1 if arm == "candidate" else 1.0,
                        "delivered_ranks": [1, 2 if complete else None],
                        "response": {"results": []}, "stages": {"selected": []}})
    return result


def test_native_gate_requires_new_multi_file_gain_over_prior_coverage():
    report = audit.native_summary(native_rows(new_gain=False))
    assert report["dense_promotion_gate_passed"] is False
    assert report["per_retrieval"]["dense"]["against_controls"]["legacy"]["net_multi_file_complete_gain"] == 1
    assert report["per_retrieval"]["dense"]["against_controls"]["coverage_control"]["net_multi_file_complete_gain"] == 0
    report = audit.native_summary(native_rows(new_gain=True))
    assert report["dense_promotion_gate_passed"] is True
    assert report["per_retrieval"]["dense"]["active_composite_overhead"]["samples"] == 0
    assert report["per_retrieval"]["dense"]["median_wall_ratio_to_legacy"] == 1.1


@pytest.mark.parametrize("fast_control", ["legacy", "coverage_control"])
def test_native_warm_latency_gate_requires_both_controls_not_only_one(fast_control):
    values = native_rows(new_gain=True)
    for item in values:
        item["elapsed_backend_s"] = 0.8 if item["arm"] == fast_control else 1.1
    report = audit.native_summary(values)
    dense = report["per_retrieval"]["dense"]
    assert dense["median_wall_ratio_to_controls"][fast_control] == 1.375
    assert dense["passed"] is False
    assert report["dense_promotion_gate_passed"] is False


def test_native_both_control_latency_boundary_is_inclusive():
    values = native_rows(new_gain=True)
    for item in values:
        item["elapsed_backend_s"] = 1.25 if item["arm"] == "candidate" else 1.0
    dense = audit.native_summary(values)["per_retrieval"]["dense"]
    assert dense["median_wall_ratio_to_controls"] == {"legacy": 1.25, "coverage_control": 1.25}
    assert dense["passed"] is True


def test_historical_execution_checks_actual_git_blob_and_reports_later_working_edit(tmp_path):
    import json
    folder = tmp_path / "results"
    folder.mkdir()
    code = tmp_path / "producer.py"
    code.write_text("original\n")
    plan_path = folder / "frozen-plan.json"
    plan = {"producer_sha256": {"producer.py": audit.sha(code.read_bytes())}, "input_sha256": {}}
    plan_path.write_text(json.dumps(plan))
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=Auditor", "-c", "user.email=auditor@example.invalid",
                    "commit", "-qm", "prospective freeze"], check=True)
    commit = subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()
    code.write_text("later edit\n")
    started = {"execution_commit": commit, "created_at_utc": "2099-01-01T00:00:00+00:00"}
    chain, execution, resolutions = audit.historical_bindings(tmp_path, plan_path, plan, started, {})
    assert execution == chain["commit"] == commit
    assert resolutions["producer.py"]["frozen_sha256"] != resolutions["producer.py"]["current_sha256"]
    forged = deepcopy(plan)
    forged["producer_sha256"]["producer.py"] = audit.sha(b"never committed\n")
    with pytest.raises(ValueError, match="Frozen committed blob differs"):
        audit.historical_bindings(tmp_path, plan_path, forged, started, {})


def test_full_trace_reconstructs_unchanged_raw_pool_and_transient_composite_stages():
    sources, inventory, keys, ranked, composite = fixture()
    stages = {"ranked": [{"snippet": value["snippet"], "score": value["score"]} for value in ranked.values()],
              "selected": [{"snippet": composite, "score": 0.9}],
              "eligible": [{"snippet": composite, "score": 0.9}],
              "origins": {composite["id"]: ["retrieval", None]},
              "definition_keys": {**keys, composite["id"]: keys["left"]}, "ambiguous_symbols": []}
    packed = response([row(composite)])
    recorded = {"selector": "connected_union", "stages": stages,
                "pre_postprocess_response": deepcopy(packed), "response": packed,
                "backfill_candidates": [row(composite)],
                "ranked_candidates": [row(inventory["left"]), row(inventory["right"], score=0.7)],
                "compact_payload": audit.compact_payload(packed),
                "compact_sha256": audit.sha(audit.compact_payload(packed).encode()),
                "actual_compact_tokens": packed["token_usage"]["tokens"]}
    fixture_data = {"sources": sources, "inventory": inventory, "definition_keys": keys}
    audit.verify_trace(recorded, fixture_data, ByteCounter(), expected_ranked=stages["ranked"])
    forged = deepcopy(recorded)
    forged["ranked_candidates"].reverse()
    with pytest.raises(ValueError, match="Full candidate order"):
        audit.verify_trace(forged, fixture_data, ByteCounter())
