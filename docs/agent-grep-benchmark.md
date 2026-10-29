# Fast semantic search versus ordinary repository tools

This prospectively frozen study asks whether the current local search helps a
coding agent complete maintenance tasks. It compares ordinary repository tools
(including grep/rg, source reads, edits and tests) with those same tools plus the
current dense Qwen search through MCP. Neither condition uses a reranker.

The four tasks are new source-authored public API contracts: Requests session
timeout defaults, Click choice aliases, Starlette background-task continuation,
and HTTPX quoted-comma header parsing. These are controlled, realistic maintenance
scenarios, not claims to be sampled production issues. Repositories are previously
observed; tasks were selected before any retrieval query or candidate coding
output. They span a parser bug and three API extensions at different complexity.
No task is chosen or removed based on which search method succeeds.

Each task starts from its pinned upstream Git revision, with a fresh checkout and
agent conversation. Evaluation checks and reference implementations remain outside
the candidate checkout and the search index. This is an evaluation convention,
not a security boundary. The identical prompt forbids reading parent/evaluator
directories, external services, dependency installation, delegation, and commits.

There are four tasks × two conditions × two repetitions: 16 attempts, eight pairs.
The order alternates by task and reverses in repetition two. Model, reasoning
effort, timeout, environment, tool output cap, and prompts are identical. The
existing harness uses gpt-6.1-sol with ultra effort and a 900-second limit. The
search condition is prompted to use search_code before broad reads, with dense
retrieval and reranking disabled. This measures a prompted search-assisted
workflow, not spontaneous adoption. Baseline agents may use all ordinary tools.
Actual calls and overrides are retained rather than inferred from availability.

Behavioral checks cover only stated public requirements. Before freezing, the
unmodified upstream must fail and an evaluator-only reference must pass every
behavioral method. Original upstream compatibility suites must pass both on
unmodified upstream and on the reference; exact test counts are frozen. During
grading, original tracked tests, fixtures and test configuration are restored
in a separate evaluator copy. Passing an agent-authored test is not correctness
evidence. All final patches are reviewed for defects outside the frozen checks.
Such findings supplement, and do not rewrite, frozen grades.

The initial HTTPX control exposed a missing optional zstandard dependency. Retain
that failed preflight, then use a separate environment with zstandard==0.23.0 and
read access to the existing pinned environment. No coding trial runs until all
controls and a transport probe pass under this common environment. Record the
runtime package closure and all implementation/checker/control hashes. Preparation
repairs occur before coding outputs; do not retry or resume any coding attempt.

Every search trial gets a fresh index, local Qwen initialization, warmup and owned
service. Setup precedes the agent timer and is reported separately. Agent time
includes hosted waits, MCP startup and all tools; full trial time additionally
includes fixture preparation, local model setup, teardown and grading. Keepawake
and wall/performance clock checks qualify timings; interruptions are retained.
Baseline setup is recorded as zero for the semantic-search service.

Provider-reported completed-turn input plus output is gross usage. Cached input
is an input subset; uncached input is input minus cached input. Missing or
interrupted-turn usage is unknown, never zero. These counts are not billing
estimates. Report all 16 attempts first, then paired changes with contributing
identities; show all pairs, normally completed pairs, and both-correct pairs.
Never extrapolate a successful subset to the entire workload.

Raw CLI events, usage, patches, external grader logs, compatibility outcomes,
saved-source search receipts, snapshot archives, shutdown and clock evidence are
retained. Published event streams omit reasoning items. Credentials are neither
read nor copied; the CLI uses its existing normal authentication. New public
artifacts are local files until the user requests publication.

The primary outcome is normal completion with both behavior and compatibility
gates passing. Secondary outcomes are agent/full-trial time, gross/uncached
tokens and actual verified dense searches. Paired correctness wins and losses
are reported directly. Eight pairs across four authored tasks cannot establish
general superiority; cache reuse, task familiarity and hosted variation remain
limitations even with balanced order. Small timing/token differences are
descriptive. Changing production defaults requires evidence beyond this study.

Reproduction:

```sh
# Prepare controls and freeze after all controls and the transport probe pass.
BENCH_PY=.cache/agent-grep-2026-10-15/runtime-env/bin/python
$BENCH_PY scripts/prepare_agent_grep.py --controls /absolute/fresh/controls \
  --manifest /absolute/fresh/manifest.json
$BENCH_PY scripts/benchmark_agent_grep.py freeze --output /absolute/fresh/study \
  --work-root /absolute/fresh/work --common-config /absolute/common-runtime.json \
  --probe-result /absolute/transport/result.json
$BENCH_PY scripts/benchmark_agent_grep.py run --output /absolute/fresh/study
```

The runner currently binds the canonical manifest at benchmarks/agent_grep_tasks.json;
new repetitions require a new frozen output/work path and explicit new schedule,
not overwriting or selectively rerunning an existing study.
