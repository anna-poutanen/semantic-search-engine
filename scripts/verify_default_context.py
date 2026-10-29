#!/usr/bin/env python3
"""Post hoc integration parity of actual defaults against saved coverage results.

This is not a fresh quality or comparative latency experiment. It never patches
selectors or invokes a historical producer, and refuses new document encoding.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
VIEW_FIELDS = ("query", "scope", "match_status", "results", "total_source_chars", "budget_exhausted", "token_usage")
OPERATIONAL_FIELDS = ("timings", "indexstats")
SETTINGS = {"max_results": 8, "source_budget": 12000, "token_budget": 4096,
            "include_dependencies": True, "rerank": False, "candidates": 50}
TOKENIZER_KEY = "9b5ad71b2ce5302211f9c61530b329a4922fc6a4"
TOKENIZER_SHA = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
CLASSIFICATION = "Post hoc actual-default integration parity on observed data; not fresh quality or comparative latency validation"


def require(value, message):
    if not value:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def write_new(path, value):
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def source_view(response):
    require(all(name in response for name in VIEW_FIELDS), "Actual coverage response lacks required public source fields")
    # Bind every public field and private backfill key presence. Only operational
    # measurements may differ when the actual product executes again.
    return {name: value for name, value in response.items() if name not in OPERATIONAL_FIELDS}


def git_bytes(commit, path):
    return subprocess.check_output(["git", "-C", str(ROOT), "show", commit + ":" + path])


def current_bindings():
    names = ["scripts/verify_default_context.py", "tests/test_default_context_verification.py",
             *(path.relative_to(ROOT).as_posix() for path in sorted((ROOT / "src/semantic_search").glob("*.py")))]
    result = {name: file_sha(ROOT / name) for name in names}
    for name, digest in result.items():
        require(sha(git_bytes("HEAD", name)) == digest, "Commit actual product defaults and parity source/tests before running: " + name)
    require(not subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain", "--", *names], text=True).strip(),
            "Bound product/parity source has staged or unstaged edits")
    return result


def historical_expected(study):
    plan, start, audit, summary = (read(study / name) for name in
                                  ("plan.json", "run-started.json", "independent-audit.json", "summary.json"))
    require(audit.get("status") == "passed" and audit.get("checks_passed") is True
            and audit.get("study") == "source_flow_native" and audit.get("verified_requests") == 252
            and audit.get("candidate_planner_or_producer_modules_imported") is False,
            "Actual completed native study needs its successful independent audit")
    require(audit["producer_execution"] == start["execution_commit"]
            and audit["producer_freeze"] == start["prospective_freeze"]
            and plan["binding"]["settings"] == SETTINGS and summary["complete"] is True,
            "Historical native execution/settings identity differs")
    paths = {name: file_sha(study / name) for name in (*audit["artifact_sha256"], "independent-audit.json")}
    for name, digest in audit["artifact_sha256"].items():
        require(paths[name] == digest, "Historical audited artifact changed: " + name)
    require(paths["plan.json"] == start["plan_sha256"] == summary["plan_sha256"]
            and paths["searches.jsonl.gz"] == summary["raw_sha256"], "Historical raw/plan hash differs")
    relative = study.relative_to(ROOT).as_posix()
    for name, digest in paths.items():
        require(sha(git_bytes("HEAD", relative + "/" + name)) == digest,
                "Commit unchanged historical raw/audit artifacts before product parity: " + name)
    # Old source is immutable Git data. Actual new defaults may intentionally
    # differ; never attribute the old experiment to current source.
    for name, digest in plan["binding"]["implementation_sha256"].items():
        require(sha(git_bytes(start["execution_commit"], name)) == digest,
                "Historical producer source blob differs: " + name)
    expected, total = [], 0
    with gzip.open(study / "searches.jsonl.gz", "rt", encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            row = json.loads(line)
            total += 1
            if row["arm"] != "coverage_control" or row["repetition"] != 1:
                continue
            require(row["retrieval"] in ("dense", "hybrid"), "Unexpected historical retrieval mode")
            source_view(row["response"])
            require(sha(row["compact_payload"].encode()) == row["compact_sha256"], "Historical compact bytes/hash differ")
            expected.append({"row": row, "reference": {"line_number": number,
                "record_sha256": sha(canonical(row)), "compact_sha256": row["compact_sha256"]}})
    require(total == 252 and len(expected) == 28 and len({(x["row"]["query_id"], x["row"]["retrieval"]) for x in expected}) == 28,
            "Exactly28 first-repetition observed coverage rows are required")
    return plan, summary, expected, {relative + "/" + name: digest for name, digest in paths.items()}


def snapshot_cache(source, destination, inventory, embedding):
    """Close an immutable read-only probe before copying a clean original DB."""
    require(source.is_file() and source.resolve() != destination.resolve(), "Separate existing native cache DB required")
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(source) + suffix)
        require(not sidecar.exists() or sidecar.stat().st_size == 0,
                "Close native cache writers and checkpoint cleanly before copying; nonempty " + suffix)
    digest = file_sha(source)
    uri = "file:" + quote(str(source.resolve()), safe="/") + "?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        require(connection.execute("PRAGMA user_version").fetchone()[0] == 2, "Original native cache schema differs")
        rows = connection.execute("SELECT id,path,symbol,start_line,end_line,text,content_hash FROM snippets ORDER BY position").fetchall()
        fields = ("id", "path", "symbol", "start_line", "end_line", "text", "content_hash")
        require(canonical([dict(zip(fields, row)) for row in rows]) == canonical(inventory), "Native cache source-window inventory differs")
        active = connection.execute("SELECT value FROM metadata WHERE key='active'").fetchone()
        require(active is not None and canonical(json.loads(active[0])["embedding"]) == canonical(embedding),
                "Native cache embedding fingerprint/metadata differs")
    with source.open("rb") as original, destination.open("xb") as copy:
        for block in iter(lambda: original.read(1024 * 1024), b""):
            copy.write(block)
    require(file_sha(source) == file_sha(destination) == digest, "Original cache changed during clean copy")
    return {"source_db_sha256_before": digest, "copied_db_sha256_before": digest,
            "readonly_probe_closed_before_copy": True, "source_windows": len(inventory)}


def assert_default_hooks():
    from semantic_search import backend, selection
    from semantic_search.context_backfill import coalesce_context
    require(backend.select_context is selection.definition_context and backend.postprocess_context is coalesce_context,
            "Actual backend defaults must be definition_context + unchanged coalesce_context; no selector patch is allowed")


def make_embedder(model):
    from semantic_search.embeddings import QwenEmbedder
    class Recorded(QwenEmbedder):
        query_calls = document_calls = 0
        def embed_query(self, query):
            value = super().embed_query(query)
            self.query_calls += 1
            self.last_query = value.copy()
            return value
        def embed_documents(self, *args, **kwargs):
            self.document_calls += 1
            raise RuntimeError("Post hoc cache parity refuses new document encoding; copied native cache must be compatible")
    return Recorded(model, progress=False)


def make_backend(corpus, data, cache, embedder):
    from semantic_search.backend import SearchBackend
    return SearchBackend(corpus, data["source_root"], cache, embedder=embedder, source_exclusions=data.get("source_exclusions", ()))


def check_response(expected, response, vector_hash, query_calls, document_calls):
    from semantic_search import context
    require(type(query_calls) is int and query_calls == 1 and type(document_calls) is int and document_calls == 0,
            "Each parity request requires exactly one actual query embedding and zero document work")
    require(vector_hash == expected["query_vector_sha256"], "Actual query vector differs from historical first-repetition vector")
    view = source_view(response)
    require(canonical(view) == canonical(source_view(expected["response"])), "Actual default full source/provenance/backfill view differs")
    text = context.serialize_context(context.compact_result(response))
    require(text == expected["compact_payload"] and sha(text.encode()) == expected["compact_sha256"],
            "Actual default exact compact payload differs")
    return view, text


def run(args):
    require(not args.output.exists(), "Use a new parity output directory; never overwrite or resume")
    implementation = current_bindings()
    plan, summary, expected, historical = historical_expected(args.expected_study)
    binding = plan["binding"]
    require(args.cache_db.resolve() == Path(summary["setup"]["status"]["db_path"]).resolve(), "Cache DB must be the actual native study's closed cache")
    data = read(ROOT / binding["dataset_path"])
    require(file_sha(ROOT / binding["dataset_path"]) == binding["dataset_sha256"], "Historical query dataset changed")
    require(subprocess.check_output(["git", "-C", str(args.corpus), "rev-parse", "HEAD"], text=True).strip() == binding["revision"]
            and not subprocess.check_output(["git", "-C", str(args.corpus), "status", "--porcelain"], text=True).strip(), "Pinned observed corpus must remain clean")
    for path, digest in binding["source_sha256"].items():
        require(file_sha(args.corpus / path) == digest, "Pinned observed source changed: " + path)
    actual_model = {path.relative_to(args.model).as_posix(): file_sha(path) for path in sorted(args.model.rglob("*"))
                    if path.is_file() and ".cache" not in path.relative_to(args.model).parts}
    require(actual_model == binding["model_files_sha256"], "Original pinned checkpoint/config/tokenizer/provenance files changed")
    require(file_sha(args.tokenizer_cache / TOKENIZER_KEY) == TOKENIZER_SHA, "Exact cached cl100k tokenizer required")
    args.output.mkdir(parents=True)
    execution = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    write_new(args.output / "run-start.json", {"classification": CLASSIFICATION, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "execution_commit": execution, "implementation_sha256": implementation, "historical_artifact_sha256": historical,
        "historical_execution_commit": read(args.expected_study / "run-started.json")["execution_commit"],
        "expected_requests": 28, "selector_patches": 0, "resume_allowed": False, "comparative_latency_measured": False})
    state = {"phase": "cache_copy", "attempted_requests": 0, "completed_requests": 0}
    original_db_hash = None
    embedder = failed_request = None
    try:
        original_db_hash = file_sha(args.cache_db)
        inventory = read(args.expected_study / "source-inventory.json")
        cache_receipt = snapshot_cache(args.cache_db, args.output / "index.sqlite3", inventory["inventory"], binding["embedding"])
        os.environ["TIKTOKEN_CACHE_DIR"] = str(args.tokenizer_cache)
        assert_default_hooks()
        embedder = make_embedder(args.model)
        require(canonical(embedder.metadata) == canonical(binding["embedding"]), "Actual model/runtime metadata no longer matches the saved cache")
        queries = {query["id"]: query["query"] for query in data["queries"]}
        state["phase"] = "actual_default_requests"
        with closing(make_backend(args.corpus, data, args.output / "index.sqlite3", embedder)) as backend:
            with (args.output / "responses.jsonl.gz").open("xb") as raw:
                with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as stream:
                    for saved in expected:
                        row = saved["row"]
                        failed_request = None
                        state.update(query_id=row["query_id"], retrieval=row["retrieval"])
                        state["attempted_requests"] += 1
                        before, documents = embedder.query_calls, embedder.document_calls
                        assert_default_hooks()
                        response = backend.search(queries[row["query_id"]], retrieval=row["retrieval"], **SETTINGS)
                        vector_hash = sha(embedder.last_query.tobytes())
                        # Preserve a returned mismatch even when strict validation
                        # fails before it can enter the verified response ledger.
                        failed_request = {"query_id": row["query_id"], "retrieval": row["retrieval"],
                            "historical_reference": saved["reference"], "query_vector_sha256": vector_hash,
                            "query_embedding_calls": embedder.query_calls - before,
                            "document_embedding_calls": embedder.document_calls - documents,
                            "response": {name: value for name, value in response.items() if name not in OPERATIONAL_FIELDS}}
                        view, text = check_response(row, response, vector_hash, embedder.query_calls - before, embedder.document_calls - documents)
                        value = {"query_id": row["query_id"], "retrieval": row["retrieval"], "historical_reference": saved["reference"],
                            "query_embedding_calls": embedder.query_calls - before, "document_embedding_calls": embedder.document_calls - documents,
                            "query_vector_sha256": vector_hash, "response": view, "compact_payload": text,
                            "compact_sha256": sha(text.encode()), "full_source_view_sha256": sha(canonical(view)),
                            "operational_fields_excluded": list(OPERATIONAL_FIELDS)}
                        stream.write((json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode())
                        stream.flush()
                        raw.flush()
                        state["completed_requests"] += 1
                        failed_request = None
        require(state["completed_requests"] == embedder.query_calls == 28 and embedder.document_calls == 0,
                "Exactly28 actual query embeddings and zero document encoding calls required")
        require(file_sha(args.cache_db) == original_db_hash, "Original native source DB changed during parity")
        require(current_bindings() == implementation, "Product/parity implementation changed during integration")
        write_new(args.output / "summary.json", {"classification": CLASSIFICATION, "status": "passed", "execution_commit": execution,
            "requests": 28, "unique_observed_queries": 14, "query_embedding_calls": embedder.query_calls,
            "document_embedding_calls": embedder.document_calls, "selector_patches": 0,
            "exact_compact_parity_rows": 28, "exact_full_source_provenance_backfill_rows": 28, "exact_query_vector_hash_rows": 28,
            "historical_execution_commit": read(args.expected_study / "run-started.json")["execution_commit"],
            "implementation_sha256": implementation, "historical_artifact_sha256": historical,
            "source_cache": {**cache_receipt, "source_db_sha256_after": file_sha(args.cache_db),
                "copied_db_sha256_after": file_sha(args.output / "index.sqlite3")},
            "responses_sha256": file_sha(args.output / "responses.jsonl.gz"), "comparative_latency_measured": False,
            "limits": "Observed-data integration parity only. Failed shared-flow quality/latency gates remain unchanged; no new generalization, coding correctness, billing or download claim."})
    except BaseException as error:
        write_new(args.output / "failure.json", {"classification": CLASSIFICATION, "error_type": type(error).__name__, "error": str(error),
            "progress": state, "resume_allowed": False, "source_db_sha256_before": original_db_hash,
            "source_db_sha256_after": file_sha(args.cache_db) if args.cache_db.exists() else None,
            "actual_query_embedding_calls": getattr(embedder, "query_calls", 0),
            "actual_document_embedding_calls": getattr(embedder, "document_calls", 0),
            "failed_request": failed_request})
        raise
    return {"status": "passed", "requests": 28, "classification": CLASSIFICATION}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("expected-study", "corpus", "model", "cache-db", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--tokenizer-cache", type=Path, default=ROOT / ".cache/tokenizers")
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    print(json.dumps(run(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
