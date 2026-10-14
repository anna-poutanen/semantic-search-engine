# Peer correction: directive-only method bodies

This is the current prototype qualification, following the historical v1 check.
An actual new grammar regression failed unchanged source `3327525`: a body
containing only `#if`/`#else` conditions and comments was wrongly eligible.
[The old-source failure](old-source-reproduction.log) and
[unchanged-source proof](old-source-proof.json) preserve that observation.

The corrected prototype recursively checks known preprocessor/block containers,
ignores condition identifiers, directives, comments, empty statements and local
function declarations, and requires actual statement nodes. Unknown containers
do not create eligibility. Both positive branch-return cases pass. Window byte
offsets additionally require exact integers, rejecting booleans and floats.

**14 isolated grammar/portable tests pass**, with **six portable passes and eight
explicit optional skips** in the main environment. Ruff, whitespace, and stable
source hashes pass. The authored CLI example is byte-identical to v1, retaining
its separate exact line/ID/hash/body-coverage proof. The new
[qualification](qualification.json) preserves these checks. The original grammar
smoke and historical v1 prototype evidence remain unchanged. The initial prototype
documentation's 13-test count describes v1; this correction adds the eighth actual
grammar test.

During the correction's initial edit, one unmatched Python parenthesis caused
an import SyntaxError and Ruff syntax errors; no tests ran on those intermediate
bytes. It was repaired before the final stable run and commit `ae35670`.

This remains a standalone experimental source extractor. No production language
registry, package pin, editor, main environment installation, index, compiler
execution, model inference or retrieval-quality claim changed.
