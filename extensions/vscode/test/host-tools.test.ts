import { strict as assert } from 'node:assert';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import * as os from 'node:os';
import * as http from 'node:http';
import * as net from 'node:net';
import { test } from 'node:test';
import { hash } from '../src/schema';

const { createFixture, source, mixedSources } = require('../../scripts/host-fixture.cjs');
const { argumentsFrom, redact, createRunDirectory, qwenReceiptChecks, relayClosureChecks } = require('../../scripts/smoke-host.cjs');
const { startRelay, forward } = require('../../scripts/host-relay.cjs');
type Relay = { url: string; records: Record<string, any>[]; close(): Promise<void> };
async function fixture(t: { after(fn: () => Promise<void>): void }): Promise<string> {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'editor-host-cpu-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const repo = path.join(directory, 'repo');
  await createFixture(repo); return repo;
}
async function backend(t: { after(fn: () => Promise<void>): void }, handler: http.RequestListener): Promise<string> {
  const server = http.createServer(handler);
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); });
  return `http://127.0.0.1:${(server.address() as import('node:net').AddressInfo).port}`;
}
function result() {
  const text = 'def normalize_item(value):\n    return value.strip().lower()\n';
  return { results: [{ path: 'src/inventory.py', symbol: 'normalize_item', source: text,
    start_line: 1, end_line: 2, window_end_line: 2, source_hash: hash(text), content_hash: hash(text) }] };
}
test('isolated host user data supports a real Unix IPC socket', { skip: process.platform === 'win32' }, async t => {
  const directory = await createRunDirectory();
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const userData = path.join(directory, 'user-data'); await fs.mkdir(userData);
  const socket = path.join(userData, '1.14-main.sock');
  assert.ok(Buffer.byteLength(socket) <= 100);
  const server = net.createServer();
  await new Promise<void>((resolve, reject) => { server.once('error', reject); server.listen(socket, resolve); });
  await new Promise<void>(resolve => server.close(() => resolve()));
});
test('host launch requires explicit run and complete absolute fixture/output arguments', () => {
  assert.throws(() => argumentsFrom(['--repo', '/fixture']));
  assert.throws(() => argumentsFrom(['--run', '--repo', 'relative', '--url', 'http://127.0.0.1:1', '--output', '/out']));
  assert.throws(() => argumentsFrom(['--run', '--repo', '/fixture', '--url', 'http://127.0.0.1:1', '--output', '/out', '--unknown', 'x']));
  assert.deepEqual(argumentsFrom(['--run', '--repo', '/fixture', '--url', 'http://127.0.0.1:1', '--output', '/out']),
    { repo: '/fixture', url: 'http://127.0.0.1:1', output: '/out' });
  assert.deepEqual(redact({ source: '/home/user/fixture/a', nested: ['/home/user/private'] },
    [['/home/user/fixture', '<fixture>'], ['/home/user', '<home>']]), { source: '<fixture>/a', nested: ['<home>/private'] });
});
test('Qwen host flag is explicit and separate from other dirty/saved-edit smoke modes', () => {
  const base = ['--run', '--repo', '/fixture', '--url', 'http://127.0.0.1:1', '--output', '/out'];
  assert.deepEqual(argumentsFrom([...base, '--qwen-reranker']), { repo: '/fixture', url: 'http://127.0.0.1:1', output: '/out', qwenReranker: true });
  assert.throws(() => argumentsFrom([...base, '--qwen-reranker', '--qwen-reranker']), /Duplicate/);
  for (const mode of ['--overlays', '--saved-edits']) { assert.throws(() => argumentsFrom([...base, '--qwen-reranker', mode]), /separate/); }
});
test('prospective Qwen receipt gate requires real saved/dirty provider identity and the configured fast-off request', () => {
  const request = { retrieval: 'hybrid', rerank: true, rerank_provider: 'qwen', candidates: 24,
    max_results: 5, token_budget: 2000, source_budget: 12000, include_dependencies: false };
  const saved = { endpoint: '/search', status: 200, request, response: { rerank: true, rerank_provider: 'qwen' } };
  const dirty = { ...saved, request: { ...request, overlays: [{ path: 'src/delta.ts' }] } };
  const { rerank_provider: _provider, ...fastRequest } = request;
  const fast = { ...saved, request: { ...fastRequest, rerank: false }, response: { rerank: false } };
  const valid = [saved, dirty, fast]; assert.ok(Object.values(qwenReceiptChecks(valid)).every(Boolean));
  for (const rows of [[], [saved, fast], [saved, dirty], [saved, dirty, fast, saved],
    [saved, { ...dirty, request: { ...dirty.request, candidates: 4 } }, fast],
    [saved, { ...dirty, response: { rerank: true, rerank_provider: 'h2o' } }, fast],
    [saved, { ...dirty, response: { rerank: true } }, fast],
    [saved, { ...dirty, request: { ...request } }, fast],
    [saved, dirty, { ...fast, request: { ...fast.request, rerank_provider: 'qwen' } }],
    [saved, dirty, { ...fast, response: { rerank: true } }]]) {
    assert.ok(Object.values(qwenReceiptChecks(rows)).some(value => value === false));
  }
});
test('host publication rejects unfinished or transport-error evidence added by relay close', () => {
  const closed = { records_stable: true, unfinished_receipts: 0, forced_incomplete_receipts: 0 };
  const clean = [{ endpoint: '/search', status: 200 }];
  assert.ok(Object.values(relayClosureChecks(closed, clean)).every(Boolean));
  for (const [closure, rows] of [
    [{ ...closed, records_stable: false }, clean],
    [{ ...closed, unfinished_receipts: 1 }, clean],
    [{ ...closed, forced_incomplete_receipts: 1 }, clean],
    [closed, [...clean, { endpoint: '/search', incomplete: true }]],
    [closed, [...clean, { endpoint: '/search', transport_error: 'late handler unfinished' }]],
    [closed, [...clean, { endpoint: '/search', hold_released_by_close: true }]]
  ] as [Record<string, unknown>, Record<string, unknown>[]][]) {
    assert.ok(Object.values(relayClosureChecks(closure, rows)).some(value => value === false));
  }
});
test('fixture preparation creates saved tracked generic source without starting a service/model', async t => {
  const repo = await fixture(t);
  assert.equal(await fs.readFile(path.join(repo, 'src/inventory.py'), 'utf8'), source);
  await assert.rejects(createFixture(repo));
});
test('mixed host fixture preserves Python plus BOM/CRLF JavaScript and typed implementation source', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'editor-host-mixed-cpu-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const repo = path.join(directory, 'repo');
  const manifest = await createFixture(repo, { mixed: true });
  assert.equal(manifest.mode, 'mixed'); assert.equal(manifest.functions, 5);
  assert.equal(manifest.model_loaded, false); assert.equal(manifest.service_started, false);
  for (const [filename, text] of Object.entries(mixedSources)) { assert.equal(await fs.readFile(path.join(repo, filename), 'utf8'), text); }
  assert.deepEqual(manifest.cases.map((row: { language: string }) => row.language), ['python', 'javascript', 'typescript']);
});
test('independent relay verifies source bounds/hashes and linked cancellation without a model', async t => {
  const repo = await fixture(t), requests: string[] = [];
  const url = await backend(t, (request, response) => {
    requests.push(request.url!);
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify(request.url === '/search' ? result() : { cancelled: true, request_id: 'fixture-request' }));
  });
  let observed!: () => void;
  const started = new Promise<void>(resolve => { observed = resolve; });
  const relay: Relay = await startRelay(url, repo, { holdQuery: 'hold', onSearchStart: () => observed() });
  t.after(() => relay.close());
  const search = forward(relay.url, '/search', 'POST', Buffer.from(JSON.stringify({ query: 'hold', request_id: 'fixture-request' })));
  await started;
  await forward(relay.url, '/cancel', 'POST', Buffer.from(JSON.stringify({ request_id: 'fixture-request' })));
  const response = await search;
  assert.equal(response.status, 200);
  assert.deepEqual(requests.sort(), ['/cancel', '/search']);
  const receipt = relay.records.find(row => row.endpoint === '/search'); assert.ok(receipt);
  assert.equal(receipt.response_held_for_cancel_test, true);
  assert.equal(receipt.returned_source_checks.length, 1);
  assert.equal(receipt.returned_source_checks[0].exact_saved_source, true);
  assert.equal(receipt.before_source_sha256, receipt.after_source_sha256);
  assert.equal(receipt.request.request_id, 'fixture-request');
});
test('relay refuses browser origins and never follows backend redirects', async t => {
  const repo = await fixture(t); let calls = 0;
  const url = await backend(t, (_, response) => { calls++; response.writeHead(302, { Location: 'http://127.0.0.1:9/private' }); response.end(); });
  const relay: Relay = await startRelay(url, repo); t.after(() => relay.close());
  const address = new URL(relay.url);
  const browser = await new Promise<number>(resolve => {
    const request = http.get({ hostname: '127.0.0.1', port: address.port, path: '/status',
      headers: { Host: address.host, Origin: 'https://example.invalid' } }, response => { response.resume(); response.on('end', () => resolve(response.statusCode!)); });
    request.on('error', error => { throw error; });
  });
  assert.equal(browser, 403); assert.equal(calls, 0);
  const redirected = await forward(relay.url, '/status', 'GET', Buffer.alloc(0));
  assert.equal(redirected.status, 502); assert.equal(calls, 1);
  assert.equal(relay.records[0]?.transport_error, 'Unexpected backend transport response');
});
test('relay source proof fails when source changes during the actual upstream response', async t => {
  const repo = await fixture(t);
  const url = await backend(t, async (_, response) => {
    await fs.writeFile(path.join(repo, 'src/inventory.py'), source.replace('value.strip().lower()', 'value.upper()'));
    response.writeHead(200, { 'Content-Type': 'application/json' }); response.end(JSON.stringify(result()));
  });
  const relay: Relay = await startRelay(url, repo); t.after(() => relay.close());
  await forward(relay.url, '/search', 'POST', Buffer.from(JSON.stringify({ query: 'generic', request_id: 'changed-source' })));
  const receipt = relay.records[0]; assert.ok(receipt);
  assert.notEqual(receipt.before_source_sha256, receipt.after_source_sha256);
  assert.equal(receipt.returned_source_checks[0].exact_saved_source, false);
});
test('mixed-language relay independently preserves exact BOM and typed implementation source hashes', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'editor-host-mixed-proof-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const repo = path.join(directory, 'repo'); await createFixture(repo, { mixed: true });
  const rows = Object.entries(mixedSources).filter(([filename]) => !filename.endsWith('.py')).map(([filename, source]) => {
    const text = source as string;
    return { path: filename, symbol: filename.endsWith('.js') ? 'decorate_code' : 'calculate_delta', source: text,
      start_line: 1, end_line: 3, window_end_line: 3, source_hash: hash(text), content_hash: hash(text) };
  });
  const url = await backend(t, (_, response) => { response.writeHead(200, { 'Content-Type': 'application/json' }); response.end(JSON.stringify({ results: rows })); });
  const relay: Relay = await startRelay(url, repo); t.after(() => relay.close());
  await forward(relay.url, '/search', 'POST', Buffer.from(JSON.stringify({ query: 'generic code', request_id: 'mixed-proof' })));
  const receipt = relay.records[0]; assert.ok(receipt);
  assert.equal(receipt.returned_source_checks.length, 2);
  assert.ok(receipt.returned_source_checks.every((row: { exact_saved_source: boolean }) => row.exact_saved_source));
  assert.equal(receipt.before_source_manifest_sha256, receipt.after_source_manifest_sha256);
});
