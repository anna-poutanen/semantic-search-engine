"""Saved-receipt reporting tests: no backend, parser, model or HTTP requests."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location(
    "language_report", Path(__file__).parents[1] / "scripts/report_language_quality.py"
)
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)


class CharacterEncoder:
    def encode(self, text, *, disallowed_special):
        assert disallowed_special == ()
        return list(text)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def update_tokens(response):
    # The counter is itself included in the encoded envelope.
    response["token_usage"]["tokens"] = 0
    for _ in range(10):
        counted = len(CharacterEncoder().encode(report.compact_text(response), disallowed_special=()))
        if counted == response["token_usage"]["tokens"]:
            return
        response["token_usage"]["tokens"] = counted
    raise AssertionError("Synthetic token counter did not converge")


class Study:
    def __init__(self, root):
        self.root = root
        self.path = root / "study"
        self.path.mkdir()
        self.rows = []
        self.setups = []
        self.entries = []
        self.snapshots = []
        self.preflight_datasets = []
        for dataset, role, language in (("express", "development", "javascript"),
                                        ("zod", "repository_holdout", "typescript")):
            self.add_dataset(dataset, role, language)
        manifest = {"datasets": self.entries, "totals": {"queries": 30, "present_queries": 24,
                    "absent_queries": 6, "multi_file_and_queries": 8, "required_evidence_groups": 38,
                    "gold_target_rows": 41}}
        manifest_path = root / "benchmarks/language_quality_manifest.json"
        write_json(manifest_path, manifest)
        self.frozen = {
            "python": "synthetic-python", "git_commit": "synthetic-freeze-commit",
            "manifest_sha256": report.sha(manifest_path.read_bytes()),
            "implementation_sha256": {"frozen.py": "saved-implementation-proof"}, "packages": {},
            "h2o": False, "threshold": None, "query_cache": False,
            "conditions": ["bm25", "dense", "hybrid"], "repetitions": 3,
            "planned_requests": 270, "schedule": [], "candidate_depth": 50,
            "settings": {"max_results": 8, "source_budget": 12000, "token_budget": 4096},
            "sources": self.snapshots,
            "embedding": {"id": "synthetic-no-model"}, "fusion": {}, "dependencies": {}, "extractor": {},
            "candidate_measurement": "synthetic CPU receipts",
        }
        # Explicit complete schedule rather than inferring denominator from successes.
        self.frozen["schedule"] = [{key: row[key] for key in report.IDENTITY} for row in self.rows]
        self.preflight = {"checks_passed": True, "model_calls": 0,
                          "manifest_sha256": self.frozen["manifest_sha256"],
                          "implementation_sha256": self.frozen["implementation_sha256"],
                          "datasets": self.preflight_datasets}
        self.summary = {"complete": True, "quality_repetition": 1, "requests": 270,
                        "unique_queries": 30, "setups": self.setups, "datasets": {}}
        for dataset in ("express", "zod"):
            self.summary["datasets"][dataset] = {
                condition: report.metrics([row for row in self.rows
                                           if row["dataset"] == dataset and row["condition"] == condition])
                for condition in self.frozen["conditions"]
            }
        self.save()

    def add_dataset(self, dataset, role, language):
        corpus = self.root / "corpora" / dataset
        corpus.mkdir(parents=True)
        raw_sources = {"src/a.js": 'const separator = "\u2028";\r\nfirst();\rsecond();\n',
                       "src/b.js": "const b = 1;\nwork();\nfinish();\n"}
        inventory = []
        sources = {}
        for name, source in raw_sources.items():
            path = corpus / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(source.encode())
            sources[name] = report.sha(source.encode())
            inventory.append({"id": dataset + name, "path": name, "symbol": "diagnostic_" + name,
                              "start_line": 1, "end_line": 3, "source_hash": sources[name]})

        def target(name, line):
            return {"path": name, "symbol": "label_names_do_not_need_to_match",
                    "evidence_lines": [line, line],
                    "evidence_sha256": report.sha(report.physical_lines(raw_sources[name])[line - 1].encode())}

        queries = []
        for number in range(15):
            query = {"id": f"{dataset}_q{number}", "query": f"find implementation {number}\u2028saved",
                     "kind": "present" if number < 12 else "absent"}
            if number < 4:
                query["target_groups"] = [[target("src/a.js", 2)], [target("src/b.js", 2)]]
            elif number < 7:
                query["target_groups"] = [[target("src/a.js", 2)], [target("src/a.js", 3)]]
            else:
                query["targets"] = [target("src/a.js", 2)] if number < 12 else []
            if number == 7 or (dataset == "zod" and number == 8):
                query["targets"].append(target("src/a.js", 3))
            queries.append(query)
        dataset_file = self.root / "benchmarks" / f"{dataset}_queries.json"
        write_json(dataset_file, {"queries": queries})
        self.entries.append({"id": dataset, "role": role, "language": language,
                             "dataset": str(dataset_file.relative_to(self.root)),
                             "sha256": report.sha(dataset_file.read_bytes()),
                             "corpus": str(corpus.relative_to(self.root)), "source_root": "src"})
        self.snapshots.append({"id": dataset, "source_file_sha256": sources, "windows": 2,
                               "window_inventory_sha256": report.sha(json.dumps(inventory, sort_keys=True).encode())})
        self.preflight_datasets.append({"id": dataset, "source_file_sha256": sources,
                                        "window_inventory": inventory})
        counts = {"completed_query_calls": 1, "completed_document_calls": 1, "document_texts": 2}
        identity = {"source_files": 2, "source_manifest": dataset + "-saved-source", "model_fingerprint": "synthetic-model"}
        self.setups.append({"dataset": dataset, "cold_index_and_model_warm_s": 1.0,
                            "indexstats": {**identity, "indexed_windows": 2, "reused_windows": 0, "total_windows": 2},
                            "embedding_calls_after_cold_setup": counts,
                            "embedding_calls_after_queries": {**counts, "completed_query_calls": 91},
                            "persistent_reopen": {"indexstats": {**identity, "indexed_windows": 0, "reused_windows": 2,
                                                                   "total_windows": 2},
                                                  "embedding_calls": {"completed_query_calls": 0,
                                                                      "completed_document_calls": 0, "document_texts": 0}},
                            "memory_after_cold_setup": {"physical_footprint": "1G"},
                            "memory_after_queries": {"physical_footprint": "1G", "physical_footprint_peak": "2G"}})
        for repetition in range(1, 4):
            for number, query in enumerate(queries):
                for condition in ("bm25", "dense", "hybrid"):
                    windows = [{**window, "source": raw_sources[window["path"]], "truncated": False,
                                "origin": "retrieval", "related_to": None} for window in inventory]
                    if condition == "dense" and repetition == 1:
                        if number == 0:
                            windows = windows[:1]
                        elif number == 1:
                            window = windows[1]
                            window["end_line"] = 1
                            window["source"] = report.physical_lines(window["source"])[0]
                            window["source_hash"] = report.sha(window["source"].encode())
                            window["truncated"] = True
                    truncated = any(window["truncated"] for window in windows)
                    response = {"query": query["query"], "retrieval": condition, "rerank": False,
                                "scope": {"source_root": "src"}, "source_budget": 12000,
                                "candidate_windows": 2, "match_status": "candidates",
                                "results": windows, "total_source_chars": sum(len(window["source"]) for window in windows),
                                "budget_exhausted": truncated, "indexstats": {**identity, "indexed_windows": 0,
                                                                              "total_windows": 2},
                                "token_usage": {"encoding": "cl100k_base", "budget": 4096,
                                                "tokens": 0, "truncated": truncated}}
                    update_tokens(response)
                    gold = report.groups(query)
                    self.rows.append({"dataset": dataset, "role": role, "language": language,
                                      "query_id": query["id"], "condition": condition, "repetition": repetition,
                                      "kind": query["kind"], "elapsed_http_s": .1 * repetition,
                                      "response": response, "candidate_windows": copy.deepcopy(inventory),
                                      "evidence_ranks": report.rank_groups(windows, gold),
                                      "candidate_evidence_ranks": report.rank_groups(inventory, gold),
                                      "multi_file_and": number < 4,
                                      "exact_source_and_budgets_verified": True})

    def save(self):
        write_json(self.path / "frozen-config.json", self.frozen)
        write_json(self.path / "extractor-preflight.json", self.preflight)
        write_json(self.path / "setups.json", self.setups)
        (self.path / "searches.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in self.rows))
        self.summary["searches_sha256"] = report.sha((self.path / "searches.jsonl").read_bytes())
        write_json(self.path / "summary.json", self.summary)
        # Corruption tests update outer proof hashes, exercising substantive verification.
        write_json(self.path / "runtime-audit.json", {
            "checks_passed": True, "checks": {"runtime": True},
            "actual_implementation_sha256": self.frozen["implementation_sha256"],
            "actual_packages": self.frozen["packages"],
            "actual_python": self.frozen["python"], "frozen_git_commit": self.frozen["git_commit"],
            "artifact_sha256": {name: report.sha((self.path / name).read_bytes()) for name in
                                ("frozen-config.json", "summary.json", "searches.jsonl", "setups.json", "extractor-preflight.json")},
        })

    def build(self):
        return report.build_report(self.path, project_root=self.root, encoder=CharacterEncoder())


@pytest.fixture
def study(tmp_path):
    return Study(tmp_path)


def test_primary_quality_and_and_groups_do_not_inflate_with_repetitions(study):
    result = study.build()
    assert result["requests"] == 270 and result["unique_queries"] == 30
    dense = result["datasets"]["express"]["conditions"]["dense"]
    assert (dense["present_queries"], dense["absent_queries"], dense["required_groups"]) == (12, 3, 19)
    assert (dense["candidate_complete"], dense["delivered_complete"], dense["delivered_groups"]) == (12, 10, 17)
    assert (dense["multi_file_queries"], dense["multi_file_complete"]) == (4, 2)
    assert dense["latency_samples"] == 45 and dense["later_repetition_latency_samples"] == 30
    assert dense["repeat_coverage_mismatches"] == ["express_q0", "express_q1"] * 2
    assert len(result["primary_misses"]) == 4
    assert len(result["primary_absent_decoys"]) == 18
    assert all(row["returned_decoys"] for row in result["primary_absent_decoys"])
    cut = next(row for row in result["primary_misses"] if row["query_id"] == "express_q1")
    assert cut["missing_groups"][0]["evidence_cut_within_returned_window"][0]["delivered_lines"] == [1, 1]
    assert "not additional query evidence" in report.render(result)


def test_jsonl_unicode_separators_and_cr_lf_source_are_preserved(study):
    assert len(report.jsonl(study.path / "searches.jsonl")) == 270
    assert report.physical_lines('"\u2028"\r\na();\rb();\n') == ['"\u2028"\r\n', 'a();\r', 'b();\n']
    assert study.build()["source_proofs"]["verified_http_responses"] == 270


@pytest.mark.parametrize("change", ["missing", "duplicate", "reorder"])
def test_exact_frozen_request_inventory_is_required(study, change):
    if change == "missing":
        study.rows.pop()
    elif change == "duplicate":
        study.rows[-1] = copy.deepcopy(study.rows[0])
    else:
        study.rows[0], study.rows[1] = study.rows[1], study.rows[0]
    study.save()
    with pytest.raises(ValueError, match="Request identities/order"):
        study.build()


@pytest.mark.parametrize("change", ["source", "hash", "bounds"])
def test_saved_bytes_and_physical_bounds_are_independent_of_guard_flags(study, change):
    response = study.rows[0]["response"]
    window = response["results"][0]
    if change == "source":
        window["source"] = window["source"].replace("first", "wrong")
    elif change == "hash":
        window["source_hash"] = "fabricated"
    else:
        window["end_line"] = 2
    update_tokens(response)
    study.save()
    with pytest.raises(ValueError, match="source|hash"):
        study.build()


def test_exact_compact_tokens_are_recomputed(study):
    study.rows[0]["response"]["token_usage"]["tokens"] += 1
    study.save()
    with pytest.raises(ValueError, match="Exact compact token"):
        study.build()


def test_declared_evidence_ranks_and_summary_are_recomputed(study):
    study.rows[0]["evidence_ranks"] = [1, 1]
    study.save()
    with pytest.raises(ValueError, match="Declared evidence ranks"):
        study.build()


def test_summary_quality_cannot_count_later_successes(study):
    study.summary["datasets"]["express"]["dense"]["delivered_complete"] = 12
    study.save()
    with pytest.raises(ValueError, match="Published summary"):
        study.build()


def test_reopen_embedding_calls_and_query_phase_indexing_are_checked(study):
    study.setups[0]["persistent_reopen"]["embedding_calls"]["completed_query_calls"] = 1
    study.save()
    with pytest.raises(ValueError, match="Reopen invoked embedding"):
        study.build()


@pytest.mark.parametrize("phase", ["warm_source", "reopen_model", "documents"])
def test_index_identity_and_document_reuse_are_checked(study, phase):
    if phase == "warm_source":
        study.rows[0]["response"]["indexstats"]["source_manifest"] = "changed-source"
        expected = "Warm index membership"
    elif phase == "reopen_model":
        study.setups[0]["persistent_reopen"]["indexstats"]["model_fingerprint"] = "different-model"
        expected = "Reopened index identity"
    else:
        study.setups[0]["embedding_calls_after_queries"]["completed_document_calls"] += 1
        expected = "Query phase reembedded documents"
    study.save()
    with pytest.raises(ValueError, match=expected):
        study.build()


def test_outer_receipt_hash_is_required(study):
    with (study.path / "searches.jsonl").open("a") as stream:
        stream.write("\n")
    with pytest.raises(ValueError, match="Runtime proof input artifact changed"):
        study.build()


def test_miss_diagnostics_distinguish_candidate_depth_from_prefix_cuts():
    candidates = [{"id": str(index), "path": "other.js", "symbol": f"s{index}",
                   "start_line": 1, "end_line": 2} for index in range(1, 9)]
    eligible = {"id": "9", "path": "target.js", "symbol": "target", "start_line": 1, "end_line": 5}
    candidates.append(eligible)
    gold = [[{"path": "target.js", "evidence_lines": [3, 5]}]]
    row = {"dataset": "zod", "condition": "dense", "query_id": "q", "evidence_ranks": [None],
           "candidate_evidence_ranks": [9], "candidate_windows": candidates,
           "response": {"results": [], "budget_exhausted": False, "token_usage": {"truncated": False}}}
    inventory = {window["id"]: window for window in candidates}
    missing = report.miss_details(row, gold, inventory)["missing_groups"][0]
    assert missing["outside_first8_raw_candidate_windows"] is True
    assert missing["evidence_cut_within_returned_window"] == []
    row["response"]["results"] = [{**eligible, "end_line": 2, "truncated": True}]
    missing = report.miss_details(row, gold, inventory)["missing_groups"][0]
    assert missing["observation"] == "evidence cut within returned window"
    assert missing["evidence_cut_within_returned_window"][0]["full_window_lines"] == [1, 5]
