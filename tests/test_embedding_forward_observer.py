"""Native-forward observation contracts, using invented CPU doubles only."""
import hashlib
import json

import numpy as np
import pytest

from semantic_search.cancellation import SearchCancelled
from semantic_search.embeddings import DIMENSIONS, MAX_TOKENS, QUERY_PROMPT, QwenEmbedder, normalize_rows


class Tokenizer:
    def __call__(self, texts, **kwargs):
        return {"input_ids": [list(text.encode())[:kwargs["max_length"]] for text in texts]}


class Cache:
    def __init__(self):
        self.clears = 0

    def clear_cache(self):
        self.clears += 1


@pytest.fixture(autouse=True)
def forbid_loading(monkeypatch):
    def require_double(embedder):
        assert embedder._model is not None, "these tests must never load a checkpoint"

    monkeypatch.setattr(QwenEmbedder, "_ensure_loaded", require_double)


def fake(*, batch_size=4, max_padded_tokens=8192):
    embedder = QwenEmbedder(batch_size=batch_size, max_padded_tokens=max_padded_tokens,
                            progress=False)
    embedder._model, embedder._tokenizer, embedder._mx = object(), Tokenizer(), Cache()
    calls = []

    def forward(rows):
        calls.append([list(row) for row in rows])
        vectors = np.zeros((len(rows), DIMENSIONS), dtype=np.float32)
        for index, row in enumerate(rows):
            vectors[index, :3] = len(row), row[-1], sum(row)
        return normalize_rows(vectors)

    embedder._embed_batch = forward
    return embedder, calls


def text_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def token_hash(tokens):
    return hashlib.sha256(json.dumps(tokens, separators=(",", ":")).encode()).hexdigest()


def test_exact_scheduled_batches_identity_output_parity_and_metadata():
    observed, calls = fake()
    control, control_calls = fake()
    texts = [chr(65 + i) * length for i, length in enumerate(
        [5000, 2048, 3000, 1000, 2048, 8192, 4096, 1000])]
    metadata, events = observed.metadata, []
    actual = observed.embed_documents(texts, forward_observer=events.append)
    np.testing.assert_array_equal(actual, control.embed_documents(texts))
    assert calls == control_calls
    assert observed._mx.clears == control._mx.clears == 5
    assert observed.metadata == metadata
    assert len(events) == 2 * len(calls) == 8
    assert [event["input_indices"] for event in events[::2]] == [
        [3, 7, 1, 4], [2, 6], [0], [5]]
    assert sorted(i for event in events[::2] for i in event["input_indices"]) == list(range(8))
    for index, (started, completed) in enumerate(zip(events[::2], events[1::2])):
        assert started["schema"] == "qwen_embedding_forward_v1"
        assert started["status"] == "started"
        assert completed["status"] == "completed"
        assert started["forward_index"] == completed["forward_index"] == index
        indices, lengths = started["input_indices"], started["token_lengths"]
        assert started["input_count"] == len(texts)
        assert started["batch_input_count"] == len(indices)
        assert lengths == [len(texts[i]) for i in indices]
        assert started["padded_tokens"] == len(indices) * max(lengths) <= 8192
        assert started["padded_shape"] == [len(indices), max(lengths)]
        assert started["expected_output_shape"] == [len(indices), DIMENSIONS]
        assert started["input_sha256"] == [text_hash(texts[i]) for i in indices]
        assert started["token_ids_sha256"] == [token_hash(list(texts[i].encode())) for i in indices]
        assert completed["output_shape"] == [len(indices), DIMENSIONS]
        assert completed["output_dtype"] == "float32"
        assert 0 <= started["elapsed_seconds"] <= completed["elapsed_seconds"]
        assert 0 <= completed["duration_seconds"] <= (
            completed["elapsed_seconds"] - started["elapsed_seconds"])
        for key in started.keys() - {"status", "elapsed_seconds"}:
            assert started[key] == completed[key]


@pytest.mark.parametrize("method", ["embed_query", "embed_queries"])
def test_query_events_hash_actual_instruction_and_do_not_add_a_forward(method):
    embedder, calls = fake()
    events, query = [], "resolve pending work"
    request = query if method == "embed_query" else [query]
    actual = getattr(embedder, method)(request, forward_observer=events.append)
    assert actual.shape == ((DIMENSIONS,) if method == "embed_query" else (1, DIMENSIONS))
    assert len(calls) == 1
    assert events[0]["input_sha256"] == [text_hash(QUERY_PROMPT + query)]
    assert events[0]["token_lengths"] == [len(QUERY_PROMPT + query)]
    assert events[0]["token_ids_sha256"] == [token_hash(list((QUERY_PROMPT + query).encode()))]


