# Prospective context allocation development replay

This is a separately authorized CPU development experiment. The 144 original
development queries and all 15 HTTPX queries have already been observed. HTTPX
is **development evidence here**, and no result from this replay constitutes a
fresh repository holdout, model inference, retrieval measurement, coding trial,
provider-token saving, or product default change.

The sole inputs to selection are the original `hybrid_k24_b1` first-repetition
scored orders, unchanged literal source windows/scalars, static call references,
query text for compact formatting, original scope metadata, and fixed caps.
There is no rescoring, retrieval, prefix enlargement, query rewriting, altered
deduplication, containment removal, source reformating, or budget backfill.
Targets, query IDs, kinds, evidence spans and gold ranks enter only subsequent
scoring, never the selector.

| Condition | Maximum excerpts | Static dependencies |
|---|---:|---|
| `results8_dependenciestrue` | 8 | Enabled; original control |
| `results8_dependenciesfalse` | 8 | Disabled |
| `results12_dependenciestrue` | 12 | Enabled |
| `results12_dependenciesfalse` | 12 | Disabled |
| `results20_dependenciestrue` | 20 | Enabled |
| `results20_dependenciesfalse` | 20 | Disabled |

Every condition retains the original **12,000 source-character** and **4,096
`cl100k_base` compact JSON-token** caps. Raising the excerpt count spends capacity
on additional fragments and metadata; it is an explicit tradeoff, not free
quality. Scope, locations, source hashes and token-usage fields count toward the
same exact text budget. Counts describe one compact payload, not consumer billing.

The original first-ranked window per exact `(path, qualified symbol)` is retained
within scored K24; a later window of the same symbol never substitutes for it.
The eligible prefix contains at most the condition's maximum distinct symbols.
With dependencies enabled, keep the first five anchors, add the first two static
callees per anchor in source-graph order until the result cap, then fill remaining
slots with other eligible anchors. With dependencies disabled, deliver eligible
anchors in their original scored order. Dependency results can originate outside
scored K24 or candidate 50; they retain their dependency owner and inherited
anchor score and are never represented as newly scored windows.

Packing uses the original two-pass procedure: spend the source-character budget
on complete physical CR/LF/CRLF line prefixes in selected order, then apply the
frozen exact compact-token packer. No later character capacity is reclaimed after
token clipping. A selected window with no fitting whole-line prefix is omitted;
the original procedure's flags and hashes remain truthful. The source/context
and static graph modules are loaded only from bound archived original bytes into
a private temporary package. No live production backend, model, index, vector,
or runtime module is imported. The pinned CPU tiktoken/tree-sitter extensions are
allowed; MLX, model packages, native numerical packages and network connections
are refused. Missing/corrupt `cl100k_base` cached rank bytes fail without downloads
or cache writes. No package, library, environment or API is updated.

Before any real variant outcome, commit the new script, CPU tests, this protocol
and `results/context-allocation-development-2026-10-14/frozen-plan.json`. Freeze
records their hashes, audited input/archive hashes, full corpus file hashes,
runtime pins, six conditions, baseline inventories and the gate below. Synthetic
tests exercise only invented source fixtures. Freeze neither reconstructs nor
scores real variants. Run refuses untracked/uncommitted changed producer or plan
bytes, altered sources/inputs, or preexisting output artifacts.
After those checks, an exclusive `run-start.json` claims the attempt before any
control is reconstructed. Exceptions preserve `failure.json` with the stage,
verified control count and attempted/packed/verified condition counts. Either
marker prevents another run in that directory; a repair uses a newly named
attempt with the preceding evidence retained.

At execution, reproduce **all 159 actual original 8+dependencies contexts** before
computing any other condition. Require byte-identical compact text after restoring
the fixed metadata key order lost by canonical artifact storage, identical complete
result rows, source-character totals, budget flags and token usage. Timing, index
and CPU instrumentation metadata remain outside this immutable context envelope.
Any control mismatch aborts before variant computation/output. The all-case gate
receipt precedes the new replay ledger.

Original complete positives are **112/118 development** and **8/12 observed HTTPX**.
Predeclare a qualifying condition only if it:

- Retains every individually baseline-complete positive in each subset.
- Adds at least one complete positive across the two subsets combined.
- Keeps multi-file complete counts at least baseline separately in each subset.
- Keeps delivered required-group counts at least baseline separately in each subset.

Report all six conditions and all regressions, including individual group losses
even when aggregate gates pass. Full-span credit requires one actual verified
excerpt containing the whole executable evidence interval; OR alternatives stay
within their original group, and every AND group is required for complete success.
Multi-file success uses the original multi-file labels. Preserve exact output,
token/character/excerpt counts, scoped-absence inventories and every lost group
previously supplied only by static dependencies. No-return or returned candidates
do not prove a scoped feature absent. List qualifying conditions without selecting
an automatic winner; if none qualifies, there is no winner. A later proposed
product change requires separate review and a new source-reviewed holdout for
independent generalization claims.

```sh
.venv/bin/python scripts/replay_context_allocation.py \
  --output results/context-allocation-development-2026-10-14 --freeze
# Review and commit the unchanged script, tests, protocol and plan first.
.venv/bin/python scripts/replay_context_allocation.py \
  --output results/context-allocation-development-2026-10-14 --run
```

No real variant outcomes are produced during preparation. Execution is reserved
for root review after the currently frozen coding/native benchmark work allows it.
