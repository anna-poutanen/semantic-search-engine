"""CPU proofs that interrupted inference caches work without publishing stale source."""
from __future__ import annotations

import hashlib
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

import semantic_search.backend as backend_module
from semantic_search.backend import SearchBackend, SourceSnapshotError
from semantic_search.cancellation import SearchCancelled
from semantic_search.corpus import extract_snippets
from semantic_search.index import document_text
from semantic_search.store import SQLiteIndex


OLD_SOURCE = "def original():\n    return 'originalsentinel'\n"
NEW_SOURCE = OLD_SOURCE + """

def first_added():
    return 'firstsentinel'

def second_added():
    return 'secondsentinel'

def third_added():
    return 'thirdsentinel'
"""


def vector(document):
    fraction = int(hashlib.sha256(document.encode()).hexdigest()[:8], 16) / 2**32
    return np.asarray([1.0, 0.1 + fraction])


class BatchEmbedder:
    """A cooperative real callback producer with no tokenizer/model/network."""

    def __init__(self, *, revision="fixed", after_batch=None):
        self.metadata = {"id": "resumable-backend-cpu", "revision": revision, "dimensions": 2}
        self.documents = []
        self.after_batch = after_batch

    def embed_documents(self, documents, *, cancel_check=None, progress_callback=None,
                        batch_callback=None):
        self.documents.extend(documents)
        values = []
        for index, document in enumerate(documents):
            if cancel_check is not None:
                cancel_check()
            value = vector(document)
            values.append(value)
            if batch_callback is not None:
                batch_callback([index], value.reshape(1, -1))
            if progress_callback is not None:
                progress_callback(index + 1, len(documents))
            if self.after_batch is not None:
                self.after_batch(index)
            if cancel_check is not None:
                cancel_check()
        return np.asarray(values).reshape(-1, 2)

    def close(self):
        pass


def active_rows(database):
    """Read all promoted state through a separate connection, excluding cache."""
    with sqlite3.connect(database) as connection:
        return {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in ("snippets", "snippets_fts", "active_vectors", "metadata")
        }


