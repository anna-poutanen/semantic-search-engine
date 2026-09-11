import { test } from 'node:test';
import * as assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import * as os from 'node:os';
import * as http from 'node:http';
import { once } from 'node:events';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { bindRepository } from '../src/evidence';
import { BufferOverride, captureBundle, EditorBuffer, eligiblePath, globMatches, validateOverrides, verifyBundle, verifyEditorBuffer, verifyOverlayResult } from '../src/overlays';
import { defaultOptions, hash, parseSearch, parseStatus, physicalLines, Result, SearchResponse, Status } from '../src/schema';
import { ServiceClient, SearchApi } from '../src/client';
import { SearchController } from '../src/controller';
import { parseFindRequest } from '../src/commands';
import { response, source, status } from './fixtures';
const run = promisify(execFile);
async function fixture(files: Record<string, string | Buffer> = { 'src/app.py': source }) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'semantic-overlays-'));
  for (const [name, text] of Object.entries(files)) { await fs.mkdir(path.dirname(path.join(root, name)), { recursive: true }); await fs.writeFile(path.join(root, name), text); }
  await run('git', ['-C', root, 'init', '-q']); await run('git', ['-C', root, 'add', '.']);
  const binding = await bindRepository(root, root, 'src'), scope: Status = { ...status(root), source_exclusions: [] };
  const buffers: EditorBuffer[] = [];
  return { root, binding, scope, buffers, read: () => buffers, add: (filename: string, text: string, version = 2) => {
    const buffer = { key: filename, path: path.join(root, filename), text, version, dirty: true, closed: false }; buffers.push(buffer); return buffer;
  }, close: () => fs.rm(root, { recursive: true, force: true }) };
}
function override(pathname = 'src/app.py', text = source): BufferOverride { return { path: pathname, text, version: 2, content_hash: hash(text) }; }
function overlayResult(buffer: BufferOverride): Result {
  return { ...response('/repo').results[0]!, path: buffer.path, source: buffer.text, source_hash: hash(buffer.text), content_hash: hash(buffer.text),
    start_line: 1, end_line: physicalLines(buffer.text).length, window_end_line: physicalLines(buffer.text).length,
    source_origin: 'overlay', buffer_version: buffer.version, buffer_hash: buffer.content_hash };
}
function overlayResponse(root: string, query: string, buffers: readonly BufferOverride[], selected?: BufferOverride): SearchResponse {
  const value = response(root, query);
  value.scope = { source_state: 'overlay', saved_source_manifest: 'a'.repeat(64), overlays: buffers.map(({ path, version, content_hash }) => ({ path, version, content_hash })) };
  value.results = selected ? [overlayResult(selected)] : [];
  value.total_source_chars = value.results.reduce((total, row) => total + [...row.source].length, 0);
  value.indexstats = { total_windows: 42, source_manifest: 'b'.repeat(64) }; return value;
}
async function until(predicate: () => boolean): Promise<void> {
  for (let i = 0; i < 100 && !predicate(); i++) { await new Promise<void>(resolve => setTimeout(resolve, 2)); }
  assert.ok(predicate(), 'request reached fake API');
}
class FakeApi implements SearchApi {
  calls: { id: string; buffers: readonly BufferOverride[]; signal?: AbortSignal }[] = []; cancellations: string[] = [];
  deliver!: (value: SearchResponse) => void;
  constructor(readonly root: string) {}
  async status() { return { ...status(this.root), source_exclusions: [] }; }
  async refresh() { return 1; }
  search(_query: string, _options: unknown, id: string, signal?: AbortSignal, buffers: readonly BufferOverride[] = []): Promise<SearchResponse> {
    this.calls.push({ id, buffers, ...(signal ? { signal } : {}) }); return new Promise(resolve => this.deliver = resolve);
  }
  async cancel(id: string) { this.cancellations.push(id); return true; }
}
async function controllerFixture(files?: Record<string, string | Buffer>) {
  const value = await fixture(files), api = new FakeApi(value.root), controller = new SearchController(() => true, () => undefined, () => api, value.read);
  assert.ok(await controller.connect(value.root, 'http://127.0.0.1:8750'));
  return { ...value, api, controller, close: async () => { controller.dispose(); await value.close(); } };
}

