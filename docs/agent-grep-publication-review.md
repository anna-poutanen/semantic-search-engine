# Agent/grep evidence publication review

The [October 15–16 maintenance study](../results/agent-grep-2026-10-15/findings.md)
retains all sixteen attempts. It supports a descriptive comparison, not a claim
that semantic search generally improves coding correctness or saves paid tokens.
This October 29 review publishes the previously local evidence and checks it
without running new agents, models, searches or original upstream test suites.

## Provenance boundary

The original protocol, task manifest, checker and benchmark driver were local,
uncommitted files during the historical study. The saved configuration binds
their SHA-256 hashes; run-start and continuation records bind that configuration,
and captured timestamps place the claimed local freezes before the corresponding
attempts. These are locally recorded prospective controls. They do **not** supply
a prospective Git commit, an external timestamp attestation, or independent proof
of when those records were authored. Publishing them now cannot create that proof.
The historical phrase “prospectively frozen” must be read within this boundary.

The original protocol, continuation amendment, drivers, tests and all original
results remain byte-for-byte unchanged. The [new input manifest](../results/agent-grep-publication-review-2026-10-29/input-manifest.json)
provides all 64 bytesets bound by the original configuration, including twenty
small evaluator control logs/reference patches previously present only in the
ignored local cache. Historical source files are saved as `.txt` data; matching
old tracked bytes were recovered from existing Git blobs where necessary. This
recovery proves a byte match to the saved hashes, not execution of those bytes.
Nineteen earlier preflight files, including the failed HTTPX dependency control,
are retained separately without claiming the final freeze bound them.

The original runtime-specific reporter still checks the exact historical CLI,
installed packages and runtime source. It is unsuitable as an offline verifier
of a current checkout. Its outputs are preserved rather than regenerated under
a different runtime. Use the separate standard-library verifier instead:

```sh
python3 scripts/audit_agent_grep_publication.py \
  --study results/agent-grep-2026-10-15 \
  --review results/agent-grep-publication-review-2026-10-29 \
  --output /tmp/agent-grep-publication-audit.json
```

Choose a fresh output path. This command never runs a coding trial or loads the
current search backend, model runtime or tokenizer. It checks 268 original files,
64 frozen input hashes, all sixteen per-run/raw event bindings, provider usage,
frozen behavior/compatibility grades, original and qualified summaries,
continuation prefixes, patch-review coverage and the supplemental defect. It also
matches all fourteen actual single-text MCP payloads to HTTP receipts and all
61 delivered excerpts to literal physical lines in retained source snapshots.
It verifies saved evidence rather than independently rerunning the original
agents or graders. The [review receipt](../results/agent-grep-publication-review-2026-10-29/audit.json)
keeps this distinction explicit.

## Outcome and interpretation

Both conditions complete seven of eight attempts normally. Each condition has
one interrupted Starlette attempt with no completed-turn usage receipt. Neither
interrupted attempt is retried, and neither full-workload token total is known.
Four transport diagnostics are outside the coding inventory and paired totals.

All fourteen normally completed attempts pass the frozen automated checks. The
original supplemental probe finds one known Click canonical-normalization
regression in `03-r1-click_choice_aliases-mcp`. The two interrupted attempts have
empty patches and cannot be evaluated as completed implementations. Reinspection
of the retained implementation diffs agrees with the documented mechanisms;
this is scoped review, not an exhaustive proof that the remaining patches are
correct. Original frozen grades stay unchanged.

Six normally completed pairs show 9.22% fewer gross tokens and 4.57% more full
trial time for search. Excluding the known Click defect leaves five post-output
pairs: 4.97% fewer gross tokens, 5.77% less uncached input, 0.18% more agent time
and 10.15% more full trial time. These are matched-sum changes, not full-workload
savings, significance estimates or billing estimates. The post-output subset
must remain labelled and cannot replace the full inventory or primary grades.

All four tasks were authored in already observed public repositories. Prompted
search-first use, provider/cache variation, infrastructure interruptions,
selected checks and the missing prospective Git proof limit interpretation.
The practical conclusion is that this additional study still does not establish
a coding-quality or general efficiency advantage. It does not justify changing
retrieval defaults or restarting the coding comparison without a stronger reason.

## Publication checkout verification

The first Linux CI checkout exposed twenty recovered evaluator-control files
that were present locally but omitted from Git by the nested `.cache` ignore
rule. The repair explicitly adds only these hash-bound evidence files and checks
Git tracking of every input-manifest path. The failed CI log and separate repair
checks remain in the review directory; all historical outcomes and source bytes
are unchanged. This closes a publication completeness gap, without changing the
study interpretation or claiming new coding-agent evidence.
