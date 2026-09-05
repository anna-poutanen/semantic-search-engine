#!/usr/bin/env python3
"""CPU provenance/bounds audit of source-authored JS/TS labels, no retrieval.

This checks bytes, paths, declarations and metadata. A human source review must
separately decide whether the query behavior and scoped absence labels are true.
No production extractor or model implementation is imported or evaluated.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SUFFIXES = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.PIPE)


def physical_lines(text):
    return [line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text) if line]


def groups(row):
    if row["kind"] == "absent":
        require(row.get("targets") == [] and "target_groups" not in row, f"Absent gold: {row['id']}")
        require(row.get("absence_review", {}).get("scope") and row["absence_review"].get("closest_decoys"),
                f"Missing absence review: {row['id']}")
        return []
    require(row["kind"] == "present", f"Unknown label: {row['id']}")
    require(not ("targets" in row and "target_groups" in row), f"Ambiguous gold semantics: {row['id']}")
    result = row.get("target_groups", [row.get("targets")])
    require(isinstance(result, list) and result and all(isinstance(g, list) and g for g in result),
            f"Empty gold groups: {row['id']}")
    return result


def inspect(entry):
    dataset_path = ROOT / entry["dataset"]
    require(digest(dataset_path.read_bytes()) == entry["sha256"], f"Dataset changed: {entry['id']}")
    dataset = json.loads(dataset_path.read_text())
    repo = ROOT / entry["corpus"]
    require(git(repo, "rev-parse", "HEAD").decode().strip() == entry["revision"], f"Wrong corpus HEAD: {entry['id']}")
    require(not git(repo, "status", "--porcelain").strip(), f"Dirty corpus: {entry['id']}")
    require(git(repo, "rev-parse", f"{entry['release']}^{{commit}}").decode().strip() == entry["revision"],
            f"Release tag differs: {entry['id']}")
    for field in ("repo_url", "revision", "release", "source_root", "source_exclusions", "language"):
        require(dataset[field] == entry[field], f"Dataset metadata differs: {field}")
    license_bytes = git(repo, "show", f"{entry['revision']}:LICENSE")
    require((ROOT / entry["license_path"]).read_bytes() == license_bytes
            and digest(license_bytes) == entry["license_sha256"], f"License differs: {entry['id']}")
    filenames = git(repo, "ls-tree", "-r", "--name-only", "-z", entry["revision"], "--", entry["source_root"])
    names = sorted(name.decode() for name in filenames.split(b"\0") if name)
    names = [name for name in names if Path(name).suffix in SUFFIXES
             and not any(fnmatch.fnmatchcase(name, pattern) for pattern in entry["source_exclusions"])]
    sources, hashes = {}, {}
    for name in names:
        raw = git(repo, "show", f"{entry['revision']}:{name}")
        require((repo / name).read_bytes() == raw, f"Source differs from Git: {name}")
        sources[name] = raw.decode("utf-8")
        hashes[name] = digest(raw)
    rows = dataset["queries"]
    ids = [row["id"] for row in rows]
    require(len(set(ids)) == len(ids), "Duplicate query identities")
    method_count, required_groups, multiple, target_rows = 0, 0, 0, []
    absent_searches = []
    for row in rows:
        gold = groups(row)
        required_groups += len(gold)
        paths = set()
        for group in gold:
            for target in group:
                name = target["path"]
                require(name in sources, f"Gold outside production scope: {name}")
                require(target["source_file_sha256"] == hashes[name], f"Gold file hash differs: {row['id']}")
                lines = physical_lines(sources[name])
                a, b = target["definition_lines"]
                lo, hi = target["evidence_lines"]
                require(all(type(value) is int for value in (a, b, lo, hi))
                        and 1 <= a <= lo <= hi <= b <= len(lines), f"Invalid gold bounds: {row['id']}/{target['symbol']}")
                require(target["declaration_anchor"] in "".join(lines[a-1:min(a+3,b)]),
                        f"Declaration anchor differs: {row['id']}/{target['symbol']}")
                require(lines[b-1].strip() in {"}", "};", "});"},
                        f"Definition ending differs: {row['id']}/{target['symbol']} line{b}: {lines[b-1].strip()!r}")
                excerpt = "".join(lines[lo-1:hi])
                require(digest(excerpt.encode()) == target["evidence_sha256"], f"Evidence changed: {row['id']}")
                require(re.search(r"\b(return|if|for|while|throw|new|const|var|let)\b|\w+\(", excerpt),
                        f"No executable evidence: {row['id']}")
                windows = [(start, min(start + 159, b)) for start in range(a, b + 1, 120)]
                eligible = [(start, end) for start, end in windows if start <= lo <= hi <= end]
                require(eligible, f"Evidence cannot fit a declared 160/40 window: {row['id']}")
                paths.add(name)
                method_count += 1
                target_rows.append({"query_id":row["id"],"path":name,"symbol":target["symbol"],
                                    "evidence_lines":[lo,hi],"evidence_sha256":target["evidence_sha256"],
                                    "declared_definition_160_40_eligible_windows":eligible})
        multiple += len(paths) > 1
        if row["kind"] == "absent":
            for decoy in row["absence_review"]["closest_decoys"]:
                require(decoy["path"] in sources, f"Absence decoy outside scope: {row['id']}")
            matches = {}
            for pattern in row["absence_review"]["searches"]:
                matches[pattern] = [{"path":name,"lines":[i for i,line in enumerate(physical_lines(text),1)
                                                          if re.search(pattern,line,re.IGNORECASE)]}
                                    for name,text in sources.items() if re.search(pattern,text,re.IGNORECASE)]
            absent_searches.append({"query_id":row["id"],"matches":matches,
                                   "interpretation":"Search inventory supports source review; match counts do not establish semantic absence"})
    counts = Counter(row["kind"] for row in rows)
    require(len(rows) == entry["queries"] and counts["present"] == entry["present_queries"]
            and counts["absent"] == entry["absent_queries"] and multiple == entry["multi_file_and_queries"],
            f"Manifest counts differ: {entry['id']}")
    require(method_count == entry["gold_target_rows"]
            and required_groups == entry["required_evidence_groups"],
            f"Manifest evidence inventory differs: {entry['id']}")
    return {"id":entry["id"],"revision":entry["revision"],"source_files":len(sources),
            "source_file_sha256":hashes,"queries":len(rows),"present":counts["present"],
            "absent":counts["absent"],"multi_file_and_queries":multiple,
            "gold_target_rows":method_count,"required_evidence_groups":required_groups,
            "targets":target_rows,"absence_search_inventory":absent_searches,
            "checks_passed":True,"semantic_review_independent":False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "benchmarks/language_quality_manifest.json")
    parser.add_argument("--output", type=Path, default=ROOT / "results/language-source-preflight-2026-10-13/source-audit.json")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    scoring_rule = manifest["source_evidence_scoring"]
    require(scoring_rule and "irrespective of symbol label" in scoring_rule,
            "Source-evidence equivalence rule is missing")
    for entry in manifest["datasets"]:
        dataset = json.loads((ROOT / entry["dataset"]).read_text())
        require(dataset.get("source_evidence_scoring") == scoring_rule,
                f"Dataset scoring contract differs: {entry['id']}")
    notices = manifest["notice_files"]
    for name, expected in notices.items():
        require(digest((ROOT / name).read_bytes()) == expected, f"Corpus notice changed: {name}")
        text = (ROOT / name).read_text()
        for entry in manifest["datasets"]:
            require(entry["revision"] in text and entry["release"] in text,
                    f"Corpus notice lacks release provenance: {name}/{entry['id']}")
        require("Jaen" in text and "mapOnSchema" in text and "Apache-2.0" in text,
                f"Zod traversal attribution missing: {name}")
    rows = [inspect(entry) for entry in manifest["datasets"]]
    for name, field in (("queries", "queries"), ("present_queries", "present"),
                        ("absent_queries", "absent"), ("multi_file_and_queries", "multi_file_and_queries"),
                        ("required_evidence_groups", "required_evidence_groups"), ("gold_target_rows", "gold_target_rows")):
        require(sum(row[field] for row in rows) == manifest["totals"][name], f"Total inventory differs: {name}")
    record = {"version":1,"created_at":datetime.now(timezone.utc).isoformat(),
              "classification":"CPU source-byte/metadata preflight; independent semantic review and extractor/runtime freeze pending",
              "window_feasibility":"160 lines with40 overlap from authored definition bounds; actual future extractor eligibility still requires its separate CPU gate",
              "manifest_sha256":digest(args.manifest.read_bytes()),"script_sha256":digest(Path(__file__).read_bytes()),
              "notice_file_sha256":notices,
              "checks_passed":True,"model_or_retrieval_calls":0,"datasets":rows}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(record,indent=2,sort_keys=True)+'\n')
    print(json.dumps({"checks_passed":True,"datasets":[{k:v for k,v in row.items() if k in
               ("id","source_files","queries","present","absent","multi_file_and_queries","gold_target_rows","required_evidence_groups")} for row in rows]},indent=2))


if __name__ == "__main__":
    main()
