"""CPU-only contracts for the prospective Click child source freeze."""
from copy import deepcopy
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "validate_task_evidence_untouched_freeze",
    ROOT / "scripts/validate_task_evidence_untouched_freeze.py",
)
freeze = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(freeze)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def git(repo, *args, env=None):
    return subprocess.check_output(
        ["git", "-C", str(repo), "-c", "user.name=Fixture",
         "-c", "user.email=fixture@example.invalid", *args],
        text=True, stderr=subprocess.DEVNULL, env=env,
    ).strip()


def commit_at(repo, message, timestamp):
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = timestamp
    env["GIT_COMMITTER_DATE"] = timestamp
    git(repo, "commit", "-qm", message, env=env)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def real_scope():
    if not freeze.DEFAULT_CORPUS.is_dir():
        pytest.skip("Pinned Click corpus is not present")
    public = freeze.read_json(freeze.DEFAULT_PUBLIC)
    return public, freeze.reconstruct_scope(public, freeze.DEFAULT_CORPUS)


def test_duplicate_json_keys_are_rejected(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"version":1,"version":2}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate JSON key: version"):
        freeze.read_json(path)


def test_validator_imports_only_source_extraction_modules():
    tree = ast.parse(
        (ROOT / "scripts/validate_task_evidence_untouched_freeze.py").read_text(
            encoding="utf-8"
        )
    )
    semantic_imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("semantic_search"):
                semantic_imports.add(node.module)
        elif isinstance(node, ast.Import):
            semantic_imports.update(
                alias.name for alias in node.names
                if alias.name.startswith("semantic_search")
            )
    assert semantic_imports == {
        "semantic_search.corpus",
        "semantic_search.languages",
        "semantic_search.source",
    }


def test_real_public_scope_and_gold_validate(real_scope):
    public, scope = real_scope
    public_result = freeze.validate_public(public)
    gold = freeze.read_json(freeze.DEFAULT_GOLD)
    result = freeze.validate_gold(gold, public, freeze.DEFAULT_CORPUS, scope)
    assert public_result["request_hashes"] == {
        "legacy_control": "2f21bcfd7ba0cc35d413d4e3ee7a2b5a345b34b78c28f831970f4cf8e38478cb",
        "task_evidence": "c63735e89521f2de9c95e45cf36a45a591db8bdfcad203de51ed97625c49e42f",
    }
    assert scope["counts"] == {
        "eligible_supported_files": 70,
        "excluded_supported_files": 25,
        "included_supported_files": 45,
        "parsed_files": 45,
        "parsed_files_without_windows": 2,
        "implementation_windows": 474,
        "test_windows": 597,
        "total_windows": 1071,
    }
    assert result == {
        "groups": 25,
        "targets": 25,
        "indexed_targets": 25,
        "unsupported_targets": 0,
        "minimum_distinct_indexed_excerpts": 25,
        "full_original_window_source_chars": 20914,
    }


@pytest.mark.parametrize("mutation", [
    "version_bool", "candidate_order", "candidate_hash", "candidate_position_bool",
    "selected", "observation_bool", "extra_key",
])
def test_selection_lock_is_reconstructed_and_fails_closed(mutation):
    selection = freeze.read_json(ROOT / freeze.SELECTION_PATH)
    changed = deepcopy(selection)
    if mutation == "version_bool":
        changed["version"] = True
    elif mutation == "candidate_order":
        changed["eligible_manifest_candidates"].reverse()
    elif mutation == "candidate_hash":
        changed["eligible_manifest_candidates"][0]["strict_checker_sha256"] = "0" * 64
    elif mutation == "candidate_position_bool":
        changed["eligible_manifest_candidates"][0]["position_after_anchor"] = True
    elif mutation == "selected":
        changed["selected"]["task_id"] = "starlette_body_limits"
    elif mutation == "observation_bool":
        changed["observation_state_at_lock"]["candidate_retrieval_calls"] = False
    else:
        changed["selected"]["private_outcome"] = True
    with pytest.raises(ValueError):
        freeze.validate_selection_lock(changed)


def test_selection_lock_reconstructs_click_then_starlette_in_manifest_order():
    result = freeze.validate_selection_lock(
        freeze.read_json(ROOT / freeze.SELECTION_PATH)
    )
    assert [item["task_id"] for item in result["candidates"]] == [
        "click_atomic_transactions", "starlette_body_limits",
    ]
    assert result["selected"]["task_id"] == "click_atomic_transactions"


