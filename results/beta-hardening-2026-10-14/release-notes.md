The Mac preview is easier to use on your own repository.

- Own-repository searches default to eight excerpts, 12,000 source characters,
  4,096 compact tokens and dependency context, with configurable caps.
- Indexing has its own 30-minute startup allowance and visible file/document
  progress. HTTP searches have a separate 120-second timeout and permit retries.
- The download includes offline `--doctor` diagnostics, local `--feedback`
  collection, portable and VS Code MCP snippets, and fixed-port support.
- Embedding batches have a padded-token cap while preserving each document's
  8,192-token context limit. Repeated interrupts wait for bounded owned cleanup.
- Explicit source exclusions support generated/template and minified files.

Use **semantic-search-mac-0.1.2.zip**, unzip it, open Terminal in the extracted
folder and run `bash "Get Started.command"`. The ZIP includes the MIT-licensed
source and **Semantic Search.vsix**. Model weights and the dedicated runtime are
installed separately by the launcher. Compare assets against **SHA256SUMS.txt**.

The expanded CPU run passed 2,440 tests with nine skipped; the final focused
launcher/MCP/bundle suite passed 130 checks. The exact VSIX passed ordinary
installed-profile checks. Four real Django document embeddings matched fresh
singleton forwards exactly in the recorded parity probe. These are scoped
integration checks, not a new retrieval or coding-performance benchmark.

A selected Django scope completed 10,734 implementation windows. Two process
reopens reused all 10,734 document embeddings, including an immediate same-port
restart. Two source-authored MCP spot checks delivered the complete expected
implementations, and the final ZIP passed three HTTP and three official MCP toy
searches. Django validation explicitly excluded its invalid JavaScript template
and minified vendor files; the preserved unfiltered stress attempts are separate.

This remains an Apple Silicon preview tested on one existing M4 / 24 GB Mac,
with no independent external beta users yet. Older vector caches rebuild once;
custom match policies must be recalibrated. Minified/vendor scope choices are
explicit rather than automatic.

[Getting started](https://github.com/anna-poutanen/semantic-search-engine/blob/main/docs/getting-started.md)
· [Qualification and preserved failures](https://github.com/anna-poutanen/semantic-search-engine/blob/main/results/beta-hardening-2026-10-14/findings.md)
