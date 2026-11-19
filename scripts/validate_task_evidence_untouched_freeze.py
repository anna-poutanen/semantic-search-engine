#!/usr/bin/env python3
"""Validate the source-only Click untouched task-evidence child freeze.

This command reconstructs saved-source membership and evaluator facts without
calling retrieval, packing, selection, indexing, embeddings, models, or the
network.  It also proves that the child freeze was prepared after the named
Phase 0/selection anchors and before any Click packet output.
"""
from __future__ import annotations

import argparse
import ast
from copy import deepcopy
import fnmatch
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess

from semantic_search.corpus import extract_snippets
from semantic_search.languages import (
    extractor_fingerprint,
    path_excluded,
    source_eligible,
)
from semantic_search.source import physical_lines


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PUBLIC = ROOT / "benchmarks/task_evidence_untouched_click.json"
DEFAULT_GOLD = ROOT / "benchmarks/task_evidence_gold/click_atomic_transactions.json"
DEFAULT_BINDING = ROOT / "benchmarks/task_evidence_gold/untouched_click_freeze.json"
DEFAULT_PROTOCOL = ROOT / "docs/task-evidence-untouched-click-protocol.md"
DEFAULT_CORPUS = ROOT / ".cache/corpora/click"

PROJECT_BASE_REVISION = "a41d07742d0648dcee62cdd3d00d5996f52a1162"
PARENT_ANCHOR_REVISION = "5b8c02ee53f541b6bd31587ec3385b1beddfa512"
SELECTION_REVISION = PROJECT_BASE_REVISION
PARENT_BINDING_PATH = "benchmarks/task_evidence_gold/phase0_freeze.json"
PARENT_BINDING_SHA256 = "7f3007129c7800ad6801677cd4c178a396f44ffe3ea2292e78b1a8d77ab316d0"
PARENT_VALIDATOR_PATH = "scripts/validate_task_evidence_freeze.py"
PARENT_VALIDATOR_SHA256 = "384d8da78ba9885a7356bf9f3bbe5f99c1bee133fc95edba909b1cf6049ec7e7"
SELECTION_PATH = "benchmarks/task_evidence_untouched_selection.json"
SELECTION_SHA256 = "55aa7fde326a5783e528caaa327443c3be8202000f5cb30d24021509ee886fed"
OUTPUT_PATH = "results/task-evidence-untouched-click"

CHILD_PATHS = {
    ".github/workflows/task-evidence-untouched-freeze.yml",
    "benchmarks/task_evidence_gold/click_atomic_transactions.json",
    "benchmarks/task_evidence_gold/untouched_click_freeze.json",
    "benchmarks/task_evidence_untouched_click.json",
    "docs/task-evidence-untouched-click-protocol.md",
    "scripts/validate_task_evidence_untouched_freeze.py",
    "tests/test_task_evidence_untouched_freeze.py",
}
BOUND_ARTIFACT_PATHS = {
    ".github/workflows/task-evidence-untouched-freeze.yml",
    "benchmarks/coding_workflows_multifile.json",
    "benchmarks/coding_workflows_strict.json",
    "benchmarks/multifile_checks/check_click_atomic_transactions.py",
    "benchmarks/multifile_checks/common.py",
    "benchmarks/strict_multifile_checks/check_click_atomic_transactions.py",
    "benchmarks/strict_multifile_checks/manifest.json",
    "benchmarks/strict_multifile_checks/strict_common.py",
    PARENT_BINDING_PATH,
    "benchmarks/task_evidence_gold/click_atomic_transactions.json",
    "benchmarks/task_evidence_untouched_click.json",
    SELECTION_PATH,
    "docs/task-evidence-untouched-click-protocol.md",
    PARENT_VALIDATOR_PATH,
    "scripts/probe_click_atomic_resource_edges.py",
    "scripts/validate_task_evidence_untouched_freeze.py",
    "src/semantic_search/corpus.py",
    "src/semantic_search/languages.py",
    "src/semantic_search/source.py",
    "tests/test_task_evidence_untouched_freeze.py",
}

TASK_ID = "click_atomic_transactions"
CORPUS_REVISION = "934813e4d421071a1b3db3973c02fe2721359a6e"
CORPUS_REPO = "https://github.com/pallets/click"
NEEDS = ("implementation", "callers", "tests", "behavioral_constraints")
EXCLUSIONS = ["docs/*.py", "examples/*.py"]
PREFIX_GLOBS = {
    "src/click/": ["src/click/*.py"],
    "tests/": ["tests/*.py", "tests/typing/*.py"],
}
EXPECTED_INVENTORY = {
    "eligible_supported_files": 70,
    "excluded_supported_files": 25,
    "included_supported_files": 45,
    "parsed_files": 45,
    "parsed_files_without_windows": 2,
    "implementation_windows": 474,
    "test_windows": 597,
    "total_windows": 1071,
    "included_files_sha256": "e3130b2f1d298dc884e9c1d26adf3690add8b9f0e0cf6ff612b97f60d3387b4f",
    "implementation_files_sha256": "e841818743a119a18e822e9d132d39f6d9793c513ffe0fc639236b24d7b4a6b0",
    "test_files_sha256": "9c8f1dff5b8d45dfff8b6976785d6a2ed0151e5f2e2d8068ced554dfec6542ce",
    "excluded_files_sha256": "45516743a9112e034be69b5150212f6b565b6c72f666ab794b7b2f2b3b28e471",
    "windows_sha256": "5f6cf093da973ca62d84c86d2dfcbf5c26dfdd793126430a55693b1d9475fd83",
}
REQUEST_FIELDS = {
    "query", "retrieval", "rerank", "rerank_provider", "candidates",
    "max_results", "source_budget", "token_budget", "include_dependencies",
    "overlays",
}
EXPECTED_CALLS = {
    "model": 0,
    "embedding": 0,
    "index": 0,
    "retrieval": 0,
    "planner": 0,
    "selector": 0,
    "packing": 0,
    "network": 0,
}
WORKTREE_POLICY = (
    "Before an exact child anchor exists, every touched path after "
    "project_base_revision must belong to the seven child artifacts and the result "
    "path must be absent; afterward, all bound authority and child artifacts remain "
    "protected while selector and execution work may proceed outside that closure"
)
SCOPE_OVERRIDE = (
    "Both conditions intentionally override the original workflow source_root src/click "
    "with repository root so indexed tests are eligible; claims apply only to this "
    "frozen child scope."
)
GAP_RULES = [
    "A requested artifact whose file kind has no registered extractor is unresolved "
    "with unsupported_source_kind; the engine must not claim that the artifact or "
    "behavior does not exist",
    "A supported Python source path matched by docs/*.py or examples/*.py is unresolved "
    "with outside_indexed_scope, not unsupported_source_kind",
    "An included supported source file that parses but emits no implementation window "
    "is unresolved with no_extractable_window",
    "Required module-, class-, assignment-, or annotation-only structure not covered by "
    "an emitted implementation window is unresolved with unsupported_artifact_kind",
    "Retrieval, relation, selection, result, character, and token failures retain their "
    "distinct procedural reasons and never become claims of corpus absence",
]
GAP_EVALUATOR_BOUNDARY = (
    "Literal checklist completeness is computed only after both final packets. Public "
    "candidate or partial status is not a completeness claim, and public gaps contain "
    "no evaluator-only path, symbol, or line-span label."
)
NORMALIZATION_CONTRACT = (
    "The separate Click execution freeze must bind the exact normalizer source and "
    "SHA-256 before the attempt. Equality is canonical compact sorted JSON of the "
    "complete compact response after removing only timing and request identifiers named "
    "prospectively by that freeze; selected row order and identities, excerpts, evidence "
    "statuses and gaps, budgets, source hashes, and relation provenance remain. Retain "
    "every raw payload and raw hash."
)
ONE_SHOT_RECEIPT_CONTRACT = (
    "The execution freeze must bind one unique attempt ID, prove the result path absent "
    "through its own commit, and bind an append-only start and finish or failure receipt "
    "schema. Every raw and normalized condition payload, infrastructure failure, and "
    "gate failure is retained; no discarded local run, retry, resume, or replacement is "
    "permitted."
)
EXECUTION_BINDING_PATH = (
    "benchmarks/task_evidence_gold/untouched_click_execution_freeze.json"
)
EXECUTION_HANDOFF_CONTRACT = {
    "classification": "task_evidence_untouched_click_execution_freeze",
    "child_handoff_required_fields": [
        "child_freeze_revision", "child_binding_sha256",
        "requests_packet_gate_receipt_sha256",
        "requests_native_latency_gate_receipt_sha256", "attempt_id", "result_path",
        "result_path_state_at_execution_freeze",
    ],
    "ordering": (
        "The execution binding must be committed with the result path absent after both "
        "Requests gate receipts and before any Click result-path touch."
    ),
}
CLAIM_POLICY = (
    "This is an outcome-unobserved, predeclared packet validation on an observed "
    "development codebase. In the inherited selection lock, untouched by selector tuning "
    "means no Click selector execution, packet outcome, or Click-based parameter "
    "adjustment; it does not mean designer blindness to reviewed Click source and gold or "
    "prove that generic rule design was uninfluenced. The execution freeze must prove "
    "corpus neutrality; no Click coding-agent conversation, patch, test execution, or "
    "grading is permitted."
)
PUBLIC_CLASSIFICATION = (
    "Harness-visible prospective Click child source and request contract; the exact "
    "resolved request is the only condition-specific harness input, while both conditions "
    "share execution-frozen candidate and relation inventories"
)
PLANNER_INPUT = (
    "The exact resolved backend request is the only condition-specific input; both "
    "conditions also receive the same execution-frozen candidate and relation inventories. "
    "This manifest, task id, scope inventory, parent and selection bindings, schedule, "
    "gates, and evaluator-only checklist are not planner inputs"
)
GATE_REQUIREMENTS = {
    "source_freeze_integrity": (
        "The pinned revision, exact exclusions and membership, extractor fingerprint, "
        "counts, hashes, parent and selection chronology, absent result path, and planner "
        "boundary match the child freeze."
    ),
    "retrieval_parity": (
        "Control and treatment use identical requests except needed_evidence and identical "
        "source, ordered candidates, scores, relation graph, tokenizer, and budgets."
    ),
    "legacy_path_equality": (
        "The legacy control compact response is byte-for-byte identical to the unchanged "
        "legacy path."
    ),
    "complementary_gain": (
        "Treatment adds at least one evaluator-predeclared complete indexed implementation "
        "or caller group from a newly covered production source file whose path is absent "
        "from the control's covered required groups."
    ),
    "no_individual_loss": (
        "Treatment loses no evaluator-predeclared required group covered by control and "
        "preserves the excerpts establishing that coverage byte-for-byte."
    ),
    "exact_budgets_and_source": (
        "Every packet respects the 8-result, 12000-source-character, and 4096-token caps, "
        "and every excerpt matches frozen source bytes, line bounds, and hashes."
    ),
    "truthful_gap_reconciliation": (
        "Every missing evaluator group has an accurate factual cause and every requested "
        "public need has a non-overclaiming status or procedural gap."
    ),
    "deterministic_replay": (
        "All three executions per condition produce identical selected rows and payloads "
        "under the prospectively bound normalization contract from the same frozen inputs."
    ),
    "corpus_neutrality": (
        "The same selector and packer source must execute Requests and Click; an "
        "execution-freeze audit must reject literal Click, task, or gold IDs, paths, "
        "symbols, hashes, and conditionals keyed to them while allowing generic features "
        "computed uniformly from request and candidate data."
    ),
    "planner_gold_isolation": (
        "No evaluator-only key, group ID, target path, non-prompt target symbol, line-span "
        "metadata, source or evidence hash, or outcome enters either resolved request; the "
        "execution freeze and harness must keep the full evaluator checklist unavailable "
        "to selection and packing."
    ),
}

