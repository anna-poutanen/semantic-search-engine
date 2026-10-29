"""Invented-input checks for post hoc parity; no inference or indexing."""
from argparse import Namespace
from copy import deepcopy
import gzip
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("default_context_verification", ROOT / "scripts/verify_default_context.py")
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


def response():
    text = "def run():\n    return 1\n"
    row = {"id": "a.py:run:1-2", "path": "a.py", "symbol": "run", "start_line": 1,
           "end_line": 2, "window_end_line": 2, "source": text, "source_hash": verify.sha(text.encode()),
           "content_hash": verify.sha(text.encode()), "truncated": False, "origin": "retrieval",
           "related_to": None, "retrieval_score": 0.5, "h2o_score": None, "qwen_score": None}
    return {"query": "find run", "scope": {"source_root": "."}, "match_status": "candidates",
            "results": [row], "total_source_chars": len(text), "budget_exhausted": False,
            "token_usage": {"encoding": "cl100k_base", "tokens": 42, "budget": 4096, "truncated": False},
            "candidate_windows": 1, "match_evidence": {"policy": "invented"},
            "_selection_backfill": {"coalesced": [], "appended": []}, "timings": {"search_s": 1},
            "indexstats": {"memory_cache_hit": False}}


def expected(value=None):
    from semantic_search.context import compact_result, serialize_context
    value = response() if value is None else value
    text = serialize_context(compact_result(value))
    return {"response": value, "compact_payload": text, "compact_sha256": verify.sha(text.encode()),
            "query_vector_sha256": verify.sha(b"invented-query-vector")}


def test_full_view_ignores_only_operational_fields_and_preserves_private_key_presence():
    saved = expected()
    actual = deepcopy(saved["response"])
    actual["timings"] = {"different": 99}
    actual["indexstats"] = {"different": True}
    verify.check_response(saved, actual, saved["query_vector_sha256"], 1, 0)
    assert set(verify.source_view(actual)) == set(actual) - {"timings", "indexstats"}
    del actual["_selection_backfill"]
    with pytest.raises(ValueError, match="full source"):
        verify.check_response(saved, actual, saved["query_vector_sha256"], 1, 0)


@pytest.mark.parametrize("mutation", [
    lambda value: value["results"][0].update(source="def run():\n    return 2\n"),
    lambda value: value["results"][0].update(source_hash="wrong"),
    lambda value: value["results"][0].update(content_hash="wrong"),
    lambda value: value["results"][0].update(related_to="other-owner"),
    lambda value: value["results"][0].update(source_origin="buffer"),
    lambda value: value["results"][0].update(retrieval_score=0.4),
    lambda value: value["token_usage"].update(tokens=41),
    lambda value: value["_selection_backfill"].update(appended=["other"]),
    lambda value: value.update(candidate_windows=2),
    lambda value: value["match_evidence"].update(policy="changed"),
])
def test_source_provenance_scalars_tokens_private_debug_and_other_public_tampering_fails(mutation):
    saved = expected()
    actual = deepcopy(saved["response"])
    mutation(actual)
    with pytest.raises(ValueError, match="full source"):
        verify.check_response(saved, actual, saved["query_vector_sha256"], 1, 0)


@pytest.mark.parametrize("queries,documents", [(0, 0), (2, 0), (True, 0), (1, 1), (1, False)])
def test_exact_typed_actual_call_counts(queries, documents):
    saved = expected()
    with pytest.raises(ValueError, match="exactly one"):
        verify.check_response(saved, saved["response"], saved["query_vector_sha256"], queries, documents)


def test_query_vector_and_exact_serialized_payload_tampering_fails():
    saved = expected()
    with pytest.raises(ValueError, match="query vector"):
        verify.check_response(saved, saved["response"], "wrong", 1, 0)
    saved["compact_payload"] += " "
    saved["compact_sha256"] = verify.sha(saved["compact_payload"].encode())
    with pytest.raises(ValueError, match="exact compact"):
        verify.check_response(saved, saved["response"], saved["query_vector_sha256"], 1, 0)


