'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const h = require('../scripts/ordinary_vscode_profile.cjs');
const side = require('../scripts/ordinary_vscode_sidecar/extension.cjs');
function owned() {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'ordinary-cpu-')));
  const owner = {root, nonce: crypto.randomBytes(24).toString('hex'), handles: new Set(), interrupted: false, deadline: Date.now() + 60000};
  fs.writeFileSync(path.join(root, 'owner.json'), JSON.stringify({nonce: owner.nonce})); return owner;
}
async function remove(owner) {
  for (const handle of owner.handles) { if (!handle.stop) { await h.stopOwned(owner, handle); } }
  fs.rmSync(owner.root, {recursive: true});
}
const defaultBody = () => ({query: 'Return local message', retrieval: 'dense', rerank: false, candidates: 4,
  max_results: 8, source_budget: 12000, token_budget: 4096, include_dependencies: true, request_id: 'owned-1'});
test('ordinary argv has explicit isolated dirs, never development/test host or disable extensions', () => {
  for (const mode of ['trusted', 'restricted']) {
    const argv = h.launchArgs(mode, '/owned/repository', '/owned/data', '/owned/extensions');
    assert.ok(argv.includes('--user-data-dir=/owned/data')); assert.ok(argv.includes('--extensions-dir=/owned/extensions'));
    assert.ok(argv.every(item => !/extension(Development|Tests)Path|disable-extensions|enable-proposed-api/.test(item)));
    assert.equal(argv.includes('--disable-workspace-trust'), mode === 'trusted');
    assert.equal(h.settings()['security.workspace.trust.enabled'], true);
  }
});
test('CLI requires explicit prepare/run, absolute exclusive output and frozen plan hash', () => {
  assert.deepEqual(h.args(['--prepare', '--output', '/new/plan.json']), {'--prepare': true, '--output': '/new/plan.json'});
  for (const argv of [[], ['--run', '--output', '/new'], ['--prepare', '--run', '--output', '/new'],
    ['--prepare', '--output', 'relative'], ['--prepare', '--output', '/new', '--output', '/again'], ['--prepare', '--wat', 'x']]) {
    assert.throws(() => h.args(argv));
  }
});
test('environment removes editor overrides, injections, inherited probe/model vars, proxies and Git hooks', () => {
  const result = h.environment({PATH: '/safe', HOME: '/owned-home', NODE_OPTIONS: '--require bad', NODE_USE_ENV_PROXY: '1',
    VSCODE_IPC_HOOK: 'bad', ELECTRON_RUN_AS_NODE: '1', SEMANTIC_QWEN_PYTHON: 'bad', ORDINARY_PROFILE_NONCE: 'bad',
    HTTP_PROXY: 'bad', GIT_CONFIG_COUNT: '1'});
  assert.equal(result.PATH, '/safe'); assert.equal(result.HOME, '/owned-home');
  for (const key of ['NODE_OPTIONS', 'VSCODE_IPC_HOOK', 'NODE_USE_ENV_PROXY', 'ELECTRON_RUN_AS_NODE', 'SEMANTIC_QWEN_PYTHON',
    'ORDINARY_PROFILE_NONCE', 'HTTP_PROXY', 'GIT_CONFIG_COUNT']) { assert.equal(result[key], undefined); }
  assert.equal(result.GIT_CONFIG_GLOBAL, '/dev/null');
});
test('default schema fixture passes unchanged shipped parser and literal physical span checks', () => {
  const body = defaultBody(), response = h.fixtureResponse('/owned/repository', body);
  assert.equal(response.code, 200); assert.equal(response.body.model_loaded, false);
  const schema = require('../extensions/vscode/dist/src/schema.js');
  const parsed = schema.parseSearch(response.body, body.query, schema.defaultOptions);
  assert.equal(parsed.results[0].source_hash, h.sha(h.SOURCE)); assert.equal(parsed.results[0].window_end_line, 2);
  assert.equal(parsed.total_source_chars, [...h.SOURCE].length); assert.equal(parsed.token_usage.tokens, 128);
});
for (const [key, value] of [['retrieval', 'hybrid'], ['candidates', 24], ['max_results', 12], ['include_dependencies', false],
  ['token_budget', 8000], ['source_budget', 5000], ['rerank_provider', 'h2o']]) {
  test('fixture detects product default/request drift: ' + key, () => { assert.throws(() => h.fixtureResponse('/repo', {...defaultBody(), [key]: value})); });
}
test('Qwen fixture denial uses real structured error contract; never supplies scores/inference', () => {
  const response = h.fixtureResponse('/repo', {...defaultBody(), rerank: true, rerank_provider: 'qwen'});
  assert.deepEqual(response, {code: 400, body: {error: {code: 'invalid_request', message: 'Qwen reranking is not configured'}}});
});
test('inventory refuses symlinks and hashes every actual file', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ordinary-inventory-'));
  try {
    fs.writeFileSync(path.join(dir, 'entry'), 'abc'); assert.equal(h.inventory(dir).entry.sha256, h.sha('abc'));
    fs.symlinkSync(path.join(dir, 'entry'), path.join(dir, 'alias')); assert.throws(() => h.inventory(dir), /symlinks/);
  } finally { fs.rmSync(dir, {recursive: true}); }
});
test('installed payload verification rejects member or literal byte substitution', () => {
  const dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'ordinary-install-')));
  const manifest = {publisher: 'owned', name: 'probe', version: '1.0.0', main: './entry.cjs'};
  const archive = {manifest, members: {'extension/package.json': Buffer.from(JSON.stringify(manifest)),
    'extension/entry.cjs': Buffer.from('exact'), 'extension.vsixmanifest': Buffer.from('container')}};
  try {
    fs.writeFileSync(path.join(dir, 'package.json'), JSON.stringify({...manifest, __metadata: {test: true}}));
    fs.writeFileSync(path.join(dir, 'entry.cjs'), 'exact'); fs.writeFileSync(path.join(dir, '.vsixmanifest'), 'container');
    assert.equal(h.verifyInstalled(dir, archive, 'owned.probe', '1.0.0').manifest_normalized_matches, true);
    fs.writeFileSync(path.join(dir, 'entry.cjs'), 'wrong'); assert.throws(() => h.verifyInstalled(dir, archive, 'owned.probe', '1.0.0'), /bytes differ/);
    fs.writeFileSync(path.join(dir, 'entry.cjs'), 'exact'); fs.writeFileSync(path.join(dir, 'extra'), 'unknown');
    assert.throws(() => h.verifyInstalled(dir, archive, 'owned.probe', '1.0.0'), /membership differs/);
  } finally { fs.rmSync(dir, {recursive: true}); }
});
test('real CPU child PID/PGID/nonce is owned before return, output retained and stop proved', async () => {
  const owner = owned();
  try {
    const handle = h.startOwned(owner, process.execPath, ['-e', 'console.log("owned");setInterval(()=>{},1000)']);
    assert.ok(owner.handles.has(handle)); assert.equal(handle.pid, handle.pgid); assert.equal(handle.actual_pgid_verified, true);
    await new Promise(resolve => setTimeout(resolve, 100));
    const receipt = await h.stopOwned(owner, handle); await handle.exit;
    assert.equal(receipt.clean, true); assert.equal(receipt.forced_kill, false); assert.match(handle.stdout, /owned/);
  } finally { await remove(owner); }
});
test('spoofed nonce/foreign handle cannot signal any group', async () => {
  const owner = owned();
  try {
    assert.throws(() => h.signalOwned(owner, {pid: process.pid, pgid: process.pid, nonce: owner.nonce}, 'SIGTERM'), /Unowned/);
    const handle = h.startOwned(owner, process.execPath, ['-e', 'setInterval(()=>{},1000)']);
    handle.nonce = 'bad'; assert.throws(() => h.signalOwned(owner, handle, 'SIGTERM')); handle.nonce = owner.nonce;
  } finally { await remove(owner); }
});
test('bounded real CPU command times out, preserves cleanup and rejects qualification', async () => {
  const owner = owned();
  try {
    await assert.rejects(h.command(owner, process.execPath, ['-e', 'setInterval(()=>{},1000)'], {timeoutMs: 70}), /timed out/);
    const [handle] = owner.handles; assert.equal(handle.stop.group_absent, true); assert.equal(handle.stop.clean, true);
  } finally { await remove(owner); }
});
test('failed spawn refuses absent PID without unhandled error or unsafe cleanup', async () => {
  const owner = owned();
  try { assert.throws(() => h.startOwned(owner, '/missing/ordinary-cpu-command', []), /no owned PID/); await new Promise(resolve => setTimeout(resolve, 20)); }
  finally { await remove(owner); }
});
test('interrupted owner halts new commands before spawning', async () => {
  const owner = owned();
  try { owner.interrupted = true; assert.throws(() => h.startOwned(owner, process.execPath, [])); assert.equal(owner.handles.size, 0); }
  finally { await remove(owner); }
});
test('sidecar refuses missing ownership synchronously before any command/await', () => {
  assert.throws(() => side.ownership({}), /assert|expression/i);
});
test('restricted ordinary sidecar never force activates target and requires every command denied', async () => {
  const owner = owned();
  try {
    const result = path.join(owner.root, 'result.json'); let calls = 0;
    const vscode = {version: '1.141.0', env: {}, workspace: {isTrusted: false, workspaceFolders: [{uri: {fsPath: owner.root}}]},
      extensions: {getExtension: () => undefined}, commands: {getCommands: async () => [], executeCommand: async command => { calls++; throw new Error(`command '${command}' not found`); }}};
    const row = await side.probe(vscode, {root: owner.root, nonce: owner.nonce, repository: owner.root, mode: 'restricted', result}, {ORDINARY_PROFILE_URL: 'http://127.0.0.1:1'});
    assert.equal(row.passed, true); assert.equal(row.observed_trusted, false); assert.equal(row.extension_available, false);
    assert.equal(calls, 3); assert.equal(row.model_calls, 0); assert.equal(fs.existsSync(result), true);
  } finally { await remove(owner); }
});
test('restricted ordinary sidecar retains failed command success instead of claiming denial', async () => {
  const owner = owned();
  try {
    const result = path.join(owner.root, 'result.json');
    const vscode = {version: '1.141.0', env: {}, workspace: {isTrusted: false, workspaceFolders: [{uri: {fsPath: owner.root}}]},
      extensions: {getExtension: () => ({isActive: false})}, commands: {getCommands: async () => [], executeCommand: async () => ({phase: 'ready'})}};
    const row = await side.probe(vscode, {root: owner.root, nonce: owner.nonce, repository: owner.root, mode: 'restricted', result});
    assert.equal(row.passed, false); assert.match(row.error, /must be unavailable or deny/);
  } finally { await remove(owner); }
});
test('packaged product executable must match every prepared shipped compiled file', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, '../extensions/vscode/package.json')));
  const bytes = Buffer.from('frozen shipped executable');
  const archive = {manifest, members: {'extension/dist/src/extension.js': bytes}};
  const plan = {source: {target: {'dist/src/extension.js': {sha256: h.sha(bytes), bytes: bytes.length}}}};
  assert.equal(h.checkArchiveBindings(archive, plan, 'product').manifest_matches_source, true);
  archive.members['extension/dist/src/extension.js'] = Buffer.from('substitution');
  assert.throws(() => h.checkArchiveBindings(archive, plan, 'product'), /executable drift/);
});
test('authored loopback fixture retains actual requests/defaults/denial and shuts down', async () => {
  const http = require('node:http'), service = await h.fixture('/owned/repository');
  async function request(endpoint, method = 'GET', body, headers = {}) {
    const encoded = body ? JSON.stringify(body) : undefined;
    return new Promise((resolve, reject) => {
      const req = http.request(service.url + endpoint, {method, headers: {...headers, ...(encoded ? {'Content-Type': 'application/json'} : {})}}, res => {
        const parts = []; res.on('data', part => parts.push(part)); res.on('end', () => resolve({code: res.statusCode, value: JSON.parse(Buffer.concat(parts))}));
      }); req.on('error', reject); req.end(encoded);
    });
  }
  try {
    assert.equal(service.records.length, 0);
    assert.equal((await request('/status')).value.windows, 1);
    assert.equal((await request('/search', 'POST', defaultBody())).code, 200);
    assert.equal((await request('/search', 'POST', {...defaultBody(), rerank: true, rerank_provider: 'qwen'})).code, 400);
    assert.equal((await request('/status', 'GET', undefined, {Host: 'external.example'})).code, 403);
    assert.equal(service.records.length, 4); assert.equal(service.records[1].request.candidates, 4);
    assert.equal(service.records[2].request.rerank_provider, 'qwen'); assert.equal(service.records[1].response.model_loaded, false);
  } finally { assert.equal(await service.close(), true); assert.equal(service.server.listening, false); }
});
test('trusted sidecar uses only supported public commands and observes installed activation/navigation/provider denial', async () => {
  const owner = owned();
  try {
    const source = h.SOURCE, filename = path.join(owner.root, 'src/ordinary.py'); fs.mkdirSync(path.dirname(filename)); fs.writeFileSync(filename, source);
    const result = path.join(owner.root, 'result.json'), calls = [];
    const extension = {id: 'anna-poutanen.semantic-search-local', isActive: false, extensionPath: owner.root,
      packageJSON: {version: '0.1.0', main: './dist/src/extension.js', contributes: {configuration: {properties: {'semanticSearch.rerankProvider': {default: 'h2o'}}}}},
      activate() { throw new Error('Must use public command autoactivation'); }};
    const found = {phase: 'ready', entries: [{result: {source, source_hash: h.sha(source)}, staleReason: null}]};
    const vscode = {version: '1.141.0', env: {}, workspace: {isTrusted: true, workspaceFolders: [{uri: {fsPath: owner.root}}]},
      extensions: {getExtension: () => extension}, window: {}, commands: {executeCommand: async (command, argument) => {
        calls.push({command, argument});
        if (command === 'semanticSearch.connect') { extension.isActive = true; return {phase: 'ready'}; }
        if (command === 'semanticSearch.find') { return argument.options?.rerank ? {phase: 'error', message: 'Qwen reranking is not configured. Start with --enable-qwen-reranker'} : found; }
        if (command === 'semanticSearch.preview') { vscode.window.activeTextEditor = {document: {getText: () => source}}; return {scheme: 'semantic-search-preview'}; }
        if (command === 'semanticSearch.open') { vscode.window.activeTextEditor = {document: {uri: {fsPath: filename}, isDirty: false}, selection: {start: {line: 0}, end: {line: 1}}}; return {fsPath: filename, toString: () => 'file://' + filename}; }
        if (command === 'semanticSearch.status') { return {phase: 'ready', status: {windows: 1}}; }
        throw new Error('Unexpected command');
      }}};
    const row = await side.probe(vscode, {root: owner.root, nonce: owner.nonce, repository: owner.root, mode: 'trusted', result},
      {ORDINARY_PROFILE_TARGET: owner.root, ORDINARY_PROFILE_SOURCE: source, ORDINARY_PROFILE_URL: 'http://127.0.0.1:1'});
    assert.equal(row.passed, true); assert.equal(row.installed_target.version, '0.1.0');
    assert.deepEqual(calls.map(item => item.command), ['semanticSearch.connect', 'semanticSearch.find', 'semanticSearch.preview', 'semanticSearch.open', 'semanticSearch.status', 'semanticSearch.find']);
    assert.equal(calls[1].argument.options, undefined); assert.equal(calls[5].argument.options.rerank_provider, 'qwen');
    assert.equal(row.checks.public_commands_reached_installed_active_product, true); assert.equal(row.model_calls, 0);
  } finally { await remove(owner); }
});
test('noncanonical owner is refused before any CPU process is created', async () => {
  const owner = owned(), original = owner.root;
  try {
    owner.root += '/.'; assert.throws(() => h.startOwned(owner, process.execPath, [])); assert.equal(owner.handles.size, 0);
  } finally { owner.root = original; await remove(owner); }
});
test('forced kill is retained and cannot claim clean lifecycle', async () => {
  const owner = owned();
  try {
    const handle = h.startOwned(owner, process.execPath, ['-e', 'process.on("SIGTERM",()=>{});console.log("ready");setInterval(()=>{},1000)']);
    const until = Date.now() + 1000;
    while (!handle.stdout.includes('ready') && Date.now() < until) { await new Promise(resolve => setTimeout(resolve, 10)); }
    assert.match(handle.stdout, /ready/);
    const receipt = await h.stopOwned(owner, handle); await handle.exit;
    assert.equal(receipt.group_absent, true); assert.equal(receipt.forced_kill, true); assert.equal(receipt.clean, false);
  } finally { await remove(owner); }
});
test('late signal during final profile removal is retained and cannot publish passing receipt', async () => {
  const owner = owned(); owner.signals = [];
  const output = fs.mkdtempSync(path.join(os.tmpdir(), 'ordinary-late-signal-'));
  const handler = () => { owner.signals.push('SIGTERM'); owner.interrupted = true; };
  process.on('SIGTERM', handler);
  try {
    const receipt = {passed: true};
    await h.finalizePublication(owner, receipt, output, async root => {
      await new Promise(resolve => setTimeout(resolve, 5));
      process.emit('SIGTERM'); fs.rmSync(root, {recursive: true});
    });
    const published = JSON.parse(fs.readFileSync(path.join(output, 'receipt.json')));
    assert.equal(published.passed, false); assert.equal(published.interrupted, true);
    assert.deepEqual(published.observed_signals, ['SIGTERM']); assert.equal(published.owned_profile_removed, true);
    assert.ok(process.listeners('SIGTERM').includes(handler));
  } finally { process.removeListener('SIGTERM', handler); fs.rmSync(output, {recursive: true}); if (fs.existsSync(owner.root)) { await remove(owner); } }
});
test('distinct surviving sidecar group is unproved and private profile is retained', async () => {
  const owner = owned(), output = fs.mkdtempSync(path.join(os.tmpdir(), 'ordinary-sidecar-survive-'));
  try {
    const handle = {kind: 'editor', stop: {clean: true, group_absent: true}, sidecar_identity: {process_pid: 1234, observed_pgid: 5678}};
    handle.sidecar_stop = await h.probeSidecarGone(handle.sidecar_identity, 20, {pidAlive: () => false, groupAlive: () => true});
    assert.equal(handle.sidecar_stop.pid_absent, true); assert.equal(handle.sidecar_stop.pgid_absent, false);
    owner.handles.add(handle); const receipt = {passed: true};
    await h.finalizePublication(owner, receipt, output);
    assert.equal(receipt.passed, false); assert.equal(receipt.owned_profile_removed, false); assert.equal(fs.existsSync(owner.root), true);
    owner.handles.delete(handle);
  } finally { await remove(owner); fs.rmSync(output, {recursive: true}); }
});
test('missing sidecar identity never permits profile deletion despite Code group absence', async () => {
  const owner = owned(), output = fs.mkdtempSync(path.join(os.tmpdir(), 'ordinary-sidecar-missing-'));
  try {
    const handle = {kind: 'editor', stop: {clean: true, group_absent: true}}; owner.handles.add(handle);
    const receipt = {passed: true}; await h.finalizePublication(owner, receipt, output);
    assert.equal(receipt.all_editor_sidecars_proved_absent, false); assert.equal(receipt.owned_profile_removed, false); assert.equal(receipt.passed, false);
    owner.handles.delete(handle);
  } finally { await remove(owner); fs.rmSync(output, {recursive: true}); }
});
test('sidecar disappearance independently requires both actual PID and group absent', async () => {
  const identity = {process_pid: 1234, observed_pgid: 5678};
  for (const [pid, group] of [[true, false], [false, true], [true, true], [false, false]]) {
    const receipt = await h.probeSidecarGone(identity, 0, {pidAlive: () => pid, groupAlive: () => group});
    assert.equal(receipt.proved_absent, !pid && !group);
  }
  const failed = await h.probeSidecarGone(identity, 0, {pidAlive: () => { throw new Error('probe denied'); }, groupAlive: () => false});
  assert.equal(failed.proved_absent, false); assert.deepEqual(failed.errors, ['probe denied']);
});
test('restricted denial classifier accepts only exact guard or command-specific unavailable', () => {
  const command = 'semanticSearch.find';
  assert.equal(side.denialKind(new Error("command 'semanticSearch.find' not found"), command), 'command-unavailable');
  assert.equal(side.denialKind(new Error('Use a trusted local desktop workspace for the local service'), command), 'trusted-local-guard');
  for (const message of ['command not found', "command 'other.command' not found", 'semanticSearch.find timed out',
    'Could not reach the local search service', 'Invalid service JSON', 'Unexpected test failure']) {
    assert.equal(side.denialKind(new Error(message), command), null);
  }
});
for (const message of ['semanticSearch.find timed out', 'Could not reach the local search service', 'Invalid service JSON']) {
  test('restricted unrelated/timeout error is retained and fails: ' + message, async () => {
    const owner = owned();
    try {
      const vscode = {version: '1.141.0', env: {}, workspace: {isTrusted: false, workspaceFolders: [{uri: {fsPath: owner.root}}]},
        extensions: {getExtension: () => undefined}, commands: {getCommands: async () => [], executeCommand: async () => { throw new Error(message); }}};
      const row = await side.probe(vscode, {root: owner.root, nonce: owner.nonce, repository: owner.root, mode: 'restricted', result: path.join(owner.root, 'result.json')});
      assert.equal(row.passed, false); assert.ok(row.denials.every(item => !item.refused && item.error === message));
    } finally { await remove(owner); }
  });
}
test('restricted trust is rechecked before every command and a mid-loop trust change fails', async () => {
  const owner = owned();
  try {
    let calls = 0;
    const vscode = {version: '1.141.0', env: {}, workspace: {isTrusted: false, workspaceFolders: [{uri: {fsPath: owner.root}}]},
      extensions: {getExtension: () => undefined}, commands: {getCommands: async () => [], executeCommand: async command => {
        calls++; vscode.workspace.isTrusted = true; throw new Error(`command '${command}' not found`);
      }}};
    const row = await side.probe(vscode, {root: owner.root, nonce: owner.nonce, repository: owner.root, mode: 'restricted', result: path.join(owner.root, 'result.json')});
    assert.equal(row.passed, false); assert.equal(calls, 1); assert.equal(row.denials[0].observed_trusted_before, false);
    assert.match(row.error, /trust changed before public command/);
  } finally { await remove(owner); }
});
test('actual promise timeout is not classified as Restricted Mode denial', async () => {
  await assert.rejects(side.limit(new Promise(() => {}), 'semanticSearch.find', 5), error => {
    assert.equal(side.denialKind(error, 'semanticSearch.find'), null); return true;
  });
});

