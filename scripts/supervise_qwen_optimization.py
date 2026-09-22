#!/usr/bin/env python3
"""Bounded owner of the frozen resident worker, driver and optional Mac keepawake."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
READY_TIMEOUT_S = 180
TERM_GRACE_S = 30
KILL_GRACE_S = 10
POLL_S = .25
ATTEMPT_MARKERS = ("supervisor-start.json", "supervisor.json", "supervisor-validation-failure.json",
                   "reranker-start.json", "reranker-stop.json", "run-start.json", "completion.json",
                   "forwards.jsonl.gz", "searches.jsonl.gz")


class SupervisorInterrupted(Exception):
    def __init__(self, signum):
        self.signum = signum
        super().__init__("Supervisor interrupted by signal " + str(signum))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def publish(value, root):
    if isinstance(value, str):
        return value.replace(str(root), "<PROJECT>").replace(str(Path.home()), "<HOME>")
    if isinstance(value, dict):
        return {key: publish(part, root) for key, part in value.items()}
    if isinstance(value, list):
        return [publish(part, root) for part in value]
    return value


def write_new_json(path, value, *, root=ROOT):
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(publish(value, root), sort_keys=True, indent=2, allow_nan=False) + "\n")


def private_file(path):
    """Create mode600 atomically, including when the caller's umask is permissive."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.fchmod(descriptor, 0o600)
    return os.fdopen(descriptor, "w", encoding="utf-8")


def group_operation(process, signum, group_signal, permission_receipts):
    """Reap before probing/signaling; preserve and retry EPERM after a fresh poll.

    An exited leader does not prove its descendants have stopped. Only a group
    lookup returning ESRCH proves disappearance; unresolved live EPERM is raised.
    """
    process.poll()
    try:
        group_signal(process.pid, signum)
    except ProcessLookupError:
        return False
    except PermissionError as error:
        # Darwin can report EPERM for a just-exited, unreaped session leader.
        # The process may also have exited between the preceding poll and probe.
        retry = {"signal": int(signum), "error_type": type(error).__name__, "error": str(error),
                 "leader_exit_before_fresh_poll": process.returncode, "recovered": False}
        permission_receipts.append(retry)
        process.poll()
        retry["leader_exit_after_fresh_poll"] = process.returncode
        try:
            group_signal(process.pid, signum)
        except ProcessLookupError:
            retry["recovered"] = True
            retry["resolution"] = "Owned group disappeared after fresh leader poll/reap"
            return False
        except PermissionError:
            retry["resolution"] = "Permission error persists; group disappearance is unproved"
            raise
        else:
            retry["recovered"] = True
            retry["resolution"] = "Owned group operation succeeded after fresh leader poll"
    return True


def group_exists(process, group_signal, permission_receipts):
    return group_operation(process, 0, group_signal, permission_receipts)


def stop_owned(process, *, clock=time.monotonic, sleep=time.sleep, group_signal=os.killpg):
    """Signal only the new session ID belonging to the supplied owned Popen."""
    result = {"pid": process.pid, "owned_group": process.pid, "term_sent": False,
              "forced_kill": False, "owned_group_stopped": False, "exit_code": None,
              "started_cleanup_utc": utc_now(), "errors": [], "group_permission_retries": []}

    def exists():
        return group_exists(process, group_signal, result["group_permission_retries"])

    def send(signum):
        return group_operation(process, signum, group_signal, result["group_permission_retries"])

    try:
        if exists():
            result["term_sent"] = send(signal.SIGTERM)
            deadline = clock() + TERM_GRACE_S
            while exists() and clock() < deadline:
                sleep(POLL_S)
            if exists():
                result["forced_kill"] = send(signal.SIGKILL)
                deadline = clock() + KILL_GRACE_S
                while exists() and clock() < deadline:
                    sleep(POLL_S)
        process.poll()
        if process.returncode is None:
            try:
                process.wait(timeout=KILL_GRACE_S)
            except subprocess.TimeoutExpired as error:
                result["errors"].append({"error_type": type(error).__name__, "error": str(error)})
        result["exit_code"] = process.returncode
        result["owned_group_stopped"] = not exists()
    except BaseException as error:
        result["errors"].append({"error_type": type(error).__name__, "error": str(error)})
    finally:
        # Preserve the actual leader status even if descendant group checks fail.
        try:
            process.poll()
            result["exit_code"] = process.returncode
        except BaseException as error:
            result["errors"].append({"error_type": type(error).__name__, "error": str(error)})
    result["finished_cleanup_utc"] = utc_now()
    return result


