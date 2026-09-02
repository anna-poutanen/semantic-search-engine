"""CPU-only source-label, aggregation, and evaluation-order contracts.

The runner probes substitute every embedding/backend/HTTP dependency. Their
synthetic timings exercise bookkeeping and are never model latency evidence.
"""
from contextlib import ExitStack, redirect_stdout
import copy
import hashlib
import io
import itertools
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from semantic_search.corpus import Snippet, extract_snippets
from semantic_search import quality


METADATA = {
    "id": "cpu-test-embedder", "revision": "f" * 40,
    "query_prompt": "Instruct: retrieve code\nQuery: {query}",
    "pooling": "last_token", "normalization": "l2", "dimensions": 2,
}


def target(path="src/code.py", symbol="alpha", interval=(2, 3)):
    return {"path": path, "symbol": symbol, "evidence_lines": list(interval)}


def snippet(path="src/code.py", symbol="alpha", start=1, end=3):
    source = "".join(f"line {line}\n" for line in range(start, end + 1))
    digest = hashlib.sha256(source.encode()).hexdigest()
    return Snippet(f"{path}:{symbol}:{start}", path, symbol, start, end, source, digest)


def metric_row(identifier, *, ranks, candidates=None, kind="present", role="development",
               split="development", elapsed=1., status="candidates"):
    return {"query_id": identifier, "condition": "dense", "role": role,
            "kind": kind, "split": split, "evidence_ranks": ranks,
            "candidate_evidence_ranks": ranks if candidates is None else candidates,
            "elapsed_http_s": elapsed, "response": {"match_status": status}}