SELECTION_SOURCE_MANIFESTS = [
    {"path": "benchmarks/coding_workflows_multifile.json",
     "sha256": "e2274eae067bb4c13bd5730fdf84f34adc8ff04bd9d9dc8e76be48dfa189acac"},
    {"path": "benchmarks/coding_workflows_strict.json",
     "sha256": "607f7491f5f0dd265591513ebccbe23e0a1685612b7cc25a00dd7c4846351f84"},
]
SELECTION_RULE = {
    "start_after_task_id": "requests_response_limit",
    "scan_order": "The exact tasks array order in coding_workflows_multifile.json",
    "choose": ("The first eligible task; do not compare retrieval, packing, coverage, "
               "latency, or agent outcomes"),
    "eligibility": [
        "The corpus_id differs from requests",
        "Every reference production path has a currently supported Python, JavaScript, "
        "or TypeScript saved-source extension",
        "repo_url and revision are pinned, preparation_patch is null, "
        "minimum_production_files is at least two, and at least two distinct reference "
        "production files are named",
        "coding_workflows_strict.json has the same task id, corpus id, repo URL, revision, "
        "and prompt; strict_control_passed is true; seed base fails; seed reference passes; "
        "and the strict checker and shared helper have frozen SHA-256 values",
        "The selection is committed before candidate source review, retrieval, packing, "
        "selector implementation, Requests packet outcomes, or coding-agent trials",
    ],
}
SELECTION_SUBSTITUTION_POLICY = (
    "Do not fall back to Starlette if Click has no complementary candidate, cannot fit "
    "the frozen budgets, or fails admission. Record that failure. Replacing Click "
    "requires a new named selection lock created before further source or outcome "
    "inspection."
)
SELECTION_CLAIM_BOUNDARY = {
    "untouched_means": (
        "Click is untouched by the Phase 0 task-evidence selector, Requests packet "
        "outcomes, and any selector tuning performed after this lock"
    ),
    "does_not_mean": (
        "Click is not a fresh project-wide repository holdout; it is an observed "
        "development repository with existing task and checker artifacts"
    ),
    "supported_claim_if_passed": (
        "A separately selected observed codebase did not lose frozen evidence under the "
        "task-evidence selector"
    ),
    "unsupported_claims": [
        "generalization to an unseen repository",
        "JavaScript or TypeScript generalization",
        "general agent-token savings",
    ],
}
SELECTION_OBSERVATION_STATE = {
    "candidate_source_review": False,
    "candidate_retrieval_calls": 0,
    "candidate_packing_calls": 0,
    "candidate_selector_calls": 0,
    "requests_packet_outcomes": 0,
    "agent_trials": 0,
}
SELECTION_REQUIRED_NEXT_FREEZE = [
    "pinned clean Click checkout and exact tracked supported-source membership",
    "repository-root scope with exact exclusions and implementation/test classification",
    "source-authored evaluator-only evidence checklist and unsupported structural gaps",
    "the same resolved control and treatment budgets used by the Requests packet gate",
    "a no-individual-loss admission rule and deterministic replay schedule",
    "validator, tests, artifact hashes, and chronology binding committed before selector "
    "implementation",
]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode()


def decode_json(raw):
    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw, object_pairs_hook=reject_duplicates)


def read_json(path):
    return decode_json(Path(path).read_text(encoding="utf-8"))


def exact_keys(value, expected, label):
    require(type(value) is dict and set(value) == set(expected),
            f"{label} fields changed")
    return value


def strict_int(value, label, *, minimum=0):
    require(type(value) is int and value >= minimum,
            f"{label} must be an integer >= {minimum}")
    return value


def require_version_one(value, label):
    require(type(value) is int and value == 1,
            f"{label} version must be the strict integer 1")


def is_hex_digest(value, length=64):
    return (type(value) is str and len(value) == length
            and all(character in "0123456789abcdef" for character in value))


def relative_path(value):
    require(type(value) is str and value and "\\" not in value,
            "Paths must be nonempty repository-relative POSIX strings")
    path = PurePosixPath(value)
    require(not path.is_absolute() and ".." not in path.parts and str(path) == value,
            "Paths must be normalized and cannot escape the repository")
    return path


def git(repo, *args):
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True, stderr=subprocess.DEVNULL
    ).strip()


def _is_ancestor(root, ancestor, descendant):
    return subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", ancestor, descendant],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


def segment_glob(path, pattern):
    """Match anchored POSIX globs while preventing '*' from crossing '/'."""
    path_parts, pattern_parts = path.split("/"), pattern.split("/")
    return (len(path_parts) == len(pattern_parts)
            and all(fnmatch.fnmatchcase(part, expected)
                    for part, expected in zip(path_parts, pattern_parts)))


def planner_request(public, condition_id):
    conditions = {item["id"]: item for item in public["conditions"]}
    require(condition_id in conditions, f"Unknown condition: {condition_id}")
    request = deepcopy(public["common_request"])
    require(request.pop("query_from", None) == "task.prompt",
            "Common request must resolve query only from task.prompt")
    request["query"] = public["task"]["prompt"]
    needed = conditions[condition_id]["needed_evidence"]
    if needed is not None:
        request["needed_evidence"] = deepcopy(needed)
    return request


