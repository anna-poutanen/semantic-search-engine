"""Synthetic CPU checks for literal selection controls, gates and reporting units."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("selection_replay", ROOT / "scripts/replay_selection.py")
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)


def snippet(identifier, *, symbol=None, text="return 1\n"):
    return SimpleNamespace(id=identifier, path="src/" + (symbol or identifier) + ".py", symbol=symbol or identifier,
        start_line=1, end_line=len(text.splitlines()), text=text, content_hash=replay.sha(text.encode()))


class Graph:
    def __init__(self, mapping=None):
        self.mapping = mapping or {}

    def dependencies(self, item, *, limit):
        return self.mapping.get(item.id, [])[:limit]


def test_independent_legacy_control_uses_first_display_window_and_dependency_capacity():
    items = [snippet(str(number)) for number in range(9)]
    duplicate = snippet("later", symbol="0")
    helpers = [snippet("helper" + str(number)) for number in range(3)]
    pairs = [(items[0], 10.), (duplicate, 9.5), *[(item, 9. - number) for number, item in enumerate(items[1:])]]
    result = replay.legacy_stream(pairs, Graph({"0": helpers[:2], "1": helpers[2:]}))
    assert [item.id for item, _, _, _ in result] == ["0", "1", "2", "3", "4", "helper0", "helper1", "helper2"]
    assert result[-1][2:] == ("dependency", "1")


def test_groups_are_full_excerpt_or_alternatives_and_across_groups():
    windows = [{"path": "a.py", "start_line": 1, "end_line": 3},
               {"path": "a.py", "start_line": 4, "end_line": 6},
               {"path": "b.py", "start_line": 10, "end_line": 12}]
    evidence = [[{"path": "a.py", "evidence_lines": [1, 6]}],
                [{"path": "missing.py", "evidence_lines": [1, 2]}, {"path": "b.py", "evidence_lines": [10, 12]}]]
    assert replay.evidence_ranks(windows, evidence) == [None, 3]


def test_every_group_loss_remains_visible_on_an_already_incomplete_query():
    evidence = [[{"path": "a.py", "evidence_lines": [1, 3]}], [{"path": "b.py", "evidence_lines": [1, 3]}]]
    original = {"results": [{"id": "a", "path": "a.py", "start_line": 1, "end_line": 3}]}
    before, after, gains, losses = replay.group_changes(original, {"results": []}, evidence)
    assert before == [1, None] and after == [None, None] and gains == []
    assert losses == [{"group": 1, "evidence": evidence[0], "baseline_rank": 1, "variant_rank": None,
                       "baseline_excerpts": ["a"], "variant_excerpts": [],
                       "baseline_covering_origins": [{"id": "a", "origin": "retrieval", "related_to": None}],
                       "baseline_dependency_only": False}]


def test_control_barrier_fails_before_return_on_any_original_mismatch(monkeypatch):
    class Context:
        @staticmethod
        def serialize_context(value):
            return str(value)

        @staticmethod
        def compact_result(value):
            return value

    original = {"results": [], "total_source_chars": 0, "budget_exhausted": False, "token_usage": {}}
    cases = [{"original": deepcopy(original), "pairs": [], "graph": Graph(), "subset": "s", "dataset": "d", "query_id": name, "condition": "dense"} for name in ("first", "second")]
    calls = []
    def pack(template, *args):
        calls.append(template)
        result = deepcopy(template)
        if len(calls) == 2:
            result["total_source_chars"] = 1
        return result
    monkeypatch.setattr(replay, "pack", pack)
    monkeypatch.setattr(replay, "verify_response", lambda response, *args: str(response))
    with pytest.raises(ValueError, match="Exact baseline mismatch"):
        replay.control_barrier(cases, {"context": Context, "corpus": SimpleNamespace(physical_lines=str.splitlines)})
    assert len(calls) == 2


def test_selector_argument_boundary_excludes_labels_ids_and_evidence():
    item = snippet("one")
    received = {}
    def selector(pairs, **kwargs):
        received.update(kwargs)
        assert pairs == [(item, 1.)]
        return pairs, pairs, {}
    case = {"pairs": [(item, 1.)], "original": {"query": "look for code"}, "graph": Graph(),
            "definition_keys": {"one": ("src/one.py", "one", 1, 1)}, "kind": "present", "query_id": "gold-id", "groups": [["gold"]]}
    stream, diagnostics, candidates = replay.selected_stream(case, selector)
    assert set(received) == {"query", "max_results", "include_dependencies", "references", "definition_keys"}
    assert stream == [(item, 1., "retrieval", None)] and diagnostics["selected"] == 1
    assert candidates == [(item, 1.)]


def test_counts_do_not_inflate_queries_by_study_or_condition():
    cases = [{"subset": subset, "dataset": "d", "query_id": "same", "condition": condition}
             for subset in ("language_observed", "native_express_observed") for condition in ("dense", "hybrid")]
    result = replay.counts(cases)
    assert result["observed_rows"] == 4 and result["unique_queries"] == 1
    assert result["unique_queries_by_subset"]["language_observed"] == 1


def outcome(selector, query_id, before, after, *, losses=None, condition="dense", multi_file=False):
    return {"selector": selector, "subset": "s", "dataset": "d", "query_id": query_id, "condition": condition,
        "kind": "present", "multi_file": multi_file, "baseline_ranks": before, "evidence_ranks": after,
        "group_gains": [], "group_losses": losses or [],
        "response": {"results": [], "total_source_chars": 0, "token_usage": {"tokens": 100, "truncated": False}}}


def test_qualification_requires_full_group_nonregression_and_distinct_complete_gain():
    items = []
    for selector in replay.CONDITIONS:
        after = [1, 2] if selector != "legacy" else [1, None]
        items.extend([outcome(selector, "gained", [1, None], after, condition=condition, multi_file=True) for condition in ("dense", "hybrid")])
        items.append(outcome(selector, "incomplete", [1, None], [None, None] if selector == "definition" else [1, None],
            losses=[{"group": 1}] if selector == "definition" else [], multi_file=True))
    result = replay.summarize(items, {})
    assert result["qualification"]["definition"]["qualified"] is False
    assert result["qualification"]["rank_preserving"]["qualified"] is True
    assert result["qualification"]["combined"]["gained_unique_complete_queries"] == 1
    assert len(result["all_changes"]["combined"]["complete_gains"]) == 2
    assert len(result["all_changes"]["definition"]["group_losses"]) == 1


def test_network_and_model_imports_are_blocked_without_loading_models():
    import socket
    assert not any(name.startswith(("torch", "mlx", "semantic_search.backend")) for name in sys.modules)
    with replay.offline_cpu_guard():
        with pytest.raises(RuntimeError, match="Network is forbidden"):
            socket.create_connection(("example.com", 443))
        with pytest.raises(RuntimeError, match="Model/numerical import forbidden"):
            importlib.import_module("semantic_search.backend")


def test_undeclared_conditions_fail_before_git_or_output_access(tmp_path):
    with pytest.raises(ValueError, match="Undeclared"):
        replay.validate_run(tmp_path, tmp_path, {"conditions": {}, "settings": replay.SETTINGS})


def test_existing_output_is_never_overwritten(tmp_path):
    with pytest.raises(ValueError, match="new freeze directory"):
        replay.freeze(tmp_path, tmp_path, tmp_path)


def test_coverage_check_rejects_union_fragments_and_changed_old_metadata():
    old = {"id": "child", "path": "a", "start_line": 2, "end_line": 3, "source": "two\nthree\n", "origin": "retrieval"}
    parent = {"id": "parent", "path": "a", "start_line": 1, "end_line": 4, "source": "one\ntwo\nthree\nfour\n"}
    replay.require_source_preserved({"results": [old]}, {"results": [parent]}, lambda text: text.splitlines(True))
    with pytest.raises(ValueError, match="lost original"):
        replay.require_source_preserved({"results": [old]}, {"results": [dict(parent, end_line=2, source="one\ntwo\n")]}, lambda text: text.splitlines(True))
    with pytest.raises(ValueError, match="changed a retained"):
        replay.require_source_preserved({"results": [old]}, {"results": [dict(old, origin="dependency")]}, lambda text: text.splitlines(True))
