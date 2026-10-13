# Paired coding workflow pilot

This experiment measures actual code changes made by the same coding agent with
ordinary repository tools, then with those tools plus the local MCP
`search_code` tool. It measures correctness, elapsed completion time, and the
agent runtime's reported token usage. The local `cl100k_base` excerpt counter is
not the token-usage measure for this experiment.

The first pilot contains six developer-authored tasks, two each in pinned
Requests, Click, and Starlette repositories. Each task starts from a deliberately
introduced regression in real upstream code. These are reproducible seeded
bugs, not previously reported upstream issues or a representative sample of
developer work. Task authors read source and public behavior without consulting
retrieval rankings to select the tasks. These repositories appeared in the
earlier retrieval evaluation; this pilot is not an independent repository
holdout or a claim that the code was absent from agent pretraining.

## Freeze before agent runs

The task manifest records the upstream commit and source root, task prompt,
regression patch, and withheld correctness-check entry point for each task.
Record the SHA-256 hashes of the manifest, prompts, patches, verifier code,
runner, MCP adapter, retrieval implementation, model metadata, dependency lock,
and concrete run configuration before the first agent execution. Preserve the
exact frozen configuration with the results. Changes after the first output
require a separately identified follow-up experiment; do not silently rewrite
the original tasks, methods, or failed outcomes.

Verify every check before running either condition:

1. It passes against the pinned upstream implementation.
2. It fails against the seeded starting tree for the intended behavior.
3. It passes when the minimal reference correction is applied.
4. Its negative controls preserve related behavior and catch common overbroad
   corrections.

Checks exercise actual public APIs, use no live network, and run against the
agent's resulting source tree. A prose answer, retrieved excerpt, patch
similarity, or agent claim that its tests passed cannot substitute for these
checks. Reference corrections and check files are outside the agent's task
checkout and omitted from its prompt and MCP index. This is withheld evaluation
material, not a claim that arbitrary filesystem access is a security sandbox.
Record check-file integrity and inspect tool traces for evaluation leakage.

## Conditions and execution

Use one paired run per task: twelve agent executions in total, with a 600-second
agent timeout for each. Predeclare the task order and alternate which condition
runs first, giving each condition three first runs. Do not reorder tasks from
observed difficulty or select only successful pairs. Every execution gets a new
checkout of the same upstream commit with the same regression applied and a new
conversation. Neither run
receives the other run's patch, transcript, tests, or outcome.

Each task checkout contains the real upstream repository, including its public
documentation and tests, with the regression committed as its initial visible
state. Its ordinary Git diff therefore does not reveal the seeded edit. The
upstream revision and exact seeded bytes are preserved in experiment provenance.

The baseline has ordinary coding-agent tools and no semantic-search MCP server.
The MCP condition has the same tools, model, reasoning effort, permissions,
task prompt, working-tree state, environment, and time limit, with the local
server additionally available. Isolate inherited MCP registrations so the
baseline cannot accidentally use the tool. Do not give the MCP condition extra
implementation hints or require a call the baseline cannot make. The agent may
choose whether to use the available tool; report that choice.

