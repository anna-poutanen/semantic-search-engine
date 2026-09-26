"""Small CPU evidence/ownership/persistence tests; never load MLX or large corpus."""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import selectors
import signal
import sqlite3
import subprocess
import sys

import numpy as np
import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
core = importlib.import_module("django_index_profile_core")
runner = importlib.import_module("run_django_index_profile")


class FakeTokenizer:
    def __call__(self, texts, *, truncation=False, max_length=None, **kwargs):
        values = [[ord(char) % 113 + 2 for char in text] + [1] for text in texts]
        if truncation:
            values = [tokens if len(tokens) <= max_length else tokens[:max_length - 1] + [1] for tokens in values]
        return {"input_ids": values}


class FakeBase:
    def __init__(self, **kwargs):
        self.batch_size = kwargs.get("batch_size", 4)
        self._model = self._tokenizer = self._mx = None
        self.metadata = {"id": "small-cpu-fake", "dimensions": 4, "batch_size": self.batch_size}
        self.forward_inputs = []

    def _ensure_loaded(self):
        if self._model is None:
            self._model, self._tokenizer = object(), FakeTokenizer()

    def _embed_batch(self, tokens):
        self.forward_inputs.append(tokens)
        result = np.asarray([[len(value), sum(value), value[0], value[-1]] for value in tokens], dtype=np.float32)
        return result / np.linalg.norm(result, axis=1, keepdims=True)

    def _embed_texts(self, texts):
        self._ensure_loaded()
        values = self._tokenizer(texts, padding=False, truncation=True, max_length=16, add_special_tokens=True)["input_ids"]
        order = sorted(range(len(values)), key=lambda i: (len(values[i]), i))
        output = np.empty((len(texts), 4), dtype=np.float32)
        for start in range(0, len(order), self.batch_size):
            selected = order[start:start + self.batch_size]
            output[selected] = self._embed_batch([values[i] for i in selected])
        return output

    def embed_documents(self, texts, *, cancel_check=None):
        if cancel_check:
            cancel_check()
        return self._embed_texts(texts)

    def embed_query(self, query):
        from semantic_search.embeddings import QUERY_PROMPT
        return self._embed_texts([QUERY_PROMPT + query])[0]

    def close(self):
        self._model = self._tokenizer = self._mx = None


class MemoryJournal:
    def __init__(self):
        self.rows = []

    def emit(self, event, **fields):
        row = {"event": event, **fields}
        self.rows.append(row)
        return row


@pytest.fixture
def snippets(tmp_path):
    from semantic_search.corpus import extract_snippets
    directory = tmp_path / "repo/src"
    directory.mkdir(parents=True)
    (directory / "items.py").write_text("def alpha():\n    return 1\n\ndef beta(value):\n    return value + 1\n")
    return extract_snippets(tmp_path / "repo", "src", max_lines=160, overlap_lines=40)


def recorded(tmp_path, snippets, base=FakeBase):
    journal = MemoryJournal()
    instance = core.recording_embedder_class(base)(batch_size=1, journal=journal, work=tmp_path)
    instance.bind_snippets(snippets)
    instance.bind_queries([{"id": "test-query", "query": "find alpha"}])
    return instance, journal


def test_recording_preserves_actual_math_and_original_matrix_order(tmp_path, snippets):
    instance, journal = recorded(tmp_path, list(reversed(snippets)))
    texts = [core.document(snippet) for snippet in reversed(snippets)]
    plain = FakeBase(batch_size=1)
    expected = plain.embed_documents(texts)
    actual = instance.embed_documents(texts)
    assert np.array_equal(actual, expected)
    assert instance.forward_inputs == plain.forward_inputs
    completed = [row for row in journal.rows if row["event"] == "embedding_operation_completed"][0]
    assert completed["input_sha256"] == [core.digest(text.encode()) for text in texts]
    assert np.array_equal(np.load(core.resolve(completed["matrix_file"])), expected)
    starts = [row for row in journal.rows if row["event"] == "native_batch_started"]
    assert len(starts) == 2
    for row in starts:
        item = row["inputs"][0]
        assert item["input_ids"][-1] == 1
        assert item["input_ids_sha256"] == core.digest(json.dumps(item["input_ids"], separators=(",", ":")).encode())
        assert item["truncated"] and item["actual_tokens"] == 16 < item["full_tokens"]
    assert instance.counts == {"document_inputs": 2, "query_inputs": 0, "attempted_forwards": 2, "completed_forwards": 2}


