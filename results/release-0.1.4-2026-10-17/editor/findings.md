# v0.1.4 editor package qualification

The exact release candidate is `semantic-search-local-0.1.4.vsix`, 55,344 bytes,
SHA-256 `66d7db1d983b9d87a6eb7ef0742de3564f606a831add7aaf9c055ffcc95032c1`.
It matches the file produced by the completed release packaging command. The
retained [package log](package.log) records successful TypeScript compilation and
VSIX creation; this qualification did not rebuild the artifact or rerun editor
tests.

The archive passes its CRC check. Its package and VSIX container identify
`anna-poutanen.semantic-search-local`, version `0.1.4`; the package declares MIT
and the container points to the included MIT license. Both the license and
third-party notice match their source files. All eighteen packaged compiled
JavaScript/source-map members match the release worktree's compiled `dist`
files byte-for-byte.

Compared by uncompressed archive-member bytes with the published v0.1.3 VSIX
(SHA-256 `af5c556a9013e4c2a7d0fa77df9d0eab5136a5204af90a9f1f6e598c14289b48`),
only `extension.vsixmanifest`, `extension/package.json` and
`extension/readme.md` changed. There were no added or removed files. All nine
runtime JavaScript members and both license/notice members are byte-identical.
[The artifact receipt](vsix-validation.json) records the complete comparison and
compiled-member checks.

VS Code 1.142.0 arm64 installed this exact VSIX through its ordinary CLI into
newly created, isolated user-data and extension directories.
`--list-extensions --show-versions` reported
`anna-poutanen.semantic-search-local@0.1.4`. All 23 packaged extension members
were verified after installation: original package-manifest values matched,
allowing only VS Code's added `__metadata`, and all other files matched
byte-for-byte. The owned profile directories were deleted after verification;
commands used explicit isolated paths throughout. See the
[installation receipt](cli-install-validation.json) and retained CLI logs.

This run verifies the exact package, compiled-member consistency and ordinary
CLI installation. Native GUI activation, actual Restricted Mode and real-model
extension searches were not rerun. Earlier editor acceptance evidence applies
to byte-identical runtime code but remains historical evidence, rather than a
new v0.1.4 GUI run.
