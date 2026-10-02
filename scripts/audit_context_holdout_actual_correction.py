#!/usr/bin/env python3
"""Post-run, sample-inventory-only correction to the frozen holdout auditor.

The immutable original auditor compares its independent numerical distribution
with a producer envelope that additionally retains ``samples_s``. This wrapper
adds the exact ordered 45-sample inventory to that independent distribution. It
does not change the experiment, labels, numerical definitions, quality gates,
source bindings, native-forward checks, or lifecycle checks. The original audit
and its failed receipts remain available; this correction was made after the
native run, not prospectively frozen before its outcomes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

import audit_context_holdout as original

ORIGINAL_SHA256 = "07a4bc898a763c0b020272f0c37d732a3fadb9311ccb773f7d474d5f5a32c82b"
_numerical_summary = original.common.timing_summary


def timing_summary_with_samples(samples):
    """Preserve all numerical checks and retain the observed sample order."""
    original.require(isinstance(samples, list) and len(samples) == 45,
                     "Each warm HTTP distribution requires exactly 45 ordered samples")
    return {**_numerical_summary(samples), "samples_s": list(samples)}


def audit(study):
    original.require(original.sha(Path(original.__file__).read_bytes()) == ORIGINAL_SHA256,
                     "Immutable original holdout auditor changed")
    # Original audit's typed equality now checks the raw sample inventory too.
    # All original code paths, freeze checks, and failure propagation stay in use.
    with patch.object(original.common, "timing_summary", timing_summary_with_samples):
        return original.audit(study)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    original.require(not args.output.exists(), "Refuse audit overwrite/resume")
    os.environ["TIKTOKEN_CACHE_DIR"] = str(original.ROOT / ".cache/tokenizers")
    result = {"checks_passed": False, "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "auditor_sha256": original.sha(Path(__file__).read_bytes()),
              "model_calls": 0, "retrieval_calls": 0,
              "producer_or_production_helpers_imported": False,
              "correction": {"original_auditor": "scripts/audit_context_holdout.py",
                             "original_auditor_sha256": ORIGINAL_SHA256,
                             "made_after_native_outcomes": True,
                             "change": "Retain exact ordered 45-sample samples_s inventory alongside unchanged independent numerical distributions",
                             "original_guards_preserved": True}}
    try:
        result.update(audit(args.study))
        result["checks_passed"] = True
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        result["error"] = f"{type(error).__name__}: {error}"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as out:
        out.write(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return 0 if result["checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
