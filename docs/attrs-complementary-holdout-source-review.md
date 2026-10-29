# attrs complementary-context holdout: source-only review

This repository is reserved for a future native validation of a separately
chosen complementary-definition/window policy. No attrs index, embedding,
retrieval, reranker, selector activation or coding-agent outcome has been run.
The rule designer and experiment runner may receive only repository, release,
inventory count, artifact hash and review-status metadata until development
qualification and a committed native plan permit the held-out inputs to be read.
This is independence within this project's evaluation, not a claim about model
pretraining or the absence of attrs from the development environment's dependencies.

## Pin and scope

The corpus is [python-attrs/attrs](https://github.com/python-attrs/attrs), published
release [25.3.0](https://github.com/python-attrs/attrs/releases/tag/25.3.0).
Git's annotated tag object is `bd2446d77736a164b59b268a3792ed3e4bfba088`, peeled
commit `3dca08ce5bbe673d7df25f44f3dda92505d1043d`. The GitHub release API reports
published `2025-03-13T11:09:34Z`, `draft=false`, `prerelease=false`. The local
checkout is clean and detached at that commit. This verifies the primary release
metadata and Git objects; it is not a signature-verification claim.

The source root is `src`. The pinned wheel configuration publishes `src/attr`
and `src/attrs`, so both namespaces are included rather than dropping the
modern forwarding modules. All 19 Git-tracked supported Python files under
that root are included. There are no supported in-root exclusions. Twelve
unsupported `.pyi`/`py.typed` files are inventoried separately. Tests, documentation,
packaging and external integrations lie outside the source root.

Existing project benchmark repository manifests and ignored corpus remotes were
checked before selection. They contained Flask, Requests, Click, Starlette,
Rich, Express, Zod, Koa, Jinja, urllib3, Werkzeug, Django and TanStack Query;
attrs had not been a retrieval/coding evaluation corpus. A previous corpus is
never made fresh merely by assigning it a new directory or tag.

The default 160-line/40-line-overlap saved-source extraction yields:

| Inventory | Count |
| --- | ---: |
| Included supported production files | 19 |
| Files with executable extracted implementations | 13 |
| Exact canonical definitions with emitted windows | 202 |
| Original source windows | 204 |
| Definitions with more than one emitted window | 2 |
| Supported files explicitly excluded | 0 |
| Unsupported in-root stubs or typed markers | 12 |

Every source byte, source-root membership decision, excluded/unsupported file,
canonical key and source window is bound in the dataset and preflight receipt.
No third-party repository module is imported or executed to obtain this inventory.

## Authorship and separation

The complete fourteen-query draft was written before the author received the
new rule designer's detailed implementation handoff. Its byte SHA-256 is
`2170ca4c21fc3750233a76a905918a914e9f2e0ba50dda3c30064cdefa89923d`.
The handoff contained implementation details; after receiving it, the author
kept all query wording, semantic requirements and target intervals unchanged.
Only a separately justified source-integrity correction may alter the draft
before the joint freeze, and any such correction must be disclosed with both
old and new hashes. No label or source contents have been sent to the designer
or runner. The independent source reviewer has no rule-design role.

The [dataset](../benchmarks/attrs_complementary_holdout_queries.json) has eight
multi-file positives, four single-file controls and two scoped absences. Its
35 required groups contain 40 source alternatives. Query wording describes
behaviors and conditions; it was authored from upstream implementation and
corroborating test source, without examining retrieval scores or outputs.

| Case | Required groups | Necessary source behavior |
| --- | ---: | --- |
| Assignment hook pipeline | 4 | Choose/invoke the field hook, thread the value through hooks, convert, then validate before final assignment. |
| Disabled validation context | 4 | Disable globally, enable in `finally`, and honor that flag in explicit and assignment validation. |
| Frozen inheritance and conflicting hooks | 3 | Inherit frozen behavior, reject conflicting construction hooks and block ordinary later assignment. |
| Required-field initializer generation | 4 | Forward field metadata; use the constructor alias and converter; emit required assignment; validate before post-init. |
| Self-aware factory plus converter | 4 | Forward an explicit factory default; retain `takes_self`; distinguish the missing sentinel; convert both supplied and factory-produced values. |
| Dictionary filtering and key conversion | 5 | Split filter selectors, match names/exact types, filter before serialization, mark dictionary keys and use hashable collection-key forms. |
| Optional contextual conversion | 4 | Skip `None`, retain instance/field context, adapt callback arguments and emit the matching initializer call convention. |
| Custom comparison propagation | 3 | Complete partial ordering when equality exists; preserve predicate `NotImplemented`; preserve it through inequality. |
| Boolean string conversion | 1 | Lowercase accepted spellings and reject unsupported values. |
| Deep mapping validation | 1 | Validate the container before each key/value pair. |
| Default private-name alias | 1 | Strip leading underscores without stripping trailing underscores. |
| Version parsing | 1 | Parse numeric components and supply a missing `final` release level. |

The two initializer cases exercise different physically separated executable
portions of one long definition. They are legitimate independent behavior
requirements, not a requirement to return an arbitrary whole function. Their
source evidence is eligible in the original emitted windows before inference.
Other cases require complementary definitions across files. The suite is small
and authored, and shares helpers across some behaviors; it is not a random
sample of independent software requirements.

All groups are AND requirements. Alternatives within a group are OR over
different qualified AST definitions containing the **same complete physical
interval and evidence hash**. For example, a containing parent and its nested
closure can prove the same source region. A same-named closure in a different
lexical branch is a different canonical definition and cannot substitute.
One returned excerpt must cover the entire interval and exact bytes; joining
fragments, matching only a symbol, or crediting an unreturned full window is
invalid. Multiple groups in one long definition remain separate requirements.

For every target, record the full source-file hash, physical evidence bounds,
UTF-8 byte bounds, exact evidence hash, qualified definition and decorated span,
exact AST byte bounds/hash, full definition-source bounds/hash, executable
evidence lines, canonical key and every eligible original window ID. The
validator independently checks those facts against the pinned source.

Required executable blocks and their surrounding declaration, transformation,
class-building and helper linkage were read. Complete referenced upstream test
bodies were read as corroboration, with qualified test names, file hashes and
body hashes recorded. Those upstream tests were **not executed**. Some tests
corroborate separate components rather than every possible combined configuration;
the labels assert the source implementation, not newly verified runtime behavior.

Conditions matter: validation disabling is global and not thread-safe, and its
context manager enables validation in `finally` rather than restoring an
arbitrary previous flag. Frozen setters permit specific exception bookkeeping.
Type filters compare exact classes. Factory `takes_self=True` is supplied through
an explicit `Factory` default, not the zero-argument `factory=` convenience.
Generated source strings are labeled at their real generator/closure definitions;
no generated runtime method is invented as an extracted target.

## Scoped absences

The absent queries ask for built-in JSON Schema export and for awaiting coroutine
validators during construction. All included source paths were inspected, along
with nearby annotation/metadata conversion and synchronous validator entry points.
Literal and AST checks are recorded separately in
`results/attrs-complementary-source-only-preflight-2026-10-29/absence-checks.json`.

No included implementation provides a JSON Schema exporter. No included source
contains an async function or `Await` node, and initializer generator strings,
manual validation and assignment validation invoke callbacks synchronously.
A user may supply an external wrapper or callback returning an awaitable; that
does not add built-in awaiting to these two packages. External schema libraries,
plugins, tests and application code remain outside the stated `src` scope.
Literal searches alone are supporting evidence, not a general proof of absence.

## License and mechanical review

The pinned sources carry `SPDX-License-Identifier: MIT`. The full root license,
Copyright (c) 2015 Hynek Schlawack and the attrs contributors, is preserved byte
for byte in [ATTRS_LICENSE.txt](../benchmarks/ATTRS_LICENSE.txt), SHA-256
`882115c95dfc2af1eeb6714f8ec6d5cbcabf667caff8729f42420da63f714e9f`.
The source-only review found no separately licensed copied implementation block
or tracked NOTICE file requiring a different implementation notice.

The new repository-agnostic Python checker is
[audit_complementary_preflight.py](../scripts/audit_complementary_preflight.py).
It shares only the existing saved-source decoder/extractor. It independently
checks AST identities, literal physical/UTF-8 byte spans and hashes, full-window
eligibility, complete membership/exclusions, upstream test references, the full
license copy and explicit absence predicates. It blocks imports of model,
numerical, index, retrieval and selector modules, third-party attrs execution,
and network access while producing the source-only receipt. It has synthetic
tests rather than running any reserved query through a retrieval policy.

An independent reviewer must still verify semantic necessity, contextual
conditions, the long-window requirements and scoped absences from source. The
mechanical receipt explicitly does not certify that semantic review. The final
dataset, protocol, checker/tests, mechanical receipt, release metadata and
independent source review must be committed before any outcome. Any later native
experiment needs its own qualified development parent and prospective committed
configuration. Once retrieval begins, attrs becomes observed development data
and cannot select parameters or be presented as fresh again.
