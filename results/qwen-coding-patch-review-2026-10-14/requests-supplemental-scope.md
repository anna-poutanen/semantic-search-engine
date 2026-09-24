# Scope of the cached redirect-history hook probe

This is supplemental review after coding output, not a change to the frozen
18-case Requests grade or its preflight controls. The task's unchanged prompt
requires: "Apply the budget to each response independently, including intermediate
and final redirects" and includes "cached responses returned by response hooks".
It also requires stopping a crossing chunk before it reaches the reader.

The evaluator uses public Session and response-hook APIs with deterministic
in-memory adapters. An unlimited Session creates a real two-response redirect
chain: an eight-byte intermediate body and a two-byte final body are cached.
The first hook of a separate four-byte-budget request returns that final Response,
including its genuine redirect history. A later hook tries to iterate the
intermediate cached response. The expected inherited per-response budget rejects
that eight-byte cached body before any bytes reach the later hook.

This scenario does not ask the bounded request to retroactively prevent the
earlier unlimited transport. It checks whether the complete cached response chain
returned by the replacement hook is bounded before subsequent hooks consume it.
There are no sockets, external servers, private budget-field assignments or model
calls. All probes execute in new evaluator copies of finished source snapshots.

Completed attempts 01 and 02 both yield `abcd` and `efgh` to the later hook, then
raise `ResponseTooLarge(limit=4, received=8)` when Session.send eventually attaches
the limit to history. The source attaches limits to history after the entire
response-hook sequence; attempt 01 additionally protects the replacement final
response between callbacks, while attempt 02 does not. Thus ordinary final-body
hook protection and the historical-body timing gap are distinct observations.

The same unchanged probe on a fresh copy of the frozen strict reference also
yields both chunks and never raises for the historical body. This is a reference
and checker coverage gap: the frozen hook probes cover live/cached replacement
final bodies, not replacement-history consumption inside the next hook. It is
not grounds to silently alter original grades, select successful trials or add
feedback to running agents. The exact scenario and reference result are retained
so readers can assess its interpretation independently. Remaining completed
Requests attempts receive the same unchanged probe.
