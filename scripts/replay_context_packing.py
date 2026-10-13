#!/usr/bin/env python3
"""CPU development replay of immutable candidate orders; never retrieves or embeds."""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

FILES = ("__init__", "context", "source", "corpus", "languages", "javascript",
         "javascript_relations", "relations")
SETTINGS = {"candidates": 50, "include_dependencies": True, "max_results": 8,
            "rerank": False, "source_budget": 12000, "token_budget": 4096}
POLICY = "first5-static-callees-backfill-v1"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique(pairs):
    seen, result = set(), []
    for snippet, score, origin, related in pairs:
        key = (snippet.path, snippet.symbol)
        if key not in seen:
            seen.add(key)
            result.append((snippet, score, origin, related))
    return result


def ordered_stream(pairs, graph, *, baseline=False):
    raw = unique([(snippet, score, "retrieval", None) for snippet, score in pairs])
    anchors = raw[:5]
    stream = list(anchors)
    for snippet, score, _, _ in anchors:
        for callee in graph.dependencies(snippet, limit=2):
            stream.append((callee, score, "dependency", snippet.id))
            stream = unique(stream)
            if baseline and len(stream) >= SETTINGS["max_results"]:
                break
        if baseline and len(stream) >= SETTINGS["max_results"]:
            break
    stream = unique([*stream, *(raw[:8] if baseline else raw[5:])])
    return stream[:8] if baseline else stream


def make_row(item, source, lines):
    snippet, score, origin, related = item
    return {"id": snippet.id, "path": snippet.path, "symbol": snippet.symbol,
            "start_line": snippet.start_line,
            "end_line": snippet.start_line + len(lines(source)) - 1,
            "window_end_line": snippet.end_line, "source": source,
            "content_hash": snippet.content_hash, "source_hash": sha(source.encode("utf-8")),
            "retrieval_score": float(score), "h2o_score": None,
            "origin": origin, "related_to": related, "truncated": source != snippet.text}


def char_prefix(source, remaining, lines):
    output, used = [], 0
    for line in lines(source):
        if used + len(line) > remaining:
            break
        output.append(line)
        used += len(line)
    return "".join(output)


def baseline_response(template, stream, context, lines):
    result = copy.deepcopy(template)
    result.pop("token_usage", None)
    result["results"], remaining = [], SETTINGS["source_budget"]
    for item in stream:
        source = char_prefix(item[0].text, remaining, lines)
        if source:
            result["results"].append(make_row(item, source, lines))
            remaining -= len(source)
    result["total_source_chars"] = SETTINGS["source_budget"] - remaining
    result["budget_exhausted"] = (any(row["truncated"] for row in result["results"])
                                  or len(result["results"]) < len(stream))
    return context.apply_token_budget(result, SETTINGS["token_budget"])


def covered(path, start, end, delivered):
    """Containment by the union of inclusive delivered physical-line intervals."""
    cursor = start
    for lo, hi in sorted((row["start_line"], row["end_line"])
                         for row in delivered if row["path"] == path):
        if hi < cursor:
            continue
        if lo > cursor:
            return False
        cursor = max(cursor, hi + 1)
        if cursor > end:
            return True
    return False


def replay_response(template, stream, context, lines, *, containment):
    result = copy.deepcopy(template)
    result["results"] = []
    result["budget_exhausted"] = False
    result["token_usage"] = {"encoding": "cl100k_base", "tokens": 0,
                             "budget": SETTINGS["token_budget"], "truncated": False}
    require(context.count_context(result) <= SETTINGS["token_budget"], "Empty metadata exceeds cap")
    events = []
    for item in stream:
        if len(result["results"]) >= SETTINGS["max_results"]:
            break
        remaining = SETTINGS["source_budget"] - sum(len(row["source"]) for row in result["results"])
        source = char_prefix(item[0].text, remaining, lines)
        if not source:
            events.append({"id": item[0].id, "action": "no_character_prefix"})
            continue
        trial = copy.deepcopy(result)
        row = make_row(item, source, lines)
        trial["results"].append(row)
        trial["budget_exhausted"] |= row["truncated"]
        token_clip = False
        if context.count_context(trial) > SETTINGS["token_budget"]:
            token_clip = True
            trial["budget_exhausted"] = trial["token_usage"]["truncated"] = True
            prefix = lines(source)
            while prefix:
                prefix.pop()
                source = "".join(prefix)
                row.update(make_row(item, source, lines))
                if prefix and context.count_context(trial) <= SETTINGS["token_budget"]:
                    break
            if not prefix:
                events.append({"id": item[0].id, "action": "no_token_prefix"})
                continue
        # Full windows or character-only prefixes cannot stand in for actual delivery.
        if containment and covered(row["path"], row["start_line"], row["end_line"], result["results"]):
            events.append({"id": item[0].id, "action": "contained_after_clip",
                           "start_line": row["start_line"], "end_line": row["end_line"]})
            continue
        result = trial
        events.append({"id": item[0].id, "action": "delivered",
                       "start_line": row["start_line"], "end_line": row["end_line"],
                       "token_clipped": token_clip})
    result["total_source_chars"] = sum(len(row["source"]) for row in result["results"])
    require(context.count_context(result) <= SETTINGS["token_budget"], "Replay exceeds compact cap")
    return result, events


