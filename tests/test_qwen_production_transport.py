"""Real CPU loopback requests to an injected worker; no model/dependency changes."""
import json
from types import SimpleNamespace
import threading
import urllib.request

import pytest

from semantic_search.qwen_reranker import QwenRerankerClient
from semantic_search.qwen_worker import ScoringEngine, create_server
from semantic_search.qwen_reranker_core import PairBuilder, binary_score
from test_qwen_production_core import CharacterTokenizer


TOKEN = "private-cpu-test-token-" + "x" * 40


@pytest.fixture
def worker():
    calls, events = [], []
    def score(built):
        calls.append(built)
        return binary_score(2, -.5)
    engine = ScoringEngine(PairBuilder(CharacterTokenizer()), score, emit=events.append)
    server = create_server(engine, TOKEN)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01})
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    yield engine, calls, events, url
    server.shutdown()
    server.server_close()
    thread.join(2)


def test_singleton_literal_requests_repeatedly_forward_without_caching(worker, monkeypatch):
    engine, calls, events, url = worker
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    client = QwenRerankerClient(url, TOKEN, timeout=2)
    snippet = SimpleNamespace(text="def literal():\n    return 'café'\n", path="NOT_IN_INPUT", symbol="NOT_IN_INPUT")
    first = client.score("literal behavior", snippet)
    second = client.score("literal behavior", snippet)
    assert first.score == second.score == 2.5
    assert len(calls) == 2 and all("NOT_IN_INPUT" not in json.dumps(call) for call in calls)
    assert [event["event"] for event in events] == ["started", "completed"] * 2
    assert engine.counters()["completed_forwards"] == client.status()["counts"]["completed_forwards"] == 2


@pytest.mark.parametrize("payload", [
    b'{"request_id":"x","query":"x","source":"def x(): pass","batch_size":2}',
    b'{"request_id":"x","query":"x","query":"y","source":"def x(): pass"}',
    b'{"request_id":"x","query":"x","source":NaN}',
    b'{"request_id":"x","query":"x","source":""}',
])
def test_invalid_requests_never_forward(worker, payload):
    engine, calls, _, url = worker
    request = urllib.request.Request(url + "/score", data=payload,
        headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with pytest.raises(urllib.error.HTTPError) as caught:
        opener.open(request, timeout=2)
    assert caught.value.code == 400
    assert engine.counters()["attempted_forwards"] == 0 and calls == []


def test_auth_and_browser_origin_cannot_reach_engine(worker):
    _, calls, _, url = worker
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for headers in ({"Authorization": "Bearer " + "z" * 40},
                    {"Authorization": "Bearer " + TOKEN, "Origin": "https://example.invalid"}):
        request = urllib.request.Request(url + "/status", headers=headers)
        with pytest.raises(urllib.error.HTTPError) as caught:
            opener.open(request, timeout=2)
        assert caught.value.code == 403
    assert calls == []


@pytest.mark.parametrize("url", ["https://localhost:1", "http://example.com", "http://user:secret@localhost",
                                "http://localhost/score", "http://localhost:0", "http://localhost:"])
def test_client_rejects_remote_or_ambiguous_addresses(url):
    with pytest.raises(ValueError):
        QwenRerankerClient(url, TOKEN)


def test_client_rejects_wrong_hash_arithmetic_and_shape():
    client = QwenRerankerClient("http://127.0.0.1:1", TOKEN)
    snippet = SimpleNamespace(text="def x(): pass")
    from semantic_search.qwen_reranker_core import sha256
    good = {"request_id": "x", "batch_size": 1,
        "counts": {"requests": 1, "attempted_pairs": 1, "completed_pairs": 1,
                   "attempted_forwards": 1, "completed_forwards": 1}, "result": {"source_sha256": sha256(snippet.text.encode()),
        "query_sha256": sha256(b"q"), "total_tokens": 20, **binary_score(2, 1)}}
    variants = [dict(good, result=[]), dict(good, batch_size=2), dict(good, batch_size=True),
                dict(good, request_id="wrong"), dict(good, counts={}),
                dict(good, counts=dict(good["counts"], completed_pairs=True))]
    for field, bad in (("source_sha256", "bad"), ("logit_difference", 9), ("yes_rank_signal", 1),
                       ("yes_logit", float("nan")), ("total_tokens", True)):
        variants.append(dict(good, result=dict(good["result"], **{field: bad})))
    for value in variants:
        client._request = lambda *args, value=value: value
        with pytest.raises(RuntimeError):
            client.score("q", snippet, request_id="x")


def test_failed_native_evaluation_preserves_attempted_not_completed():
    events = []
    def fail(_):
        raise RuntimeError("injected native failure")
    engine = ScoringEngine(PairBuilder(CharacterTokenizer()), fail, emit=events.append)
    with pytest.raises(RuntimeError):
        engine.score("fail", "behavior", "def x(): pass")
    assert engine.counters()["attempted_forwards"] == 1
    assert engine.counters()["completed_forwards"] == 0
    assert [event["event"] for event in events] == ["started", "failed"]


def test_engine_close_releases_scorer_even_if_handler_still_holds_engine():
    import weakref
    class Weights:
        pass
    weights = Weights()
    reference = weakref.ref(weights)
    def scorer(built, resident=weights):
        return binary_score(2, 1)
    engine = ScoringEngine(PairBuilder(CharacterTokenizer()), scorer)
    server = create_server(engine, TOKEN)
    del weights, scorer
    assert reference() is not None
    engine.close()
    assert reference() is None
    server.server_close()


@pytest.mark.parametrize("owned", [False, True])
def test_incomplete_http_reply_cleans_owned_worker_or_quarantines_external_client(owned):
    from email.message import Message
    from http.client import IncompleteRead
    cleanups = []
    def cleanup():
        cleanups.append("owned stop completed")
        return {"owned_group_stopped": True}
    class Reply:
        headers = Message()
        headers["Content-Type"] = "application/json"
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def read(self, limit):
            raise IncompleteRead(b'{"request_id":', 200)
    client = QwenRerankerClient("http://127.0.0.1:1", TOKEN,
                                on_failure=cleanup if owned else None)
    client._opener = SimpleNamespace(open=lambda *args, **kwargs: Reply())
    with pytest.raises(RuntimeError, match="Cannot reach"):
        client.score("behavior", SimpleNamespace(text="def x(): pass"))
    assert client.safe_for_embedding is owned
    assert len(cleanups) == int(owned)
    with pytest.raises(RuntimeError, match="disabled"):
        client.status()
