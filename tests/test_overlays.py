"""CPU-only editor text overrides, isolation, disposal, and freshness bindings."""
import copy
import hashlib
import http.client
import json
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from semantic_search.backend import SearchBackend, SourceSnapshotError
from semantic_search.cancellation import SearchCancelled
from semantic_search.client import SearchClient, StaleBufferError, ensure_overlay_current
from semantic_search.context import compact_result, count_context
from semantic_search.overlays import (MAX_OVERLAY_BODY_BYTES, MAX_OVERLAY_FILE_BYTES,
                                      MAX_OVERLAY_FILES, MAX_OVERLAY_TOTAL_BYTES,
                                      normalize_overlays)
from semantic_search.server import create_server


def overlay(path, text, version=1):
    return {"path": path, "text": text, "version": version,
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()}


class OverlayEmbedder:
    metadata = {"id": "cpu-overlay", "revision": "fixed", "dimensions": 2}

    def __init__(self):
        self.documents, self.queries, self.closes = [], [], 0
        self.on_documents = self.on_query = None

    def embed_documents(self, texts):
        self.documents.append(list(texts))
        if self.on_documents:
            self.on_documents()
        return np.array([[1., .25 + ("overlay" in text)] for text in texts]).reshape(-1, 2)

    def embed_query(self, text):
        self.queries.append(text)
        if self.on_query:
            self.on_query()
        return np.array([1., .25])

    def close(self):
        self.closes += 1


class OverlayFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name).resolve()
        (self.repo / "src").mkdir()
        self.path = self.repo / "src/main.py"
        self.path.write_bytes(b"def saved_name():\r\n    return 'saved'\r\n")
        (self.repo / "src/helper.py").write_text("def shared():\n    return 'helper'\n")
        self.git("init", "-q")
        self.git("add", "src/main.py", "src/helper.py")
        self.embedder = OverlayEmbedder()
        self.backend = SearchBackend(self.repo, "src", self.repo / "saved.sqlite3", embedder=self.embedder)
        self.addCleanup(self.backend.close)

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True).strip()

    def saved_state(self):
        with sqlite3.connect(self.backend.db_path) as connection:
            return {table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                    for table in ("snippets", "embeddings", "active_vectors", "metadata", "snippets_fts")}