def _manifest_task(path, task_id):
    manifest = read_json(path)
    tasks = manifest.get("tasks")
    require(type(tasks) is list, f"{path} has no tasks array")
    matches = [item for item in tasks
               if type(item) is dict and item.get("id") == task_id]
    require(len(matches) == 1, f"{task_id} is not unique in {path}")
    return matches[0]


def _eligible_selection_candidate(task, strict_by_id, *, root):
    """Return the lock candidate derived from the two bound workflow manifests."""
    if type(task) is not dict or task.get("corpus_id") == "requests":
        return None
    task_id = task.get("id")
    strict = strict_by_id.get(task_id)
    if type(task_id) is not str or not task_id or type(strict) is not dict:
        return None
    repository = task.get("repo_url")
    revision = task.get("revision")
    minimum = task.get("minimum_production_files")
    production = task.get("reference_production_files")
    if (type(repository) is not str or not repository.startswith("https://")
            or not is_hex_digest(revision, 40)
            or task.get("preparation_patch") is not None
            or type(minimum) is not int or minimum < 2
            or type(production) is not list or len(production) < minimum
            or len(production) != len(set(production)) or len(production) < 2):
        return None
    try:
        normalized = [str(relative_path(path)) for path in production]
    except (TypeError, ValueError):
        return None
    if normalized != production or not all(source_eligible(path) for path in production):
        return None
    identity_fields = ("id", "corpus_id", "repo_url", "revision", "prompt")
    if any(strict.get(name) != task.get(name) for name in identity_fields):
        return None
    if strict.get("strict_control_passed") is not True:
        return None
    seed = strict.get("seed_validation")
    if (type(seed) is not dict or type(seed.get("base")) is not dict
            or type(seed.get("reference")) is not dict
            or seed["base"].get("passed") is not False
            or seed["reference"].get("passed") is not True):
        return None
    verification = strict.get("verification")
    if type(verification) is not dict:
        return None
    checker = verification.get("script")
    checker_digest = verification.get("sha256")
    helper = verification.get("shared_helper")
    helper_digest = verification.get("shared_helper_sha256")
    try:
        checker_path = Path(root) / relative_path(checker)
        helper_path = Path(root) / relative_path(helper)
    except (TypeError, ValueError):
        return None
    if (not checker_path.is_file() or not helper_path.is_file()
            or not is_hex_digest(checker_digest)
            or not is_hex_digest(helper_digest)
            or sha256(checker_path.read_bytes()) != checker_digest
            or sha256(helper_path.read_bytes()) != helper_digest):
        return None
    prompt = task.get("prompt")
    if type(prompt) is not str or not prompt:
        return None
    return {
        "task_id": task_id,
        "corpus_id": task.get("corpus_id"),
        "repo_url": repository,
        "revision": revision,
        "prompt_sha256": sha256(prompt.encode()),
        "minimum_production_files": minimum,
        "reference_production_file_count": len(production),
        "strict_checker_path": checker,
        "strict_checker_sha256": checker_digest,
    }


def validate_selection_lock(selection, *, root=ROOT):
    """Reconstruct the prospective first-eligible choice from bound authorities."""
    exact_keys(selection, {
        "version", "classification", "phase_0_anchor_revision", "selection_date",
        "source_manifests", "selection_rule", "eligible_manifest_candidates",
        "selected", "substitution_policy", "claim_boundary",
        "observation_state_at_lock", "required_next_freeze",
    }, "Prospective selection lock")
    require_version_one(selection["version"], "Selection lock")
    require(selection["classification"]
            == "prospective_task_evidence_untouched_selection_lock"
            and selection["phase_0_anchor_revision"] == PARENT_ANCHOR_REVISION
            and selection["selection_date"] == "2026-11-19",
            "Selection-lock identity changed")
    manifests = selection["source_manifests"]
    require(type(manifests) is list and manifests == SELECTION_SOURCE_MANIFESTS,
            "Selection-lock authority manifests changed")
    loaded = []
    for item in manifests:
        exact_keys(item, {"path", "sha256"}, "Selection authority")
        path = Path(root) / relative_path(item["path"])
        require(path.is_file() and sha256(path.read_bytes()) == item["sha256"],
                f"Selection authority bytes changed: {item['path']}")
        loaded.append(read_json(path))
    require(selection["selection_rule"] == SELECTION_RULE,
            "Prospective selection rule changed")
    workflow, strict_workflow = loaded
    tasks = workflow.get("tasks")
    strict_tasks = strict_workflow.get("tasks")
    require(type(tasks) is list and type(strict_tasks) is list,
            "Selection authorities must contain task arrays")
    task_ids = [task.get("id") for task in tasks if type(task) is dict]
    strict_ids = [task.get("id") for task in strict_tasks if type(task) is dict]
    require(len(task_ids) == len(tasks) and len(task_ids) == len(set(task_ids))
            and len(strict_ids) == len(strict_tasks)
            and len(strict_ids) == len(set(strict_ids)),
            "Selection authority task IDs must be unique")
    anchor_id = SELECTION_RULE["start_after_task_id"]
    require(anchor_id in task_ids, "Selection scan anchor is absent")
    strict_by_id = {task["id"]: task for task in strict_tasks}
    after_anchor = tasks[task_ids.index(anchor_id) + 1:]
    reconstructed = []
    for position, task in enumerate(after_anchor, 1):
        candidate = _eligible_selection_candidate(task, strict_by_id, root=root)
        if candidate is not None:
            reconstructed.append({"position_after_anchor": position, **candidate})
    candidates = selection["eligible_manifest_candidates"]
    require(type(candidates) is list and candidates == reconstructed and candidates,
            "Eligible candidate list differs from the bound manifest scan")
    candidate_fields = {
        "position_after_anchor", "task_id", "corpus_id", "repo_url", "revision",
        "prompt_sha256", "minimum_production_files",
        "reference_production_file_count", "strict_checker_path",
        "strict_checker_sha256",
    }
    for candidate in candidates:
        exact_keys(candidate, candidate_fields, "Eligible candidate")
        require(type(candidate["position_after_anchor"]) is int
                and type(candidate["minimum_production_files"]) is int
                and type(candidate["reference_production_file_count"]) is int,
                "Candidate positions and counts must be strict integers")
    first = reconstructed[0]
    selected = exact_keys(selection["selected"],
                          {"task_id", "corpus_id", "repo_url", "revision", "reason"},
                          "Selected candidate")
    require(selected == {
        "task_id": first["task_id"], "corpus_id": first["corpus_id"],
        "repo_url": first["repo_url"], "revision": first["revision"],
        "reason": "First eligible manifest task after requests_response_limit",
    } and selected["task_id"] == TASK_ID,
            "Selection lock does not choose the first eligible Click task")
    require(selection["substitution_policy"] == SELECTION_SUBSTITUTION_POLICY
            and selection["claim_boundary"] == SELECTION_CLAIM_BOUNDARY
            and selection["required_next_freeze"] == SELECTION_REQUIRED_NEXT_FREEZE,
            "Selection substitution, claim, or required-next-freeze contract changed")
    observation = exact_keys(selection["observation_state_at_lock"],
                             set(SELECTION_OBSERVATION_STATE),
                             "Selection observation state")
    require(observation == SELECTION_OBSERVATION_STATE
            and observation["candidate_source_review"] is False
            and all(type(observation[name]) is int for name in observation
                    if name != "candidate_source_review"),
            "Selection observation state changed")
    return {"selected": selected, "candidates": reconstructed,
            "source_manifests": manifests}


