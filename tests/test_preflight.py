"""Source preflight reports real saved scope without inference or index writes."""
import fnmatch
import glob
import hashlib
import importlib.abc
import json
from pathlib import Path
import shlex
import shutil
import socket
import sqlite3
import subprocess
import sys
from unittest.mock import patch
import urllib.request

import pytest

from semantic_search import preflight
from semantic_search.corpus import _source_files, extract_snippets, Snippet
from semantic_search.index import document_text


def write(repo, relative, source):
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(source.encode() if isinstance(source, str) else source)
    return path


def git(repo, *arguments):
    return subprocess.run(["git", "-C", str(repo), *arguments], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "src").mkdir()
    return tmp_path


@pytest.fixture
def tracked_repo(repo):
    if shutil.which("git") is None:
        pytest.skip("Git is unavailable")
    git(repo, "init", "-q")
    return repo


def test_directory_fallback_counts_languages_and_matches_real_extraction(repo):
    private = "SOURCE_SENTINEL_NEVER_IN_REPORT"
    write(repo, "src/a.py", f'def same():\n    return "{private}"\n\ndef same():\n    return "{private}"\n')
    write(repo, "src/code.js", "function run(){return 1;}\n")
    write(repo, "src/code.ts", "function typed(): number {return 2;}\n")
    report = preflight.inspect_source(repo)
    snippets = extract_snippets(repo, "src", 160, 40)
    assert report["status"] == "ready" and report["stable_snapshot"]
    assert report["membership"]["kind"] == "directory_fallback"
    assert report["files"] == {"eligible": 3, "excluded": 0, "included": 3, "parsed": 3,
                               "languages": {language: {"eligible": 1, "excluded": 0, "included": 1, "parsed": 1}
                                             for language in preflight.LANGUAGES}}
    assert report["windows"]["total"] == len(snippets) == 4
    assert report["windows"]["unique_documents"] == len({hashlib.sha256(document_text(item).encode()).hexdigest()
                                                         for item in snippets}) == 3
    assert report["windows"]["complete"]
    assert report["window_policy"] == {"max_lines": 160, "overlap_lines": 40}
    assert len(report["snapshot_sha256"]) == 64
    assert private not in json.dumps(report)
    assert report["repo"] == str(repo.resolve()) and report["source_root"] == "src"


def test_git_membership_builtin_language_policy_and_explicit_exclusions(tracked_repo):
    repo = tracked_repo
    sources = {"src/code.js": "function run(){return 1;}", "src/excluded.py": "def e():\n    return 1\n",
               "src/build/retained.py": "def p():\n    return 1\n",
               "src/build/output.js": "function b(){return 1;}",
               "src/node_modules/dep.ts": "function d(){return 1;}",
               "src/types.d.ts": "declare function no(): void;", "src/readme.txt": "not source"}
    for path, source in sources.items():
        write(repo, path, source)
    git(repo, "add", "src")
    write(repo, "src/untracked.py", "def no():\n    return 1\n")
    report = preflight.inspect_source(repo, source_exclusions=["src/excluded.py", "src/excluded.py"])
    expected = _source_files(repo, repo / "src", ["src/excluded.py"])
    assert report["status"] == "ready" and report["membership"]["kind"] == "git_tracked"
    assert report["files"]["eligible"] == 3 and report["files"]["excluded"] == 1
    assert report["files"]["included"] == len(expected) == 2
    assert report["files"]["languages"]["python"] == {"eligible": 2, "excluded": 1, "included": 1, "parsed": 1}
    assert report["source_exclusions"] == ["src/excluded.py"]
    assert report["windows"]["total"] == 2


@pytest.mark.parametrize("kwargs", [
    {"limit": 0}, {"limit": 101}, {"limit": True}, {"limit": 1.2},
    {"source_root": ""}, {"source_root": "../src"}, {"source_root": "/tmp"},
    {"source_root": "src\nPRIVATE"}, {"source_root": None},
    {"source_exclusions": "src/*"}, {"source_exclusions": ["../*"]},
    {"source_exclusions": ["/tmp/*"]}, {"source_exclusions": ["src/\x1bPRIVATE"]},
    {"source_exclusions": [None]},
])
def test_invalid_arguments_fail_before_discovery_or_source_reads(repo, kwargs):
    with patch.object(preflight, "_capture", side_effect=AssertionError("discovery")), \
            patch.object(Path, "read_bytes", side_effect=AssertionError("source read")):
        with pytest.raises(ValueError):
            preflight.inspect_source(repo, **kwargs)