def test_contained_original_dependency_owner_pointer_is_preserved_without_id_membership_requirement():
    value = response()
    owner = value["results"][0]
    owner["id"] = "a.py:run:1-4"
    owner["end_line"] = owner["window_end_line"] = 4
    owner["source"] += "    helper()\n    return 2\n"
    owner["source_hash"] = verify.sha(owner["source"].encode())
    dependency = deepcopy(owner)
    dependency.update(id="b.py:helper:1-2", path="b.py", symbol="helper", start_line=1, end_line=2,
                      origin="dependency", related_to="a.py:run:1-2", source="def helper():\n    pass\n")
    dependency["source_hash"] = dependency["content_hash"] = verify.sha(dependency["source"].encode())
    value["results"].append(dependency)
    assert dependency["related_to"] not in {item["id"] for item in value["results"]}
    assert owner["source"].startswith(response()["results"][0]["source"])
    saved = expected(value)
    view, _ = verify.check_response(saved, deepcopy(value), saved["query_vector_sha256"], 1, 0)
    assert view["results"][1]["related_to"] == "a.py:run:1-2"
    assert view["results"][1]["content_hash"] == dependency["content_hash"]


def cache(path, embedding=None):
    item = response()["results"][0]
    inventory = [{key: item[key] for key in ("id", "path", "symbol", "start_line", "end_line", "content_hash")}]
    inventory[0]["text"] = item["source"]
    embedding = {"id": "invented-model"} if embedding is None else embedding
    with sqlite3.connect(path) as connection:
        connection.executescript("PRAGMA user_version=2; CREATE TABLE snippets(id,path,symbol,start_line,end_line,text,content_hash,position); CREATE TABLE metadata(key,value);")
        connection.execute("INSERT INTO snippets VALUES (?,?,?,?,?,?,?,0)", tuple(inventory[0][key] for key in
                           ("id", "path", "symbol", "start_line", "end_line", "text", "content_hash")))
        connection.execute("INSERT INTO metadata VALUES ('active',?)", (json.dumps({"embedding": embedding}),))
    return inventory, embedding


def test_clean_cache_copy_preserves_original_and_can_be_modified_independently(tmp_path):
    original, copy = tmp_path / "source.sqlite3", tmp_path / "copy.sqlite3"
    inventory, embedding = cache(original)
    before = verify.file_sha(original)
    receipt = verify.snapshot_cache(original, copy, inventory, embedding)
    assert receipt["readonly_probe_closed_before_copy"] is True
    assert receipt["source_windows"] == 1
    assert verify.file_sha(copy) == verify.file_sha(original) == before
    with sqlite3.connect(copy) as connection:
        connection.execute("UPDATE snippets SET text='changed'")
    assert verify.file_sha(original) == before != verify.file_sha(copy)
    with pytest.raises(FileExistsError):
        verify.snapshot_cache(original, copy, inventory, embedding)


@pytest.mark.parametrize("change", ["inventory", "embedding", "wal", "same_path", "schema"])
def test_incompatible_or_open_cache_is_rejected_before_copy(tmp_path, change):
    original, copy = tmp_path / "source.sqlite3", tmp_path / "copy.sqlite3"
    inventory, embedding = cache(original)
    if change == "inventory":
        inventory[0]["text"] += "tamper"
    elif change == "embedding":
        embedding = {"id": "different"}
    elif change == "wal":
        Path(str(original) + "-wal").write_bytes(b"uncheckpointed")
    elif change == "same_path":
        copy = original
    elif change == "schema":
        with sqlite3.connect(original) as connection:
            connection.execute("PRAGMA user_version=1")
    with pytest.raises(ValueError):
        verify.snapshot_cache(original, copy, inventory, embedding)
    if change != "same_path":
        assert not copy.exists()