class QualityEvidenceTests(unittest.TestCase):
    def test_legacy_targets_are_one_or_group_and_compound_groups_are_and(self):
        alpha, alternative, beta = target(), target(symbol="alternative"), target("src/other.py", "beta")
        legacy = quality.target_groups({"kind": "present", "targets": [alpha, alternative]})
        self.assertEqual(quality.evidence_ranks([snippet(symbol="alternative")], legacy), [1])
        compound = quality.target_groups({"kind": "present", "target_groups": [[alpha, alternative], [beta]]})
        ranked = [snippet(symbol="related"), snippet(symbol="alternative"), snippet("src/other.py", "beta")]
        self.assertEqual(quality.evidence_ranks(ranked, compound), [2, 3])
        self.assertEqual(quality.evidence_ranks(ranked[:2], compound), [2, None])

    def test_whole_evidence_interval_and_exact_path_symbol_are_required(self):
        groups = [[target(interval=(20, 25))]]
        for result in (snippet(start=21, end=25), snippet(start=20, end=24),
                       snippet(path="src/other.py", start=1, end=40),
                       snippet(symbol="Alpha.alpha", start=1, end=40)):
            with self.subTest(result=result):
                self.assertEqual(quality.evidence_ranks([result], groups), [None])
        self.assertEqual(quality.evidence_ranks([snippet(start=20, end=25)], groups), [1])

    def test_retained_excerpt_bounds_override_original_full_window(self):
        full = snippet(start=10, end=30)
        retained_source = "".join(f"line {line}\n" for line in range(10, 20))
        response = {"results": [{"id": full.id, "path": full.path, "symbol": full.symbol,
                    "start_line": 10, "end_line": 19, "window_end_line": 30,
                    "source": retained_source,
                    "source_hash": hashlib.sha256(retained_source.encode()).hexdigest(),
                    "content_hash": full.content_hash,
                    "truncated": True}]}
        self.assertEqual(quality.evidence_ranks([full], [[target(interval=(20, 25))]]), [1])
        retained = quality.delivered_snippets(response)
        self.assertEqual(retained[0].end_line, 19)
        self.assertEqual(quality.evidence_ranks(retained, [[target(interval=(20, 25))]]), [None])
        self.assertEqual(quality.evidence_ranks([], [[target()]]), [None])

    def test_empty_or_ambiguous_positive_groups_are_rejected(self):
        cases = [
            {"kind": "present", "targets": []},
            {"kind": "present", "target_groups": []},
            {"kind": "present", "target_groups": [[]]},
            {"kind": "present", "target_groups": [[target()]], "targets": [target()]},
        ]
        for query in cases:
            with self.subTest(query=query), self.assertRaises(ValueError):
                quality.target_groups(query)

    def test_gold_evidence_bounds_are_validated_before_scoring(self):
        invalid = [None, [], [2], [2, 3, 4], [0, 3], [-1, 3], [4, 3],
                   [True, 3], [2, False], [2., 3], [2, "3"]]
        for interval in invalid:
            gold = target()
            if interval is None:
                gold.pop("evidence_lines")
            else:
                gold["evidence_lines"] = interval
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                quality.target_groups({"kind": "present", "targets": [gold]})

    def test_unknown_kind_and_absent_gold_are_rejected(self):
        self.assertEqual(quality.target_groups({"kind": "absent", "targets": []}), [])
        cases = [{"kind": "typo", "targets": [target()]},
                 {"kind": "absent", "targets": [target()]},
                 {"kind": "absent", "target_groups": [[target()]]}]
        for query in cases:
            with self.subTest(query=query), self.assertRaises(ValueError):
                quality.target_groups(query)

    def test_malformed_delivered_bounds_or_hash_cannot_receive_evidence_credit(self):
        source = "def alpha():\n    return 1\n"
        row = {"id": "alpha-window", "path": "src/code.py", "symbol": "alpha",
               "start_line": 5, "end_line": 6, "source": source,
               "source_hash": hashlib.sha256(source.encode()).hexdigest()}
        cases = [{"start_line": 0}, {"start_line": True}, {"end_line": 4},
                 {"end_line": True}, {"end_line": 6.}, {"end_line": 10},
                 {"source_hash": "b" * 64}, {"source_hash": "not-a-hash"},
                 {"source": "def alpha():\n"}, {"source": ""},
                 {"source": 1}, {"source": "\ud800"}]
        for changed in cases:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                quality.delivered_snippets({"results": [{**row, **changed}]})

    def test_delivered_source_counts_physical_lines_and_preserves_unicode(self):
        source = "def alpha():\n    return 'café\u2028text'"
        row = {"id": "unicode-window", "path": "src/code.py", "symbol": "alpha",
               "start_line": 5, "end_line": 6, "source": source,
               "source_hash": hashlib.sha256(source.encode()).hexdigest()}
        delivered = quality.delivered_snippets({"results": [row]})
        self.assertEqual(delivered[0].text, source)
        self.assertEqual((delivered[0].start_line, delivered[0].end_line), (5, 6))
        self.assertEqual(quality.evidence_ranks(delivered, [[target(interval=(6, 6))]]), [1])


