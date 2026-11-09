"""Research-only exact float32 validation; the original provider code is reused.

Only plain native NumPy float32 arrays take the fast path. Every other value,
including malformed arrays and captured payloads, uses the unchanged validator.
No production globals, source/provenance checks or inference boundaries change.
"""
from __future__ import annotations

from types import FunctionType

import numpy as np

from semantic_search import semantic_features as _features
from semantic_search import semantic_provider as _provider


ORIGINAL_VECTOR = _features._vector
ORIGINAL_FEATURES = _features.make_features
ORIGINAL_FACTORY = _provider.make_live_feature_provider


def fast_vector(value, *, captured):
    """Return identical Python floats or defer exact errors to the old boundary.

Every component of a finite float32 array is already exact float32. For 1024
components, its sum of squares in Python float is positive iff any component is
nonzero: even the smallest float32 subnormal squared exceeds float64 underflow,
and 1024 maximum float32 squares remain below float64 overflow. ``tolist`` also
preserves signed zero. Other representations receive no changed validation.
"""
    if (captured is False and type(value) is np.ndarray and value.dtype == np.dtype(np.float32)
            and value.ndim == 1 and value.shape == (_features.DIMENSIONS,)):
        # The original first snapshots its sequence. Validate a private copy so
        # a caller mutation cannot change components between predicates/output.
        snapshot = value.copy()
        if bool(np.isfinite(snapshot).all()) and bool(np.any(snapshot != 0)):
            return snapshot.tolist()
    return ORIGINAL_VECTOR(value, captured=captured)


def _clone(function, replacements):
    namespace = dict(function.__globals__)
    namespace.update(replacements)
    clone = FunctionType(function.__code__, namespace, function.__name__, function.__defaults__, function.__closure__)
    clone.__kwdefaults__ = None if function.__kwdefaults__ is None else dict(function.__kwdefaults__)
    clone.__annotations__ = dict(function.__annotations__)
    clone.__dict__.update(function.__dict__)
    clone.__doc__ = function.__doc__
    clone.__qualname__ = function.__qualname__
    clone.__module__ = function.__module__
    return clone


def clone_functions():
    """Fresh copied namespaces, identical original bytecode, two explicit seams.

    The feature maker changes only ``_vector``. The provider changes only its
    ``_vector`` alias and ``make_features`` alias. All closures created by those
    original code objects inherit these private namespaces, without monkeypatch.
    Exposed for source-contract tests and isolated CPU stage instrumentation.
    """
    features = _clone(ORIGINAL_FEATURES, {"_vector": fast_vector})
    factory = _clone(ORIGINAL_FACTORY, {"_vector": fast_vector, "make_features": features})
    return factory, features


def make_fast_feature_provider(request_context, *, capture=None):
    """Explicit opt-in research factory; all other provider behavior is original."""
    factory, _ = clone_functions()
    return factory(request_context, capture=capture)
