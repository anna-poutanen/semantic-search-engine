"""Real retrieval/packing with injected CPU embeddings and worker; no models."""
from __future__ import annotations

from contextlib import nullcontext
import gzip
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("optimization_pipeline_tested", ROOT / "scripts/qwen_optimization_pipeline.py")
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


def configuration(retrieval="dense", shortlist=5, batch_size=2):
    return {"id": retrieval if not batch_size else f"{retrieval}_k{shortlist}_b{batch_size}",
            "retrieval": retrieval, "shortlist": shortlist, "batch_size": batch_size}


class Reply:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def read(self):
        return json.dumps(self.value).encode()


class Worker:
    def __init__(self, *, fail=False, wrong_batch=False):
        self.requests = []
        self.fail, self.wrong_batch = fail, wrong_batch

    def open(self, request, timeout):
        assert timeout == 240
        assert request.full_url == "http://127.0.0.1:43210/score"
        assert request.headers["Authorization"] == "Bearer test-private-token"
        payload = json.loads(request.data)
        assert set(payload) == {"request_id", "pairs", "batch_size"}
        assert all(set(pair) == {"query", "source"} for pair in payload["pairs"])
        self.requests.append(payload)
        if self.fail:
            raise RuntimeError("injected actual worker failure")
        results = [{"source_sha256": pipeline.sha(pair["source"].encode()),
                    "query_sha256": pipeline.sha(pair["query"].encode()),
                    "logit_difference": index // 2} for index, pair in enumerate(payload["pairs"])]
        return Reply({"request_id": payload["request_id"], "pairs_sha256": pipeline.sha(pipeline.canonical(payload["pairs"])),
                      "batch_size": 8 if self.wrong_batch else payload["batch_size"], "results": results,
                      "batch_receipts": [{"event": "completed", "pairs": len(results)}]})


def candidate_pairs():
    return [(SimpleNamespace(id=f"s{index:02}", text=f"source-{index}"), 1.0 - index / 100)
            for index in range(50)]


def test_scores_only_fresh_prefix_restores_rank_ties_and_never_adds_tail():
    worker, pairs = Worker(), candidate_pairs()
    selected, observed, elapsed, order = pipeline.select_prefix(
        pairs, "Find behavior", configuration(shortlist=5), worker,
        {"url": "http://127.0.0.1:43210"}, "test-private-token", "first")
    assert [pair["source"] for pair in worker.requests[0]["pairs"]] == [f"source-{index}" for index in range(5)]
    assert order == ["s04", "s02", "s03", "s00", "s01"]
    assert [snippet.id for snippet, _ in selected] == order
    assert len(selected) == 5 and observed["batch_receipts"] and elapsed >= 0
    assert all(snippet.id not in order for snippet, _ in pairs[5:])


def test_identical_query_and_source_are_scored_again_on_each_actual_request():
    worker, pairs = Worker(), candidate_pairs()
    for identifier in ("first", "second"):
        pipeline.select_prefix(pairs, "same query", configuration(), worker,
                               {"url": "http://127.0.0.1:43210"}, "test-private-token", identifier)
    assert len(worker.requests) == 2
    assert worker.requests[0]["pairs"] == worker.requests[1]["pairs"]
    assert worker.requests[0]["request_id"] != worker.requests[1]["request_id"]


def test_baseline_keeps_full_pool_and_does_not_call_scorer():
    pairs = candidate_pairs()
    selected, scored, elapsed, order = pipeline.select_prefix(
        pairs, "query", configuration(shortlist=50, batch_size=0), None, {}, "", "baseline")
    assert selected is pairs and scored is None and elapsed == 0
    assert order == [snippet.id for snippet, _ in pairs]


def test_wrong_worker_batch_or_missing_full_pool_is_rejected():
    with pytest.raises(ValueError, match="Worker input/result identity"):
        pipeline.select_prefix(candidate_pairs(), "query", configuration(), Worker(wrong_batch=True),
                               {"url": "http://127.0.0.1:43210"}, "test-private-token", "wrong")
    with pytest.raises(ValueError, match="full50"):
        pipeline.select_prefix(candidate_pairs()[:49], "query", configuration(), None, {}, "", "short")


@pytest.mark.parametrize("change", [{"shortlist": True}, {"shortlist": 0}, {"shortlist": 51},
                                  {"batch_size": True}, {"batch_size": 3}, {"retrieval": "bm25"},
                                  {"id": "wrong"}, {"gold": []}])
def test_invalid_configuration_cannot_change_inference_or_ranking(change):
    with pytest.raises(ValueError):
        pipeline.check_configuration(configuration() | change)


class Owner:
    metadata = {"id": "fake-test", "revision": "fixed", "dimensions": 2}

    def __init__(self):
        self._model = object()
        self.query_calls = self.document_calls = self.document_texts = 0
        self.closed = False

    def embed_query(self, text):
        self.query_calls += 1
        return np.asarray([1.0, 0.0], dtype=np.float32)

    def embed_documents(self, texts):
        self.document_calls += 1
        self.document_texts += len(texts)
        return np.asarray([[1.0, index / 100] for index, _ in enumerate(texts)], dtype=np.float32)

    def counts(self):
        return {"completed_query_calls": self.query_calls, "attempted_query_calls": self.query_calls,
                "completed_document_calls": self.document_calls, "attempted_document_calls": self.document_calls,
                "document_texts": self.document_texts, "attempted_document_texts": self.document_texts}

    def close(self):
        self._model, self.closed = None, True


def fixture(tmp_path, monkeypatch, *, failure=False):
    from semantic_search.backend import SearchBackend
    source_dir = tmp_path / "corpus/src"
    source_dir.mkdir(parents=True)
    text = "\n".join(f"def behavior_{index}():\n    return {index}\n" for index in range(50))
    (source_dir / "code.py").write_text(text)
    sources = {"src/code.py": text}
    query = {"id": "q", "kind": "present", "query": "Find the numeric behavior implementation.",
             "targets": [{"path": "src/code.py", "symbol": "behavior_0", "evidence_lines": [1, 2]}]}
    entry = {"id": "fake", "corpus": "corpus", "source_root": "src", "source_exclusions": [], "language": "python"}
    configs = [configuration(shortlist=50, batch_size=0), configuration(), configuration("hybrid")]
    schedule = [{"dataset": "fake", "query_id": "q", "condition": config["id"], "repetition": repetition}
                for repetition in (1, 2) for config in configs]
    frozen = {"conditions": configs, "settings": pipeline.packing.SETTINGS.copy(), "schedule": schedule,
              "clock_integrity": pipeline.clocks.POLICY.copy(),
              "planned_requests": len(schedule), "embedding": Owner.metadata.copy(), "cache_seed": {"fake": None},
              "sources": [{"id": "fake", "queries": [{"query_id": "q",
                            "groups": pipeline.previous.normalized_groups(query, sources)}]}]}
    owner, backends = Owner(), []

    def backend(*args, **kwargs):
        instance = SearchBackend(*args, **kwargs)
        backends.append(instance)
        return instance

    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    monkeypatch.setattr(pipeline, "observed_embedder", lambda: owner)
    monkeypatch.setattr(pipeline, "search_backend", backend)
    monkeypatch.setattr(pipeline.packing, "pinned_tokenizer", nullcontext)
    output = tmp_path / "actual-test-stage"
    output.mkdir()
    return output, [(entry, {"queries": [query]}, sources)], frozen, Worker(fail=failure), owner, backends


def rows(path):
    with gzip.open(path, "rt") as stream:
        return [json.loads(line) for line in stream]


def test_full_pipeline_uses_fresh_embeddings_real_packer_exact_ledgers_and_caps(tmp_path, monkeypatch):
    output, entries, frozen, worker, owner, backends = fixture(tmp_path, monkeypatch)
    completion = pipeline.run_pipeline(output, entries, frozen, worker,
                                       {"url": "http://127.0.0.1:43210"}, "test-private-token")
    searches = rows(output / "searches.jsonl.gz")
    assert completion["complete"] and completion["completed_requests"] == len(frozen["schedule"]) == 6
    assert [row["identity"] for row in searches] == frozen["schedule"]
    assert owner.query_calls == 7  # Six actual requests plus one excluded setup warmup.
    assert owner.document_calls == 1 and owner.document_texts == 50
    assert len(worker.requests) == 4 and all(len(request["pairs"]) == 5 for request in worker.requests)
    assert completion["embedding_owner_closed"] and owner.closed
    assert all(backend.status()["closed"] for backend in backends)
    assert not completion["paired_candidate_mismatches"] and not completion["repeated_candidate_mismatches"]
    assert len(rows(output / "candidate-pools.jsonl.gz")) == 2  # Dense/hybrid receipt pools.
    assert len(rows(output / "source-content.jsonl.gz")) == 50
    for row in searches:
        assert row["candidate_count"] == 50
        assert row["counts_after"]["completed_query_calls"] == row["counts_before"]["completed_query_calls"] + 1
        pipeline.previous.validate_caps(row["response"], entries[0][2])
        assert row["shortlist_count"] == (5 if row["configuration"]["batch_size"] else 50)
        assert len(row["ranking_order"]) == row["shortlist_count"]
        assert row["response"]["retrieval"] == row["configuration"]["retrieval"]
        assert row["vector"]["dtype"] == "float32" and len(row["vector"]["values"]) == 2
        if row["configuration"]["batch_size"]:
            assert row["reranker"]["batch_receipts"]
    setups = json.loads((output / "setups.json").read_text())
    assert setups[0]["backend_closed"] and setups[0]["excluded_from_latency"]


def test_actual_worker_failure_keeps_attempt_row_counts_and_closes_owners(tmp_path, monkeypatch):
    output, entries, frozen, worker, owner, backends = fixture(tmp_path, monkeypatch, failure=True)
    with pytest.raises(RuntimeError, match="actual worker failure"):
        pipeline.run_pipeline(output, entries, frozen, worker,
                              {"url": "http://127.0.0.1:43210"}, "test-private-token")
    searches = rows(output / "searches.jsonl.gz")
    assert [row["status"] for row in searches] == ["completed", "failed"]
    assert searches[-1]["active_phase"] == "reranker_http"
    assert searches[-1]["counts_after"]["completed_query_calls"] == 3
    assert len(worker.requests) == 1 and owner.closed and backends[0].status()["closed"]
    completion = json.loads((output / "completion.json").read_text())
    assert not completion["complete"] and completion["attempted_requests"] == 2 and completion["completed_requests"] == 1
    assert completion["embedding_owner_closed"]
    assert json.loads((output / "failure.json").read_text())["error_type"] == "RuntimeError"


def test_borrowed_index_close_never_closes_shared_model():
    owner = Owner()
    pipeline.Borrowed(owner).close()
    assert not owner.closed and owner._model is not None


def test_changed_caps_fail_before_model_creation(tmp_path, monkeypatch):
    output, entries, frozen, worker, _, _ = fixture(tmp_path, monkeypatch)
    frozen["settings"]["max_results"] = 9
    monkeypatch.setattr(pipeline, "observed_embedder", lambda: pytest.fail("must reject before model ownership"))
    with pytest.raises(ValueError, match="packing caps"):
        pipeline.run_pipeline(output, entries, frozen, worker, {}, "")


class FakeClocks:
    def __init__(self):
        self.wall, self.perf = 1_000_000_000_000, 1_000_000_000

    def wall_clock(self):
        return self.wall

    def perf_clock(self):
        return self.perf

    def suspend(self):
        self.wall += 3_000_000_000
        self.perf += 100_000_000


@pytest.mark.parametrize("when", ["within_query", "between_queries", "during_setup"])
def test_suspension_aborts_records_failed_clocks_and_closes_model_without_retry(tmp_path, monkeypatch, when):
    output, entries, frozen, worker, owner, backends = fixture(tmp_path, monkeypatch)
    fake = FakeClocks()
    original_guard = pipeline.clocks.ClockIntegrity
    monkeypatch.setattr(pipeline.clocks, "ClockIntegrity", lambda: original_guard(wall_clock=fake.wall_clock, perf_clock=fake.perf_clock))
    original_embed = owner.embed_query
    def embed(text):
        result = original_embed(text)
        if (when == "during_setup" and owner.query_calls == 1) or (when == "within_query" and owner.query_calls == 2):
            fake.suspend()
        return result
    owner.embed_query = embed
    original_append = pipeline.append
    def append(stream, row):
        original_append(stream, row)
        if when == "between_queries" and row.get("status") == "completed":
            fake.suspend()
    monkeypatch.setattr(pipeline, "append", append)
    with pytest.raises(pipeline.clocks.ClockIntegrityError):
        pipeline.run_pipeline(output, entries, frozen, worker,
                              {"url": "http://127.0.0.1:43210"}, "test-private-token")
    completion = json.loads((output / "completion.json").read_text())
    assert not completion["complete"] and completion["embedding_owner_closed"] and owner.closed
    assert backends[0].status()["closed"]
    assert completion["clock_baseline"] == {"wall_time_ns": 1_000_000_000_000, "perf_counter_ns": 1_000_000_000,
                                             "utc": pipeline.clocks.utc_from_ns(1_000_000_000_000)}
    assert completion["clock_integrity"] == pipeline.clocks.POLICY
    failure = json.loads((output / "failure.json").read_text())
    assert failure["error_type"] == "ClockIntegrityError" and not failure["clock_failure"]["consistent"]
    searches = rows(output / "searches.jsonl.gz")
    if when == "during_setup":
        assert searches == [] and owner.query_calls == 1 and worker.requests == []
        setup = json.loads((output / "setups.json").read_text())[0]
        assert not setup["clock_end"]["consistent"] and setup["counts_after"]["completed_query_calls"] == 1
    else:
        assert searches[-1]["status"] == "failed"
        assert "clock_start" in searches[-1] and "clock_end" in searches[-1]
        assert not searches[-1]["clock_failure"]["consistent"]
        assert owner.query_calls == 2 and worker.requests == []
        assert completion["attempted_requests"] == (2 if when == "between_queries" else 1)
        assert completion["completed_requests"] == (1 if when == "between_queries" else 0)


def test_clock_policy_drift_is_rejected_before_model_ownership(tmp_path, monkeypatch):
    output, entries, frozen, worker, _, _ = fixture(tmp_path, monkeypatch)
    frozen["clock_integrity"] = frozen["clock_integrity"] | {"max_absolute_drift_ns": 3_000_000_000}
    monkeypatch.setattr(pipeline, "observed_embedder", lambda: pytest.fail("must not own model"))
    with pytest.raises(ValueError, match="clock policy differs"):
        pipeline.run_pipeline(output, entries, frozen, worker, {}, "")