def validate_plan(root, study):
    require(study.is_dir(), "Study must be an existing directory with a frozen plan")
    require(not any((study / name).exists() for name in ATTEMPT_MARKERS), "No retry/overwrite of an attempted study")
    plan_path = study / "frozen-plan.json"
    plan_raw = plan_path.read_bytes()
    plan = json.loads(plan_raw)
    require(isinstance(plan.get("implementation_sha256"), dict), "Frozen implementation bindings required")
    for required in ("scripts/resident_qwen_optimization.py", "scripts/run_qwen_optimization.py",
                     "scripts/qwen_reranker_batch.py", "scripts/supervise_qwen_optimization.py"):
        require(required in plan["implementation_sha256"], "Frozen plan lacks required implementation: " + required)
    for name, expected in plan["implementation_sha256"].items():
        require(isinstance(name, str) and not Path(name).is_absolute() and ".." not in Path(name).parts,
                "Frozen implementation path must remain inside project")
        path = (root / name).resolve(strict=True)
        require(path.is_relative_to(root), "Frozen implementation resolves outside project")
        require(sha(path.read_bytes()) == expected, "Frozen implementation changed: " + name)
    return plan, sha(plan_raw)


def validate_ready(path, process, plan_hash):
    require(path.is_file() and not path.is_symlink(), "Readiness must be a regular owned-cache file")
    path.chmod(0o600)
    ready = json.loads(path.read_text())
    require(ready.get("pid") == ready.get("process_group") == process.pid, "Readiness process identity differs")
    require(ready.get("resident_ready") is True and ready.get("generic_warmup_completed") is True,
            "Worker readiness/warmup incomplete")
    require(ready.get("frozen_plan_sha256") == plan_hash, "Readiness plan hash differs")
    url = ready.get("url")
    require(isinstance(url, str) and url.startswith("http://127.0.0.1:"), "Loopback-only readiness URL required")
    port = url[len("http://127.0.0.1:"):]
    require(port.isascii() and port.isdecimal() and 1 <= int(port) <= 65535, "Invalid loopback readiness port")
    return ready


