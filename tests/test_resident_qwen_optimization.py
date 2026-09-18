"""CPU worker lifecycle tests with fake engines/server; no sockets or models."""
from __future__ import annotations

import gzip
import importlib.util
import io
import json
from pathlib import Path
import signal
import sys
from types import SimpleNamespace
import weakref

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/resident_qwen_optimization.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("resident_optimization_tested", SCRIPT)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
sys.path.pop(0)


class Engine:
    def __init__(self, model, emit):
        self.scorer = lambda: model
        self.emit = emit
        self.calls = []

    def score(self, request_id, pairs, *, batch_size=1, warmup=False):
        self.calls.append((request_id, len(pairs), batch_size, warmup))
        result = {"request_id": request_id, "results": [], "warmup": warmup}
        self.emit({"event": "fake_completed", "request_id": request_id})
        return result

    def counters(self):
        return {"requests": len(self.calls), "completed_pairs": sum(row[1] for row in self.calls),
                "completed_forwards": sum((row[1] + row[2] - 1) // row[2] for row in self.calls)}


class Model:
    pass


def arrange(tmp_path, monkeypatch, *, request=None, failure=None):
    output = tmp_path / "output"
    output.mkdir()
    token = tmp_path / "token.secret"
    token.write_text("a" * 48)
    ready, plan = tmp_path / "ready.json", tmp_path / "plan.json"
    plan.write_text(json.dumps({"implementation_sha256": {}, "reranker": {"packages": {},
        "runtime": {"packages": {"fake": "1"}, "qwen3_source_sha256": "fake-sha"}}}))
    monkeypatch.setattr(worker.importlib.metadata, "distributions", lambda: [])
    state = {"signals": {}, "closed": False, "cache_cleared": False, "calls": None}
    monkeypatch.setattr(worker.signal, "signal", lambda signum, callback: state["signals"].__setitem__(signum, callback))

    def load(checkpoint, emit):
        if failure == "load":
            raise RuntimeError("fake load failure")
        model = Model()
        state["model_ref"] = weakref.ref(model)
        engine = Engine(model, emit)
        state["engine_ref"] = weakref.ref(engine)
        state["calls"] = engine.calls

        def clear():
            assert state["model_ref"]() is None, "Model captured by closure/ownership tuple remains alive"
            assert state["engine_ref"]() is None, "Engine closure remains alive"
            state["cache_cleared"] = True

        mx = SimpleNamespace(clear_cache=clear, get_active_memory=lambda: 8, get_peak_memory=lambda: 1234)
        provenance = {"runtime": {"packages": {"fake": "1"}, "qwen3_source_sha256": "fake-sha"}}
        if failure == "runtime":
            provenance["runtime"]["qwen3_source_sha256"] = "wrong"
        return engine, provenance, (model, None, mx)

    class Server:
        server_port = 12345

        def __init__(self, address, handler):
            assert address == ("127.0.0.1", 0)
            self.handler = handler

        def handle_request(self):
            if request is not None:
                payload, authorization = request
                raw = json.dumps(payload).encode()
                handler = object.__new__(self.handler)
                handler.path = "/score"
                handler.headers = {"Authorization": authorization, "Content-Length": str(len(raw))}
                handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
                handler.send_error = lambda status, *args: state.setdefault("errors", []).append(status)
                handler.send_response = lambda status: state.setdefault("responses", []).append(status)
                handler.send_header = lambda *args: None
                handler.end_headers = lambda: None
                # HTTPServer catches handler exceptions and then returns to its owner loop.
                try:
                    handler.do_POST()
                except BaseException as error:
                    state["request_error"] = type(error).__name__
            state["signals"][signal.SIGTERM](signal.SIGTERM, None)

        def server_close(self):
            state["closed"] = True

    monkeypatch.setattr(worker.batch, "load_engine", load)
    monkeypatch.setattr(worker, "HTTPServer", Server)
    return output, ready, token, plan, state


def test_warmups_and_cleanup_release_all_model_owners_before_clear(tmp_path, monkeypatch):
    output, ready, token, plan, state = arrange(tmp_path, monkeypatch)
    worker.serve(tmp_path, output, ready, token, plan)
    assert state["calls"] == [("generic-warmup-b" + str(size), 8, size, True) for size in [1, 2, 4, 8]]
    assert state["closed"] and state["cache_cleared"]
    stopped = json.loads((output / "reranker-stop.json").read_text())
    assert stopped["clean_stop"] is True and stopped["listener_closed"] is True
    assert stopped["requests"] == 4 and stopped["completed_pairs"] == 32 and stopped["completed_forwards"] == 15
    assert stopped["mlx_active_memory_after_cleanup"] == 8 and stopped["mlx_process_lifetime_peak_bytes"] == 1234
    assert json.loads(ready.read_text())["frozen_plan_sha256"] == worker.sha(plan.read_bytes())
    with gzip.open(output / "forwards.jsonl.gz", "rt") as stream:
        assert len([json.loads(line) for line in stream]) == 4


@pytest.mark.parametrize("failure", ["load", "runtime"])
def test_initialization_failures_preserve_failure_and_stop_receipts(tmp_path, monkeypatch, failure):
    output, ready, token, plan, state = arrange(tmp_path, monkeypatch, failure=failure)
    with pytest.raises((ValueError, RuntimeError)):
        worker.serve(tmp_path, output, ready, token, plan)
    assert not ready.exists() and (output / "reranker-failure.json").exists()
    stopped = json.loads((output / "reranker-stop.json").read_text())
    assert stopped["clean_stop"] is False and stopped["listener_closed"] is False
    if failure == "runtime":
        assert state["cache_cleared"]


def test_authorized_literal_request_uses_requested_batch_size(tmp_path, monkeypatch):
    payload = {"request_id": "actual", "pairs": [{"query": "q", "source": "s"}], "batch_size": 4}
    output, ready, token, plan, state = arrange(tmp_path, monkeypatch, request=(payload, "Bearer " + "a" * 48))
    worker.serve(tmp_path, output, ready, token, plan)
    assert state["calls"][-1] == ("actual", 1, 4, False)
    assert state["responses"] == [200] and state["closed"] and state["cache_cleared"]


def test_unauthorized_request_does_not_score_or_fail_the_model_owner(tmp_path, monkeypatch):
    payload = {"request_id": "actual", "pairs": [{"query": "q", "source": "s"}], "batch_size": 4}
    output, ready, token, plan, state = arrange(tmp_path, monkeypatch, request=(payload, "Bearer wrong"))
    worker.serve(tmp_path, output, ready, token, plan)
    assert len(state["calls"]) == 4 and state["errors"] == [403]
    assert not (output / "reranker-failure.json").exists()


def test_unexpected_payload_field_fails_and_prevents_resuming_worker(tmp_path, monkeypatch):
    payload = {"request_id": "actual", "pairs": [{"query": "q", "source": "s"}], "batch_size": 4, "gold": []}
    output, ready, token, plan, state = arrange(tmp_path, monkeypatch, request=(payload, "Bearer " + "a" * 48))
    with pytest.raises(ValueError, match="Failed worker cannot resume"):
        worker.serve(tmp_path, output, ready, token, plan)
    assert len(state["calls"]) == 4 and state["errors"] == [500]
    assert state["closed"] and state["cache_cleared"]
    assert json.loads((output / "reranker-failure.json").read_text())["error_type"] == "ValueError"


def test_attempt_marker_refuses_before_loading_or_overwriting(tmp_path, monkeypatch):
    output, ready, token, plan, _ = arrange(tmp_path, monkeypatch)
    marker = output / "reranker-start.json"
    marker.write_text("old attempt")
    with pytest.raises(ValueError, match="No resume/overwrite"):
        worker.serve(tmp_path, output, ready, token, plan)
    assert marker.read_text() == "old attempt" and not ready.exists()