@pytest.mark.parametrize("leak", ["group_id", "path", "hash", "key", "symbol"])
def test_planner_boundary_rejects_evaluator_only_leaks(real_scope, leak):
    public, _ = real_scope
    public_result = freeze.validate_public(public)
    gold = freeze.read_json(freeze.DEFAULT_GOLD)
    changed = deepcopy(public_result)
    target = gold["groups"][0]["alternatives"][0]
    values = {
        "group_id": gold["groups"][0]["id"],
        "path": target["path"],
        "hash": target["evidence_sha256"],
        "symbol": target["symbol"],
    }
    if leak == "key":
        changed["control_request"]["evidence_lines"] = [1, 2]
    else:
        changed["control_request"]["query"] += " " + values[leak]
    with pytest.raises(ValueError):
        freeze.validate_planner_boundary(changed, gold)


def test_prompt_literal_target_symbol_is_not_misclassified_as_a_gold_leak(real_scope):
    public, _ = real_scope
    public_result = freeze.validate_public(public)
    gold = freeze.read_json(freeze.DEFAULT_GOLD)
    assert "open_file" in public["task"]["prompt"]
    assert any(target["symbol"] == "open_file" for group in gold["groups"]
               for target in group["alternatives"])
    freeze.validate_planner_boundary(public_result, gold)


@pytest.mark.parametrize("mutation", [
    "claim", "calls", "result_path", "retry", "gain", "inventory",
    "gap_reason", "condition", "extra_key", "version_bool", "classification",
    "scope_override", "gap_rule", "gap_boundary", "normalization", "receipt",
    "requests_gates", "handoff_contract", "gate_requirement", "planner_input",
    "claim_policy", "calls_bool",
])
def test_public_contract_fails_closed(real_scope, mutation):
    public, _ = real_scope
    changed = deepcopy(public)
    if mutation == "claim":
        changed["claim_boundary"]["supported_claim_if_passed"] += " changed"
    elif mutation == "calls":
        changed["calls_at_scope_freeze"]["retrieval"] = 1
    elif mutation == "result_path":
        changed["schedule"]["untouched_validation_prerequisite"]["result_path"] += "-2"
    elif mutation == "retry":
        changed["schedule"]["packet_gate"]["retry_policy"] = "retry_once"
    elif mutation == "gain":
        changed["schedule"]["packet_gate"]["complementary_gain"] = False
    elif mutation == "inventory":
        changed["index_scope"]["expected_inventory"]["parsed_files_without_windows"] = 1
    elif mutation == "gap_reason":
        changed["gap_contract"]["reason_vocabulary"][0] = "missing"
    elif mutation == "condition":
        changed["conditions"][1]["needed_evidence"] = ["implementation"]
    elif mutation == "extra_key":
        changed["task"]["private_gold"] = True
    elif mutation == "version_bool":
        changed["version"] = True
    elif mutation == "classification":
        changed["classification"] += " changed"
    elif mutation == "scope_override":
        changed["index_scope"]["scope_override"] += " changed"
    elif mutation == "gap_rule":
        changed["gap_contract"]["rules"][0] += " changed"
    elif mutation == "gap_boundary":
        changed["gap_contract"]["evaluator_boundary"] += " changed"
    elif mutation == "normalization":
        changed["schedule"]["packet_gate"]["normalization_contract"] += " changed"
    elif mutation == "receipt":
        changed["schedule"]["untouched_validation_prerequisite"][
            "one_shot_receipt_contract"
        ] += " changed"
    elif mutation == "requests_gates":
        changed["schedule"]["untouched_validation_prerequisite"][
            "execution_freeze_required_after"
        ].reverse()
    elif mutation == "handoff_contract":
        changed["schedule"]["untouched_validation_prerequisite"][
            "execution_handoff_contract"
        ]["ordering"] += " changed"
    elif mutation == "gate_requirement":
        changed["gates"][0]["requirement"] += " changed"
    elif mutation == "planner_input":
        changed["planner_boundary"]["input"] += " changed"
    elif mutation == "claim_policy":
        changed["schedule"]["untouched_validation_prerequisite"][
            "claim_policy"
        ] += " changed"
    else:
        changed["calls_at_scope_freeze"]["retrieval"] = False
    with pytest.raises(ValueError):
        freeze.validate_public(changed)


