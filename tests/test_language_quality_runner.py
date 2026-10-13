"""CPU evidence and reporting checks; no parsers, models or retrieval feedback."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("language_quality_runner", Path(__file__).parents[1] / "scripts/run_language_quality.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def window(source, *, symbol="enclosing", start=1, end=3):
    return {"path": "lib/a.js", "symbol": symbol, "start_line": start, "end_line": end,
            "source": source, "source_hash": module.sha(source.encode())}


def target(source, lo=2, hi=2):
    evidence = "".join(module.physical_lines(source)[lo - 1:hi])
    return {"path": "lib/a.js", "symbol": "exactName", "evidence_lines": [lo, hi],
            "evidence_sha256": module.sha(evidence.encode())}


def test_enclosing_window_can_cover_multiple_groups_without_symbol_equality():
    source = "function enclosing() {\r\n  return 'a\u2028b';\r  }\n"
    row = window(source)
    gold = [[target(source)], [target(source, 2, 3)]]
    assert module.evidence_ranks([row], gold, {"lib/a.js": source}) == [1, 1]


def test_prefix_without_complete_evidence_does_not_hit():
    source = "function enclosing() {\n  return 1;\n}\n"
    row = window(source.splitlines(keepends=True)[0], end=1)
    assert module.evidence_ranks([row], [[target(source)]], {"lib/a.js": source}) == [None]


@pytest.mark.parametrize("mutation", ["bytes", "hash", "bounds", "path"])
def test_source_tampering_rejected_before_scoring(mutation):
    source = "function enclosing() {\n  return 1;\n}\n"
    row = window(source)
    if mutation == "bytes":
        row["source"] = row["source"].replace("1", "2")
        row["source_hash"] = module.sha(row["source"].encode())
    elif mutation == "hash":
        row["source_hash"] = "0" * 64
    elif mutation == "bounds":
        row["end_line"] = 4
    else:
        row["path"] = "../a.js"
    with pytest.raises(ValueError):
        module.evidence_ranks([row], [[target(source)]], {"lib/a.js": source})


def test_or_alternative_and_distinct_groups():
    source = "function enclosing() {\n  return 1;\n}\n"
    other = {**target(source), "path": "lib/b.js"}
    assert module.evidence_ranks([window(source)], [[other, target(source)], [other]], {"lib/a.js": source}) == [1, None]


def test_repetitions_are_latency_samples_without_inflating_quality_denominator():
    rows = []
    for repetition in (1, 2, 3):
        for kind in ("present", "absent"):
            rows.append({"dataset": "zod", "condition": "dense", "repetition": repetition,
                         "query_id": kind, "kind": kind, "multi_file_and": kind == "present",
                         "evidence_ranks": [1, None] if kind == "present" else [],
                         "candidate_evidence_ranks": [1, 2] if kind == "present" else [],
                         "elapsed_http_s": repetition / 10,
                         "response": {"match_status": "candidates", "token_usage": {"tokens": 150}}})
    summary = module.summarize(rows)["zod"]["dense"]
    assert summary["present_queries"] == summary["absent_queries"] == 1
    assert summary["candidate_complete"] == 1 and summary["delivered_complete"] == 0
    assert summary["required_groups"] == 2 and summary["delivered_groups"] == 1
    assert summary["latency_samples"] == 6 and summary["repeat_coverage_mismatches"] == []
    rows[-2]["evidence_ranks"] = [1, 2]
    assert module.summarize(rows)["zod"]["dense"]["repeat_coverage_mismatches"] == ["present"]
