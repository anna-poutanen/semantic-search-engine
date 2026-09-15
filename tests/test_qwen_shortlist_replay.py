"""Meaningful CPU tests of prefix isolation, exact packing and quality diagnostics."""
from copy import deepcopy
import importlib.util
import json
import math
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "qwen_shortlist_replay", Path(__file__).parents[1] / "scripts/replay_qwen_shortlists.py"
)
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)


def score(value):
    return {"yes_logit": value, "no_logit": 0., "logit_difference": value,
            "yes_rank_signal": 1 / (1 + math.exp(-value)) if value >= 0
            else math.exp(value) / (1 + math.exp(value))}


def test_prefix_uses_original_retrieval_order_and_never_backfills_high_scoring_tail():
    raw = [{"id": str(number), "retrieval_score": .5} for number in range(8)]
    scores = [score(value) for value in (1., 0., 2., 2., -1., 100., 200., 300.)]
    before = deepcopy((raw, scores))
    assert [item["id"] for item in replay.prefix_ranking(raw, scores, 5)] == ["2", "3", "0", "1", "4"]
    assert [item["id"] for item in replay.prefix_ranking(raw, scores, 8)][:3] == ["7", "6", "5"]
    assert (raw, scores) == before


@pytest.mark.parametrize("depth", [True, 0, -1, 51, 5.0])
def test_invalid_prefix_depth_cannot_be_silently_coerced(depth):
    with pytest.raises(ValueError, match="Strict prefix"):
        replay.prefix_ranking([{"id": "a"}], [score(1.)], depth)


@pytest.mark.parametrize("corruption", ["duplicate", "missing", "nan", "mismatch"])
def test_invalid_candidate_identity_or_scalar_score_rejected(corruption):
    raw, scores = [{"id": "a"}, {"id": "b"}], [score(1.), score(2.)]
    if corruption == "duplicate":
        raw[1]["id"] = "a"
    elif corruption == "missing":
        scores.pop()
    elif corruption == "nan":
        scores[0]["yes_logit"] = math.nan
    else:
        scores[0]["logit_difference"] = 9.
    with pytest.raises(ValueError):
        replay.prefix_ranking(raw, scores, 5)


def fixture_source(identifier, *, lines=3, path=None, symbol=None, text=None):
    from semantic_search.corpus import Snippet
    source = text if text is not None else "value = 1\n" * lines
    physical = replay.audit.evidence.physical_lines(source)
    return Snippet(identifier, path or f"lib/{identifier}.js", symbol or identifier,
                   1, len(physical), source, replay.sha(source.encode()))


class Graph:
    def __init__(self, targets):
        self.targets = targets

    def dependencies(self, snippet, *, limit):
        assert limit == 2
        return self.targets.get(snippet.id, [])[:limit]


def source_fixture(snippets, dependencies=None):
    return {"snippets": {item.id: item for item in snippets}, "graph": Graph(dependencies or {})}


def raw(snippet, value=.5):
    return {"id": snippet.id, "path": snippet.path, "symbol": snippet.symbol,
            "start_line": snippet.start_line, "end_line": snippet.end_line,
            "source": snippet.text, "source_hash": snippet.content_hash,
            "retrieval_score": value}


def scope():
    return replay.audit.expected_scope({"source_root": "lib"})


def test_shortlist_still_delivers_static_callee_outside_original_prefix():
    a, b, c = [fixture_source(key) for key in "abc"]
    source = source_fixture([a, b, c], {"a": [c]})
    result = replay.pack_source("Find behavior", scope(), [raw(a), raw(b)], source)
    assert [item["id"] for item in result["results"]] == ["a", "b", "c"]
    assert result["results"][2]["origin"] == "dependency"
    assert result["results"][2]["related_to"] == "a"
    gold = [[{"path": c.path, "evidence_lines": [1, 3]}]]
    assert replay.audit.ranks([raw(a), raw(b)], gold) == [None]
    assert replay.audit.ranks(result["results"], gold) == [3]