def test_query_count_padded_budget_and_truncation_are_actual_forward_counts():
    embedder, calls = fake(batch_size=2)
    queries = ["x" * (MAX_TOKENS + 10), "short", "middle", "longer", "final"]
    events = []
    embedder.embed_queries(queries, forward_observer=events.append)
    completed = [event for event in events if event["status"] == "completed"]
    assert [len(rows) for rows in calls] == [2, 2, 1]
    assert len(completed) == len(calls) == 3
    assert sum(event["batch_input_count"] for event in completed) == len(queries)
    assert completed[-1]["input_indices"] == [0]
    assert completed[-1]["token_lengths"] == [MAX_TOKENS]
    assert completed[-1]["input_sha256"] == [text_hash(QUERY_PROMPT + queries[0])]
    assert completed[-1]["token_ids_sha256"] == [
        token_hash(list((QUERY_PROMPT + queries[0]).encode())[:MAX_TOKENS])]
    embedder, calls = fake()
    events = []
    embedder.embed_queries(["a" * 4097, "b" * 4097], forward_observer=events.append)
    assert len(calls) == len([event for event in events if event["status"] == "completed"]) == 2


def test_event_mutation_and_late_mutation_cannot_change_schedule_or_other_events():
    embedder, calls = fake(batch_size=1)
    control, _ = fake(batch_size=1)
    texts = ["longer", "a", "middle"]
    original = list(texts)
    retained, snapshots = [], []

    def mutate(event):
        snapshots.append(json.loads(json.dumps(event)))
        retained.append(event)
        event["input_indices"][:] = [999]
        event["token_lengths"][:] = [-1]
        event["padded_shape"][:] = [0]
        event["input_sha256"][:] = ["wrong"]
        event["token_ids_sha256"][:] = ["wrong"]
        event["schema"] = "wrong"
        # Hash identities bind the original encoded input, even if an external
        # callback changes the caller-owned input list for later batches.
        texts[:] = ["changed"] * len(texts)

    actual = embedder.embed_documents(texts, forward_observer=mutate)
    np.testing.assert_array_equal(actual, control.embed_documents(original))
    assert [rows[0] for rows in calls] == [list(text.encode()) for text in ["a", "longer", "middle"]]
    assert [event["input_indices"] for event in snapshots[::2]] == [[1], [0], [2]]
    assert snapshots[-1]["input_sha256"] == [text_hash("middle")]
    retained[0].clear()
    assert snapshots[1]["schema"] == "qwen_embedding_forward_v1"


def test_cancel_before_forward_emits_no_started_event():
    embedder, calls = fake()
    events = []

    def cancel():
        raise SearchCancelled("before forward")

    with pytest.raises(SearchCancelled, match="before forward"):
        embedder.embed_queries(["query"], cancel_check=cancel, forward_observer=events.append)
    assert calls == events == []


def test_post_forward_cancel_preserves_completed_work_but_no_checkpoint_or_progress():
    embedder, calls = fake(batch_size=1)
    events, checkpoint, progress = [], [], []

    def cancel():
        if calls:
            raise SearchCancelled("after forward")

    with pytest.raises(SearchCancelled, match="after forward"):
        embedder.embed_documents(["a", "bb"], cancel_check=cancel,
                                 forward_observer=events.append,
                                 batch_callback=lambda *args: checkpoint.append(args),
                                 progress_callback=lambda *args: progress.append(args))
    assert len(calls) == 1
    assert [event["status"] for event in events] == ["started", "completed"]
    assert checkpoint == progress == []


def test_observer_callback_and_existing_checkpoint_order():
    embedder, _ = fake(batch_size=1)
    timeline, original = [], embedder._embed_batch

    def forward(rows):
        timeline.append("forward")
        return original(rows)

    embedder._embed_batch = forward
    embedder.embed_documents(
        ["a", "bb"], cancel_check=lambda: timeline.append("check"),
        forward_observer=lambda event: timeline.append(event["status"]),
        batch_callback=lambda *_: timeline.append("checkpoint"),
        progress_callback=lambda *_: timeline.append("progress"))
    assert timeline == ["check", "started", "forward", "completed", "check",
                        "checkpoint", "progress"] * 2


