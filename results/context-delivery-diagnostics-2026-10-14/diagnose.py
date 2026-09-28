#!/usr/bin/env python3
"""Inspect original source-bound context losses only; no imports of runtime code."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import tokenize

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
STUDIES = {
    "development": ROOT / "results/qwen-shortlist-development-2026-10-14",
    "httpx": ROOT / "results/qwen-shortlist-httpx-2026-10-14",
}
CONFIGURATION = "hybrid_k24_b1"


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def lines(source):
    return [line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", source) if line]


def jsonl(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def contains(window, group):
    return any(window["path"] == target["path"]
               and window["start_line"] <= target["evidence_lines"][0]
               and window["end_line"] >= target["evidence_lines"][1] for target in group)


def ranks(windows, groups):
    return [next((rank for rank, window in enumerate(windows, 1) if contains(window, group)), None)
            for group in groups]


class Study:
    def __init__(self, name, path):
        self.name, self.path = name, path
        self.audit = read(path / "independent-audit.json")
        require(self.audit["checks_passed"] is True, "Prior independent audit did not pass")
        names = ("frozen-plan.json", "searches.jsonl.gz", "candidate-pools.jsonl.gz", "source-content.jsonl.gz")
        self.inputs = {str((path / item).relative_to(ROOT)): sha((path / item).read_bytes()) for item in names}
        for item in names:
            require(self.inputs[str((path / item).relative_to(ROOT))] == self.audit["artifact_sha256"][item],
                    f"Previously audited input changed: {path.name}/{item}")
        self.inputs[str((path / "independent-audit.json").relative_to(ROOT))] = sha((path / "independent-audit.json").read_bytes())
        self.plan = read(path / "frozen-plan.json")
        self.code = {}
        for filename in ("src/semantic_search/backend.py", "src/semantic_search/context.py", "scripts/run_clause_fusion.py"):
            raw = gzip.decompress((path / "frozen-sources" / (filename + ".gz")).read_bytes())
            require(sha(raw) == self.plan["implementation_sha256"][filename], "Frozen packing code archive differs")
            self.code[filename] = sha(raw)
        self.entries = {entry["id"]: entry for entry in self.plan["sources"]}
        self.gold = {(entry["id"], query["query_id"]): query for entry in self.plan["sources"] for query in entry["queries"]}
        self.inventory = {entry["id"]: {window["id"]: window for window in entry["window_inventory"]}
                          for entry in self.plan["sources"]}
        self.content = {row["sha256"]: row["source"] for row in jsonl(path / "source-content.jsonl.gz")}
        require(all(sha(source.encode()) == digest for digest, source in self.content.items()), "Source-content blob SHA differs")
        self.pools = {pool["pool_id"]: pool for pool in jsonl(path / "candidate-pools.jsonl.gz")}
        self.rows = [row for row in jsonl(path / "searches.jsonl.gz")
                     if row["configuration"]["id"] == CONFIGURATION and row["identity"]["repetition"] == 1]
        self.sources, self.files = {}, {}
        self.excerpts_checked = 0

    def source(self, dataset, filename):
        key = dataset, filename
        if key not in self.sources:
            raw = (ROOT / ".cache/corpora" / dataset / filename).read_bytes()
            digest = sha(raw)
            require(digest == self.entries[dataset]["source_file_sha256"][filename], "Saved corpus source differs from the freeze")
            codec, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
            self.sources[key] = lines(raw.decode(codec))
            self.files[f"{dataset}/{filename}"] = digest
        return self.sources[key]

    def check_window(self, dataset, window, *, actual=False):
        full = self.inventory[dataset][window["id"]]
        require(all(window[key] == full[key] for key in ("path", "symbol", "start_line")), "Window identity changed")
        require(full["start_line"] <= window["end_line"] <= full["end_line"], "Window bounds differ")
        source = "".join(self.source(dataset, window["path"])[window["start_line"] - 1:window["end_line"]])
        digest = sha(source.encode())
        if actual:
            require(window["source"] == source and window["source_hash"] == digest,
                    "Actual delivered source/hash differs from whole saved lines")
            require(window["content_hash"] == full["source_hash"] and window["window_end_line"] == full["end_line"]
                    and window["truncated"] == (window["end_line"] < full["end_line"]), "Actual full-window/truncation metadata differs")
            self.excerpts_checked += 1
        else:
            require(window["source_hash"] == digest and self.content[digest] == source, "Scored window differs from literal recorded source")

    def selection(self, row):
        dataset, query_id = row["identity"]["dataset"], row["identity"]["query_id"]
        groups = self.gold[dataset, query_id]["groups"]
        pool = self.pools[row["pool_id"]]["windows"]
        require([window["id"] for window in pool] == row["candidate_order"], "Saved pool order differs")
        require(row["shortlist_original_order"] == row["candidate_order"][:24]
                and set(row["ranking_order"]) == set(row["shortlist_original_order"]), "Scored-prefix order differs")
        for window in pool:
            self.check_window(dataset, window)
        ranked = [self.inventory[dataset][identifier] for identifier in row["ranking_order"]]
        distinct, seen = [], set()
        for window in ranked:
            identity = window["path"], window["symbol"]
            if identity not in seen:
                seen.add(identity)
                distinct.append(window)
        actual = row["response"]["results"]
        for window in actual:
            self.check_window(dataset, window, actual=True)
        require(ranks(pool, groups) == row["candidate_evidence_ranks"]
                and ranks(pool[:24], groups) == row["shortlist_evidence_ranks"]
                and ranks(ranked, groups) == row["ranked_candidate_evidence_ranks"]
                and ranks(actual, groups) == row["evidence_ranks"], "Full-span evidence ranks differ")
        return groups, pool, ranked, distinct, actual

    def targets(self, dataset, group):
        result = []
        for target in group:
            low, high = target["evidence_lines"]
            text = "".join(self.source(dataset, target["path"])[low - 1:high])
            require(sha(text.encode()) == target["evidence_sha256"], "Required executable evidence SHA differs")
            result.append({**target, "source_file_sha256": self.entries[dataset]["source_file_sha256"][target["path"]],
                           "required_physical_lines": high - low + 1})
        return result


def diagnose(study):
    misses, dependency_only = [], []
    for row in study.rows:
        if row["kind"] != "present":
            continue
        groups, pool, ranked, distinct, actual = study.selection(row)
        dataset, query_id = row["identity"]["dataset"], row["identity"]["query_id"]
        for number, group in enumerate(groups, 1):
            covered = [window for window in actual if contains(window, group)]
            if covered and all(window["origin"] == "dependency" for window in covered):
                dependency_only.append({"dataset": dataset, "query_id": query_id, "group": number,
                    "targets": study.targets(dataset, group), "candidate_rank": ranks(pool, [group])[0],
                    "inside_raw_retrieval_first_eight": any(contains(window, group) for window in pool[:8]),
                    "scored_rank": ranks(ranked, [group])[0],
                    "inside_first_eight_distinct_scored_windows": any(contains(window, group) for window in distinct[:8]),
                    "delivered_by": [{key: window[key] for key in ("id", "origin", "related_to", "start_line", "end_line")}
                                     for window in covered]})
            if covered or not any(contains(window, group) for window in ranked):
                continue
            distinct_rank = ranks(distinct, [group])[0]
            matching_ids = {window["id"] for window in ranked if contains(window, group)}
            dependency_slots = sum(window["origin"] == "dependency" for window in actual)
            earlier_symbols = {(window["path"], window["symbol"]) for window in distinct[:(distinct_rank or 1) - 1]}
            additional_dependency_slots = sum(window["origin"] == "dependency"
                and (window["path"], window["symbol"]) not in earlier_symbols for window in actual)
            if distinct_rank is None:
                cause = "duplicate_window_selection"
            elif distinct_rank > 8:
                cause = "outside_eight_symbol_eligible_prefix"
            elif (distinct_rank > 5 and distinct_rank > 8 - additional_dependency_slots
                  and not any(window["id"] in matching_ids for window in actual)):
                # Each actually delivered dependency proves one expanded slot
                # was used before later anchors. This lower bound suffices
                # even if another selected row was subsequently clipped away.
                cause = "dependency_slots_displace_scored_anchor"
            else:
                cause = "packing_or_selection_requires_further_trace"
            matching = [{**window, "scored_rank": rank} for rank, window in enumerate(ranked, 1) if contains(window, group)]
            target_paths = {target["path"] for target in group}
            actual_same_path = [{key: window[key] for key in
                ("id", "path", "start_line", "end_line", "origin", "related_to", "truncated", "source_hash")}
                for window in actual if window["path"] in target_paths]
            overlap = [{"id": window["id"], "intersection": [max(window["start_line"], target["evidence_lines"][0]),
                min(window["end_line"], target["evidence_lines"][1])]} for window in actual for target in group
                if window["path"] == target["path"] and window["start_line"] <= target["evidence_lines"][1]
                and window["end_line"] >= target["evidence_lines"][0]]
            misses.append({"dataset": dataset, "query_id": query_id, "group": number, "targets": study.targets(dataset, group),
                "candidate_rank": row["candidate_evidence_ranks"][number - 1],
                "scored_rank": row["ranked_candidate_evidence_ranks"][number - 1],
                "first_distinct_scored_rank": distinct_rank, "matching_scored_windows": matching,
                "cause": cause, "duplicate_exclusion": distinct_rank is None,
                "dependency_slots_proven_used": dependency_slots,
                "dependency_slots_proven_additional_to_earlier_scored_symbols": additional_dependency_slots,
                "required_window_partially_delivered": any(window["id"] in matching_ids for window in actual),
                "packing_clipped_other_delivered_windows": any(window["truncated"] for window in actual),
                "actual_same_path_whole_line_intervals": actual_same_path, "actual_evidence_line_intersections": overlap,
                "delivered_rows": [{key: window[key] for key in ("id", "origin", "related_to", "start_line", "end_line")}
                                   for window in actual],
                "delivered_source_chars": row["response"]["total_source_chars"],
                "prior_audited_compact_tokens": row["response"]["token_usage"]["tokens"],
                "budget_exhausted": row["response"]["budget_exhausted"]})
    return {"first_repetition_queries": len(study.rows),
            "present_queries": sum(row["kind"] == "present" for row in study.rows),
            "missing_required_groups_with_full_evidence_in_scored24": misses,
            "causes": dict(Counter(miss["cause"] for miss in misses)),
            "existing_groups_delivered_only_by_dependencies": dependency_only,
            "pinned_source_files_verified": study.files, "actual_excerpts_verified": study.excerpts_checked}


def report(record):
    httpx = record["studies"]["httpx"]
    lines_out = ["# Context delivery: seven scored HTTPX spans missing from output", "",
        "All seven required groups are present as complete literal source windows in the scored hybrid K24 prefix. "
        "The original four affected responses contain eight untruncated excerpts each, with 1,498–2,278 compact "
        "tokens and 2,384–6,578 source characters. None hits the 4,096-token or 12,000-character cap. "
        "Four spans lose the initial eight-symbol eligibility cutoff; three are eligible but lose slots to static callees. "
        "There are no duplicate-window losses or partially delivered evidence lines among these seven spans.", "",
        "| Query / required span | Retrieval rank | Scored rank | Actual evidence lines delivered | Exclusion |",
        "|---|---:|---:|---|---|"]
    for miss in httpx["missing_required_groups_with_full_evidence_in_scored24"]:
        target = miss["targets"][0]
        bounds = target["evidence_lines"]
        exclusion = "outside first eight distinct scored symbols" if miss["cause"] == "outside_eight_symbol_eligible_prefix" else "static callees take remaining slots"
        lines_out.append(f"| `{miss['query_id']}`: `{target['symbol']}` ({target['path']}:{bounds[0]}–{bounds[1]}) "
                         f"| {miss['candidate_rank']} | {miss['scored_rank']} | none | {exclusion} |")
    lines_out += ["", "The frozen packer first retains eight distinct scored symbols, preserves the first five as anchors, "
        "then inserts at most two static callees per anchor before considering anchors six through eight. "
        "No scored symbol after the eighth is eligible even when source/token capacity remains. The actual complete "
        "line intervals, dependency owners, source-file/evidence/window hashes, and original result IDs are in "
        "[diagnostics.json](diagnostics.json). This is a source-only diagnosis of retained outputs, not a new policy replay.", "",
        "## Development evidence and a bounded next experiment", "",
        "The full observed-development hybrid K24 run has four additional scored required groups missing from delivery:", "",
        "| Development query / group | Scored rank | Observed exclusion |",
        "|---|---:|---|"]
    for miss in record["studies"]["development"]["missing_required_groups_with_full_evidence_in_scored24"]:
        lines_out.append(f"| `{miss['query_id']}` / group {miss['group']} | {miss['scored_rank']} | {miss['cause']} |")
    lines_out += ["", "These development losses justify separately freezing an experiment about competition between "
        "scored anchors and helper slots. They do not establish a safe replacement policy. In particular, a rule "
        "that always spends all eight slots on scored anchors would threaten the existing Starlette "
        "`starlette_sync_endpoint_worker` success: its required `run_in_threadpool` span is delivered only as "
        "a dependency and lies outside scored K24. HTTPX's already complete redirect-header query also relies "
        "on two dependency-only groups outside the first eight scored windows: `_same_origin` at scored rank "
        "15 and `_port_or_default` (77–80), absent from the entire raw candidate 50 as well as scored K24. "
        "HTTPX has now been observed and cannot serve as a fresh holdout for a policy motivated by these results.", "",
        "| Existing dependency-only required group | Raw candidate rank | Scored rank | Full evidence in first eight scored symbols? |",
        "|---|---:|---:|---|"]
    for name in ("development", "httpx"):
        for group in record["studies"][name]["existing_groups_delivered_only_by_dependencies"]:
            raw = group["candidate_rank"] if group["candidate_rank"] is not None else "absent from 50"
            scored = group["scored_rank"] if group["scored_rank"] is not None else "absent from K24"
            lines_out.append(f"| `{group['dataset']}/{group['query_id']}` / group {group['group']} "
                f"| {raw} | {scored} | {'yes' if group['inside_first_eight_distinct_scored_windows'] else 'no'} |")
    lines_out += ["",
        "The prior prospective Express/Zod containment-plus-backfill replay changed 28/90 contexts and skipped "
        "47 covered prefixes, yet produced zero complete-query or evidence-group gains across all 72 positive "
        "query/condition cases. Repeating that control alone has no demonstrated relevance benefit. The current "
        "seven HTTPX losses occur without clipping or covered duplicate spans, so they point to rank/slot "
        "allocation rather than that earlier containment mechanism.", "",
        "Recommended next scope: approve and commit a separate development protocol comparing a small, "
        "predeclared anchor/helper allocation family on the existing source-bound orders, preserving scored-prefix "
        "provenance and measuring all regressions and dependency-only groups. Make no default change from "
        "these diagnostics. Candidate-rank losses at 9/10/17/19 need a separate relevance/diversity hypothesis; "
        "a dependency-priority repair alone cannot recover them. Any later generalization claim requires a new "
        "source-reviewed repository holdout. No control is implemented or scored here.", "",
        "Checks: pinned literal corpus bytes, full candidate/evidence spans, physical whole-line hashes, saved "
        "rank/dedup identities and the prior independent-audit input digests. Compact token counts are inherited "
        "from the bound independent audit; no new tokenizer execution or latency measurement is performed. "
        "Only standard-library CPU code runs. No production code, earlier result, settings, model environment, "
        "inference, network or hosted coding trial is changed.", ""]
    return "\n".join(lines_out)


def main():
    studies = {name: Study(name, path) for name, path in STUDIES.items()}
    data = {name: diagnose(study) for name, study in studies.items()}
    require(len(data["httpx"]["missing_required_groups_with_full_evidence_in_scored24"]) == 7,
            "The expected seven scored HTTPX misses changed")
    require(data["httpx"]["causes"] == {"outside_eight_symbol_eligible_prefix": 4, "dependency_slots_displace_scored_anchor": 3},
            "Expected original loss categories changed")
    record = {"version": 1, "classification": "CPU-only post-hoc source-bound diagnostic; no selector evaluation",
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "producer_sha256": sha(Path(__file__).read_bytes()),
        "configuration": CONFIGURATION, "quality_repetition": 1,
        "inputs_sha256": {key: value for study in studies.values() for key, value in study.inputs.items()},
        "frozen_packer_sha256": {name: study.code for name, study in studies.items()}, "studies": data,
        "prior_containment_replay_inputs_sha256": {str(path.relative_to(ROOT)): sha(path.read_bytes()) for path in
            (ROOT / "results/context-packing-development-2026-10-13/gold-scoring.json",
             ROOT / "results/context-packing-development-2026-10-13/gold-scoring.md")},
        "calls": {"model": 0, "retrieval": 0, "network": 0, "hosted_agent": 0},
        "limits": ["Retained original rank/source/packing outputs; not prospective evaluation of a new selector.",
                   "All development repositories and HTTPX outcomes are now observed.",
                   "Prior audit supplies exact compact token counts; this diagnostic does not rerun tokenization.",
                   "No provider token savings, fresh speed measurement, or coding correctness follows from source coverage."]}
    (OUT / "diagnostics.json").write_text(json.dumps(record, indent=2, allow_nan=False) + "\n")
    (OUT / "findings.md").write_text(report(record))
    print(json.dumps({"httpx_scored_missing_groups": 7, "httpx_causes": data["httpx"]["causes"],
                      "development_scored_missing_groups": len(data["development"]["missing_required_groups_with_full_evidence_in_scored24"]),
                      "verified_actual_excerpts": sum(study.excerpts_checked for study in studies.values())}))


if __name__ == "__main__":
    main()
