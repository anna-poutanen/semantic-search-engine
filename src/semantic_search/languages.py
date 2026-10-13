"""Saved-source language registry and versioned extraction membership policy.

The optional parsers are loaded only for JavaScript/TypeScript source. Python
keeps its encoding-cookie decoder, AST validation, and original extractor.
"""
from __future__ import annotations

import ast
import fnmatch
import io
import sys
import tokenize
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import PurePath


PARSER_PINS = {"tree-sitter": "0.26.0", "tree-sitter-javascript": "0.25.0",
               "tree-sitter-typescript": "0.23.2"}
# Bump when extraction, naming, validation, relations, or membership changes.
EXTRACTION_POLICY = "saved-implementations-v1"
JS_EXCLUDED_DIRECTORIES = frozenset({"node_modules", "bower_components", "dist", "build",
                                    "coverage", ".next", ".nuxt", ".svelte-kit", "out"})


@dataclass(frozen=True)
class Extractor:
    language: str
    grammar: str | None


EXTRACTORS = {".py": Extractor("python", None),
              **{ext: Extractor("javascript", "javascript")
                 for ext in (".js", ".jsx", ".mjs", ".cjs")},
              **{ext: Extractor("typescript", "typescript")
                 for ext in (".ts", ".mts", ".cts")},
              ".tsx": Extractor("typescript", "tsx")}


def extractor_for(path):
    try:
        return EXTRACTORS[PurePath(path).suffix]
    except KeyError as error:
        raise ValueError(f"Unsupported source extension: {path}") from error


def is_declaration_file(path):
    return str(path).endswith((".d.ts", ".d.mts", ".d.cts"))


def source_eligible(path):
    path = PurePath(path)
    if path.suffix not in EXTRACTORS:
        return False
    # Preserve Python discovery even beneath directories named build/dist.
    return path.suffix == ".py" or (not is_declaration_file(path)
        and not JS_EXCLUDED_DIRECTORIES.intersection(path.parts[:-1]))


def validate_exclusions(exclusions):
    if isinstance(exclusions, str):
        raise ValueError("source_exclusions must be a sequence of repository-relative globs")
    try:
        result = tuple(exclusions)
    except TypeError as error:
        raise ValueError("source_exclusions must be a sequence of repository-relative globs") from error
    if any(not isinstance(item, str) or not item.strip() or PurePath(item).is_absolute()
           or ".." in PurePath(item).parts for item in result):
        raise ValueError("source_exclusions must contain nonempty repository-relative globs")
    return tuple(sorted(set(result)))


def path_excluded(path, exclusions):
    return any(fnmatch.fnmatchcase(str(path), pattern) for pattern in exclusions)


def decode_source(path, data):
    if extractor_for(path).language == "python":
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
        return data.decode(encoding)
    # Retain a UTF-8 BOM in source windows; the grammar accepts it.
    return data.decode("utf-8")


def validate_source(path, source):
    if extractor_for(path).language == "python":
        ast.parse(source, filename=str(path))
    else:
        from semantic_search.javascript import parse_source
        parse_source(path, source)


def extractor_fingerprint():
    installed = {}
    for package in PARSER_PINS:
        try:
            installed[package] = version(package)
        except PackageNotFoundError:
            installed[package] = None
    return {"policy": EXTRACTION_POLICY, "python_ast": list(sys.version_info[:2]),
            "extensions": {ext: item.grammar for ext, item in EXTRACTORS.items()},
            "parser_pins": PARSER_PINS, "installed_parsers": installed,
            "js_excluded_directories": sorted(JS_EXCLUDED_DIRECTORIES),
            "window_lines": 160, "overlap_lines": 40}
