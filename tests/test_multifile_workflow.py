"""Exercise the real backend/HTTP/MCP contracts without loading weights."""
from contextlib import closing
import hashlib
import threading
from types import SimpleNamespace

import numpy as np

from semantic_search.backend import SearchBackend
from semantic_search.client import SearchClient
from semantic_search.context import compact_result, encoding, serialize_context
from semantic_search.server import create_server
from semantic_search.retrieval import MultifileRetriever


class Embedder:
    metadata = {"id": "multifile-test", "dimensions": 2}

    def __init__(self):
        self.queries, self.documents = [], []

    def embed_documents(self, texts):
        self.documents.append(texts)
        return np.tile([1., 0.], (len(texts), 1))

    def embed_query(self, query):
        self.queries.append(query)
        return np.array([1., 0.])

    def close(self):
        pass


def test_multifile_http_and_mcp_find_complementary_sources_with_one_embedding(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    for number in range(12):
        (source / f"anchor{number:02}.py").write_text(f"def anchor{number}():\n    return {number}\n")
    (source / "signed.py").write_text("def signed_cookie():\n    return 'normalize signed cookie data'\n")
    (source / "registry.py").write_text("def weak_registry():\n    return 'persist weak registry mappings'\n")
    embedder = Embedder()
    with closing(SearchBackend(tmp_path, "src", tmp_path / "index.sqlite3", embedder=embedder)) as backend:
        backend.refresh()
        # Deliberately put both implementations beyond the response prefix.
        ordered = sorted(backend._snippets, key=lambda s: (not s.path.startswith("src/anchor"), s.id))
        backend._dense = SimpleNamespace(snippets=ordered,
            search=lambda vector, limit=10: [(s, .95 - n * .001) for n, s in enumerate(ordered[:limit])])
        backend._multifile = MultifileRetriever(backend._lexical, backend._dense)
        # A weak-match threshold must see full-query cosine, never merged rank priority.
        backend._policy = SimpleNamespace(threshold=.8, rejects=lambda cosine, metadata: cosine < .8)
        server = create_server(backend, port=0)
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01})
        worker.start()
        try:
            client = SearchClient(f"http://127.0.0.1:{server.server_address[1]}")
            query = "Normalize signed cookie data, then persist weak registry mappings"
            settings = dict(max_results=8, include_dependencies=True, token_budget=4096)
            baseline = client.search(query, retrieval="dense", **settings)
            result = client.search(query, retrieval="multifile", **settings)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
        assert not {"signed_cookie", "weak_registry"} & {r["symbol"] for r in baseline["results"]}
        assert {"signed_cookie", "weak_registry"} <= {r["symbol"] for r in result["results"]}
        assert len(embedder.queries) == 2 and len(embedder.documents) == 1
        assert result["match_status"] == "candidates"
        assert result["match_evidence"]["top_cosine"] == .95
        assert result["match_evidence"]["retrieval_score_kind"] == "multifile_rank_priority"
        for row in result["results"]:
            assert row["source_hash"] == hashlib.sha256(row["source"].encode()).hexdigest()
        envelope = compact_result(result)
        assert result["token_usage"]["tokens"] == len(encoding().encode(serialize_context(envelope))) <= 4096
        from semantic_search.mcp_server import SearchArguments, _validate_context
        arguments = SearchArguments(query=query, retrieval="multifile", **settings)
        _validate_context(envelope, arguments)