def test_query_hashes_include_real_production_instruction(tmp_path, snippets):
    instance, journal = recorded(tmp_path, snippets)
    actual = instance.embed_query("find alpha")
    assert actual.dtype == np.float32 and actual.shape == (4,)
    complete = [row for row in journal.rows if row["event"] == "embedding_operation_completed"][0]
    from semantic_search.embeddings import QUERY_PROMPT
    assert complete["query_id"] == "test-query"
    assert complete["input_sha256"] == [core.digest((QUERY_PROMPT + "find alpha").encode())]
    assert len(instance.initialization) == 1


@pytest.mark.parametrize("texts", [[], [""], [None], [42], ["unbound source"]])
def test_unbound_or_malformed_docs_fail_before_native_call(tmp_path, snippets, texts):
    instance, journal = recorded(tmp_path, snippets)
    with pytest.raises(ValueError):
        instance.embed_documents(texts)
    assert instance._model is None and not journal.rows and not instance.operations


def test_unknown_query_refused_without_model(tmp_path, snippets):
    instance, journal = recorded(tmp_path, snippets)
    with pytest.raises(ValueError, match="Unbound query"):
        instance.embed_query("changed query")
    assert instance._model is None and not journal.rows


@pytest.mark.parametrize("fault", ["nan", "zero", "shape", "dtype"])
def test_bad_native_outputs_preserve_started_attempt_without_false_completion(tmp_path, snippets, fault):
    class Broken(FakeBase):
        def _embed_batch(self, tokens):
            result = super()._embed_batch(tokens)
            if fault == "nan":
                result[0, 0] = np.nan
            elif fault == "zero":
                result[:] = 0
            elif fault == "shape":
                result = result[:, :2]
            else:
                result = result.astype(np.float64)
            return result
    instance, journal = recorded(tmp_path, snippets, Broken)
    with pytest.raises(ValueError, match="Native batch output invalid"):
        instance.embed_documents([core.document(snippets[0])])
    assert instance.active is None and not instance.operations
    assert instance.counts["attempted_forwards"] == 1 and instance.counts["completed_forwards"] == 0
    assert any(row["event"] == "native_batch_started" for row in journal.rows)
    assert not any(row["event"] in ("native_batch_completed", "embedding_operation_completed") for row in journal.rows)


def test_schedule_balances_rotation_and_preserves_first_warmup_measured_separation():
    queries = [f"q{i}" for i in range(8)]
    providers = ["bm25", "dense", "hybrid"]
    rows = core.schedule(queries, providers)
    assert rows[:3] == [{"phase": "first", "sweep": -1, "query_id": "q0", "provider": provider} for provider in providers]
    assert len(rows) == 315
    for provider in providers:
        assert sum(row["provider"] == provider and row["phase"] == "measured" for row in rows) == 80
        assert sum(row["provider"] == provider and row["phase"] == "warmup" for row in rows) == 24
    for sweep in range(10):
        actual = [row for row in rows if row["phase"] == "measured" and row["sweep"] == sweep]
        assert [row["query_id"] for row in actual[::3]] == queries[sweep % 8:] + queries[:sweep % 8]
        assert actual[:3] == [{"phase": "measured", "sweep": sweep, "query_id": queries[sweep % 8],
                               "provider": providers[(sweep + i) % 3]} for i in range(3)]
    assert 8 + sum(row["provider"] != "bm25" for row in rows) + 16 == 234


@pytest.mark.parametrize("queries,providers,warmups,sweeps", [([], ["dense"], 3, 10), (["q", "q"], ["dense"], 3, 10),
    (["q"], ["dense", "dense"], 3, 10), (["q"], ["dense"], True, 10), (["q"], ["dense"], 3, 0)])
def test_invalid_schedules_refused(queries, providers, warmups, sweeps):
    with pytest.raises(ValueError):
        core.schedule(queries, providers, warmups, sweeps)


def test_source_changes_outside_function_spans_are_not_silently_accepted(tmp_path):
    file = tmp_path / "source.py"
    file.write_text("CONSTANT = 1\n\ndef alpha():\n    return 'unchanged'\n")
    bindings = {str(file): core.sha(file)}
    runner.verify_hashes(bindings)
    file.write_text("CONSTANT = 2\n\ndef alpha():\n    return 'unchanged'\n")
    with pytest.raises(ValueError, match="Frozen file changed"):
        runner.verify_hashes(bindings)


