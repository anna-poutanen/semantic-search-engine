# v0.1.3 editor package qualification

Built the versioned VSIX from the release worktree using Node 24.12.0 and npm 11.6.2 after `npm ci --ignore-scripts`. npm reported zero vulnerabilities. All 177 editor/helper tests passed: 116 extension tests, 53 ordinary-profile helper tests, and 8 shared-storage helper tests. Commands and complete logs are retained here.

The exact release candidate is `semantic-search-local-0.1.3.vsix`, 55,345 bytes, SHA-256 `af5c556a9013e4c2a7d0fa77df9d0eab5136a5204af90a9f1f6e598c14289b48`.

Compared by uncompressed archive-member bytes with the published v0.1.2 VSIX (SHA-256 `5952a8f053f4f338391138a7a189a4235e63da41bd016f7db724c0d386ed7c5f`), only `extension.vsixmanifest`, `extension/package.json`, and `extension/readme.md` changed. There were no added or removed files. All nine runtime JavaScript files and both license/notice files are byte-identical. The JSON comparison records every changed member.

VS Code 1.142.0 arm64 successfully installed this exact VSIX through its ordinary CLI into newly created, isolated user-data and extensions directories. `--list-extensions --show-versions` reported `anna-poutanen.semantic-search-local@0.1.3`. All 23 packaged extension files were checked after installation: original manifest values matched (VS Code adds installation metadata), and all other files matched byte-for-byte. The owned profile directories were removed after verification; the user's normal VS Code profile was untouched.

This run verifies packaging, editor/helper tests, and ordinary CLI installation. Native GUI activation, actual Restricted Mode, and real-model extension searches were not rerun today. Earlier v0.1.2 ordinary-profile acceptance applies to byte-identical runtime code, but is historical evidence rather than a new v0.1.3 GUI run.
