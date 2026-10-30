"""Synthetic feature-boundary checks; no checkpoints, source corpora or indices."""
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import unittest
from unittest.mock import patch

from semantic_search.semantic_features import (
    DIMENSIONS, MODEL_ID, MODEL_REVISION, QUERY_PROMPT, SCHEMA,
    document_text, expected_facets, make_features, row_binding, validated_scores,
)


QUERY = ("Find shared requests for running transports, and publish successful values to active observers")


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def row(identifier, source="first()\nsecond()\n", **updates):
    result = {"id": identifier, "path": f"src/{identifier}.py", "symbol": f"run_{identifier}",
              "start_line": 1, "end_line": 2, "window_end_line": 2, "source": source,
              "source_hash": digest(source), "content_hash": digest(source), "truncated": False,
              "retrieval_score": 0.25, "h2o_score": None, "qwen_score": None,
              "origin": "retrieval", "related_to": None, "source_origin": "saved"}
    result.update(updates)
    return result


def vector(index=0, value=1.0):
    result = [0.0] * DIMENSIONS
    result[index] = value
    return result


def metadata():
    return {"id": MODEL_ID, "revision": MODEL_REVISION, "dimensions": DIMENSIONS,
            "query_prompt": QUERY_PROMPT, "normalization": "L2, float32",
            "packages": {"invented-runtime": "0.0"}, "batch_size": 4,
            "document_prompt": None}


def fixture():
    raw = [row("a"), row("b")]
    dependency = row("c", origin="dependency", related_to="a")
    reference = {"query": QUERY, "results": [deepcopy(raw[0]), dependency]}
    features = make_features(QUERY, raw, reference, [vector(i) for i in range(3)],
                             {"a": vector(0), "b": vector(1), "c": vector(0, -1.0)}, metadata())
    return raw, reference, features


