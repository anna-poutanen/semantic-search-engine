"""Generic CPU fixtures for prospective replay; no study labels or model calls."""
import hashlib
import importlib.util
from pathlib import Path
from unittest.mock import patch

from semantic_search import context
from semantic_search.corpus import Snippet
from semantic_search.source import physical_lines


SPEC = importlib.util.spec_from_file_location("context_replay", Path(__file__).resolve().parents[1] / "scripts/replay_context_packing.py")
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)


def snippet(symbol, start, text, path="src/code.ts"):
    return Snippet(f"{path}:{symbol}:{start}", path, symbol, start,
                   start + len(physical_lines(text)) - 1, text, hashlib.sha256(text.encode()).hexdigest())


def stream(*snippets):
    return [(item, 1.0, "retrieval", None) for item in snippets]


def template():
    return {"query": "generic fixture", "match_status": "candidates", "scope": context.indexed_scope("src")}


def test_union_containment_uses_delivered_path_and_actual_clipped_bounds():
    rows = [{"path": "a", "start_line": 2, "end_line": 4},
            {"path": "a", "start_line": 5, "end_line": 7},
            {"path": "a", "start_line": 9, "end_line": 12}]
    assert replay.covered("a", 3, 7, rows)
    assert not replay.covered("a", 3, 9, rows)
    assert not replay.covered("b", 3, 7, rows)
    assert not replay.covered("a", 1, 7, rows)
    assert not replay.covered("a", 2, 13, rows)


def test_containment_control_skips_only_wholly_covered_and_backfills_in_order():
    a = snippet("outer", 1, "one\ntwo\nthree\n")
    b = snippet("nested", 2, "two\n")
    c = snippet("partial", 3, "three\nfour\n")
    d = snippet("other", 1, "next\n", "src/other.ts")
    with patch.dict(replay.SETTINGS, max_results=3):
        plain, _ = replay.replay_response(template(), stream(a, b, c, d), context, physical_lines, containment=False)
        contained, events = replay.replay_response(template(), stream(a, b, c, d), context, physical_lines, containment=True)
    assert [row["symbol"] for row in plain["results"]] == ["outer", "nested", "partial"]
    assert [row["symbol"] for row in contained["results"]] == ["outer", "partial", "other"]
    assert events[1]["action"] == "contained_after_clip"
    assert contained["token_usage"]["tokens"] == context.count_context(contained) <= 4096


def test_character_clip_precedes_containment_and_skips_do_not_spend_budget():
    a = snippet("outer", 1, "a\nb\n")
    b = snippet("long", 1, "a\nb\nc\nd\n")
    c = snippet("later", 1, "z\n", "src/other.ts")
    with patch.dict(replay.SETTINGS, source_budget=6):
        result, events = replay.replay_response(template(), stream(a, b, c), context, physical_lines, containment=True)
    assert events[1] == {"id": b.id, "action": "contained_after_clip", "start_line": 1, "end_line": 1}
    assert [row["symbol"] for row in result["results"]] == ["outer", "later"]
    assert result["total_source_chars"] == 6


def test_empty_whole_line_prefix_does_not_prevent_later_short_candidate():
    a = snippet("too_wide", 1, "x" * 2000 + "\r\n")
    b = snippet("short", 1, "é😀\r", "src/b.ts")
    with patch.dict(replay.SETTINGS, source_budget=20):
        result, events = replay.replay_response(template(), stream(a, b), context, physical_lines, containment=True)
    assert events[0]["action"] == "no_character_prefix"
    assert result["results"][0]["source"] == "é😀\r"
    assert result["results"][0]["source_hash"] == hashlib.sha256("é😀\r".encode()).hexdigest()
    assert result["results"][0]["end_line"] == 1


def test_token_empty_prefix_backfills_and_actual_token_clip_is_truthful():
    a = snippet("large_line", 1, "😀" * 600 + "\n")
    b = snippet("short", 7, "ok\n", "src/b.ts")
    with patch.dict(replay.SETTINGS, token_budget=220):
        result, events = replay.replay_response(template(), stream(a, b), context, physical_lines, containment=True)
    assert events[0]["action"] == "no_token_prefix"
    assert [row["symbol"] for row in result["results"]] == ["short"]
    assert context.count_context(result) <= 220
    long = snippet("many_lines", 1, "".join(f"const v{i} = '😀';\r\n" for i in range(80)))
    with patch.dict(replay.SETTINGS, token_budget=350):
        clipped, events = replay.replay_response(template(), stream(long), context, physical_lines, containment=True)
    row = clipped["results"][0]
    assert events[0]["token_clipped"] and row["truncated"]
    assert 1 <= row["end_line"] < long.end_line
    assert row["source"] == "".join(physical_lines(long.text)[:row["end_line"]])
    assert context.count_context(clipped) <= 350
    assert not replay.covered(row["path"], row["end_line"] + 1, long.end_line, clipped["results"])


def test_fixed_first_window_symbol_dedup_and_dependency_order():
    items = [snippet(f"s{i}", 1, f"{i}\n", f"src/{i}.ts") for i in range(10)]
    later_same_symbol = snippet("s0", 50, "late\n", "src/0.ts")
    dep = snippet("callee", 1, "callee\n", "src/dep.ts")
    class Graph:
        def dependencies(self, item, limit):
            assert limit == 2
            return [dep] if item == items[0] else []
    ordered = replay.ordered_stream([(item, 10 - i) for i, item in enumerate([*items, later_same_symbol])], Graph())
    assert [row[0] for row in ordered] == [*items[:5], dep, *items[5:]]
    assert ordered[5][2:] == ("dependency", items[0].id)
    assert len(replay.ordered_stream([(item, 1) for item in items], Graph(), baseline=True)) == 8


def test_baseline_keeps_original_prelimit_no_backfill_behavior():
    first = snippet("wide", 1, "wide\n")
    second = snippet("later", 1, "a\n", "src/b.ts")
    with patch.dict(replay.SETTINGS, source_budget=3):
        result = replay.baseline_response(template(), stream(first, second), context, physical_lines)
    assert [row["symbol"] for row in result["results"]] == ["later"]
    assert result["budget_exhausted"]


def test_plan_freezes_both_controls_code_inputs_and_pinned_source_hashes(tmp_path):
    frozen = {"git_commit": "abc", "implementation_sha256": {"src/semantic_search/" + name + ".py": "a" * 64 for name in replay.FILES},
              "sources": [{"id": "toy", "source_file_sha256": {"src/a.ts": "b" * 64}}]}
    with patch.object(replay, "runtime_bindings", return_value={"tiktoken": "fixture"}), patch.object(replay, "inputs", return_value=(frozen, {}, [], {}, {"toy": "/toy"}, {"trace": "c" * 64})):
        plan = replay.freeze(tmp_path, tmp_path / "new")
    assert plan["controls"] == ["backfill_only", "containment_and_backfill"]
    assert plan["script_sha256"] == replay.sha(Path(replay.__file__).read_bytes())
    assert plan["sources"] == frozen["sources"]
    assert (tmp_path / "new/plan.json").is_file()
    assert not (tmp_path / "new/replay.jsonl").exists()