def cached_hashes(database, revision="fixed"):
    metadata = {"id": "resumable-backend-cpu", "revision": revision, "dimensions": 2}
    fingerprint = hashlib.sha256(json.dumps(
        metadata, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with sqlite3.connect(database) as connection:
        return {row[0] for row in connection.execute(
            "SELECT document_hash FROM embeddings WHERE model_fingerprint=?", (fingerprint,))}


def documents(repo):
    return [document_text(snippet) for snippet in extract_snippets(
        repo, "src", max_lines=160, overlap_lines=40)]


def document_hash(document):
    return hashlib.sha256(document.encode()).hexdigest()


@pytest.fixture
def project(tmp_path):
    (tmp_path / "src").mkdir()
    source = tmp_path / "src/code.py"
    source.write_text(OLD_SOURCE)
    return tmp_path, source, tmp_path / "index.sqlite3"


def seed_project(project, *, revision="fixed"):
    repo, _, database = project
    with_backend = SearchBackend(repo, "src", database, embedder=BatchEmbedder(revision=revision))
    try:
        assert with_backend.refresh()["indexed_windows"] == 1
    finally:
        with_backend.close()
    store = SQLiteIndex(database)
    return (active_rows(database), store.search_lexical("originalsentinel"),
            store.search_dense(np.asarray([1.0, 0.0])))


def assert_old_active(database, baseline):
    rows, lexical, dense = baseline
    assert active_rows(database) == rows
    store = SQLiteIndex(database)
    assert store.search_lexical("originalsentinel") == lexical
    assert store.search_dense(np.asarray([1.0, 0.0])) == dense
    assert store.search_lexical("firstsentinel") == []


@pytest.mark.parametrize("with_progress", [False, True])
def test_cancelled_refresh_resumes_only_missing_documents_and_preserves_active(project, with_progress):
    repo, source, database = project
    baseline = seed_project(project)
    source.write_text(NEW_SOURCE)
    expected = documents(repo)
    previous_hashes = cached_hashes(database)
    events = []

    def report(event):
        events.append(event)
        if event["phase"] == "embed" and event["completed"]:
            # A fresh reader must already see every reported finished batch.
            assert len(cached_hashes(database) - previous_hashes) >= event["completed"]

    embedder = BatchEmbedder()
    backend = SearchBackend(repo, "src", database, embedder=embedder,
                            progress=report if with_progress else None)
    embedder.after_batch = lambda index: backend.cancel("cancel-first") if index == 0 else None
    try:
        with pytest.raises(SearchCancelled):
            backend.refresh(request_id="cancel-first")
        assert len(cached_hashes(database) - previous_hashes) == 1
        assert_old_active(database, baseline)
        checkpoint_hashes = cached_hashes(database)
    finally:
        backend.close()

    resumed_events = []
    resumed_embedder = BatchEmbedder()
    restarted = SearchBackend(repo, "src", database, embedder=resumed_embedder,
                              progress=resumed_events.append)
    try:
        stats = restarted.refresh()
        assert stats["total_windows"] == 4
        assert stats["reused_windows"] == 2
        assert stats["indexed_windows"] == 2
        assert {document_hash(document) for document in resumed_embedder.documents} == {
            document_hash(document) for document in expected} - checkpoint_hashes
        assert len(resumed_embedder.documents) == 2
        cache = [event for event in resumed_events if event["phase"] == "cache"]
        assert len(cache) == 1
        assert cache[0]["cached_documents"] == 2
        assert cache[0]["pending_documents"] == 2
        assert {snippet.symbol for snippet in restarted._get_index().snippets} == {
            "original", "first_added", "second_added", "third_added"}
        assert restarted.status()["ready"]
    finally:
        restarted.close()


def test_source_edit_rejects_promotion_but_reuses_only_compatible_document_cache(project):
    repo, source, database = project
    baseline = seed_project(project)
    source.write_text(NEW_SOURCE)
    captured_documents = documents(repo)
    changed_source = NEW_SOURCE.replace("'secondsentinel'", "'secondsentinel_changed'")
    embedder = BatchEmbedder(after_batch=lambda index: source.write_text(changed_source)
                             if index == 0 else None)
    backend = SearchBackend(repo, "src", database, embedder=embedder)
    try:
        with pytest.raises(SourceSnapshotError, match="Source changed"):
            backend.refresh()
        assert_old_active(database, baseline)
        assert cached_hashes(database) == {document_hash(item) for item in captured_documents}
    finally:
        backend.close()

    current_documents = documents(repo)
    expected_missing = set(current_documents) - set(captured_documents)
    assert len(expected_missing) == 1
    resumed_embedder = BatchEmbedder()
    restarted = SearchBackend(repo, "src", database, embedder=resumed_embedder)
    try:
        stats = restarted.refresh()
        assert set(resumed_embedder.documents) == expected_missing
        assert stats["reused_windows"] == 3
        assert stats["indexed_windows"] == 1
        active_documents = {document_text(item) for item in restarted._get_index().snippets}
        assert active_documents == set(current_documents)
        assert not (set(captured_documents) - set(current_documents)) & active_documents
        assert len(cached_hashes(database)) == 5  # Rejected source remains cache-only.
    finally:
        restarted.close()


@pytest.mark.parametrize("failure", ["cancel", "source-change"])
def test_failure_during_promotion_rolls_back_active_but_preserves_checkpoints(project, failure):
    repo, source, database = project
    baseline = seed_project(project)
    source.write_text(NEW_SOURCE)
    captured_hashes = {document_hash(item) for item in documents(repo)}
    index = SQLiteIndex(database)
    embedder = BatchEmbedder()
    backend = SearchBackend(repo, "src", database, index=index, embedder=embedder)
    write_fts = index._write_fts

    def interrupt_after_fts_write(connection, snippets):
        write_fts(connection, snippets)
        # The fault occurs after snippets/FTS have changed within the promotion
        # transaction, beyond the earlier source/cancellation check.
        if failure == "cancel":
            backend.cancel("cancel-promotion")
        else:
            source.write_text(NEW_SOURCE.replace("'thirdsentinel'", "'thirdsentinel_changed'"))

    index._write_fts = interrupt_after_fts_write
    try:
        expected_error = SearchCancelled if failure == "cancel" else SourceSnapshotError
        with pytest.raises(expected_error):
            backend.refresh(request_id="cancel-promotion")
        assert_old_active(database, baseline)
        assert cached_hashes(database) == captured_hashes
    finally:
        backend.close()

    resumed_embedder = BatchEmbedder()
    restarted = SearchBackend(repo, "src", database, embedder=resumed_embedder)
    try:
        stats = restarted.refresh()
        missing = {item for item in documents(repo) if document_hash(item) not in captured_hashes}
        assert set(resumed_embedder.documents) == missing
        assert stats["indexed_windows"] == len(missing)
        assert stats["reused_windows"] == 4 - len(missing)
    finally:
        restarted.close()


def test_partial_cache_isolated_by_model_fingerprint(project):
    repo, source, database = project
    seed_project(project)
    source.write_text(NEW_SOURCE)
    embedder = BatchEmbedder()
    backend = SearchBackend(repo, "src", database, embedder=embedder)
    embedder.after_batch = lambda index: backend.cancel("model-a") if index == 0 else None
    try:
        with pytest.raises(SearchCancelled):
            backend.refresh(request_id="model-a")
    finally:
        backend.close()
    assert len(cached_hashes(database, "fixed")) == 2

    other = BatchEmbedder(revision="different")
    backend = SearchBackend(repo, "src", database, embedder=other)
    try:
        stats = backend.refresh()
        assert len(other.documents) == 4
        assert stats["indexed_windows"] == 4
        assert stats["reused_windows"] == 0
        assert len(cached_hashes(database, "different")) == 4
    finally:
        backend.close()

    original_model = BatchEmbedder()
    backend = SearchBackend(repo, "src", database, embedder=original_model)
    try:
        stats = backend.refresh()
        assert len(original_model.documents) == 2
        assert stats["indexed_windows"] == 2
        assert stats["reused_windows"] == 2
        assert len(cached_hashes(database, "fixed")) == 4
        assert len(cached_hashes(database, "different")) == 4
    finally:
        backend.close()


CHILD = r'''
import hashlib
import json
import sys
import numpy as np
from semantic_search.backend import SearchBackend

repo, database, mode = sys.argv[1:]
class Embedder:
    metadata = {"id": "resumable-backend-cpu", "revision": "fixed", "dimensions": 2}
    def __init__(self):
        self.documents = []
    def embed_documents(self, documents, *, cancel_check=None, progress_callback=None,
                        batch_callback=None):
        self.documents.extend(documents)
        values = []
        for index, document in enumerate(documents):
            if cancel_check:
                cancel_check()
            fraction = int(hashlib.sha256(document.encode()).hexdigest()[:8], 16) / 2**32
            value = np.asarray([1., .1 + fraction])
            values.append(value)
            if batch_callback is not None:
                batch_callback([index], value.reshape(1, -1))
                if mode == "pause" and index == 0:
                    # The marker proves the callback returned before the parent kills us.
                    print(json.dumps({"checkpoint": hashlib.sha256(document.encode()).hexdigest()}),
                          flush=True)
                    sys.stdin.buffer.read(1)
            if progress_callback:
                progress_callback(index + 1, len(documents))
        return np.asarray(values).reshape(-1, 2)
    def close(self):
        pass
embedder = Embedder()
backend = SearchBackend(repo, "src", database, embedder=embedder)
try:
    stats = backend.refresh()
    print(json.dumps({"documents": embedder.documents, "stats": stats}), flush=True)
finally:
    backend.close()
'''


def test_sigkill_checkpoint_is_durable_and_a_fresh_process_only_embeds_remaining(project):
    repo, source, database = project
    baseline = seed_project(project)
    source.write_text(NEW_SOURCE)
    expected_hashes = {document_hash(item) for item in documents(repo)}
    initial_hashes = cached_hashes(database)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(backend_module.__file__).resolve().parents[1])
    command = [sys.executable, "-u", "-c", CHILD, str(repo), str(database)]
    child = subprocess.Popen([*command, "pause"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=environment, text=True)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=20), "Child never reached its durable checkpoint"
            line = child.stdout.readline()
        assert line, child.stderr.read() if child.poll() is not None else "Child exited before checkpoint"
        marker = json.loads(line)
        assert set(marker) == {"checkpoint"}
        assert marker["checkpoint"] not in initial_hashes
        assert cached_hashes(database) == initial_hashes | {marker["checkpoint"]}
        assert_old_active(database, baseline)
        child.send_signal(signal.SIGKILL)
        child.communicate(timeout=10)
        assert child.returncode == -signal.SIGKILL
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)
    assert cached_hashes(database) == initial_hashes | {marker["checkpoint"]}
    assert_old_active(database, baseline)
    resumed = subprocess.run([*command, "resume"], env=environment, capture_output=True,
                             text=True, timeout=20, check=True)
    result = json.loads(resumed.stdout)
    assert len(result["documents"]) == 2
    assert {document_hash(item) for item in result["documents"]} == (
        expected_hashes - initial_hashes - {marker["checkpoint"]})
    assert result["stats"]["indexed_windows"] == 2
    assert result["stats"]["reused_windows"] == 2
    assert cached_hashes(database) == expected_hashes
    assert {item.symbol for item in SQLiteIndex(database).snippets} == {
        "original", "first_added", "second_added", "third_added"}
