"""Product defaults use the previously evaluated definition/coverage control.

Only CPU fixture embedders run here. The default selector/postprocessor are never
patched: the expected source context is assembled by directly invoking the
existing control functions over the actual resident retriever's ranked pairs.
"""
import asyncio
from contextlib import ExitStack, closing
from copy import deepcopy
import hashlib
import json
import re
import subprocess
from unittest.mock import patch

import numpy as np
import pytest

from semantic_search import backend as backend_module, context, context_backfill, selection
from semantic_search.backend import SearchBackend, SourceSnapshotError
from semantic_search.cancellation import SearchCancelled
from semantic_search.source import physical_lines


QUERY = "implementation return"
LIMITS = [
    {"max_results": 8, "source_budget": 12000, "token_budget": 4096},
    {"max_results": 4, "source_budget": 150, "token_budget": 1024},
    {"max_results": 2, "source_budget": 60, "token_budget": 512},
    {"max_results": 8, "source_budget": 12000, "token_budget": 512},
    {"max_results": 8, "source_budget": 0, "token_budget": 512},
]


class CountingEmbedder:
    metadata = {"id": "default-context-contract", "revision": "fixed", "dimensions": 2}

    def __init__(self):
        self.documents = self.queries = 0
        self.after_query = lambda: None

    def embed_documents(self, texts):
        self.documents += 1
        vectors = []
        for text in texts:
            match = re.search(r"def implementation_(\d+)", text)
            number = int(match[1]) if match else int(re.search(r"# rank: (\d+)", text)[1])
            vectors.append([1., number / 10])
        return np.asarray(vectors).reshape(-1, 2)

    def embed_query(self, query):
        self.queries += 1
        self.after_query()
        return np.asarray([1., 0.])

    def close(self):
        pass


@pytest.fixture
def make_backend(tmp_path):
    with ExitStack() as stack:
        counter = 0

        def make(files):
            nonlocal counter
            repo = tmp_path / str(counter)
            counter += 1
            for name, text in files.items():
                path = repo / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(text.encode())
            embedder = CountingEmbedder()
            backend = stack.enter_context(closing(SearchBackend(
                repo, "src", repo / "index.sqlite3", embedder=embedder)))
            return repo, backend, embedder

        yield make


def containing_files(*, nested_dependency=False):
    body = "implementation_9()" if nested_dependency else "'café'"
    code = (("from helper import implementation_9\n\n" if nested_dependency else "")
            + "def implementation_1():\n"
            "    def implementation_2():\n"
            f"        return {body}\n"
            "    return implementation_2()\n\n")
    code += "\n".join(f"def implementation_{n}():\n    return {n}\n" for n in range(3, 9))
    return {"src/code.py": code, "src/helper.py": "def implementation_9():\n    return 9\n"}


def source_view(response):
    return {key: deepcopy(response[key]) for key in (
        "query", "scope", "match_status", "results", "total_source_chars",
        "budget_exhausted", "token_usage", "_selection_backfill") if key in response}


def explicit_control(backend, actual, *, retrieval, include_dependencies, **limits):
    """Use existing control functions and the real raw ranks, without embedding again."""
    vector = np.asarray([1., 0.])
    if retrieval == "dense":
        pairs = backend._dense.search(vector, 50)
    elif retrieval == "hybrid":
        pairs = backend._hybrid.search(QUERY, vector, 50)
    else:
        pairs = backend._lexical.search(QUERY, 50)
    selected, eligible, origins = selection.definition_context(
        pairs, query=QUERY, max_results=limits["max_results"],
        include_dependencies=include_dependencies, references=backend._references,
        definition_keys=backend._definition_keys,
        ambiguous_symbols=backend._ambiguous_definitions)

    def row(snippet, score, source, origin="retrieval", related_to=None):
        return {"id": snippet.id, "path": snippet.path, "symbol": snippet.symbol,
                "start_line": snippet.start_line,
                "end_line": snippet.start_line + len(physical_lines(source)) - 1,
                "window_end_line": snippet.end_line, "source": source,
                "content_hash": snippet.content_hash,
                "source_hash": hashlib.sha256(source.encode()).hexdigest(),
                "retrieval_score": float(score), "h2o_score": None, "qwen_score": None,
                "origin": origin, "related_to": related_to,
                "truncated": source != snippet.text}

    remaining, rows = limits["source_budget"], []
    for snippet, score in eligible:
        lines, used = [], 0
        for line in physical_lines(snippet.text):
            if used + len(line) > remaining:
                break
            lines.append(line)
            used += len(line)
        source = "".join(lines)
        if source:
            remaining -= used
            rows.append(row(snippet, score, source, *origins[snippet.id]))
    packed = {key: deepcopy(actual[key]) for key in ("query", "scope", "match_status")}
    packed.update(results=rows, total_source_chars=limits["source_budget"] - remaining,
                  budget_exhausted=any(r["truncated"] for r in rows) or len(rows) < len(eligible))
    packed = context.apply_token_budget(packed, limits["token_budget"])
    return context_backfill.coalesce_context(packed,
        (row(snippet, score, snippet.text) for snippet, score in selected), **limits)


