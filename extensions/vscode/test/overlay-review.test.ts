import { test } from 'node:test';
import * as assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import * as os from 'node:os';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { createHash } from 'node:crypto';
import { bindRepository, readSavedEvidence, verifySavedResult } from '../src/evidence';
import { BufferOverride, EditorBuffer, captureBundle, verifyBundle, verifyEditorBuffer, verifyOverlayResult } from '../src/overlays';
import { SearchController } from '../src/controller';
import { SearchApi } from '../src/client';
import { defaultOptions, hash, physicalLines, Result, SearchOptions, SearchResponse, Status } from '../src/schema';
import { response, result, source, status } from './fixtures';

const run = promisify(execFile);
async function repository(files: Record<string, string | Buffer> = { 'src/app.py': source }) {
  const base = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'overlay-independent-')));
  const root = path.join(base, 'repository'); await fs.mkdir(root);
  for (const [name, data] of Object.entries(files)) {
    await fs.mkdir(path.dirname(path.join(root, name)), { recursive: true }); await fs.writeFile(path.join(root, name), data);
  }
  await run('git', ['-C', root, 'init', '-q']); await run('git', ['-C', root, 'add', '.']);
  const scope: Status = { ...status(root), source_exclusions: [] };
  const buffers: EditorBuffer[] = [];
  const add = (filename: string, text: string, version = 1): EditorBuffer => {
    const value = { key: `document-${buffers.length}`, path: path.join(root, filename), text, version, dirty: true, closed: false };
    buffers.push(value); return value;
  };
  return { base, root, scope, buffers, add, read: () => buffers, close: () => fs.rm(base, { recursive: true, force: true }) };
}
async function aliasFixture() {
  const value = await repository(); const second = path.join(value.base, 'second');
  await fs.mkdir(path.join(second, 'src'), { recursive: true }); await fs.writeFile(path.join(second, 'src/app.py'), source);
  const alias = path.join(value.base, 'selected-workspace'); await fs.symlink(value.root, alias);
  return { ...value, alias, retarget: async () => { await fs.unlink(alias); await fs.symlink(second, alias); },
    assertOriginal: async () => assert.equal(await fs.readFile(path.join(value.root, 'src/app.py'), 'utf8'), source) };
}
class DeferredApi implements SearchApi {
  statusCalls = 0;
  calls: { query: string; buffers: readonly BufferOverride[]; id: string; resolve(value: SearchResponse): void }[] = [];
  constructor(readonly root: string) {}
  async status(): Promise<Status> { this.statusCalls++; return { ...status(this.root), source_exclusions: [] }; }
  async refresh(): Promise<number> { return 1; }
  search(query: string, _options: SearchOptions, id: string, _signal?: AbortSignal, buffers: readonly BufferOverride[] = []): Promise<SearchResponse> {
    return new Promise(resolve => this.calls.push({ query, buffers, id, resolve }));
  }
  async cancel(): Promise<boolean> { return true; }
}
async function dispatched(api: DeferredApi): Promise<void> {
  const deadline = Date.now() + 2000;
  while (!api.calls.length && Date.now() < deadline) { await new Promise(resolve => setTimeout(resolve, 2)); }
  assert.equal(api.calls.length, 1);
}
function overlayRow(buffer: BufferOverride): Result {
  const lines = physicalLines(buffer.text);
  return { ...result(buffer.text), id: `${buffer.path}:literal:1-${lines.length}`, path: buffer.path,
    start_line: 1, end_line: lines.length, window_end_line: lines.length, source_origin: 'overlay',
    buffer_version: buffer.version, buffer_hash: buffer.content_hash };
}
function overlayResponse(root: string, query: string, buffers: readonly BufferOverride[]): SearchResponse {
  const row = overlayRow(buffers[0]!);
  return { ...response(root, query), results: [row], total_source_chars: [...row.source].length,
    scope: { source_state: 'overlay', saved_source_manifest: 'a'.repeat(64),
      overlays: buffers.map(({ path, version, content_hash }) => ({ path, version, content_hash })) } };
}