The predeclared agent is `gpt-6.1-sol` with `ultra` reasoning effort, matching the
user's configured agent settings. Both conditions use `workspace-write` with
approval policy `never`. Disable inherited apps, plugins, browsing, skills, and
other MCP servers consistently; preserve the resolved configuration and actual
tool traces. The official [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
and [MCP settings](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) describe
these controls. These restrictions keep unrelated integrations out of the paired
comparison, and do not imply that every possible evaluation-file access is
prevented by a security boundary.

The MCP tool defaults to fast dense retrieval, static dependency context, a
4,096-token compact-text budget, and no H2O reranking. No calibrated rejection
policy or H2O server is configured. The agent can choose exposed retrieval and
budget arguments; publish those actual choices and errors rather than describing
every call as a default call. Its service is fixed to that run's checkout
and source root, indexes the same seeded saved files the agent sees, and follows
saved edits. Index startup and warmup are excluded from warmed agent-completion
comparisons but measured and published separately. Service setup is a real
integration cost; a warm result does not represent first-use latency.

Serialize executions on this Mac. Record the exact resolved CLI version, model,
reasoning effort, authentication mode without credentials, runtime dependencies,
host hardware, run identifiers, agent settings, and any CLI configuration
overrides. Preserve the JSON event stream and agent-created patch for every run.
Do not expose authentication tokens, private configuration, or environment
secrets in public artifacts.

## Correctness, time, and usage

A completed repair passes every withheld behavior check against the final
checkout. Report check names and outcomes, agent exit status, final patch, and
any invalid execution separately. The correctness claim is limited to these
checks; it is not proof that every upstream behavior is correct. Record any
ordinary repository tests run by the agent, but apply the same withheld checks
to both conditions after completion.

Measure wall-clock durations with a monotonic clock. Preserve service
startup/indexing, complete agent-process elapsed time, and post-run grading time
as separate quantities. The agent duration includes its own initialization,
MCP connection startup, tool calls, and testing. Do not describe a model-only
response time or summed tool durations as end-to-end completion time. A failed
or timed-out run retains its elapsed time and any reported usage.

Use the agent CLI's terminal usage event for actual `input_tokens`,
`cached_input_tokens`, and `output_tokens`; preserve the original event rather
than reconstructing usage from transcript text. The official
[non-interactive mode documentation](https://learn.chatgpt.com/docs/non-interactive-mode)
describes the JSON event stream and terminal usage event. Preserve any reported
`cache_write_input_tokens` and `reasoning_output_tokens` as well. Cached input is
a subset of input, and reasoning output is a subset of output; cache and
reasoning subcategories must not be added a second time. Define total tokens as
input plus output, and additionally show uncached input as input minus cached
input.
Report cache behavior because paired order and provider caching can affect
cost and latency. Neither local excerpt tokens nor these counts alone establish
a dollar bill; this experiment makes no dollar-cost claim without verified
billing data.

If the CLI produces multiple terminal turns, explicitly reconcile their usage
semantics before aggregating, avoiding cumulative-event double counting. Missing
or incomplete usage stays unavailable; it is not zero. Tool-call counts come
from recorded events, with ordinary shell/file and MCP calls shown separately.
Whether the host includes both MCP text and structured content in its prompt is
part of actual reported usage, not corrected by the local counter.

## Failures and comparisons

The first pilot has no automatic retry or resumed agent execution. A 600-second
timeout ends that run. Do not silently retry failed tasks until they succeed.
Preserve original attempts, their elapsed time,
usage when available, and the reason for any subsequent attempt. Infrastructure
failures, agent timeouts, malformed usage events, and failing repairs remain in
the published run inventory. An additional attempt is separately labeled and
its resource use is retained.

Publish task-level paired deltas for correctness, elapsed time, input, cached
input, output, and total tokens, plus aggregate counts and medians. Report all
runs, and identify a both-correct subset separately when comparing resource use
at comparable completion. A token saving caused by an incorrect or prematurely
terminated repair is not evidence of useful efficiency. Show how many MCP runs
actually called `search_code`; availability is the assigned condition, and
non-use is an outcome rather than a reason to exclude the pair.

Six task pairs with one run each provide a small descriptive pilot. They cannot
establish statistical significance, stability across random agent runs, broad
generalization, or the project's aspirational 30% token-saving target. Treat
the raw outcomes and failures as evidence for the next study, and freeze fresh
tasks before tuning from this pilot.

## Reproduction

Use the pinned Mac runtime and Qwen checkpoint described in the README, fetch
the three pinned corpora, and authenticate the installed Codex CLI with ChatGPT.
The agent uses a hosted coding model; Qwen search runs locally. Install the common
offline test dependencies before freezing, without changing either trial's
environment later:

```bash
.cache/bootstrap/bin/uv pip install --python .venv/bin/python -r requirements-workflows.lock
.venv/bin/python -m semantic_search.workflows freeze \
  --output results/local/workflow-run --work-root .cache/workflows/workflow-run
.venv/bin/python -m semantic_search.workflows run --output results/local/workflow-run
```

The runner checks every frozen runtime/task hash, creates fresh committed seeded
fixtures, and refuses an existing run directory. It provides the checkout's
package parent through `PYTHONPATH` in both conditions so tests exercise the
fixture rather than globally installed Requests/Click/Starlette. The withheld
checks disable bytecode caching and require the exact declared all-green test
counts. JSON events and patches are published with local paths redacted;
reasoning events and unredacted raw logs stay in ignored evaluator storage.