def assert_literal(repo, response, *, texts=None):
    for row in response["results"]:
        full = texts[row["path"]] if texts and row["path"] in texts else (repo / row["path"]).read_bytes().decode()
        lines = physical_lines(full)
        assert row["source"] == "".join(lines[row["start_line"] - 1:row["end_line"]])
        assert row["source_hash"] == hashlib.sha256(row["source"].encode()).hexdigest()
        original_window = "".join(lines[row["start_line"] - 1:row["window_end_line"]])
        assert row["content_hash"] == hashlib.sha256(original_window.encode()).hexdigest()
        assert row["h2o_score"] is None and row["qwen_score"] is None
        assert "assembly" not in row


def assert_mcp(response, **settings):
    pytest.importorskip("mcp")
    from semantic_search.mcp_server import SearchArguments, _validate_context

    compact = context.compact_result(response)
    assert json.loads(_validate_context(compact, SearchArguments(query=QUERY, **settings))) == compact
    return compact


def test_only_backend_default_aliases_change_and_research_aliases_stay_legacy():
    assert backend_module.select_context is selection.definition_context
    assert backend_module.postprocess_context is context_backfill.coalesce_context
    assert selection.select_context is selection.ranked_context
    assert selection.postprocess_context is selection.unchanged_context


@pytest.mark.parametrize("retrieval", ["dense", "hybrid", "bm25"])
@pytest.mark.parametrize("include_dependencies", [False, True])
@pytest.mark.parametrize("limits", LIMITS)
def test_actual_default_equals_existing_control_under_normal_and_small_caps(
        make_backend, retrieval, include_dependencies, limits):
    repo, backend, embedder = make_backend(containing_files())
    settings = dict(retrieval=retrieval, include_dependencies=include_dependencies, **limits)
    response = backend.search(QUERY, **settings)
    assert source_view(response) == source_view(explicit_control(backend, response, **settings))
    assert embedder.queries == (retrieval != "bm25")
    assert embedder.documents == (retrieval != "bm25")
    assert len(response["results"]) <= limits["max_results"]
    assert response["total_source_chars"] <= limits["source_budget"]
    assert response["token_usage"]["tokens"] <= limits["token_budget"]
    assert_literal(repo, response)
    assert_mcp(response, **settings)


@pytest.mark.parametrize("include_dependencies", [False, True])
def test_covered_nested_source_frees_slot_for_complementary_file(make_backend, include_dependencies):
    repo, backend, embedder = make_backend(containing_files())
    response = backend.search(QUERY, include_dependencies=include_dependencies, **LIMITS[0])
    assert [row["symbol"] for row in response["results"]] == [
        "implementation_1", *[f"implementation_{n}" for n in range(3, 10)]]
    assert response["results"][-1]["path"] == "src/helper.py"
    assert len(response["_selection_backfill"]["coalesced_ids"]) == 1
    assert response["_selection_backfill"]["appended_ids"] == [response["results"][-1]["id"]]
    assert embedder.documents == embedder.queries == 1
    assert_literal(repo, response)


def test_contained_dependency_owner_remains_truthful_original_source_identifier(make_backend):
    repo, backend, _ = make_backend(containing_files(nested_dependency=True))
    settings = dict(retrieval="dense", include_dependencies=True, **LIMITS[0])
    response = backend.search(QUERY, **settings)
    assert source_view(response) == source_view(explicit_control(backend, response, **settings))
    snippets = {snippet.symbol: snippet for snippet in backend._snippets}
    owner = snippets["implementation_1.implementation_2"]
    dependency = next(row for row in response["results"] if row["symbol"] == "implementation_9")
    assert owner.id not in {row["id"] for row in response["results"]}
    assert dependency["origin"] == "dependency" and dependency["related_to"] == owner.id
    raw = {snippet.id: score for snippet, score in backend._dense.search(np.asarray([1., 0.]), 50)}
    assert dependency["retrieval_score"] == raw[owner.id]
    assert dependency["retrieval_score"] != raw[dependency["id"]]
    outer = next(row for row in response["results"] if row["symbol"] == "implementation_1")
    offset = owner.start_line - outer["start_line"]
    assert "".join(physical_lines(outer["source"])[offset:offset + len(physical_lines(owner.text))]) == owner.text
    compact = assert_mcp(response, **settings)
    assert all("id" not in row for row in compact["results"])
    assert_literal(repo, response)


