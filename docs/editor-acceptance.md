# Separate editor acceptance checks

This prospective check runs the frozen installed VSIX in a fresh owned VS Code
Extension Development Host. Its HTTP server returns an authored literal Python
fixture with matching saved bytes. It starts no real semantic-search backend,
embedding model or H2O service. Real Qwen editor proofs are recorded separately;
this test makes no retrieval-quality, provider-token, model or performance claim.
The response's `token_usage.tokens=128` is a schema fixture placeholder, not an
independent cl100k encoding or actual model usage.

The two modes use separately owned short temporary user-data/extensions roots
and authored two-folder workspaces. No ordinary editor profile or existing
repository is opened. The local VSIX requires an independently frozen SHA-256,
exact publisher/name/version/entry point, semantic manifest equality except
installer `__metadata`, and exact payload bytes including the container-bound
`.vsixmanifest`. These checks reuse the reviewed strict package verifier. The
launcher captures the actual installed extension path, before/after payload and
protocol hashes, owned request/control receipts, source equality, process-group
cleanup and profile/listener cleanup. It refuses existing output files.

```sh
cd extensions/vscode
npm run compile
node scripts/acceptance-host.cjs --run --mode restricted \
  --vsix /absolute/path/semantic-search-local-0.1.0.vsix \
  --expected-sha256 FROZEN_64_CHARACTER_LOWERCASE_SHA256 \
  --output /absolute/path/new-restricted-acceptance.json
node scripts/acceptance-host.cjs --run --mode trusted \
  --vsix /absolute/path/semantic-search-local-0.1.0.vsix \
  --expected-sha256 FROZEN_64_CHARACTER_LOWERCASE_SHA256 \
  --output /absolute/path/new-trusted-acceptance.json
```

The restricted launch enables Workspace Trust and suppresses the startup trust
prompt in its owned profile. It passes neither `--disable-workspace-trust` nor
an untrusted-support override. Before any product command, the test must observe
actual `vscode.workspace.isTrusted=false`; a different observed state is a
retained failure. It activates the exact installed development payload, checks
that the API getter and programmatic connect/search/status guards reject, and
requires zero HTTP attempts at the owned fixture. All received attempts count,
including rejected Host/Origin/control requests; only exact authenticated owned
observer and stop operations are excluded.

A Development Host can enable development extensions despite the ordinary
manifest enablement policy. This test therefore exercises product guards with
actual false trust, and does not establish ordinary installed-extension
activation policy in Restricted Mode. The pinned [trust calculation](https://github.com/microsoft/vscode/blob/2a59476c9bfcb90b3ddc372c36762471b7dfad1c/src/vs/workbench/services/workspaces/common/workspaceTrust.ts#L273),
[startup-prompt handling](https://github.com/microsoft/vscode/blob/2a59476c9bfcb90b3ddc372c36762471b7dfad1c/src/vs/workbench/contrib/workspace/browser/workspace.contribution.ts#L472)
and [development-extension enablement](https://github.com/microsoft/vscode/blob/2a59476c9bfcb90b3ddc372c36762471b7dfad1c/src/vs/workbench/services/extensions/common/abstractExtensionService.ts#L1326)
explain that distinction. The launcher directly invokes the pinned executable;
SDK `runTests` is deliberately not used because pinned SDK 3.1.0 injects the
trust-disable flag itself.

The trusted launch disables trust only in its owned profile. It opens two fresh
repositories in a saved workspace file: an unrelated first folder and the
indexed second folder. Connection against the wrong folder must return an error
without a search; the correct folder must connect and yield the exact saved toy
source. Removing that connected second folder must clear connection, status,
response and entries. Programmatic search/reconnect against the removed folder
must reject without another HTTP attempt. The first folder stays fixed to avoid
first-folder extension-host restart. After restoring the owned indexed folder,
the test reconnects, stops only its own fixture listener, observes actual
connection refusal at that port, and requires status/search to settle with error
and no result. Every workspace update waits for its folder event.

The direct host subprocess has a 175-second deadline and bounded process-group
escalation; the host phase is capped below 180 seconds including cleanup.
Individual API/control operations are bounded. An inability to establish false
trust, activate the development payload, observe workspace changes or settle an
offline operation remains a failure. No runtime capability override, retry or
product repair is made by the launcher.

CPU checks use fake native installer/host callbacks, real owned HTTP listeners,
public response-schema validation and harmless source fixtures. They distinguish
these synthetic checks from actual Electron execution. Native runs are explicit
and separately recorded only after source and CPU peer review.
