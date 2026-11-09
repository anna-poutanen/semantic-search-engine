# Annotated-dependency diagnosis from the preserved first attempt

These are read-only diagnosis notes, prepared before the complete continuation
replay and independent audit. They use the first attempt's already saved graph
receipt, its frozen source bundle and the previously published native traces.
No new graph, guard, selector, retrieval or model call was executed to write
these notes. No future continuation outcomes were read. Final development
qualification remains pending the complete replay and independent audit.

## Evidence authority

- Preserved first attempt: `results/dependency-context-development-2026-11-09/`.
  It stopped after 396 stored outcomes on a timing-envelope comparison. The
  original failure and its artifacts were committed in `d16f876`.
- Saved graph: `replay/graphs.jsonl.gz`, attrs source bundle
  `sources_8281fea96781b509b94882ba289ab147f787cc79f1119cc4c56f81294236e821`.
- Frozen source: `prepared/source-bundles.jsonl.gz`, attrs revision
  `3dca08ce5bbe673d7df25f44f3dda92505d1043d`. The original raw SHA-256 for
  `src/attr/_make.py` is
  `94150f3e6c6203505e1f307a3a51e808487efad56f335a335cef1e5ce6ba8387`.
- Previously published native comparisons:
  `results/role-native-holdout-v2-2026-11-03/attempt/searches.jsonl.gz`.
  These are observed development evidence, not a new holdout for this rule.

## Attrs has zero accepted annotation edges

The first attempt's saved attrs graph contains **0 accepted edges**. Its 68
decline entries are parameter-level diagnostics, including primitive or
otherwise unsupported annotations; they are not 68 independently sampled
dependency opportunities:

| Saved decline reason | Entries |
| --- | ---: |
| `receiver_alias_escape_or_dynamic_attribute` | 41 |
| `nested_conditional_or_unindexed_caller` | 10 |
| `unsupported_annotation_or_variadic` | 10 |
| `annotation_unresolved_or_ambiguous` | 4 |
| `receiver_rebound_or_scope_declared` | 2 |
| `receiver_nested_scope` | 1 |

This result does not mean attrs lacks typed callers or method definitions. The
specific relationship motivating this experiment exists literally in the
source, but it is outside the fixed resolver's accepted binding conditions.

## The formatter relationship is explicit, but its class name is rebound

`_setattr_with_converter` is a complete definition at `_make.py:2013–2020`.
Its parameter annotation at line 2014 is `converter: Converter`, and line 2020
contains this actual Python method call inside an f-string expression:

```python
return f"_setattr('{attr_name}', {converter._fmt_converter_call(attr_name, value_var)})"
```

The formatter is the complete method `Converter._fmt_converter_call` at
`_make.py:2867–2882`. Its required original evidence interval is 2873–2882,
where the generated call argument convention depends on `takes_self` and
`takes_field`.

`Converter` is declared at line 2802. The module then explicitly reassigns its
name at lines 2918–2920:

```python
Converter = _add_hash(
    _add_eq(_add_repr(Converter, attrs=_f), attrs=_f), attrs=_f
)
```

The saved decline for `_setattr_with_converter.converter` is
`annotation_unresolved_or_ambiguous`. The resolver requires a unique unchanged
class binding. It does not execute or analyze the return identity of the
transformation helpers, so it does not assume that the assigned result denotes
the original class. This is a conservative source-resolution limitation. It
is not evidence that those helpers actually replace the class or its formatter.
The file already uses `from __future__ import annotations` at line 3, so a
missing deferred-annotation declaration is not the issue.

## Passing the receiver onward also exceeds the initial scope

`_assign_with_converter` at `_make.py:2034–2044` has the same annotation at line
2035. Its conditional branch at line 2042 passes the parameter as a bare
argument to another helper:

```python
return _setattr_with_converter(attr_name, value_var, True, converter)
```

The other branch at line 2044 directly calls the formatter while constructing
an assignment string. The saved parameter decline is
`receiver_alias_escape_or_dynamic_attribute`: the fixed rule declines a
receiver across its entire function when it escapes through another call.
It does not prove helper effects or analyze mutually exclusive branches to
retain a relationship in one branch. That limitation is independent of the
separate module-level `Converter` rebinding.

Neither of these declines should be bypassed with a helper-name, class-name,
repository, query or path exception. The current frozen experiment must retain
them and report the resulting coverage limitation.

## What the terminal guard can and cannot recover

For `attrs_optional_context_converter_and_generated_arguments`, the original
first-repetition native traces distinguish two omissions:

| Mode | Formatter raw rank | Coverage evidence rank | Prior-role evidence rank |
| --- | ---: | ---: | ---: |
| Dense | 11 | absent | absent |
| Hybrid | 8 | 8 | absent |

The hybrid formatter was retrieved and delivered before a semantic exchange
removed it. Recognized complementary baseline pairs could protect that kind
of delivered evidence, but the saved attrs graph recognizes no pairs. The
targeted formatter protection is therefore not established by this rule.

The dense formatter was retrieved but already omitted by coverage. This
terminal filter accepts only one exact saved operand: the prior-role proposal
or coverage. It admits no missing window, runs no packing/backfill and makes no
new retrieval call. Even a recognized caller-to-formatter edge would not create
a protected delivered pair when the formatter is absent from coverage. The
fixed eight-excerpt, 12,000-character and 4,096-token limits remain in force.

The zero-edge receipt suggests no additional attrs pair protection; the final
count of changed responses, individual losses and incremental complete gains
must still come from the complete replay and independent audit. Preserving an
old Jinja gain alone cannot pass the new incremental-gain gate versus prior-role.

## Remaining source and selection challenge

The old syntactic state-write guard does not mistake generated assignment text
for an executed attribute-store AST node. The converter helpers generate call
and assignment strings, while the formatter's contextual-argument decisions
remain necessary implementation evidence. Treating every source-generating
function as a state writer would not itself establish complementary task
coverage.

Future work would need a separate prospectively frozen general experiment to
address class identity through transformation chains, effects of forwarding an
annotated receiver to helpers, and complementary caller/callee evidence under
the existing output budget. Recovering an already retrieved but undelivered
implementation also requires an admission/selection mechanism; this terminal
preservation rule cannot supply it. None of these broader analyses or selection
changes has been tested by this experiment, and no rescue rule is proposed here.

Attrs is now observed development evidence. Any later changed rule would need
the full observed regression matrix and a newly untouched repository for fresh
confirmation. CPU development integrity, quality qualification, native latency,
production promotion and coding-agent token savings remain separate claims.
