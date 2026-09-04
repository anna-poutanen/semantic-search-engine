"""Withheld CPU behavioral checks against an explicitly selected checkout."""

import argparse
import json
from pathlib import Path
import shutil
import sys
import unittest


def checkout(package, source_root):
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    args = parser.parse_args()
    root = args.repo.resolve(strict=True)
    source = (root / source_root).resolve(strict=True)
    sys.dont_write_bytecode = True
    for cache in source.rglob("__pycache__"):
        shutil.rmtree(cache)
    sys.path.insert(0, str(source))
    module = __import__(package)
    if not Path(module.__file__).resolve().is_relative_to(source):
        raise RuntimeError("Check imported a package outside the evaluated checkout")
    return root


def run_tests():
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules["__main__"])
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(suite)
    print(
        json.dumps(
            {
                "tests_run": result.testsRun,
                "failures": len(result.failures),
                "errors": len(result.errors),
                "skipped": len(result.skipped),
                "passed": result.wasSuccessful(),
            },
            sort_keys=True,
        )
    )
    raise SystemExit(0 if result.wasSuccessful() else 1)
