#!/usr/bin/env python3
"""Fail closed when wall/performance clocks disagree during a timing study."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import time

MAX_DRIFT_NS = 2_000_000_000
POLICY = {
    "version": 1,
    "wall_clock": "time.time_ns",
    "performance_clock": "time.perf_counter_ns",
    "max_absolute_drift_ns": MAX_DRIFT_NS,
    "checks": ["cumulative_since_pipeline_start", "within_each_measured_query", "after_each_setup_and_between_queries"],
    "failure_policy": "Abort and preserve the failing clock/query receipt; no filtering, retry or selection of an interrupted run",
    "sampling": "Wall then performance clock, integer nanoseconds; UTC derived from each recorded wall sample",
}


def require(value, message):
    if not value:
        raise ValueError(message)


def utc_from_ns(wall_ns):
    seconds, nanos = divmod(wall_ns, 1_000_000_000)
    return datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=nanos // 1000).isoformat()


def validate_sample(sample):
    require(isinstance(sample, dict) and set(sample) == {"wall_time_ns", "perf_counter_ns", "utc"},
            "Exact wall/performance/UTC sample required")
    require(type(sample["wall_time_ns"]) is type(sample["perf_counter_ns"]) is int
            and sample["perf_counter_ns"] >= 0 and isinstance(sample["utc"], str)
            and sample["utc"] == utc_from_ns(sample["wall_time_ns"]), "Strict integer nanosecond clocks and exact recorded UTC required")
    return sample


def interval(before, after):
    """Keep signed raw deltas; perf rollback or >2s absolute drift invalidates."""
    validate_sample(before)
    validate_sample(after)
    wall = after["wall_time_ns"] - before["wall_time_ns"]
    perf = after["perf_counter_ns"] - before["perf_counter_ns"]
    drift = wall - perf
    return {"wall_elapsed_ns": wall, "perf_elapsed_ns": perf, "drift_ns": drift,
            "absolute_drift_ns": abs(drift), "max_drift_ns": MAX_DRIFT_NS,
            "consistent": perf >= 0 and abs(drift) <= MAX_DRIFT_NS}


class ClockIntegrityError(RuntimeError):
    def __init__(self, receipt):
        self.receipt = deepcopy(receipt)
        super().__init__("Timing clock integrity failed at " + receipt["label"]
                         + "; wall/performance drift exceeded 2 seconds or performance clock rolled back")


class ClockIntegrity:
    """One persistent baseline catches suspension within or between queries.

    Before a query, call check and retain receipt['sample']. After the measured
    work, call check again with since=start_sample. Also check after setup. A
    failed check never resets the baseline or filters/retries the observation.
    """

    def __init__(self, *, wall_clock=None, perf_clock=None):
        self._wall_clock = time.time_ns if wall_clock is None else wall_clock
        self._perf_clock = time.perf_counter_ns if perf_clock is None else perf_clock
        self._baseline = self._sample()

    @property
    def baseline(self):
        return deepcopy(self._baseline)

    def _sample(self):
        wall, perf = self._wall_clock(), self._perf_clock()
        require(type(wall) is type(perf) is int, "Clock APIs must return integer nanoseconds")
        return validate_sample({"wall_time_ns": wall, "perf_counter_ns": perf, "utc": utc_from_ns(wall)})

    def check(self, label, *, since=None):
        require(isinstance(label, str) and label and len(label) <= 512, "Bounded clock receipt label required")
        sample = self._sample()
        cumulative = interval(self._baseline, sample)
        within = interval(since, sample) if since is not None else None
        receipt = {"version": 1, "label": label, "baseline": self.baseline, "sample": sample,
                   "cumulative": cumulative, "interval": within, "max_drift_ns": MAX_DRIFT_NS,
                   "consistent": cumulative["consistent"] and (within is None or within["consistent"])}
        if not receipt["consistent"]:
            raise ClockIntegrityError(receipt)
        return receipt