class TinyEmbedder:
    def __init__(self):
        self.metadata = {"id": "tiny-cpu-test", "dimensions": 3}
        self.calls = []
        self.vectors = {}

    def embed_documents(self, texts):
        self.calls.append(len(texts))
        values = []
        for text in texts:
            vector = np.array(list(hashlib.sha256(text.encode()).digest()[:3]), dtype=np.float32) + 1
            vector /= np.linalg.norm(vector)
            self.vectors[core.digest(text.encode())] = vector
            values.append(vector)
        return np.stack(values)

    def close(self):
        pass


@pytest.fixture
def durable(tmp_path, snippets):
    from semantic_search.store import SQLiteIndex
    index = SQLiteIndex(tmp_path / "index.sqlite3")
    embedder = TinyEmbedder()
    matrix, stats = index.refresh(snippets, embedder)
    assert stats["indexed_windows"] == len(snippets)
    return index, embedder, matrix.astype(np.float32)


@pytest.mark.parametrize("corruption", ["fts", "active_vector", "cached_vector", "source", "extra_fts"])
def test_physical_database_checks_detect_stale_or_corrupt_content(durable, snippets, corruption):
    index, embedder, _ = durable
    assert core.inspect_database(index, snippets, embedder.vectors, embedder.metadata)["all_source_and_cached_and_active_vectors_exact"]
    with sqlite3.connect(index.db_path) as connection:
        if corruption == "fts":
            connection.execute("UPDATE snippets_fts SET terms='wrong' WHERE id=?", (snippets[0].id,))
        elif corruption == "active_vector":
            connection.execute("UPDATE active_vectors SET vector=? WHERE snippet_id=?", (np.array([1., 0., 0.], dtype='<f4').tobytes(), snippets[0].id))
        elif corruption == "cached_vector":
            connection.execute("UPDATE embeddings SET vector=?", (np.array([1., 0., 0.], dtype='<f8').tobytes(),))
        elif corruption == "source":
            connection.execute("UPDATE snippets SET text='forged source' WHERE id=?", (snippets[0].id,))
        else:
            connection.execute("INSERT INTO snippets_fts VALUES ('stale-id','stale')")
    with pytest.raises(ValueError):
        core.inspect_database(index, snippets, embedder.vectors, embedder.metadata)


def test_incremental_edit_delete_restore_and_path_rename_reembed_only_missing_documents(tmp_path, snippets, durable):
    from semantic_search.corpus import extract_snippets
    index, embedder, _ = durable
    repo = tmp_path / "repo"
    path = repo / "src/items.py"
    original = path.read_text()
    edited = original.replace("return 1", "return 1  # saved change", 1)
    path.write_text(edited)
    changed = extract_snippets(repo, "src", max_lines=160, overlap_lines=40)
    _, stats = index.refresh(changed, embedder)
    assert (stats["indexed_windows"], stats["reused_windows"]) == (1, 1)
    path.unlink()
    _, stats = index.refresh([], embedder)
    assert stats["deleted_windows"] == 2 and stats["indexed_windows"] == 0
    path.write_text(edited)
    _, stats = index.refresh(changed, embedder)
    assert stats["indexed_windows"] == 0 and stats["reused_windows"] == 2
    renamed = path.with_name("renamed.py")
    path.rename(renamed)
    renamed_snippets = extract_snippets(repo, "src", max_lines=160, overlap_lines=40)
    _, stats = index.refresh(renamed_snippets, embedder)
    assert stats["indexed_windows"] == stats["deleted_windows"] == 2 and stats["reused_windows"] == 0
    renamed.rename(path)
    path.write_text(original)
    _, stats = index.refresh(snippets, runner.NoInference(embedder.metadata))
    assert stats["indexed_windows"] == 0 and stats["reused_windows"] == 2
    assert embedder.calls == [2, 1, 2]
    assert len(embedder.vectors) == 5


def reopen_request(tmp_path, snippets, durable):
    index, embedder, matrix = durable
    repo = tmp_path / "repo"
    raw, actual = core.capture_source(repo, "src", [])
    expected = runner.matrix_file(tmp_path / "reference.npy", matrix)
    receipt = tmp_path / "fresh-result.json"
    request = {"repo": str(repo), "database": str(index.db_path), "source_root": "src", "source_exclusions": [],
               "embedding_metadata": embedder.metadata, "source_files": {path: core.digest(value) for path, value in raw.items()},
               "documents": {key: value["document_sha256"] for key, value in core.inventories(actual).items()},
               "reference_matrix": expected, "receipt": str(receipt)}
    path = tmp_path / "request.json"
    core.write_new(path, request)
    return path, receipt