def test_first_five_anchors_callees_then_remaining_unique_windows_order():
    snippets = [fixture_source(str(number)) for number in range(10)]
    source = source_fixture(snippets, {"0": [snippets[8]], "1": [snippets[9]]})
    # A duplicate symbol/window must not take a second anchor or final slot.
    duplicate = {**raw(snippets[0]), "id": "duplicate"}
    ranked = [raw(snippets[0]), duplicate] + [raw(item) for item in snippets[1:8]]
    result = replay.pack_source("Find behavior", scope(), ranked, source)
    assert [item["id"] for item in result["results"]] == ["0", "1", "2", "3", "4", "8", "9", "5"]
    assert len(result["results"]) == 8


def test_physical_source_character_clipping_and_unicode_hash_are_truthful():
    # One long line exactly fills most character cap. Next source can only fit
    # complete CRLF lines; Unicode separators must remain in a physical line.
    a = fixture_source("a", text="x" * 11000 + "\r\n")
    b = fixture_source("b", text=" " * 399 + "\r\n" + "y" * 799 + "\r\n")
    result = replay.pack_source("Find behavior", scope(), [raw(a), raw(b)], source_fixture([a, b]))
    for row in result["results"]:
        assert row["source_hash"] == replay.sha(row["source"].encode())
        assert row["end_line"] == row["start_line"] + len(replay.audit.evidence.physical_lines(row["source"])) - 1
    assert result["total_source_chars"] <= 12000
    assert result["token_usage"]["tokens"] <= 4096
    assert result["budget_exhausted"] is True


def test_token_budget_trims_only_whole_lines_and_preserves_verified_bounds():
    a = fixture_source("a", text="0123456789 " * 15 + "\n")
    b = fixture_source("b", text=("a!b@c#d$e%f^g&h*i(j)k_l+m=n?o:p;q,r.s/tv" * 2 + "\n") * 80)
    result = replay.pack_source("Find behavior", scope(), [raw(a), raw(b)], source_fixture([a, b]))
    assert result["token_usage"]["tokens"] <= 4096
    row = result["results"][1]
    assert row["truncated"] is True and row["end_line"] < b.end_line
    assert row["source"] == "".join(replay.audit.evidence.physical_lines(b.text)[:row["end_line"]])
    assert result["token_usage"]["truncated"] is True


def test_exact_reproduction_restores_original_scope_and_token_key_order():
    a = fixture_source("a")
    result = replay.pack_source("Find behavior", scope(), [raw(a)], source_fixture([a]))
    # Committed JSON is canonical and loses actual production nested-key order.
    saved = json.loads(json.dumps(result, sort_keys=True))
    replay.require_exact(result, saved)
    saved["results"][0]["source_hash"] = "0" * 64
    with pytest.raises(ValueError, match="differs"):
        replay.require_exact(result, saved)


def test_partial_line_union_is_separate_from_complete_single_excerpt_metric():
    rows = [{"path": "lib/a.js", "start_line": 1, "end_line": 3},
            {"path": "lib/a.js", "start_line": 3, "end_line": 6}]
    groups = [[{"path": "lib/a.js", "evidence_lines": [1, 6]}]]
    assert replay.audit.ranks(rows, groups) == [None]
    coverage = replay.source_line_coverage(rows, groups)
    assert coverage["covered_lines"] == coverage["required_lines"] == 6
    assert coverage["fraction"] == 1.


def test_or_weighting_chooses_best_fraction_then_original_alternative_order():
    rows = [{"path": "lib/long.js", "start_line": 1, "end_line": 9},
            {"path": "lib/short.js", "start_line": 1, "end_line": 2}]
    groups = [[{"path": "lib/long.js", "evidence_lines": [1, 10]},
               {"path": "lib/short.js", "evidence_lines": [1, 2]}],
              [{"path": "lib/short.js", "evidence_lines": [1, 2]},
               {"path": "lib/long.js", "evidence_lines": [1, 8]}]]
    coverage = replay.source_line_coverage(rows, groups)
    assert coverage["required_lines"] == coverage["covered_lines"] == 4
    assert [item["path"] for item in coverage["groups"]] == ["lib/short.js", "lib/short.js"]


