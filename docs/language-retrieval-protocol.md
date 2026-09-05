# Prospective JavaScript and TypeScript retrieval evaluation

The source-authored labels in `benchmarks/language_quality_manifest.json` prepare
the next language phase. No model, retrieval, parser, or ranking output was used
to choose these queries or their implementations. They are separate from the
frozen Python coding-workflow studies. The initial byte/metadata audit is in
`results/language-source-preflight-2026-10-13/source-audit.json`; its executable
checks do not substitute for independent semantic review.

## Corpora and scope

Official pinned release sources are cloned into ignored corpus directories:

| Repository | Release | Commit | Indexed source scope | Assigned role |
|---|---|---|---|---|
| Express | v5.3.0 | `cf6722bf89a86905281d9160b6482e08166b5ef0` | `lib`, six JavaScript production files | development |
| Zod | v4.6.7 | `b500a2ee95134f81d74da7911d7378dd2853d042` | `packages/zod/src/v4`, 107 TypeScript production files, declared exclusions | repository/language holdout |

Release provenance is available from the official [Express
release](https://github.com/expressjs/express/releases/tag/v5.3.0) and [Zod
release](https://github.com/colinhacks/zod/releases/tag/v4.6.7). Original MIT license
bytes are preserved in `benchmarks/EXPRESS_LICENSE.txt` and
`benchmarks/ZOD_LICENSE.txt`, with hashes in the manifest. Corpora stay clean at
the pinned Git commits. External dependencies are not indexed and are not valid
implementation gold for these queries. Zod tests and declaration-only `.d.ts`
files are excluded; its classic/mini/core implementations and locale source
remain available as natural decoys.

Each repository has twelve present and three scoped-absent queries. Four present
queries per repository require multiple production files. Total inventory is
thirty queries, twenty-four present, six absent, and eight multi-file AND cases.
There are thirty-eight required evidence groups and forty-one target rows;
equivalent Express header aliases and two directly executable Zod nested
implementations are explicit OR alternatives. Express topics alternate development and
calibration in authored order; every Zod query is holdout. Do not select thresholds
or ranking parameters from Zod outcomes. Inspecting parser/source-byte correctness
is distinct from tuning retrieval to these labels, and any such inspection must
be documented.

## Gold and negative labels

Targets record exact repository-relative path, source function/member name,
definition and executable-evidence line bounds, declaration anchor, full-file
hash, and evidence-slice hash. A retrieved source window must include the required
executable evidence. A matching comment, export alias, or declaration-only
overload is insufficient. Assigned functions use their assignment name, class
methods use `Class.method`, named callbacks use their source name, and nested
named definitions use dot qualification. Equivalent aliases are explicit OR
alternatives. Independent reviewers can correct source facts before freeze,
with the reason recorded; no retrieved candidate may drive relabeling.

Scoring uses verified source-file identity and complete executable-evidence
coverage, rather than requiring an exact symbol label. An enclosing class or
function, a nested implementation, or a file window is eligible when its actual
returned bytes include the entire evidence span at the pinned file and revision.
One window may satisfy multiple groups when it includes every required span.
Symbol identities remain diagnostics; a name alone never establishes a hit.
Candidate coverage and delivered coverage apply this same rule to their
respective actual source bounds, with delivered bytes independently verified.

The source-only independent review added ordering evidence for the deep-partial
visitor and runtime WeakMap/Map initialization for metadata storage. It also
accepted the nested parse callback and error-tree walker as equivalent source
implementations. These corrections preceded all extraction and retrieval output;
their provenance is retained in `results/language-source-preflight-2026-10-13/label-corrections.json`.

`targets` has OR semantics. Within `target_groups`, alternatives are OR and every
separate group is required (AND). Report both group-level coverage and complete
query coverage. Examples include parser configuration plus request consumption,
template cache plus file lookup, safe-parse dispatch plus lazy error construction
and finalization, and metadata attachment plus registry storage.

Absent queries claim absence only from the declared production source at the
pinned commit. They include a source-review scope, searches, closest decoys, and
an explanation. Keyword match counts do not prove absence. Express middleware
registration does not implement a built-in rate limiter; Zod's asynchronous parse
does not implement retry backoff; optional cookie signing is distinct from
encryption. User callbacks, plugins, examples, and unindexed dependencies can
provide behavior outside this scope. Retain these negatives even if retrieval
returns plausible-looking decoys.

## Gates before inference

1. Independently review each query against pinned source, including evidence
   bounds, multi-file completeness, competing implementations, and absence scope.
   Run `scripts/audit_language_labels.py` to verify source/label/license hashes,
   exact release commits, clean checkouts, scope, anchors, and inventory counts.
2. Implement generic language extraction, saved-source snapshot validation, and
   the declared exclusion policy. CPU checks must establish exact snippets,
   qualified identities, overload omission, Unicode/line endings, and all gold
   evidence eligibility without importing weights or inspecting retrieval ranks.
   Freeze extractor/grammar and index fingerprint versions before retrieval.
3. Freeze model revisions/settings, environment, source membership hashes, all
   label/protocol/script hashes, candidate depths, returned result/source/token
   budgets, and the planned complete run inventory. Publish the freeze artifact.

No real model or retrieval run is authorized by these preparation commands alone;
the parent task schedules inference after the current paired study and its audits
are complete. Local GPU work remains serial and uses resident models.

## Prospective measurements

Start with lexical, dense, and hybrid retrieval under identical source membership
and return budgets, with H2O disabled. Record candidate coverage at a frozen depth,
delivered full-evidence coverage, first complete evidence rank, multi-file
completeness, absent-query outcomes, truncation, source/token budgets, cold index
setup, warm query latency, and persistent-index reuse after edits. Report JavaScript
development and TypeScript holdout results separately; do not pool languages into
an apparent holdout improvement. Retrieval coverage does not establish coding
correctness or hosted token savings.

An optional weak-match threshold may be calibrated on the designated Express
calibration queries only, then evaluated unchanged on Zod. The Python threshold
and its earlier evidence are separate artifacts. Preserve every miss and false
rejection, and avoid a general absence guarantee. A future H2O comparison requires
its own frozen shortlist settings and latency accounting. Paired coding tasks in
these languages, real editor workflows, and external beta users are later gates.

The thirty authored queries are a small first language sample and do not cover
all JavaScript syntax, all TS/JSX dialects, framework dependencies, or runtime
dispatch. Neither source preflight nor retrieval success is a claim of exhaustive
correctness.

## First run inventory, fixed before extraction/ranking output

The first run uses `scripts/run_language_quality.py`. Every authored query receives
BM25, Qwen dense, and equal-weight hybrid retrieval, with H2O and abstention off.
Each condition retrieves fifty candidate windows and returns at most eight
symbols, with a 12,000-character source budget and a 4,096-token compact
`cl100k_base` JSON budget. Dependency context uses the existing five anchors,
at most two callees per anchor, and one level. No parameters or threshold will be
selected from these results.

Express runs before the Zod holdout. Three repetitions rotate condition order by
query and repetition index: 270 fresh HTTP requests in total. Quality uses only
the first repetition for each query and condition; later repetitions measure
warm latency and check coverage stability. Repeated requests do not increase the
query sample size. Query vectors are computed afresh; deterministic candidate
diagnostics reuse the exact vector after the timed request, without another
embedding call. These diagnostics and source verification are outside the HTTP
latency measurement.

The CPU gate checks actual extractor membership against all 113 pinned source
files, exact window bytes, unique identities, and complete executable-evidence
eligibility before inference. It can be run against an isolated, committed
language implementation while the coding study finishes; it loads no models and
does not evaluate ranks. Freeze then records grammar/runtime/package versions,
source and window inventories, labels, implementation hashes, and budgets.
Every miss and absent-query decoy is retained. Cold model/index setup is separate
from warm HTTP timing, and a fresh backend must reuse every unchanged persistent
document vector. Saved edits and actual editor workflows receive separate
integration checks.

Pre-inference source gates found and preserved two implementation gaps: the
pinned grammar rejected legal TypeScript variance annotations, and consecutive
class registry field initializers were available only as separate excerpts.
Generic source-neutral repairs keep original byte offsets and add explicitly
named initialization context; the exact labels and ranking settings are
unchanged. The recorded failures and independent source checks are in
`results/language-extractor-preflight-2026-10-13/`.
Zod's holdout role concerns retrieval parameter selection. Its syntax and
executable-evidence eligibility were inspected during these CPU source gates;
the parser/extractor implementation was adapted before inference. This scope
must accompany the eventual retrieval results.
