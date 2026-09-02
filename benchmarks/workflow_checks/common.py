"""Load the evaluated checkout, never the project's installed package.

These behavioral checks live outside the agent's checkout and are run only by
the evaluator after the agent exits. No model feedback contains their source.
"""

from pathlib import Path
import sys
import argparse
import json
import shutil
import unittest


def checkout(package: str, source_root: str) -> Path:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    args = parser.parse_args()
    root = args.repo.resolve(strict=True)
    source = (root / source_root).resolve(strict=True)
    # Same-size fixes written in the same second must not reuse the seed's pyc.
    sys.dont_write_bytecode = True
    for cache in source.rglob("__pycache__"):
        shutil.rmtree(cache)
    sys.path.insert(0, str(source))
    module = __import__(package)
    origin = Path(module.__file__).resolve()
    if not origin.is_relative_to(source):
        raise RuntimeError(f"Imported {package} outside evaluated checkout: {origin}")
    return root


def run_tests() -> None:
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules["__main__"])
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(suite)
    print(json.dumps({
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "passed": result.wasSuccessful(),
    }, sort_keys=True))
    raise SystemExit(0 if result.wasSuccessful() else 1)
