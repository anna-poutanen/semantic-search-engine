"""Invented Python source fixtures only; reserved labels are never read here."""
import ast
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("source_only_complementary_preflight", ROOT / "scripts/audit_complementary_preflight.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def sha(raw):
    import hashlib
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def record(path):
    raw = path.read_bytes()
    return {"bytes": len(raw), "sha256": sha(raw)}


def target(path, symbol, node, definition_lines, evidence_lines, text, eligible):
    rows = text.splitlines(keepends=True)
    low, high = evidence_lines
    offsets = [0]
    for row in rows:
        offsets.append(offsets[-1] + len(row.encode()))
    start = offsets[node.lineno - 1] + node.col_offset
    end = offsets[node.end_lineno - 1] + node.end_col_offset
    raw = text.encode()
    first, last = definition_lines
    return {"path": path, "symbol": symbol, "evidence_lines": evidence_lines,
        "source_file_sha256": sha(raw), "evidence_sha256": sha("".join(rows[low - 1:high]).encode()),
        "evidence_byte_bounds": [offsets[low - 1], offsets[high]], "definition_lines": definition_lines,
        "canonical_definition_key": [path, symbol, first, last],
        "ast_definition_byte_bounds": [start, end], "ast_definition_sha256": sha(raw[start:end]),
        "definition_source_byte_bounds": [offsets[first - 1], offsets[last]],
        "definition_source_sha256": sha(raw[offsets[first - 1]:offsets[last]]),
        "executable_evidence_lines": list(range(low, high + 1)), "eligible_window_ids": eligible}


@pytest.fixture
def source_fixture(tmp_path, monkeypatch):
    project, repo = tmp_path / "project", tmp_path / "corpus"
    project.mkdir()
    repo.mkdir()
    monkeypatch.setattr(audit, "ROOT", project)
    texts = {"src/pkg/a.py": "def outer(value):\n    def inner(item):\n        return item + 1\n    return inner(value)\n",
        "src/pkg/b.py": "def helper(value):\n    return value * 2\n",
        "src/pkg/excluded.py": "def excluded():\n    return 99\n",
        "src/pkg/types.pyi": "def declaration() -> int: ...\n",
        "tests/test_api.py": "class TestAPI:\n    def test_source(self):\n        assert True\n",
        "LICENSE": "MIT invented fixture notice\n"}
    for path, text in texts.items():
        destination = repo / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(text.encode())
    (project / "benchmarks").mkdir()
    (project / "benchmarks/fixture-license.txt").write_bytes((repo / "LICENSE").read_bytes())
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c",
            "user.email=fixture@example.invalid", *args], text=True, stderr=subprocess.DEVNULL).strip()
    git("init", "-q")
    git("add", ".")
    git("commit", "-qm", "source fixture")
    git("tag", "-a", "v1", "-m", "source fixture release")
    outer = ast.parse(texts["src/pkg/a.py"]).body[0]
    inner = outer.body[0]
    helper = ast.parse(texts["src/pkg/b.py"]).body[0]
    a = target("src/pkg/a.py", "outer", outer, [1, 4], [3, 3], texts["src/pkg/a.py"], ["src/pkg/a.py:outer:1-4"])
    nested = target("src/pkg/a.py", "outer.inner", inner, [2, 3], [3, 3], texts["src/pkg/a.py"], ["src/pkg/a.py:outer.inner:2-3"])
    b = target("src/pkg/b.py", "helper", helper, [1, 2], [2, 2], texts["src/pkg/b.py"], ["src/pkg/b.py:helper:1-2"])
    single = target("src/pkg/a.py", "outer", outer, [1, 4], [4, 4], texts["src/pkg/a.py"], ["src/pkg/a.py:outer:1-4"])
    included = {path: record(repo / path) for path in ("src/pkg/a.py", "src/pkg/b.py")}
    excluded = {"src/pkg/excluded.py": record(repo / "src/pkg/excluded.py")}
    test_body = "    def test_source(self):\n        assert True\n"
    data = {"revision": git("rev-parse", "HEAD"), "release": "v1", "annotated_tag_object": git("rev-parse", "v1"),
        "source_root": "src/pkg", "source_exclusions": ["src/pkg/excluded.py"], "source_files": included,
        "excluded_supported_files": excluded, "unsupported_source_root_files": {"src/pkg/types.pyi": record(repo / "src/pkg/types.pyi")},
        "source_inventory_sha256": sha(canonical(included)), "excluded_supported_inventory_sha256": sha(canonical(excluded)),
        "upstream_test_files_read": {"tests/test_api.py": record(repo / "tests/test_api.py")},
        "upstream_test_definitions_read": {"tests/test_api.py::TestAPI.test_source": {
            "definition_lines": [2, 3], "body_lines": [2, 3], "body_source_sha256": sha(test_body.encode()),
            "full_body_read": True, "executed": False}},
        "license": {"path": "LICENSE", "local_copy": "benchmarks/fixture-license.txt", "sha256": sha((repo / "LICENSE").read_bytes())},
        "queries": [
            {"id": "multi", "query": "Find the caller and helper implementations", "kind": "present", "multi_file": True,
             "target_groups": [[a, nested], [b]], "upstream_tests_read": ["tests/test_api.py::TestAPI.test_source"]},
            {"id": "single", "query": "Find the outer return", "kind": "present", "multi_file": False,
             "target_groups": [[single]], "upstream_tests_read": []},
            {"id": "absent", "query": "Find coroutine awaiting", "kind": "absent", "multi_file": False,
             "target_groups": [], "scope": "src/pkg", "upstream_tests_read": []}],
        "counts": {"queries": 3, "present": 2, "multi_file_present": 1, "single_file_present": 1,
            "scoped_absent": 1, "required_positive_evidence_groups": 3, "target_rows": 4,
            "source_files": 2, "excluded_supported_files": 1, "unsupported_source_root_files": 1,
            "source_windows_160_40": 3, "extracted_definitions": 3, "source_files_with_extracted_implementations": 2,
            "canonical_definitions_with_multiple_windows": 0}}
    absence = {"schema": "python_source_absence_checks_v1", "queries": {"absent": {
        "literal_patterns": [r"\bawait\b"], "forbidden_ast_nodes": ["Await", "AsyncFunctionDef"],
        "reviewed_paths": sorted(included)}}}
    return repo, project, data, absence, texts, git


