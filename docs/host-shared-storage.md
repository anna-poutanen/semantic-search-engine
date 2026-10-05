# Editor host shared-storage isolation

Future invocations of the Development Host smoke, acceptance, and local VSIX-install helpers now pass `--shared-data-dir` alongside their owned `--user-data-dir` and `--extensions-dir`. The shared directory belongs to the same private temporary run root and is removed through the existing owned-profile cleanup. Both installer SDK-prefix guards reject a supplied shared-data override before invoking an installer or host, including split and `=` argument forms.

VS Code 1.141.0 supports this argument in its local CLI schema. Without it, its shared-storage path falls back to the ordinary user home even when the user-data directory is isolated. Earlier archived helper plans and runs allowed that default. They retain their original source bytes, receipts, scopes, and grades; this future hardening does not retroactively establish their shared-storage isolation. The second ordinary-profile attempt explicitly recorded a user-home shared-storage write and failed cleanup. The third ordinary-profile run supplied the owned flag, recorded both actual owned storage paths, and passed its independent raw-evidence checks.

This change edits host harness scripts and CPU tests only. It preserves the released extension payload: product source, compiled output, package metadata, extension documentation, and the verified 53,874-byte VSIX with SHA-256 `e798b21af047e1aca6b13d4d6e14aa5c6ec0069225eb5cdd611c9f93724aed60`. It does not recompile or repack that asset, alter historical auditors/plans, or inspect, modify, or delete earlier user-home storage. Ordinary v3 remains a zero-model authored HTTP acceptance test, with its disclosed trusted-workspace UX and token-placeholder limits.

Focused CPU checks exercise the existing helper entry points with fake installers and hosts. They verify actual generated launch/install argv and prove conflicting SDK arguments prevent any installer/host call. Existing compiled acceptance/package unit checks also pass; no editor, model, package installation, or frontend compilation is performed by this qualification.

```sh
node --test tests/test_host_shared_storage.cjs
```
