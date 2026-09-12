# Prospective context packing development replay

This CPU experiment uses the first-repetition candidate orders already retained
by the Express/Zod study. Both repositories and Zod outcomes have been observed.
It is a development replay, not a new holdout or a production policy change.
No retrieval, embedding, model forward, reranking or timing measurement is run.

Before producing replay responses, `scripts/replay_context_packing.py --freeze`
creates a new `plan.json` recording the algorithm, both controls, settings, script
hash, input hashes, pinned implementation hashes and every corpus source hash.
The plan is committed before `--run`. The run checks those bindings and loads
only the frozen source/context/extraction/static-reference Python modules from
the recorded Git revision into a private temporary directory. It reconstructs
snippets from the frozen window inventory and verifies literal saved-source
bytes. It never opens the persistent vector index.

The mandatory baseline gate reconstructs the original packing procedure:
first raw window per path/symbol, first eight symbols, first five anchors with
up to two static callees each, whole-line character clipping, then the original
compact token pass. Every one of the 90 first-repetition responses must match
exact compact JSON, full result metadata, source characters and flags before
either new control is computed. Failure stops the replay.

Both controls use the same extended stream: first five raw anchors, each
anchor's first two static callee windows, then remaining raw symbols from the
immutable top 50. Global first-occurrence path/symbol deduplication preserves
the original choice of ranked window. A skipped window does not enable a later
window of the same symbol. The stream can contain static callees outside the
raw top 50, exactly as the original dependency policy does.

For each trial, clip a whole-line prefix to the remaining **delivered** character
budget, then trim whole lines until the exact compact `cl100k_base` JSON fits.
The fixed caps remain eight delivered excerpts, 12,000 source characters and
4,096 compact tokens. Prior delivered rows stay fixed. Empty prefixes are
skipped; later candidates may still fit. No text or partial overlaps are edited.
Literal Unicode, physical CR/LF/CRLF lines, locations and source hashes are
preserved. Flags describe truncation of delivered rows; separate diagnostics
record skipped trial prefixes.

- **Backfill only:** accept every nonempty fitting prefix, even if already covered.
- **Containment plus backfill:** after clipping, skip a prefix only when its full
  inclusive line interval is covered by the union of already delivered intervals
  in that same path. The skip consumes no excerpt, character or token capacity.

The second control is compared with the first, as well as with the exact original
baseline. This separates containment avoidance from continuing farther through
the stream and reclaiming characters removed by token clipping. Dependency
order, caps and symbol choice are identical across the two controls. Selection
receives only source windows, fixed scores/order, static references, query text
for compact metadata and caps. Query IDs, kinds, target paths/evidence lines and
gold ranks are unavailable to the selector. Any later evidence scoring must be
separate from selection and retain regressions as well as improvements.

Example CPU invocation, with an already installed language/tokenizer runtime:

```sh
TIKTOKEN_CACHE_DIR=/path/to/project/.cache/tokenizers \
  /path/to/python scripts/replay_context_packing.py \
  --root /path/to/project --output results/context-packing-development-2026-10-13 --freeze
# Commit the prospective plan and unchanged script before continuing.
TIKTOKEN_CACHE_DIR=/path/to/project/.cache/tokenizers \
  /path/to/python scripts/replay_context_packing.py \
  --root /path/to/project --output results/context-packing-development-2026-10-13 --run
```

The replay cannot establish new retrieval quality, packing causality for every
historical miss, speed, provider token savings or coding correctness. Its token
counts describe local compact context only. The existing study's diagnostic miss
categories are observations; they do not establish that packing caused a miss.
