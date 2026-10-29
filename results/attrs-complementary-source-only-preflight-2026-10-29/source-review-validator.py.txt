#!/usr/bin/env python3
"""Source-only, repository-agnostic Python evidence and membership preflight.

No third-party repository code is executed. The shared trust boundary consists
only of the existing saved-source extractor/decoder; AST identity, source bytes,
whole-window eligibility and every authored evidence assertion are checked here.
Semantic necessity still requires a separate human/agent source review.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import fnmatch
import hashlib
import importlib.abc
import json
from pathlib import Path, PurePosixPath
import re
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE_MODULES = ("src/semantic_search/__init__.py", "src/semantic_search/source.py",
                  "src/semantic_search/languages.py", "src/semantic_search/corpus.py")
ALLOWED_SEMANTIC = {"semantic_search", "semantic_search.source", "semantic_search.languages", "semantic_search.corpus"}
FORBIDDEN = ("numpy", "mlx", "mlx_embeddings", "torch", "transformers", "jax", "attr", "attrs")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def lines(text):
    return [line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text) if line]


def file_record(path):
    raw = path.read_bytes()
    return {"bytes": len(raw), "sha256": sha(raw)}


def read(path):
    return json.loads(path.read_bytes())


def relative(value):
    require(type(value) is str and bool(value) and "\\" not in value, "Invalid relative path")
    path = PurePosixPath(value)
    require(not path.is_absolute() and ".." not in path.parts and str(path) == value,
            "Invalid normalized relative path")
    return path


class SourceOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if ((fullname.startswith("semantic_search.") and fullname not in ALLOWED_SEMANTIC)
                or any(fullname == name or fullname.startswith(name + ".") for name in FORBIDDEN)):
            raise RuntimeError("Source-only preflight forbids import: " + fullname)


@contextmanager
def source_only():
    guard = SourceOnly()
    sys.meta_path.insert(0, guard)
    saved = {}

    def deny(*args, **kwargs):
        raise RuntimeError("Source-only preflight forbids network")

    for owner, names in ((socket, ("create_connection", "getaddrinfo")),
                         (socket.socket, ("connect", "connect_ex", "sendto"))):
        for name in names:
            saved[(owner, name)] = getattr(owner, name)
            setattr(owner, name, deny)
    try:
        yield
    finally:
        sys.meta_path.remove(guard)
        for (owner, name), value in saved.items():
            setattr(owner, name, value)


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def definitions(text):
    result, names = [], []

    class Visitor(ast.NodeVisitor):
        def visit_ClassDef(self, node):
            names.append(node.name)
            self.generic_visit(node)
            names.pop()

        def visit_FunctionDef(self, node):
            names.append(node.name)
            result.append((".".join(names), node))
            self.generic_visit(node)
            names.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

    Visitor().visit(ast.parse(text))
    return result


def executable_lines(node, low, high):
    docstrings, found = [], set()
    for child in ast.walk(node):
        if (isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and child.body
                and isinstance(child.body[0], ast.Expr) and isinstance(child.body[0].value, ast.Constant)
                and isinstance(child.body[0].value.value, str)):
            docstrings.append((child.body[0].lineno, child.body[0].end_lineno))
    for child in ast.walk(node):
        if (child is node or not isinstance(child, ast.stmt)
                or (isinstance(child, ast.Expr) and isinstance(child.value, ast.Constant)
                    and isinstance(child.value.value, str))):
            continue
        end = (max(child.lineno, child.body[0].lineno - 1)
               if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else child.end_lineno)
        found.update(number for number in range(max(low, child.lineno), min(high, end) + 1)
                     if not any(start <= number <= finish for start, finish in docstrings))
    return sorted(found)


def target_facts(target, sources, records, windows, keys, source_files):
    path, symbol = target["path"], target["symbol"]
    require(path in sources and type(symbol) is str and bool(symbol), "Target outside complete source inventory")
    interval = target["evidence_lines"]
    require(isinstance(interval, list) and len(interval) == 2
            and all(type(number) is int for number in interval), "Evidence interval must contain two integers")
    low, high = interval
    physical = lines(sources[path])
    require(1 <= low <= high <= len(physical), "Evidence interval outside source")
    key = target["canonical_definition_key"]
    require(isinstance(key, list) and len(key) == 4 and key[:2] == [path, symbol]
            and all(type(number) is int for number in key[2:]), "Unknown canonical definition identity")
    matched = [node for name, node in records[path] if name == symbol
               and [min([node.lineno, *(item.lineno for item in node.decorator_list)]), node.end_lineno] == key[2:]]
    require(len(matched) == 1, "Target canonical span does not uniquely identify its qualified definition")
    node = matched[0]
    require(key[2] <= low <= high <= key[3], "Evidence is outside canonical definition")
    offsets = [0]
    for line in physical:
        offsets.append(offsets[-1] + len(line.encode()))
    raw = sources[path].encode()
    start, end = offsets[node.lineno - 1] + node.col_offset, offsets[node.end_lineno - 1] + node.end_col_offset
    eligible = [item.id for item in windows if tuple(keys[item.id]) == tuple(key)
                and item.start_line <= low <= high <= item.end_line]
    require(bool(eligible), "No one original window contains the complete qualified evidence interval")
    executable = executable_lines(node, low, high)
    require(bool(executable), "Evidence contains no executable implementation statement")
    expected = {"path": path, "symbol": symbol, "evidence_lines": [low, high],
        "source_file_sha256": source_files[path]["sha256"],
        "evidence_sha256": sha("".join(physical[low - 1:high]).encode()),
        "evidence_byte_bounds": [offsets[low - 1], offsets[high]], "definition_lines": key[2:],
        "canonical_definition_key": key, "ast_definition_byte_bounds": [start, end],
        "ast_definition_sha256": sha(raw[start:end]),
        "definition_source_byte_bounds": [offsets[key[2] - 1], offsets[key[3]]],
        "definition_source_sha256": sha(raw[offsets[key[2] - 1]:offsets[key[3]]]),
        "executable_evidence_lines": executable, "eligible_window_ids": eligible}
    require(target == expected, "Target source/hash/AST/evidence/window metadata differs")
    return expected


def absence_facts(checks, queries, sources):
    absent = {query["id"] for query in queries if query["kind"] == "absent"}
    require(checks.get("schema") == "python_source_absence_checks_v1"
            and set(checks.get("queries", {})) == absent, "Every scoped absence needs its exact source-review checks")
    result = {}
    allowed_nodes = {"AsyncFunctionDef", "Await", "AsyncFor", "AsyncWith"}
    trees = {path: ast.parse(text) for path, text in sources.items()}
    for query_id, check in checks["queries"].items():
        patterns, forbidden_nodes = check["literal_patterns"], check["forbidden_ast_nodes"]
        require(isinstance(patterns, list) and patterns and all(type(p) is str and p for p in patterns),
                "Absence literal checks must be explicit nonempty patterns")
        require(isinstance(forbidden_nodes, list) and set(forbidden_nodes) <= allowed_nodes,
                "Unsupported absence AST predicate")
        require(set(check["reviewed_paths"]) == set(sources), "Scoped absence must review every included source file")
        matches = {pattern: [path for path, text in sources.items() if re.search(pattern, text, re.IGNORECASE)]
                   for pattern in patterns}
        nodes = {name: [path for path, tree in trees.items()
                        if any(type(node).__name__ == name for node in ast.walk(tree))] for name in forbidden_nodes}
        require(not any(matches.values()) and not any(nodes.values()), "Authored absence search or AST check has matches")
        result[query_id] = {"literal_matches": matches, "forbidden_ast_node_matches": nodes,
                            "reviewed_source_files": len(sources), "semantic_review_required": True}
    return result


def audit(data, repo, absence_checks):
    from semantic_search.corpus import extract_snippets
    from semantic_search.languages import EXTRACTORS, decode_source, extractor_fingerprint

    require(git(repo, "rev-parse", "HEAD") == data["revision"] and not git(repo, "status", "--porcelain=v1"),
            "Pinned source checkout must be clean at its recorded commit")
    require(git(repo, "rev-parse", data["release"]) == data["annotated_tag_object"]
            and git(repo, "rev-parse", data["release"] + "^{}") == data["revision"], "Annotated release/tag pin differs")
    source_root = relative(data["source_root"])
    exclusions = data["source_exclusions"]
    require(isinstance(exclusions, list) and all(type(p) is str and p and not PurePosixPath(p).is_absolute()
            and ".." not in PurePosixPath(p).parts for p in exclusions), "Invalid source exclusions")
    tracked = [path for path in git(repo, "ls-files", "-z", "--", str(source_root)).split("\0") if path]
    supported = {path for path in tracked if Path(path).suffix in EXTRACTORS}
    require(supported and all(Path(path).suffix == ".py" for path in supported),
            "This generalized source-only profile accepts Python implementation roots")
    excluded = {path for path in supported if any(fnmatch.fnmatchcase(path, pattern) for pattern in exclusions)}
    included = supported - excluded
    inventory = {path: file_record(repo / path) for path in sorted(included)}
    excluded_inventory = {path: file_record(repo / path) for path in sorted(excluded)}
    unsupported = {path: file_record(repo / path) for path in tracked if path not in supported}
    require(inventory == data["source_files"] and excluded_inventory == data["excluded_supported_files"]
            and unsupported == data["unsupported_source_root_files"], "Complete source/exclusion/unsupported membership differs")
    require(sha(canonical(inventory)) == data["source_inventory_sha256"]
            and sha(canonical(excluded_inventory)) == data["excluded_supported_inventory_sha256"], "Membership inventory digest differs")
    sources = {path: decode_source(path, (repo / path).read_bytes()) for path in inventory}
    require(all(text.encode() == (repo / path).read_bytes() for path, text in sources.items()),
            "Byte-bound profile requires literal UTF-8 Python source")
    records = {path: definitions(text) for path, text in sources.items()}
    keys = {}
    windows = extract_snippets(repo, str(source_root), 160, 40, sources=sources,
                               source_exclusions=exclusions, definition_keys=keys)
    window_inventory = []
    for item in windows:
        require(item.text == "".join(lines(sources[item.path])[item.start_line - 1:item.end_line])
                and sha(item.text.encode()) == item.content_hash, "Extractor window is not its literal physical source")
        require(item.id in keys, "Extractor omitted a canonical window identity")
        window_inventory.append({name: getattr(item, name) for name in
                                 ("id", "path", "symbol", "start_line", "end_line", "content_hash")})
    require(len({item.id for item in windows}) == len(windows) == len(keys), "Window identity/key inventory is incomplete or duplicated")
    queries = data["queries"]
    require(queries and len({query["id"] for query in queries}) == len(queries), "Query identities must be unique")
    counts = {"queries": len(queries), "present": 0, "multi_file_present": 0, "single_file_present": 0,
        "scoped_absent": 0, "required_positive_evidence_groups": 0, "target_rows": 0,
        "source_files": len(included), "excluded_supported_files": len(excluded),
        "unsupported_source_root_files": len(unsupported), "source_windows_160_40": len(windows),
        "extracted_definitions": len(set(keys.values())),
        "source_files_with_extracted_implementations": len({item.path for item in windows}),
        "canonical_definitions_with_multiple_windows": sum(number > 1 for number in Counter(keys.values()).values())}
    targets, referenced_tests = [], set()
    for query in queries:
        require(type(query["id"]) is str and query["id"] and type(query["query"]) is str and query["query"].strip()
                and type(query["multi_file"]) is bool, "Invalid query identity/text/multifile flag")
        groups = query["target_groups"]
        require(isinstance(groups, list), "Query groups must be an AND list")
        if query["kind"] == "absent":
            require(not groups and query["scope"] == str(source_root) and not query["multi_file"], "Invalid scoped absence")
            counts["scoped_absent"] += 1
        else:
            require(query["kind"] == "present" and groups, "Positive query needs evidence groups")
            counts["present"] += 1
            paths = set()
            for number, group in enumerate(groups, 1):
                require(isinstance(group, list) and bool(group), "An evidence OR group cannot be empty")
                require(len({(target["path"], tuple(target["evidence_lines"]), target["evidence_sha256"])
                             for target in group}) == 1, "OR alternatives must prove the same whole physical source interval")
                require(len({tuple(target["canonical_definition_key"]) for target in group}) == len(group), "Duplicate canonical OR alternative")
                paths.add(group[0]["path"])
                for target in group:
                    facts = target_facts(target, sources, records, windows, keys, inventory)
                    targets.append({"query_id": query["id"], "group": number, **facts})
                    counts["target_rows"] += 1
            require(query["multi_file"] == (len(paths) > 1), "Multifile label does not match necessary evidence paths")
            counts["multi_file_present" if len(paths) > 1 else "single_file_present"] += 1
            counts["required_positive_evidence_groups"] += len(groups)
        referenced_tests.update(query["upstream_tests_read"])
    require(counts == data["counts"], "Authored counts differ from complete source/evidence inventories")
    test_paths = {reference.split("::", 1)[0] for reference in referenced_tests}
    require(set(data["upstream_test_files_read"]) == test_paths
            and set(data["upstream_test_definitions_read"]) == referenced_tests, "Complete referenced upstream test inventory differs")
    tests = {}
    for path in test_paths:
        relative(path)
        require(file_record(repo / path) == data["upstream_test_files_read"][path], "Upstream corroborating test file changed")
        text = (repo / path).read_text()
        for reference in sorted(item for item in referenced_tests if item.startswith(path + "::")):
            symbol = reference.split("::", 1)[1]
            matched = [node for name, node in definitions(text) if name == symbol]
            require(len(matched) == 1, "Upstream qualified test does not uniquely exist")
            node = matched[0]
            expected = {"definition_lines": [min([node.lineno, *(item.lineno for item in node.decorator_list)]), node.end_lineno],
                "body_lines": [node.lineno, node.end_lineno],
                "body_source_sha256": sha("".join(lines(text)[node.lineno - 1:node.end_lineno]).encode()),
                "full_body_read": True, "executed": False}
            require(expected == data["upstream_test_definitions_read"][reference], "Upstream test source/reading declaration differs")
            tests[reference] = expected
    license_data = data["license"]
    relative(license_data["path"])
    relative(license_data["local_copy"])
    license_bytes = (repo / license_data["path"]).read_bytes()
    require(license_bytes == (ROOT / license_data["local_copy"]).read_bytes()
            and sha(license_bytes) == license_data["sha256"], "Complete pinned license copy differs")
    return {"counts": counts, "targets": targets, "upstream_tests": tests,
        "source_files": inventory, "excluded_supported_files": excluded_inventory,
        "unsupported_source_root_files": unsupported, "window_inventory": window_inventory,
        "definition_keys": {name: list(key) for name, key in keys.items()},
        "window_inventory_sha256": sha(canonical(window_inventory)), "definition_keys_sha256": sha(canonical(keys)),
        "absence_source_checks": absence_facts(absence_checks, queries, sources),
        "extractor_fingerprint": extractor_fingerprint(), "license_sha256": sha(license_bytes)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "corpus", "protocol", "absence-checks", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "Use a new source-review receipt; never overwrite an earlier validation")
    with source_only():
        data = read(args.dataset)
        require(data["source_review_protocol"] == args.protocol.resolve().relative_to(ROOT).as_posix(), "Bound source-review protocol path differs")
        findings = audit(data, args.corpus.resolve(), read(args.absence_checks))
    report = {"version": 1, "classification": "Source-only Python evidence/membership preflight; no retrieval or policy outcome",
        "status": "passed", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_sha256": sha(args.dataset.read_bytes()), "protocol_sha256": sha(args.protocol.read_bytes()),
        "absence_checks_sha256": sha(args.absence_checks.read_bytes()),
        "validator_sha256": sha(Path(__file__).read_bytes()),
        "shared_source_extractor_sha256": {name: sha((ROOT / name).read_bytes()) for name in SOURCE_MODULES},
        "producer_git_head": git(ROOT, "rev-parse", "HEAD"),
        "source_only_precommit_receipt": True, "calls": {"model": 0, "index": 0, "retrieval": 0, "rule_activation": 0, "network": 0},
        "source_repository_code_executed": False, "semantic_peer_review_required": True,
        "trust_boundary": "Existing saved-source extractor/decoder shared. Independent AST identity, literal bounds/bytes/hash, whole-window evidence, membership, tests/license and absence predicates checked. Semantic completeness/necessity and scoped absence require a separate source review.",
        **findings}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    print(json.dumps({"status": "passed", "receipt_sha256": sha(args.output.read_bytes()), "counts": findings["counts"]}))


if __name__ == "__main__":
    main()
