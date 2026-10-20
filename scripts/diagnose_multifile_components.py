#!/usr/bin/env python3
"""Post-hoc CPU reconstruction of frozen retrieval components, never model work.

Run only after the native study has completed and closed its saved SQLite index.
This explains component absence versus merge and downstream losses; it is not
a fresh trial, tuning step, latency measurement, or new relevance judgment.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from dataclasses import asdict
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile

import numpy as np

from semantic_search.languages import decode_source
from semantic_search.persistent_retrieval import PersistentDenseRetriever, PersistentLexicalRetriever
from semantic_search.retrieval import MultifileRetriever, multifile_clauses
from semantic_search.source import physical_lines
from semantic_search.store import SQLiteIndex
from diagnose_multifile import attribute_groups

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def ranks(windows, groups):
    return [next((rank for rank, row in enumerate(windows, 1)
                  if any(row["path"] == target["path"]
                         and row["start_line"] <= target["evidence_lines"][0]
                         <= target["evidence_lines"][1] <= row["end_line"]
                         for target in alternatives)), None) for alternatives in groups]


def verify_window(row, sources):
    start, end = row.get("start_line"), row.get("end_line")
    require(row.get("path") in sources and type(start) is int and type(end) is int
            and 1 <= start <= end <= len(sources[row["path"]]), "Component window has an invalid physical span")
    text = "".join(sources[row["path"]][start - 1:end])
    require(row.get("source", row.get("text")) == text
            and row.get("source_hash", row.get("content_hash")) == sha(text.encode()),
            "Component window differs from frozen saved source")


def query_vector(row):
    vector = np.asarray(row["query_vector"], dtype=np.float32)
    require(vector.ndim == 1 and len(vector) > 0 and np.isfinite(vector).all()
            and sha(vector.tobytes()) == row["query_vector_sha256"], "Saved float32 query vector SHA differs")
    return vector


def component_groups(dense, clause_union, merged, stages, groups):
    component_ranks = {name: ranks(windows, groups) for name, windows in
                       (("dense", dense), ("clause_union", clause_union), ("merged", merged),
                        ("before_pack", stages["expanded"]), ("delivered", stages["token_packed"]))}
    downstream = attribute_groups({"stages": stages}, groups)
    result = []
    for index in range(len(groups)):
        values = {name: values[index] for name, values in component_ranks.items()}
        if values["delivered"] is not None:
            outcome = "delivered"
        elif values["dense"] is None and values["clause_union"] is None:
            outcome = "absent_from_all_components"
        elif values["merged"] is None:
            outcome = "discarded_by_merge"
        else:
            outcome = "downstream_" + downstream[index]["outcome"]
        result.append({"group": index + 1, "outcome": outcome, "ranks": values,
                       "actual_stage_outcome": downstream[index]["outcome"]})
    return result


def reconstruct(study, dataset, corpus, database):
    plan = json.loads((study / "plan.json").read_bytes())
    summary = json.loads((study / "summary.json").read_bytes())
    require(summary.get("complete") is True, "Wait for the native producer to finish and close its index")
    binding = plan["binding"]
    require(sha(dataset.read_bytes()) == binding["dataset_sha256"], "Frozen dataset SHA differs")
    for name in ("src/semantic_search/retrieval.py", "src/semantic_search/store.py",
                 "src/semantic_search/persistent_retrieval.py", "scripts/diagnose_multifile.py"):
        require(sha((ROOT / name).read_bytes()) == binding["implementation_sha256"][name],
                "Retrieval reconstruction implementation differs from frozen native producer: " + name)
    sources = {}
    for name, expected in binding["source_sha256"].items():
        path = (corpus / name).resolve()
        require(path.is_relative_to(corpus.resolve()) and sha(path.read_bytes()) == expected,
                "Frozen corpus source bytes differ")
        sources[name] = physical_lines(decode_source(name, path.read_bytes()))
    data = json.loads(dataset.read_bytes())
    labels = {row["id"]: row for row in data["queries"]}
    with gzip.open(study / "searches.jsonl.gz", "rt", encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream]
    require(sha((study / "searches.jsonl.gz").read_bytes()) == summary["raw_sha256"], "Native receipt archive SHA differs")
    rows = [row for row in rows if row["repetition"] == 1]
    baseline = {row["query_id"]: row for row in rows if row["retrieval"] == "dense"}
    measured = [row for row in rows if row["retrieval"] == "multifile"]
    require(len(measured) == len(labels) == len(baseline)
            and {row["query_id"] for row in measured} == set(labels), "Incomplete or duplicate native query pairs")
    cases = []
    with tempfile.TemporaryDirectory(prefix="semantic-component-reconstruction-") as temporary:
        copied = Path(temporary) / "owned-copy.sqlite3"
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as source, \
                closing(sqlite3.connect(copied)) as destination:
            source.backup(destination)
        index = SQLiteIndex(copied)
        inventory = [asdict(snippet) for snippet in index.snippets]
        require(sha(json.dumps(inventory, sort_keys=True).encode()) == binding["inventory_sha256"],
                "Saved index inventory differs from the frozen extraction")
        for window in inventory:
            verify_window(window, sources)
        lexical, dense = PersistentLexicalRetriever(index), PersistentDenseRetriever(index)
        merged_retriever = MultifileRetriever(lexical, dense)
        for row in measured:
            identifier = row["query_id"]
            vector = query_vector(row)
            baseline_vector = query_vector(baseline[identifier])
            require(vector.tobytes() == baseline_vector.tobytes(), "Paired native query embeddings differ")
            query = labels[identifier]["query"]
            require(row["response"]["query"] == baseline[identifier]["response"]["query"] == query,
                    "Native response query differs from the frozen literal query")
            dense_rows = [asdict(snippet) for snippet, _ in dense.search(vector, limit=50)]
            clauses = multifile_clauses(query)
            clause_rows = [[asdict(snippet) for snippet, _ in lexical.search(clause, limit=50)] for clause in clauses]
            merged = [asdict(snippet) for snippet, _ in merged_retriever.search(query, vector, limit=50)]
            require([item["id"] for item in dense_rows]
                    == [item["id"] for item in baseline[identifier]["stages"]["retrieved"]],
                    "Reconstructed full-query dense order differs from the native baseline")
            require([item["id"] for item in merged] == [item["id"] for item in row["stages"]["retrieved"]],
                    "Reconstructed merged order differs from the actual native multifile pool")
            for window in [*dense_rows, *merged, *(item for ranking in clause_rows for item in ranking),
                           *(item for stage in row["stages"].values() for item in stage)]:
                verify_window(window, sources)
            gold = [] if labels[identifier]["kind"] == "absent" else labels[identifier].get(
                "target_groups", [labels[identifier].get("targets")])
            for alternatives in gold:
                for target in alternatives:
                    low, high = target["evidence_lines"]
                    require(sha("".join(sources[target["path"]][low - 1:high]).encode())
                            == target["evidence_sha256"], "Frozen executable evidence SHA differs")
            require(attribute_groups({"stages": row["stages"]}, gold) == row["group_diagnostics"]
                    and ranks(merged, gold) == row["candidate_ranks"]
                    and ranks(row["stages"]["token_packed"], gold) == row["delivered_ranks"],
                    "Reconstructed evidence attribution differs from saved native ranks")
            union, seen = [], set()
            for ranking in clause_rows:
                for window in ranking:
                    if window["id"] not in seen:
                        seen.add(window["id"])
                        union.append(window)
            groups = component_groups(dense_rows, union, merged, row["stages"], gold)
            for group in groups:
                group["clause_ranks"] = [ranks(ranking, gold)[group["group"] - 1] for ranking in clause_rows]
            cases.append({"query_id": identifier, "kind": labels[identifier]["kind"],
                          "query_vector_sha256": row["query_vector_sha256"], "literal_clauses": list(clauses),
                          "component_windows": {"dense": len(dense_rows), "clauses": list(map(len, clause_rows)),
                                                "clause_union": len(union), "merged": len(merged)}, "groups": groups})
        index.close()
    return {"classification": "Post-hoc CPU retrieval reconstruction, not a native model run, fresh trial or latency measurement",
            "model_calls": 0, "quality_repetition": 1, "cases": cases,
            "exclusive_group_outcomes": dict(Counter(group["outcome"] for case in cases for group in case["groups"])),
            "checks": {"paired_query_vectors_identical": True, "dense_and_merged_orders_match_native": True,
                       "source_bytes_and_index_inventory_verified": True, "frozen_retrieval_helpers_verified": True},
            "clause_union_order": "First occurrence in literal-clause order, preserving each clause's full FTS50 order; not merged rank",
            "bindings": {"plan_sha256": sha((study / "plan.json").read_bytes()),
                         "native_raw_sha256": summary["raw_sha256"], "dataset_sha256": binding["dataset_sha256"],
                         "producer_sha256": sha(Path(__file__).read_bytes())},
            "limits": "Retrospective component attribution on saved query vectors and a private temporary SQLite copy; no new relevance labels, tuning or timing claims."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("study", "dataset", "corpus", "database", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "Choose a new diagnostic receipt path")
    report = reconstruct(args.study, args.dataset, args.corpus, args.database)
    with args.output.open("x") as stream:
        json.dump(report, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
