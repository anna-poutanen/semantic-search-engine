# Experiments

- [MCP-first coding workflow follow-up, October 12, 2026](coding-workflows-mcp-first-2026-10-12/report.md):
  three new paired executions of reused tasks with identical conditional
  search-first instructions. All MCP agents use the tool; both conditions pass
  the selected checks. MCP uses 7.0% more total tokens and 14.8% more agent time
  over this workload. Both Click fixes retain the atomic commit-error defect.
  This follow-up is reported separately from the availability pilot.
- [Coding workflow availability pilot, October 12, 2026](coding-workflows-2026-10-12/report.md):
  six paired repairs in pinned Requests, Click, and Starlette codebases. Both
  conditions pass all selected checks, with actual provider token usage and
  completion time recorded. None of the six MCP-assigned agents called the
  tool, so this pilot cannot attribute differences to retrieval. Supplemental
  review finds false success on atomic commit errors in both Click fixes.
- [Retrieval quality, October 12, 2026](retrieval-quality-2026-10-12/findings.md):
  106 frozen queries across five repositories. Dense context delivers complete
  evidence for 83/88 positive behaviors overall and 18/20 Rich holdout positives;
  multi-file misses and optional abstention errors remain published.
- [Persistent index profile, October 12, 2026](index-profile-2026-10-12/report.md):
  10,000 synthetic windows, exact SQLite cosine P95 19.63 ms and durable reuse.
  This is a CPU index profile, not a real-model indexing benchmark at that scale.
- [Requests service, October 12, 2026](requests-service-2026-10-12/report.md):
  a frozen second-repository experiment with 16 positive and four absent queries,
  actual HTTP latency, Qwen retrieval and optional H2O shortlist comparisons.
- [Qwen and shortlist sweep, October 12, 2026](qwen-hybrid-shortlist-2026-10-12/report.md):
  dense retrieval recovers all twelve Flask implementation windows; smaller
  H2O shortlists expose the coverage and latency tradeoff.
- [Flask / H2O q8, October 12, 2026](flask-h2o-q8-2026-10-12/report.md):
  M4 MacBook Pro, 24 GB RAM, frozen developer-authored dataset. Correct first
  result increased from 8/12 to 11/12 using the same ten BM25 candidates. The
  scorer preferred the correct implementation over two labeled decoys on all
  12 diagnostic queries. Warm search median was 19.3 seconds; peak facade physical
  footprint was about 7.5 GB according to macOS.

Fast Qwen dense retrieval is the current default. H2O improves ordering in the
early pilots but does not meet the five-second reranked P95 aspiration. Actual
agent usage is now measured; this first active-use follow-up does not establish
reproducible token savings from retrieval.

Reports link their frozen inputs, raw measurements, source excerpts or patches,
and independent checks. Agent event streams omit reasoning events and redact
local paths; the actual terminal usage fields are retained. Local repeats should
use `results/local/<new-run>` so development output does not replace frozen runs.

The [runtime transport preflight](workflow-runtime-preflight-2026-10-12/result.json)
(11.05 seconds, 20,927 actual tokens) and
[explicit MCP exposure diagnostic](mcp-exposure-diagnostic-2026-10-12/diagnostic.json)
(16.61 seconds, 34,404 actual tokens) are separate hosted-agent checks, excluded
from coding-task correctness and paired performance. The complete inventory is
18 coding executions and these two diagnostic executions; there are no coding
retries. This inventory excludes the orchestrating conversation's own usage.
