# Isolated C# grammar smoke

The pinned Mac arm64 grammar passed this bounded syntax/source-span smoke check.
**C# is not implemented in the search engine.** This result does not establish
retrieval quality, production extraction eligibility, compiler behavior, dependency
resolution, or editor support.

The [prospective plan](frozen-plan.json), helper, tests, and publisher metadata were
committed in `4c0c966` before any actual C# parser run. The standalone helper created
a fresh `.cache/csharp-parser-preflight-2026-10-14/env` from base Python 3.12.15,
then installed only the two verified wheels with pip's isolated, no-index,
no-dependency mode. Both whole-wheel hashes matched the pinned publisher hashes
before installation. The [publisher metadata](publisher-tree-sitter-c-sharp.json)
and [binding metadata](publisher-tree-sitter.json) preserve those inputs.

- Actual versions: `tree-sitter==0.26.0`, `tree-sitter-c-sharp==0.23.5`.
- Actual grammar ABI: **15**, inside the binding's supported **13–15** range.
- Seven fixed valid fixtures parsed without root errors, ERROR nodes, or missing
  nodes. They cover block/expression methods, explicit constructor initializers,
  file-scoped and nested namespaces/types, generic types, overloads, UTF-8 BOM,
  CRLF, non-ASCII/string separator bytes, and both preprocessor branches.
- All **10** method/constructor spans in valid fixtures matched independently
  authored literal source targets. Byte offsets, exact source, hashes, physical
  CR/LF line ranges, and Tree-sitter points are retained separately.
- Both fixed invalid fixtures reported `root.has_error=true`. The malformed
  expression exposed **zero** explicit ERROR/missing nodes despite its root error;
  the missing closing brace exposed **one** ERROR and **zero** missing nodes. A
  strict parser must inspect root error state as well as node flags. These two
  cases do not demonstrate every possible recovery or missing-node shape.
- All four actual Python/native binding module files loaded from the fresh
  environment; their paths and full byte hashes are retained. Network, model and
  production-helper imports, and child execution were blocked during the parser
  probe. No C# program was executed.
- Before/after main `pyvenv.cfg`, `pyproject.toml`, and all physical main-env
  dist-info METADATA/RECORD/direct-url witnesses were equal. This is an explicit
  metadata/config witness, rather than a hash of every main-environment byte.
  Every installation command targeted the new environment. Frozen helper/test/
  plan-document hashes also remained equal.

The [raw parser observations](parser-smoke.json), [run qualification](qualification.json),
and command logs preserve the actual results. Twelve portable CPU fault tests
also passed before the run, with Ruff and stable before/after source hashes; those
tests use synthetic nodes and never import the C# grammar.

Next is the source-support work in [the C# plan](../../docs/csharp-source-plan.md):
explicit language dispatch, body eligibility and overload-safe symbols, exact
160/40 windows, language-specific membership/cache policy, reference isolation,
and coordinated editor checks. Parsing both preprocessor branches does not choose
the build's active symbols. An empty constructor included in this parser fixture
does not certify production implementation eligibility. Grammar acceptance does
not establish Roslyn/compiler semantics.
