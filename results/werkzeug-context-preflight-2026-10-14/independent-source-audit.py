"""Independent stdlib-only source checks; imports neither Werkzeug nor extractor."""

import ast
import hashlib
import io
import json
import re
import subprocess
import sys
import tokenize
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path.cwd()
BASE = Path("results/werkzeug-context-preflight-2026-10-14")
REPO = ROOT / ".cache/corpora/werkzeug-context-holdout"
DATA = Path("benchmarks/werkzeug_context_queries.json")
BIND = [DATA, *[BASE / n for n in (
    "source-freeze.json", "window-inventory.json", "eligibility.json",
    "protocol.md", "authoring-receipt.json", "WERKZEUG_LICENSE.txt",
)]]


def digest(value):
    return hashlib.sha256(value).hexdigest()


def bindings():
    return {p.as_posix(): digest((ROOT / p).read_bytes()) for p in BIND}


def git(*args):
    return subprocess.check_output(["git", "-C", str(REPO), *args], timeout=30)


def docstring(node):
    return (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str))


def placeholder(node):
    if isinstance(node, ast.Pass):
        return True
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
        return node.value.value is Ellipsis
    if isinstance(node, ast.Raise):
        target = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
        return (isinstance(target, ast.Name) and target.id == "NotImplementedError"
                or isinstance(target, ast.Attribute) and target.attr == "NotImplementedError")
    return False


def definitions(tree):
    out = []

    def descend(node, names):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names = (*names, node.name)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body[1:] if node.body and docstring(node.body[0]) else node.body
            overload = any(isinstance(d, ast.Name) and d.id == "overload"
                           or isinstance(d, ast.Attribute) and d.attr == "overload"
                           for d in node.decorator_list)
            if not overload and not all(placeholder(s) for s in body):
                out.append((".".join(names), node))
        for child in ast.iter_child_nodes(node):
            descend(child, names)

    descend(tree, ())
    return out


def active_lines(function):
    nodes = list(ast.walk(function))
    excluded = {line for n in nodes if docstring(n)
                for line in range(n.lineno, n.end_lineno + 1)}
    result = set()
    for n in nodes:
        if n is function or not isinstance(n, ast.stmt) or docstring(n):
            continue
        end = n.end_lineno
        if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            end = max(n.lineno, n.body[0].lineno - 1)
        current = set(range(n.lineno, end + 1))
        if hasattr(n, "body") or isinstance(n, ast.Match):
            current -= excluded
        result |= current
    return result


before = bindings()
freeze = json.loads((ROOT / BASE / "source-freeze.json").read_text())
dataset = json.loads((ROOT / DATA).read_text())
inventory = json.loads((ROOT / BASE / "window-inventory.json").read_text())
eligibility = json.loads((ROOT / BASE / "eligibility.json").read_text())
author = json.loads((ROOT / BASE / "authoring-receipt.json").read_text())
assert git("status", "--porcelain=v1", "--untracked-files=all") == b""
assert git("rev-parse", "HEAD").decode().strip() == freeze["revision"] == dataset["revision"]
assert freeze["revision"] == "6389612fd1ee1bd93579eed5026e8fd471d04abd"
assert git("rev-parse", "HEAD^{tree}").decode().strip() == freeze["git_tree"]
assert git("rev-parse", "3.1.3^{tag}").decode().strip() == freeze["annotated_tag_object"]
assert digest(git("cat-file", "-p", freeze["annotated_tag_object"])) == freeze["annotated_tag_raw_sha256"]
assert dataset["source_freeze_sha256"] == before[(BASE / "source-freeze.json").as_posix()]
tracked = sorted(x.decode() for x in git("ls-files", "-z", "--", "src/werkzeug").split(b"\0") if x)
assert tracked == freeze["all_tracked_scope_files"]
python_paths = [p for p in tracked if p.endswith(".py")]
assert python_paths == sorted(freeze["tracked_python_source_files"])
assert len(python_paths) == freeze["source_file_count"] == 52
assert dataset["source_exclusions"] == freeze["source_exclusions"] == [
    "**/*.js", "**/*.jsx", "**/*.mjs", "**/*.cjs", "**/*.ts", "**/*.tsx", "**/*.mts", "**/*.cts"]
assert dataset["source_root"] == freeze["source_root"] == "src/werkzeug"
for name, expected in freeze["implementation_sha256"].items():
    assert digest((ROOT / name).read_bytes()) == expected
