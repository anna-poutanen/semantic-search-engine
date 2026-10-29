# Repeated multi-file coding workflows

This is a new exploratory study of four authored feature requests in pinned Requests, Click, and Starlette code. The tasks start from unmodified upstream source and expose only public API contracts to the coding agent. The original seeded-regression pilot and MCP-first follow-up remain unchanged and are not pooled with this study.

The canonical task manifest is `benchmarks/coding_workflows_multifile.json`. It records corpus commits, public prompts, independent behavioral checker hashes, unmodified-baseline and reference checker outcomes, original integration commands, and original upstream test hashes. Task/reference preparation and all checks are reviewed before any task execution. Private reference solutions and external checker implementation stay in evaluator storage, outside the agent checkout. This separation is an evaluation practice, not a security boundary.

## Conditions and schedule

There are four tasks × two conditions × two repetitions: sixteen trials and eight paired comparisons. Conditions are ordinary repository tooling (`baseline`) and the same tooling plus the registered local `semantic_search.search_code` MCP server (`mcp`). Each trial receives a fresh checkout and conversation. Every MCP trial also receives a fresh SQLite index and Qwen instance, then tears them down before the next trial; GPU work remains serial.

In repetition one, task positions alternate baseline-first and MCP-first. Repetition two reverses the order for each task. This gives four baseline-first and four MCP-first pairs. Task order and every trial identity are frozen before running. No retries, resumed conversations, task substitutions, or discarded failures are permitted. Timeouts, startup errors, transport errors, grading failures, and unavailable usage measurements stay in the sixteen-trial inventory.

Both conditions use the same `gpt-6.1-sol` model, `ultra` reasoning effort, service tier, sandbox, common developer/user instructions, Python environment, history output cap, timeout, ordinary tools, and testing guidance. The MCP condition adds the one registered tool; the baseline is not prevented from searching or reading source with ordinary tools. The task prompt does not provide implementation paths or reference edits.

The identical common workflow instruction says:

> When search_code is available, use it before broad source reads to locate the relevant implementations. Start with max_results=5 and token_budget=2000; use its returned excerpts as source reads, then read only narrow missing definitions or line ranges needed to implement and verify the change. If it is unavailable, use ordinary repository search and reads. Both paths may use all ordinary repository tools. Run focused new tests first, then relevant existing integration tests; summarize failures with --tb=short instead of dumping large logs.

These instructions prescribe a conditional workflow; they do not measure spontaneous tool adoption. Actual tool calls and arguments are outcomes. The agent can request different search arguments, so a tool's defaults or the prompt's suggested budget are not reported as the actual values without inspecting receipts.

## Common runtime and transport preflight

The agent is isolated from the running desktop task by `--no-daemon`, `--ignore-user-config`, `--ignore-rules`, `--strict-config`, and `--ephemeral`, along with `agents.enabled=false`, `features.multi_agent=false`, and `features.multi_agent_v2=false`. An identical developer instruction requests a single agent. Known inherited app/session bridge variable names are removed from child environments; `HOME`, `CODEX_HOME`, ordinary `PATH`, and normal saved ChatGPT authentication remain available. Credential values are never copied into study artifacts. API-key/access-token environment variables are removed rather than printed. The frozen Python bin is prepended consistently so ordinary test/lint commands resolve to the shared environment.

Those flags are configuration, not proof that the alpha CLI removed every tool. A local `debug prompt-input` rendering and protocol/schema inspection can establish only what they actually show. A separately recorded hosted transport probe must complete a registered MCP call with an independent HTTP receipt before the sixteen trials are frozen. A model's own list of available tools is not accepted as tool-catalog evidence. Any delegation attempts or feature failures are retained and described as observed behavior; absence of an attempt is not proof of removal.

The probe uses an ignored tiny lexical fixture and fake model clients that fail if any Qwen/H2O inference is attempted. It uses the same model and common runtime. It must first make exactly one bounded lexical MCP call with the frozen arguments, then execute exactly one prewritten `loopback_smoke.py` through the frozen Python interpreter. The script binds an ephemeral loopback port, connects to itself, reads its sentinel, and exits. The gate checks the completed tool payload against the independent HTTP receipt and nonempty source/hash/physical-line checks, checks the controlled command's exact arguments, order, sentinel, and exit code, and requires an unchanged fixture tree, no other commands or file edits, no local model calls, normal CLI completion, and reported usage. A successful tool status alone is insufficient. This is a diagnostic, not one of the sixteen coding trials. Publish its observed provider usage and elapsed time separately, including unsuccessful attempts. The explicit `probe` subcommand invokes the hosted agent; metadata validation and freezing do not.