def test_complete_small_profile_uses_counts_from_input_not_a_particular_repository(source_fixture):
    repo, _, data, absence, _, _ = source_fixture
    result = audit.audit(data, repo, absence)
    assert result["counts"] == data["counts"]
    assert len(result["targets"]) == 4
    assert len(result["window_inventory"]) == 3 and len(result["definition_keys"]) == 3
    assert result["targets"][0]["evidence_sha256"] == result["targets"][1]["evidence_sha256"]
    assert result["targets"][0]["canonical_definition_key"] != result["targets"][1]["canonical_definition_key"]
    assert result["absence_source_checks"]["absent"]["semantic_review_required"] is True
    assert result["upstream_tests"]["tests/test_api.py::TestAPI.test_source"]["executed"] is False


@pytest.mark.parametrize("field,value", [
    ("source_file_sha256", "wrong"), ("evidence_sha256", "wrong"),
    ("evidence_byte_bounds", [0, 1]), ("definition_lines", [1, 3]),
    ("canonical_definition_key", ["src/pkg/a.py", "outer", 1, 3]),
    ("ast_definition_byte_bounds", [0, 1]), ("ast_definition_sha256", "wrong"),
    ("definition_source_byte_bounds", [0, 1]), ("definition_source_sha256", "wrong"),
    ("executable_evidence_lines", []), ("eligible_window_ids", ["not-original"]),
])
def test_literal_ast_identity_and_window_tampering_is_rejected(source_fixture, field, value):
    repo, _, data, absence, _, _ = source_fixture
    data["queries"][0]["target_groups"][0][0][field] = value
    with pytest.raises(ValueError):
        audit.audit(data, repo, absence)


@pytest.mark.parametrize("change", ["membership", "excluded", "unsupported", "counts", "multi", "duplicate_id", "duplicate_alternative", "different_interval", "empty_group", "absence_scope", "missing_test", "test_hash", "executed_test", "license"])
def test_inventory_group_test_and_license_contract_failures(source_fixture, change):
    repo, project, data, absence, _, _ = source_fixture
    if change == "membership":
        del data["source_files"]["src/pkg/b.py"]
    elif change == "excluded":
        data["excluded_supported_files"] = {}
    elif change == "unsupported":
        data["unsupported_source_root_files"] = {}
    elif change == "counts":
        data["counts"]["target_rows"] += 1
    elif change == "multi":
        data["queries"][0]["multi_file"] = False
    elif change == "duplicate_id":
        data["queries"][1]["id"] = "multi"
    elif change == "duplicate_alternative":
        data["queries"][0]["target_groups"][0].append(deepcopy(data["queries"][0]["target_groups"][0][0]))
    elif change == "different_interval":
        data["queries"][0]["target_groups"][0][1]["evidence_lines"] = [2, 3]
    elif change == "empty_group":
        data["queries"][0]["target_groups"][0] = []
    elif change == "absence_scope":
        data["queries"][2]["scope"] = "elsewhere"
    elif change == "missing_test":
        data["queries"][0]["upstream_tests_read"] = ["tests/test_api.py::missing"]
    elif change == "test_hash":
        data["upstream_test_definitions_read"]["tests/test_api.py::TestAPI.test_source"]["body_source_sha256"] = "wrong"
    elif change == "executed_test":
        data["upstream_test_definitions_read"]["tests/test_api.py::TestAPI.test_source"]["executed"] = True
    elif change == "license":
        (project / "benchmarks/fixture-license.txt").write_text("Changed license")
    with pytest.raises(ValueError):
        audit.audit(data, repo, absence)


