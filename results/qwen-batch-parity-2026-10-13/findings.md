# Initial batching parity: rejected

The first real batch implementation failed the fixed numerical gate. B2, B4 and
B8 remain excluded from setup selection; tolerances were not relaxed. All
36 planned scoring requests completed, comprising 1,664 diagnostic pair scores,
plus 32 separately recorded warmup pairs. The worker evaluated 807 actual
backbone forwards in total. Parity inputs retain the complete original candidate
peer groups from eight observed development repositories and a synthetic mixed
length/Unicode/clipping set. HTTPX retrieval has not been observed.

| Mode | Maximum yes-logit error | No-logit error | Difference error | Binary-signal error | Inversions beyond 0.25 margin |
| --- | ---: | ---: | ---: | ---: | ---: |
| B1 reference | 0 | 0 | 0 | 0 | 0 |
| B2 | 0.375 | 0.3125 | 0.25 | 0.05443016 | 2 |
| B4 | 0.375 | 0.3125 | 0.25 | 0.05443016 | 2 |
| B8 | 0.375 | 0.3125 | 0.25 | 0.05443016 | 2 |

Every larger batch fails at least one tolerance in every input set. Koa and
Requests each have a ranking inversion beyond the frozen near-tie band. Right
padding has a mathematical causal-isolation argument, but that does not establish
numerical agreement across BF16 kernels. These outputs motivate a separately
frozen precision decomposition; they do not establish the source of the error.

The supervisor also failed its termination-status check. It sent TERM, then a
group existence probe raised EPERM before the terminated child was reaped. The
original failed supervisor receipt is preserved. The worker's own stop receipt
records a closed listener and eight active allocator bytes; subsequent read-only
checks found no owned PIDs/groups/listener/token. Those later observations do not
convert the original supervised run into success. The supervisor was repaired
with polling/reaping before probes and a bounded retry, without treating live or
persistent permission errors as disappearance. Regression tests cover that race.

The independent audit verified all constructed token IDs, source prefixes,
807 actual forwards, 1,696 logical pair scores, native group layouts, scalar
math and the numerical gate failures. It records `diagnostics_verified: true`
and `study_qualified: false`, with `checks_passed: false` and
`clean_supervised_run: false`. All 43 original frozen implementation files are
retained byte-for-byte in compressed source snapshots before subsequent repairs.

See [independent findings](independent-findings.md),
[full diagnostic audit](independent-audit.json), [original supervisor](supervisor.json)
and [later cleanup observations](post-supervisor-cleanup.json). No warm retrieval
latency or coding/provider-token benefit is measured by this parity run.