def quality_case(identifier, ranks, *, kind="present", multi=False):
    return {"dataset": "example", "query_id": identifier, "kind": kind,
            "multi_file": multi, "evidence_ranks": ranks, "candidate_evidence_ranks": [1 for _ in ranks],
            "source_line_coverage": {"covered_lines": 3, "required_lines": 5} if ranks else {"covered_lines": 0, "required_lines": 0},
            "match_status": "candidates", "tokens": 100, "delivered": [{"id": "decoy"}]}


def test_quality_retains_absent_decoys_and_requires_every_multifile_group():
    rows = [quality_case("single", [1]), quality_case("multi", [2, None], multi=True),
            quality_case("missing", [], kind="absent")]
    metric = replay.quality(rows)
    assert metric["delivered_complete"] == 1 and metric["covered_groups"] == 2
    assert metric["multi_file_complete"] == 0 and metric["multi_file_queries"] == 1
    assert metric["absent_returning_decoys"] == 1 and metric["absent_statuses"] == {"candidates": 1}
    assert metric["weighted_gold_source_line_coverage"]["fraction"] == .6
    with pytest.raises(ValueError, match="unique"):
        replay.quality(rows + [rows[0]])


def test_changes_keep_each_gain_loss_group_change_and_rank_change():
    before = [quality_case("gain", [None]), quality_case("loss", [1, 2], multi=True),
              quality_case("rank", [4]), quality_case("absent", [], kind="absent")]
    after = [quality_case("gain", [3]), quality_case("loss", [None, 2], multi=True),
             quality_case("rank", [1]), quality_case("absent", [], kind="absent")]
    comparison = replay.changes(before, after)
    assert [row["query_id"] for row in comparison["complete_gains"]] == ["gain"]
    assert [row["query_id"] for row in comparison["complete_regressions"]] == ["loss"]
    assert len(comparison["group_changes"]) == 2 and len(comparison["rank_changes"]) == 3


def test_first_pass_does_not_select_better_later_outcomes():
    rows = [{"identity": {"dataset": "repo", "query_id": "q", "condition": "dense", "repetition": repetition},
             "status": "completed", "outcome": "miss" if repetition == 1 else "hit"}
            for repetition in (1, 2, 3)]
    assert replay.first_pass(rows) == [rows[0]]
    with pytest.raises(ValueError, match="Duplicate"):
        replay.first_pass(rows + [rows[0]])


def test_length_diagnostics_are_nearest_rank_counts_without_seconds_or_latency():
    diagnostic = replay.number_distribution(list(range(1, 21)))
    assert diagnostic == {"samples": 20, "sum": 210, "median": 10.5, "p95": 19, "min": 1, "max": 20}
    with pytest.raises(ValueError):
        replay.number_distribution([True])


def test_previous_audit_artifact_corruption_prevents_replay_before_source_processing(tmp_path):
    root, study = tmp_path / "repo", tmp_path / "repo/results/study"
    study.mkdir(parents=True)
    (root / "scripts").mkdir()
    auditor = root / "scripts/audit_qwen_reranker_benchmark.py"
    auditor.write_text("auditor")
    artifact = study / "searches.jsonl.gz"
    artifact.write_bytes(b"original")
    prior = {"checks_passed": True, "completed_conditions": 1728, "unique_queries": 144,
             "completed_benchmark_pair_forwards": 43200, "auditor_sha256": replay.sha(auditor.read_bytes()),
             "artifact_sha256": {artifact.name: replay.sha(artifact.read_bytes())}}
    (study / "independent-audit.json").write_text(json.dumps(prior))
    artifact.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="artifact changed"):
        replay.bound_input_audit(root, study)
