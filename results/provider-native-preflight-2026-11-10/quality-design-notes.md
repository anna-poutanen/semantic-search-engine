# Future quality experiment: source-transform identity and missing admissions

Prepared 2026-11-10 as a source-only design note. This does not change the
current native performance phase, its fixed quality rule, old protocols,
production behavior or release assets. No new graph, guard, selector, model or
quality outcome was executed to prepare it. Attrs is already observed
development evidence; no fresh repository queries or labels were inspected.

## Source authority and the two separate problems

The inspected attrs checkout is pinned to
`3dca08ce5bbe673d7df25f44f3dda92505d1043d`. Its
`src/attr/_make.py` SHA-256 remains
`94150f3e6c6203505e1f307a3a51e808487efad56f335a335cef1e5ce6ba8387`.
The earlier published source diagnosis and failed dependency experiment remain
the authorities for already observed omissions; nothing below is a tested fix.

1. **Binding/preservation:** the formatter was already delivered in hybrid,
   but the terminal annotation guard could not recognize its caller/callee
   relationship because the class name is rebound and one caller forwards its
   receiver. A stronger static summary or a different literal source-role
   predicate could permit protection of already delivered implementations.
2. **Admission:** dense retrieved the formatter at raw rank 11 but coverage
   omitted it. Returning either exact coverage or an exact saved parent proposal
   cannot introduce that missing implementation. A future selector must consider
   admitting an already retrieved window within the unchanged output budget.

These problems require separate evidence. Better class resolution alone is not
a dense-admission mechanism, and preserving a source role does not prove that
it implements a query.

## What the actual transformation helpers establish

`Converter` is declared at line 2802 and rebound at 2918–2920 through a nested
sequence of three calls. The relevant helper bodies are:

| Helper source span | Direct class attribute writes | Final return |
| --- | --- | --- |
| `_add_hash`, 1628–1639 | `cls.__hash__` at 1638 | `cls` at 1639 |
| `_add_eq`, 1747–1761 | `cls.__eq__`, `cls.__ne__` at 1758–1759 | `cls` at 1761 |
| `_add_repr`, 1821–1833 | `cls.__repr__` at 1832 | `cls` at 1833 |

None directly reassigns its local `cls` parameter. The latter two conditionally
read class attribute metadata when `attrs` is absent. All three construct source
and invoke `_compile_and_eval`; that helper at 212–223 calls `compile` and then
`eval` with supplied namespaces. Other source calls receive the class or its
metadata while constructing scripts and diagnostic filenames.

This is useful evidence for a generic **normal-return alias** summary: under a
bounded lexical semantics, successful return of the unchanged local parameter
denotes the input reference. It is not, by itself, a certificate that every class
method definition survives the call. Object identity and object contents are
different properties. The direct assignments above do not target the formatter,
but opaque calls, generated code, namespace aliases and descriptors can have
additional effects. Source-level `return cls` must not become a blanket exemption
for arbitrary class-transform calls.

## Option A: certified return origin plus method-preserving effects

This is the principled extension of the annotation resolver, and the larger one.
A prospective rule could accept a reassignment chain only if all of these facts
are established from the captured source, without running it:

1. Resolve each transformer to one ordinary synchronous source function through
   unambiguous module/import bindings. Exclude decorators, dynamic callable
   objects, unresolved overloads, inheritance dispatch and mutable aliases.
2. Bind arguments statically, including ordinary keyword parameters and literal
   defaults. Reject unknown `*args`/`**kwargs`, ambiguous alias origins and
   unsupported call forms. Compose nested transformations by reference origin,
   independently of their function or class names.
3. On every supported normal exit, the return value has the same distinguished
   parameter origin. Reject implicit fallthrough, replacement objects, generator
   or async functions, uncertain `finally` behavior, rebinding/deletion,
   global/nonlocal writes and namespace/frame operations that could invalidate
   the local-origin analysis. Unsupported control flow declines a summary.
