#!/usr/bin/env python3
"""Explore smaller original candidate prefixes from frozen observed Qwen scores.

This CPU replay performs no model, embedding, index or retrieval calls. Quality
is observed development evidence, and its execution time is not warm inference
latency. The prior run's actual forward receipts remain immutable inputs.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "shortlist_saved_evidence_audit", ROOT / "scripts/audit_qwen_reranker_benchmark.py"
)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)
PREFIXES = (5, 8, 10, 16, 24, 32, 40, 50)
CLASSIFICATION = "Exploratory post-output observed-development quality replay; no new inference or latency"


def require(value, detail):
    if not value:
        raise ValueError(detail)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      allow_nan=False, separators=(",", ":")).encode()


def read(path):
    return json.loads(path.read_bytes())


def gz_rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def bound_input_audit(root, study):
    """Bind this replay to the successful independent audit, not just a filename."""
    previous = read(study / "independent-audit.json")
    require(previous["checks_passed"] is True and previous["completed_conditions"] == 1728
            and previous["unique_queries"] == 144
            and previous["completed_benchmark_pair_forwards"] == 43200,
            "Complete successful prior independent audit required")
    require(previous["auditor_sha256"] == sha((root / "scripts/audit_qwen_reranker_benchmark.py").read_bytes()),
            "Previously certified independent auditor bytes changed")
    for name, digest in previous["artifact_sha256"].items():
        path = audit.evidence.located(study, name)
        require(sha(path.read_bytes()) == digest, "Previously audited artifact changed: " + name)
    require(previous["frozen_plan_sha256"] == sha((study / "frozen-plan.json").read_bytes()),
            "Previously audited frozen protocol changed")
    plan = read(study / "frozen-plan.json")
    for name, digest in plan["implementation_sha256"].items():
        require(sha(audit.evidence.located(root, name).read_bytes()) == digest,
                "Frozen producer/packing/extractor implementation changed: " + name)
    return previous, plan


def first_pass(rows):
    selected = [row for row in rows if row["identity"]["repetition"] == 1]
    keys = [(row["identity"]["dataset"], row["identity"]["query_id"],
             row["identity"]["condition"]) for row in selected]
    require(len(keys) == len(set(keys)), "Duplicate first-pass query/condition")
    require(all(row["status"] == "completed" for row in selected), "Incomplete first-pass row")
    return selected


def prefix_ranking(raw, scores, k):
    """Sort only the original first K; the tail is neither scored nor backfilled."""
    require(type(k) is int and 1 <= k <= 50, "Strict prefix depth between one and fifty")
    require(len(raw) == len(scores) and len(raw) <= 50
            and len({row["id"] for row in raw}) == len(raw), "Unique raw pool and one score per window")
    return audit.ordered(raw[:k], scores[:k])


def pack_source(query, scope, ranked, data):
    """Reconstruct the unchanged packer with its original global budgets.

    The full-study independent exact_packing check verifies this same contract.
    Every K=50 result is additionally required to match the actual saved payload.
    """
    from semantic_search.context import apply_token_budget

    distinct = {}
    for row in ranked:
        distinct.setdefault((row["path"], row["symbol"]), row)
    eligible = list(distinct.values())[:8]
    anchors, expanded, included = eligible[:5], [], set()
    for anchor in anchors:
        expanded.append((data["snippets"][anchor["id"]], anchor["retrieval_score"], "retrieval", None))
        included.add((anchor["path"], anchor["symbol"]))
    for anchor in anchors:
        for callee in data["graph"].dependencies(data["snippets"][anchor["id"]], limit=2):
            key = (callee.path, callee.symbol)
            if key not in included and len(expanded) < 8:
                expanded.append((callee, anchor["retrieval_score"], "dependency", anchor["id"]))
                included.add(key)
    for row in eligible:
        key = (row["path"], row["symbol"])
        if key not in included and len(expanded) < 8:
            expanded.append((data["snippets"][row["id"]], row["retrieval_score"], "retrieval", None))
            included.add(key)
    rows, remaining = [], 12000
    for snippet, score, origin, related in expanded:
        lines, used = [], 0
        for line in audit.evidence.physical_lines(snippet.text):
            if used + len(line) > remaining:
                break
            lines.append(line)
            used += len(line)
        source = "".join(lines)
        if not source:
            continue
        remaining -= len(source)
        rows.append({"id": snippet.id, "path": snippet.path, "symbol": snippet.symbol,
                     "start_line": snippet.start_line, "end_line": snippet.start_line + len(lines) - 1,
                     "window_end_line": snippet.end_line, "source": source, "content_hash": snippet.content_hash,
                     "source_hash": sha(source.encode()), "retrieval_score": float(score), "h2o_score": None,
                     "origin": origin, "related_to": related, "truncated": source != snippet.text})
    response = {"query": query, "scope": deepcopy(scope), "match_status": "candidates" if ranked else "no_candidates",
                "results": rows, "total_source_chars": 12000 - remaining,
                "budget_exhausted": any(row["truncated"] for row in rows) or len(rows) < len(expanded)}
    return apply_token_budget(response, 4096)


def require_exact(response, observed):
    expected = audit.production_order(observed)
    require(audit.evidence.compact_text(response) == audit.evidence.compact_text(expected)
            and response["results"] == expected["results"]
            and response["total_source_chars"] == expected["total_source_chars"],
            "K50/baseline replay differs from actual original compact text or full delivered rows")


def source_line_coverage(rows, groups):
    """Separate partial diagnostic: union covered physical gold lines per group.

    Choose the OR alternative with greatest covered fraction, then the original
    label order. Weight the aggregate by that alternative's physical line count.
    Group overlap remains counted once per logical group. This does not grant
    complete evidence credit to a partial span or to a multi-window union.
    """
    covered_total, required_total, details = 0, 0, []
    for alternatives in groups:
        options = []
        for index, target in enumerate(alternatives):
            lo, hi = target["evidence_lines"]
            require(type(lo) is type(hi) is int and 1 <= lo <= hi, "Strict weighted source span")
            covered = set()
            for row in rows:
                if row["path"] == target["path"]:
                    covered.update(range(max(lo, row["start_line"]), min(hi, row["end_line"]) + 1))
            length = hi - lo + 1
            options.append((len(covered) / length, -index, len(covered), length, target))
        require(options, "Nonempty weighted evidence OR group")
        _, _, covered, length, target = max(options, key=lambda item: item[:2])
        covered_total += covered
        required_total += length
        details.append({"path": target["path"], "evidence_lines": target["evidence_lines"],
                        "covered_lines": covered, "required_lines": length})
    return {"covered_lines": covered_total, "required_lines": required_total,
            "fraction": covered_total / required_total if required_total else None,
            "groups": details}


def number_distribution(values):
    require(values and all(type(value) is int and value >= 0 for value in values),
            "Nonempty strict nonnegative integer length population")
    ordered = sorted(values)
    return {"samples": len(values), "sum": sum(values), "median": statistics.median(values),
            "p95": ordered[math.ceil(.95 * len(values)) - 1], "min": ordered[0], "max": ordered[-1]}


def quality(cases):
    keys = [(case["dataset"], case["query_id"]) for case in cases]
    require(len(keys) == len(set(keys)), "Quality must use unique first-pass queries")
    positive = [case for case in cases if case["kind"] == "present"]
    absent = [case for case in cases if case["kind"] == "absent"]
    multi = [case for case in positive if case["multi_file"]]
    covered = sum(case["source_line_coverage"]["covered_lines"] for case in positive)
    required = sum(case["source_line_coverage"]["required_lines"] for case in positive)
    return {"unique_queries": len(cases), "present": len(positive), "absent": len(absent),
            "delivered_complete": sum(None not in case["evidence_ranks"] for case in positive),
            "covered_groups": sum(sum(rank is not None for rank in case["evidence_ranks"]) for case in positive),
            "required_groups": sum(len(case["evidence_ranks"]) for case in positive),
            "candidate_complete": sum(None not in case["candidate_evidence_ranks"] for case in positive),
            "candidate_covered_groups": sum(sum(rank is not None for rank in case["candidate_evidence_ranks"]) for case in positive),
            "multi_file_queries": len(multi),
            "multi_file_complete": sum(None not in case["evidence_ranks"] for case in multi),
            "absent_statuses": dict(Counter(case["match_status"] for case in absent)),
            "absent_returning_decoys": sum(bool(case["delivered"]) for case in absent),
            "weighted_gold_source_line_coverage": {"covered_lines": covered, "required_lines": required,
                                                    "fraction": covered / required if required else None},
            "compact_tokens": number_distribution([case["tokens"] for case in cases])}


def changes(before, after):
    require([(case["dataset"], case["query_id"]) for case in before]
            == [(case["dataset"], case["query_id"]) for case in after], "Matched unique comparison cases")
    result = {key: [] for key in ("complete_gains", "complete_regressions", "group_changes", "rank_changes")}
    for left, right in zip(before, after, strict=True):
        require(left["kind"] == right["kind"], "Comparison kind changed")
        if left["kind"] == "absent":
            continue
        a, b = left["evidence_ranks"], right["evidence_ranks"]
        item = {"dataset": left["dataset"], "query_id": left["query_id"], "before": a, "after": b}
        if None in a and None not in b:
            result["complete_gains"].append(item)
        if None not in a and None in b:
            result["complete_regressions"].append(item)
        if [rank is not None for rank in a] != [rank is not None for rank in b]:
            result["group_changes"].append(item)
        if a != b:
            result["rank_changes"].append(item)
    return result


def prepare(root, study):
    previous, plan = bound_input_audit(root, study)
    datasets = audit.check_sources(root, plan)
    contents = {}
    for row in gz_rows(study / "source-content.jsonl.gz"):
        require(set(row) == {"sha256", "source"} and row["sha256"] not in contents
                and sha(row["source"].encode()) == row["sha256"], "Unique exact content-addressed source")
        contents[row["sha256"]] = row["source"]
    pools = {}
    for row in gz_rows(study / "candidate-pools.jsonl.gz"):
        require(row["pool_id"] not in pools and sha(canonical(row["windows"])) == row["pool_sha256"]
                and row["pool_id"] == row["pool_key"] + "/" + row["pool_sha256"], "Immutable ordered candidate pool")
        raw = [{**window, "source": contents[window["source_hash"]]} for window in row["windows"]]
        data = datasets[row["first_identity"]["dataset"]]
        audit.verify_raw(raw, data["inventory"], data["sources"],
                         score_kind="cosine" if row["pool_key"].endswith("/dense") else "rrf_k60")
        pools[row["pool_id"]] = {"pool": row, "raw": raw}
    rows = gz_rows(study / "searches.jsonl.gz")
    require(len(rows) == 1728 and [row["identity"] for row in rows] == plan["schedule"], "Exact completed old schedule")
    selected = first_pass(rows)
    require(len(selected) == 576, "Exactly four first-pass conditions for all 144 unique queries")
    forward_groups = {}
    for row in gz_rows(study / "forwards.jsonl.gz"):
        if row["warmup"] is False:
            forward_groups.setdefault(row["request_id"], []).append(row)
    config = audit.prototype_constants((root / "scripts/prototype_qwen_reranker.py").read_text())
    tokenizer_path = root / ".cache/models/qwen3-reranker-0.6b-original/tokenizer.json"
    require(sha(tokenizer_path.read_bytes()) == config["PINS"]["tokenizer.json"][1], "Pinned CPU scoring tokenizer bytes")
    from tokenizers import Tokenizer
    tokenizer = Tokenizer.from_file(str(tokenizer_path))
    offline = plan["offline_tokenizer"]
    cache = root / ".cache/tokenizers"
    encoding = audit.evidence.encoder(root, {"runtime": {"tiktoken": offline["tiktoken"],
        "tokenizer_cache_sha256": {path.name: sha(path.read_bytes()) for path in cache.iterdir() if path.is_file()}}})
    return previous, plan, datasets, pools, selected, forward_groups, config, tokenizer, encoding


def case(query, identity, response, raw, *, k, reranked, scored=None):
    groups = query["groups"]
    return {"dataset": identity["dataset"], "query_id": identity["query_id"],
            "retrieval": identity["condition"].split("_")[0], "shortlist": k,
            "reranked": reranked, "repetition": 1, "kind": query["kind"], "multi_file": query["multi_file"],
            "evidence_ranks": audit.ranks(response["results"], groups),
            "candidate_evidence_ranks": audit.ranks(raw, groups),
            "source_line_coverage": source_line_coverage(response["results"], groups),
            "candidate_source_line_coverage": source_line_coverage(raw, groups),
            "scored_source_line_coverage": source_line_coverage(scored, groups) if scored is not None else None,
            "match_status": response["match_status"], "tokens": response["token_usage"]["tokens"],
            "compact_sha256": sha(audit.evidence.compact_text(response).encode()),
            "total_source_chars": response["total_source_chars"],
            "delivered": [{key: value for key, value in row.items() if key != "source"}
                          for row in response["results"]]}


def build_replay(root, study):
    previous, plan, datasets, pools, rows, forwards, config, tokenizer, encoding = prepare(root, study)
    baseline, variants, diagnostics, exact = {}, {}, {}, Counter()
    for retrieval in ("dense", "hybrid"):
        baseline[retrieval] = []
        variants[retrieval] = {k: [] for k in PREFIXES}
        diagnostics[retrieval] = {k: [] for k in PREFIXES}
    for row in rows:
        identity = row["identity"]
        data = datasets[identity["dataset"]]
        query = data["queries"][identity["query_id"]]
        raw, pool = pools[row["pool_id"]]["raw"], pools[row["pool_id"]]["pool"]
        retrieval = identity["condition"].split("_")[0]
        require(pool["pool_key"] == identity["dataset"] + "/" + identity["query_id"] + "/" + retrieval
                and row["candidate_order"] == [window["id"] for window in raw]
                and row["candidate_pool_sha256"] == pool["pool_sha256"], "First-pass exact raw order/pool binding")
        observed = audit.production_order(row["response"])
        scope = audit.expected_scope(data["entry"])
        require(observed["query"] == query["query"] and observed["scope"] == scope,
                "First-pass unchanged literal query and source scope")
        if not identity["condition"].endswith("_qwen"):
            require("reranker" not in row, "Unreranked original baseline must remain separate")
            rebuilt = pack_source(query["query"], scope, raw, data)
            require_exact(rebuilt, observed)
            audit.evidence.verify_response(rebuilt, rebuilt, data["inventory"], data["sources"], encoding, raw)
            baseline[retrieval].append(case(query, identity, rebuilt, raw, k=None, reranked=False))
            exact["baseline"] += 1
            continue
        receipt = row["reranker"]
        pairs = [{"query": query["query"], "source": window["source"]} for window in raw]
        previous_counts = {key: receipt["counts"][key] - (1 if key == "requests" else len(raw))
                           for key in receipt["counts"]}
        request_id = "/".join(str(identity[key]) for key in ("dataset", "query_id", "condition", "repetition"))
        audit.verify_scoring_receipt(receipt, pairs, forwards[request_id], tokenizer, config,
                                     request_id=request_id, previous_counts=previous_counts)
        scores = receipt["results"]
        require(row["ranking_order"] == [window["id"] for window in prefix_ranking(raw, scores, 50)],
                "Full observed score order differs from retained scalar logits")
        cache = {}
        for k in PREFIXES:
            ranked = prefix_ranking(raw, scores, k)
            # Packing only reads eight distinct ranked windows, then their static callees.
            distinct = {}
            for window in ranked:
                distinct.setdefault((window["path"], window["symbol"]), window)
            key = sha(canonical(list(distinct.values())[:8]))
            if key not in cache:
                cache[key] = pack_source(query["query"], scope, ranked, data)
            rebuilt = cache[key]
            audit.evidence.verify_response(rebuilt, rebuilt, data["inventory"], data["sources"], encoding, ranked)
            if k == 50:
                require_exact(rebuilt, observed)
                exact["qwen_k50"] += 1
            scored = [{**window, "end_line": window["start_line"] + score["delivered_lines"] - 1}
                      for window, score in zip(raw[:k], scores[:k], strict=True)]
            variants[retrieval][k].append(case(query, identity, rebuilt, raw[:k], k=k, reranked=True, scored=scored))
            diagnostics[retrieval][k].extend(scores[:k])
    order = {(row["dataset"], row["query_id"]): number for number, row in enumerate(plan["query_catalog"])}
    metrics, comparisons, full_comparisons, lengths = {}, {}, {}, {}
    for retrieval in ("dense", "hybrid"):
        baseline[retrieval].sort(key=lambda item: order[(item["dataset"], item["query_id"])])
        metrics[retrieval] = {"baseline": quality(baseline[retrieval]), "prefixes": {}}
        comparisons[retrieval], full_comparisons[retrieval], lengths[retrieval] = {}, {}, {}
        for k in PREFIXES:
            cases = variants[retrieval][k]
            cases.sort(key=lambda item: order[(item["dataset"], item["query_id"])])
            require(len(cases) == len(baseline[retrieval]) == 144, "Full first-pass query matrix per condition")
            metrics[retrieval]["prefixes"][k] = {"all": quality(cases),
                "previously_observed_koa": quality([item for item in cases if item["dataset"] == "koa"]),
                "repositories": {dataset: quality([item for item in cases if item["dataset"] == dataset])
                                 for dataset in datasets}}
            comparisons[retrieval][k] = changes(baseline[retrieval], cases)
            full_comparisons[retrieval][k] = changes(variants[retrieval][50], cases)
            retained = diagnostics[retrieval][k]
            source_bytes = sum(item["source_utf8_bytes"] for item in retained)
            delivered_bytes = sum(item["delivered_utf8_bytes"] for item in retained)
            lengths[retrieval][k] = {
                "observed_pairs_reused_offline": len(retained),
                "pair_total_tokens": number_distribution([item["total_tokens"] for item in retained]),
                "source_lines": number_distribution([item["source_lines"] for item in retained]),
                "scored_source_lines": number_distribution([item["delivered_lines"] for item in retained]),
                "truncated_scored_pairs": sum(item["truncated"] for item in retained),
                "original_source_utf8_bytes": source_bytes, "scored_source_utf8_bytes": delivered_bytes,
                "weighted_source_byte_retention": delivered_bytes / source_bytes if source_bytes else None}
    require(exact == {"baseline": 288, "qwen_k50": 288}, "Every unchanged full-study baseline and K50 reproduced")
    cases = [item for retrieval in ("dense", "hybrid") for item in baseline[retrieval]]
    cases.extend(item for retrieval in ("dense", "hybrid") for k in PREFIXES for item in variants[retrieval][k])
    result = {"checks_passed": True, "classification": CLASSIFICATION,
              "created_at_utc": datetime.now(timezone.utc).isoformat(), "prefixes": list(PREFIXES),
              "selection_rule": "Original retrieval prefix only; rank retained candidates by observed yes-minus-no logit, original rank then ID; no tail backfill",
              "settings": plan["settings"], "unique_queries": 144, "primary_repetition": 1,
              "all_repositories_previously_observed": True, "model_calls": 0, "query_embedding_calls": 0,
              "retrieval_calls": 0, "latency_measured": False, "exact_original_matches": dict(exact),
              "metrics": metrics, "changes_from_no_reranker": comparisons,
              "changes_from_full_qwen50": full_comparisons, "retained_pair_length_diagnostics": lengths,
              "input_study": str(study.relative_to(root)), "input_frozen_plan_sha256": previous["frozen_plan_sha256"],
              "input_independent_audit_sha256": sha((study / "independent-audit.json").read_bytes()),
              "input_artifact_sha256": previous["artifact_sha256"], "script_sha256": sha(Path(__file__).read_bytes()),
              "limits": [
                  "Scores and source prefixes were observed in the earlier sequential 50-pair study; this replay is post-output development selection, not a new model or retrieval run.",
                  "No replay execution time, shortened sum of old pair times, or token count is reported as warm latency; actual warm experiments must measure the selected shortlist and batching independently.",
                  "Koa was fresh in the earlier study but is observed development now; a new repository must validate the final selected setup without tuning.",
                  "Complete evidence needs a single delivered excerpt covering each original declared full span; partial union gold-line coverage is a separate diagnostic.",
                  "Gold-line weighting picks the best covered OR alternative, ties by label order, and aggregates physical line counts per logical group; overlapping logical groups can count a line twice.",
                  "Candidate coverage is not a delivered-coverage ceiling because unchanged static callee expansion may introduce sources outside the prefix.",
                  "No threshold/absence calibration, numerical batched parity, coding correctness, or provider token savings is established."]}
    return result, cases


def findings(result):
    lines = ["# Smaller Qwen shortlist development replay", "", result["classification"], "",
             "All 144 first-pass queries from eight now-observed repositories are retained. Original no-reranker "
             "conditions remain separate. Only original first-K candidates are sorted by the earlier observed scalar scores. "
             "Source extraction, original labels, five anchors, static callee expansion, eight excerpts, 12,000 source "
             "characters and the exact 4,096-token compact budget remain unchanged.", "",
             "| Retrieval | Prefix | Complete / 118 | Groups / 148 | Multi-file / 21 | Gains / losses vs baseline | Gains / losses vs Qwen50 |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for retrieval in ("dense", "hybrid"):
        baseline = result["metrics"][retrieval]["baseline"]
        lines.append(f"| {retrieval} | no reranker | {baseline['delivered_complete']} | {baseline['covered_groups']} | {baseline['multi_file_complete']} | — | — |")
        for k in PREFIXES:
            metric = result["metrics"][retrieval]["prefixes"][k]["all"]
            change = result["changes_from_no_reranker"][retrieval][k]
            full = result["changes_from_full_qwen50"][retrieval][k]
            lines.append(f"| {retrieval} | {k} | {metric['delivered_complete']} | {metric['covered_groups']} | {metric['multi_file_complete']} | "
                         f"{len(change['complete_gains'])} / {len(change['complete_regressions'])} | "
                         f"{len(full['complete_gains'])} / {len(full['complete_regressions'])} |")
    lines.extend(["", "Every original baseline and Qwen50 result reproduces the actual earlier compact text, "
                  "source order and full delivered rows exactly (288 baseline and 288 Qwen50 checks). "
                  "Every smaller-prefix result is independently checked against literal pinned source, physical spans, "
                  "source hashes, score provenance, dependency anchors and exact token/character/excerpt caps.", "",
                  "`replay.json` retains all per-repository metrics and query-level gains, regressions, group and rank changes. "
                  "`cases.jsonl.gz` retains each unique query/prefix's evidence ranks, partial gold-line diagnostics, "
                  "delivered bounds/hashes and exact compact digest; literal sources remain in the unchanged original study.", "",
                  "Retained pair token and physical-source-length distributions are measured inputs from the earlier scores. "
                  "They describe workload size and source retention; they do not measure shorter-shortlist or batch latency.", ""])
    lines.extend("- " + item for item in result["limits"])
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--study", type=Path, default=ROOT / "results/qwen-reranker-full-2026-10-13")
    parser.add_argument("--output", type=Path, default=ROOT / "results/qwen-shortlist-replay-2026-10-13")
    args = parser.parse_args(argv)
    root, study, output = args.root.resolve(), args.study.resolve(), args.output.resolve()
    require(study.is_relative_to(root) and output.is_relative_to(root) and output != study,
            "Separate repository-owned replay output required")
    require(not output.exists(), "Refuse to overwrite any previous replay attempt")
    result, cases = build_replay(root, study)
    output.mkdir(parents=True)
    with gzip.GzipFile(filename=str(output / "cases.jsonl.gz"), mode="wb", mtime=0) as stream:
        for item in cases:
            stream.write(canonical(item) + b"\n")
    result["cases_sha256"] = sha((output / "cases.jsonl.gz").read_bytes())
    (output / "replay.json").write_text(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n")
    (output / "findings.md").write_text(findings(result))
    print(json.dumps({"checks_passed": result["checks_passed"], "classification": result["classification"],
                      "cases": len(cases), "exact_original_matches": result["exact_original_matches"]}))


if __name__ == "__main__":
    main()
