#!/usr/bin/env python3
"""Actual frozen embedding/retrieval/prefix-scoring/packing experiment helper.

Importing this module loads neither model. The explicitly invoked pipeline owns
one embedding model and borrows it across separately owned corpus indexes.
"""
from __future__ import annotations

from datetime import datetime, timezone
import gzip
import json
import math
from urllib.request import Request

import run_qwen_reranker_benchmark as previous

ROOT = previous.ROOT
packing, language = previous.packing, previous.language
sha, require, canonical, write = previous.sha, previous.require, previous.canonical, previous.write


def append(stream, value):
    stream.write(canonical(previous.publish(value)).decode() + "\n")
    stream.flush()


def observed_embedder():
    """Import the real embedding class only from the explicit model run path."""
    from semantic_search.embeddings import QwenEmbedder

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

    return Observed()


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


def check_configuration(value):
    require(isinstance(value, dict) and set(value) == {"id", "retrieval", "shortlist", "batch_size"},
            "Exact frozen configuration fields required")
    require(value["retrieval"] in ("dense", "hybrid") and type(value["shortlist"]) is int
            and 1 <= value["shortlist"] <= 50 and type(value["batch_size"]) is int
            and value["batch_size"] in (0, 1, 2, 4, 8), "Invalid frozen configuration")
    expected = value["retrieval"] if value["batch_size"] == 0 else (
        f"{value['retrieval']}_k{value['shortlist']}_b{value['batch_size']}")
    require(value["id"] == expected and (value["batch_size"] or value["shortlist"] == 50),
            "Configuration identity or baseline full-pool depth differs")
    return value