def inputs(root):
    folder = root / "results/language-quality-2026-10-13"
    frozen, preflight = read(folder / "frozen-config.json"), read(folder / "extractor-preflight.json")
    rows = [json.loads(line) for line in (folder / "searches.jsonl").read_text(encoding="utf-8").split("\n") if line.strip()]
    rows = [row for row in rows if row["repetition"] == 1]
    require(len(rows) == 90 and frozen["settings"] == SETTINGS, "Unexpected case count/settings")
    sources, repositories = {}, {}
    for dataset in frozen["sources"]:
        name = dataset["id"]
        repo = (root / next(row["response"]["repo"] for row in rows if row["dataset"] == name)).resolve()
        require(repo.is_relative_to(root), "Corpus is outside supplied project")
        repositories[name] = repo.relative_to(root).as_posix()
        sources[name] = {}
        for path, expected in dataset["source_file_sha256"].items():
            target = (repo / path).resolve()
            require(target.is_relative_to(repo), "Source path escapes corpus")
            raw = target.read_bytes()
            require(sha(raw) == expected, "Pinned source bytes changed: " + path)
            sources[name][path] = raw.decode("utf-8")
    bindings = {str(path.relative_to(root)): sha(path.read_bytes()) for path in
                (folder / "frozen-config.json", folder / "extractor-preflight.json", folder / "searches.jsonl")}
    return frozen, preflight, rows, sources, repositories, bindings


def runtime_bindings(root):
    cache = Path(os.environ.get("TIKTOKEN_CACHE_DIR", root / ".cache/tokenizers"))
    hashes = {path.name: sha(path.read_bytes()) for path in sorted(cache.glob("*")) if path.is_file()}
    require(hashes, "Tokenizer cache missing; use the existing study cache (no replay downloads)")
    os.environ["TIKTOKEN_CACHE_DIR"] = str(cache)
    return {"tiktoken": version("tiktoken"), "tokenizer_cache_sha256": hashes}