def run(study, timeout=7200, *, root=ROOT, popen=subprocess.Popen,
        clock=time.monotonic, sleep=time.sleep, group_signal=os.killpg,
        caffeinate_path=Path("/usr/bin/caffeinate"), print_progress=print):
    require(type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0,
            "Timeout must be a positive finite number of seconds")
    root, study = Path(root).resolve(strict=True), Path(study).resolve(strict=True)
    require(study.is_relative_to(root), "Study must be inside this project")
    # Refuse old attempts before creating any new receipt or private resources.
    require(not any((study / name).exists() for name in ATTEMPT_MARKERS), "No retry/overwrite of an attempted study")
    started = clock()
    record = {"started_utc": utc_now(), "timeout_seconds": timeout,
              "ready_timeout_seconds": READY_TIMEOUT_S, "term_grace_seconds": TERM_GRACE_S,
              "kill_grace_seconds": KILL_GRACE_S, "timed_out": False, "forced_kill": False,
              "owned_sessions": True, "study": str(study.relative_to(root)),
              "supervisor_sha256": sha(Path(__file__).read_bytes())}
    try:
        _, plan_hash = validate_plan(root, study)
    except BaseException as error:
        record |= {"error_type": type(error).__name__, "error": str(error), "finished_utc": utc_now(),
                   "elapsed_seconds": clock() - started, "processes_launched": 0}
        write_new_json(study / "supervisor-validation-failure.json", record, root=root)
        return 1
    record["frozen_plan_sha256"] = plan_hash
    private = root / ".cache" / study.name
    require(not private.exists(), "Private run cache already exists; no resume/retry")
    private.mkdir(mode=0o700)
    private.chmod(0o700)
    token, ready = private / "token.secret", private / "ready.json"
    checkpoint = root / ".cache/models/qwen3-reranker-0.6b-original"
    interpreter = root / ".cache/reranker-experiment/.venv/bin/python"
    worker = main = awake = None
    logs = []
    old_signals = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGINT)}

    def interrupted(signum, _frame):
        raise SupervisorInterrupted(signum)

    for signum in old_signals:
        signal.signal(signum, interrupted)
    try:
        with private_file(token) as stream:
            stream.write(secrets.token_urlsafe(48))
        record["private_auth_token_mode"] = oct(token.stat().st_mode & 0o777)
        record["private_cache_mode"] = oct(private.stat().st_mode & 0o777)
        write_new_json(study / "supervisor-start.json", record, root=root)
        resident_log = private_file(private / "resident.log")
        logs.append(resident_log)
        main_log = private_file(private / "main.log")
        logs.append(main_log)
        if caffeinate_path is not None and Path(caffeinate_path).is_file():
            awake_log = private_file(private / "keepawake.log")
            logs.append(awake_log)
            try:
                awake = popen([str(caffeinate_path), "-i", "-s", "-w", str(os.getpid()), "-t", str(math.ceil(timeout) + 80)],
                              cwd=root, stdout=awake_log, stderr=subprocess.STDOUT, start_new_session=True)
                record["caffeinate_pid"] = awake.pid
                write_new_json(study / "supervisor-keepawake-start.json", {"pid": awake.pid,
                               "process_group": awake.pid, "started_utc": utc_now(), "owned": True}, root=root)
            except OSError as error:
                record["keepawake_error"] = {"error_type": type(error).__name__, "error": str(error)}
        worker = popen([str(interpreter), str(root / "scripts/resident_qwen_optimization.py"), "--run",
                        "--checkpoint", str(checkpoint), "--output", str(study), "--ready", str(ready),
                        "--token-file", str(token), "--frozen-plan", str(study / "frozen-plan.json")],
                       cwd=root, stdout=resident_log, stderr=subprocess.STDOUT, start_new_session=True)
        record["resident_pid"] = worker.pid
        write_new_json(study / "supervisor-resident-start.json", record | {"phase": "resident_started"}, root=root)
        print_progress(json.dumps({"resident_pid": worker.pid, "starting": True}), flush=True)
        ready_deadline = min(started + timeout, clock() + READY_TIMEOUT_S)
        while not ready.exists():
            require(worker.poll() is None, "Resident exited before readiness")
            if clock() >= ready_deadline:
                raise TimeoutError("Resident readiness timed out")
            sleep(POLL_S)
        validate_ready(ready, worker, plan_hash)
        require(worker.poll() is None, "Resident exited immediately after readiness")
        if clock() >= started + timeout:
            raise TimeoutError("Whole study timed out before main launch")
        require(sha((study / "frozen-plan.json").read_bytes()) == plan_hash, "Frozen plan changed before main launch")
        main = popen([str(root / ".venv/bin/python"), str(root / "scripts/run_qwen_optimization.py"), "--run",
                      "--output", str(study), "--reranker-python", str(interpreter), "--checkpoint", str(checkpoint),
                      "--ready", str(ready), "--token-file", str(token)],
                     cwd=root, stdout=main_log, stderr=subprocess.STDOUT, start_new_session=True)
        record["main_pid"] = main.pid
        write_new_json(study / "supervisor-main-start.json", record | {"phase": "main_started"}, root=root)
        print_progress(json.dumps({"main_pid": main.pid, "resident_ready": True}), flush=True)
        while main.poll() is None:
            require(worker.poll() is None, "Resident exited while main study was active")
            if clock() >= started + timeout:
                raise TimeoutError("Whole study timed out")
            sleep(POLL_S)
        require(main.returncode == 0, "Main study exited unsuccessfully")
    except BaseException as error:
        record |= {"error_type": type(error).__name__, "error": str(error),
                   "timed_out": isinstance(error, (TimeoutError, subprocess.TimeoutExpired))}
        if isinstance(error, SupervisorInterrupted):
            record["interrupted_signal"] = error.signum
    finally:
        # A second interrupt must not prevent cleanup of the other owned groups/token.
        for signum in old_signals:
            signal.signal(signum, signal.SIG_IGN)
        for label, process in (("main", main), ("resident", worker), ("caffeinate", awake)):
            if process is not None:
                stopped = stop_owned(process, clock=clock, sleep=sleep, group_signal=group_signal)
                record[label + "_stop"] = stopped
                record[label + "_exit_code"] = stopped["exit_code"]
                record[label + "_owned_group_stopped"] = stopped["owned_group_stopped"]
                record["forced_kill"] |= stopped["forced_kill"]
        try:
            token.unlink(missing_ok=True)
            record["private_auth_token_removed"] = not token.exists()
        except OSError as error:
            record["private_auth_token_removed"] = False
            record["token_cleanup_error"] = {"error_type": type(error).__name__, "error": str(error)}
        for stream in logs:
            stream.close()
        record["elapsed_seconds"] = clock() - started
        record["finished_utc"] = utc_now()
        ok = ("error" not in record and not record["forced_kill"] and not record["timed_out"]
              and record.get("main_exit_code") == record.get("resident_exit_code") == 0
              and record.get("main_owned_group_stopped") and record.get("resident_owned_group_stopped")
              and record.get("private_auth_token_removed")
              and all(not record.get(label + "_stop", {}).get("errors") for label in ("main", "resident", "caffeinate"))
              and (awake is None or record.get("caffeinate_owned_group_stopped")))
        record["success"] = bool(ok)
        record["supervisor_exit_code"] = 0 if ok else 1
        try:
            write_new_json(study / "supervisor.json", record, root=root)
        finally:
            for signum, handler in old_signals.items():
                signal.signal(signum, handler)
        print_progress(json.dumps(publish(record, root), sort_keys=True), flush=True)
    return record["supervisor_exit_code"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=7200)
    args = parser.parse_args(argv)
    return run(args.study, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