@pytest.mark.parametrize("mutation", [
    "hash", "boolean_line", "feasibility", "source_chars", "no_loss",
    "eligible_gap", "extra_key", "version_bool",
])
def test_gold_hash_feasibility_and_admission_fail_closed(real_scope, mutation):
    public, scope = real_scope
    gold = freeze.read_json(freeze.DEFAULT_GOLD)
    changed = deepcopy(gold)
    if mutation == "hash":
        changed["groups"][0]["alternatives"][0]["evidence_sha256"] = "0" * 64
    elif mutation == "boolean_line":
        changed["groups"][0]["alternatives"][0]["evidence_lines"][0] = True
    elif mutation == "feasibility":
        changed["feasibility"]["observed_before_selector_implementation"] = False
    elif mutation == "source_chars":
        changed["feasibility"]["full_original_window_source_chars"] += 1
    elif mutation == "no_loss":
        changed["packet_gate"]["no_individual_loss"] = False
    elif mutation == "eligible_gap":
        changed["packet_gate"]["eligible_group_ids"].append("missing")
    elif mutation == "extra_key":
        changed["groups"][0]["alternatives"][0]["rank"] = 1
    else:
        changed["version"] = True
    with pytest.raises(ValueError):
        freeze.validate_gold(
            changed, public, freeze.DEFAULT_CORPUS, scope
        )


def test_unsupported_module_import_is_source_derived_and_tamper_evident(real_scope):
    _, scope = real_scope
    path = "src/click/__init__.py"
    source = (freeze.DEFAULT_CORPUS / path).read_text(encoding="utf-8")
    rows = freeze.physical_lines(source)
    target = {
        "path": path,
        "symbol": "open_file",
        "evidence_lines": [73, 73],
        "source_file_sha256": scope["included"][path],
        "evidence_sha256": digest(rows[72].encode()),
        "indexability": "unsupported_artifact_kind",
        "structural_kind": "module_import",
        "structural_facts": {
            "module": "utils", "level": 1, "name": "open_file",
            "asname": "open_file",
        },
    }
    freeze._target_facts(
        target, freeze.DEFAULT_CORPUS, scope,
        {item.id: item for item in scope["windows"]},
    )
    changed = deepcopy(target)
    changed["structural_facts"]["name"] = "missing"
    with pytest.raises(ValueError, match="module import"):
        freeze._target_facts(
            changed, freeze.DEFAULT_CORPUS, scope,
            {item.id: item for item in scope["windows"]},
        )


def _chronology_fixture(tmp_path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / "base.txt").write_text("phase 0\n", encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "phase 0 anchor")
    parent = git(repo, "rev-parse", "HEAD")
    selection_name = "selection.json"
    selection_bytes = b'{"selected":"click"}\n'
    (repo / selection_name).write_bytes(selection_bytes)
    git(repo, "add", selection_name)
    git(repo, "commit", "-qm", "selection")
    base = git(repo, "rev-parse", "HEAD")
    return repo, parent, base, selection_name, selection_bytes


def _state(repo, parent, base, selection_name, selection_bytes,
           artifacts, binding_name, binding_bytes, child_paths):
    return freeze.validate_project_state(
        repo, artifacts, binding_name, binding_bytes,
        base_revision=base, parent_anchor=parent, selection_revision=base,
        selection_path=selection_name, selection_sha256=digest(selection_bytes),
        child_paths=child_paths, output_path="results/click",
    )


def test_candidate_chronology_allows_only_declared_child_paths(tmp_path):
    repo, parent, base, selection_name, selection_bytes = _chronology_fixture(tmp_path)
    artifact_name, binding_name = "artifact.json", "binding.json"
    artifact_bytes, binding_bytes = b'{}\n', b'{"status":"candidate"}\n'
    (repo / artifact_name).write_bytes(artifact_bytes)
    (repo / binding_name).write_bytes(binding_bytes)
    artifacts = {artifact_name: digest(artifact_bytes)}
    state = _state(
        repo, parent, base, selection_name, selection_bytes, artifacts,
        binding_name, binding_bytes, {artifact_name, binding_name},
    )
    assert state["lifecycle"] == "freeze_candidate"
    (repo / "outside.py").write_text("outcome = True\n", encoding="utf-8")
    with pytest.raises(ValueError, match="seven child artifacts"):
        _state(repo, parent, base, selection_name, selection_bytes, artifacts,
               binding_name, binding_bytes, {artifact_name, binding_name})


