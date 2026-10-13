# Frozen references, published after all sixteen attempts

These four patches are the exact reference bytes whose SHA-256 values were
frozen in `benchmarks/coding_workflows_multifile.json`. They were held in the
ignored evaluator cache during the study and never copied into agent fixtures or
indexes. Publication occurred only after the complete inventory and audit.

The references contain known holes found by post-output probes. They are retained
unchanged for reproducibility; passing their original checks does not establish
complete contract correctness. See `results/multifile-workflows-2026-10-13/patch-review.md`.

To reconstruct the original cache paths from this checkout:

```bash
.venv/bin/python scripts/restore_multifile_references.py
```

The script checks every patch against the frozen manifest and refuses to replace
different existing bytes. Historical runtime source/package/native hashes and
protocols remain in the result artifacts. Use the recorded historical source for
a fresh replication; newer product code and a later CLI/provider are different
runtimes, and fresh sampling will not reproduce exact token counts or patches.
