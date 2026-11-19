#!/usr/bin/env python3
"""Validate the source-only Requests task-evidence Phase 0 freeze candidate.

This command performs no embedding, retrieval, planner, model, or network call.
It checks the harness contract and exact planner boundary separately from evaluator-only labels,
then reconstructs source membership and evidence facts from the pinned checkout.
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
from semantic_search.languages import EXTRACTORS, extractor_fingerprint, path_excluded
from semantic_search.source import physical_lines


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PUBLIC = ROOT / "benchmarks/task_evidence_requests.json"
DEFAULT_GOLD = ROOT / "benchmarks/task_evidence_gold/requests_response_limit.json"
DEFAULT_BINDING = ROOT / "benchmarks/task_evidence_gold/phase0_freeze.json"
DEFAULT_CORPUS = ROOT / ".cache/corpora/requests"
NEEDS = ("implementation", "callers", "tests", "behavioral_constraints")
EXCLUSIONS = ["docs/*.py", "setup.py"]
PREFIX_GLOBS = {
    "src/requests/": ["src/requests/*.py"],
    "tests/": ["tests/*.py", "tests/testserver/*.py"],
}
REQUEST_FIELDS = {"query", "retrieval", "rerank", "rerank_provider", "candidates",
                  "max_results", "source_budget", "token_budget", "include_dependencies",
                  "overlays"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def read_json(path):
    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(Path(path).read_text(encoding="utf-8"),
                      object_pairs_hook=reject_duplicates)


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


def strict_int(value, name, *, minimum=0):
    require(type(value) is int and value >= minimum,
            f"{name} must be an integer >= {minimum}")
    return value


def exact_keys(value, expected, label):
    require(type(value) is dict and set(value) == set(expected),
            f"{label} fields changed")
    return value


def segment_glob(path, pattern):
    """Match an anchored POSIX glob where '*' never crosses a slash."""
    path_parts, pattern_parts = path.split("/"), pattern.split("/")
    return (len(path_parts) == len(pattern_parts)
            and all(fnmatch.fnmatchcase(part, expected)
                    for part, expected in zip(path_parts, pattern_parts)))


def planner_request(public, condition_id):
    """Resolve exactly the request that a condition may pass to the backend."""
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


def validate_public(public, *, root=ROOT):
    exact_keys(public, {"version", "phase", "classification", "task", "corpus",
                        "index_scope", "artifact_kinds", "common_request", "conditions",
                        "planner_boundary", "schedule", "phase_0_gates"},
               "Harness manifest")
    require(public.get("version") == 1,
            "Unsupported public freeze version")
    require(public.get("phase") == "phase_0_scope_freeze",
            "Unexpected public freeze phase")
    require(public.get("classification") == (
        "Harness-visible Requests task-evidence scope and paired packet contract; "
        "only the resolved request payload crosses the planner boundary"),
        "Harness visibility classification changed")
    task = exact_keys(public.get("task"),
                      {"id", "prompt", "prompt_sha256", "source_manifest"}, "Task")
    require(task.get("id") == "requests_response_limit",
            "The first workflow must be requests_response_limit")
    source = task.get("source_manifest")
    require(type(source) is dict and set(source) == {"path", "sha256"},
            "Task source manifest binding is incomplete")
    source_path = root / relative_path(source["path"])
    require(source_path.is_file() and sha256(source_path.read_bytes()) == source["sha256"],
            "Public workflow source manifest changed")
    workflow = read_json(source_path)
    matches = [item for item in workflow.get("tasks", []) if item.get("id") == task["id"]]
    require(len(matches) == 1, "Public workflow task is not unique")
    original = matches[0]
    require(task.get("prompt") == original.get("prompt")
            and task.get("prompt_sha256") == sha256(task["prompt"].encode()),
            "Task prompt differs from its frozen public source")
    corpus = exact_keys(public.get("corpus"),
                        {"id", "repo_url", "revision", "membership"}, "Corpus")
    require(corpus.get("id") == "requests"
            and corpus.get("revision") == original.get("revision")
            and corpus.get("repo_url") == original.get("repo_url"),
            "Corpus identity differs from the public workflow")

    scope = exact_keys(public.get("index_scope"),
                       {"source_root", "source_exclusions", "required_prefixes",
                        "glob_contract", "extraction_policy", "extractor_fingerprint",
                        "window_policy", "expected_inventory"}, "Index scope")
    require(scope.get("source_root") == ".",
            "Task-evidence scope must include the repository root")
    exclusions = scope.get("source_exclusions")
    require(exclusions == EXCLUSIONS, "Frozen source exclusions changed")
    require(scope.get("extraction_policy") == "saved-implementations-v3"
            and scope.get("extractor_fingerprint") == extractor_fingerprint()
            and scope.get("window_policy") == {"max_lines": 160, "overlap_lines": 40},
            "Extraction fingerprint or window policy changed")
    prefixes = scope.get("required_prefixes")
    require(type(prefixes) is list and len(prefixes) == 2,
            "Scope must freeze implementation and test prefixes")
    require([item.get("artifact_kind") for item in prefixes] == ["implementation", "test"]
            and [item.get("prefix") for item in prefixes] == ["src/requests/", "tests/"],
            "Required artifact prefixes changed")
    for entry in prefixes:
        exact_keys(entry, {"prefix", "artifact_kind", "membership_globs", "expected_files"},
                   "Required prefix")
        relative_path(entry["prefix"].rstrip("/"))
        strict_int(entry.get("expected_files"), "expected_files", minimum=1)
        require(entry.get("membership_globs") == PREFIX_GLOBS[entry["prefix"]],
                "Frozen membership globs changed")
    inventory = exact_keys(scope.get("expected_inventory"),
        {"eligible_supported_files", "excluded_supported_files", "included_supported_files",
         "parsed_files", "implementation_windows", "test_windows", "total_windows",
         "included_files_sha256", "implementation_files_sha256", "test_files_sha256",
         "excluded_files_sha256", "windows_sha256", "file_inventory_hash_contract",
         "window_inventory_hash_contract"}, "Expected inventory")
    for name in ("eligible_supported_files", "excluded_supported_files",
                 "included_supported_files", "parsed_files", "implementation_windows",
                 "test_windows", "total_windows"):
        strict_int(inventory[name], name)
    for name in ("included_files_sha256", "implementation_files_sha256",
                 "test_files_sha256", "excluded_files_sha256", "windows_sha256"):
        require(type(inventory[name]) is str and len(inventory[name]) == 64,
                f"{name} must be a SHA-256 digest")

    artifact_kinds = exact_keys(public.get("artifact_kinds"),
                                 {"indexed", "explicitly_unsupported"}, "Artifact kinds")
    require(artifact_kinds == {
        "indexed": [
            {"kind": "implementation", "unit": "Python function or method implementation window"},
            {"kind": "test", "unit": "Python test, fixture, helper, function, or method implementation window"},
        ],
        "explicitly_unsupported": [
            {"kind": "configuration", "gap_reason": "unsupported_source_kind"},
            {"kind": "documentation", "gap_reason": "unsupported_source_kind"},
            {"kind": "non_python_fixture", "gap_reason": "unsupported_source_kind"},
            {"kind": "python_module_or_class_structure_without_an_implementation_body",
             "gap_reason": "unsupported_artifact_kind"},
        ],
    }, "Frozen artifact-kind support changed")
    common = public.get("common_request")
    expected_common = {"query_from", "retrieval", "rerank", "rerank_provider", "candidates",
                       "max_results", "source_budget", "token_budget", "include_dependencies",
                       "overlays"}
    require(type(common) is dict and set(common) == expected_common,
            "Common request fields changed")
    require(common["query_from"] == "task.prompt" and common["retrieval"] == "dense"
            and common["rerank"] is False and common["rerank_provider"] == "h2o"
            and common["include_dependencies"] is True and common["overlays"] == [],
            "Common retrieval settings changed")
    for name, expected in (("candidates", 50), ("max_results", 8),
                           ("source_budget", 12000), ("token_budget", 4096)):
        require(strict_int(common[name], name, minimum=1) == expected,
                f"Frozen {name} changed")
    conditions = public.get("conditions")
    require(type(conditions) is list and len(conditions) == 2
            and [item.get("id") for item in conditions] == ["legacy_control", "task_evidence"]
            and conditions[0].get("needed_evidence") is None
            and conditions[1].get("needed_evidence") == list(NEEDS),
            "Paired conditions or evidence request changed")
    exact_keys(conditions[0], {"id", "needed_evidence", "null_behavior"},
               "Legacy condition")
    exact_keys(conditions[1], {"id", "needed_evidence"}, "Task-evidence condition")
    control, treatment = planner_request(public, "legacy_control"), planner_request(public, "task_evidence")
    require("needed_evidence" not in control
            and {key: value for key, value in treatment.items() if key != "needed_evidence"} == control,
            "Conditions may differ only by needed_evidence")
    require(set(control) == REQUEST_FIELDS and set(treatment) == REQUEST_FIELDS | {"needed_evidence"},
            "Resolved backend request schema changed")
    boundary = exact_keys(public.get("planner_boundary"),
                          {"input", "control_request_sha256",
                           "task_evidence_request_sha256", "hash_contract"},
                          "Planner boundary")
    require(boundary["control_request_sha256"] == sha256(canonical(control))
            and boundary["task_evidence_request_sha256"] == sha256(canonical(treatment)),
            "Resolved planner request hash changed")

    schedule = exact_keys(public.get("schedule"),
                          {"packet_gate", "native_latency_gate",
                           "untouched_validation_prerequisite", "agent_evaluation"},
                          "Schedule")
    packet = exact_keys(schedule["packet_gate"],
                        {"kind", "pairs", "replays_per_condition", "pair_order",
                         "shared_retrieval_capture", "required_before_agent_trials",
                         "complementary_gain"}, "Packet gate")
    packet_order = [["legacy_control", "task_evidence"],
                    ["task_evidence", "legacy_control"],
                    ["legacy_control", "task_evidence"]]
    require(packet["kind"] == "paired_packet_replay" and packet["pairs"] == 3
            and packet["replays_per_condition"] == 3
            and packet["pair_order"] == packet_order
            and packet["shared_retrieval_capture"] is True
            and packet["required_before_agent_trials"] is True,
            "Packet gate schedule changed")
    latency = exact_keys(schedule["native_latency_gate"],
                         {"warmups", "measured_searches_per_condition", "measured_pair_order",
                          "timing_boundary", "cache_policy", "statistic", "maximum_ratio"},
                         "Native latency gate")
    require(latency["warmups"] == 6 and latency["measured_searches_per_condition"] == 42
            and latency["maximum_ratio"] == 1.25,
            "Native latency schedule changed")
    prerequisite = exact_keys(schedule["untouched_validation_prerequisite"],
                              {"selection_status", "selection_required_before",
                               "execution_required_before", "rule"},
                              "Untouched validation prerequisite")
    require(prerequisite["selection_status"] == "not_yet_frozen"
            and prerequisite["selection_required_before"]
                == "selector_implementation_or_requests_packet_outcomes"
            and prerequisite["execution_required_before"] == "coding_agent_trials",
            "Untouched-codebase prerequisite changed")
    evaluation = exact_keys(schedule["agent_evaluation"],
                            {"kind", "repetitions", "pairs", "trial_count",
                             "fresh_checkout_per_trial", "fresh_conversation_per_trial",
                             "retry_policy", "pair_order"}, "Agent evaluation")
    order = [["legacy_control", "task_evidence"], ["task_evidence", "legacy_control"],
             ["legacy_control", "task_evidence"], ["task_evidence", "legacy_control"]]
    require(evaluation.get("kind") == "fresh_paired_agent_repetitions"
            and evaluation.get("repetitions") == 4 and evaluation.get("pairs") == 4
            and evaluation.get("trial_count") == 8
            and evaluation.get("fresh_checkout_per_trial") is True
            and evaluation.get("fresh_conversation_per_trial") is True
            and [item.get("conditions") for item in evaluation.get("pair_order", [])] == order,
            "Four balanced paired repetitions are required")
    require(len(evaluation["pair_order"]) == 4, "Agent pair order length changed")
    for repetition, entry in enumerate(evaluation["pair_order"], 1):
        exact_keys(entry, {"repetition", "conditions"}, "Agent pair")
        require(entry["repetition"] == repetition, "Agent repetition numbering changed")
    gate_ids = [
        "source_freeze_integrity", "retrieval_parity", "legacy_path_equality",
        "complementary_gain", "no_control_coverage_loss", "exact_budgets_and_source",
        "truthful_gap_reconciliation", "deterministic_replay", "native_latency",
        "planner_gold_isolation",
    ]
    gates = public.get("phase_0_gates")
    require(type(gates) is list and len(gates) == len(gate_ids),
            "Phase 0 gate count changed")
    for gate, identifier in zip(gates, gate_ids):
        exact_keys(gate, {"id", "requirement"}, "Phase 0 gate")
        require(gate["id"] == identifier
                and type(gate["requirement"]) is str and gate["requirement"],
                "Phase 0 gate contract changed")
    return {"task": original, "control_request": control, "treatment_request": treatment,
            "request_hashes": {"legacy_control": sha256(canonical(control)),
                               "task_evidence": sha256(canonical(treatment))}}


def reconstruct_scope(public, corpus):
    scope = public["index_scope"]
    require(corpus.is_dir(), f"Missing pinned corpus: {corpus}")
    require(git(corpus, "rev-parse", "HEAD") == public["corpus"]["revision"],
            "Pinned corpus revision changed")
    require(not git(corpus, "status", "--porcelain=v1"),
            "Pinned corpus must be clean")
    tracked = [item for item in subprocess.check_output(
        ["git", "-C", str(corpus), "ls-files", "-z"]
    ).decode("utf-8", "surrogateescape").split("\0") if item]
    supported = sorted(path for path in tracked if Path(path).suffix in EXTRACTORS)
    exclusions = scope["source_exclusions"]
    excluded = sorted(path for path in supported if path_excluded(path, exclusions))
    included = sorted(set(supported) - set(excluded))
    prefixes = scope["required_prefixes"]
    classified = {}
    for entry in prefixes:
        members = sorted(path for path in included
                         if any(segment_glob(path, pattern)
                                for pattern in entry["membership_globs"]))
        require(len(members) == entry["expected_files"],
                f"{entry['artifact_kind']} membership count changed")
        for path in members:
            require(path not in classified, "Artifact membership globs overlap")
            classified[path] = entry["artifact_kind"]
    require(set(classified) == set(included),
            "Included source contains an unclassified artifact path")

    def inventory(paths):
        return {path: sha256((corpus / path).read_bytes()) for path in paths}

    included_inventory = inventory(included)
    excluded_inventory = inventory(excluded)
    implementation_inventory = inventory(
        sorted(path for path, kind in classified.items() if kind == "implementation"))
    test_inventory = inventory(
        sorted(path for path, kind in classified.items() if kind == "test"))
    windows = extract_snippets(
        corpus, scope["source_root"],
        max_lines=scope["window_policy"]["max_lines"],
        overlap_lines=scope["window_policy"]["overlap_lines"],
        source_exclusions=exclusions,
    )
    require(len({item.id for item in windows}) == len(windows),
            "Extracted window IDs are not unique")
    window_inventory = {item.id: item.content_hash for item in sorted(windows, key=lambda row: row.id)}
    expected = scope["expected_inventory"]
    actual_counts = {
        "eligible_supported_files": len(supported),
        "excluded_supported_files": len(excluded),
        "included_supported_files": len(included),
        "parsed_files": len(included),
        "implementation_windows": sum(item.path.startswith("src/requests/") for item in windows),
        "test_windows": sum(item.path.startswith("tests/") for item in windows),
        "total_windows": len(windows),
    }
    require(all(expected.get(key) == value for key, value in actual_counts.items()),
            "Frozen source/window counts changed")
    actual_hashes = {
        "included_files_sha256": sha256(canonical(included_inventory)),
        "implementation_files_sha256": sha256(canonical(implementation_inventory)),
        "test_files_sha256": sha256(canonical(test_inventory)),
        "excluded_files_sha256": sha256(canonical(excluded_inventory)),
        "windows_sha256": sha256(canonical(window_inventory)),
    }
    require(all(expected.get(key) == value for key, value in actual_hashes.items()),
            "Frozen source/window inventory hash changed")
    return {"included": included_inventory, "excluded": excluded_inventory,
            "classified": classified, "windows": windows, "counts": actual_counts,
            "hashes": actual_hashes}


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


def _class_facts(node):
    fields = [_assignment_name(child) for child in node.body
              if isinstance(child, ast.AnnAssign)]
    total = None
    for keyword in node.keywords:
        if keyword.arg == "total":
            total = ast.literal_eval(keyword.value)
    return {"name": node.name, "bases": [ast.unparse(base) for base in node.bases],
            "fields": fields, "total": total}


def _validate_structure(target, source, low, high):
    kind = target.get("structural_kind")
    symbol = target["symbol"]
    facts = target.get("structural_facts")
    require(type(facts) is dict, "Unsupported target must freeze structural facts")
    tree = ast.parse(source, filename=target["path"])
    classes = _class_nodes(tree)
    if kind == "module_exports":
        require(symbol == "module.exports", "Module-export structural symbol changed")
        exact_keys(facts, {"imported_names", "exported_names"}, "Module-export facts")
        imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)
                   and node.module == "exceptions"
                   and low <= node.lineno <= (node.end_lineno or node.lineno) <= high]
        assignments = [node for node in tree.body
                       if isinstance(node, (ast.Assign, ast.AnnAssign))
                       and _assignment_name(node) == "__all__"
                       and low <= node.lineno <= (node.end_lineno or node.lineno) <= high]
        require(len(imports) == len(assignments) == 1,
                "Module-export evidence lacks one exact import or __all__ structure")
        imported = {alias.asname or alias.name for alias in imports[0].names}
        exported = set(ast.literal_eval(assignments[0].value))
        require(type(facts["imported_names"]) is list
                and type(facts["exported_names"]) is list
                and set(facts["imported_names"]) <= imported
                and set(facts["exported_names"]) <= exported
                and facts["imported_names"] == facts["exported_names"]
                and facts["imported_names"],
                "Frozen public-export names are not both imported and exported")
    elif kind == "class_declaration":
        exact_keys(facts, {"bases", "annotated_attributes"}, "Class facts")
        node = classes.get(symbol)
        require(node is not None and low <= node.lineno <= high,
                "Class structural symbol does not exist at the evidence span")
        actual = {"bases": [ast.unparse(base) for base in node.bases],
                  "annotated_attributes": [_assignment_name(child) for child in node.body
                                           if isinstance(child, ast.AnnAssign)]}
        require(facts == actual, "Class bases or annotated attributes changed")
    elif kind == "class_attribute":
        exact_keys(facts, {"list_members"}, "Class-attribute facts")
        parts = symbol.split(".")
        require(len(parts) >= 2, "Class-attribute symbol must be qualified")
        class_name, attribute = ".".join(parts[:-1]), parts[-1]
        node = classes.get(class_name)
        assignments = [] if node is None else [child for child in node.body
            if isinstance(child, (ast.Assign, ast.AnnAssign))
            and _assignment_name(child) == attribute]
        require(len(assignments) == 1 and low <= assignments[0].lineno
                and (assignments[0].end_lineno or assignments[0].lineno) <= high,
                "Class-attribute structural symbol does not match the evidence span")
        require(facts["list_members"] == ast.literal_eval(assignments[0].value),
                "Frozen class-attribute list members changed")
    elif kind == "class_family":
        exact_keys(facts, {"classes"}, "Class-family facts")
        expected = facts["classes"]
        require(type(expected) is list and expected and symbol == expected[0].get("name"),
                "Class-family structural facts changed")
        actual = []
        for item in expected:
            exact_keys(item, {"name", "bases", "fields", "total"}, "TypedDict class fact")
            node = classes.get(item["name"])
            require(node is not None and low <= node.lineno
                    and (node.end_lineno or node.lineno) <= high,
                    "Class-family evidence does not contain every declared class")
            actual.append(_class_facts(node))
        require(expected == actual, "TypedDict bases, fields, or total contract changed")
    else:
        raise ValueError("Unknown unsupported structural kind")


def _target_facts(target, corpus, scope, window_by_id):
    path = str(relative_path(target.get("path")))
    require(path in scope["included"], "Gold target is outside frozen indexed membership")
    symbol = target.get("symbol")
    require(type(symbol) is str and symbol, "Gold target symbol must be nonempty")
    interval = target.get("evidence_lines")
    require(type(interval) is list and len(interval) == 2
            and all(type(number) is int for number in interval),
            "Evidence interval must contain two strict integers")
    low, high = interval
    source = (corpus / path).read_text(encoding="utf-8")
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
    require(indexability in ("indexed", "unsupported_artifact_kind"),
            "Unknown target indexability")
    if indexability == "indexed":
        exact_keys(target, {"path", "symbol", "evidence_lines", "source_file_sha256",
                            "evidence_sha256", "indexability"}, "Indexed target")
        require(covering, "Indexed gold has no one complete extraction window")
    else:
        expected = {"path", "symbol", "evidence_lines", "source_file_sha256",
                    "evidence_sha256", "indexability", "structural_kind",
                    "structural_facts"}
        exact_keys(target, expected, "Unsupported target")
        require(not span_covering,
                "Unsupported gold span is covered by an extraction window")
        _validate_structure(target, source, low, high)
    return covering


def validate_gold(gold, public, corpus, scope):
    exact_keys(gold, {"version", "classification", "task_id", "corpus_revision",
                      "source_inventory_sha256", "scoring", "packet_limits",
                      "feasibility", "packet_gate", "groups"}, "Evaluator gold")
    require(gold.get("version") == 1 and gold.get("classification") == "evaluator_only",
            "Unsupported evaluator-only gold manifest")
    require(gold.get("task_id") == public["task"]["id"],
            "Gold task differs from public task")
    require(gold.get("corpus_revision") == public["corpus"]["revision"]
            and gold.get("source_inventory_sha256") == scope["hashes"]["included_files_sha256"],
            "Gold corpus/source inventory binding changed")
    scoring = exact_keys(gold.get("scoring"),
                         {"within_group", "across_groups", "coverage",
                          "fragment_union_allowed", "symbol_match_alone_is_evidence"},
                         "Gold scoring")
    require(scoring.get("within_group") == "or"
            and scoring.get("across_groups") == "and"
            and scoring.get("fragment_union_allowed") is False
            and scoring.get("symbol_match_alone_is_evidence") is False,
            "Gold scoring contract changed")
    groups = gold.get("groups")
    require(type(groups) is list and groups, "Gold evidence groups cannot be empty")
    ids = [group.get("id") for group in groups]
    require(all(type(item) is str and item for item in ids) and len(set(ids)) == len(ids),
            "Gold group IDs must be unique nonempty strings")
    window_by_id = {item.id: item for item in scope["windows"]}
    target_count = indexed = unsupported = 0
    indexed_window_ids = set()
    groups_by_id = {group["id"]: group for group in groups}
    for group in groups:
        expected_group = {"id", "need", "importance", "packet_expectation", "why",
                          "alternatives"}
        if group.get("packet_expectation") == "factual_gap_required":
            expected_group.add("expected_gap_reason")
        exact_keys(group, expected_group, "Gold group")
        require(group.get("need") in NEEDS, "Gold group has an unknown requested need")
        require(group.get("importance") == "required_for_change",
                "Every frozen checklist group must retain required importance")
        alternatives = group.get("alternatives")
        require(type(alternatives) is list and len(alternatives) == 1,
                "This freeze requires exactly one alternative per evidence group")
        seen = set()
        for target in alternatives:
            key = (target.get("path"), target.get("symbol"), tuple(target.get("evidence_lines", [])))
            require(key not in seen, "Duplicate evidence alternative")
            seen.add(key)
            covering = _target_facts(target, corpus, scope, window_by_id)
            target_count += 1
            if target["indexability"] == "indexed":
                require(len(covering) == 1,
                        "Feasibility freeze requires one exact original window per indexed target")
                indexed_window_ids.update(covering)
                indexed += 1
            else:
                unsupported += 1
        expectation = group.get("packet_expectation")
        require(expectation in ("candidate_or_factual_gap", "factual_gap_required"),
                "Unknown packet expectation")
        unsupported_group = all(item["indexability"] == "unsupported_artifact_kind"
                                for item in alternatives)
        require((expectation == "factual_gap_required") is unsupported_group,
                "Structurally unsupported groups must require a factual gap")
        if expectation == "factual_gap_required":
            require(group.get("expected_gap_reason") == "unsupported_artifact_kind",
                    "Unsupported group has the wrong expected gap reason")
    limits = gold.get("packet_limits")
    require(limits == {"max_results": 8, "source_budget": 12000,
                       "token_budget": 4096, "encoding": "cl100k_base"},
            "Evaluator packet limits differ from the public request")
    feasibility = gold.get("feasibility")
    exact_keys(feasibility,
               {"predeclared_indexable_key_evidence_fits", "reason", "indexed_groups",
                "structurally_unsupported_groups", "minimum_distinct_indexed_excerpts",
                "full_original_window_source_chars",
                "full_original_windows_exceed_source_budget", "token_lower_bound",
                "basis", "disposition",
                "observed_before_selector_implementation"}, "Gold feasibility")
    require(feasibility.get("predeclared_indexable_key_evidence_fits") is False
            and feasibility.get("reason") == "result_cap",
            "Gold must preserve the predeclared key-evidence result-cap limit")
    full_source_chars = sum(len(window_by_id[identifier].text)
                            for identifier in indexed_window_ids)
    require(feasibility.get("indexed_groups") == indexed
            and feasibility.get("structurally_unsupported_groups") == unsupported
            and feasibility.get("minimum_distinct_indexed_excerpts") == len(indexed_window_ids)
            and feasibility.get("full_original_window_source_chars") == full_source_chars
            and feasibility.get("full_original_windows_exceed_source_budget") is True
            and feasibility.get("token_lower_bound")
                == "not_required_for_the_result_cap_proof"
            and feasibility.get("observed_before_selector_implementation") is True
            and type(feasibility.get("basis")) is str and feasibility["basis"]
            and type(feasibility.get("disposition")) is str and feasibility["disposition"]
            and len(indexed_window_ids) > limits["max_results"]
            and full_source_chars > limits["source_budget"],
            "Gold checklist feasibility accounting changed")
    packet_gate = exact_keys(gold.get("packet_gate"),
                             {"minimum_complementary_groups",
                              "require_newly_covered_source_file",
                              "eligible_group_ids"}, "Gold packet gate")
    eligible = packet_gate["eligible_group_ids"]
    require(packet_gate["minimum_complementary_groups"] == 1
            and packet_gate["require_newly_covered_source_file"] is True
            and type(eligible) is list and eligible and len(set(eligible)) == len(eligible),
            "Complementary packet gate changed")
    for identifier in eligible:
        require(identifier in groups_by_id
                and groups_by_id[identifier]["need"] in ("implementation", "callers")
                and groups_by_id[identifier]["alternatives"][0]["indexability"] == "indexed",
                "Complementary eligibility must name indexed implementation/caller groups")
    return {"groups": len(groups), "targets": target_count, "indexed_targets": indexed,
            "unsupported_targets": unsupported,
            "minimum_distinct_indexed_excerpts": len(indexed_window_ids),
            "full_original_window_source_chars": full_source_chars}


def validate_planner_boundary(public_result, gold):
    payloads = {
        "legacy_control": public_result["control_request"],
        "task_evidence": public_result["treatment_request"],
    }
    forbidden_keys = {"task_id", "groups", "target_groups", "alternatives",
                      "evidence_lines", "evidence_sha256", "source_file_sha256",
                      "indexability", "packet_expectation", "expected_gap_reason",
                      "eligible_group_ids", "reference_patch", "checker_outcomes"}

    def keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from keys(child)

    sentinels = set()
    for group in gold["groups"]:
        sentinels.add(group["id"])
        for target in group["alternatives"]:
            sentinels.update((target["path"], target["evidence_sha256"],
                              target["source_file_sha256"]))
    for condition, payload in payloads.items():
        require(not forbidden_keys.intersection(keys(payload)),
                f"Evaluator-only key crossed the {condition} planner boundary")
        serialized = canonical(payload)
        require(all(value.encode() not in serialized for value in sentinels),
                f"Evaluator-only sentinel crossed the {condition} planner boundary")
    return {name: sha256(canonical(payload)) for name, payload in payloads.items()}


def _worktree_changed_paths(root):
    changed = subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain=v1", "-uall"],
        text=True,
    ).splitlines()
    changed_paths = set()
    for row in changed:
        require(len(row) >= 4 and " -> " not in row[3:],
                "Freeze worktree cannot contain renames")
        changed_paths.add(row[3:])
    return changed_paths


def _commit_path_union(root, start_revision, end_revision):
    """Return every path touched by a commit in start..end, including add/delete pairs."""
    commits = git(root, "rev-list", "--reverse",
                  f"{start_revision}..{end_revision}").splitlines()
    changed_paths = set()
    for commit in commits:
        parents = git(root, "rev-list", "--parents", "-n", "1", commit).split()
        require(len(parents) >= 2,
                "Every post-base commit must have a parent")
        raw = subprocess.check_output(
            ["git", "-C", str(root), "diff", "--no-renames", "--name-only", "-z",
             parents[1], commit]
        ).decode("utf-8", "surrogateescape")
        changed_paths.update(path for path in raw.split("\0") if path)
    return changed_paths


def _commit_blob(root, commit, path):
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"{commit}:{path}"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return result.stdout if result.returncode == 0 else None


def validate_project_state(root, base_revision, allowed_paths):
    """Validate an unanchored freeze candidate before selector work begins."""
    root = Path(root)
    require((root / ".git").exists(),
            "Phase 0 chronology validation requires a Git worktree")
    head = git(root, "rev-parse", "HEAD")
    ancestor = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", base_revision, head],
        check=False,
    )
    require(ancestor.returncode == 0,
            "Project HEAD does not descend from the Phase 0 base revision")
    if head != base_revision:
        committed = _commit_path_union(root, base_revision, head)
        require(committed <= allowed_paths,
                "A committed path after the Phase 0 base falls outside the artifact closure")
    changed_paths = _worktree_changed_paths(root)
    require(changed_paths <= allowed_paths,
            "A changed path falls outside the Phase 0 artifact closure")
    return {"head": head, "committed": head != base_revision,
            "lifecycle": "freeze_candidate", "freeze_commit": None,
            "changed_paths": sorted(changed_paths)}


def validate_bound_project_state(root, base_revision, artifacts, binding_path,
                                 binding_bytes):
    """Anchor the committed freeze, then allow later work outside protected artifacts."""
    root = Path(root)
    binding_path = str(relative_path(binding_path))
    protected_paths = set(artifacts) | {binding_path}
    require((root / ".git").exists(),
            "Phase 0 chronology validation requires a Git worktree")
    head = git(root, "rev-parse", "HEAD")
    ancestor = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", base_revision, head],
        check=False,
    )
    require(ancestor.returncode == 0,
            "Project HEAD does not descend from the Phase 0 base revision")
    commits = git(root, "rev-list", "--reverse", f"{base_revision}..{head}").splitlines()
    for commit in commits:
        if _commit_blob(root, commit, binding_path) != binding_bytes:
            continue
        tree_matches = True
        for path, expected in artifacts.items():
            blob = _commit_blob(root, commit, path)
            if blob is None or sha256(blob) != expected:
                tree_matches = False
                break
        if not tree_matches:
            continue
        before = _commit_path_union(root, base_revision, commit)
        if not before <= protected_paths:
            continue
        after = _commit_path_union(root, commit, head)
        if after.intersection(protected_paths):
            continue
        changed_paths = _worktree_changed_paths(root)
        require(not changed_paths.intersection(protected_paths),
                "A bound freeze artifact changed after the freeze commit")
        return {"head": head, "committed": True,
                "lifecycle": "committed_freeze_ancestor",
                "freeze_commit": commit,
                "changed_paths": sorted(changed_paths)}
    return validate_project_state(root, base_revision, protected_paths)


def validate_binding(binding_path=DEFAULT_BINDING, *, root=ROOT):
    binding = read_json(binding_path)
    exact_keys(binding, {"version", "classification", "status", "project_base_revision",
                         "worktree_policy", "corpus_revision", "source_inventory_sha256",
                         "calls", "artifacts"}, "Phase 0 binding")
    require(binding.get("version") == 1
            and binding.get("classification") == "task_evidence_phase_0_freeze"
            and binding.get("status") == "freeze_candidate_prepared_before_selector_implementation",
            "Unsupported Phase 0 binding")
    expected_calls = {"model": 0, "embedding": 0, "index": 0,
                      "retrieval": 0, "planner": 0, "network": 0}
    require(binding.get("calls") == expected_calls,
            "Phase 0 binding must precede every runtime outcome")
    required = {
        ".github/workflows/task-evidence-freeze.yml",
        "benchmarks/coding_workflows_multifile.json",
        "benchmarks/strict_multifile_checks/check_requests_response_limit.py",
        "benchmarks/strict_multifile_checks/manifest.json",
        "benchmarks/strict_multifile_checks/strict_common.py",
        "benchmarks/task_evidence_gold/requests_response_limit.json",
        "benchmarks/task_evidence_requests.json",
        "docs/project-plan.md",
        "docs/task-evidence-development-protocol.md",
        "scripts/probe_response_limit_edges.py",
        "scripts/validate_task_evidence_freeze.py",
        "src/semantic_search/corpus.py",
        "src/semantic_search/languages.py",
        "src/semantic_search/source.py",
        "tests/test_task_evidence_freeze.py",
    }
    artifacts = binding.get("artifacts")
    require(type(artifacts) is dict and set(artifacts) == required,
            "Phase 0 bound artifact closure changed")
    for name, expected in artifacts.items():
        path = root / relative_path(name)
        require(path.is_file() and type(expected) is str and len(expected) == 64
                and sha256(path.read_bytes()) == expected,
                f"Phase 0 bound artifact changed: {name}")
    require(binding.get("corpus_revision")
            == "611c6162cbc4ac2020a2f91c7cfa4f3abf9bbb60"
            and binding.get("source_inventory_sha256")
            == "e014860ebe8847add3eaccff04c19f1620c8a2aa244398b2eef08c4c84ba40e0",
            "Phase 0 corpus binding changed")
    require(binding.get("project_base_revision")
            == "9da5ecbf7b547959f9e05911fbb51edd22f92386"
            and binding.get("worktree_policy")
            == "Before a matching freeze commit is anchored, every touched path after project_base_revision must belong to the Phase 0 artifact closure; afterward, implementation paths may change but bound artifacts must remain untouched",
            "Phase 0 project chronology binding changed")
    project_state = validate_bound_project_state(
        root, binding["project_base_revision"], artifacts,
        "benchmarks/task_evidence_gold/phase0_freeze.json",
        Path(binding_path).read_bytes())
    return {"artifact_count": len(artifacts), "artifacts": artifacts,
            "calls": expected_calls, "status": binding["status"],
            "project_state": project_state}


def validate(public_path=DEFAULT_PUBLIC, gold_path=DEFAULT_GOLD,
             corpus=DEFAULT_CORPUS, *, binding_path=DEFAULT_BINDING, root=ROOT):
    binding_result = validate_binding(binding_path, root=root)
    require(sha256(Path(public_path).read_bytes())
            == binding_result["artifacts"]["benchmarks/task_evidence_requests.json"],
            "Supplied public manifest is not the bound public manifest")
    require(sha256(Path(gold_path).read_bytes())
            == binding_result["artifacts"]["benchmarks/task_evidence_gold/requests_response_limit.json"],
            "Supplied gold manifest is not the bound evaluator gold")
    public = read_json(public_path)
    public_result = validate_public(public, root=root)
    scope = reconstruct_scope(public, Path(corpus).resolve())
    gold = read_json(gold_path)
    gold_result = validate_gold(gold, public, Path(corpus).resolve(), scope)
    boundary_result = validate_planner_boundary(public_result, gold)
    return {"status": "passed", "calls": {"model": 0, "embedding": 0,
            "index": 0, "retrieval": 0, "planner": 0, "network": 0},
            "scope": {**scope["counts"], **scope["hashes"]}, "gold": gold_result,
            "binding": {key: value for key, value in binding_result.items()
                        if key != "artifacts"},
            "planner_boundary": boundary_result,
            "requests_differ_only_by_needed_evidence": (
                "needed_evidence" not in public_result["control_request"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", type=Path, default=DEFAULT_PUBLIC)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--binding", type=Path, default=DEFAULT_BINDING)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    args = parser.parse_args()
    print(json.dumps(validate(args.public, args.gold, args.corpus,
                              binding_path=args.binding),
                     sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
