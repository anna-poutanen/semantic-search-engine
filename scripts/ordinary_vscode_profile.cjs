'use strict';
// CPU metadata preparation by default. Installation/Electron require explicit --run.
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const http = require('node:http');
const {spawn, execFileSync} = require('node:child_process');
const assert = require('node:assert/strict');
const ROOT = path.resolve(__dirname, '..');
const EXT = path.join(ROOT, 'extensions/vscode');
const SIDECAR = path.join(__dirname, 'ordinary_vscode_sidecar');
const config = require(path.join(EXT, 'scripts/host-config.cjs'));
const SOURCE = 'def ordinary_message():\n    return "owned ordinary profile fixture"\n';
const DEADLINE = Date.parse('2026-10-14T13:37:05Z');
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const json = filename => JSON.parse(fs.readFileSync(filename, 'utf8'));
const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object'
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
const same = (a, b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
function exclusive(filename, value) { fs.writeFileSync(filename, JSON.stringify(value, null, 2) + '\n', {flag: 'wx', mode: 0o600}); }
function inventory(directory, ignore = () => false) {
  const found = {};
  function visit(relative) {
    for (const name of fs.readdirSync(path.join(directory, relative)).sort()) {
      const key = relative ? relative + '/' + name : name;
      if (ignore(key)) { continue; }
      const filename = path.join(directory, key), stat = fs.lstatSync(filename);
      assert.ok(!stat.isSymbolicLink(), 'Inventory refuses symlinks: ' + key);
      if (stat.isDirectory()) { visit(key); }
      else { assert.ok(stat.isFile()); found[key] = {bytes: stat.size, sha256: sha(fs.readFileSync(filename))}; }
    }
  }
  visit(''); return found;
}
function environment(base = process.env) {
  const remove = /^(VSCODE_|ORDINARY_PROFILE_|SEMANTIC_|ELECTRON_|NODE_|GIT_)/;
  return {...Object.fromEntries(Object.entries(base).filter(([key]) => !remove.test(key)
    && !/^(HTTP_PROXY|HTTPS_PROXY|ALL_PROXY|NO_PROXY)$/i.test(key))),
    GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null', GIT_CONFIG_SYSTEM: '/dev/null'};
}
function settings() {
  return {'telemetry.telemetryLevel': 'off', 'extensions.autoCheckUpdates': false,
    'extensions.autoUpdate': false, 'update.mode': 'none', 'workbench.startupEditor': 'none',
    'security.workspace.trust.enabled': true, 'security.workspace.trust.startupPrompt': 'never',
    'security.workspace.trust.banner': 'never', 'window.restoreWindows': 'none'};
}
function ipcPathReceipt(userData, version = config.version) {
  const filename = path.join(userData, version.slice(0, 4) + '-main.sock');
  const bytes = Buffer.byteLength(filename, 'utf8');
  assert.ok(bytes <= 103, 'Owned Code Unix socket path exceeds macOS 103-byte limit');
  return {path: filename, utf8_bytes: bytes, maximum_bytes: 103, version_prefix: version.slice(0, 4)};
}
function shortOwnedRoot() {
  // /tmp is deliberately shorter than the macOS per-user os.tmpdir() path.
  const base = fs.realpathSync('/tmp'); assert.ok(fs.statSync(base).isDirectory());
  const root = fs.mkdtempSync(path.join(base, 'ssp-')); fs.chmodSync(root, 0o700);
  assert.equal(fs.realpathSync(root), root);
  for (const mode of ['trusted', 'restricted']) { ipcPathReceipt(path.join(root, mode, 'user-data')); }
  return root;
}
function launchArgs(mode, workspace, userData, extensions) {
  assert.ok(['trusted', 'restricted'].includes(mode));
  for (const name of [workspace, userData, extensions]) { assert.ok(path.isAbsolute(name)); }
  ipcPathReceipt(userData);
  return [workspace, '--new-window', `--user-data-dir=${userData}`, `--extensions-dir=${extensions}`,
    '--disable-updates', '--skip-welcome', '--skip-release-notes', '--no-cached-data',
    ...(mode === 'trusted' ? ['--disable-workspace-trust'] : [])];
}
function prepare() {
  const base = path.join(config.cache, `vscode-${config.platform}-${config.version}`);
  const app = path.join(base, 'Visual Studio Code.app/Contents');
  const executable = path.join(app, 'MacOS/Code'), cli = path.join(app, 'Resources/app/bin/code');
  assert.ok(fs.existsSync(path.join(base, 'is-complete')), 'Existing prepared host is required; no download fallback');
  assert.equal(sha(fs.readFileSync(executable)), config.expected_executable_sha256);
  assert.equal(json(path.join(app, 'Resources/app/product.json')).commit, config.commit);
  assert.equal(json(path.join(app, 'Resources/app/package.json')).version, config.version);
  const vsce = path.join(EXT, 'node_modules/@vscode/vsce'); assert.equal(json(path.join(vsce, 'package.json')).version, '4.0.0');
  const target = json(path.join(EXT, 'package.json'));
  assert.equal(target.capabilities.untrustedWorkspaces.supported, false);
  assert.equal(`${target.publisher}.${target.name}`, 'anna-poutanen.semantic-search-local');
  assert.equal(target.version, '0.1.0'); assert.equal(target.main, './dist/src/extension.js');
  const excluded = name => /^(node_modules|\.cache|\.git)(\/|$)/.test(name) || name.endsWith('.vsix');
  return {schema: 1, classification: 'Prospective CPU-only metadata freeze; no packaging/install/editor/model execution',
    prepared_utc: new Date().toISOString(), deadline_utc: new Date(DEADLINE).toISOString(),
    model_calls: 0, inference_calls: 0, editor_launches: 0,
    host: {executable, cli, version: config.version, commit: config.commit,
      executable_sha256: config.expected_executable_sha256, cli_sha256: sha(fs.readFileSync(cli))},
    node: {executable: process.execPath, version: process.version, sha256: sha(fs.readFileSync(process.execPath))},
    vsce: {path: vsce, version: '4.0.0', files: inventory(vsce, name => name.startsWith('node_modules/'))},
    source: {target: inventory(EXT, excluded), sidecar: inventory(SIDECAR),
      helper: {path: path.relative(ROOT, __filename), sha256: sha(fs.readFileSync(__filename))}},
    expected: {id: `${target.publisher}.${target.name}`, version: target.version,
      sidecar_id: 'semantic-search-evaluation.semantic-search-profile-probe', sidecar_version: '0.0.1'},
    owned_profile_location_policy: {canonical_base: fs.realpathSync('/tmp'), prefix: 'ssp-', maximum_socket_path_bytes: 103},
    modes: ['trusted', 'restricted'], fixture: {source: SOURCE, sha256: sha(SOURCE), token_count_is_placeholder: true},
    limits: ['Trusted profile explicitly disables workspace trust; trust-enabled trusted-folder UX is not established.',
      'Restricted profile retains trust enablement without extension support overrides or forced activation.',
      'Authored loopback schema fixture uses no model/retrieval/tokenizer; token count is a placeholder.',
      'Local VSIX install and ordinary Code only; no development/test-host arguments and no user profile use.']};
}
function checkPlan(plan) {
  const observed = prepare();
  for (const key of ['schema', 'deadline_utc', 'host', 'node', 'vsce', 'source', 'expected', 'owned_profile_location_policy', 'modes', 'fixture']) {
    assert.ok(same(plan[key], observed[key]), 'Prepared input drift: ' + key);
  }
}
function groupAlive(pid) {
  try { process.kill(-pid, 0); return true; } catch (error) { if (error.code === 'ESRCH') { return false; } throw error; }
}
function assertOwner(owner, handle) {
  const disk = json(path.join(owner.root, 'owner.json'));
  assert.equal(disk.nonce, owner.nonce); assert.equal(fs.realpathSync(owner.root), owner.root);
  assert.ok(handle && handle.nonce === owner.nonce && Number.isSafeInteger(handle.pid) && handle.pid > 1 && handle.pgid === handle.pid);
  assert.ok(owner.handles.has(handle), 'Unowned process handle');
}
function signalOwned(owner, handle, signal) {
  assertOwner(owner, handle);
  try { process.kill(-handle.pgid, signal); } catch (error) { if (error.code !== 'ESRCH') { throw error; } }
}
// Detached ownership is registered synchronously, before returning a Promise or awaiting.
function startOwned(owner, command, args, options = {}) {
  assert.equal(fs.realpathSync(owner.root), owner.root);
  assert.equal(json(path.join(owner.root, 'owner.json')).nonce, owner.nonce);
  assert.ok(!owner.interrupted); assert.ok(Date.now() < (owner.deadline ?? DEADLINE));
  const child = spawn(command, args, {cwd: options.cwd ?? ROOT, env: options.env ?? environment(),
    detached: true, stdio: ['ignore', 'pipe', 'pipe']});
  const handle = {nonce: owner.nonce, pid: child.pid, pgid: child.pid, command, arguments: args,
    started_utc: new Date().toISOString(), stdout: '', stderr: '', stdout_bytes: 0, stderr_bytes: 0,
    child, settled: false, overflow: false, kind: options.kind ?? 'command'};
  child.once('error', error => { handle.error = error.message; });
  assert.ok(Number.isSafeInteger(handle.pid) && handle.pid > 1, 'Spawn supplied no owned PID');
  owner.handles.add(handle); assertOwner(owner, handle);
  handle.exit = new Promise(resolve => {
    for (const key of ['stdout', 'stderr']) { child[key].on('data', bytes => {
      handle[key + '_bytes'] += bytes.length;
      if (handle[key + '_bytes'] <= 512 * 1024) { handle[key] += bytes.toString('utf8'); }
      else { handle.overflow = true; signalOwned(owner, handle, 'SIGTERM'); }
    }); }
    child.once('close', (code, signal) => { handle.settled = true; handle.code = code; handle.signal = signal;
      handle.ended_utc = new Date().toISOString(); resolve(handle); });
  });
  // This independent synchronous check prevents targeting an unrelated process group.
  try {
    const pgid = Number(execFileSync('/bin/ps', ['-o', 'pgid=', '-p', String(handle.pid)], {encoding: 'utf8'}).trim());
    assert.equal(pgid, handle.pgid); handle.actual_pgid_verified = true;
  } catch (error) { handle.error = 'Initial PGID proof failed: ' + error.message; signalOwned(owner, handle, 'SIGTERM'); throw new Error(handle.error); }
  return handle;
}
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function stopOwned(owner, handle) {
  assertOwner(owner, handle);
  const receipt = {pid: handle.pid, pgid: handle.pgid, nonce: owner.nonce, actual_pgid_verified: handle.actual_pgid_verified === true,
    forced_kill: false, errors: [], group_absent: false, started_utc: new Date().toISOString()};
  try {
    if (groupAlive(handle.pgid)) { signalOwned(owner, handle, 'SIGTERM'); }
    let until = Date.now() + 3000;
    while (groupAlive(handle.pgid) && Date.now() < until) { await delay(25); }
    if (groupAlive(handle.pgid)) { receipt.forced_kill = true; signalOwned(owner, handle, 'SIGKILL'); }
    until = Date.now() + 3000;
    while (groupAlive(handle.pgid) && Date.now() < until) { await delay(25); }
    receipt.group_absent = !groupAlive(handle.pgid);
  } catch (error) { receipt.errors.push(error.message); }
  receipt.clean = receipt.actual_pgid_verified && receipt.group_absent && !receipt.forced_kill && !receipt.errors.length;
  receipt.finished_utc = new Date().toISOString(); handle.stop = receipt; return receipt;
}
function publicHandle(handle) {
  const {child, exit, ...record} = handle; return record;
}
async function command(owner, executable, args, options = {}) {
  const handle = startOwned(owner, executable, args, options);
  const until = Math.min((owner.deadline ?? DEADLINE) - 6000, Date.now() + (options.timeoutMs ?? 60000));
  try {
    while (!handle.settled && Date.now() < until && !owner.interrupted && !handle.overflow) { await delay(20); }
    assert.ok(handle.settled && !owner.interrupted && !handle.overflow && !handle.error, 'Owned command interrupted/failed/timed out');
    assert.equal(handle.code, 0, 'Owned command failed'); return publicHandle(handle);
  } finally { await stopOwned(owner, handle); assert.ok(handle.stop.clean, 'Owned command cleanup not proved clean'); }
}
function pidAlive(pid) {
  try { process.kill(pid, 0); return true; } catch (error) { if (error.code === 'ESRCH') { return false; } throw error; }
}
function captureSidecar(owner, handle, filename) {
  const observed = json(filename); assertOwner(owner, handle);
  assert.equal(observed.nonce, owner.nonce); assert.equal(observed.root, owner.root);
  assert.equal(observed.owned_code_pid, handle.pid);
  assert.ok(Number.isSafeInteger(observed.process_pid) && observed.process_pid > 1);
  assert.ok(Number.isSafeInteger(observed.observed_pgid) && observed.observed_pgid > 1);
  assert.ok(observed.observed_pgid === handle.pgid || observed.ancestry_verified === true);
  handle.sidecar_identity = observed; return observed;
}
async function probeSidecarGone(identity, timeoutMs = 3000, probes = {pidAlive, groupAlive}) {
  const receipt = {identity, pid_absent: false, pgid_absent: false, errors: [], started_utc: new Date().toISOString()};
  const until = Date.now() + timeoutMs;
  try {
    do {
      receipt.pid_absent = !probes.pidAlive(identity.process_pid);
      receipt.pgid_absent = !probes.groupAlive(identity.observed_pgid);
      if (receipt.pid_absent && receipt.pgid_absent) { break; }
      if (Date.now() >= until) { break; }
      await delay(25);
    } while (true);
  } catch (error) { receipt.errors.push(error.message); }
  receipt.proved_absent = receipt.pid_absent && receipt.pgid_absent && !receipt.errors.length;
  receipt.finished_utc = new Date().toISOString(); return receipt;
}
async function finalizePublication(owner, receipt, output, remove = fsp.rm) {
  receipt.all_owned_groups_absent = [...owner.handles].every(handle => handle.stop?.group_absent === true);
  receipt.all_owned_cleanup_clean = [...owner.handles].every(handle => handle.stop?.clean === true);
  receipt.all_editor_sidecars_proved_absent = [...owner.handles].filter(handle => handle.kind === 'editor')
    .every(handle => handle.sidecar_identity && handle.sidecar_stop?.proved_absent === true);
  receipt.owned_profile_removed = false;
  if (receipt.all_owned_groups_absent && receipt.all_editor_sidecars_proved_absent) {
    assert.equal(fs.realpathSync(owner.root), owner.root);
    assert.equal(json(path.join(owner.root, 'owner.json')).nonce, owner.nonce);
    try { await remove(owner.root, {recursive: true}); receipt.owned_profile_removed = !fs.existsSync(owner.root); }
    catch (error) { receipt.profile_removal_error = error.message; }
  }
  // No await between the last interruption sample and exclusive publication.
  receipt.observed_signals = [...(owner.signals ?? [])]; receipt.interrupted = owner.interrupted;
  receipt.passed = receipt.passed === true && receipt.all_owned_cleanup_clean
    && receipt.all_editor_sidecars_proved_absent && receipt.owned_profile_removed && !receipt.interrupted;
  receipt.finished_utc = new Date().toISOString(); exclusive(path.join(output, 'receipt.json'), receipt);
}
async function readArchive(filename) {
  const stat = fs.lstatSync(filename); assert.ok(stat.isFile() && !stat.isSymbolicLink() && stat.size <= 8 * 1024 * 1024);
  const before = sha(fs.readFileSync(filename));
  const yauzl = require(require.resolve('yauzl', {paths: [path.join(EXT, 'node_modules/@vscode/vsce')]}));
  const members = {}, archive = await new Promise((resolve, reject) => yauzl.open(filename, {lazyEntries: true}, (error, zip) => error ? reject(error) : resolve(zip)));
  let total = 0;
  await new Promise((resolve, reject) => {
    archive.on('error', reject); archive.on('end', resolve);
    archive.on('entry', entry => {
      const name = entry.fileName;
      if (!/^(extension\/[A-Za-z0-9_./-]+|extension\.vsixmanifest|\[Content_Types\]\.xml)$/.test(name)
        || name.split('/').includes('..') || name.includes('//') || Object.hasOwn(members, name)) { archive.close(); reject(new Error('Unsafe/duplicate VSIX member')); return; }
      if (name.endsWith('/')) { archive.readEntry(); return; }
      total += entry.uncompressedSize;
      if (total > 8 * 1024 * 1024 || Object.keys(members).length >= 256) { archive.close(); reject(new Error('VSIX content cap')); return; }
      archive.openReadStream(entry, (error, stream) => {
        if (error) { reject(error); return; } const buffers = []; let size = 0;
        stream.on('error', reject); stream.on('data', bytes => { size += bytes.length; if (size > entry.uncompressedSize) { stream.destroy(new Error('VSIX size mismatch')); } else { buffers.push(bytes); } });
        stream.on('end', () => { members[name] = Buffer.concat(buffers); archive.readEntry(); });
      });
    }); archive.readEntry();
  });
  assert.equal(sha(fs.readFileSync(filename)), before); assert.ok(members['extension/package.json'] && members['extension.vsixmanifest']);
  return {filename, sha256: before, bytes: stat.size, members, manifest: JSON.parse(members['extension/package.json'].toString('utf8'))};
}
function checkArchiveBindings(archive, plan, name) {
  const expected = plan.source[name === 'product' ? 'target' : 'sidecar'];
  const payload = Object.fromEntries(Object.entries(archive.members).filter(([key]) => key.startsWith('extension/'))
    .map(([key, bytes]) => [key.slice(10), {sha256: sha(bytes), bytes: bytes.length}]));
  const executable = name === 'product' ? Object.keys(expected).filter(key => key.startsWith('dist/src/')) : ['extension.cjs'];
  assert.ok(executable.length > 0);
  for (const key of executable) { assert.ok(same(payload[key], expected[key]), 'Packaged frozen executable drift: ' + key); }
  assert.ok(same(archive.manifest, json(path.join(name === 'product' ? EXT : SIDECAR, 'package.json'))), 'Packaged manifest drift');
  return {executable_files: Object.fromEntries(executable.map(key => [key, payload[key]])), manifest_matches_source: true};
}
function verifyInstalled(directory, archive, id, version) {
  assert.equal(fs.realpathSync(directory), directory); const manifest = json(path.join(directory, 'package.json'));
  assert.equal(`${manifest.publisher}.${manifest.name}`, id); assert.equal(manifest.version, version);
  const normalized = {...manifest}; delete normalized.__metadata; assert.ok(same(normalized, archive.manifest));
  const files = inventory(directory), expected = Object.fromEntries(Object.entries(archive.members)
    .filter(([name]) => name.startsWith('extension/')).map(([name, bytes]) => [name.slice(10), {sha256: sha(bytes), bytes: bytes.length}]));
  expected['.vsixmanifest'] = {sha256: sha(archive.members['extension.vsixmanifest']), bytes: archive.members['extension.vsixmanifest'].length};
  assert.ok(same(Object.keys(files).sort(), Object.keys(expected).sort()), 'Installed membership differs');
  for (const name of Object.keys(expected).filter(name => name !== 'package.json')) { assert.ok(same(files[name], expected[name]), 'Installed bytes differ: ' + name); }
  return {id, version, path: directory, manifest_normalized_matches: true, files};
}
function fixtureResponse(repository, body) {
  assert.ok(body && typeof body.query === 'string' && body.query.length <= 8192 && !body.overlays);
  if (body.rerank === true && body.rerank_provider === 'qwen') {
    return {code: 400, body: {error: {code: 'invalid_request', message: 'Qwen reranking is not configured'}}};
  }
  const expected = {retrieval: 'dense', rerank: false, candidates: 4, max_results: 8,
    source_budget: 12000, token_budget: 4096, include_dependencies: true};
  for (const [key, value] of Object.entries(expected)) { assert.equal(body[key], value, 'Product default differs: ' + key); }
  assert.equal(body.rerank_provider, undefined); assert.match(body.request_id, /^[A-Za-z0-9_-]{1,64}$/);
  return {code: 200, body: {repo: repository, source_root: 'src', query: body.query, retrieval: 'dense', rerank: false,
    results: [{id: 'src/ordinary.py:ordinary_message:1-2', path: 'src/ordinary.py', symbol: 'ordinary_message',
      start_line: 1, end_line: 2, window_end_line: 2, source: SOURCE, source_hash: sha(SOURCE), content_hash: sha(SOURCE),
      retrieval_score: null, h2o_score: null, truncated: false, origin: 'retrieval', related_to: null}],
    match_status: 'candidates', source_budget: body.source_budget, total_source_chars: [...SOURCE].length, budget_exhausted: false,
    token_usage: {encoding: 'cl100k_base', tokens: 128, budget: body.token_budget, truncated: false},
    indexstats: {total_windows: 1, source_manifest: sha(SOURCE)}, fixture: true, model_loaded: false}};
}
async function fixture(repository) {
  const records = []; let host;
  const server = http.createServer((request, reply) => {
    const row = {method: request.method, endpoint: request.url, observed_utc: new Date().toISOString()}; records.push(row);
    let bytes = 0; const parts = [];
    const send = (code, value) => { row.status = code; row.response = value; reply.writeHead(code, {'Content-Type': 'application/json'}); reply.end(JSON.stringify(value)); };
    if (request.headers.host !== host || request.headers.origin) { send(403, {error: {code: 'fixture_only', message: 'Owned numeric loopback only'}}); return; }
    request.on('error', () => reply.destroy());
    request.on('data', part => { bytes += part.length; if (bytes > 16384) { row.error = 'Body cap'; request.destroy(); } else { parts.push(part); } });
    request.on('end', () => {
      try {
        if (request.method === 'GET' && request.url === '/status') { send(200, {repo: repository, source_root: 'src', source_exclusions: [],
          closed: false, ready: true, windows: 1, last_error: null, source_manifest: sha(SOURCE), watcher: null, model_loaded: false}); }
        else if (request.method === 'POST' && request.url === '/search') {
          row.request = JSON.parse(Buffer.concat(parts).toString('utf8')); const response = fixtureResponse(repository, row.request); send(response.code, response.body);
        } else { send(404, {error: {code: 'fixture_only', message: 'No model/backend endpoint'}}); }
      } catch (error) { row.error = error.message; send(400, {error: {code: 'fixture_contract', message: error.message}}); }
    });
  }); server.requestTimeout = 10000; server.headersTimeout = 10000;
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  host = '127.0.0.1:' + server.address().port;
  return {url: 'http://' + host, records, server,
    async close() { server.closeAllConnections(); if (server.listening) { await new Promise(resolve => server.close(resolve)); } return !server.listening; }};
}
async function run(plan, output, planBinding = null) {
  checkPlan(plan); assert.ok(path.isAbsolute(output)); assert.ok(Date.now() < DEADLINE - 45000, 'Deadline leaves insufficient cleanup budget');
  fs.mkdirSync(output, {mode: 0o700}); // Exclusive run guard, before ownership/launch.
  const root = shortOwnedRoot();
  const owner = {root, nonce: crypto.randomBytes(24).toString('hex'), handles: new Set(), interrupted: false, deadline: DEADLINE, signals: []};
  exclusive(path.join(root, 'owner.json'), {nonce: owner.nonce, launcher_pid: process.pid, created_utc: new Date().toISOString()});
  const receipt = {schema: 1, classification: 'Ordinary installed-profile zero-model authored HTTP acceptance', passed: false,
    plan_canonical_sha256: sha(JSON.stringify(canonical(plan))), plan_file: planBinding, source: plan.source, host: plan.host, limits: plan.limits,
    started_utc: new Date().toISOString(), model_calls: 0, inference_calls: 0, modes: [], owned_root: root};
  let service;
  const interrupted = signal => { owner.signals.push(signal); owner.interrupted = true; for (const handle of owner.handles) { try { signalOwned(owner, handle, 'SIGTERM'); } catch {} } };
  const onSigint = () => interrupted('SIGINT'), onSigterm = () => interrupted('SIGTERM');
  process.on('SIGINT', onSigint); process.on('SIGTERM', onSigterm);
  try {
    const packModule = path.join(plan.vsce.path, 'out/package.js');
    for (const [name, cwd] of [['product', EXT], ['sidecar', SIDECAR]]) {
      const filename = path.join(root, name + '.vsix');
      const script = `require(${JSON.stringify(packModule)}).pack(${JSON.stringify({cwd, packagePath: filename, dependencies: false, skipLicense: true,
        baseContentUrl: 'https://github.com/anna-poutanen/semantic-search-engine/blob/main/extensions/vscode',
        baseImagesUrl: 'https://github.com/anna-poutanen/semantic-search-engine/raw/main/extensions/vscode'})}).catch(e=>{console.error(e.message);process.exitCode=1})`;
      await command(owner, process.execPath, ['-e', script], {cwd});
      const archive = await readArchive(filename); receipt[name + '_source_binding'] = checkArchiveBindings(archive, plan, name); fs.copyFileSync(filename, path.join(output, name + '.vsix'), fs.constants.COPYFILE_EXCL);
      receipt[name + '_vsix'] = {sha256: archive.sha256, bytes: archive.bytes,
        members: Object.fromEntries(Object.entries(archive.members).map(([key, value]) => [key, {sha256: sha(value), bytes: value.length}]))};
    }
    checkPlan(plan);
    const archives = {product: await readArchive(path.join(root, 'product.vsix')), sidecar: await readArchive(path.join(root, 'sidecar.vsix'))};
    for (const mode of plan.modes) {
      assert.ok(!owner.interrupted);
      const directory = path.join(root, mode); fs.mkdirSync(directory, {mode: 0o700});
      const userData = path.join(directory, 'user-data'), extensions = path.join(directory, 'extensions'), repository = path.join(directory, 'repository');
      fs.mkdirSync(path.join(userData, 'User'), {recursive: true}); fs.mkdirSync(extensions); fs.mkdirSync(path.join(repository, 'src'), {recursive: true});
      fs.writeFileSync(path.join(userData, 'User/settings.json'), JSON.stringify(settings())); fs.writeFileSync(path.join(repository, 'src/ordinary.py'), SOURCE);
      const emptyTemplate = path.join(directory, 'empty-git-template'); fs.mkdirSync(emptyTemplate);
      await command(owner, '/usr/bin/git', ['init', '--quiet', '--template=' + emptyTemplate, repository]);
      await command(owner, '/usr/bin/git', ['-C', repository, 'add', 'src/ordinary.py']);
      const row = {mode, passed: false, ipc_path: ipcPathReceipt(userData), settings: settings(), trust_disabled_cli: mode === 'trusted', installs: [], installed_before: []}; receipt.modes.push(row);
      for (const name of ['product', 'sidecar']) {
        row.installs.push(await command(owner, plan.host.cli, [`--user-data-dir=${userData}`, `--extensions-dir=${extensions}`,
          '--install-extension', path.join(root, name + '.vsix'), '--force', '--do-not-sync'], {timeoutMs: 60000}));
      }
      const installed = fs.readdirSync(extensions).filter(name => fs.lstatSync(path.join(extensions, name)).isDirectory());
      assert.equal(installed.length, 2, 'Unexpected installed extension directory count');
      const target = path.join(extensions, installed.find(name => name.startsWith(plan.expected.id + '-')) ?? 'missing');
      const sidecar = path.join(extensions, installed.find(name => name.startsWith(plan.expected.sidecar_id + '-')) ?? 'missing');
      row.installed_before = [verifyInstalled(target, archives.product, plan.expected.id, plan.expected.version),
        verifyInstalled(sidecar, archives.sidecar, plan.expected.sidecar_id, plan.expected.sidecar_version)];
      service = await fixture(repository);
      const result = path.join(directory, 'sidecar-result.json'), args = launchArgs(mode, repository, userData, extensions);
      const handle = startOwned(owner, plan.host.executable, args, {kind: 'editor', env: {...environment(), ORDINARY_PROFILE_ROOT: root,
        ORDINARY_PROFILE_NONCE: owner.nonce, ORDINARY_PROFILE_MODE: mode, ORDINARY_PROFILE_RESULT: result,
        ORDINARY_PROFILE_REPO: repository, ORDINARY_PROFILE_TARGET: target, ORDINARY_PROFILE_URL: service.url, ORDINARY_PROFILE_SOURCE: SOURCE}});
      handle.sidecar_owner_file = path.join(directory, 'sidecar-owner.json');
      exclusive(path.join(directory, 'launch-owner.json'), {nonce: owner.nonce, pid: handle.pid, pgid: handle.pgid});
      const until = Math.min(DEADLINE - 8000, Date.now() + 110000);
      while (!fs.existsSync(result) && Date.now() < until && !handle.settled && !owner.interrupted && !handle.overflow) { await delay(50); }
      assert.ok(fs.existsSync(result) && !owner.interrupted && !handle.error && !handle.overflow, 'Ordinary sidecar did not produce a terminal proof');
      fs.copyFileSync(result, path.join(output, mode + '-sidecar-result.json'), fs.constants.COPYFILE_EXCL);
      row.sidecar = json(result); assert.equal(row.sidecar.nonce, owner.nonce);
      row.sidecar_identity = captureSidecar(owner, handle, handle.sidecar_owner_file);
      assert.equal(row.sidecar.passed, true);
      row.host_cleanup = await stopOwned(owner, handle); row.sidecar_cleanup = await probeSidecarGone(handle.sidecar_identity); handle.sidecar_stop = row.sidecar_cleanup;
      row.host = publicHandle(handle); assert.ok(row.host_cleanup.clean); assert.ok(row.sidecar_cleanup.proved_absent, 'Sidecar PID/group still alive or unproved');
      row.product_http = service.records; row.listener_closed = await service.close(); service = undefined;
      row.installed_after = [verifyInstalled(target, archives.product, plan.expected.id, plan.expected.version),
        verifyInstalled(sidecar, archives.sidecar, plan.expected.sidecar_id, plan.expected.sidecar_version)];
      assert.ok(same(row.installed_before, row.installed_after));
      assert.equal(fs.readFileSync(path.join(repository, 'src/ordinary.py'), 'utf8'), SOURCE);
      assert.ok(row.listener_closed);
      if (mode === 'restricted') { assert.equal(row.product_http.length, 0); }
      else {
        assert.equal(row.product_http.filter(item => item.endpoint === '/search' && item.status === 200).length, 1);
        assert.equal(row.product_http.filter(item => item.endpoint === '/search' && item.status === 400 && item.request?.rerank_provider === 'qwen').length, 1);
        assert.ok(row.product_http.every(item => item.status === 200 || item.status === 400));
      }
      row.passed = true; checkPlan(plan);
    }
    receipt.passed = receipt.modes.length === 2 && receipt.modes.every(row => row.passed);
  } catch (error) { receipt.error = error.message; receipt.interrupted = owner.interrupted; }
  finally {
    try {
      if (service) { receipt.failed_fixture_requests = service.records; receipt.failed_listener_closed = await service.close(); }
      receipt.cleanup = [];
      for (const handle of owner.handles) {
        if (handle.kind === 'editor' && !handle.sidecar_identity && fs.existsSync(handle.sidecar_owner_file)) {
          try { captureSidecar(owner, handle, handle.sidecar_owner_file); } catch (error) { handle.sidecar_capture_error = error.message; }
        }
        if (!handle.stop) { await stopOwned(owner, handle); }
        if (handle.sidecar_identity && !handle.sidecar_stop) { handle.sidecar_stop = await probeSidecarGone(handle.sidecar_identity); }
        receipt.cleanup.push(publicHandle(handle));
      }
      await finalizePublication(owner, receipt, output);
    } finally {
      process.removeListener('SIGINT', onSigint); process.removeListener('SIGTERM', onSigterm);
    }
  }
  return receipt;
}
function args(argv) {
  const result = {};
  for (let i = 0; i < argv.length; i++) {
    const key = argv[i]; assert.ok(['--prepare', '--run', '--plan', '--expected-plan-sha256', '--output'].includes(key));
    assert.ok(!Object.hasOwn(result, key));
    result[key] = ['--prepare', '--run'].includes(key) ? true : argv[++i]; assert.ok(result[key]);
  }
  assert.equal(Boolean(result['--prepare']) + Boolean(result['--run']), 1, 'Choose explicit --prepare or --run');
  assert.ok(path.isAbsolute(result['--output'] ?? ''));
  if (result['--run']) { assert.ok(path.isAbsolute(result['--plan'] ?? '') && /^[a-f0-9]{64}$/.test(result['--expected-plan-sha256'] ?? '')); }
  return result;
}
module.exports = {SOURCE, DEADLINE, sha, canonical, same, inventory, environment, settings, ipcPathReceipt, shortOwnedRoot, launchArgs, prepare, checkPlan,
  startOwned, assertOwner, signalOwned, stopOwned, command, readArchive, checkArchiveBindings, verifyInstalled, fixtureResponse, fixture, pidAlive, captureSidecar, probeSidecarGone, finalizePublication, run, args};
if (require.main === module) {
  (async () => {
    const options = args(process.argv.slice(2));
    if (options['--prepare']) { exclusive(options['--output'], prepare()); console.log(JSON.stringify({prepared: true, model_calls: 0, editor_launches: 0})); }
    else {
      assert.equal(sha(fs.readFileSync(options['--plan'])), options['--expected-plan-sha256']);
      const result = await run(json(options['--plan']), options['--output'],
        {path: options['--plan'], sha256: options['--expected-plan-sha256']});
      console.log(JSON.stringify({passed: result.passed, output: options['--output'], model_calls: 0})); if (!result.passed) { process.exitCode = 1; }
    }
  })().catch(error => { console.error(error.message); process.exitCode = 1; });
}
