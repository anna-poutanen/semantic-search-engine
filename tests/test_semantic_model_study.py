"""Invented CPU study contracts; never open observed operands/checkpoints."""
from copy import deepcopy
import gzip
import importlib.util
import json
import os
import stat
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

import numpy as np

from semantic_search.context import apply_token_budget
from semantic_search.embeddings import DIMENSIONS, QwenEmbedder
from semantic_search.semantic_features import QUERY_PROMPT, document_text, expected_facets, row_binding


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("semantic_model_study_test", ROOT / "scripts/semantic_model_study.py")
study = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(study)
QUERY = "Find shared requests for running transports, and publish successful values to active observers"


class Counter:
    def encode(self, text, **kwargs):
        return list(text)


class InventedTokenizer:
    def __call__(self, texts, **kwargs):
        return {"input_ids": [list(text.encode())[:kwargs["max_length"]] for text in texts]}


def fake_embedder():
    embedder = QwenEmbedder(batch_size=4, progress=False)
    embedder._ensure_loaded = lambda: None
    embedder._tokenizer = InventedTokenizer()
    calls = []

    def forward(rows):
        calls.append(deepcopy(rows))
        output = np.zeros((len(rows), DIMENSIONS), dtype=np.float32)
        for index, tokens in enumerate(rows):
            text = bytes(tokens).decode()
            axis = 1 if "def b():" in text or ("Query:Find" in text and ", and" not in text) else 0
            if "Query:publish" in text:
                axis = 2
            output[index, axis] = 1.0
        return output

    embedder._embed_batch = forward
    return embedder, calls


def row(identifier):
    source = f"def {identifier}():\n    return None\n"
    return {"id": identifier, "path": f"invented/{identifier}.py", "symbol": identifier,
        "start_line": 1, "end_line": 2, "window_end_line": 2, "source": source,
        "content_hash": study.digest(source.encode()), "source_hash": study.digest(source.encode()),
        "truncated": False, "origin": "retrieval", "related_to": None,
        "retrieval_score": 0.5, "h2o_score": None, "qwen_score": None}


