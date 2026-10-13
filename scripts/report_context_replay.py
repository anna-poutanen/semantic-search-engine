#!/usr/bin/env python3
"""Independently verify replay bytes/caps; no gold labels or production helpers."""
import argparse
from collections import defaultdict
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import re


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def physical_lines(text):
    return [line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text) if line]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify(root, output):
    plan, summary = read(output / "plan.json"), read(output / "summary.json")
    require(sha((output / "plan.json").read_bytes()) == summary["plan_sha256"], "Plan hash changed")
    require(sha((output / "replay.jsonl").read_bytes()) == summary["replay_sha256"], "Replay hash changed")
    for name, expected in plan["input_sha256"].items():
        require(sha((root / name).read_bytes()) == expected, "Frozen input changed")
    rows = [json.loads(line) for line in (output / "replay.jsonl").read_text(encoding="utf-8").split("\n") if line.strip()]
    require(len(rows) == 90 and len({(row["dataset"], row["query_id"], row["condition"]) for row in rows}) == 90,
            "First-repetition case matrix differs")
    preflight = read(root / "results/language-quality-2026-10-13/extractor-preflight.json")
    inventories = {data["id"]: {window["id"]: window for window in data["window_inventory"]}
                   for data in preflight["datasets"]}
    sources = {}
    for data in plan["sources"]:
        sources[data["id"]] = {}
        for name, expected in data["source_file_sha256"].items():
            raw = (root / plan["repositories"][data["id"]] / name).read_bytes()
            require(sha(raw) == expected, "Pinned corpus bytes changed")
            sources[data["id"]][name] = physical_lines(raw.decode("utf-8"))
    # Independent tokenizer setup uses only verified, captured cache bytes.
    url = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
    expected_hash = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
    cache_key = hashlib.sha1(url.encode()).hexdigest()
    raw = (root / ".cache/tokenizers" / cache_key).read_bytes()
    require(sha(raw) == expected_hash and version("tiktoken") == plan["runtime"]["tiktoken"], "Tokenizer differs")
    import tiktoken
    import tiktoken.load
    def captured(blobpath, expected_hash=None):
        require(blobpath == url and expected_hash == sha(raw), "Unexpected tokenizer data request")
        return raw
    tiktoken.load.read_file_cached = captured
    encoding = tiktoken.get_encoding("cl100k_base")
    variants = ("baseline", "backfill_only", "containment_and_backfill")
    source_rows, groups = 0, defaultdict(list)
    for item in rows:
        groups[(item["dataset"], item["condition"])].append(item)
        for name in variants:
            response = item[name] if name == "baseline" else item[name]["response"]
            require(len(response["results"]) <= 8, "Excerpt cap exceeded")
            require(len({(row["path"], row["symbol"]) for row in response["results"]}) == len(response["results"]),
                    "Symbol deduplication differs")
            require(response["query"] == item["baseline"]["query"] and response["scope"] == item["baseline"]["scope"],
                    "Query/scope differs from baseline")
            require(response["token_usage"]["budget"] == 4096 and response["token_usage"]["encoding"] == "cl100k_base",
                    "Declared compact cap/encoding differs")
            require(sum(len(row["source"]) for row in response["results"]) == response["total_source_chars"] <= 12000,
                    "Source character cap/count differs")
            compact = []
            for row in response["results"]:
                full = inventories[item["dataset"]][row["id"]]
                require(all(row[key] == full[key] for key in ("path", "symbol", "start_line")), "Window metadata differs")
                require(row["window_end_line"] == full["end_line"] and row["content_hash"] == full["source_hash"], "Full window identity differs")
                require(row["start_line"] <= row["end_line"] <= full["end_line"], "Delivered bounds differ")
                text = "".join(sources[item["dataset"]][row["path"]][row["start_line"] - 1:row["end_line"]])
                require(row["source"] == text and row["source_hash"] == sha(text.encode()), "Delivered bytes/hash differ")
                require(row["truncated"] == (row["end_line"] < full["end_line"]), "Truncation flag differs")
                source_rows += 1
                compact.append({**{key: row[key] for key in
                                    ("path", "symbol", "start_line", "end_line", "source", "source_hash", "truncated")},
                                "origin": row.get("origin", "retrieval"), "related_to": row.get("related_to")})
            envelope = {"query": response["query"], "match_status": response["match_status"], "results": compact,
                        "budget_exhausted": response["budget_exhausted"], "token_usage": response["token_usage"], "scope": response["scope"]}
            text = json.dumps(envelope, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            require(len(encoding.encode(text, disallowed_special=())) == response["token_usage"]["tokens"] <= 4096,
                    "Compact token cap/count differs")
    audit = {"classification": "Independent CPU saved-byte and exact compact-cap verification; no gold scoring",
             "checks_passed": True, "responses_checked": len(rows) * 3, "source_rows_checked": source_rows,
             "source_files_checked": sum(len(paths) for paths in sources.values()),
             "labels_used": False, "production_helpers_used": False,
             "script_sha256": sha(Path(__file__).read_bytes()), "replay_sha256": summary["replay_sha256"]}
    (output / "cpu-audit.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    lines = ["# Context packing development replay", "",
             "The prospective plan and both controls were committed before any replay output. The final pre-output commit is `ead12e0`; the initial `b35d4ea` plan was superseded only to close an offline tokenizer-cache guard gap found by independent review. No outcome informed that repair.", "",
             f"All **{summary['baseline_exact_cases']} first-repetition baselines** matched exact compact JSON, full rows and source characters before either control was computed. Both controls reused immutable candidate orders and pinned source/static-reference/context code. No model or retrieval calls were made.", "",
             f"A separate verifier checked **{audit['responses_checked']} responses**, **{source_rows} exact saved-source excerpts** and **{audit['source_files_checked']} pinned source files** without production helpers or gold labels. All responses satisfy the eight-excerpt, 12,000-character and 4,096 exact compact-token caps.", "",
             "| Repository / condition | Baseline rows | Backfill rows | Containment + backfill rows | Control cases changed / 15 | Covered-prefix skips |",
             "|---|---:|---:|---:|---:|---:|"]
    total_changed = total_skips = 0
    for (dataset, condition), cases in sorted(groups.items()):
        counts = [sum(len((item[name] if name == "baseline" else item[name]["response"])["results"]) for item in cases) for name in variants]
        changed = sum(item["backfill_only"]["response"]["results"] != item["containment_and_backfill"]["response"]["results"] for item in cases)
        skips = sum(event["action"] == "contained_after_clip" for item in cases for event in item["containment_and_backfill"]["events"])
        total_changed += changed
        total_skips += skips
        lines.append(f"| {dataset} / {condition} | {counts[0]} | {counts[1]} | {counts[2]} | {changed} | {skips} |")
    lines += ["", f"Containment changed **{total_changed}/90** control responses and skipped **{total_skips}** wholly covered prefixes after clipping. The table includes present and absent authored queries. Row counts measure delivery, not relevance. Some streams deliver fewer rows because later whole lines do not fit; backfill does not guarantee eight usable excerpts.", "",
              "The selector receives no target paths, evidence lines, query kinds or ranks. This report also uses no gold labels. Evidence improvements, losses and multi-file coverage must be scored separately against the retained labels before interpreting relevance. Containment only considers earlier delivered intervals; later larger windows may still overlap or contain earlier smaller excerpts.", "",
              "Both repositories and Zod outcomes had already been observed. This is a development counterfactual on frozen candidate orders, not a new holdout, production/default change, retrieval rerun, speed measurement, provider-token/billing result or coding-correctness study. The existing miss categories do not prove that packing caused those misses.", "",
              f"Plan SHA256: `{summary['plan_sha256']}`. Replay SHA256: `{summary['replay_sha256']}`.", "",
              "See [the prospective protocol](../../docs/context-packing-replay.md), [plan](plan.json), [raw replay](replay.jsonl), [run summary](summary.json) and [independent CPU audit](cpu-audit.json).", ""]
    (output / "findings.md").write_text("\n".join(lines), encoding="utf-8")
    return audit


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.root.resolve(), args.output.resolve()), indent=2))
