"""Detect suspend and clock steps without retrying or filtering timed samples."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("optimization_dual_clock",
    Path(__file__).parents[1] / "scripts/qwen_optimization_clock.py")
clock = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(clock)


class Clocks:
    def __init__(self, wall=10_000_000_000, perf=1_000_000_000):
        self.wall, self.perf = wall, perf

    def advance(self, *, wall, perf):
        self.wall += wall
        self.perf += perf

    def guard(self):
        return clock.ClockIntegrity(wall_clock=lambda: self.wall, perf_clock=lambda: self.perf)


def test_normal_query_setup_and_between_query_receipts_preserve_raw_ns_and_utc():
    source = Clocks()
    guard = source.guard()
    source.advance(wall=3_000_000_000, perf=3_000_000_000)
    setup = guard.check("setup")
    first = guard.check("query1/start")
    source.advance(wall=123_456_789, perf=123_456_788)
    last = guard.check("query1/end", since=first["sample"])
    assert setup["cumulative"]["wall_elapsed_ns"] == 3_000_000_000
    assert last["interval"]["drift_ns"] == 1 and last["consistent"] is True
    assert last["sample"]["utc"] == "1970-01-01T00:00:13.123456+00:00"
    json.dumps({"policy": clock.POLICY, "baseline": guard.baseline, "clock_start": first, "clock_end": last}, allow_nan=False)


def test_suspend_within_query_stores_failure_with_performance_pause_and_full_wall_delay():
    source = Clocks()
    guard = source.guard()
    start = guard.check("query/start")
    source.advance(wall=7_200_100_000_000, perf=100_000_000)
    with pytest.raises(clock.ClockIntegrityError) as failed:
        guard.check("query/end", since=start["sample"])
    receipt = failed.value.receipt
    assert receipt["consistent"] is False
    assert receipt["interval"]["wall_elapsed_ns"] == 7_200_100_000_000
    assert receipt["interval"]["perf_elapsed_ns"] == 100_000_000
    assert receipt["interval"]["drift_ns"] == 7_200_000_000_000
    assert receipt["cumulative"]["drift_ns"] == 7_200_000_000_000


def test_suspend_between_queries_cannot_be_hidden_by_starting_a_new_interval():
    source = Clocks()
    guard = source.guard()
    first = guard.check("query1/start")
    source.advance(wall=500_000_000, perf=500_000_000)
    guard.check("query1/end", since=first["sample"])
    source.advance(wall=60_000_000_000, perf=0)
    with pytest.raises(clock.ClockIntegrityError) as failed:
        guard.check("query2/start")
    assert failed.value.receipt["interval"] is None
    assert failed.value.receipt["cumulative"]["drift_ns"] == 60_000_000_000


def test_suspend_during_setup_is_caught_before_first_query():
    source = Clocks()
    guard = source.guard()
    source.advance(wall=10_000_000_000, perf=1_000_000_000)
    with pytest.raises(clock.ClockIntegrityError, match="setup"):
        guard.check("setup")


@pytest.mark.parametrize("wall_step", [3_000_000_000, -3_000_000_000])
def test_forward_or_backward_wall_clock_steps_fail_with_signed_receipt(wall_step):
    source = Clocks()
    guard = source.guard()
    source.advance(wall=100_000_000 + wall_step, perf=100_000_000)
    with pytest.raises(clock.ClockIntegrityError) as failed:
        guard.check("clock-step")
    assert failed.value.receipt["cumulative"]["drift_ns"] == wall_step
    assert failed.value.receipt["cumulative"]["absolute_drift_ns"] == abs(wall_step)


def test_individually_tolerated_small_clock_steps_accumulate_to_a_failure():
    source = Clocks()
    guard = source.guard()
    source.advance(wall=1_000_000_000, perf=0)
    assert guard.check("first-small-step")["consistent"] is True
    source.advance(wall=1_000_000_000, perf=0)
    assert guard.check("second-small-step")["consistent"] is True
    source.advance(wall=1, perf=0)
    with pytest.raises(clock.ClockIntegrityError) as failed:
        guard.check("accumulated-step")
    assert failed.value.receipt["cumulative"]["drift_ns"] == 2_000_000_001


def test_within_query_step_reversal_is_detected_even_when_cumulative_end_is_tolerated():
    source = Clocks()
    guard = source.guard()
    source.advance(wall=2_000_000_000, perf=0)
    start = guard.check("query/start")
    source.advance(wall=-4_000_000_000, perf=0)
    with pytest.raises(clock.ClockIntegrityError) as failed:
        guard.check("query/end", since=start["sample"])
    assert failed.value.receipt["cumulative"]["consistent"] is True
    assert failed.value.receipt["interval"]["drift_ns"] == -4_000_000_000
    assert failed.value.receipt["consistent"] is False


def test_perf_clock_rollback_fails_even_below_two_second_drift():
    source = Clocks()
    guard = source.guard()
    source.advance(wall=0, perf=-1)
    with pytest.raises(clock.ClockIntegrityError) as failed:
        guard.check("perf-reset")
    assert failed.value.receipt["cumulative"]["absolute_drift_ns"] == 1
    assert failed.value.receipt["cumulative"]["perf_elapsed_ns"] == -1


def test_failure_and_external_copy_cannot_reset_cumulative_baseline_or_filter_delay():
    source = Clocks()
    guard = source.guard()
    initial = deepcopy(guard.baseline)
    external = guard.baseline
    external["wall_time_ns"] += 50_000_000_000
    source.advance(wall=3_000_000_000, perf=0)
    with pytest.raises(clock.ClockIntegrityError) as first:
        guard.check("first")
    first.value.receipt["baseline"]["wall_time_ns"] = 0
    with pytest.raises(clock.ClockIntegrityError) as second:
        guard.check("next")
    assert guard.baseline == initial
    assert second.value.receipt["cumulative"]["drift_ns"] == 3_000_000_000


@pytest.mark.parametrize("value", [True, 1.0, "1", None])
def test_fake_noninteger_nanosecond_clock_api_is_rejected(value):
    with pytest.raises(ValueError):
        clock.ClockIntegrity(wall_clock=lambda: value, perf_clock=lambda: 1)


def test_changed_prior_sample_utc_and_invalid_label_are_rejected():
    source = Clocks()
    guard = source.guard()
    old = guard.check("start")["sample"]
    old["utc"] = "invented"
    with pytest.raises(ValueError, match="UTC"):
        guard.check("end", since=old)
    with pytest.raises(ValueError, match="label"):
        guard.check("")