def request_scores(http, ready, token, request_id, pairs, batch_size):
    """One non-retried request; only literal query/source and batch size go out."""
    previous.validate_loopback_url(ready["url"])
    payload = {"request_id": request_id, "pairs": pairs, "batch_size": batch_size}
    started = previous.time.perf_counter()
    request = Request(ready["url"] + "/score", data=canonical(payload),
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"}, method="POST")
    with http.open(request, timeout=240) as reply:
        result = json.loads(reply.read())
    elapsed = previous.time.perf_counter() - started
    require(result["request_id"] == request_id and result["pairs_sha256"] == sha(canonical(pairs))
            and result["batch_size"] == batch_size and len(result["results"]) == len(pairs),
            "Worker input/result identity mismatch")
    return result, elapsed


def select_prefix(pairs, query, configuration, http, ready, token, request_id):
    """Observe every scored prefix afresh and never append its unscored tail."""
    check_configuration(configuration)
    require(isinstance(pairs, list) and len(pairs) == 50, "Exact freshly retrieved full50 pool required")
    if not configuration["batch_size"]:
        return pairs, None, 0.0, [snippet.id for snippet, _ in pairs]
    prefix = pairs[:configuration["shortlist"]]
    payload_pairs = [{"query": query, "source": snippet.text} for snippet, _ in prefix]
    scored, elapsed = request_scores(http, ready, token, request_id, payload_pairs, configuration["batch_size"])
    for score in scored["results"]:
        require(type(score["logit_difference"]) in (int, float) and math.isfinite(score["logit_difference"]),
                "Reranker ordering scalar must be finite")
    order = previous.qwen_order(prefix, scored["results"])
    selected = [(prefix[index][0], scored["results"][index]["logit_difference"]) for index in order]
    return selected, scored, elapsed, [prefix[index][0].id for index in order]


def search_backend(*args, **kwargs):
    from semantic_search.backend import SearchBackend
    return SearchBackend(*args, **kwargs)


def run_pipeline(output, entries, frozen, http, ready, token):
    """Write unchanged full-run ledgers plus configuration/prefix/batch receipts.

    The driver validates frozen source/runtime/readiness and writes run-start and
    embedding weight verification first. This helper never resumes or overwrites
    an attempted ledger. It records every attempted query and closes its owned
    embedding model even after a scoring, source, packing, index or write failure.
    """
    import numpy as np

    configurations = {value["id"]: check_configuration(value) for value in frozen["conditions"]}
    require(len(configurations) == len(frozen["conditions"]), "Duplicate frozen configurations")
    require(frozen["settings"] == packing.SETTINGS, "Original packing caps changed")
    require(frozen["planned_requests"] == len(frozen["schedule"]), "Planned schedule count differs")
    entry_ids = [entry["id"] for entry, _, _ in entries]
    require(len(set(entry_ids)) == len(entry_ids), "Duplicate corpus entry")
    require([row for identifier in entry_ids for row in frozen["schedule"] if row["dataset"] == identifier]
            == frozen["schedule"], "Schedule differs from contiguous frozen corpus order")
    for identity in frozen["schedule"]:
        require(set(identity) == {"dataset", "query_id", "condition", "repetition"}
                and identity["condition"] in configurations and identity["dataset"] in entry_ids,
                "Unexpected frozen schedule identity")
    owner = observed_embedder()
    rows, setups, pools, content_seen = [], [], {}, set()
    paired_pools, repeated_pools = {}, {}
    backend = None
    complete = False
    completion = None
    try:
        require(owner.metadata == frozen["embedding"], "Embedding owner differs from freeze")
        with packing.pinned_tokenizer(), gzip.open(output / "searches.jsonl.gz", "xt", encoding="utf-8") as ledger, \
                gzip.open(output / "candidate-pools.jsonl.gz", "xt", encoding="utf-8") as pool_ledger, \
                gzip.open(output / "source-content.jsonl.gz", "xt", encoding="utf-8") as content_ledger:
            for entry, dataset, sources in entries:
                identifier = entry["id"]
                destination = ROOT / ".cache/indexes" / (output.name + "-" + identifier + ".sqlite3")
                destination.parent.mkdir(parents=True, exist_ok=True)
                require(not destination.exists(), "Owned per-stage cache already exists")
                seed = previous.seed_database(destination, frozen["cache_seed"][identifier])
                backend = search_backend(ROOT / entry["corpus"], entry["source_root"], destination,
                    embedder=Borrowed(owner), source_exclusions=entry["source_exclusions"])
                phase = previous.time.perf_counter()
                before = owner.counts()
                warmup = backend.search(previous.GENERIC_WARMUP, retrieval="dense", max_results=1, rerank=False)
                setup = {"dataset": identifier, "index_and_embedding_warmup_s": previous.time.perf_counter() - phase,
                    "seed": seed, "indexstats": warmup["indexstats"], "counts_before": before,
                    "counts_after": owner.counts(), "excluded_from_latency": True}
                setups.append(setup)
                write(output / "setups.json", previous.publish(setups))
                queries = {query["id"]: query for query in dataset["queries"]}
                source = next(record for record in frozen["sources"] if record["id"] == identifier)
                normalized = {query["query_id"]: query for query in source["queries"]}
                for identity in [row for row in frozen["schedule"] if row["dataset"] == identifier]:
                    query = queries[identity["query_id"]]
                    configuration = configurations[identity["condition"]]
                    row = {"identity": identity, "query": query["query"], "configuration": configuration,
                           "status": "started", "counts_before": owner.counts(), "active_phase": "query_embedding"}
                    started = previous.time.perf_counter()
                    try:
                        retrieval = configuration["retrieval"]
                        phase = previous.time.perf_counter()
                        vector = owner.embed_query(query["query"])
                        row["query_embedding_s"] = previous.time.perf_counter() - phase
                        row["active_phase"] = "candidate_retrieval"
                        phase = previous.time.perf_counter()
                        if retrieval == "dense":
                            pairs = backend._dense.search(vector, 50)
                            top_cosine = pairs[0][1] if pairs else None
                        else:
                            pairs = backend._hybrid.search(query["query"], vector, 50)
                            top_dense = backend._dense.search(vector, 1)
                            top_cosine = top_dense[0][1] if top_dense else None
                        row["candidate_retrieval_s"] = previous.time.perf_counter() - phase
                        raw_windows = [language.snippet_window(snippet, score) for snippet, score in pairs]
                        pool_key = identifier + "/" + query["id"] + "/" + retrieval
                        signature = [{key: value for key, value in window.items() if key != "source"} for window in raw_windows]
                        pool_hash = sha(canonical(signature))
                        vector_values = np.asarray(vector)
                        row["vector"] = {"sha256": sha(vector_values.tobytes()), "dtype": str(vector_values.dtype),
                                         "shape": list(vector_values.shape)}
                        pool_id = pool_key + "/" + pool_hash
                        row["pool_id"], row["candidate_pool_sha256"] = pool_id, pool_hash
                        row["candidate_count"] = len(pairs)
                        row["candidate_order"] = [snippet.id for snippet, _ in pairs]
                        identity_hash = sha(canonical([{key: value for key, value in window.items()
                                                      if key not in ("source", "retrieval_score")} for window in raw_windows]))
                        row["candidate_identity_source_sha256"] = identity_hash
                        pair_key = pool_key + "/" + str(identity["repetition"])
                        row["matches_prior_condition_pool_in_pair"] = (None if pair_key not in paired_pools
                                                                       else paired_pools[pair_key] == identity_hash)
                        paired_pools.setdefault(pair_key, identity_hash)
                        row["matches_first_observed_pool_across_repetitions"] = (None if pool_key not in repeated_pools
                                                                                else repeated_pools[pool_key] == identity_hash)
                        repeated_pools.setdefault(pool_key, identity_hash)
                        row["active_phase"] = "reranker_http" if configuration["batch_size"] else "baseline_ordering"
                        request_id = "/".join(str(identity[key]) for key in ("dataset", "query_id", "condition", "repetition"))
                        selected, scored, elapsed, order = select_prefix(pairs, query["query"], configuration,
                                                                        http, ready, token, request_id)
                        row["reranker_http_s"], row["ranking_order"] = elapsed, order
                        row["shortlist_count"] = len(selected)
                        row["shortlist_original_order"] = row["candidate_order"][:len(selected)]
                        if scored is not None:
                            row["reranker"] = scored
                            score_kind = "experimental_qwen_yes_minus_no_logit_difference"
                        else:
                            score_kind = "cosine" if retrieval == "dense" else "equal_weight_rrf_priority_k60"
                        row["active_phase"] = "packing"
                        phase = previous.time.perf_counter()
                        response = packing.pack_candidates(backend, query["query"], vector, selected,
                            top_full_cosine=top_cosine, score_kind=score_kind)
                        row["packing_s"] = previous.time.perf_counter() - phase
                        row["elapsed_warm_pipeline_s"] = previous.time.perf_counter() - started
                        # Validation, gold scoring and writes are outside the same original timing boundary.
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
                        previous.validate_caps(response, sources)
                        gold = normalized[query["id"]]["groups"]
                        row["kind"] = query["kind"]
                        row["multi_file_and"] = len(gold) > 1 and len({target["path"] for group in gold for target in group}) > 1
                        row["required_groups"] = len(gold)
                        row["evidence_ranks"] = previous.evidence_ranks(response["results"], gold, sources)
                        row["candidate_evidence_ranks"] = previous.evidence_ranks(raw_windows, gold, sources)
                        row["shortlist_evidence_ranks"] = previous.evidence_ranks(raw_windows[:len(selected)], gold, sources)
                        row["ranked_candidate_evidence_ranks"] = previous.evidence_ranks(
                            [language.snippet_window(snippet, score) for snippet, score in selected], gold, sources)
                        if entry.get("language", "python") == "python":
                            row["symbol_compatibility_evidence_ranks"] = previous.evidence_ranks(
                                response["results"], gold, sources, symbol_compatibility=True)
                            row["symbol_compatibility_candidate_evidence_ranks"] = previous.evidence_ranks(
                                raw_windows, gold, sources, symbol_compatibility=True)
                        row["response"], row["counts_after"] = response, owner.counts()
                        require(row["counts_after"]["completed_query_calls"] - row["counts_before"]["completed_query_calls"] == 1,
                                "Each measured request must recompute exactly one query embedding")
                        require(row["counts_after"]["completed_document_calls"] == row["counts_before"]["completed_document_calls"],
                                "Measured query unexpectedly embedded documents")
                        row["status"], row["active_phase"] = "completed", "complete"
                    except BaseException as error:
                        row["status"], row["error_type"], row["error"] = "failed", type(error).__name__, str(error)
                        row["counts_after"] = owner.counts()
                        row["failed_elapsed_s"] = previous.time.perf_counter() - started
                        rows.append(row)
                        append(ledger, row)
                        raise
                    rows.append(row)
                    append(ledger, row)
                backend.close()
                setup["backend_closed"] = backend.status()["closed"]
                backend = None
                write(output / "setups.json", previous.publish(setups))
                print(json.dumps({"dataset_completed": identifier, "completed_requests": len(rows),
                                  "planned_requests": frozen["planned_requests"]}), flush=True)
        complete = (len(rows) == frozen["planned_requests"] and all(row["status"] == "completed" for row in rows)
                    and [row["identity"] for row in rows] == frozen["schedule"])
        require(complete, "Actual search ledger does not complete exact frozen schedule")
    except BaseException as error:
        write(output / "failure.json", {"error_type": type(error).__name__, "error": str(error),
              "attempted_requests": len(rows), "completed_requests": sum(row["status"] == "completed" for row in rows)})
        raise
    finally:
        try:
            if backend is not None:
                backend.close()
        finally:
            owner.close()
            completion = {"complete": complete, "attempted_requests": len(rows),
                "completed_requests": sum(row["status"] == "completed" for row in rows),
                "embedding_counts": owner.counts(),
                "paired_candidate_mismatches": [row["identity"] for row in rows
                                                if row.get("matches_prior_condition_pool_in_pair") is False],
                "repeated_candidate_mismatches": [row["identity"] for row in rows
                                                  if row.get("matches_first_observed_pool_across_repetitions") is False],
                "embedding_owner_closed": owner._model is None,
                "finished_at_utc": datetime.now(timezone.utc).isoformat()}
            write(output / "completion.json", completion)
    return completion
