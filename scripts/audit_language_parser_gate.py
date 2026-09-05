#!/usr/bin/env python3
"""CPU-only syntax compatibility gate; never read gold queries or load models."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CORPORA = (
    ("express", ".cache/corpora/express", "lib", (), 6),
    ("zod", ".cache/corpora/zod", "packages/zod/src/v4",
     ("**/tests/**", "**/*.test.ts", "**/*.test.tsx", "**/*.d.ts"), 107),
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def audit(implementation, baseline):
    # PYTHONPATH selects the isolated implementation; this audit contains no
    # dataset/gold input and never instantiates an embedder or search backend.
    from semantic_search.corpus import _source_files
    from semantic_search import javascript
    from semantic_search.languages import decode_source, extractor_fingerprint
    from semantic_search.source import physical_lines

    javascript_path = implementation / "src/semantic_search/javascript.py"
    if Path(javascript.__file__).resolve() != javascript_path.resolve():
        raise ValueError("PYTHONPATH selected a different implementation")
    old_raw = subprocess.check_output(["git", "-C", str(implementation), "show",
                                       baseline + ":src/semantic_search/javascript.py"])
    saved = ROOT / ".cache/language-parser-review/baseline_javascript.py"
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_bytes(old_raw)
    spec = importlib.util.spec_from_file_location("baseline_javascript_gate", saved)
    old = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = old
    spec.loader.exec_module(old)
    datasets = []
    for identifier, relative, scope, exclusions, expected_count in CORPORA:
        repo = ROOT / relative
        paths = _source_files(repo, repo / scope, exclusions)
        if len(paths) != expected_count:
            raise ValueError("Source-only inventory changed")
        old_errors, fixed_errors, files, windows = [], [], {}, 0
        for path in paths:
            name = path.relative_to(repo).as_posix()
            raw = path.read_bytes()
            source = decode_source(name, raw)
            files[name] = sha(raw)
            try:
                old.parse_source(name, source)
            except SyntaxError as error:
                old_errors.append({"path": name, "error": str(error)})
            try:
                snippets = javascript.extract_javascript(name, source, 160, 40)
                lines = physical_lines(source)
                for snippet in snippets:
                    expected = "".join(lines[snippet.start_line - 1:snippet.end_line])
                    if snippet.text != expected or snippet.content_hash != sha(expected.encode()):
                        raise ValueError("Source bytes or offsets changed")
                windows += len(snippets)
            except (SyntaxError, ValueError) as error:
                fixed_errors.append({"path": name, "error": str(error)})
        datasets.append({"id": identifier, "source_root": scope,
                         "source_exclusions": list(exclusions), "source_files": len(paths),
                         "revision": subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip(),
                         "source_file_sha256": files, "baseline_errors": old_errors,
                         "repaired_errors": fixed_errors, "exact_saved_windows": windows})
    return {"classification": "CPU source-only parser compatibility and exact byte/offset check",
            "checks_passed": all(not row["repaired_errors"] for row in datasets),
            "models_loaded": 0, "retrieval_queries": 0, "gold_files_read": 0,
            "baseline_commit": baseline, "baseline_javascript_sha256": sha(old_raw),
            "implementation_commit": subprocess.check_output(["git", "-C", str(implementation), "rev-parse", "HEAD"], text=True).strip(),
            "implementation_sha256": {name: sha((implementation / name).read_bytes()) for name in
                                      ("src/semantic_search/javascript.py", "src/semantic_search/languages.py",
                                       "tests/test_typescript_variance.py")},
            "extractor": extractor_fingerprint(), "datasets": datasets,
            "limitations": ["Parser acceptance and source bytes do not establish runtime semantic correctness.",
                            "Gold eligibility and real retrieval remain separate gates."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--implementation", type=Path, required=True)
    parser.add_argument("--baseline", default="21538ad")
    parser.add_argument("--output", type=Path, default=ROOT / "results/language-extractor-preflight-2026-10-13/variance-repair-review.json")
    args = parser.parse_args()
    result = audit(args.implementation.resolve(), args.baseline)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"checks_passed": result["checks_passed"], "datasets": [
        {key: row[key] for key in ("id", "source_files", "baseline_errors", "repaired_errors", "exact_saved_windows")}
        for row in result["datasets"]]}, indent=2))
    return 0 if result["checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