def test_missing_or_symlink_escaping_source_root_is_rejected_before_reads(repo, tmp_path):
    external = tmp_path.parent / (tmp_path.name + "-external")
    external.mkdir()
    try:
        (repo / "escape").symlink_to(external, target_is_directory=True)
        with patch.object(preflight, "_capture", side_effect=AssertionError("discovery")), \
                patch.object(Path, "read_bytes", side_effect=AssertionError("source read")):
            for source_root in ("absent", "escape"):
                with pytest.raises(ValueError):
                    preflight.inspect_source(repo, source_root)
    finally:
        external.rmdir()


def test_nested_source_directory_and_symlinks_match_existing_discovery(repo):
    write(repo, "src/a.py", "def a():\n    return 1\n")
    outside = write(repo, "outside.py", "def outside():\n    return 2\n")
    (repo / "src/escape.py").symlink_to(outside)
    (repo / "src/internal.py").symlink_to(repo / "src/a.py")
    report = preflight.inspect_source(repo, "src/./")
    assert report["status"] == "ready" and report["source_root"] == "src"
    assert report["files"]["included"] == len(_source_files(repo, repo / "src")) == 2
    assert report["windows"]["total"] == 2


def test_empty_and_declaration_only_scopes_are_ready_with_clear_guidance(repo):
    empty = preflight.inspect_source(repo)
    assert empty["status"] == "ready" and empty["windows"]["total"] == 0
    assert "No eligible implementations" in preflight.format_preflight(empty)
    write(repo, "src/types.py", "def absent():\n    ...\n")
    declaration = preflight.inspect_source(repo)
    assert declaration["status"] == "ready" and declaration["files"]["parsed"] == 1
    assert declaration["windows"]["total"] == 0
    assert "No implementation bodies" in preflight.format_preflight(declaration)


def test_physical_line_windows_and_unicode_rendered_character_counts(repo):
    source = "def large():\r" + "    value = 'é😊'\r" * 330 + "    return value\r"
    write(repo, "src/large.py", source)
    snippets = extract_snippets(repo, "src", 160, 40)
    report = preflight.inspect_source(repo)
    assert report["status"] == "ready"
    assert report["windows"]["total"] == len(snippets) == 3
    assert report["windows"]["largest_window_characters"] == max(map(lambda item: len(document_text(item)), snippets))
    assert report["windows"]["largest_window_characters"] < max(len(document_text(item).encode()) for item in snippets)


def test_real_single_line_minified_source_reports_all_large_windows_with_bounded_details(repo):
    write(repo, "src/vendor/a.min.js", 'function outer(){function inner(){return "' + "x" * 32_010 + '";}return inner();}\n')
    write(repo, "src/vendor/b.min.mjs", "export const run = () => 1;\n")
    write(repo, "src/vendor/c.min.cjs", "function run(){return 2;}\n")
    report = preflight.inspect_source(repo, limit=1)
    snippets = extract_snippets(repo, "src", 160, 40)
    expected_large = [item for item in snippets if len(document_text(item)) > 32_000]
    assert report["status"] == "ready"
    assert report["windows"]["total"] == len(snippets)
    assert report["windows"]["over_threshold"] == len(expected_large) == 2
    assert len(report["windows"]["largest"]) == 1 and report["windows"]["omitted"] == 1
    assert report["likely_minified"] == {"count": 3, "paths": ["src/vendor/a.min.js"], "omitted": 2}
    assert report["exclusion_suggestions"]["count"] == 3
    assert len(report["exclusion_suggestions"]["items"]) == 1
    assert "not token counts" in report["windows"]["measurement"]
    assert "nothing was excluded automatically" in preflight.format_preflight(report)
    assert report["files"]["excluded"] == 0


def test_threshold_applies_to_rendered_document_strictly_greater_than_32000(repo):
    write(repo, "src/a.py", "def run():\n    return 1\n")
    def make(size, symbol):
        item = Snippet(symbol, "src/a.py", symbol, 1, 2, "", "unused")
        return Snippet(symbol, item.path, symbol, 1, 2, "x" * (size - len(document_text(item))), "unused")
    with patch.object(preflight, "extract_snippets", return_value=[make(31_999, "a"), make(32_000, "b"), make(32_001, "c")]):
        report = preflight.inspect_source(repo)
    assert report["windows"]["total"] == 3 and report["windows"]["over_threshold"] == 1
    assert report["windows"]["largest"][0]["rendered_characters"] == 32_001


