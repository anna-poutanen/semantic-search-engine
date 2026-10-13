import { strict as assert } from 'node:assert';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import * as os from 'node:os';
import * as http from 'node:http';
import { test } from 'node:test';
import { hash } from '../src/schema';

const { createFixture, source } = require('../../scripts/host-fixture.cjs');
const { argumentsFrom, redact } = require('../../scripts/smoke-host.cjs');
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
test('host launch requires explicit run and complete absolute fixture/output arguments', () => {
  assert.throws(() => argumentsFrom(['--repo', '/fixture']));
  assert.throws(() => argumentsFrom(['--run', '--repo', 'relative', '--url', 'http://127.0.0.1:1', '--output', '/out']));
  assert.throws(() => argumentsFrom(['--run', '--repo', '/fixture', '--url', 'http://127.0.0.1:1', '--output', '/out', '--unknown', 'x']));
  assert.deepEqual(argumentsFrom(['--run', '--repo', '/fixture', '--url', 'http://127.0.0.1:1', '--output', '/out']),
    { repo: '/fixture', url: 'http://127.0.0.1:1', output: '/out' });
  assert.deepEqual(redact({ source: '/home/user/fixture/a', nested: ['/home/user/private'] },
    [['/home/user/fixture', '<fixture>'], ['/home/user', '<home>']]), { source: '<fixture>/a', nested: ['<home>/private'] });
});
test('fixture preparation creates saved tracked generic source without starting a service/model', async t => {
  const repo = await fixture(t);
  assert.equal(await fs.readFile(path.join(repo, 'src/inventory.py'), 'utf8'), source);
  await assert.rejects(createFixture(repo));
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
