#!/usr/bin/env python3
"""CPU-only, post-hoc budget and evidence-path analysis of an audited quality run.

Repack the saved HTTP responses; never rerun retrieval or load model weights.
Smaller caps are descriptive reuse, not independently held-out configurations.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys

from audit_quality import load_corpus, read_json, require, sha
from semantic_search.context import apply_token_budget, compact_result, encoding, serialize_context
from semantic_search.experiment import matches
from semantic_search.quality import delivered_snippets, evidence_ranks, summarize, target_groups
from semantic_search.relations import ReferenceGraph
from semantic_search.source import physical_lines

CAPS = (512, 1024, 2048, 4096)
TIMING_FIELDS = frozenset(("timing_samples", "median_s", "p95_s", "max_s"))


def without_timings(metrics):
    """Reuse the frozen quality aggregation, but publish no inherited latency."""
    return {condition: {group: {key: value for key, value in record.items() if key not in TIMING_FIELDS}
                        for group, record in groups.items()} for condition, groups in metrics.items()}


def verified_excerpt_manifest(response, corpus):
    text = serialize_context(compact_result(response))
    actual = len(encoding().encode(text, disallowed_special=()))
    usage = response["token_usage"]
    require(usage["encoding"] == "cl100k_base" and usage["tokens"] == actual
            and actual <= usage["budget"], "Repacked compact-context tokens do not match the cap")
    require(response["total_source_chars"] == sum(len(item["source"]) for item in response["results"]),
            "Repacked source-character counter differs")
    excerpts = []
    for item in response["results"]:
        window = corpus["windows"][item["id"]]
        lines = physical_lines(item["source"])
        require(item["path"] == window.path and item["symbol"] == window.symbol
                and item["start_line"] == window.start_line
                and item["start_line"] <= item["end_line"] <= window.end_line
                and item["end_line"] == item["start_line"] + len(lines) - 1,
                "Repacked excerpt has invalid physical source bounds")
        pinned = "".join(physical_lines(corpus["sources"][item["path"]])[
            item["start_line"] - 1:item["end_line"]])
        require(item["source"] == pinned and item["source_hash"] == sha(pinned.encode("utf-8")),
                "Repacked excerpt differs from pinned source/hash")
        require(item["truncated"] == (item["source"] != window.text), "Incorrect repacked truncation flag")
        excerpts.append({key: item[key] for key in
            ("id", "path", "symbol", "start_line", "end_line", "source_hash", "truncated", "origin", "related_to")})
        excerpts[-1]["source_chars"] = len(item["source"])
    return {"context_sha256": sha(text.encode("utf-8")), "tokens": actual,
            "source_chars": response["total_source_chars"], "excerpts": excerpts}


def selection_path(row, corpus):
    """Reconstruct the frozen rank/dedup/expansion path using source only."""
    candidates = [corpus["windows"][item["id"]] for item in row["candidate_windows"]]
    dedup, seen = [], set()
    for snippet in candidates:
        key = (snippet.path, snippet.symbol)
        if key not in seen:
            seen.add(key)
            dedup.append(snippet)
    if row["response"]["match_status"] == "insufficient_evidence":
        return candidates, dedup, [], {}
    context = row["condition"] in ("dense_context", "dense_context_policy")
    eligible = dedup[:8 if context else 5]
    origins = {snippet.id: ("retrieval", None) for snippet in eligible}
    if context:
        expanded, included = [], set()
        anchors = eligible[:5]
        for snippet in anchors:
            expanded.append(snippet)
            included.add((snippet.path, snippet.symbol))
        for snippet in anchors:
            for related in corpus["references"].dependencies(snippet, limit=2):
                key = (related.path, related.symbol)
                if key not in included and len(expanded) < 8:
                    expanded.append(related)
                    included.add(key)
                    origins[related.id] = ("dependency", snippet.id)
        for snippet in eligible:
            key = (snippet.path, snippet.symbol)
            if key not in included and len(expanded) < 8:
                expanded.append(snippet)
                included.add(key)
        eligible = expanded
    return candidates, dedup, eligible, origins


def evidence_origin(row, group, dedup):
    found = []
    distinct_rank = {snippet.id: rank for rank, snippet in enumerate(dedup, 1)}
    for rank, (item, snippet) in enumerate(zip(row["response"]["results"], delivered_snippets(row["response"])), 1):
        if any(matches(snippet, target) for target in group):
            kind = ("dependency" if item["origin"] == "dependency" else
                    "extra_anchor_slot" if distinct_rank.get(item["id"], 0) > 5 else "first_five_anchor")
            found.append({"rank": rank, "id": item["id"], "origin": item["origin"],
                          "related_to": item["related_to"], "deduplicated_rank": distinct_rank.get(item["id"]),
                          "recovery_kind": kind})
    return found


def context_comparison(rows, corpora):
    by_key = {(row["dataset"], row["query_id"], row["condition"]): row for row in rows}
    changes = []
    for row in rows:
        if row["condition"] != "dense_context" or row["kind"] != "present":
            continue
        baseline = by_key[(row["dataset"], row["query_id"], "dense")]
        if row["evidence_ranks"] == baseline["evidence_ranks"]:
            continue
        corpus = corpora[row["dataset"]]
        groups = target_groups(corpus["queries"][row["query_id"]])
        _, dedup, _, _ = selection_path(row, corpus)
        recovered = [{"group": number + 1, "evidence": evidence_origin(row, group, dedup)}
                     for number, group in enumerate(groups)
                     if baseline["evidence_ranks"][number] is None and row["evidence_ranks"][number] is not None]
        lost = [number + 1 for number in range(len(groups))
                if baseline["evidence_ranks"][number] is not None and row["evidence_ranks"][number] is None]
        changes.append({"dataset": row["dataset"], "query_id": row["query_id"], "role": row["role"],
                        "split": row["split"], "dense_ranks": baseline["evidence_ranks"],
                        "context_ranks": row["evidence_ranks"],
                        "new_complete_query": all(row["evidence_ranks"]) and not all(baseline["evidence_ranks"]),
                        "lost_complete_query": all(baseline["evidence_ranks"]) and not all(row["evidence_ranks"]),
                        "recovered_groups": recovered, "lost_groups": lost})
    origin_counts = Counter(item["recovery_kind"] for change in changes
                            for group in change["recovered_groups"] for item in group["evidence"][:1])
    return {"new_complete_queries": sum(change["new_complete_query"] for change in changes),
            "lost_complete_queries": sum(change["lost_complete_query"] for change in changes),
            "recovered_groups_by_first_delivered_origin": dict(sorted(origin_counts.items())), "changes": changes}


def missing_group_trace(row, query, corpus):
    groups = target_groups(query)
    candidates, dedup, eligible, origins = selection_path(row, corpus)
    stages = [evidence_ranks(windows, groups) for windows in (candidates, dedup, eligible)]
    require(stages[0] == row["candidate_evidence_ranks"], "Candidate evidence differs during path reconstruction")
    expected = {snippet.id for snippet in eligible}
    require(all(item["id"] in expected and (item["origin"], item["related_to"]) == origins[item["id"]]
                for item in row["response"]["results"]), "Saved output differs from reconstructed frozen selection")
    traces = []
    for number, delivered_rank in enumerate(row["evidence_ranks"]):
        if delivered_rank is not None:
            continue
        candidate_rank, dedup_rank, eligible_rank = [stage[number] for stage in stages]
        if row["response"]["match_status"] == "insufficient_evidence":
            cause = "weak_match_rejection"
        elif eligible_rank is not None:
            cause = "original_source_or_token_packing"
        elif candidate_rank is None:
            cause = "outside_candidate50_and_selected_callees"
        elif dedup_rank is None:
            cause = "deduplication_kept_window_without_full_evidence"
        else:
            cause = "outside_selected_anchor_and_callee_slots"
        traces.append({"group": number + 1, "targets": groups[number], "candidate50_rank": candidate_rank,
                       "deduplicated_rank": dedup_rank, "selected_full_window_rank": eligible_rank,
                       "delivered_rank": None, "cause": cause})
    return traces


def analyze(run, manifest_path, root):
    frozen = read_json(run / "frozen-config.json")
    audit = read_json(run / "audit.json")
    require(audit["status"] == "passed", "The original run must first pass audit_quality.py")
    paths = [manifest_path, *[run / name for name in
        ("frozen-config.json", "searches.jsonl", "abstention-policy.json", "results.json")]]
    for path in paths:
        require(audit["inputs_sha256"][path.name] == sha(path.read_bytes()), f"Audited input changed: {path.name}")
    for name in ("context", "quality", "corpus", "relations", "source"):
        filename = f"src/semantic_search/{name}.py"
        require(sha((root / filename).read_bytes()) == frozen["implementation_sha256"][filename],
                f"Analysis helper changed since frozen inference: {filename}")
    require(importlib.metadata.version("tiktoken") == frozen["packages"]["tiktoken"], "Tokenizer pin changed")
    corpora = {}
    for entry in read_json(manifest_path)["datasets"]:
        path = root / entry["dataset"]
        require(sha(path.read_bytes()) == entry["sha256"], f"Dataset changed: {entry['dataset']}")
        dataset = read_json(path)
        corpus = load_corpus(entry, dataset, root, frozen)
        corpus["queries"] = {query["id"]: query for query in dataset["queries"]}
        corpus["references"] = ReferenceGraph(corpus["sources"], list(corpus["windows"].values()))
        corpora[entry["dataset"]] = corpus
    rows = [json.loads(line) for line in (run / "searches.jsonl").read_text().splitlines()]
    require(len(rows) == audit["rows"] == 448, "Unexpected original row count")
    derived, cap_records, excerpts_checked = [], {}, 0
    for cap in CAPS:
        repacked = []
        for row in rows:
            corpus = corpora[row["dataset"]]
            query = corpus["queries"][row["query_id"]]
            response = apply_token_budget(row["response"], cap)
            details = verified_excerpt_manifest(response, corpus)
            excerpts_checked += len(details["excerpts"])
            ranks = evidence_ranks(delivered_snippets(response), target_groups(query))
            # Original timings are only an internal argument required by the
            # unchanged summarize API. All latency fields are removed below.
            repacked.append({**row, "response": response, "evidence_ranks": ranks})
            derived.append({"cap": cap, "dataset": row["dataset"], "query_id": row["query_id"],
                            "condition": row["condition"], "role": row["role"], "split": row["split"],
                            "kind": row["kind"], "evidence_ranks": ranks,
                            "candidate_evidence_ranks": row["candidate_evidence_ranks"],
                            "match_status": response["match_status"],
                            "token_truncated": response["token_usage"]["truncated"],
                            "budget_exhausted": response["budget_exhausted"], **details})
        summary = without_timings(summarize(repacked))
        if cap == 4096:
            original = without_timings(read_json(run / "results.json")["summary"])
            require(summary == original, "4096-token repacking must preserve the original aggregate quality")
        cap_records[str(cap)] = {"summary": summary, "repacked_rows": len(repacked),
            "lost_complete_queries_vs_original": sum(row["kind"] == "present" and all(row["evidence_ranks"])
                and not all(new["evidence_ranks"]) for row, new in zip(rows, repacked))}
    misses = []
    for row in rows:
        if row["kind"] != "present" or all(row["evidence_ranks"]):
            continue
        corpus = corpora[row["dataset"]]
        traces = missing_group_trace(row, corpus["queries"][row["query_id"]], corpus)
        misses.append({key: row[key] for key in ("dataset", "query_id", "condition", "role", "split")})
        misses[-1]["groups"] = traces
    # Merely importing the runtime classes must not initialize Metal or MLX.
    require(not any(name == "mlx" or name.startswith("mlx.") for name in sys.modules),
            "CPU analysis unexpectedly imported MLX")
    return {"version": 1, "status": "passed", "analysis_kind": "post_hoc_saved_response_repacking",
            "analyzed_at_utc": datetime.now(timezone.utc).isoformat(),
            "analysis_script_sha256": sha(Path(__file__).read_bytes()),
            "audit_helper_sha256": sha((root / "scripts/audit_quality.py").read_bytes()),
            "inputs_sha256": {path.name: sha(path.read_bytes()) for path in [*paths, run / "audit.json"]},
            "tokenizer": {"encoding": "cl100k_base", "tiktoken": importlib.metadata.version("tiktoken")},
            "caps": cap_records, "context_vs_dense": context_comparison(rows, corpora),
            "original_missed_groups": misses, "derived_rows": derived,
            "verification": {"original_rows": len(rows), "repacked_rows": len(derived),
                             "source_faithful_excerpts_checked": excerpts_checked,
                             "exact_token_caps_checked": len(derived), "mlx_imported": False},
            "limits": ["Post-hoc descriptive reuse of saved responses; no fresh retrieval, inference, or latency measurements.",
                       "Caps below 4096 were not predeclared held-out configurations. No default or threshold is selected from this analysis.",
                       "Cannot recover windows or lines already omitted by original ranking, deduplication, result limits, 12000 source characters, or 4096 tokens.",
                       "Original repository_holdout labels identify the source query partition; smaller-cap outcomes are derived descriptions, not a new independent holdout.",
                       "Source dependencies are reconstructed with the frozen static graph; runtime dispatch or complete execution-path correctness is not proven.",
                       "Exact token counts cover one compact context payload, not provider billing or paired-agent token savings."]}


def render_report(record):
    lines = ["# Post-hoc context-budget analysis", "",
        "This CPU-only analysis repacks the 448 saved HTTP responses from the frozen 106-query run. "
        "It performs no retrieval or model inference. Smaller caps are descriptive reuse of the saved outputs; "
        "they are not new independently held-out configurations and do not select a default.", "",
        "Every derived compact JSON payload was counted exactly with pinned `cl100k_base`, including metadata "
        "and its self-reported token count. Excerpts retain whole physical source lines and their verified SHA-256. "
        "No fresh latency is reported. The JSON artifact includes every derived excerpt's bounds/hash and context hash.", "",
        "| Tokens | Mode | Query partition | Complete present | Complete multi-file | Evidence groups | Median / max delivered tokens |",
        "|---:|---|---|---:|---:|---:|---:|"]
    partitions = ("all", "development", "development_queries", "calibration", "repository_holdout")
    for cap, records in record["caps"].items():
        for name, groups in records["summary"].items():
            for partition in partitions:
                if partition not in groups:
                    continue
                metric = groups[partition]
                lines.append(f"| {cap} | {name} | {partition} | {metric['delivered_complete']}/{metric['present']} | "
                    f"{metric['multi_file_complete']}/{metric['multi_file_queries']} | "
                    f"{metric['delivered_groups']}/{metric['required_groups']} | "
                    f"{metric['median_tokens']:g} / {metric['max_tokens']} |")
    comparison = record["context_vs_dense"]
    lines += ["", "## Where expanded context recovered evidence", "",
        "The frozen expanded mode preserves five dense anchors, adds at most two static callees per anchor, "
        "and fills unused slots with later anchors, up to eight total results. Added slots can improve quality "
        "without resolving a dependency. These comparisons use the original 4096-token outputs.", "",
        f"Expanded context adds {comparison['new_complete_queries']} complete queries and loses "
        f"{comparison['lost_complete_queries']} compared with dense-five. Recovered evidence groups by their "
        f"first delivered origin: `{json.dumps(comparison['recovered_groups_by_first_delivered_origin'], sort_keys=True)}`.", "",
        "| Query | Dense-five evidence ranks | Expanded evidence ranks | Recovered group origins |",
        "|---|---|---|---|"]
    for change in comparison["changes"]:
        if not change["recovered_groups"] and not change["lost_groups"]:
            continue
        origins = "; ".join(f"group {group['group']}: {group['evidence'][0]['recovery_kind']} "
                            f"at {group['evidence'][0]['id']}" for group in change["recovered_groups"])
        if change["lost_groups"]:
            origins += f"; lost groups {change['lost_groups']}"
        lines.append(f"| `{change['query_id']}` | {change['dense_ranks']} | {change['context_ranks']} | {origins} |")
    lines += ["", "## Missing evidence through the selection stages", "",
        "The JSON artifact traces every originally incomplete present-query group through the saved first 50 "
        "candidates, exact path/symbol deduplication, reconstructed full selected windows, and actual delivered "
        "excerpts. The default expanded-context failures are listed here; no labels or rankings were changed.", "",
        "| Query | Group | Candidate 50 rank | Deduplicated rank | Full selected window rank | Limitation |",
        "|---|---:|---:|---:|---:|---|"]
    for miss in record["original_missed_groups"]:
        if miss["condition"] != "dense_context":
            continue
        for group in miss["groups"]:
            values = [group[key] if group[key] is not None else "—" for key in
                      ("candidate50_rank", "deduplicated_rank", "selected_full_window_rank")]
            lines.append(f"| `{miss['query_id']}` | {group['group']} | {values[0]} | {values[1]} | {values[2]} | {group['cause']} |")
    lines += ["", "## Limits", "", *[f"- {limit}" for limit in record["limits"]], ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=Path("benchmarks/quality_manifest.json"))
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    root = args.project_root.resolve()
    run, manifest = (root / args.run).resolve(), (root / args.manifest).resolve()
    record = analyze(run, manifest, root)
    (run / "budget-analysis.json").write_text(json.dumps(record, indent=2, allow_nan=False) + "\n")
    (run / "budget-analysis.md").write_text(render_report(record))
    print(json.dumps({"status": record["status"], **record["verification"],
                      "context_gains": record["context_vs_dense"]["new_complete_queries"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
