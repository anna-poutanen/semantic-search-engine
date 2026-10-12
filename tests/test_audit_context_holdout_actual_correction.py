"""Invented CPU mutation checks for the post-run sample-envelope correction."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path
import statistics
import sys
from unittest.mock import patch

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("context_holdout_sample_correction",
    SCRIPTS / "audit_context_holdout_actual_correction.py")
correction = importlib.util.module_from_spec(spec)
spec.loader.exec_module(correction)


def samples():
    # Deliberately unsorted: sorting preserves numerical metrics but loses order.
    return [float((number * 7) % 45 + 1) for number in range(45)]


def expected_distribution():
    values = samples()
    return {"count": 45, "median_s": 23., "p95_s": 42.8,
            "min_s": 1., "max_s": 45., "samples_s": values}


def test_correction_keeps_numeric_definitions_and_exact_order_without_aliasing():
    values = samples()
    result = correction.timing_summary_with_samples(values)
    assert result == expected_distribution()
    assert result["median_s"] == statistics.median(values)
    assert result["samples_s"] != sorted(values)
    values[0] = -100.
    assert result["samples_s"] == samples()


@pytest.mark.parametrize("damage", ["missing", "extra", "bool", "integer", "nan", "infinity", "negative"])
def test_correction_requires_all45_original_finite_float_samples(damage):
    values = samples()
    if damage == "missing":
        values.pop()
    elif damage == "extra":
        values.append(46.)
    else:
        values[0] = {"bool": True, "integer": 1, "nan": float("nan"),
                     "infinity": float("inf"), "negative": -1.}[damage]
    with pytest.raises(ValueError):
        correction.timing_summary_with_samples(values)


@pytest.mark.parametrize("damage", ["missing-sample", "extra-sample", "reorder", "changed-sample",
                                    "bool-sample", "count", "median", "p95", "min", "max", "missing-key", "extra-key"])
def test_original_typed_equality_rejects_raw_inventory_and_summary_damage(damage):
    reported = deepcopy(expected_distribution())
    if damage == "missing-sample":
        reported["samples_s"].pop()
    elif damage == "extra-sample":
        reported["samples_s"].append(46.)
    elif damage == "reorder":
        reported["samples_s"] = sorted(reported["samples_s"])
    elif damage == "changed-sample":
        reported["samples_s"][0] += .001
    elif damage == "bool-sample":
        reported["samples_s"][0] = True
    elif damage == "missing-key":
        reported.pop("samples_s")
    elif damage == "extra-key":
        reported["invented"] = 1
    else:
        key = {"median": "median_s", "p95": "p95_s", "min": "min_s", "max": "max_s"}.get(damage, damage)
        reported[key] += 1

    def original_audit(study):
        independent = correction.original.common.timing_summary(samples())
        correction.original.require(correction.original.common.typed_equal(independent, reported),
                                    "Independent warm HTTP distributions differ")
        return {"latency": independent}

    with patch.object(correction.original, "audit", original_audit), pytest.raises(ValueError):
        correction.audit(Path("invented-study"))


def test_correction_delegates_to_original_and_restores_independent_helper():
    numerical = correction.original.common.timing_summary
    def original_audit(study):
        assert study == Path("invented-study")
        independent = correction.original.common.timing_summary(samples())
        correction.original.require(correction.original.common.typed_equal(independent, expected_distribution()), "mismatch")
        return {"inventory_complete": True, "latency": independent}
    with patch.object(correction.original, "audit", original_audit):
        result = correction.audit(Path("invented-study"))
    assert result == {"inventory_complete": True, "latency": expected_distribution()}
    assert correction.original.common.timing_summary is numerical


def test_original_guard_failure_propagates_and_restores_independent_helper():
    numerical = correction.original.common.timing_summary
    with patch.object(correction.original, "audit", side_effect=ValueError("Bound source drift")), \
            pytest.raises(ValueError, match="Bound source drift"):
        correction.audit(Path("invented-study"))
    assert correction.original.common.timing_summary is numerical


def test_correction_rejects_changed_original_auditor_before_any_audit():
    with patch.object(correction, "ORIGINAL_SHA256", "0" * 64), \
            patch.object(correction.original, "audit") as original, \
            pytest.raises(ValueError, match="original holdout auditor changed"):
        correction.audit(Path("invented-study"))
    original.assert_not_called()
