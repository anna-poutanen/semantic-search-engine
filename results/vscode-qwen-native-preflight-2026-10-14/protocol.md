# Prospective native editor check

This is a five-window integration fixture. No native model, editor, service,
runtime freeze or package installation has run for this protocol yet.

The wrapper uses the new isolated reranker interpreter and checkpoint under
`.cache/qwen-install-validation-2026-10-14`, the ordinary embedding environment,
and the already prepared pinned VS Code Development Host. It installs nothing.

After all 18 coding trials finish and their independent full-inventory audit
passes, complete and independently audit the fresh isolated installation,
416-pair production parity, and native HTTP/MCP smoke. The coding driver's
recorded PID and process group must also be gone. Use the exact completed gate
files, rather than a prefix audit or the older original-runtime certificates.

Commit the wrapper, launcher repair and CPU tests before freezing. From the
project root, choose new unused output and private work directories:

```sh
.venv/bin/python -B scripts/smoke_vscode_qwen.py --freeze \
  --output results/vscode-qwen-native-2026-10-14 \
  --work-dir .cache/vscode-qwen-native-2026-10-14 \
  --coding-audit results/qwen-coding-audit-final-2026-10-14/independent-audit.json \
  --setup-receipt results/qwen-installer-validation-2026-10-14/install.stdout.txt \
  --parity-audit results/qwen-installed-production-parity-2026-10-14/independent-audit.json \
  --mcp-audit results/qwen-installed-native-mcp-smoke-2026-10-14/independent-audit.json
```

Freeze reads metadata and creates the authored mixed-language fixture; it starts
no model/editor and performs no installation. Commit its `frozen-plan.json`,
then run the identical plan explicitly:

```sh
.venv/bin/python -B scripts/smoke_vscode_qwen.py --run \
  --output results/vscode-qwen-native-2026-10-14 \
  --work-dir .cache/vscode-qwen-native-2026-10-14
```

The install receipt is the JSON emitted by the actual explicit install, with
`after.ready`, all 34 package records, and verified weight identity; its outer
preservation verification and clean install/verify command receipts are also
bound. The parity certificate must bind all 416 original pairs and actual
owner start/stop interpreter and checkpoint paths. The installed MCP certificate
uses the new reusable native auditor schema with `explicit_paths` and
`frozen_plan_sha256`. Older certificate schemas are not substituted.

The actual run owns a Qwen subprocess under the private work directory and an
HTTP service with a fresh SQLite index. It invokes the existing optional host
case through `npm run host:smoke -- --run --qwen-reranker`. The additional saved,
dirty and reranking-disabled searches take their choices from normal fixture
folder settings, without programmatic search-option overrides.

Qualification requires two actual Qwen searches of the available five-window
pool: ten measured forwards, plus one separately identified startup warmup.
Literal query/source/tokenizer identities, finite score arithmetic, ordered
native events and cumulative counters must match the HTTP receipts. Published
and retained private ledgers must contain identical bytes. Compact token counts
and source/result caps are independently recounted from actual responses.
Each of the five cumulative counters in every HTTP scorer receipt must be an
exact integer equal to that receipt's ordered forward number; booleans and
floats are rejected.

Embedding counters observe production API entries: five initial document texts,
seven deterministic successful searches, and one changed document text for the
dirty request. A separate cancellation attempt may enter query embedding zero
or one times and may finish zero or one backend responses. The host barrier
observes relay dispatch, before the backend's cancellation/embedding stage;
it does not fix which valid stage the cancel reaches. Request-scoped API
entry/exit receipts and started/completed/cancelled search records reconcile
the actual outcome, with no fabricated successful response. There are eight
search attempts and seven or eight actual query API entries. These counts do
not claim embedding native-forward cardinality or interruption of GPU work.
The saved index manifest and five-window scope must remain unchanged.

The launcher publishes a private nonce/PID/group/executable/argument receipt
before queued signals can trigger cleanup. Its TERM/KILL and output drain are
bounded. The wrapper can additionally stop only that nonce-bound detached editor
group after a launcher interruption. Qualified cleanup and clean completion are
separate observations. Passing requires the actual owned worker group gone,
authentication file absent, both listeners closed, owned editor profile removed,
and no forced/failed/interrupted cleanup. Signals retain failure evidence and
are deferred during owned cleanup and receipt publication. A durable, exclusive
`signals.jsonl` records each actual wrapper TERM/INT. Native qualification
requires this journal to be empty and its bytes to match the verification's
artifact hash; a signal after JSON serialization invalidates that certificate
even if its provisional `checks_passed` flag was already written. Independent
audit must enforce both requirements.

The relay allows handlers a fixed 500 ms receipt drain, then freezes actual
unfinished request/body-prefix/source-stage evidence and aborts only its owned
outbound connections. It bounds an additional settlement drain to 100 ms and
prevents late callbacks from changing published records. Final host publication
checks the close summary, zero unfinished/transport-error receipts, and no
observer-held response released by close. After owned profile removal, final
Node receipt publication and guard restoration have no intervening asynchronous
step. These observers are not inference latency measurements.

The prior 105-test provider qualification and launcher at `fd0e7d8` are retained.
The original 109 extension and 12 Python qualification and source are preserved
under `preparation-v1`. The corrected prospective wrapper has 116 extension CPU
tests and 45 Python CPU tests; the earlier 30/116 qualification is retained
under `preparation-v2`. These simulate processes and receipts, not
editor/native inference. New output paths are
required after an attempted failure; never overwrite or resume an attempt.

This check does not establish retrieval quality, coding correctness, paid-token
savings, fresh-machine performance or ordinary user-profile activation. VSIX
packaging and release/version decisions remain separate later steps.