Both conditions set `sandbox_workspace_write.network_access=true` so agents can run original local HTTP integration fixtures. This is a broad network permission, not a restriction to loopback. The identical prompt forbids external services and permits local fixtures, and captured commands remain available for audit. A CPU-only named sandbox profile verifies loopback binding/connection; the controlled command in the hosted probe additionally verifies that the actual exec configuration permits it. Neither check involves writing user-global configuration.

The installed shared runtime is pinned in `requirements-multifile.lock`. Trio is upgraded to `0.32.0`, the minimum declared by the installed AnyIO `4.15.1` Trio extra. The former `0.29.0` combination fails a direct cancellation check because AnyIO passes a cancellation reason to Trio's older incompatible method. Both async backends and full selected original integration tests are checked before freeze. Both conditions receive the precise common `PYTEST_ADDOPTS` warning filter:

```text
--tb=short -W "ignore:The anyio.abc.BlockingPortal alias is deprecated:DeprecationWarning" -W "ignore:The `cancellable=` keyword argument:DeprecationWarning"
```

These filter two known compatibility deprecations; they do not remove test cases, disable the Trio backend, or suppress unrelated warnings. The unmodified pinned Starlette HTTP/background/response/TestClient gate is tested under this common setup. Compatibility fixes and shared dependency changes define a new runtime; prior-study outcomes are not overwritten or reclassified.

The common `tool_output_token_limit=4096` bounds stored tool/function output in model history for both conditions. It is distinct from the search tool's own compact payload budget. The MCP adapter is explicitly launched with `--agent-text-only`, returning the exact compact JSON text without an additional structured output copy. Its default SDK mode remains compatible for other consumers. This changes representation; it does not establish that a host previously billed both copies or that the new mode saves a particular number of paid tokens.

## Setup, timing, and usage

The agent timeout is 900 seconds from CLI launch. Trial timestamps distinguish fixture extraction, baseline feature checking, local service/Qwen/index setup, agent execution, teardown, and independent grading. Service warmup is excluded from agent time and reported separately. Agent time includes adapter startup, hosted-model waits, local tool calls, and the harness's observation overhead. OS filesystem and provider prompt caches cannot be reset completely; balanced order and reported cached-token subsets make this limitation visible.

Timestamped CLI JSON events provide observed start/completion intervals for MCP and command items. These are event-reception intervals, not exact provider phase timings. A recording backend independently stores every HTTP search's request/settings, start/end timestamps, backend-call duration, compact-response hash, and saved tracked-source snapshots before and after the call. The source-copy overhead is recorded separately and remains part of the observed workflow. Receipts and source snapshots support checking that returned paths, line bounds, hashes, and truncation correspond to the actual candidate checkout, rather than relying on an agent's claim that it used the tool.

Actual hosted usage comes from `turn.completed`: gross tokens are reported input plus output. Cached input, cache-write input, and reasoning output are subsets, not extra tokens to add. Unavailable fields remain unavailable. The installed `codex exec --json` interface does not expose usage per underlying model response, so that field is explicitly null. The app-server has a different prospective usage-notification interface; it is not substituted into this study without a new protocol. Local `cl100k_base` excerpt counts are separately reported tool payload measurements, not provider billing estimates.

Report all eight pairs, condition totals/medians, per-task repetition outcomes, failures, and both-completed-and-independently-correct comparisons. If any trial lacks reported usage, the complete-inventory token total stays unavailable; measured subtotals and missing counts are shown. Diagnostic/probe costs remain separate from trial costs.

## Independent grading and test integrity

