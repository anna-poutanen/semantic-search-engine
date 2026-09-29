#!/usr/bin/env python3
"""Freeze and run the separate three-condition, strict-check coding study.

Validation and freezing are CPU/metadata work. `probe` and `run` explicitly
invoke hosted Codex; only `run` loads local models. Earlier studies are intact.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time

import run_multifile_workflows as mf
from qwen_optimization_clock import ClockIntegrity, ClockIntegrityError
import qwen_workflow_evidence as evidence
import qwen_workflow_process as process_capture
from semantic_search.context import compact_result, serialize_context

ROOT = mf.ROOT
SELF = Path("scripts/run_qwen_workflows.py")
PROTOCOL = Path("docs/qwen-coding-workflow-protocol.md")
MANIFEST = Path("benchmarks/coding_workflows_strict.json")
INSTRUCTION = (
    mf.WORKFLOW_INSTRUCTION + " When search_code is available, retain its configured retrieval "
    "and reranking defaults: do not pass retrieval, rerank, rerank_provider or candidates. "
    "You may change the query, result count, source/token budgets and dependency option."
)
sha = mf.old.digest
write = mf.write_json
read = mf.read_json


def retain_interruption(record, error, *, signum=None):
    """Keep known measurements while preventing an interrupted attempt qualifying."""
    signum = signum if signum is not None else getattr(error, "signal_number", None)
    record.update(interrupted=True, failed=True, timing_qualified=False, completed_correct=False,
                  interruption_type=type(error).__name__, interruption_signal=signum)
    record.setdefault("interruptions", []).append({"type": type(error).__name__,
        "signal": signum, "received": mf.stamp()})


@contextmanager
def cleanup_interrupts(record):
    """Retain soft signals without abandoning the current bounded owned cleanup."""
    previous = {}
    if threading.current_thread() is threading.main_thread():
        def retain(signum, frame):
            retain_interruption(record, KeyboardInterrupt(), signum=signum)
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


def public_record(record, substitutions):
    # Raw malformed JSON can contain private reasoning. Keep it only in the
    # evaluator log; publish its receipt/hash rather than the invalid line.
    result = dict(record)
    if "invalid_event_lines" in result:
        result["invalid_event_lines"] = [{**{key: value for key, value in row.items() if key != "line"},
            "line_sha256": hashlib.sha256(row.get("line", "").encode()).hexdigest()}
            for row in result["invalid_event_lines"]]
    return mf.public_redact(result, substitutions)


def prompt(task, python):
    return mf.prompt(task, python).replace(mf.WORKFLOW_INSTRUCTION, INSTRUCTION)


def validate_manifest(path=MANIFEST):
    manifest = read(ROOT / path)
    tasks = manifest.get("tasks", [])
    evidence.schedule(tasks)
    original = {task["id"]: task for task in read(ROOT / mf.MANIFEST)["tasks"]}
    for task in tasks:
        prior = original[task["id"]]
        for key in ("prompt", "corpus", "revision", "source_root", "preparation_patch",
                    "compatibility_commands", "upstream_test_sha256"):
            if task.get(key) != prior.get(key):
                raise ValueError("Observed task contract or upstream fixture changed: " + key)
        for name, expected in mf.task_artifacts(task).items():
            if sha(ROOT / name) != expected:
                raise ValueError("Task artifact changed: " + name)
        if mf.old.git(ROOT / task["corpus"], "rev-parse", "HEAD").stdout.decode().strip() != task["revision"]:
            raise ValueError("Pinned upstream revision changed")
        for name, expected in task.get("strict_artifact_sha256", {}).items():
            if sha(ROOT / mf.artifact(name)) != expected:
                raise ValueError("Strict control/checker changed: " + name)
        if not task.get("strict_control_passed") or not task.get("strict_artifact_sha256"):
            raise ValueError("Passing, source-bound strict reference control required")
        if task["seed_validation"]["reference"].get("passed") is not True:
            raise ValueError("Strict reference must pass before hosted attempts")
    return manifest


def selected_profile(decision_path, fresh_study):
    decision = read(decision_path)
    selected = decision.get("selected_config")
    if (decision.get("stage") != "development" or decision.get("ready_for_fresh_validation") is not True
            or not isinstance(selected, dict)
            or selected.get("batch_size") != 1 or selected.get("retrieval") not in ("dense", "hybrid")):
        raise ValueError("Certified full-development singleton decision required")
    family = decision.get("selected_by_family", {}).get(selected["retrieval"], {})
    if not family or family.get("qualified") is not True or family.get("configuration") != selected:
        raise ValueError("Selected singleton did not pass full-development quality gates")
    binding = decision["study_binding"]
    development = ROOT / binding["study"]
    dev_audit = read(development / "independent-audit.json")
    if (dev_audit.get("checks_passed") is not True or dev_audit.get("stage") != "development"
            or sha(development / "independent-audit.json") != binding["audit_sha256"]
            or sha(development / "frozen-plan.json") != binding["frozen_plan_sha256"]
            or sha(development / "searches.jsonl.gz") != binding["searches_sha256"]):
        raise ValueError("Full-development selection certificate binding changed")
    certificate = read(fresh_study / "independent-audit.json")
    plan = read(fresh_study / "frozen-plan.json")
    if certificate.get("checks_passed") is not True or plan.get("stage") != "fresh":
        raise ValueError("Fresh HTTPX study must be independently certified")
    for name, expected in certificate["artifact_sha256"].items():
        if Path(name).name != name or sha(fresh_study / name) != expected:
            raise ValueError("Fresh certified artifact changed: " + name)
    if selected not in plan["conditions"]:
        raise ValueError("Chosen profile was not prospectively tested on HTTPX")
    bound = plan.get("decision_inputs", {})
    # The exact published decision must be among the fresh plan's frozen inputs.
    if bound.get(mf.artifact(decision_path)) != sha(decision_path):
        raise ValueError("Fresh plan does not bind this pre-observation selection")
    return selected, plan


def grade_projection(detail):
    """Drop varying diagnostic text/paths; keep strict oracle identity and grades."""
    if not isinstance(detail, dict):
        return None
    if (detail.get("version") != 1 or type(detail.get("passed")) is not bool
            or any(type(detail.get(key)) is not int or detail[key] < 0 for key in
                   ("model_calls", "tests_run", "failures", "errors", "skipped"))):
        return None
    projected = {key: detail.get(key) for key in ("version", "task_id", "strict_manifest_sha256",
                 "model_calls", "tests_run", "failures", "errors", "skipped", "passed",
                 "original_grade_unchanged")}
    projected["components"] = []
    for component in detail.get("components", []):
        item = {key: component.get(key) for key in ("script", "script_sha256", "exit_code", "timed_out", "passed")}
        payload = component.get("result")
        if isinstance(payload, dict) and isinstance(payload.get("cases"), dict):
            item["supplemental_version"] = payload.get("version")
            item["probe_sha256"] = payload.get("probe_sha256")
            item["cases"] = {key: value.get("passed") if isinstance(value, dict) else None
                             for key, value in payload["cases"].items()}
        item["receipt_error"] = component.get("receipt_error")
        projected["components"].append(item)
    return projected


def grade(task, workspace):
    verification = task["verification"]
    command = [sys.executable, str(ROOT / verification["script"]),
               *[arg.replace("{workspace}", str(workspace)) for arg in verification["args"]]]
    start = mf.stamp()
    try:
        result = subprocess.run(command, capture_output=True, text=True,
            timeout=verification["timeout_s"], env=mf.environment(workspace, task["source_root"]))
        try:
            detail = json.loads(result.stdout)
        except (ValueError, TypeError):
            detail = None
        projection = grade_projection(detail)
        return {"passed": result.returncode == 0 and projection == task["seed_validation"]["reference"],
                "exit_code": result.returncode, "detail": detail, "projection": projection,
                "stdout": result.stdout, "stderr": result.stderr, "start": start, "end": mf.stamp()}
    except subprocess.TimeoutExpired as error:
        return {"passed": False, "exit_code": None, "detail": None, "projection": None,
                "error": "strict_check_timeout", "stdout": error.stdout, "stderr": error.stderr,
                "start": start, "end": mf.stamp()}


def production_certificate(directory):
    """Bind a successful source-current production control without running it."""
    directory = Path(directory)
    plan_path, certificate_path = directory / "frozen-plan.json", directory / "verification.json"
    certificate, plan = read(certificate_path), read(plan_path)
    if (certificate.get("checks_passed") is not True
            or certificate.get("frozen_plan_sha256") != sha(plan_path)):
        raise ValueError("Passing frozen production certificate required: " + str(directory))
    sources = plan.get("source_sha256")
    if not isinstance(sources, dict) or not sources:
        raise ValueError("Production certificate must bind current source")
    for name, expected in sources.items():
        path = (ROOT / name).resolve()
        if not path.is_relative_to(ROOT.resolve()) or sha(path) != expected:
            raise ValueError("Production certificate source changed: " + name)
    if "source_sha256" in certificate and certificate["source_sha256"] != sources:
        raise ValueError("Production certificate source binding differs from plan")
    for name, expected in certificate.get("artifact_sha256", {}).items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or sha(path) != expected:
            raise ValueError("Certified production artifact changed: " + name)
    # Include every public result/ledger/receipt alongside the certificate, even
    # older certificate formats that do not carry their own artifact map.
    return {mf.artifact(path): sha(path) for path in directory.iterdir() if path.is_file()}


def freeze_common(output, decision_path, fresh_study, production_parity, production_smoke):
    if output.exists():
        raise ValueError("Choose a fresh common-runtime directory")
    selected, fresh_plan = selected_profile(decision_path, fresh_study)
    parity_binding = production_certificate(production_parity)
    smoke_binding = production_certificate(production_smoke)
    config = mf.freeze_common(output / "base-runtime")
    files = [SELF, PROTOCOL, Path("scripts/qwen_workflow_evidence.py"),
             Path("scripts/qwen_workflow_process.py"),
             Path("scripts/run_multifile_workflows.py"), Path("scripts/qwen_optimization_clock.py"),
             *[path.relative_to(ROOT) for path in (ROOT / "src/semantic_search").glob("*.py")]]
    for path in files:
        current = (ROOT / path).read_bytes()
        if mf.old.git(ROOT, "show", f"{config['runtime_git_revision']}:{path}").stdout != current:
            raise ValueError("Commit implementation before freezing: " + str(path))
    config["artifact_sha256"].update({str(path): sha(ROOT / path) for path in files})
    config["artifact_sha256"].update({mf.artifact(decision_path): sha(decision_path),
        mf.artifact(fresh_study / "independent-audit.json"): sha(fresh_study / "independent-audit.json"),
        mf.artifact(fresh_study / "frozen-plan.json"): sha(fresh_study / "frozen-plan.json")})
    config["artifact_sha256"].update(parity_binding)
    config["artifact_sha256"].update(smoke_binding)
    config.update({"classification": "Three-condition observed-task development rerun with stricter prospective gates",
        "selected_profile": selected, "development_decision": mf.artifact(decision_path),
        "fresh_study": mf.artifact(fresh_study), "workflow_instruction": INSTRUCTION,
        "production_parity": mf.artifact(production_parity),
        "production_smoke": mf.artifact(production_smoke),
        "conditions": list(evidence.CONDITIONS), "planned_attempts": 18,
        "reranker_python": str((ROOT / ".cache/reranker-experiment/.venv/bin/python").absolute()),
        "reranker_checkpoint": str((ROOT / ".cache/models/qwen3-reranker-0.6b-original").resolve()),
        "reranker_expected_runtime": fresh_plan["reranker"],
        "usage": "Actual reported completed-turn input+output; cache and reasoning fields are subsets. Interrupted-turn usage unknown.",
        "causal_scope": "Tool availability/default profiles plus observed use; cache and provider behavior remain measured confounds."})
    config["reranker_python_sha256"] = sha(config["reranker_python"])
    verify_native_runtime(config)
    write(output / "common-runtime.json", config)
    return config


def verify_native_runtime(config):
    if sha(config["reranker_python"]) != config["reranker_python_sha256"]:
        raise ValueError("Frozen native Python changed")
    result = subprocess.run([config["reranker_python"], "-B", "-c",
        "import importlib.metadata,json; print(json.dumps(dict(sorted((d.metadata['Name'],d.version) "
        "for d in importlib.metadata.distributions()))))"], check=True, capture_output=True, text=True)
    if json.loads(result.stdout) != config["reranker_expected_runtime"]["packages"]:
        raise ValueError("Frozen native package closure changed")


def cli_command(config, workspace, final_path, user_prompt, service_url=None, condition="baseline", source_root=None):
    command = mf.cli_command(config, workspace, final_path, user_prompt, service_url, source_root)
    if service_url is not None:
        profile = config["selected_profile"]
        args = ["-m", "semantic_search.mcp_server", "--server", service_url, "--agent-text-only",
                "--default-retrieval", profile["retrieval"],
                "--default-candidates", str(profile["shortlist"])]
        if condition == "mcp_qwen":
            args += ["--default-rerank", "--default-rerank-provider", "qwen"]
        setting = "mcp_servers.semantic_search.args="
        matches = [index for index, value in enumerate(command) if value.startswith(setting)]
        if len(matches) != 1:
            raise ValueError("Expected one explicit MCP argv setting")
        command[matches[0]] = setting + mf.old.toml_value(args)
    return command


def freeze(output, work_root, common_path, probe_path, manifest_path=MANIFEST):
    if output.exists() or work_root.exists():
        raise ValueError("Fresh study and workspace directories required")
    config = read(common_path)
    mf.verify_runtime(config)
    verify_native_runtime(config)
    probe = read(probe_path)
    if (probe.get("common_runtime_sha256") != sha(common_path)
            or not probe.get("completed_correct")
            or probe.get("successful_expected_mcp_calls") != 1
            or probe.get("successful_expected_http_receipts") != 1
            or probe.get("forbidden_local_model_calls") != 0
            or probe.get("observed_command_items") != 1
            or len(probe.get("matched_loopback_commands", [])) != 1
            or probe.get("observed_file_change_items") != 0
            or probe.get("initial_file_hashes") != probe.get("final_file_hashes")
            or not probe.get("usage")):
        raise ValueError("Successful source-bound lexical/loopback hosted probe required")
    manifest = validate_manifest(manifest_path)
    config.update({"manifest": mf.artifact(manifest_path), "manifest_sha256": sha(ROOT / manifest_path),
        "work_root": str(work_root), "common_runtime_source": mf.artifact(common_path),
        "common_runtime_sha256": sha(common_path), "transport_probe": mf.artifact(probe_path),
        "transport_probe_sha256": sha(probe_path), "schedule": evidence.schedule(manifest["tasks"]),
        "prompt_sha256": {task["id"]: hashlib.sha256(prompt(task, config["python"]).encode()).hexdigest()
                          for task in manifest["tasks"]}})
    config["artifact_sha256"].update({mf.artifact(manifest_path): sha(ROOT / manifest_path),
        mf.artifact(common_path): sha(common_path), mf.artifact(probe_path): sha(probe_path)})
    review_path = Path("results/strict-coding-preflight-2026-10-14/independent-review/review.json")
    review = read(ROOT / review_path)
    if (review.get("checks_passed") is not True or review.get("model_calls") != 0
            or review.get("actual_controls_sha256") != manifest["control_receipt_sha256"]):
        raise ValueError("Source-bound independent strict control review required")
    config["artifact_sha256"][str(review_path)] = sha(ROOT / review_path)
    for task in manifest["tasks"]:
        config["artifact_sha256"].update(mf.task_artifacts(task))
        config["artifact_sha256"].update(task["strict_artifact_sha256"])
    output.mkdir(parents=True)
    write(output / "frozen-config.json", config)
    shutil.copyfile(ROOT / manifest_path, output / "tasks.json")
    return config


def recording_backend(workspace, source_root, raw_dir, qwen_client=None):
    from semantic_search.backend import SearchBackend

    class RecordingBackend(SearchBackend):
        record_requests = False
        receipt_count = 0
        receipt_lock = threading.Lock()

        def search(self, query, **settings):
            if not self.record_requests:
                return super().search(query, **settings)
            with self.receipt_lock:
                self.receipt_count += 1
                number = self.receipt_count
            directory = raw_dir / "snapshots" / f"{number:04d}"
            receipt = {"number": number, "start": mf.stamp(), "query": query,
                       "settings": settings, "snapshots": {}}
            before = time.perf_counter()
            try:
                receipt["snapshots"]["before"] = mf.snapshot(workspace, source_root, directory / "before")
                receipt["snapshot_before_s"] = time.perf_counter() - before
                call_start = time.perf_counter()
                response = super().search(query, **settings)
                receipt["backend_call_s"] = time.perf_counter() - call_start
                receipt["response"] = response
                receipt["compact_text_sha256"] = hashlib.sha256(serialize_context(compact_result(response)).encode()).hexdigest()
                receipt["success"] = True
                return response
            except Exception as error:
                receipt.update(success=False, error=f"{type(error).__name__}: {error}")
                raise
            finally:
                after = time.perf_counter()
                try:
                    receipt["snapshots"]["after"] = mf.snapshot(workspace, source_root, directory / "after")
                    if receipt.get("success"):
                        receipt["returned_source_checks"] = mf.check_receipt_source(receipt["response"], directory / "after")
                except Exception as error:
                    receipt["snapshot_error"] = f"{type(error).__name__}: {error}"
                receipt["snapshot_after_s"] = time.perf_counter() - after
                receipt["end"] = mf.stamp()
                with self.receipt_lock, (raw_dir / "http-receipts.jsonl").open("a") as stream:
                    stream.write(json.dumps(receipt, allow_nan=False) + "\n")

    return RecordingBackend(workspace, source_root, db_path=raw_dir / "index.sqlite3", qwen_client=qwen_client)


def publish_qwen_evidence(owner, public, substitutions):
    """Keep every native event and proof; never copy the private auth file."""
    directory = getattr(owner, "directory", None)
    if directory is None:
        return {"native_artifacts": {}, "native_counts_complete": False}
    artifacts = {}
    target = public / "qwen"
    target.mkdir(exist_ok=True)
    for name in ("forwards.jsonl", "start.json", "stop.json", "ready.json"):
        path = directory / name
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if name.endswith(".jsonl"):
            rows = [json.loads(line) for line in raw.decode().splitlines()]
            rendered = "".join(json.dumps(mf.public_redact(row, substitutions), sort_keys=True,
                allow_nan=False) + "\n" for row in rows).encode()
            destination = target / (name + ".gz")
            destination.write_bytes(gzip.compress(rendered, mtime=0))
        else:
            destination = target / name
            write(destination, mf.public_redact(json.loads(raw), substitutions))
        artifacts[name] = {"raw_sha256": hashlib.sha256(raw).hexdigest(),
                          "published_file": str(destination.relative_to(public)),
                          "published_sha256": sha(destination)}
    ledger = directory / "forwards.jsonl"
    if not ledger.is_file():
        return {"native_artifacts": artifacts, "native_counts_complete": False}
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    scoring = [row for row in rows if row.get("warmup") is False]
    attempted = sum(row.get("event") == "started" for row in scoring)
    completed = sum(row.get("event") == "completed" for row in scoring)
    stop = getattr(owner, "receipts", {}).get("worker_stop", {})
    counts = stop.get("counts", {})
    all_attempted = sum(row.get("event") == "started" for row in rows)
    all_completed = sum(row.get("event") == "completed" for row in rows)
    complete_counts = (counts.get("attempted_forwards") == all_attempted
                       and counts.get("completed_forwards") == all_completed
                       and stop.get("clean_stop") is True)
    return {"native_artifacts": artifacts, "native_counts_complete": complete_counts,
            "observed_qwen_attempted_forwards": attempted,
            "observed_qwen_completed_forwards": completed,
            "native_warmup_events": sum(row.get("warmup") is True for row in rows),
            "native_counter_receipt": counts,
            "native_count_scope": "Non-warmup B1 started/completed events; incomplete worker stop supplies lower bounds only"}


def run_trial(task, trial, number, config, output, clock):
    name = f"{number:02d}-r{trial['repetition']}-{task['id']}-{trial['condition']}"
    raw_dir = Path(config["work_root"]) / "raw" / name
    workspace = Path(config["work_root"]) / "workspaces" / name
    public = output / "runs" / name
    raw_dir.mkdir(parents=True, exist_ok=False)
    public.mkdir(parents=True, exist_ok=False)
    substitutions = [(str(workspace), "<WORKSPACE>"), (str(raw_dir), "<EVALUATOR_LOG>"),
                     (str(ROOT), "<PROJECT>"), (str(Path.home()), "<HOME>")]
    record = {"run_id": name, **trial, "phase_timestamps": {"fixture_start": mf.stamp()},
        "correct": False, "completed_correct": False, "completed": False, "failed": False,
        "timed_out": False, "usage": None, "mcp_calls": 0, "successful_qwen_searches": 0,
        "agent_elapsed_s": None, "timing_qualified": True, "lifecycle_qualified": True,
        "clock_start": clock.check("trial-start")}
    backend = server = worker = owner = None
    events = observed = []
    measurements = None
    try:
        base_commit, base_files = mf.prepare(task, workspace)
        write(public / "base-files.json", base_files)
        record.update(base_commit=base_commit, base_tree_sha256=hashlib.sha256(
                      json.dumps(base_files, sort_keys=True).encode()).hexdigest())
        record["seed_grade"] = grade(task, workspace)
        if record["seed_grade"]["projection"] != task["seed_validation"]["base"]:
            raise RuntimeError("Unmodified feature-check control changed")
        record["phase_timestamps"]["fixture_end"] = mf.stamp()
        service_url = None
        if trial["condition"] != "baseline":
            from semantic_search.server import create_server
            record["phase_timestamps"]["service_setup_start"] = mf.stamp()
            client = None
            if trial["condition"] == "mcp_qwen":
                from semantic_search.qwen_reranker import QwenRerankerProcess
                owner = QwenRerankerProcess(python=config["reranker_python"],
                    checkpoint=config["reranker_checkpoint"], log_dir=raw_dir / "qwen")
                client = owner.start()
                record["qwen_start"] = owner.receipt
            backend = recording_backend(workspace, task["source_root"], raw_dir, client)
            record["initial_index"] = backend.refresh()
            backend.search("Find a function implementation", max_results=1, token_budget=512)
            backend.start_watching()
            backend.record_requests = True
            server = create_server(backend, port=0)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            service_url = f"http://127.0.0.1:{server.server_port}"
            record["phase_timestamps"]["service_setup_end"] = mf.stamp()
            record["service_setup_s"] = (record["phase_timestamps"]["service_setup_end"]["monotonic_s"]
                - record["phase_timestamps"]["service_setup_start"]["monotonic_s"])
        user_prompt = prompt(task, config["python"])
        record["prompt_sha256"] = hashlib.sha256(user_prompt.encode()).hexdigest()
        if record["prompt_sha256"] != config["prompt_sha256"][task["id"]]:
            raise ValueError("Common prompt changed after freeze")
        command = cli_command(config, workspace, raw_dir / "final.txt", user_prompt,
                              service_url, trial["condition"], task["source_root"])
        write(raw_dir / "command.json", command)
        (public / "prompt.txt").write_text(mf.public_redact(user_prompt, substitutions))
        record["clock_agent_start"] = clock.check("agent-start")
        print(f"Running {name}", flush=True)
        measurements, events, observed = process_capture.run_agent(command,
            {**config, "source_root": task["source_root"]}, workspace, raw_dir,
            environment=mf.environment)
        record.update(measurements)
        record.update(evidence.provider_usage(events))
        record["clock_agent_end"] = clock.check("agent-end", since=record["clock_agent_start"]["sample"])
        if backend:
            record["final_backend_status"] = backend.status()
    except ClockIntegrityError as error:
        record.update(timing_qualified=False, clock_failure=error.receipt,
                      infrastructure_error=str(error), failed=True)
    except KeyboardInterrupt as error:
        # Recover the helper's durable evidence if the signal landed between
        # its return and the assignment/update in this caller. No usage is
        # estimated; this is the actual checkpoint from the owned CLI attempt.
        try:
            if measurements is None and (raw_dir / "agent-measurements.json").is_file():
                measurements = read(raw_dir / "agent-measurements.json")
            if isinstance(measurements, dict):
                record.update(measurements)
            if not observed and (raw_dir / "observed-events.json").is_file():
                observed = read(raw_dir / "observed-events.json")
                events = [row["event"] for row in observed]
            if events:
                record.update(evidence.provider_usage(events))
        except Exception as recovery_error:
            record["capture_recovery_error"] = f"{type(recovery_error).__name__}: {recovery_error}"
        retain_interruption(record, error)
    except Exception as error:
        record.update(infrastructure_error=f"{type(error).__name__}: {error}", failed=True)
    finally:
        with cleanup_interrupts(record):
            record["phase_timestamps"]["shutdown_start"] = mf.stamp()
            try:
                if server:
                    if worker is not None and worker.is_alive():
                        server.shutdown()
                    server.server_close()
                if worker and worker.ident is not None:
                    worker.join(5)
                    if worker.is_alive():
                        raise RuntimeError("Search listener thread did not stop")
                if backend:
                    backend.close()
            except (Exception, KeyboardInterrupt) as error:
                if isinstance(error, KeyboardInterrupt):
                    retain_interruption(record, error)
                record.update(shutdown_error=f"{type(error).__name__}: {error}", failed=True,
                              lifecycle_qualified=False)
            finally:
                if owner:
                    try:
                        owner.close()
                        record["qwen_stop"] = owner.stop_receipt
                        record["qwen_final_receipts"] = getattr(owner, "receipts", {})
                        stop = record["qwen_stop"]
                        if not isinstance(stop, dict) or stop.get("owned_group_stopped") is not True:
                            record.update(failed=True, lifecycle_qualified=False,
                                          qwen_shutdown_error="Owned Qwen process-group shutdown is unproved")
                        elif stop.get("forced_kill") or stop.get("errors") or stop.get("exit_code") != 0:
                            record.update(failed=True, qwen_shutdown_error="Owned Qwen process stopped without clean completion")
                        else:
                            final = record["qwen_final_receipts"]
                            if (final.get("auth_token_removed") is not True
                                    or final.get("private_cleanup_errors")
                                    or final.get("worker_stop", {}).get("clean_stop") is not True):
                                record.update(failed=True, qwen_shutdown_error="Owned Qwen private/native cleanup is not clean")
                    except (Exception, KeyboardInterrupt) as error:
                        if isinstance(error, KeyboardInterrupt):
                            retain_interruption(record, error)
                        record.update(qwen_shutdown_error=f"{type(error).__name__}: {error}", failed=True,
                                      lifecycle_qualified=False)
            record["phase_timestamps"]["shutdown_end"] = mf.stamp()
            # Preserve known usage/timing before export or receipt parsing can fail.
            write(public / "result.json", public_record(record, substitutions))
            try:
                record["artifact_sha256"] = mf.public_artifacts(raw_dir, public, events, observed, substitutions)
                receipts = ([json.loads(line) for line in (raw_dir / "http-receipts.jsonl").read_text().splitlines()]
                            if (raw_dir / "http-receipts.jsonl").exists() else [])
                record["successful_qwen_searches"] = sum(bool(row.get("success") and row.get("response", {}).get("rerank")
                    and row.get("response", {}).get("rerank_provider") == "qwen") for row in receipts)
                if owner is not None:
                    record["qwen_evidence"] = publish_qwen_evidence(owner, public, substitutions)
            except (Exception, KeyboardInterrupt) as error:
                if isinstance(error, KeyboardInterrupt):
                    retain_interruption(record, error)
                record.update(publication_error=f"{type(error).__name__}: {error}", failed=True,
                              successful_qwen_searches=None)
            write(public / "result.json", public_record(record, substitutions))
    if record.get("interrupted"):
        record["grading_unstarted_reason"] = "Attempt interrupted; preserve evidence and stop remaining schedule"
    elif workspace.is_dir() and "base_tree_sha256" in record:
        try:
            record["phase_timestamps"]["grading_start"] = mf.stamp()
            record["grade"] = grade(task, workspace)
            record["compatibility"] = mf.compatibility(task, workspace, base_files, raw_dir)
            record["correct"] = record["grade"]["passed"] and record["compatibility"]["passed"]
            record["phase_timestamps"]["grading_end"] = mf.stamp()
            mf.old.git(workspace, "add", "--intent-to-add", ".")
            (public / "changes.patch").write_text(mf.public_redact(
                mf.old.git(workspace, "diff", "--binary", base_commit).stdout.decode(), substitutions))
            record["changed_files"] = mf.old.git(workspace, "diff", "--name-only", base_commit).stdout.decode().splitlines()
            record["patch_sha256"] = sha(public / "changes.patch")
        except (Exception, KeyboardInterrupt) as error:
            if isinstance(error, KeyboardInterrupt):
                retain_interruption(record, error)
            record.update(grading_error=f"{type(error).__name__}: {error}", correct=False)
    record["completed_correct"] = evidence.complete(record) and record["correct"]
    record["phase_timestamps"]["trial_end"] = mf.stamp()
    record["full_trial_elapsed_s"] = (record["phase_timestamps"]["trial_end"]["monotonic_s"]
        - record["phase_timestamps"]["fixture_start"]["monotonic_s"])
    try:
        record["clock_end"] = clock.check("trial-end", since=record["clock_start"]["sample"])
    except ClockIntegrityError as error:
        record.update(timing_qualified=False, clock_failure=error.receipt)
    except KeyboardInterrupt as error:
        retain_interruption(record, error)
    write(public / "result.json", public_record(record, substitutions))
    return public_record(record, substitutions)


def run(output):
    config_path = output / "frozen-config.json"
    config = read(config_path)
    if (output / "runs").exists():
        raise ValueError("No automatic retries or resume")
    mf.verify_runtime(config)
    verify_native_runtime(config)
    manifest = validate_manifest(Path(config["manifest"]))
    tasks = {task["id"]: task for task in manifest["tasks"]}
    frozen_hash = sha(config_path)
    clock = ClockIntegrity()
    write(output / "run-start.json", {"started": mf.stamp(), "clock": clock.baseline,
                                      "frozen_config_sha256": frozen_hash})
    records = []
    for number, trial in enumerate(config["schedule"], 1):
        mf.verify_runtime(config)
        verify_native_runtime(config)
        if sha(config_path) != frozen_hash or sha(config["reranker_python"]) != config["reranker_python_sha256"]:
            raise ValueError("Frozen runtime changed during study")
        try:
            record = run_trial(tasks[trial["task_id"]], trial, number, config, output, clock)
        except (Exception, KeyboardInterrupt) as error:
            name = f"{number:02d}-r{trial['repetition']}-{trial['task_id']}-{trial['condition']}"
            directory = output / "runs" / name
            directory.mkdir(parents=True, exist_ok=True)
            retained = read(directory / "result.json") if (directory / "result.json").exists() else {}
            record = {"run_id": name, **trial, "correct": False, "completed_correct": False,
                      "completed": False, "failed": True, "usage": None, "mcp_calls": 0,
                      "agent_elapsed_s": None, "timing_qualified": True, "lifecycle_qualified": False, **retained,
                      "infrastructure_error": f"{type(error).__name__}: {error}"}
            record["failed"] = True
            record["completed_correct"] = False
            if isinstance(error, ClockIntegrityError):
                record.update(timing_qualified=False, clock_failure=error.receipt)
            if isinstance(error, KeyboardInterrupt):
                retain_interruption(record, error)
            write(directory / "result.json", record)
        records.append(record)
        write(output / "results.json", records)
        write(output / "summary.json", evidence.summarize(records, config["schedule"]))
        print(f"Finished {record['run_id']}: correct={record['correct']}, "
              f"completed_correct={record['completed_correct']}, usage={record.get('usage')}", flush=True)
        if record.get("interrupted"):
            raise RuntimeError("Interrupted attempt preserved: remaining planned attempts remain unstarted")
        if not record["timing_qualified"]:
            raise RuntimeError("Clock failure: remaining planned attempts remain unstarted")
        if not record.get("lifecycle_qualified"):
            raise RuntimeError("Unproved model/listener cleanup: remaining planned attempts remain unstarted")
    mf.verify_runtime(config)
    verify_native_runtime(config)
    write(output / "completion.json", {"completed": mf.stamp(), "recorded_attempts": len(records),
        "clock": clock.check("study-complete"), "frozen_config_sha256": frozen_hash})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("validate", "freeze-common", "probe", "freeze", "run"))
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--common-config", type=Path)
    parser.add_argument("--probe-result", type=Path)
    parser.add_argument("--development-decision", type=Path)
    parser.add_argument("--fresh-study", type=Path)
    parser.add_argument("--production-parity", type=Path)
    parser.add_argument("--production-smoke", type=Path)
    args = parser.parse_args(argv)
    if args.action == "validate":
        validate_manifest(args.manifest)
    elif args.action == "freeze-common":
        if not all((args.development_decision, args.fresh_study, args.production_parity, args.production_smoke)):
            parser.error("freeze-common requires --development-decision, --fresh-study, --production-parity and --production-smoke")
        freeze_common(args.output.resolve(), args.development_decision.resolve(), args.fresh_study.resolve(),
                      args.production_parity.resolve(), args.production_smoke.resolve())
    elif args.action in ("probe", "freeze"):
        if not args.common_config or not args.work_root:
            parser.error("probe/freeze require --common-config and --work-root")
        if args.action == "probe":
            mf.probe(args.common_config.resolve(), args.output.resolve(), args.work_root.resolve())
        else:
            if not args.probe_result:
                parser.error("freeze requires --probe-result")
            freeze(args.output.resolve(), args.work_root.resolve(), args.common_config.resolve(),
                   args.probe_result.resolve(), args.manifest)
    else:
        with process_capture.guarded_sigterm():
            run(args.output.resolve())


if __name__ == "__main__":
    main()