def validate_public(public, *, root=ROOT):
    exact_keys(public, {
        "version", "phase", "classification", "parent_freeze", "selection_lock",
        "task", "corpus", "index_scope", "artifact_kinds", "common_request",
        "conditions", "planner_boundary", "claim_boundary", "gap_contract",
        "calls_at_scope_freeze", "schedule", "gates",
    }, "Untouched Click manifest")
    require_version_one(public.get("version"), "Public child freeze")
    require(public.get("phase") == "untouched_click_child_scope_freeze"
            and public.get("classification") == PUBLIC_CLASSIFICATION,
            "Unsupported untouched Click manifest")

    parent = exact_keys(public["parent_freeze"],
                        {"anchor_revision", "selection_revision"}, "Parent freeze")
    require(parent == {"anchor_revision": PARENT_ANCHOR_REVISION,
                       "selection_revision": SELECTION_REVISION},
            "Parent freeze revisions changed")
    selection = exact_keys(public["selection_lock"], {"path", "sha256"},
                           "Selection lock")
    require(selection == {"path": SELECTION_PATH, "sha256": SELECTION_SHA256},
            "Selection-lock binding changed")
    selection_path = root / relative_path(selection["path"])
    require(selection_path.is_file()
            and sha256(selection_path.read_bytes()) == SELECTION_SHA256,
            "Selection-lock bytes changed")
    selection_manifest = read_json(selection_path)
    selection_result = validate_selection_lock(selection_manifest, root=root)
    selected = selection_result["selected"]

    task = exact_keys(public["task"], {
        "id", "prompt", "prompt_sha256", "source_manifest",
        "strict_source_manifest",
    }, "Task")
    require(task["id"] == TASK_ID and type(task["prompt"]) is str and task["prompt"]
            and task["prompt_sha256"] == sha256(task["prompt"].encode()),
            "Click task identity or prompt digest changed")
    sources = (
        ("source_manifest", "benchmarks/coding_workflows_multifile.json"),
        ("strict_source_manifest", "benchmarks/coding_workflows_strict.json"),
    )
    source_tasks = []
    for key, expected_path in sources:
        binding = exact_keys(task[key], {"path", "sha256"}, key)
        require(binding["path"] == expected_path, f"{key} path changed")
        path = root / relative_path(binding["path"])
        require(path.is_file() and sha256(path.read_bytes()) == binding["sha256"],
                f"{key} bytes changed")
        source_tasks.append(_manifest_task(path, TASK_ID))
    require([task["source_manifest"], task["strict_source_manifest"]]
            == selection_result["source_manifests"],
            "Public task authorities differ from the prospective selection lock")
    require(all(item.get("prompt") == task["prompt"] for item in source_tasks),
            "Click prompt differs between its frozen source manifests")

    corpus = exact_keys(public["corpus"],
                        {"id", "repo_url", "revision", "membership"}, "Corpus")
    require(corpus["id"] == "click" and corpus["repo_url"] == CORPUS_REPO
            and corpus["revision"] == CORPUS_REVISION
            and all(item.get("corpus_id") == "click"
                    and item.get("repo_url") == CORPUS_REPO
                    and item.get("revision") == CORPUS_REVISION
                    for item in source_tasks)
            and selected == {"task_id": TASK_ID, "corpus_id": "click",
                             "repo_url": CORPUS_REPO, "revision": CORPUS_REVISION,
                             "reason": "First eligible manifest task after requests_response_limit"},
            "Click corpus or prospective selection identity changed")
    require(corpus["membership"]
            == "saved implementations; Git-tracked files only in Git projects",
            "Corpus membership contract changed")

    scope = exact_keys(public["index_scope"], {
        "source_root", "scope_override", "source_exclusions", "required_prefixes",
        "glob_contract", "extraction_policy", "extractor_fingerprint",
        "window_policy", "expected_inventory",
    }, "Index scope")
    require(scope["source_root"] == "." and scope["scope_override"] == SCOPE_OVERRIDE
            and scope["source_exclusions"] == EXCLUSIONS,
            "Click root scope or exclusions changed")
    require(scope["extraction_policy"] == "saved-implementations-v3"
            and scope["extractor_fingerprint"] == extractor_fingerprint()
            and scope["window_policy"] == {"max_lines": 160, "overlap_lines": 40}
            and type(scope["glob_contract"]) is str and scope["glob_contract"],
            "Click extraction or glob contract changed")
    prefixes = scope["required_prefixes"]
    require(type(prefixes) is list and len(prefixes) == 2
            and [item.get("prefix") for item in prefixes]
                == ["src/click/", "tests/"]
            and [item.get("artifact_kind") for item in prefixes]
                == ["implementation", "test"],
            "Click implementation/test prefixes changed")
    expected_file_counts = {"src/click/": 16, "tests/": 29}
    for entry in prefixes:
        exact_keys(entry, {"prefix", "artifact_kind", "membership_globs",
                           "expected_files"}, "Required prefix")
        require(entry["membership_globs"] == PREFIX_GLOBS[entry["prefix"]]
                and strict_int(entry["expected_files"], "expected_files", minimum=1)
                    == expected_file_counts[entry["prefix"]],
                "Click prefix membership changed")

    inventory = exact_keys(scope["expected_inventory"], {
        *EXPECTED_INVENTORY,
        "file_inventory_hash_contract", "window_inventory_hash_contract",
    }, "Expected inventory")
    for name, expected in EXPECTED_INVENTORY.items():
        require(inventory[name] == expected,
                f"Frozen Click inventory changed: {name}")
    require(type(inventory["file_inventory_hash_contract"]) is str
            and inventory["file_inventory_hash_contract"]
            and type(inventory["window_inventory_hash_contract"]) is str
            and inventory["window_inventory_hash_contract"],
            "Inventory hash contracts must be explicit")

    artifact_kinds = exact_keys(public["artifact_kinds"],
                                {"indexed", "explicitly_unsupported"},
                                "Artifact kinds")
    require(artifact_kinds == {
        "indexed": [
            {"kind": "implementation",
             "unit": "Python function or method implementation window"},
            {"kind": "test",
             "unit": "Python test, fixture, helper, function, or method implementation window"},
        ],
        "explicitly_unsupported": [
            {"kind": "configuration_without_a_registered_extractor",
             "gap_reason": "unsupported_source_kind"},
            {"kind": "documentation_without_a_registered_extractor",
             "gap_reason": "unsupported_source_kind"},
            {"kind": "non_python_fixture_without_a_registered_extractor",
             "gap_reason": "unsupported_source_kind"},
            {"kind": "python_module_or_class_structure_without_an_implementation_body",
             "gap_reason": "unsupported_artifact_kind"},
        ],
    }, "Frozen Click artifact-kind support changed")

    common = exact_keys(public["common_request"], {
        "query_from", "retrieval", "rerank", "rerank_provider", "candidates",
        "max_results", "source_budget", "token_budget", "include_dependencies",
        "overlays",
    }, "Common request")
    require(common["query_from"] == "task.prompt" and common["retrieval"] == "dense"
            and common["rerank"] is False and common["rerank_provider"] == "h2o"
            and common["candidates"] == 50 and common["max_results"] == 8
            and common["source_budget"] == 12000 and common["token_budget"] == 4096
            and common["include_dependencies"] is True and common["overlays"] == []
            and all(type(common[name]) is int for name in
                    ("candidates", "max_results", "source_budget", "token_budget")),
            "Resolved Click request settings changed")
    conditions = public["conditions"]
    require(type(conditions) is list and len(conditions) == 2,
            "Click freeze requires exactly two conditions")
    exact_keys(conditions[0], {"id", "needed_evidence", "null_behavior"},
               "Legacy condition")
    exact_keys(conditions[1], {"id", "needed_evidence"}, "Task-evidence condition")
    require(conditions[0]["id"] == "legacy_control"
            and conditions[0]["needed_evidence"] is None
            and type(conditions[0]["null_behavior"]) is str
            and conditions[0]["null_behavior"]
            and conditions[1] == {"id": "task_evidence",
                                  "needed_evidence": list(NEEDS)},
            "Paired Click conditions changed")
    control = planner_request(public, "legacy_control")
    treatment = planner_request(public, "task_evidence")
    require(set(control) == REQUEST_FIELDS
            and set(treatment) == REQUEST_FIELDS | {"needed_evidence"}
            and {key: value for key, value in treatment.items()
                 if key != "needed_evidence"} == control,
            "Click conditions may differ only by needed_evidence")
    boundary = exact_keys(public["planner_boundary"], {
        "input", "control_request_sha256", "task_evidence_request_sha256",
        "hash_contract",
    }, "Planner boundary")
    require(boundary["input"] == PLANNER_INPUT
            and type(boundary["hash_contract"]) is str and boundary["hash_contract"]
            and boundary["control_request_sha256"] == sha256(canonical(control))
            and boundary["task_evidence_request_sha256"] == sha256(canonical(treatment)),
            "Resolved Click planner boundary changed")

    require(public["claim_boundary"] == selection_manifest.get("claim_boundary"),
            "Public claim boundary differs from the precommitted selection lock")
    gap_contract = exact_keys(public["gap_contract"], {
        "public_statuses", "reason_vocabulary", "rules", "evaluator_boundary",
    }, "Gap contract")
    require(gap_contract["public_statuses"] == ["candidate", "partial", "unresolved"]
            and gap_contract["reason_vocabulary"] == [
                "unsupported_source_kind", "unsupported_artifact_kind",
                "no_extractable_window", "outside_indexed_scope", "not_retrieved",
                "selection_excluded", "relation_unresolved",
                "result_limit_exhausted", "source_budget_exhausted",
                "token_budget_exhausted",
            ]
            and gap_contract["rules"] == GAP_RULES
            and gap_contract["evaluator_boundary"] == GAP_EVALUATOR_BOUNDARY,
            "Click procedural gap vocabulary changed")
    require(public["calls_at_scope_freeze"] == EXPECTED_CALLS
            and all(type(value) is int
                    for value in public["calls_at_scope_freeze"].values()),
            "Click scope freeze must precede every runtime call")
    schedule = exact_keys(public["schedule"],
                          {"packet_gate", "untouched_validation_prerequisite"},
                          "Schedule")
    packet = exact_keys(schedule["packet_gate"], {
        "kind", "pairs", "replays_per_condition", "pair_order",
        "shared_retrieval_capture", "required_after_requests_gates",
        "validation_attempts", "retry_policy", "complementary_gain",
        "no_individual_loss", "normalization_contract",
    }, "Packet replay schedule")
    expected_order = [
        ["legacy_control", "task_evidence"],
        ["task_evidence", "legacy_control"],
        ["legacy_control", "task_evidence"],
    ]
    require(packet == {
        "kind": "paired_packet_replay", "pairs": 3,
        "replays_per_condition": 3, "pair_order": expected_order,
        "shared_retrieval_capture": True,
        "required_after_requests_gates": True,
        "validation_attempts": 1, "retry_policy": "none",
        "complementary_gain": True, "no_individual_loss": True,
        "normalization_contract": NORMALIZATION_CONTRACT,
    } and all(type(packet[name]) is int for name in
              ("pairs", "replays_per_condition", "validation_attempts")),
            "Click deterministic replay schedule changed")
    prerequisite = exact_keys(schedule["untouched_validation_prerequisite"], {
        "execution_freeze_status", "execution_freeze_required_after",
        "execution_freeze_required_before", "result_path",
        "result_path_state_at_child_freeze", "substitution_policy",
        "coding_agent_trial_count", "claim_policy", "one_shot_receipt_contract",
        "execution_binding_path", "execution_handoff_contract",
    }, "Untouched validation prerequisite")
    require(prerequisite["execution_freeze_status"] == "not_yet_frozen"
            and prerequisite["execution_freeze_required_after"]
                == ["requests_packet_gate", "requests_native_latency_gate"]
            and prerequisite["execution_freeze_required_before"]
                == "click_packet_execution"
            and prerequisite["result_path"] == OUTPUT_PATH
            and prerequisite["result_path_state_at_child_freeze"] == "absent"
            and prerequisite["substitution_policy"]
                == selection_manifest.get("substitution_policy")
            and prerequisite["coding_agent_trial_count"] == 0
            and type(prerequisite["coding_agent_trial_count"]) is int
            and type(prerequisite["claim_policy"]) is str
            and prerequisite["claim_policy"] == CLAIM_POLICY
            and prerequisite["one_shot_receipt_contract"]
                == ONE_SHOT_RECEIPT_CONTRACT
            and prerequisite["execution_binding_path"] == EXECUTION_BINDING_PATH
            and prerequisite["execution_handoff_contract"]
                == EXECUTION_HANDOFF_CONTRACT,
            "Click untouched prerequisite changed")
    gate_ids = [
        "source_freeze_integrity", "retrieval_parity", "legacy_path_equality",
        "complementary_gain", "no_individual_loss", "exact_budgets_and_source",
        "truthful_gap_reconciliation", "deterministic_replay",
        "corpus_neutrality", "planner_gold_isolation",
    ]
    gates = public["gates"]
    require(type(gates) is list and len(gates) == len(gate_ids),
            "Click gate count changed")
    for gate, identifier in zip(gates, gate_ids):
        exact_keys(gate, {"id", "requirement"}, "Click gate")
        require(gate["id"] == identifier
                and gate["requirement"] == GATE_REQUIREMENTS[identifier],
                "Click gate contract changed")
    return {"task": source_tasks[0], "control_request": control,
            "treatment_request": treatment,
            "request_hashes": {"legacy_control": sha256(canonical(control)),
                               "task_evidence": sha256(canonical(treatment))},
            "output_path": OUTPUT_PATH}


