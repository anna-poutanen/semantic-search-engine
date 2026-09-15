#!/usr/bin/env python3
"""One frozen, text-only clause-fusion development probe; inference requires --run."""

from __future__ import annotations

import argparse
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import importlib.metadata
import importlib
import importlib.util
import json
from pathlib import Path
import platform
import re
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "docs/clause-fusion-development.md"
MANIFEST = ROOT / "benchmarks/language_quality_manifest.json"
CONDITIONS = ("full_query_dense", "clause_rrf")
SETTINGS = {
    "rerank": False,
    "candidates": 50,
    "max_results": 8,
    "source_budget": 12000,
    "token_budget": 4096,
    "include_dependencies": True,
}
RULE = {
    "version": 1,
    "separators": ["and where", "and then", "while", "then", "before"],
    "case_insensitive": True,
    "word_boundaries": True,
    "minimum_clause_whitespace_words": 4,
    "maximum_additional_clauses": 3,
    "deduplication": "collapsed whitespace and casefold",
    "selection": "full original query, then first eligible distinct clauses in source order",
    "rrf_k": 60,
    "weights": "equal",
    "per_list_depth": 50,
    "merged_depth": 50,
    "ties": "original full-query rank then stable snippet ID",
    "threshold": None,
    "h2o": False,
}
_SEPARATOR = re.compile(
    r"\b(?:and\s+where|and\s+then|while|then|before)\b", re.IGNORECASE
)
CACHE_URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
CACHE_SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
CACHE_KEY = hashlib.sha1(CACHE_URL.encode()).hexdigest()


def require(ok, detail):
    if not ok:
        raise ValueError(detail)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        + "\n"
    )


def clause_inputs(query):
    """Only literal query text enters this rule; no labels, paths or target kinds."""
    require(isinstance(query, str) and query.strip(), "Nonempty query text required")
    query.encode("utf-8")
    result = [query]

    def key(text):
        return " ".join(text.split()).casefold()

    seen = {key(query)}
    for raw in _SEPARATOR.split(query):
        clause = " ".join(raw.split())
        if len(clause.split()) >= 4 and key(clause) not in seen:
            result.append(clause)
            seen.add(key(clause))
            if len(result) == 4:
                break
    return result


def fuse_rankings(rankings):
    """Pure source-rank fusion. Scores/paths/source text/gold fields are never read."""
    require(
        isinstance(rankings, list) and 1 <= len(rankings) <= 4,
        "One full list and at most three clause lists",
    )
    scores, items = {}, {}
    full_ranks = {}
    for number, ranking in enumerate(rankings):
        require(
            isinstance(ranking, list) and len(ranking) <= 50,
            "Rank list depth exceeds fifty",
        )
        seen = set()
        for rank, row in enumerate(ranking, 1):
            identifier = row["id"]
            require(
                isinstance(identifier, str) and identifier and identifier not in seen,
                "Invalid/duplicate ranked identity",
            )
            seen.add(identifier)
            items.setdefault(identifier, row)
            scores[identifier] = scores.get(identifier, Fraction()) + Fraction(
                1, 60 + rank
            )
            if number == 0:
                full_ranks[identifier] = rank
    order = sorted(
        scores,
        key=lambda identifier: (
            -scores[identifier],
            full_ranks.get(identifier, float("inf")),
            identifier,
        ),
    )[:50]
    return [
        {
            **items[identifier],
            "fusion_priority": float(scores[identifier]),
            "fusion_exact": str(scores[identifier]),
            "full_query_rank": full_ranks.get(identifier),
        }
        for identifier in order
    ]


