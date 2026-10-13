# MCP-first coding workflow follow-up

This is a separate, explicitly prompted follow-up to the original twelve-trial
availability pilot. It is prepared because providing the tool may not make the
agent use it. Run it only if every original trial has finished, all six original
MCP-assigned runs recorded no MCP calls, and a separate tool-exposure diagnostic
has confirmed a successful `semantic_search.search_code` call. The original
results, prompts, checks and protocol remain unchanged.

The study uses three reused developer-authored seeded regressions in pinned
Requests, Click and Starlette source. Select the first task for each repository
in original manifest order: `requests_auth_ports`, `click_atomic_close`, and
`starlette_background_order`. This deterministic rule does not select tasks
from their observed savings. These tasks were already run in the first pilot
and are development examples, not fresh tasks or an independent holdout. The
new manifest records the original manifest hash, task identities, original
behavior-prompt hashes, and unchanged seed/check/reference hashes.

Append exactly the same instruction to the behavior prompt in both conditions:

> When the search_code MCP tool is available, use it before reading broad source
> files to locate the implementation relevant to this task. If that tool is
> unavailable, proceed with ordinary repository file search. Continue making
> and testing the repair using the available tools.

The instruction names the tool and encourages its use without giving source
paths or implementation locations. The baseline receives the identical prompt
and has ordinary repository tools; the MCP condition additionally receives the
registered local search server. This measures a prescribed search workflow,
rather than spontaneous adoption of an available tool. Actual calls and
arguments remain outcomes: do not declare active retrieval benefit merely from
tool availability or instruction compliance claims.

There are six new agent executions: one baseline and one MCP run per task.
The existing paired scheduler produces baseline-first Requests, MCP-first Click,
and baseline-first Starlette. The three-pair schedule cannot evenly balance
which condition runs first; publish the frozen order and cache categories.
Fresh seeded checkouts and conversations are created for every execution. No
run receives another run's patch, transcript, supplemental diagnostics, or
outcome. No automatic retries or resumed attempts are allowed.

Use exactly the original model, reasoning effort, CLI binary/version, common
configuration, runtime packages, embedding metadata, Python interpreter, MCP
defaults, permissions, authentication mode and 600-second timeout. The wrapper
checks these against the source configuration before the new runs. Keep the
original requested collaboration-disable flags, and report the observed alpha
CLI behavior separately; those flags previously did not prevent failed spawn
attempts. Ordinary source search and testing remain available in both
conditions. H2O remains disabled and MCP defaults to dense Qwen retrieval with
static dependency context and a 4,096-token compact-text budget.

The three tasks reuse their original external hidden behavioral checks: three
Requests methods, four Click methods, and three Starlette methods, ten methods
per condition. The full original pilot had twenty-one methods across six tasks.
The check scripts and their hashes are unchanged. This selected gate is narrow:
the original Click patches passed it despite a subsequently discovered
cleanup-error compatibility defect. Supplemental CPU diagnostics and manual
patch review must be reported separately from frozen check labels, including
the date and reason they were selected. Keep any additional probe results and
fault-injection instructions outside the agent checkout and prompt.

The atomic-output cleanup-error probe is predefined for this follow-up from
the original post-hoc finding. Before any new outputs, freeze the reusable
`scripts/probe_workflow_close_errors.py` hash and the original
`close-error-probe.json` evidence. Apply that same probe to the follow-up's
Click repairs and pinned upstream reference after the agents exit. Report its
outcomes separately; it does not replace or extend the ten-method formal gate.

Freeze the amended prompts, manifest, source configuration, successful exposure
diagnostic, wrapper, this protocol, and existing runtime artifacts before the
first follow-up execution. The wrapper temporarily chooses the new manifest
while calling the existing freeze function, then adds these follow-up artifacts
and provenance to the new frozen configuration before any agent starts. It does
not edit the original runner or original protocol. During execution the runner
checks all those hashes. Any later method change requires another named study.

The correctness, timing, token-accounting, caching, artifact preservation and
failure rules in [the original protocol](coding-workflow-protocol.md) apply.
Publish every new run and per-task paired difference, with all-pair and
both-completed-and-selected-check-passing comparisons. Setup/index warmup and
grading are measured separately. Gross tokens are reported input plus output;
cached input and reasoning output are included subcategories, and unavailable
counts remain unavailable. Do not pool follow-up tokens or timing with the
original pilot or the tool-exposure diagnostic. Include each study's resource
cost separately in the full experiment inventory.

Three reused task pairs and one run per condition are especially limited.
Prompt changes were informed by the first study's lack of uptake, so this is an
exploratory follow-up. It cannot establish significance, broad generalization,
the project's 30% savings target, or the effect on fresh developer tasks. Any
positive result requires validation on newly frozen tasks and repeated trials.

After the original pilot and successful separate exposure diagnostic, review
and commit the follow-up preparation, then freeze and run it explicitly:

```bash
.venv/bin/python scripts/run_workflow_followup.py validate
.venv/bin/python scripts/run_workflow_followup.py freeze \
  --source-config results/coding-workflows-2026-10-12/frozen-config.json \
  --diagnostic results/mcp-exposure-diagnostic-2026-10-12/diagnostic.json \
  --output results/local/mcp-first-follow-up \
  --work-root .cache/workflows/mcp-first-follow-up
.venv/bin/python scripts/run_workflow_followup.py run \
  --output results/local/mcp-first-follow-up
```

`validate` performs only CPU manifest checks. `freeze` records metadata without
agent execution; `run` launches the hosted agent and local embedding inference.
The script does not invoke either automatically.
