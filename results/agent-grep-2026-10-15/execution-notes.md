# Execution interruptions and interpretation

The frozen 16-attempt inventory is unchanged. No coding conversation was resumed,
and no attempted identity was retried or replaced.

- Attempt 05, the first Starlette baseline, emitted repeated provider connection
  failures across an overnight sleep/connection gap. The evaluator signaled the
  owned runner through its capture guard. Its interrupted state, unknown usage,
  unqualified timing and original outcome remain in the inventory.
- After a passing transport check under the identical runtime, continuation-v1
  froze and executed only unattempted identities 06 onward. Attempts 06–12
  completed. During attempt 13, the second Starlette search-assisted run, the
  one-second clock monitor detected approximately 957 seconds of wall/performance
  drift and stopped the owned runner. That interrupted outcome is also retained.
- A further successful transport check preceded continuation-v2, which froze
  only the three remaining unattempted identities 14–16. Each continuation
  preserves its previous results, completion, summary and audit byte-for-byte
  in the corresponding prior-segment directory.

Both Starlette timing pairs contain an infrastructure interruption. They cannot
establish a comparative time/token effect, and the interrupted completions must
not be attributed to the search method. Valid matched efficiency comparisons
therefore cover Requests, Click and HTTPX. Full-inventory usage totals remain
unknown for both conditions because each has an interrupted turn without a
completed-turn usage receipt.

Manual review found one scoped Click compatibility regression missing from the
fixed checker. The separately executable supplemental probe applies the same
case to all four completed Click candidates and both controls. The original
fixed grades remain unchanged. Review-adjusted comparisons are explicitly
post-output diagnostics and must not replace or conceal the full inventory.
