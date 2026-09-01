"""Compare retrieval coverage and H2O prefixes without changing the frozen queries."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import statistics
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from copy import deepcopy

from semantic_search.corpus import extract_snippets
from semantic_search.experiment import matches, percentile, rank_of, runtime_provenance, unique_symbols
from semantic_search.index import build_or_load_index
from semantic_search.model import H2OClient, INSTRUCTIONS, RUBRIC
from semantic_search.retrieval import BM25Retriever, DenseRetriever, HybridRetriever

SHORTLIST_SIZES = (1, 2, 3, 4, 5, 8, 10)
ROOT = Path(__file__).resolve().parents[2]


def machine_provenance():
    return {"platform": platform.platform(), "processor": platform.machine(),
            "hardware": subprocess.check_output(["sysctl", "-n", "hw.model"], text=True).strip(),
            "memory_bytes": int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True))}


def metrics_for_ranks(ranks):
    count = len(ranks)
    if not count:
        raise ValueError("Metrics require present queries")
    return {"queries": count, "hit_at_1": sum(rank == 1 for rank in ranks) / count,
            "hit_at_3": sum(rank is not None and rank <= 3 for rank in ranks) / count,
            "hit_at_5": sum(rank is not None and rank <= 5 for rank in ranks) / count,
            "mrr": sum(1 / rank if rank else 0 for rank in ranks) / count}


def rerank_prefix(pairs, judgments, size):
    prefix = pairs[:size]
    return sorted(prefix, key=lambda item: (-judgments[item[0].id].score, -item[1], item[0].id))


def prepare_retrieval(repo, dataset_path, output_dir, embedder, cache_dir):
    dataset_bytes = dataset_path.read_bytes()
    dataset = json.loads(dataset_bytes)
    revision = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    if revision != dataset["revision"] or subprocess.check_output(
            ["git", "-C", str(repo), "status", "--porcelain"], text=True).strip():
        raise ValueError("Corpus must be clean and match the dataset revision")
    if not any(q["kind"] == "present" for q in dataset["queries"]):
        raise ValueError("Dataset requires present queries")
    output_dir.mkdir(parents=True, exist_ok=False)
    snippets = extract_snippets(repo, dataset["source_root"], max_lines=160, overlap_lines=40)
    vectors, index_info = build_or_load_index(snippets, embedder, cache_dir)
    lexical = BM25Retriever(snippets)
    dense = DenseRetriever(snippets, vectors)
    hybrid = HybridRetriever(lexical, dense, rrf_k=60, fetch_depth=50)
    rows, candidates = [], {}
    for number, query in enumerate(dataset["queries"], 1):
        started = time.perf_counter()
        vector = embedder.embed_query(query["query"])
        embedding_s = time.perf_counter() - started
        ranked = {"bm25": lexical.search(query["query"], 50),
                  "dense": dense.search(vector, 50),
                  "hybrid": hybrid.search(query["query"], vector, 50)}
        row = {"id": query["id"], "query": query["query"], "kind": query["kind"],
               "query_embedding_s": embedding_s, "rankings": {}, "ranks": {}, "coverage": {}}
        for name, pairs in ranked.items():
            row["rankings"][name] = [{"id": s.id, "score": float(score)} for s, score in pairs]
            row["ranks"][name] = rank_of(unique_symbols([s for s, _ in pairs]), query["targets"])
            row["coverage"][name] = {str(k): any(matches(s, target) for s, _ in pairs[:k]
                                                        for target in query["targets"])
                                      for k in SHORTLIST_SIZES}
        rows.append(row)
        candidates[query["id"]] = ranked["hybrid"]
        print(f"[{number}/{len(dataset['queries'])}] retrieval {query['id']}: "
              f"BM25={row['ranks']['bm25']} dense={row['ranks']['dense']} "
              f"hybrid={row['ranks']['hybrid']} embed={embedding_s:.3f}s", flush=True)
    present = [row for row in rows if row["kind"] == "present"]
    result = {"schema_version": 1, "experiment": "qwen-hybrid-h2o-shortlist",
              "corpus": {"url": dataset["repo_url"], "revision": revision,
                         "source_root": dataset["source_root"], "snippets": len(snippets)},
              "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
              "embedding": embedder.metadata,
              "machine": machine_provenance(),
              "index": {key: value for key, value in index_info.items() if key != "manifest"},
              "settings": {"max_lines": 160, "overlap_lines": 40, "include_docstrings": True,
                           "rrf_k": 60, "rrf_weights": [1, 1], "fetch_depth": 50,
                           "shortlist_sizes": list(SHORTLIST_SIZES),
                           "shortlist_unit": "source windows, one H2O call per window",
                           "output": "only scored prefix, then highest-ranked window per symbol",
                           "selection_rule": "smallest K with maximum prefix coverage and hit@1 >= K=10"},
              "retrieval_metrics": {name: metrics_for_ranks([row["ranks"][name] for row in present])
                                    for name in ("bm25", "dense", "hybrid")},
              "candidate_coverage": {name: {str(k): sum(row["coverage"][name][str(k)] for row in present)
                                                       / len(present) for k in SHORTLIST_SIZES}
                                     for name in ("bm25", "dense", "hybrid")},
              "queries": rows,
              "limitations": ["Same 12 developer-authored Flask queries; no held-out claim.",
                              "No tuning of query text, labels, H2O rubric, or RRF weights.",
                              "Quality replay may reuse exact judgments; latency runs always perform new inference.",
                              "No unscored candidates are appended to the H2O result list.",
                              "Absent queries are inspected separately; no rejection threshold fitted.",
                              "Warm latency excludes one-time document indexing/model startup.",
                              "No paid-agent token savings measured."]}
    (output_dir / "retrieval.json").write_text(json.dumps(result, indent=2) + "\n")
    (output_dir / "index_manifest.json").write_text(json.dumps(index_info["manifest"], indent=2) + "\n")
    return dataset, snippets, lexical, dense, hybrid, candidates, result


def reusable_judgments(old_run, result, snippets):
    """Reuse quality scores only for identical model/rubric/dataset/source inputs."""
    from semantic_search.model import Judgment
    quality_run = (old_run / "quality.json").exists()
    old = json.loads((old_run / ("quality.json" if quality_run else "results.json")).read_text())
    old_model = old["h2o"] if quality_run else old["model"]
    if (old["dataset_sha256"] != result["dataset_sha256"]
            or old_model["rubric"] != RUBRIC or old_model["instructions"] != INSTRUCTIONS):
        raise ValueError("Prior run cannot be reused: dataset or scoring instructions changed")
    if old_model["model_revision" if quality_run else "revision"] != result["h2o"]["model_revision"]:
        raise ValueError("Prior run model revision differs")
    for key in ("runtime_revision", "config_quantization", "head_dtype"):
        if old_model["provenance"][key] != result["h2o"]["provenance"][key]:
            raise ValueError(f"Prior runtime provenance differs: {key}")
    new_health = result["h2o"]["provenance"]["health_before_run"]["shim"]
    old_health = old_model["provenance"]["health_before_run"]["shim"]
    for key in ("model", "temperature_by_type", "layout", "chat_template", "state_compact",
                "prefill", "max_state_tokens"):
        if old_health[key] != new_health[key]:
            raise ValueError(f"Prior run serving configuration changed: {key}")
    available = {s.id: s for s in snippets}
    cache = {}
    for line in (old_run / ("quality_judgments.jsonl" if quality_run else "judgments.jsonl")).read_text().splitlines():
        record = json.loads(line)
        snippet = available.get(record["snippet"]["id"])
        if snippet is None or record["snippet"] != asdict(snippet):
            continue
        response = record["response"]
        answer = response["answers"]["relevance"]
        judgment = Judgment(record["score"], answer["probabilities"], record["elapsed_s"],
                            record["input_tokens"], response)
        cache[(record["query_id"], snippet.id)] = judgment
    return cache


def quality_sweep(dataset, snippets, candidates, result, output_dir, url, old_run=None,
                  retrieval_name="hybrid", additional_quality_run=None):
    _, provenance = runtime_provenance(url)
    result["h2o"] = {"model_revision": "a1048d02d9e713ba9f7481efa5c3df7ec8a2adaa",
                     "rubric": RUBRIC, "instructions": INSTRUCTIONS, "provenance": provenance}
    reuse = reusable_judgments(old_run, result, snippets) if old_run else {}
    origins = {key: str(old_run) for key in reuse}
    if additional_quality_run:
        additional = reusable_judgments(additional_quality_run, result, snippets)
        reuse.update(additional)
        origins.update({key: str(additional_quality_run) for key in additional})
    client = H2OClient(url)
    result["reranked_retrieval"] = retrieval_name
    output_dir.mkdir(parents=True, exist_ok=True)
    rows, reused, fresh = [], 0, 0
    with (output_dir / "quality_judgments.jsonl").open("w") as audit:
        for number, query in enumerate(dataset["queries"], 1):
            pairs = candidates[query["id"]][:max(SHORTLIST_SIZES)]
            judgments = {}
            for snippet, _ in pairs:
                key = (query["id"], snippet.id)
                if key in reuse:
                    judgment, origin = reuse[key], "prior exact-input judgment"
                    reused += 1
                else:
                    judgment, origin = client.score(query["query"], snippet), "new live judgment"
                    fresh += 1
                judgments[snippet.id] = judgment
                audit.write(json.dumps({"query_id": query["id"], "origin": origin,
                                        "prior_run": origins.get(key),
                                        "snippet": asdict(snippet), **asdict(judgment)}) + "\n")
                audit.flush()
            row = {"id": query["id"], "kind": query["kind"], "by_k": {}}
            for size in SHORTLIST_SIZES:
                ranked = rerank_prefix(pairs, judgments, size)
                retained = unique_symbols([s for s, _ in ranked])
                row["by_k"][str(size)] = {
                    "rank": rank_of(retained, query["targets"]),
                    "candidate_has_gold": any(matches(s, t) for s, _ in pairs[:size] for t in query["targets"]),
                    "retained_has_gold": any(matches(s, t) for s in retained for t in query["targets"]),
                    "order": [s.id for s in retained],
                    "max_h2o_score": max(judgments[s.id].score for s, _ in pairs[:size]),
                }
            rows.append(row)
            print(f"[{number}/{len(dataset['queries'])}] {retrieval_name} quality {query['id']}: "
                  + " ".join(f"K{k}={row['by_k'][str(k)]['rank']}" for k in SHORTLIST_SIZES), flush=True)
    present = [row for row in rows if row["kind"] == "present"]
    result["quality"] = {str(size): {
        **metrics_for_ranks([row["by_k"][str(size)]["rank"] for row in present]),
        "candidate_coverage": sum(row["by_k"][str(size)]["candidate_has_gold"] for row in present) / len(present),
        "retained_coverage": sum(row["by_k"][str(size)]["retained_has_gold"] for row in present) / len(present),
    } for size in SHORTLIST_SIZES}
    result["quality_queries"] = rows
    result["quality_judgment_counts"] = {"reused": reused, "new_live": fresh}
    maximum_coverage = max(item["candidate_coverage"] for item in result["quality"].values())
    full_accuracy = result["quality"][str(max(SHORTLIST_SIZES))]["hit_at_1"]
    result["selected_k"] = next(size for size in SHORTLIST_SIZES
                                if result["quality"][str(size)]["candidate_coverage"] == maximum_coverage
                                and result["quality"][str(size)]["hit_at_1"] >= full_accuracy)
    (output_dir / "quality.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def memory_footprints(pids):
    footprints = {}
    for name, pid in pids.items():
        output = subprocess.check_output(["vmmap", "-summary", str(pid)], text=True)
        fields = {}
        for label, key in (("Physical footprint", "current"), ("Physical footprint (peak)", "peak")):
            match = re.search(r"^" + re.escape(label) + r":\s+(\S+)", output, re.MULTILINE)
            if not match:
                raise RuntimeError(f"No {label} for {name}")
            fields[key] = match.group(1)
        footprints[name] = fields
    return {"tool": "macOS vmmap -summary", "processes": footprints,
            "note": "Includes Metal. Individual rounded peaks since process launch are not a simultaneous combined peak."}


def fresh_latency_sweep(dataset, prepared, quality_by_method, embedder, dense, hybrid,
                        output_dir, url, index_info):
    """Fresh query embedding and scoring for every condition; no judgment cache."""
    runtime_state, provenance = runtime_provenance(url)
    client = H2OClient(url)
    conditions = [("dense", 0), ("hybrid", 0), ("dense", 1), ("hybrid", 1)]
    for name in ("hybrid", "dense"):
        size = quality_by_method[name]["selected_k"]
        conditions += [(name, size)]
        if size > 1:
            conditions += [(name, size - 1)]
    conditions = list(dict.fromkeys(conditions))
    warm_query = dataset["queries"][0]["query"]
    warm_vector = embedder.embed_query(warm_query)
    warm_snippet = hybrid.search(warm_query, warm_vector, 1)[0][0]
    warm_judgment = client.score(warm_query, warm_snippet)
    (output_dir / "warmup.json").write_text(json.dumps({
        "h2o_elapsed_s": warm_judgment.elapsed_s, "model_response": warm_judgment.response,
        "health": provenance, "note": "Warmup is excluded from measured rows."
    }, indent=2) + "\n")
    rows = []
    fixed_orders = {row["id"]: row["rankings"] for row in prepared["queries"]}
    with (output_dir / "live_judgments.jsonl").open("w") as audit:
        for number, query in enumerate(dataset["queries"], 1):
            # Rotate condition order per query to avoid a systematic first-condition advantage.
            shift = (number - 1) % len(conditions)
            for name, size in conditions[shift:] + conditions[:shift]:
                started = time.perf_counter()
                phase_started = time.perf_counter()
                vector = embedder.embed_query(query["query"])
                embedding_s = time.perf_counter() - phase_started
                phase_started = time.perf_counter()
                candidate_limit = size if size else 10
                pairs = (dense.search(vector, candidate_limit) if name == "dense"
                         else hybrid.search(query["query"], vector, candidate_limit))
                expected = [p["id"] for p in fixed_orders[query["id"]][name][:candidate_limit]]
                if [s.id for s, _ in pairs] != expected:
                    raise ValueError(f"Fresh retrieval order changed: {query['id']} {name}")
                retrieval_s = time.perf_counter() - phase_started
                phase_started = time.perf_counter()
                judgments = {snippet.id: client.score(query["query"], snippet) for snippet, _ in pairs} if size else {}
                ranked = rerank_prefix(pairs, judgments, size) if size else pairs
                returned = unique_symbols([s for s, _ in ranked])[:5]
                h2o_s = sum(j.elapsed_s for j in judgments.values())
                ranking_s = time.perf_counter() - phase_started - h2o_s
                elapsed_s = time.perf_counter() - started
                row = {"query_id": query["id"], "kind": query["kind"], "retrieval": name,
                       "k": size, "query_embedding_s": embedding_s, "retrieval_s": retrieval_s,
                       "h2o_s": h2o_s, "ranking_and_client_overhead_s": ranking_s,
                       "elapsed_s": elapsed_s,
                       "candidate_ids": [s.id for s, _ in pairs], "returned_ids": [s.id for s in returned],
                       "rank": rank_of(returned, query["targets"]),
                       "candidate_has_gold": any(matches(s, t) for s, _ in pairs for t in query["targets"]),
                       "max_h2o_score": max((j.score for j in judgments.values()), default=None)}
                # Audit serialization is deliberately outside the timed request interval.
                for snippet, _ in pairs:
                    if snippet.id in judgments:
                        audit.write(json.dumps({"query_id": query["id"], "retrieval": name, "k": size,
                                                "snippet": asdict(snippet), **asdict(judgments[snippet.id])}) + "\n")
                audit.flush()
                rows.append(row)
                print(f"[{number}/{len(dataset['queries'])}] live {name} K{size} {query['id']}: "
                      f"rank={row['rank']} total={elapsed_s:.3f}s", flush=True)
    summary = {}
    for name, size in conditions:
        selected = [row for row in rows if row["retrieval"] == name and row["k"] == size]
        positives = [row for row in selected if row["kind"] == "present"]
        elapsed = [row["elapsed_s"] for row in selected]
        summary[f"{name}:{size}"] = {
            **metrics_for_ranks([row["rank"] for row in positives]),
            "returned_coverage": sum(row["rank"] is not None for row in positives) / len(positives),
            "samples": len(elapsed), "median_s": statistics.median(elapsed),
            "p95_s": percentile(elapsed, .95), "max_s": max(elapsed),
            "median_query_embedding_s": statistics.median(row["query_embedding_s"] for row in selected),
            "median_h2o_s": statistics.median(row["h2o_s"] for row in selected),
        }
    result = {"schema_version": 1, "conditions": [{"retrieval": name, "k": size} for name, size in conditions],
              "warm_models": True, "query_embedding_cache": False, "h2o_judgment_cache": False,
              "output": "scored prefix only; K0 means retrieval-only top5 results",
              "timing_setup_index": {key: value for key, value in index_info.items() if key != "manifest"},
              "dataset_sha256": prepared["dataset_sha256"], "embedding": embedder.metadata,
              "h2o_provenance": provenance, "summary": summary, "rows": rows,
              "memory_footprint": memory_footprints({"embedding_worker": os.getpid(),
                                                    "h2o_facade": runtime_state["pids"][0],
                                                    "h2o_shim": runtime_state["pids"][1]})}
    (output_dir / "latency.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def render_shortlist_report(prepared, quality_by_method, latency):
    lines = ["# Qwen retrieval and H2O shortlist experiment", "",
             "Frozen Flask development pilot: 12 implementation queries and 2 absent features. "
             "Same evidence-qualified source windows and H2O rubric as the previous experiment.", "",
             "Qwen3-Embedding-0.6B uses original BF16 weights, 1024 dimensions, "
             "a fixed behavior-search query instruction, last-token pooling, and local MLX inference.", "",
             f"One-time indexing of {prepared['corpus']['snippets']} windows: {prepared['index']['elapsed_s']:.2f}s.", "",
             "## Retrieval before H2O", "",
             "| Retrieval | Correct first result | Correct within first 5 | Candidate coverage K3 | K4 |", "|---|---:|---:|---:|---:|"]
    for name in ("bm25", "dense", "hybrid"):
        m = prepared["retrieval_metrics"][name]
        c = prepared["candidate_coverage"][name]
        lines.append(f"| {name} | {round(m['hit_at_1']*12)}/12 | {round(m['hit_at_5']*12)}/12 | "
                     f"{round(c['3']*12)}/12 | {round(c['4']*12)}/12 |")
    lines += ["", "Hybrid uses equal-weight reciprocal-rank fusion, rank constant 60, each input depth 50. "
              "This fixed fusion can demote a strong semantic-only match: the dispatch implementation "
              "is dense rank 2 but hybrid rank 24 because BM25 omits it. No fusion weights were tuned on these labels.", "",
              "## Quality sweep (replayed scores)", "",
              "| Retrieval | H2O K | Candidate coverage | Returned coverage | Correct first result |", "|---|---:|---:|---:|---:|"]
    for name, result in quality_by_method.items():
        for size in SHORTLIST_SIZES:
            m = result["quality"][str(size)]
            lines.append(f"| {name} | {size} | {round(m['candidate_coverage']*12)}/12 | "
                         f"{round(m['retained_coverage']*12)}/12 | {round(m['hit_at_1']*12)}/12 |")
    lines += ["", "Quality reuses prior judgments only when source, model revision, serving configuration, "
              "dataset hash and rubric match; missing pairs receive new local inference. This is not a latency measurement. "
              "Every reuse is marked with its originating run in `quality_judgments.jsonl`.", "",
              "## Fresh warm latency", "",
              "Each query is re-embedded and every shortlisted window is freshly scored. Both models are warmed; "
              "condition order rotates by query. Document indexing, model startup, and audit serialization are excluded.", "",
              "| Retrieval | H2O K | Correct first result | Correct in returned results | Median | P95 / max | Samples |", "|---|---:|---:|---:|---:|---:|---:|"]
    for key, m in latency["summary"].items():
        name, size = key.split(":")
        lines.append(f"| {name} | {size} | {round(m['hit_at_1']*12)}/12 | "
                     f"{round(m['returned_coverage']*12)}/12 | {m['median_s']:.3f}s | "
                     f"{m['p95_s']:.3f}s / {m['max_s']:.3f}s | {m['samples']} |")
    lines += ["", "K counts scored source windows, not symbols. Only the scored prefix is returned after "
              "symbol deduplication; K0 retrieves ten windows and returns the first five symbols without H2O. "
              "With 14 samples, nearest-rank P95 equals the slowest sample. These are warm resident-worker "
              "timings, not total CLI invocation times.", "",
              "## Selected shortlists and absent queries", "",
              "Selection chooses the smallest K with the best candidate coverage in the declared grid "
              "and first-result accuracy at least as high as K10. It is applied separately to both methods.", ""]
    for name, result in quality_by_method.items():
        lines.append(f"- {name}: selected K{result['selected_k']}.")
    lines += ["", "| Absent feature | Method | Selected K | Highest H2O score / 3 |", "|---|---|---:|---:|"]
    for name, result in quality_by_method.items():
        k = result["selected_k"]
        for row in result["quality_queries"]:
            if row["kind"] == "absent":
                lines.append(f"| {row['id']} | {name} | {k} | {row['by_k'][str(k)]['max_h2o_score']:.3f} |")
    lines += ["", "The search still returns candidates for absent features; no rejection threshold was fitted.", "",
              "## Memory", ""]
    for name, memory in latency["memory_footprint"]["processes"].items():
        lines.append(f"- {name}: current {memory['current']}, peak {memory['peak']} (`vmmap -summary`, includes Metal).")
    lines += ["", latency["memory_footprint"]["note"], "", "## Limits", "",
              *[f"- {item}" for item in prepared["limitations"]], "",
              "Original comparison: BM25+H2O K10 ranked 11/12 first, "
              "19.29s warm median / 23.90s P95. This run measures new retrieval and smaller prefixes live.", ""]
    return "\n".join(lines)


def run_shortlist_experiment(repo, dataset_path, output_dir, cache_dir, url, prior_run=None):
    """Build/cache documents, replay quality prefixes, then measure fresh searches."""
    from semantic_search.embeddings import QwenEmbedder

    embedder = QwenEmbedder()
    try:
        dataset, snippets, _, dense, hybrid, hybrid_candidates, prepared = prepare_retrieval(
            repo, dataset_path, output_dir, embedder, cache_dir)
        available = {s.id: s for s in snippets}
        dense_candidates = {row["id"]: [(available[pair["id"]], pair["score"])
                                         for pair in row["rankings"]["dense"]]
                            for row in prepared["queries"]}
        quality = {}
        for name, candidates in (("hybrid", hybrid_candidates), ("dense", dense_candidates)):
            quality[name] = quality_sweep(
                dataset, snippets, candidates, deepcopy(prepared), output_dir / name,
                url, old_run=prior_run, retrieval_name=name,
                additional_quality_run=output_dir / "hybrid" if name == "dense" else None)
        # Loading the content-addressed index again proves the live pass uses the
        # exact same manifest; this never re-embeds unchanged documents.
        _, index_info = build_or_load_index(snippets, embedder, cache_dir)
        latency = fresh_latency_sweep(dataset, prepared, quality, embedder, dense, hybrid,
                                      output_dir, url, index_info)
        (output_dir / "report.md").write_text(render_shortlist_report(prepared, quality, latency))
        return latency
    finally:
        embedder.close()
