"""Exact validator/provider differentials using invented inputs, never a model."""
from array import array
from copy import deepcopy
from fractions import Fraction
import struct
from unittest.mock import patch

import numpy as np
import pytest

from experiments.dependency_context import fast_provider as fast
from experiments.dependency_context import provider_profile as profile
from semantic_search import semantic_features as sf
from semantic_search import semantic_provider as sp
from test_dependency_provider_profile import invented_record, vector


class ArraySubclass(np.ndarray):
    pass


class ListSubclass(list):
    pass


class FloatSubclass(float):
    pass


class BrokenIterable:
    def __iter__(self):
        raise RuntimeError("invented iterable failure")


def cases():
    return [
        ("float32", lambda: vector()),
        ("signed_zero", lambda: np.array([0.5, -0.0] + [0.0]*1022, dtype=np.float32)),
        ("subnormal", lambda: np.full(1024, np.nextafter(np.float32(0), np.float32(1)), dtype=np.float32)),
        ("maximum", lambda: np.full(1024, np.finfo(np.float32).max, dtype=np.float32)),
        ("negative_stride", lambda: vector()[::-1]),
        ("strided", lambda: np.ones(2048, dtype=np.float32)[::2]),
        ("big_endian", lambda: np.ones(1024, dtype=">f4")),
        ("float64_exact", lambda: np.ones(1024, dtype=np.float64)),
        ("float64_inexact", lambda: np.full(1024, 0.1, dtype=np.float64)),
        ("float16", lambda: np.ones(1024, dtype=np.float16)),
        ("object_float", lambda: np.array([0.5]*1024, dtype=object)),
        ("object_fraction", lambda: np.array([Fraction(1, 2)]*1024, dtype=object)),
        ("int", lambda: np.ones(1024, dtype=np.int32)),
        ("complex", lambda: np.ones(1024, dtype=np.complex64)),
        ("bool", lambda: np.ones(1024, dtype=np.bool_)),
        ("nan", lambda: np.full(1024, np.nan, dtype=np.float32)),
        ("infinity", lambda: np.full(1024, np.inf, dtype=np.float32)),
        ("zero", lambda: np.zeros(1024, dtype=np.float32)),
        ("wrong_length", lambda: np.ones(1023, dtype=np.float32)),
        ("wrong_rank", lambda: np.ones((1024, 1), dtype=np.float32)),
        ("scalar", lambda: np.float32(1)),
        ("array_subclass", lambda: np.ones(1024, dtype=np.float32).view(ArraySubclass)),
        ("list", lambda: [0.5]*1024),
        ("list_subclass", lambda: ListSubclass([0.5]*1024)),
        ("float_subclass", lambda: [FloatSubclass(0.5)]*1024),
        ("float32_list", lambda: [np.float32(0.5)]*1024),
        ("mixed", lambda: [0.5]*1023 + [1]),
        ("bool_list", lambda: [True]*1024),
        ("tuple", lambda: tuple([0.5]*1024)),
        ("generator", lambda: (0.5 for _ in range(1024))),
        ("fraction", lambda: [Fraction(1, 2)]*1024),
        ("fraction_inexact", lambda: [Fraction(1, 3)]*1024),
        ("overflow", lambda: [1e100]*1024),
        ("array_f", lambda: array("f", [0.5]*1024)),
        ("broken", BrokenIterable),
        ("none", lambda: None),
        ("string", lambda: "0"*1024),
        ("bytes", lambda: b"0"*1024),
        ("dict", lambda: {index: 0.5 for index in range(1024)}),
    ]


def outcome(function, value, captured):
    try:
        result = function(value, captured=captured)
        return ("ok", [type(item) for item in result], struct.pack("<1024f", *result))
    except BaseException as error:
        cause = None if error.__cause__ is None else (type(error.__cause__), str(error.__cause__))
        return ("error", type(error), str(error), cause)


@pytest.mark.parametrize("name,build", cases(), ids=[item[0] for item in cases()])
@pytest.mark.parametrize("captured", [False, True, 0, np.bool_(False)])
def test_validator_exact_result_bytes_types_and_exception_contract(name, build, captured):
    assert outcome(fast.fast_vector, build(), captured) == outcome(sf._vector, build(), captured)


def test_only_valid_plain_native_float32_arrays_skip_original_validator():
    with patch.object(fast, "ORIGINAL_VECTOR", side_effect=AssertionError("slow fallback")):
        result = fast.fast_vector(vector(), captured=False)
        assert struct.pack("<1024f", *result) == vector().tobytes()
        for value, captured in ((np.zeros(1024, dtype=np.float32), False),
                (vector().astype(np.float64), False), (vector().view(ArraySubclass), False),
                (vector(), True), (vector().tolist(), False), (vector(), 0)):
            with pytest.raises(AssertionError, match="slow fallback"):
                fast.fast_vector(value, captured=captured)


def test_private_snapshot_is_not_changed_between_predicate_and_output():
    value = vector().copy()
    before = value.tobytes()
    original = np.isfinite

    def mutate_caller(snapshot):
        assert snapshot is not value and not np.shares_memory(snapshot, value)
        value[0] = np.nan
        return original(snapshot)

    with patch.object(fast.np, "isfinite", side_effect=mutate_caller):
        result = fast.fast_vector(value, captured=False)
    assert struct.pack("<1024f", *result) == before
    assert np.isnan(value[0])


