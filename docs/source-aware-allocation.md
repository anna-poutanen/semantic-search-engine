# Source-aware complementary implementation selection

This follows the [query-aspect allocation study](complementary-allocation.md).
That study found new complete results but also dropped required implementations;
its lexical term guard did not protect their actual source after packing.
This experiment tests conservative static relationships and a guard on the
source that a client actually receives, within the same eight excerpts,
12,000 source characters and 4,096 exact compact JSON tokens.

## Source diagnosis and fixed rules

The existing graph resolves Express `app.set` calling `compileQueryParser` and
`compileTrust`. Protecting five anchors and their verified direct neighbors
would retain the implementations lost by the earlier fill rule. Other apparent
relationships remain unresolved: Jinja's bytecode-cache receiver comes through
an attribute, its include implementation emits calls inside generated strings,
and urllib3's connection comes from a factory. The new rule does not invent
those edges or claim that it explains the three earlier gains.

The [prospective protocol](source-allocation-development-protocol.md) fixes
two research conditions. Both preserve the first five anchors, original
dependencies and owners, and baseline implementations directly connected to
those anchors. They admit at most two new retrieved caller/callee neighbors in
original rank order and replace the latest unprotected baseline rows. No score,
source window, model, reranker shortlist or retrieval order changes.

After ordinary packing and coverage backfill, `source_graph` checks actual
structurally protected reference source; `source_graph_all` checks every actual
reference row. Physical ranges, literal bytes, hashes, origins and editor-buffer
provenance must survive. Containing excerpts that absorbed a protected window
are protected too. A rejected proposal returns the exact coverage control.
Receipts stay outside the public response and MCP schema. Production aliases
remain unchanged.

## Evaluation and independent checks

The development matrix reuses 365 observed cases sharing 187 dataset/query units.
It reproduces 365 original responses and 337 prior coverage controls, then all
730 original control arms before any new condition. The two failed allocation
arms remain immutable historical references. Reused retrieval conditions are
shared observations, not independent queries.

The new plan binds complete source/definition/graph inventories, old record and
artifact hashes, original Git identities, current implementation, tests and
auditor bytes. The independent auditor shares only the frozen conservative
source graph extractor. It independently reconstructs selection, protected
source checks, packing, backfill and all quality metrics. Exact compact tokens
are distinct from provider-billed agent usage.

Qualification requires no individual group, complete-row or single-file hit
loss against either control, plus a newly complete distinct query beyond
coverage control. A separately frozen native comparison on the reserved
TanStack Query corpus follows only an independently audited qualifier.

## Results and decision

The [frozen development replay](../results/source-allocation-development-2026-10-27/findings.md)
retains all 730 new outcomes, with 730 original control references and 730
historical negative references. Rules, source, tests and the independent auditor
were committed in `e34d6f6` before candidate execution. The
[control-only preflight](../results/source-allocation-preflight-2026-10-27/packing-parity.json)
first reproduced all 365 ordinary packs and all 365 complete coalesced responses.
The committed [independent auditor](../results/source-allocation-development-2026-10-27/independent-audit.json)
passes all 730 new traces and both historical reference matrices on its first
attempt, including exact packing, source, scores, token counts and every quality
gain and loss.

| New condition | Additional distinct complete queries vs coverage | Lost groups vs legacy / coverage | Lost complete rows vs legacy / coverage | Qualifies |
| --- | ---: | ---: | ---: | --- |
| `source_graph` | 2 | 1 / 1 | 0 / 0 | No |
| `source_graph_all` | 0 | 0 / 0 | 0 / 0 | No |

The new complete behaviors concern Express's configured query parser and trusted
forwarded protocol. Promoting `app.set` through verified caller edges recovers
their configuration side effects. Six improved case rows share only two distinct
query units across reused language/native and retrieval conditions; they are not
six independent behaviors. These gains differ from the earlier literal-term
study's Jinja and urllib3 gains.

The one loss is HTTPX's JSON request/header behavior. The original eligible list
contains five anchors and two protected dependencies; its remaining slot holds
`Request._prepare` (raw rank six). The graph proposal replaces that slot with
`encode_request` (raw rank eight), which directly calls three protected encoder
anchors. It recovers the encoding group while losing the existing header
preparation group. Both outputs fit comfortably: 5,007 to 5,461 characters and
2,087 to 2,223 compact tokens. Nothing was clipped and no backfill ran. The
missing implementation was retrieved and delivered before allocation evicted it.

This task was already incomplete, so a complete-query-only regression check
would miss the loss. The frozen individual-group gate correctly rejects it.
The stricter all-source guard rejects 53 of 58 actual proposals. It accepts five,
changes two final compact contexts, and adds no complete behavior beyond coverage
control. Structural protection accepts 50 proposals and changes 47 final contexts.
Both use the same 58 proposals, so guard acceptance, changed output and quality
improvement are reported separately.

The [posthoc source diagnosis](../results/source-allocation-development-2026-10-27/diagnosis.md)
explains the boundary: `Request.__init__` explicitly calls both the promoted
`encode_request` and the lost `Request._prepare`. The full graph contains the
relationship, but the anchor-only neighborhood does not protect that shared
constructor flow. This is an explanation of the observed result, not a new
selection rule or an independently validated improvement. All derived activity
and diagnosis receipts are marked separately from the canonical audit.

Neither candidate qualifies for fresh inference or promotion. TanStack Query
remains unqueried; production defaults, context limits, version and release
assets remain unchanged. A next development experiment can test complementary
implementations connected through a shared orchestrator, with a new prospective
freeze and the same individual-group and actual-source guards. This replay does
not establish native warm latency, coding correctness or billed token savings.

## Validation

The [validation receipt](../results/source-allocation-validation-2026-10-27/validation.json)
records 3,178 passing core tests (10 skipped), including 161 new selector,
backend/MCP, replay and adversarial auditor tests. The 179 editor/host-helper
tests pass. Five actual CLI processes using this worktree's entrypoint verify
lexical source, cache reopen/edit and malformed-source recovery with zero model
imports or socket connection attempts; this is source-checkout validation, not
a new package or download qualification.

Backend checks cover exact source and compact MCP caps, unsaved-buffer versions
and hashes, CPU-injected H2O/Qwen score provenance, single query embedding, cache
reuse, cancellation during the guard and final source snapshot rejection.
The auditor independently reconstructs exact maximal packing/backfill and rejects
forged early trimming, omitted excerpts, changed backfill order, bad witnesses,
clipped protected source, changed provenance and missing dependency owners.
An interruption test preserves the first completed trace when the next candidate
fails; attempted runs cannot be resumed or overwritten.

The initial authored backend fixture incorrectly treated `content_hash` as a
whole-file hash. It is the original snippet hash; correcting that assertion made
the checks agree with the existing source contract. The initial failure log and
final passing checks are retained. No observed candidate run was repaired or
replaced, and no selection rule changed after its freeze.