def historical_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(verify, "ROOT", tmp_path)
    study = tmp_path / "saved-study"
    study.mkdir()
    plan = {"binding": {"settings": verify.SETTINGS, "implementation_sha256": {"old.py": verify.sha(b"old frozen source")}}}
    verify.write_new(study / "plan.json", plan)
    start = {"execution_commit": "old-execution", "prospective_freeze": {"commit": "old-freeze"},
             "plan_sha256": verify.file_sha(study / "plan.json")}
    verify.write_new(study / "run-started.json", start)
    rows = []
    for query in range(14):
        for mode in ("dense", "hybrid"):
            rows.append({**expected(), "arm": "coverage_control", "repetition": 1,
                         "query_id": str(query), "retrieval": mode})
    rows += [{"arm": "candidate", "repetition": 2}] * (252 - len(rows))
    with gzip.open(study / "searches.jsonl.gz", "wt") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")
    verify.write_new(study / "summary.json", {"complete": True, "plan_sha256": start["plan_sha256"],
                     "raw_sha256": verify.file_sha(study / "searches.jsonl.gz")})
    artifacts = {name: verify.file_sha(study / name) for name in ("plan.json", "run-started.json", "summary.json", "searches.jsonl.gz")}
    verify.write_new(study / "independent-audit.json", {"status": "passed", "checks_passed": True,
        "study": "source_flow_native", "verified_requests": 252, "candidate_planner_or_producer_modules_imported": False,
        "producer_execution": start["execution_commit"], "producer_freeze": start["prospective_freeze"], "artifact_sha256": artifacts})
    committed = {("HEAD", "saved-study/" + path.name): path.read_bytes() for path in study.iterdir()}
    committed[("old-execution", "old.py")] = b"old frozen source"
    monkeypatch.setattr(verify, "git_bytes", lambda commit, path: committed[(commit, path)])
    return study, committed


def test_historical_artifact_and_execution_blobs_are_bound_without_reexecuting_producer(tmp_path, monkeypatch):
    study, committed = historical_fixture(tmp_path, monkeypatch)
    _, _, rows, hashes = verify.historical_expected(study)
    assert len(rows) == 28 and len(hashes) == 5
    assert rows[0]["reference"]["line_number"] == 1
    committed[("old-execution", "old.py")] += b"tamper"
    with pytest.raises(ValueError, match="Historical producer source blob"):
        verify.historical_expected(study)


@pytest.mark.parametrize("change", ["raw", "audit", "current_blob"])
def test_historical_tampering_is_rejected(tmp_path, monkeypatch, change):
    study, committed = historical_fixture(tmp_path, monkeypatch)
    if change == "raw":
        with (study / "searches.jsonl.gz").open("ab") as stream:
            stream.write(b"tamper")
    elif change == "audit":
        value = verify.read(study / "independent-audit.json")
        value["candidate_planner_or_producer_modules_imported"] = True
        (study / "independent-audit.json").write_text(json.dumps(value))
    else:
        committed[("HEAD", "saved-study/summary.json")] += b"tamper"
    with pytest.raises(ValueError):
        verify.historical_expected(study)


def test_current_product_and_verifier_source_must_be_committed(tmp_path, monkeypatch):
    monkeypatch.setattr(verify, "ROOT", tmp_path)
    for name in ("scripts/verify_default_context.py", "tests/test_default_context_verification.py", "src/semantic_search/backend.py"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# invented source\n")
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], stderr=subprocess.DEVNULL)
    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "invented")
    assert len(verify.current_bindings()) == 3
    (tmp_path / "src/semantic_search/backend.py").write_text("# changed default\n")
    with pytest.raises(ValueError, match="Commit actual product"):
        verify.current_bindings()


