# Prospective app-server usage observability diagnostic

This diagnostic is separate from the frozen coding comparison. It cannot replace its six missing usage receipts, estimate their tokens, or change those outcomes. No model call or app-server process was started during scaffold preparation; offline tests use authored JSON streams.

The installed CLI is `0.162.0-alpha.17.2`. Its generated JSON schemas are the source for exact field names. The official [App Server documentation](https://learn.chatgpt.com/docs/app-server) describes the initialization handshake, streaming turn/item events, `turn/completed` statuses and `thread/tokenUsage/updated`. It describes thread usage updates without promising a notification for every provider response. Installed help advertises app-server configuration overrides and strict configuration validation; it does not advertise `--ignore-user-config` or `--ignore-rules`.

`ThreadTokenUsageUpdatedNotification` requires thread ID, turn ID and a `tokenUsage` object containing `last` and `total`. Each breakdown requires input, cached input, output, reasoning output and total token fields; cache-write input is optional. Missing optional quantities stay null. Cached input and reasoning output are subcategories, so they are never added to gross input/output. The collector preserves the reported total and separately checks input plus output. It keeps every duplicate/cumulative snapshot and never adds snapshots together. Arithmetic differences between consecutive thread snapshots are descriptive deltas; they do not identify individual provider responses. `GetAccountTokenUsage.threadUsage` is explicitly estimated and is never requested.

The proposed run creates one ephemeral app-server conversation and sends two sequential word-only prompts using exactly `gpt-6.1-sol`, `ultra`, and standard `default` service tier. Each turn has a 120-second observer deadline. No retries, tool requests, agent delegation, file reads, model substitution or historic-account-usage queries are permitted. A failed first turn prevents the second. Missing notifications remain missing even when a final answer arrives. Malformed notifications and decreasing cumulative counters remain recorded issues. Two plain turns can establish observed cadence for this small case; they cannot establish coverage during multi-response tool workflows, compaction, retries or interruption.

Normal account authentication remains available to the native server. The probe does not read authentication files, modify config or reset `HOME`/`CODEX_HOME`. It removes inherited API-token, desktop bridge and Git directory-routing variables without printing their values. Its working directory is a newly owned empty temporary directory under `/private/tmp`, outside the project, home and every Git ancestor. The filesystem gate runs before starting app-server. Only that owned directory is removed in `finally`, including gate/startup failures; ignored raw and public artifacts remain. Its actual random path is private/redacted. Overrides disable MCP tables, plugin/hook/app features, agent features, skill catalogs, environment access and ordinary tool features. Explicit base/developer instructions request plain words. Effective-config values must match every requested override; returned model, effort, tier, provider, ephemeral state, read-only policy and empty instruction-file sources must match. The MCP inventory must be empty before either `turn/start` request.

These gates do not prove an empty native tool catalog, complete exclusion of every inherited configuration layer/rule, or OS read isolation. The installed protocol says `multiAgentMode` is ignored and a saved `disabledPluginIds` list does not yet filter capabilities; this probe relies on feature overrides and observed gates instead. If effective values are unavailable or a gate fails, the script stops before inference. Unexpected server requests, tool/hook/agent items or model rerouting stop the attempt. Root must review these limitations before executing it.

Exact private stream lines and observer UTC/monotonic timestamps go to ignored `.cache/usage-notification-probe/run/raw-events.jsonl`. Publication uses a whitelist: numeric usage, validated thread/turn/item IDs, statuses, numeric error codes, timing observations and our declared settings. Only exact known `ALPHA`/`BETA` replies are copied; unexpected final text publishes hash/size metadata. Reasoning contents, stderr text, arbitrary RPC results and unknown notification fields are withheld; original line hashes bind them to the private capture. Matched configuration values publish our own requested constants. Unmatched inherited values publish only type/count/length and explicit missing/null metadata, with no entry names, values or credential-derived digests. Raw effective configuration is never published. Observer timestamps can bunch together because of pipe buffering and do not establish response or tool execution latency. Captured diagnostic token notifications are reported quantities, not a price calculation or independently verified bill.

Generate the schema locally without starting the server, then prepare the plan:

```sh
codex app-server generate-json-schema --experimental --out .cache/usage-notification-probe/schema
.venv/bin/python scripts/probe_usage_notifications.py
.venv/bin/python -m pytest tests/test_usage_notifications.py -q
```

The default command writes a prospective plan into `results/usage-notifications-2026-10-13-preflight`. It sends no RPC. After root review and explicit authorization for the one bounded diagnostic:

```sh
.venv/bin/python scripts/probe_usage_notifications.py --execute --acknowledge-inherited-config-limitations
```

Each attempt refuses to overwrite an existing output or raw-run directory. An unsuccessful transport/config gate is an outcome to preserve, rather than a reason to silently change the settings or retry. All future benchmark adoption requires a new protocol and appropriate intermediate-response/interruption checks; this scaffold changes no frozen runner.