def language_helpers():
    spec = importlib.util.spec_from_file_location(
        "clause_probe_language_helpers", ROOT / "scripts/run_language_quality.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def publication(value):
    if isinstance(value, str):
        return value.replace(str(ROOT), "<PROJECT>").replace(str(Path.home()), "<HOME>")
    if isinstance(value, list):
        return [publication(item) for item in value]
    if isinstance(value, dict):
        return {key: publication(item) for key, item in value.items()}
    return value


def source_hashes():
    paths = sorted((ROOT / "src/semantic_search").glob("*.py")) + [
        Path(__file__),
        PROTOCOL,
        ROOT / "scripts/run_language_quality.py",
        ROOT / "tests/test_clause_fusion.py",
        ROOT / "pyproject.toml",
        ROOT / "requirements-languages.lock",
    ]
    return {str(path.relative_to(ROOT)): sha(path.read_bytes()) for path in paths}


def tokenizer_cache(cache=None):
    cache = Path(cache) if cache is not None else ROOT / ".cache/tokenizers"
    target = cache / CACHE_KEY
    require(
        target.is_file(), "Verified offline cl100k cache missing; downloads forbidden"
    )
    raw = target.read_bytes()
    require(
        sha(raw) == CACHE_SHA256,
        "Verified offline cl100k cache corrupt; downloads forbidden",
    )
    return raw, {
        "tiktoken": importlib.metadata.version("tiktoken"),
        "url": CACHE_URL,
        "cache_key": CACHE_KEY,
        "sha256": CACHE_SHA256,
        "bytes": len(raw),
        "network": False,
        "cache_writes": False,
    }


@contextmanager
def pinned_tokenizer(cache=None):
    import tiktoken.load
    import tiktoken.registry
    from semantic_search.context import encoding

    raw, identity = tokenizer_cache(cache)
    original_reader = tiktoken.load.read_file_cached
    previous = tiktoken.registry.ENCODINGS.pop("cl100k_base", None)
    encoding.cache_clear()

    def captured(blobpath, expected_hash=None):
        require(
            blobpath == CACHE_URL and expected_hash == CACHE_SHA256,
            "Unexpected tokenizer URL/hash; downloads forbidden",
        )
        return raw

    try:
        tiktoken.load.read_file_cached = captured
        encoding()
        yield identity
    finally:
        tiktoken.load.read_file_cached = original_reader
        tiktoken.registry.ENCODINGS.pop("cl100k_base", None)
        if previous is not None:
            tiktoken.registry.ENCODINGS["cl100k_base"] = previous
        encoding.cache_clear()


def module_origins():
    result = {}
    for name in (
        "backend",
        "corpus",
        "context",
        "embeddings",
        "index",
        "persistent_retrieval",
        "languages",
        "relations",
        "store",
    ):
        module = importlib.import_module(f"semantic_search.{name}")
        path = Path(module.__file__).resolve()
        require(
            path == (ROOT / "src/semantic_search" / f"{name}.py").resolve(),
            "Imported semantic module comes from another editable checkout",
        )
        result[module.__name__] = {
            "project_relative_path": str(path.relative_to(ROOT)),
            "sha256": sha(path.read_bytes()),
        }
    return result


def validate_plan(current, frozen):
    metadata = {"frozen_at_utc", "source_commit"}
    require(set(frozen) == set(current) | metadata, "Frozen plan fields differ")
    require(
        isinstance(frozen["frozen_at_utc"], str)
        and re.fullmatch(r"[0-9a-f]{40}", frozen["source_commit"]),
        "Missing freeze provenance",
    )
    for key, value in current.items():
        require(value == frozen[key], f"Exact frozen invariant differs: {key}")


def checkpoint_inventory():
    from semantic_search.embeddings import DEFAULT_MODEL_PATH

    require(
        DEFAULT_MODEL_PATH.is_dir(),
        "Local pinned Qwen checkpoint required; this probe never downloads",
    )
    result = {}
    provenance = json.loads(
        (DEFAULT_MODEL_PATH / "semantic_search_checkpoint.json").read_text()
    )
    for path in sorted(DEFAULT_MODEL_PATH.iterdir()):
        if path.is_file():
            if path.name.endswith(".safetensors"):
                # Freeze reads metadata only, never the weight file. The root's
                # explicit --run verifies its complete bytes before model load.
                result[path.name] = {
                    "bytes": path.stat().st_size,
                    "mtime_ns": path.stat().st_mtime_ns,
                    "expected_sha256_from_provenance": provenance["weights_etag"],
                }
            else:
                result[path.name] = {
                    "bytes": path.stat().st_size,
                    "sha256": sha(path.read_bytes()),
                }
    require(
        "config.json" in result
        and "semantic_search_checkpoint.json" in result
        and any(name.endswith(".safetensors") for name in result),
        "Incomplete local checkpoint",
    )
    return result


def verify_weights(checkpoint):
    from semantic_search.embeddings import DEFAULT_MODEL_PATH

    observed = {}
    for name, record in checkpoint.items():
        if name.endswith(".safetensors"):
            digest = hashlib.sha256()
            with (DEFAULT_MODEL_PATH / name).open("rb") as stream:
                for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                    digest.update(chunk)
            actual = digest.hexdigest()
            require(
                actual == record["expected_sha256_from_provenance"],
                "Actual local weights differ from pinned provenance",
            )
            observed[name] = {
                "sha256": actual,
                "bytes": (DEFAULT_MODEL_PATH / name).stat().st_size,
                "matches_pinned_provenance": True,
            }
    return observed


def query_catalog(entries):
    return [
        {
            "dataset": entry["id"],
            "query_id": query["id"],
            "query": query["query"],
            "control_inputs": clause_inputs(query["query"]),
        }
        for entry, dataset, _ in entries
        for query in dataset["queries"]
    ]


def schedule(catalog):
    result = []
    for number, query in enumerate(catalog):
        order = CONDITIONS if number % 2 == 0 else CONDITIONS[::-1]
        for condition in order:
            result.append(
                {
                    "dataset": query["dataset"],
                    "query_id": query["query_id"],
                    "condition": condition,
                }
            )
    return result


def freeze_inputs():
    from semantic_search.embeddings import QwenEmbedder

    helper = language_helpers()
    entries, preflight = helper.load_sources()
    catalog = query_catalog(entries)
    require(
        len(catalog) == 30
        and len({(row["dataset"], row["query_id"]) for row in catalog}) == 30,
        "Expected all thirty unique observed queries",
    )
    embedder = QwenEmbedder(progress=False)
    packages = dict(
        sorted(
            (distribution.metadata["Name"], distribution.version)
            for distribution in importlib.metadata.distributions()
        )
    )
    frozen = {
        "version": 1,
        "classification": "Observed Express/Zod development probe; neither repository is a new holdout",
        "selector_inputs": "literal query text and raw source-rank identities only; gold scored afterward",
        "rule": deepcopy(RULE),
        "settings": deepcopy(SETTINGS),
        "conditions": list(CONDITIONS),
        "queries": catalog,
        "schedule": schedule(catalog),
        "planned_attempts": 60,
        "unique_queries": 30,
        "planned_query_embedding_calls": {
            "full_query_dense": 30,
            "clause_rrf": sum(len(row["control_inputs"]) for row in catalog),
        },
        "dataset_sha256": {
            str((ROOT / entry["dataset"]).relative_to(ROOT)): sha(
                (ROOT / entry["dataset"]).read_bytes()
            )
            for entry, _, _ in entries
        },
        "manifest_sha256": sha(MANIFEST.read_bytes()),
        "source_sha256": source_hashes(),
        "packages": packages,
        "semantic_module_origins": module_origins(),
        "offline_tokenizer": tokenizer_cache()[1],
        "python": platform.python_version(),
        "machine": {
            "platform": platform.platform(),
            "architecture": platform.machine(),
        },
        "embedding": embedder.metadata,
        "checkpoint": checkpoint_inventory(),
        "extractor": preflight["extractor"],
        "sources": [
            {
                key: row[key]
                for key in (
                    "id",
                    "source_file_sha256",
                    "window_inventory_sha256",
                    "windows",
                )
            }
            for row in preflight["datasets"]
        ],
        "packing": "unchanged Backend.search packaging with precomputed candidate/vector adapters, shared global eight/12000/4096 and current dependency expansion",
        "timing": "in-process development intervals include direct model calls/retrieval/fusion/packing, not HTTP latency or provider usage",
        "query_vector_reuse": "none between conditions; only reuse each already-observed full vector when invoking the unchanged packer",
        "model_calls_in_freeze": 0,
        "weights_loaded_in_freeze": False,
        "limitations": [
            "Both corpora and all labels were already observed; no new holdout, pretraining holdout or generalization claim.",
            "One attempt per query/condition; model/order/cache variance and extra query cost are descriptive.",
            "RRF is a priority score, never cosine similarity or calibrated confidence.",
            "No production/default/ranking/gold/threshold change; H2O off; previous experiments remain untouched.",
        ],
    }
    require(embedder._model is None, "CPU freeze unexpectedly loaded weights")
    return entries, preflight, frozen


class PreparedEmbedder:
    """Reuses the just-observed vector only inside the existing packaging call."""

    def __init__(self, original, query, vector):
        self.original, self.query, self.vector, self.reuses = original, query, vector, 0

    @property
    def metadata(self):
        return self.original.metadata

    def embed_query(self, query):
        require(query == self.query, "Packing requested a different query")
        self.reuses += 1
        return self.vector

    def embed_documents(self, texts, **kwargs):
        raise ValueError("Packing attempted document inference after frozen setup")


class PreparedRetriever:
    def __init__(self, vector, pairs):
        self.vector, self.pairs, self.calls = vector, pairs, 0

    def search(self, vector, limit):
        require(
            vector is self.vector and limit == 50,
            "Packing changed the frozen vector/candidate depth",
        )
        self.calls += 1
        return list(self.pairs)


def pack_candidates(backend, query, vector, pairs, *, top_full_cosine, score_kind):
    require(backend._policy is None, "Probe requires no abstention threshold")
    original_embedder, original_dense = backend._embedder, backend._dense
    prepared_embedder = PreparedEmbedder(original_embedder, query, vector)
    prepared_dense = PreparedRetriever(vector, pairs)
    try:
        backend._embedder, backend._dense = prepared_embedder, prepared_dense
        response = backend.search(query, retrieval="dense", **SETTINGS)
        require(
            backend._dense is prepared_dense
            and prepared_embedder.reuses == prepared_dense.calls == 1,
            "Packaging did not use exactly the precomputed vector and ranking",
        )
    finally:
        backend._embedder, backend._dense = original_embedder, original_dense
    # This metadata is outside compact_result, so the exact packing/token result
    # is unchanged. Avoid labeling the fused priority as a cosine score.
    response["match_evidence"]["top_cosine"] = top_full_cosine
    response["match_evidence"]["ranking_score_kind"] = score_kind
    response["match_evidence"]["meaning"] = (
        "Raw full-query cosine and ranking priority are not proof of a behavior."
    )
    return response


def vector_record(vector, text):
    import numpy as np

    values = np.asarray(vector)
    require(
        values.ndim == 1 and np.isfinite(values).all(), "Invalid observed query vector"
    )
    return {
        "input": text,
        "sha256": sha(values.tobytes()),
        "dtype": str(values.dtype),
        "shape": list(values.shape),
        "l2_norm": float(np.linalg.norm(values)),
        "values": values.tolist(),
    }


def run_condition(backend, embedder, query, condition, helper, sources, row):
    inputs = [query] if condition == "full_query_dense" else clause_inputs(query)
    row["counts_before"] = embedder.counts()
    row["query_inputs"] = list(inputs)
    vectors, rankings, actual_pairs = [], [], []
    row["query_vectors"] = []
    row["raw_ranked_source_lists"] = []
    row["active_phase"] = "query_embedding"
    started = time.perf_counter()
    phase = time.perf_counter()
    for text in inputs:
        vectors.append(embedder.embed_query(text))
        row["query_vectors"].append(vector_record(vectors[-1], text))
    row["query_embeddings_s"] = time.perf_counter() - phase
    row["active_phase"] = "candidate_retrieval"
    phase = time.perf_counter()
    for text, vector in zip(inputs, vectors):
        pairs = backend._dense.search(vector, 50)
        actual_pairs.append(pairs)
        windows = [helper.snippet_window(snippet, score) for snippet, score in pairs]
        for window in windows:
            helper.verify_window(window, sources)
        rankings.append(windows)
        row["raw_ranked_source_lists"].append(
            {"input": text, "score_kind": "cosine", "windows": windows}
        )
    row["candidate_retrieval_s"] = time.perf_counter() - phase
    row["active_phase"] = "fusion"
    phase = time.perf_counter()
    if condition == "clause_rrf":
        merged = fuse_rankings(rankings)
        snippets = {
            snippet.id: snippet for pairs in actual_pairs for snippet, _ in pairs
        }
        selected = [
            (snippets[window["id"]], window["fusion_priority"]) for window in merged
        ]
        score_kind = "equal_weight_rrf_priority_k60"
    else:
        merged = rankings[0]
        selected = actual_pairs[0]
        score_kind = "cosine"
    row["fusion_s"] = time.perf_counter() - phase
    row["merged_candidates"] = deepcopy(merged)
    row["active_phase"] = "packing"
    phase = time.perf_counter()
    response = pack_candidates(
        backend,
        query,
        vectors[0],
        selected,
        top_full_cosine=actual_pairs[0][0][1] if actual_pairs[0] else None,
        score_kind=score_kind,
    )
    row["packing_s"] = time.perf_counter() - phase
    row["elapsed_inprocess_s"] = time.perf_counter() - started
    from semantic_search.context import compact_result, encoding, serialize_context

    require(
        len(merged) <= 50 and len(response["results"]) <= 8,
        "Global candidate/result cap exceeded",
    )
    require(
        response["total_source_chars"]
        == sum(len(window["source"]) for window in response["results"])
        <= 12000,
        "Source cap/accounting differs",
    )
    actual_tokens = len(
        encoding().encode(
            serialize_context(compact_result(response)), disallowed_special=()
        )
    )
    require(
        actual_tokens == response["token_usage"]["tokens"] <= 4096,
        "Exact production compact token cap differs",
    )
    for window in response["results"]:
        helper.verify_window(window, sources)
    row["response"] = response
    row["score_kind"] = score_kind
    row["source_and_shared_caps_verified"] = True
    row["active_phase"] = "complete"
    row["counts_after"] = embedder.counts()
    require(
        row["counts_after"]["completed_query_calls"]
        - row["counts_before"]["completed_query_calls"]
        == len(inputs),
        "Observed query model calls differ",
    )
    require(
        row["counts_after"]["completed_document_calls"]
        == row["counts_before"]["completed_document_calls"],
        "Query phase unexpectedly embedded documents",
    )
    return response, merged


def summarize(rows, frozen):
    result = {
        "classification": frozen["classification"],
        "complete": len(rows) == 60
        and all(row["status"] == "completed" for row in rows),
        "planned_attempts": 60,
        "attempted": len(rows),
        "completed": sum(row["status"] == "completed" for row in rows),
        "failed": [row["identity"] for row in rows if row["status"] != "completed"],
        "unique_query_denominator": 30,
        "datasets": {},
        "paired_changes": [],
        "limitations": frozen["limitations"],
    }
    for dataset in ("express", "zod"):
        result["datasets"][dataset] = {}
        for condition in CONDITIONS:
            selected = [
                row
                for row in rows
                if row["identity"]["dataset"] == dataset
                and row["identity"]["condition"] == condition
                and row["status"] == "completed"
            ]
            positive = [row for row in selected if row["kind"] == "present"]
            negative = [row for row in selected if row["kind"] == "absent"]
            multi = [row for row in positive if row["multi_file_and"]]
            result["datasets"][dataset][condition] = {
                "completed_queries": len(selected),
                "present_queries": len(positive),
                "absent_queries": len(negative),
                "candidate_complete": sum(
                    None not in row["candidate_evidence_ranks"] for row in positive
                ),
                "delivered_complete": sum(
                    None not in row["evidence_ranks"] for row in positive
                ),
                "required_groups": sum(len(row["evidence_ranks"]) for row in positive),
                "delivered_groups": sum(
                    sum(rank is not None for rank in row["evidence_ranks"])
                    for row in positive
                ),
                "multi_file_queries": len(multi),
                "multi_file_complete": sum(
                    None not in row["evidence_ranks"] for row in multi
                ),
                "absent_with_decoys": sum(
                    bool(row["response"]["results"]) for row in negative
                ),
                "elapsed_inprocess_s": sum(
                    row["elapsed_inprocess_s"] for row in selected
                ),
                "observed_query_calls": sum(
                    row["counts_after"]["completed_query_calls"]
                    - row["counts_before"]["completed_query_calls"]
                    for row in selected
                ),
                "misses": [
                    {
                        "query_id": row["identity"]["query_id"],
                        "evidence_ranks": row["evidence_ranks"],
                        "candidate_evidence_ranks": row["candidate_evidence_ranks"],
                    }
                    for row in positive
                    if None in row["evidence_ranks"]
                ],
            }
    for query in frozen["queries"]:
        selected = {
            row["identity"]["condition"]: row
            for row in rows
            if row["identity"]["dataset"] == query["dataset"]
            and row["identity"]["query_id"] == query["query_id"]
            and row["status"] == "completed"
        }
        if len(selected) == 2:
            a, b = (selected[condition] for condition in CONDITIONS)
            result["paired_changes"].append(
                {
                    "dataset": query["dataset"],
                    "query_id": query["query_id"],
                    "kind": a["kind"],
                    "baseline_evidence_ranks": a["evidence_ranks"],
                    "fusion_evidence_ranks": b["evidence_ranks"],
                    "baseline_candidate_ranks": a["candidate_evidence_ranks"],
                    "fusion_candidate_ranks": b["candidate_evidence_ranks"],
                    "baseline_s": a["elapsed_inprocess_s"],
                    "fusion_s": b["elapsed_inprocess_s"],
                }
            )
    return result


def _run(output):
    from semantic_search.backend import SearchBackend
    from semantic_search.embeddings import QwenEmbedder

    helper = language_helpers()
    frozen = json.loads((output / "frozen-plan.json").read_text())
    require(
        not (output / "attempts.jsonl").exists()
        and not (output / "summary.json").exists(),
        "Refusing to repeat/resume/overwrite an attempted probe",
    )
    entries, _, current = freeze_inputs()
    validate_plan(current, frozen)
    write(
        output / "run-verification.json",
        {
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "weights": verify_weights(frozen["checkpoint"]),
            "semantic_module_origins": module_origins(),
            "offline_tokenizer": tokenizer_cache()[1],
            "source_sha256": source_hashes(),
            "frozen_plan_sha256": sha((output / "frozen-plan.json").read_bytes()),
        },
    )

    class ObservedEmbedder(QwenEmbedder):
        def __init__(self):
            super().__init__(progress=True)
            self.attempted_document_calls = self.completed_document_calls = (
                self.document_texts
            ) = 0
            self.attempted_query_calls = self.completed_query_calls = 0
            self.observed_query_inputs = []

        def embed_documents(self, texts, *, cancel_check=None):
            self.attempted_document_calls += 1
            result = super().embed_documents(texts, cancel_check=cancel_check)
            self.completed_document_calls += 1
            self.document_texts += len(texts)
            return result

        def embed_query(self, query):
            self.attempted_query_calls += 1
            self.observed_query_inputs.append(query)
            result = super().embed_query(query)
            self.completed_query_calls += 1
            return result

        def counts(self):
            return {
                key: deepcopy(getattr(self, key))
                for key in (
                    "attempted_document_calls",
                    "completed_document_calls",
                    "document_texts",
                    "attempted_query_calls",
                    "completed_query_calls",
                    "observed_query_inputs",
                )
            }

    rows, setups = [], []
    with (output / "attempts.jsonl").open("x") as stream:
        try:
            for entry, dataset, sources in entries:
                db = (
                    ROOT
                    / ".cache/indexes"
                    / f"{output.name}-{entry['id']}-clause-development.sqlite3"
                )
                require(
                    not any(
                        path.exists()
                        for path in (db, Path(str(db) + "-wal"), Path(str(db) + "-shm"))
                    ),
                    "Fresh development index already exists",
                )
                embedder = ObservedEmbedder()
                backend = SearchBackend(
                    ROOT / entry["corpus"],
                    entry["source_root"],
                    db,
                    embedder=embedder,
                    source_exclusions=entry["source_exclusions"],
                )
                setup = {
                    "dataset": entry["id"],
                    "counts_before_setup": embedder.counts(),
                }
                try:
                    phase = time.perf_counter()
                    setup["indexstats"] = backend.refresh()
                    setup["cold_index_s"] = time.perf_counter() - phase
                    setup["counts_after_setup"] = embedder.counts()
                    setup["memory_after_setup"] = helper.footprint()
                    for query in dataset["queries"]:
                        identities = [
                            identity
                            for identity in frozen["schedule"]
                            if identity["dataset"] == entry["id"]
                            and identity["query_id"] == query["id"]
                        ]
                        for identity in identities:
                            require(
                                identity == frozen["schedule"][len(rows)],
                                "Attempt differs from frozen inventory/order",
                            )
                            row = {
                                "identity": deepcopy(identity),
                                "query": query["query"],
                                "status": "started",
                                "classification": "observed development",
                            }
                            before = embedder.counts()
                            started = time.perf_counter()
                            failure_error = None
                            try:
                                response, candidates = run_condition(
                                    backend,
                                    embedder,
                                    query["query"],
                                    identity["condition"],
                                    helper,
                                    sources,
                                    row,
                                )
                                # Gold enters only after candidate ordering and compact packing are final.
                                gold = helper.groups(query)
                                row.update(
                                    {
                                        "kind": query["kind"],
                                        "multi_file_and": len(gold) > 1
                                        and len(
                                            {
                                                target["path"]
                                                for group in gold
                                                for target in group
                                            }
                                        )
                                        > 1,
                                        "candidate_evidence_ranks": helper.evidence_ranks(
                                            candidates, gold, sources
                                        ),
                                        "evidence_ranks": helper.evidence_ranks(
                                            response["results"], gold, sources
                                        ),
                                        "status": "completed",
                                    }
                                )
                            except BaseException as error:
                                failure_error = error
                                row.update(
                                    {
                                        "status": "failed",
                                        "error_type": type(error).__name__,
                                        "error": str(error),
                                        "counts_before": before,
                                        "counts_after": embedder.counts(),
                                        "elapsed_until_failure_s": time.perf_counter()
                                        - started,
                                    }
                                )
                            rows.append(publication(row))
                            stream.write(
                                json.dumps(
                                    rows[-1], ensure_ascii=False, allow_nan=False
                                )
                                + "\n"
                            )
                            stream.flush()
                            write(
                                output / "summary.json",
                                publication(summarize(rows, frozen)),
                            )
                            print(
                                f"{entry['id']} {query['id']} {identity['condition']} {row['status']}",
                                flush=True,
                            )
                            if failure_error is not None:
                                raise failure_error
                    setup["counts_after_queries"] = embedder.counts()
                    setup["memory_after_queries"] = helper.footprint()
                finally:
                    backend.close()
                    setup["after_close"] = {
                        "backend_status": backend.status(),
                        "weights_resident": embedder._model is not None,
                        "metal_runtime_resident": embedder._mx is not None,
                        "backend_close_returned": True,
                        "index_lifecycle": "SQLiteIndex uses per-operation connections; close is an observed successful backend call",
                    }
                    require(
                        backend.status()["closed"]
                        and embedder._model is None
                        and embedder._mx is None,
                        "Model/backend failed to close",
                    )
                    setup["counts_at_close"] = embedder.counts()
                    if db.exists():
                        setup["closed_database_sha256"] = sha(db.read_bytes())
                    setups.append(publication(setup))
                    write(output / "setups.json", setups)
            require(
                source_hashes() == frozen["source_sha256"],
                "Implementation changed during probe",
            )
            result = summarize(rows, frozen)
            require(result["complete"], "Incomplete frozen schedule")
            result["attempts_sha256"] = sha((output / "attempts.jsonl").read_bytes())
            write(output / "summary.json", publication(result))
        except BaseException as error:
            write(
                output / "failure.json",
                publication(
                    {
                        "error_type": type(error).__name__,
                        "error": str(error),
                        "attempted": len(rows),
                        "pending": frozen["schedule"][len(rows) :],
                        "retry": False,
                    }
                ),
            )
            failed_summary = summarize(rows, frozen)
            failed_summary["complete"] = False
            failed_summary["run_failure"] = {
                "error_type": type(error).__name__,
                "error": str(error),
            }
            write(output / "summary.json", publication(failed_summary))
            raise


def run(output):
    require(
        (output / "frozen-plan.json").is_file(),
        "CPU freeze required before explicit run",
    )
    require(
        not (output / "run-start.json").exists(),
        "Refusing to retry an attempted development run",
    )
    with (output / "run-start.json").open("x") as stream:
        json.dump(
            {
                "started_at_utc": datetime.now(timezone.utc).isoformat(),
                "classification": "Explicit local inference development attempt",
            },
            stream,
        )
        stream.write("\n")
    try:
        with pinned_tokenizer():
            _run(output)
    except BaseException as error:
        if not (output / "failure.json").exists():
            write(
                output / "failure.json",
                publication(
                    {
                        "phase": "pre-model setup gate",
                        "error_type": type(error).__name__,
                        "error": str(error),
                        "attempted_conditions": 0,
                        "model_calls": 0,
                        "retry": False,
                    }
                ),
            )
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if args.freeze:
        require(
            not output.exists(), "Refusing to overwrite an existing development freeze"
        )
        _, preflight, frozen = freeze_inputs()
        output.mkdir(parents=True, exist_ok=False)
        frozen["frozen_at_utc"] = datetime.now(timezone.utc).isoformat()
        frozen["source_commit"] = subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
        ).strip()
        write(output / "CPU-preflight.json", publication(preflight))
        write(output / "frozen-plan.json", frozen)
        print(
            json.dumps(
                {
                    "frozen": True,
                    "model_calls": 0,
                    "weights_loaded": False,
                    "planned_attempts": 60,
                    "query_model_calls": frozen["planned_query_embedding_calls"],
                }
            )
        )
    else:

        def interrupted(signum, frame):
            raise KeyboardInterrupt("Owned development run received SIGTERM")

        previous = signal.signal(signal.SIGTERM, interrupted)
        try:
            run(output)
        finally:
            signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    main()