class SemanticFeatureTests(unittest.TestCase):
    def test_literal_facets_preserve_full_query_and_clause_order(self):
        self.assertEqual(expected_facets(QUERY), [QUERY,
            "Find shared requests for running transports",
            "publish successful values to active observers"])
        self.assertEqual(expected_facets("Find the request implementation"), [])
        self.assertEqual(expected_facets("save a file and load it"), [])
        with self.assertRaises(ValueError):
            expected_facets("")

    def test_full_raw_and_full_reference_membership_and_exact_signed_scores(self):
        raw, reference, features = fixture()
        facets, scores = validated_scores(reference, raw, features)
        self.assertEqual(features["schema"], SCHEMA)
        self.assertEqual(facets, expected_facets(QUERY))
        self.assertEqual(set(features["windows"]), {"a", "b", "c"})
        self.assertEqual(scores, {"a": (1_000_000, 0, 0), "b": (0, 1_000_000, 0),
                                  "c": (-1_000_000, 0, 0)})
        self.assertNotIn("scores", features)
        self.assertEqual(set(features), {"schema", "query", "facets", "query_prompt",
            "model_metadata", "model_fingerprint", "facet_vectors", "windows"})

    def test_document_wrapper_and_binding_keep_exact_bytes_and_provenance(self):
        source = 'first("x\u2028y")\r\nsecond()\r'
        original = row("a", source, source_origin="buffer", buffer_version=7,
                       buffer_hash="b" * 64)
        text = f'Path: src/a.py\nSymbol: run_a\nSource:\n{source}'
        self.assertEqual(document_text(original), text)
        self.assertEqual(row_binding(original), {
            "id": "a", "path": "src/a.py", "symbol": "run_a", "start_line": 1,
            "end_line": 2, "source_hash": digest(source), "document_sha256": digest(text),
            "source_origin": "buffer", "buffer_version": 7, "buffer_hash": "b" * 64})
        absent = deepcopy(original)
        absent.pop("buffer_version")
        self.assertNotEqual(row_binding(absent), row_binding(original))

    def test_capture_copies_vectors_metadata_and_nested_provenance(self):
        raw = [row("a", buffer_version={"invented": [1]})]
        reference = {"query": QUERY, "results": []}
        query_vectors, documents, model = [vector(i) for i in range(3)], {"a": vector()}, metadata()
        original = deepcopy((raw, reference, query_vectors, documents, model))
        features = make_features(QUERY, raw, reference, query_vectors, documents, model)
        self.assertEqual((raw, reference, query_vectors, documents, model), original)
        query_vectors[0][0] = 0.0
        documents["a"][0] = 0.0
        model["packages"]["invented-runtime"] = "changed"
        raw[0]["buffer_version"]["invented"].append(2)
        self.assertEqual(features["facet_vectors"][0][0], 1.0)
        self.assertEqual(features["windows"]["a"]["vector"][0], 1.0)
        self.assertEqual(features["model_metadata"]["packages"]["invented-runtime"], "0.0")
        self.assertEqual(features["windows"]["a"]["binding"]["buffer_version"], {"invented": [1]})

    def test_capture_accepts_array_provider_without_importing_numerical_runtime(self):
        from array import array

        raw = [row("a")]
        reference = {"query": QUERY, "results": []}
        features = make_features(QUERY, raw, reference,
            [array("f", vector(i)) for i in range(3)], {"a": array("f", vector())}, metadata())
        self.assertTrue(all(type(item) is float for item in features["windows"]["a"]["vector"]))
        self.assertEqual(validated_scores(reference, raw, features)[1]["a"], (1_000_000, 0, 0))

    def test_numpy_float32_provider_values_are_captured_exactly_but_booleans_are_rejected(self):
        import numpy as np

        raw = [row("a")]
        reference = {"query": QUERY, "results": []}
        facets = np.asarray([vector(i) for i in range(3)], dtype=np.float32)
        documents = {"a": np.asarray(vector(), dtype=np.float32)}
        features = make_features(QUERY, raw, reference, facets, documents, metadata())
        self.assertTrue(all(type(item) is float for item in features["facet_vectors"][0]))
        self.assertEqual(validated_scores(reference, raw, features)[1]["a"], (1_000_000, 0, 0))
        for invalid in (np.asarray(vector(), dtype=np.bool_),
                        np.asarray([0.1] * DIMENSIONS, dtype=np.float64)):
            with self.subTest(dtype=invalid.dtype), self.assertRaises(ValueError):
                make_features(QUERY, raw, reference, facets, {"a": invalid}, metadata())

    def test_clipped_reference_is_not_captured_but_its_raw_full_window_is(self):
        raw = [row("a"), row("b")]
        clipped = deepcopy(raw[0])
        clipped.update(source="first()\n", end_line=1, source_hash=digest("first()\n"), truncated=True)
        outside = row("clipped-dependency", source="first()\n", end_line=1, window_end_line=2,
                      truncated=True, content_hash="c" * 64, origin="dependency", related_to="a")
        reference = {"query": QUERY, "results": [clipped, outside]}
        features = make_features(QUERY, raw, reference, [vector(i) for i in range(3)],
                                 {"a": vector(), "b": vector(1)}, metadata())
        self.assertEqual(set(features["windows"]), {"a", "b"})
        self.assertEqual(validated_scores(reference, raw, features)[1]["a"], (1_000_000, 0, 0))
        with self.assertRaisesRegex(ValueError, "exact full source"):
            row_binding(clipped)

    def test_clipped_same_id_requires_exact_prefix_identity_and_provenance(self):
        raw, _, _ = fixture()
        clipped = deepcopy(raw[0])
        clipped.update(source="first()\n", end_line=1, source_hash=digest("first()\n"), truncated=True)
        changes = ({"source": "other()\n", "source_hash": digest("other()\n")},
                   {"symbol": "other"}, {"path": "other.py"}, {"window_end_line": 3},
                   {"content_hash": "d" * 64}, {"source_origin": "buffer"}, {"buffer_version": 1})
        for updates in changes:
            with self.subTest(updates=updates):
                reference = {"query": QUERY, "results": [{**clipped, **updates}]}
                with self.assertRaisesRegex(ValueError, "literal prefix"):
                    make_features(QUERY, raw, reference, [vector(i) for i in range(3)],
                                  {"a": vector(), "b": vector(1)}, metadata())

    def test_full_same_id_conflicting_bytes_or_provenance_rejects(self):
        raw, reference, features = fixture()
        for updates in ({"symbol": "changed"}, {"buffer_version": None}, {"source_origin": "buffer"},
                        {"source": "other()\nsecond()\n", "source_hash": digest("other()\nsecond()\n"),
                         "content_hash": digest("other()\nsecond()\n")}):
            with self.subTest(updates=updates):
                changed = deepcopy(reference)
                changed["results"][0].update(updates)
                with self.assertRaisesRegex(ValueError, "Conflicting full source"):
                    validated_scores(changed, raw, features)

    def test_invalid_physical_rows_hashes_flags_or_bounds_reject(self):
        invalid = ({"source_hash": "bad"}, {"content_hash": "0" * 64}, {"end_line": 1},
                   {"start_line": True}, {"end_line": 3, "window_end_line": 3},
                   {"window_end_line": 1}, {"truncated": 0}, {"truncated": True},
                   {"source": ""}, {"symbol": None})
        for updates in invalid:
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                row_binding({**row("a"), **updates})

    def test_raw_cap_unique_ids_and_full_rows_are_required(self):
        raw, reference, features = fixture()
        for rows in ([deepcopy(raw[0])] * 2, [row(str(i)) for i in range(51)],
                     [{**raw[0], "truncated": True}], tuple(raw)):
            with self.subTest(size=len(rows)), self.assertRaises(ValueError):
                validated_scores(reference, rows, features)
        duplicate = deepcopy(reference)
        duplicate["results"].append(deepcopy(duplicate["results"][0]))
        with self.assertRaisesRegex(ValueError, "Reference source IDs"):
            validated_scores(duplicate, raw, features)

    def test_missing_or_extra_full_window_capture_rejects(self):
        raw, reference, features = fixture()
        for identifier in ("a", "b", "c"):
            changed = deepcopy(features)
            changed["windows"].pop(identifier)
            with self.subTest(identifier=identifier), self.assertRaisesRegex(ValueError, "all and only"):
                validated_scores(reference, raw, changed)
        changed = deepcopy(features)
        changed["windows"]["unscored-tail"] = deepcopy(changed["windows"]["a"])
        with self.assertRaisesRegex(ValueError, "all and only"):
            validated_scores(reference, raw, changed)
        with self.assertRaisesRegex(ValueError, "all and only"):
            make_features(QUERY, raw, reference, [vector(i) for i in range(3)],
                          {"a": vector(), "b": vector(1)}, metadata())

    def test_feature_query_facets_prompt_schema_and_cached_scores_reject(self):
        raw, reference, features = fixture()
        changes = ({"schema": "other"}, {"query": QUERY + "?"},
                   {"facets": list(reversed(features["facets"]))}, {"query_prompt": "other"},
                   {"scores": {}}, {"facet_vectors": features["facet_vectors"][:2]})
        for updates in changes:
            with self.subTest(updates=list(updates)), self.assertRaises(ValueError):
                validated_scores(reference, raw, {**features, **updates})

    def test_invalid_capture_query_reference_or_facet_count_rejects(self):
        raw, reference, _ = fixture()
        vectors = {"a": vector(), "b": vector(), "c": vector()}
        for query, response, facets in ((QUERY + "?", reference, [vector()] * 3),
                ("Find one implementation", {"query": "Find one implementation", "results": []}, []),
                (QUERY, reference, [vector()] * 2), (QUERY, None, [vector()] * 3)):
            with self.subTest(query=query), self.assertRaises(ValueError):
                make_features(query, raw, response, facets, vectors, metadata())

    def test_changed_actual_query_or_source_rejects_preexisting_capture(self):
        raw, reference, features = fixture()
        changed = deepcopy(reference)
        changed["query"] = QUERY + "?"
        with self.assertRaises(ValueError):
            validated_scores(changed, raw, features)
        changed = deepcopy(raw)
        changed[1]["source"] = "other()\nsecond()\n"
        changed[1].update(source_hash=digest(changed[1]["source"]),
                          content_hash=digest(changed[1]["source"]))
        with self.assertRaisesRegex(ValueError, "binding differs"):
            validated_scores(reference, changed, features)

    def test_model_pins_metadata_fingerprint_and_finite_json_are_required(self):
        raw, reference, features = fixture()
        changes = ({"id": "other"}, {"revision": "other"}, {"dimensions": 1024.0},
                   {"query_prompt": "other"}, {"normalization": "none"},
                   {"packages": {"runtime": math.nan}}, {"non-json": (1, 2)})
        for updates in changes:
            changed = deepcopy(features)
            changed["model_metadata"].update(updates)
            with self.subTest(updates=list(updates)), self.assertRaises(ValueError):
                validated_scores(reference, raw, changed)
        changed = deepcopy(features)
        changed["model_metadata"]["packages"]["invented-runtime"] = "changed"
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            validated_scores(reference, raw, changed)
        encoded = json.dumps(features["model_metadata"], sort_keys=True, separators=(",", ":"))
        self.assertEqual(features["model_fingerprint"], digest(encoded))

    def test_provenance_field_presence_and_value_types_are_bound(self):
        raw, reference, features = fixture()
        raw[1]["buffer_version"] = 1
        features["windows"]["b"]["binding"]["buffer_version"] = 1
        self.assertIn("b", validated_scores(reference, raw, features)[1])
        for replacement in (True, 1.0, None):
            changed = deepcopy(features)
            changed["windows"]["b"]["binding"]["buffer_version"] = replacement
            with self.subTest(replacement=replacement), self.assertRaisesRegex(ValueError, "binding differs"):
                validated_scores(reference, raw, changed)
        changed = deepcopy(features)
        changed["windows"]["b"]["binding"].pop("buffer_version")
        with self.assertRaisesRegex(ValueError, "binding differs"):
            validated_scores(reference, raw, changed)

    def test_boolean_integer_nonfinite_nonfloat32_zero_and_bad_dimensions_reject(self):
        raw, reference, features = fixture()
        for bad in (True, 1, math.nan, math.inf, 0.1, 1e40):
            changed = deepcopy(features)
            changed["windows"]["a"]["vector"][0] = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validated_scores(reference, raw, changed)
        for bad_vector in ([0.0] * DIMENSIONS, vector()[:-1], vector() + [0.0], tuple(vector())):
            changed = deepcopy(features)
            changed["facet_vectors"][0] = bad_vector
            with self.subTest(size=len(bad_vector)), self.assertRaises(ValueError):
                validated_scores(reference, raw, changed)

    def test_invalid_capture_vectors_reject_without_lossy_conversion(self):
        raw = [row("a")]
        reference = {"query": QUERY, "results": []}
        for bad in (True, 1, math.nan, math.inf, 0.1, 1e40):
            invalid = vector()
            invalid[0] = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                make_features(QUERY, raw, reference, [vector(i) for i in range(3)],
                              {"a": invalid}, metadata())

    def test_scores_use_standard_norm_product_rounding_and_one_computation_per_pair(self):
        raw, reference, features = fixture()
        value = struct.unpack("<f", struct.pack("<f", 0.75))[0]
        features["windows"]["a"]["vector"][0:2] = [value, 1.0]
        # The norm is 1.25, so the first two similarities are exactly .6 and .8.
        import semantic_search.semantic_features as module

        with patch.object(module, "_cosine_ppm", wraps=module._cosine_ppm) as compute:
            scores = validated_scores(reference, raw, features)[1]
        self.assertEqual(scores["a"], (600_000, 800_000, 0))
        self.assertEqual(compute.call_count, 3 * 3)
        with patch.object(module, "_cosine_ppm", side_effect=AssertionError("capture scored")):
            fixture()

    def test_original_scalar_fields_are_not_feature_bindings_or_rewritten(self):
        raw, reference, features = fixture()
        original = deepcopy((raw, reference, features))
        self.assertNotIn("retrieval_score", features["windows"]["a"]["binding"])
        validated_scores(reference, raw, features, definition_keys={"a": ["invented", "key"]})
        self.assertEqual((raw, reference, features), original)

    def test_import_and_validation_work_when_model_index_and_numpy_imports_are_denied(self):
        script = r'''
import importlib.abc, sys
class Deny(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = ("numpy", "mlx", "mlx_embeddings", "semantic_search.embeddings",
                   "semantic_search.index", "semantic_search.store", "semantic_search.backend")
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            raise AssertionError("Forbidden import: " + fullname)
sys.meta_path.insert(0, Deny())
from semantic_search.semantic_features import (
    expected_facets, make_features, validated_scores, MODEL_ID, MODEL_REVISION, QUERY_PROMPT
)
import hashlib
query = "Find shared requests for running transports, and publish successful values to active observers"
assert len(expected_facets(query)) == 3
text = "return result\n"
source_hash = hashlib.sha256(text.encode()).hexdigest()
raw = [{"id": "one", "path": "one.py", "symbol": "one", "source": text,
        "start_line": 1, "end_line": 1, "window_end_line": 1, "truncated": False,
        "source_hash": source_hash, "content_hash": source_hash}]
reference = {"query": query, "results": []}
vector = [1.0] + [0.0] * 1023
metadata = {"id": MODEL_ID, "revision": MODEL_REVISION, "dimensions": 1024,
            "query_prompt": QUERY_PROMPT, "normalization": "L2, float32"}
features = make_features(query, raw, reference, [vector] * 3, {"one": vector}, metadata)
assert validated_scores(reference, raw, features)[1]["one"] == (1000000,) * 3
print("stdlib-only feature boundary")
'''
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        completed = subprocess.run([sys.executable, "-c", script], env=env, text=True,
                                   capture_output=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("stdlib-only feature boundary", completed.stdout)


if __name__ == "__main__":
    unittest.main()