test('explicit dirty command validates its flag while defaults retain saved-only shape', () => {
  assert.equal(parseFindRequest({ query: 'behavior' }, defaultOptions)?.includeUnsaved, undefined);
  assert.equal(parseFindRequest({ query: 'behavior', includeUnsaved: true }, defaultOptions)?.includeUnsaved, true);
  assert.throws(() => parseFindRequest({ query: 'behavior', includeUnsaved: 'yes' }, defaultOptions));
});
test('source eligibility mirrors language defaults, configured fnmatch globs and Python discovery', () => {
  assert.ok(eligiblePath('src/dist/app.py', [])); assert.equal(eligiblePath('src/dist/app.ts', []), false);
  for (const file of ['src/app.d.ts', 'src/app.d.mts', 'src/app.d.cts', 'src/thing.json', 'src/node_modules/x.ts']) { assert.equal(eligiblePath(file, []), false); }
  assert.equal(eligiblePath('src/test/deep/app.py', ['src/test/*']), false);
  assert.ok(globMatches('src/a/b.ts', 'src/*.ts')); assert.ok(globMatches('src/😀.ts', 'src/?.ts'));
  assert.ok(globMatches('src/a.ts', 'src/[a-z].ts')); assert.ok(globMatches('src/].ts', 'src/[]].ts'));
  assert.equal(globMatches('src/a.ts', 'src/[!a].ts'), false); assert.ok(globMatches('src/[.ts', 'src/[.ts'));
});
test('capture retains literal cookie-bearing Unicode Python and verified JS BOM with exact physical lines', async () => {
  const value = await fixture({ 'src/app.py': '# coding: ascii\r\ndef f():\r\n return 1\r\n', 'src/app.ts': '\ufefffunction f(){return 1;}\r\n' });
  try {
    const text = '# coding: ascii\r\ndef f():\r\n return "😀é\u2028"\r\n'; value.add('src/app.py', text);
    value.add('src/app.ts', '\ufefffunction f(){return "😀";}\r\n');
    const bundle = await captureBundle(value.binding, value.scope, value.read);
    assert.equal(bundle.overrides[0]?.text, text); assert.equal(bundle.overrides[0]?.content_hash, hash(text));
    assert.equal(bundle.overrides[1]?.text, '\ufeff\ufefffunction f(){return "😀";}\r\n');
    for (const buffer of bundle.overrides) { verifyOverlayResult(overlayResult(buffer), buffer); }
    assert.equal(await fs.readFile(path.join(value.root, 'src/app.py'), 'utf8'), '# coding: ascii\r\ndef f():\r\n return 1\r\n');
    await verifyBundle(value.binding, value.scope, bundle, value.read);
  } finally { await value.close(); }
});
test('new/deleted/unsupported/excluded/outside paths have concise clear exclusion status', async () => {
  const value = await fixture({ 'src/app.py': source, 'src/deleted.ts': 'function f(){return 1;}', 'outside.py': source, 'src/app.d.ts': 'declare function f():void;' });
  try {
    value.add('src/app.py', source); value.add('src/deleted.ts', 'function f(){return 2;}'); await fs.unlink(path.join(value.root, 'src/deleted.ts'));
    value.add('src/new.ts', 'function f(){return 2;}'); value.add('src/app.d.ts', 'declare function f():void;'); value.add('outside.py', source);
    value.buffers.push({ key: 'untitled', path: 'untitled:new.py', text: source, version: 1, dirty: true, closed: false });
    const bundle = await captureBundle(value.binding, value.scope, value.read);
    assert.deepEqual(bundle.overrides.map(item => item.path), ['src/app.py']);
    for (const reason of ['deleted', 'untracked', 'unsupported or excluded', 'outside source root', 'untitled or non-file']) { assert.match(bundle.excluded, new RegExp(reason)); }
    value.scope.source_exclusions = ['src/*']; assert.equal((await captureBundle(value.binding, value.scope, value.read)).overrides.length, 0);
  } finally { await value.close(); }
});
test('symlink escapes and duplicate physical aliases never become overrides', async () => {
  const value = await fixture({ 'src/app.py': source, 'outside.py': source });
  try {
    await fs.symlink('../outside.py', path.join(value.root, 'src/escape.py')); await run('git', ['-C', value.root, 'add', 'src/escape.py']);
    value.add('src/escape.py', source); await assert.rejects(captureBundle(value.binding, value.scope, value.read), /outside/);
    value.buffers.length = 0; await fs.symlink('app.py', path.join(value.root, 'src/alias.py')); await run('git', ['-C', value.root, 'add', 'src/alias.py']);
    value.add('src/app.py', source); value.add('src/alias.py', source); await assert.rejects(captureBundle(value.binding, value.scope, value.read), /same file/);
  } finally { await value.close(); }
});
test('UTF-8 text/path/count caps reject whole requests rather than dropping buffers', () => {
  validateOverrides([override('src/app.py', '😀'.repeat(65536))]);
  for (const values of [[override('src/app.py', '😀'.repeat(65537))], Array.from({ length: 17 }, (_, i) => override(`src/${i}.py`)),
    Array.from({ length: 5 }, (_, i) => override(`src/${i}.py`, 'x'.repeat(256 * 1024))), [override('src/' + 'é'.repeat(254) + '.py')],
    [override('src/app.py', '\ud800')], [{ ...override(), version: -1 }], [{ ...override(), content_hash: 'a'.repeat(64) }], [override(), override()]]) {
    assert.throws(() => validateOverrides(values));
  }
});
test('all input bindings are acknowledged even when omitted from hits and origin/hash echoes are strict', () => {
  const buffers = [override(), override('src/extra.py', source)], valid = overlayResponse('/repo', 'behavior', buffers, buffers[0]);
  assert.equal(parseSearch(valid, 'behavior', defaultOptions, buffers).scope?.overlays.length, 2);
  assert.throws(() => parseSearch(valid, 'behavior', defaultOptions));
  for (const change of [() => valid.scope!.overlays.pop(), () => valid.scope!.overlays[0]!.version++,
    () => valid.results[0]!.buffer_hash = 'a'.repeat(64), () => valid.results[0]!.source_origin = 'saved']) {
    const copy = structuredClone(valid); change(); assert.throws(() => parseSearch(valid, 'behavior', defaultOptions, buffers)); Object.assign(valid, copy);
  }
  const saved = response('/repo'); saved.results[0]!.source_origin = 'saved'; assert.equal(parseSearch(saved, 'behavior', defaultOptions).scope, undefined);
  assert.deepEqual(parseStatus({ ...status('/repo'), source_exclusions: ['src/test/*'] }).source_exclusions, ['src/test/*']);
});
test('edit/save/close/reopen and new dirty tracked membership invalidate the entire bundle', async () => {
  const value = await fixture({ 'src/app.py': source, 'src/extra.py': source });
  try {
    const buffer = value.add('src/app.py', source), bundle = await captureBundle(value.binding, value.scope, value.read);
    for (const mutate of [() => buffer.version++, () => buffer.text += '#tail', () => buffer.dirty = false,
      () => buffer.closed = true, () => buffer.key = 'reopened']) {
      const original = { ...buffer }; mutate(); await assert.rejects(verifyBundle(value.binding, value.scope, bundle, value.read), /buffers|identity/); Object.assign(buffer, original);
    }
    value.add('src/extra.py', source); await assert.rejects(verifyBundle(value.binding, value.scope, bundle, value.read), /buffers/); value.buffers.pop();
    await run('git', ['-C', value.root, 'rm', '--cached', 'src/app.py']); await assert.rejects(verifyBundle(value.binding, value.scope, bundle, value.read), /buffers/);
  } finally { await value.close(); }
});
test('disk changes outside a returned excerpt and rename/delete expire buffer proof', async () => {
  const value = await fixture();
  try {
    value.add('src/app.py', source); const bundle = await captureBundle(value.binding, value.scope, value.read);
    await fs.appendFile(path.join(value.root, 'src/app.py'), '# disk-only tail\n'); await assert.rejects(verifyBundle(value.binding, value.scope, bundle, value.read), /disk bytes/);
    await fs.rename(path.join(value.root, 'src/app.py'), path.join(value.root, 'src/renamed.py')); await assert.rejects(verifyBundle(value.binding, value.scope, bundle, value.read), /buffers/);
  } finally { await value.close(); }
});
test('capture detects edits across membership await and cancellation before filesystem requests', async () => {
  const value = await fixture();
  try {
    const buffer = value.add('src/app.py', source);
    await assert.rejects(captureBundle(value.binding, value.scope, value.read, undefined, async () => { buffer.version++; return new Set(['src/app.py']); }), /changed during capture/);
    const abort = new AbortController(); abort.abort(); let called = false;
    await assert.rejects(captureBundle(value.binding, value.scope, value.read, abort.signal, async () => { called = true; return new Set(); }), /cancelled/);
    assert.equal(called, false); const oldStatus = { ...value.scope }; delete oldStatus.source_exclusions;
    await assert.rejects(captureBundle(value.binding, oldStatus, value.read), /advertise source exclusions/);
  } finally { await value.close(); }
});
test('dirty delivery/navigation uses same editor buffer and preserves saved status and disk', async () => {
  const value = await controllerFixture();
  try {
    const buffer = value.add('src/app.py', source.replace('ready', 'unsaved 😀')), beforeStatus = value.controller.state.status;
    const search = value.controller.search('behavior', defaultOptions, true); await until(() => value.api.calls.length > 0);
    const sent = value.api.calls[0]!.buffers; value.api.deliver(overlayResponse(value.root, 'behavior', sent, sent[0])); await search;
    assert.equal(value.controller.state.phase, 'ready'); assert.equal(value.controller.state.status, beforeStatus); assert.equal(value.controller.state.status?.windows, 1);
    const entry = value.controller.state.entries[0]!; assert.ok(entry.overlay); assert.equal(await value.controller.navigation(entry, async () => true), buffer.path);
    assert.equal(verifyEditorBuffer(entry.overlay, buffer), sent[0]!.text); assert.equal(await fs.readFile(buffer.path, 'utf8'), source);
    buffer.version++; await assert.rejects(value.controller.navigation(entry, async () => true), /buffers/); assert.throws(() => verifyEditorBuffer(entry.overlay!, buffer), /version/);
  } finally { await value.close(); }
});
test('an omitted buffer edited before delivery rejects results and overridden paths cannot claim saved origin', async () => {
  const value = await controllerFixture({ 'src/app.py': source, 'src/extra.py': source });
  try {
    value.add('src/app.py', source); const extra = value.add('src/extra.py', source);
    const search = value.controller.search('behavior', defaultOptions, true); await until(() => value.api.calls.length > 0);
    extra.version++; const sent = value.api.calls[0]!.buffers; value.api.deliver(overlayResponse(value.root, 'behavior', sent, sent[0])); await search;
    assert.equal(value.controller.state.phase, 'error'); assert.equal(value.controller.state.entries.length, 0);
    const next = value.controller.search('again', defaultOptions, true); await until(() => value.api.calls.length === 2);
    const savedResponse = overlayResponse(value.root, 'again', value.api.calls[1]!.buffers);
    savedResponse.results = [{ ...response(value.root).results[0]!, path: 'src/extra.py', source_origin: 'saved' }]; savedResponse.total_source_chars = [...source].length;
    value.api.deliver(savedResponse); await next; assert.equal(value.controller.state.phase, 'error'); assert.match(value.controller.state.message, /origin/);
  } finally { await value.close(); }
});
test('save/edit events cancel exactly one ID, suppress late delivery and retain captured previews', async () => {
  const value = await controllerFixture();
  try {
    value.add('src/app.py', source); const search = value.controller.search('behavior', defaultOptions, true); await until(() => value.api.calls.length > 0);
    value.controller.buffersChanged(); value.api.deliver(overlayResponse(value.root, 'behavior', value.api.calls[0]!.buffers)); await search;
    assert.equal(value.api.calls[0]!.signal?.aborted, true); assert.deepEqual(value.api.cancellations, [value.api.calls[0]!.id]); assert.equal(value.controller.state.response, null);
    const next = value.controller.search('again', defaultOptions, true); await until(() => value.api.calls.length === 2);
    value.api.deliver(overlayResponse(value.root, 'again', value.api.calls[1]!.buffers, value.api.calls[1]!.buffers[0])); await next;
    const entry = value.controller.state.entries[0]!; value.controller.buffersChanged(); assert.equal(entry.result.source, source);
    assert.match(entry.staleReason!, /changed/); await assert.rejects(value.controller.navigation(entry, async () => true), /expired/);
  } finally { await value.close(); }
});
test('no eligible dirty inputs honestly falls back; ordinary saved search ignores dirty events', async () => {
  const value = await controllerFixture();
  try {
    value.add('src/new.py', source); const search = value.controller.search('behavior', defaultOptions, true); await until(() => value.api.calls.length > 0);
    assert.equal(value.api.calls[0]!.buffers.length, 0); value.api.deliver(response(value.root)); await search;
    assert.match(value.controller.state.message, /No eligible dirty tracked buffers/); assert.match(value.controller.state.message, /untracked/);
    value.buffers.length = 0; const ordinary = value.controller.search('saved', defaultOptions); assert.equal(value.api.calls.length, 2);
    value.controller.buffersChanged(); assert.equal(value.api.calls[1]!.signal?.aborted, false); value.api.deliver(response(value.root, 'saved')); await ordinary;
    assert.equal(value.controller.state.phase, 'ready');
  } finally { await value.close(); }
});
test('native HTTP sends literal UTF-8 above saved cap and bounds encoded JSON escaping', async () => {
  let requests = 0; const server = http.createServer((req, res) => {
    requests++; const chunks: Buffer[] = []; req.on('data', chunk => chunks.push(chunk)); req.on('end', () => {
      const raw = Buffer.concat(chunks); assert.ok(raw.includes(Buffer.from('😀é', 'utf8'))); assert.ok(raw.length > 65536);
      const body = JSON.parse(raw.toString('utf8')) as { overlays: BufferOverride[] };
      res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(overlayResponse('/repo', 'behavior', body.overlays)));
    });
  });
  server.listen(0, '127.0.0.1'); await once(server, 'listening'); const address = server.address(); assert.ok(address && typeof address === 'object');
  try {
    const client = new ServiceClient(`http://127.0.0.1:${address.port}`), buffers = [override('src/app.py', '#😀é\n' + 'x'.repeat(70000))];
    assert.equal((await client.search('behavior', defaultOptions, 'request', undefined, buffers)).scope?.overlays[0]?.content_hash, buffers[0]!.content_hash);
    const controls = Array.from({ length: 4 }, (_, i) => override(`src/${i}.py`, '\0'.repeat(256 * 1024)));
    await assert.rejects(client.search('behavior', defaultOptions, 'request', undefined, controls), /Encoded JSON/); assert.equal(requests, 1);
  } finally { await new Promise<void>(resolve => server.close(() => resolve())); }
});

