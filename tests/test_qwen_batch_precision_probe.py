"""CPU precision-diagnostic contracts; fake native arrays/model, no MLX load."""
from __future__ import annotations

import gzip
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import weakref

import numpy as np
import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/probe_qwen_batch_precision.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("precision_probe_tested", SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def built(ids, index):
    return {"input_ids": ids, "source": "delivered", "source_sha256": f"source-{index}",
            "delivered_sha256": f"delivered-{index}", "query_sha256": "query", "total_tokens": len(ids)}


def fake_native(*, batch_hidden_shift=0, batch_head_shift=.25, tied=True):
    calls = []
    def project(final):
        calls.append(("projection", final.shape))
        logits = np.zeros((len(final), 1, 5), dtype=np.float32)
        logits[:, :, 3] = final[:, :, 0]
        logits[:, :, 4] = final[:, :, 1]
        if len(final) > 1:
            logits[:, :, 3] += batch_head_shift
        return logits
    class Backbone:
        embed_tokens = SimpleNamespace(as_linear=project)
        def __call__(self, inputs):
            calls.append(("backbone", inputs.shape))
            prefix = np.cumsum(inputs, axis=1).astype(np.float32)
            positions = np.broadcast_to(np.arange(inputs.shape[1], dtype=np.float32), inputs.shape)
            output = np.stack((prefix, positions), axis=-1)
            if len(inputs) > 1:
                output += batch_hidden_shift
            return output
    model = SimpleNamespace(args=SimpleNamespace(tie_word_embeddings=tied), model=Backbone(), lm_head=project)
    def evaluate(array):
        calls.append(("eval", array.shape))
    mx = SimpleNamespace(array=np.array, eval=evaluate, float32=np.float32)
    return model, mx, calls


def test_import_free_preflight_loads_no_weights_or_native_runtime():
    code = (f"import runpy,sys;sys.path.insert(0,{str(SCRIPT.parent)!r});sys.argv=['probe'];"
            f"runpy.run_path({str(SCRIPT)!r},run_name='__main__');"
            "assert 'mlx.core' not in sys.modules and 'mlx_lm' not in sys.modules")
    result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, check=True, timeout=20)
    data = json.loads(result.stdout)
    assert not data["weights_loaded"] and not data["network"] and data["model_calls"] == 0
    assert data["batch_sizes"] == [2, 4, 8]


@pytest.mark.parametrize("tied", [False, True])
def test_shared_hidden_control_isolates_head_shape_without_another_backbone(tied):
    model, mx, calls = fake_native(tied=tied)
    receipts = []
    operations = probe.Operations(receipts.append)
    pairs = [built([1, 2], 0), built([3, 4, 5, 6], 1), built([7, 8, 9], 2)]
    result = probe.probe_set(model, pairs, "case", 3, 4, mx, operations, batch_sizes=(2,))
    assert [group["pair_indexes"] for group in result["batched"]["2"]["groups"]] == [[0, 2], [1]]
    assert [row["pair_index"] for row in result["batched"]["2"]["rows"]] == [0, 1, 2]
    assert operations.completed == {"sequential_backbone": 3, "sequential_projection": 3,
        "sequential_materialized_projection": 3, "batched_backbone": 2, "batched_projection": 2,
        "materialized_batched_projection": 2, "per_row_projection": 3}
    assert sum(kind == "backbone" for kind, _ in calls) == 5
    assert sum(kind == "projection" for kind, _ in calls) == 13
    for index, row in enumerate(result["batched"]["2"]["rows"]):
        baseline = result["sequential"][index]
        assert row["input_ids_sha256"] == probe.batch.digest(pairs[index]["input_ids"])
        assert row["last_hidden"]["values"] == baseline["last_hidden"]["values"]
        assert row["last_hidden_difference_from_sequential"]["max_absolute"] == 0
        assert row["per_row_projection_on_same_batched_hidden_score"] == baseline["score"]
        assert row["original_batched_projection_score"] == row["materialized_batched_projection_on_same_hidden_score"]
    assert result["batched"]["2"]["rows"][0]["original_batched_projection_score"] != result["sequential"][0]["score"]
    assert result["shared_hidden_reuse"] and result["diagnostic_only_not_reranking_latency"]
    assert len(receipts) == 2 * sum(operations.completed.values())


def test_backbone_difference_uses_correct_original_pair_and_saved_vectors():
    model, mx, _ = fake_native(batch_hidden_shift=.125, batch_head_shift=0)
    result = probe.probe_set(model, [built([1, 2], 0), built([3, 4, 5], 1)], "case", 3, 4,
                             mx, probe.Operations(lambda _: None), batch_sizes=(2,))
    for row in result["batched"]["2"]["rows"]:
        assert row["last_hidden_difference_from_sequential"] == {"max_absolute": .125, "mean_absolute": .125, "dimensions": 2}
        values = np.asarray(row["last_hidden"]["values"], dtype=np.float32)
        assert probe.sha(values.tobytes()) == row["last_hidden"]["float32_sha256"]