test('selected workspace alias retarget invalidates a captured canonical dirty editor', async t => {
  const value = await aliasFixture(); t.after(value.close);
  const binding = await bindRepository(value.alias, value.root, 'src');
  value.add('src/app.py', source.replace('ready', 'dirty'));
  const bundle = await captureBundle(binding, value.scope, value.read);
  assert.equal(bundle.overrides.length, 1);
  await value.retarget(); await value.assertOriginal();
  await assert.rejects(verifyBundle(binding, value.scope, bundle, value.read), /workspace|alias|reconnect/);
  await assert.rejects(captureBundle(binding, value.scope, value.read), /workspace|alias|reconnect/);
});

test('saved evidence rejects retargeted workspace even with identical unchanged canonical bytes', async t => {
  const value = await aliasFixture(); t.after(value.close);
  const binding = await bindRepository(value.alias, value.root, 'src');
  const before = await verifySavedResult(binding, result());
  await value.retarget(); await value.assertOriginal();
  await assert.rejects(readSavedEvidence(binding, 'src/app.py'), /workspace|alias|reconnect/);
  await assert.rejects(verifySavedResult(binding, result(), before), /workspace|alias|reconnect/);
});

test('saved-mode delivery aborts after selected alias changes while backend stays on original repo', async t => {
  const value = await aliasFixture(); t.after(value.close); const api = new DeferredApi(value.root);
  const controller = new SearchController(() => true, () => undefined, () => api); t.after(() => controller.dispose());
  assert.equal(await controller.connect(value.alias, 'http://127.0.0.1:8750'), true);
  const search = controller.search('behavior', defaultOptions); await dispatched(api);
  await value.retarget(); await value.assertOriginal(); api.calls[0]!.resolve(response(value.root)); await search;
  assert.equal(controller.state.phase, 'error'); assert.equal(controller.state.entries.length, 0);
  assert.equal(controller.state.response, null); assert.match(controller.state.message, /workspace|alias|reconnect/);
});

test('saved navigation and status cannot revive an original-repo result after workspace retarget', async t => {
  const value = await aliasFixture(); t.after(value.close); const api = new DeferredApi(value.root);
  const controller = new SearchController(() => true, () => undefined, () => api); t.after(() => controller.dispose());
  assert.ok(await controller.connect(value.alias, 'http://127.0.0.1:8750'));
  const search = controller.search('behavior', defaultOptions); await dispatched(api); api.calls[0]!.resolve(response(value.root)); await search;
  const entry = controller.state.entries[0]!; assert.ok(entry.saved);
  await value.retarget(); await value.assertOriginal();
  await assert.rejects(controller.navigation(entry, async () => false), /workspace|alias|reconnect/);
  await controller.status(); assert.equal(controller.state.phase, 'error');
});

test('connect observes selected alias at completion rather than initial status dispatch', async t => {
  const value = await aliasFixture(); t.after(value.close); let resolve!: (value: Status) => void;
  const api: SearchApi = { status: () => new Promise(r => resolve = r), refresh: async () => 1,
    search: async () => response(value.root), cancel: async () => true };
  const controller = new SearchController(() => true, () => undefined, () => api); t.after(() => controller.dispose());
  const connecting = controller.connect(value.alias, 'http://127.0.0.1:8750');
  await value.retarget(); resolve(value.scope); assert.equal(await connecting, false);
  assert.equal(controller.repository, null); assert.equal(controller.state.phase, 'error'); await value.assertOriginal();
});

test('literal repaired Python buffer does not decode or overwrite malformed saved-cookie bytes', async t => {
  const bytes = Buffer.concat([Buffer.from('# coding: ascii\r\ndef implementation():\r\n return "'), Buffer.from([255]), Buffer.from('"\r\n')]);
  const value = await repository({ 'src/app.py': bytes }); t.after(value.close);
  const binding = await bindRepository(value.root, value.root, 'src');
  const text = '# coding: ascii\r\ndef implementation():\r\n return "λ😀\u2028"\r\n';
  const editor = value.add('src/app.py', text, 0), bundle = await captureBundle(binding, value.scope, value.read);
  assert.equal(bundle.overrides[0]!.text, text); assert.equal(bundle.overrides[0]!.content_hash, hash(text));
  assert.equal(verifyEditorBuffer(bundle.evidence.get('src/app.py')!, editor), text);
  verifyOverlayResult(overlayRow(bundle.overrides[0]!), bundle.overrides[0]!);
  assert.deepEqual(await fs.readFile(editor.path), bytes); await verifyBundle(binding, value.scope, bundle, value.read);
});