test('literal excerpts include complete physical CR/CRLF lines and bind the original full window', () => {
  const buffer = override('src/app.ts', 'function f(){\r return "😀\u2029";\r\n}\n// trailing initializer\r');
  const row = { ...overlayResult(buffer), end_line: 2, source: physicalLines(buffer.text).slice(0, 2).join(''), truncated: true };
  row.source_hash = hash(row.source); verifyOverlayResult(row, buffer);
  assert.throws(() => verifyOverlayResult({ ...row, content_hash: hash(row.source) }, buffer), /literal buffer/);
  assert.equal(globMatches('a'.repeat(500) + 'c', '*a'.repeat(50) + 'b'), false);
});
test('saved hits in an overlay response retain saved navigation plus all-buffer freshness checks', async () => {
  const value = await controllerFixture({ 'src/app.py': source, 'src/extra.py': source });
  try {
    const extra = value.add('src/extra.py', source);
    const search = value.controller.search('behavior', defaultOptions, true); await until(() => value.api.calls.length > 0);
    const returned = overlayResponse(value.root, 'behavior', value.api.calls[0]!.buffers);
    returned.results = [{ ...response(value.root).results[0]!, source_origin: 'saved' }]; returned.total_source_chars = [...source].length;
    value.api.deliver(returned); await search; assert.equal(value.controller.state.phase, 'ready'); const entry = value.controller.state.entries[0]!;
    assert.equal(entry.overlay, undefined); assert.equal(await value.controller.navigation(entry, async () => false), await fs.realpath(path.join(value.root, 'src/app.py')));
    let checks = 0;
    await assert.rejects(value.controller.navigation(entry, async () => { if (++checks === 2) { extra.version++; } return false; }), /buffers/);
  } finally { await value.close(); }
});
test('cancel/dispose while status or capture waits suppresses a subsequent overlay request', async () => {
  const value = await fixture(); value.add('src/app.py', source);
  let resolveStatus!: (value: Status) => void, calls = 0, statuses = 0;
  const api: SearchApi = { status: async () => ++statuses === 1 ? value.scope : new Promise(resolve => resolveStatus = resolve), refresh: async () => 1,
    search: async () => { calls++; return response(value.root); }, cancel: async () => true };
  const controller = new SearchController(() => true, () => undefined, () => api, value.read);
  try {
    assert.ok(await controller.connect(value.root, 'http://127.0.0.1:8750'));
    const search = controller.search('behavior', defaultOptions, true); controller.cancel(); resolveStatus(value.scope); await search;
    assert.equal(calls, 0); assert.equal(controller.state.response, null);
    const next = controller.search('behavior', defaultOptions, true); controller.dispose(); resolveStatus(value.scope); await next; assert.equal(calls, 0);
  } finally { controller.dispose(); await value.close(); }
});