def test_original_head_evaluates_before_hidden_materialization_and_serialization(monkeypatch):
    model, mx, calls = fake_native()
    original = probe.hidden_record
    def serialize(final, native):
        calls.append(("serialize_hidden", final.shape))
        return original(final, native)
    monkeypatch.setattr(probe, "hidden_record", serialize)
    probe.probe_set(model, [built([1, 2], 0)], "case", 3, 4, mx,
                    probe.Operations(lambda _: None), batch_sizes=(2,))
    assert calls[:5] == [("backbone", (1, 2)), ("projection", (1, 1, 2)),
                        ("eval", (1, 1, 5)), ("eval", (1, 1, 2)), ("serialize_hidden", (1, 1, 2))]


def test_started_failed_operation_is_preserved_without_completion():
    receipts = []
    operations = probe.Operations(receipts.append)
    def fail():
        raise RuntimeError("native failure")
    with pytest.raises(RuntimeError, match="native failure"):
        operations.call("batched_backbone", {"set_id": "case", "pair_indexes": [0, 1]}, fail)
    assert [row["event"] for row in receipts] == ["started", "failed"]
    assert operations.attempted == {"batched_backbone": 1} and operations.completed == {}
    assert receipts[-1]["error_type"] == "RuntimeError"


@pytest.mark.parametrize("values", [[], [{"id": "case", "pairs": [], "gold": []}],
    [{"id": "case", "pairs": [{"query": "q", "source": "s", "path": "leaked"}]}],
    [{"id": "case", "pairs": [{"query": "", "source": "s"}]}],
    [{"id": "case", "pairs": [{"query": "q", "source": ""}]}],
    [{"id": "same", "pairs": [{"query": "q", "source": "s"}]}] * 2])
def test_metadata_empty_or_duplicate_inputs_cannot_enter_probe(tmp_path, values):
    path = tmp_path / "pairs.json"
    path.write_text(json.dumps(values))
    with pytest.raises(ValueError):
        probe.load_sets(path)


def test_witness_comparison_detects_input_drift_and_reports_scores_without_new_tolerance():
    pair = built([1, 2], 0)
    constructed = {key: value for key, value in pair.items() if key not in ("source", "input_ids")}
    score = probe.prototype.binary_score(1, 0)
    reference = constructed | score | {"input_ids_sha256": probe.batch.digest(pair["input_ids"])}
    exact = probe.witness_comparison(reference, constructed, score, reference["input_ids_sha256"])
    assert exact["exact_score_match"] and all(value == 0 for value in exact["absolute_score_errors"].values())
    changed = probe.witness_comparison(reference, constructed, probe.prototype.binary_score(1.2, 0), reference["input_ids_sha256"])
    assert not changed["exact_score_match"] and changed["absolute_score_errors"]["yes_logit"] == pytest.approx(.2)
    with pytest.raises(ValueError, match="input differs"):
        probe.witness_comparison(reference, constructed, score, "wrong tokens")


def test_original_witness_loading_restores_original_indexes_after_length_sorted_batches(tmp_path):
    path = tmp_path / "scores.jsonl.gz"
    row = {"status": "completed", "identity": {"parity_set": "case", "batch_size": 2},
           "reranker": {"results": [{"source_sha256": "first"}, {"source_sha256": "second"}],
                        "batch_receipts": [{"pairs": [{"pair_index": 1, "input_ids_sha256": "second-tokens"},
                                                       {"pair_index": 0, "input_ids_sha256": "first-tokens"}]}]}}
    with gzip.open(path, "wt") as stream:
        stream.write(json.dumps(row) + "\n")
    result = probe.load_witnesses(path)
    assert result[("case", 2)][0]["input_ids_sha256"] == "first-tokens"
    assert result[("case", 2)][1]["input_ids_sha256"] == "second-tokens"


def test_reference_plan_keeps_exact_all_sets_and_peers_while_removing_classification(tmp_path, monkeypatch):
    groups = [{"id": "case", "pairs": [{"query": "q", "source": "s"}]}]
    pairs, reference = tmp_path / "pairs.json", tmp_path / "reference.json"
    pairs.write_text(json.dumps(groups))
    reference.write_text(json.dumps({"parity_sets": [groups[0] | {"classification": "fixed original diagnostic"}]}))
    metadata = {"runtime": {"errors": [], "packages": {}, "qwen3_source_sha256": "native",
                            "batching_supporting_sources": {}}, "checkpoint": {"model_id": "pinned", "revision": "fixed", "files": {}},
                "implementation_sha256": {}, "packages": {}}
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    monkeypatch.setattr(probe, "preflight", lambda _: metadata)
    result = probe.build_plan(Path("unused"), pairs, reference)
    assert result["source_reference"]["sha256"] == probe.sha(reference.read_bytes())
    assert result["set_count"] == result["pair_count"] == 1
    pairs.write_text(json.dumps([{**groups[0], "pairs": [{"query": "q", "source": "changed"}]}]))
    with pytest.raises(ValueError, match="complete original"):
        probe.build_plan(Path("unused"), pairs, reference)