def fake_run(tmp_path, monkeypatch, *, failure=None):
    # run() deliberately sets its tokenizer path. Register the original value
    # with pytest so this invented cache cannot leak into unrelated tests.
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", os.environ.get("TIKTOKEN_CACHE_DIR", str(ROOT / ".cache/tokenizers")))
    monkeypatch.setattr(verify, "ROOT", tmp_path)
    corpus, model, tokenizer, study = (tmp_path / name for name in ("corpus", "model", "tokenizer", "study"))
    for folder in (corpus, model, tokenizer, study):
        folder.mkdir()
    (corpus / "a.py").write_text("def run():\n    return 1\n")
    (model / "config.json").write_text("{}")
    (tokenizer / verify.TOKENIZER_KEY).write_bytes(b"invented-tokenizer")
    monkeypatch.setattr(verify, "TOKENIZER_SHA", verify.sha(b"invented-tokenizer"))
    source_db = tmp_path / "native.sqlite3"
    inventory, embedding = cache(source_db)
    verify.write_new(study / "source-inventory.json", {"inventory": inventory})
    verify.write_new(study / "run-started.json", {"execution_commit": "historical-execution"})
    data = {"source_root": ".", "source_exclusions": ["excluded/**"],
            "queries": [{"id": str(index), "query": "query " + str(index)} for index in range(14)]}
    verify.write_new(tmp_path / "dataset.json", data)
    rows = []
    for query in data["queries"]:
        for mode in ("dense", "hybrid"):
            value = response()
            value["query"] = query["query"]
            rows.append({"row": {**expected(value), "query_id": query["id"], "retrieval": mode},
                         "reference": {"line_number": len(rows) + 1, "record_sha256": "old-row"}})
    plan = {"binding": {"dataset_path": "dataset.json", "dataset_sha256": verify.file_sha(tmp_path / "dataset.json"),
        "revision": "corpus-revision", "source_sha256": {"a.py": verify.file_sha(corpus / "a.py")},
        "model_files_sha256": {"config.json": verify.file_sha(model / "config.json")}, "embedding": embedding}}
    summary = {"setup": {"status": {"db_path": str(source_db)}}}
    monkeypatch.setattr(verify, "current_bindings", lambda: {"backend.py": "new-default-source"})
    monkeypatch.setattr(verify, "historical_expected", lambda path: (plan, summary, rows, {"raw": "old-ledger-hash"}))
    def command(args, **kwargs):
        if args[-2:] == ["rev-parse", "HEAD"]:
            return "corpus-revision\n" if args[2] == str(corpus) else "new-product-commit\n"
        if args[-2:] == ["status", "--porcelain"]:
            return ""
        raise AssertionError(args)
    monkeypatch.setattr(verify.subprocess, "check_output", command)
    hooks = []
    monkeypatch.setattr(verify, "assert_default_hooks", lambda: hooks.append("actual-default-check"))
    class Vector:
        def tobytes(self):
            return b"invented-query-vector"
    class Embedder:
        query_calls = document_calls = 0
        metadata = embedding
        last_query = Vector()
    embedder = Embedder()
    monkeypatch.setattr(verify, "make_embedder", lambda path: embedder)
    class Backend:
        closed = False
        def search(self, query, *, retrieval, **settings):
            assert settings == verify.SETTINGS
            saved = rows[embedder.query_calls]["row"]
            assert query == saved["response"]["query"] and retrieval == saved["retrieval"]
            embedder.query_calls += 1
            if failure == "runtime" and embedder.query_calls == 2:
                raise RuntimeError("invented request interruption")
            value = deepcopy(saved["response"])
            value["timings"] = {"different": 99}
            value["indexstats"] = {"memory_cache_hit": True}
            if failure == "mismatch" and embedder.query_calls == 2:
                value["results"][0]["source_hash"] = "returned-mismatch"
            return value
        def close(self):
            self.closed = True
    backend = Backend()
    def factory(actual_corpus, actual_data, copied_db, actual_embedder):
        assert actual_corpus == corpus and actual_data == data and actual_embedder is embedder
        assert copied_db != source_db and copied_db.read_bytes() == source_db.read_bytes()
        return backend
    monkeypatch.setattr(verify, "make_backend", factory)
    args = Namespace(expected_study=study, corpus=corpus, model=model, cache_db=source_db,
                     output=tmp_path / "posthoc-parity", tokenizer_cache=tokenizer)
    return args, embedder, backend, hooks