4. Separately summarize writes and escapes of that origin, including transitive
   helper calls. Writes to a statically known *other* attribute may preserve the
   requested method. Writes to that method, dynamic attribute names, dispatch
   hooks, bases/metaclass changes, aliases escaping to unknown callees or an
   unmodeled effect decline method preservation. A return-origin summary alone
   does not satisfy this step.
5. Preserve exact module/class/method source identities through the entire
   rebinding sequence and later module mutations. Emit source-bound witnesses
   for argument binding, each return origin, each permitted write and every
   transitive effect summary, not merely a final method-name match.

The first implementation should use a fixed, small abstract language and bounded
call depth/node budget, declining recursion and unknown effects. It must not
execute source to learn a return value or insert helper-name exceptions after
seeing failures.

### Generated code is the difficult boundary

The actual helpers exercise this boundary, so a toy `return cls` checker would
not establish a complete proof for them. A conservative code-execution summary
could be allowed only when static analysis proves the complete generated
program and its namespaces satisfy an explicitly frozen grammar. One narrow
example is an isolated, freshly created dictionary receiving plain function
definitions with no executing decorators, defaults or annotations and no other
module-level effects. Function bodies are deferred, but definitions can execute
defaults/decorators/annotations immediately. Those distinctions must be modeled.

The actual builders assemble code from templates, control flow and metadata such
as attribute names. Unknown substitutions can inject syntax or execute arbitrary
formatting behavior. A sound extension would need bounded value/effect analysis
through those builders, or decline them. Parsing an invented safe placeholder
and assuming every substitution behaves like it is insufficient. Even isolated
globals do not make arbitrary executed code harmless: imports and reflective
access can reach external state. The analysis must establish the permitted
program shape, not rely on the word `compile` or a new dictionary alone.

Consequently, the source suggests a possible return-origin proof but does not
justify claiming that this small extension already resolves the real formatter.
Resolving the full effect closure may be too large for the next quality step.

## Option B: a literal returned-template role, without type resolution

A smaller independent hypothesis protects an already delivered full definition
when its literal Python AST has a `return` value that is a `JoinedStr` containing
an actual evaluated call in a `FormattedValue`. Require a direct call expression
inside the returned template, not text that merely resembles a call. Bind the
complete definition, return expression and call bytes to canonical source and
provenance. Terminally reject a proposal changing that row, just as the existing
state-write role guard preserves its rows.

This predicate can recognize the formatter's returned templates and the
assignment helpers without resolving the annotated class through transformations
or following an escaped receiver. It therefore addresses an actual structural
limitation of the previous annotation guard. It remains a **returned-template
role**, not a detector of all code generators, runtime mutations or required
behavior. A user-message formatter may also satisfy it; a generator that builds
strings in a list and returns a join may not. Those are explicit false-positive
and coverage limits, not exceptions to fix by repository names.

This is the smallest concrete candidate worth a separate, prospectively frozen
development experiment. It could protect existing hybrid evidence while still
failing the complete regression gate by overprotecting other baseline rows. It
cannot repair the dense omission. Do not combine it with new admission rules in
the same first experiment or claim that a positive source match predicts a gain.

## Option C: a separately frozen budgeted dependency admission

Dense needs an admission proposal, not a stronger terminal rejection rule. A
generic future proposal could consider an already delivered caller with a
source-bound call and an absent callee that is already present in the original
raw pool. Candidate construction would be independent of evaluator labels:

- Use a certified declared dependency from Option A, or clearly label a weaker
  structural candidate as uncertain. A globally unique method-name match does
  not prove dispatch; it must not be promoted to a verified edge just because
  the receiver is unresolved.
- Consider caller/callee closure as a joint source unit. Preserve existing
  anchors, actual dependencies/owners, explicit state writes, uncertain source
  and any prospectively chosen role protections. Reject a removal that breaks
  an already delivered certified pair.