def test_clones_have_identical_code_defaults_and_only_declared_global_overrides():
    factory, features = fast.clone_functions()
    for original, clone, names in ((sf.make_features, features, {"_vector"}),
            (sp.make_live_feature_provider, factory, {"_vector", "make_features"})):
        assert clone.__code__ is original.__code__
        assert clone.__defaults__ == original.__defaults__
        assert clone.__kwdefaults__ == original.__kwdefaults__
        assert clone.__globals__ is not original.__globals__
        assert clone.__globals__.keys() == original.__globals__.keys()
        assert {name for name in original.__globals__ if original.__globals__[name] is not clone.__globals__[name]} == names
    again, again_features = fast.clone_functions()
    assert again.__globals__ is not factory.__globals__
    assert again_features.__globals__ is not features.__globals__
    assert sf._vector is sp._vector is fast.ORIGINAL_VECTOR
    assert sf.make_features is sp.make_features is fast.ORIGINAL_FEATURES


def run_provider(factory, fault=None, *, mode="dense", windows=5):
    prepared = profile.prepare_record(invented_record(mode, windows))
    record = prepared["record"]
    original = record["candidate_capture"]
    receipt = original["provider_receipts"][0]
    index = profile.ReplayIndex(record["resident_reads"][0], prepared["vectors"])
    embedder = profile.ReplayEmbedder(prepared["metadata"], original["features"]["facets"], prepared["clause_vectors"],
        receipt["extra_query_forward_events"])
    confirmations, captures = [], []
    context = {**deepcopy(original["request_binding"]), "query_vector": prepared["query_vector"],
        "query_forward_events": deepcopy(receipt["original_query_forward_events"]), "index": index,
        "embedder": embedder, "source_provenance": lambda path: prepared["provenances"][path]}

    def confirm():
        confirmations.append(True)
        if fault == "source_drift" and len(confirmations) == 2:
            raise RuntimeError("invented source changed")

    def cancel():
        if fault == "cancel_after_clause" and embedder.calls:
            raise KeyboardInterrupt("invented cancellation")

    def capture(value):
        captures.append(deepcopy(value))
        if fault == "capture_error":
            raise RuntimeError("invented capture failure")

    context["confirm_snapshot"] = confirm
    if fault == "bad_provenance":
        prepared["provenances"]["src/invented_0.py"] = {}
    elif fault == "metadata_drift":
        embedder._metadata["batch_size"] = 3
    elif fault == "missing_vector":
        del prepared["vectors"]["invented-0"]
    elif fault == "invalid_vector":
        prepared["vectors"]["invented-0"] = np.zeros(1024, dtype=np.float32)
    elif fault == "duplicate":
        prepared["raw"].append(deepcopy(prepared["raw"][0]))
    elif fault == "query_drift":
        context["query_vector"] = vector(100)
        # A self-consistent new original vector is legal; mutate it on validation.
        def confirm_and_change():
            confirmations.append(True)
            context["query_vector"] = vector(101)
        context["confirm_snapshot"] = confirm_and_change
    elif fault in ("read_error", "clause_error"):
        def fail(*args, **kwargs):
            raise RuntimeError("invented boundary failure")
        if fault == "read_error":
            index.read_semantic_vectors = fail
        else:
            embedder.embed_queries = fail
    try:
        features = factory(context, capture=capture)(prepared["reference"], prepared["raw"], cancel_check=cancel)
        outcome_value = ("ok", profile.canonical(features))
    except BaseException as error:
        outcome_value = ("error", type(error), str(error),
            None if error.__cause__ is None else (type(error.__cause__), str(error.__cause__)))
    return outcome_value, profile.canonical(captures), index.calls, embedder.calls, len(confirmations)


@pytest.mark.parametrize("mode", ["dense", "hybrid"])
def test_full_provider_50_windows_exact_features_receipts_bytes_and_calls(mode):
    assert run_provider(fast.make_fast_feature_provider, mode=mode, windows=50) == run_provider(sp.make_live_feature_provider,
        mode=mode, windows=50)


@pytest.mark.parametrize("fault", ["read_error", "clause_error", "cancel_after_clause", "source_drift", "bad_provenance",
    "metadata_drift", "missing_vector", "invalid_vector", "duplicate", "query_drift", "capture_error"])
def test_full_provider_errors_cancellation_and_partial_receipts_are_unchanged(fault):
    old = run_provider(sp.make_live_feature_provider, fault)
    new = run_provider(fast.make_fast_feature_provider, fault)
    assert old[0][0] == "error"
    assert new == old


@pytest.mark.parametrize("context,capture", [(None, None), ([], None), ({}, 1)])
def test_factory_argument_errors_remain_original(context, capture):
    def attempt(factory):
        try:
            factory(context, capture=capture)
        except BaseException as error:
            return type(error), str(error)
    assert attempt(fast.make_fast_feature_provider) == attempt(sp.make_live_feature_provider)