def reconstruct_scope(public, corpus):
    corpus = Path(corpus)
    scope = public["index_scope"]
    require(corpus.is_dir(), f"Missing pinned Click corpus: {corpus}")
    require(git(corpus, "rev-parse", "HEAD") == CORPUS_REVISION,
            "Pinned Click corpus revision changed")
    require(not git(corpus, "status", "--porcelain=v1"),
            "Pinned Click corpus must be clean")
    tracked = [item for item in subprocess.check_output(
        ["git", "-C", str(corpus), "ls-files", "-z"]
    ).decode("utf-8", "surrogateescape").split("\0") if item]
    supported = sorted(path for path in tracked if source_eligible(path))
    excluded = sorted(path for path in supported
                      if path_excluded(path, scope["source_exclusions"]))
    included = sorted(set(supported) - set(excluded))
    classified = {}
    for entry in scope["required_prefixes"]:
        members = sorted(path for path in included
                         if any(segment_glob(path, pattern)
                                for pattern in entry["membership_globs"]))
        require(len(members) == entry["expected_files"],
                f"{entry['artifact_kind']} membership count changed")
        for path in members:
            require(path not in classified, "Artifact membership globs overlap")
            classified[path] = entry["artifact_kind"]
    require(set(classified) == set(included),
            "Included Click source contains an unclassified artifact path")

    def inventory(paths):
        return {path: sha256((corpus / path).read_bytes()) for path in paths}

    included_inventory = inventory(included)
    excluded_inventory = inventory(excluded)
    implementation_inventory = inventory(sorted(
        path for path, kind in classified.items() if kind == "implementation"))
    test_inventory = inventory(sorted(
        path for path, kind in classified.items() if kind == "test"))
    windows = extract_snippets(
        corpus, scope["source_root"],
        max_lines=scope["window_policy"]["max_lines"],
        overlap_lines=scope["window_policy"]["overlap_lines"],
        source_exclusions=scope["source_exclusions"],
    )
    require(len({item.id for item in windows}) == len(windows),
            "Extracted Click window IDs are not unique")
    window_inventory = {
        item.id: item.content_hash for item in sorted(windows, key=lambda row: row.id)
    }
    counts = {
        "eligible_supported_files": len(supported),
        "excluded_supported_files": len(excluded),
        "included_supported_files": len(included),
        "parsed_files": len(included),
        "parsed_files_without_windows": len(set(included)
                                            - {item.path for item in windows}),
        "implementation_windows": sum(
            classified.get(item.path) == "implementation" for item in windows),
        "test_windows": sum(classified.get(item.path) == "test" for item in windows),
        "total_windows": len(windows),
    }
    hashes = {
        "included_files_sha256": sha256(canonical(included_inventory)),
        "implementation_files_sha256": sha256(canonical(implementation_inventory)),
        "test_files_sha256": sha256(canonical(test_inventory)),
        "excluded_files_sha256": sha256(canonical(excluded_inventory)),
        "windows_sha256": sha256(canonical(window_inventory)),
    }
    require({**counts, **hashes} == EXPECTED_INVENTORY,
            "Reconstructed Click inventory differs from the frozen inventory")
    return {"included": included_inventory, "excluded": excluded_inventory,
            "classified": classified, "windows": windows, "counts": counts,
            "hashes": hashes}


def _class_nodes(tree):
    result = {}

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.parents = []

        def visit_ClassDef(self, node):
            name = ".".join([*self.parents, node.name])
            result[name] = node
            self.parents.append(node.name)
            self.generic_visit(node)
            self.parents.pop()

    Visitor().visit(tree)
    return result


def _assignment_name(node):
    target = node.target if isinstance(node, ast.AnnAssign) else node.targets[0]
    return target.id if isinstance(target, ast.Name) else None