def freeze(root, output):
    frozen, _, _, _, repositories, bindings = inputs(root)
    plan = {"classification": "Prospective CPU development replay; Express and already-observed Zod",
            "frozen_at": datetime.now(timezone.utc).isoformat(), "policy": POLICY,
            "settings": SETTINGS, "controls": ["backfill_only", "containment_and_backfill"],
            "baseline_gate": "Exact compact JSON plus full result metadata, chars and flags for all90 first-repetition cases",
            "algorithm": ["Retain first raw ranked window per path/symbol within immutable top50.",
                          "First5 anchors, each anchor's first2 deterministic static callees, then remaining raw symbols; global first-occurrence symbol dedup.",
                          "Clip each trial by whole physical lines to remaining delivered-character budget, then exact compact cl100k JSON cap.",
                          "Containment control skips only clipped prefixes wholly covered by union of already DELIVERED same-path intervals; no partial overlap trimming.",
                          "Continue ordered stream until8 delivered rows or exhaustion. Skips spend no row/character/token capacity. Never substitute another raw window of the same symbol.",
                          "Selectors receive query text for compact metadata, source windows, scores, static graph and caps only; no gold, query IDs, kinds or evidence ranks.",
                          "Budget flags describe delivered truncation, not skipped trial prefixes; skipped reasons live only in replay diagnostics."],
            "limits": ["No retrieval/embedding/reranking rerun or production/default change.",
                       "Backfill control separates containment from expanded stream and post-token character-budget reuse.",
                       "Already-observed-source development replay, not a new holdout, speed, billing or coding-correctness claim."],
            "script_sha256": sha(Path(__file__).read_bytes()), "input_sha256": bindings,
            "runtime": runtime_bindings(root),
            "frozen_git_commit": frozen["git_commit"], "repositories": repositories,
            "module_sha256": {"src/semantic_search/" + name + ".py": frozen["implementation_sha256"]["src/semantic_search/" + name + ".py"] for name in FILES},
            "sources": frozen["sources"]}
    output.mkdir(parents=True, exist_ok=False)
    (output / "plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return plan


def run(root, output):
    plan = read(output / "plan.json")
    require(plan["script_sha256"] == sha(Path(__file__).read_bytes()) and plan["policy"] == POLICY,
            "Replay code differs from prospective freeze")
    frozen, preflight, rows, sources, repositories, bindings = inputs(root)
    require(bindings == plan["input_sha256"] and repositories == plan["repositories"], "Frozen inputs changed")
    require(runtime_bindings(root) == plan["runtime"]
            and plan["runtime"]["tiktoken"] == frozen["packages"]["tiktoken"], "Tokenizer runtime differs from frozen study")
    require(not any(name == "semantic_search" or name.startswith("semantic_search.") for name in sys.modules),
            "Run replay in a clean Python process")
    with tempfile.TemporaryDirectory(prefix="packing-frozen-") as temporary:
        package = Path(temporary) / "semantic_search"
        package.mkdir()
        for path, expected in plan["module_sha256"].items():
            raw = subprocess.check_output(["git", "-C", str(root), "show", plan["frozen_git_commit"] + ":" + path])
            require(sha(raw) == expected, "Frozen implementation mismatch: " + path)
            (package / Path(path).name).write_bytes(raw)
        sys.path.insert(0, temporary)
        context = importlib.import_module("semantic_search.context")
        lines = importlib.import_module("semantic_search.source").physical_lines
        Snippet = importlib.import_module("semantic_search.corpus").Snippet
        Graph = importlib.import_module("semantic_search.relations").ReferenceGraph
        inventories, graphs = {}, {}
        for data in preflight["datasets"]:
            name, windows = data["id"], data["window_inventory"]
            expected = next(x for x in frozen["sources"] if x["id"] == name)
            require(sha(json.dumps(windows, sort_keys=True).encode()) == expected["window_inventory_sha256"], "Inventory changed")
            snippets = []
            for window in windows:
                text = "".join(lines(sources[name][window["path"]])[window["start_line"] - 1:window["end_line"]])
                require(sha(text.encode()) == window["source_hash"], "Inventory source hash differs")
                snippets.append(Snippet(window["id"], window["path"], window["symbol"], window["start_line"], window["end_line"], text, window["source_hash"]))
            inventories[name] = {snippet.id: snippet for snippet in snippets}
            graphs[name] = Graph(sources[name], snippets)
        prepared = []
        for row in rows:
            name = row["dataset"]
            pairs = []
            for window in row["candidate_windows"]:
                snippet = inventories[name][window["id"]]
                require(all(window[key] == getattr(snippet, key) for key in ("path", "symbol", "start_line", "end_line"))
                        and window["source_hash"] == snippet.content_hash, "Candidate identity changed")
                pairs.append((snippet, window["retrieval_score"]))
            template = {key: copy.deepcopy(row["response"][key]) for key in
                        ("query", "scope", "match_status")}
            baseline = baseline_response(template, ordered_stream(pairs, graphs[name], baseline=True), context, lines)
            actual = row["response"]
            require(context.serialize_context(context.compact_result(baseline)) == context.serialize_context(context.compact_result(actual)),
                    "Baseline compact mismatch: " + row["query_id"] + "/" + row["condition"])
            require(baseline["results"] == actual["results"] and baseline["total_source_chars"] == actual["total_source_chars"],
                    "Baseline full result mismatch")
            prepared.append((row, template, ordered_stream(pairs, graphs[name])))
        # No replay is computed or written unless every baseline passes.
        outputs = []
        for row, template, stream in prepared:
            item = {"dataset": row["dataset"], "query_id": row["query_id"], "condition": row["condition"],
                    "baseline": row["response"]}
            for name, containment in (("backfill_only", False), ("containment_and_backfill", True)):
                response, events = replay_response(template, stream, context, lines, containment=containment)
                item[name] = {"response": response, "events": events}
            outputs.append(item)
        target = output / "replay.jsonl"
        with target.open("x", encoding="utf-8") as handle:
            for item in outputs:
                handle.write(json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n")
        summary = {"classification": plan["classification"], "plan_sha256": sha((output / "plan.json").read_bytes()),
                   "replay_sha256": sha(target.read_bytes()), "baseline_exact_cases": len(prepared), "model_calls": 0,
                   "retrieval_calls": 0, "cases": len(outputs)}
        (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze", action="store_true")
    mode.add_argument("--run", action="store_true")
    args = parser.parse_args()
    result = freeze(args.root.resolve(), args.output.resolve()) if args.freeze else run(args.root.resolve(), args.output.resolve())
    print(json.dumps({"policy": POLICY, "output": str(args.output), "cases": result.get("cases"),
                      "classification": result["classification"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
