#!/usr/bin/env python3
"""Profile durable FTS5/sqlite-vec with seeded synthetic CPU vectors, not model quality."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sqlite3
import sys
import time

import numpy as np

from semantic_search.corpus import extract_snippets
from semantic_search.index import document_text
from semantic_search.store import SQLiteIndex


def distribution(samples):
    return {"count": len(samples), "median_s": float(np.median(samples)),
            "p95_s": float(np.percentile(samples, 95, method="linear")),
            "min_s": min(samples), "max_s": max(samples), "samples_s": samples}


def measure(operation):
    start = time.perf_counter()
    value = operation()
    return value, time.perf_counter() - start


class SyntheticEmbedder:
    """Only returns the seeded vectors attached to the generated documents."""

    def __init__(self, snippets, vectors, seed):
        self.metadata = {"id": "synthetic-gaussian-cpu", "revision": str(seed),
                         "dimensions": vectors.shape[1], "normalization": "L2",
                         "model_inference": False}
        self.lookup = {document_text(snippet): vector for snippet, vector in zip(snippets, vectors)}
        self.calls = []
        self.forbid_inference = False

    def embed_documents(self, documents):
        if self.forbid_inference:
            raise AssertionError("Reopening unchanged SQLite must reuse every cached vector")
        self.calls.append(len(documents))
        return np.stack([self.lookup[document] for document in documents])


def generate_fixtures(output, count):
    corpus = output / "corpus"
    sources = {}
    for file_start in range(0, count, 50):
        path = f"src/module_{file_start // 50:04d}.py"
        source = "".join(f"def implementation_{i:05d}(payload):\n"
                         f"    return 'marker{i:05d}', payload\n\n"
                         for i in range(file_start, min(count, file_start + 50)))
        sources[path] = source
        destination = corpus / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source)
    # These generated fixtures are deliberately ignored/untracked. Captured sources
    # bypass Git membership just as the backend's immutable snapshot extraction does.
    snippets = extract_snippets(corpus, "src", max_lines=160, overlap_lines=40, sources=sources)
    if len(snippets) != count:
        raise AssertionError("Generated source did not produce exactly one window per function")
    for snippet in snippets:
        lines = sources[snippet.path].splitlines(keepends=True)
        if snippet.text != "".join(lines[snippet.start_line - 1:snippet.end_line]):
            raise AssertionError("Generated location does not match exact saved source")
    manifest = {path: hashlib.sha256(source.encode()).hexdigest() for path, source in sources.items()}
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    return snippets, digest


def run_profile(output, count=10000, dimensions=1024, searches=30, seed=20261012, top_k=10):
    if not 10 <= count <= 100000 or not 2 <= dimensions <= 4096 or not 5 <= searches <= 100:
        raise ValueError("Require count10–100000, dimensions2–4096, searches5–100")
    if not 1 <= top_k <= min(count, 100):
        raise ValueError("top_k must be1–100 and at most count")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    database = output / "index.sqlite3"
    if database.exists() or (output / "results.json").exists():
        raise ValueError("Choose a fresh output directory; profiling never overwrites an index")
    preparation_started = time.perf_counter()
    snippets, source_manifest = generate_fixtures(output, count)
    random = np.random.default_rng(seed)
    vectors = random.standard_normal((count, dimensions), dtype=np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    embedder = SyntheticEmbedder(snippets, vectors, seed)
    prepared_s = time.perf_counter() - preparation_started
    print(f"Prepared {count} saved synthetic windows and {dimensions}D CPU vectors", flush=True)

    index, create_s = measure(lambda: SQLiteIndex(database))
    (_, build_stats), build_s = measure(lambda: index.refresh(snippets, embedder))
    if embedder.calls != [count] or build_stats["indexed_windows"] != count:
        raise AssertionError("Initial durable build did not index every generated window")
    status = index.status()
    index.close()
    reopened, reopen_s = measure(lambda: SQLiteIndex(database))
    embedder.forbid_inference = True
    (reused, reuse_stats), reuse_s = measure(lambda: reopened.refresh(snippets, embedder))
    if reuse_stats["reused_windows"] != count or reuse_stats["indexed_windows"] != 0:
        raise AssertionError("Reopening failed to reuse all document vectors")
    if not np.array_equal(reused, vectors):
        raise AssertionError("Cached float64 vectors do not preserve the original float32 values")
    del reused
    print(f"Built in {build_s:.3f}s; reopened/reused in {reopen_s + reuse_s:.3f}s", flush=True)

    # Derive the float32 form actually stored, then compute independent NumPy
    # cosine scores with float64 accumulation. Verification is outside timings.
    reference = vectors.astype(np.float64)
    reference /= np.max(np.abs(reference), axis=1, keepdims=True)
    reference /= np.linalg.norm(reference, axis=1, keepdims=True)
    reference = reference.astype(np.float32).astype(np.float64)
    reference /= np.linalg.norm(reference, axis=1, keepdims=True)
    identifiers = np.asarray([snippet.id for snippet in snippets])
    rows, dense_times, lexical_times, broad_lexical_times, errors = [], [], [], [], []
    first_dense = first_lexical = None
    # Every request uses a new SQLite connection and loads its fixed extension.
    # First-use samples are recorded separately; warmups do not enter percentiles.
    for warmup in range(3):
        query = vectors[(warmup * 7919) % count]
        _, seconds = measure(lambda: reopened.search_dense(query, top_k))
        if first_dense is None:
            first_dense = seconds
        _, seconds = measure(lambda: reopened.search_lexical(f"marker{(warmup * 7919) % count:05d}", top_k))
        if first_lexical is None:
            first_lexical = seconds
    for position in range(searches):
        anchor = int(random.integers(count))
        noise = random.standard_normal(dimensions)
        noise /= np.linalg.norm(noise)
        query = vectors[anchor].astype(np.float64) + noise * .2
        query32 = query / np.max(np.abs(query))
        query32 = (query32 / np.linalg.norm(query32)).astype(np.float32).astype(np.float64)
        query32 /= np.linalg.norm(query32)
        scores = reference @ query32
        expected = np.lexsort((identifiers, -scores))[:top_k]
        dense, seconds = measure(lambda: reopened.search_dense(query, top_k))
        dense_times.append(seconds)
        actual = [snippet.id for snippet, _ in dense]
        expected_ids = identifiers[expected].tolist()
        if actual != expected_ids:
            raise AssertionError(f"sqlite-vec top-k differs from NumPy for query{position}")
        score_errors = [abs(score - scores[row]) for (_, score), row in zip(dense, expected)]
        if max(score_errors) > 3e-6:
            raise AssertionError("sqlite-vec cosine differs materially from NumPy")
        errors.extend(score_errors)
        lexical_query = f"marker{anchor:05d}"
        lexical, seconds = measure(lambda: reopened.search_lexical(lexical_query, top_k))
        lexical_times.append(seconds)
        if [snippet.id for snippet, _ in lexical] != [snippets[anchor].id]:
            raise AssertionError("FTS5 did not retrieve the unique generated literal")
        broad, seconds = measure(lambda: reopened.search_lexical("payload implementation", top_k))
        broad_lexical_times.append(seconds)
        if [snippet.id for snippet, _ in broad] != sorted(identifiers.tolist())[:top_k]:
            raise AssertionError("FTS5 common-term ties must resolve by stable ID")
        rows.append({"query_number": position, "anchor_id": snippets[anchor].id,
                     "dense_ids": actual, "expected_numpy_ids": expected_ids,
                     "dense_max_cosine_error": max(score_errors),
                     "lexical_query": lexical_query,
                     "lexical_ids": [snippet.id for snippet, _ in lexical],
                     "broad_lexical_ids": [snippet.id for snippet, _ in broad]})
    reopened.close()
    sizes = {path.name: path.stat().st_size for path in
             (database, Path(str(database) + "-wal"), Path(str(database) + "-shm")) if path.exists()}
    result = {
        "kind": "seeded_synthetic_cpu_index_profile", "model_inference": False,
        "semantic_quality_measurement": False,
        "config": {"windows": count, "dimensions": dimensions, "searches": searches,
                   "seed": seed, "top_k": top_k,
                   "warmups_per_retriever": {"dense": 3, "fts5": 3, "fts5_broad": 0},
                   "fts5_selective_workload": "unique marker literal; one matching row",
                   "fts5_broad_workload": "payload implementation; all windows match",
                   "extraction": {"max_lines": 160, "overlap_lines": 40}},
        "runtime": {"python": sys.version.split()[0], "platform": platform.platform(),
                    "machine": platform.machine(), "numpy": np.__version__,
                    "sqlite": sqlite3.sqlite_version,
                    "sqlite_vec": importlib.metadata.version("sqlite-vec")},
        "fixture_source_manifest": source_manifest,
        "timings": {"fixture_and_vector_preparation_s": prepared_s, "create_schema_s": create_s,
                    "build_s": build_s, "reopen_s": reopen_s, "reuse_refresh_s": reuse_s,
                    "first_dense_s": first_dense, "first_fts5_s": first_lexical,
                    "dense": distribution(dense_times), "fts5": distribution(lexical_times),
                    "fts5_broad": distribution(broad_lexical_times)},
        "build_stats": build_stats, "reuse_stats": reuse_stats, "index_status": status,
        "database_bytes": sizes, "total_database_bytes": sum(sizes.values()),
        "verification": {"exact_top_k_matches_numpy": True,
                         "cosine_max_absolute_error": max(errors),
                         "all_fts5_unique_literal_hits": True,
                         "all_fts5_common_term_orderings_exact": True,
                         "cached_vectors_exactly_reused": True},
        "queries": rows,
        "limitations": ["Synthetic Gaussian CPU vectors do not measure Qwen retrieval quality.",
                        "Dense lookup is an exact SQLite cosine scan, not an approximate vector index.",
                        "Preparation and NumPy reference verification are excluded from query timings.",
                        "SQLite query timings include connection creation and extension loading.",
                        "Fixture source and SQLite database remain in the ignored output directory."],
    }
    (output / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"output": str(output), "dense": result["timings"]["dense"]["median_s"],
                      "fts5": result["timings"]["fts5"]["median_s"],
                      "fts5_broad": result["timings"]["fts5_broad"]["median_s"],
                      "database_bytes": result["total_database_bytes"]}), flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("results/local/sqlite-index-profile"))
    parser.add_argument("--windows", type=int, default=10000)
    parser.add_argument("--dimensions", type=int, default=1024)
    parser.add_argument("--searches", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20261012)
    args = parser.parse_args()
    run_profile(args.output, args.windows, args.dimensions, args.searches, args.seed)
