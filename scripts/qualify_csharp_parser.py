#!/usr/bin/env python3
"""Isolated Mac grammar smoke check; never imports the production search engine."""
from __future__ import annotations

import argparse
import hashlib
import importlib.abc
import importlib.metadata
import json
import platform
import re
import socket
import subprocess
import sys
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("scripts/qualify_csharp_parser.py", "tests/test_csharp_parser_preflight.py",
           "docs/csharp-source-plan.md")
PINS = {
    "tree-sitter": {"version": "0.26.0",
        "filename": "tree_sitter-0.26.0-cp312-cp312-macosx_11_0_arm64.whl",
        "sha256": "918d89529786873f0982a0f59c2a303cd065fbfd1b903d71a8e4e1584f67b42e"},
    "tree-sitter-c-sharp": {"version": "0.23.5",
        "filename": "tree_sitter_c_sharp-0.23.5-cp310-abi3-macosx_11_0_arm64.whl",
        "sha256": "a75994a11f6fed3f5b8c36ad6a00e5dc43205bd912c43af3a2a54fdf649664eb"}}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as output:
        json.dump(value, output, indent=2, ensure_ascii=False)
        output.write("\n")


def read_json(path):
    return json.loads(Path(path).read_text())


def source_hashes():
    return {name: sha((ROOT / name).read_bytes()) for name in SOURCES}


def fixtures():
    def case(name, source, members=(), invalid=False):
        return {"name": name, "source": source, "raw_sha256": sha(source.encode("utf-8")),
                "expected_members": list(members), "expect_invalid": invalid}
    return [
        case("block", "namespace Demo { public class C { public int Add(int value) { return value + 1; } } }",
             ["public int Add(int value) { return value + 1; }"]),
        case("expression", "class C { public int Twice(int value) => value * 2; }",
             ["public int Twice(int value) => value * 2;"]),
        case("constructor_initializer", "class Parent { public Parent(int value) {} }\nclass C : Parent { public C(int value) : base(value) { Value = value; } public int Value; }\n",
             ["public Parent(int value) {}", "public C(int value) : base(value) { Value = value; }"]),
        case("file_scoped_generic_overloads", "namespace Demo.Space;\npublic class Box<T> { public T Echo(T value) => value; public int Echo(int value) => value; }\n",
             ["public T Echo(T value) => value;", "public int Echo(int value) => value;"]),
        case("nested_namespace_types", "namespace Outer { namespace Inner { class A { class B { public int Run() { return 1; } } } } }",
             ["public int Run() { return 1; }"]),
        case("bom_crlf_unicode", '\ufeffnamespace Demo;\r\npublic class C {\r\n public string Text() { return "café😎\u2028line"; }\r\n}\r\n',
             ['public string Text() { return "café😎\u2028line"; }']),
        case("preprocessor_both_branches", "#if FEATURE\nnamespace Demo { class C { int First() => 1; } }\n#else\nnamespace Demo { class C { int Second() => 2; } }\n#endif\n",
             ["int First() => 1;", "int Second() => 2;"]),
        case("invalid_expression", "class Broken { public int Add(int value) { return value + ; } }", invalid=True),
        case("missing_closing_brace", "class Broken { public int Add(int value) { return value; }", invalid=True)]


def select_wheel(metadata, package, pin):
    if metadata["info"]["name"] != package or metadata["info"]["version"] != pin["version"]:
        raise ValueError("Publisher package identity differs")
    matches = [row for row in metadata["urls"] if row["filename"] == pin["filename"]]
    if len(matches) != 1:
        raise ValueError("Expected exactly one pinned wheel")
    row = matches[0]
    url = urlparse(row["url"])
    if (row["digests"]["sha256"] != pin["sha256"] or row["yanked"]
            or row["packagetype"] != "bdist_wheel" or url.scheme != "https"
            or url.netloc != "files.pythonhosted.org" or url.username or url.password
            or type(row["size"]) is not int or not 0 < row["size"] <= 4 * 1024 * 1024
            or Path(url.path).name != pin["filename"]):
        raise ValueError("Publisher wheel pin or origin differs")
    return row