def test_model_error_retains_prior_completed_and_failed_batch_without_more_forwards():
    embedder, calls = fake(batch_size=1)
    original, events = embedder._embed_batch, []
    failure = RuntimeError("invented failure")

    def forward(rows):
        if calls:
            calls.append(rows)
            raise failure
        return original(rows)

    embedder._embed_batch = forward
    with pytest.raises(RuntimeError) as error:
        embedder.embed_queries(["a", "bb", "ccc"], forward_observer=events.append)
    assert error.value is failure
    assert len(calls) == 2
    assert [event["status"] for event in events] == ["started", "completed", "started", "failed"]
    assert events[-1]["forward_index"] == 1
    assert events[-1]["failure_phase"] == "forward"
    assert events[-1]["exception_type"] == "builtins.RuntimeError"
    assert events[-1]["duration_seconds"] >= 0
    assert "output_shape" not in events[-1]


@pytest.mark.parametrize("bad", [np.ones((1, DIMENSIONS - 1)), None])
def test_invalid_output_reports_validation_failure_and_never_completion(bad):
    embedder, calls = fake()
    events = []

    def invalid(rows):
        calls.append(rows)
        return bad

    embedder._embed_batch = invalid
    with pytest.raises((ValueError, AttributeError)):
        embedder.embed_queries(["a"], forward_observer=events.append)
    assert len(calls) == 1
    assert [event["status"] for event in events] == ["started", "failed"]
    assert events[-1]["failure_phase"] == "output_validation"


@pytest.mark.parametrize("status,expected_calls", [("started", 0), ("completed", 1)])
def test_observer_failure_propagates_without_fabricated_model_failure(status, expected_calls):
    embedder, calls = fake(batch_size=1)
    events, failure = [], RuntimeError("observer failed")

    def observer(event):
        events.append(event)
        if event["status"] == status:
            raise failure

    with pytest.raises(RuntimeError) as error:
        embedder.embed_queries(["a", "bb"], forward_observer=observer)
    assert error.value is failure
    assert len(calls) == expected_calls
    assert [event["status"] for event in events] == (
        ["started"] if status == "started" else ["started", "completed"])


def test_model_failure_keeps_original_error_if_failure_observer_also_fails():
    embedder, calls = fake()
    events = []
    model_error, observer_error = RuntimeError("model failed"), ValueError("ledger failed")

    def forward(rows):
        calls.append(rows)
        raise model_error

    def observer(event):
        events.append(event)
        if event["status"] == "failed":
            raise observer_error

    embedder._embed_batch = forward
    with pytest.raises(RuntimeError) as error:
        embedder.embed_query("query", forward_observer=observer)
    assert error.value is model_error
    assert error.value.__cause__ is observer_error
    assert len(calls) == 1
    assert [event["status"] for event in events] == ["started", "failed"]


def test_empty_input_never_loads_times_forwards_or_observes(monkeypatch):
    embedder = QwenEmbedder(model_path="/invented/missing", progress=False)
    events = []

    def forbidden():
        raise AssertionError("empty input must remain lazy")

    monkeypatch.setattr("semantic_search.embeddings.time.perf_counter", forbidden)
    assert embedder.embed_queries([], forward_observer=events.append).shape == (0, DIMENSIONS)
    assert embedder.embed_documents([], forward_observer=events.append).shape == (0, DIMENSIONS)
    assert events == []


@pytest.mark.parametrize("method,inputs", [
    ("embed_queries", ["query"]), ("embed_query", "query"),
    ("embed_documents", ["document"]), ("_embed_texts", ["text"]),
])
def test_noncallable_observer_is_rejected_before_loading(method, inputs):
    embedder = QwenEmbedder(model_path="/invented/missing", progress=False)
    with pytest.raises(TypeError, match="forward_observer must be callable"):
        getattr(embedder, method)(inputs, forward_observer=True)


def test_no_observer_does_not_time_or_hash_inference(monkeypatch):
    embedder, calls = fake()

    def forbidden(*_):
        raise AssertionError("ordinary inference must not perform observation work")

    monkeypatch.setattr("semantic_search.embeddings.time.perf_counter", forbidden)
    monkeypatch.setattr("semantic_search.embeddings.hashlib.sha256", forbidden)
    actual = embedder.embed_queries(["a", "bb"])
    assert actual.shape == (2, DIMENSIONS)
    assert len(calls) == 1