class QualitySummaryTests(unittest.TestCase):
    def rows(self):
        return [
            metric_row("single-first", ranks=[1], elapsed=1.),
            metric_row("compound-third", ranks=[1, 3], elapsed=2.),
            metric_row("compound-partial", ranks=[2, None], candidates=[2, 5], elapsed=3.),
            metric_row("calibration-miss", ranks=[None], split="calibration", elapsed=4.),
            metric_row("holdout-fifth", ranks=[5], role="repository_holdout", split="repository_holdout", elapsed=5.),
            metric_row("absent-rejected", ranks=[], kind="absent", split="calibration", elapsed=10.,
                       status="insufficient_evidence"),
            metric_row("absent-accepted", ranks=[], kind="absent", role="repository_holdout",
                       split="repository_holdout", elapsed=20.),
        ]

    def test_positive_denominators_include_compounds_and_exclude_absent_rows(self):
        metrics = quality.summarize(self.rows())["dense"]["all"]
        self.assertEqual((metrics["present"], metrics["absent"], metrics["queries"]), (5, 2, 7))
        self.assertEqual(metrics["candidate_complete"], 4)
        self.assertEqual(metrics["delivered_complete"], 3)
        self.assertEqual((metrics["multi_file_complete"], metrics["multi_file_queries"]), (1, 2))
        self.assertAlmostEqual(metrics["hit_at_1"], 1 / 5)
        self.assertAlmostEqual(metrics["hit_at_3"], 2 / 5)
        self.assertAlmostEqual(metrics["hit_at_5"], 3 / 5)
        self.assertAlmostEqual(metrics["mrr"], (1 + 1 / 3 + 1 / 5) / 5)
        self.assertEqual((metrics["delivered_groups"], metrics["required_groups"]), (5, 7))
        self.assertEqual(metrics["candidate_groups"], 6)

    def test_absence_counts_and_latency_samples_include_every_query(self):
        metrics = quality.summarize(self.rows())["dense"]["all"]
        self.assertEqual(metrics["absent_rejected"], 1)
        self.assertEqual(metrics["absent_accepted"], 1)
        self.assertEqual(metrics["timing_samples"], 7)
        self.assertEqual(metrics["median_s"], 4.)
        self.assertEqual(metrics["p95_s"], 20.)
        self.assertEqual(metrics["max_s"], 20.)

    def test_calibration_and_holdout_have_separate_aggregates(self):
        summary = quality.summarize(self.rows())["dense"]
        self.assertEqual(summary["calibration"]["queries"], 2)
        self.assertEqual(summary["development_queries"]["queries"], 3)
        self.assertEqual(summary["repository_holdout"]["queries"], 2)
        self.assertEqual(summary["calibration"]["present"], 1)
        self.assertEqual(summary["repository_holdout"]["absent"], 1)

    def test_only_absent_rows_have_zero_positive_metrics_without_division_error(self):
        metrics = quality.summarize([metric_row("negative", ranks=[], kind="absent")])["dense"]["all"]
        self.assertEqual(metrics["present"], 0)
        for name in ("hit_at_1", "hit_at_3", "hit_at_5", "mrr"):
            self.assertIsNone(metrics[name])

    def test_absent_no_candidates_is_separate_from_policy_rejection_and_acceptance(self):
        rows = [metric_row("accepted", ranks=[], kind="absent", status="candidates"),
                metric_row("rejected", ranks=[], kind="absent", status="insufficient_evidence"),
                metric_row("no-source", ranks=[], kind="absent", status="no_candidates")]
        metrics = quality.summarize(rows)["dense"]["all"]
        self.assertEqual(metrics["absent"], 3)
        self.assertEqual(metrics["absent_accepted"], 1)
        self.assertEqual(metrics["absent_rejected"], 1)
        self.assertEqual(metrics["absent_no_candidates"], 1)
        self.assertEqual(metrics["timing_samples"], 3)


class QualityRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "results"
        self.events, self.backends, self.calls, self.embedders = [], [], [], []
        self.failure_query = None
        self.development = self.dataset("development", "development", [
            self.query("calib-positive", "present", "calibration", "calibrated present"),
            self.query("calib-absent", "absent", "calibration", "absent calibrated"),
            self.query("observed-test", "present", "test", "low-score development test"),
        ])
        self.holdout = self.dataset("holdout", "repository_holdout", [
            self.query("holdout-positive", "present", "repository_holdout", "holdout present"),
            self.query("holdout-absent", "absent", "repository_holdout", "absent holdout"),
        ])
        self.manifest = self.root / "manifest.json"
        # Reversed input proves execution order is based on the declared role.
        self.write_manifest([self.holdout, self.development])
        case = self

        class Embedder:
            metadata = METADATA

            def __init__(self, **kwargs):
                case.assertTrue((case.output / "frozen-config.json").is_file())
                self.queries = []
                case.embedders.append(self)
                case.events.append("embedding initialized")

            def embed_query(self, query):
                self.queries.append(query)
                score = .75 if query == "calibrated present" else .05 if query.startswith("low-score") else .2 if query.startswith("absent") else .6
                self.last_query = np.asarray([score, 0.])
                return self.last_query

        class Retriever:
            def __init__(self, backend):
                self.backend = backend

            def search(self, *args, limit=50):
                return [(self.backend.snippet, .6)]

        class Backend:
            def __init__(self, repo, source_root, db_path, *, embedder):
                self.repo, self.embedder = Path(repo), embedder
                self.snippet = extract_snippets(self.repo, source_root, max_lines=160, overlap_lines=40)[0]
                self._lexical = self._dense = self._hybrid = Retriever(self)
                self._policy, self.closed = None, False
                if self.repo.name == "holdout":
                    policy_path = case.output / "abstention-policy.json"
                    case.assertTrue(policy_path.is_file(), "Policy must be saved before any held-out backend exists")
                    case.events.append("held-out backend initialized after policy freeze")
                case.backends.append(self)
                case.current_backend = self

            def search(self, query, **settings):
                case.calls.append((self.repo.name, query, copy.deepcopy(settings)))
                if query == case.failure_query:
                    raise RuntimeError("synthetic request failure")
                score = None
                if settings.get("retrieval", "dense") != "bm25":
                    score = float(self.embedder.embed_query(query)[0])
                rejected = self._policy is not None and self._policy.rejects(score, self.embedder.metadata)
                item = self.snippet
                results = [] if rejected else [{"id": item.id, "path": item.path, "symbol": item.symbol,
                    "start_line": item.start_line, "end_line": item.end_line, "source": item.text,
                    "source_hash": item.content_hash, "truncated": False}]
                return {"query": query, "results": results, "indexstats": {"total_windows": 1},
                    "match_status": "insufficient_evidence" if rejected else "candidates",
                    "match_evidence": {"top_cosine": score}}

            def close(self):
                self.closed = True

        class Server:
            server_address = ("127.0.0.1", 9999)

            def serve_forever(self, **kwargs):
                pass

            def shutdown(self):
                pass

            def server_close(self):
                pass

        class Client:
            def __init__(self, url):
                self.backend = case.current_backend

            def search(self, query, **settings):
                return self.backend.search(query, **settings)

        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch.object(quality, "RecordingEmbedder", Embedder))
        self.patches.enter_context(patch.object(quality, "SearchBackend", Backend))
        self.patches.enter_context(patch.object(quality, "SearchClient", Client))
        self.patches.enter_context(patch.object(quality, "create_server", lambda backend, port: Server()))
        self.patches.enter_context(patch.object(quality, "machine_provenance", lambda: {"cpu_test": True}))
        self.patches.enter_context(patch.object(quality.time, "perf_counter", side_effect=itertools.count(0., .25)))

    def query(self, identifier, kind, split, text):
        return {"id": identifier, "kind": kind, "split": split, "query": text,
                "targets": [target(interval=(2, 2))] if kind == "present" else [], "notes": "CPU fixture"}

    def dataset(self, name, role, queries):
        repo = self.root / name
        (repo / "src").mkdir(parents=True)
        (repo / "src/code.py").write_text("def alpha():\n    return 1\n")
        def git(*args):
            return subprocess.check_output(["git", "-C", str(repo), *args], text=True, stderr=subprocess.DEVNULL).strip()
        git("init", "-q")
        git("add", "src/code.py")
        git("-c", "user.name=CPU Test", "-c", "user.email=cpu@example.invalid", "commit", "-qm", "fixture")
        path = self.root / f"{name}.json"
        path.write_text(json.dumps({"repo_url": "https://example.invalid/fixture", "revision": git("rev-parse", "HEAD"),
                                   "source_root": "src", "queries": queries}))
        return {"dataset": str(path), "corpus": str(repo), "role": role,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def write_manifest(self, entries):
        self.manifest.write_text(json.dumps({"version": 1, "datasets": entries}))

    def run_fixture(self, **kwargs):
        with redirect_stdout(io.StringIO()):
            return quality.run_quality(self.manifest, self.output, **kwargs)

    def test_entire_manifest_is_source_validated_before_any_model_initialization(self):
        changed = {**self.holdout, "sha256": "0" * 64}
        self.write_manifest([self.development, changed])
        with self.assertRaisesRegex(ValueError, "Dataset changed after freeze"):
            self.run_fixture()
        self.assertEqual(self.embedders, [])
        self.assertEqual(self.calls, [])

    def test_missing_full_evidence_stops_before_any_model_initialization(self):
        path = Path(self.holdout["dataset"])
        dataset = json.loads(path.read_text())
        dataset["queries"][0]["targets"][0]["evidence_lines"] = [2, 30]
        path.write_text(json.dumps(dataset))
        self.holdout["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.write_manifest([self.development, self.holdout])
        with self.assertRaisesRegex(ValueError, "Missing full-evidence source window"):
            self.run_fixture()
        self.assertEqual(self.embedders, [])

    def test_policy_is_fitted_to_declared_calibration_and_frozen_before_holdout(self):
        result = self.run_fixture()
        policy = result["policy"]
        self.assertEqual(policy["threshold"], .75)
        self.assertEqual(policy["calibration"]["query_ids"], ["calib-positive", "calib-absent"])
        self.assertEqual(policy["calibration"]["positive_queries"], 1)
        self.assertEqual(policy["calibration"]["absent_queries"], 1)
        self.assertEqual(policy["calibration"]["dataset_sha256"], [self.development["sha256"]])
        self.assertEqual(json.loads((self.output / "abstention-policy.json").read_text()), policy)
        self.assertEqual([backend.repo.name for backend in self.backends], ["development", "holdout"])
        self.assertTrue(all(backend.closed for backend in self.backends))
        self.assertEqual(result["search_rows"], 3 * 4 + 2 * 5)
        rows = [json.loads(line) for line in (self.output / "searches.jsonl").read_text().splitlines()]
        self.assertTrue(all(row["elapsed_http_s"] == .25 for row in rows))
        policy_rows = [row for row in rows if row["condition"] == "dense_context_policy"]
        self.assertEqual(len(policy_rows), 2)
        self.assertTrue(all(row["response"]["match_status"] == "insufficient_evidence" for row in policy_rows))
        for query in ("holdout present", "absent holdout"):
            # Three semantic baselines plus one fresh policy call. BM25 doesn't embed.
            self.assertEqual(self.embedders[1].queries.count(query), 4)

    def test_every_live_condition_forwards_both_declared_budget_caps(self):
        self.run_fixture()
        for repo, query, settings in self.calls:
            if query == "Find the implementation":
                continue
            with self.subTest(repo=repo, query=query, settings=settings):
                self.assertEqual(settings.get("source_budget", 12000), 12000)
                self.assertEqual(settings.get("token_budget"), 4096)

    def test_request_failure_does_not_advance_to_holdout_and_closes_backend(self):
        self.failure_query = "calibrated present"
        with self.assertRaisesRegex(RuntimeError, "synthetic request failure"):
            self.run_fixture()
        self.assertEqual([backend.repo.name for backend in self.backends], ["development"])
        self.assertTrue(self.backends[0].closed)
        self.assertFalse((self.output / "abstention-policy.json").exists())
        self.assertFalse((self.output / "results.json").exists())

    def test_holdout_only_filter_cannot_skip_development_calibration(self):
        with self.assertRaisesRegex(ValueError, "development calibration before repository holdout"):
            self.run_fixture(limit_role="repository_holdout")
        self.assertEqual(self.embedders, [])
        self.assertEqual(self.backends, [])

    def test_missing_negative_calibration_stops_before_held_out_backend(self):
        path = Path(self.development["dataset"])
        dataset = json.loads(path.read_text())
        dataset["queries"][1]["split"] = "development"
        path.write_text(json.dumps(dataset))
        self.development["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.write_manifest([self.development, self.holdout])
        with self.assertRaisesRegex(ValueError, "both present and absent"):
            self.run_fixture()
        self.assertEqual([backend.repo.name for backend in self.backends], ["development"])
        self.assertTrue(self.backends[0].closed)
        self.assertFalse((self.output / "abstention-policy.json").exists())
        self.assertFalse((self.output / "results.json").exists())


if __name__ == "__main__":
    unittest.main()