license_bytes = (REPO / "LICENSE.txt").read_bytes()
assert license_bytes == git("show", freeze["revision"] + ":LICENSE.txt")
assert license_bytes == (ROOT / BASE / "WERKZEUG_LICENSE.txt").read_bytes()
assert digest(license_bytes) == freeze["license"]["sha256"]
sources, defs, rebuilt = {}, {}, []
for name in python_paths:
    raw = (REPO / name).read_bytes()
    assert digest(raw) == freeze["tracked_python_source_files"][name]
    assert raw == git("show", freeze["revision"] + ":" + name)
    encoding = tokenize.detect_encoding(io.BytesIO(raw).readline)[0]
    text = raw.decode(encoding)
    lines = re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text)
    lines = [line for line in lines if line]
    sources[name] = lines
    defs[name] = definitions(ast.parse(text, filename=name))
    for symbol, node in defs[name]:
        start = min([node.lineno, *[d.lineno for d in node.decorator_list]])
        active = active_lines(node)
        while start <= node.end_lineno:
            end = min(start + 159, node.end_lineno)
            if active & set(range(start, end + 1)):
                rebuilt.append({"id": f"{name}:{symbol}:{start}-{end}", "path": name,
                                "symbol": symbol, "start_line": start, "end_line": end,
                                "source_hash": digest("".join(lines[start - 1:end]).encode())})
            if end == node.end_lineno:
                break
            start += 120
assert rebuilt == inventory and len(rebuilt) == freeze["window_count"] == 1025
canonical = json.dumps(rebuilt, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
assert digest(canonical) == freeze["window_inventory_sha256"]
assert len({w["id"] for w in inventory}) == 1025
proofs, ids = [], set()
for query in dataset["queries"]:
    assert query["id"] not in ids and query["split"] == "repository_holdout"
    ids.add(query["id"])
    if query["kind"] == "absent":
        assert query["targets"] == [] and "target_groups" not in query
        continue
    assert query["kind"] == "present"
    groups, paths = [], set()
    for group in query["target_groups"]:
        assert len(group) == 1
        target = group[0]
        name, symbol = target["path"], target["symbol"]
        matches = [n for s, n in defs[name] if s == symbol]
        assert len(matches) == 1
        node = matches[0]
        assert target["definition_lines"] == target["evidence_lines"] == [node.lineno, node.end_lineno]
        literal = "".join(sources[name][node.lineno - 1:node.end_lineno]).encode()
        assert digest(literal) == target["evidence_sha256"]
        assert target["source_file_sha256"] == freeze["tracked_python_source_files"][name]
        assert sources[name][node.lineno - 1].strip() == target["declaration_anchor"]
        windows = [w["id"] for w in inventory if w["path"] == name and w["symbol"] == symbol
                   and w["start_line"] <= node.lineno <= node.end_lineno <= w["end_line"]]
        assert windows
        assert name not in query["query"] and symbol not in query["query"]
        groups.append({**target, "eligible_window_ids": windows})
        paths.add(name)
    proofs.append({"id": query["id"], "required_groups": len(groups),
                   "multi_file": len(paths) > 1, "groups": groups})
assert (len(ids), len(proofs), len(ids) - len(proofs), sum(p["required_groups"] for p in proofs),
        sum(p["multi_file"] for p in proofs)) == (15, 12, 3, 34, 8)
assert eligibility["checks_passed"] is True
for receipt in (freeze, eligibility, author):
    for counter in ("model_calls", "embedding_calls", "retrieval_calls"):
        assert type(receipt[counter]) is int and receipt[counter] == 0
    assert receipt["werkzeug_runtime_imported"] is False
for name, expected in eligibility["artifact_sha256"].items():
    assert digest((ROOT / name).read_bytes()) == expected
for name, expected in author["source_sha256"].items():
    assert digest((ROOT / name).read_bytes()) == expected
assert not any(n.split(".")[0] in {"werkzeug", "semantic_search", "mlx", "mlx_lm", "mlx_vlm", "numpy"}
               for n in sys.modules)
after = bindings()
assert before == after
receipt = {"version": 1, "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
           "checks_passed": True, "classification": "Independent stdlib-only source/AST proof; semantic review supplied separately",
           "artifact_sha256": before, "artifact_sha256_after": after,
           "auditor_sha256": digest(Path(__file__).read_bytes()),
           "revision": freeze["revision"], "git_tree": freeze["git_tree"],
           "annotated_tag_object": freeze["annotated_tag_object"], "git_clean": True,
           "source_file_count": 52, "window_count": 1025, "query_count": 15,
           "positive_queries": 12, "scoped_absent_queries": 3, "required_groups": 34,
           "multi_file_positive_queries": 8, "or_alternative_groups": 0,
           "model_calls": 0, "embedding_calls": 0, "index_calls": 0, "retrieval_calls": 0,
           "no_prior_outcomes": True, "werkzeug_runtime_imported": False,
           "producer_helpers_imported": False, "all_source_blobs_match_git": True,
           "all_windows_independently_reconstructed": True,
           "all_targets_complete_nonplaceholder_ast_definitions": True,
           "query_proofs": proofs,
           "interpretation": "The definition begins at def; eligible windows additionally include decorators. All groups require their complete literal spans, not a name, docstring or partial body."}
with (ROOT / BASE / "independent-source-proof.json").open("x") as out:
    out.write(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
print(json.dumps({k: receipt[k] for k in ("checks_passed", "source_file_count", "window_count", "query_count",
                                        "positive_queries", "scoped_absent_queries", "required_groups", "multi_file_positive_queries")}))
