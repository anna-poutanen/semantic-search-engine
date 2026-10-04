import { test } from 'node:test';
import * as assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import * as os from 'node:os';
import * as http from 'node:http';
import { createHash } from 'node:crypto';

const { startRelay, forward } = require('../../scripts/host-relay.cjs');
const delay = (milliseconds: number) => new Promise<void>(resolve => setTimeout(resolve, milliseconds));
const hash = (value: Buffer | string) => createHash('sha256').update(value).digest('hex');
type Relay = { url: string; records: Record<string, any>[]; close(): Promise<Record<string, any>> };
type Test = { after(fn: () => Promise<void>): void };

async function fixture(t: Test) {
  const repo = await fs.mkdtemp(path.join(os.tmpdir(), 'relay-close-cpu-'));
  t.after(() => fs.rm(repo, { recursive: true, force: true }));
  await fs.mkdir(path.join(repo, 'src'));
  await fs.writeFile(path.join(repo, 'src/inventory.py'), 'def only():\n    return 1\n');
  return repo;
}
async function backend(t: Test, handler: http.RequestListener) {
  const server = http.createServer(handler);
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); });
  return `http://127.0.0.1:${(server.address() as import('node:net').AddressInfo).port}`;
}

test('close retains an actual delayed backend receipt that finishes within its bounded drain', async t => {
  const repo = await fixture(t);
  let started!: () => void;
  const received = new Promise<void>(resolve => { started = resolve; });
  const url = await backend(t, (_request, response) => {
    started(); setTimeout(() => { response.writeHead(200, { 'Content-Type': 'application/json' }); response.end('{"results":[]}'); }, 40);
  });
  const relay: Relay = await startRelay(url, repo, { closeGraceMs: 500 });
  t.after(async () => { await relay.close(); });
  const input = { query: 'Delayed CPU receipt', request_id: 'delayed' };
  const request = forward(relay.url, '/search', 'POST', Buffer.from(JSON.stringify(input))).catch(() => undefined);
  await received;
  const closed = await relay.close(); await request;
  assert.equal(closed.unfinished_receipts, 0); assert.equal(closed.records_stable, true);
  assert.equal(relay.records.length, 1);
  assert.deepEqual({ ...relay.records[0]!.request }, input);
  assert.equal(relay.records[0]!.status, 200);
  assert.equal(relay.records[0]!.transport_error, undefined);
  assert.equal(relay.records[0]!.incomplete, undefined);
  assert.deepEqual({ ...relay.records[0]!.response }, { results: [] });
});

test('close preserves unfinished request/source identity and aborts only its owned forward', async t => {
  const repo = await fixture(t);
  let started!: () => void, connectionClosed!: () => void;
  const received = new Promise<void>(resolve => { started = resolve; });
  const upstreamClosed = new Promise<void>(resolve => { connectionClosed = resolve; });
  let heldResponse!: http.ServerResponse;
  const url = await backend(t, (_request, response) => {
    heldResponse = response; response.once('close', connectionClosed); started();
  });
  const relay: Relay = await startRelay(url, repo, { closeGraceMs: 30 });
  t.after(async () => { await relay.close(); });
  const input = { query: 'Unfinished CPU receipt', request_id: 'unfinished' };
  const body = Buffer.from(JSON.stringify(input));
  const request = forward(relay.url, '/search', 'POST', body).catch(() => undefined);
  await received;
  const before = performance.now(); const closed = await relay.close(); await request;
  assert.ok(performance.now() - before < 2000, 'Relay cleanup remains bounded');
  assert.equal(closed.unfinished_receipts, 1); assert.equal(closed.records_stable, true);
  assert.equal(relay.records.length, 1);
  const record = relay.records[0]!;
  assert.equal(record.incomplete, true); assert.equal(record.closure_stage, 'forwarding_backend');
  assert.equal(record.endpoint, '/search'); assert.equal(record.method, 'POST');
  assert.deepEqual({ ...record.request }, input); assert.equal(record.request_sha256, hash(body));
  assert.equal(record.request_body_complete, true); assert.equal(record.request_bytes_received, body.length);
  assert.equal(record.received_prefix_sha256, hash(body));
  assert.equal(record.before_source_sha256, hash('def only():\n    return 1\n'));
  assert.match(record.before_source_manifest_sha256, /^[a-f0-9]{64}$/);
  assert.equal(record.backend_status, null); assert.equal(record.backend_finished_utc, null);
  assert.match(record.transport_error, /closed before handler completed/);
  let timer!: ReturnType<typeof setTimeout>;
  try { await Promise.race([upstreamClosed, new Promise<void>((_resolve, reject) => { timer = setTimeout(() => reject(new Error('Owned upstream connection remained open')), 1000); })]); }
  finally { clearTimeout(timer); }
  const frozen = JSON.stringify(relay.records);
  heldResponse.writeHead(200, { 'Content-Type': 'application/json' }); heldResponse.end('{"results":[]}');
  await delay(50);
  assert.equal(JSON.stringify(relay.records), frozen, 'No late receipt appends after publication');
  assert.deepEqual(await relay.close(), closed, 'Repeated close is idempotent');
});

