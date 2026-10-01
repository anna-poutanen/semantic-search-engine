'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const config = require('./host-config.cjs');
const { prepareHost } = require('./prepare-host.cjs');
const { startRelay, snapshot, forward } = require('./host-relay.cjs');
const { query, source, mixedSources, cases } = require('./host-fixture.cjs');
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
async function createRunDirectory() {
  // macOS AF_UNIX paths have a 103-byte limit. Worktree paths and the default
  // per-user TMPDIR can both exceed it before VS Code adds its IPC filename.
  const parent = await fs.realpath(process.platform === 'darwin' ? '/tmp' : os.tmpdir());
  const directory = await fs.mkdtemp(path.join(parent, 'sshost-'));
  if (process.platform !== 'win32' && Buffer.byteLength(path.join(directory, 'user-data', '1.14-main.sock')) > 100) {
    await fs.rm(directory, { recursive: true, force: true });
    throw new Error('Temporary host directory exceeds the local IPC path limit');
  }
  return directory;
}
function argumentsFrom(argv) {
  if (!argv.includes('--run')) { throw new Error('Explicit --run is required to launch Electron; prepare/download is a separate command'); }
  const result = {};
  for (let i = 0; i < argv.length; i++) {
    const key = argv[i]; if (key === '--run') { continue; }
    if (key === '--overlays') { if (result.overlays) { throw new Error('Duplicate overlay smoke flag'); } result.overlays = true; continue; }
    if (key === '--saved-edits') { if (result.savedEdits) { throw new Error('Duplicate saved-edit smoke flag'); } result.savedEdits = true; continue; }
    if (key === '--qwen-reranker') { if (result.qwenReranker) { throw new Error('Duplicate Qwen reranker smoke flag'); } result.qwenReranker = true; continue; }
    if (!['--repo', '--url', '--output'].includes(key) || !argv[i + 1]) { throw new Error('Use --run --repo ABSOLUTE_FIXTURE --url LOOPBACK_URL --output ABSOLUTE_JSON'); }
    if (Object.hasOwn(result, key.slice(2))) { throw new Error('Duplicate smoke argument'); }
    result[key.slice(2)] = argv[++i];
  }
  if (!result.repo || !result.url || !result.output || !path.isAbsolute(result.repo) || !path.isAbsolute(result.output)) { throw new Error('Absolute fixture/output paths and URL are required'); }
  if (result.qwenReranker && (result.overlays || result.savedEdits)) { throw new Error('Qwen settings smoke is separate from overlay/saved-edit modes'); }
  return result;
}
function redact(value, replacements) {
  if (typeof value === 'string') { for (const [from, to] of replacements) { value = value.replaceAll(from, to); } return value; }
  if (Array.isArray(value)) { return value.map(item => redact(item, replacements)); }
  if (value && typeof value === 'object') { return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, redact(item, replacements)])); }
  return value;
}
function qwenReceiptChecks(records) {
  const searches = records.filter(row => row.endpoint === '/search' && row.status === 200);
  const qwen = searches.filter(row => row.request?.rerank === true && row.request?.rerank_provider === 'qwen');
  const expectedSettings = row => row.request.retrieval === 'hybrid' && row.request.candidates === 24
    && row.request.max_results === 5 && row.request.token_budget === 2000 && row.request.source_budget === 12000
    && row.request.include_dependencies === false;
  const settingsFast = searches.filter(row => row.request?.rerank === false && row.request?.retrieval === 'hybrid'
    && row.request?.candidates === 24 && !Object.hasOwn(row.request, 'rerank_provider'));
  return { two_qwen_searches: qwen.length === 2,
    saved_and_dirty_qwen: qwen.some(row => !row.request.overlays?.length) && qwen.some(row => row.request.overlays?.length === 1),
    configured_qwen_request_settings: qwen.length === 2 && qwen.every(expectedSettings),
    configured_qwen_response_identity: qwen.length === 2 && qwen.every(row => row.response?.rerank === true && row.response?.rerank_provider === 'qwen'),
    rerank_off_omits_provider: settingsFast.length === 1 && expectedSettings(settingsFast[0]) && settingsFast[0].response?.rerank === false };
}
async function smoke(options) {
  const repo = await fs.realpath(options.repo), output = path.resolve(options.output);
  const extensionPath = options.extensionPath || config.root;
  const before = await snapshot(repo);
  if (before.source !== source) { throw new Error('Smoke requires the fresh three-function host-fixture.cjs repository'); }
  const entries = before.entries;
  const mixed = entries.length === 3 && Object.keys(mixedSources).every(filename => entries.includes(path.basename(filename))
    && before.files[filename]?.source === mixedSources[filename]);
  if (!mixed && (entries.length !== 1 || entries[0] !== 'inventory.py')) { throw new Error('Smoke must use the exact Python-only or mixed-language host-fixture.cjs scope'); }
  if ((options.overlays || options.savedEdits || options.qwenReranker) && !mixed) { throw new Error('Extended smoke requires the mixed fixture'); }
  if (options.qwenReranker && (options.overlays || options.savedEdits)) { throw new Error('Qwen settings smoke is separate from overlay/saved-edit modes'); }
  const preflight = await forward(options.url, '/status', 'GET', Buffer.alloc(0));
  const backend = JSON.parse(preflight.body.toString('utf8'));
  if (preflight.status !== 200 || await fs.realpath(backend.repo) !== repo || backend.source_root !== 'src' || backend.windows !== (mixed ? 5 : 3) || !backend.ready || backend.closed) { throw new Error('Start a ready local backend indexing this exact fixture before launching the host'); }
  const prepared = await prepareHost();
  const directory = await createRunDirectory();
  const userData = path.join(directory, 'user-data'), extensions = path.join(directory, 'extensions');
  await fs.mkdir(path.join(userData, 'User'), { recursive: true }); await fs.mkdir(extensions);
  await fs.writeFile(path.join(userData, 'User', 'settings.json'), JSON.stringify({
    'telemetry.telemetryLevel': 'off', 'extensions.autoCheckUpdates': false, 'extensions.autoUpdate': false,
    'update.mode': 'none', 'workbench.startupEditor': 'none'
  }, null, 2));
  const receiptStarts = path.join(directory, 'search-starts.json');
  const relay = await startRelay(options.url, repo, { holdQuery: query + ' Cancellation transport smoke.',
    onSearchStart: starts => fs.writeFile(receiptStarts, JSON.stringify(starts)) });
  const hostResult = path.join(directory, 'host-result.json');
  const args = [repo, `--extensionDevelopmentPath=${extensionPath}`, `--extensionTestsPath=${path.join(config.root, 'dist/test/host/index.js')}`,
    `--user-data-dir=${userData}`, `--extensions-dir=${extensions}`, '--disable-extensions', '--disable-workspace-trust',
    '--disable-updates', '--skip-welcome', '--skip-release-notes', '--no-cached-data'];
  const replacements = [[repo, '<fixture>'], [directory, '<host-run>'], [extensionPath, '<extension>'],
    ...(extensionPath !== config.root ? [[config.root, '<extension-source>']] : []), [os.homedir(), '<home>']];
  const record = { protocol: 'explicit editor fixture integration smoke, not a ranking benchmark',
    started_utc: new Date().toISOString(), host_preparation: prepared.record,
    backend_status: backend, fixture_mode: mixed ? 'mixed' : 'python', overlay_smoke: options.overlays === true,
    saved_edit_smoke: options.savedEdits === true,
    qwen_reranker_smoke: options.qwenReranker === true,
    workspace_settings_write_policy: options.qwenReranker ? 'Temporary normal fixture folder settings are restored; no user profile settings are changed' : 'No workspace settings writes',
    source_write_policy: options.savedEdits ? 'Controlled fixture edits are restored; final equality does not claim no intervening writes' : 'No fixture source writes',
    source_before_sha256: before.sha256, source_before_manifest_sha256: before.source_manifest_sha256,
    launch_arguments: args, node_version: process.version, passed: false,
    limits: ['Fresh fixture only; no private repository or query.', 'Trusted-path host disables Workspace Trust only in isolated user data; Restricted Mode is not proven.',
      'One test query response is held at the receipt relay until cancel arrives; cancellation receipts/view suppression do not prove interruption of a running Metal forward.',
      'HTTP receipt intervals include this observer; no inference performance claim.'] };
  let child, timeout, killTimer, timedOut = false; const stdout = [], stderr = []; let outSize = 0, errSize = 0;
  try {
    const ended = await new Promise((resolve, reject) => {
      child = spawn(prepared.executable, args, { detached: true, env: { ...process.env,
        SEMANTIC_SEARCH_HOST_REPO: repo, SEMANTIC_SEARCH_HOST_URL: relay.url,
        SEMANTIC_SEARCH_HOST_RESULT: hostResult, SEMANTIC_SEARCH_HOST_QUERY: query,
        SEMANTIC_SEARCH_HOST_OVERLAYS: options.overlays ? '1' : '0',
        SEMANTIC_SEARCH_HOST_SAVED_EDITS: options.savedEdits ? '1' : '0',
        SEMANTIC_SEARCH_HOST_QWEN_RERANKER: options.qwenReranker ? '1' : '0',
        SEMANTIC_SEARCH_HOST_EXTENSION_PATH: options.extensionPath ? extensionPath : '',
        SEMANTIC_SEARCH_HOST_RECEIPT_STARTS: receiptStarts,
        SEMANTIC_SEARCH_HOST_CASES: JSON.stringify(mixed ? cases : cases.slice(0, 1)) }, stdio: ['ignore', 'pipe', 'pipe'] });
      const stop = signal => { try { process.kill(-child.pid, signal); } catch {} };
      timeout = setTimeout(() => { timedOut = true; stop('SIGTERM'); killTimer = setTimeout(() => stop('SIGKILL'), 3000); }, 300000);
      child.on('error', reject); child.on('close', (code, signal) => resolve({ code, signal }));
      child.stdout.on('data', bytes => { outSize += bytes.length; if (outSize <= 256 * 1024) { stdout.push(bytes); } });
      child.stderr.on('data', bytes => { errSize += bytes.length; if (errSize <= 256 * 1024) { stderr.push(bytes); } });
    });
    record.exit = ended; record.timed_out = timedOut;
    record.host = JSON.parse(await fs.readFile(hostResult, 'utf8'));
    const after = await snapshot(repo); record.source_after_sha256 = after.sha256;
    record.source_after_manifest_sha256 = after.source_manifest_sha256;
    // Cancel travels on a separate connection; wait for its receipt to finish.
    await new Promise(resolve => setTimeout(resolve, 250));
    const searches = relay.records.filter(row => row.endpoint === '/search' && row.status === 200);
    const cancels = relay.records.filter(row => row.endpoint === '/cancel' && row.status === 200);
    const sourceChecks = searches.flatMap(row => row.returned_source_checks);
    const cancelLinked = cancels.some(cancel => relay.records.some(search => search.endpoint === '/search'
      && search.request?.request_id === cancel.request?.request_id));
    const exact = row => row.exact_source ?? row.exact_saved_source;
    const overlaySearches = searches.filter(row => row.request?.overlays?.length);
    record.independent_receipt_checks = { searches_with_results: searches.filter(row => row.returned_source_checks.length).length,
      nonempty_exact_source_checks: sourceChecks.length > 0 && sourceChecks.every(exact),
      overlay_input_bindings_verified: !options.overlays || overlaySearches.length >= 2 && overlaySearches.every(row => row.overlay_input_bindings_verified === true),
      cancelled_request_linked_to_search: cancelLinked,
      ...(options.savedEdits ? { source_restored_after_controlled_edits: before.source_manifest_sha256 === after.source_manifest_sha256 }
        : { source_unchanged: before.source_manifest_sha256 === after.source_manifest_sha256 }),
      receipts_without_transport_error: relay.records.every(row => !row.transport_error) };
    if (options.qwenReranker) { record.independent_receipt_checks.qwen_workspace_settings = qwenReceiptChecks(relay.records); }
    record.passed = ended.code === 0 && !timedOut && record.host.passed === true && searches.length >= 2 && cancelLinked
      && sourceChecks.length > 0 && sourceChecks.every(exact) && record.independent_receipt_checks.overlay_input_bindings_verified
      && before.source_manifest_sha256 === after.source_manifest_sha256
      && relay.records.every(row => !row.transport_error)
      && (!options.qwenReranker || Object.values(record.independent_receipt_checks.qwen_workspace_settings).every(Boolean));
    if (!record.passed) { process.exitCode = 1; }
  } catch (error) { record.error = error.message; process.exitCode = 1; }
  finally {
    clearTimeout(timeout); clearTimeout(killTimer);
    if (child?.pid) { try { process.kill(-child.pid, 'SIGTERM'); } catch {} }
    await relay.close();
    record.finished_utc = new Date().toISOString();
    record.receipts = relay.records;
    record.stdout = Buffer.concat(stdout).toString('utf8'); record.stderr = Buffer.concat(stderr).toString('utf8');
    record.stdout_bytes_observed = outSize; record.stderr_bytes_observed = errSize;
    record.stdout_truncated = outSize > 256 * 1024; record.stderr_truncated = errSize > 256 * 1024;
    const publicRecord = redact(record, replacements);
    for (const receipt of publicRecord.receipts) { if (receipt.response) { receipt.redacted_response_sha256 = sha(JSON.stringify(receipt.response)); } }
    await fs.mkdir(path.dirname(output), { recursive: true });
    await fs.writeFile(output, JSON.stringify(publicRecord, null, 2) + '\n');
    await fs.rm(directory, { recursive: true, force: true });
    console.log(JSON.stringify({ passed: record.passed, output, checks: record.independent_receipt_checks ?? null }));
  }
}
module.exports = { argumentsFrom, redact, smoke, createRunDirectory, qwenReceiptChecks };
if (require.main === module) { Promise.resolve().then(() => smoke(argumentsFrom(process.argv.slice(2)))).catch(error => { console.error(error.message); process.exitCode = 1; }); }
