import { strict as assert } from 'node:assert';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import * as os from 'node:os';
import * as http from 'node:http';
import { test } from 'node:test';
import { hash, physicalLines } from '../src/schema';

const { createFixture } = require('../../scripts/host-fixture.cjs');
const { sourceChecks, snapshot, overlayBindings, startRelay, forward } = require('../../scripts/host-relay.cjs');
async function fixture(t: { after(fn: () => Promise<void>): void }): Promise<string> {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'host-overlay-proof-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const repo = path.join(directory, 'repo'); await createFixture(repo, { mixed: true }); return repo;
}
function input() {
  return { query: 'dirty code', overlays: [
    { path: 'src/decorate.js', text: '\ufeffexport function dirty_code() {\r\n  return "café🧪";\r\n}\r\n', version: 7 },
    { path: 'src/delta.ts', text: 'export function dirty_delta(x: number): number { return x / 2; }\n', version: 8 }
  ].map(row => ({ ...row, content_hash: hash(row.text) })) };
}
function response(request: ReturnType<typeof input>) {
  const row = request.overlays[0];
  assert.ok(row);
  return { scope: { source_state: 'overlay', overlays: request.overlays.map(({ text, ...binding }) => binding) },
    results: [{ path: row.path, symbol: 'dirty_code', source: row.text, source_hash: hash(row.text),
      content_hash: hash(row.text), start_line: 1, end_line: physicalLines(row.text).length,
      window_end_line: physicalLines(row.text).length, source_origin: 'overlay', buffer_version: row.version, buffer_hash: row.content_hash }] };
}
test('receipt proves literal dirty Unicode/BOM/CRLF source against all input bindings', async t => {
  const repo = await fixture(t), saved = await snapshot(repo), request = input(), result = response(request);
  assert.equal(overlayBindings(request, result).valid, true);
  const checks = await sourceChecks(repo, result, saved, saved, request);
  assert.equal(checks.length, 1); assert.equal(checks[0].exact_source, true);
  assert.equal(checks[0].exact_saved_source, false); assert.equal(checks[0].source_origin, 'overlay');
  const omitted = structuredClone(result); omitted.scope.overlays.pop();
  assert.equal((await sourceChecks(repo, omitted, saved, saved, request))[0].exact_source, false);
  const changed = structuredClone(result); changed.scope.overlays[1]!.version++;
  assert.equal((await sourceChecks(repo, changed, saved, saved, request))[0].exact_source, false);
  const falseOrigin = structuredClone(result); falseOrigin.results[0]!.source_origin = 'saved';
  assert.equal((await sourceChecks(repo, falseOrigin, saved, saved, request))[0].exact_source, false);
  const after = structuredClone(saved); after.files['src/decorate.js'].sha256 = '0'.repeat(64);
  after.source_manifest_sha256 = '1'.repeat(64);
  assert.equal((await sourceChecks(repo, result, saved, after, request))[0].exact_source, false);
});
test('relay permits bounded overlay bodies above saved cap and rejects oversized saved requests', async t => {
  const repo = await fixture(t); let calls = 0;
  const backend = http.createServer(async (request, result) => {
    calls++; const chunks: Buffer[] = [];
    for await (const chunk of request) { chunks.push(chunk); }
    const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    result.writeHead(200, { 'Content-Type': 'application/json' });
    result.end(JSON.stringify({ scope: { source_state: 'overlay', overlays: body.overlays.map(({ text, ...binding }: any) => binding) }, results: [] }));
  });
  await new Promise<void>(resolve => backend.listen(0, '127.0.0.1', resolve));
  t.after(async () => { backend.closeAllConnections(); await new Promise<void>(resolve => backend.close(() => resolve())); });
  const relay = await startRelay(`http://127.0.0.1:${(backend.address() as import('node:net').AddressInfo).port}`, repo);
  t.after(() => relay.close());
  const text = 'a'.repeat(70000);
  const body = Buffer.from(JSON.stringify({ query: 'dirty', overlays: [{ path: 'src/decorate.js', text, version: 1, content_hash: hash(text) }] }));
  assert.ok(body.length > 65536);
  assert.equal((await forward(relay.url, '/search', 'POST', body)).status, 200);
  assert.equal(relay.records[0].overlay_input_bindings_verified, true);
  assert.equal((await forward(relay.url, '/search', 'POST', Buffer.from(JSON.stringify({ query: text })))).status, 413);
  assert.equal(calls, 1);
});