- Admit only original full windows with their original source/provenance/scores;
  no inference, new retrieval, fabricated runtime methods, stitching or symbol
  deduplication that discards distinct definitions. Use fixed raw-rank ordering,
  a fixed small number of trials and deterministic ties, chosen before outcomes.
- Replace only an unprotected victim and count the actual final compact payload
  against eight excerpts, 12,000 source characters and 4,096 tokens. If no victim
  is available, decline. Character/token slack does not create a ninth row.
- Treat newly delivered callsite coverage as a structural objective, not proof
  of task coverage. Per-facet maximum embedding similarity can overlook a
  necessary complementary implementation; replacing it with raw dependency
  counts can likewise favor irrelevant helpers. Both require full quality gates.

This is a proposal design, not an outcome or implementation recommendation for
the native performance phase. If all rows are protected, dense admission is
impossible without changing either selection or protection. That conflict must
be measured honestly; it cannot be hidden by larger budgets or gold-aware victim
selection. Source-lineage context through a class transform can be useful as an
explicitly uncertain retrieval hint, but it is not a substitute for an identity
or method-preservation certificate.

## Required invented tests before any observed candidate outcomes

| Area | Positive and adversarial obligations |
| --- | --- |
| Return origin | Direct return of an unchanged parameter; safe local alias chains; branches returning the same origin; replacement class/factory; reassigned/deleted parameter; a different branch result; fallthrough; exception/finally return override; async/generator forms. |
| Binding and dispatch | Renamed functions/classes/parameters; imports/reexports and aliases; keyword/default argument binding; shadowed/reassigned transformers; module export mutation; duplicate definitions; dynamic callable/decorator; custom metaclass/descriptors; inheritance and dispatch-hook changes. |
| Effects | Literal writes to another method versus the called method; dynamic `setattr`/delete; receiver aliases in containers or closures; unknown calls retaining an alias; global/nonlocal/frame writes; transitive helper replacement; recursive summaries; fixed analysis-budget exhaustion. |
| Generated programs | Plain isolated function definition; executing default/decorator/annotation; top-level assignment/import/call; shared globals/locals; unknown dictionaries; interpolation containing quotes/newlines/statement injection; unknown format conversion; deferred body versus immediate definition effects. |
| Template role | Real evaluated call in returned f-string; renamed identifiers; comments/docstrings and call-looking text excluded; non-call formatted values excluded; nested lexical functions kept separate; ordinary message formatter qualifying without a relevance claim; join-based generator outside the initial scope. |
| Admissions | Full context with no legal victim; exact character/token boundary and one-over failure; duplicated IDs; same-named distinct classes; multiple windows of one definition; clipped/partial callees; cross-file source/provenance mismatch; changed scores; absent raw candidate; broken existing caller/callee closure. |
| Source contracts | Literal UTF-8 byte/physical-line witnesses, CRLF/CR and Unicode, decorated canonical spans, source drift, typed metadata, cancellation identity, no execution/model/network/index access and no fallback to guessed facts. |

An invented example must test the stated general grammar. It must not encode the
real helper/class/query names as privileged cases or assert that a broad role
predicate somehow excludes an ordinary formatter that satisfies its syntax.

## Evaluation and separation from performance work

Before implementing any option, choose exactly one candidate scope, its proof
assumptions, unsupported cases, deterministic tie rules and quality gate. Commit
the code/tests/protocol and complete observed operands before new outcomes.
Recheck all existing observed repositories and individual group, complete and
single-file preservation against the declared controls, including prior-role.
Require an incremental distinct multi-file gain; retained earlier gains are not
new progress. Keep technical auditing and quality qualification separate.

The present provider-native phase must keep its quality rule frozen and measure
only its already chosen performance implementation. A future quality rule would
need its own native validation and a newly untouched repository after freezing;
attrs cannot supply fresh confirmation again. None of these notes establishes
retrieval improvement, native speedup, production readiness or agent-token
savings.