def test_actual_ambiguous_definitions_are_retained_without_false_dependency_owner(make_backend):
    code = ("def duplicate():\n    # rank: 1\n    return implementation_9()\n\n"
            "def duplicate():\n    # rank: 2\n    return implementation_9()\n\n"
            "def implementation_9():\n    return 9\n")
    repo, backend, embedder = make_backend({"src/code.py": code})
    response = backend.search(QUERY, max_results=8, include_dependencies=True, token_budget=4096)
    duplicates = [row for row in response["results"] if row["symbol"] == "duplicate"]
    assert len(duplicates) == 2 and {row["start_line"] for row in duplicates} == {1, 5}
    assert backend._ambiguous_definitions == {("src/code.py", "duplicate")}
    assert all(row["origin"] == "retrieval" and row["related_to"] is None for row in response["results"])
    assert embedder.documents == embedder.queries == 1
    assert_literal(repo, response)
    assert_mcp(response, max_results=8, include_dependencies=True, token_budget=4096)


def test_warm_default_keeps_document_vectors_and_uses_one_query_embedding(make_backend):
    _, backend, embedder = make_backend(containing_files())
    first = backend.search(QUERY, include_dependencies=True, **LIMITS[0])
    with patch("semantic_search.backend.extract_snippets", side_effect=AssertionError("warm reparse")):
        second = backend.search(QUERY, include_dependencies=True, **LIMITS[0])
    assert source_view(second) == source_view(first)
    assert second["indexstats"]["memory_cache_hit"]
    assert embedder.documents == 1 and embedder.queries == 2


def test_default_overlay_keeps_literal_buffer_provenance_and_saved_index(make_backend):
    files = containing_files()
    repo, backend, _ = make_backend(files)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "src"], check=True)
    saved = backend.search(QUERY, include_dependencies=True, **LIMITS[0])
    text = files["src/code.py"].replace("'café'", "'unsaved café'")
    digest = hashlib.sha256(text.encode()).hexdigest()
    response = backend.search(QUERY, include_dependencies=True, overlays=[
        {"path": "src/code.py", "version": 17, "text": text, "content_hash": digest}], **LIMITS[0])
    assert response["results"] and "unsaved café" in response["results"][0]["source"]
    for row in response["results"]:
        if row["path"] == "src/code.py":
            assert row["source_origin"] == "overlay"
            assert row["buffer_version"] == 17 and row["buffer_hash"] == digest
        else:
            assert row["source_origin"] == "saved"
            assert not {"buffer_version", "buffer_hash"} & row.keys()
    assert_literal(repo, response, texts={"src/code.py": text})
    assert source_view(backend.search(QUERY, include_dependencies=True, **LIMITS[0])) == source_view(saved)
    assert (repo / "src/code.py").read_bytes().decode() == files["src/code.py"]


def test_cancel_after_query_forward_recovers_with_same_document_cache(make_backend):
    _, backend, embedder = make_backend(containing_files())
    backend.refresh()
    embedder.after_query = lambda: backend.cancel("default-cancel")
    with pytest.raises(SearchCancelled):
        backend.search(QUERY, request_id="default-cancel", **LIMITS[0])
    embedder.after_query = lambda: None
    recovered = backend.search(QUERY, **LIMITS[0])
    assert recovered["indexstats"]["memory_cache_hit"]
    assert embedder.documents == 1 and embedder.queries == 2


@pytest.mark.parametrize("interruption", ["cancel", "source_change"])
def test_final_snapshot_and_cancellation_checks_still_follow_default_coalescing(make_backend, interruption):
    repo, backend, _ = make_backend(containing_files())
    original = context_backfill._recount
    count = 0

    def interrupt(response):
        nonlocal count
        value = original(response)
        count += 1
        if count == 1:
            if interruption == "cancel":
                backend.cancel("default-pack")
            else:
                (repo / "src/helper.py").write_text("def implementation_9():\n    return 999\n")
        return value

    with patch.object(context_backfill, "_recount", interrupt):
        with pytest.raises(SearchCancelled if interruption == "cancel" else SourceSnapshotError):
            backend.search(QUERY, request_id="default-pack", **LIMITS[0])
    assert count >= 1


def test_actual_mcp_adapter_delivers_default_context_without_duplicate_source(make_backend):
    pytest.importorskip("mcp")
    from mcp.types import CallToolRequestParams
    from semantic_search.mcp_server import create_mcp_server

    _, backend, embedder = make_backend(containing_files())
    server = create_mcp_server(client=backend)
    result = asyncio.run(server._request_handlers["tools/call"].handler(
        None, CallToolRequestParams(name="search_code", arguments={"query": QUERY, **LIMITS[0]})))
    assert not result.is_error
    compact = result.structured_content
    assert json.loads(result.content[0].text) == compact
    assert [row["symbol"] for row in compact["results"]] == [
        "implementation_1", *[f"implementation_{n}" for n in range(3, 10)]]
    assert compact["token_usage"]["tokens"] <= LIMITS[0]["token_budget"]
    assert embedder.documents == embedder.queries == 1
