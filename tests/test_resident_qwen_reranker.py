"""CPU fake observation tests; never import MLX or read checkpoint weights."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("resident_test_subject", ROOT / "scripts/resident_qwen_reranker.py")
resident = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resident)


class Builder:
    def build(self, query, source):
        return {"input_ids": [1, 2, 3], "source": source,
                "source_sha256": resident.sha(source.encode()), "query_sha256": resident.sha(query.encode()),
                "delivered_sha256": resident.sha(source.encode()), "total_tokens": 3,
                "truncated": False, "delivered_lines": 1}


def test_all_real_callback_events_and_observed_results():
    events, calls, verified = [], [], []
    def score(built):
        calls.append(built)
        return {"logit_difference": 3.0, "yes_logit": 2.0, "no_logit": -1.0, "yes_rank_signal": 0.95}
    engine = resident.ScoringEngine(Builder(), score, lambda: verified.append(True), events.append)
    pairs = [{"query": "find this", "source": "return 1"}, {"query": "find this", "source": "return 2"}]
    result = engine.score("request-a", pairs)
    assert len(calls) == engine.attempted == engine.completed == 2
    assert [event["event"] for event in events] == ["started", "completed", "started", "completed"]
    assert [event["pair_index"] for event in events] == [0, 0, 1, 1]
    assert result["pairs_sha256"] == resident.pairs_digest(pairs)
    assert all("input_ids" not in row and "source" not in row for row in result["results"])
    assert len(verified) == 2 and result["forward_s"] >= 0
    assert events[0]["input_ids_sha256"] == resident.sha(json.dumps([1, 2, 3], separators=(",", ":")).encode())


def test_partial_failure_keeps_attempted_and_completed_separate():
    events = []
    def fail(_):
        raise RuntimeError("forward failed")
    engine = resident.ScoringEngine(Builder(), fail, lambda: None, events.append)
    with pytest.raises(RuntimeError, match="forward failed"):
        engine.score("broken", [{"query": "q", "source": "source"}])
    assert engine.attempted == 1 and engine.completed == 0
    assert [event["event"] for event in events] == ["started", "failed"]
    assert events[-1]["error_type"] == "RuntimeError"


@pytest.mark.parametrize("pair", [{"query": "q", "source": "s", "symbol": "gold"}, {"query": "q"}, {}, "s"])
def test_scorer_rejects_extra_or_missing_pair_inputs(pair):
    engine = resident.ScoringEngine(Builder(), lambda _: {}, lambda: None, lambda _: None)
    with pytest.raises(ValueError, match="literal query/source"):
        engine.score("id", [pair])
    assert engine.attempted == 0


@pytest.mark.parametrize("pairs", [[], [{}] * 51, None])
def test_bounded_pair_list(pairs):
    engine = resident.ScoringEngine(Builder(), lambda _: {}, lambda: None, lambda _: None)
    with pytest.raises(ValueError, match="one to fifty"):
        engine.score("id", pairs)


def test_warmup_recorded_but_marked_and_scores_not_cached():
    events = []
    engine = resident.ScoringEngine(Builder(), lambda _: {"logit_difference": 0}, lambda: None, events.append)
    engine.score("warmup", [resident.WARMUP], warmup=True)
    engine.score("repetition-1", [resident.WARMUP])
    engine.score("repetition-2", [resident.WARMUP])
    assert engine.requests == engine.attempted == engine.completed == 3
    assert [event["warmup"] for event in events[::2]] == [True, False, False]
