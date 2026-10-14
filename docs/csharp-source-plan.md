# C# saved-source support: prospective plan

Status: **not implemented or qualified**. This source-only review ran no parser,
model, index, retrieval, compiler, or editor experiment. Parser extraction tests
will establish source safety; a separate authored benchmark is needed to establish
C# retrieval quality.

The project plan places C# after validation of the current core. A conservative
implementation fits the existing backend, provided dispatch and eligibility are
explicit. Adding `.cs` to the registry alone is unsafe: current non-Python
validation, extraction, and reference resolution all dispatch to JS/TS helpers.

## Grammar and runtime pin

Use `tree-sitter-c-sharp==0.23.5` with the existing `tree-sitter==0.26.0`, as an
optional language dependency. The official tagged generated parser declares ABI
15, within the current Python binding's documented ABI range 13–15. These source
and package constraints are compatible; an isolated CPU wheel/parser smoke test
is still required before accepting the installed binary.

Primary sources:

- [Publisher's 0.23.5 release and wheels](https://pypi.org/project/tree-sitter-c-sharp/0.23.5/):
  Python >=3.9, MIT, Mac arm64 and Linux x86-64/arm64 wheels. Its stated C# 1–13
  syntax support has limitations around contextual identifiers.
- [Tagged generated parser](https://raw.githubusercontent.com/tree-sitter/tree-sitter-c-sharp/v0.23.5/src/parser.c):
  `LANGUAGE_VERSION 15` was read from the literal header, without downloading the
  complete generated file.
- [Tagged package metadata](https://raw.githubusercontent.com/tree-sitter/tree-sitter-c-sharp/v0.23.5/pyproject.toml):
  optional core constraint `tree-sitter~=0.22` permits the existing 0.26.0 pin.
- [Official 0.26.0 binding documentation](https://tree-sitter.github.io/py-tree-sitter/):
  supported language ABI range 13–15 and `Language`/`Parser` APIs.

The publisher's Mac arm64 wheel SHA-256 is
`a75994a11f6fed3f5b8c36ad6a00e5dc43205bd912c43af3a2a54fdf649664eb`;
Linux x86-64 is
`aa88a780204cd153c4c1ae2d59c654cee1402212fa0d069823d6d34301587438`.
Neither wheel was installed or executed in this review. Do not change the main
benchmark environment to perform this smoke test.

## Initial supported subset

Start with named methods and constructors with explicit block or expression
bodies. Preserve declaration attributes, signatures, constructor initializers,
and literal body text in the enclosing source span. Omit declaration-only
abstract/extern/interface members and empty/comment-only bodies without an
explicit constructor initializer. Do not infer that an unresolved exception type
or `throw` statement is a placeholder implementation.

Defer accessors, indexers, operators, local functions, anonymous lambdas, and
top-level statements until each has explicit naming and span tests. Document this
subset instead of claiming all C# executable forms. Names must contain namespace,
nested type, member, and a stable overload discriminator; the current backend
deduplicates `(path, symbol)`, so plain method names could silently merge overloads.
Do not resolve overloads or partial classes semantically.

## Membership, bytes, and parse safety

- Accept `.cs` only, retaining current Git-tracked membership and repository/source
  root containment checks. Outside Git, preserve the documented saved-directory
  fallback. `.csx`, project files, binaries, and newly untracked Git files remain
  outside this initial source subset.
- Add C#-specific directory exclusions for `bin`, `obj`, `.vs`, and `packages`;
  preserve Python/JS/TS policies. Retain explicit repository-relative exclusion
  globs for additional generated files. Do not infer that every `*.g.cs` is
  generated or every file outside these directories is authored.
- Decode strict UTF-8, retaining a BOM. Reject unsupported encodings explicitly.
  Use existing physical CR/LF/CRLF lines and byte-offset mapping; Unicode paragraph
  separators inside strings remain data. Never reformat or rewrite saved source.
- Reject grammar error/missing nodes before indexing. Treat preprocessor branches
  as saved source, not as proof of the build's selected symbols or compiled
  behavior. Unsupported syntax should fail visibly, rather than be silently
  repaired to a different program.
- Reuse 160-line windows and 40-line overlap; test coverage of every eligible body
  line, exact saved substrings, inclusive line bounds, IDs, and content hashes.

## Dispatch, caching, and references

Add explicit C# paths in `languages.validate_source` and `corpus.extract_snippets`,
using a new dedicated C# extractor. Keep JS/TS dispatch restricted to those
languages. Bump the extraction policy and bind the C# grammar pin, installed
version, extensions, and language-specific exclusions into `extractor_fingerprint`.
This invalidates incompatible source/window caches while retaining existing
embedding model/content identity rules. Missing or changed parser pins must fail
clearly before producing an index.

Initially C# has no static dependency edges. Restrict `ReferenceGraph`'s existing
JS/TS source selection explicitly, so C# never enters its module resolver. Preserve
Python and JS/TS edges. Namespace/import names, extension methods, inheritance,
partial types, or dynamic calls are not reliable dependency proof without a
separate resolver and tests. Label this limit in returned capability documentation.

## Editor and overlay boundary

Backend enablement alone is insufficient. VS Code's `src/source.ts` suffix list,
response schema validation, and `src/overlays.ts` eligibility currently permit
only the existing languages. Update those together after backend qualification;
keep the C# generated-directory policy identical on both sides. Existing UTF-8
decoding can be reused only after BOM and physical-line tests.

Saved search remains saved source. Explicit unsaved overlays must retain current
tracked-file membership, all-buffer binding, saved-index isolation, change
invalidation, trust/root checks, and navigation validation. Do not weaken these
checks to admit new or unsupported files. Package and test a new VSIX after source
support is accepted; current published/native editor evidence covers its original
language set.

## Acceptance before enabling

1. In an isolated CPU environment, verify publisher wheel hashes, exact package
   versions, actual grammar ABI, and clean parsing of representative fixtures.
2. Test block/expression bodies, constructors/initializers, namespaces including
   file-scoped forms, nested/generic/partial types, attributes, and overload
   distinction. Test every excluded declaration and deferred executable form.
3. Test invalid syntax, preprocessor branches, UTF-8 BOM, CR/LF/CRLF, Unicode string
   separators, complete window coverage, and exact source/hash preservation.
4. Test tracked/untracked membership, generated-directory exclusions, explicit
   globs, root escape/symlinks, missing/version-mismatched parsers, and unchanged
   existing-language behavior.
5. With a CPU fake embedder, test saved edit/new/delete/rename, cache invalidation,
   reopen, overload deduplication, and no accidental JS/TS dependency dispatch.
6. Test editor schema/source/navigation and explicit overlays, then package/run an
   actual editor smoke check. Author and freeze a C# retrieval benchmark separately
   before reading outcomes; parser or token safety is not retrieval-quality evidence.