def test_hidden_difference_rejects_mismatched_or_nonfinite_vectors():
    for after in ({"values": [1, 2]}, {"values": [float("nan")]}):
        with pytest.raises(ValueError):
            probe.hidden_difference({"values": [1]}, after)


def test_run_requires_owned_standalone_process_before_loading(monkeypatch, tmp_path):
    monkeypatch.setattr(probe.os, "getpid", lambda: 12)
    monkeypatch.setattr(probe.os, "getpgrp", lambda: 11)
    monkeypatch.setattr(probe.batch, "load_engine", lambda *args: pytest.fail("must not load"))
    with pytest.raises(ValueError, match="owned standalone"):
        probe.run(Path("unused"), Path("unused"), Path("unused"), tmp_path)


def test_write_new_refuses_to_replace_failure_evidence(tmp_path):
    path = tmp_path / "failure.json"
    probe.write_new(path, {"first": True})
    with pytest.raises(FileExistsError):
        probe.write_new(path, {"second": True})
    assert json.loads(path.read_text()) == {"first": True}


@pytest.mark.parametrize("failure", [False, True])
def test_full_run_releases_all_model_owners_and_preserves_failed_operation(tmp_path, monkeypatch, failure):
    pairs_path, plan_path, output = tmp_path / "pairs.json", tmp_path / "plan.json", tmp_path / "output"
    pairs_path.write_text(json.dumps([{"id": "case", "pairs": [{"query": "q", "source": "s"}]}]))
    output.mkdir()
    current = {"source_reference": None, "score_witnesses": None, "packages": {},
               "reranker": {"runtime": {"packages": {}, "qwen3_source_sha256": "native"}},
               "batch_sizes": [2], "max_padded_tokens": 16384,
               "implementation_sha256": {name: probe.sha((probe.ROOT / name).read_bytes()) for name in probe.SOURCE_FILES}}
    plan_path.write_text(json.dumps(current | {"frozen_at_utc": "fixed", "source_commit": "a" * 40}))
    monkeypatch.setattr(probe, "build_plan", lambda *args: current)
    monkeypatch.setattr(probe.os, "getpid", lambda: 12)
    monkeypatch.setattr(probe.os, "getpgrp", lambda: 12)
    monkeypatch.setattr(probe.prototype, "verify_witnesses", lambda *args: None)
    state = {}
    class Model(SimpleNamespace):
        pass
    class Engine(SimpleNamespace):
        pass
    def load(*args):
        base, mx, _ = fake_native()
        model = Model(**vars(base))
        if failure:
            def failing_backbone(*args):
                raise RuntimeError("failed native backbone")
            model.model = failing_backbone
        builder = SimpleNamespace(build=lambda *args: built([1, 2], 0), yes_id=[3], no_id=[4])
        engine = Engine(builder=builder, scorer=lambda: model)
        state["model"], state["engine"] = weakref.ref(model), weakref.ref(engine)
        def clear():
            assert state["model"]() is None and state["engine"]() is None
            state["cleaned"] = True
        mx.clear_cache, mx.get_active_memory, mx.get_peak_memory = clear, lambda: 0, lambda: 123
        return engine, {"packages": {}, "runtime": current["reranker"]["runtime"], "model": {}}, (model, None, mx)
    monkeypatch.setattr(probe.batch, "load_engine", load)
    if failure:
        with pytest.raises(RuntimeError, match="failed native backbone"):
            probe.run(Path("unused"), pairs_path, plan_path, output)
    else:
        probe.run(Path("unused"), pairs_path, plan_path, output)
    completion = json.loads((output / "completion.json").read_text())
    assert completion["complete"] is not failure
    assert completion["model_owners_released"] and state["cleaned"]
    assert completion["cleanup"]["mlx_active_memory_after_cleanup"] == 0
    assert completion["checkpoint_witnesses_final_passed"]
    if failure:
        assert completion["operation_counts"]["attempted_operations"] == {"sequential_backbone": 1}
        assert not completion["operation_counts"]["completed_operations"]
        assert "failed native backbone" in json.loads((output / "failure.json").read_text())["traceback"]
    else:
        assert completion["operation_counts"]["completed_operations"]["sequential_backbone"] == 1
