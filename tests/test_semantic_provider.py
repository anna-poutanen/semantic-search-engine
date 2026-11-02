"""Invented execution stubs only: no model, persisted corpus or index loading."""
from array import array
from copy import deepcopy
import hashlib
import json
import struct
import unittest
from unittest.mock import patch

from semantic_search.semantic_features import (
    DIMENSIONS, MODEL_ID, MODEL_REVISION, QUERY_PROMPT,
    expected_facets, validated_scores,
)
from semantic_search.semantic_provider import make_live_feature_provider


QUERY = "Find shared requests for running transports, and publish successful values to active observers"


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def vector(index=0):
    values = array("f", [0.0] * DIMENSIONS)
    values[index] = 1.0
    values[-1] = -0.0
    return values


def row(identifier, **updates):
    source = f"def {identifier}():\n    return None\n"
    value = {"id": identifier, "path": f"src/{identifier}.py", "symbol": identifier,
             "start_line": 1, "end_line": 2, "window_end_line": 2, "source": source,
             "source_hash": sha(source), "content_hash": sha(source), "truncated": False,
             "source_origin": "saved", "origin": "retrieval", "related_to": None,
             "retrieval_score": 0.4, "h2o_score": None, "qwen_score": None}
    value.update(updates)
    return value


def metadata():
    return {"id": MODEL_ID, "revision": MODEL_REVISION, "dimensions": DIMENSIONS,
            "query_prompt": QUERY_PROMPT, "normalization": "L2, float32",
            "packages": {"invented-runtime": "0.0"}, "batch_size": 4,
            "document_prompt": None}


def forward_events(texts, groups=None):
    events = []
    for ordinal, indices in enumerate(groups or [list(range(len(texts)))]):
        tokens = [len(texts[index]) for index in indices]
        event = {"schema": "qwen_embedding_forward_v1", "status": "started",
                 "forward_index": ordinal, "input_count": len(texts),
                 "batch_input_count": len(indices), "input_indices": indices,
                 "input_sha256": [sha(texts[index]) for index in indices],
                 "token_ids_sha256": [sha(f"invented tokens {index}") for index in indices],
                 "token_lengths": tokens, "padded_tokens": max(tokens) * len(indices),
                 "padded_shape": [len(indices), max(tokens)],
                 "expected_output_shape": [len(indices), DIMENSIONS], "elapsed_seconds": ordinal * 0.1}
        events.append(event)
        events.append({**deepcopy(event), "status": "completed", "elapsed_seconds": ordinal * 0.1 + 0.02,
                       "duration_seconds": 0.01, "output_shape": [len(indices), DIMENSIONS],
                       "output_dtype": "float32"})
    return events


class InventedEmbedder:
    def __init__(self):
        self.current_metadata = metadata()
        self.calls = []
        self.after = None
        self.alter_events = lambda events: events
        self.groups = None

    @property
    def metadata(self):
        return deepcopy(self.current_metadata)

    def embed_queries(self, queries, *, cancel_check, forward_observer):
        cancel_check()
        self.calls.append(list(queries))
        events = forward_events([QUERY_PROMPT + query for query in queries], self.groups)
        for event in self.alter_events(events):
            forward_observer(event)
        if self.after is not None:
            self.after()
        cancel_check()
        return [vector(index + 1) for index in range(len(queries))]

    def embed_query(self, *args, **kwargs):
        raise AssertionError("Original retrieval query must never be re-encoded")

    def embed_documents(self, *args, **kwargs):
        raise AssertionError("Feature provider must never infer document vectors")


class InventedIndex:
    def __init__(self):
        self.calls = []
        self.after = None
        self.alter = lambda values: values

    def read_semantic_vectors(self, rows, *, model_metadata, cancel_check):
        cancel_check()
        self.calls.append((deepcopy(rows), deepcopy(model_metadata)))
        values = self.alter({item["id"]: vector() for item in rows})
        if self.after is not None:
            self.after()
        return values