test('canonical short owned profile keeps actual Code IPC socket below macOS byte cap', () => {
  const root = h.shortOwnedRoot();
  try {
    assert.equal(fs.realpathSync(root), root); assert.equal(fs.statSync(root).mode & 0o777, 0o700);
    for (const mode of ['trusted', 'restricted']) {
      const userData = path.join(root, mode, 'user-data'), receipt = h.ipcPathReceipt(userData, '1.141.0');
      assert.ok(receipt.utf8_bytes <= 103); assert.equal(receipt.path, path.join(userData, '1.14-main.sock'));
      assert.ok(h.launchArgs(mode, path.join(root, mode, 'repository'), userData, path.join(root, mode, 'extensions')).length > 0);
    }
    assert.throws(() => h.ipcPathReceipt('/' + 'a'.repeat(100), '1.141.0'), /103-byte/);
    assert.throws(() => h.launchArgs('trusted', '/owned/repo', '/' + 'a'.repeat(100), '/owned/extensions'), /103-byte/);
    assert.throws(() => h.ipcPathReceipt('/' + 'é'.repeat(47), '1.141.0'), /103-byte/);
  } finally { fs.rmSync(root, {recursive: true}); }
});
test('proof publication is complete and atomic before sidecar public quit invocation', async () => {
  const owner = owned();
  try {
    const mode = 'trusted'; fs.mkdirSync(path.join(owner.root, mode));
    const ownedSidecar = {root: owner.root, nonce: owner.nonce, mode, process_pid: 4321, result: path.join(owner.root, mode, 'proof.json')};
    const proof = {nonce: owner.nonce, mode, passed: true, checks: {actual_test: true}};
    let invoked = false;
    side.publishProof(ownedSidecar.result, proof);
    const vscode = {commands: {executeCommand: command => {
      invoked = true; assert.equal(command, 'workbench.action.quit');
      assert.deepEqual(JSON.parse(fs.readFileSync(ownedSidecar.result)), proof);
      assert.equal(fs.existsSync(ownedSidecar.result + '.publishing'), false);
      const request = JSON.parse(fs.readFileSync(path.join(owner.root, mode, 'quit-request.json')));
      assert.equal(request.after_terminal_proof, true); assert.equal(request.proof_sha256, h.sha(fs.readFileSync(ownedSidecar.result)));
      return Promise.resolve();
    }}};
    await side.requestQuitAfterProof(vscode, ownedSidecar); assert.equal(invoked, true);
  } finally { await remove(owner); }
});
test('sidecar cannot request public quit before proof exists or when publication is duplicate', async () => {
  const owner = owned();
  try {
    fs.mkdirSync(path.join(owner.root, 'trusted'));
    const ownedSidecar = {root: owner.root, nonce: owner.nonce, mode: 'trusted', result: path.join(owner.root, 'trusted/proof.json')};
    let calls = 0; const vscode = {commands: {executeCommand: () => { calls++; }}};
    assert.throws(() => side.requestQuitAfterProof(vscode, ownedSidecar), /ENOENT/); assert.equal(calls, 0);
    const original = {nonce: owner.nonce, mode: 'trusted', passed: true}; side.publishProof(ownedSidecar.result, original);
    assert.throws(() => side.publishProof(ownedSidecar.result, {...original, passed: false}), /EEXIST/);
    assert.deepEqual(JSON.parse(fs.readFileSync(ownedSidecar.result)), original); assert.equal(calls, 0);
  } finally { await remove(owner); }
});
test('rejected public quit remains separate lifecycle failure evidence', async () => {
  const owner = owned();
  try {
    fs.mkdirSync(path.join(owner.root, 'trusted'));
    const ownedSidecar = {root: owner.root, nonce: owner.nonce, mode: 'trusted', result: path.join(owner.root, 'trusted/proof.json')};
    side.publishProof(ownedSidecar.result, {nonce: owner.nonce, mode: 'trusted', passed: true});
    await assert.rejects(side.requestQuitAfterProof({commands: {executeCommand: async () => { throw new Error('quit rejected'); }}}, ownedSidecar), /quit rejected/);
    assert.equal(JSON.parse(fs.readFileSync(path.join(owner.root, 'trusted/quit-error.json'))).error, 'quit rejected');
    assert.equal(JSON.parse(fs.readFileSync(ownedSidecar.result)).passed, true);
  } finally { await remove(owner); }
});
test('editor parent waits for actual normal PID/group exit after proof instead of sending TERM', async () => {
  const owner = owned();
  try {
    const handle = h.startOwned(owner, process.execPath, ['-e', 'setTimeout(()=>process.exit(0),150)'], {kind: 'editor'});
    handle.quit_requested = true;
    const receipt = await h.stopOwned(owner, handle, {gracefulWaitMs: 1000}); await handle.exit;
    assert.equal(receipt.clean, true); assert.equal(receipt.graceful_wait.graceful_exit_observed, true);
    assert.equal(receipt.graceful_wait.pid_absent, true); assert.equal(receipt.graceful_wait.group_absent, true);
    assert.deepEqual(receipt.signals_sent, []); assert.equal(handle.code, 0); assert.equal(handle.signal, null);
  } finally { await remove(owner); }
});
test('editor graceful timeout retains evidence before strict bounded TERM fallback', async () => {
  const owner = owned();
  try {
    const handle = h.startOwned(owner, process.execPath, ['-e', 'setInterval(()=>{},1000)'], {kind: 'editor'});
    handle.quit_requested = true;
    const receipt = await h.stopOwned(owner, handle, {gracefulWaitMs: 40}); await handle.exit;
    assert.equal(receipt.graceful_wait.graceful_exit_observed, false); assert.ok(receipt.graceful_wait.elapsed_ms >= 40);
    assert.deepEqual(receipt.signals_sent, ['SIGTERM']); assert.equal(receipt.group_absent, true); assert.equal(receipt.forced_kill, false);
  } finally { await remove(owner); }
});
test('ordinary launch explicitly isolates the supported pinned Code shared-data directory', () => {
  for (const mode of ['trusted', 'restricted']) {
    const argv = h.launchArgs(mode, '/owned/repository', '/owned/' + mode + '/user-data', '/owned/' + mode + '/extensions');
    assert.ok(argv.includes('--shared-data-dir=/owned/' + mode + '/shared-data'));
  }
});