@pytest.mark.parametrize("interval", [[True, 3], [3, 99], [0, 3], [4, 3]])
def test_physical_evidence_interval_is_strictly_typed_and_bounded(source_fixture, interval):
    repo, _, data, absence, _, _ = source_fixture
    for item in data["queries"][0]["target_groups"][0]:
        item["evidence_lines"] = interval
    with pytest.raises(ValueError):
        audit.audit(data, repo, absence)


def test_complete_interval_cannot_be_credited_by_joining_separate_windows():
    text = "def long():\n" + "    total += 1\n" * 200
    node = ast.parse(text).body[0]
    raw_target = target("src/long.py", "long", node, [1, 201], [120, 180], text, ["invented"])
    class Window:
        id, path, symbol = "src/long.py:long:1-160", "src/long.py", "long"
        start_line, end_line = 1, 160
    with pytest.raises(ValueError, match="No one original window"):
        audit.target_facts(raw_target, {"src/long.py": text}, {"src/long.py": [("long", node)]},
            [Window()], {Window.id: ("src/long.py", "long", 1, 201)}, {"src/long.py": {"sha256": sha(text.encode())}})


def test_docstring_only_evidence_is_not_executable():
    text = 'def docs():\n    """Only description."""\n    return 1\n'
    node = ast.parse(text).body[0]
    data = target("src/a.py", "docs", node, [1, 3], [2, 2], text, ["source-window"])
    class Window:
        id, start_line, end_line = "source-window", 1, 3
    with pytest.raises(ValueError, match="no executable"):
        audit.target_facts(data, {"src/a.py": text}, {"src/a.py": [("docs", node)]},
            [Window()], {Window.id: ("src/a.py", "docs", 1, 3)}, {"src/a.py": {"sha256": sha(text.encode())}})


def test_duplicate_display_symbol_cannot_substitute_another_canonical_definition():
    text = "def same():\n    return 1\n\ndef same():\n    return 2\n"
    first, second = ast.parse(text).body
    raw = target("src/a.py", "same", first, [1, 2], [2, 2], text, ["first"])
    raw["canonical_definition_key"] = ["src/a.py", "same", 4, 5]
    with pytest.raises(ValueError, match="outside canonical"):
        audit.target_facts(raw, {"src/a.py": text}, {"src/a.py": [("same", first), ("same", second)]}, [], {}, {})


@pytest.mark.parametrize("mutation", ["missing_query", "literal_match", "ast_match", "unknown_ast", "missing_path"])
def test_absence_checks_cannot_omit_paths_or_silently_accept_present_source(source_fixture, mutation):
    _, _, data, absence, texts, _ = source_fixture
    sources = {path: texts[path] for path in data["source_files"]}
    if mutation == "missing_query":
        absence["queries"] = {}
    elif mutation == "literal_match":
        absence["queries"]["absent"]["literal_patterns"] = ["return"]
    elif mutation == "ast_match":
        sources["src/pkg/b.py"] = "async def helper():\n    return await call()\n"
        absence["queries"]["absent"]["literal_patterns"] = ["not_in_source"]
    elif mutation == "unknown_ast":
        absence["queries"]["absent"]["forbidden_ast_nodes"] = ["Name"]
    else:
        absence["queries"]["absent"]["reviewed_paths"].pop()
    with pytest.raises(ValueError):
        audit.absence_facts(absence, data["queries"], sources)


def test_unicode_crlf_and_bare_cr_physical_lines_remain_literal():
    text = 'a\r\nb\rc\nvalue = "\u2028"\n'
    assert audit.lines(text) == ["a\r\n", "b\r", "c\n", 'value = "\u2028"\n']


def test_source_guard_denies_model_selector_repository_imports_and_network_then_restores_state():
    original = socket.getaddrinfo
    for name in ("numpy", "mlx", "semantic_search.backend", "semantic_search.selection", "semantic_search.complementary_context", "attrs", "attr"):
        with pytest.raises(RuntimeError, match="forbids import"):
            audit.SourceOnly().find_spec(name)
    with audit.source_only():
        with pytest.raises(RuntimeError, match="forbids network"):
            socket.getaddrinfo("must-not-resolve.invalid", 443)
    assert socket.getaddrinfo is original


def test_help_is_stdlib_only_and_does_not_load_fresh_data():
    code = ("import runpy,sys;sys.argv=['audit_complementary_preflight.py','--help'];\n"
            "try:runpy.run_path('scripts/audit_complementary_preflight.py',run_name='__main__')\n"
            "except SystemExit as e:assert e.code==0\n"
            "assert not any(n.startswith(('semantic_search','numpy','mlx','torch','attrs')) for n in sys.modules)")
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                            env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
    assert result.returncode == 0, result.stderr
    assert "--absence-checks" in result.stdout and "--corpus" in result.stdout
