#!/usr/bin/env python3
"""Score completed packing replays from exact saved bytes; no runtime/model imports."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import re
import statistics
import subprocess
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = ("baseline", "backfill_only", "containment_and_backfill")
CAPS = {"candidates": 50, "include_dependencies": True, "max_results": 8,
        "rerank": False, "source_budget": 12000, "token_budget": 4096}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]


def physical_lines(text):
    return [line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text) if line]


def located(root, relative):
    require(isinstance(relative, str) and relative and "\\" not in relative and ":" not in relative
            and all(part not in ("", ".", "..") for part in relative.split("/")), "Unsafe relative path")
    path = (root / relative).resolve()
    require(path.is_relative_to(root.resolve()), "Path leaves the supplied root")
    return path


def compact_text(response):
    fields = ("path", "symbol", "start_line", "end_line", "source", "source_hash", "truncated")
    rows = [{**{key: row[key] for key in fields}, "origin": row.get("origin", "retrieval"),
             "related_to": row.get("related_to")} for row in response["results"]]
    result = {"query": response["query"], "match_status": response["match_status"], "results": rows,
              "budget_exhausted": response["budget_exhausted"], "token_usage": response["token_usage"]}
    if "scope" in response:
        result["scope"] = response["scope"]
    return json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def encoder(root, plan):
    url = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
    expected = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
    cache = root / ".cache/tokenizers"
    raw = (cache / hashlib.sha1(url.encode()).hexdigest()).read_bytes()
    require(sha(raw) == expected and importlib.metadata.version("tiktoken") == plan["runtime"]["tiktoken"], "Tokenizer runtime/cache differs")
    require({p.name: sha(p.read_bytes()) for p in cache.iterdir() if p.is_file()}
            == plan["runtime"]["tokenizer_cache_sha256"], "Tokenizer cache inventory differs")
    import tiktoken
    import tiktoken.load

    def captured(blobpath, expected_hash=None):
        require(blobpath == url and expected_hash == expected, "Unexpected tokenizer data request; network is forbidden")
        return raw

    with patch.object(tiktoken.load, "read_file_cached", captured):
        return tiktoken.get_encoding("cl100k_base")


def gold_groups(query, sources, hashes):
    if query.get("kind") == "absent":
        require(query.get("targets") == [] and "target_groups" not in query, "Absent query has gold targets")
        return []
    require(query.get("kind") == "present" and not ("targets" in query and "target_groups" in query), "Invalid positive kind/groups")
    groups = query.get("target_groups", [query.get("targets")])
    require(isinstance(groups, list) and groups and all(isinstance(group, list) and group for group in groups), "Empty positive gold group")
    for group in groups:
        for target in group:
            require(target["path"] in sources, "Gold target is outside pinned saved sources")
            bounds, definition = target["evidence_lines"], target["definition_lines"]
            require(isinstance(bounds, list) and isinstance(definition, list) and len(bounds) == len(definition) == 2
                    and all(type(value) is int for value in [*bounds, *definition])
                    and 1 <= definition[0] <= bounds[0] <= bounds[1] <= definition[1] <= len(sources[target["path"]]), "Invalid full gold evidence interval")
            source = "".join(sources[target["path"]][bounds[0] - 1:bounds[1]])
            require(target["source_file_sha256"] == hashes[target["path"]]
                    and target["evidence_sha256"] == sha(source.encode()), "Gold evidence/source hash differs")
    return groups


def evidence_ranks(rows, groups):
    # Source-only coverage is the frozen language-study contract. Symbol labels
    # do not disqualify a verified enclosing window containing all evidence.
    return [next((rank for rank, row in enumerate(rows, 1) if any(row["path"] == target["path"]
                  and row["start_line"] <= target["evidence_lines"][0] <= target["evidence_lines"][1] <= row["end_line"]
                  for target in alternatives)), None) for alternatives in groups]


def verify_response(response, baseline, inventory, sources, encoding, raw_candidates):
    require(response["query"] == baseline["query"] and response["scope"] == baseline["scope"]
            and response["match_status"] == baseline["match_status"], "Query/scope/status changed")
    rows, usage = response["results"], response["token_usage"]
    require(isinstance(rows, list) and len(rows) <= 8
            and len({(row["path"], row["symbol"]) for row in rows}) == len(rows), "Excerpt cap/symbol dedup differs")
    require(usage["encoding"] == "cl100k_base" and type(usage["tokens"]) is int
            and type(usage["budget"]) is int and usage["budget"] == 4096
            and type(usage["truncated"]) is bool and type(response["budget_exhausted"]) is bool, "Declared budget/types differ")
    distinct = {}
    for item in raw_candidates:
        distinct.setdefault((item["path"], item["symbol"]), item)
    anchors = {row["id"]: row for row in list(distinct.values())[:5]}
    for row in rows:
        require(row["id"] in inventory, "Delivered ID is not a frozen window")
        full = inventory[row["id"]]
        require(all(row[key] == full[key] for key in ("path", "symbol", "start_line")), "Delivered window identity changed")
        start, end, window_end = row["start_line"], row["end_line"], row["window_end_line"]
        require(all(type(value) is int for value in (start, end, window_end))
                and 1 <= start <= end <= window_end == full["end_line"] <= len(sources[row["path"]]), "Delivered physical bounds changed")
        actual = "".join(sources[row["path"]][start - 1:end])
        whole = "".join(sources[row["path"]][start - 1:window_end])
        require(row["source"] == actual and row["source_hash"] == sha(actual.encode())
                and row["content_hash"] == full["source_hash"] == sha(whole.encode()), "Exact delivered/full-window bytes/hash differ")
        require(type(row["truncated"]) is bool and row["truncated"] == (end < window_end)
                and end - start + 1 == len(physical_lines(row["source"])), "Whole physical lines/truncation changed")
        require(type(row["retrieval_score"]) in (int, float) and math.isfinite(row["retrieval_score"])
                and row["h2o_score"] is None, "Unreranked score provenance differs")
        if row["origin"] == "retrieval":
            original = distinct.get((row["path"], row["symbol"]))
            require(original is not None and original["id"] == row["id"] and row["related_to"] is None
                    and row["retrieval_score"] == original["retrieval_score"], "First raw symbol/window/score changed")
        else:
            require(row["origin"] == "dependency" and row["related_to"] in anchors
                    and row["retrieval_score"] == anchors[row["related_to"]]["retrieval_score"], "Static context anchor provenance differs")
    require(type(response["total_source_chars"]) is int and response["total_source_chars"]
            == sum(len(row["source"]) for row in rows) <= 12000, "Actual source-character count/cap differs")
    require(len(encoding.encode(compact_text(response), disallowed_special=())) == usage["tokens"] <= 4096,
            "Actual compact-token count/cap differs")
    return len(rows)


def metrics(rows):
    positive = [row for row in rows if row["kind"] == "present"]
    negative = [row for row in rows if row["kind"] == "absent"]
    complete = [max(row["ranks"]) if None not in row["ranks"] else None for row in positive]
    multi = [row for row in positive if row["multi_file"]]
    return {"cases": len(rows), "present": len(positive), "absent": len(negative),
            "complete": sum(rank is not None for rank in complete),
            "required_groups": sum(len(row["ranks"]) for row in positive),
            "covered_groups": sum(sum(rank is not None for rank in row["ranks"]) for row in positive),
            "multi_file_queries": len(multi), "multi_file_complete": sum(None not in row["ranks"] for row in multi),
            **{f"complete_at_{depth}": sum(rank is not None and rank <= depth for rank in complete) for depth in (1, 3, 5, 8)},
            "complete_evidence_mrr": sum(1 / rank for rank in complete if rank is not None) / len(positive) if positive else None,
            "absent_statuses": dict(Counter(row["match_status"] for row in negative)),
            "median_compact_tokens": statistics.median(row["tokens"] for row in rows) if rows else None,
            "max_compact_tokens": max((row["tokens"] for row in rows), default=None)}


def changes(rows, left, right):
    result = {"comparison": f"{right} versus {left}", "complete_gains": [], "complete_regressions": [],
              "group_changes": [], "rank_changes": []}
    for row in rows:
        if row["kind"] != "present":
            continue
        a, b = row["variants"][left]["ranks"], row["variants"][right]["ranks"]
        identity = {key: row[key] for key in ("dataset", "query_id", "condition")}
        record = {**identity, "before": a, "after": b}
        if None in a and None not in b:
            result["complete_gains"].append(record)
        if None not in a and None in b:
            result["complete_regressions"].append(record)
        if [value is not None for value in a] != [value is not None for value in b]:
            result["group_changes"].append(record)
        if a != b:
            result["rank_changes"].append(record)
    return result


def verify_bindings(plan, frozen):
    names = ("__init__", "context", "source", "corpus", "languages", "javascript", "javascript_relations", "relations")
    expected = {"src/semantic_search/" + name + ".py": frozen["implementation_sha256"]["src/semantic_search/" + name + ".py"] for name in names}
    require(plan["sources"] == frozen["sources"] and plan["frozen_git_commit"] == frozen["git_commit"]
            and plan["module_sha256"] == expected, "Replay provenance differs from the original frozen source/modules")
    require(plan["settings"] == frozen["settings"] == CAPS
            and all(type(plan["settings"][key]) is type(value) for key, value in CAPS.items()), "Replay setting types differ")


def verify_cases(original, replay, schedule):
    def identity(row):
        return row["dataset"], row["query_id"], row["condition"]

    expected = [identity(row) for row in schedule if row["repetition"] == 1]
    require(len(original) == len(expected) == 90 and [identity(row) for row in original] == expected,
            "Original first-repetition matrix differs")
    require(len(replay) == len(set(map(identity, replay))) == 90 and list(map(identity, replay)) == expected,
            "Replay case identities/order/count differ")


def build_report(root, study, *, encoding=None):
    plan, summary = read(study / "plan.json"), read(study / "summary.json")
    require(sha((study / "plan.json").read_bytes()) == summary["plan_sha256"]
            and sha((study / "replay.jsonl").read_bytes()) == summary["replay_sha256"], "Replay/plan hash changed")
    require(plan["settings"] == CAPS and plan["controls"] == list(VARIANTS[1:])
            and summary["baseline_exact_cases"] == summary["cases"] == 90
            and summary["model_calls"] == summary["retrieval_calls"] == 0
            and all(type(summary[key]) is int for key in ("baseline_exact_cases", "cases", "model_calls", "retrieval_calls")), "Prospective settings/counts differ")
    require(sha((root / "scripts/replay_context_packing.py").read_bytes()) == plan["script_sha256"], "Selector script changed")
    for name, expected in plan["input_sha256"].items():
        require(sha(located(root, name).read_bytes()) == expected, "Frozen replay input changed: " + name)
    for name, expected in plan["module_sha256"].items():
        located(root, name)
        raw = subprocess.check_output(["git", "-C", str(root), "show", plan["frozen_git_commit"] + ":" + name], timeout=10)
        require(sha(raw) == expected, "Frozen module hash changed")
    frozen = read(root / "results/language-quality-2026-10-13/frozen-config.json")
    verify_bindings(plan, frozen)
    manifest_path = root / "benchmarks/language_quality_manifest.json"
    require(sha(manifest_path.read_bytes()) == frozen["manifest_sha256"], "Original label manifest changed")
    manifest = read(manifest_path)
    preflight = read(root / "results/language-quality-2026-10-13/extractor-preflight.json")
    original = [row for row in jsonl(root / "results/language-quality-2026-10-13/searches.jsonl") if row["repetition"] == 1]
    replay = jsonl(study / "replay.jsonl")
    verify_cases(original, replay, frozen["schedule"])
    labels, sources, hashes, inventories = {}, {}, {}, {}
    label_hashes, files_checked = {}, 0
    for entry in manifest["datasets"]:
        name = entry["id"]
        corpus = located(root, plan["repositories"][name])
        dataset_path = located(root, entry["dataset"])
        require(sha(dataset_path.read_bytes()) == entry["sha256"], "Gold dataset changed")
        data = read(dataset_path)
        require(data["revision"] == entry["revision"] == subprocess.check_output(["git", "-C", str(corpus), "rev-parse", "HEAD"], text=True).strip(), "Pinned corpus revision changed")
        require(len(data["queries"]) == entry["queries"] and len({q["id"] for q in data["queries"]}) == entry["queries"], "Gold query count/uniqueness differs")
        label_hashes[name] = entry["sha256"]
        source_plan = next(value for value in plan["sources"] if value["id"] == name)
        sources[name], hashes[name] = {}, source_plan["source_file_sha256"]
        for path, expected in hashes[name].items():
            raw = located(corpus, path).read_bytes()
            require(sha(raw) == expected, "Pinned saved source changed")
            sources[name][path] = physical_lines(raw.decode("utf-8"))
            files_checked += 1
        windows = next(value["window_inventory"] for value in preflight["datasets"] if value["id"] == name)
        require(sha(json.dumps(windows, sort_keys=True).encode()) == source_plan["window_inventory_sha256"]
                and len(windows) == source_plan["windows"] == len({value["id"] for value in windows}), "Frozen window inventory changed")
        inventories[name] = {value["id"]: value for value in windows}
        for window in windows:
            start, end = window["start_line"], window["end_line"]
            require(window["path"] in sources[name] and type(start) is type(end) is int
                    and 1 <= start <= end <= len(sources[name][window["path"]])
                    and sha("".join(sources[name][window["path"]][start - 1:end]).encode()) == window["source_hash"],
                    "Frozen full-window bytes/bounds differ")
        labels[name] = {query["id"]: query for query in data["queries"]}
        gold_inventory = []
        for query in data["queries"]:
            gold_inventory.append(gold_groups(query, sources[name], hashes[name]))
        require(sum(query["kind"] == "present" for query in data["queries"]) == entry["present_queries"]
                and sum(query["kind"] == "absent" for query in data["queries"]) == entry["absent_queries"]
                and sum(len(groups) > 1 for groups in gold_inventory) == entry["multi_file_and_queries"]
                and sum(len(groups) for groups in gold_inventory) == entry["required_evidence_groups"]
                and sum(len(group) for groups in gold_inventory for group in groups) == entry["gold_target_rows"],
                "Frozen gold denominators differ")
    encoding = encoding or encoder(root, plan)
    scored, source_rows = [], 0
    for current, old in zip(replay, original, strict=True):
        require(current["baseline"] == old["response"], "Retained baseline differs from original response")
        name, query = current["dataset"], labels[current["dataset"]][current["query_id"]]
        require(current["baseline"]["query"] == query["query"], "Gold query text does not match request")
        groups = gold_groups(query, sources[name], hashes[name])
        require(len(old["candidate_windows"]) <= 50 and len({row["id"] for row in old["candidate_windows"]}) == len(old["candidate_windows"]), "Raw candidate count/identity differs")
        for window in old["candidate_windows"]:
            require(window["id"] in inventories[name] and all(window[key] == inventories[name][window["id"]][key]
                    for key in ("path", "symbol", "start_line", "end_line", "source_hash"))
                    and type(window["retrieval_score"]) in (int, float) and math.isfinite(window["retrieval_score"]),
                    "Raw candidate metadata/score differs from pinned inventory")
        item = {key: current[key] for key in ("dataset", "query_id", "condition")}
        item.update(kind=query["kind"], multi_file=len(groups) > 1, variants={})
        for variant in VARIANTS:
            response = current[variant] if variant == "baseline" else current[variant]["response"]
            source_rows += verify_response(response, current["baseline"], inventories[name], sources[name], encoding, old["candidate_windows"])
            ranks = evidence_ranks(response["results"], groups)
            if variant == "baseline":
                require(ranks == old["evidence_ranks"], "Independent baseline gold ranks differ from historical ranks")
            item["variants"][variant] = {"ranks": ranks, "tokens": response["token_usage"]["tokens"],
                                         "match_status": response["match_status"], "source_chars": response["total_source_chars"]}
        scored.append(item)
    metrics_by_group = {}
    for name in sources:
        metrics_by_group[name] = {}
        for condition in frozen["conditions"]:
            subset = [row for row in scored if row["dataset"] == name and row["condition"] == condition]
            require(len(subset) == 15, "Repository/condition denominator differs")
            metrics_by_group[name][condition] = {variant: metrics([{**row, **row["variants"][variant]} for row in subset]) for variant in VARIANTS}
    return {"classification": "Observed Express/Zod development replay; independent post-selection full-span gold scoring",
            "checks_passed": True, "plan_sha256": summary["plan_sha256"], "replay_sha256": summary["replay_sha256"],
            "label_sha256": label_hashes, "scorer_sha256": sha(Path(__file__).read_bytes()),
            "baseline_exact_cases": 90, "responses_checked": 270, "source_rows_checked": source_rows,
            "source_files_checked": files_checked, "cases": scored, "metrics": metrics_by_group,
            "comparisons": [changes(scored, "baseline", "backfill_only"),
                            changes(scored, "backfill_only", "containment_and_backfill"),
                            changes(scored, "baseline", "containment_and_backfill")],
            "limits": ["Both repositories and Zod outcomes were already observed; this is not a new repository/model-pretraining holdout.",
                       "A gold OR group is covered by one verified excerpt containing the complete executable span; all groups are required for AND completion. Symbol names alone never earn credit.",
                       "Selection was frozen before this scorer accessed labels; no retrieval, inference, reranking, latency or provider billing measurement is performed.",
                       "Only first-repetition query/condition cases are scored. Conditions reuse the same30 authored queries; aggregate cases are not independent user trials.",
                       "Fixed-order development counterfactual; no production/default change, broad causal claim or coding-correctness claim."]}


def render(result):
    positives = sum(row["kind"] == "present" for row in result["cases"])
    negatives = len(result["cases"]) - positives
    distinct_queries = len({(row["dataset"], row["query_id"]) for row in result["cases"]})
    lines = ["# Development packing replay: independent evidence scores", "",
             "Both Express and Zod and their earlier outcomes had already been observed. These scores describe a post-selection development replay of fixed candidate orders. They do not create a new holdout or change the shipped packing policy.", "",
             f"All {result['baseline_exact_cases']} first-repetition baselines match the original responses. The independent scorer checked {result['responses_checked']} payloads and their actual compact token caps, {result['source_rows_checked']} saved-source excerpts and {result['source_files_checked']} pinned files. Gold credit requires a complete executable evidence span in one verified excerpt; OR alternatives and all required AND groups retain the original language-study rule.", "",
             "| Repository / retrieval | Complete baseline | Backfill only | Containment + backfill | AND baseline / backfill / containment | Groups baseline / backfill / containment |", "|---|---:|---:|---:|---|---|"]
    for repository, conditions in result["metrics"].items():
        for condition, variants in conditions.items():
            values = [variants[name] for name in VARIANTS]
            lines.append(f"| {repository} / {condition} | {values[0]['complete']}/{values[0]['present']} | {values[1]['complete']}/{values[1]['present']} | {values[2]['complete']}/{values[2]['present']} | "
                         + " / ".join(f"{value['multi_file_complete']}/{value['multi_file_queries']}" for value in values) + " | "
                         + " / ".join(f"{value['covered_groups']}/{value['required_groups']}" for value in values) + " |")
    lines += ["", "| Paired comparison | Complete gains | Complete regressions | Cases with changed group coverage | Cases with changed evidence ranks |",
              "|---|---:|---:|---:|---:|"]
    for comparison in result["comparisons"]:
        lines.append(f"| {comparison['comparison']} | {len(comparison['complete_gains'])} | {len(comparison['complete_regressions'])} | {len(comparison['group_changes'])} | {len(comparison['rank_changes'])} |")
    lines += ["", f"These comparisons include all {positives} positive query/condition cases, including regressions. The {negatives} absent cases have no executable gold and are reported separately in the JSON; returned candidates do not establish absence or an abstention success. Retrieval conditions reuse the same {distinct_queries} authored queries, so pooled cases are not independent user trials.", "",
              "The backfill control uses the same expanded stream and reclaims characters removed by token clipping. Compare containment with that control before attributing differences to containment. Neither replay measures fresh rankings, speed, model behavior, provider tokens/billing or coding correctness. All source caps remain eight excerpts, 12,000 characters and 4,096 exact compact tokens.", "",
              "Per-query rank transitions, complete gains/losses and all denominators are retained in [gold-scoring.json](gold-scoring.json). Original selector output and no-gold technical findings remain unchanged.", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--study", type=Path, default=ROOT / "results/context-packing-development-2026-10-13")
    args = parser.parse_args(argv)
    result = build_report(args.root.resolve(), args.study.resolve())
    (args.study / "gold-scoring.json").write_text(json.dumps(result, indent=2) + "\n")
    (args.study / "gold-scoring.md").write_text(render(result))
    print(json.dumps({"checks_passed": result["checks_passed"], "responses": result["responses_checked"], "source_rows": result["source_rows_checked"]}))


if __name__ == "__main__":
    main()