def _validate_structure(target, source, low, high):
    """Derive unsupported Python structure from source, never from a gold assertion."""
    kind = target["structural_kind"]
    facts = target["structural_facts"]
    symbol = target["symbol"]
    require(type(facts) is dict, "Unsupported target must freeze structural facts")
    tree = ast.parse(source, filename=target["path"])
    classes = _class_nodes(tree)
    if kind in {"module_import", "module_imports"}:
        nodes = [node for node in tree.body if isinstance(node, ast.ImportFrom)
                 and low <= node.lineno
                 and (node.end_lineno or node.lineno) <= high]
        actual = [{"module": node.module, "level": node.level,
                   "name": alias.name, "asname": alias.asname}
                  for node in nodes for alias in node.names]
        if kind == "module_import":
            exact_keys(facts, {"module", "level", "name", "asname"},
                       "Module-import facts")
            require(len(actual) == 1 and facts == actual[0],
                    "Frozen module import changed")
        else:
            exact_keys(facts, {"imports"}, "Module-import facts")
            require(facts["imports"] == actual and actual,
                    "Frozen module imports changed")
        require(symbol in {item["asname"] or item["name"] for item in actual},
                "Module-import symbol is not imported in the evidence span")
    elif kind == "module_assignment":
        exact_keys(facts, {"target", "value"}, "Module-assignment facts")
        nodes = [node for node in tree.body
                 if isinstance(node, (ast.Assign, ast.AnnAssign))
                 and _assignment_name(node) == facts["target"]
                 and low <= node.lineno
                 and (node.end_lineno or node.lineno) <= high]
        require(len(nodes) == 1 and symbol == facts["target"]
                and ast.unparse(nodes[0].value) == facts["value"],
                "Frozen module assignment changed")
    elif kind == "class_declaration":
        exact_keys(facts, {"bases", "annotated_attributes"}, "Class facts")
        node = classes.get(symbol)
        require(node is not None and low <= node.lineno
                and (node.end_lineno or node.lineno) <= high,
                "Class declaration does not match the evidence span")
        actual = {"bases": [ast.unparse(base) for base in node.bases],
                  "annotated_attributes": [_assignment_name(child)
                                           for child in node.body
                                           if isinstance(child, ast.AnnAssign)]}
        require(facts == actual, "Frozen class structure changed")
    elif kind == "class_attribute":
        exact_keys(facts, {"value"}, "Class-attribute facts")
        parts = symbol.split(".")
        require(len(parts) >= 2, "Class-attribute symbol must be qualified")
        node = classes.get(".".join(parts[:-1]))
        matches = [] if node is None else [child for child in node.body
            if isinstance(child, (ast.Assign, ast.AnnAssign))
            and _assignment_name(child) == parts[-1]
            and low <= child.lineno and (child.end_lineno or child.lineno) <= high]
        require(len(matches) == 1 and ast.unparse(matches[0].value) == facts["value"],
                "Frozen class attribute changed")
    else:
        raise ValueError("Unknown unsupported structural kind")


def _target_facts(target, corpus, scope, window_by_id):
    path = str(relative_path(target.get("path")))
    require(path in scope["included"],
            "Gold target is outside frozen Click indexed membership")
    symbol = target.get("symbol")
    require(type(symbol) is str and symbol, "Gold target symbol must be nonempty")
    interval = target.get("evidence_lines")
    require(type(interval) is list and len(interval) == 2
            and all(type(number) is int for number in interval),
            "Evidence interval must contain two strict integers")
    low, high = interval
    source = (Path(corpus) / path).read_text(encoding="utf-8")
    rows = physical_lines(source)
    require(1 <= low <= high <= len(rows), "Evidence interval is outside source")
    evidence = "".join(rows[low - 1:high]).encode()
    require(target.get("source_file_sha256") == scope["included"][path]
            and target.get("evidence_sha256") == sha256(evidence),
            "Gold source or evidence hash changed")
    covering = sorted(item.id for item in window_by_id.values()
                      if item.path == path and item.symbol == symbol
                      and item.start_line <= low <= high <= item.end_line)
    span_covering = sorted(item.id for item in window_by_id.values()
                           if item.path == path
                           and item.start_line <= low <= high <= item.end_line)
    indexability = target.get("indexability")
    require(indexability in {"indexed", "unsupported_artifact_kind"},
            "Unknown target indexability")
    if indexability == "indexed":
        exact_keys(target, {"path", "symbol", "evidence_lines",
                            "source_file_sha256", "evidence_sha256",
                            "indexability"}, "Indexed target")
        require(len(covering) == 1,
                "Indexed Click target must have one exact complete extraction window")
    else:
        exact_keys(target, {"path", "symbol", "evidence_lines",
                            "source_file_sha256", "evidence_sha256", "indexability",
                            "structural_kind", "structural_facts"},
                   "Unsupported target")
        require(not span_covering,
                "Unsupported Click span is covered by an extraction window")
        _validate_structure(target, source, low, high)
    return covering


def validate_gold(gold, public, corpus, scope):
    exact_keys(gold, {
        "version", "classification", "task_id", "corpus_revision",
        "source_inventory_sha256", "scoring", "packet_limits", "feasibility",
        "packet_gate", "groups",
    }, "Evaluator gold")
    require_version_one(gold["version"], "Evaluator gold")
    require(gold["classification"] == "evaluator_only" and gold["task_id"] == TASK_ID
            and gold["corpus_revision"] == CORPUS_REVISION
            and gold["source_inventory_sha256"]
                == scope["hashes"]["included_files_sha256"],
            "Click evaluator-gold identity changed")
    scoring = exact_keys(gold["scoring"], {
        "within_group", "across_groups", "coverage", "fragment_union_allowed",
        "symbol_match_alone_is_evidence",
    }, "Gold scoring")
    require(scoring["within_group"] == "or" and scoring["across_groups"] == "and"
            and type(scoring["coverage"]) is str and scoring["coverage"]
            and scoring["fragment_union_allowed"] is False
            and scoring["symbol_match_alone_is_evidence"] is False,
            "Click gold scoring contract changed")
    limits = exact_keys(gold["packet_limits"],
                        {"max_results", "source_budget", "token_budget", "encoding"},
                        "Gold packet limits")
    request = public["common_request"]
    require(limits["max_results"] == request["max_results"] == 8
            and limits["source_budget"] == request["source_budget"] == 12000
            and limits["token_budget"] == request["token_budget"] == 4096
            and limits["encoding"] == "cl100k_base"
            and all(type(limits[key]) is int
                    for key in ("max_results", "source_budget", "token_budget")),
            "Click gold packet limits differ from the public request")
    groups = gold["groups"]
    require(type(groups) is list and groups, "Gold evidence groups cannot be empty")
    identifiers = [group.get("id") for group in groups]
    require(all(type(item) is str and item for item in identifiers)
            and len(identifiers) == len(set(identifiers)),
            "Gold group IDs must be unique nonempty strings")
    groups_by_id = {group["id"]: group for group in groups}
    window_by_id = {item.id: item for item in scope["windows"]}
    target_count = indexed = unsupported = 0
    indexed_windows = set()
    for group in groups:
        fields = {"id", "need", "importance", "packet_expectation", "why",
                  "alternatives"}
        if group.get("packet_expectation") == "factual_gap_required":
            fields.add("expected_gap_reason")
        exact_keys(group, fields, "Gold group")
        require(group["need"] in NEEDS
                and group["importance"] == "required_for_change"
                and type(group["why"]) is str and group["why"],
                "Gold evidence need or importance changed")
        alternatives = group["alternatives"]
        require(type(alternatives) is list and len(alternatives) == 1,
                "This freeze requires one source-authored alternative per group")
        target = alternatives[0]
        covering = _target_facts(target, corpus, scope, window_by_id)
        target_count += 1
        if target["indexability"] == "indexed":
            require(group["packet_expectation"] == "candidate_or_factual_gap"
                    and "expected_gap_reason" not in group,
                    "Indexed evidence must remain a packet candidate")
            indexed += 1
            indexed_windows.update(covering)
        else:
            require(group["packet_expectation"] == "factual_gap_required"
                    and group.get("expected_gap_reason")
                        == "unsupported_artifact_kind",
                    "Unsupported structure must require a truthful factual gap")
            unsupported += 1

    feasibility = exact_keys(gold["feasibility"], {
        "predeclared_indexable_key_evidence_fits", "reason", "indexed_groups",
        "structurally_unsupported_groups", "minimum_distinct_indexed_excerpts",
        "full_original_window_source_chars",
        "full_original_windows_exceed_source_budget", "token_lower_bound", "basis",
        "disposition", "observed_before_selector_implementation",
    }, "Gold feasibility")
    full_source_chars = sum(len(window_by_id[item].text) for item in indexed_windows)
    require(all(type(feasibility[name]) is int for name in (
                "indexed_groups", "structurally_unsupported_groups",
                "minimum_distinct_indexed_excerpts",
                "full_original_window_source_chars"))
            and feasibility["predeclared_indexable_key_evidence_fits"] is False
            and feasibility["reason"] == "result_cap"
            and feasibility["indexed_groups"] == indexed
            and feasibility["structurally_unsupported_groups"] == unsupported
            and feasibility["minimum_distinct_indexed_excerpts"]
                == len(indexed_windows)
            and feasibility["full_original_window_source_chars"] == full_source_chars
            and feasibility["full_original_windows_exceed_source_budget"]
                is (full_source_chars > limits["source_budget"])
            and feasibility["full_original_windows_exceed_source_budget"] is True
            and feasibility["token_lower_bound"]
                == "not_required_for_the_result_cap_proof"
            and type(feasibility["basis"]) is str and feasibility["basis"]
            and type(feasibility["disposition"]) is str
            and feasibility["disposition"]
            and feasibility["observed_before_selector_implementation"] is True
            and len(indexed_windows) > limits["max_results"],
            "Click feasibility proof or full-window budget relation changed")
    packet_gate = exact_keys(gold["packet_gate"], {
        "minimum_complementary_groups", "require_newly_covered_source_file",
        "no_individual_loss", "eligible_group_ids",
    }, "Gold packet gate")
    eligible = packet_gate["eligible_group_ids"]
    require(type(packet_gate["minimum_complementary_groups"]) is int
            and packet_gate["minimum_complementary_groups"] == 1
            and packet_gate["require_newly_covered_source_file"] is True
            and packet_gate["no_individual_loss"] is True
            and type(eligible) is list and eligible
            and len(eligible) == len(set(eligible)),
            "Click complementary/no-loss gate changed")
    for identifier in eligible:
        require(identifier in groups_by_id
                and groups_by_id[identifier]["need"] in ("implementation", "callers")
                and groups_by_id[identifier]["alternatives"][0]["indexability"]
                    == "indexed",
                "Complementary eligibility must name indexed implementation/caller groups")
    return {"groups": len(groups), "targets": target_count,
            "indexed_targets": indexed, "unsupported_targets": unsupported,
            "minimum_distinct_indexed_excerpts": len(indexed_windows),
            "full_original_window_source_chars": full_source_chars}


