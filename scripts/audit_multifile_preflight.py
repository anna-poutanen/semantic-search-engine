#!/usr/bin/env python3
"""Independent CPU-only preflight of explicit multifile feature contracts.

Builds pristine fixtures from pinned Git archives, applies the withheld reference
patches only in ignored evaluator copies, and runs baseline/reference, original
upstream, single-file, and omit-layer gates. No model, network service, dependency
installation, repository mutation, or reference implementation publication.
Loopback-only fixtures inside the authored behavioral checks are allowed.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks/coding_workflows_multifile.json"
DEFAULT_OUTPUT = ROOT / "results/multifile-preflight-2026-10-13"
DEFAULT_WORK = ROOT / ".cache/multifile-independent-preflight"
PYTEST_ADDOPTS = ('--tb=short -W "ignore:The anyio.abc.BlockingPortal alias is deprecated:DeprecationWarning" '
                  '-W "ignore:The `cancellable=` keyword argument:DeprecationWarning"')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def environment(workspace, source_root):
    env = dict(os.environ)
    env["PYTHONPATH"] = str((workspace / source_root).parent)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTEST_ADDOPTS"] = PYTEST_ADDOPTS
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    return env


def execute(command, *, workspace, source_root, timeout, log_base, parse_detail=False):
    started = time.monotonic()
    try:
        result = subprocess.run(command, cwd=workspace, env=environment(workspace, source_root),
                                capture_output=True, text=True, timeout=timeout)
        stdout, stderr, exit_code, timed_out = result.stdout, result.stderr, result.returncode, False
    except subprocess.TimeoutExpired as error:
        stdout = (error.stdout or b"").decode() if isinstance(error.stdout, bytes) else error.stdout or ""
        stderr = (error.stderr or b"").decode() if isinstance(error.stderr, bytes) else error.stderr or ""
        exit_code, timed_out = None, True
    log_base.parent.mkdir(parents=True, exist_ok=True)
    stdout_path, stderr_path = log_base.with_suffix(".stdout"), log_base.with_suffix(".stderr")
    stdout_path.write_text(stdout)
    stderr_path.write_text(stderr)
    detail = None
    if parse_detail:
        try:
            detail = json.loads(stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            pass
    labels = sorted(set(re.findall(r"^(?:ERROR|FAIL): (test_[^\n]+)", stderr, re.M)))
    summary = next((line for line in reversed(stdout.strip().splitlines()) if line.strip()), "")
    pytest_counts = {name: 0 for name in ("passed", "failed", "skipped", "xfailed", "xpassed", "errors", "deselected")}
    for count, name in re.findall(r"\b(\d+) (passed|failed|skipped|xfailed|xpassed|errors?|deselected)\b", summary):
        pytest_counts["errors" if name == "error" else name] = int(count)
    return {"exit_code": exit_code, "timed_out": timed_out,
            "elapsed_s": round(time.monotonic() - started, 6), "detail": detail,
            "failure_labels": labels,
            "summary": summary, "pytest_counts": pytest_counts,
            "stdout": str(stdout_path.relative_to(ROOT)), "stdout_sha256": digest(stdout_path),
            "stderr": str(stderr_path.relative_to(ROOT)), "stderr_sha256": digest(stderr_path)}


def reset_copy(source, destination):
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))


def inventory(root, task):
    return {path: digest(root / path) if (root / path).is_file() else None
            for path in task["upstream_test_sha256"]}


def semantic_probes(task, reference, work, output, command):
    """Check stated behavior beyond file-layer linkage, including a valid alternative."""
    probes = []
    if task["id"] == "requests_ndjson":
        probes.append(("eager_ndjson_buffering", "src/requests/models.py",
                       "for chunk in self.iter_content(chunk_size=chunk_size, decode_unicode=False):",
                       "for chunk in [self.content]:", False,
                       "test_laziness_and_late_error"))
    elif task["id"] == "requests_response_limit":
        probes.append(("per_iterator_budget_reset", "src/requests/models.py",
                       'received = (getattr(self, "_decoded_bytes_received", 0) if live_stream else received) + len(chunk)',
                       "received = received + len(chunk)", False,
                       "test_live_budget_cannot_be_reset_by_another_iterator_or_content"))
        probes.append(("old_iterator_reads_after_crossing", "src/requests/models.py",
                       '                    if getattr(self, "_response_limit_error", None) is not None:\n                        raise self._response_limit_error',
                       "                    if False:\n                        raise self._response_limit_error", False,
                       "test_live_budget_cannot_be_reset_by_another_iterator_or_content"))
    elif task["id"] == "click_atomic_transactions":
        probes.append(("discard_exception_context", "src/click/core.py",
                       "stack.__exit__(None if successful_exit else exc_type,\n                               None if successful_exit else exc_value,\n                               None if successful_exit else tb)",
                       "stack.__exit__(None, None, None)", False,
                       "test_context_resources_receive_error_and_context_stack_is_restored"))
        probes.append(("equivalent_atomic_temporary_prefix", "src/click/_compat.py",
                       'f".__atomic-write{random.randrange(1 << 32):08x}"',
                       'f".__transaction-write{random.randrange(1 << 32):08x}"', True, None))
        probes.append(("equivalent_atomic_remove_api", "src/click/_compat.py",
                       "os.unlink(self._tmp_filename)", "os.remove(self._tmp_filename)", True, None))
    elif task["id"] == "starlette_body_limits":
        probes.append(("eager_unlimited_middleware_buffering", "starlette/middleware/body_limit.py",
                       'if scope["type"] != "http" or self.max_bytes is None:',
                       'if scope["type"] != "http":', False,
                       "test_none_limit_does_not_buffer_before_calling_application"))
        probes.append(("equivalent_multichunk_replay", "starlette/middleware/body_limit.py",
                       '''        delivered = False
        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()''',
                       '''        delivered = False
        offset = 0
        async def replay():
            nonlocal delivered, offset
            if not delivered:
                chunk = body[offset:offset + 1]
                offset += len(chunk)
                delivered = offset >= len(body)
                return {"type": "http.request", "body": chunk, "more_body": not delivered}
            return await receive()''', True, None))
    outcomes = []
    for label, path, old_text, new_text, expected_pass, failure_label in probes:
        fixture = work / label
        reset_copy(reference, fixture)
        source = fixture / path
        text = source.read_text()
        if text.count(old_text) != 1:
            raise ValueError(f"Semantic probe anchor changed: {task['id']}:{label}")
        source.write_text(text.replace(old_text, new_text))
        item = execute(command + [str(fixture)], workspace=fixture, source_root=task["source_root"],
                       timeout=task["verification"]["timeout_s"], log_base=output / label, parse_detail=True)
        observed_pass = item["exit_code"] == 0 and item["detail"] is not None and item["detail"]["passed"] is True
        named_failure = failure_label is None or any(failure_label in x for x in item["failure_labels"])
        item.update(probe=label, production_file=path, expected_pass=expected_pass,
                    expected_failure_label=failure_label,
                    matched=observed_pass == expected_pass and item["detail"] is not None
                            and not item["timed_out"] and named_failure)
        outcomes.append(item)
    return outcomes


def audit_task(task, args):
    task_id = task["id"]
    corpus = ROOT / task["corpus"]
    work = args.work_root / task_id
    output = args.output / "logs" / task_id
    reference_patch = ROOT / ".cache/multifile-reference" / task_id / "reference.patch"
    authored_reference = reference_patch.parent / "reference"
    checker = ROOT / task["verification"]["script"]
    expected = task["upstream_test_sha256"]
    initial_hashes = {"checker": digest(checker), "helper": digest(ROOT / task["verification"]["shared_helper"]),
                      "reference_patch": digest(reference_patch)}
    if initial_hashes["checker"] != task["verification"]["sha256"]:
        raise ValueError(f"Unrefreshed checker manifest: {task_id}")
    if initial_hashes["reference_patch"] != task["reference_patch_sha256"]:
        raise ValueError(f"Unrefreshed reference manifest: {task_id}")
    if initial_hashes["helper"] != task["verification"]["shared_helper_sha256"]:
        raise ValueError(f"Unrefreshed helper manifest: {task_id}")
    revision = subprocess.check_output(["git", "-C", str(corpus), "rev-parse", "HEAD"], text=True).strip()
    if revision != task["revision"] or task["preparation_patch"] is not None:
        raise ValueError(f"Unpinned or prepared corpus: {task_id}")
    baseline = work / "baseline"
    if baseline.exists():
        shutil.rmtree(baseline)
    baseline.mkdir(parents=True)
    archive = subprocess.check_output(["git", "-C", str(corpus), "archive", task["revision"]])
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        stream.extractall(baseline, filter="data")
    reference = work / "reference"
    reset_copy(baseline, reference)
    subprocess.run(["git", "apply", str(reference_patch)], cwd=reference, check=True, capture_output=True)
    files = task["reference_production_files"]
    source_paths = set(path.relative_to(baseline) for path in (baseline / task["source_root"]).rglob("*.py"))
    source_paths.update(path.relative_to(reference) for path in (reference / task["source_root"]).rglob("*.py"))
    observed_changed_files = sorted(str(path) for path in source_paths
                                    if ((baseline / path).read_bytes() if (baseline / path).is_file() else None)
                                    != ((reference / path).read_bytes() if (reference / path).is_file() else None))
    if observed_changed_files != sorted(files):
        raise ValueError(f"Reference changed-file inventory differs: {task_id}")
    reference_matches_author = all(digest(reference / name) == digest(authored_reference / name) for name in files)
    if not reference_matches_author or len(files) < task["minimum_production_files"]:
        raise ValueError(f"Reference reconstruction differs: {task_id}")
    tests_before = {"corpus": inventory(corpus, task), "authored_reference": inventory(authored_reference, task),
                    "baseline": inventory(baseline, task), "reference": inventory(reference, task)}
    if any(hashes != expected for hashes in tests_before.values()):
        raise ValueError(f"Original test/config bytes differ before checks: {task_id}")
    command = [str(Path(sys.executable).absolute()), str(checker), "--repo"]
    records = {}
    import_origins = []
    for label, fixture in (("baseline", baseline), ("reference", reference)):
        package = task["corpus_id"]
        import_check = (
            f"import {package}; from pathlib import Path; "
            f"p=Path({package}.__file__).resolve(); print(p); "
            f"assert p.is_relative_to(Path({str(fixture / task['source_root'])!r}).resolve())"
        )
        origin = execute([command[0], "-c", import_check], workspace=fixture,
                         source_root=task["source_root"], timeout=30, log_base=output / f"{label}-import-origin")
        origin["fixture"] = label
        import_origins.append(origin)
        records[label] = execute(command + [str(fixture)], workspace=fixture, source_root=task["source_root"],
                                 timeout=task["verification"]["timeout_s"], log_base=output / label, parse_detail=True)
    compatibility = []
    for label, fixture in (("baseline", baseline), ("reference", reference)):
        for index, selection in enumerate(task["compatibility_commands"]):
            item = execute([str(Path(sys.executable).absolute()), *selection["args"]],
                           workspace=fixture, source_root=task["source_root"], timeout=selection["timeout_s"],
                           log_base=output / f"{label}-upstream-{index}")
            expected_summary = selection.get("expected_summary")
            counts_match = isinstance(expected_summary, dict) and all(
                item["pytest_counts"].get(name) == value for name, value in expected_summary.items()
            )
            item.update(fixture=label, command_args=selection["args"],
                        expected_summary=expected_summary, counts_match=counts_match)
            compatibility.append(item)
    mutants = []
    for mode in ("single_file", "omit_one_layer"):
        for index, name in enumerate(files):
            fixture = work / f"{mode}-{index}"
            reset_copy(baseline if mode == "single_file" else reference, fixture)
            donor = reference if mode == "single_file" else baseline
            target = fixture / name
            if (donor / name).is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(donor / name, target)
            elif target.exists():
                target.unlink()
            item = execute(command + [str(fixture)], workspace=fixture, source_root=task["source_root"],
                           timeout=task["verification"]["timeout_s"],
                           log_base=output / f"{mode}-{index}", parse_detail=True)
            item.update(mode=mode, production_file=name,
                        rejected=item["exit_code"] not in (0, None) and not item["timed_out"],
                        rejection_kind="behavioral_oracle" if item["detail"] is not None else "incomplete_module_import")
            mutants.append(item)
    semantic = semantic_probes(task, reference, work, output, command)
    tests_after = {"corpus": inventory(corpus, task), "authored_reference": inventory(authored_reference, task),
                   "baseline": inventory(baseline, task), "reference": inventory(reference, task)}
    final_hashes = {"checker": digest(checker), "helper": digest(ROOT / task["verification"]["shared_helper"]),
                    "reference_patch": digest(reference_patch)}
    baseline_red = records["baseline"]["exit_code"] == 1 and records["baseline"]["detail"]["passed"] is False
    reference_green = records["reference"]["exit_code"] == 0 and records["reference"]["detail"]["passed"] is True
    return {"task_id": task_id, "revision": revision, "preparation_patch": None,
            "production_files": files, "reconstructed_reference_matches_author": reference_matches_author,
            "original_test_and_config_files": len(expected),
            "original_bytes_unchanged": tests_before == tests_after and all(x == expected for x in tests_after.values()),
            "provenance_stable": initial_hashes == final_hashes,
            "artifact_sha256": initial_hashes, "baseline_missing_feature": records["baseline"],
            "reference": records["reference"], "import_origins": import_origins,
            "upstream_compatibility": compatibility,
            "partial_mutations": mutants, "semantic_probes": semantic,
            "passed": baseline_red and reference_green and tests_before == tests_after and initial_hashes == final_hashes
                      and all(item["exit_code"] == 0 and item["counts_match"] for item in compatibility)
                      and all(item["exit_code"] == 0 for item in import_origins)
                      and all(item["rejected"] for item in mutants)
                      and all(item["matched"] for item in semantic)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK)
    args = parser.parse_args()
    args.manifest = args.manifest.resolve()
    args.output = args.output.resolve()
    args.work_root = args.work_root.resolve()
    if not args.work_root.is_relative_to(ROOT / ".cache") or not args.output.is_relative_to(ROOT):
        parser.error("Evaluator copies must be inside ignored .cache; outputs must be inside this project")
    manifest_hash = digest(args.manifest)
    manifest = json.loads(args.manifest.read_text())
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks = list(pool.map(lambda task: audit_task(task, args), manifest["tasks"]))
    versions = {name: importlib.metadata.version(name) for name in
                ("pytest", "pytest-httpbin", "pytest-mock", "anyio", "trio", "httpx", "urllib3")}
    data = {"schema_version": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
            "scope": "Independent CPU-only evaluator preflight; no hosted/GPU calls",
            "method": "Pristine pinned git archive, apply ignored reference patch, execute all gates in separate copies",
            "manifest": str(args.manifest.relative_to(ROOT)), "manifest_sha256": manifest_hash,
            "manifest_stable": manifest_hash == digest(args.manifest),
            "script_sha256": digest(__file__), "python": sys.version, "package_versions": versions,
            "pytest_addopts": PYTEST_ADDOPTS, "tasks": tasks,
            "summary": {"baseline_feature_gates_rejected": sum(t["baseline_missing_feature"]["exit_code"] == 1 for t in tasks),
                        "reference_feature_methods_passed": sum(t["reference"]["detail"]["tests_run"] for t in tasks),
                        "upstream_selections_passed": sum(r["exit_code"] == 0 for t in tasks for r in t["upstream_compatibility"]),
                        "partial_mutations_rejected": sum(r["rejected"] for t in tasks for r in t["partial_mutations"]),
                        "partial_mutations_total": sum(len(t["partial_mutations"]) for t in tasks),
                        "semantic_probe_outcomes_matched": sum(r["matched"] for t in tasks for r in t["semantic_probes"]),
                        "semantic_probes_total": sum(len(t["semantic_probes"]) for t in tasks),
                        "all_passed": all(t["passed"] for t in tasks) and manifest_hash == digest(args.manifest)}}
    write_json(args.output / "audit.json", data)
    lines = ["# Independent multifile preflight", "", "CPU-only checks reconstructed each pristine pinned checkout from Git and applied only the ignored reference patch in evaluator copies. No model or GPU calls were made.", "",
             "| Task | Reference methods | Partial repairs rejected | Original baseline/reference suites | Test/config bytes unchanged |",
             "| --- | ---: | ---: | --- | --- |"]
    for task in tasks:
        lines.append(f"| {task['task_id']} | {task['reference']['detail']['tests_run']} | {sum(r['rejected'] for r in task['partial_mutations'])}/{len(task['partial_mutations'])} | " +
                     "; ".join(r["fixture"] + ": " + r["summary"] for r in task["upstream_compatibility"]) + f" | {task['original_bytes_unchanged']} |")
    lines.extend(["", "Single-file mutants apply exactly one reference production file to pristine upstream. Omit-layer mutants restore exactly one changed file to upstream, deleting a newly added module when appropriate. Rejections include incomplete imports as well as behavioral failures; detailed categories and original transcripts are retained in audit.json and logs.", "",
                  "The baseline gate failures are expected missing-feature outcomes. This preflight establishes the selected contract and original-suite gates; it is not a proof of all library behavior or an independent repository holdout.", "",
                  f"All preflight gates passed: **{data['summary']['all_passed']}**. Frozen task assets were stable during audit: **{data['manifest_stable'] and all(t['provenance_stable'] for t in tasks)}**.", ""])
    (args.output / "findings.md").write_text("\n".join(lines))
    print(json.dumps(data["summary"], indent=2))
    return 0 if data["summary"]["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
