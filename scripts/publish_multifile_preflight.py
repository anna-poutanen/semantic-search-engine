#!/usr/bin/env python3
"""Redact preflight publication paths without rerunning any evaluation.

Preserve original artifacts and their hashes in ignored evaluator storage, replace
project/home paths in public transcripts, and refresh all dependent hash links.
No hosted calls, GPU calls, or mutable agent checkout access.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "results/multifile-preflight-2026-10-13"
PRESERVED = ROOT / ".cache/publication-preserved/multifile-preflight-2026-10-13"
EXECUTION_SCRIPTS = ROOT / ".cache/publication-preserved/multifile-preflight-execution-scripts-2026-10-13"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def redact(value):
    if isinstance(value, str):
        return value.replace(str(ROOT), "<PROJECT>").replace(str(Path.home()), "<HOME>")
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()}
    return value


def refresh_logs(value):
    if isinstance(value, dict):
        if isinstance(value.get("stdout"), str) and "stdout_sha256" in value:
            value["original_stdout_sha256"] = value["stdout_sha256"]
            value["stdout_sha256"] = sha(ROOT / value["stdout"])
        if isinstance(value.get("stderr"), str) and "stderr_sha256" in value:
            value["original_stderr_sha256"] = value["stderr_sha256"]
            value["stderr_sha256"] = sha(ROOT / value["stderr"])
        for item in value.values():
            refresh_logs(item)
    elif isinstance(value, list):
        for item in value:
            refresh_logs(item)


def main():
    if not PRESERVED.exists():
        PRESERVED.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(PUBLIC, PRESERVED)
    originals = {str(path.relative_to(PRESERVED)): sha(path) for path in PRESERVED.rglob("*") if path.is_file()}
    original_audit = read(PRESERVED / "audit.json")
    executed_scripts = {}
    expected_scripts = {"audit_multifile_preflight.py": original_audit["script_sha256"]}
    if (PRESERVED / "transport-review.json").is_file():
        expected_scripts["audit_transport_probe.py"] = read(PRESERVED / "transport-review.json")["evidence_sha256"]["script"]
    EXECUTION_SCRIPTS.mkdir(parents=True, exist_ok=True)
    for name, expected in expected_scripts.items():
        target = EXECUTION_SCRIPTS / name
        if not target.exists():
            shutil.copyfile(ROOT / "scripts" / name, target)
        if sha(target) != expected:
            raise ValueError(f"Original executed script differs: {name}")
        executed_scripts[str(target.relative_to(ROOT))] = expected
    # Rebuild publication from preserved originals for deterministic repeated use.
    for relative in originals:
        source, destination = PRESERVED / relative, PUBLIC / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix in (".stdout", ".stderr", ".md"):
            destination.write_text(redact(source.read_text()))
        elif source.suffix == ".json":
            write(destination, redact(read(source)))
        else:
            shutil.copyfile(source, destination)
    audit = read(PUBLIC / "audit.json")
    refresh_logs(audit)
    audit["publication"] = {"redacted_path_labels": ["<PROJECT>", "<HOME>"],
                            "original_audit_sha256": originals["audit.json"],
                            "original_storage": str(PRESERVED.relative_to(ROOT)),
                            "publication_helper_sha256": sha(__file__),
                            "outcomes_unchanged": audit["summary"] == original_audit["summary"]}
    write(PUBLIC / "audit.json", audit)
    audit_hash = sha(PUBLIC / "audit.json")
    review = read(PUBLIC / "review.json")
    review["original_audit_sha256"] = originals["audit.json"]
    review["audit_sha256"] = audit_hash
    write(PUBLIC / "review.json", review)
    findings = read(PUBLIC / "prefreeze-findings.json")
    for finding in findings["findings"]:
        finding["original_verification_sha256"] = finding["verification_sha256"]
        finding["verification_sha256"] = audit_hash
    write(PUBLIC / "prefreeze-findings.json", findings)
    for path in PUBLIC.rglob("*"):
        if path.is_file() and path.suffix in (".json", ".stdout", ".stderr", ".md"):
            text = path.read_text()
            if str(ROOT) in text or str(Path.home()) in text:
                raise ValueError(f"Unredacted publication path: {path.relative_to(PUBLIC)}")
    published = {str(path.relative_to(PUBLIC)): sha(path) for path in PUBLIC.rglob("*")
                 if path.is_file() and path.name != "publication.json"}
    manifest = {"schema_version": 1, "classification": "Path redaction only; no evaluation rerun or outcome changes",
                "original_storage": str(PRESERVED.relative_to(ROOT)),
                "helper": "scripts/publish_multifile_preflight.py", "helper_sha256": sha(__file__),
                "original_artifact_sha256": originals, "published_artifact_sha256": published,
                "preserved_execution_script_sha256": executed_scripts,
                "audit_outcomes_unchanged": audit["summary"] == original_audit["summary"],
                "path_policy": "Replace exact project and home prefixes with <PROJECT> and <HOME> in public transcripts and strings.",
                "raw_logs_preserved": True, "hosted_gpu_calls": 0, "evaluation_reruns": 0}
    write(PUBLIC / "publication.json", manifest)
    print(json.dumps({"publication_files": len(published), "audit_sha256": audit_hash,
                      "outcomes_unchanged": manifest["audit_outcomes_unchanged"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
