# Complementary implementation windows

This follow-up tests literal source assembly under the existing **eight excerpts,
12,000 source characters and 4,096 exact compact JSON tokens**. Retrieval,
embeddings, reranking and static dependency allocation remain fixed. Experimental
conditions are available to the benchmark harnesses; production keeps its existing
selector unless a prospective validation gate passes. The v0.1.4 downloads are
unchanged.

## Why assembly needs a separate experiment

The [source-only capacity diagnostic](../results/window-preflight-2026-10-21/capacity-methodology.md)
finds all eight slots occupied in 298/337 historical development rows and 22/28
urllib3 baseline rows. Median unused capacity is 5,835 characters / 1,674 tokens
and 2,348 characters / 842 tokens respectively. Extra characters do not guarantee
room for another result. No pair of actually delivered windows shares the same
canonical definition in these baselines. Most literal source savings come from
nested containment, already addressed by the prior coverage control.

Only five historical development rows have a different retrieved forward
continuation for a delivered long definition; one complete suffix fits the
available character capacity before token costs. Some needed windows are not
delivered at all. This motivates testing a union before packing as well as a
source-preserving extension after packing. The diagnostic constructs no new
responses and reports no quality gain.

## Implementation and comparison controls

- `legacy` is the actual production display-label selector and unchanged packer.
- `coverage_control` is the previous research definition selector with contained
  source removal and bounded backfill. Its two known development gains are not
  new gains for this study.
- `connected_union` joins the first-ranked window of a canonical AST definition
  with its first connected retrieved companion, at the original rank and score.
  It can crowd out later source; every such regression is retained.
- `guarded_continuation` starts from coverage control and appends whole source
  lines to delivered retrieval windows, preserving all existing source.
- `guarded_connected_union` packs a connected proposal and a same-input coverage
  reference. It accepts only proposals containing every reference source interval
  with the same saved/buffer provenance; otherwise it returns the reference.
  Its native timing includes both packing passes and the guard.

Each accepted union is continuous, exact saved source from at most two retrieved
windows of the same actual definition. Matching display names alone cannot join
windows. New opaque `context:connected_windows:` IDs identify the intended union;
full and clipped bounds, source hashes, member spans and original score owner
remain verifiable. The scalar is inherited from its original retrieved anchor;
H2O/Qwen fields are null because the union was never reranked. Composite snippets
never enter the index or embedding cache. Assembly diagnostics stay outside the
compact MCP envelope; all returned source and dependency IDs count toward its
exact token budget.

## Frozen development replay

The [prospective protocol](window-development-protocol.md) and
[frozen plan](../results/window-development-2026-10-21/frozen-plan.json) cover
365 saved first-repetition rows sharing 187 dataset/query units. Repeated studies
and retrieval conditions are not independent queries. The raw matrix retains all
five conditions, stage receipts, original candidate scores, exact compact text,
guard operands and every evidence-group gain/loss against both controls.

Before any new condition ran, all 365 original compact/full-result controls and
all 337 saved prior coverage responses reproduced exactly. Freeze and execution
use committed bytes, verified pinned sources and offline syntax/tokenizer tools.
No model, index, embedding, retrieval or network call is made during replay.

Qualification requires zero lost groups, complete queries or single-file first
hits against either control, plus at least one additional distinct complete query
over coverage control. A newly covered group on an incomplete query is useful
diagnostic evidence but does not satisfy the complete-query gate.

### Results and decision

The [complete findings](../results/window-development-2026-10-21/findings.md)
retain every study/dataset/retrieval stratum. Across the 365 paired condition rows:

| New condition | Additional covered groups vs coverage control | Additional distinct complete queries | Lost groups vs either control | Decision |
|---|---:|---:|---:|---|
| Connected union | 0 | 0 | 1 | Reject |
| Guarded continuation | 0 | 0 | 0 | No incremental quality gain |
| Guarded connected union | 0 | 0 | 0 | No incremental quality gain |

The lost group is the Zod local JSON-schema reference query in the original BM25
study. Its required `resolveRef` source was returned at rank three by both
controls; enlarging an earlier window displaced that evidence. This also loses
one complete single-file query. Both guarded conditions avoid the regression.
The prior coverage control's two complete-query gains remain known development
results and are not evidence that window assembly improved quality.

The [independent audit](../results/window-development-2026-10-21/independent-audit.json)
passes on its first attempt and verifies all **1,825 outcomes**, both exact
historical barriers, physical source/assembly receipts, compact tokens and every
comparison. It resolves the committed producer execution `387b652` and committed
auditor execution `90b70e3`; no producer code or model is imported for validation.

**No condition qualifies for fresh inference or production promotion.** The
production selector remains unchanged. The TanStack Query corpus is retained
unobserved for a later candidate; there are no fresh retrieval results or native
latency claims from this experiment. This is a negative result about these fixed
assembly rules, not evidence that every possible window policy is ineffective.

## Fresh validation and interpretation

The [native protocol](window-native-protocol.md) reserves pinned TanStack Query
core source for a candidate chosen after development qualification and independent
audit. Labels, complete source/exclusion inventories, canonical windows, qualified
evidence and two source-only reviews are committed before retrieval. This is
project evaluation independence; it makes no claim about model pretraining.

Historical development scoring retains path plus full physical evidence interval.
The authored fresh set additionally requires an allowed qualified symbol and exact
evidence bytes/hash. Report these datasets separately. A source-evidence gain is
not proof of coding correctness, lower provider-token usage or faster coding.

The native comparison fixes three arms, dense/hybrid, three repetitions and one
fresh query embedding per measured request. Its gate requires a new complete
multi-file behavior over coverage control, no individual loss against either
control and median latency at most 1.25 times **each** control. Dense must pass;
hybrid cannot rescue it. Failed attempts and gates remain published.

## Reproduction and verification

Use the pinned Mac environment, local corpus checkouts and verified offline
tokenizer cache. Both harnesses require a new output directory and reject changed
bindings, uncommitted freezes or reuse of an attempted run. Commands and freeze
requirements are in the linked protocols. Historical audits resolve the exact
committed producer bytes rather than attributing later code to earlier results.

`scripts/audit_windows.py` independently checks source, canonical identities,
member continuity, composite IDs, original scores, dependency references, exact
compact tokens, controls, schedule and quality gates. It imports no producer,
backend, model or numerical runtime and forbids network calls. Adversarial tests
cover altered source/hash, hidden full-window credit, identity substitution,
source/provenance loss, crowding, control relabeling and asymmetric latency gates.
Python-produced full/clipped fixtures are accepted by strict MCP validation and
the editor's saved-source verifier. Cancellation, immutable indexed snippets and
one-query-embedding behavior also have integration coverage.

The local full core suite passes **2,876 tests, 10 skipped**; the final expanded
audit/native harness suite passes **60 tests**, including seven gate regressions
added after the full-suite run. The editor passes **118 tests**, and its profile
and shared-storage helpers pass **61 tests**. Five installed lexical CLI processes
also pass with no model imports or network attempts. Raw logs and hashed receipts
are saved in [validation](../results/window-validation-2026-10-21/validation.json).
