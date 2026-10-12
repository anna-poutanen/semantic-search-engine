'use strict';
// Authored HTTP fixture acceptance, separate from the retained real-Qwen proofs.
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const http = require('node:http');
const config = require('./host-config.cjs');
const { readVsix, validateManifest, verifyInstalled, fileInventory, runCommand, cliEnvironment } = require('./package-host.cjs');
const { createRunDirectory, redact } = require('./smoke-host.cjs');
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const SOURCE = 'def acceptance_message():\n    return "authored fixture ready"\n';
function assert(ok, message) { if (!ok) { throw new Error(message); } }
function argumentsFrom(argv) {
  assert(argv.includes('--run'), 'Explicit --run required for owned VSIX installation and Electron');
  const options = { run: true };
  for (let index = 0; index < argv.length; index++) {
    const flag = argv[index]; if (flag === '--run') { continue; }
    assert(['--mode', '--vsix', '--expected-sha256', '--output'].includes(flag) && argv[index + 1], 'Unknown or incomplete acceptance-host argument');
    const key = flag.slice(2); assert(!Object.hasOwn(options, key), 'Duplicate acceptance-host argument'); options[key] = argv[++index];
  }
  assert(['trusted', 'restricted'].includes(options.mode), 'Use trusted or restricted acceptance mode');
  assert(path.isAbsolute(options.vsix ?? '') && path.isAbsolute(options.output ?? ''), 'Absolute local VSIX/output required');
  assert(/^[0-9a-f]{64}$/.test(options['expected-sha256'] ?? ''), 'Exact frozen VSIX SHA-256 required');
  return options;
}
function settings(mode) {
  assert(['trusted', 'restricted'].includes(mode), 'Unknown acceptance mode');
  return { 'telemetry.telemetryLevel': 'off', 'extensions.autoCheckUpdates': false, 'extensions.autoUpdate': false,
    'update.mode': 'none', 'workbench.startupEditor': 'none', 'security.workspace.trust.enabled': true,
    'security.workspace.trust.startupPrompt': 'never', 'security.workspace.trust.banner': 'never' };
}
function launchArguments(mode, workspaceFile, extensionPath, userData, extensions) {
  return [workspaceFile, `--extensionDevelopmentPath=${extensionPath}`, `--extensionTestsPath=${path.join(config.root, 'dist/test/host/acceptance.js')}`,
    `--user-data-dir=${userData}`, `--extensions-dir=${extensions}`, `--shared-data-dir=${path.join(path.dirname(userData), 'shared-data')}`, '--disable-extensions',
    ...(mode === 'trusted' ? ['--disable-workspace-trust'] : []), '--disable-updates', '--skip-welcome', '--skip-release-notes', '--no-cached-data'];
}
async function protocolInventory() {
  const paths = { 'scripts/acceptance-host.cjs': __filename,
    'test/host/acceptance.ts': path.join(config.root, 'test/host/acceptance.ts'),
    'dist/test/host/acceptance.js': path.join(config.root, 'dist/test/host/acceptance.js'),
    'scripts/package-host.cjs': path.join(config.root, 'scripts/package-host.cjs'),
    'scripts/host-config.cjs': path.join(config.root, 'scripts/host-config.cjs'),
    'scripts/prepare-host.cjs': path.join(config.root, 'scripts/prepare-host.cjs'),
    'scripts/smoke-host.cjs': path.join(config.root, 'scripts/smoke-host.cjs') };
  const result = {};
  for (const [name, filename] of Object.entries(paths)) { const bytes = await fs.readFile(filename); result[name] = { sha256: sha(bytes), bytes: bytes.length }; }
  return result;
}
async function createRepositories(directory, command = runCommand) {
  const paths = { a: path.join(directory, 'repository-a'), b: path.join(directory, 'repository-b') };
  const template = path.join(directory, 'empty-git-template'); await fs.mkdir(template);
  const env = { ...Object.fromEntries(Object.entries(cliEnvironment(process.env)).filter(([name]) => !name.startsWith('GIT_'))),
    GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null', GIT_CONFIG_SYSTEM: '/dev/null' };
  for (const repository of Object.values(paths)) {
    await fs.mkdir(path.join(repository, 'src'), { recursive: true }); await fs.writeFile(path.join(repository, 'src/acceptance.py'), SOURCE);
    const init = await command('git', ['init', '--quiet', `--template=${template}`, repository], { env }); assert(init.code === 0, 'Cannot initialize owned repository');
    const add = await command('git', ['-C', repository, 'add', 'src/acceptance.py'], { env }); assert(add.code === 0, 'Cannot track owned source');
  }
  const workspaceFile = path.join(directory, 'acceptance.code-workspace');
  // The indexed folder is second: removing it keeps the first-folder identity.
  await fs.writeFile(workspaceFile, JSON.stringify({ folders: [{ path: paths.b, name: 'Other authored repository' },
    { path: paths.a, name: 'Indexed authored repository' }] }, null, 2));
  return { ...paths, workspaceFile };
}
function status(repository) {
  return { repo: repository, source_root: 'src', source_exclusions: [], closed: false, ready: true, windows: 1,
    last_error: null, source_manifest: sha(SOURCE), watcher: { running: false, pending: false, last_error: null },
    acceptance_fixture: true, model_loaded: false };
}
function searchResponse(repository, request) {
  assert(request && typeof request.query === 'string' && request.query.length <= 10000 && !request.overlays?.length,
    'Fixture accepts only bounded saved-source search');
  assert(request.retrieval === 'dense' && request.rerank === false && Number.isSafeInteger(request.token_budget)
    && request.token_budget >= 512 && Number.isSafeInteger(request.source_budget) && request.source_budget >= SOURCE.length,
    'Unexpected fixture search settings');
  return { repo: repository, source_root: 'src', query: request.query, retrieval: request.retrieval, rerank: false,
    results: [{ id: 'src/acceptance.py:acceptance_message:1-2', path: 'src/acceptance.py', symbol: 'acceptance_message',
      start_line: 1, end_line: 2, window_end_line: 2, source: SOURCE, source_hash: sha(SOURCE), content_hash: sha(SOURCE),
      retrieval_score: null, h2o_score: null, truncated: false, origin: 'retrieval', related_to: null }],
    match_status: 'candidates', source_budget: request.source_budget, total_source_chars: SOURCE.length, budget_exhausted: false,
    token_usage: { encoding: 'cl100k_base', tokens: 128, budget: request.token_budget, truncated: false },
    indexstats: { total_windows: 1, source_manifest: sha(SOURCE) }, acceptance_fixture: true };
}
async function startFixtureService(repository) {
  const records = [], controls = []; const token = crypto.randomUUID(); let address, closing = false;
  const server = http.createServer((request, reply) => {
    const respond = (code, body) => { reply.writeHead(code, { 'Content-Type': 'application/json' }); reply.end(JSON.stringify(body)); };
    const controlled = request.url?.startsWith('/__acceptance_');
    const authority = request.headers.host === address?.host && !request.headers.origin;
    const authenticated = request.headers['x-acceptance-control'] === token;
    const recognizedControl = request.method === 'GET' && request.url === '/__acceptance_receipts'
      || request.method === 'POST' && request.url === '/__acceptance_stop';
    const row = { endpoint: request.url, method: request.method, observed_utc: new Date().toISOString() };
    // Every received attempt counts, including rejected headers/control access.
    // Only our exact authenticated observer/stop operations are excluded.
    if (!(authority && authenticated && recognizedControl)) { records.push(row); }
    if (!authority) { row.status = 403; respond(403, { error: 'Owned loopback fixture only' }); return; }
    if (controlled && !authenticated) { row.status = 403; respond(403, { error: 'Owned control capability required' }); return; }
    let size = 0; const buffers = [];
    request.on('error', () => reply.destroy());
    request.on('data', bytes => { size += bytes.length; if (size > 16384) { row.status = null; row.error = 'Fixture body cap exceeded'; request.destroy(); } else { buffers.push(bytes); } });
    request.on('end', () => {
      if (controlled) {
        controls.push({ endpoint: request.url, method: request.method });
        if (request.method === 'GET' && request.url === '/__acceptance_receipts') { respond(200, { product_requests: records.length, records }); return; }
        if (request.method === 'POST' && request.url === '/__acceptance_stop') { respond(200, { closing: true }); closing = true; server.close(); return; }
        row.status = 404; respond(404, { error: 'Unknown owned control' }); return;
      }
      try {
        if (request.method === 'GET' && request.url === '/status') { row.status = 200; respond(200, status(repository)); return; }
        if (request.method === 'POST' && request.url === '/search') {
          const body = JSON.parse(Buffer.concat(buffers).toString('utf8')); const response = searchResponse(repository, body);
          row.status = 200; row.request = body; row.response = response; respond(200, response); return;
        }
        row.status = 404; respond(404, { error: 'No model or other service endpoint in this fixture' });
      } catch (error) { row.status = 400; row.error = error.message; respond(400, { error: error.message }); }
    });
  });
  server.requestTimeout = 10000; server.headersTimeout = 10000;
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  address = new URL(`http://127.0.0.1:${server.address().port}`);
  return { url: address.origin, token, records, controls, get closed() { return !server.listening; },
    async close() { closing = true; server.closeAllConnections(); if (server.listening) { await new Promise(resolve => server.close(resolve)); } },
    get requested_stop() { return closing; } };
}
async function acceptanceHost(options, dependencies = {}) {
  assert(options.run === true && ['trusted', 'restricted'].includes(options.mode) && path.isAbsolute(options.vsix ?? '')
    && path.isAbsolute(options.output ?? '') && /^[0-9a-f]{64}$/.test(options['expected-sha256'] ?? ''), 'Invalid explicit acceptance options');
  try { await fs.lstat(options.output); throw new Error('Refusing existing acceptance receipt'); } catch (error) { if (error.code !== 'ENOENT') { throw error; } }
  const archive = await (dependencies.readVsix ?? readVsix)(options.vsix); assert(archive.sha256 === options['expected-sha256'], 'Frozen VSIX hash mismatch');
  const manifest = JSON.parse(await fs.readFile(path.join(config.root, 'package.json'), 'utf8'));
  const expected = { id: `${manifest.publisher}.${manifest.name}`, version: manifest.version }; validateManifest(archive.manifest, expected);
  assert(archive.manifest.capabilities?.untrustedWorkspaces?.supported === false, 'Frozen Restricted Mode capability changed');
  const prepared = await (dependencies.prepareHost ?? require('./prepare-host.cjs').prepareHost)();
  const directory = await (dependencies.createRunDirectory ?? createRunDirectory)(); let service;
  const record = { classification: 'Installed frozen payload in an owned Development Host with authored HTTP fixture; no real backend/model',
    mode: options.mode, passed: false, started_utc: new Date().toISOString(), model_loaded: false,
    vsix: { sha256: archive.sha256, bytes: archive.bytes }, host_preparation: prepared.record,
    limits: ['Authored deterministic HTTP fixture; no retrieval accuracy, actual embedding/token usage, or inference performance claim.',
      'This is a Development Host; ordinary-profile Restricted Mode activation/enablement is not established.',
      'Restricted Mode must be observed false before guarded commands, without trust/support overrides; failure is retained.'] };
  try {
    record.protocol_before = await protocolInventory();
    const userData = path.join(directory, 'user-data'), extensions = path.join(directory, 'extensions');
    await fs.mkdir(path.join(userData, 'User'), { recursive: true }); await fs.mkdir(extensions);
    record.settings = settings(options.mode); await fs.writeFile(path.join(userData, 'User/settings.json'), JSON.stringify(record.settings));
    if (!dependencies.resolveCli) { assert(require('@vscode/test-electron/package.json').version === config.test_electron_version, 'Host SDK differs from reviewed pin'); }
    record.test_electron_version = config.test_electron_version;
    const resolveCli = dependencies.resolveCli ?? require('@vscode/test-electron').resolveCliArgsFromVSCodeExecutablePath;
    const [cli, ...prefix] = resolveCli(prepared.executable, { reuseMachineInstall: true });
    assert(path.isAbsolute(cli) && !prefix.some(arg => /^--(user-data-dir|extensions-dir|shared-data-dir)/.test(arg)), 'Unexpected native CLI profile defaults');
    const args = [...prefix, `--user-data-dir=${userData}`, `--extensions-dir=${extensions}`, `--shared-data-dir=${path.join(directory, 'shared-data')}`, '--install-extension', options.vsix, '--force', '--do-not-sync'];
    const install = await (dependencies.runCommand ?? runCommand)(cli, args, { env: cliEnvironment(process.env), timeoutMs: 60000, limit: 256 * 1024 });
    record.install_arguments = [cli, ...args]; record.install_receipt = { code: install.code, signal: install.signal,
      stdout: install.stdout.toString('utf8'), stderr: install.stderr.toString('utf8'), owned_process_group_stopped: install.owned_process_group_stopped ?? null };
    assert(install.code === 0, 'Owned frozen VSIX installation failed');
    const folders = [];
    for (const name of await fs.readdir(extensions)) { const candidate = path.join(extensions, name), info = await fs.lstat(candidate);
      if (info.isDirectory() && !info.isSymbolicLink()) { folders.push(candidate); } }
    assert(folders.length === 1, 'Expected exactly one ordinary installed payload'); const extensionPath = await fs.realpath(folders[0]);
    assert(path.dirname(extensionPath) === await fs.realpath(extensions), 'Installed extension escaped owned root');
    record.installed_extension_path = extensionPath; record.installed_inventory_before_validation = await fileInventory(extensionPath);
    record.payload_before_host = await verifyInstalled(extensionPath, archive, expected);
    const repositories = await (dependencies.createRepositories ?? createRepositories)(directory);
    record.fixture = { ...repositories, source: SOURCE, source_sha256: sha(SOURCE), model_loaded: false,
      token_accounting: '128 is a schema fixture placeholder, not independent cl100k encoding or provider/model usage' };
    record.source_before = { a: sha(await fs.readFile(path.join(repositories.a, 'src/acceptance.py'))), b: sha(await fs.readFile(path.join(repositories.b, 'src/acceptance.py'))) };
    service = await (dependencies.startFixtureService ?? startFixtureService)(repositories.a);
    const hostResult = path.join(directory, 'host-result.json'), hostArgs = launchArguments(options.mode, repositories.workspaceFile, extensionPath, userData, extensions);
    record.launch_arguments = [prepared.executable, ...hostArgs];
    const host = await (dependencies.runCommand ?? runCommand)(prepared.executable, hostArgs, { env: { ...cliEnvironment(process.env),
      SEMANTIC_ACCEPTANCE_MODE: options.mode, SEMANTIC_ACCEPTANCE_A: repositories.a, SEMANTIC_ACCEPTANCE_B: repositories.b,
      SEMANTIC_ACCEPTANCE_URL: service.url, SEMANTIC_ACCEPTANCE_CONTROL: service.token, SEMANTIC_ACCEPTANCE_EXTENSION: extensionPath,
      SEMANTIC_ACCEPTANCE_RESULT: hostResult }, timeoutMs: 175000, limit: 512 * 1024 });
    record.host_exit = { code: host.code, signal: host.signal, owned_process_group_stopped: host.owned_process_group_stopped ?? null };
    record.host_stdout = host.stdout.toString('utf8'); record.host_stderr = host.stderr.toString('utf8');
    record.host = JSON.parse(await fs.readFile(hostResult, 'utf8'));
    record.payload_after_host = await verifyInstalled(extensionPath, archive, expected);
    record.source_after = { a: sha(await fs.readFile(path.join(repositories.a, 'src/acceptance.py'))), b: sha(await fs.readFile(path.join(repositories.b, 'src/acceptance.py'))) };
    record.source_unchanged = JSON.stringify(record.source_before) === JSON.stringify(record.source_after);
    record.vsix_unchanged = sha(await fs.readFile(options.vsix)) === archive.sha256;
    record.protocol_after = await protocolInventory(); record.protocol_unchanged = JSON.stringify(record.protocol_before) === JSON.stringify(record.protocol_after);
    record.passed = host.code === 0 && record.host.passed === true && record.source_unchanged && record.vsix_unchanged
      && record.protocol_unchanged
      && (options.mode === 'restricted' ? record.host.observed_trusted === false && service.records.length === 0
        : record.host.observed_trusted === true && service.requested_stop && service.closed);
    assert(record.passed, 'Acceptance checks did not establish the requested gates');
  } catch (error) { record.error = error.message; if (!dependencies.runCommand) { process.exitCode = 1; } }
  finally {
    if (service) { await service.close(); record.fixture_requests = service.records; record.fixture_controls = service.controls;
      record.owned_listener_closed = service.closed; }
    await fs.rm(directory, { recursive: true, force: true }); record.owned_profile_removed = true; record.finished_utc = new Date().toISOString();
    const publicRecord = redact(record, [[directory, '<acceptance-run>'], [options.vsix, '<release-vsix>'], [config.root, '<extension-source>'],
      ...(service ? [[service.token, '<owned-control-capability>']] : []), [os.homedir(), '<home>']]);
    await fs.mkdir(path.dirname(options.output), { recursive: true }); await fs.writeFile(options.output, JSON.stringify(publicRecord, null, 2) + '\n');
  }
  return record;
}
module.exports = { SOURCE, argumentsFrom, settings, launchArguments, createRepositories, status, searchResponse, startFixtureService, acceptanceHost };
if (require.main === module) { acceptanceHost(argumentsFrom(process.argv.slice(2))).then(record => {
  console.log(JSON.stringify({ passed: record.passed, mode: record.mode, output: process.argv[process.argv.indexOf('--output') + 1] }));
}).catch(error => { console.error(error.message); process.exitCode = 1; }); }
