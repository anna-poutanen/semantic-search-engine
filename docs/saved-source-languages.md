# Saved JavaScript and TypeScript source

The saved-source backend supports `.py`, `.js`, `.jsx`, `.mjs`, `.cjs`, `.ts`,
`.tsx`, `.mts`, and `.cts`. Python retains the existing AST extractor and exact
snippet format. JS/TS requires the optional pinned parser dependencies:

```sh
python -m pip install -e '.[languages]'
# Or install the same exact parser versions from requirements-languages.lock.
```

The pins were checked against the official [Python Tree-sitter bindings and
usage](https://github.com/tree-sitter/py-tree-sitter),
[binding release](https://pypi.org/project/tree-sitter/0.26.0/),
[JavaScript grammar release](https://pypi.org/project/tree-sitter-javascript/0.25.0/),
and [TypeScript/TSX grammar release](https://pypi.org/project/tree-sitter-typescript/0.23.2/).
Tree-sitter 0.26.0 accepts JavaScript grammar ABI 15 and TypeScript/TSX grammar ABI
14. CPU tests use those wheels on macOS arm64 with Python 3.12.15. Missing or
mismatched parser versions produce an explicit saved-source error for JS/TS;
Python-only indexing does not import the parsers.

## Membership and snapshots

Inside Git, only tracked saved source beneath `source_root` is eligible. Outside
Git, supported files beneath that root are discovered recursively. Symlinks must
resolve inside the source root. JS/TS declaration files (`.d.ts`, `.d.mts`,
`.d.cts`) and files beneath `node_modules`, `bower_components`, `dist`, `build`,
`coverage`, `.next`, `.nuxt`, `.svelte-kit`, and `out` are excluded. These default
directory exclusions apply only to JS/TS, preserving existing Python discovery.
An arbitrary bundle outside those directories is eligible; this policy does not
guess from filenames or source contents whether a file was generated.

Configure additional repository-relative exclusions at backend construction with
`source_exclusions`, or repeat `--exclude-source GLOB` for `serve` and standalone
`search --repo`. For example:

```sh
semantic-search serve --repo /path/to/project --source-root src \
  --exclude-source '*/tests/*' --exclude-source '*.test.ts' \
  --exclude-source '*.spec.ts'
```

Patterns use Python `fnmatch` against repository-relative POSIX paths; `*` can
match `/`. Exclusions are service configuration, never per-query ranking hints.
They appear in status and the snapshot fingerprint. Backends with different
exclusions receive distinct default database names. When a database path is
supplied explicitly, the caller should use a separate path for each concurrently
running configuration.

Snapshots hash actual saved bytes, tracked membership, Git revision/branch,
exclusions, extractor policy, Python AST version, parser pins and installed parser
versions, and the backend's 160-line/40-line-overlap window policy. An extractor
change forces extraction and index promotion; identical rendered documents can
reuse their cached vectors. Installed parser versions are checked before every
JS/TS parse. Every source window is checked against captured physical lines before
the atomic SQLite refresh. Missing syntax tokens or any Tree-sitter error reject
the entire eligible saved snapshot with `SourceSnapshotError`; the old persistent
index is retained for recovery, and the failed request returns no stale results.

## Implementations and names

JS/TS indexes functions, generators, async functions, class/object methods,
constructors, accessors, and function expressions/arrows bound to a variable,
field, object property, or static member assignment. A variable bound to a factory
call with a direct function argument provides a name for that callback. A single
callback uses the binding name; several use `.callback1`, `.callback2`, and so on.
The callback's behavior is indexed without inferring the factory's return type.
Unbound anonymous callbacks are traversed for named nested definitions but do not
receive independent implementation windows.

Named default exports retain their declared name; anonymous default functions,
arrows, and classes use `default`. Nested functions and named objects/classes add
their lexical names. Block-scoped definitions use an explicit `<block@LINE:BYTE_COLUMN>`
component to distinguish shadowed functions. Getters/setters use `get NAME` and
`set NAME`. Static string property names are accepted; computed runtime names are
unresolved. Named module initialization uses `<module>.NAME`, standalone module
effects use `<module>`, fields retain `Class.field`, and static blocks use
`Class.<static:1>` (ordered per class). Runtime initializer windows cover executed
source that function windows do not cover, including mixed objects with methods
and other initialized values, default export expressions, and runtime TypeScript
enums. Ambient and `const` enums are omitted.

Declaration-only signatures, ambient/type/interface declarations, overload
signatures, empty/comment-only bodies, and directive-only bodies are omitted.
Returning `undefined`, throwing an error, or an explicit conditional is executable
behavior and remains eligible. Decorators on methods/fields are retained. JSX/TSX,
UTF-8 BOMs, Unicode strings, LF/CRLF/bare-CR, and long bodies preserve exact saved
source. Line numbers derive from UTF-8 byte offsets and physical source lines,
independent of Tree-sitter point rows. Multiple bodies sharing one physical line
necessarily share that source line in their windows.
Repeated same-line names receive a source-byte suffix on colliding window IDs.
The pinned grammars can misread a line break between `export` and its declaration;
AST-confirmed export trivia is parsed with equal-byte-length whitespace padding,
while windows and hashes always use original saved source. Parser validation is
syntax-tree based; it does not type-check or execute the program.

## Conservative context references

Python references retain their existing logic. JS/TS references resolve direct
lexical function/arrow calls, nested scopes, and relative named/default/namespace
ES imports only through actual direct exports, including local export aliases.
Relative CommonJS `require` supports namespaces, simple object destructuring,
one static member, and unambiguous `exports.fn`, `module.exports.fn`, or a single
`module.exports` assignment to a known function or static object of functions.
Shadowed `require`, reassigned bindings/namespaces, conflicting exports, dynamic
exports, escaped export objects, module replacement mixed with the `exports` alias, and reflection through
the exports object stay unresolved. File existence alone never proves an export.
When emitted `.js`/`.mjs`/`.cjs` imports have no matching saved file, one unambiguous
`.ts`/`.tsx`/`.mts`/`.cts` counterpart can resolve. Extensionless imports require
one eligible matching source or index file.

External packages, re-exports, runtime/parameter/instance/inherited dispatch,
factory products, reflection, dynamic `with`/direct `eval` scopes, and uncertain
computed calls remain unresolved.
A resolved reference supplies bounded related source context; it does not prove
behavioral relevance. This graph is intentionally incomplete.

The extraction and persistence tests use temporary source/Git fixtures and fake
embeddings. No model inference, retrieval holdout labels, or real-query tuning is
part of these implementation checks. Real JS/TS retrieval quality and latency
remain a separate evaluation after source eligibility and evaluation protocol are
frozen.