test('tracked membership does not substitute staged source for current saved-byte proof', async t => {
  const value = await repository(); t.after(value.close); const filename = path.join(value.root, 'src/app.py');
  const working = source + '# saved but not staged\n'; await fs.writeFile(filename, working);
  const blob = (await run('git', ['-C', value.root, 'show', ':src/app.py'])).stdout; assert.equal(blob, source);
  value.add('src/app.py', source.replace('ready', 'dirty'));
  const binding = await bindRepository(value.root, value.root, 'src'), bundle = await captureBundle(binding, value.scope, value.read);
  assert.equal(bundle.evidence.get('src/app.py')!.fileHash, createHash('sha256').update(working).digest('hex'));
  await fs.writeFile(filename, source); await assert.rejects(verifyBundle(binding, value.scope, bundle, value.read), /disk bytes|saved membership/);
});

test('retargeting an internal file alias to equal bytes invalidates its canonical identity', async t => {
  const value = await repository({ 'src/app.py': source, 'src/other.py': source }); t.after(value.close);
  const alias = path.join(value.root, 'src/link.py'); await fs.symlink('app.py', alias);
  await run('git', ['-C', value.root, 'add', 'src/link.py']); value.add('src/link.py', source);
  const binding = await bindRepository(value.root, value.root, 'src'), bundle = await captureBundle(binding, value.scope, value.read);
  await fs.unlink(alias); await fs.symlink('other.py', alias);
  assert.equal(await fs.readFile(alias, 'utf8'), source);
  await assert.rejects(verifyBundle(binding, value.scope, bundle, value.read), /identity|disk bytes|saved membership/);
});

test('a hidden saved JS BOM and two literal dirty BOMs remain three distinct characters', async t => {
  const value = await repository({ 'src/marker.ts': '\ufefffunction marker(){return 1;}\r\n' }); t.after(value.close);
  const editor = value.add('src/marker.ts', '\ufeff\ufefffunction marker(){return "😀\u2029";}\r\n');
  const binding = await bindRepository(value.root, value.root, 'src'), bundle = await captureBundle(binding, value.scope, value.read);
  assert.equal(bundle.overrides[0]!.text, '\ufeff' + editor.text);
  assert.ok(bundle.overrides[0]!.text.startsWith('\ufeff\ufeff\ufeff'));
  assert.equal(verifyEditorBuffer(bundle.evidence.get('src/marker.ts')!, editor), bundle.overrides[0]!.text);
  verifyOverlayResult(overlayRow(bundle.overrides[0]!), bundle.overrides[0]!);
});

test('saved-only search never reads editor text and overlay delivery rejects a reopened identity', async t => {
  const value = await repository(); t.after(value.close); const api = new DeferredApi(value.root);
  let reads = 0; const controller = new SearchController(() => true, () => undefined, () => api, () => { reads++; return value.buffers; });
  t.after(() => controller.dispose()); assert.ok(await controller.connect(value.root, 'http://127.0.0.1:8750'));
  const saved = controller.search('saved', defaultOptions); await dispatched(api); api.calls[0]!.resolve(response(value.root, 'saved')); await saved;
  assert.equal(reads, 0);
  const editor = value.add('src/app.py', source), pending = controller.search('dirty', defaultOptions, true);
  const deadline = Date.now() + 2000; while (api.calls.length < 2 && Date.now() < deadline) { await new Promise(resolve => setTimeout(resolve, 2)); }
  assert.equal(api.calls.length, 2); editor.key = 'different reopened document';
  api.calls[1]!.resolve(overlayResponse(value.root, 'dirty', api.calls[1]!.buffers)); await pending;
  assert.equal(controller.state.phase, 'error'); assert.equal(controller.state.entries.length, 0);
  assert.equal(controller.state.response, null); assert.match(controller.state.message, /identity|membership/);
});
