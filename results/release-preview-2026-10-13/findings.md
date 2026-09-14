# Published portfolio preview

The [GitHub prerelease](https://github.com/anna-poutanen/semantic-search-engine/releases/tag/v0.1.0-preview.1)
ships the exact VSIX tested on this Mac. Its 51,493-byte asset has SHA-256
`be4f32bdb58e45693244a933dbfd2effc35fdaaa4cc3f3bb44237bb1c3b9da9b`.
GitHub's uploaded asset digest and size match the local frozen package. The
[release receipt](release.json) records the asset ID, download URL and source
target `363a26a40b4f44fd302a4a244009b23d696e10a5`, which passed both CI jobs.

The [installed-package proof](../editor-package-2026-10-13/findings.md) covers
seven real Qwen saved/dirty searches, 35 exact excerpts, navigation and
cancellation in an isolated VS Code Development Host. The
[separate guard checks](../editor-acceptance-2026-10-13/findings.md) exercise
actual false workspace trust, wrong repository selection, folder removal and
offline errors with an authored HTTP fixture. Native installer and host process
groups stopped, and their owned profiles/listeners were removed.

See [the preview guide](../../docs/release-preview.md) for manual VSIX installation
and the reproducible HTTP/MCP walkthrough. Python and model setup remain separate
from extension installation. No Marketplace publication or license grant was
added; the package remains UNLICENSED. Ordinary installed user-profile activation
and external beta feedback remain untested.

The preview does not close the retrieval or coding-evaluation gaps. Zod multi-file
delivery remains 0/4 under the frozen budget, the development packing replay
recovers no complete misses, and the three qualified coding pairs use 9.23% more
gross tokens with MCP. Six missing usage receipts remain unknown.
