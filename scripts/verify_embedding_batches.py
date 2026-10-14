#!/usr/bin/env python3
"""Qualify bounded Qwen document batches against fresh singleton forwards.

Uses four original saved Django Python source windows. Selection is CPU-only by default;
--run explicitly loads the local checkpoint and performs real model forwards.
No index, cached vector, query embedding, source edit or text cropping is used.
This research validation driver is not included in the consumer download.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

import numpy as np

from semantic_search.corpus import extract_snippets
from semantic_search.embeddings import DEFAULT_MODEL_PATH, DIMENSIONS, MAX_TOKENS, QwenEmbedder
from semantic_search.index import document_text
from semantic_search.languages import decode_source, validate_exclusions
from semantic_search.source import physical_lines

MAX_PADDED_TOKENS = 8192
MIN_COSINE = 0.99999
MAX_ABSOLUTE_DIFFERENCE = 0.002
FIXTURE_WINDOW_LINES = 480
FIXTURE_OVERLAP_LINES = 120


def digest_ids(ids):
    return hashlib.sha256(json.dumps([int(token) for token in ids], separators=(",", ":")).encode()).hexdigest()


def git_revision(repo):
    result = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, check=False, timeout=10)
    revision = result.stdout.decode("ascii").strip()
    if result.returncode or len(revision) != 40 or any(char not in "0123456789abcdef" for char in revision):
        raise ValueError("Parity qualification requires a Git commit snapshot")
    return revision


def physical_source_check(repo, snippet):
    path = repo / snippet.path
    if not path.is_file() or not path.resolve().is_relative_to(repo):
        raise ValueError("Selected window left the saved repository")
    lines = physical_lines(decode_source(snippet.path, path.read_bytes()))
    actual = "".join(lines[snippet.start_line - 1:snippet.end_line])
    if (not 1 <= snippet.start_line <= snippet.end_line <= len(lines) or actual != snippet.text
            or hashlib.sha256(actual.encode()).hexdigest() != snippet.content_hash):
        raise ValueError("Selected physical source window changed")


def choose_windows(snippets, tokenizer, *, candidate_limit=512):
    """Bound CPU tokenization while selecting actual lengths, without cropping."""
    if type(candidate_limit) is not int or candidate_limit < 4 or candidate_limit > 2048:
        raise ValueError("Candidate limit must be from 4 to 2048")
    # Character lengths only prioritize the bounded sample; actual token IDs,
    # including the production path/symbol header, determine eligibility.
    ranked = []
    for target in (4000, 16000):
        ranked.extend(sorted(snippets, key=lambda item: (abs(len(document_text(item)) - target), item.path, item.start_line, item.id))[:candidate_limit // 2])
    candidates, seen = [], set()
    for item in ranked:
        identity = hashlib.sha256(document_text(item).encode()).hexdigest()
        if identity not in seen:
            candidates.append(item)
            seen.add(identity)
    documents = [document_text(item) for item in candidates]
    encoded = tokenizer(documents, padding=False, truncation=True, max_length=MAX_TOKENS, add_special_tokens=True)["input_ids"]
    if len(encoded) != len(candidates):
        raise ValueError("Tokenizer returned unexpected candidate membership")
    medium, long = [], []
    for item, ids in zip(candidates, encoded):
        row = (item, [int(token) for token in ids])
        if 800 <= len(ids) <= 1600:
            medium.append(row)
        elif 3000 <= len(ids) <= 5000:
            long.append(row)
    medium.sort(key=lambda row: (abs(len(row[1]) - 1000), row[0].path, row[0].start_line))
    long.sort(key=lambda row: (abs(len(row[1]) - 3800), row[0].path, row[0].start_line))
    if len(medium) < 2 or len(long) < 2:
        raise ValueError("Bounded sample lacks two medium and two long original Django windows; increase --candidate-limit")
    # Deliberately interleave lengths to verify restoration of input order.
    chosen = [long[0], medium[0], long[1], medium[1]]
    identities = [digest_ids(ids) for _, ids in chosen]
    if len(set(identities)) != 4 or max(len(ids) for _, ids in chosen) * 4 <= MAX_PADDED_TOKENS:
        raise ValueError("Selection does not exercise distinct variable document batches")
    return chosen, {"candidate_limit": candidate_limit, "tokenized_candidates": len(candidates),
                    "medium_candidates": len(medium), "long_candidates": len(long)}


def compare_vectors(adaptive, singleton):
    adaptive, singleton = np.asarray(adaptive), np.asarray(singleton)
    for vectors in (adaptive, singleton):
        if vectors.shape != (4, DIMENSIONS) or not np.isfinite(vectors).all() or np.any(np.abs(vectors) > 1.000001):
            raise ValueError("Model produced invalid dimensions, nonfinite values or out-of-range normalized components")
        if not np.allclose(np.linalg.norm(vectors.astype(np.float64), axis=1), 1, atol=1e-5, rtol=0):
            raise ValueError("Model vectors are not unit-normalized")
    first, second = adaptive.astype(np.float64), singleton.astype(np.float64)
    cosine = np.sum(first * second, axis=1) / (np.linalg.norm(first, axis=1) * np.linalg.norm(second, axis=1))
    differences = np.max(np.abs(first - second), axis=1)
    if np.min(cosine) < MIN_COSINE or np.max(differences) > MAX_ABSOLUTE_DIFFERENCE:
        raise ValueError("Adaptive document batches differ from fresh singleton forwards beyond qualification bounds")
    return {"cosine_per_input": cosine.tolist(), "minimum_cosine": float(np.min(cosine)),
            "maximum_absolute_difference_per_input": differences.tolist(),
            "maximum_absolute_difference": float(np.max(differences)),
            "required_minimum_cosine": MIN_COSINE, "allowed_maximum_absolute_difference": MAX_ABSOLUTE_DIFFERENCE,
            "finite": True, "unit_normalized": True, "input_order_matches": True}


def model_parity(embedder, chosen):
    texts = [document_text(item) for item, _ in chosen]
    expected = {digest_ids(ids): index for index, (_, ids) in enumerate(chosen)}
    original = embedder._embed_batch
    phase, forwards = "adaptive", []

    def observed_batch(token_ids):
        hashes = [digest_ids(ids) for ids in token_ids]
        if any(value not in expected for value in hashes):
            raise ValueError("Actual model token IDs differ from selected unmodified document inputs")
        lengths = [len(ids) for ids in token_ids]
        padded = len(lengths) * max(lengths)
        if len(lengths) > 4 or padded > MAX_PADDED_TOKENS:
            raise ValueError("Actual model forward exceeded the document or padded-token limit")
        before = time.perf_counter()
        vectors = original(token_ids)
        forwards.append({"phase": phase, "input_positions": [expected[value] for value in hashes],
                         "token_counts": lengths, "token_ids_sha256": hashes, "padded_tokens": padded,
                         "elapsed_seconds": time.perf_counter() - before})
        return vectors

    embedder._embed_batch = observed_batch
    try:
        embedder._ensure_loaded()
        embedder._mx.clear_cache()
        started = time.perf_counter()
        adaptive = embedder.embed_documents(texts)
        adaptive_seconds = time.perf_counter() - started
        embedder._mx.clear_cache()
        phase = "singleton"
        started = time.perf_counter()
        singleton = np.vstack([embedder.embed_documents([text])[0] for text in texts])
        singleton_seconds = time.perf_counter() - started
        embedder._mx.clear_cache()
    finally:
        embedder._embed_batch = original
    grouped = [row for row in forwards if row["phase"] == "adaptive"]
    singles = [row for row in forwards if row["phase"] == "singleton"]
    if (len(grouped) < 2 or not any(1 < len(row["input_positions"]) < 4 for row in grouped)
            or sorted(index for row in grouped for index in row["input_positions"]) != [0, 1, 2, 3]
            or [row["input_positions"] for row in singles] != [[0], [1], [2], [3]]):
        raise ValueError("Actual forwards did not exercise adaptive batches and four fresh singleton documents")
    return {"actual_forward_batches": forwards, "adaptive_forward_count": len(grouped),
            "singleton_forward_count": len(singles), "adaptive_elapsed_seconds": adaptive_seconds,
            "singleton_elapsed_seconds": singleton_seconds, "no_cached_or_query_vectors": True,
            "token_ids_identical_between_conditions": True, "comparison": compare_vectors(adaptive, singleton)}


def qualify(repo, source_root, model_path, exclusions, candidate_limit, run):
    from transformers import AutoTokenizer

    repo, model_path = Path(repo).resolve(), Path(model_path).resolve()
    revision = git_revision(repo)
    if not (repo / "django/__init__.py").is_file():
        raise ValueError("This bounded qualification requires the public Django source tree")
    # These original longer spans exercise memory-bounded model batching. The
    # application's extraction policy is not changed by a qualification fixture.
    snippets = extract_snippets(repo, source_root, max_lines=FIXTURE_WINDOW_LINES,
                                overlap_lines=FIXTURE_OVERLAP_LINES, source_exclusions=exclusions)
    snippets = [item for item in snippets if Path(item.path).suffix == ".py"]
    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True, padding_side="right", truncation_side="right")
    chosen, selection = choose_windows(snippets, tokenizer, candidate_limit=candidate_limit)
    for item, _ in chosen:
        physical_source_check(repo, item)
    if git_revision(repo) != revision:
        raise ValueError("Repository revision changed during window selection")
    report = {"kind": "bounded real-source embedding batch parity qualification; not retrieval quality or coding performance",
              "captured_at_utc": datetime.now(timezone.utc).isoformat(), "git_revision": revision,
              "source_root": source_root, "source_exclusions": list(exclusions), "selection": selection,
              "source_fixture_policy": {"window_lines": FIXTURE_WINDOW_LINES,
                  "overlap_lines": FIXTURE_OVERLAP_LINES, "language": "python",
                  "production_extraction_policy_unchanged": True},
              "max_document_tokens": MAX_TOKENS, "max_padded_tokens": MAX_PADDED_TOKENS, "maximum_documents_per_batch": 4,
              "documents": [{"input_position": index, "path": item.path, "symbol": item.symbol,
                  "start_line": item.start_line, "end_line": item.end_line, "source_sha256": item.content_hash,
                  "document_sha256": hashlib.sha256(document_text(item).encode()).hexdigest(),
                  "token_count": len(ids), "token_ids_sha256": digest_ids(ids)} for index, (item, ids) in enumerate(chosen)],
              "physical_source_unchanged": True, "original_documents_not_cropped": True, "actual_model_run": run}
    if run:
        embedder = QwenEmbedder(model_path=model_path, batch_size=4, max_padded_tokens=MAX_PADDED_TOKENS, progress=False)
        before_metadata = embedder.metadata
        try:
            report["parity"] = model_parity(embedder, chosen)
            if embedder.metadata != before_metadata:
                raise ValueError("Embedding metadata changed with model lifecycle")
            report["embedding_metadata"] = embedder.metadata
        finally:
            embedder.close()
        for item, _ in chosen:
            physical_source_check(repo, item)
        if git_revision(repo) != revision:
            raise ValueError("Repository revision changed during model forwards")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--source-root", default="django")
    parser.add_argument("--model-path", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--exclude-source", action="append", default=["django/views/templates/*"])
    parser.add_argument("--candidate-limit", type=int, default=512)
    parser.add_argument("--run", action="store_true", help="Explicitly perform real adaptive and fresh singleton model forwards")
    parser.add_argument("--output", type=Path, required=True, help="New receipt in an existing directory")
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink() or not args.output.parent.is_dir():
        parser.error("Choose a new receipt filename in an existing directory")
    if not 4 <= args.candidate_limit <= 2048:
        parser.error("Candidate limit must be from 4 to 2048")
    exclusions = validate_exclusions(args.exclude_source)
    report = qualify(args.repo, args.source_root, args.model_path, exclusions, args.candidate_limit, args.run)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n")
    print("Real adaptive and singleton document forwards qualified." if args.run else "CPU-only source windows selected; model was not loaded. Add --run for parity qualification.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
