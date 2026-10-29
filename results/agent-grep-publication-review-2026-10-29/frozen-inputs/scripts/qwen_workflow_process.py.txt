"""Owned CLI capture with durable partial evidence and bounded group cleanup.

Only callers invoke subprocesses; import is CPU metadata work. Raw files stay
private evaluator artifacts. The unchanged workflow publisher handles redaction
and excludes reasoning from public output.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import tempfile
import threading
import time

POLL_S = .05
TERM_GRACE_S = KILL_GRACE_S = 5.
DRAIN_GRACE_S = 1.


def helper(name):
    spec = importlib.util.spec_from_file_location("workflow_capture_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evidence = helper("qwen_workflow_evidence")


def stamp():
    wall, perf = time.time_ns(), time.perf_counter_ns()
    seconds, nanos = divmod(wall, 1_000_000_000)
    return {"utc": datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=nanos // 1000).isoformat(),
            "monotonic_s": time.monotonic(), "wall_time_ns": wall, "perf_counter_ns": perf}


def write_checkpoint(path, value):
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode() + b"\n"
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class CapturedSIGTERM(KeyboardInterrupt):
    signal_number = signal.SIGTERM


@contextmanager
def guarded_sigterm():
    """Explicit root-main scope: interrupt once, ignore repeats, restore policy."""
    if threading.current_thread() is not threading.main_thread():
        raise ValueError("SIGTERM guard belongs to the root main thread only")
    previous = signal.getsignal(signal.SIGTERM)
    receipt = {"installed": True, "received": False, "repeated_signals_ignored": False}

    def interrupt(signum, frame):
        if receipt["received"]:
            return
        receipt.update(received=True, received_at=stamp(), repeated_signals_ignored=True)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        raise CapturedSIGTERM("Root SIGTERM captured for owned CLI cleanup")

    signal.signal(signal.SIGTERM, interrupt)
    try:
        yield receipt
    finally:
        signal.signal(signal.SIGTERM, previous)
        receipt["restored"] = True


class ObservedProcess:
    """Stamp the first actual observed leader exit, including inside cleanup."""
    def __init__(self, process, on_exit):
        self.process, self.on_exit = process, on_exit
        self.pid = process.pid

    @property
    def returncode(self):
        return self.process.returncode

    def poll(self):
        result = self.process.poll()
        if result is not None:
            self.on_exit()
        return result

    def wait(self, timeout=None):
        result = self.process.wait(timeout=timeout)
        self.on_exit()
        return result


def stop_owned(process):
    """Bounded stop of the fresh Popen session/group; never use global process scans."""
    result = {"pid": process.pid, "owned_group": process.pid, "term_sent": False,
              "forced_kill": False, "owned_group_stopped": False, "errors": [], "permission_retries": [],
              "started_cleanup": stamp(), "term_grace_s": TERM_GRACE_S, "kill_grace_s": KILL_GRACE_S}

    def operation(signum):
        for attempt in range(3):
            process.poll()  # Darwin must reap a terminated leader before group probes.
            try:
                os.killpg(process.pid, signum)
                return True
            except ProcessLookupError:
                return False
            except PermissionError as error:
                result["permission_retries"].append({"signal": int(signum), "error_type": type(error).__name__, "attempt": attempt})
                if attempt == 2:
                    raise
                time.sleep(POLL_S)
        return False

    try:
        if operation(0):
            result["term_sent"] = operation(signal.SIGTERM)
            deadline = time.monotonic() + TERM_GRACE_S
            while operation(0) and time.monotonic() < deadline:
                time.sleep(POLL_S)
            if operation(0):
                result["forced_kill"] = operation(signal.SIGKILL)
                deadline = time.monotonic() + KILL_GRACE_S
                while operation(0) and time.monotonic() < deadline:
                    time.sleep(POLL_S)
        if process.poll() is None:
            process.wait(timeout=KILL_GRACE_S)
        result["owned_group_stopped"] = not operation(0)
    except BaseException as error:
        result["errors"].append({"error_type": type(error).__name__, "error": str(error)[:200]})
    result.update(exit_code=process.poll(), finished_cleanup=stamp())
    return result


def measurements(events, observed):
    items, commands, starts, tools = {}, {}, {}, []
    for index, row in enumerate(observed):
        event, received = row["event"], row["received"]
        item = event.get("item") or {}
        if not isinstance(item, dict):
            continue
        if event.get("type") == "item.started" and isinstance(item.get("id"), str) and item["id"]:
            starts[item["id"]] = received
        if event.get("type") == "item.completed" and item.get("type") in ("mcp_tool_call", "command_execution"):
            key = item.get("id") if isinstance(item.get("id"), str) and item["id"] else f"event-{index}"
            (items if item["type"] == "mcp_tool_call" else commands)[key] = item
            began = starts.get(key)
            tools.append({"item_id": item.get("id"), "type": item["type"], "start": began, "end": received,
                          "observed_duration_s": received["monotonic_s"] - began["monotonic_s"] if began else None})
    return {**evidence.provider_usage(events), "completed": any(e.get("type") == "turn.completed" for e in events),
            "failed": any(e.get("type") in ("turn.failed", "error") for e in events),
            "mcp_calls": len(items), "command_calls": len(commands), "mcp_items": list(items.values()),
            "errors": [e for e in events if e.get("type") in ("error", "turn.failed")], "tool_intervals": tools}


def run_agent(command, config, workspace, raw_dir, *, timeout=None, environment=None, stopper=stop_owned):
    """Return the original tuple shape even after captured interruption/start failure.

    Every received line is saved before parsing; interim checkpoints contain
    known completed-turn measurements. Agent timing ends at first observed CLI
    leader exit, before separately recorded group cleanup and output draining.
    """
    limit = config["timeout_s"] if timeout is None else timeout
    if type(limit) not in (int, float) or not math.isfinite(limit) or limit <= 0:
        raise ValueError("CLI timeout must be positive and finite")
    if environment is None:
        environment = helper("run_multifile_workflows").environment
    workspace, raw_dir = Path(workspace), Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.chmod(0o700)
    events, observed, invalid, errors = [], [], [], []
    process = owned = None
    start = end = cleanup = None
    buffer = b""
    timed_out = interrupted = False
    interrupt_type = interrupt_signal = None
    pid_receipt = {"launched": False, "start_new_session": True,
                   "command_sha256": hashlib.sha256(json.dumps(command, ensure_ascii=False).encode()).hexdigest()}

    def record_exit():
        nonlocal end
        if end is None:
            end = stamp()

    def checkpoint(phase):
        result = measurements(events, observed)
        result.update(agent_start=start, agent_end=end,
            agent_elapsed_s=end["monotonic_s"] - start["monotonic_s"] if end and start else None,
            agent_exit_code=owned.returncode if owned else None, timed_out=timed_out,
            interrupted=interrupted, interruption_type=interrupt_type, interruption_signal=interrupt_signal,
            invalid_event_lines=invalid, capture_errors=errors,
            failed=result["failed"] or interrupted or bool(errors) or bool(invalid),
            agent_process=pid_receipt, agent_cleanup=cleanup,
            cleanup_qualified=bool(cleanup and cleanup.get("owned_group_stopped") is True),
            per_model_call_usage=None, per_model_call_usage_reason="Not emitted by exec --json")
        try:
            write_checkpoint(raw_dir / "agent-measurements.json", {"phase": phase, **result,
                             "observed_event_count": len(observed), "raw_event_log": "events.jsonl"})
        except Exception as error:
            errors.append({"operation": "checkpoint", "error_type": type(error).__name__, "error": str(error)[:200]})
            result["failed"] = True
        return result

    def record_interrupt(signum):
        nonlocal interrupted, interrupt_type, interrupt_signal
        interrupted, interrupt_signal = True, signum
        interrupt_type = "CapturedSIGTERM" if signum == signal.SIGTERM else "KeyboardInterrupt"

    @contextmanager
    def launch_signals():
        # Do not raise between Popen's actual fork and assigning its ownership.
        # Caught handlers reset on exec; blocking or ignoring signals here would
        # instead make the launched CLI inherit an unresponsive signal policy.
        previous, pending = {}, []
        if threading.current_thread() is threading.main_thread():
            def retain(signum, frame):
                record_interrupt(signum)
                if signum not in pending:
                    pending.append(signum)
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.getsignal(signum)
                signal.signal(signum, retain)
        try:
            yield
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
            for signum in pending:
                if callable(previous[signum]):
                    try:
                        previous[signum](signum, None)
                    except KeyboardInterrupt:
                        pass

    @contextmanager
    def cleanup_signals():
        # A first signal during cleanup must be retained without abandoning the
        # owned group. Delegate to the root guard for its receipt, swallowing its
        # captured interrupt until this bounded critical section finishes.
        previous = {}
        if threading.current_thread() is threading.main_thread():
            def retain(signum, frame):
                record_interrupt(signum)
                before = previous[signum]
                if callable(before):
                    try:
                        before(signum, frame)
                    except KeyboardInterrupt:
                        pass
                signal.signal(signum, signal.SIG_IGN)
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.getsignal(signum)
                signal.signal(signum, retain)
        try:
            yield
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)

    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate CLI JSON keys")
            result[key] = value
        return result

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Nonfinite CLI JSON number")
        return number

    def constant(value):
        raise ValueError("Nonfinite CLI JSON constant: " + value)

    def consume(line):
        received = stamp()
        try:
            event = json.loads(line.decode("utf-8"), object_pairs_hook=object_pairs,
                               parse_float=finite_float, parse_constant=constant)
            if not isinstance(event, dict):
                raise ValueError("CLI event must be a JSON object")
            events.append(event)
            observed.append({"received": received, "event": event})
        except (ValueError, UnicodeError, RecursionError) as error:
            invalid.append({"received": received, "line": line.decode("utf-8", errors="replace"),
                            "error_type": type(error).__name__})
        checkpoint("capturing")

    with (raw_dir / "events.jsonl").open("xb") as raw_events, (raw_dir / "stderr.txt").open("xb") as stderr:
        (raw_dir / "events.jsonl").chmod(0o600)
        (raw_dir / "stderr.txt").chmod(0o600)
        selector = selectors.DefaultSelector()
        eof = False

        def drain(wait):
            nonlocal buffer, eof
            for key, _ in selector.select(wait):
                try:
                    data = os.read(key.fd, 65536)
                except BlockingIOError:
                    continue
                if not data:
                    eof = True
                    selector.unregister(key.fd)
                    break
                raw_events.write(data)
                raw_events.flush()
                buffer += data
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    consume(line + b"\n")

        try:
            start = stamp()
            with launch_signals():
                process = subprocess.Popen(command, cwd=workspace, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=stderr, start_new_session=True,
                    env=environment(workspace, config.get("source_root", "src")))
                owned = ObservedProcess(process, record_exit)
                pid_receipt.update(launched=True, pid=process.pid, owned_group=process.pid, launched_at=stamp())
                write_checkpoint(raw_dir / "agent-process.json", pid_receipt)
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ)
            checkpoint("launched")
            deadline = start["monotonic_s"] + limit
            while not interrupted and owned.poll() is None:
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                drain(min(POLL_S, max(0, deadline - time.monotonic())))
        except KeyboardInterrupt as error:
            interrupted, interrupt_type = True, type(error).__name__
            interrupt_signal = getattr(error, "signal_number", None)
        except Exception as error:
            errors.append({"error_type": type(error).__name__, "error": str(error)[:300]})
        finally:
            with cleanup_signals():
                checkpoint("before_cleanup")
                cleanup_start = stamp()
                try:
                    cleanup = stopper(owned) if owned else {"owned_group_stopped": True, "process_launched": False}
                except BaseException as error:
                    cleanup = {"owned_group_stopped": False, "errors": [{"error_type": type(error).__name__, "error": str(error)[:200]}]}
                if owned:
                    owned.poll()
                cleanup_end = stamp()
                cleanup = {**cleanup, "capture_cleanup_start": cleanup_start, "capture_cleanup_end": cleanup_end,
                           "capture_cleanup_s": cleanup_end["monotonic_s"] - cleanup_start["monotonic_s"]}
                try:
                    deadline = time.monotonic() + DRAIN_GRACE_S
                    while process is not None and not eof and time.monotonic() < deadline:
                        drain(min(POLL_S, max(0, deadline - time.monotonic())))
                    if buffer:
                        consume(buffer)
                except Exception as error:
                    errors.append({"operation": "output_drain", "error_type": type(error).__name__, "error": str(error)[:200]})
                finally:
                    selector.close()
                    if process is not None:
                        try:
                            process.stdout.close()
                        except OSError as error:
                            errors.append({"operation": "close_stdout", "error_type": type(error).__name__, "error": str(error)[:200]})
    try:
        write_checkpoint(raw_dir / "observed-events.json", observed)
    except Exception as error:
        errors.append({"operation": "observed_checkpoint", "error_type": type(error).__name__, "error": str(error)[:200]})
    result = checkpoint("finished")
    result["output_drain_complete"] = eof or process is None
    result["cleanup_qualified"] = result["cleanup_qualified"] and result["output_drain_complete"]
    result["lifecycle_qualified"] = result["cleanup_qualified"]
    result["cleanup_clean"] = (result["cleanup_qualified"] and not cleanup.get("forced_kill")
                               and not cleanup.get("errors"))
    if not result["cleanup_clean"]:
        result["failed"] = True
    if interrupted or not result["cleanup_qualified"]:
        result["timing_qualified"] = False
    try:
        write_checkpoint(raw_dir / "agent-measurements.json", result)
    except Exception as error:
        errors.append({"operation": "final_checkpoint", "error_type": type(error).__name__, "error": str(error)[:200]})
        result["failed"] = True
    return result, events, observed