def child_reopen(path, owner=None):
    return subprocess.run([sys.executable, "-B", str(SCRIPTS / "run_django_index_profile.py"), "--_reopen", str(path),
                           "--owner-pid", str(owner if owner is not None else os.getpid())], capture_output=True, text=True, timeout=20)


def test_real_fresh_cpu_process_reuses_vectors_without_model_imports_or_inference(tmp_path, snippets, durable):
    request, receipt = reopen_request(tmp_path, snippets, durable)
    completed = child_reopen(request)
    assert completed.returncode == 0, completed.stderr
    result = core.read(receipt)
    assert result["passed"] and result["fresh_process"] and result["parent_pid"] == os.getpid()
    assert result["native_imports"] == result["blocked_import_attempts"] == []
    assert result["document_inference_calls"] == result["query_inference_calls"] == 0
    assert result["stats"]["indexed_windows"] == 0 and result["stats"]["reused_windows"] == 2
    assert result["cached_values_exact"] and result["before_refresh_database"]["all_source_and_cached_and_active_vectors_exact"]


@pytest.mark.parametrize("fault", ["source", "matrix", "fts", "owner"])
def test_fresh_child_refuses_drift_and_wrong_ownership_before_inference(tmp_path, snippets, durable, fault):
    request, receipt = reopen_request(tmp_path, snippets, durable)
    if fault == "source":
        path = tmp_path / "repo/src/items.py"
        path.write_text("GLOBAL = 123\n" + path.read_text())
    elif fault == "matrix":
        (tmp_path / "reference.npy").write_bytes(b"corrupt")
    elif fault == "fts":
        with sqlite3.connect(durable[0].db_path) as connection:
            connection.execute("UPDATE snippets_fts SET terms='forged'")
    completed = child_reopen(request, os.getpid() + 100000 if fault == "owner" else None)
    assert completed.returncode == 1
    result = core.read(receipt)
    assert not result["passed"] and result["document_inference_calls"] == result["query_inference_calls"] == 0
    assert result["native_imports"] == [] and result["error"]


def test_independent_numeric_check_detects_changed_sqlite_rank_and_scores(tmp_path, snippets):
    from semantic_search.store import SQLiteIndex
    class Orthogonal:
        metadata = {"id": "orthogonal", "dimensions": 3}
        def embed_documents(self, texts):
            return np.array([[1., 0., 0.], [0., 1., 0.]], dtype=np.float32)
    index = SQLiteIndex(tmp_path / "numeric.sqlite3")
    matrix, _ = index.refresh(snippets, Orthogonal())
    query = np.array([1., .1, 0.], dtype=np.float32)
    result = core.verify_numeric(index, snippets, matrix.astype(np.float32), [query], top_k=1)
    assert result["checks_passed"]
    with sqlite3.connect(index.db_path) as connection:
        connection.execute("UPDATE active_vectors SET vector=? WHERE snippet_id=?", (np.array([0., 1., 0.], dtype='<f4').tobytes(), snippets[0].id))
    result = core.verify_numeric(index, snippets, matrix.astype(np.float32), [query], top_k=1)
    assert not result["checks_passed"]


def test_deferred_signal_records_ownership_before_interrupt_and_restores_handlers():
    before = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGINT)}
    written = []
    with pytest.raises(core.ProfileInterrupt):
        with core.Signals() as guard:
            with guard.defer():
                guard.handler(signal.SIGTERM, None)
                written.append("owned child receipt committed")
    assert written and guard.receipt["received"][0]["signal"] == signal.SIGTERM and guard.receipt["restored"]
    assert all(signal.getsignal(number) == handler for number, handler in before.items())


