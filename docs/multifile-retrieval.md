# Multi-file retrieval

`retrieval="multifile"` is an explicit alternative to dense search for compound
behavior questions. It uses the existing Qwen full-query embedding once, keeps
the first three distinct dense symbols, and interleaves lexical matches for
literal clauses of the question. It does not invoke a query-writing model or a
reranker. Dense remains the default.

The fixed splitter recognizes commas and the whole words `and`, `while`, and
`then`. A clause needs three distinct content terms; fewer than two qualifying
clauses falls back to dense order. At most four clauses contribute. Each clause
fetches 50 FTS5 matches and contributes its first three distinct symbols, in
round-robin order, after the three dense anchors. Remaining dense symbols fill
the merged list to at most 50. Scores are rank priorities, not cosine similarity
or confidence. Weak-match policy still receives the original full-query cosine.
The existing dependency, result, character and exact compact-token packers apply
to this merged order. This mode can displace useful dense results; it does not
guarantee that all requested implementations will be found.

## Diagnosis and prospective validation

The CPU tracer in `scripts/diagnose_multifile.py` reconstructs all 90 original
first-repetition Express/Zod compact responses exactly before assigning losses.
It uses source spans, not symbol-name hits. Of 27 missing required groups, nine
were absent from the retrieved pool, two were discarded by first-window symbol
deduplication, fifteen fell beyond the result cap, and one was displaced by
static dependencies. None were lost to character or token clipping in this
study. These are exclusive losses after any dependency recovery. They describe
the observed stream, rather than a causal explanation of model behavior.

Query-coverage and path-diversity prototypes were explored on these already
observed repositories. They caused regressions. The selected dense-plus-clause
probe recovered one Zod multi-file case without a complete-query loss against
dense in either development repository. Those outcomes are development evidence.
They do not justify changing the default or claiming a general improvement.

Before the first Jinja retrieval outcome, freeze the implementation, literal
queries, exact evidence spans, corpus revision, source/window hashes, runtime,
settings and prospective gate in a committed `plan.json`. The fresh Jinja 3.1.6
dataset has eight multi-file, four single-file and two scoped absent queries;
its source-only authorship and independent review are recorded separately.
No holdout outcome may change the algorithm, wording, labels or limits. A failed
holdout stays a failed holdout; later work needs another independent repository.

Compare `dense`, `multifile` and `hybrid` through the actual resident backend.
All requests use eight excerpts, 12,000 source characters, 4,096 compact JSON
`cl100k_base` tokens, dependencies enabled, depth 50, and no reranking or
abstention. Rotate condition order by query and repetition. Run three repetitions;
score quality only on repetition one and use repetitions for latency/stability.
Each measured request must perform exactly one fresh query embedding. Cold
index/model setup and source-diagnostic serialization are outside warm timers;
saved-source checks, query embedding, retrieval and packing are inside them.
The timer measures the backend, not HTTP, MCP, editor or agent completion time.

The gate compares multifile with dense: at least one net complete multi-file
gain, no complete single-file loss, and at most 25% warm median latency overhead.
Retain every complete and group regression, raw candidate order, stage trace,
literal delivered excerpt, exact budget verification and absent-query decoy.
Evidence credit requires the exact pinned path and the full executable interval
in one verified excerpt; enclosing symbols are allowed, alternatives within a
group are OR, and required groups are AND. Absent-query candidates never prove
that the requested behavior exists or is absent. This small authored sample
does not establish coding correctness or provider token savings.

Reproduce with the pinned corpus and the installed Apple Silicon model runtime:

```sh
PYTHONPATH=src .venv/bin/python scripts/benchmark_multifile.py \
  --dataset benchmarks/multifile_holdout_queries.json \
  --corpus /path/to/pinned/jinja --model /path/to/qwen3-embedding-0.6b \
  --output results/local/new-multifile-run --freeze
# Commit the plan before --run; the run rejects changed bindings or old output.
PYTHONPATH=src .venv/bin/python scripts/benchmark_multifile.py \
  --dataset benchmarks/multifile_holdout_queries.json \
  --corpus /path/to/pinned/jinja --model /path/to/qwen3-embedding-0.6b \
  --output results/local/new-multifile-run --run
```

The command is a reproduction on an already observed corpus after publication;
it cannot become another fresh holdout by changing its output directory.