def test_all_files_are_checked_and_parse_errors_never_emit_source_or_exception_text(repo):
    sentinel = "PRIVATE_SYNTAX_SOURCE_SENTINEL"
    for index in range(3):
        write(repo, f"src/bad{index}.py", f"def broken():\n    return {sentinel} !\n")
    write(repo, "src/good.py", "def good():\n    return 1\n")
    report = preflight.inspect_source(repo, limit=1)
    assert report["status"] == "blocked" and report["stable_snapshot"]
    assert report["files"]["included"] == 4 and report["files"]["parsed"] == 1
    assert report["errors"] == {"count": 3, "items": [{"code": "parse_error", "path": "src/bad0.py", "line": 2}], "omitted": 2}
    assert not report["windows"]["complete"] and report["windows"]["total"] == 1
    assert sentinel not in json.dumps(report) and sentinel not in preflight.format_preflight(report)
    assert "Partial extraction" in preflight.format_preflight(report)


def test_js_parser_error_reports_line_without_source_contents(repo):
    write(repo, "src/broken.js", "function broken(){\n return !PRIVATE !;\n}\n")
    report = preflight.inspect_source(repo)
    assert report["status"] == "blocked"
    assert report["errors"]["items"][0] == {"code": "parse_error", "path": "src/broken.js", "line": 2}
    assert "PRIVATE" not in json.dumps(report)


def test_encoding_cookie_is_honored_and_bad_utf8_is_source_free(repo):
    write(repo, "src/latin.py", b"# coding: latin-1\ndef run():\n    return '\xe9'\n")
    write(repo, "src/bad.js", b"function x(){return '\xff';}\n")
    report = preflight.inspect_source(repo)
    assert report["status"] == "blocked" and report["files"]["parsed"] == 1
    assert report["errors"]["items"] == [{"code": "decode_error", "path": "src/bad.js", "line": None}]


@pytest.mark.parametrize("error, code", [(ImportError("PRIVATE"), "parser_unavailable"),
                                         (ValueError("PRIVATE"), "extraction_error"),
                                         (RecursionError("PRIVATE"), "extraction_error")])
def test_extractor_failures_are_bounded_fixed_categories(repo, error, code):
    write(repo, "src/a.py", "def run():\n    return 1\n")
    with patch.object(preflight, "extract_snippets", side_effect=error):
        report = preflight.inspect_source(repo)
    assert report["status"] == "blocked"
    assert report["errors"]["items"] == [{"code": code, "path": "src/a.py", "line": None}]
    assert "PRIVATE" not in json.dumps(report)


def test_literal_file_exclusions_escape_globs_and_shell_syntax(repo):
    path = "src/odd[1]*?';$.py"
    write(repo, path, "def broken(\n")
    report = preflight.inspect_source(repo)
    suggestion = report["exclusion_suggestions"]["items"][0]
    assert suggestion["path"] == path and suggestion["pattern"] == glob.escape(path)
    assert shlex.split(suggestion["argument"]) == ["--exclude-source", glob.escape(path)]
    assert fnmatch.fnmatchcase(path, suggestion["pattern"])
    assert not fnmatch.fnmatchcase("src/odd1xxx';$.py", suggestion["pattern"])
    excluded = preflight.inspect_source(repo, source_exclusions=[suggestion["pattern"]])
    assert excluded["status"] == "ready" and excluded["files"]["excluded"] == 1
    assert suggestion["argument"] in preflight.format_preflight(report)


def test_human_output_escapes_terminal_control_characters_in_filenames(repo):
    write(repo, "src/bad\x1b[31m\nname.py", "def broken(\n")
    report = preflight.inspect_source(repo)
    text = preflight.format_preflight(report)
    assert "\x1b" not in text and "\\u001b" in text
    assert "\\nname.py" in text


def test_saved_byte_change_blocks_ready_even_when_new_source_parses(repo):
    path = write(repo, "src/a.py", "def run():\n    return 1\n")
    original = extract_snippets
    def change(*args, **kwargs):
        result = original(*args, **kwargs)
        path.write_text("def run():\n    return 2\n")
        return result
    with patch.object(preflight, "extract_snippets", side_effect=change):
        report = preflight.inspect_source(repo)
    assert report["status"] == "blocked" and not report["stable_snapshot"]
    assert report["snapshot_sha256"] is None
    assert {"code": "source_changed", "path": None, "line": None} in report["errors"]["items"]


def test_git_membership_change_blocks_ready(tracked_repo):
    write(tracked_repo, "src/a.py", "def run():\n    return 1\n")
    git(tracked_repo, "add", "src/a.py")
    original = extract_snippets
    def add(*args, **kwargs):
        result = original(*args, **kwargs)
        write(tracked_repo, "src/new.py", "def new():\n    return 2\n")
        git(tracked_repo, "add", "src/new.py")
        return result
    with patch.object(preflight, "extract_snippets", side_effect=add):
        report = preflight.inspect_source(tracked_repo)
    assert report["status"] == "blocked" and not report["stable_snapshot"]