class OverlayTests(OverlayFixture):

    def test_overlay_replaces_file_for_one_request_preserving_saved_state_and_source_bytes(self):
        self.backend.refresh()
        before = copy.deepcopy(self.backend.status())
        rows, disk = self.saved_state(), self.path.read_bytes()
        db_bytes = self.backend.db_path.read_bytes()
        entries = [overlay("src/main.py", "def overlay_name():\n    return 'overlay'\n", 8)]
        result = self.backend.search("overlay_name", retrieval="bm25", overlays=entries)
        self.assertEqual([r["symbol"] for r in result["results"]], ["overlay_name"])
        row = result["results"][0]
        self.assertEqual((row["source_origin"], row["buffer_version"], row["buffer_hash"]),
                         ("overlay", 8, entries[0]["content_hash"]))
        self.assertEqual(row["source"], entries[0]["text"])
        self.assertEqual(self.path.read_bytes(), disk)
        self.assertEqual(self.saved_state(), rows)
        self.assertEqual(self.backend.db_path.read_bytes(), db_bytes)
        self.assertEqual(self.backend.status(), before)
        saved = self.backend.search("saved_name", retrieval="bm25")
        self.assertEqual(saved["results"][0]["source"], disk.decode())
        self.assertNotIn("source_origin", saved["results"][0])

    def test_cold_overlay_does_not_create_saved_store_or_initialize_model_for_lexical(self):
        self.backend._embedder = None
        before = self.backend.status()
        with patch.object(self.backend, "_get_index", side_effect=AssertionError("saved index touched")), \
                patch.object(self.backend, "_get_embedder", side_effect=AssertionError("model touched")):
            result = self.backend.search("overlay_name", retrieval="bm25", overlays=[overlay(
                "src/main.py", "def overlay_name():\n    return 2\n")])
        self.assertEqual(result["results"][0]["source_origin"], "overlay")
        self.assertEqual(self.backend.status(), before)
        self.assertFalse(self.backend.db_path.exists())
        self.assertIsNone(self.backend._index)
        self.backend._embedder = self.embedder

    def test_literal_python_cookie_unicode_crlf_and_unrelated_saved_source_provenance(self):
        text = "# coding: latin-1\r\nfrom helper import shared\r\ndef café():\r\n    return 'λ 😀\u2028data' + shared()\r\n"
        result = self.backend.search("café", retrieval="bm25", overlays=[overlay("src/main.py", text, 3)],
                                     include_dependencies=True, max_results=2)
        self.assertEqual([row["symbol"] for row in result["results"]], ["café", "shared"])
        self.assertEqual(result["results"][0]["source"], "def café():\r\n    return 'λ 😀\u2028data' + shared()\r\n")
        self.assertIn("λ 😀\u2028data", result["results"][0]["source"])
        self.assertEqual(result["results"][1]["source_origin"], "saved")
        self.assertNotIn("buffer_version", result["results"][1])
        self.assertEqual(self.path.read_bytes(), b"def saved_name():\r\n    return 'saved'\r\n")

    def test_overridden_malformed_saved_file_can_be_fixed_by_literal_text(self):
        self.path.write_bytes(b"# coding: latin-1\ndef invalid(\xff")
        response = self.backend.search("fixed", retrieval="bm25", overlays=[overlay(
            "src/main.py", "def fixed():\n    return 'λ'\n")])
        self.assertEqual(response["results"][0]["symbol"], "fixed")
        self.assertIn(b"\xff", self.path.read_bytes())
        self.assertIsNone(self.backend._manifest)

    def test_syntax_errors_reject_entire_request_without_poisoning_saved_backend(self):
        self.backend.refresh()
        state, status = self.saved_state(), self.backend.status()
        with patch.object(self.backend, "_get_embedder", side_effect=AssertionError("model before validation")):
            with self.assertRaisesRegex(SourceSnapshotError, "Cannot parse overlay"):
                self.backend.search("anything", overlays=[overlay("src/main.py", "def invalid(\n")])
        self.assertEqual(self.saved_state(), state)
        self.assertEqual(self.backend.status(), status)
        (self.repo / "src/helper.py").write_text("def invalid(\n")
        with self.assertRaisesRegex(SourceSnapshotError, "Cannot parse saved source"):
            self.backend.search("anything", overlays=[overlay("src/main.py", "def okay():\n    return 1\n")])
        self.assertEqual(self.saved_state(), state)

    def test_same_resident_model_reuses_saved_vectors_and_does_not_cache_overlay_vectors_globally(self):
        self.backend.refresh()
        state = self.saved_state()
        self.assertEqual(len(self.embedder.documents), 1)
        entries = [overlay("src/main.py", "def overlay_name():\n    return 'overlay'\n")]
        response = self.backend.search("anything", overlays=entries)
        self.assertEqual(response["indexstats"]["indexed_windows"], 1)
        self.assertEqual(response["indexstats"]["reused_windows"], 1)
        self.assertEqual(len(self.embedder.documents), 2)
        self.assertEqual(len(self.embedder.documents[-1]), 1)
        self.assertEqual(self.embedder.closes, 0)
        self.assertIs(self.backend._get_embedder(), self.embedder)
        self.assertEqual(self.saved_state(), state)
        self.backend.search("anything", overlays=entries)
        self.assertEqual(len(self.embedder.documents), 3)
        self.assertEqual(self.saved_state(), state)
        unchanged = overlay("src/main.py", self.path.read_bytes().decode(), 12)
        result = self.backend.search("anything", overlays=[unchanged])
        self.assertEqual(result["indexstats"]["indexed_windows"], 0)
        self.assertEqual(len(self.embedder.documents), 3)

    def test_unopened_existing_saved_database_can_seed_vectors_read_only(self):
        self.backend.refresh()
        self.backend.close()
        embedder = OverlayEmbedder()
        reopened = SearchBackend(self.repo, "src", self.backend.db_path, embedder=embedder)
        self.addCleanup(reopened.close)
        db_bytes = reopened.db_path.read_bytes()
        result = reopened.search("anything", overlays=[overlay("src/main.py", self.path.read_bytes().decode(), 5)])
        self.assertEqual(result["indexstats"]["indexed_windows"], 0)
        self.assertEqual(embedder.documents, [])
        self.assertIsNone(reopened._index)
        self.assertIsNone(reopened._manifest)
        self.assertEqual(reopened.db_path.read_bytes(), db_bytes)

    def test_model_fingerprint_changes_do_not_reuse_old_saved_vectors_or_mutate_saved_index(self):
        self.backend.refresh()
        state = self.saved_state()
        self.embedder.metadata = {**self.embedder.metadata, "revision": "changed"}
        result = self.backend.search("anything", overlays=[overlay("src/main.py", self.path.read_bytes().decode())])
        self.assertEqual(result["indexstats"]["indexed_windows"], 2)
        self.assertEqual(result["indexstats"]["reused_windows"], 0)
        self.assertEqual(self.saved_state(), state)

    def test_request_mutation_after_capture_does_not_change_buffer_identity_or_text(self):
        entries = [overlay("src/main.py", "def overlay_name():\n    return 2\n", 5)]
        expected = copy.deepcopy(entries[0])
        self.embedder.on_documents = lambda: entries[0].update(text="def raced():\n    return 3\n", version=6)
        response = self.backend.search("anything", overlays=entries)
        row = next(item for item in response["results"] if item["path"] == "src/main.py")
        self.assertEqual(row["source"], expected["text"])
        self.assertEqual(row["buffer_version"], 5)
        self.assertEqual(row["buffer_hash"], expected["content_hash"])

    def test_saved_edit_delete_rename_or_branch_change_during_request_is_rejected(self):
        self.backend.refresh()
        state = self.saved_state()
        original = self.path.read_bytes()
        entries = [overlay("src/main.py", "def overlay_name():\n    return 'overlay'\n")]
        mutations = [lambda: self.path.write_text("def changed():\n    return 3\n"), self.path.unlink,
                     lambda: self.path.rename(self.repo / "src/renamed.py")]
        for mutate in mutations:
            self.embedder.on_documents = mutate
            with self.subTest(mutate=mutate), self.assertRaises(SourceSnapshotError):
                self.backend.search("anything", overlays=entries)
            renamed = self.repo / "src/renamed.py"
            if renamed.exists():
                renamed.unlink()
            self.path.write_bytes(original)
            self.assertEqual(self.saved_state(), state)
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "initial")
        self.embedder.on_documents = lambda: self.git("checkout", "-qb", "other")
        with self.assertRaises(SourceSnapshotError):
            self.backend.search("anything", overlays=entries)
        self.assertEqual(self.saved_state(), state)

    def test_tracked_membership_root_extensions_exclusions_missing_files_and_symlink_escape(self):
        (self.repo / "src/untracked.py").write_text("def untracked():\n    return 1\n")
        (self.repo / "other.py").write_text("def outside():\n    return 1\n")
        (self.repo / "src/types.d.ts").write_text("declare function missing(): void;")
        (self.repo / "src/linked.py").symlink_to(self.repo / "other.py")
        self.git("add", "other.py", "src/types.d.ts", "src/linked.py")
        for path in ("src/untracked.py", "other.py", "src/types.d.ts", "src/linked.py", "src/missing.py", "src/data.txt"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.backend.search("anything", retrieval="bm25", overlays=[overlay(path, "def override():\n    return 1\n")])
        excluded = SearchBackend(self.repo, "src", source_exclusions=["*/main.py"])
        self.addCleanup(excluded.close)
        with self.assertRaises(ValueError):
            excluded.search("anything", retrieval="bm25", overlays=[overlay("src/main.py", "def override():\n    return 1\n")])
        self.assertFalse(excluded.db_path.exists())

    def test_non_git_projects_remain_saved_searchable_but_cannot_take_overlays(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "code.py").write_text("def saved():\n    return 1\n")
            backend = SearchBackend(repo, ".")
            try:
                with self.assertRaisesRegex(ValueError, "tracked files"):
                    backend.search("saved", retrieval="bm25", overlays=[overlay("code.py", "def buffer():\n    return 2\n")])
                self.assertEqual(backend.search("saved", retrieval="bm25")["results"][0]["symbol"], "saved")
            finally:
                backend.close()

    def test_empty_overlay_lists_and_null_preserve_default_saved_response(self):
        first = self.backend.search("saved_name", retrieval="bm25")
        for entries in ([], None):
            result = self.backend.search("saved_name", retrieval="bm25", overlays=entries)
            self.assertEqual(result["scope"], first["scope"])
            self.assertEqual(result["results"], first["results"])
            self.assertNotIn("source_state", result["scope"])

    def test_new_versions_use_new_literal_text_without_leaking_previous_overrides(self):
        first = overlay("src/main.py", "def first_buffer():\n    return 1\n", 1)
        second = overlay("src/main.py", "def second_buffer():\n    return 2\n", 2)
        self.assertEqual(self.backend.search("first_buffer", retrieval="bm25", overlays=[first])["results"][0]["symbol"],
                         "first_buffer")
        response = self.backend.search("second_buffer", retrieval="bm25", overlays=[second])
        self.assertEqual(response["results"][0]["buffer_version"], 2)
        self.assertEqual(response["results"][0]["buffer_hash"], second["content_hash"])
        previous_query = self.backend.search("first_buffer", retrieval="bm25", overlays=[second])
        self.assertNotIn("first_buffer", [row["symbol"] for row in previous_query["results"]])
        self.assertEqual(self.backend.search("saved_name", retrieval="bm25")["results"][0]["symbol"], "saved_name")

    def test_js_tsx_unicode_bom_and_physical_lines_follow_literal_buffer_text(self):
        for extension, source in ((".js", "\ufefffunction café(){\r\n return 'λ 😀';\r\n}\r\n"),
                                  (".tsx", "const View = () => {\r return <span>λ 😀</span>;\r};\r")):
            path = "src/component" + extension
            (self.repo / path).write_text("function saved_component(){return 1;}\n")
            self.git("add", path)
            response = self.backend.search("café" if extension == ".js" else "View", retrieval="bm25",
                                           overlays=[overlay(path, source, 6)])
            row = next(item for item in response["results"] if item["path"] == path)
            self.assertEqual(row["source"], source)
            self.assertEqual(row["source_origin"], "overlay")
            self.assertEqual((row["start_line"], row["end_line"]), (1, 3))
            self.assertEqual(row["buffer_hash"], hashlib.sha256(source.encode("utf-8")).hexdigest())

    def test_dense_overlay_lazily_initializes_one_parent_model_without_saved_index(self):
        self.backend._embedder = None
        entries = [overlay("src/main.py", "def overlay_name():\n    return 2\n")]
        with patch("semantic_search.embeddings.QwenEmbedder", return_value=self.embedder) as factory:
            self.backend.search("anything", overlays=entries)
            self.backend.search("anything", overlays=entries)
        factory.assert_called_once_with(progress=False)
        self.assertIs(self.backend._embedder, self.embedder)
        self.assertIsNone(self.backend._index)
        self.assertIsNone(self.backend._manifest)
        self.assertFalse(self.backend.db_path.exists())
        self.assertEqual(self.embedder.closes, 0)

    def test_temp_request_databases_are_disposed_on_success_failure_and_cancellation(self):
        created = []
        factory = tempfile.TemporaryDirectory

        def track(*args, **kwargs):
            temporary = factory(*args, **kwargs)
            created.append(Path(temporary.name))
            return temporary

        entries = [overlay("src/main.py", "def overlay_name():\n    return 2\n")]
        with patch("semantic_search.overlays.tempfile.TemporaryDirectory", side_effect=track):
            self.backend.search("overlay_name", retrieval="bm25", overlays=entries)
            self.embedder.on_documents = lambda: self.backend.cancel("cancel-overlay")
            with self.assertRaises(SearchCancelled):
                self.backend.search("anything", overlays=entries, request_id="cancel-overlay")
            self.embedder.on_documents = lambda: self.path.write_text("def raced():\n    return 3\n")
            with self.assertRaises(SourceSnapshotError):
                self.backend.search("anything", overlays=entries)
        self.assertEqual(len(created), 3)
        self.assertTrue(all(not path.exists() for path in created))
        self.assertEqual(self.embedder.closes, 0)
        self.assertIsNone(self.backend._index)

    def test_cancelled_requests_register_once_and_leave_saved_index_intact(self):
        self.backend.refresh()
        before = self.saved_state()
        observed = []

        def cancel():
            observed.append(list(self.backend._requests.active))
            self.backend.cancel("overlay-request")

        self.embedder.on_documents = cancel
        with self.assertRaises(SearchCancelled):
            self.backend.search("anything", overlays=[overlay("src/main.py", "def overlay_name():\n    return 2\n")],
                                request_id="overlay-request")
        self.assertEqual(observed, [["overlay-request"]])
        self.assertEqual(self.backend._requests.active, {})
        self.assertEqual(self.saved_state(), before)
        self.assertEqual(self.embedder.closes, 0)

    def test_cancel_while_waiting_for_global_lock_never_backs_up_or_loads_model(self):
        errors = []
        entered = threading.Event()
        entries = [overlay("src/main.py", "def overlay_name():\n    return 2\n")]

        def request():
            entered.set()
            try:
                self.backend.search("anything", overlays=entries, request_id="queued-overlay")
            except Exception as error:
                errors.append(error)

        with self.backend._lock, patch.object(self.backend, "_snapshot", side_effect=AssertionError("snapshot before lock")):
            worker = threading.Thread(target=request)
            worker.start()
            self.assertTrue(entered.wait(2))
            self.backend.cancel("queued-overlay")
            worker.join(2)
            self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], SearchCancelled)
        self.assertFalse(self.backend.db_path.exists())
        self.assertEqual(self.embedder.documents, [])

    def test_buffer_bindings_and_source_origin_are_counted_before_token_packing(self):
        source = "def overlay_name():\n" + "".join(f"    item{i} = '{'data ' * 30}'\n" for i in range(30)) + "    return item29\n"
        entry = overlay("src/main.py", source, 9)
        result = self.backend.search("overlay_name", retrieval="bm25", overlays=[entry], token_budget=512)
        compact = compact_result(result)
        self.assertLessEqual(result["token_usage"]["tokens"], 512)
        self.assertEqual(count_context(result), result["token_usage"]["tokens"])
        self.assertEqual(compact["scope"]["overlays"], [{key: entry[key] for key in ("path", "version", "content_hash")}])
        self.assertEqual(compact["results"][0]["source_origin"], "overlay")
        self.assertEqual(compact["results"][0]["buffer_version"], 9)
        self.assertEqual(compact["results"][0]["buffer_hash"], entry["content_hash"])
        self.assertTrue(result["token_usage"]["truncated"])
        no_source = self.backend.search("overlay_name", retrieval="bm25", overlays=[entry], source_budget=0)
        self.assertEqual(no_source["results"], [])
        self.assertEqual(no_source["scope"]["overlays"], compact["scope"]["overlays"])


