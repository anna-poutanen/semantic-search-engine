#!/usr/bin/env python3
"""Post-outcome diagnostic-wording recheck; original study sources stay frozen.

The first observed audit stopped because two independently rejected None-feature
captures used different diagnostic strings. This correction adopts the already
frozen audit_semantic_context.verify_record precedent, only for intentionally
inactive cases. Every selector decision, source, feature hash, payload, counter
and quality gate remains subject to the original strict study audit.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import audit_semantic_study as study

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "semantic_study_auditor_recheck_v1"
ORIGINAL_STUDY_FREEZE = "cca82913ef67bf41d565f6424b9619fec8270edc"
PLAN = "results/semantic-model-development-2026-11-02/auditor-recheck-plan.json"
SOURCES = ("scripts/recheck_observed_model_study.py", "tests/test_observed_model_study_recheck.py")
PRECEDENT = "scripts/audit_semantic_context.py"
ORIGINAL_AUDIT_OUTCOMES = study.audit_outcomes
ORIGINAL_AUDIT = study.audit


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def normalize_inactive_diagnostics(cases, outcomes, capture, feature_requests, *, counter):
    """Copy audit operands and normalize only independently rejected diagnostics."""
    by_id = study._unique(cases, "case_id")
    requests = study._unique(feature_requests["cases"], "case_id")
    copied, normalizations = deepcopy(outcomes), []
    for row in copied:
        actual = row.get("semantic_receipt")
        if not isinstance(actual, dict) or actual.get("fallback_reason") != "invalid_features":
            continue
        identity = row.get("case_id")
        require(identity in by_id and identity in requests, "Unknown intentionally inactive case")
        case, request = by_id[identity], requests[identity]
        require(case.get("feature_request_id") is None and request.get("active") is False
                and request.get("query_request_id") is None,
                "Diagnostic correction requires an intentionally inactive feature request")
        features = study.features_for_case(case, capture, request)
        require(features is None, "Diagnostic correction cannot alter an active feature capture")
        _, independent = study.context.reconstruct(case["reference_response"], case["raw_rows"], features,
            definition_keys=case["definition_keys"], precoalesce_rows=case["precoalesce_rows"], counter=counter, **case["limits"])
        require(independent.get("fallback_reason") == "invalid_features",
                "Both independent decisions must reject invalid features")
        original_message, independent_message = actual.get("feature_error"), independent.get("feature_error")
        require(type(original_message) is str and bool(original_message)
                and type(independent_message) is str and bool(independent_message),
                "Both invalid-feature diagnostics must be nonempty strings")
        if original_message == independent_message:
            continue
        actual["feature_error"] = independent_message
        # Do not accept any decision or guard discrepancy along with wording.
        require(study.same(actual, independent), "Diagnostic wording cannot conceal a changed selector decision")
        normalizations.append({"case_id": identity, "key": deepcopy(case["key"]),
            "actual_message": original_message, "independent_message": independent_message,
            "fallback_reason": "invalid_features", "features": None, "feature_request_active": False,
            "modified_path": "semantic_receipt.feature_error"})
    return copied, normalizations


def recheck_outcomes(cases, outcomes, capture, feature_requests, *, counter, recorder=None, **kwargs):
    copied, normalizations = normalize_inactive_diagnostics(cases, outcomes, capture, feature_requests, counter=counter)
    # The unchanged frozen auditor checks every field other than the documented
    # diagnostic copy above, including all individual evidence losses.
    result = ORIGINAL_AUDIT_OUTCOMES(cases, copied, capture, feature_requests, counter=counter, **kwargs)
    if recorder is not None:
        recorder.extend(normalizations)
    return result


def verify_recheck_freeze(root, audit_freeze, *, started_at, original_freeze=ORIGINAL_STUDY_FREEZE):
    """Require the correction, tests and plan to be committed before rechecking."""
    root = Path(root).resolve()
    require(type(audit_freeze) is str and re.fullmatch(r"[0-9a-f]{40}", audit_freeze), "--audit-freeze must be a full Git SHA")
    require(subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", audit_freeze, "HEAD"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0, "Correction freeze must be an ancestor of HEAD")
    committed = datetime.fromisoformat(git(root, "show", "-s", "--format=%cI", audit_freeze).decode().strip())
    require(committed <= datetime.fromisoformat(started_at), "Correction commit must precede this recheck")
    plan_path = root / PLAN
    raw_plan = plan_path.read_bytes()
    plan = json.loads(raw_plan)
    require(plan.get("schema") == SCHEMA and plan.get("original_study_freeze") == original_freeze,
            "Original experiment identity or post-outcome correction plan differs")
    require(isinstance(plan.get("source_sha256"), dict) and set(plan["source_sha256"]) == set(SOURCES),
            "Correction source and test closure must be exact")
    require(type(plan.get("expected_normalizations")) is int and plan["expected_normalizations"] >= 0,
            "Known post-outcome normalization count required")
    failure = plan.get("original_audit_failure")
    require(isinstance(failure, dict) and isinstance(failure.get("path"), str), "Original failed audit must remain bound")
    failure_path = (root / failure["path"]).resolve()
    require(failure_path.is_relative_to(root) and failure_path.is_file(), "Original failure log must stay inside project")
    bound = {**plan["source_sha256"], PLAN: sha(raw_plan), failure["path"]: failure.get("sha256")}
    for name, digest in bound.items():
        path = (root / name).resolve()
        require(path.is_relative_to(root) and re.fullmatch(r"[0-9a-f]{64}", digest or "")
                and sha(path.read_bytes()) == digest
                and all(sha(git(root, "show", commit + ":" + name)) == digest for commit in (audit_freeze, "HEAD")),
                "Committed correction source/test/plan/failure bytes changed: " + name)
    frozen_sources = {}
    for name in ("scripts/audit_semantic_study.py", PRECEDENT):
        original = git(root, "show", original_freeze + ":" + name)
        digest = sha(original)
        require(sha((root / name).read_bytes()) == sha(git(root, "show", "HEAD:" + name)) == digest,
                "Original frozen auditor/precedent must remain unchanged")
        frozen_sources[name] = digest
    return {"schema": SCHEMA, "audit_freeze": audit_freeze, "audit_execution_commit": git(root, "rev-parse", "HEAD").decode().strip(),
            "started_at": started_at, "plan_path": PLAN, "plan_sha256": sha(raw_plan),
            "source_sha256": plan["source_sha256"], "original_study_freeze": original_freeze,
            "original_audit_failure": failure, "expected_normalizations": plan["expected_normalizations"],
            "original_frozen_auditor_sha256": frozen_sources}


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--audit-freeze", required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    args, remaining = parser.parse_known_args(sys.argv[1:] if argv is None else argv)
    require("--check-controls" not in remaining, "Post-outcome correction requires the complete original study audit")
    root = args.root.resolve()
    require(Path(__file__).resolve() == root / SOURCES[0]
            and Path(study.__file__).resolve() == root / "scripts/audit_semantic_study.py"
            and Path(study.context.__file__).resolve() == root / PRECEDENT,
            "Executing correction and frozen auditor must come from the bound project")
    started_at = datetime.now(timezone.utc).isoformat()
    proof = verify_recheck_freeze(root, args.audit_freeze, started_at=started_at)
    normalizations = []
    old_outcomes, old_audit, old_argv = study.audit_outcomes, study.audit, sys.argv

    def outcomes(*values, **kwargs):
        return recheck_outcomes(*values, recorder=normalizations, **kwargs)

    def complete(*values, **kwargs):
        result = ORIGINAL_AUDIT(*values, **kwargs)
        require(result["producer_freeze"]["commit"] == ORIGINAL_STUDY_FREEZE, "Original study freeze changed")
        require(len(normalizations) == proof["expected_normalizations"]
                and len({row["case_id"] for row in normalizations}) == len(normalizations),
                "Known diagnostic-only normalization class differs")
        result["post_outcome_auditor_correction"] = {**proof, "classification": "Explicit post-outcome audit diagnostic correction",
            "policy": "Only copied nonempty feature_error wording for intentional inactive None features when both reject invalid_features",
            "precedent": {"path": PRECEDENT, "function": "verify_record", "freeze": ORIGINAL_STUDY_FREEZE,
                          "sha256": proof["original_frozen_auditor_sha256"][PRECEDENT]},
            "normalization_count": len(normalizations), "normalizations": normalizations,
            "frozen_files_modified": False, "producer_outcomes_recomputed": False,
            "quality_gate_changed": False, "model_calls": 0, "retrieval_calls": 0, "attrs_reads": 0}
        return result

    try:
        study.audit_outcomes, study.audit = outcomes, complete
        sys.argv = [str(Path(study.__file__)), "--root", str(args.root), *remaining]
        # Preserve the frozen main's producer-import/network guards, tokenizer,
        # exclusive output, full model/source/freeze audit and quality checks.
        study.main()
    finally:
        study.audit_outcomes, study.audit, sys.argv = old_outcomes, old_audit, old_argv


if __name__ == "__main__":
    main()