def validate_planner_boundary(public_result, gold):
    payloads = {"legacy_control": public_result["control_request"],
                "task_evidence": public_result["treatment_request"]}
    forbidden_keys = {
        "task_id", "groups", "alternatives", "evidence_lines", "evidence_sha256",
        "source_file_sha256", "indexability", "packet_expectation",
        "expected_gap_reason", "eligible_group_ids", "reference_patch",
        "checker_outcomes", "feasibility",
    }

    def keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from keys(child)

    sentinels = set()
    target_symbols = set()
    for group in gold["groups"]:
        sentinels.add(group["id"])
        for target in group["alternatives"]:
            sentinels.update((target["path"], target["evidence_sha256"],
                              target["source_file_sha256"]))
            target_symbols.add(target["symbol"])
    prompt = public_result["task"]["prompt"]
    forbidden_symbols = {symbol for symbol in target_symbols if symbol not in prompt}
    for condition, payload in payloads.items():
        require(not forbidden_keys.intersection(keys(payload)),
                f"Evaluator-only key crossed the {condition} planner boundary")
        serialized = canonical(payload)
        require(all(value.encode() not in serialized for value in sentinels),
                f"Evaluator-only sentinel crossed the {condition} planner boundary")
        require(all(value.encode() not in serialized for value in forbidden_symbols),
                f"Non-prompt evaluator symbol crossed the {condition} planner boundary")
    return {name: sha256(canonical(payload)) for name, payload in payloads.items()}


def _worktree_changed_paths(root):
    rows = subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain=v1", "-uall"], text=True
    ).splitlines()
    changed = set()
    for row in rows:
        require(len(row) >= 4 and " -> " not in row[3:],
                "Child freeze worktree cannot contain renames")
        changed.add(row[3:])
    return changed


def _commit_path_union(root, start_revision, end_revision):
    """Return every path touched in start..end, including add/delete pairs."""
    commits = git(root, "rev-list", "--reverse",
                  f"{start_revision}..{end_revision}").splitlines()
    changed = set()
    for commit in commits:
        changed.update(_commit_changed_paths(root, commit))
    return changed


def _commit_changed_paths(root, commit):
    parents = git(root, "rev-list", "--parents", "-n", "1", commit).split()
    require(len(parents) >= 2, "Every post-base commit must have a parent")
    raw = subprocess.check_output(
        ["git", "-C", str(root), "diff", "--no-renames", "--name-only", "-z",
         parents[1], commit]
    ).decode("utf-8", "surrogateescape")
    return {path for path in raw.split("\0") if path}


def _commit_blob(root, commit, path):
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"{commit}:{path}"], check=False,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    return result.stdout if result.returncode == 0 else None


def _tree_has_path(root, commit, path):
    raw = subprocess.check_output(
        ["git", "-C", str(root), "ls-tree", "-r", "--name-only", "-z", commit,
         "--", path]
    )
    return bool(raw)


def _path_is_within(path, directory):
    directory = str(relative_path(directory)).rstrip("/")
    return path == directory or path.startswith(directory + "/")