def main_metadata():
    """Read metadata/config witnesses, without claiming a full environment byte audit."""
    paths = [ROOT / ".venv/pyvenv.cfg", ROOT / "pyproject.toml"]
    site = ROOT / ".venv/lib/python3.12/site-packages"
    for folder in sorted(site.glob("*.dist-info")):
        paths.extend(folder / name for name in ("METADATA", "RECORD", "direct_url.json")
                     if (folder / name).is_file())
    return {str(path.relative_to(ROOT)): sha(path.read_bytes()) for path in paths}


def fetch(url, maximum):
    with urlopen(url, timeout=20) as response:
        data = response.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("Download exceeds bounded size")
    return data


def freeze(work, output, base_python):
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("This wheel qualification is Mac arm64 only")
    if sys.version_info[:2] != (3, 12):
        raise ValueError("Qualification authoring requires Python 3.12")
    work.mkdir(exist_ok=False)
    output.mkdir(exist_ok=False)
    try:
        base = Path(base_python).resolve(strict=True)
        identity = subprocess.check_output([str(base), "-I", "-c",
            "import sys,json; print(json.dumps({'version':list(sys.version_info[:3]),'prefix':sys.prefix,'base_prefix':sys.base_prefix}))"],
            text=True, timeout=20)
        base_info = json.loads(identity)
        if base_info["version"][:2] != [3, 12] or base_info["prefix"] != base_info["base_prefix"]:
            raise ValueError("Must create isolated environment from actual base Python 3.12")
        downloads = work / "wheels"
        downloads.mkdir()
        rows = {}
        for package, pin in PINS.items():
            url = f"https://pypi.org/pypi/{package}/{pin['version']}/json"
            raw = fetch(url, 2 * 1024 * 1024)
            metadata_path = output / f"publisher-{package}.json"
            metadata_path.write_bytes(raw)
            wheel = select_wheel(json.loads(raw), package, pin)
            data = fetch(wheel["url"], 4 * 1024 * 1024)
            if sha(data) != pin["sha256"] or len(data) != wheel["size"]:
                raise ValueError("Actual wheel bytes differ before installation")
            (downloads / pin["filename"]).write_bytes(data)
            rows[package] = {**pin, "url": wheel["url"], "bytes": len(data),
                "publisher_metadata_path": str(metadata_path.relative_to(ROOT)),
                "publisher_metadata_sha256": sha(raw), "metadata_url": url}
        frozen = {"created_utc": datetime.now(timezone.utc).isoformat(), "schema_version": 1,
            "scope": "Isolated CPU grammar smoke only; no production extractor, compiled C# execution, retrieval or inference.",
            "work": str(work.relative_to(ROOT)), "output": str(output.relative_to(ROOT)),
            "base_python": str(base), "base_python_sha256": sha(base.read_bytes()),
            "base_python_identity": base_info, "source_sha256": source_hashes(),
            "main_environment_metadata_before": main_metadata(), "wheels": rows,
            "fixtures": fixtures(), "expected_abi": 15,
            "expected_runtime_packages": {name: pin["version"] for name, pin in PINS.items()},
            "attempt_policy": "Exclusive single attempt; failures preserve partial artifacts and prohibit automatic retry.",
            "parser_results_observed": False, "model_calls": 0, "retrieval_calls": 0}
        write_json(output / "frozen-plan.json", frozen)
        return frozen
    except BaseException as error:
        write_json(output / "freeze-failure.json", {"type": type(error).__name__, "error": str(error)})
        raise


def physical_span(raw, start, end):
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(raw):
        raise ValueError("Invalid source byte span")
    offsets = [0, *(match.end() for match in re.finditer(rb"\r\n|\r|\n", raw))]
    return [bisect_right(offsets, start), bisect_right(offsets, end - 1)]


