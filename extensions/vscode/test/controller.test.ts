import { test } from 'node:test';
import * as assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import * as os from 'node:os';
import { SearchController } from '../src/controller';
import { SearchApi } from '../src/client';
import { defaultOptions, SearchResponse } from '../src/schema';
import { response, source, status } from './fixtures';

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void; return { promise: new Promise<T>(done => resolve = done), resolve };
}
class FakeApi implements SearchApi {
  cancellations: string[] = []; queries: { query: string; id: string; signal: AbortSignal | undefined }[] = [];
  pending = new Map<string, ReturnType<typeof deferred<SearchResponse>>>();
  constructor(readonly root: string) {}
  async status() { return status(this.root); }
  async refresh() { return 2; }
  search(query: string, _options: unknown, id: string, signal?: AbortSignal): Promise<SearchResponse> {
    this.queries.push({ query, id, signal }); const next = deferred<SearchResponse>(); this.pending.set(query, next); return next.promise;
  }
  async cancel(id: string) { this.cancellations.push(id); return true; }
}
async function setup(): Promise<{ root: string; api: FakeApi; controller: SearchController; close: () => Promise<void> }> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'semantic-controller-')); await fs.mkdir(path.join(root, 'src'));
  await fs.writeFile(path.join(root, 'src/app.py'), source);
  const api = new FakeApi(root), controller = new SearchController(() => true, () => undefined, () => api);
  assert.ok(await controller.connect(root, 'http://127.0.0.1:8750'));
  return { root, api, controller, close: async () => { controller.dispose(); await fs.rm(root, { recursive: true }); } };
}
test('replacement search cancels its request ID and suppresses late results even when transport ignores abort', async () => {
  const fixture = await setup();
  try {
    const old = fixture.controller.search('old', defaultOptions), newer = fixture.controller.search('new', defaultOptions);
    assert.equal(fixture.api.queries[0]?.signal?.aborted, true);
    assert.deepEqual(fixture.api.cancellations, [fixture.api.queries[0]?.id]);
    fixture.api.pending.get('new')?.resolve(response(fixture.root, 'new')); await newer;
    fixture.api.pending.get('old')?.resolve(response('/nonexistent/old-repository', 'old')); await old;
    assert.equal(fixture.controller.state.response?.query, 'new');
    assert.equal(fixture.controller.state.entries.length, 1);
  } finally { await fixture.close(); }
});
test('explicit cancellation does not publish a late result and disposes active requests', async () => {
  const fixture = await setup();
  try {
    const running = fixture.controller.search('slow', defaultOptions);
    fixture.controller.cancel(); fixture.api.pending.get('slow')?.resolve(response(fixture.root, 'slow')); await running;
    assert.equal(fixture.controller.state.response, null); assert.equal(fixture.controller.state.entries.length, 0);
    const second = fixture.controller.search('dispose', defaultOptions); fixture.controller.dispose();
    fixture.api.pending.get('dispose')?.resolve(response(fixture.root, 'dispose')); await second;
    assert.equal(fixture.api.cancellations.length, 2);
  } finally { await fixture.close(); }
});
test('current clean source navigates; dirty buffers, saved edits and expired entries preserve preview but block navigation', async () => {
  const fixture = await setup();
  try {
    const query = fixture.controller.search('behavior', defaultOptions); fixture.api.pending.get('behavior')?.resolve(response(fixture.root)); await query;
    const entry = fixture.controller.state.entries[0]; assert.ok(entry);
    assert.equal(await fixture.controller.navigation(entry, async () => false), await fs.realpath(path.join(fixture.root, 'src/app.py')));
    await fixture.controller.status(); // Status alone should not expire current results.
    assert.ok(await fixture.controller.navigation(entry, async () => false));
    await assert.rejects(fixture.controller.navigation(entry, async () => true), /unsaved/);
    await fs.writeFile(path.join(fixture.root, 'src/app.py'), source.replace('ready', 'changed'));
    await assert.rejects(fixture.controller.navigation(entry, async () => false), /changed after/);
    assert.equal(entry.result.source, source);
    await fixture.controller.refresh(); await assert.rejects(fixture.controller.navigation(entry, async () => false), /expired/);
  } finally { await fixture.close(); }
});
test('Workspace Trust blocks even injected connection and query implementations', async () => {
  let calls = 0;
  const controller = new SearchController(() => false, () => undefined, () => { calls++; return new FakeApi('/repo'); });
  await assert.rejects(controller.connect('/repo', 'http://127.0.0.1:8750'), /Trust/);
  await assert.rejects(controller.search('behavior', defaultOptions), /Trust/);
  assert.equal(calls, 0);
});
test('wrong repository cannot connect; file changes before delivery become captured-preview-only results', async () => {
  const fixture = await setup(), other = await fs.mkdtemp(path.join(os.tmpdir(), 'semantic-other-'));
  try {
    const mismatch = new SearchController(() => true, () => undefined, () => fixture.api);
    assert.equal(await mismatch.connect(other, 'http://127.0.0.1:8750'), false);
    assert.equal(mismatch.repository, null);
    const query = fixture.controller.search('behavior', defaultOptions);
    await fs.writeFile(path.join(fixture.root, 'src/app.py'), source.replace('ready', 'changed'));
    fixture.api.pending.get('behavior')?.resolve(response(fixture.root)); await query;
    const entry = fixture.controller.state.entries[0]; assert.ok(entry); assert.equal(entry.saved, null);
    await assert.rejects(fixture.controller.navigation(entry, async () => false), /no longer matches/);
    mismatch.dispose();
  } finally { await fixture.close(); await fs.rm(other, { recursive: true }); }
});
