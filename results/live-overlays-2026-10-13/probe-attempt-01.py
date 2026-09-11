#!/usr/bin/env python3
"""Real Qwen/HTTP integration on authored buffers; never a relevance benchmark."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def files(paths):
    return {path.name: {"sha256": sha(path.read_bytes()), "bytes": path.stat().st_size,
                        "mtime_ns": path.stat().st_mtime_ns, "inode": path.stat().st_ino}
            for path in paths if path.exists()}


def verify(output, model_path=None):
    from semantic_search.backend import SearchBackend
    from semantic_search.client import SearchClient, StaleBufferError, ensure_overlay_current
    from semantic_search.context import compact_result, count_context
    from semantic_search.embeddings import QwenEmbedder
    from semantic_search.languages import decode_source
    from semantic_search.server import create_server
    from semantic_search.source import physical_lines

    class Observed(QwenEmbedder):
        def __init__(self):
            super().__init__(model_path=model_path, progress=False)
            self.documents = self.queries = self.document_calls = 0

        def embed_documents(self, texts, *, cancel_check=None):
            result = super().embed_documents(texts, cancel_check=cancel_check)
            self.document_calls += 1
            self.documents += len(texts)
            return result

        def embed_query(self, text):
            result = super().embed_query(text)
            self.queries += 1
            return result

        def counts(self):
            return {"completed_document_calls": self.document_calls,
                    "document_texts": self.documents, "completed_query_calls": self.queries}

    require(not output.exists(), "Refusing to overwrite a live integration attempt")
    output.parent.mkdir(parents=True, exist_ok=True)
    record = {"classification": "Real Qwen and HTTP authored-buffer integration, not retrieval quality or performance",
              "started_utc": datetime.now(timezone.utc).isoformat(), "passed": False,
              "checks": {}, "responses": [], "weights": "Existing pinned checkpoint; no fresh download",
              "implementation_sha256": {str(p.relative_to(ROOT)): sha(p.read_bytes())
                  for p in [*sorted((ROOT / "src/semantic_search").glob("*.py")), Path(__file__)]},
              "limits": ["Four-function fixture does not establish general relevance or coding correctness.",
                         "Counts observe completed embed_documents/embed_query calls, not individual Metal forwards.",
                         "Request-only overlays re-embed changed text on later requests; no session cache.",
                         "No cross-process SQLite lock or interruption of an active Metal forward is established."]}
    backend = server = worker = None
    try:
        with tempfile.TemporaryDirectory(prefix="semantic-live-overlays-") as temporary:
            repo = Path(temporary).resolve(); (repo / "src").mkdir()
            saved = {"src/value.py": b"# coding: latin-1\nfrom .helper import label\n\ndef saved_value():\n    return 'caf\xe9'\n",
                     "src/helper.py": b"def label():\n    return 'shared'\n",
                     "src/math.js": b"\xef\xbb\xbfexport function savedJavascript(value) {\r\n  return value + 1;\r\n}\r\n",
                     "src/math.ts": b"export function savedTypeScript(value: number): number {\n  return value * 2;\n}\n"}
            for name, data in saved.items():
                (repo / name).write_bytes(data)
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
            subprocess.run(["git", "-C", str(repo), "add", "src"], check=True)
            texts = {"src/value.py": "# coding: latin-1\nfrom .helper import label\n\ndef overlay_value():\n    return 'café🧪 ' + label()\n",
                     "src/math.js": "\ufeffexport function overlayJavascript(value) {\r\n  return value - 3;\r\n}\r\n",
                     "src/math.ts": "export function overlayTypeScript(value: number): number {\n  return value / 2;\n}\n"}
            bundle = [{"path": name, "text": text, "version": n + 7, "content_hash": sha(text.encode())}
                      for n, (name, text) in enumerate(texts.items())]
            bindings = {item["path"]: {key: item[key] for key in ("version", "content_hash")} for item in bundle}
            decoded = {name: texts.get(name, decode_source(name, data)) for name, data in saved.items()}
            model = Observed(); backend = SearchBackend(repo, "src", repo / "saved.sqlite3", embedder=model)
            server = create_server(backend, port=0)
            worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .05}); worker.start()
            client = SearchClient(f"http://127.0.0.1:{server.server_address[1]}")
            settings = {"rerank": False, "max_results": 8, "token_budget": 4096,
                        "source_budget": 12000, "include_dependencies": True}
            client.search("Return the saved value", retrieval="dense", **settings)
            require(model._model is not None and model._checkpoint_verified, "Actual pinned Qwen was not loaded")
            record["embedding"] = model.metadata
            record["embedding_after_saved_warm"] = model.counts()
            before_status = deepcopy(backend.status())
            cache_paths = [backend.db_path, Path(str(backend.db_path) + "-wal"), Path(str(backend.db_path) + "-shm")]
            before_cache = files(cache_paths)
            before_sources = {name: sha((repo / name).read_bytes()) for name in saved}

            def check_response(response):
                require(response["scope"]["source_state"] == "overlay", "Missing overlay scope")
                ensure_overlay_current(response, bindings)
                require(len(response["scope"]["overlays"]) == len(bundle), "Input binding omitted")
                require(count_context(compact_result(response)) == response["token_usage"]["tokens"] <= response["token_usage"]["budget"], "Token provenance differs")
                require(sum(len(row["source"]) for row in response["results"]) == response["total_source_chars"] <= response["source_budget"], "Source counter differs")
                for row in response["results"]:
                    lines = physical_lines(decoded[row["path"]])
                    expected = "".join(lines[row["start_line"] - 1:row["end_line"]])
                    full = "".join(lines[row["start_line"] - 1:row["window_end_line"]])
                    require(row["source"] == expected and row["source_hash"] == sha(expected.encode()), "Literal excerpt mismatch")
                    require(row["content_hash"] == sha(full.encode()), "Full-window mismatch")
                    if row["path"] in bindings:
                        require(row["source_origin"] == "overlay" and row["buffer_version"] == bindings[row["path"]]["version"]
                                and row["buffer_hash"] == bindings[row["path"]]["content_hash"], "Wrong overlay origin")
                    else:
                        require(row["source_origin"] == "saved", "Wrong saved origin")
                public = deepcopy(response); public["repo"] = "<fixture>"
                record["responses"].append(public)

            for query, symbol in [("Return the café test-tube literal and shared label", "overlay_value"),
                                  ("Subtract three from the input", "overlayJavascript"),
                                  ("Divide a typed number by two", "overlayTypeScript")]:
                response = client.search(query, retrieval="dense", overlays=bundle,
                                         current_buffers=lambda: deepcopy(bindings), **settings)
                check_response(response)
                require(any(row["symbol"] == symbol for row in response["results"]), "Fixture implementation missing")
            record["checks"]["real_qwen_three_language_overlays"] = True
            record["checks"]["literal_unicode_cookie_bom_crlf_and_saved_context"] = any(
                row.get("source_origin") == "saved" for response in record["responses"] for row in response["results"])
            narrow = client.search("overlayTypeScript", retrieval="bm25", overlays=bundle,
                                   **{**settings, "max_results": 1, "include_dependencies": False})
            check_response(narrow)
            omitted = next(name for name in bindings if name not in {row["path"] for row in narrow["results"]})
            stale = deepcopy(bindings); stale[omitted]["version"] += 1
            try:
                ensure_overlay_current(narrow, stale)
            except StaleBufferError:
                record["checks"]["unreturned_buffer_change_invalidates_response"] = True
            else:
                raise ValueError("A changed nonreturned input did not invalidate the result")
            clipped = client.search("Divide a typed number by two", retrieval="dense", overlays=bundle,
                                    **{**settings, "token_budget": 512, "source_budget": 80})
            check_response(clipped)
            record["checks"]["provenance_in_small_token_and_source_caps"] = True
            try:
                client.search("Return the literal", retrieval="dense", overlays=bundle,
                              current_buffers=lambda: stale, **settings)
            except StaleBufferError:
                record["checks"]["client_delivery_callback_rejects_changed_buffer"] = True
            else:
                raise ValueError("Stale delivery callback accepted a result")
            record["embedding_after_overlay_requests"] = model.counts()
            record["saved_cache_before"] = before_cache
            record["saved_cache_after"] = files(cache_paths)
            record["checks"]["saved_database_sidecars_bytes_mtime_membership_unchanged"] = files(cache_paths) == before_cache
            record["checks"]["parent_status_unchanged"] = backend.status() == before_status
            record["source_before_sha256"] = before_sources
            record["source_after_sha256"] = {name: sha((repo / name).read_bytes()) for name in saved}
            record["checks"]["saved_sources_unchanged"] = before_sources == record["source_after_sha256"]
            again = client.search("saved_value", retrieval="bm25", **settings)
            require(any(row["symbol"] == "saved_value" for row in again["results"])
                    and "source_state" not in again["scope"], "Overlay escaped its request")
            record["checks"]["later_saved_search_uses_saved_source"] = True
            server.shutdown(); server.server_close(); worker.join(); server = worker = None
            backend.close(); backend = None
            reopened_model = Observed(); reopened = SearchBackend(repo, "src", repo / "saved.sqlite3", embedder=reopened_model)
            try:
                record["persistent_reopen"] = {"indexstats": reopened.refresh(), "embedding_calls": reopened_model.counts()}
                stats = record["persistent_reopen"]["indexstats"]
                require(stats["indexed_windows"] == 0 and stats["reused_windows"] == stats["total_windows"]
                        and reopened_model.documents == reopened_model.queries == 0, "Saved restart failed vector reuse")
                record["checks"]["saved_restart_reuses_all_vectors_without_inference"] = True
            finally:
                reopened.close()
            require(all(record["checks"].values()), "An integration check failed")
            record["passed"] = True
    except Exception as error:
        record["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        if server is not None:
            server.shutdown(); server.server_close(); worker.join()
        if backend is not None:
            backend.close()
        record["finished_utc"] = datetime.now(timezone.utc).isoformat()
        output.write_text(json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n")
    print(json.dumps({"passed": record["passed"], "checks": record["checks"], "output": str(output)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "results/live-overlays-2026-10-13/proof.json")
    parser.add_argument("--model-path", type=Path)
    args = parser.parse_args()
    verify(args.output, args.model_path)


if __name__ == "__main__":
    main()