def probe_case(parser, case):
    raw = case["source"].encode("utf-8")
    if sha(raw) != case["raw_sha256"]:
        raise ValueError("Frozen fixture bytes differ")
    tree = parser.parse(raw)
    root = tree.root_node
    pending, errors, missing, members, node_counts = [root], [], [], [], {}
    while pending:
        node = pending.pop()
        pending.extend(reversed(node.children))
        node_counts[node.type] = node_counts.get(node.type, 0) + 1
        if not 0 <= node.start_byte <= node.end_byte <= len(raw):
            raise ValueError("Parser node escapes original bytes")
        if node.is_error:
            errors.append({"type": node.type, "start_byte": node.start_byte, "end_byte": node.end_byte})
        if node.is_missing:
            missing.append({"type": node.type, "start_byte": node.start_byte, "end_byte": node.end_byte})
        if node.type in {"method_declaration", "constructor_declaration"}:
            text = raw[node.start_byte:node.end_byte].decode("utf-8")
            members.append({"type": node.type, "source": text, "raw_sha256": sha(text.encode("utf-8")),
                "start_byte": node.start_byte, "end_byte": node.end_byte,
                "tree_start_point": list(node.start_point), "tree_end_point": list(node.end_point),
                "physical_lines": physical_span(raw, node.start_byte, node.end_byte)})
    invalid = bool(root.has_error or errors or missing)
    checks = {"source_bytes_unchanged": sha(raw) == case["raw_sha256"],
              "root_consumes_all_bytes": root.end_byte == len(raw),
              "expected_validity": invalid == case["expect_invalid"],
              "exact_member_spans": case["expect_invalid"] or
                  sorted(row["source"] for row in members) == sorted(case["expected_members"])}
    return {"name": case["name"], "input_bytes": len(raw), "input_sha256": sha(raw),
            "root_has_error": bool(root.has_error), "errors": errors, "missing": missing,
            "node_counts": node_counts, "members": members, "checks": checks,
            "checks_passed": all(checks.values())}


