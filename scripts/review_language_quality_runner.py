#!/usr/bin/env python3
"""Independent runner review with artificial CPU vectors; no labels or models."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def vector_reuse():
    from semantic_search.backend import SearchBackend
    from semantic_search.client import SearchClient
    from semantic_search.embeddings import QwenEmbedder
    from semantic_search.quality import RecordingEmbedder
    from semantic_search.server import create_server

    query_calls = []
    observed_vector = np.array([1., .25, .5], dtype=np.float32)

    def fake_query(_self, query):
        query_calls.append(query)
        return observed_vector.copy()

    class CPUEmbedder(RecordingEmbedder):
        @property
        def metadata(self):
            return {"id": "independent-runner-fixture", "revision": "1", "dimensions": 3}

        def __init__(self):
            super().__init__(progress=False)
            self.document_calls = []

        def embed_documents(self, texts):
            self.document_calls.append(list(texts))
            return np.array([[1., number + .25, .5] for number in range(len(texts))], dtype=np.float32)

    class ReopenEmbedder(CPUEmbedder):
        def embed_documents(self, texts):
            raise AssertionError("Unchanged reopen invoked document inference")

    records = []
    with tempfile.TemporaryDirectory() as folder, patch.object(QwenEmbedder, "embed_query", fake_query):
        repo = Path(folder)
        (repo / "src").mkdir()
        (repo / "src/sample.py").write_text("def first():\n    return 1\n\ndef second():\n    return 2\n")
        db = repo / "index.sqlite3"
        embedder = CPUEmbedder()
        backend = SearchBackend(repo, "src", db, embedder=embedder)
        server = worker = None
        try:
            backend.search("warm", retrieval="dense", rerank=False, max_results=1)
            server = create_server(backend, port=0)
            worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01})
            worker.start()
            client = SearchClient(f"http://127.0.0.1:{server.server_address[1]}")
            for condition in ("bm25", "dense", "hybrid"):
                before = len(query_calls)
                vectors, phase = [], ["request"]
                search = backend._dense.search

                def observed_search(vector, *args, **kwargs):
                    vectors.append({"phase": phase[0], "sha256": sha(np.asarray(vector).tobytes())})
                    return search(vector, *args, **kwargs)

                with patch.object(backend._dense, "search", observed_search):
                    response = client.search("Find the return implementation", retrieval=condition,
                                             rerank=False, candidates=50, max_results=8, source_budget=12000,
                                             token_budget=4096, include_dependencies=True)
                    after_request = len(query_calls)
                    phase[0] = "candidate_diagnostic"
                    if condition == "bm25":
                        candidates = backend._lexical.search(response["query"], 50)
                    elif condition == "dense":
                        candidates = backend._dense.search(embedder.last_query, 50)
                    else:
                        candidates = backend._hybrid.search(response["query"], embedder.last_query, 50)
                expected = 0 if condition == "bm25" else 1
                assert after_request - before == expected
                assert len(query_calls) == after_request
                assert len(response["results"]) <= 8 and len(candidates) <= 50
                assert [row["id"] for row in response["results"]] == [snippet.id for snippet, _score in candidates[:8]]
                if condition != "bm25":
                    assert all(row["sha256"] == sha(embedder.last_query.tobytes()) for row in vectors)
                    assert {row["phase"] for row in vectors} == {"request", "candidate_diagnostic"}
                records.append({"condition": condition, "request_query_embedding_calls": expected,
                                "candidate_diagnostic_embedding_calls": 0,
                                "observed_dense_vectors": vectors,
                                "identical_request_and_diagnostic_vectors": True if condition != "bm25" else None,
                                "candidate_count": len(candidates), "result_count": len(response["results"])})
        finally:
            if server is not None:
                server.shutdown()
                server.server_close()
                worker.join()
            backend.close()
        reopen = SearchBackend(repo, "src", db, embedder=ReopenEmbedder())
        try:
            reuse = reopen.refresh()
            assert reuse["indexed_windows"] == 0 and reuse["reused_windows"] == reuse["total_windows"] == 2
        finally:
            reopen.close()
    return {"conditions": records, "persistent_reopen": {"document_embedding_calls": 0,
            "indexed_windows": reuse["indexed_windows"], "reused_windows": reuse["reused_windows"]}}


def main():
    path = ROOT / "scripts/run_language_quality.py"
    spec = importlib.util.spec_from_file_location("reviewed_language_runner", path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    result = {"classification": "Independent CPU review of prospective retrieval runner",
              "runner_sha256": sha(path.read_bytes()), "checks_passed": True,
              "real_model_calls": 0, "gold_files_read_by_this_script": 0,
              "inference_gate": "This mechanics review does not establish real-model quality or actual gold eligibility.",
              "cpu_fixture": vector_reuse(),
              "manual_review": [
                  "Scoring verifies exact pinned file window bytes/hash before any full-evidence hit; symbols are diagnostic.",
                  "OR alternatives and separate AND groups score independently; one complete window may satisfy several groups.",
                  "Only repetition1 supplies quality denominators; three repetitions supply warm latency samples and coverage stability.",
                  "Candidate reconstruction uses the current recorded numerical vector in a serialized request sequence.",
                  "Source membership/window hashes and runtime source/package/model metadata are rechecked against the freeze.",
                  "HTTP timing excludes deterministic candidate diagnostics and independent source/token verification."],
              "resolved_before_freeze": [
                  "Root added candidate_groups alongside delivered_groups.",
                  "Root added returned/candidate depth guards and observed completed inference counts.",
                  "Root added bounded per-source physical-line caching after the failed CPU gate.",
                  "Root freezes and verifies the exact dataset/query/condition/repetition request matrix."],
              "recommendations": [],
              "limitations": ["Artificial CPU vectors prove call/vector reuse mechanics, not Qwen quality or latency.",
                              "Actual model/checkpoint and completed real request inventory still require inference-time verification."]}
    output = ROOT / "results/language-runner-review-2026-10-13/review.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"checks_passed": True, "runner_sha256": result["runner_sha256"],
                      "cpu_fixture": result["cpu_fixture"]}, indent=2))


if __name__ == "__main__":
    main()
