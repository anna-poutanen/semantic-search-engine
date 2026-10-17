# Check source before indexing

Included in the [v0.1.3 Mac download](https://github.com/anna-poutanen/semantic-search-engine/releases/tag/v0.1.3)
and source checkout. Earlier release assets remain unchanged.

An invalid tracked template can stop indexing, while minified vendor files can
turn a modest file count into very large embedding inputs. Check the exact saved
source scope before downloading embedding weights or spending time on inference:

```bash
bash "Get Started.command" --preflight --repo "/absolute/path/to/repo" --source-root src
```

The launcher can install its managed Python, runtime packages and tokenizer cache
first. It then performs CPU source inspection and exits. Normal own-repository
launcher sessions perform the same check before embedding model setup. The toy,
`--check` and `--setup-only` flows keep their existing behavior.

For an existing developer environment with the language parser dependencies:

```bash
semantic-search inspect --repo "/absolute/path/to/repo" --source-root src
semantic-search inspect --repo "/absolute/path/to/repo" --source-root src --json --limit 10
```

The CLI inspection does not load models or tokenizers, access a network, create
SQLite files, start a service or modify the selected checkout. It uses the same
saved-source membership, parsers and 160-line/40-line-overlap extraction policy
as production indexing. Git selects tracked files; outside a working Git checkout,
the CLI uses the engine's directory fallback and reports that membership mode.
The Mac launcher continues to require a Git checkout root.

The report contains:

- Eligible, explicitly excluded, included and parsed file counts by language.
- Extracted windows and distinct rendered-document hashes.
- Likely minified filenames and windows over a **32,000 rendered-character**
  heuristic, including a bounded list of the largest windows. This is not a token
  count, model limit, latency estimate or reason to automatically omit a file.
- Source-free error codes with file paths and line numbers, and explicit literal
  file exclusion suggestions. The shell arguments escape glob metacharacters as
  well as shell metacharacters.

`--limit` bounds each detail list, from 1 to 100; totals remain exact for work
completed. A blocked parse report counts only successfully extracted windows,
so those window totals describe a partial scope. Exit status is **0** only for a
stable, fully parsed snapshot and **1** for blocked inspection; invalid command
options exit **2**. If files, membership or Git refs change during inspection, the
result is blocked. A ready report describes that captured snapshot; the service
still validates its own source before indexing and publishing an index.
The report's `snapshot_sha256` identifies the inspection's captured membership,
Git refs and file bytes. It is separate from the backend's `source_manifest` and
must not be used as an index cache key or proof that a running service has that
scope.

Fix invalid files, narrow `--source-root`, or explicitly pass the reviewed
`--exclude-source` patterns and repeat the check. Patterns are relative to the
repository root and change the searchable scope and index identity. Large and
minified files produce warnings; valid included source stays included.

Reports intentionally contain local repository paths, source filenames, counts
and source/document hashes. They do not contain source
text. Review them before sharing. They are separate from the allowlisted beta
feedback JSON and are not automatically uploaded or attached to it. `--doctor`
continues to inspect only the environment, without reading repository source.
