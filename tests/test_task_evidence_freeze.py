"""CPU-only contracts for the task-evidence Phase 0 freeze."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

from semantic_search.corpus import extract_snippets


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "validate_task_evidence_freeze",
    ROOT / "scripts/validate_task_evidence_freeze.py",
)
freeze = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(freeze)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def git(repo, *args):
    return subprocess.check_output(
        ["git", "-C", str(repo), "-c", "user.name=Fixture",
         "-c", "user.email=fixture@example.invalid", *args],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()


@pytest.fixture
def phase0_fixture(tmp_path):
    project, corpus = tmp_path / "project", tmp_path / "corpus"
    project.mkdir()
    corpus.mkdir()
    oversized_literal = "x0_" * 5000
    method_names = ["iter_content", *(f"helper_{number}" for number in range(1, 9))]
    model_rows = ["class Response:\n"]
    for number, name in enumerate(method_names):
        model_rows.append(f"    def {name}(self):\n")
        if number == 0:
            model_rows.append(f"        yield b'{oversized_literal}'\n")
        else:
            model_rows.append(f"        return {number}\n")
    files = {
        "src/requests/models.py": "".join(model_rows),
        "tests/test_models.py": (
            "def test_stream():\n"
            "    assert list(iter([b'x'])) == [b'x']\n"
        ),
        "docs/conf.py": "def docs_only():\n    return True\n",
        "setup.py": "def build_only():\n    return True\n",
    }
    for name, text in files.items():
        path = corpus / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git(corpus, "init", "-q")
    git(corpus, "add", ".")
    git(corpus, "commit", "-qm", "fixture")
    revision = git(corpus, "rev-parse", "HEAD")

    prompt = "Add a decoded response byte budget."
    workflow = {"tasks": [{"id": "requests_response_limit", "prompt": prompt,
        "revision": revision, "repo_url": "https://example.invalid/requests"}]}
    workflow_path = project / "benchmarks/coding_workflows_multifile.json"
    write_json(workflow_path, workflow)
    exclusions = ["docs/*.py", "setup.py"]
    included_paths = ["src/requests/models.py", "tests/test_models.py"]
    excluded_paths = ["docs/conf.py", "setup.py"]

    def inventory(paths):
        return {name: digest((corpus / name).read_bytes()) for name in paths}

    included = inventory(included_paths)
    implementation = inventory(included_paths[:1])
    tests = inventory(included_paths[1:])
    excluded = inventory(excluded_paths)
    windows = extract_snippets(corpus, ".", 160, 40, source_exclusions=exclusions)
    window_inventory = {row.id: row.content_hash for row in sorted(windows, key=lambda row: row.id)}
    public = deepcopy(freeze.read_json(freeze.DEFAULT_PUBLIC))
    public["task"] = {"id": "requests_response_limit", "prompt": prompt,
        "prompt_sha256": digest(prompt.encode()),
        "source_manifest": {"path": "benchmarks/coding_workflows_multifile.json",
                            "sha256": digest(workflow_path.read_bytes())}}
    public["corpus"] = {"id": "requests", "repo_url": "https://example.invalid/requests",
                        "revision": revision,
                        "membership": "saved implementations; Git-tracked files only in Git projects"}
    public["index_scope"]["required_prefixes"][0]["expected_files"] = 1
    public["index_scope"]["required_prefixes"][1]["expected_files"] = 1
    public["index_scope"]["expected_inventory"].update({
        "eligible_supported_files": 4, "excluded_supported_files": 2,
        "included_supported_files": 2, "parsed_files": 2,
        "implementation_windows": 9, "test_windows": 1,
        "total_windows": 10,
        "included_files_sha256": digest(freeze.canonical(included)),
        "implementation_files_sha256": digest(freeze.canonical(implementation)),
        "test_files_sha256": digest(freeze.canonical(tests)),
        "excluded_files_sha256": digest(freeze.canonical(excluded)),
        "windows_sha256": digest(freeze.canonical(window_inventory)),
    })
    public["planner_boundary"]["control_request_sha256"] = digest(
        freeze.canonical(freeze.planner_request(public, "legacy_control")))
    public["planner_boundary"]["task_evidence_request_sha256"] = digest(
        freeze.canonical(freeze.planner_request(public, "task_evidence")))
    source_hash = included["src/requests/models.py"]
    lines = files["src/requests/models.py"].splitlines(keepends=True)
    unsupported_hash = digest("".join(lines[0:1]).encode())
    indexed_groups = []
    indexed_windows = []
    for number, name in enumerate(method_names):
        body_line = 3 + number * 2
        symbol = f"Response.{name}"
        indexed_groups.append({
            "id": f"implementation_{number}", "need": "implementation",
            "importance": "required_for_change",
            "packet_expectation": "candidate_or_factual_gap",
            "why": "Synthetic implementation evidence.",
            "alternatives": [{"path": "src/requests/models.py",
                "symbol": symbol, "evidence_lines": [body_line, body_line],
                "source_file_sha256": source_hash,
                "evidence_sha256": digest(lines[body_line - 1].encode()),
                "indexability": "indexed"}],
        })
        indexed_windows.append(next(row for row in windows if row.symbol == symbol))
    full_source_chars = sum(len(row.text) for row in indexed_windows)
    gold = {
        "version": 1, "classification": "evaluator_only",
        "task_id": "requests_response_limit", "corpus_revision": revision,
        "source_inventory_sha256": digest(freeze.canonical(included)),
        "scoring": {"within_group": "or", "across_groups": "and",
                    "coverage": "One final delivered excerpt must contain the complete path, symbol, and physical evidence span",
                    "fragment_union_allowed": False,
                    "symbol_match_alone_is_evidence": False},
        "packet_limits": {"max_results": 8, "source_budget": 12000,
                          "token_budget": 4096, "encoding": "cl100k_base"},
        "feasibility": {"predeclared_indexable_key_evidence_fits": False,
                        "reason": "result_cap", "indexed_groups": 9,
                        "structurally_unsupported_groups": 1,
                        "minimum_distinct_indexed_excerpts": 9,
                        "full_original_window_source_chars": full_source_chars,
                        "full_original_windows_exceed_source_budget": True,
                        "token_lower_bound": "not_required_for_the_result_cap_proof",
                        "basis": "Synthetic singleton alternatives.",
                        "disposition": "Preserve gaps.",
                        "observed_before_selector_implementation": True},
        "packet_gate": {"minimum_complementary_groups": 1,
                        "require_newly_covered_source_file": True,
                        "eligible_group_ids": [group["id"] for group in indexed_groups]},
        "groups": [*indexed_groups,
            {"id": "class_contract", "need": "behavioral_constraints",
             "importance": "required_for_change",
             "packet_expectation": "factual_gap_required",
             "expected_gap_reason": "unsupported_artifact_kind",
             "why": "Synthetic class-only structure.",
             "alternatives": [{"path": "src/requests/models.py",
                 "symbol": "Response", "evidence_lines": [1, 1],
                 "source_file_sha256": source_hash,
                 "evidence_sha256": unsupported_hash,
                 "indexability": "unsupported_artifact_kind",
                 "structural_kind": "class_declaration",
                 "structural_facts": {"bases": [], "annotated_attributes": []}}]},
        ],
    }
    public_path = project / "benchmarks/task_evidence_requests.json"
    gold_path = project / "benchmarks/task_evidence_gold/requests_response_limit.json"
    write_json(public_path, public)
    write_json(gold_path, gold)
    return project, corpus, public, gold, public_path, gold_path


def validate_fixture(value):
    project, corpus, public, gold, _, _ = value
    public_result = freeze.validate_public(public, root=project)
    scope = freeze.reconstruct_scope(public, corpus)
    gold_result = freeze.validate_gold(gold, public, corpus, scope)
    return public_result, scope, gold_result


def test_valid_synthetic_freeze_separates_public_requests_and_gold(phase0_fixture):
    public_result, scope, gold = validate_fixture(phase0_fixture)
    assert "needed_evidence" not in public_result["control_request"]
    assert public_result["treatment_request"]["needed_evidence"] == list(freeze.NEEDS)
    assert set(public_result["treatment_request"]) - set(public_result["control_request"]) == {"needed_evidence"}
    assert scope["counts"]["total_windows"] == 10
    assert {key: gold[key] for key in ("groups", "targets", "indexed_targets",
                                       "unsupported_targets",
                                       "minimum_distinct_indexed_excerpts")} == {
        "groups": 10, "targets": 10, "indexed_targets": 9,
        "unsupported_targets": 1, "minimum_distinct_indexed_excerpts": 9}
    assert gold["full_original_window_source_chars"] > 12000


def test_planner_boundary_rejects_evaluator_sentinel(phase0_fixture):
    project, _, public, gold, _, _ = phase0_fixture
    public_result = freeze.validate_public(public, root=project)
    leaked = deepcopy(public_result)
    leaked["treatment_request"]["query"] += " " + gold["groups"][0]["id"]
    with pytest.raises(ValueError, match="sentinel"):
        freeze.validate_planner_boundary(leaked, gold)


@pytest.mark.parametrize("kind", ["module_exports", "class_declaration",
                                   "class_attribute", "class_family"])
def test_unsupported_structural_facts_are_derived_and_tamper_evident(kind):
    cases = {
        "module_exports": (
            "from .exceptions import RequestException\n__all__ = ('RequestException',)\n",
            {"symbol": "module.exports", "path": "module.py",
             "structural_kind": kind, "structural_facts": {
                 "imported_names": ["RequestException"],
                 "exported_names": ["RequestException"]}}, 1, 2, "imported_names"),
        "class_declaration": (
            "class RequestException(IOError):\n    response: object\n    request: object\n",
            {"symbol": "RequestException", "path": "exceptions.py",
             "structural_kind": kind, "structural_facts": {
                 "bases": ["IOError"], "annotated_attributes": ["response", "request"]}},
            1, 3, "bases"),
        "class_attribute": (
            "class Session:\n    __attrs__: list[str] = ['headers', 'cookies']\n",
            {"symbol": "Session.__attrs__", "path": "sessions.py",
             "structural_kind": kind,
             "structural_facts": {"list_members": ["headers", "cookies"]}},
            2, 2, "list_members"),
        "class_family": (
            "class Base(TypedDict, total=False):\n    first: int\n"
            "class Child(Base, total=False):\n    second: str\n",
            {"symbol": "Base", "path": "types.py", "structural_kind": kind,
             "structural_facts": {"classes": [
                 {"name": "Base", "bases": ["TypedDict"], "fields": ["first"],
                  "total": False},
                 {"name": "Child", "bases": ["Base"], "fields": ["second"],
                  "total": False}]}},
            1, 4, "classes"),
    }
    source, target, low, high, tamper_key = cases[kind]
    freeze._validate_structure(target, source, low, high)
    changed = deepcopy(target)
    if kind == "class_family":
        changed["structural_facts"][tamper_key][0]["fields"] = ["missing"]
    else:
        changed["structural_facts"][tamper_key] = ["missing"]
    with pytest.raises(ValueError):
        freeze._validate_structure(changed, source, low, high)


@pytest.mark.parametrize("mutation", ["prompt", "leaked_gold", "boolean_budget", "condition",
                                       "exclusions", "membership_glob", "extra_nested",
                                       "extractor", "schedule", "packet_replay",
                                       "prerequisite", "gate_object"])
def test_public_prompt_boundary_types_and_parity_fail_closed(phase0_fixture, mutation):
    project, _, public, _, _, _ = phase0_fixture
    changed = deepcopy(public)
    if mutation == "prompt":
        changed["task"]["prompt"] += " changed"
    elif mutation == "leaked_gold":
        changed["groups"] = []
    elif mutation == "boolean_budget":
        changed["common_request"]["token_budget"] = True
    elif mutation == "condition":
        changed["conditions"][0]["needed_evidence"] = []
    elif mutation == "exclusions":
        changed["index_scope"]["source_exclusions"] = ["docs/conf.py", "setup.py"]
    elif mutation == "membership_glob":
        changed["index_scope"]["required_prefixes"][1]["membership_globs"] = ["tests/**/*.py"]
    elif mutation == "extra_nested":
        changed["index_scope"]["required_prefixes"][0]["private_note"] = "gold"
    elif mutation == "extractor":
        changed["index_scope"]["extractor_fingerprint"]["python_ast"] = [3, 11]
    elif mutation == "schedule":
        changed["schedule"]["native_latency_gate"]["warmups"] = 5
    elif mutation == "packet_replay":
        changed["schedule"]["packet_gate"]["replays_per_condition"] = 2
    elif mutation == "prerequisite":
        changed["schedule"]["untouched_validation_prerequisite"][
            "selection_required_before"
        ] = "coding_agent_trials"
    else:
        changed["phase_0_gates"][0]["id"] = "changed_gate"
    with pytest.raises(ValueError):
        freeze.validate_public(changed, root=project)


@pytest.mark.parametrize("mutation", ["hash", "boolean_line", "false_indexed", "wrong_gap",
                                       "fake_symbol", "unsupported_as_candidate",
                                       "observed_after_selector", "empty_basis",
                                       "empty_disposition"])
def test_gold_hash_bounds_indexability_and_gap_reason_fail_closed(phase0_fixture, mutation):
    _, corpus, public, gold, _, _ = phase0_fixture
    scope = freeze.reconstruct_scope(public, corpus)
    changed = deepcopy(gold)
    if mutation == "hash":
        changed["groups"][0]["alternatives"][0]["evidence_sha256"] = "0" * 64
    elif mutation == "boolean_line":
        changed["groups"][0]["alternatives"][0]["evidence_lines"] = [True, 3]
    elif mutation == "false_indexed":
        changed["groups"][-1]["alternatives"][0]["indexability"] = "indexed"
        changed["groups"][-1]["alternatives"][0].pop("structural_kind")
        changed["groups"][-1]["alternatives"][0].pop("structural_facts")
        changed["groups"][-1]["packet_expectation"] = "candidate_or_factual_gap"
        changed["groups"][-1].pop("expected_gap_reason")
    elif mutation == "wrong_gap":
        changed["groups"][-1]["expected_gap_reason"] = "not_retrieved"
    elif mutation == "fake_symbol":
        changed["groups"][-1]["alternatives"][0]["symbol"] = "MissingClass"
    elif mutation == "unsupported_as_candidate":
        changed["groups"][-1]["packet_expectation"] = "candidate_or_factual_gap"
        changed["groups"][-1].pop("expected_gap_reason")
    elif mutation == "observed_after_selector":
        changed["feasibility"]["observed_before_selector_implementation"] = False
    elif mutation == "empty_basis":
        changed["feasibility"]["basis"] = ""
    else:
        changed["feasibility"]["disposition"] = ""
    with pytest.raises(ValueError):
        freeze.validate_gold(changed, public, corpus, scope)


def test_dirty_corpus_is_rejected_before_evidence_validation(phase0_fixture):
    _, corpus, public, _, _, _ = phase0_fixture
    (corpus / "tests/test_models.py").write_text("def changed():\n    return True\n")
    with pytest.raises(ValueError, match="clean"):
        freeze.reconstruct_scope(public, corpus)


def test_clean_wrong_revision_corpus_is_rejected_before_evidence_validation(
    phase0_fixture,
):
    _, corpus, public, _, _, _ = phase0_fixture
    (corpus / "revision-marker.txt").write_text("new revision\n", encoding="utf-8")
    git(corpus, "add", "revision-marker.txt")
    git(corpus, "commit", "-qm", "advance fixture revision")
    assert not git(corpus, "status", "--porcelain=v1")
    assert git(corpus, "rev-parse", "HEAD") != public["corpus"]["revision"]
    with pytest.raises(ValueError, match="revision"):
        freeze.reconstruct_scope(public, corpus)


def test_phase0_binding_rejects_any_changed_artifact(tmp_path):
    if not freeze.DEFAULT_BINDING.is_file():
        pytest.skip("Phase 0 binding has not been generated")
    binding = freeze.read_json(freeze.DEFAULT_BINDING)
    binding["artifacts"]["docs/project-plan.md"] = "0" * 64
    changed = tmp_path / "changed-binding.json"
    write_json(changed, binding)
    with pytest.raises(ValueError, match="docs/project-plan.md"):
        freeze.validate_binding(changed)


def test_phase0_binding_rejects_appended_outcome_field(tmp_path):
    binding = freeze.read_json(freeze.DEFAULT_BINDING)
    binding["observed_selector_outcome"] = {"coverage_gain": 999}
    changed = tmp_path / "outcome-binding.json"
    write_json(changed, binding)
    with pytest.raises(ValueError, match="fields changed"):
        freeze.validate_binding(changed)


def test_project_state_accepts_freeze_commit_and_rejects_later_scope_expansion(tmp_path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / "base.txt").write_text("base\n")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "freeze.txt").write_text("freeze\n")
    git(repo, "add", "freeze.txt")
    git(repo, "commit", "-qm", "freeze")
    state = freeze.validate_project_state(repo, base, {"freeze.txt"})
    assert state["committed"] is True
    (repo / "selector.py").write_text("outcome = True\n")
    git(repo, "add", "selector.py")
    git(repo, "commit", "-qm", "selector")
    with pytest.raises(ValueError, match="committed path"):
        freeze.validate_project_state(repo, base, {"freeze.txt"})


def test_project_state_rejects_outside_path_added_then_deleted_in_history(tmp_path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")

    outside = repo / "outside.txt"
    outside.write_text("temporary outcome\n", encoding="utf-8")
    git(repo, "add", "outside.txt")
    git(repo, "commit", "-qm", "add outside path")
    outside.unlink()
    git(repo, "add", "-u", "outside.txt")
    git(repo, "commit", "-qm", "delete outside path")

    assert not outside.exists()
    assert not git(repo, "status", "--porcelain=v1")
    with pytest.raises(ValueError, match="committed path"):
        freeze.validate_project_state(repo, base, {"freeze.txt"})


def test_bound_project_state_allows_selector_work_but_rejects_protected_rewrites(
    tmp_path,
):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")

    artifact_name = "freeze.txt"
    binding_name = "binding.json"
    artifact_bytes = b"frozen artifact\n"
    binding_bytes = b'{"status":"frozen"}\n'
    (repo / artifact_name).write_bytes(artifact_bytes)
    (repo / binding_name).write_bytes(binding_bytes)
    git(repo, "add", artifact_name, binding_name)
    git(repo, "commit", "-qm", "commit exact freeze")
    freeze_commit = git(repo, "rev-parse", "HEAD")
    artifacts = {artifact_name: digest(artifact_bytes)}

    (repo / "selector.py").write_text("outcome = True\n", encoding="utf-8")
    git(repo, "add", "selector.py")
    git(repo, "commit", "-qm", "implement selector")
    state = freeze.validate_bound_project_state(
        repo, base, artifacts, binding_name, binding_bytes
    )
    assert state["lifecycle"] == "committed_freeze_ancestor"
    assert state["freeze_commit"] == freeze_commit

    (repo / artifact_name).write_text("temporarily changed\n", encoding="utf-8")
    git(repo, "add", artifact_name)
    git(repo, "commit", "-qm", "touch protected artifact")
    (repo / artifact_name).write_bytes(artifact_bytes)
    git(repo, "add", artifact_name)
    git(repo, "commit", "-qm", "restore protected artifact")
    assert (repo / artifact_name).read_bytes() == artifact_bytes
    with pytest.raises(ValueError, match="committed path"):
        freeze.validate_bound_project_state(
            repo, base, artifacts, binding_name, binding_bytes
        )


def test_project_state_requires_git_metadata(tmp_path):
    with pytest.raises(ValueError, match="Git worktree"):
        freeze.validate_project_state(tmp_path, "0" * 40, set())


@pytest.mark.parametrize("kind", ["public", "gold"])
def test_frozen_validation_rejects_unbound_manifest_override(tmp_path, kind):
    if not freeze.DEFAULT_CORPUS.is_dir() or not freeze.DEFAULT_BINDING.is_file():
        pytest.skip("Pinned Phase 0 inputs are not present")
    source = freeze.DEFAULT_PUBLIC if kind == "public" else freeze.DEFAULT_GOLD
    changed = freeze.read_json(source)
    if kind == "public":
        changed["phase_0_gates"][0]["requirement"] += " changed"
    else:
        changed["feasibility"]["disposition"] += " changed"
    changed_path = tmp_path / source.name
    write_json(changed_path, changed)
    arguments = {"public_path": changed_path} if kind == "public" else {"gold_path": changed_path}
    with pytest.raises(ValueError, match=f"Supplied {kind}"):
        freeze.validate(**arguments)


def test_phase0_freeze_candidate_validates_when_pinned_corpus_is_available():
    if not freeze.DEFAULT_CORPUS.is_dir():
        pytest.skip("Pinned Requests corpus is not present in this checkout")
    result = freeze.validate()
    assert result["status"] == "passed"
    assert result["calls"] == {"model": 0, "embedding": 0, "index": 0,
                               "retrieval": 0, "planner": 0, "network": 0}
    assert result["gold"]["groups"] == 21
    assert result["scope"]["test_windows"] == 436
