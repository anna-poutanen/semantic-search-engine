"""CPU-only injected tests: no weights, MLX runtime or network."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/qwen_reranker_batch.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("qwen_reranker_batch_tested", SCRIPT)
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)
sys.path.pop(0)


def built(ids):
    return {"input_ids": ids}


class Tokenizer:
    def encode(self, text, *, add_special_tokens):
        assert not add_special_tokens
        if text == "yes":
            return [3]
        if text == "no":
            return [4]
        return list(text.encode())


def test_preflight_does_not_import_native_model_or_load_weights():
    code = (
        f"import runpy,sys; sys.path.insert(0,{str(SCRIPT.parent)!r}); sys.argv=['batch']; "
        f"runpy.run_path({str(SCRIPT)!r},run_name='__main__'); "
        "assert 'mlx.core' not in sys.modules and 'mlx_lm' not in sys.modules"
    )
    output = subprocess.run([sys.executable, "-B", "-c", code], text=True, capture_output=True, check=True)
    result = json.loads(output.stdout)
    assert result["weights_loaded"] is False and result["network"] is False and result["model_calls"] == 0
    assert result["batch_sizes"] == [1, 2, 4, 8] and result["max_padded_tokens"] == 16384


@pytest.mark.parametrize("value", [True, 0, -1, 3, 16, 1.0, "2", None])
def test_rejects_unbounded_or_noninteger_batch_size(value):
    with pytest.raises(ValueError, match="Batch size"):
        batch.plan_batches([built([1])], value)


@pytest.mark.parametrize("value", [True, 0, -1, 16385, 1.0, "2", None])
def test_rejects_unbounded_or_noninteger_token_budget(value):
    with pytest.raises(ValueError, match="Padded-token budget"):
        batch.plan_batches([built([1])], 2, max_padded_tokens=value)


def test_stable_length_sort_original_index_ties_and_final_singleton():
    pairs = [built([1] * length) for length in [5, 2, 3, 2, 9]]
    assert batch.plan_batches(pairs, 2) == [[1, 3], [2, 0], [4]]
    assert batch.plan_batches(pairs, 1) == [[0], [1], [2], [3], [4]]


@pytest.mark.parametrize("size", [1, 2, 4, 8])
def test_no_dropped_rows_and_real_allocated_budget(size):
    pairs = [built([1] * length) for length in [100, 30, 8192, 8192, 8192, 100, 8192, 8192, 8192]]
    groups = batch.plan_batches(pairs, size)
    indexes = [index for group in groups for index in group]
    assert sorted(indexes) == list(range(len(pairs)))
    for group in groups:
        layout = batch.batch_layout([pairs[index] for index in group])
        assert len(group) <= size
        assert layout["allocated_input_tokens"] <= 16384
        assert layout["real_input_tokens"] + layout["padding_tokens"] == layout["allocated_input_tokens"]


def test_no_silent_drop_when_one_sequence_exceeds_budget():
    with pytest.raises(ValueError, match="no sequence is dropped"):
        batch.plan_batches([built([1]), built([1] * 101)], 8, max_padded_tokens=100)
    with pytest.raises(ValueError, match="8192"):
        batch.plan_batches([built([1] * 8193)], 8)
    with pytest.raises(ValueError, match="16384"):
        batch.batch_layout([built([1] * 3000)] * 8)


@pytest.mark.parametrize("ids", [[], [-1], [True], [1.0], ["1"], np.array([1]), None])
def test_bad_token_sequences_are_rejected(ids):
    with pytest.raises(ValueError):
        batch.plan_batches([built(ids)], 2)


def test_layout_reports_zero_offset_right_padding_and_real_final_positions():
    layout = batch.batch_layout([built([5, 6]), built([7, 8, 9, 10])], pad_id=42)
    assert layout == {"actual_batch_size": 2, "sequence_lengths": [2, 4], "padded_sequence_length": 4,
                      "real_input_tokens": 6, "allocated_input_tokens": 8, "padding_tokens": 2,
                      "padding_ratio": .25, "pad_token_id": 42, "padding_side": "right", "position_offset": 0,
                      "attention_mask": "native_causal", "last_token_positions": [1, 3],
                      "singleton_original_path": False}


def fake_model(tied=False):
    calls = []

    class Backbone:
        embed_tokens = SimpleNamespace()

        def __call__(self, inputs):
            calls.append(("backbone", inputs.copy()))
            # Each output depends only on its own prefix, modeling causal isolation.
            hidden = np.cumsum(inputs, axis=1).astype(float)
            positions = np.broadcast_to(np.arange(inputs.shape[1]), inputs.shape)
            return np.stack([hidden, positions], axis=-1)

    def project(final):
        calls.append(("head", final.copy()))
        assert final.shape[1] == 1
        output = np.zeros((len(final), 1, 5))
        output[:, :, 3] = final[:, :, 0] / 100
        output[:, :, 4] = final[:, :, 1] / 100
        return output

    model = SimpleNamespace(args=SimpleNamespace(tie_word_embeddings=tied), model=Backbone(), lm_head=project)
    model.model.embed_tokens.as_linear = project
    return model, calls


@pytest.mark.parametrize("tied", [False, True])
def test_last_real_positions_and_invariance_to_longer_peers_and_padding_values(tied):
    model, calls = fake_model(tied)
    short, long = built([20, 21]), built([30, 31, 32, 33, 34])
    reference = batch.score_batch(model, [short], 3, 4, np.array)
    paired = batch.score_batch(model, [short, long], 3, 4, np.array, pad_id=99999)
    padded_other = batch.score_batch(model, [short, long], 3, 4, np.array, pad_id=0)
    assert paired[0] == padded_other[0] == reference[0]
    assert calls[2][1].tolist() == [[20, 21, 99999, 99999, 99999], [30, 31, 32, 33, 34]]
    assert calls[3][1].tolist() == [[[41, 1]], [[160, 4]]]
    assert paired[1] == batch.score_batch(model, [long], 3, 4, np.array)[0]


def test_every_singleton_delegates_exact_original_function(monkeypatch):
    pair = built([20, 21])
    observed = []
    monkeypatch.setattr(batch.prototype, "score_pair", lambda *args: observed.append(args) or {"original": True})
    def array(value):
        pytest.fail("original function owns arrays")

    def evaluate(value):
        pytest.fail("original function owns evaluation")
    assert batch.score_batch("model", [pair], 3, 4, array, evaluate) == [{"original": True}]
    assert observed == [("model", pair, 3, 4, array, evaluate)]


def test_one_final_graph_evaluation_and_no_full_sequence_head():
    model, calls = fake_model()
    evaluated = []
    result = batch.score_batch(model, [built([1, 2]), built([3, 4, 5])], 3, 4, np.array, evaluated.append)
    assert len(evaluated) == 1 and evaluated[0].shape == (2, 1, 5)
    assert len(calls) == 2 and len(result) == 2
    assert all(np.isfinite(value) for score in result for value in score.values())


@pytest.mark.parametrize("bad", ["backbone", "head", "nonfinite"])
def test_unexpected_model_outputs_fail(bad):
    model, _ = fake_model()
    if bad == "backbone":
        model.model = lambda inputs: np.ones((2, 1, 4))
    elif bad == "head":
        model.lm_head = lambda final: np.ones((2, 3, 5))
    else:
        model.lm_head = lambda final: np.full((2, 1, 5), np.nan)
    with pytest.raises(ValueError):
        batch.score_batch(model, [built([1, 2]), built([3])], 3, 4, np.array)


def make_engine(*, scorer=None):
    receipts, verification = [], []
    builder = batch.prototype.PairBuilder(Tokenizer())
    def default(pairs):
        return [batch.prototype.binary_score(len(pair["input_ids"]) / 1000, 0) for pair in pairs]
    engine = batch.BatchScoringEngine(builder, scorer or default, lambda: verification.append(True), receipts.append)
    return engine, receipts, verification


def test_engine_restores_original_order_and_receipts_account_actual_forwards():
    engine, receipts, verification = make_engine()
    pairs = [{"query": "q", "source": source} for source in ["12345", "12", "123", "12", "123456789"]]
    result = engine.score("r", pairs, batch_size=2)
    assert verification == [True, True]
    assert len(receipts) == 6 and [row["event"] for row in receipts] == ["started", "completed"] * 3
    completed = result["batch_receipts"]
    assert [[item["pair_index"] for item in receipt["pairs"]] for receipt in completed] == [[1, 3], [2, 0], [4]]
    assert [row["source_sha256"] for row in result["results"]] == [batch.prototype.sha256(p["source"].encode()) for p in pairs]
    assert result["counts"]["attempted_batch_forwards"] == result["counts"]["completed_batch_forwards"] == 3
    assert result["counts"]["attempted_pairs"] == result["counts"]["completed_pairs"] == 5
    assert completed[-1]["layout"]["singleton_original_path"]
    assert result["counts"]["completed_real_input_tokens"] == sum(row["total_tokens"] for row in result["results"])
    assert result["counts"]["completed_padding_tokens"] == sum(row["layout"]["padding_tokens"] for row in completed)
    assert result["pairs_sha256"] == batch.digest(pairs)
    assert all("source" not in row and "input_ids" not in row for row in result["results"])


def test_original_singleton_scheduling_and_fresh_repeated_requests():
    seen = []

    def scorer(pairs):
        seen.append(pairs[0]["source_sha256"])
        return [batch.prototype.binary_score(1, 0)]

    engine, _, _ = make_engine(scorer=scorer)
    pairs = [{"query": "q", "source": source} for source in ["longer", "x", "medium"]]
    engine.score("one", pairs, batch_size=1)
    result = engine.score("two", pairs, batch_size=1)
    expected = [batch.prototype.sha256(pair["source"].encode()) for pair in pairs]
    assert seen == expected + expected
    assert result["counts"]["completed_batch_forwards"] == 6
    assert result["counts"]["requests"] == 2


def test_builder_is_original_literal_template_and_truncation_logic():
    engine, receipts, _ = make_engine()
    query, source = "Find return", "\ufeffdef café():\r\n    return '🚀'\n"
    reference = batch.prototype.PairBuilder(Tokenizer()).build(query, source)
    result = engine.score("literal", [{"query": query, "source": source}], batch_size=8)
    assert result["results"][0]["delivered_sha256"] == reference["delivered_sha256"]
    assert receipts[0]["pairs"][0]["input_ids_sha256"] == batch.digest(reference["input_ids"])
    assert result["results"][0]["total_tokens"] == reference["total_tokens"]


def test_no_label_metadata_allowed_before_builder_or_scorer():
    engine, receipts, verification = make_engine(scorer=lambda pairs: pytest.fail("must not score"))
    with pytest.raises(ValueError, match="literal query/source"):
        engine.score("r", [{"query": "q", "source": "s", "gold": []}], batch_size=4)
    assert not receipts and not verification and engine.counts()["attempted_batch_forwards"] == 0


def test_budget_failure_before_any_model_attempt():
    engine, receipts, _ = make_engine(scorer=lambda pairs: pytest.fail("must not score"))
    with pytest.raises(ValueError, match="no sequence is dropped"):
        engine.score("r", [{"query": "q", "source": "s"}], batch_size=8, max_padded_tokens=1)
    assert not receipts and engine.counts()["attempted_pairs"] == 0


def test_partial_failure_records_failed_batch_without_counting_completion():
    calls = []

    def scorer(pairs):
        calls.append(True)
        if len(calls) == 2:
            raise RuntimeError("injected forward failure")
        return [batch.prototype.binary_score(1, 0) for _ in pairs]

    engine, receipts, verification = make_engine(scorer=scorer)
    with pytest.raises(RuntimeError, match="injected"):
        engine.score("r", [{"query": "q", "source": "s"}] * 5, batch_size=2)
    assert [row["event"] for row in receipts] == ["started", "completed", "started", "failed"]
    assert engine.counts()["attempted_batch_forwards"] == 2 and engine.counts()["completed_batch_forwards"] == 1
    assert engine.counts()["attempted_pairs"] == 4 and engine.counts()["completed_pairs"] == 2
    assert receipts[-1]["error_type"] == "RuntimeError" and verification == [True]


@pytest.mark.parametrize("malformation", ["count", "nan", "math", "fields"])
def test_bad_scalar_receipts_are_rejected(malformation):
    score = batch.prototype.binary_score(1, 0)
    if malformation == "nan":
        score["yes_logit"] = float("nan")
    elif malformation == "math":
        score["yes_rank_signal"] = .1
    elif malformation == "fields":
        score["extra"] = 1
    scores = [] if malformation == "count" else [score]
    engine, receipts, _ = make_engine(scorer=lambda pairs: scores)
    with pytest.raises(ValueError):
        engine.score("r", [{"query": "q", "source": "s"}])
    assert receipts[-1]["event"] == "failed" and engine.completed == 0


def test_missing_runtime_blocks_loading_before_weight_bytes_or_native_import(monkeypatch):
    monkeypatch.setattr(batch, "runtime_preflight", lambda: {"errors": ["missing pinned runtime"]})
    monkeypatch.setattr(batch.prototype, "checkpoint_preflight", lambda *args, **kwargs: pytest.fail("must not read weights"))
    with pytest.raises(ValueError, match="missing pinned"):
        batch.load_engine("unused", lambda value: None)


def test_supporting_source_preflight_rejects_changed_attention_or_rope(tmp_path, monkeypatch):
    qwen = tmp_path / "qwen3.py"
    qwen.write_text("qwen")
    monkeypatch.setattr(batch.prototype, "runtime_preflight", lambda: {"qwen3_source_path": str(qwen), "errors": []})
    for name in batch.SUPPORTING_SOURCE_SHA256:
        (tmp_path / name).write_text("changed")
    result = batch.runtime_preflight()
    assert len(result["errors"]) == 2
    assert set(result["batching_supporting_sources"]) == {"base.py", "rope_utils.py"}


def test_changed_supporting_source_blocks_import(monkeypatch):
    runtime = {"batching_supporting_sources": {name: {"path": name, "sha256": expected}
               for name, expected in batch.SUPPORTING_SOURCE_SHA256.items()}}
    monkeypatch.setattr(batch.prototype, "file_hash", lambda path: "changed")
    monkeypatch.setattr(batch.prototype, "verified_qwen3_module", lambda runtime: pytest.fail("must not import"))
    with pytest.raises(ValueError, match="changed before import"):
        batch.verified_qwen3_module(runtime)


def test_shadow_imported_attention_module_is_rejected(tmp_path, monkeypatch):
    runtime = {"batching_supporting_sources": {name: {"path": str(tmp_path / name), "sha256": expected}
               for name, expected in batch.SUPPORTING_SOURCE_SHA256.items()}}
    for name in batch.SUPPORTING_SOURCE_SHA256:
        (tmp_path / name).write_text("fake")
    (tmp_path / "shadow.py").write_text("shadow")
    monkeypatch.setattr(batch.prototype, "file_hash", lambda path: batch.SUPPORTING_SOURCE_SHA256[path.name])
    monkeypatch.setattr(batch.prototype, "verified_qwen3_module", lambda runtime: "qwen")
    monkeypatch.setitem(sys.modules, "mlx_lm.models.base", SimpleNamespace(__file__=str(tmp_path / "shadow.py")))
    with pytest.raises(ValueError, match="origin differs"):
        batch.verified_qwen3_module(runtime)
