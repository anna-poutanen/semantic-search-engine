"""CPU injection for native-smoke control flow; CPU output cannot certify a model."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

from semantic_search.qwen_reranker_core import PairBuilder, binary_score, sha256

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("qwen_mcp_smoke_cpu", ROOT / "scripts/smoke_qwen_mcp.py")
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class CPUEmbedder:
    metadata = {"id": "cpu-injected-fixture", "revision": "no-model", "dimensions": 2}
    def embed_documents(self, texts, *, cancel_check=None):
        if cancel_check:
            cancel_check()
        return np.asarray([[1., 0.] if "def backoff_delay" in text else [0., 1.] for text in texts])
    def embed_query(self, query):
        return np.asarray([1., 0.])
    def close(self):
        pass


class Tokenizer:
    def encode(self, text, add_special_tokens=False):
        return [90001 if text == "yes" else 90002] if text in {"yes", "no"} else [ord(char) for char in text]


class CPUOwner:
    """Explicit CPU observer. It creates no model, child process or auth secret."""
    def __init__(self, *, log_dir, **kwargs):
        self.directory = Path(log_dir)
        self.directory.mkdir()
        self.client = self
        self.builder = PairBuilder(Tokenizer())
        self.requests = self.attempted = self.completed = 0
        self.receipt = {"cpu_injected": True, "model_calls": 0}
        self.events = []
    def counts(self):
        return {"requests": self.requests, "attempted_pairs": self.attempted, "completed_pairs": self.completed,
                "attempted_forwards": self.attempted, "completed_forwards": self.completed}
    def start(self):
        self.score("Locate addition.", SimpleNamespace(text="def add(a,b):\n    return a+b\n"), request_id="warmup", warmup=True)
    def score(self, query, snippet, *, request_id, warmup=False):
        self.requests += 1
        built = self.builder.build(query, snippet.text)
        self.attempted += 1
        identity = {"request_id": request_id, "warmup": warmup, "batch_size": 1,
                    "query_sha256": built["query_sha256"], "source_sha256": built["source_sha256"],
                    "delivered_sha256": built["delivered_sha256"], "total_tokens": built["total_tokens"],
                    "input_ids_sha256": sha256(json.dumps(built["input_ids"], separators=(",", ":")).encode()),
                    "forward_number": self.attempted}
        self.events.append(dict(identity, event="started"))
        score = binary_score(2. if "def backoff_delay" in snippet.text else 0., 0.)
        self.completed += 1
        self.events.append(dict(identity, event="completed", score=score))
        raw = {key: value for key, value in built.items() if key not in {"source", "input_ids"}} | score
        response = {"request_id": request_id, "batch_size": 1, "result": raw, "counts": self.counts()}
        return SimpleNamespace(score=score["logit_difference"], response=response)
    def status(self):
        return {"counts": self.counts(), "cpu_injected": True}
    def close(self):
        (self.directory / "forwards.jsonl").write_text("".join(json.dumps(row) + "\n" for row in self.events))
        self.receipt.update(stop={"owned_group_stopped": True, "exit_code": 0, "forced_kill": False, "errors": []},
                            auth_token_removed=True, private_cleanup_errors=[], worker_stop={"clean_stop": True,
                            "listener_closed": True, "counts": self.counts(), "auth_token_removed": True,
                            "auth_cleanup_error": None})


class LiveLedgerCPUOwner(CPUOwner):
    def score(self, *args, **kwargs):
        result = super().score(*args, **kwargs)
        (self.directory / "forwards.jsonl").write_text("".join(json.dumps(row) + "\n" for row in self.events))
        return result


def cpu_freeze(tmp_path):
    output, work = tmp_path / "results", tmp_path / "work"
    smoke.freeze(output, work, Path("unused-python"), Path("unused-checkpoint"),
                 runtime_override={"embedding": CPUEmbedder.metadata, "cpu_injected": True})
    return output, work


def test_cpu_full_http_mcp_saved_edit_flow_cannot_certify_native_inference(tmp_path):
    output, work = cpu_freeze(tmp_path)
    certificate = smoke.run(output, work, Path("unused"), Path("unused"), timeout=10,
        owner_factory=LiveLedgerCPUOwner, embedder_factory=CPUEmbedder, native=False)
    assert certificate["checks_passed"] is False and certificate["cpu_injected"] is True
    result = json.loads((output / "results.json").read_text())
    assert result["checks_passed"] is True and result["native_inference"] is False
    assert result["native_ledger"]["measured_forwards"] == 96
    assert result["embedding_counts"] == {"query_calls": 6, "document_calls": 2, "document_texts": 62}
    assert [row["phase"] for row in result["steps"]] == [
        "http_before", "mcp_before", "watcher_saved_edit", "http_after", "mcp_after", "mcp_explicit_disable"]
    assert result["steps"][-1]["raw_tool_arguments"] == {"query": smoke.QUERY, "rerank": False}
    assert all(row["effective_http_settings"]["candidates"] == 24 for row in result["steps"] if "effective_http_settings" in row)
    assert result["source_stable"] and result["lifecycle_passed"]
    assert set(certificate["artifact_sha256"]) == {"frozen-plan.json", "run-start.json", "results.json", "native-forwards.jsonl"}
    for name, expected in certificate["artifact_sha256"].items():
        assert smoke.sha((output / name).read_bytes()) == expected
    assert not list((work / "repo/.git").glob("refs/heads/*"))  # No fixture/project commit was created.


def test_freeze_and_run_refuse_overwrite_or_fixture_drift_before_starting_owner(tmp_path):
    output, work = cpu_freeze(tmp_path)
    with pytest.raises(AssertionError, match="overwrite"):
        smoke.freeze(output, work, Path("unused"), Path("unused"), runtime_override={})
    (work / "repo/src/sample.py").write_text("def changed():\n    return 1\n")
    def owner(**kwargs):
        raise AssertionError("owner must not start")
    with pytest.raises(AssertionError, match="fixture changed"):
        smoke.run(output, work, Path("unused"), Path("unused"), owner_factory=owner,
                  embedder_factory=CPUEmbedder, native=False)
    assert not (output / "run-start.json").exists()


def test_corrupted_native_ledger_cannot_pass(tmp_path):
    (tmp_path / "forwards.jsonl").write_text('{"event":"completed","warmup":true}\n')
    with pytest.raises(AssertionError, match="ledger/counters"):
        smoke.verify_ledger(tmp_path, {"completed_forwards": 97}, [])


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_failed_owner_start_is_preserved_and_cannot_retry(tmp_path, cleanup_fails):
    output, work = cpu_freeze(tmp_path)
    class FailedStart(CPUOwner):
        def start(self):
            raise RuntimeError("injected owned startup failure")
        def close(self):
            super().close()
            if cleanup_fails:
                raise PermissionError("injected unproven cleanup")
    certificate = smoke.run(output, work, Path("unused"), Path("unused"), owner_factory=FailedStart,
                            embedder_factory=CPUEmbedder, native=False)
    result = json.loads((output / "results.json").read_text())
    assert not certificate["checks_passed"] and not result["checks_passed"]
    assert result["failure"]["error"] == "injected owned startup failure"
    assert (output / "run-start.json").exists() and "owner" in result
    assert bool(result["cleanup_errors"]) is cleanup_fails
    with pytest.raises(AssertionError, match="retry/resume"):
        smoke.run(output, work, Path("unused"), Path("unused"), owner_factory=FailedStart,
                  embedder_factory=CPUEmbedder, native=False)


def test_git_gate_binds_exact_committed_source_and_plan_bytes(monkeypatch):
    raw = b"frozen source or plan bytes\n"
    commands = []
    def git(command, **kwargs):
        commands.append(command)
        if command[1:3] == ["show", "HEAD:scripts/missing.py"]:
            raise subprocess.CalledProcessError(128, command)
        if command[1] == "show":
            return raw
        return "current-head-commit\n"
    monkeypatch.setattr(smoke.subprocess, "check_output", git)
    assert smoke.committed_bindings({"scripts/frozen.py": smoke.sha(raw),
                                    "results/smoke/frozen-plan.json": smoke.sha(raw)}) == "current-head-commit"
    assert [row[2] for row in commands[:-1]] == ["HEAD:scripts/frozen.py", "HEAD:results/smoke/frozen-plan.json"]
    with pytest.raises(AssertionError, match="Committed frozen bytes differ"):
        smoke.committed_bindings({"scripts/frozen.py": "different"})
    with pytest.raises(AssertionError, match="must be committed"):
        smoke.committed_bindings({"scripts/missing.py": smoke.sha(raw)})
    with pytest.raises(AssertionError, match="Unsafe"):
        smoke.committed_bindings({"../outside": smoke.sha(raw)})


def test_first_sigterm_interrupts_then_repeated_sigterm_cannot_skip_cleanup(tmp_path):
    output, work = cpu_freeze(tmp_path)
    old_handler = signal.getsignal(signal.SIGTERM)
    class InterruptedOwner(CPUOwner):
        def start(self):
            os.kill(os.getpid(), signal.SIGTERM)
        def close(self):
            os.kill(os.getpid(), signal.SIGTERM)
            super().close()
    certificate = smoke.run(output, work, Path("unused"), Path("unused"), owner_factory=InterruptedOwner,
                            embedder_factory=CPUEmbedder, native=False)
    result = json.loads((output / "results.json").read_text())
    assert not certificate["checks_passed"] and result["failure"]["error_type"] == "KeyboardInterrupt"
    assert result["interrupt_signals"] == {"SIGTERM": 2} and not result["cleanup_errors"]
    assert result["owner"]["stop"]["owned_group_stopped"]
    assert signal.getsignal(signal.SIGTERM) is old_handler
    assert (output / "native-forwards.jsonl").exists()  # Even a failed attempt retains its partial raw ledger.


def test_sigint_during_cleanup_is_deferred_recorded_and_disqualifies_success(tmp_path):
    output, work = cpu_freeze(tmp_path)
    previous = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGINT)}
    class CleanupInterruptedOwner(LiveLedgerCPUOwner):
        def close(self):
            os.kill(os.getpid(), signal.SIGINT)
            super().close()
    certificate = smoke.run(output, work, Path("unused"), Path("unused"), timeout=10,
        owner_factory=CleanupInterruptedOwner, embedder_factory=CPUEmbedder, native=False)
    result = json.loads((output / "results.json").read_text())
    assert not certificate["checks_passed"] and not result["checks_passed"]
    assert result["native_ledger"]["completed"] == 97 and result["lifecycle_passed"]
    assert result["interrupt_signals"] == {"SIGINT": 1} and not result["cleanup_errors"]
    assert all(signal.getsignal(number) is handler for number, handler in previous.items())


@pytest.mark.parametrize("section,key,value", [
    ("stop", "forced_kill", True), ("stop", "errors", [{"error": "unproved"}]),
    ("stop", "owned_group_stopped", False), ("stop", "exit_code", -9),
    ("worker_stop", "auth_token_removed", False), ("worker_stop", "auth_cleanup_error", "EPERM"),
    ("worker_stop", "clean_stop", False), ("worker_stop", "listener_closed", False),
    ("worker_stop", "counts", {"requests": True}),
    (None, "auth_token_removed", False), (None, "private_cleanup_errors", ["EPERM"]),
])
def test_lifecycle_certificate_rejects_unproven_cleanup_or_counter_drift(tmp_path, section, key, value):
    owner = CPUOwner(log_dir=tmp_path / "owner")
    owner.start()
    owner.close()
    status = owner.status()
    assert smoke.clean_lifecycle(owner.receipt, status, True)
    if section:
        owner.receipt[section][key] = value
    else:
        owner.receipt[key] = value
    assert not smoke.clean_lifecycle(owner.receipt, status, True)
