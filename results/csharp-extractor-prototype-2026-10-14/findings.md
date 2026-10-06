# Standalone C# extractor development check

The experimental extractor passed **13** stdlib unittest tests using the existing
isolated grammar environment. The main environment passed **six** portable tests
and explicitly skipped **seven** optional grammar tests. Ruff and whitespace
checks passed. Source/test/document hashes were equal before and after the final
guarded run.

The [prototype status and limits](../../docs/csharp-extractor-prototype.md) describe
the supported method/constructor subset and literal signature identities. C# is
**not enabled** in the engine or editor; there is no retrieval-quality result.
The original grammar smoke's source/plan/test hashes, all four actual binding
file hashes, and main config/dist-info metadata witnesses remain equal.

The [authored CLI source](final-v1/authored-source.cs) yielded six definitions and
eight [exact windows](final-v1/prototype-example.json). A separate stdlib check
reconstructed literal physical lines, IDs, hashes, limits and every body's line
coverage without importing the prototype's window helper. The fixture includes
BOM/CRLF/non-ASCII bytes, attributes, an initializer-only constructor, two literal
overloads, a long body, and both preprocessor branches. Separate tests reject
identical qualified/signature identities across branches, grammar root errors,
deferred/declaration-only forms and invalid display paths.

All final test/CLI child processes blocked model/production-helper imports,
socket connections, subprocess children and `os.system`. No packages were
installed for this phase, and no C# program was executed. Metadata/config and
binding byte witnesses are specific checks; this is not a hash of every
environment byte, including incidental Python cache files.

The initial authoring source and observations are retained in
[preparation-v1](preparation-v1/authoring-receipt.json): 13 initial tests passed,
with one Ruff import-placement failure subsequently repaired. Archives use gzip
to keep historical tests out of live pytest collection. The final guarded logs,
raw CLI example and [qualification](final-v1/qualification.json) preserve the
current development evidence. This is an authored development fixture check,
not a blind holdout or a production integration certificate.
