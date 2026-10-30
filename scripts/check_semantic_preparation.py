#!/usr/bin/env python3
"""Exercise the opt-in semantic hook using only invented CPU fixture vectors."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import importlib.abc
import json
from pathlib import Path
import socket
import sys
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
QUERY = "load alpha payload and render beta template"
LIMITS = {"max_results": 8, "source_budget": 12000, "token_budget": 4096}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def invented_rows():
    rows = []
    for number in range(10):
        source = f"def invented_{number}():\n    return {number}\n"
        rows.append({"id": f"invented-{number}", "path": f"src/invented_{number}.py",
            "symbol": f"invented_{number}", "start_line": 1, "end_line": 2,
            "window_end_line": 2, "source": source, "content_hash": sha(source.encode()),
            "source_hash": sha(source.encode()), "truncated": False,
            "retrieval_score": 1.0 / (number + 1), "h2o_score": None, "qwen_score": None,
            "origin": "retrieval", "related_to": None, "source_origin": "saved"})
    whole, part = "line_a()\nline_b()\nline_c()\n", "line_b()\n"
    rows[5].update(path="src/shared.py", source=whole, end_line=3, window_end_line=3,
                   content_hash=sha(whole.encode()), source_hash=sha(whole.encode()))
    rows[6].update(path="src/shared.py", source=part, start_line=2, end_line=2, window_end_line=2,
                   content_hash=sha(part.encode()), source_hash=sha(part.encode()))
    return rows


def check(cache):
    attempts = {"forbidden_imports": 0, "network_calls": 0}

    class DenyModels(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            blocked = ("numpy", "mlx", "mlx_embeddings", "torch", "transformers",
                       "semantic_search.embeddings", "semantic_search.index",
                       "semantic_search.store", "semantic_search.backend")
            if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
                attempts["forbidden_imports"] += 1
                raise RuntimeError("CPU readiness forbids import: " + fullname)

    def deny_network(*args, **kwargs):
        attempts["network_calls"] += 1
        raise RuntimeError("CPU readiness forbids network access")

    guard = DenyModels()
    sys.meta_path.insert(0, guard)
    sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
    try:
        with patch.object(socket.socket, "connect", deny_network), \
             patch.object(socket, "create_connection", deny_network), \
             patch.object(socket, "getaddrinfo", deny_network):
            from audit_selection import offline_encoding, TOKENIZER_SHA
            import audit_semantic_context as auditor
            from semantic_search import context
            import semantic_search.semantic_context as selector
            import semantic_search.semantic_context_adapter as adapter
            from semantic_search.semantic_features import (
                make_features, MODEL_ID, MODEL_REVISION, QUERY_PROMPT,
            )

            counter = offline_encoding(cache)
            rows = invented_rows()
            response = {"query": QUERY, "match_status": "candidates", "results": deepcopy(rows[:8]),
                "total_source_chars": sum(len(row["source"]) for row in rows[:8]),
                "budget_exhausted": False, "token_usage": {"encoding": "cl100k_base", "tokens": 0,
                    "budget": LIMITS["token_budget"], "truncated": False}}
            calls, captures = [], []

            def unit(axis):
                value = [0.0] * 1024
                value[axis] = 1.0
                return value

            def features(reference, raw, *, cancel_check):
                calls.append("invented_feature_provider")
                cancel_check()
                vectors = {row["id"]: unit(1 if row["id"] == "invented-8" else
                    2 if row["id"] == "invented-9" else 0) for row in raw}
                return make_features(QUERY, raw, reference, [unit(i) for i in range(3)], vectors,
                    {"id": MODEL_ID, "revision": MODEL_REVISION, "dimensions": 1024,
                     "query_prompt": QUERY_PROMPT, "normalization": "L2, float32",
                     "synthetic_fixture": "Invented unit vectors; NOT real model outputs"})

            with patch.object(context, "encoding", return_value=counter), \
                 patch.object(adapter, "coalesce_context", wraps=adapter.coalesce_context) as coverage, \
                 patch.object(selector, "semantic_final_context", wraps=selector.semantic_final_context) as semantic:
                context.count_context(response)
                result = adapter.make_semantic_postprocessor(features, capture=captures.append)(
                    response, rows[:8], ranked_candidates=rows, definition_keys={}, **LIMITS)
                assert coverage.call_count == semantic.call_count == len(calls) == len(captures) == 1
            record = deepcopy(captures[0])
            payload = context.serialize_context(context.compact_result(result))
            record.update(compact_payload=payload, compact_sha256=sha(payload.encode()),
                actual_compact_tokens=len(counter.encode(payload, disallowed_special=())),
                subsequent_backfill_calls=0)
            verified = auditor.verify_record(record, counter, reference=record["reference_response"],
                raw_rows=rows, features=record["features"], definition_keys={}, precoalesce_rows=response["results"])
            assert verified == result and record["semantic_receipt"]["accepted"]
            admissions = record["semantic_receipt"]["exchanges"]
            assert len(admissions) == 2 and admissions[0]["victim_id"] is None
            assert admissions[1]["victim_id"] == "invented-7"
            assert not any(attempts.values())
            files = {"src/semantic_search/embeddings.py", "src/semantic_search/semantic_features.py",
                "src/semantic_search/semantic_context.py", "src/semantic_search/semantic_context_adapter.py",
                "scripts/audit_semantic_context.py", "scripts/check_semantic_preparation.py",
                "tests/test_embedding_queries.py", "tests/test_semantic_features.py",
                "tests/test_semantic_context.py", "tests/test_semantic_context_adapter.py",
                "tests/test_semantic_context_audit.py"}
            # Bind transitive helpers actually imported in this process as well
            # as the explicit component/test files. This includes the source
            # datamodel, clause detector, packer, tokenizer loader and auditor.
            imported_local = set()
            for module in tuple(sys.modules.values()):
                filename = getattr(module, "__file__", None)
                if filename is not None:
                    path = Path(filename).resolve()
                    if path.suffix == ".py" and path.is_relative_to(ROOT):
                        imported_local.add(path.relative_to(ROOT).as_posix())
            files.update(imported_local)
            sources = {name: sha((ROOT / name).read_bytes()) for name in sorted(files)}
            audit = {"passed": True, "auditor_source_sha256": sources["scripts/audit_semantic_context.py"],
                     "verified_response_sha256": sha(canonical(verified))}
            def projection(value):
                return [{key: row[key] for key in ("id", "retrieval_score", "h2o_score", "qwen_score")}
                        for row in value["results"]]
            return {"classification": "invented CPU readiness only; not quality/native performance",
                "passed": True, "fixture": "10 invented full source rows; 3 invented 1024-dimensional unit-vector facets",
                "source_reviewed_activity_claims": {
                    "classification": "Source-reviewed claims; not measured call guards or filesystem enforcement",
                    "basis": "This script constructs its fixture and vectors directly; no corpus or model provider is invoked",
                    "real_model_calls": 0, "real_codebase_index_calls": 0, "real_retrieval_calls": 0,
                    "observed_candidate_outcomes": 0, "reserved_corpus_reads": 0},
                "actual_coverage_coalesce_calls": coverage.call_count, "actual_feature_calls": len(calls),
                "actual_semantic_calls": semantic.call_count, "subsequent_backfill_calls": 0,
                "guards": attempts,
                "guard_scope": "Forbidden module-import and network-call attempts only; no generic filesystem enforcement",
                "actual_call_count_scope": "CPU coalescer, invented feature provider and terminal selector executions only",
                "tokenizer_ranks_sha256": TOKENIZER_SHA, "limits": LIMITS,
                "actual_compact_tokens": record["actual_compact_tokens"],
                "original_rows": projection(response), "coverage_rows": projection(record["reference_response"]),
                "final_rows": projection(result), "admissions": admissions,
                "feature_sha256": sha(canonical(record["features"])), "response_sha256": sha(canonical(result)),
                "compact_sha256": record["compact_sha256"], "independent_audit": audit,
                "audit_sha256": sha(canonical(audit)), "source_sha256": sources,
                "imported_local_source_paths": sorted(imported_local),
                "source_hash_scope": "Explicit component/test files plus all local Python modules actually imported by this readiness process"}
    finally:
        sys.meta_path.remove(guard)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write a new JSON report; existing files are never replaced")
    parser.add_argument("--tokenizer-cache", type=Path, default=ROOT / ".cache/tokenizers")
    args = parser.parse_args()
    if args.output is not None and args.output.exists():
        parser.error("Output already exists; choose a fresh path")
    report = check(args.tokenizer_cache.resolve())
    text = json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if args.output is not None:
        with args.output.open("x") as stream:
            stream.write(text)
    print(text, end="")


if __name__ == "__main__":
    main()