test('matching metadata cannot turn a wrong literal overlay excerpt into a preview result', async () => {
  const value = await controllerFixture();
  try {
    value.add('src/app.py', source.replace('ready', 'dirty'));
    const search = value.controller.search('behavior', defaultOptions, true); await until(() => value.api.calls.length > 0);
    const sent = value.api.calls[0]!.buffers, tampered = overlayResponse(value.root, 'behavior', sent, sent[0]);
    tampered.results[0]!.source = source; tampered.results[0]!.source_hash = hash(source); tampered.results[0]!.content_hash = hash(source);
    tampered.total_source_chars = [...source].length; value.api.deliver(tampered); await search;
    assert.equal(value.controller.state.phase, 'error'); assert.equal(value.controller.state.entries.length, 0); assert.match(value.controller.state.message, /literal buffer/);
  } finally { await value.close(); }
});

test('canonical editor paths remain eligible when the selected workspace uses a filesystem alias', async () => {
  const value = await fixture();
  try {
    const alias = path.join(value.root, 'workspace-alias'); await fs.symlink(value.binding.root, alias);
    const aliased = await bindRepository(alias, value.root, 'src');
    value.buffers.push({ key: 'canonical-editor', path: path.join(value.binding.root, 'src/app.py'), text: source, version: 2, dirty: true, closed: false });
    const bundle = await captureBundle(aliased, value.scope, value.read); assert.deepEqual(bundle.overrides.map(item => item.path), ['src/app.py']);
    await verifyBundle(aliased, value.scope, bundle, value.read);
  } finally { await value.close(); }
});

test('source-root symlink retarget rejects canonical dirty buffers and ordinary saved proof', async () => {
  const value = await fixture();
  try {
    const alias = path.join(value.root, 'source-alias'); await fs.symlink('src', alias);
    const binding = await bindRepository(value.root, value.root, 'source-alias'); value.add('src/app.py', source);
    const bundle = await captureBundle(binding, value.scope, value.read);
    await fs.mkdir(path.join(value.root, 'other')); await fs.writeFile(path.join(value.root, 'other/app.py'), source);
    await fs.unlink(alias); await fs.symlink('other', alias);
    await assert.rejects(verifyBundle(binding, value.scope, bundle, value.read), /source-root alias/);
    const { verifySavedResult } = await import('../src/evidence');
    await assert.rejects(verifySavedResult(binding, response(value.root).results[0]!), /source-root alias/);
  } finally { await value.close(); }
});