def test_owned_cpu_session_cleanup_does_not_touch_unrelated_process(tmp_path):
    code = ("import json,os,sys,time; print(json.dumps({'ready':True,'pid':os.getpid(),"
            "'parent_pid':os.getppid(),'process_group':os.getpgid(0),'original_argv':sys.orig_argv}),flush=True); time.sleep(30)")
    command = [sys.executable, "-B", "-c", code]
    owned = unrelated = None
    evidence = {"command": command, "ready": {}, "cleanup": {}}
    try:
        owned = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True, start_new_session=True)
        unrelated = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True, start_new_session=True)
        for name, process in (("owned", owned), ("unrelated", unrelated)):
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                assert selector.select(timeout=2), {"worker": name, "pid": process.pid, "exit_code": process.poll()}
                line = process.stdout.readline()
            marker = json.loads(line)
            evidence["ready"][name] = marker
            assert marker == {"ready": True, "pid": process.pid, "parent_pid": os.getpid(),
                              "process_group": process.pid, "original_argv": command}, marker
            assert os.getpgid(process.pid) == process.pid and process.poll() is None, marker
        receipt = runner.stop_owned(owned)
        evidence["cleanup"]["owned"] = receipt
        assert receipt["owned_group_stopped"] and not receipt["forced_kill"] and not receipt["errors"], evidence
        assert unrelated.poll() is None and os.getpgid(unrelated.pid) == unrelated.pid, evidence
    finally:
        for name, process in (("owned", owned), ("unrelated", unrelated)):
            if process is not None:
                evidence["cleanup"]["finally-" + name] = runner.stop_owned(process)
                process.stdout.close()
        core.write_new(tmp_path / "owned-session-proofs.json", evidence)


def test_checkpoint_wrong_bytes_fail_before_any_model_import(tmp_path):
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    (checkpoint / "model.safetensors").write_bytes(b"original tiny test bytes")
    binding = {"path": str(checkpoint), "files": {"model.safetensors": {
        "bytes": (checkpoint / "model.safetensors").stat().st_size,
        "sha256": core.sha(checkpoint / "model.safetensors")}}}
    runner.verify_checkpoint(binding)
    (checkpoint / "model.safetensors").write_bytes(b"modified tiny test bytes")
    with pytest.raises(ValueError, match="Checkpoint bytes differ"):
        runner.verify_checkpoint(binding)


def test_atomic_partial_checkpoint_and_new_outputs_never_overwrite(tmp_path):
    path = tmp_path / "partial.json"
    core.write_atomic(path, {"complete": False, "completed_stages": ["initial"]})
    assert core.read(path)["complete"] is False
    with pytest.raises(FileExistsError):
        core.write_new(path, {"complete": True})
    assert core.read(path)["completed_stages"] == ["initial"]


def coding_pointer(tmp_path):
    path = tmp_path / "driver.json"
    core.write_new(path, {"origin": "Known owned launch", "driver": {
        "pid": 48615, "parent_pid": 14140, "process_group": 48615,
        "command": ".venv/bin/python -B scripts/run_qwen_workflows.py run --output results/qwen-coding-workflows-2026-10-14"},
        "deadline_utc": "2026-10-14T13:37:05+00:00"})
    return path


@pytest.mark.parametrize("state", ["active", "exited", "wrong_group", "reused_pid", "unknown_error"])
def test_coding_gate_uses_exact_nested_pid_group_argv_and_proves_exit(tmp_path, monkeypatch, state):
    pointer = coding_pointer(tmp_path)
    driver = core.read(pointer)["driver"]
    stdout = f"48615 48615 {driver['command']}"
    if state == "wrong_group":
        stdout = f"48615 99999 {driver['command']}"
    elif state == "reused_pid":
        stdout = "48615 48615 unrelated-command"
    code = 0
    if state == "exited":
        code, stdout = 1, ""
    elif state == "unknown_error":
        code, stdout = 2, ""
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], code, stdout, ""))
    if state == "exited":
        result = runner.coding_guard(pointer)
        assert not result["active"] and result["pid"] == result["owned_group"] == 48615
        assert result["recorded_argv"] == driver["command"].split() and result["proof_sha256"] == core.sha(pointer)
    else:
        with pytest.raises(ValueError):
            runner.coding_guard(pointer)


@pytest.mark.parametrize("mutation", ["top_level_pid", "bool_pid", "wrong_argv", "extra_driver_field", "missing_proof"])
def test_malformed_or_missing_coding_proof_fails_closed(tmp_path, mutation):
    pointer = coding_pointer(tmp_path)
    value = core.read(pointer)
    if mutation == "top_level_pid":
        value = {"pid": 48615}
    elif mutation == "bool_pid":
        value["driver"]["pid"] = True
    elif mutation == "wrong_argv":
        value["driver"]["command"] += " --extra-override"
    elif mutation == "extra_driver_field":
        value["driver"]["unknown"] = "unknown"
    else:
        pointer.unlink()
    if mutation != "missing_proof":
        core.write_atomic(pointer, value)
    with pytest.raises(ValueError):
        runner.coding_guard(pointer)