def test_complete_posthoc_pipeline_streams_28_verified_rows_on_a_separate_cache(tmp_path, monkeypatch):
    args, embedder, backend, hooks = fake_run(tmp_path, monkeypatch)
    source_hash = verify.file_sha(args.cache_db)
    assert verify.run(args)["requests"] == 28
    summary = verify.read(args.output / "summary.json")
    assert summary["query_embedding_calls"] == 28 and summary["document_embedding_calls"] == 0
    assert summary["selector_patches"] == 0 and summary["comparative_latency_measured"] is False
    assert summary["historical_execution_commit"] == "historical-execution"
    assert "not fresh quality" in summary["classification"]
    assert summary["source_cache"]["source_db_sha256_after"] == source_hash == verify.file_sha(args.cache_db)
    with gzip.open(args.output / "responses.jsonl.gz", "rt") as stream:
        rows = [json.loads(line) for line in stream]
    assert len(rows) == len(hooks) - 1 == 28
    assert all(row["query_embedding_calls"] == 1 and row["document_embedding_calls"] == 0 for row in rows)
    assert all("timings" not in row["response"] and "indexstats" not in row["response"] for row in rows)
    assert all("_selection_backfill" in row["response"] for row in rows)
    assert backend.closed and embedder.document_calls == 0
    before = (args.output / "responses.jsonl.gz").read_bytes()
    with pytest.raises(ValueError, match="never overwrite or resume"):
        verify.run(args)
    assert (args.output / "responses.jsonl.gz").read_bytes() == before


@pytest.mark.parametrize("failure", ["runtime", "mismatch"])
def test_interrupted_or_mismatched_request_preserves_exact_verified_prefix_and_failure(tmp_path, monkeypatch, failure):
    args, embedder, backend, _ = fake_run(tmp_path, monkeypatch, failure=failure)
    with pytest.raises((ValueError, RuntimeError)):
        verify.run(args)
    with gzip.open(args.output / "responses.jsonl.gz", "rt") as stream:
        rows = [json.loads(line) for line in stream]
    assert len(rows) == 1 and rows[0]["query_id"] == "0" and rows[0]["retrieval"] == "dense"
    receipt = verify.read(args.output / "failure.json")
    assert receipt["progress"]["attempted_requests"] == 2 and receipt["progress"]["completed_requests"] == 1
    assert receipt["actual_query_embedding_calls"] == 2 and receipt["actual_document_embedding_calls"] == 0
    assert receipt["resume_allowed"] is False and not (args.output / "summary.json").exists()
    assert receipt["source_db_sha256_before"] == receipt["source_db_sha256_after"]
    if failure == "mismatch":
        assert receipt["failed_request"]["response"]["results"][0]["source_hash"] == "returned-mismatch"
    else:
        assert receipt["failed_request"] is None
    assert backend.closed and embedder.query_calls == 2


def test_recorded_embedder_refuses_any_document_inference_and_counts_actual_super_query(monkeypatch, tmp_path):
    from semantic_search.embeddings import QwenEmbedder
    called = []
    class Vector:
        def copy(self):
            return self
    def fake_query(self, query):
        called.append(query)
        return Vector()
    monkeypatch.setattr(QwenEmbedder, "embed_query", fake_query)
    embedder = verify.make_embedder(tmp_path)
    assert embedder._model is None
    embedder.embed_query("invented")
    assert called == ["invented"] and embedder.query_calls == 1
    with pytest.raises(RuntimeError, match="refuses new document encoding"):
        embedder.embed_documents(["must not infer"])
    assert embedder.document_calls == 1 and embedder._model is None


def test_real_default_hook_identity_check_rejects_selector_patching(monkeypatch):
    from semantic_search import backend, selection
    from semantic_search.context_backfill import coalesce_context
    monkeypatch.setattr(backend, "select_context", selection.definition_context)
    monkeypatch.setattr(backend, "postprocess_context", coalesce_context)
    verify.assert_default_hooks()
    monkeypatch.setattr(backend, "select_context", lambda *args, **kwargs: None)
    with pytest.raises(ValueError, match="no selector patch"):
        verify.assert_default_hooks()


def test_help_imports_neither_model_backend_nor_historical_native_producer():
    code = "import runpy,sys;sys.argv=['verify_default_context.py','--help'];\ntry:runpy.run_path('scripts/verify_default_context.py',run_name='__main__')\nexcept SystemExit as e:assert e.code==0\nassert not any(n in sys.modules for n in ('semantic_search.backend','semantic_search.embeddings','benchmark_flow_allocation','mlx','mlx_embeddings'))"
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "--expected-study" in result.stdout and "--cache-db" in result.stdout