def fixture():
    embedder, index = InventedEmbedder(), InventedIndex()
    raw = [row("a"), row("b")]
    reference = {"query": QUERY, "retrieval": "dense", "match_status": "candidates",
                 "results": [deepcopy(raw[0]), row("dependency", origin="dependency", related_to="a")]}
    model = metadata()
    checks = []
    provenances = {item["path"]: {"source_origin": "saved"} for item in [*raw, *reference["results"]]}

    def confirm():
        checks.append("snapshot")

    context = {"query": QUERY, "retrieval": "dense", "query_vector": vector(),
               "query_forward_events": forward_events([QUERY_PROMPT + QUERY]),
               "model_metadata": model,
               "model_fingerprint": sha(json.dumps(model, sort_keys=True, separators=(",", ":"))),
               "source_snapshot": "invented-stable-source", "index": index, "embedder": embedder,
               "confirm_snapshot": confirm, "source_provenance": lambda path: provenances[path]}
    return context, raw, reference, checks, provenances


class SemanticProviderTests(unittest.TestCase):
    def test_actual_query_api_and_forward_observer_interoperate_with_cpu_doubles(self):
        import numpy as np
        from semantic_search.embeddings import QwenEmbedder

        class InventedTokenizer:
            def __call__(self, texts, **kwargs):
                return {"input_ids": [list(text.encode())[:kwargs["max_length"]] for text in texts]}

        context, raw, reference, _, _ = fixture()
        embedder = QwenEmbedder(batch_size=1, progress=False)
        embedder._tokenizer = InventedTokenizer()
        forwards = []

        def invented_forward(rows):
            forwards.append(deepcopy(rows))
            return np.asarray([vector() for _ in rows], dtype=np.float32)

        embedder._embed_batch = invented_forward
        original_events = []
        with patch.object(embedder, "_ensure_loaded", return_value=None):
            context["query_vector"] = embedder.embed_query(QUERY, forward_observer=original_events.append)
            context["query_forward_events"] = original_events
            context["embedder"] = embedder
            context["model_metadata"] = embedder.metadata
            context["model_fingerprint"] = sha(json.dumps(embedder.metadata, sort_keys=True,
                                                         separators=(",", ":")))
            captures = []
            features = make_live_feature_provider(context, capture=captures.append)(reference, raw)
        self.assertEqual(len(forwards), 3)  # one original plus two actual clause forwards
        self.assertEqual(captures[0]["original_query_model_forward_calls"], 1)
        self.assertEqual(captures[0]["extra_query_api_calls"], 1)
        self.assertEqual(captures[0]["extra_query_model_forward_calls"], 2)
        self.assertEqual(features["facet_vectors"][0], context["query_vector"].tolist())
        self.assertIsNone(embedder._model)

    def test_original_vector_bytes_reused_and_only_extra_clauses_encoded(self):
        context, raw, reference, checks, _ = fixture()
        original_bytes = context["query_vector"].tobytes()
        captures = []
        features = make_live_feature_provider(context, capture=captures.append)(reference, raw)
        self.assertEqual(features["facets"], expected_facets(QUERY))
        self.assertEqual(context["embedder"].calls, [expected_facets(QUERY)[1:]])
        self.assertEqual(len(context["index"].calls), 1)
        self.assertEqual([item["id"] for item in context["index"].calls[0][0]], ["a", "b", "dependency"])
        self.assertEqual(struct.pack(f"<{DIMENSIONS}f", *features["facet_vectors"][0]), original_bytes)
        self.assertEqual(context["query_vector"].tobytes(), original_bytes)
        self.assertEqual(checks, ["snapshot"] * 3)
        facets, scores = validated_scores(reference, raw, features)
        self.assertEqual(facets, expected_facets(QUERY))
        self.assertEqual(scores["a"], (1_000_000, 0, 0))
        receipt = captures[0]
        self.assertEqual(receipt["status"], "active")
        self.assertEqual(receipt["original_query_vector"], {
            "origin": "executing_retrieval_query_vector", "reencoded": False,
            "dimensions": DIMENSIONS, "float32_le_sha256": hashlib.sha256(original_bytes).hexdigest()})
        self.assertEqual(receipt["extra_clause_prompts"], [QUERY_PROMPT + part for part in facets[1:]])
        self.assertEqual(receipt["extra_query_api_calls"], 1)
        self.assertEqual(receipt["extra_query_model_forward_calls"], 1)
        self.assertEqual(receipt["extra_query_encoded_texts"], 2)
        self.assertEqual(receipt["original_query_model_forward_calls"], 1)
        self.assertEqual(receipt["original_query_encoded_texts"], 1)
        self.assertEqual(receipt["resident_vector_reads"], 1)
        self.assertEqual(receipt["document_inference_calls"], 0)
        self.assertEqual(receipt["extra_retrieval_calls"], 0)
        self.assertEqual(set(receipt["windows"]), {"a", "b", "dependency"})
        self.assertNotIn("forward_events", features)

    def test_multiple_actual_forwards_reported_from_one_clause_api_call(self):
        context, raw, reference, _, _ = fixture()
        context["embedder"].groups = [[1], [0]]
        captures = []
        make_live_feature_provider(context, capture=captures.append)(reference, raw)
        receipt = captures[0]
        self.assertEqual(receipt["extra_query_api_calls"], 1)
        self.assertEqual(receipt["extra_query_model_forward_calls"], 2)
        self.assertEqual(receipt["extra_query_encoded_texts"], 2)
        self.assertEqual(receipt["extra_query_forward_events"][0]["input_indices"], [1])

    def test_dense_hybrid_and_multifile_are_explicitly_supported(self):
        for mode in ("dense", "hybrid", "multifile"):
            with self.subTest(mode=mode):
                context, raw, reference, _, _ = fixture()
                context["retrieval"] = reference["retrieval"] = mode
                self.assertIsInstance(make_live_feature_provider(context)(reference, raw), dict)

    def test_inactive_policies_do_no_vector_or_clause_work(self):
        for reason in ("lexical_only", "missing_original_query_vector", "abstained", "empty_raw_pool",
                       "empty_reference", "ineligible_query"):
            with self.subTest(reason=reason):
                context, raw, reference, checks, _ = fixture()
                if reason == "lexical_only":
                    context["retrieval"] = reference["retrieval"] = "bm25"
                elif reason == "missing_original_query_vector":
                    context["query_vector"] = None
                elif reason == "abstained":
                    reference["match_status"] = "insufficient_evidence"
                elif reason == "empty_raw_pool":
                    raw.clear()
                elif reason == "empty_reference":
                    reference["results"].clear()
                else:
                    context["query"] = reference["query"] = "Find the request implementation"
                captures = []
                self.assertIsNone(make_live_feature_provider(context, capture=captures.append)(reference, raw))
                self.assertEqual(captures[0]["reason"], reason)
                self.assertEqual(captures[0]["extra_query_api_calls"], 0)
                self.assertEqual(context["embedder"].calls, [])
                self.assertEqual(context["index"].calls, [])
                self.assertEqual(checks, [])

    def test_lexical_inactive_does_not_touch_missing_model_or_source_handles(self):
        context, raw, reference, _, _ = fixture()
        context = {"query": QUERY, "retrieval": "bm25"}
        reference["retrieval"] = "bm25"
        self.assertIsNone(make_live_feature_provider(context)(reference, raw))

    def test_clipped_reference_excluded_and_full_raw_identity_retained(self):
        context, raw, reference, _, _ = fixture()
        clipped = deepcopy(raw[0])
        clipped.update(source="def a():\n", source_hash=sha("def a():\n"), end_line=1, truncated=True)
        reference["results"] = [clipped]
        features = make_live_feature_provider(context)(reference, raw)
        self.assertEqual(set(features["windows"]), {"a", "b"})
        self.assertEqual(features["windows"]["a"]["binding"]["end_line"], 2)

    def test_clipped_reference_without_full_raw_identity_has_no_vector(self):
        context, raw, reference, _, _ = fixture()
        clipped = reference["results"][1]
        clipped.update(source="def dependency():\n", source_hash=sha("def dependency():\n"),
                       end_line=1, truncated=True)
        features = make_live_feature_provider(context)(reference, raw)
        self.assertEqual(set(features["windows"]), {"a", "b"})

    def test_buffer_provenance_requires_exact_executing_child_version_and_hash(self):
        context, raw, reference, _, provenances = fixture()
        for item in [*raw, *reference["results"]]:
            item.update(source_origin="buffer", buffer_version=7, buffer_hash="b" * 64)
            provenances[item["path"]] = {"source_origin": "buffer", "buffer_version": 7,
                                          "buffer_hash": "b" * 64}
        features = make_live_feature_provider(context)(reference, raw)
        self.assertEqual(features["windows"]["a"]["binding"]["buffer_version"], 7)
        self.assertEqual(features["windows"]["dependency"]["binding"]["buffer_hash"], "b" * 64)

    def test_source_provenance_presence_type_and_value_checked_before_any_work(self):
        for field, value in (("source_origin", "buffer"), ("buffer_version", 1),
                             ("buffer_hash", "e" * 64)):
            with self.subTest(field=field):
                context, raw, reference, _, _ = fixture()
                reference["results"][1][field] = value
                with self.assertRaisesRegex(ValueError, "provenance differs"):
                    make_live_feature_provider(context)(reference, raw)
                self.assertEqual(context["index"].calls, [])
                self.assertEqual(context["embedder"].calls, [])
        context, raw, reference, _, provenances = fixture()
        raw[1]["buffer_version"] = 1
        provenances[raw[1]["path"]]["buffer_version"] = True
        with self.assertRaisesRegex(ValueError, "provenance differs"):
            make_live_feature_provider(context)(reference, raw)

    def test_executing_provenance_drift_after_vector_read_prevents_clause_work(self):
        context, raw, reference, _, provenances = fixture()
        context["index"].after = lambda: provenances[raw[0]["path"]].update(source_origin="buffer")
        with self.assertRaisesRegex(ValueError, "provenance differs"):
            make_live_feature_provider(context)(reference, raw)
        self.assertEqual(context["embedder"].calls, [])

    def test_conflicting_duplicate_source_identity_fails_before_any_work(self):
        context, raw, reference, _, _ = fixture()
        reference["results"][0]["path"] = "elsewhere.py"
        with self.assertRaisesRegex(ValueError, "Conflicting full"):
            make_live_feature_provider(context)(reference, raw)
        self.assertEqual(context["index"].calls, [])

    def test_wrong_reference_query_or_mode_raises_instead_of_inactive(self):
        for field, value in (("query", "Another query"), ("retrieval", "bm25")):
            with self.subTest(field=field):
                context, raw, reference, _, _ = fixture()
                reference[field] = value
                with self.assertRaisesRegex(ValueError, "differs from the reference"):
                    make_live_feature_provider(context)(reference, raw)

    def test_model_fingerprint_and_metadata_drift_fail_before_read(self):
        for mutation in ("fingerprint", "metadata", "runtime"):
            with self.subTest(mutation=mutation):
                context, raw, reference, _, _ = fixture()
                if mutation == "fingerprint":
                    context["model_fingerprint"] = "0" * 64
                elif mutation == "metadata":
                    context["model_metadata"]["revision"] = "different"
                else:
                    context["embedder"].current_metadata["packages"]["invented-runtime"] = "changed"
                with self.assertRaises(ValueError):
                    make_live_feature_provider(context)(reference, raw)
                self.assertEqual(context["index"].calls, [])

    def test_context_and_model_drift_after_clause_encoding_propagate(self):
        for mutation in ("source_snapshot", "query", "retrieval", "query_vector", "index", "embedder",
                         "model_metadata", "runtime", "query_forward_events", "source_provenance"):
            with self.subTest(mutation=mutation):
                context, raw, reference, _, _ = fixture()

                def alter():
                    if mutation == "query_vector":
                        context["query_vector"][0] = -1.0
                    elif mutation == "model_metadata":
                        context["model_metadata"]["packages"]["invented-runtime"] = "changed"
                    elif mutation == "runtime":
                        context["embedder"].current_metadata["packages"]["invented-runtime"] = "changed"
                    elif mutation == "query_forward_events":
                        context[mutation].clear()
                    elif mutation == "source_provenance":
                        context[mutation] = lambda path: {"source_origin": "saved"}
                    else:
                        context[mutation] = "changed"

                context["embedder"].after = alter
                captures = []
                with self.assertRaises(ValueError):
                    make_live_feature_provider(context, capture=captures.append)(reference, raw)
                self.assertEqual([item["status"] for item in captures], ["failed"])

    def test_current_snapshot_exception_propagates_before_read(self):
        context, raw, reference, _, _ = fixture()

        def stale():
            raise RuntimeError("invented source changed")

        context["confirm_snapshot"] = stale
        with self.assertRaisesRegex(RuntimeError, "source changed"):
            make_live_feature_provider(context)(reference, raw)
        self.assertEqual(context["index"].calls, [])

    def test_vector_accessor_error_and_bad_membership_prevent_clause_encoding(self):
        for invalid in ("raises", "missing", "extra", "wrong_dimension", "not_float32"):
            with self.subTest(invalid=invalid):
                context, raw, reference, _, _ = fixture()

                def alter(values):
                    if invalid == "raises":
                        raise RuntimeError("invented vector lookup failed")
                    if invalid == "missing":
                        values.pop("b")
                    elif invalid == "extra":
                        values["extra"] = vector()
                    elif invalid == "wrong_dimension":
                        values["a"] = [1.0]
                    else:
                        values["a"] = list(vector())
                        values["a"][1] = 0.1
                    return values

                context["index"].alter = alter
                with self.assertRaises((RuntimeError, ValueError)):
                    make_live_feature_provider(context)(reference, raw)
                self.assertEqual(context["embedder"].calls, [])

    def test_bad_original_query_vector_rejected_before_read(self):
        for bad in ([1.0], [0.0] * DIMENSIONS, [float("nan")] * DIMENSIONS, [1] * DIMENSIONS):
            with self.subTest(bad=bad[:1]):
                context, raw, reference, _, _ = fixture()
                context["query_vector"] = bad
                with self.assertRaises(ValueError):
                    make_live_feature_provider(context)(reference, raw)
                self.assertEqual(context["index"].calls, [])

    def test_missing_or_invalid_original_forward_receipts_fail_before_read(self):
        for invalid in ("missing", "missing_start", "input_hash", "failed", "duplicate", "completion"):
            with self.subTest(invalid=invalid):
                context, raw, reference, _, _ = fixture()
                events = context["query_forward_events"]
                if invalid == "missing":
                    context.pop("query_forward_events")
                elif invalid == "missing_start":
                    del events[0]
                elif invalid == "input_hash":
                    for event in events:
                        event["input_sha256"] = ["e" * 64]
                elif invalid == "failed":
                    events[1]["status"] = "failed"
                elif invalid == "duplicate":
                    events.extend(deepcopy(events))
                else:
                    events[1]["token_lengths"] = [1]
                with self.assertRaises(ValueError):
                    make_live_feature_provider(context)(reference, raw)
                self.assertEqual(context["index"].calls, [])

    def test_extra_query_receipts_require_actual_complete_once_per_input_coverage(self):
        for invalid in ("missing", "missing_terminal", "duplicate_input", "wrong_prompt", "wrong_count"):
            with self.subTest(invalid=invalid):
                context, raw, reference, _, _ = fixture()
                def alter(events):
                    if invalid == "missing":
                        return []
                    if invalid == "missing_terminal":
                        return events[:1]
                    for event in events:
                        if invalid == "duplicate_input":
                            event["input_indices"] = [0, 0]
                            event["input_sha256"] = [event["input_sha256"][0]] * 2
                        elif invalid == "wrong_prompt":
                            event["input_sha256"] = ["f" * 64] * 2
                        else:
                            event["input_count"] = 1
                    return events

                context["embedder"].alter_events = alter
                captures = []
                with self.assertRaises(ValueError):
                    make_live_feature_provider(context, capture=captures.append)(reference, raw)
                self.assertEqual([item["status"] for item in captures], ["failed"])

    def test_clause_encoder_exception_is_not_an_inactive_result(self):
        context, raw, reference, _, _ = fixture()

        def broken():
            raise RuntimeError("invented clause model failed")

        context["embedder"].after = broken
        with self.assertRaisesRegex(RuntimeError, "clause model failed"):
            make_live_feature_provider(context)(reference, raw)

    def test_cancellation_propagates_before_work_and_after_clause_forward(self):
        class Cancelled(RuntimeError):
            pass

        context, raw, reference, _, _ = fixture()

        def stop():
            raise Cancelled("invented cancelled")

        with self.assertRaises(Cancelled):
            make_live_feature_provider(context)(reference, raw, cancel_check=stop)
        self.assertEqual(context["index"].calls, [])
        stopped = []
        context["embedder"].after = lambda: stopped.append(True)

        def check():
            if stopped:
                raise Cancelled("invented after forward")

        captures = []
        with self.assertRaises(Cancelled):
            make_live_feature_provider(context, capture=captures.append)(reference, raw, cancel_check=check)
        self.assertEqual([item["status"] for item in captures], ["failed"])
        self.assertEqual(len(context["embedder"].calls), 1)

    def test_capture_is_detached_from_context_events_and_feature_bindings(self):
        context, raw, reference, _, _ = fixture()
        captures = []
        features = make_live_feature_provider(context, capture=captures.append)(reference, raw)
        context["query_forward_events"].clear()
        context["model_metadata"]["packages"].clear()
        raw[0]["source_origin"] = "buffer"
        features["windows"]["a"]["binding"]["path"] = "changed.py"
        self.assertEqual(len(captures[0]["original_query_forward_events"]), 2)
        self.assertEqual(captures[0]["windows"]["a"]["binding"]["path"], "src/a.py")
        self.assertEqual(captures[0]["model_metadata"]["packages"], {"invented-runtime": "0.0"})

    def test_failed_clause_call_retains_started_only_without_claiming_a_forward(self):
        context, raw, reference, _, _ = fixture()
        context["embedder"].alter_events = lambda events: events[:1]
        error = RuntimeError("invented observer stopped before model")

        def fail():
            raise error

        context["embedder"].after = fail
        captures = []
        with self.assertRaises(RuntimeError) as actual:
            make_live_feature_provider(context, capture=captures.append)(reference, raw)
        self.assertIs(actual.exception, error)
        receipt = captures[0]
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["stage"], "clause_encoding")
        self.assertEqual(receipt["error_type"], "builtins.RuntimeError")
        self.assertEqual([event["status"] for event in receipt["extra_query_forward_events"]], ["started"])
        self.assertEqual(receipt["extra_query_work"]["reported_model_forward_calls"], 0)
        self.assertEqual(receipt["extra_query_api_calls"], 1)
        self.assertEqual(receipt["extra_query_api_completed_calls"], 0)
        self.assertEqual(receipt["resident_vector_read_attempts"], 1)
        self.assertEqual(receipt["resident_vector_reads"], 1)
        self.assertEqual(receipt["original_query_forward_events"], context["query_forward_events"])

    def test_failed_native_forward_is_retained_with_its_original_exception(self):
        context, raw, reference, _, _ = fixture()
        error = RuntimeError("invented model exception")

        def failed_events(events):
            events[1]["status"] = "failed"
            events[1]["exception_type"] = "builtins.RuntimeError"
            events[1]["failure_phase"] = "forward"
            return events

        def fail():
            raise error

        context["embedder"].alter_events = failed_events
        context["embedder"].after = fail
        captures = []
        with self.assertRaises(RuntimeError) as actual:
            make_live_feature_provider(context, capture=captures.append)(reference, raw)
        self.assertIs(actual.exception, error)
        receipt = captures[0]
        self.assertEqual([event["status"] for event in receipt["extra_query_forward_events"]], ["started", "failed"])
        self.assertEqual(receipt["extra_query_work"]["reported_model_forward_calls"], 1)
        self.assertEqual(receipt["extra_query_work"]["reported_failed_model_forward_calls"], 1)
        self.assertEqual(receipt["extra_query_work"]["reported_completed_model_forward_calls"], 0)
        self.assertEqual(receipt["extra_query_work"]["reported_encoded_texts"], 2)

    def test_post_forward_cancellation_keeps_completed_work_and_original_receipts(self):
        context, raw, reference, _, _ = fixture()
        stopped = []
        context["embedder"].after = lambda: stopped.append(True)
        error = RuntimeError("invented cancelled after forward")

        def check():
            if stopped:
                raise error

        captures = []
        with self.assertRaises(RuntimeError) as actual:
            make_live_feature_provider(context, capture=captures.append)(reference, raw, cancel_check=check)
        self.assertIs(actual.exception, error)
        receipt = captures[0]
        self.assertEqual(receipt["stage"], "clause_encoding")
        self.assertEqual(receipt["extra_query_work"]["reported_completed_model_forward_calls"], 1)
        self.assertEqual(receipt["extra_query_api_completed_calls"], 0)
        self.assertEqual(receipt["original_query_work"]["reported_completed_model_forward_calls"], 1)

    def test_source_drift_after_encoding_captures_completed_clause_work(self):
        context, raw, reference, _, provenances = fixture()
        context["embedder"].after = lambda: provenances[raw[0]["path"]].update(source_origin="buffer")
        captures = []
        with self.assertRaisesRegex(ValueError, "provenance differs"):
            make_live_feature_provider(context, capture=captures.append)(reference, raw)
        receipt = captures[0]
        self.assertEqual(receipt["stage"], "source_validation_after_clauses")
        self.assertEqual(receipt["extra_query_api_completed_calls"], 1)
        self.assertEqual(receipt["extra_query_work"]["reported_completed_model_forward_calls"], 1)
        self.assertEqual(receipt["extra_query_work"]["reported_encoded_texts"], 2)

    def test_entry_cancellation_keeps_original_query_events_but_no_provider_work(self):
        context, raw, reference, _, _ = fixture()
        error = RuntimeError("invented entry cancellation")

        def check():
            raise error

        captures = []
        with self.assertRaises(RuntimeError) as actual:
            make_live_feature_provider(context, capture=captures.append)(reference, raw, cancel_check=check)
        self.assertIs(actual.exception, error)
        receipt = captures[0]
        self.assertEqual(receipt["stage"], "request_validation")
        self.assertEqual(receipt["original_query_forward_events"], context["query_forward_events"])
        self.assertEqual(receipt["extra_query_api_calls"], 0)
        self.assertEqual(receipt["resident_vector_read_attempts"], 0)
        self.assertEqual(receipt["extra_query_forward_events"], [])

    def test_read_failure_reports_attempt_separately_from_a_successful_read(self):
        context, raw, reference, _, _ = fixture()
        error = RuntimeError("invented read failed")

        def fail(values):
            raise error

        context["index"].alter = fail
        captures = []
        with self.assertRaises(RuntimeError) as actual:
            make_live_feature_provider(context, capture=captures.append)(reference, raw)
        self.assertIs(actual.exception, error)
        receipt = captures[0]
        self.assertEqual(receipt["stage"], "resident_vector_read")
        self.assertEqual(receipt["resident_vector_read_attempts"], 1)
        self.assertEqual(receipt["resident_vector_reads"], 0)
        self.assertEqual(receipt["extra_query_api_calls"], 0)

    def test_failed_capture_cannot_replace_the_original_execution_exception(self):
        context, raw, reference, _, _ = fixture()
        execution_error = RuntimeError("invented original")
        capture_error = LookupError("invented capture")

        def fail():
            raise execution_error

        def failed_capture(receipt):
            self.assertEqual(receipt["status"], "failed")
            raise capture_error

        context["embedder"].after = fail
        with self.assertRaises(RuntimeError) as actual:
            make_live_feature_provider(context, capture=failed_capture)(reference, raw)
        self.assertIs(actual.exception, execution_error)
        self.assertIs(actual.exception.__cause__, capture_error)

    def test_failed_receipt_keeps_entry_binding_when_live_context_mutates(self):
        context, raw, reference, _, _ = fixture()
        context["embedder"].after = lambda: context.update(query="changed", source_snapshot="changed")
        captures = []
        with self.assertRaises(ValueError):
            make_live_feature_provider(context, capture=captures.append)(reference, raw)
        self.assertEqual(captures[0]["query"], QUERY)
        self.assertEqual(captures[0]["source_snapshot"], "invented-stable-source")

    def test_inconsistent_forward_shape_budget_times_and_order_are_rejected(self):
        invalid_cases = ("negative_time", "nan_time", "time_backwards", "negative_duration", "long_duration",
                         "negative_length", "excess_tokens", "padded_shape", "padded_tokens", "padded_budget",
                         "batch_size", "token_hash", "expected_output", "output_shape", "output_dtype", "order")
        for invalid in invalid_cases:
            with self.subTest(invalid=invalid):
                context, raw, reference, _, _ = fixture()
                if invalid == "batch_size":
                    context["model_metadata"]["batch_size"] = 1
                    context["embedder"].current_metadata["batch_size"] = 1
                    context["model_fingerprint"] = sha(json.dumps(context["model_metadata"], sort_keys=True,
                                                                  separators=(",", ":")))

                def alter(events):
                    if invalid == "order":
                        return list(reversed(events))
                    for event in events:
                        if invalid == "negative_time":
                            event["elapsed_seconds"] = -1.0
                        elif invalid == "nan_time":
                            event["elapsed_seconds"] = float("nan")
                        elif invalid == "time_backwards":
                            event["elapsed_seconds"] = 1.0 if event["status"] == "started" else 0.0
                        elif invalid == "negative_duration":
                            event["duration_seconds"] = -1.0
                        elif invalid == "long_duration":
                            event["duration_seconds"] = 10.0
                        elif invalid == "negative_length":
                            event["token_lengths"] = [-1, -1]
                        elif invalid == "excess_tokens":
                            event["token_lengths"] = [8193, 8193]
                        elif invalid == "padded_shape":
                            event["padded_shape"] = [1, 1]
                        elif invalid == "padded_tokens":
                            event["padded_tokens"] = True
                        elif invalid == "padded_budget":
                            event["token_lengths"] = [8192, 8192]
                            event["padded_shape"] = [2, 8192]
                            event["padded_tokens"] = 16384
                        elif invalid == "token_hash":
                            event["token_ids_sha256"] = ["not-a-hash"] * 2
                        elif invalid == "expected_output":
                            event["expected_output_shape"] = [2, 3]
                        elif invalid == "output_shape":
                            event["output_shape"] = [2, 3]
                        elif invalid == "output_dtype":
                            event["output_dtype"] = "float64"
                    return events

                context["embedder"].alter_events = alter
                captures = []
                with self.assertRaises(ValueError):
                    make_live_feature_provider(context, capture=captures.append)(reference, raw)
                self.assertEqual(captures[-1]["status"], "failed")

    def test_factory_validates_context_and_capture(self):
        with self.assertRaises(TypeError):
            make_live_feature_provider(None)
        with self.assertRaises(TypeError):
            make_live_feature_provider({}, capture="not callable")


if __name__ == "__main__":
    unittest.main()