def _validate_execution_handoff_blob(raw, *, child_anchor, child_binding_bytes,
                                     output_path):
    try:
        execution = decode_json(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Execution handoff binding is not strict UTF-8 JSON") from error
    require(type(execution) is dict
            and execution.get("classification")
                == "task_evidence_untouched_click_execution_freeze",
            "Execution handoff classification changed")
    handoff = execution.get("child_handoff")
    fields = set(EXECUTION_HANDOFF_CONTRACT["child_handoff_required_fields"])
    exact_keys(handoff, fields, "Execution child handoff")
    require(handoff["child_freeze_revision"] == child_anchor
            and handoff["child_binding_sha256"] == sha256(child_binding_bytes)
            and is_hex_digest(handoff["requests_packet_gate_receipt_sha256"])
            and is_hex_digest(handoff["requests_native_latency_gate_receipt_sha256"])
            and type(handoff["attempt_id"]) is str
            and bool(handoff["attempt_id"].strip())
            and handoff["result_path"] == output_path
            and handoff["result_path_state_at_execution_freeze"] == "absent",
            "Execution child handoff does not bind the exact child/result chronology")
    return handoff


def _validate_execution_history(root, child_anchor, head, child_binding_bytes,
                                *, output_path, execution_binding_path):
    """Require a valid committed handoff before any post-child result-path touch."""
    commits = git(root, "rev-list", "--reverse",
                  f"{child_anchor}..{head}").splitlines()
    changed_by_commit = {
        commit: _commit_changed_paths(root, commit) for commit in commits
    }
    binding_touches = [
        commit for commit in commits
        if execution_binding_path in changed_by_commit[commit]
    ]
    handoff_commit = None
    if binding_touches:
        first_touches = [
            commit for commit in binding_touches
            if not any(other != commit and _is_ancestor(root, other, commit)
                       for other in binding_touches)
        ]
        require(len(first_touches) == 1,
                "Execution handoff history has parallel first binding touches")
        handoff_commit = first_touches[0]
        require(all(_is_ancestor(root, handoff_commit, commit)
                    for commit in binding_touches),
                "Execution-binding history must descend from its first handoff")
        blob = _commit_blob(root, handoff_commit, execution_binding_path)
        require(blob is not None,
                "The first execution-binding touch must commit a binding")
        _validate_execution_handoff_blob(
            blob, child_anchor=child_anchor,
            child_binding_bytes=child_binding_bytes, output_path=output_path,
        )
        require(not _tree_has_path(root, handoff_commit, output_path),
                "Execution handoff commit must retain an absent result path")

    for commit, changed_paths in changed_by_commit.items():
        if any(_path_is_within(path, output_path) for path in changed_paths):
            require(handoff_commit is not None
                    and _is_ancestor(root, handoff_commit, commit),
                    "Click result history must descend from a valid committed "
                    "execution handoff")

    changed = _worktree_changed_paths(root)
    output_exists = ((root / output_path).exists()
                     or (root / output_path).is_symlink())
    execution_exists = ((root / execution_binding_path).exists()
                        or (root / execution_binding_path).is_symlink())
    output_changed = any(_path_is_within(path, output_path) for path in changed)
    execution_changed = execution_binding_path in changed
    require(handoff_commit is not None
            or not (output_exists or output_changed or execution_exists
                    or execution_changed),
            "Current Click execution/result state lacks a committed execution handoff")
    return handoff_commit


def validate_project_state(root, artifacts, binding_path, binding_bytes,
                           *, base_revision=PROJECT_BASE_REVISION,
                           output_path=OUTPUT_PATH,
                           parent_anchor=PARENT_ANCHOR_REVISION,
                           selection_revision=SELECTION_REVISION,
                           selection_path=SELECTION_PATH,
                           selection_sha256=SELECTION_SHA256,
                           child_paths=CHILD_PATHS,
                           execution_binding_path=EXECUTION_BINDING_PATH):
    """Find an exact child anchor or validate a seven-path pre-anchor candidate."""
    root = Path(root)
    binding_path = str(relative_path(binding_path))
    output_path = str(relative_path(output_path))
    execution_binding_path = str(relative_path(execution_binding_path))
    protected = set(artifacts) | {binding_path}
    require((root / ".git").exists(),
            "Untouched freeze chronology validation requires a Git worktree")
    head = git(root, "rev-parse", "HEAD")
    require(base_revision == selection_revision,
            "Child base must be the committed selection revision")
    require(_is_ancestor(root, parent_anchor, base_revision)
            and _is_ancestor(root, base_revision, head),
            "Project history does not descend from the Phase 0 and selection anchors")
    require(_commit_path_union(root, parent_anchor, base_revision)
            == {selection_path},
            "Selection-lock chronology changed between the parent anchor and child base")
    selection_blob = _commit_blob(root, base_revision, selection_path)
    require(selection_blob is not None and sha256(selection_blob) == selection_sha256,
            "Selection-lock commit bytes changed")
    require(not _tree_has_path(root, base_revision, output_path),
            "Click packet output already existed at the child base revision")
    require(_commit_blob(root, base_revision, execution_binding_path) is None,
            "Click execution binding already existed at the child base revision")

    commits = git(root, "rev-list", "--reverse",
                  f"{base_revision}..{head}").splitlines()
    for commit in commits:
        if _commit_blob(root, commit, binding_path) != binding_bytes:
            continue
        if any((blob := _commit_blob(root, commit, path)) is None
               or sha256(blob) != expected for path, expected in artifacts.items()):
            continue
        if not _commit_path_union(root, base_revision, commit) <= set(child_paths):
            continue
        if _tree_has_path(root, commit, output_path):
            continue
        after = _commit_path_union(root, commit, head)
        if after.intersection(protected):
            continue
        outside_child_history = [
            history_commit for history_commit in commits
            if (_commit_changed_paths(root, history_commit) - set(child_paths))
        ]
        if any(not _is_ancestor(root, commit, history_commit)
               for history_commit in outside_child_history):
            continue
        changed = _worktree_changed_paths(root)
        require(not changed.intersection(protected),
                "A protected child-freeze artifact changed after the anchor")
        handoff_commit = _validate_execution_history(
            root, commit, head, binding_bytes, output_path=output_path,
            execution_binding_path=execution_binding_path,
        )
        return {"head": head, "committed": True,
                "lifecycle": ("execution_handoff_ancestor" if handoff_commit
                              else "committed_freeze_ancestor"),
                "freeze_commit": commit,
                "execution_handoff_commit": handoff_commit,
                "changed_paths": sorted(changed)}

    committed = _commit_path_union(root, base_revision, head)
    require(committed <= set(child_paths),
            "A pre-anchor committed path falls outside the seven child artifacts")
    changed = _worktree_changed_paths(root)
    require(not (root / output_path).exists()
            and not (root / output_path).is_symlink(),
            "Click packet output must be absent before the child anchor")
    require(not (root / execution_binding_path).exists()
            and not (root / execution_binding_path).is_symlink(),
            "Click execution binding must be absent before the child anchor")
    require(changed <= set(child_paths),
            "A pre-anchor worktree path falls outside the seven child artifacts")
    return {"head": head, "committed": head != base_revision,
            "lifecycle": "freeze_candidate", "freeze_commit": None,
            "execution_handoff_commit": None, "changed_paths": sorted(changed)}


def validate_binding(binding_path=DEFAULT_BINDING, *, root=ROOT):
    binding_path = Path(binding_path)
    expected_binding_path = (Path(root)
                             / "benchmarks/task_evidence_gold/untouched_click_freeze.json")
    require(binding_path.resolve() == expected_binding_path.resolve(),
            "Child freeze validation requires the official repository binding path")
    binding = read_json(binding_path)
    exact_keys(binding, {
        "version", "classification", "status", "parent_phase0", "selection_lock",
        "project_base_revision", "worktree_policy", "corpus_revision",
        "source_inventory_sha256", "calls", "artifacts",
    }, "Untouched Click binding")
    require_version_one(binding["version"], "Child binding")
    require(binding["classification"] == "task_evidence_untouched_click_freeze"
            and binding["status"]
                == "freeze_candidate_prepared_before_selector_implementation",
            "Unsupported untouched Click binding")
    parent = exact_keys(binding["parent_phase0"], {
        "anchor_revision", "binding_path", "binding_sha256", "validator_path",
        "validator_sha256",
    }, "Parent Phase 0 binding")
    require(parent == {
        "anchor_revision": PARENT_ANCHOR_REVISION,
        "binding_path": PARENT_BINDING_PATH,
        "binding_sha256": PARENT_BINDING_SHA256,
        "validator_path": PARENT_VALIDATOR_PATH,
        "validator_sha256": PARENT_VALIDATOR_SHA256,
    }, "Parent Phase 0 authority changed")
    selection = exact_keys(binding["selection_lock"],
                           {"revision", "path", "sha256"},
                           "Binding selection lock")
    require(selection == {"revision": SELECTION_REVISION, "path": SELECTION_PATH,
                          "sha256": SELECTION_SHA256},
            "Binding selection-lock authority changed")
    require(binding["project_base_revision"] == PROJECT_BASE_REVISION
            and binding["worktree_policy"] == WORKTREE_POLICY
            and binding["corpus_revision"] == CORPUS_REVISION
            and binding["source_inventory_sha256"]
                == EXPECTED_INVENTORY["included_files_sha256"]
            and binding["calls"] == EXPECTED_CALLS
            and all(type(value) is int for value in binding["calls"].values()),
            "Child chronology, corpus, inventory, or zero-call binding changed")
    artifacts = binding["artifacts"]
    require(type(artifacts) is dict and set(artifacts) == BOUND_ARTIFACT_PATHS,
            "Untouched Click bound artifact closure changed")
    for name, expected in artifacts.items():
        path = Path(root) / relative_path(name)
        require(path.is_file() and type(expected) is str and len(expected) == 64
                and sha256(path.read_bytes()) == expected,
                f"Untouched Click bound artifact changed: {name}")
    require(artifacts[PARENT_BINDING_PATH] == PARENT_BINDING_SHA256
            and artifacts[PARENT_VALIDATOR_PATH] == PARENT_VALIDATOR_SHA256
            and artifacts[SELECTION_PATH] == SELECTION_SHA256,
            "Parent or selection authority hash is not bound in the artifact closure")
    state = validate_project_state(
        root, artifacts,
        "benchmarks/task_evidence_gold/untouched_click_freeze.json",
        binding_path.read_bytes(),
    )
    return {"artifact_count": len(artifacts), "artifacts": artifacts,
            "calls": EXPECTED_CALLS, "status": binding["status"],
            "project_state": state}


def validate(public_path=DEFAULT_PUBLIC, gold_path=DEFAULT_GOLD,
             corpus=DEFAULT_CORPUS, *, binding_path=DEFAULT_BINDING,
             protocol_path=DEFAULT_PROTOCOL, root=ROOT):
    binding_result = validate_binding(binding_path, root=root)
    required_inputs = {
        "public": (public_path, "benchmarks/task_evidence_untouched_click.json"),
        "gold": (gold_path, "benchmarks/task_evidence_gold/click_atomic_transactions.json"),
        "protocol": (protocol_path, "docs/task-evidence-untouched-click-protocol.md"),
    }
    for label, (path, artifact_name) in required_inputs.items():
        require(sha256(Path(path).read_bytes())
                == binding_result["artifacts"][artifact_name],
                f"Supplied {label} is not the bound child-freeze artifact")
    public = read_json(public_path)
    public_result = validate_public(public, root=root)
    scope = reconstruct_scope(public, Path(corpus).resolve())
    gold = read_json(gold_path)
    gold_result = validate_gold(gold, public, Path(corpus).resolve(), scope)
    boundary = validate_planner_boundary(public_result, gold)
    return {"status": "passed", "calls": EXPECTED_CALLS,
            "scope": {**scope["counts"], **scope["hashes"]},
            "gold": gold_result,
            "binding": {key: value for key, value in binding_result.items()
                        if key != "artifacts"},
            "planner_boundary": boundary,
            "requests_differ_only_by_needed_evidence": (
                "needed_evidence" not in public_result["control_request"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", type=Path, default=DEFAULT_PUBLIC)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--binding", type=Path, default=DEFAULT_BINDING)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    args = parser.parse_args()
    print(json.dumps(validate(
        args.public, args.gold, args.corpus, binding_path=args.binding,
        protocol_path=args.protocol,
    ), sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
