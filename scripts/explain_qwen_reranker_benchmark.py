#!/usr/bin/env python3
"""Explain frozen retrieval evidence after model processes have stopped.

This independent CPU diagnostic imports no producer, retriever, tokenizer or
model runtime. It reads saved receipts and exact pinned source, and complements
the scalar/cap/runtime audit rather than replacing it.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import tokenize

ROOT = Path(__file__).resolve().parents[1]
PYTHON_REPOSITORIES = {"flask", "requests", "click", "starlette", "rich"}
CONDITIONS = ("dense", "dense_qwen", "hybrid", "hybrid_qwen")


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def jsonl_gzip(path):
    # A closed gzip stream is mandatory; do not analyze an actively written run.
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def physical_lines(source):
    return [line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", source) if line]


def decode_source(path, raw):
    if Path(path).suffix == ".py":
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        return raw.decode(encoding)
    return raw.decode("utf-8")


def contains(window, target, *, exact_symbol=False):
    return (window["path"] == target["path"]
            and (not exact_symbol or window["symbol"] == target["symbol"])
            and window["start_line"] <= target["evidence_lines"][0]
            <= target["evidence_lines"][1] <= window["end_line"])


def rank(windows, group, *, exact_symbol=False):
    return next((number for number, window in enumerate(windows, 1)
                 if any(contains(window, target, exact_symbol=exact_symbol)
                        for target in group)), None)


def first_symbols(windows):
    result, seen = [], set()
    for window in windows:
        key = (window["path"], window["symbol"])
        if key not in seen:
            result.append(window)
            seen.add(key)
    return result


def source_slice(window, sources):
    require(window["path"] in sources, "Window outside pinned source scope")
    lines = physical_lines(sources[window["path"]])
    lo, hi = window["start_line"], window["end_line"]
    require(type(lo) is int and type(hi) is int and 1 <= lo <= hi <= len(lines),
            "Window physical bounds outside pinned source")
    return "".join(lines[lo - 1:hi])


def checked_windows(windows, sources, *, delivered):
    for window in windows:
        source = source_slice(window, sources)
        require(window["source_hash"] == sha(source.encode()),
                "Window source hash differs from exact pinned bytes")
        if delivered:
            require(window["source"] == source, "Delivered code differs from pinned bytes")
    return windows


def scored_prefixes(raw, receipt, sources):
    """Bind semantic scored bounds to actual literal prefix bytes, without inference."""
    scores = receipt["results"]
    require(len(scores) == len(raw), "One observed source prefix per candidate required")
    result = []
    for window, score in zip(raw, scores, strict=True):
        source = source_slice(window, sources)
        lines = physical_lines(source)
        count = score["delivered_lines"]
        require(type(count) is int and 1 <= count <= len(lines), "Invalid scored physical prefix")
        prefix = "".join(lines[:count])
        require(score["source_lines"] == len(lines)
                and score["source_sha256"] == sha(source.encode())
                and score["delivered_sha256"] == sha(prefix.encode())
                and score["truncated"] is (count < len(lines)),
                "Observed scored prefix does not match complete literal source lines")
        result.append({**window, "end_line": window["start_line"] + count - 1,
                       "source_hash": score["delivered_sha256"],
                       "full_window_end_line": window["end_line"],
                       "scoring_truncated": score["truncated"]})
    return result


def result_detail(window, raw, prefixes):
    raw_ids = {item["id"] for item in raw}
    direct = next((item for item in prefixes or [] if item["id"] == window["id"]), None)
    same_span = {"path": window["path"], "symbol": window["symbol"],
                 "evidence_lines": [window["start_line"], window["end_line"]]}
    return {key: window[key] for key in ("id", "path", "symbol", "start_line", "end_line")} | {
        "origin": window.get("origin", "retrieval"),
        "related_to": window.get("related_to"),
        "exact_window_id_outside_raw_pool": window["id"] not in raw_ids,
        "entire_delivered_span_inside_some_raw_window": rank(raw, [same_span]) is not None,
        "direct_scored_prefix_end_line": direct["end_line"] if direct else None,
        "delivered_extends_beyond_direct_scored_prefix":
            window["end_line"] > direct["end_line"] if direct else None,
        "entire_delivered_span_inside_some_scored_prefix":
            rank(prefixes, [same_span]) is not None if prefixes is not None else None,
    }


def group_detail(group, raw, ordered, returned, prefixes):
    matched = [window for window in returned
               if any(contains(window, target) for target in group)]
    raw_rank = rank(raw, group)
    delivered_rank = rank(returned, group)
    prefix_rank = rank(prefixes, group) if prefixes is not None else None
    unique = first_symbols(ordered)
    unique_rank = rank(unique, group)
    observations = []
    if delivered_rank is None:
        if raw_rank is None:
            observations.append("Complete declared evidence is absent from the raw candidate pool.")
        elif unique_rank is None:
            observations.append("A full-span candidate exists, but the first window for its path/symbol omits that span.")
        elif unique_rank > 5:
            observations.append("Complete evidence ranks after the first five anchors; later slots depend on callees and packing.")
        else:
            observations.append("A first-five anchor contains complete evidence, but it is not fully delivered after packing.")
    elif matched[0].get("origin") == "dependency":
        observations.append("The first complete returned evidence is marked as a static dependency.")
    if prefixes is not None and raw_rank is not None and prefix_rank is None:
        observations.append("A raw window contains full gold, but no scored source prefix contains the complete gold span.")
    if delivered_rank is not None and prefixes is not None and prefix_rank is None:
        observations.append("Complete delivered evidence was not present in any complete scored prefix.")
    return {
        "targets": group,
        "raw_candidate_evidence_rank": raw_rank,
        "ordered_candidate_evidence_rank": rank(ordered, group),
        "first_per_symbol_evidence_rank": unique_rank,
        "scored_prefix_evidence_rank_in_original_order": prefix_rank,
        "delivered_evidence_rank": delivered_rank,
        "exact_symbol_delivered_evidence_rank": rank(returned, group, exact_symbol=True),
        "covered_only_by_dependency_results": bool(matched)
            and all(window.get("origin") == "dependency" for window in matched),
        "delivered_evidence_absent_from_raw_pool": delivered_rank is not None and raw_rank is None,
        "delivered_without_complete_gold_in_any_scored_prefix":
            delivered_rank is not None and prefix_rank is None if prefixes is not None else None,
        "covering_results": [result_detail(window, raw, prefixes) for window in matched],
        "observations": observations,
    }


def inspect_row(row, pools, gold, sources):
    raw = checked_windows(pools[row["pool_id"]]["windows"], sources, delivered=False)
    require([window["id"] for window in raw] == row["candidate_order"], "Candidate order receipt changed")
    mapping = {window["id"]: window for window in raw}
    require(len(mapping) == len(raw) and set(row["ranking_order"]) == set(mapping)
            and len(row["ranking_order"]) == len(raw), "Ranking is not a permutation of observed candidates")
    ordered = [mapping[identifier] for identifier in row["ranking_order"]]
    returned = checked_windows(row["response"]["results"], sources, delivered=True)
    qwen = row["identity"]["condition"].endswith("_qwen")
    prefixes = scored_prefixes(raw, row["reranker"], sources) if qwen and raw else ([] if qwen else None)
    details = [group_detail(group, raw, ordered, returned, prefixes) for group in gold]
    observed = [item["delivered_evidence_rank"] for item in details]
    require(observed == row["evidence_ranks"], "Producer source-span ranks differ from diagnostic")
    compatibility = [item["exact_symbol_delivered_evidence_rank"] for item in details]
    if row["identity"]["dataset"] in PYTHON_REPOSITORIES:
        require(compatibility == row["symbol_compatibility_evidence_ranks"],
                "Python historical exact-symbol metric changed")
    return {"identity": row["identity"], "query": row["query"], "kind": row["kind"],
            "groups": details, "complete": all(rank is not None for rank in observed) if gold else None,
            "delivered_evidence_ranks": observed,
            "symbol_compatibility_evidence_ranks": compatibility if row["identity"]["dataset"] in PYTHON_REPOSITORIES else None,
            "symbol_compatibility_complete": all(rank is not None for rank in compatibility) if gold
                and row["identity"]["dataset"] in PYTHON_REPOSITORIES else None,
            "candidate_ids": row["candidate_order"],
            "candidate_source_hashes": [window["source_hash"] for window in raw],
            "returned_results": [result_detail(window, raw, prefixes) for window in returned],
            "match_status": row["response"]["match_status"]}


def changes(before, after):
    groups = []
    for number, (old, new) in enumerate(zip(before["groups"], after["groups"], strict=True), 1):
        if old["delivered_evidence_rank"] != new["delivered_evidence_rank"]:
            groups.append({"group": number, "coverage_gained": old["delivered_evidence_rank"] is None
                           and new["delivered_evidence_rank"] is not None,
                           "coverage_lost": old["delivered_evidence_rank"] is not None
                           and new["delivered_evidence_rank"] is None,
                           "before": old, "after": new})
    same_pool = before["candidate_ids"] == after["candidate_ids"] \
        and before["candidate_source_hashes"] == after["candidate_source_hashes"]
    return {"dataset": before["identity"]["dataset"], "query_id": before["identity"]["query_id"],
            "query": before["query"], "kind": before["kind"],
            "same_ordered_candidate_identity_and_source": same_pool,
            "complete_gained": before["complete"] is False and after["complete"] is True,
            "complete_lost": before["complete"] is True and after["complete"] is False,
            "before_complete": before["complete"], "after_complete": after["complete"],
            "before_ranks": before["delivered_evidence_ranks"], "after_ranks": after["delivered_evidence_ranks"],
            "changed_groups": groups,
            "python_compatibility_before": before["symbol_compatibility_evidence_ranks"],
            "python_compatibility_after": after["symbol_compatibility_evidence_ranks"]}


def analyze(plan, rows, pools, sources):
    queries = {(record["id"], query["query_id"]): query for record in plan["sources"]
               for query in record["queries"]}
    by_identity = {}
    primary = []
    clipped = []
    actual_scored_pairs = 0
    for row in rows:
        require(row["status"] == "completed", "Final semantic report requires completed scheduled rows")
        identity = row["identity"]
        key = (identity["dataset"], identity["query_id"], identity["condition"], identity["repetition"])
        require(key not in by_identity, "Repeated scheduled identity in results")
        by_identity[key] = row
        if identity["condition"].endswith("_qwen") and row["candidate_order"]:
            raw = pools[row["pool_id"]]["windows"]
            prefixes = scored_prefixes(raw, row["reranker"], sources[identity["dataset"]])
            actual_scored_pairs += len(prefixes)
            for window, prefix in zip(raw, prefixes, strict=True):
                if prefix["scoring_truncated"]:
                    clipped.append({"identity": identity, "window_id": window["id"],
                                    "path": window["path"], "symbol": window["symbol"],
                                    "full_lines": [window["start_line"], window["end_line"]],
                                    "scored_lines": [prefix["start_line"], prefix["end_line"]]})
        if identity["repetition"] == plan["primary_quality_repetition"]:
            query = queries[(identity["dataset"], identity["query_id"])]
            require(query["kind"] == row["kind"], "Frozen query kind changed")
            primary.append(inspect_row(row, pools, query["groups"], sources[identity["dataset"]]))
    expected = {(row["dataset"], row["query_id"], row["condition"], row["repetition"])
                for row in plan["schedule"]}
    require(set(by_identity) == expected and len(rows) == plan["planned_requests"],
            "Whole frozen schedule must complete before final conclusions")
    index = {(row["identity"]["dataset"], row["identity"]["query_id"], row["identity"]["condition"]): row
             for row in primary}
    comparison = {}
    for family in ("dense", "hybrid"):
        cases = [changes(index[(dataset, query_id, family)], index[(dataset, query_id, family + "_qwen")])
                 for dataset, query_id in queries]
        comparison[family] = {
            "paired_unique_queries": len(cases),
            "complete_gains": [case for case in cases if case["complete_gained"]],
            "complete_regressions": [case for case in cases if case["complete_lost"]],
            "group_coverage_changes": [case for case in cases
                if any(group["coverage_gained"] or group["coverage_lost"] for group in case["changed_groups"])],
            "rank_only_changes": [case for case in cases if case["changed_groups"]
                and not any(group["coverage_gained"] or group["coverage_lost"] for group in case["changed_groups"])],
            "candidate_pair_mismatches": [case for case in cases if not case["same_ordered_candidate_identity_and_source"]],
        }
    compatibility = [row for row in primary if row["kind"] == "present"
                     and row["symbol_compatibility_evidence_ranks"] is not None
                     and row["delivered_evidence_ranks"] != row["symbol_compatibility_evidence_ranks"]]
    dependencies = [{"identity": row["identity"], "query": row["query"], "group": number, "evidence": group}
                    for row in primary for number, group in enumerate(row["groups"], 1)
                    if group["delivered_evidence_rank"] is not None
                    and (group["covered_only_by_dependency_results"]
                         or any(result["origin"] == "dependency"
                                and result["exact_window_id_outside_raw_pool"]
                                for result in group["covering_results"]))]
    unscored_gold = [{"identity": row["identity"], "query": row["query"], "group": number, "evidence": group}
                    for row in primary for number, group in enumerate(row["groups"], 1)
                    if group["delivered_without_complete_gold_in_any_scored_prefix"] is True]
    unscored_suffixes = [{"identity": row["identity"], "result": result}
                        for row in primary for result in row["returned_results"]
                        if result["delivered_extends_beyond_direct_scored_prefix"] is True]
    outside_raw = [{"identity": row["identity"], "query": row["query"], "group": number, "evidence": group}
                   for row in primary for number, group in enumerate(row["groups"], 1)
                   if group["delivered_evidence_absent_from_raw_pool"]]
    dependency_results = [{"identity": row["identity"], "result": result}
                          for row in primary for result in row["returned_results"]
                          if result["origin"] == "dependency" and result["exact_window_id_outside_raw_pool"]]
    return {"complete_scheduled_requests": len(rows), "primary_unique_queries": len(queries),
            "primary_condition_rows": len(primary), "comparisons": comparison,
            "primary_dependency_evidence": dependencies,
            "primary_dependency_results_outside_raw_pool": dependency_results,
            "primary_result_origin_counts": dict(Counter(result["origin"] for row in primary
                for result in row["returned_results"])),
            "primary_delivered_gold_absent_from_raw_pool": outside_raw,
            "primary_delivered_gold_without_complete_scored_prefix": unscored_gold,
            "primary_delivered_suffix_beyond_direct_scored_prefix": unscored_suffixes,
            "scoring_prefix_clips": {"actual_measured_pairs": actual_scored_pairs,
                "clipped_pair_occurrences": len(clipped),
                "unique_query_condition_windows": len({(row["identity"]["dataset"], row["identity"]["query_id"],
                    row["identity"]["condition"], row["window_id"]) for row in clipped}),
                "occurrences": clipped},
            "python_symbol_compatibility_differences": compatibility,
            "primary_case_details": primary}


def pinned_sources(plan, root):
    entries = {}
    for path, expected in plan["manifest_sha256"].items():
        raw = (root / path).read_bytes()
        require(sha(raw) == expected, "Original manifest bytes changed")
        for entry in json.loads(raw)["datasets"]:
            entries[entry["id"]] = entry
    result = {}
    for record in plan["sources"]:
        entry = entries[record["id"]]
        corpus = (root / entry["corpus"]).resolve()
        values = {}
        for path, expected in record["source_file_sha256"].items():
            file = (corpus / path).resolve()
            require(file.is_relative_to(corpus), "Source path escapes pinned corpus")
            raw = file.read_bytes()
            require(sha(raw) == expected, "Pinned corpus source bytes changed")
            values[path] = decode_source(path, raw)
        for query in record["queries"]:
            for group in query["groups"]:
                for target in group:
                    lines = physical_lines(values[target["path"]])
                    lo, hi = target["evidence_lines"]
                    require(sha("".join(lines[lo - 1:hi]).encode()) == target["evidence_sha256"],
                            "Frozen executable evidence slice changed")
        result[record["id"]] = values
    return result


def markdown(report):
    lines = ["# Qwen reranker evidence explanations", "",
             "This report describes observed source delivery and ranking changes in the completed frozen study. "
             "It does not infer model intent or establish coding correctness. "
             "All comparisons below use the first quality repetition; repeated pair forwards are counted separately.", ""]
    for family, comparison in report["diagnostics"]["comparisons"].items():
        lines.extend([f"## {family.title()}", "",
                      f"Complete-evidence gains: {len(comparison['complete_gains'])}; "
                      f"regressions: {len(comparison['complete_regressions'])}. "
                      f"Ordered candidate identity/source mismatches: {len(comparison['candidate_pair_mismatches'])}.", "",
                      "| Repository | Query | Before ranks | After ranks | Changed evidence |",
                      "|---|---|---|---|---|"])
        cases = comparison["group_coverage_changes"]
        for case in cases:
            descriptions = []
            for change in case["changed_groups"]:
                if change["coverage_gained"] or change["coverage_lost"]:
                    state = "gained" if change["coverage_gained"] else "lost"
                    targets = change["after"]["targets"]
                    names = ", ".join(f"{target['path']}:{target['symbol']}" for target in targets)
                    origins = sorted({row["origin"] for row in change["after"]["covering_results"]})
                    descriptions.append(f"group {change['group']} {state}: {names} (after origin {', '.join(origins) or 'none'})")
            text = "; ".join(descriptions).replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {case['dataset']} | {case['query_id']} | {case['before_ranks']} | {case['after_ranks']} | {text} |")
        if not cases:
            lines.append("| — | No group coverage changes | — | — | — |")
        lines.append("")
    diagnostics = report["diagnostics"]
    clips = diagnostics["scoring_prefix_clips"]
    lines.extend(["## Source delivery and score-input limits", "",
                  f"{len(diagnostics['primary_delivered_gold_absent_from_raw_pool'])} primary condition/group occurrences "
                  "deliver complete evidence absent from all raw candidates; the report binds their static dependency origins.", "",
                  f"{clips['clipped_pair_occurrences']} of {clips['actual_measured_pairs']} measured pair inputs "
                  f"were source-prefix clipped ({clips['unique_query_condition_windows']} distinct query/condition/window inputs). "
                  f"{len(diagnostics['primary_delivered_suffix_beyond_direct_scored_prefix'])} primary delivered excerpts "
                  "extend past their directly scored prefixes. "
                  f"{len(diagnostics['primary_delivered_gold_without_complete_scored_prefix'])} primary Qwen condition/group "
                  "occurrences deliver complete gold absent from every scored source prefix.", "",
                  f"{len(diagnostics['python_symbol_compatibility_differences'])} primary Python condition/query occurrences "
                  "have different span-based and historical exact-symbol evidence ranks. The original labels and intervals remain unchanged.", "",
                  "Exact dependency-window membership and evidence-span membership are distinct: an unranked callee window "
                  "can overlap a ranked enclosing window. A returned suffix can also be absent from its direct scoring pair "
                  "but present in another scored prefix. See the complete machine report for both distinctions.", "",
                  "Warm latency, scalar/cap checks, complete forward reconciliation and runtime cleanup are established by "
                  "the separate independent audit. These source diagnostics do not replace it.", ""])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    directory = args.artifact_dir
    output = directory / "evidence-explanations.json"
    require(not output.exists() and not (directory / "evidence-explanations.md").exists(),
            "Refuse to overwrite an existing semantic report")
    require(read(directory / "completion.json")["complete"] is True,
            "Model study must finish before full case analysis")
    require((directory / "reranker-stop.json").is_file(), "Resident scorer must stop before source analysis")
    paths = ("frozen-plan.json", "completion.json", "reranker-stop.json", "searches.jsonl.gz",
             "candidate-pools.jsonl.gz")
    plan = read(directory / "frozen-plan.json")
    rows = jsonl_gzip(directory / "searches.jsonl.gz")
    pool_rows = jsonl_gzip(directory / "candidate-pools.jsonl.gz")
    pools = {row["pool_id"]: row for row in pool_rows}
    require(len(pools) == len(pool_rows), "Duplicated content-addressed candidate pool")
    report = {"version": 1, "classification": "Post-run descriptive frozen source-evidence diagnostic",
              "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "input_sha256": {path: sha((directory / path).read_bytes()) for path in paths},
              "generator_sha256": sha(Path(__file__).read_bytes()),
              "model_calls": 0, "retrieval_calls": 0,
              "diagnostics": analyze(plan, rows, pools, pinned_sources(plan, args.root)),
              "limitations": ["Observed pipeline changes are not a causal attribution of model reasoning.",
                  "The candidate pool, scored literal prefixes and delivered dependency/packing source are distinct stages.",
                  "First-repetition quality is not multiplied by repeated timing runs.",
                  "Python declared spans can be narrower than language/Koa full bodies; original symbols/spans are unchanged.",
                  "No paid-agent token usage, coding correctness, warm production HTTP latency or broad generalization is measured here."]}
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    (directory / "evidence-explanations.md").write_text(markdown(report))
    print(json.dumps({"output": str(output), "requests": len(rows), "model_calls": 0,
                      "retrieval_calls": 0, "clipped_pair_occurrences":
                      report["diagnostics"]["scoring_prefix_clips"]["clipped_pair_occurrences"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