def fixture(*, lexical=False):
    raw = [row("a"), row("b")]
    reference = {"query": QUERY, "match_status": "candidates", "results": [deepcopy(raw[0])],
                 "total_source_chars": len(raw[0]["source"]), "budget_exhausted": False}
    with patch("semantic_search.context.encoding", return_value=Counter()):
        reference = apply_token_budget(reference, 4096)
    facets = expected_facets(QUERY)
    query_id = "query_" + study.digest(json.dumps({"query": QUERY, "facets": facets, "query_prompt": QUERY_PROMPT},
        ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    feature_id = None if lexical else query_id
    cases, maps = [], []
    documents = {}
    for index in range(2):
        case_id = f"invented_case_{index}"
        case = {"case_id": case_id, "key": ["invented", "invented", "two_parts", "bm25" if lexical else "hybrid"],
                "subset": "invented", "dataset": "invented", "query_id": "two_parts", "condition": "bm25" if lexical else "hybrid",
                "query": QUERY, "kind": "present", "multi_file": True, "qualified": False,
                "groups": [[{"path": "invented/a.py", "evidence_lines": [1, 2]}],
                           [{"path": "invented/b.py", "evidence_lines": [1, 2]}]],
                "raw_rows": deepcopy(raw), "reference_response": deepcopy(reference),
                "precoalesce_rows": deepcopy(reference["results"]), "definition_keys": {},
                "legacy_control": deepcopy(reference), "feature_request_id": feature_id,
                "limits": {"max_results": 8, "source_budget": 12000, "token_budget": 4096}}
        window_documents, bindings = {}, {}
        if not lexical:
            for item in raw:
                text = document_text(item)
                sha = study.digest(text.encode())
                document_id = "doc_" + sha
                documents[document_id] = {"document_id": document_id, "document_sha256": sha, "text": text}
                window_documents[item["id"]] = document_id
                bindings[item["id"]] = row_binding(item)
        cases.append(case)
        maps.append({"case_id": case_id, "active": not lexical, "reason": "bm25" if lexical else None,
            "query_request_id": feature_id, "window_documents": window_documents, "window_bindings": bindings})
    feature_requests = {"document_requests": list(documents.values()), "query_requests": [] if lexical else [
        {"request_id": query_id, "query": QUERY, "facets": facets,
         "case_ids": [case["case_id"] for case in cases]}], "cases": maps}
    return {"cases": cases, "feature_requests": feature_requests}


def binding(embedder):
    return {"classification": "invented CPU contract test; no checkpoint loaded", "model_binding": {
        "metadata": embedder.metadata, "fingerprint": study.model_fingerprint(embedder.metadata)}}


def lines(path):
    return study.read_jsonl(path)


class SemanticModelStudyTests(unittest.TestCase):
    def test_fresh_deduplicated_documents_singleton_query_then_only_its_clauses(self):
        prepared = fixture()
        embedder, calls = fake_embedder()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "capture"
            report = study.capture(prepared, output, embedder=embedder, binding=binding(embedder))
            self.assertEqual(report["status"], "passed")
            self.assertEqual(len(report["documents"]), 2)
            self.assertEqual(len(report["queries"]), 1)
            self.assertEqual(report["calls"]["cache_hits"], 0)
            self.assertEqual(report["calls"]["retrieval_calls"], 0)
            self.assertEqual(report["forward_summary"], {
                "documents": {"api_calls": 1, "model_forward_calls": 1, "encoded_texts": 2},
                "query_full": {"api_calls": 1, "model_forward_calls": 1, "encoded_texts": 1},
                "query_clauses": {"api_calls": 1, "model_forward_calls": 1, "encoded_texts": 2},
                "total_model_forward_calls": 3, "total_encoded_texts": 5})
            self.assertEqual(len(calls), 3)
            events = lines(output / "forward-events.jsonl")
            self.assertEqual([row["sequence"] for row in events], list(range(6)))
            self.assertEqual([row["stage"] for row in events], ["documents"] * 2 + ["query_full"] * 2 + ["query_clauses"] * 2)
            query_checkpoints = lines(output / "query-vectors.jsonl")
            self.assertEqual([row["stage"] for row in query_checkpoints], ["query_full", "query_clauses"])
            self.assertEqual([len(row["vectors"]) for row in query_checkpoints], [1, 2])
            self.assertEqual(study.read_capture(output), report)
            self.assertIsNone(embedder._model)

    def test_reused_features_cover_repeated_occurrences_without_extra_model_or_backfill(self):
        prepared = fixture()
        embedder, calls = fake_embedder()
        audited = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = study.capture(prepared, root / "capture", embedder=embedder, binding=binding(embedder))
            call_count = len(calls)
            before = deepcopy(prepared)
            with patch("semantic_search.context.encoding", return_value=Counter()):
                summary = study.replay(prepared, report, root / "replay", counter=Counter(),
                                       audit_hook=lambda case, outcome, features: audited.append(case["case_id"]))
            self.assertEqual(prepared, before)
            self.assertEqual(len(calls), call_count)
            self.assertEqual(audited, ["invented_case_0", "invented_case_1"])
            self.assertEqual(summary["stored_candidate_rows"], 2)
            self.assertEqual(summary["distinct_dataset_query_units"], 1)
            self.assertEqual(summary["referenced_control_rows"], 4)
            self.assertTrue(summary["quality_gate"]["qualified"])
            self.assertEqual(summary["quality_gate"]["additional_distinct_complete_multi_queries_vs_coverage"],
                             [["invented", "two_parts"]])
            outcomes = lines(root / "replay/outcomes.jsonl.gz")
            self.assertEqual(summary["artifact_sha256"]["outcomes.jsonl.gz"],
                             study.file_sha(root / "replay/outcomes.jsonl.gz"))
            self.assertTrue(all("features" not in row and len(row["features_sha256"]) == 64 for row in outcomes))
            self.assertTrue(all(row["coverage_coalesce_calls"] == 0 for row in outcomes))
            self.assertTrue(all(row["semantic_calls"] == row["feature_calls"] == 1 for row in outcomes))
            self.assertTrue(all(row["subsequent_backfill_calls"] == 0 for row in outcomes))

    def test_bm25_inactive_capture_and_replay_perform_zero_model_calls(self):
        prepared = fixture(lexical=True)
        embedder, calls = fake_embedder()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = study.capture(prepared, root / "capture", embedder=embedder, binding=binding(embedder))
            with patch("semantic_search.context.encoding", return_value=Counter()):
                summary = study.replay(prepared, report, root / "replay", counter=Counter())
            self.assertEqual(calls, [])
            self.assertEqual(report["forward_summary"]["total_model_forward_calls"], 0)
            self.assertFalse(summary["quality_gate"]["qualified"])
            for case, outcome in zip(prepared["cases"], lines(root / "replay/outcomes.jsonl.gz")):
                self.assertEqual(outcome["response"], case["reference_response"])
                self.assertEqual(outcome["features_sha256"], study.digest(b"null"))
                self.assertEqual(outcome["feature_calls"], 0)

    def test_capture_directory_is_exclusive_and_existing_attempt_preserved(self):
        embedder, _ = fake_embedder()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "capture"
            study.capture(fixture(), output, embedder=embedder, binding=binding(embedder))
            original = (output / "capture.json").read_bytes()
            with self.assertRaises(FileExistsError):
                study.capture(fixture(), output, embedder=embedder, binding=binding(embedder))
            self.assertEqual((output / "capture.json").read_bytes(), original)

    def test_partial_document_forward_failure_preserves_prior_durable_batch(self):
        prepared = fixture()
        embedder, calls = fake_embedder()
        embedder.batch_size = 1
        original = embedder._embed_batch
        error = RuntimeError("invented second document forward failure")

        def fail(rows):
            if calls:
                raise error
            return original(rows)

        embedder._embed_batch = fail
        finished = []
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "capture"
            with self.assertRaises(RuntimeError) as actual:
                study.capture(prepared, output, embedder=embedder, binding=binding(embedder),
                              finish=lambda: finished.append(True))
            self.assertIs(actual.exception, error)
            self.assertEqual(finished, [True])
            self.assertFalse((output / "capture.json").exists())
            failure = json.loads((output / "failure.json").read_bytes())
            self.assertEqual(failure["stage"], "documents")
            self.assertEqual(failure["durable_document_vectors"], 1)
            self.assertEqual(failure["forward_summary"]["documents"]["model_forward_calls"], 2)
            self.assertEqual([row["event"]["status"] for row in lines(output / "forward-events.jsonl")],
                             ["started", "completed", "started", "failed"])
            self.assertEqual(len(lines(output / "document-vectors.jsonl")), 1)

    def test_clause_failure_keeps_full_query_checkpoint_and_partial_clause_work(self):
        embedder, calls = fake_embedder()
        original = embedder._embed_batch
        error = RuntimeError("invented clause failure")

        def fail(rows):
            if len(calls) == 2:
                raise error
            return original(rows)

        embedder._embed_batch = fail
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "capture"
            with self.assertRaises(RuntimeError) as actual:
                study.capture(fixture(), output, embedder=embedder, binding=binding(embedder))
            self.assertIs(actual.exception, error)
            failure = json.loads((output / "failure.json").read_bytes())
            self.assertEqual(failure["stage"], "query_clauses")
            self.assertEqual(failure["durable_query_stage_checkpoints"], 1)
            self.assertEqual(lines(output / "query-vectors.jsonl")[0]["stage"], "query_full")
            self.assertEqual(lines(output / "forward-events.jsonl")[-1]["event"]["status"], "failed")

    def test_bad_initial_metadata_still_finalizes_and_records_failed_attempt(self):
        embedder, calls = fake_embedder()
        frozen = binding(embedder)
        frozen["model_binding"]["fingerprint"] = "0" * 64
        finished = []
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "capture"
            with self.assertRaisesRegex(ValueError, "metadata differs"):
                study.capture(fixture(), output, embedder=embedder, binding=frozen,
                              finish=lambda: finished.append(True))
            self.assertEqual(calls, [])
            self.assertEqual(finished, [True])
            self.assertEqual(json.loads((output / "failure.json").read_bytes())["stage"], "initialization")

    def test_finish_error_keeps_original_model_exception_primary(self):
        embedder, _ = fake_embedder()
        original = RuntimeError("invented model failure")
        cleanup = LookupError("invented cleanup failure")

        def fail(rows):
            raise original

        def finish():
            raise cleanup

        embedder._embed_batch = fail
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(RuntimeError) as actual:
                study.capture(fixture(), Path(temporary) / "capture", embedder=embedder,
                              binding=binding(embedder), finish=finish)
            self.assertIs(actual.exception, original)
            self.assertIs(actual.exception.__cause__, cleanup)

    def test_checkpoint_tampering_rejected_before_replay(self):
        embedder, _ = fake_embedder()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "capture"
            study.capture(fixture(), output, embedder=embedder, binding=binding(embedder))
            with (output / "query-vectors.jsonl").open("ab") as stream:
                stream.write(b"{}\n")
            with self.assertRaisesRegex(ValueError, "artifact changed"):
                study.read_capture(output)

    def test_individual_loss_against_either_control_fails_even_with_other_gain(self):
        candidate = {"evidence_ranks": [1, None], "complete": False, "single_file_first_hit": False}
        old = {"evidence_ranks": [None, 1], "complete": False, "single_file_first_hit": False}
        difference = study.compare(old, candidate)
        self.assertEqual(difference["group_gains"], [1])
        self.assertEqual(difference["group_losses"], [2])

    def test_qualified_group_requires_literal_symbol_and_evidence_sha(self):
        item = row("a")
        case = {"groups": [[{"path": item["path"], "symbol": "a", "evidence_lines": [1, 2],
                              "evidence_sha256": study.digest(item["source"].encode())}]],
                "qualified": True, "kind": "present", "multi_file": False}
        self.assertTrue(study.evidence(case, {"results": [item]})["complete"])
        self.assertFalse(study.evidence(case, {"results": [{**item, "symbol": "wrong"}]})["complete"])

    def test_replay_auditor_failure_preserves_partial_exclusive_ledger(self):
        embedder, _ = fake_embedder()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = study.capture(fixture(), root / "capture", embedder=embedder, binding=binding(embedder))

            def reject(case, outcome, features):
                raise RuntimeError("invented independent audit rejection")

            with patch("semantic_search.context.encoding", return_value=Counter()):
                with self.assertRaisesRegex(RuntimeError, "audit rejection"):
                    study.replay(fixture(), report, root / "replay", counter=Counter(), audit_hook=reject)
            self.assertFalse((root / "replay/summary.json").exists())
            self.assertEqual(json.loads((root / "replay/failure.json").read_bytes())["stored_rows"], 0)

    def test_gpu_lease_is_exclusive_and_released_without_loading_any_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            subprocess.run(["git", "init", "-q", temporary], check=True)
            with study.gpu_lease(temporary) as first:
                self.assertIsNotNone(first["acquired_at"])
                with self.assertRaises(BlockingIOError):
                    with study.gpu_lease(temporary):
                        self.fail("second lease admitted")
            self.assertIsNotNone(first["released_at"])
            with study.gpu_lease(temporary) as second:
                self.assertIsNotNone(second["acquired_at"])

    def test_existing_directory_never_claimed_or_modified(self):
        embedder, _ = fake_embedder()
        claims = []
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "old"
            output.mkdir()
            (output / "original").write_text("preserve this attempt")
            before = {path.name: path.read_bytes() for path in output.iterdir()}
            with self.assertRaises(FileExistsError):
                study.capture(fixture(), output, embedder=embedder, binding=binding(embedder),
                              on_claim=lambda: claims.append(True))
            self.assertEqual(claims, [])
            self.assertEqual({path.name: path.read_bytes() for path in output.iterdir()}, before)

    def test_cli_existing_output_rejects_before_runtime_or_gpu_lease(self):
        prepared = fixture()
        fake_inputs = SimpleNamespace(verify_frozen=lambda *args, **kwargs: {},
                                      load_prepared=lambda *args: prepared)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "old"
            output.mkdir()
            (output / "original").write_text("untouched")
            arguments = ["semantic_model_study", "--capture", temporary, "--output", str(output),
                         "--freeze", "invented-freeze", "--runtime-manifest", "invented.json", "--model-path", "invented"]
            with patch.object(sys, "argv", arguments), patch.dict(sys.modules, {"semantic_study_inputs": fake_inputs}), \
                    patch.object(study, "git", return_value=""), \
                    patch.object(study, "verify_runtime", side_effect=AssertionError("runtime accessed")), \
                    patch.object(study, "gpu_lease", side_effect=AssertionError("GPU lease acquired")):
                with self.assertRaisesRegex(ValueError, "already exists"):
                    study.main()
            self.assertEqual([path.name for path in output.iterdir()], ["original"])

    def test_independent_auditor_reconstructs_invented_capture_and_all_outcomes(self):
        import audit_semantic_study as auditor

        prepared = fixture()
        embedder, _ = fake_embedder()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = study.capture(prepared, root / "capture", embedder=embedder, binding=binding(embedder))
            counts = auditor.verify_capture(report, prepared["feature_requests"],
                forward_events=lines(root / "capture/forward-events.jsonl"),
                document_checkpoints=lines(root / "capture/document-vectors.jsonl"),
                query_checkpoints=lines(root / "capture/query-vectors.jsonl"))
            self.assertEqual(counts, report["forward_summary"])
            with patch("semantic_search.context.encoding", return_value=Counter()):
                summary = study.replay(prepared, report, root / "replay", counter=Counter())
            gate = auditor.audit_outcomes(prepared["cases"], lines(root / "replay/outcomes.jsonl.gz"),
                report, prepared["feature_requests"], counter=Counter(), expected_rows=2, expected_units=1)
            self.assertEqual(gate["qualified"], summary["quality_gate"]["qualified"])

    def test_outer_close_error_cannot_replace_active_model_error(self):
        model_error = RuntimeError("invented model")
        cleanup_error = LookupError("invented close")

        def close():
            raise cleanup_error

        with self.assertRaises(RuntimeError) as actual:
            try:
                raise model_error
            finally:
                study.close_preserving_error(SimpleNamespace(close=close))
        self.assertIs(actual.exception, model_error)
        self.assertIs(actual.exception.__cause__, cleanup_error)

    def test_compressed_ledger_flushes_durable_rows_and_hashes_final_trailer(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rows.jsonl.gz"
            ledger = study.Ledger(path, compressed=True)
            with patch.object(study.os, "fsync", wraps=os.fsync) as fsync:
                ledger.append({"row": 1})
                first = path.read_bytes()
                self.assertEqual(first[4:8], b"\0" * 4)  # reproducible gzip mtime
                self.assertEqual(zlib.decompressobj(31).decompress(first), b'{"row":1}\n')
                ledger.append({"row": 2})
                self.assertEqual(zlib.decompressobj(31).decompress(path.read_bytes()),
                                 b'{"row":1}\n{"row":2}\n')
                self.assertEqual(fsync.call_count, 2)
                before_close = study.file_sha(path)
                ledger.close()
                self.assertEqual(fsync.call_count, 3)
            self.assertNotEqual(before_close, study.file_sha(path))
            self.assertEqual(gzip.decompress(path.read_bytes()), b'{"row":1}\n{"row":2}\n')
            self.assertEqual(lines(path), [{"row": 1}, {"row": 2}])
            ledger.close()  # idempotent; the artifact hash remains final

    def test_partial_replay_failure_hash_includes_closed_gzip_trailer(self):
        embedder, _ = fake_embedder()
        checked = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = study.capture(fixture(), root / "capture", embedder=embedder, binding=binding(embedder))

            def reject_second(case, outcome, features):
                checked.append(case["case_id"])
                if len(checked) == 2:
                    raise RuntimeError("invented second audit rejection")

            with patch("semantic_search.context.encoding", return_value=Counter()):
                with self.assertRaisesRegex(RuntimeError, "second audit rejection"):
                    study.replay(fixture(), report, root / "replay", counter=Counter(), audit_hook=reject_second)
            path = root / "replay/outcomes.jsonl.gz"
            failure = json.loads((root / "replay/failure.json").read_bytes())
            self.assertEqual(failure["stored_rows"], 1)
            self.assertEqual(failure["outcomes_sha256"], study.file_sha(path))
            self.assertEqual(len(lines(path)), 1)
            self.assertEqual(lines(path)[0]["case_id"], "invented_case_0")

    def test_feature_digest_is_sorted_literal_utf8_including_null(self):
        self.assertEqual(study.features_sha256(None), study.digest(b"null"))
        first, second = {"é": 1, "a": [2.0]}, {"a": [2.0], "é": 1}
        expected = study.digest(json.dumps(first, ensure_ascii=False, sort_keys=True,
                                          separators=(",", ":"), allow_nan=False).encode())
        self.assertEqual(study.features_sha256(first), expected)
        self.assertEqual(study.features_sha256(second), expected)

    def test_replay_parent_link_is_durable_before_any_proposal(self):
        embedder, _ = fake_embedder()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = study.capture(fixture(), root / "capture", embedder=embedder, binding=binding(embedder))
            parent_stat = root.stat()
            parent_synced = []
            real_fsync = os.fsync

            def fsync(descriptor):
                details = os.fstat(descriptor)
                if stat.S_ISDIR(details.st_mode) and (details.st_dev, details.st_ino) == (parent_stat.st_dev, parent_stat.st_ino):
                    parent_synced.append(True)
                real_fsync(descriptor)

            def proposal(*args, **kwargs):
                self.assertTrue(parent_synced, "Replay parent directory must be synced before a proposal")
                raise RuntimeError("invented stop before proposal")

            with patch.object(study.os, "fsync", side_effect=fsync), \
                    patch("semantic_search.semantic_context.semantic_final_context", side_effect=proposal):
                with self.assertRaisesRegex(RuntimeError, "stop before proposal"):
                    study.replay(fixture(), report, root / "replay", counter=Counter())
            self.assertEqual(parent_synced, [True])


if __name__ == "__main__":
    unittest.main()