def coding_certificate(tmp_path):
    study = tmp_path / "study"
    study.mkdir()
    core.write_new(study / "frozen-config.json", {"id": "frozen-test-runtime"})
    frozen = core.sha(study / "frozen-config.json")
    core.write_new(study / "results.json", [{"run_id": f"r{i}"} for i in range(18)])
    core.write_new(study / "completion.json", {"recorded_attempts": 18, "frozen_config_sha256": frozen})
    for name in ("summary.json", "run-start.json", "tasks.json"):
        core.write_new(study / name, {"fixture": name})
    certificate = {"checks_passed": True, "inventory_complete": True, "final_completion_receipt": True,
                   "planned_attempts": 18, "recorded_attempts": 18, "missing_attempts": [],
                   "frozen_config_sha256": frozen,
                   "artifact_sha256": {path.name: core.sha(path) for path in study.iterdir()}}
    audit = tmp_path / "audit.json"
    core.write_new(audit, certificate)
    return study, audit


@pytest.mark.parametrize("fault", [None, "prefix", "failed_audit", "no_completion", "source_changed", "duplicate_trial"])
def test_native_precondition_requires_full_source_bound_coding_certificate(tmp_path, fault):
    study, audit = coding_certificate(tmp_path)
    value = core.read(audit)
    if fault == "prefix":
        value["recorded_attempts"] = 17
        core.write_atomic(audit, value)
    elif fault == "failed_audit":
        value["checks_passed"] = False
        core.write_atomic(audit, value)
    elif fault == "no_completion":
        (study / "completion.json").unlink()
    elif fault == "source_changed":
        core.write_atomic(study / "summary.json", {"changed": True})
    elif fault == "duplicate_trial":
        rows = core.read(study / "results.json")
        rows[1] = rows[0]
        core.write_atomic(study / "results.json", rows)
    if fault is None:
        assert runner.coding_completion(audit, study)["completed_attempts"] == 18
    else:
        with pytest.raises((ValueError, FileNotFoundError)):
            runner.coding_completion(audit, study)


def test_failed_clock_keeps_returned_query_receipt_without_accepting_sample():
    from qwen_optimization_clock import ClockIntegrity, ClockIntegrityError
    wall = iter([0, 1, 3_000_000_000])
    perf = iter([0, 1, 2])
    clock = ClockIntegrity(wall_clock=lambda: next(wall), perf_clock=lambda: next(perf))
    journal = MemoryJournal()
    returned = {"query": "completed before suspend observation", "results": []}
    with pytest.raises(ClockIntegrityError):
        runner.timed(clock, "failed-query", lambda: returned, journal)
    assert journal.rows[0]["event"] == "clock_failed_after_operation"
    assert journal.rows[0]["returned_operation_value"] == returned
    assert journal.rows[0]["clock_failure"]["consistent"] is False


def fake_parent_plan(tmp_path, monkeypatch):
    output, work = tmp_path / "output", tmp_path / "owned-work"
    output.mkdir()
    plan = {"output": str(output), "work": str(work), "worker_timeout_s": 5,
            "coding_precondition": {"audit": str(tmp_path / "audit.json")}}
    core.write_new(output / "frozen-plan.json", plan)
    monkeypatch.setattr(core, "block_network", lambda: None)
    monkeypatch.setattr(runner, "committed", lambda *args: "fake-test-commit")
    monkeypatch.setattr(runner, "verify_environment", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "coding_guard", lambda *args: {"active": False})
    monkeypatch.setattr(runner, "coding_completion", lambda *args: plan["coding_precondition"])
    return output, work


def test_work_reservation_failure_retains_claimed_failed_attempt_without_spawn(tmp_path, monkeypatch):
    output, work = fake_parent_plan(tmp_path, monkeypatch)
    original = Path.mkdir
    def refuse(self, *args, **kwargs):
        if self == work:
            raise OSError("planned CPU work-reservation failure")
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", refuse)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Reservation failure must never spawn"))
    assert runner.run(output) == 1
    receipt = core.read(output / "parent-result.json")
    assert receipt["attempt_claimed"] and not receipt["checks_passed"] and not receipt["complete"]
    assert receipt["cleanup"]["owned_group_stopped"] and receipt["model_calls_in_parent"] == 0
    before = (output / "parent-result.json").read_bytes()
    with pytest.raises(ValueError, match="No attempt overwrite"):
        runner.run(output)
    assert (output / "parent-result.json").read_bytes() == before


