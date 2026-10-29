"""Source-only data boundary checks; invented records, no model/ranking calls."""
from dataclasses import asdict
from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import complementary_inputs as data


def window(identifier, symbol="helper", low=1):
    text = "def helper():\n    return 1\n"
    return data.Window(identifier, "src/code.py", symbol, low, low + 1, text, data.sha(text.encode()))


def test_static_dependencies_preserve_target_order_first_two_and_earliest_window():
    first, late, earliest, other = window("anchor", "anchor"), window("late", low=10), window("earliest"), window("other", "other")
    inventory = {row.id: row for row in (first, late, earliest, other)}
    graph = data.FrozenGraph(inventory, [{"caller": [first.path, first.symbol], "targets": [
        ["src/absent.py", "missing"], [late.path, late.symbol], [other.path, other.symbol]]}])
    assert [row.id for row in graph.dependencies(first, 2)] == ["earliest", "other"]
    assert graph.dependencies(first, 0) == []
    with pytest.raises(ValueError):
        graph.dependencies(first, True)


def test_source_view_preserves_optional_diagnostic_presence_without_operational_metadata():
    response = {name: {} for name in data.FIELDS}
    response["timings"] = {"native": 1}
    assert "timings" not in data.source_view(response)
    assert "_selection_backfill" not in data.source_view(response)
    response["_selection_backfill"] = {"trials": 0}
    view = data.source_view(response)
    assert view["_selection_backfill"] == {"trials": 0}
    view["_selection_backfill"]["trials"] = 1
    assert response["_selection_backfill"]["trials"] == 0


def invented():
    item = window("one")
    fx = {"inventory": {item.id: item}, "definition_keys": {item.id: (item.path, item.symbol, 1, 2)}}
    row = {"subset": "invented", "dataset": "invented", "query_id": "one", "condition": "dense",
           "kind": "behavior", "multi_file": False, "evidence_groups": [],
           "stages": {"ranked": [{"snippet": asdict(item), "score": 0.2}]},
           "response": {name: {} for name in data.FIELDS}}
    return row, fx


@pytest.mark.parametrize("mutation", ["nonfinite", "forged_source", "duplicate", "unscored"])
def test_frozen_raw_pool_rejects_forged_windows_scores_and_unscored_tail(mutation):
    row, fx = invented()
    if mutation == "nonfinite":
        row["stages"]["ranked"][0]["score"] = float("nan")
    elif mutation == "forged_source":
        row["stages"]["ranked"][0]["snippet"]["text"] = "fabricated"
    elif mutation == "duplicate":
        row["stages"]["ranked"] *= 2
    else:
        row["stages"]["ranked"][0]["score"] = None
    with pytest.raises(ValueError):
        data.make_case(row, fx)


def test_source_qualified_semantics_are_explicit_not_inferred_from_dataset():
    row, fx = invented()
    assert data.make_case(row, fx)["qualified"] is False
    assert data.make_case(row, fx, qualified=True)["qualified"] is True
