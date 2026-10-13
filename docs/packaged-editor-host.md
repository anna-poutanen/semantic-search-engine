# Packaged editor host check

`extensions/vscode/scripts/package-host.cjs` installs a caller-provided, frozen local VSIX into its own short temporary extensions directory. It then runs the existing editor integration smoke with `--extensionDevelopmentPath` pointing to that installed payload. This proves the packaged JavaScript can activate and serve the smoke workflow in an Extension Development Host. It does not establish activation in an ordinary user profile or marketplace installation.

The launcher requires explicit `--run`, an absolute VSIX path, its independently recorded SHA-256, a ready loopback service indexing the exact smoke fixture, and a new absolute output path. It refuses to overwrite an existing receipt. Compile the test entry before running; compilation does not repackage or alter the frozen VSIX.

```sh
cd extensions/vscode
npm run compile
node scripts/package-host.cjs --run \
  --vsix /absolute/path/semantic-search-local-0.1.0.vsix \
  --expected-sha256 FROZEN_64_CHARACTER_LOWERCASE_SHA256 \
  --repo /absolute/path/mixed-host-fixture \
  --url http://127.0.0.1:8765 \
  --output /absolute/path/new-packaged-host.json \
  --overlays
```

The prospective packaged run uses the saved-source and dirty-overlay checks with `--overlays` and requires unchanged fixture source. The service is started separately. Both extended modes require the mixed fixture from `scripts/host-fixture.cjs`.

The launcher also supports optional `--saved-edits`, but that distinct packaged phase is not covered by the prospective overlay-only check. Earlier saved-edit evidence comes from its separately recorded source host. Saved-edit checks deliberately modify the disposable fixture and restore its bytes; final equality does not mean no intervening writes. The same smoke uses its existing independent HTTP receipt relay, source checks and cancellation tests.

The installer uses the pinned `@vscode/test-electron` CLI resolver and explicit owned user-data/extensions paths. On macOS the shared short-directory helper uses canonical `/private/tmp`; no user's VS Code profile is used. Running-editor CLI routing variables and Node injection options are removed from the installer environment. Only the local VSIX path is requested; dependency/extension-pack installation requests are refused. Owned profiles and processes are cleaned up after the check.

The receipt binds the VSIX bytes and all installed payload members, validates publisher/name/version and the entry point, and compares every member before and after the host. The installer may add only `package.json.__metadata`; that field is excluded from semantic manifest equality and its presence is recorded. All compiled JavaScript/map hashes are retained. The host checks its actual `extension.extensionPath` against the installed path before activation. Installation arguments/stdout/stderr, host/HTTP receipts and cleanup outcome are recorded with local paths redacted.

The pinned VS Code installer also extracts the ZIP's top-level `extension.vsixmanifest` as `.vsixmanifest`: its [extension extractor](https://github.com/microsoft/vscode/blob/2a59476c9bfcb90b3ddc372c36762471b7dfad1c/src/vs/platform/extensionManagement/node/extensionManagementService.ts#L589) passes the `extension` prefix to the [ZIP helper](https://github.com/microsoft/vscode/blob/2a59476c9bfcb90b3ddc372c36762471b7dfad1c/src/vs/base/node/zip.ts#L299), which strips that prefix. The gate admits this single metadata file only when its complete bytes match that exact frozen container member. All other extra/missing files and changed runtime or container bytes remain failures. The receipt retains expected and observed inventories before validation, plus both normalized package manifests after successful comparison. The original strict membership failure is preserved as attempt 01, before this evidence-based correction; it performed no host search.

The test entry itself comes from the repository's compiled `dist/test/host/index.js`; it is not shipped in the VSIX. The extension under test comes from the verified installed payload. Historical saved/overlay host evidence remains bound to its original source revision rather than being rewritten after this launcher change.

CPU tests use fake installers/hosts, synthetic ZIPs and harmless bounded subprocesses. They exercise wrong hashes/manifests, extra/missing/symlink payload members, changed package code, absent activation-path proof, installer failure and cleanup. They make no inference, Electron, marketplace or real installation request.
