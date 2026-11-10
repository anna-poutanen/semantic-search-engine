"""Research-only original/fast provider comparison inside unchanged native search.

The frozen role harness still performs retrieval, coverage, semantic-v1 and the
terminal role guard. This outer seam chooses only the provider factory. Factory
and provider component timers run inside the full ``backend.search`` timer;
snapshots, reporting and iterator draining remain outside that timer.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import importlib
import math
from pathlib import Path
import sys
import time
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "provider_native_integration_v1"
ARMS = ("legacy", "coverage_control", "original_role", "fast_role")
ROLE_ARMS = {"original_role": "original", "fast_role": "fast"}
FIELDS = {"schema", "arm", "implementation", "factory_attempts", "factory_completed", "factory_failed",
          "provider_attempts", "provider_completed", "provider_failed", "factory_elapsed_s", "provider_elapsed_s"}


def require(value, message):
    if not value:
        raise ValueError(message)


def source_authority(root=ROOT):
    """Validate actual loaded helpers; never silently use another editable tree."""
    root = Path(root).resolve()
    require(Path(__file__).resolve() == root / "experiments/provider_native/integration.py",
            "Executing provider integration escapes the bound checkout")
    script_names = {path.stem for path in (root / "scripts").glob("*.py")}
    for name, module in tuple(sys.modules.items()):
        if name == "semantic_search" or name.startswith("semantic_search."):
            base = root / "src/semantic_search"
        elif name == "experiments" or name.startswith("experiments."):
            base = root / "experiments"
        elif name in script_names or name.startswith("scripts."):
            base = root / "scripts"
        else:
            continue
        location = getattr(module, "__file__", None)
        require(location is not None and Path(location).resolve().is_relative_to(base),
                "Executing provider integration helper escapes the bound checkout: " + name)


def modules():
    """Lazy imports keep collection independent of the full-suite import order."""
    native = importlib.import_module("benchmark_role_native")
    fast = importlib.import_module("experiments.dependency_context.fast_provider")
    source_authority()
    require(Path(native.__file__).resolve() == ROOT / "scripts/benchmark_role_native.py"
            and Path(fast.__file__).resolve() == ROOT / "experiments/dependency_context/fast_provider.py",
            "Executing provider factory/helper source differs")
    return native, fast


def mapped_arm(arm):
    require(arm in ARMS, "Unknown provider native arm")
    return "candidate" if arm in ROLE_ARMS else arm


def new_metrics(arm):
    mapped_arm(arm)
    return {"schema": SCHEMA, "arm": arm, "implementation": ROLE_ARMS.get(arm),
            "factory_attempts": 0, "factory_completed": 0, "factory_failed": 0,
            "provider_attempts": 0, "provider_completed": 0, "provider_failed": 0,
            "factory_elapsed_s": 0.0, "provider_elapsed_s": 0.0}


def timed_factory(factory, metrics):
    """Retain original errors and arguments; record no payloads inside timing."""
    def construct(*args, **kwargs):
        metrics["factory_attempts"] += 1
        started = time.perf_counter()
        try:
            provider = factory(*args, **kwargs)
            metrics["factory_completed"] += 1

            def provide(*args, **kwargs):
                metrics["provider_attempts"] += 1
                began = time.perf_counter()
                try:
                    value = provider(*args, **kwargs)
                    metrics["provider_completed"] += 1
                    return value
                except BaseException:
                    metrics["provider_failed"] += 1
                    raise
                finally:
                    metrics["provider_elapsed_s"] += time.perf_counter() - began

            return provide
        except BaseException:
            metrics["factory_failed"] += 1
            raise
        finally:
            metrics["factory_elapsed_s"] += time.perf_counter() - started

    return construct


@contextmanager
def _capture(arm, backend, *, native, fast, original_capture, metrics):
    condition = native.CANDIDATE if arm in ROLE_ARMS else mapped_arm(arm)
    if arm in ROLE_ARMS:
        original_factory = native.provider_module.make_live_feature_provider
        require(fast.ORIGINAL_FACTORY is original_factory
                and fast.ORIGINAL_FEATURES is native.provider_module.make_features,
                "Original provider aliases changed before native capture")
        factory = original_factory if arm == "original_role" else fast.make_fast_feature_provider
        # Enter before old capture snapshots its actual_factory alias. The old
        # live adapter constructs this factory during backend.search, including
        # on ineligible requests that never invoke the returned provider.
        with patch.object(native.provider_module, "make_live_feature_provider", timed_factory(factory, metrics)):
            with original_capture(condition, backend) as state:
                state["provider_integration"] = metrics
                yield state
    else:
        with original_capture(condition, backend) as state:
            state["provider_integration"] = metrics
            yield state


@contextmanager
def capture(arm, backend=None):
    """Same live capture as the frozen role harness, with an explicit arm seam."""
    native, fast = modules()
    metrics = new_metrics(arm)
    with _capture(arm, backend, native=native, fast=fast, original_capture=native.capture, metrics=metrics) as state:
        yield state


def checked_metrics(metrics, row):
    """Validate actual successful work after search/reporting has completed."""
    require(type(metrics) is dict and set(metrics) == FIELDS and metrics["schema"] == SCHEMA,
            "Provider integration schema differs")
    arm = metrics["arm"]
    mapped_arm(arm)
    require(metrics["implementation"] == ROLE_ARMS.get(arm), "Provider implementation identity differs")
    for phase in ("factory", "provider"):
        attempted, completed, failed = (metrics[phase + "_" + key] for key in ("attempts", "completed", "failed"))
        elapsed = metrics[phase + "_elapsed_s"]
        require(all(type(value) is int and value >= 0 for value in (attempted, completed, failed))
                and attempted == completed + failed and failed == 0,
                "Successful provider integration call counts differ")
        require(type(elapsed) is float and math.isfinite(elapsed) and elapsed >= 0
                and ((attempted == 0 and elapsed == 0.0) or (attempted > 0 and elapsed > 0)),
                "Provider integration duration differs")
    if arm in ROLE_ARMS:
        event = row["candidate_capture"]
        require(metrics["factory_attempts"] == metrics["factory_completed"] == 1
                and metrics["provider_attempts"] == metrics["provider_completed"] == event["feature_calls"]
                and event["feature_calls"] in (0, 1), "Actual role provider factory/call counts differ")
        phase = event["phase_timings"]
        require(metrics["provider_elapsed_s"] <= phase["live_feature_provider_s"] + 1e-9
                and sum(phase.values()) + metrics["factory_elapsed_s"] <= row["elapsed_backend_s"] + 1e-9,
                "Provider factory/call phases exceed actual complete search timer")
    else:
        require(row["candidate_capture"] is None
                and metrics["factory_attempts"] == metrics["provider_attempts"] == 0,
                "Control executed provider integration work")
    require(metrics["factory_elapsed_s"] + metrics["provider_elapsed_s"] <= row["elapsed_backend_s"] + 1e-9,
            "Provider component timers exceed actual complete search")
    return deepcopy(metrics)


def receipt(query, repetition, retrieval, arm, response, captured, elapsed, vector):
    """Drain/copy the exact frozen receipt after timing, then add arm metadata."""
    native, _ = modules()
    old_arm = mapped_arm(arm)
    condition = native.CANDIDATE if arm in ROLE_ARMS else old_arm
    value = native.receipt(query, repetition, retrieval, old_arm, condition, response, captured, elapsed, vector)
    value["arm"] = arm
    value["provider_integration"] = checked_metrics(captured["provider_integration"], value)
    return value


def measure(backend, embedder, query, repetition, arm, retrieval, *, on_failure=None):
    """Use the original whole-search timer and failure ledger without new work.

    Both role arms retain the old ``condition`` so the independent old auditor
    can validate each projected arm with identical controls. The new ``arm`` and
    provider timing receipt are attached only after the old timer and reporting.
    No selector, skip certificate, feature cache or new retrieval is installed.
    """
    native, fast = modules()
    old_arm = mapped_arm(arm)
    original_capture = native.capture
    metrics = new_metrics(arm)

    @contextmanager
    def selected_capture(condition, actual_backend=None):
        expected = native.CANDIDATE if arm in ROLE_ARMS else old_arm
        require(condition == expected, "Frozen native measurement condition differs")
        with _capture(arm, actual_backend, native=native, fast=fast,
                      original_capture=original_capture, metrics=metrics) as state:
            yield state

    def failed(value):
        if on_failure is not None:
            on_failure({**value, "arm": arm, "provider_integration": deepcopy(metrics)})

    # Only harness capture is replaced; the old measure implementation owns
    # the complete backend.search timer and all original receipt validation.
    with patch.object(native, "capture", selected_capture):
        value = native.measure(backend, embedder, query, repetition, old_arm, retrieval, on_failure=failed)
    value["arm"] = arm
    try:
        value["provider_integration"] = checked_metrics(metrics, value)
    except BaseException as error:
        if on_failure is not None:
            try:
                on_failure({"query_id": query["id"], "query": query["query"], "repetition": repetition,
                    "arm": arm, "retrieval": retrieval, "error_type": type(error).__name__,
                    "provider_integration": deepcopy(metrics), "reporting_error_after_search": True,
                    "completed_native_receipt": value})
            except BaseException as recording_error:
                raise error from recording_error
        raise
    return value
