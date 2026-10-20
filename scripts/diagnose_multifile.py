#!/usr/bin/env python3
"""CPU stage attribution for retained source-window orders; never retrieves or embeds."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
from unittest.mock import patch

from report_language_quality import groups, physical_lines, rank_groups, verify_window
from semantic_search import context
from semantic_search.corpus import Snippet
from semantic_search.relations import ReferenceGraph

ROOT = Path(__file__).resolve().parents[1]
STAGES = ("retrieved", "ranked", "deduplicated", "eligible", "anchors", "expanded",
          "character_packed", "token_packed")
LOSSES = ("upstream", "shortlist", "deduplication", "result_cap", "dependency_displacement",
          "character_clipping", "token_clipping")
CACHE_URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
CACHE_KEY = hashlib.sha1(CACHE_URL.encode()).hexdigest()
CACHE_SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def window(item, *, source=None):
    snippet, score, origin, related = item
    text = snippet.text if source is None else source
    return {"id": snippet.id, "path": snippet.path, "symbol": snippet.symbol,
            "start_line": snippet.start_line,
            "end_line": snippet.start_line + len(physical_lines(text)) - 1,
            "window_end_line": snippet.end_line, "source": text,
            "content_hash": snippet.content_hash, "source_hash": digest(text.encode()),
            "retrieval_score": float(score), "h2o_score": None, "origin": origin,
            "related_to": related, "truncated": text != snippet.text}


def trace_selection(pairs, graph, template, *, retrieved=None, max_results=8,
                    include_dependencies=True, source_budget=12000, token_budget=4096):
    """Replay the existing selector, exposing each boundary without consulting gold.

    ``pairs`` is the ordered scored/selected candidate prefix; ``retrieved`` may
    supply the earlier full pool. No rank, graph, source or model is recomputed.
    The graph supplies only its ordinary ``dependencies(snippet, limit=2)`` API.
    """
    require(type(max_results) is int and max_results >= 1, "Invalid result cap")
    require(type(include_dependencies) is bool, "Invalid dependency flag")
    require(type(source_budget) is int and source_budget >= 0, "Invalid source budget")
    require(type(token_budget) is int and token_budget >= 1, "Invalid token budget")
    pairs = list(pairs)
    raw = list(pairs if retrieved is None else retrieved)
    require(all(type(score) in (int, float) and math.isfinite(score)
                for _, score in [*raw, *pairs]), "Invalid ranking score")
    raw_ids = {snippet.id for snippet, _ in raw}
    require(all(snippet.id in raw_ids for snippet, _ in pairs), "Ranked window is outside retrieval")
    distinct, seen = [], set()
    for snippet, score in pairs:
        key = snippet.path, snippet.symbol
        if key not in seen:
            distinct.append((snippet, score, "retrieval", None))
            seen.add(key)
    eligible = distinct[:max_results]
    anchors = eligible[:min(5, max_results)]
    expanded = list(eligible)
    if include_dependencies and graph is not None:
        expanded = list(anchors)
        included = {(snippet.path, snippet.symbol) for snippet, *_ in anchors}
        for snippet, score, *_ in anchors:
            for related in graph.dependencies(snippet, limit=2):
                key = related.path, related.symbol
                if key not in included and len(expanded) < max_results:
                    expanded.append((related, score, "dependency", snippet.id))
                    included.add(key)
        for item in eligible:
            key = item[0].path, item[0].symbol
            if key not in included and len(expanded) < max_results:
                expanded.append(item)
                included.add(key)
    remaining, character_rows = source_budget, []
    for item in expanded:
        prefix, used = [], 0
        for line in physical_lines(item[0].text):
            if used + len(line) > remaining:
                break
            prefix.append(line)
            used += len(line)
        text = "".join(prefix)
        if text:
            remaining -= len(text)
            character_rows.append(window(item, source=text))
    response = deepcopy(template)
    response.pop("token_usage", None)
    response["results"] = character_rows
    response["total_source_chars"] = source_budget - remaining
    response["budget_exhausted"] = (any(row["truncated"] for row in character_rows)
                                      or len(character_rows) < len(expanded))
    final = context.apply_token_budget(response, token_budget)
    stages = {"retrieved": [window((s, score, "retrieval", None)) for s, score in raw],
              "ranked": [window((s, score, "retrieval", None)) for s, score in pairs],
              "deduplicated": [window(item) for item in distinct],
              "eligible": [window(item) for item in eligible],
              "anchors": [window(item) for item in anchors],
              "expanded": [window(item) for item in expanded],
              "character_packed": character_rows, "token_packed": final["results"]}
    return {"stages": stages, "response": final}


def attribute_groups(trace, gold):
    """Assign each final missing group once, after any dependency recovery.

    AND groups are scored separately; alternatives inside a group are OR.
    Credit requires a full executable span in one excerpt, irrespective of its
    symbol label. The five-anchor intermediate stage is descriptive: later
    anchors may return, so its temporary omissions are not counted as losses.
    """
    ranks = {name: rank_groups(trace["stages"][name], gold) for name in STAGES}
    transitions = (("character_packed", "token_clipping"),
                   ("expanded", "character_clipping"),
                   ("eligible", "dependency_displacement"),
                   ("deduplicated", "result_cap"), ("ranked", "deduplication"),
                   ("retrieved", "shortlist"))
    rows = []
    for index in range(len(gold)):
        cause = "delivered" if ranks["token_packed"][index] is not None else next(
            (reason for stage, reason in transitions if ranks[stage][index] is not None), "upstream")
        rows.append({"group": index + 1, "outcome": cause,
                     "ranks": {name: ranks[name][index] for name in STAGES}})
    return rows


def _build_language_report(study, project_root=ROOT, *, corpus_root=None):
    """Diagnose all first-repetition language-study cases with an exact baseline gate."""
    corpus_root = project_root if corpus_root is None else corpus_root
    frozen = json.loads((study / "frozen-config.json").read_text())
    preflight = json.loads((study / "extractor-preflight.json").read_text())
    manifest_path = project_root / "benchmarks/language_quality_manifest.json"
    require(digest(manifest_path.read_bytes()) == frozen["manifest_sha256"], "Label manifest changed")
    manifest = json.loads(manifest_path.read_text())
    rows = [json.loads(line) for line in (study / "searches.jsonl").read_text().split("\n") if line]
    rows = [row for row in rows if row["repetition"] == 1]
    require(len(rows) == 90 and len({(r["dataset"], r["query_id"], r["condition"]) for r in rows}) == 90,
            "Expected the 90 unique original first-repetition cases")
    snapshots = {row["id"]: row for row in frozen["sources"]}
    inventories = {row["id"]: row for row in preflight["datasets"]}
    fixtures = {}
    bindings = {str(path.relative_to(project_root)): digest(path.read_bytes()) for path in
                (manifest_path, study / "frozen-config.json", study / "extractor-preflight.json",
                 study / "searches.jsonl")}
    for entry in manifest["datasets"]:
        name = entry["id"]
        label_path = project_root / entry["dataset"]
        require(digest(label_path.read_bytes()) == entry["sha256"], "Dataset labels changed")
        bindings[entry["dataset"]] = entry["sha256"]
        queries = {q["id"]: q for q in json.loads(label_path.read_text())["queries"]}
        repo = (corpus_root / entry["corpus"]).resolve()
        sources = {}
        require(inventories[name]["source_file_sha256"] == snapshots[name]["source_file_sha256"],
                "Source snapshot manifests differ")
        for relative, expected in snapshots[name]["source_file_sha256"].items():
            path = (repo / relative).resolve()
            require(path.is_relative_to(repo), "Source path escaped corpus")
            data = path.read_bytes()
            require(digest(data) == expected, "Pinned source changed: " + relative)
            sources[relative] = data.decode("utf-8")
        lines = {path: physical_lines(text) for path, text in sources.items()}
        inventory = {row["id"]: row for row in inventories[name]["window_inventory"]}
        snippets = {}
        for identifier, row in inventory.items():
            text = "".join(lines[row["path"]][row["start_line"] - 1:row["end_line"]])
            require(digest(text.encode()) == row["source_hash"], "Frozen window bytes differ")
            snippets[identifier] = Snippet(identifier, row["path"], row["symbol"],
                                           row["start_line"], row["end_line"], text, row["source_hash"])
        for query in queries.values():
            for alternatives in groups(query):
                for target in alternatives:
                    lo, hi = target["evidence_lines"]
                    require(digest("".join(lines[target["path"]][lo - 1:hi]).encode())
                            == target["evidence_sha256"], "Executable gold bytes differ")
        fixtures[name] = queries, inventory, snippets, lines, ReferenceGraph(sources, snippets.values())
    cases = []
    for row in rows:
        queries, inventory, snippets, lines, graph = fixtures[row["dataset"]]
        query = queries[row["query_id"]]
        for item in row["candidate_windows"]:
            verify_window(item, inventory, lines, delivered=False)
        for item in row["response"]["results"]:
            verify_window(item, inventory, lines, delivered=True)
        pairs = [(snippets[item["id"]], item["retrieval_score"]) for item in row["candidate_windows"]]
        trace = trace_selection(pairs, graph, row["response"], **{
            key: frozen["settings"][key] for key in
            ("max_results", "include_dependencies", "source_budget", "token_budget")})
        require(context.serialize_context(context.compact_result(trace["response"]))
                == context.serialize_context(context.compact_result(row["response"])),
                "Baseline compact response differs: " + row["query_id"] + "/" + row["condition"])
        gold = groups(query)
        require(rank_groups(trace["stages"]["retrieved"], gold) == row["candidate_evidence_ranks"]
                and rank_groups(trace["stages"]["token_packed"], gold) == row["evidence_ranks"],
                "Recorded group ranks differ")
        cases.append({"dataset": row["dataset"], "query_id": row["query_id"],
                      "condition": row["condition"], "kind": query["kind"],
                      "multi_file": row["multi_file_and"], "groups": attribute_groups(trace, gold),
                      "stage_counts": {name: len(trace["stages"][name]) for name in STAGES}})
    counts = Counter(group["outcome"] for case in cases for group in case["groups"])
    return {"classification": "Post-output CPU stage attribution on already observed language outcomes",
            "baseline_compact_responses_exact": len(cases), "positive_cases": sum(c["kind"] == "present" for c in cases),
            "missing_required_groups": sum(counts[cause] for cause in LOSSES),
            "exclusive_group_outcomes": {cause: counts[cause] for cause in ("delivered", *LOSSES)},
            "cases": cases, "input_sha256": bindings,
            "producer_sha256": digest(Path(__file__).read_bytes()),
            "limits": ["No retrieval, embeddings, reranking, service, network or speed comparison.",
                       "Exclusive stage losses describe this exact stream, not causal model mechanisms.",
                       "A dependency may restore evidence absent from an earlier stage; final losses are assigned after that recovery."]}


def build_language_report(study, project_root=ROOT, *, corpus_root=None):
    """Use only verified existing tokenizer bytes, with no download fallback."""
    import tiktoken.load
    cache = Path(os.environ.get("TIKTOKEN_CACHE_DIR", project_root / ".cache/tokenizers"))
    rank_bytes = (cache / CACHE_KEY).read_bytes()
    require(digest(rank_bytes) == CACHE_SHA256, "Pinned tokenizer cache differs")

    def captured(url, expected_hash=None):
        require(url == CACHE_URL and expected_hash == CACHE_SHA256, "Unexpected tokenizer resource")
        return rank_bytes

    with patch.object(tiktoken.load, "read_file_cached", captured):
        return _build_language_report(study, project_root, corpus_root=corpus_root)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--study", type=Path)
    parser.add_argument("--corpus-root", type=Path)
    parser.add_argument("--tokenizer-cache", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="New source-free JSON report; never overwrite")
    args = parser.parse_args(argv)
    root = args.project_root.resolve()
    if args.tokenizer_cache is not None:
        os.environ["TIKTOKEN_CACHE_DIR"] = str(args.tokenizer_cache.resolve())
    report = build_language_report(args.study or root / "results/language-quality-2026-10-13", root,
                                   corpus_root=args.corpus_root)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps({key: report[key] for key in
                     ("baseline_compact_responses_exact", "missing_required_groups", "exclusive_group_outcomes")}))


if __name__ == "__main__":
    main()