def test_candidate_rejects_preexisting_result_path(tmp_path):
    repo, parent, base, selection_name, selection_bytes = _chronology_fixture(tmp_path)
    artifact_name, binding_name = "artifact.json", "binding.json"
    artifact_bytes, binding_bytes = b'{}\n', b'{"status":"candidate"}\n'
    (repo / artifact_name).write_bytes(artifact_bytes)
    (repo / binding_name).write_bytes(binding_bytes)
    output = repo / "results/click"
    output.mkdir(parents=True)
    (output / "receipt.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="output must be absent"):
        _state(
            repo, parent, base, selection_name, selection_bytes,
            {artifact_name: digest(artifact_bytes)}, binding_name, binding_bytes,
            {artifact_name, binding_name},
        )


def test_exact_anchor_allows_outside_work_but_rejects_protected_touch_restore(
    tmp_path,
):
    repo, parent, base, selection_name, selection_bytes = _chronology_fixture(tmp_path)
    artifact_name, binding_name = "artifact.json", "binding.json"
    artifact_bytes, binding_bytes = b'{}\n', b'{"status":"candidate"}\n'
    (repo / artifact_name).write_bytes(artifact_bytes)
    (repo / binding_name).write_bytes(binding_bytes)
    git(repo, "add", artifact_name, binding_name)
    git(repo, "commit", "-qm", "child freeze")
    anchor = git(repo, "rev-parse", "HEAD")
    artifacts = {artifact_name: digest(artifact_bytes)}
    (repo / "selector.py").write_text("outcome = True\n", encoding="utf-8")
    git(repo, "add", "selector.py")
    git(repo, "commit", "-qm", "selector outside closure")
    state = _state(
        repo, parent, base, selection_name, selection_bytes, artifacts,
        binding_name, binding_bytes, {artifact_name, binding_name},
    )
    assert state["lifecycle"] == "committed_freeze_ancestor"
    assert state["freeze_commit"] == anchor

    (repo / artifact_name).write_text('{"changed":true}\n', encoding="utf-8")
    git(repo, "add", artifact_name)
    git(repo, "commit", "-qm", "touch protected")
    (repo / artifact_name).write_bytes(artifact_bytes)
    git(repo, "add", artifact_name)
    git(repo, "commit", "-qm", "restore protected")
    with pytest.raises(ValueError):
        _state(repo, parent, base, selection_name, selection_bytes, artifacts,
               binding_name, binding_bytes, {artifact_name, binding_name})


def test_parallel_selector_history_does_not_count_as_post_freeze(tmp_path):
    repo, parent, base, selection_name, selection_bytes = _chronology_fixture(tmp_path)
    artifact_name, binding_name = "artifact.json", "binding.json"
    artifact_bytes, binding_bytes = b'{}\n', b'{"status":"candidate"}\n'
    (repo / artifact_name).write_bytes(artifact_bytes)
    (repo / binding_name).write_bytes(binding_bytes)
    git(repo, "add", artifact_name, binding_name)
    git(repo, "commit", "-qm", "child freeze")
    git(repo, "branch", "child-anchor")

    git(repo, "checkout", "-qb", "parallel-selector", base)
    (repo / "selector.py").write_text("outcome = True\n", encoding="utf-8")
    git(repo, "add", "selector.py")
    git(repo, "commit", "-qm", "selector without child-freeze ancestor")
    git(repo, "checkout", "-q", "child-anchor")
    git(repo, "merge", "--no-ff", "-qm", "merge parallel selector",
        "parallel-selector")

    with pytest.raises(ValueError):
        _state(
            repo, parent, base, selection_name, selection_bytes,
            {artifact_name: digest(artifact_bytes)}, binding_name, binding_bytes,
            {artifact_name, binding_name},
        )


@pytest.mark.parametrize("result_state", ["worktree", "committed", "add_delete"])
def test_exact_anchor_rejects_later_result_without_handoff(tmp_path, result_state):
    repo, parent, base, selection_name, selection_bytes = _chronology_fixture(tmp_path)
    artifact_name, binding_name = "artifact.json", "binding.json"
    artifact_bytes, binding_bytes = b'{}\n', b'{"status":"candidate"}\n'
    (repo / artifact_name).write_bytes(artifact_bytes)
    (repo / binding_name).write_bytes(binding_bytes)
    git(repo, "add", artifact_name, binding_name)
    git(repo, "commit", "-qm", "child freeze")
    output = repo / "results/click"
    output.mkdir(parents=True)
    receipt = output / "receipt.json"
    receipt.write_text("{}\n", encoding="utf-8")
    if result_state != "worktree":
        git(repo, "add", "results/click/receipt.json")
        git(repo, "commit", "-qm", "premature Click result")
    if result_state == "add_delete":
        receipt.unlink()
        git(repo, "add", "-u", "results/click/receipt.json")
        git(repo, "commit", "-qm", "hide premature Click result")
    with pytest.raises(ValueError):
        _state(
            repo, parent, base, selection_name, selection_bytes,
            {artifact_name: digest(artifact_bytes)}, binding_name, binding_bytes,
            {artifact_name, binding_name},
        )


def _execution_binding(anchor, binding_bytes, **changes):
    handoff = {
        "child_freeze_revision": anchor,
        "child_binding_sha256": digest(binding_bytes),
        "requests_packet_gate_receipt_sha256": "1" * 64,
        "requests_native_latency_gate_receipt_sha256": "2" * 64,
        "attempt_id": "click-attempt-1",
        "result_path": "results/click",
        "result_path_state_at_execution_freeze": "absent",
    }
    handoff.update(changes)
    return {
        "classification": "task_evidence_untouched_click_execution_freeze",
        "child_handoff": handoff,
    }


def test_committed_execution_handoff_allows_later_result(tmp_path):
    repo, parent, base, selection_name, selection_bytes = _chronology_fixture(tmp_path)
    artifact_name, binding_name = "artifact.json", "binding.json"
    artifact_bytes, binding_bytes = b'{}\n', b'{"status":"candidate"}\n'
    (repo / artifact_name).write_bytes(artifact_bytes)
    (repo / binding_name).write_bytes(binding_bytes)
    git(repo, "add", artifact_name, binding_name)
    git(repo, "commit", "-qm", "child freeze")
    anchor = git(repo, "rev-parse", "HEAD")
    execution = repo / freeze.EXECUTION_BINDING_PATH
    write_json(execution, _execution_binding(anchor, binding_bytes))
    git(repo, "add", freeze.EXECUTION_BINDING_PATH)
    git(repo, "commit", "-qm", "execution handoff")
    handoff_commit = git(repo, "rev-parse", "HEAD")
    output = repo / "results/click/receipt.json"
    output.parent.mkdir(parents=True)
    output.write_text("{}\n", encoding="utf-8")
    git(repo, "add", "results/click/receipt.json")
    git(repo, "commit", "-qm", "Click result")
    state = _state(
        repo, parent, base, selection_name, selection_bytes,
        {artifact_name: digest(artifact_bytes)}, binding_name, binding_bytes,
        {artifact_name, binding_name},
    )
    assert state["lifecycle"] == "execution_handoff_ancestor"
    assert state["freeze_commit"] == anchor
    assert state["execution_handoff_commit"] == handoff_commit


def test_parallel_result_history_does_not_descend_from_handoff(tmp_path):
    repo, parent, base, selection_name, selection_bytes = _chronology_fixture(tmp_path)
    artifact_name, binding_name = "artifact.json", "binding.json"
    artifact_bytes, binding_bytes = b'{}\n', b'{"status":"candidate"}\n'
    (repo / artifact_name).write_bytes(artifact_bytes)
    (repo / binding_name).write_bytes(binding_bytes)
    git(repo, "add", artifact_name, binding_name)
    git(repo, "commit", "-qm", "child freeze")
    anchor = git(repo, "rev-parse", "HEAD")

    git(repo, "checkout", "-qb", "execution-handoff", anchor)
    execution = repo / freeze.EXECUTION_BINDING_PATH
    write_json(execution, _execution_binding(anchor, binding_bytes))
    git(repo, "add", freeze.EXECUTION_BINDING_PATH)
    commit_at(repo, "execution handoff", "2026-01-01T00:00:00Z")

    git(repo, "checkout", "-qb", "parallel-result", anchor)
    output = repo / "results/click/receipt.json"
    output.parent.mkdir(parents=True)
    output.write_text("{}\n", encoding="utf-8")
    git(repo, "add", "results/click/receipt.json")
    commit_at(repo, "parallel Click result", "2026-01-01T00:01:00Z")
    git(repo, "checkout", "-q", "execution-handoff")
    git(repo, "merge", "--no-ff", "-qm", "merge parallel result",
        "parallel-result")

    with pytest.raises(ValueError, match="must descend from"):
        _state(
            repo, parent, base, selection_name, selection_bytes,
            {artifact_name: digest(artifact_bytes)}, binding_name, binding_bytes,
            {artifact_name, binding_name},
        )


def test_uncommitted_execution_binding_does_not_count_as_a_handoff(tmp_path):
    repo, parent, base, selection_name, selection_bytes = _chronology_fixture(tmp_path)
    artifact_name, binding_name = "artifact.json", "binding.json"
    artifact_bytes, binding_bytes = b'{}\n', b'{"status":"candidate"}\n'
    (repo / artifact_name).write_bytes(artifact_bytes)
    (repo / binding_name).write_bytes(binding_bytes)
    git(repo, "add", artifact_name, binding_name)
    git(repo, "commit", "-qm", "child freeze")
    anchor = git(repo, "rev-parse", "HEAD")
    write_json(
        repo / freeze.EXECUTION_BINDING_PATH,
        _execution_binding(anchor, binding_bytes),
    )
    with pytest.raises(ValueError, match="committed execution handoff"):
        _state(
            repo, parent, base, selection_name, selection_bytes,
            {artifact_name: digest(artifact_bytes)}, binding_name, binding_bytes,
            {artifact_name, binding_name},
        )


@pytest.mark.parametrize("case", ["late", "bad", "add_delete_before"])
def test_invalid_or_late_execution_handoff_is_rejected(tmp_path, case):
    repo, parent, base, selection_name, selection_bytes = _chronology_fixture(tmp_path)
    artifact_name, binding_name = "artifact.json", "binding.json"
    artifact_bytes, binding_bytes = b'{}\n', b'{"status":"candidate"}\n'
    (repo / artifact_name).write_bytes(artifact_bytes)
    (repo / binding_name).write_bytes(binding_bytes)
    git(repo, "add", artifact_name, binding_name)
    git(repo, "commit", "-qm", "child freeze")
    anchor = git(repo, "rev-parse", "HEAD")
    output = repo / "results/click/receipt.json"
    if case in {"late", "add_delete_before"}:
        output.parent.mkdir(parents=True)
        output.write_text("{}\n", encoding="utf-8")
        git(repo, "add", "results/click/receipt.json")
        git(repo, "commit", "-qm", "premature Click result")
    if case == "add_delete_before":
        output.unlink()
        git(repo, "add", "-u", "results/click/receipt.json")
        git(repo, "commit", "-qm", "hide premature Click result")
    execution = repo / freeze.EXECUTION_BINDING_PATH
    changes = {"child_freeze_revision": "0" * 40} if case == "bad" else {}
    write_json(execution, _execution_binding(anchor, binding_bytes, **changes))
    git(repo, "add", freeze.EXECUTION_BINDING_PATH)
    git(repo, "commit", "-qm", "execution handoff")
    with pytest.raises(ValueError):
        _state(
            repo, parent, base, selection_name, selection_bytes,
            {artifact_name: digest(artifact_bytes)}, binding_name, binding_bytes,
            {artifact_name, binding_name},
        )


def test_pre_anchor_add_delete_outside_path_is_rejected(tmp_path):
    repo, parent, base, selection_name, selection_bytes = _chronology_fixture(tmp_path)
    artifact_name, binding_name = "artifact.json", "binding.json"
    artifact_bytes, binding_bytes = b'{}\n', b'{"status":"candidate"}\n'
    outside = repo / "outside.txt"
    outside.write_text("temporary\n", encoding="utf-8")
    git(repo, "add", "outside.txt")
    git(repo, "commit", "-qm", "add outside")
    outside.unlink()
    git(repo, "add", "-u", "outside.txt")
    git(repo, "commit", "-qm", "delete outside")
    (repo / artifact_name).write_bytes(artifact_bytes)
    (repo / binding_name).write_bytes(binding_bytes)
    with pytest.raises(ValueError, match="pre-anchor committed path"):
        _state(
            repo, parent, base, selection_name, selection_bytes,
            {artifact_name: digest(artifact_bytes)}, binding_name, binding_bytes,
            {artifact_name, binding_name},
        )


def test_binding_contract_closes_authorities_and_zero_call_surfaces():
    assert freeze.CHILD_PATHS == {
        ".github/workflows/task-evidence-untouched-freeze.yml",
        "benchmarks/task_evidence_gold/click_atomic_transactions.json",
        "benchmarks/task_evidence_gold/untouched_click_freeze.json",
        "benchmarks/task_evidence_untouched_click.json",
        "docs/task-evidence-untouched-click-protocol.md",
        "scripts/validate_task_evidence_untouched_freeze.py",
        "tests/test_task_evidence_untouched_freeze.py",
    }
    expected_authority_closure = {
        ".github/workflows/task-evidence-untouched-freeze.yml",
        "benchmarks/coding_workflows_multifile.json",
        "benchmarks/coding_workflows_strict.json",
        "benchmarks/multifile_checks/check_click_atomic_transactions.py",
        "benchmarks/multifile_checks/common.py",
        "benchmarks/strict_multifile_checks/check_click_atomic_transactions.py",
        "benchmarks/strict_multifile_checks/manifest.json",
        "benchmarks/strict_multifile_checks/strict_common.py",
        "benchmarks/task_evidence_gold/phase0_freeze.json",
        "benchmarks/task_evidence_gold/click_atomic_transactions.json",
        "benchmarks/task_evidence_untouched_click.json",
        "benchmarks/task_evidence_untouched_selection.json",
        "docs/task-evidence-untouched-click-protocol.md",
        "scripts/probe_click_atomic_resource_edges.py",
        "scripts/validate_task_evidence_freeze.py",
        "scripts/validate_task_evidence_untouched_freeze.py",
        "src/semantic_search/corpus.py",
        "src/semantic_search/languages.py",
        "src/semantic_search/source.py",
        "tests/test_task_evidence_untouched_freeze.py",
    }
    assert len(expected_authority_closure) == 20
    assert freeze.BOUND_ARTIFACT_PATHS == expected_authority_closure
    assert freeze.EXPECTED_CALLS == {
        "model": 0, "embedding": 0, "index": 0, "retrieval": 0,
        "planner": 0, "selector": 0, "packing": 0, "network": 0,
    }


def test_binding_rejects_boolean_version_before_chronology(tmp_path):
    root = tmp_path / "project"
    path = root / "benchmarks/task_evidence_gold/untouched_click_freeze.json"
    binding = {
        "version": True,
        "classification": "task_evidence_untouched_click_freeze",
        "status": "freeze_candidate_prepared_before_selector_implementation",
        "parent_phase0": {}, "selection_lock": {},
        "project_base_revision": freeze.PROJECT_BASE_REVISION,
        "worktree_policy": freeze.WORKTREE_POLICY,
        "corpus_revision": freeze.CORPUS_REVISION,
        "source_inventory_sha256": freeze.EXPECTED_INVENTORY[
            "included_files_sha256"
        ],
        "calls": freeze.EXPECTED_CALLS,
        "artifacts": {},
    }
    write_json(path, binding)
    with pytest.raises(ValueError, match="strict integer 1"):
        freeze.validate_binding(path, root=root)


def test_bound_child_freeze_validates_when_binding_is_present():
    if not freeze.DEFAULT_BINDING.is_file() or not freeze.DEFAULT_CORPUS.is_dir():
        pytest.skip("Committed child binding or Click corpus is not present")
    result = freeze.validate()
    assert result["status"] == "passed"
    assert result["calls"] == freeze.EXPECTED_CALLS
    assert result["binding"]["project_state"]["lifecycle"] in {
        "freeze_candidate", "committed_freeze_ancestor", "execution_handoff_ancestor",
    }
