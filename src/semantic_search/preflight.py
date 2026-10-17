"""Inspect saved-source scope without loading models or creating an index.

This is a local report: paths and Git metadata identify the inspected scope, but
source contents and exception messages never appear in its output. Character
counts describe source shape, not tokenizer lengths or embedding latency.
"""
from __future__ import annotations

from dataclasses import dataclass
import glob
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess

from semantic_search.corpus import extract_snippets
from semantic_search.index import document_text
from semantic_search.languages import (decode_source, extractor_for, extractor_fingerprint,
                                      path_excluded, source_eligible, validate_exclusions)

MAX_LINES = 160
OVERLAP_LINES = 40
RENDERED_CHARACTER_THRESHOLD = 32_000
LANGUAGES = ("python", "javascript", "typescript")


def _has_controls(value):
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _validate(repo, source_root, exclusions, limit):
    # Validate the entire request before filesystem discovery or source reads.
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer between 1 and 100")
    if (not isinstance(source_root, str) or not source_root.strip()
            or _has_controls(source_root) or Path(source_root).is_absolute()
            or ".." in Path(source_root).parts):
        raise ValueError("source_root must be a repository-relative directory without '..'")
    exclusions = validate_exclusions(exclusions)
    if any(_has_controls(pattern) for pattern in exclusions):
        raise ValueError("source_exclusions must not contain control characters")
    try:
        repo = Path(repo).expanduser()
        if _has_controls(str(repo)):
            raise ValueError
        repo = repo.resolve()
        root = (repo / source_root).resolve()
        if not repo.is_dir() or not root.is_relative_to(repo) or not root.is_dir():
            raise ValueError
    except (OSError, TypeError, ValueError, RuntimeError) as error:
        raise ValueError("repo and source_root must name existing directories inside the repository") from error
    return repo, root, exclusions


