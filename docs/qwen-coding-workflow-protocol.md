# Three-condition coding workflow comparison

This is a prospective rerun of observed development tasks, prepared after the
earlier multi-file study and its independent patch review. It is not a fresh
coding benchmark. Its purpose is to compare ordinary repository tools, fast
local retrieval through MCP, and the same retrieval with the Qwen reranker
selected by the separately frozen retrieval experiment.

No hosted coding trial may begin until the full retrieval confirmation and
fresh HTTPX validation have finished, their independent audits have passed,
and the production integration, checker controls, common runtime, transport
probe, task manifest and complete schedule have been frozen and committed.
HTTPX results must not be used to tune the selected reranker.

## Tasks and correctness

Use the existing public feature contracts for Requests response byte budgets,
Click atomic file transactions, and Starlette request body limits. Each starts
from its pinned unmodified upstream checkout in a separate fresh fixture. The
same public task prompt is supplied in every condition; evaluation scripts,
reference patches and prior agent solutions stay outside the agent checkout.
This separation is an evaluation practice rather than a security boundary.

Freeze a new strict checker manifest. Preserve the original selected checks
and add the accepted contract probes from the prior independent patch review
before any new output exists. In particular, check suspended live and cached
readers after a response budget breach, response context cleanup, response
replacement and reads within a hook chain, independently registered suppressing
resources during an atomic rollback, and preservation of a primary callback
exception through other registered cleanup. Apply every new check to both an
unmodified baseline and a new evaluator-only reference control. If a control
needs repair, preserve its old result and exact repair hash before freezing.

Passing the strict checker is one gate. Passing all frozen compatibility
commands in an evaluator-only copy with the original pinned tests, fixtures
and test configuration restored is a separate gate. `correct` requires both.
`completed_correct` additionally requires normal CLI completion, exit zero,
no timeout and no recorded infrastructure or turn failure. Independently
review every retained production, test and configuration edit. Supplemental
findings must not silently replace frozen grades.

## Conditions and schedule

Run three conditions: `baseline`, `mcp_fast`, and `mcp_qwen`. The two MCP
conditions use the retrieval family selected by the full development decision.
The fast condition disables reranking; the Qwen condition defaults to the
selected shortlist with original singleton scoring. Both expose the same
search tool and ordinary repository tools, bounded compact source output and
testing guidance. The advertised defaults and descriptions are recorded as
part of each condition. Record any explicit agent overrides and actual search
receipts; availability is not evidence that a tool or reranker was used.

Use two independent repetitions of three tasks, for eighteen scheduled
attempts. Within repetition one, rotate the three conditions across tasks.
Within repetition two, reverse those orders. This gives each condition two
appearances in each position. Freeze all task identities, condition settings,
orders and prompts before the first trial. Do not retry, resume, substitute
tasks, discard failures or change the checker after seeing outputs.

Both paths retain the existing `gpt-6.1-sol` model, `ultra` reasoning effort,
default service tier, ordinary tools, isolated CLI configuration and
900-second timeout. Disable collaboration inside the coding trials. Use the
same frozen Python environment, 4096-token tool-history cap, local-loopback
permission and common instructions in every condition. The same prompt asks
agents to use `search_code` before broad reads when available, start with five
results and a 2000-token compact context budget, and inspect only missing
definitions or ranges. An MCP adapter must return one compact text payload.

## Runtime, source evidence and timing

Verify the CLI binary, packages, source, checkpoint, task and checker hashes
before running the frozen schedule. A separate hosted transport probe must
perform a bounded lexical MCP call and the controlled loopback command with
model clients that reject local inference. Record every probe attempt and its
reported usage separately from the eighteen coding attempts.

Run trials serially, with freshly owned local services and indexes. Complete
initial indexing and model warmup before the agent timer begins, recording
those costs separately. Agent time spans CLI launch through process exit and
includes adapter startup, hosted model waits and all tool calls. Full trial
time additionally includes fixture preparation, startup, teardown and grading.
Capture independent search receipts with saved-source snapshots before and
after calls, returned source hashes, retrieval and reranker settings, native
inference counters and measured backend time. A saved edit must be reflected
in subsequent index/search results. Keep model shutdown and process cleanup
receipts. Suspend or power interruptions invalidate timing claims rather than
becoming removable slow outliers.

## Actual usage and reporting

Use the provider-reported `turn.completed.usage` fields from `codex exec
--json`. Input plus output is the observed gross total. Cached input and cache
writes are reported input subsets; reasoning output is an output subset. Do
not add subset fields to the gross total. Report uncached input separately as
input minus cached input when both measurements are available. Keep the raw
reported fields and completed-turn count. These CLI events do not establish
per-model-request billing or complete usage for an interrupted turn.

The [official non-interactive CLI documentation](https://learn.chatgpt.com/docs/non-interactive-mode)
documents JSONL events and completed-turn usage. The
[official prompt-cache diagnostics guide](https://developers.openai.com/api/docs/guides/prompt-caching/diagnostics)
distinguishes measured cache reuse from cache estimates. The harness uses
existing normal CLI authentication and never reads or publishes credentials.

Publish every scheduled attempt, including timeout, capacity, startup, transport
and grading failures. Missing usage is unknown, never zero. Report completion
and correctness rates over the full inventory first. Compare paired token and
time measurements only where both actual values exist, with the number and
identities of contributing pairs. Label normally completed and completed-correct
subsets explicitly. Separate gross tokens, uncached input, cache reuse, agent
time, setup time and full trial time. Do not attribute a difference solely to
retrieval or reranking when observed tool use, cache reuse, provider failures
or condition defaults differ. This small development rerun can establish
measured outcomes on these tasks, not a general token-saving claim.