test('a pending search-start observer cannot begin a backend forward after forced close', async t => {
  const repo = await fixture(t); let calls = 0, started!: () => void, release!: () => void;
  const received = new Promise<void>(resolve => { started = resolve; });
  const held = new Promise<void>(resolve => { release = resolve; });
  const url = await backend(t, (_request, response) => { calls++; response.writeHead(200, { 'Content-Type': 'application/json' }); response.end('{"results":[]}'); });
  const relay: Relay = await startRelay(url, repo, { closeGraceMs: 10, onSearchStart: async () => { started(); await held; } });
  t.after(async () => { release(); await relay.close(); });
  const input = { query: 'Observer interrupted', request_id: 'observer' };
  const request = forward(relay.url, '/search', 'POST', Buffer.from(JSON.stringify(input))).catch(() => undefined);
  await received; const closed = await relay.close(); await request;
  assert.equal(closed.unfinished_receipts, 1); assert.equal(relay.records[0]!.closure_stage, 'recording_search_start');
  assert.deepEqual({ ...relay.records[0]!.request }, input);
  const frozen = JSON.stringify(relay.records);
  release(); await delay(30);
  assert.equal(calls, 0); assert.equal(JSON.stringify(relay.records), frozen);
});

test('completed normal relay requests retain their original receipt shape', async t => {
  const repo = await fixture(t);
  const url = await backend(t, (_request, response) => { response.writeHead(200, { 'Content-Type': 'application/json' }); response.end('{"ready":true}'); });
  const relay: Relay = await startRelay(url, repo);
  t.after(async () => { await relay.close(); });
  await forward(relay.url, '/status', 'GET', Buffer.alloc(0));
  const frozen = JSON.stringify(relay.records);
  const closed = await relay.close();
  assert.equal(closed.handlers_at_close, 0); assert.equal(closed.unfinished_receipts, 0);
  assert.equal(JSON.stringify(relay.records), frozen);
  assert.equal(relay.records[0]!.incomplete, undefined);
  assert.equal(relay.records[0]!.closure_stage, undefined);
});

test('closing an incomplete inbound body retains its actual received prefix without inventing a request', async t => {
  const repo = await fixture(t); let calls = 0;
  const url = await backend(t, (_request, response) => { calls++; response.end(); });
  const relay: Relay = await startRelay(url, repo, { closeGraceMs: 30 });
  t.after(async () => { await relay.close(); });
  const prefix = Buffer.from('{"query":"partial');
  const client = http.request(relay.url + '/search', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
  client.on('error', () => undefined); client.write(prefix);
  await delay(20);
  const closed = await relay.close();
  assert.equal(closed.unfinished_receipts, 1); assert.equal(calls, 0);
  assert.equal(relay.records.length, 1);
  const record = relay.records[0]!;
  assert.equal(record.incomplete, true); assert.equal(record.closure_stage, 'reading_request');
  assert.equal(record.request, null); assert.equal(record.request_sha256, null);
  assert.equal(record.request_body_complete, false); assert.equal(record.request_bytes_received, prefix.length);
  assert.equal(record.received_prefix_sha256, hash(prefix));
});

test('close releases an observer-held completed response and labels that release distinctly from cancellation', async t => {
  const repo = await fixture(t);
  let backendReady!: () => void;
  const ready = new Promise<void>(resolve => { backendReady = resolve; });
  const url = await backend(t, (_request, response) => {
    response.writeHead(200, { 'Content-Type': 'application/json' }); response.end('{"results":[]}'); backendReady();
  });
  const relay: Relay = await startRelay(url, repo, { holdQuery: 'hold', closeGraceMs: 100 });
  t.after(async () => { await relay.close(); });
  const request = forward(relay.url, '/search', 'POST', Buffer.from('{"query":"hold","request_id":"held"}')).catch(() => undefined);
  await ready;
  const closed = await relay.close(); await request;
  assert.equal(closed.unfinished_receipts, 0);
  assert.equal(relay.records[0]!.status, 200);
  assert.equal(relay.records[0]!.hold_released_by_close, true);
  assert.equal(relay.records.filter(row => row.endpoint === '/cancel').length, 0);
});
