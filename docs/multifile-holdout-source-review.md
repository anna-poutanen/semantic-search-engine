# Fresh multi-file holdout: source-only Jinja review

[The dataset](../benchmarks/multifile_holdout_queries.json) contains 14 authored
behavior queries: eight multi-file positives, four single-file controls and two
scoped absent features. It is reserved for the independently frozen multi-file
retrieval comparison. No Jinja ranking, retrieved candidate, model score,
clause-rule activation or search result was inspected during authoring.

## Corpus and freeze identity

- Repository: [pallets/jinja](https://github.com/pallets/jinja), release **3.1.6**.
- Peeled commit: `15206881c006c79667fe5154fe80c01c65410679`.
- Annotated tag object: `2d4ce43010630478ee88b463f731389fa18953f4`.
- Scope: all 25 saved, Git-tracked Python files under `src/jinja2`; no exclusions.
  The production `src/jinja2/tests.py` contains template predicates and remains
  included; upstream test suites, examples and dependencies are outside scope.
- Local ignored corpus: `.cache/corpora/jinja-multifile-holdout` in the primary
  checkout. `scripts/fetch_corpus.py --dataset benchmarks/multifile_holdout_queries.json`
  accepts the same pinned dataset with a chosen `--destination`.
- Dataset SHA-256: `29ec1612ba3342ce51ab71571d1a5f4a50a891c0a643607fb8a4c66231ae0007`.
- Source-inventory SHA-256: `f945458472cb85a4cc350ce84996f5a2c305c7029181c4288cc142538d1cbce8`.

The JSON records every included file's byte length and SHA-256, plus the full-file
and literal evidence-span SHA-256 on every target. The inventory digest hashes
UTF-8 JSON of `source_files` with sorted keys and compact comma/colon separators,
followed by one LF. Each evidence digest hashes the complete inclusive physical
line range as UTF-8, preserving source line endings.

Jinja was selected after a repository-history exposure check found no
`pallets/jinja`, `corpora/jinja`, Jinja query dataset or Jinja holdout records
in this project's benchmarks, docs, results, scripts or tests. Flask integration
code mentions Jinja and imports its public API; this is therefore a fresh
repository evaluation within a previously encountered ecosystem, not a claim
of framework-family independence or absence from model pretraining. Previously
evaluated Flask, Click, Requests, HTTPX, Rich, Express, Koa, Zod, Starlette,
Werkzeug and Django repositories were excluded from holdout selection.

## Labeling and eligibility

Queries were written from implementation reading with no explicit source path
or qualified symbol in their text. The eight compound cases ask for connected
behavior implemented across different files. Required groups describe distinct
steps of that flow; nearby forwarding or support helpers read to confirm the
connection are noted without automatically becoming additional gold groups.
Four single-file controls and two plausible but absent features were then
fixed without looking at retrieval behavior. Wording was not changed to force
clause splitting or favor an observed ranking.

The author performed a second source pass independent of the initial notes:

1. Rechecked the pinned Git HEAD, clean saved checkout and exact 25-file inventory.
2. Parsed source with Python AST and resolved each qualified gold symbol.
   Every inclusive evidence interval is inside its executable implementation
   and includes executable statements rather than only signatures/docstrings.
3. Recomputed all full-file and evidence hashes from physical saved lines.
4. Used production extraction at **160 lines with 40-line overlap**. It produced
   **745 source windows**. Every one of the 23 gold rows has a source-faithful
   exact-symbol window containing its complete evidence span. This was an
   extraction eligibility check, with no indexing, ranking or inference.
5. Followed the cross-file call/delegation paths and reread the referenced
   upstream tests. Test names were AST-checked; tests were read, not executed.
   This corrected three test-class reference qualifications. File freshness
   wording was qualified to a new load attempt, since a missing file can make
   that attempt fail. These changes happened before this dataset hash and
   before any holdout retrieval.

A separate source reviewer must check the same frozen labels before inference.
Any correction must come from source, receive a new dataset hash and reason,
and be finalized before evaluation. Retrieval misses must never be repaired by
changing gold evidence after outputs are visible.

## Required multi-file evidence

Targets within a group are alternatives (OR); every group is required (AND).
These eight cases contain **19 required groups**. The table uses abbreviated
IDs; all IDs in the JSON begin with `jinja_` and paths begin with `src/jinja2/`.

| Query ID | Required implementation spans | Why all groups are necessary |
|---|---|---|
| bytecode_reject_and_recompile | `bccache.py::Bucket.load_bytecode` 68–81; `loaders.py::BaseLoader.load` 130–145 | Reject incompatible cached code, then compile and store its replacement. |
| file_change_invalidates_template_cache | `loaders.py::FileSystemLoader.get_source` 217–226; `environment.py::Environment._load_template` 962–978 | Compute filesystem freshness, then make the cached-template reuse/load decision. |
| include_parent_and_local_context | `compiler.py::CodeGenerator.visit_Include` 1074–1086; `runtime.py::new_context` 103–118 | Emit the include with shared/current locals, then merge locals without mutating shared input. |
| decorated_helper_context_injection | `utils.py::pass_context` 51–52; `runtime.py::Context.call` 284–303 | Mark the callable's requested context, then prepend that context at invocation. |
| json_policy_and_html_safe_serialization | `filters.py::do_tojson` 1713–1721; `utils.py::htmlsafe_json_dumps` 665–674 | Select serializer/indent policy, then replace HTML-sensitive characters and produce safe-marked output. |
| async_filter_first_and_iteration | `async_utils.py::async_variant` 17–48; `filters.py::do_first` 667–670; `async_utils.py::auto_aiter` 90–93 | Choose async dispatch, handle the first item/empty case, and adapt either kind of iterable. |
| sandbox_attribute_security_placeholder | `sandbox.py::SandboxedEnvironment.getattr` 329–332; `sandbox.py::SandboxedEnvironment.unsafe_undefined` 336–342; `runtime.py::Undefined._fail_with_undefined_error` 859–859 | Route denied attributes, construct the security-tagged placeholder, then raise its stored exception. |
| filename_autoescape_to_generated_output | `utils.py::select_autoescape` 621–634; `nodes.py::EvalContext.__init__` 79–84; `compiler.py::CodeGenerator._output_child_pre` 1482–1487 | Select filename/string policy, evaluate it for the template, then emit conditional escaping. |

The sandbox label intentionally requires a later failing operation. A blocked
attribute can still stringify as empty text; the query does not assert that
every blocked lookup immediately raises. The HTML-safe JSON label does not
claim safety inside a double-quoted HTML attribute. The filename policy label
excludes constant-output folding and the external MarkupSafe implementation.

## Single-file controls

| Query ID | Required executable evidence |
|---|---|
| batch_last_row_padding | `filters.py::do_batch` 1132–1145 |
| truncate_suffix_and_tolerance | `filters.py::do_truncate` 905–918 |
| bounded_cache_replacement | `utils.py::LRUCache.__setitem__` 534–541 |
| template_path_components | `loaders.py::split_template_path` 29–39 |

These add four groups, for **23 positive evidence groups and 23 target rows**
across twelve positive queries. The shortening label describes an actual
last-space split on the prefix; it does not promise never to cut a long word
when the prefix contains no space. The path-component label is not a general
proof of filesystem isolation against arbitrary symlink arrangements.

## Scoped absent features

- **Rendered-output disk cache with expiry and input-value reuse:** reviewed
  bytecode cache interfaces/backends, template loading, rendering, default
  modules and module body storage. Bytecode caches store compiled code. The
  in-memory default module can cache rendered body text, but it does not provide
  the requested input-keyed on-disk rendered-output cache and expiry policy.
  The memcached bytecode backend can pass a timeout to its external client;
  that is expiry of compiled bytecode, not the requested rendered outputs.
- **Edit-triggered live browser fragment push:** reviewed file freshness,
  template cache decisions and synchronous/asynchronous render/generate entry
  points. Automatic reload checks freshness on loading a template. No browser
  connection registry, page diff, socket push or edit-driven broadcast was found
  in the scoped implementation. Async iteration and URL-link generation do not
  establish that behavior.

The JSON preserves the source-search terms and nearby implementations behind
each absence label. These are bounded source-review judgments, excluding host
applications, third-party extensions, dependency internals and upstream tests.
They are not runtime proofs that a feature is impossible. Absent cases remain
outside positive coverage denominators; report their delivery/abstention
behavior under the independently frozen evaluation rule.

## Scoring and holdout use

The agreed scorer uses **exact pinned path and complete literal source span**,
following the language-quality evaluation. An enclosing or nested window can
cover a group regardless of its qualified-symbol label. The stored `symbol` is
a navigation aid and source-review identity, not a substitute for evidence.
One returned row can satisfy multiple groups if its verified source contains
all their intervals. A group lost to symbol deduplication, result caps or
character/token truncation is missing from delivered evidence even if it was
present in the candidate pool.

Only query text enters retrieval. Gold paths, symbols, evidence spans/hashes,
notes and split labels cannot enter candidate construction, clause selection,
prompts, graph expansion or tuning. Freeze the implementation and conditions
before the first holdout run. Use the independently declared result/source/token
budgets unchanged across conditions; report complete compound coverage, group
coverage, the four single-file controls, latency and every regression separately.
After results guide a change, this dataset becomes development evidence and a
new holdout is needed for another independent claim.

This deliberately multi-file-heavy authored sample is small and is not a
representative user workload. It measures retrieval evidence delivery, not
coding correctness, paid-agent token savings or another machine's latency.

## License

Jinja 3.1.6 source is BSD-3-Clause, **Copyright 2007 Pallets**. The verbatim
[license copy](../benchmarks/JINJA_LICENSE.txt) has SHA-256
`3b49dcee4105eb37bac10faf1be260408fe85d252b8e9df2e0979fc1e094437b`.
Attribution is recorded in the [corpus notices](../benchmarks/CORPUS_NOTICES.md)
and [third-party notices](../THIRD_PARTY_NOTICES.md). The ignored checkout is
not committed as an additional source tree; source excerpts in evaluation
artifacts retain those upstream notices.
