# Saved-outcome activity details

This read-only analysis follows the independent audit. It calls no selector,
model or retrieval API and does not change the published outcomes or gates.

Of 393 saved semantic proposals, 307 already equal coverage. The guard accepts
those plus 31 genuinely changed proposals, and rejects the remaining 55 changed
proposals. The previous 99 exchanges become 34 retained and 65 blocked exchanges.
All accepted final responses equal their saved v1 proposal exactly; all rejected
ones equal their original coverage response exactly.

The three lost implementations from semantic v1 are restored in full: Starlette's
exception registration/dispatch behavior and Zod's metadata attachment/registry
behavior become complete again. HTTPX's JSON-header preparation group returns,
while that query still lacks other required groups. Each lost implementation has
an explicit state write. Rejection can also protect unrelated uncertain rows;
this experiment does not attribute every rejected proposal to the known losses.

Compared with semantic v1, the guarded result also gives up three partial group
gains: Jinja filename autoescape (hybrid, group 3), HTTPX stacked decoding
(group 4), and TanStack invalidation (hybrid, group 2). Those behaviors remain
incomplete in both arms. This is why the aggregate group count stays 418 even
though the guarded result restores three groups and adds two complete
occurrences. The hard preservation controls are original legacy and coverage,
not the previous failed semantic variant. There are no individual losses against
either hard control.

The two additional complete occurrences versus coverage represent the same
Jinja include/context behavior in dense and hybrid. This is one distinct new
behavior. No more independent successes are inferred from repeated conditions.
The largest outputs reach the original limits (8 results, 12,000 source
characters, 4,096 compact tokens), with no overruns or budget changes.
