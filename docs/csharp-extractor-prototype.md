# Experimental C# extractor

Status: standalone development prototype, **not enabled in the search engine or
editor**. The prototype uses the separately qualified Mac grammar environment;
it does not import production search helpers, build an index, execute C# programs,
or measure retrieval quality. The original grammar plan/helper/test files remain
unchanged.

`scripts/csharp_extractor_prototype.py` extracts named methods and constructors
with nonempty block or expression bodies, and explicit constructor initializers
even when their block is empty. It retains attributes, declarations, signatures,
initializers and exact saved body text. Declaration-only methods and
empty/comment-only blocks are excluded. Accessors, operators, local functions,
anonymous lambdas and top-level statements are not separate implementations;
a local function can remain literal context inside its enclosing method.

Qualified names contain namespace, nested type, generic type arity, member name
and a full SHA-256 discriminator of the literal declaration kind/signature token
sequence. That sequence includes parameter names/default-value tokens, method
generic parameters, explicit-interface tokens and a static modifier. Whitespace
and comments outside literal tokens do not change it; body edits do not change
it. A method or parameter rename, or a changed default value, can change identity.
Return types are omitted. This is **not compiler overload resolution**: aliases,
generic substitutions, partial types, `ref`/`out` legality and other semantic
equivalences are not resolved.

Both preprocessor branches remain saved source. Identical qualified names and
literal signature identities in a file fail as ambiguous, instead of silently
deduplicating branches. Different literal token signatures that are semantically
equivalent can remain distinct; this prototype does not choose build symbols.
Conditional/nested or repeated file-scoped namespaces fail explicitly. Root
`has_error`, ERROR and missing-node flags are all checked before extraction.
Grammar acceptance does not imply a Roslyn-valid program.

Windows preserve inclusive whole physical lines with a default maximum of 160
and overlap of 40. They retain UTF-8 BOM, CR/LF/CRLF endings and Unicode paragraph
separator bytes inside strings. IDs bind display path, qualified symbol and line
range; content hashes cover exact window UTF-8 bytes. If multiple declarations
share a line, a whole-line window can also contain their adjacent text. Windows
are source spans, not a claim that every byte belongs exclusively to one method.
Display paths must be literal relative `.cs` paths without empty/dot components,
backslashes or control/format/line-separator characters. Source-membership/Git,
generated-file exclusions and root containment are intentionally left to future
integration, not certified by this standalone extractor.

Run the authored tests with the isolated environment, without installing pytest:

```sh
.cache/csharp-parser-preflight-2026-10-14/env/bin/python -I tests/test_csharp_extractor_prototype.py -v
```

The stdlib unittest suite has six portable source-window/naming/error invariants
and seven actual grammar fixture tests. Actual grammar tests skip when the pinned
optional C# grammar is unavailable; portable tests still run in normal CI. The
fixtures cover declaration eligibility, attributes, initializer-only
constructors, namespaces, generic/nested types, literal overload distinctions,
identity stability, deferred forms, BOM/CRLF/Unicode bytes, long-body coverage,
preprocessor ambiguity, and invalid syntax/UTF-8. They are development fixtures,
not a frozen codebase retrieval benchmark.

Before product integration, complete the remaining
[saved-source plan](csharp-source-plan.md): language dispatch, membership policy,
cache fingerprints and invalidation, JS/TS reference isolation, backend/reopen
tests, editor schema/overlay/navigation checks, and a packaged editor smoke.
Author retrieval-quality targets separately before reading retrieval outcomes.
