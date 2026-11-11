"""Prospectively frozen replay of one returned-template preservation rule."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json
import re
import subprocess
import time

from experiments.dependency_context import study_inputs as inputs
from experiments.dependency_context import study as durable
from experiments.dependency_context import audit as authority
from scripts.semantic_model_study import compare, evidence
from semantic_search.context import compact_result, serialize_context

ROOT = Path(__file__).resolve().parents[2]
PARENT = "b7bcac5d66e5ae6e3c2798fb5b3703fc06de64bf"
SCHEMA = "template_preservation_study_v1"
CLASSIFICATION = "observed_development_template_preservation"
PREPARED = "results/dependency-context-continuation-2026-11-09/prepared"
DEPENDENCY = "results/dependency-context-continuation-2026-11-09"
ORIGINAL = "results/dependency-context-development-2026-11-09"
PREFLIGHT = "results/provider-native-preflight-2026-11-10"
FIRST_NATIVE = "results/provider-native-observed-2026-11-10"
NATIVE = "results/provider-native-observed-v2-2026-11-10"
GATE = {"zero_individual_losses_against": ["legacy", "coverage", "prior_role"],
        "at_least_one_distinct_new_complete_multi_positive_vs": "prior_role"}
REMAINING = {"native_median_max_ratio_vs_each_control": 1.25, "fresh_codebase_required": True}
OPERANDS = ["reference", "proposal", "source_bundle", "definition_keys"]
NEW_WORK = {name: 0 for name in ("v1_proposals", "model", "index", "retrieval", "coalesce", "backfill",
                                "network", "corpus_modules_imported_or_executed")}
require, same, read, sha, canonical, file_sha = (inputs.require, inputs.same, inputs.read,
    inputs.sha, inputs.canonical, inputs.file_sha)


def utc():
    return datetime.now(timezone.utc).isoformat()


def exact_operand(left, right):
    """Full typed native JSON, including signed zero and mapping order."""
    return same(left, right) and json.dumps(left, ensure_ascii=False, allow_nan=False, separators=(",", ":")) == \
        json.dumps(right, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def git(*args, root=ROOT):
    return inputs.git(root, *args).decode().strip()


def ancestor(first, last, *, root=ROOT):
    require(subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", first, last],
                           capture_output=True).returncode == 0, "Freeze ancestry differs")


def source_authority(root=ROOT):
    durable.source_authority(root)


def source_hashes(root=ROOT):
    root = Path(root)
    paths = set()
    for folder in ("src", "scripts", "experiments", "tests"):
        paths.update((root / folder).rglob("*.py"))
    paths.update(root / name for name in ("docs/template-preservation-protocol.md", "pyproject.toml", "uv.lock",
        "requirements-mac.lock", "results/semantic-model-development-2026-11-02/model-binding.json"))
    return {path.relative_to(root).as_posix(): file_sha(path) for path in sorted(paths)}


def verify_blobs(hashes, commit, *, root=ROOT, current=True):
    require(type(hashes) is dict and hashes, "Complete bound artifact map required")
    for name, digest in hashes.items():
        path = Path(name)
        require(type(name) is str and not path.is_absolute() and ".." not in path.parts
                and type(digest) is str and re.fullmatch(r"[0-9a-f]{64}", digest), "Invalid bound artifact")
        require(sha(inputs.git(root, "show", commit + ":" + name)) == digest,
                "Committed artifact differs: " + name)
        if current:
            require(file_sha(Path(root) / name) == digest, "Current bound artifact differs: " + name)


def merge_hashes(target, values):
    for name, digest in values.items():
        require(name not in target or target[name] == digest, "Conflicting immutable authority: " + name)
        target[name] = digest


def parent_binding(root=ROOT):
    """Current published evidence and earlier source snapshots have distinct authorities."""
    root = Path(root)
    hashes = dict(inputs.parent_binding(root)["sha256"])
    for folder in (ORIGINAL, DEPENDENCY, PREFLIGHT, FIRST_NATIVE, NATIVE):
        for name in git("ls-tree", "-r", "--name-only", PARENT, "--", folder, root=root).splitlines():
            merge_hashes(hashes, {name: sha(inputs.git(root, "show", PARENT + ":" + name))})
    original, continuation = (read(root / folder / "study-freeze.json") for folder in (ORIGINAL, DEPENDENCY))
    first, native = (read(root / folder / "plan.json") for folder in (FIRST_NATIVE, NATIVE))
    for values in (continuation["sources"], continuation["artifacts"],
        read(root / DEPENDENCY / "preflight/failure-authority.json")["original_failure_artifact_sha256"],
        first["parent"]["artifacts"], native["parent"]["artifacts"], native["source_freeze"]["sha256"]):
        merge_hashes(hashes, values)
    historical = [
        {"commit": "82ad69cccfcf41acfbd97962c406861043b23c44", "sha256": original["sources"]},
        {"commit": "aa8e5824a490f963339fc0b8de803c6a8456e9b4", "sha256": continuation["sources"]},
        {"commit": "dab55c239cf3a451ec7c389f0133c84ad7917e22",
         "sha256": read(root / PREFLIGHT / "validation.json")["source_sha256"]},
        {key: first["source_freeze"][key] for key in ("commit", "sha256")},
        {key: native["source_freeze"][key] for key in ("commit", "sha256")}]
    for value in historical:
        verify_blobs(value["sha256"], value["commit"], root=root, current=False)
        ancestor(value["commit"], PARENT, root=root)
    ancestor(PARENT, "HEAD", root=root)
    verify_blobs(hashes, PARENT, root=root)
    verify_blobs(hashes, "HEAD", root=root)
    previous = read(root / DEPENDENCY / "independent-audit.json")
    native_audit = read(root / NATIVE / "independent-audit.json")
    require(previous["checks_passed"] is True
            and read(root / DEPENDENCY / "replay/summary.json")["quality_gate"]["qualified"] is False,
            "Preserve failed dependency quality and its technical audit")
    require(native_audit["checks_passed"] is True and native_audit["verified_requests"] == 336
            and native_audit["verified_warmups"] == 8 and native_audit["exact_fast_original_parity"] is True
            and read(root / NATIVE / "attempt/summary.json")["metrics"]["role_arms"]["fast_role"]
                ["dense_promotion_gate_passed"] is False, "Preserve audited native parity and failed promotion")
    return {"commit": PARENT, "artifacts": dict(sorted(hashes.items())), "historical_sources": historical}


def prepared_binding(root=ROOT):
    root = Path(root)
    paths = [PREPARED + "/" + name for name in (*inputs.ARTIFACTS, "frozen-plan.json")]
    return {"path": PREPARED, "artifacts": {name: file_sha(root / name) for name in paths}}


def prepare(results, source_freeze, *, root=ROOT):
    root, results = Path(root), Path(results)
    source_authority(root)
    require(type(source_freeze) is str and re.fullmatch(r"[0-9a-f]{40}", source_freeze),
            "Full prospective source-freeze SHA required")
    require(results.is_relative_to(root) and results.parent.is_dir() and not results.exists(),
            "Use an exclusive new plan inside checkout")
    sources = source_hashes(root)
    verify_blobs(sources, source_freeze, root=root)
    verify_blobs(sources, "HEAD", root=root)
    ancestor(PARENT, source_freeze, root=root)
    ancestor(source_freeze, "HEAD", root=root)
    parent, prepared = parent_binding(root), prepared_binding(root)
    inputs.load_prepared(root / PREPARED, root=root, verify=True)
    plan = {"schema": SCHEMA, "version": 1, "classification": CLASSIFICATION,
        "frozen_at_utc": utc(), "frozen_from_commit": git("rev-parse", "HEAD", root=root),
        "source_freeze": {"commit": source_freeze, "sha256": sources, "bound_blob_count": len(sources),
            "committed_at_unix": int(git("show", "-s", "--format=%ct", source_freeze, root=root))},
        "parent": parent, "prepared": prepared, "counts": deepcopy(inputs.COUNTS), "limits": deepcopy(inputs.LIMITS),
        "runtime": authority.runtime(root), "gate": deepcopy(GATE), "remaining_gates": deepcopy(REMAINING),
        "candidate": "returned_template_v1", "candidate_operands": list(OPERANDS)}
    results.mkdir(exist_ok=False)
    durable.sync_directory(results.parent)
    durable.write_new(results / "plan.json", plan)
    return plan


def verify_input_audit(audit, plan, plan_sha):
    require(type(audit) is dict and audit.get("schema") == "template_preservation_input_audit_v1"
        and audit.get("status") == "passed" and audit.get("checks_passed") is True
        and audit.get("classification") == CLASSIFICATION and audit.get("plan_sha256") == plan_sha
        and all(same(audit.get(name), plan[name]) for name in ("source_freeze", "parent", "prepared", "counts", "runtime"))
        and same(audit.get("source_only_calls"), inputs.ZERO_CALLS)
        and type(audit.get("new_outcomes_computed")) is int and audit["new_outcomes_computed"] == 0
        and type(audit.get("execution_commit")) is str and re.fullmatch(r"[0-9a-f]{40}", audit["execution_commit"]),
        "Exact independent source-only input barrier required")


def verify_plan(results, *, root=ROOT):
    root, results = Path(root), Path(results)
    source_authority(root)
    plan = read(results / "plan.json")
    require(plan.get("schema") == SCHEMA and type(plan.get("version")) is int and plan["version"] == 1
        and plan.get("classification") == CLASSIFICATION and plan.get("candidate") == "returned_template_v1"
        and plan.get("candidate_operands") == OPERANDS and same(plan.get("gate"), GATE)
        and same(plan.get("remaining_gates"), REMAINING) and same(plan.get("counts"), inputs.COUNTS)
        and same(plan.get("limits"), inputs.LIMITS), "Fixed candidate/scope/gate differs")
    frozen = plan["source_freeze"]
    require(type(frozen.get("commit")) is str and re.fullmatch(r"[0-9a-f]{40}", frozen["commit"])
        and same(frozen.get("sha256"), source_hashes(root)) and type(frozen.get("bound_blob_count")) is int
        and frozen["bound_blob_count"] == len(frozen["sha256"]), "Complete executing source closure differs")
    verify_blobs(frozen["sha256"], frozen["commit"], root=root)
    verify_blobs(frozen["sha256"], "HEAD", root=root)
    ancestor(PARENT, frozen["commit"], root=root)
    ancestor(frozen["commit"], "HEAD", root=root)
    require(type(frozen.get("committed_at_unix")) is int and frozen["committed_at_unix"] ==
        int(git("show", "-s", "--format=%ct", frozen["commit"], root=root))
        <= datetime.fromisoformat(plan["frozen_at_utc"]).timestamp(), "Source-first chronology differs")
    audit_path = results / "input-audit.json"
    audit = read(audit_path)
    verify_input_audit(audit, plan, file_sha(results / "plan.json"))
    hashes = {**frozen["sha256"], **plan["parent"]["artifacts"], **plan["prepared"]["artifacts"],
        (results / "plan.json").relative_to(root).as_posix(): file_sha(results / "plan.json"),
        audit_path.relative_to(root).as_posix(): file_sha(audit_path)}
    input_commit = git("log", "-1", "--format=%H", "--", audit_path.relative_to(root).as_posix(), root=root)
    verify_blobs(hashes, input_commit, root=root)
    verify_blobs(hashes, "HEAD", root=root)
    for first, last in ((frozen["commit"], audit["execution_commit"]),
        (audit["execution_commit"], input_commit), (input_commit, "HEAD")):
        ancestor(first, last, root=root)
    committed = int(git("show", "-s", "--format=%ct", input_commit, root=root))
    reviewed = datetime.fromisoformat(audit["reviewed_at_utc"]).timestamp()
    require(datetime.fromisoformat(plan["frozen_at_utc"]).timestamp() <= reviewed
        and int(reviewed) <= committed <= datetime.now(timezone.utc).timestamp(), "Input-before-execution chronology differs")
    require(not git("status", "--porcelain", "--untracked-files=no", root=root), "Tracked checkout must be clean")
    require(same(plan["parent"], parent_binding(root)) and same(plan["prepared"], prepared_binding(root))
        and same(plan["runtime"], authority.runtime(root)), "Parent, original snapshot or exact runtime differs")
    prepared = inputs.load_prepared(root / PREPARED, root=root, verify=True)
    return plan, prepared, {"commit": input_commit, "committed_at_unix": committed, "bound_blob_count": len(hashes)}


def summarize(outcomes):
    selected = [row for row in outcomes if row["quality"]]
    arms = ("legacy", "coverage", "prior_role")
    losses, gains, metrics, restorations = {arm: [] for arm in arms}, set(), {}, []
    for row in selected:
        for arm in arms:
            change = row["comparisons"][arm]
            if change["group_losses"] or change["complete_loss"] or change["single_file_first_hit_loss"]:
                losses[arm].append({"id": row["id"], "dataset": row["dataset"], "query_id": row["query_id"],
                                   "mode": row["mode"], **change})
        if row["comparisons"]["prior_role"]["complete_gain"]:
            restorations.append({name: row[name] for name in ("id", "dataset", "query_id", "mode", "multi_file")})
            if row["multi_file"]:
                gains.add((row["dataset"], row["query_id"]))
    for arm in (*arms, "candidate"):
        values = [row["scores"][arm] for row in selected]
        metrics[arm] = {"groups": sum(value["covered_groups"] for value in values),
            "complete": sum(value["complete"] for value in values),
            "multi_file_complete": sum(row["multi_file"] and row["scores"][arm]["complete"] for row in selected),
            "single_file_first_hit": sum(value["single_file_first_hit"] for value in values)}
    return {"decisions": len(outcomes), "quality_occurrences": len(selected),
        "distinct_units": len({(row["dataset"], row["query_id"]) for row in selected}), "metrics": metrics,
        "quality_gate": {"qualified": bool(gains) and not any(losses.values()), "individual_losses": losses,
            "distinct_complete_multi_gains_vs_prior_role": [list(pair) for pair in sorted(gains)],
            "fresh_native_required": True, "default_change": False},
        "activity": {"accepted": sum(row["guard_receipt"]["accepted"] for row in outcomes),
            "changed_vs_prior_role": sum(row["changed_vs_prior_role"] for row in outcomes),
            "protected_rows": sum(len(row["guard_receipt"]["protected_ids"]) for row in outcomes),
            "fallback_reasons": dict(Counter(row["guard_receipt"]["fallback_reason"] or "accepted" for row in outcomes))},
        "restorations": restorations}


def replay(prepared, output, *, binding, counter=None, guard=None, final_check=lambda: None):
    if guard is None:
        from experiments.template_preservation.guard import apply_template_guard
        guard = apply_template_guard
    counter = authority.offline_counter(ROOT) if counter is None else counter
    records, operands = (inputs.unique(prepared[name], "id") for name in ("records", "operands"))
    require(list(records) == list(operands), "Complete evaluator/selector operand membership/order required")
    output = Path(output)
    require(output.parent.is_dir(), "Attempt parent must exist")
    output.mkdir(exist_ok=False)
    durable.sync_directory(output.parent)
    outcomes, began = [], time.perf_counter()
    try:
        durable.write_new(output / "run-start.json", {"schema": SCHEMA, "classification": CLASSIFICATION,
            "started_at_utc": utc(), **deepcopy(binding), "resume_allowed": False})
        bundle_hashes = {key: sha(canonical(value)) for key, value in prepared["source_bundles"].items()}
        with durable.Ledger(output / "outcomes.jsonl.gz") as stream:
            for identifier, operand in operands.items():
                record = records[identifier]
                bundle = prepared["source_bundles"][operand["source_bundle_id"]]
                response, receipt = guard(deepcopy(operand["reference"]), deepcopy(operand["proposal"]),
                    source_bundle=deepcopy(bundle), definition_keys=deepcopy(operand["definition_keys"]))
                require(exact_operand(response, operand["reference"]) or exact_operand(response, operand["proposal"]),
                        "Terminal result must be one exact frozen operand")
                inputs.validate_response(response, bundle)
                payload = serialize_context(compact_result(response))
                require(payload == inputs.compact_payload(response), "Public compact envelope differs")
                tokens = len(counter.encode(payload, disallowed_special=()))
                limits = operand["limits"]
                require(tokens == response["token_usage"]["tokens"] <= limits["token_budget"]
                    and len(response["results"]) <= limits["max_results"]
                    and sum(len(row["source"]) for row in response["results"]) == response["total_source_chars"]
                        <= limits["source_budget"], "Exact compact/source/result caps differ")
                scores = {arm: evidence(record["scoring"], value) for arm, value in
                    (("legacy", record["legacy"]), ("coverage", operand["reference"]),
                     ("prior_role", operand["proposal"]), ("candidate", response))}
                row = {"id": identifier, **{name: record[name] for name in ("dataset", "query_id", "mode", "repetition", "quality")},
                    "multi_file": record["scoring"]["multi_file"], "operand_sha256": sha(canonical(operand)),
                    "source_bundle_sha256": bundle_hashes[operand["source_bundle_id"]], "response": response,
                    "guard_receipt": receipt, "compact_payload": payload, "compact_sha256": sha(payload.encode()),
                    "actual_compact_tokens": tokens, "changed_vs_prior_role": not exact_operand(response, operand["proposal"]),
                    "scores": scores, "comparisons": {arm: compare(score, scores["candidate"])
                        for arm, score in scores.items() if arm != "candidate"}}
                stream.append(row)
                outcomes.append(row)
        final_check()
        summary = {"schema": SCHEMA, "classification": CLASSIFICATION, "status": "passed", **summarize(outcomes),
            "terminal_decisions": len(outcomes), "new_work": deepcopy(NEW_WORK),
            "cpu_total_s_including_guard_and_io": time.perf_counter()-began,
            "artifacts": {name: file_sha(output / name) for name in ("run-start.json", "outcomes.jsonl.gz")}}
        durable.write_new(output / "summary.json", summary)
        return summary
    except BaseException as error:
        try:
            durable.write_new(output / "failure.json", {"schema": SCHEMA, "status": "failed",
                "error_type": type(error).__name__, "error": str(error), "stored_outcomes": len(outcomes),
                "resume_allowed": False, "artifacts": {path.name: file_sha(path) for path in output.iterdir() if path.is_file()}})
        except BaseException as recorder_error:
            raise error from recorder_error
        raise


def run(results, *, root=ROOT):
    plan, prepared, chain = verify_plan(results, root=root)
    binding = {"execution_commit": git("rev-parse", "HEAD", root=root), "source_freeze": plan["source_freeze"],
        "plan_sha256": file_sha(Path(results) / "plan.json"),
        "input_audit_sha256": file_sha(Path(results) / "input-audit.json"), "input_freeze": chain}
    def final_check():
        require(same(verify_plan(results, root=root)[0], plan), "Frozen authority changed during replay")

    return replay(prepared, Path(results) / "replay", binding=binding, final_check=final_check)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--freeze", action="store_true")
    action.add_argument("--run", action="store_true")
    parser.add_argument("--source-freeze")
    args = parser.parse_args()
    results = args.results.resolve()
    with authority.cpu_only():
        value = prepare(results, args.source_freeze) if args.freeze else run(results)
    print({key: value[key] for key in ("schema", "classification", "status", "terminal_decisions", "quality_gate") if key in value})


if __name__ == "__main__":
    main()
