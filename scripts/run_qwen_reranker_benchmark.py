#!/usr/bin/env python3
"""Frozen experimental dense/hybrid versus resident Qwen reranking comparison."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sqlite3
import subprocess
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import run_clause_fusion as packing
import run_language_quality as language

ROOT = Path(__file__).resolve().parents[1]
CONDITIONS = ("dense", "dense_qwen", "hybrid", "hybrid_qwen")
REPETITIONS = 3
MANIFESTS = ("benchmarks/quality_manifest.json", "benchmarks/language_quality_manifest.json",
             "benchmarks/koa_clause_holdout_manifest.json")
PROTOCOL = ROOT / "docs/qwen-reranker-full-protocol.md"
GENERIC_WARMUP = "Find the implementation that processes an incoming request."
OLD_CACHES = {
    "flask": "flask_queries-quality.sqlite3", "requests": "new_requests_queries-quality.sqlite3",
    "click": "click_queries-quality.sqlite3", "starlette": "starlette_queries-quality.sqlite3",
    "rich": "rich_queries-quality.sqlite3", "express": "language-quality-2026-10-13-express.sqlite3",
    "zod": "language-quality-2026-10-13-zod.sqlite3",
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise ValueError("Resident scorer must not redirect authenticated requests")


def loopback_opener():
    return build_opener(ProxyHandler({}), NoRedirect())


def validate_loopback_url(url):
    parts = urlsplit(url)
    require(parts.scheme == "http" and parts.hostname == "127.0.0.1" and parts.port is not None
            and parts.username is None and parts.password is None and not parts.path
            and not parts.query and not parts.fragment, "Only owned loopback server accepted")
    return url


def publish(value):
    return packing.publication(value)


def schedule(catalog):
    result = []
    for dataset in dict.fromkeys(row["dataset"] for row in catalog):
        selected = [row for row in catalog if row["dataset"] == dataset]
        for repetition in range(1, REPETITIONS + 1):
            for number, query in enumerate(selected):
                offset = (number + repetition - 1) % len(CONDITIONS)
                for condition in CONDITIONS[offset:] + CONDITIONS[:offset]:
                    result.append({"dataset": dataset, "query_id": query["query_id"],
                                   "condition": condition, "repetition": repetition})
    return result


def normalized_groups(query, sources):
    from semantic_search.quality import target_groups
    result = deepcopy(target_groups(query))
    for group in result:
        for target in group:
            require(target["path"] in sources, "Gold outside production scope")
            lines = language.physical_lines(sources[target["path"]])
            lo, hi = target["evidence_lines"]
            require(1 <= lo <= hi <= len(lines), "Gold bounds outside pinned source")
            evidence_hash = sha("".join(lines[lo - 1:hi]).encode())
            require(target.get("evidence_sha256", evidence_hash) == evidence_hash, "Existing evidence hash changed")
            target["evidence_sha256"] = evidence_hash
    return result


def evidence_ranks(windows, groups, sources, *, symbol_compatibility=False):
    if not symbol_compatibility:
        return language.evidence_ranks(windows, groups, sources)
    verified = [language.verify_window(window, sources) for window in windows]
    return [next((rank for rank, window in enumerate(verified, 1) if any(
        window["path"] == target["path"] and window["symbol"] == target["symbol"]
        and window["start_line"] <= target["evidence_lines"][0]
        and target["evidence_lines"][1] <= window["end_line"] for target in group)), None) for group in groups]


def source_inputs():
    from semantic_search.corpus import _source_files, extract_snippets
    from semantic_search.languages import decode_source, extractor_fingerprint

    entries, records, catalog = [], [], []
    for manifest_name in MANIFESTS:
        manifest = json.loads((ROOT / manifest_name).read_text())
        for original in manifest["datasets"]:
            entry = deepcopy(original)
            entry["source_exclusions"] = entry.get("source_exclusions", [])
            repo = ROOT / entry["corpus"]
            data = (ROOT / entry["dataset"]).read_bytes()
            require(sha(data) == entry["sha256"], "Dataset differs from original manifest")
            dataset = json.loads(data)
            require(subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
                    == entry["revision"], "Corpus revision differs")
            require(not subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"], text=True).strip(),
                    "Corpus has uncommitted changes")
            paths = _source_files(repo, repo / entry["source_root"], entry["source_exclusions"])
            raw = {path.relative_to(repo).as_posix(): path.read_bytes() for path in paths}
            sources = {name: decode_source(name, value) for name, value in raw.items()}
            snippets = extract_snippets(repo, entry["source_root"], max_lines=160, overlap_lines=40,
                                        source_exclusions=entry["source_exclusions"], sources=sources)
            windows = [language.snippet_window(snippet) for snippet in snippets]
            require(len({window["id"] for window in windows}) == len(windows), "Duplicate extracted identities")
            query_records = []
            for query in dataset["queries"]:
                groups = normalized_groups(query, sources)
                ranks = evidence_ranks(windows, groups, sources)
                require(all(rank is not None for rank in ranks), "Declared full source span has no eligible window")
                query_records.append({"query_id": query["id"], "kind": query["kind"], "groups": groups,
                    "eligible_windows": [[window["id"] for window in windows
                        if evidence_ranks([window], [group], sources)[0] is not None] for group in groups]})
                catalog.append({"dataset": entry["id"], "query_id": query["id"], "query": query["query"]})
            inventory = [{key: value for key, value in window.items() if key != "source"} for window in windows]
            records.append({"id": entry["id"], "source_file_sha256": {name: sha(value) for name, value in raw.items()},
                "windows": len(windows), "window_inventory": inventory,
                "window_inventory_sha256": sha(canonical(inventory)), "queries": query_records,
                "classification": "fresh retrieval repository" if entry["id"] == "koa" else "previously observed development",
                "original_manifest": manifest_name})
            entries.append((entry, dataset, sources))
    require(len(entries) == 8 and len(catalog) == 144 and len({(q["dataset"], q["query_id"]) for q in catalog}) == 144,
            "Full 144-query/eight-repository inventory required")
    return entries, records, catalog, extractor_fingerprint()


def implementation_hashes():
    paths = sorted((ROOT / "src/semantic_search").glob("*.py")) + [Path(__file__),
        ROOT / "scripts/resident_qwen_reranker.py", ROOT / "scripts/prototype_qwen_reranker.py",
        ROOT / "scripts/run_clause_fusion.py", ROOT / "scripts/run_language_quality.py",
        ROOT / "pyproject.toml", ROOT / "requirements-languages.lock", PROTOCOL]
    return {str(path.relative_to(ROOT)): sha(path.read_bytes()) for path in paths}


def freeze_inputs(reranker_python, checkpoint):
    from semantic_search.embeddings import QwenEmbedder

    entries, sources, catalog, extractor = source_inputs()
    preflight = json.loads(subprocess.check_output([str(reranker_python), str(ROOT / "scripts/resident_qwen_reranker.py"),
                          "--checkpoint", str(checkpoint)], text=True))
    require(not preflight["runtime"]["errors"] and not preflight["weights_loaded"], "Reranker metadata preflight failed")
    embedder = QwenEmbedder(progress=False)
    plan = {"version": 1, "classification": "Full observed development benchmark plus previously unranked Koa source corpus",
        "conditions": list(CONDITIONS), "repetitions": REPETITIONS, "settings": packing.SETTINGS,
        "query_catalog": catalog, "schedule": schedule(catalog), "planned_requests": 1728,
        "planned_reranker_requests": 864, "maximum_pair_forwards": 43200, "generic_reranker_warmup_forwards": 1,
        "generic_embedding_warmup": GENERIC_WARMUP, "primary_quality_repetition": 1,
        "manifest_sha256": {name: sha((ROOT / name).read_bytes()) for name in MANIFESTS},
        "implementation_sha256": implementation_hashes(), "semantic_module_origins": packing.module_origins(),
        "extractor": extractor, "sources": sources, "embedding": embedder.metadata,
        "embedding_checkpoint": packing.checkpoint_inventory(), "offline_tokenizer": packing.tokenizer_cache()[1],
        "reranker": {"model_id": preflight["checkpoint"]["model_id"], "revision": preflight["checkpoint"]["revision"],
            "checkpoint_files": preflight["checkpoint"]["files"], "prototype_sha256": preflight["prototype_sha256"],
            "packages": preflight["packages"],
            "runtime": {key: preflight["runtime"][key] for key in ("packages", "qwen3_source_sha256")}},
        "packages": dict(sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions())),
        "python": platform.python_version(), "machine": {"platform": platform.platform(), "architecture": platform.machine()},
        "cache_seed": {entry["id"]: OLD_CACHES.get(entry["id"]) for entry, _, _ in entries},
        "ranking": "Descending freshly observed yes-minus-no logit difference, then original candidate rank, then stable ID",
        "primary_scoring": "Complete original declared executable source spans; symbol-independent, hashes verified",
        "python_compatibility": "Also report original exact-symbol plus original full-span metric; labels/intervals untouched",
        "timing": "Warm in-process embedding/retrieval/helper-loopback-HTTP/packing pipeline, not production full HTTP; instrumentation remains included",
        "same_candidate_pool": "Freshly recomputed in every condition; verify paired identity/order/source hashes and report all repeated-pool drift without retries",
        "query_cache": False, "score_cache": False, "threshold": None, "h2o": False,
        "model_calls_in_freeze": 0, "weights_loaded_in_freeze": False,
        "limitations": ["Python/Express/Zod labels and repositories already observed; Koa source/labels reviewed but retrieval unobserved.",
            "Python declared spans can be narrower executable branches; JS/TS/Koa spans usually whole declared bodies; report strata.",
            "Authored sample; no pretraining holdout or provider billing/coding correctness claim.",
            "No numerical PyTorch parity proof; model clips only complete source-line prefixes at 8192 total tokens.",
            "Reranking only reorders its fresh top50 pool; unchanged static dependency expansion can add evidence outside that pool, so candidate coverage is not a delivered-evidence ceiling; absent queries retain decoys.",
            "Three resident timing passes on one Mac are descriptive; no score/query caching; gold evaluated after timing."]}
    require(embedder._model is None, "CPU freeze loaded embedding weights")
    return entries, plan


def qwen_order(pairs, scores):
    require(len(pairs) == len(scores), "All candidates must have observed scores")
    for (snippet, _), score in zip(pairs, scores):
        require(score["source_sha256"] == sha(snippet.text.encode()), "Scored source differs from candidate")
    return sorted(range(len(pairs)), key=lambda i: (-scores[i]["logit_difference"], i, pairs[i][0].id))


def validate_caps(response, sources):
    from semantic_search.context import compact_result, encoding, serialize_context

    require(len(response["results"]) <= 8 and response["total_source_chars"]
            == sum(len(row["source"]) for row in response["results"]) <= 12000, "Shared result/source caps changed")
    tokens = len(encoding().encode(serialize_context(compact_result(response)), disallowed_special=()))
    require(tokens == response["token_usage"]["tokens"] <= 4096, "Exact compact token accounting differs")
    for window in response["results"]:
        language.verify_window(window, sources)


def seed_database(destination, filename):
    if filename is None or not (ROOT / ".cache/indexes" / filename).exists():
        return {"seeded": False, "reason": "No existing seed index; actual setup records cold embedding work"}
    source = ROOT / ".cache/indexes" / filename
    require(not destination.exists(), "New owned per-run index already exists")
    before = sha(source.read_bytes())
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as old:
        with sqlite3.connect(destination) as new:
            old.backup(new)
    require(sha(source.read_bytes()) == before, "Seed database main file changed")
    return {"seeded": True, "source": str(source.relative_to(ROOT)), "source_main_file_sha256": before,
            "method": "SQLite read-only connection backup into new owned destination"}


def run(output, reranker_python, checkpoint, ready_path, token_path):
    import numpy as np
    from semantic_search.backend import SearchBackend
    from semantic_search.embeddings import QwenEmbedder

    require(not (output / "run-start.json").exists(), "No resume/retry or overwriting attempted runs")
    frozen = json.loads((output / "frozen-plan.json").read_text())
    entries, current = freeze_inputs(reranker_python, checkpoint)
    require(set(frozen) == set(current) | {"frozen_at_utc", "source_commit"}, "Unexpected frozen plan fields")
    for key, value in current.items():
        require(value == frozen[key], "Frozen invariant changed: " + key)
    ready = json.loads(ready_path.read_text())
    require(ready["frozen_plan_sha256"] == sha((output / "frozen-plan.json").read_bytes())
            and ready["resident_ready"] and ready["generic_warmup_completed"], "Resident server not ready for this exact plan")
    validate_loopback_url(ready["url"])
    http = loopback_opener()
    token = token_path.read_text().strip()
    write(output / "run-start.json", {"started_at_utc": datetime.now(timezone.utc).isoformat(),
          "frozen_plan_sha256": sha((output / "frozen-plan.json").read_bytes())})
    write(output / "embedding-weight-verification.json", packing.verify_weights(frozen["embedding_checkpoint"]))

    class Observed(QwenEmbedder):
        def __init__(self):
            super().__init__(progress=True)
            self.query_calls = self.document_calls = self.document_texts = 0
            self.attempted_query_calls = self.attempted_document_calls = self.attempted_document_texts = 0

        def embed_query(self, text):
            self.attempted_query_calls += 1
            result = super().embed_query(text)
            self.query_calls += 1
            return result

        def embed_documents(self, texts, **kwargs):
            self.attempted_document_calls += 1
            self.attempted_document_texts += len(texts)
            result = super().embed_documents(texts, **kwargs)
            self.document_calls += 1
            self.document_texts += len(texts)
            return result

        def counts(self):
            return {"completed_query_calls": self.query_calls, "completed_document_calls": self.document_calls,
                    "document_texts": self.document_texts, "attempted_query_calls": self.attempted_query_calls,
                    "attempted_document_calls": self.attempted_document_calls,
                    "attempted_document_texts": self.attempted_document_texts}

    class Borrowed:
        def __init__(self, owner):
            self.owner = owner

        @property
        def metadata(self):
            return self.owner.metadata

        def embed_query(self, *args, **kwargs):
            return self.owner.embed_query(*args, **kwargs)

        def embed_documents(self, *args, **kwargs):
            return self.owner.embed_documents(*args, **kwargs)

        def close(self):
            pass

    owner = Observed()
    rows, setups, pools, content_seen = [], [], {}, set()
    paired_pools, repeated_pools = {}, {}
    backend = None
    complete = False
    try:
        with packing.pinned_tokenizer(), gzip.open(output / "searches.jsonl.gz", "xt", encoding="utf-8") as ledger, \
                gzip.open(output / "candidate-pools.jsonl.gz", "xt", encoding="utf-8") as pool_ledger, \
                gzip.open(output / "source-content.jsonl.gz", "xt", encoding="utf-8") as content_ledger:
            def append(stream, value):
                stream.write(canonical(publish(value)).decode() + "\n")
                stream.flush()

            for entry, dataset, sources in entries:
                identifier = entry["id"]
                destination = ROOT / ".cache/indexes" / (output.name + "-" + identifier + ".sqlite3")
                require(not destination.exists(), "Owned per-run cache already exists")
                seed = seed_database(destination, frozen["cache_seed"][identifier])
                backend = SearchBackend(ROOT / entry["corpus"], entry["source_root"], destination,
                    embedder=Borrowed(owner), source_exclusions=entry["source_exclusions"])
                phase = time.perf_counter()
                before = owner.counts()
                warmup = backend.search(GENERIC_WARMUP, retrieval="dense", max_results=1, rerank=False)
                setup = {"dataset": identifier, "index_and_embedding_warmup_s": time.perf_counter() - phase,
                    "seed": seed, "indexstats": warmup["indexstats"], "counts_before": before,
                    "counts_after": owner.counts(), "excluded_from_latency": True}
                setups.append(setup)
                write(output / "setups.json", publish(setups))
                queries = {q["id"]: q for q in dataset["queries"]}
                frozen_source = next(record for record in frozen["sources"] if record["id"] == identifier)
                normalized = {q["query_id"]: q for q in frozen_source["queries"]}
                for identity in [row for row in frozen["schedule"] if row["dataset"] == identifier]:
                    query = queries[identity["query_id"]]
                    row = {"identity": identity, "query": query["query"], "status": "started", "counts_before": owner.counts(),
                           "active_phase": "query_embedding"}
                    started = time.perf_counter()
                    try:
                        condition = identity["condition"]
                        retrieval = condition.split("_")[0]
                        phase = time.perf_counter()
                        vector = owner.embed_query(query["query"])
                        row["query_embedding_s"] = time.perf_counter() - phase
                        row["active_phase"] = "candidate_retrieval"
                        phase = time.perf_counter()
                        if retrieval == "dense":
                            pairs = backend._dense.search(vector, 50)
                            top_cosine = pairs[0][1] if pairs else None
                        else:
                            pairs = backend._hybrid.search(query["query"], vector, 50)
                            top_dense = backend._dense.search(vector, 1)
                            top_cosine = top_dense[0][1] if top_dense else None
                        row["candidate_retrieval_s"] = time.perf_counter() - phase
                        raw_windows = [language.snippet_window(snippet, score) for snippet, score in pairs]
                        pool_key = identifier + "/" + query["id"] + "/" + retrieval
                        signature = [{key: value for key, value in window.items() if key != "source"} for window in raw_windows]
                        pool_hash = sha(canonical(signature))
                        vector_values = np.asarray(vector)
                        row["vector"] = {"sha256": sha(vector_values.tobytes()), "dtype": str(vector_values.dtype),
                                         "shape": list(vector_values.shape)}
                        # Every fresh candidate list has a receipt. Identical pools reference one stored copy.
                        pool_id = pool_key + "/" + pool_hash
                        row["pool_id"] = pool_id
                        row["candidate_pool_sha256"] = pool_hash
                        row["candidate_count"] = len(pairs)
                        row["candidate_order"] = [snippet.id for snippet, _ in pairs]
                        identity_hash = sha(canonical([{key: value for key, value in window.items() if key not in ("source", "retrieval_score")}
                                                      for window in raw_windows]))
                        row["candidate_identity_source_sha256"] = identity_hash
                        pair_key = pool_key + "/" + str(identity["repetition"])
                        row["matches_prior_condition_pool_in_pair"] = None if pair_key not in paired_pools else paired_pools[pair_key] == identity_hash
                        paired_pools.setdefault(pair_key, identity_hash)
                        row["matches_first_observed_pool_across_repetitions"] = None if pool_key not in repeated_pools else repeated_pools[pool_key] == identity_hash
                        repeated_pools.setdefault(pool_key, identity_hash)
                        scores = []
                        row["reranker_http_s"] = 0.0
                        if condition.endswith("_qwen") and pairs:
                            row["active_phase"] = "reranker_http"
                            payload = {"request_id": "/".join(str(identity[key]) for key in ("dataset", "query_id", "condition", "repetition")),
                                       "pairs": [{"query": query["query"], "source": snippet.text} for snippet, _ in pairs]}
                            phase = time.perf_counter()
                            request = Request(ready["url"] + "/score", data=canonical(payload),
                                              headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"}, method="POST")
                            with http.open(request, timeout=180) as reply:
                                scored = json.loads(reply.read())
                            row["reranker_http_s"] = time.perf_counter() - phase
                            require(scored["request_id"] == payload["request_id"] and scored["pairs_sha256"]
                                    == sha(canonical(payload["pairs"])), "Resident response identity/input mismatch")
                            scores = scored["results"]
                            row["active_phase"] = "reranker_ordering"
                            order = qwen_order(pairs, scores)
                            selected = [(pairs[i][0], scores[i]["logit_difference"]) for i in order]
                            row["reranker"] = scored
                            row["ranking_order"] = [pairs[i][0].id for i in order]
                            score_kind = "experimental_qwen_yes_minus_no_logit_difference"
                        else:
                            selected = pairs
                            row["ranking_order"] = row["candidate_order"]
                            score_kind = "cosine" if retrieval == "dense" else "equal_weight_rrf_priority_k60"
                        row["active_phase"] = "packing"
                        phase = time.perf_counter()
                        response = packing.pack_candidates(backend, query["query"], vector, selected,
                            top_full_cosine=top_cosine, score_kind=score_kind)
                        row["packing_s"] = time.perf_counter() - phase
                        row["elapsed_warm_pipeline_s"] = time.perf_counter() - started
                        # Below is excluded from measured intervals: validation, label scoring and artifact writes.
                        row["active_phase"] = "post_timing_validation_and_scoring"
                        response["experimental_candidate_retrieval"] = retrieval
                        response["experimental_packing_adapter"] = "dense adapter with externally obtained candidate order"
                        response["retrieval"] = retrieval
                        row["vector"]["values"] = vector_values.tolist()
                        if pool_id not in pools:
                            for window in raw_windows:
                                language.verify_window(window, sources)
                                content_key = sha(window["source"].encode())
                                if content_key not in content_seen:
                                    append(content_ledger, {"sha256": content_key, "source": window["source"]})
                                    content_seen.add(content_key)
                            append(pool_ledger, {"pool_id": pool_id, "pool_key": pool_key, "pool_sha256": pool_hash,
                                                 "first_identity": identity, "windows": signature})
                            pools[pool_id] = signature
                        validate_caps(response, sources)
                        gold = normalized[query["id"]]["groups"]
                        row["kind"] = query["kind"]
                        row["multi_file_and"] = len(gold) > 1 and len({t["path"] for group in gold for t in group}) > 1
                        row["required_groups"] = len(gold)
                        row["evidence_ranks"] = evidence_ranks(response["results"], gold, sources)
                        row["candidate_evidence_ranks"] = evidence_ranks(raw_windows, gold, sources)
                        row["ranked_candidate_evidence_ranks"] = evidence_ranks(
                            [language.snippet_window(snippet, score) for snippet, score in selected], gold, sources)
                        if identifier in OLD_CACHES and identifier not in ("express", "zod"):
                            row["symbol_compatibility_evidence_ranks"] = evidence_ranks(response["results"], gold, sources, symbol_compatibility=True)
                            row["symbol_compatibility_candidate_evidence_ranks"] = evidence_ranks(raw_windows, gold, sources, symbol_compatibility=True)
                        row["response"] = response
                        row["counts_after"] = owner.counts()
                        require(row["counts_after"]["completed_query_calls"] - row["counts_before"]["completed_query_calls"] == 1,
                                "Each actual request must recompute exactly one query embedding")
                        require(row["counts_after"]["completed_document_calls"] == row["counts_before"]["completed_document_calls"],
                                "Measured query unexpectedly embedded documents")
                        row["status"] = "completed"
                        row["active_phase"] = "complete"
                    except BaseException as error:
                        row["status"] = "failed"
                        row["error_type"], row["error"] = type(error).__name__, str(error)
                        row["counts_after"] = owner.counts()
                        row["failed_elapsed_s"] = time.perf_counter() - started
                        rows.append(row)
                        append(ledger, row)
                        raise
                    rows.append(row)
                    append(ledger, row)
                backend.close()
                setup["backend_closed"] = backend.status()["closed"]
                backend = None
                write(output / "setups.json", publish(setups))
                print(json.dumps({"dataset_completed": identifier, "completed_requests": len(rows), "planned_requests": 1728}), flush=True)
        require(implementation_hashes() == frozen["implementation_sha256"], "Producer/helper/core source changed during run")
        require(packing.module_origins() == frozen["semantic_module_origins"], "Imported module changed during run")
        complete = len(rows) == frozen["planned_requests"] and all(row["status"] == "completed" for row in rows)
    except BaseException as error:
        write(output / "failure.json", {"error_type": type(error).__name__, "error": str(error),
              "attempted_requests": len(rows), "completed_requests": sum(r["status"] == "completed" for r in rows)})
        raise
    finally:
        if backend is not None:
            backend.close()
        owner.close()
        write(output / "completion.json", {"complete": complete, "attempted_requests": len(rows),
            "completed_requests": sum(row["status"] == "completed" for row in rows), "embedding_counts": owner.counts(),
            "paired_candidate_mismatches": [row["identity"] for row in rows if row.get("matches_prior_condition_pool_in_pair") is False],
            "repeated_candidate_mismatches": [row["identity"] for row in rows if row.get("matches_first_observed_pool_across_repetitions") is False],
            "embedding_owner_closed": owner._model is None, "finished_at_utc": datetime.now(timezone.utc).isoformat()})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--freeze", action="store_true")
    modes.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reranker-python", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--ready", type=Path)
    parser.add_argument("--token-file", type=Path)
    args = parser.parse_args(argv)
    if args.freeze:
        args.output.mkdir(parents=True, exist_ok=False)
        _, plan = freeze_inputs(args.reranker_python, args.checkpoint)
        plan["frozen_at_utc"] = datetime.now(timezone.utc).isoformat()
        plan["source_commit"] = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
        write(args.output / "frozen-plan.json", plan)
        print(json.dumps({"model_calls": 0, "weights_loaded": False, "unique_queries": len(plan["query_catalog"]),
                          "planned_requests": len(plan["schedule"])}))
    else:
        if args.ready is None or args.token_file is None:
            parser.error("--run requires --ready and --token-file")
        run(args.output, args.reranker_python, args.checkpoint, args.ready, args.token_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