def test_native_spawn_failure_preserves_attempt_and_owned_work_without_retry(tmp_path, monkeypatch):
    output, work = fake_parent_plan(tmp_path, monkeypatch)
    def broken(*args, **kwargs):
        raise OSError("planned CPU spawn failure")
    monkeypatch.setattr(runner.subprocess, "Popen", broken)
    assert runner.run(output) == 1
    receipt = core.read(output / "parent-result.json")
    assert receipt["work_reserved"] and not receipt["checks_passed"] and work.exists()
    assert receipt["error"]["type"] == "OSError" and receipt["cleanup"]["launched"] is False
    with pytest.raises(ValueError, match="No profile retry"):
        runner.run(output)


def test_truncated_worker_publication_cannot_be_qualified_as_success(tmp_path, monkeypatch):
    output, work = fake_parent_plan(tmp_path, monkeypatch)
    original = subprocess.Popen
    def cpu_only_worker(command, **kwargs):
        code = "from pathlib import Path; Path(" + repr(str(output / "worker-result.json")) + ").write_text('{truncated')"
        return original([sys.executable, "-B", "-c", code], **kwargs)
    monkeypatch.setattr(runner.subprocess, "Popen", cpu_only_worker)
    assert runner.run(output) == 1
    receipt = core.read(output / "parent-result.json")
    assert receipt["exit_code"] == 0 and receipt["complete"] and not receipt["checks_passed"]
    assert receipt["worker_evidence_error"] and receipt["cleanup"]["owned_group_stopped"]
    assert (output / "worker-result.json").read_text() == "{truncated"


def test_sigterm_ignoring_owned_group_is_forced_and_reported(tmp_path):
    ready = tmp_path / "ready"
    code = ("import signal,time; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            "Path(" + repr(str(ready)) + ").write_text('ready'); time.sleep(30)")
    process = subprocess.Popen([sys.executable, "-B", "-c", code], start_new_session=True)
    try:
        import time
        deadline = time.monotonic() + 2
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists()
        receipt = runner.stop_owned(process, term_grace_s=.05, kill_grace_s=.5)
        assert receipt["forced_kill"] and receipt["owned_group_stopped"] and receipt["exit_code"] == -signal.SIGKILL
        assert not receipt["errors"]
    finally:
        runner.stop_owned(process)


@pytest.mark.parametrize("lm_installed", [True, False])
def test_loader_helper_sources_and_actual_origins_are_bound_without_importing_models(tmp_path, monkeypatch, lm_installed):
    from types import SimpleNamespace
    helper = tmp_path / "mlx_vlm/utils.py"
    helper.parent.mkdir()
    helper.write_text("def sanitize_weights(weights):\n    return weights\n")
    language = tmp_path / "mlx_lm/models/base.py"
    language.parent.mkdir(parents=True)
    language.write_text("def create_attention_mask(value):\n    return value\n")
    class FakeDistribution:
        def __init__(self, files):
            self.files = files
        def locate_file(self, value):
            return tmp_path / value
    def distribution(name):
        if name == "mlx-vlm":
            return FakeDistribution([Path("mlx_vlm/utils.py")])
        if name == "mlx-lm":
            if not lm_installed:
                raise core.importlib.metadata.PackageNotFoundError(name)
            return FakeDistribution([Path("mlx_lm/models/base.py")])
        return FakeDistribution([])
    monkeypatch.setattr(core.importlib.metadata, "distribution", distribution)
    sources = core.native_sources()
    assert sources[str(helper)] == core.sha(helper)
    assert (str(language) in sources) is lm_installed
    monkeypatch.setitem(sys.modules, "mlx_vlm.utils", SimpleNamespace(__file__=str(helper)))
    origins = core.loaded_native_origins()
    assert origins["mlx_vlm.utils"] == {"path": str(helper), "sha256": sources[str(helper)]}
    # No loader helper code is executed: these synthetic modules expose only a
    # file identity, while the source binding is independently read and hashed.
