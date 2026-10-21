"""Synthetic checks for immutable controls, source receipts and two-control gates."""
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("window_replay", ROOT / "scripts/replay_windows.py")
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)


@dataclass(frozen=True)
class Item:
    id: str
    path: str
    symbol: str
    start_line: int
    end_line: int
    text: str
    content_hash: str


@dataclass(frozen=True)
class Composite(Item):
    assembly: dict


def physical(text):
    return text.splitlines(True)


class Graph:
    def __init__(self, edges=None):
        self.edges = edges or {}

    def dependencies(self, item, *, limit):
        return self.edges.get(item.id, [])[:limit]


class Context:
    @staticmethod
    def serialize_context(value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def compact_result(value):
        fields = ("path", "symbol", "start_line", "end_line", "source", "source_hash", "truncated", "origin", "related_to")
        return {"query": value["query"], "scope": value["scope"], "match_status": value["match_status"],
                "results": [{name: row[name] for name in fields} for row in value["results"]],
                "budget_exhausted": value["budget_exhausted"], "token_usage": value["token_usage"]}

    @staticmethod
    def encoding():
        return SimpleNamespace(encode=lambda text, **kwargs: list(text))

    @classmethod
    def apply_token_budget(cls, value, budget):
        result = deepcopy(value)
        result["token_usage"] = {"encoding": "cl100k_base", "tokens": 0, "budget": budget, "truncated": False}
        for _ in range(12):
            count = len(cls.serialize_context(cls.compact_result(result)))
            if count == result["token_usage"]["tokens"]:
                break
            result["token_usage"]["tokens"] = count
        assert count <= budget
        return result


def item(identifier, begin, end, *, path="a.py", symbol="f", source="one\ntwo\nthree\nfour\nfive\nsix\n"):
    text = "".join(physical(source)[begin-1:end])
    return Item(identifier, path, symbol, begin, end, text, replay.sha(text.encode()))


def example():
    source = "one\ntwo\nthree\nfour\nfive\nsix\n"
    first, second = item("first", 1, 4), item("second", 3, 6)
    template = {"query": "find a behavior", "scope": {"source_root": "", "membership": "saved", "source_exclusions": [], "exclusions_omitted": 0},
                "match_status": "candidates", "results": []}
    original = replay.pack(template, [(first, 10., "retrieval", None)], Context, physical)
    return {"subset": "synthetic", "dataset": "toy", "query_id": "q", "condition": "dense", "kind": "present", "multi_file": True,
            "pairs": [(first, 10.), (second, 9.)], "inventory": {first.id: first, second.id: second},
            "sources": {"a.py": source}, "definition_keys": {first.id: ("a.py", "f", 1, 6), second.id: ("a.py", "f", 1, 6)},
            "ambiguous_symbols": set(), "graph": Graph(), "original": original,
            "groups": [[{"path": "a.py", "evidence_lines": [1, 6]}]]}


def composite_row(case, *, anchor_id="first", companion_id="second", used_end=None, delivered_end=None):
    by_id = case["inventory"]
    anchor, companion = by_id[anchor_id], by_id[companion_id]
    ranks = {value.id: rank for rank, (value, _) in enumerate(case["pairs"], 1)}
    scores = {value.id: score for value, score in case["pairs"]}
    descriptors = []
    for number, value in enumerate((anchor, companion)):
        end = used_end if number == 0 and used_end is not None else value.end_line
        source = "".join(physical(case["sources"][value.path])[value.start_line-1:end])
        descriptors.append({"id": value.id, "path": value.path, "symbol": value.symbol,
                            "start_line": value.start_line, "end_line": value.end_line, "content_hash": value.content_hash,
                            "used_start_line": value.start_line, "used_end_line": end,
                            "used_source_hash": replay.sha(source.encode())})
    begin = min(row["used_start_line"] for row in descriptors)
    end = max(row["used_end_line"] for row in descriptors)
    full = "".join(physical(case["sources"][anchor.path])[begin-1:end])
    key = list(case["definition_keys"][anchor.id])
    binding = {"kind": "connected_windows", "definition_key": key,
               "members": sorted(descriptors, key=lambda value: (value["id"], value["used_start_line"], value["used_end_line"], value["used_source_hash"])),
               "start_line": begin, "end_line": end, "content_hash": replay.sha(full.encode())}
    assembly = {"kind": "connected_windows", "definition_key": key, "members": descriptors,
                "score_anchor_id": anchor.id, "anchor_rank": ranks[anchor.id], "companion_rank": ranks[companion.id]}
    if used_end is not None:
        assembly["inherited_scores"] = {"retrieval_score": scores[anchor.id], "h2o_score": None, "qwen_score": None}
    delivered_end = end if delivered_end is None else delivered_end
    source = "".join(physical(case["sources"][anchor.path])[begin-1:delivered_end])
    return {"id": "context:connected_windows:" + replay.sha(replay.canonical(binding)),
            "path": anchor.path, "symbol": anchor.symbol, "start_line": begin, "end_line": delivered_end,
            "window_end_line": end, "source": source, "source_hash": replay.sha(source.encode()),
            "content_hash": replay.sha(full.encode()), "retrieval_score": scores[anchor.id],
            "h2o_score": None, "qwen_score": None, "origin": "retrieval", "related_to": None,
            "truncated": source != full, "assembly": assembly}


def response_with(case, rows):
    result = {name: deepcopy(case["original"][name]) for name in ("query", "scope", "match_status")}
    result.update(results=deepcopy(rows), total_source_chars=sum(len(row["source"]) for row in rows), budget_exhausted=any(row["truncated"] for row in rows))
    return Context.apply_token_budget(result, 4096)


def test_independent_composite_verifier_accepts_overlap_union_and_clipped_delivered_prefix():
    case = example()
    row = composite_row(case, delivered_end=5)
    assert replay.verify_assembly(row, case, physical) == case["sources"]["a.py"]
    text = replay.verify_response(response_with(case, [row]), case, Context, physical)
    assert '"end_line":5' in text and "assembly" not in text


def test_full_union_identity_is_independent_of_member_orientation_and_tracks_actual_anchor_score():
    case = example()
    first = composite_row(case)
    second = composite_row(case, anchor_id="second", companion_id="first")
    assert first["id"] == second["id"] and first["retrieval_score"] != second["retrieval_score"]
    assert replay.verify_assembly(second, case, physical) == case["sources"]["a.py"]


@pytest.mark.parametrize("change,match", [
    (lambda row: row.update(id="forged"), "deterministic identity"),
    (lambda row: row.update(content_hash="f" * 64), "deterministic identity"),
    (lambda row: row.update(retrieval_score=0.), "new score"),
    (lambda row: row.update(h2o_score=1.), "new score"),
    (lambda row: row.update(qwen_score=1.), "new score"),
    (lambda row: row["assembly"].update(anchor_rank=4), "rank/score anchor"),
    (lambda row: row["assembly"].update(score_anchor_id="second"), "rank/score anchor"),
    (lambda row: row["assembly"]["members"][0].update(used_source_hash="f" * 64), "used-member bytes"),
    (lambda row: row["assembly"]["members"][0].update(content_hash="f" * 64), "original inventory"),
    (lambda row: row["assembly"]["members"][0].update(used_start_line=2), "whole physical prefix"),
    (lambda row: row["assembly"].update(definition_key=["a.py", "different", 1, 6]), "same exact definition"),
])
def test_composite_verifier_rejects_forged_source_identity_and_scoring(change, match):
    case = example()
    row = composite_row(case)
    change(row)
    with pytest.raises(ValueError, match=match):
        replay.verify_assembly(row, case, physical)


def test_composite_cannot_join_separate_fragments_across_a_gap():
    case = example()
    first, second = item("first", 1, 2), item("second", 5, 6)
    case.update(pairs=[(first, 10.), (second, 9.)], inventory={first.id: first, second.id: second})
    with pytest.raises(ValueError, match="gap"):
        replay.verify_assembly(composite_row(case), case, physical)


def test_clipped_anchor_can_extend_through_its_own_original_full_ranked_window():
    case = example()
    row = composite_row(case, companion_id="first", used_end=2)
    assert replay.verify_assembly(row, case, physical) == case["inventory"]["first"].text
    row["assembly"]["inherited_scores"]["retrieval_score"] = -1
    with pytest.raises(ValueError, match="inherited-score"):
        replay.verify_assembly(row, case, physical)


def test_dependency_parent_can_point_to_composite_but_must_resolve_to_real_allowed_anchor():
    case = example()
    helper = item("helper", 1, 1, path="b.py", symbol="helper", source="help\n")
    case["inventory"][helper.id] = helper
    case["sources"][helper.path] = helper.text
    case["definition_keys"][helper.id] = ("b.py", "helper", 1, 1)
    case["graph"] = Graph({"first": [helper]})
    parent = composite_row(case)
    dependent = replay.source_row(helper, 10., "dependency", parent["id"], helper.text, physical)
    result = response_with(case, [parent, dependent])
    replay.verify_response(result, case, Context, physical)
    case["graph"] = Graph()
    with pytest.raises(ValueError, match="allowed original score anchor"):
        replay.verify_response(result, case, Context, physical)


def test_packing_preserves_assembly_receipt_outside_compact_payload():
    case = example()
    row = composite_row(case)
    value = Composite(row["id"], row["path"], row["symbol"], row["start_line"], row["window_end_line"],
                      case["sources"]["a.py"], row["content_hash"], row["assembly"])
    result = replay.pack(case["original"], [(value, 10., "retrieval", None)], Context, physical)
    assert result["results"][0]["assembly"] == row["assembly"]
    assert "assembly" not in Context.serialize_context(Context.compact_result(result))


def test_selector_and_postprocessor_boundaries_exclude_gold_and_include_raw_order():
    case = example()
    received = {}
    def selector(pairs, **kwargs):
        received["selection"] = kwargs
        assert pairs == case["pairs"]
        return pairs[:1], pairs[:1], {"first": ("retrieval", None)}
    def postprocessor(response, candidates, **kwargs):
        received["postprocessor"] = kwargs
        received["distinct"] = candidates
        return response
    modules = {"selection": SimpleNamespace(ranked_context=selector, unchanged_context=postprocessor),
               "corpus": SimpleNamespace(physical_lines=physical), "context": Context}
    result = replay.evaluate(case, "legacy", modules)
    assert set(received["selection"]) == {"query", "max_results", "include_dependencies", "references", "definition_keys", "ambiguous_symbols"}
    assert set(received["postprocessor"]) == {"ranked_candidates", "definition_keys", "max_results", "source_budget", "token_budget"}
    assert [row["id"] for row in received["postprocessor"]["ranked_candidates"]] == ["first", "second"]
    assert [row["id"] for row in received["distinct"]] == ["first"]
    assert set(result["stages"]) == {"ranked", "selected", "eligible", "origins", "definition_keys", "ambiguous_symbols"}
    assert not any(key in str(received) for key in ("query_id", "evidence_lines", "target_groups"))


def test_exact_legacy_barrier_stops_on_any_metadata_difference():
    case = example()
    modules = {"context": Context, "corpus": SimpleNamespace(physical_lines=physical)}
    assert replay.control_barrier([case], modules)[0]["query_id"] == "q"
    case["original"]["budget_exhausted"] = True
    with pytest.raises(ValueError, match="Exact baseline mismatch"):
        replay.control_barrier([case], modules)


def test_source_preservation_requires_one_literal_covering_excerpt():
    before = {"results": [{"path": "a", "start_line": 1, "end_line": 4, "source": "one\ntwo\nthree\nfour\n"}]}
    split = {"results": [{"path": "a", "start_line": 1, "end_line": 2, "source": "one\ntwo\n"},
                         {"path": "a", "start_line": 3, "end_line": 4, "source": "three\nfour\n"}]}
    with pytest.raises(ValueError, match="lost prior actual"):
        replay.preserved(before, split, physical)


def test_source_guard_rejects_changed_buffer_provenance():
    old = {"path": "a", "start_line": 1, "end_line": 1, "source": "one\n", "buffer_hash": "old"}
    assert replay.source_preserved({"results": [old]}, {"results": [deepcopy(old)]}, physical)
    changed = dict(old, buffer_hash="new")
    assert not replay.source_preserved({"results": [old]}, {"results": [changed]}, physical)
    assert not replay.source_preserved({"results": [old]}, {"results": [{key: value for key, value in old.items() if key != "buffer_hash"}]}, physical)


def test_retained_dependency_metadata_changes_only_for_verified_parent_replacement():
    case = example()
    parent = composite_row(case)
    old_parent = replay.source_row(case["inventory"]["first"], 10., "retrieval", None,
                                   case["inventory"]["first"].text, physical)
    old_child = {"id": "child", "path": "b.py", "start_line": 1, "end_line": 1,
                 "source": "child\n", "origin": "dependency", "related_to": "first"}
    new_child = dict(old_child, related_to=parent["id"])
    replay.preserved({"results": [old_parent, old_child]}, {"results": [parent, new_child]}, physical)
    with pytest.raises(ValueError, match="retained old metadata"):
        replay.preserved({"results": [old_parent, old_child]},
                         {"results": [parent, dict(new_child, origin="retrieval")]}, physical)


def test_historical_coverage_barrier_checks_full_response_even_when_compact_hash_matches(monkeypatch):
    case = example()
    text = Context.serialize_context(Context.compact_result(case["original"]))
    saved = {"response": {**case["original"], "unexpected_debug": True}, "compact_sha256": replay.sha(text.encode()),
             "evidence_ranks": [None]}
    monkeypatch.setattr(replay, "evaluate", lambda *args: {"response": case["original"], "compact_sha256": replay.sha(text.encode())})
    with pytest.raises(ValueError, match="Exact historical coverage-control mismatch"):
        replay.coverage_barrier([case], {replay.identity_tuple(case): saved}, {})


def actual_modules():
    from semantic_search import context, corpus, selection
    return {"context": context, "corpus": corpus, "selection": selection}


def test_guarded_connected_trace_retains_actual_reference_and_accepts_source_safe_union():
    result = replay.evaluate(example(), "guarded_connected_union", actual_modules())
    assert result["response"]["_window_guard"]["accepted"] is True
    assert result["reference_response"]["results"][0]["end_line"] == 4
    assert result["proposal_response"]["results"][0]["end_line"] == 6
    assert result["response"]["results"] == result["proposal_response"]["results"]


def test_guarded_connected_fallback_retains_coverage_control_when_union_crowds_later_source():
    modules = actual_modules()
    case = example()
    long_source, helper_source = ("a" * 999 + "\n") * 11, ("b" * 999 + "\n") * 4
    first = item("first", 1, 3, source=long_source)
    second = item("second", 3, 11, source=long_source)
    helper = item("helper", 1, 4, path="b.py", symbol="helper", source=helper_source)
    case.update(pairs=[(first, 10.), (second, 9.), (helper, 8.)],
                inventory={value.id: value for value in (first, second, helper)},
                sources={"a.py": long_source, "b.py": helper_source},
                definition_keys={"first": ("a.py", "f", 1, 11), "second": ("a.py", "f", 1, 11),
                                 "helper": ("b.py", "helper", 1, 4)})
    case["original"] = replay.pack(case["original"], [(first, 10., "retrieval", None), (helper, 8., "retrieval", None)],
                                    modules["context"], physical)
    result = replay.evaluate(case, "guarded_connected_union", modules)
    assert result["response"]["_window_guard"]["accepted"] is False
    assert result["proposal_response"]["results"][1]["end_line"] == 1
    assert result["reference_response"]["results"][1]["end_line"] == 4
    assert result["response"]["results"] == result["reference_response"]["results"]
    assert result["response"]["total_source_chars"] == 7000


def outcome(name, query, ranks, legacy, coverage, *, condition="dense", multi_file=True):
    groups = [[{"path": "a", "evidence_lines": [1, 1]}] for _ in ranks]
    def change(control):
        gains = [{"group": i+1} for i, (before, after) in enumerate(zip(control, ranks)) if before is None and after is not None]
        losses = [{"group": i+1} for i, (before, after) in enumerate(zip(control, ranks)) if before is not None and after is None]
        return {"control_ranks": control, "variant_ranks": ranks, "group_gains": gains, "group_losses": losses,
                "complete_gain": None in control and None not in ranks,
                "complete_loss": None not in control and None in ranks}
    return {"selector": name, "subset": "s", "dataset": "d", "query_id": query, "condition": condition,
            "kind": "present", "multi_file": multi_file, "evidence_groups": groups, "evidence_ranks": ranks,
            "comparisons": {"legacy": change(legacy), "coverage_control": change(coverage)},
            "response": {"results": [], "total_source_chars": 0, "match_status": "candidates", "token_usage": {"tokens": 10, "truncated": False}}}


def test_old_coverage_gains_do_not_count_as_additional_improvement():
    outcomes = [outcome(name, "already-known", [1, None] if name == "legacy" else [1, 2], [1, None], [1, 2]) for name in replay.CONDITIONS]
    summary = replay.summarize(outcomes, {})
    for verdict in summary["qualification"].values():
        assert verdict["qualified"] is False and verdict["gained_additional_distinct_complete_queries"] == 0


def test_two_control_gate_counts_distinct_queries_and_rejects_any_covered_group_regression():
    outcomes = []
    for name in replay.CONDITIONS:
        for mode in ("dense", "hybrid"):
            after = [1, 2] if name in replay.CANDIDATES else [1, None]
            outcomes.append(outcome(name, "new", after, [1, None], [1, None], condition=mode))
        outcomes.append(outcome(name, "already-incomplete", [None, None] if name == "connected_union" else [1, None],
                                [1, None], [1, None]))
    result = replay.summarize(outcomes, {})
    assert result["qualification"]["guarded_continuation"]["qualified"] is True
    assert result["qualification"]["guarded_continuation"]["gained_additional_distinct_complete_queries"] == 1
    assert result["qualification"]["connected_union"]["qualified"] is False
    assert result["qualification"]["connected_union"]["against_controls"]["coverage_control"]["lost_groups"] == 1
    assert len(result["all_changes"]["guarded_continuation"]["coverage_control"]["complete_gains"]) == 2


def test_freeze_only_runs_immutable_legacy_and_saved_coverage_barriers(tmp_path, monkeypatch):
    case = example()
    calls = []
    monkeypatch.setattr(replay.prior, "runtime", lambda root: ({"fake": True}, b"ranks"))
    monkeypatch.setattr(replay.prior, "offline_cpu_guard", nullcontext)
    monkeypatch.setattr(replay.prior, "cpu_modules", lambda *args: nullcontext({}))
    monkeypatch.setattr(replay, "load_inputs", lambda *args: ([case], {}, {}, {}))
    monkeypatch.setattr(replay, "control_barrier", lambda *args: calls.append("legacy") or ["legacy"])
    monkeypatch.setattr(replay, "coverage_barrier", lambda *args: calls.append("saved coverage") or ["saved"])
    monkeypatch.setattr(replay, "own_bindings", lambda root: {})
    monkeypatch.setattr(replay, "evaluate", lambda *args: pytest.fail("Freeze ran a new variant"))
    output = tmp_path / "new-study"
    result = replay.freeze(tmp_path, tmp_path, output)
    assert calls == ["legacy", "saved coverage"] and result["new_variant_outcomes_computed"] == 0
    assert set(path.name for path in output.iterdir()) == set(replay.FROZEN_FILES)
    assert replay.read(output / "frozen-plan.json")["artifacts"]["source-fixtures.json"] == replay.sha((output / "source-fixtures.json").read_bytes())


def test_changed_or_undeclared_frozen_code_is_rejected_before_git_access(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="Undeclared"):
        replay.validate_run(tmp_path, tmp_path, {"conditions": {}, "settings": replay.SETTINGS})
    monkeypatch.setattr(replay, "own_bindings", lambda root: {"a": "changed"})
    with pytest.raises(ValueError, match="changed after freeze"):
        replay.validate_run(tmp_path, tmp_path, {"conditions": replay.CONDITIONS, "settings": replay.SETTINGS, "producer_sha256": {"a": "old"}})


def test_existing_freeze_directory_is_never_overwritten(tmp_path):
    with pytest.raises(ValueError, match="new freeze directory"):
        replay.freeze(tmp_path, tmp_path, tmp_path)


def test_new_runner_reuses_the_network_and_numerical_runtime_import_guards():
    code = '''
import importlib.util, socket, sys
spec=importlib.util.spec_from_file_location("windows",sys.argv[1])
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with module.prior.offline_cpu_guard():
    for action in (lambda:socket.create_connection(("example.com",443)), lambda:__import__("semantic_search.backend"), lambda:__import__("numpy")):
        try: action()
        except RuntimeError: pass
        else: raise AssertionError("Forbidden operation escaped CPU replay guard")
assert not any(name.startswith(("mlx", "torch", "numpy", "semantic_search.backend")) for name in sys.modules)
'''
    subprocess.run([sys.executable, "-c", code, str(ROOT / "scripts/replay_windows.py")], check=True, capture_output=True, text=True)
