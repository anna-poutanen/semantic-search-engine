# Fixed clause-fusion development probe

This separate, opt-in local-Qwen experiment uses all 30 already-observed Express
and Zod queries. Both corpora are development data here. It makes no new holdout,
production-default, coding-correctness, paid-token or generalization claim, and
leaves previous retrieval, coding-workflow and packing-replay evidence untouched.

One text-only rule is frozen before outputs: include the exact full query, then
at most the first three distinct nonempty clauses having at least four
whitespace-separated words. Split case-insensitively at whole-word `and where`,
`and then`, `while`, `then`, or `before`, matching longer phrases first. Never
split arbitrary `and`. Trim/collapse clause whitespace; deduplicate by collapsed
whitespace plus casefold. Full-query text is preserved literally. No kind,
target path, gold symbol, evidence span, source score or label controls splitting.

Each dense list has depth 50. The control fuses its full-query and eligible-clause
lists with equal-weight reciprocal rank, k=60, using exact rational sums. Ties
use original full-query rank, then stable snippet ID. Exactly one global merged
list of at most 50 is passed to the unchanged production Backend packer. There
are no per-clause output allocations. The final shared settings remain eight
excerpts, 12,000 source characters, 4,096 exact compact cl100k tokens, dependencies
enabled using the existing five-anchor/two-callee rule, H2O off and no threshold.
RRF values are priorities, never cosine similarity or calibrated confidence.
The separate reported top cosine is the actual raw full-query top-list value.

```sh
.venv/bin/python scripts/run_clause_fusion.py --freeze \
  --output results/local/clause-fusion-new-run
.venv/bin/python scripts/run_clause_fusion.py --run \
  --output results/local/clause-fusion-new-run
```

Freeze is CPU-only: it reads source, installed package and local checkpoint
metadata and extracts saved windows. It does not initialize model weights,
read weight-file bytes, download files, query Qwen/H2O, or access user config.
The fixed existing cl100k cache URL/key/SHA is checked, its immutable bytes are
captured for the explicit run, and unexpected tokenizer URL/hash requests are
rejected. Inherited cache paths do not allow a download or cache write.
It binds source/protocol hashes, all 30 literal texts and selected clause inputs,
all 60 scheduled attempts, installed distributions, checkpoint metadata, parser
fingerprint, source membership and exact extracted-window identities. Weight
size/mtime and the already-recorded pinned weight SHA are frozen; explicit run
verifies the complete weight bytes against that SHA before model loading.
Existing output paths cannot be overwritten, and an attempted run cannot resume
or retry. Failures preserve their observed counters/partial records and remaining
schedule; they are not treated as completed misses or replaced by selected pairs.

Each condition independently embeds its full query. The fusion condition also
embeds its eligible clauses, without query-vector caching across conditions.
Both conditions reuse the same freshly built per-repository SQLite document
vectors. An experiment-only adapter supplies each just-observed full vector and
precomputed candidate ranking to Backend.search, solely to reuse its unchanged
packing, dependency and exact token logic without extra inference. Packing
requires the same source snapshot and exactly one prepared vector/list reuse.
The production files/defaults are never edited. Phase intervals record real
query embedding, actual SQLite retrieval, fusion and packaging separately; the
outer interval is in-process development time, not the old HTTP timing. Backend
inner timing includes a prepared-vector reuse and is not presented as actual
model time. Cold index setup is reported separately. Condition order alternates
by unique query index; there is one attempt per query/condition, not repetitions.

Observed attempted/completed document/query call counts and actual input strings
are immutable before/after snapshots. Query-vector dtype, shape, norm and byte
SHA plus complete numeric values are retained for independent reconstruction.
These are model API calls/texts rather than individual Metal batch forwards.
Full baseline and every control raw source list, merged order,
all delivered excerpts, exact physical/source hashes and shared-budget checks
are preserved. Existing gold labels enter scoring only after source-only
selection and packing have finished. Quality denominators remain 30 unique
queries (24 present, six absent), with AND-group completeness, decoys, gains,
regressions and every miss reported separately. Memory observations are outside
the timed condition. No model/provider token usage is inferred from these counts.

Both datasets were inspected in previous experiments and the syntax extractor
was adapted before the original language inference. This is a single,
predetermined development probe of query formulation, with extra model cost
measured descriptively. Its outcomes cannot establish an independent holdout
improvement or justify tuning a shipped default on these observed labels.

Cooperative SIGTERM/keyboard interruption retains the attempted condition and
actual before/after counters before closing the model/backend and rethrowing.
A hard kill or machine loss can prevent a final receipt; no estimate or retry
substitutes for it. CPU real-encoder checks copy the existing verified cache into
an explicit private fixture, or clearly skip when that offline setup is missing;
missing/corrupt-cache and synthetic no-network/restoration checks always run.