Never grade from the agent's final text, test-pass claim, or a test it wrote. After each agent exits, run the frozen external public-behavior checker and the frozen original upstream integration commands. Selected behavioral checks must match the declared reference outcome exactly. Each compatibility command must exit zero and match its frozen counts for passed, skipped, failed, errors, xfailed, xpassed, and deselected cases; skipping all tests cannot satisfy the gate. Existing upstream tests run against the candidate implementation in an evaluator-only copy where original tracked tests, fixtures, and test configuration (`pyproject.toml`, `pytest.ini`, `setup.cfg`, `tox.ini`) are restored from the pinned corpus, and newly added files with those configuration names are removed. This keeps agent test edits from weakening the original compatibility gate. The agent's actual checkout and patch remain preserved unchanged.

Hash all original tracked tests and test configuration before execution and compare them afterward. Deleted or changed originals and added configuration are listed for review; a change is not automatically evidence of weakening because it may add a legitimate regression test. The common prompt recommends new regression test files and preserving originals. Newly added tests and source files remain in the captured patch. Both frozen behavioral success and original integration success are required for `correct`; `completed_correct` additionally requires normal CLI completion, no timeout, and no recorded agent/infrastructure failure. Individual checks, skip/xfail counts, and infrastructure failures are reported so a coarse success flag cannot hide them. This selected gate is stronger than the previous pilot's checks but still is not a proof of all library behavior.

## Freeze and artifacts

Before the transport probe, freeze the common model/effort, full Python package versions, executable/version/hash, runtime source hashes, embedding revision/settings without loading weights, adapter mode, environment policy, command construction, and this protocol. After the probe and completed independent task preflight, freeze the four task/check/reference hashes, original integration commands/test hashes, exact sixteen-trial schedule, common user-prompt hashes, successful diagnostic evidence, and common runtime provenance. Check these hashes and package versions before every trial and after the run. A code, dependency, task, setting, or grading change after freeze requires a new named study.

Raw events and reasoning remain in ignored evaluator storage. Publish every trial's non-reasoning events, event timestamps, full received tool responses, independent receipts, source snapshot manifests, command configuration, final answer, patch, source/test hash inventory, usage, phase timings, and grading results with local paths and credential-like patterns redacted. Published reasoning is excluded; raw-log hashes identify preserved evaluator records. Snapshots containing source remain in ignored storage, with manifests and relevant receipts available for audit. This does not promise isolation from a deliberately adversarial agent that ignores the repository-only instruction.

The new result compares an improved workflow package against ordinary repository search under the same new common setup. It does not isolate effects of tool guidance, output representation, history caps, dependency updates, or environment cleanup individually. Earlier analysis found a smaller raw tool-output proxy despite greater reported hosted tokens, so environment noise alone is not asserted to explain the earlier paired difference. Four authored feature tasks and two repetitions are a small exploratory sample, not statistical evidence of broad savings or the project's 30% target.

Primary runtime references: [official non-interactive CLI documentation](https://learn.chatgpt.com/docs/non-interactive-mode), [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference), [debug prompt-input documentation](https://learn.chatgpt.com/docs/developer-commands?surface=cli), and [AnyIO 4.15.1 release metadata](https://pypi.org/project/anyio/4.15.1/). Installed CLI help/schema and pinned-package metadata are retained as local evidence because the bundled alpha can differ from general documentation.

Commands are explicit and separate:

```sh
# CPU metadata only; no hosted/GPU calls.
.venv/bin/python scripts/run_multifile_workflows.py validate
.venv/bin/python scripts/run_multifile_workflows.py freeze-common \
  --output results/local/multifile-common-runtime

# Hosted transport diagnostic; local model inference is guarded to fail.
.venv/bin/python scripts/run_multifile_workflows.py probe \
  --common-config results/local/multifile-common-runtime/common-runtime.json \
  --output results/local/multifile-transport-probe \
  --work-root .cache/workflows/multifile-transport-probe

# Freeze only after source/check/runtime and diagnostic reviews are complete.
.venv/bin/python scripts/run_multifile_workflows.py freeze \
  --common-config results/local/multifile-common-runtime/common-runtime.json \
  --probe-result results/local/multifile-transport-probe/result.json \
  --output results/local/multifile-workflows \
  --work-root .cache/workflows/multifile-workflows

# Starts the sixteen hosted trials and serial local Qwen services.
.venv/bin/python scripts/run_multifile_workflows.py run \
  --output results/local/multifile-workflows
```