def _discover(repo, root):
    """Mirror corpus._source_files, retaining which discovery path succeeded.

    Explicit exclusions are applied after discovery so the report can count the
    same eligible files both before and after the user's scope choices.
    """
    try:
        result = subprocess.run(["git", "-C", str(repo), "ls-files", "-z", "--", str(root.relative_to(repo))],
                                capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is not None and result.returncode == 0:
        candidates = [repo / name.decode("utf-8", "surrogateescape")
                      for name in result.stdout.split(b"\0") if name]
        kind = "git_tracked"
    else:
        candidates = list(root.rglob("*"))
        kind = "directory_fallback"
    paths = sorted(path for path in candidates
                   if source_eligible(path.relative_to(repo)) and path.is_file()
                   and path.resolve().is_relative_to(root))
    return paths, kind


def _git_refs(repo, kind):
    if kind != "git_tracked":
        return {"git_revision": None, "git_branch": None}
    refs = {}
    for field, arguments in (("git_revision", ["rev-parse", "HEAD"]),
                             ("git_branch", ["symbolic-ref", "--quiet", "HEAD"])):
        result = subprocess.run(["git", "-C", str(repo), *arguments], capture_output=True,
                                text=True, errors="surrogateescape", timeout=10, check=False)
        refs[field] = result.stdout.strip() if result.returncode == 0 else None
    return refs


@dataclass
class _Snapshot:
    membership: dict
    eligible: tuple[str, ...]
    excluded: tuple[str, ...]
    raw: dict[str, bytes]
    failures: list[dict]

    def identity(self):
        value = {"membership": self.membership, "eligible": self.eligible,
                 "excluded": self.excluded,
                 "files": {path: hashlib.sha256(raw).hexdigest() for path, raw in self.raw.items()},
                 "failures": self.failures}
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def _error(code, path=None, line=None):
    return {"code": code, "path": path, "line": line}


def _capture(repo, root, exclusions):
    paths, kind = _discover(repo, root)
    eligible = tuple(path.relative_to(repo).as_posix() for path in paths)
    excluded = tuple(path for path in eligible if path_excluded(path, exclusions))
    excluded_set = set(excluded)
    raw, failures = {}, []
    for path in paths:
        relative = path.relative_to(repo).as_posix()
        if relative in excluded_set:
            continue
        try:
            # Resolve again immediately before reading; directory discovery also
            # rejects symlinks leading outside the selected source directory.
            if not path.resolve().is_relative_to(root):
                failures.append(_error("source_changed", relative))
                continue
            raw[relative] = path.read_bytes()
        except (OSError, ValueError, RuntimeError):
            failures.append(_error("source_unreadable", relative))
    membership = {"kind": kind, **_git_refs(repo, kind)}
    return _Snapshot(membership, eligible, excluded, raw, failures)


def _syntax_line(error):
    line = getattr(error, "lineno", None)
    if type(line) is int and line > 0:
        return line
    # JS/TS parser exceptions supply a location in a fixed message. Never return
    # the exception text, which can include paths or a Python source line.
    matches = re.findall(r"\bat line ([1-9][0-9]*)\b", str(error))
    return int(matches[-1]) if matches else None


def _bounded(items, limit, *, key="items"):
    return {"count": len(items), key: items[:limit], "omitted": max(0, len(items) - limit)}


def inspect_source(repo, source_root="src", *, source_exclusions=(), limit=10):
    """Return a bounded, source-free local report for the exact saved scope.

    Invalid arguments raise ValueError before source reads. ``ready`` means all
    included files parsed/extracted and their bytes, eligible membership and Git
    refs were unchanged by a second capture. It does not mean model availability,
    retrieval quality, tokenizer length or embedding performance was checked.
    The report writes nothing, and suggested exclusions are never applied.
    """
    repo, root, exclusions = _validate(repo, source_root, source_exclusions, limit)
    relative_root = root.relative_to(repo).as_posix()
    report = {"schema_version": 1, "status": "blocked", "stable_snapshot": False,
              "repo": str(repo), "source_root": relative_root,
              "source_exclusions": list(exclusions), "snapshot_sha256": None,
              "membership": {"kind": None, "git_revision": None, "git_branch": None},
              "window_policy": {"max_lines": MAX_LINES, "overlap_lines": OVERLAP_LINES},
              "extractor": extractor_fingerprint()}
    language_counts = {language: {"eligible": 0, "excluded": 0, "included": 0, "parsed": 0}
                       for language in LANGUAGES}
    errors, largest, document_hashes, minified = [], [], set(), []
    suggestions = {}
    total_windows = over_threshold = largest_size = parsed_files = 0
    eligible_count = excluded_count = included_count = 0
    try:
        initial = _capture(repo, root, exclusions)
        report["membership"] = initial.membership
        eligible_count, excluded_count = len(initial.eligible), len(initial.excluded)
        included_count = eligible_count - excluded_count
        errors.extend(initial.failures)
        excluded = set(initial.excluded)
        for path in initial.eligible:
            counts = language_counts[extractor_for(path).language]
            counts["eligible"] += 1
            counts["excluded" if path in excluded else "included"] += 1
            if path not in excluded and path.endswith((".min.js", ".min.mjs", ".min.cjs")):
                minified.append(path)
                suggestions.setdefault(path, set()).add("likely_minified")
        for path, raw in initial.raw.items():
            try:
                decoded = decode_source(path, raw)
                snippets = extract_snippets(repo, relative_root, MAX_LINES, OVERLAP_LINES,
                                            sources={path: decoded}, source_exclusions=exclusions)
            except SyntaxError as error:
                errors.append(_error("parse_error", path, _syntax_line(error)))
                suggestions.setdefault(path, set()).add("parse_error")
                continue
            except (UnicodeError, LookupError):
                errors.append(_error("decode_error", path))
                continue
            except ImportError:
                errors.append(_error("parser_unavailable", path))
                continue
            except (OSError, ValueError, RecursionError):
                errors.append(_error("extraction_error", path))
                continue
            parsed_files += 1
            language_counts[extractor_for(path).language]["parsed"] += 1
            for snippet in snippets:
                rendered = document_text(snippet)
                digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
                document_hashes.add(digest)
                total_windows += 1
                size = len(rendered)
                largest_size = max(largest_size, size)
                if size > RENDERED_CHARACTER_THRESHOLD:
                    over_threshold += 1
                    largest.append({"path": snippet.path, "start_line": snippet.start_line,
                                    "end_line": snippet.end_line, "rendered_characters": size,
                                    "document_sha256": digest})
                    # Only retain the largest detail rows. Total counts and
                    # distinct document hashes remain exact for parsed files.
                    largest.sort(key=lambda item: (-item["rendered_characters"], item["path"],
                                                   item["start_line"], item["end_line"],
                                                   item["document_sha256"]))
                    del largest[limit:]
                    suggestions.setdefault(path, set()).add("oversized_window")
        final = _capture(repo, root, exclusions)
        unchanged = initial.identity() == final.identity()
        report["stable_snapshot"] = unchanged and not initial.failures and not final.failures
        if not unchanged:
            errors.append(_error("source_changed"))
        elif report["stable_snapshot"]:
            report["snapshot_sha256"] = initial.identity()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        errors.append(_error("snapshot_unavailable"))
    # A blocked report's counts describe only the captured/parsed files, never a
    # promise that a full implementation inventory was produced.
    report["files"] = {"eligible": eligible_count, "excluded": excluded_count,
                       "included": included_count, "parsed": parsed_files,
                       "languages": language_counts}
    report["windows"] = {"total": total_windows, "unique_documents": len(document_hashes),
                         "complete": report["stable_snapshot"] and not errors,
                         "rendered_character_threshold": RENDERED_CHARACTER_THRESHOLD,
                         "over_threshold": over_threshold, "largest_window_characters": largest_size,
                         "largest": largest, "omitted": max(0, over_threshold - limit),
                         "measurement": "Rendered Unicode characters are a source-shape heuristic, not token counts or a latency estimate."}
    report["likely_minified"] = _bounded(minified, limit, key="paths")
    report["errors"] = _bounded(errors, limit)
    suggestion_rows = []
    for path in sorted(suggestions):
        pattern = glob.escape(path)
        suggestion_rows.append({"path": path, "pattern": pattern,
                                "argument": "--exclude-source " + shlex.quote(pattern),
                                "reasons": sorted(suggestions[path])})
    report["exclusion_suggestions"] = _bounded(suggestion_rows, limit)
    if report["stable_snapshot"] and not errors:
        report["status"] = "ready"
    return report


def _visible(value):
    # Quote paths so unusual filenames cannot inject terminal escape sequences.
    return json.dumps(value, ensure_ascii=True)


def format_preflight(report):
    """Format inspect_source output as a concise local scope summary."""
    files, windows = report["files"], report["windows"]
    kind = report["membership"]["kind"]
    membership = {"git_tracked": "Git-tracked files", "directory_fallback": "directory fallback"}.get(kind, "unavailable membership")
    lines = [f"Source preflight: {report['status']} ({membership}).",
             f"Repository: {_visible(report['repo'])}; source directory: {_visible(report['source_root'])}.",
             f"Files: {files['included']} included, {files['excluded']} explicitly excluded, {files['parsed']} parsed ({files['eligible']} eligible)."]
    if report["source_exclusions"]:
        lines.append("Exclusions: " + ", ".join(_visible(item) for item in report["source_exclusions"]) + ".")
    language_text = ", ".join(f"{language} {counts['included']}"
                              for language, counts in files["languages"].items() if counts["included"])
    if language_text:
        lines.append("Included languages: " + language_text + ".")
    qualifier = "" if windows["complete"] else "Partial extraction: "
    lines.append(f"{qualifier}{windows['total']} implementation windows, {windows['unique_documents']} distinct rendered documents.")
    if not files["included"]:
        lines.append("No eligible implementations are in this scope. Check the source directory, exclusions, saved files and Git tracking.")
    elif windows["complete"] and not windows["total"]:
        lines.append("No implementation bodies were extracted from the included files.")
    if windows["over_threshold"] or report["likely_minified"]["count"]:
        lines.append(f"Source shape: {windows['over_threshold']} windows exceed {windows['rendered_character_threshold']} rendered characters; "
                     f"{report['likely_minified']['count']} likely minified files. These are heuristics, not token counts or latency estimates.")
    for item in windows["largest"]:
        lines.append(f"Large window: {_visible(item['path'])}:{item['start_line']}-{item['end_line']} ({item['rendered_characters']} rendered characters).")
    if windows["omitted"]:
        lines.append(f"{windows['omitted']} more oversized windows omitted from this display.")
    error_messages = {"parse_error": "source does not parse", "decode_error": "source encoding could not be decoded",
                      "parser_unavailable": "the pinned language parser is unavailable",
                      "extraction_error": "implementation extraction failed; check pinned language parser installation",
                      "source_unreadable": "saved source could not be read", "source_changed": "saved source, membership or Git refs changed; retry with stable files",
                      "snapshot_unavailable": "a stable saved-source snapshot could not be read"}
    for item in report["errors"]["items"]:
        location = f" {_visible(item['path'])}" if item["path"] is not None else ""
        if item["line"] is not None:
            location += f":{item['line']}"
        lines.append(f"Blocked{location}: {error_messages[item['code']]}.")
    if report["errors"]["omitted"]:
        lines.append(f"{report['errors']['omitted']} more source errors omitted from this display.")
    if report["exclusion_suggestions"]["items"]:
        lines.append("If these files are outside your intended search scope, rerun with explicit exclusions (nothing was excluded automatically):")
        for item in report["exclusion_suggestions"]["items"]:
            # Preserve a directly copyable shell argument for ordinary filenames;
            # print an escaped JSON representation for filenames with controls.
            argument = item["argument"]
            lines.append("  " + (_visible(argument) if _has_controls(argument) else argument))
        if report["exclusion_suggestions"]["omitted"]:
            lines.append(f"  {report['exclusion_suggestions']['omitted']} more file suggestions omitted.")
    lines.append("Preflight checked source only; it did not load models, measure tokens, or create an index.")
    return "\n".join(lines)