class OverlayValidationTests(unittest.TestCase):
    def test_versions_hashes_unicode_canonical_paths_duplicates_and_unknown_fields(self):
        valid = overlay("src/code.py", "def okay():\n    return 'é'\n")
        cases = [[{**valid, "version": True}], [{**valid, "version": -1}], [{**valid, "version": 2**53}],
                 [{**valid, "content_hash": "0" * 64}], [{**valid, "content_hash": valid["content_hash"].upper()}],
                 [{**valid, "text": b"bytes"}], [{**valid, "text": "\ud800"}], [{**valid, "path": "/src/code.py"}],
                 [{**valid, "path": "src/../code.py"}], [{**valid, "path": "./src/code.py"}],
                 [{**valid, "path": "src\\code.py"}], [{**valid, "path": "src//code.py"}],
                 [{**valid, "path": "src/\x00code.py"}], [{**valid, "path": "x" * 513}],
                 [{**valid, "extra": "unknown"}], [{key: value for key, value in valid.items() if key != "version"}],
                 [valid, valid], {"src/code.py": valid}, "not a list"]
        for entries in cases:
            with self.subTest(entries=str(entries)[:80]), self.assertRaises(ValueError):
                normalize_overlays(entries)
        self.assertEqual(normalize_overlays([]), ())
        self.assertEqual(normalize_overlays(None), ())

    def test_number_per_file_and_total_utf8_byte_caps(self):
        with self.assertRaises(ValueError):
            normalize_overlays([overlay(f"src/{i}.py", "") for i in range(MAX_OVERLAY_FILES + 1)])
        with self.assertRaises(ValueError):
            normalize_overlays([overlay("src/code.py", "é" * (MAX_OVERLAY_FILE_BYTES // 2 + 1))])
        full = [overlay(f"src/{i}.py", "#" * MAX_OVERLAY_FILE_BYTES) for i in range(MAX_OVERLAY_TOTAL_BYTES // MAX_OVERLAY_FILE_BYTES)]
        self.assertEqual(len(normalize_overlays(full)), len(full))
        with self.assertRaises(ValueError):
            normalize_overlays(full + [overlay("src/more.py", "x")])


class OverlayTransportTests(OverlayFixture):
    def start_server(self):
        server = create_server(self.backend, port=0)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01})
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join(2)

        self.addCleanup(stop)
        return server.server_address[1]

    def test_real_client_large_utf8_overlay_roundtrip_and_stale_delivery_callback(self):
        port = self.start_server()
        client = SearchClient(f"http://127.0.0.1:{port}")
        text = "#" + "😀" * 19000 + "\ndef overlay_name():\n    return 'é'\n"
        entry = overlay("src/main.py", text, 12)
        state = {entry["path"]: {"version": entry["version"], "content_hash": entry["content_hash"]}}
        result = client.search("overlay_name", retrieval="bm25", overlays=[entry], current_buffers=lambda: state)
        self.assertEqual(result["results"][0]["source"], "def overlay_name():\n    return 'é'\n")
        self.assertEqual(result["results"][0]["buffer_hash"], entry["content_hash"])
        state[entry["path"]]["version"] = 13
        with self.assertRaises(StaleBufferError):
            client.search("overlay_name", retrieval="bm25", overlays=[entry], current_buffers=lambda: state)
        self.assertFalse(self.backend.db_path.exists())

    def test_buffer_freshness_checks_all_inputs_even_without_overlay_results(self):
        entry = overlay("src/main.py", "def overlay_name():\n    return 2\n", 4)
        response = self.backend.search("shared", retrieval="bm25", overlays=[entry])
        self.assertEqual(response["results"][0]["source_origin"], "saved")
        state = {entry["path"]: {"version": 4, "content_hash": entry["content_hash"]}}
        self.assertIs(ensure_overlay_current(response, state), response)
        for current in ({}, {entry["path"]: {"version": 5, "content_hash": entry["content_hash"]}},
                        {entry["path"]: {"version": 4, "content_hash": "0" * 64}}):
            with self.subTest(current=current), self.assertRaises(StaleBufferError):
                ensure_overlay_current(response, current)

    def test_overlay_conflicts_validation_transport_ceiling_and_saved_transport_cap(self):
        port = self.start_server()

        def request(data=None, raw=None, length=None):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            body = raw if raw is not None else json.dumps(data).encode()
            headers = {"Content-Type": "application/json"}
            if length is not None:
                headers["Content-Length"] = str(length)
            connection.request("POST", "/search", body=body, headers=headers)
            response = connection.getresponse()
            result = response.status, json.loads(response.read())
            connection.close()
            return result

        bad = overlay("src/main.py", "def invalid(\n")
        self.assertEqual(request({"query": "anything", "retrieval": "bm25", "overlays": [bad]})[0], 409)
        bad["content_hash"] = "0" * 64
        self.assertEqual(request({"query": "anything", "retrieval": "bm25", "overlays": [bad]})[0], 400)
        self.assertEqual(request(raw=b"{}", length=MAX_OVERLAY_BODY_BYTES + 1)[0], 413)
        raw = b'{"query":"anything","retrieval":"bm25"}' + b" " * (64 * 1024)
        self.assertEqual(request(raw=raw)[0], 413)


if __name__ == "__main__":
    unittest.main()