def test_git_ref_change_blocks_ready_without_source_change(tracked_repo):
    write(tracked_repo, "src/a.py", "def run():\n    return 1\n")
    git(tracked_repo, "add", "src/a.py")
    original = extract_snippets
    def change_ref(*args, **kwargs):
        result = original(*args, **kwargs)
        git(tracked_repo, "symbolic-ref", "HEAD", "refs/heads/changed")
        return result
    with patch.object(preflight, "extract_snippets", side_effect=change_ref):
        report = preflight.inspect_source(tracked_repo)
    assert report["status"] == "blocked" and not report["stable_snapshot"]


def test_excluded_content_and_untracked_git_files_are_outside_snapshot(tracked_repo):
    write(tracked_repo, "src/a.py", "def run():\n    return 1\n")
    excluded = write(tracked_repo, "src/excluded.py", "def old():\n    return 1\n")
    git(tracked_repo, "add", "src")
    original = extract_snippets
    def change(*args, **kwargs):
        result = original(*args, **kwargs)
        excluded.write_text("INVALID PRIVATE SOURCE\n")
        write(tracked_repo, "src/untracked.py", "INVALID PRIVATE SOURCE\n")
        return result
    with patch.object(preflight, "extract_snippets", side_effect=change):
        report = preflight.inspect_source(tracked_repo, source_exclusions=["src/excluded.py"])
    assert report["status"] == "ready" and report["files"]["included"] == 1


def test_source_unreadable_is_blocked_without_exception_text(repo):
    write(repo, "src/a.py", "def run():\n    return 1\n")
    with patch.object(Path, "read_bytes", side_effect=PermissionError("PRIVATE")):
        report = preflight.inspect_source(repo)
    assert report["status"] == "blocked" and report["files"]["parsed"] == 0
    assert not report["stable_snapshot"]
    assert report["errors"]["items"] == [{"code": "source_unreadable", "path": "src/a.py", "line": None}]
    assert report["snapshot_sha256"] is None and "PRIVATE" not in json.dumps(report)


def test_confirmation_failure_never_leaves_a_ready_or_stable_claim(repo):
    write(repo, "src/a.py", "def run():\n    return 1\n")
    capture = preflight._capture
    calls = 0
    def fail_confirmation(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("PRIVATE confirmation failure")
        return capture(*args, **kwargs)
    with patch.object(preflight, "_capture", side_effect=fail_confirmation):
        report = preflight.inspect_source(repo)
    assert report["status"] == "blocked" and not report["stable_snapshot"]
    assert report["snapshot_sha256"] is None and not report["windows"]["complete"]
    assert report["files"]["parsed"] == 1 and report["windows"]["total"] == 1
    assert report["errors"]["items"] == [{"code": "snapshot_unavailable", "path": None, "line": None}]
    assert "PRIVATE" not in json.dumps(report)


@pytest.mark.parametrize("error", [FileNotFoundError(), subprocess.TimeoutExpired("git", 10)])
def test_git_failure_uses_the_same_directory_fallback_as_corpus(repo, error):
    write(repo, "src/a.py", "def run():\n    return 1\n")
    with patch.object(preflight.subprocess, "run", side_effect=error):
        report = preflight.inspect_source(repo)
    assert report["status"] == "ready" and report["membership"]["kind"] == "directory_fallback"
    assert report["files"]["included"] == 1


def test_inspection_never_loads_models_or_opens_network_database_or_output_files(repo):
    write(repo, "src/a.py", "def run():\n    return 1\n")
    before = {path.relative_to(repo): path.read_bytes() for path in repo.rglob("*") if path.is_file()}
    class RejectInferenceImports(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.startswith(("mlx", "torch", "transformers", "huggingface_hub", "semantic_search.embeddings",
                                    "semantic_search.backend", "semantic_search.store", "semantic_search.server")):
                raise AssertionError("Unexpected inference/service import: " + fullname)
    blocker = RejectInferenceImports()
    sys.meta_path.insert(0, blocker)
    run = subprocess.run
    def git_only(arguments, **kwargs):
        assert arguments[0] == "git"
        return run(arguments, **kwargs)
    try:
        with patch.object(socket, "create_connection", side_effect=AssertionError("network")), \
                patch.object(urllib.request, "urlopen", side_effect=AssertionError("download")), \
                patch.object(sqlite3, "connect", side_effect=AssertionError("database")), \
                patch.object(Path, "write_text", side_effect=AssertionError("write")), \
                patch.object(Path, "write_bytes", side_effect=AssertionError("write")), \
                patch.object(preflight.subprocess, "run", side_effect=git_only):
            report = preflight.inspect_source(repo)
    finally:
        sys.meta_path.remove(blocker)
    assert report["status"] == "ready"
    after = {path.relative_to(repo): path.read_bytes() for path in repo.rglob("*") if path.is_file()}
    assert before == after