def probe(frozen, env):
    class NoModels(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split(".")[0] in {"mlx", "mlx_lm", "mlx_vlm", "torch", "transformers", "semantic_search"}:
                raise RuntimeError("Models and production helpers are blocked")
    sys.meta_path.insert(0, NoModels())
    def blocked(*args, **kwargs):
        raise RuntimeError("Network/child execution is blocked during parser probe")
    socket.socket.connect = socket.socket.connect_ex = socket.socket.sendto = blocked
    subprocess.Popen = blocked
    if (Path(sys.prefix).resolve() != env.resolve() or sys.prefix == sys.base_prefix
            or list(sys.version_info[:3]) != frozen["base_python_identity"]["version"]):
        raise ValueError("Parser probe must use the fresh isolated environment")
    installed = {name: importlib.metadata.version(name) for name in PINS}
    if installed != frozen["expected_runtime_packages"]:
        raise ValueError("Actual installed parser pins differ")
    import tree_sitter
    import tree_sitter_c_sharp
    origins = {}
    loaded = {name: module for name, module in sys.modules.items()
              if name.split(".")[0] in {"tree_sitter", "tree_sitter_c_sharp"}}
    if not {"tree_sitter._binding", "tree_sitter_c_sharp._binding"}.issubset(loaded):
        raise ValueError("Actual native grammar/binding module origins are missing")
    for name, module in sorted(loaded.items()):
        path = Path(module.__file__).resolve(strict=True)
        if not path.is_relative_to(env.resolve()):
            raise ValueError("Parser loaded outside isolated environment")
        origins[name] = {"path": str(path.relative_to(env)), "sha256": sha(path.read_bytes())}
    language = tree_sitter.Language(tree_sitter_c_sharp.language())
    parser = tree_sitter.Parser(language)
    cases = [probe_case(parser, case) for case in frozen["fixtures"]]
    return {"schema_version": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
        "python_version": list(sys.version_info[:3]), "isolated_prefix": str(env.relative_to(ROOT)),
        "packages": installed, "module_origins": origins,
        "grammar_abi": language.abi_version,
        "binding_abi_range": [tree_sitter.MIN_COMPATIBLE_LANGUAGE_VERSION, tree_sitter.LANGUAGE_VERSION],
        "cases": cases, "checks_passed": language.abi_version == frozen["expected_abi"]
            and all(row["checks_passed"] for row in cases),
        "model_calls": 0, "retrieval_calls": 0, "csharp_program_executions": 0,
        "limits": "Grammar smoke only. No production extraction/window eligibility, compiler semantics, build symbols, dependency resolution, editor or retrieval quality qualification."}


def run(output):
    plan = output / "frozen-plan.json"
    frozen = read_json(plan)
    work = ROOT / frozen["work"]
    env = work / "env"
    write_json(output / "run-start.json", {"created_utc": datetime.now(timezone.utc).isoformat(),
        "frozen_plan_sha256": sha(plan.read_bytes()), "source_sha256": source_hashes()})
    stage, commands = "validate", []
    try:
        if source_hashes() != frozen["source_sha256"] or fixtures() != frozen["fixtures"]:
            raise ValueError("Prospective source or fixtures changed")
        if main_metadata() != frozen["main_environment_metadata_before"]:
            raise ValueError("Main environment metadata changed before run")
        base = Path(frozen["base_python"])
        if sha(base.read_bytes()) != frozen["base_python_sha256"] or env.exists():
            raise ValueError("Base Python changed or isolated environment already exists")
        wheels = []
        for package, row in frozen["wheels"].items():
            metadata = ROOT / row["publisher_metadata_path"]
            if sha(metadata.read_bytes()) != row["publisher_metadata_sha256"]:
                raise ValueError("Frozen publisher metadata changed")
            select_wheel(read_json(metadata), package, PINS[package])
            wheel = work / "wheels" / row["filename"]
            if sha(wheel.read_bytes()) != row["sha256"] or wheel.stat().st_size != row["bytes"]:
                raise ValueError("Wheel bytes changed before install")
            wheels.append(str(wheel))
        def command(name, argv):
            nonlocal stage
            stage = name
            result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
            with (output / f"{name}.log").open("x") as log:
                log.write(result.stdout + result.stderr)
            commands.append({"stage": name, "argv": [arg.replace(str(ROOT), "<PROJECT>") for arg in argv],
                             "returncode": result.returncode})
            if result.returncode:
                raise RuntimeError(f"{name} failed with code {result.returncode}")
        command("create-env", [str(base), "-I", "-m", "venv", str(env)])
        python = str(env / "bin/python")
        command("install-verified-wheels", [python, "-I", "-m", "pip", "--isolated", "install",
            "--no-index", "--no-deps", "--no-cache-dir", "--disable-pip-version-check", *wheels])
        command("parser-probe", [python, "-I", str(ROOT / SOURCES[0]), "--probe",
            "--output", str(output), "--env", str(env)])
        result = read_json(output / "parser-smoke.json")
        after = main_metadata()
        checks = {"probe": result["checks_passed"], "sources_unchanged": source_hashes() == frozen["source_sha256"],
                  "main_environment_metadata_unchanged": after == frozen["main_environment_metadata_before"]}
        write_json(output / "qualification.json", {"checks": checks, "checks_passed": all(checks.values()),
            "commands": commands, "main_environment_metadata_after": after,
            "source_sha256_after": source_hashes(), "parser_result_sha256": sha((output / "parser-smoke.json").read_bytes()),
            "frozen_plan_sha256": sha(plan.read_bytes()), "implemented_in_search_engine": False,
            "scope": "Actual isolated Mac grammar ABI/fixture smoke only; no retrieval/inference/compiler/editor claim.",
            "main_environment_witness_scope": "Before/after pyvenv.cfg, pyproject and all physical dist-info METADATA/RECORD/direct_url files; not every environment byte."})
        if not all(checks.values()):
            raise ValueError("Parser qualification checks failed; retain observations without tuning fixtures")
    except BaseException as error:
        write_json(output / "failure.json", {"stage": stage, "type": type(error).__name__,
            "error": str(error), "commands": commands, "main_environment_metadata_after": main_metadata(),
            "source_sha256_after": source_hashes(), "automatic_retry_permitted": False})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--freeze", action="store_true")
    modes.add_argument("--run", action="store_true")
    modes.add_argument("--probe", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path)
    parser.add_argument("--base-python")
    parser.add_argument("--env", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT):
        raise ValueError("Evidence must be inside the project")
    if args.freeze:
        if args.work is None or args.base_python is None:
            parser.error("--freeze requires --work and --base-python")
        work = args.work.resolve()
        if not work.is_relative_to(ROOT / ".cache"):
            raise ValueError("Fresh isolated work must be inside project .cache")
        freeze(work, output, args.base_python)
    elif args.run:
        run(output)
    else:
        frozen = read_json(output / "frozen-plan.json")
        write_json(output / "parser-smoke.json", probe(frozen, args.env.resolve()))


if __name__ == "__main__":
    main()
