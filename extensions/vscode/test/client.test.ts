import { test } from 'node:test';
import * as assert from 'node:assert/strict';
import * as http from 'node:http';
import { once } from 'node:events';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import * as path from 'node:path';
import { ServiceClient, MAX_RESPONSE_BYTES, localAddress, ServiceError } from '../src/client';
import { defaultOptions, parseStrictJson, parseResult, parseSearch } from '../src/schema';
import { result, response, source, status } from './fixtures';

async function server(handler: http.RequestListener): Promise<{ url: string; close: () => Promise<void> }> {
  const instance = http.createServer(handler); instance.listen(0, '127.0.0.1'); await once(instance, 'listening');
  const address = instance.address(); assert.ok(address && typeof address === 'object');
  return { url: `http://127.0.0.1:${address.port}`, close: () => new Promise(resolve => { instance.closeAllConnections(); instance.close(() => resolve()); }) };
}
function json(res: http.ServerResponse, body: unknown, code = 200): void { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)); }

test('URL guard rejects credentials, nonloopback, paths, DNS aliases and numeric aliases', () => {
  for (const value of ['https://127.0.0.1:8750', 'http://evil.test', 'http://localhost.evil:8750', 'http://127.0.0.1/a',
    'http://user@127.0.0.1:8750', 'http://localhost:8750?q=x', 'http://localhost:8750#x', 'http://2130706433:8750', 'http://127.1:8750', 'http://127.0.0.1:0']) {
    assert.throws(() => localAddress(value));
  }
  assert.equal(localAddress('http://localhost:8750/').base, 'http://localhost:8750');
});
test('JSON parser rejects duplicate keys, huge depth, overflow, invalid Unicode and non-JSON whitespace', () => {
  for (const body of ['{"x":1,"x":2}', '{"__proto__":1,"__proto__":2}', '1e400', '"\\ud800"', '\u00a0{}', '['.repeat(70) + '0' + ']'.repeat(70)]) {
    assert.throws(() => parseStrictJson(body));
  }
  assert.equal((parseStrictJson('{"__proto__":{"value":1}}') as Record<string, unknown>).__proto__ instanceof Object, false);
});
test('real HTTP requests use expected endpoints/body/Host and accept future metadata', async () => {
  const seen: string[] = [];
  const fixture = await server((req, res) => {
    seen.push(req.url ?? '');
    assert.match(req.headers.host ?? '', /^127\.0\.0\.1:\d+$/);
    let data = ''; req.on('data', chunk => data += chunk); req.on('end', () => {
      if (req.url === '/status') { json(res, { ...status('/repo'), future_scope: 'python' }); }
      else if (req.url === '/index') { assert.equal(data, '{}'); json(res, { indexstats: { total_windows: 2 } }); }
      else if (req.url === '/cancel') { json(res, { request_id: 'request-1', cancelled: true }); }
      else { const body = JSON.parse(data) as Record<string, unknown>; assert.equal(body.request_id, 'request-1');
        json(res, { ...response('/repo'), results: [{ ...result(), future_language: 'python' }] }); }
    });
  });
  try {
    const client = new ServiceClient(fixture.url);
    assert.equal((await client.status()).repo, '/repo');
    assert.equal(await client.refresh(), 2);
    assert.equal((await client.search('behavior', defaultOptions, 'request-1')).results[0]?.source, source);
    assert.equal(await client.cancel('request-1'), true);
    assert.deepEqual(seen, ['/status', '/index', '/search', '/cancel']);
  } finally { await fixture.close(); }
});
test('redirects are never followed and oversized declared or streamed responses are rejected', async () => {
  let followed = 0;
  const fixture = await server((req, res) => {
    if (req.url === '/status') { res.writeHead(302, { Location: '/redirected', 'Content-Type': 'application/json' }); res.end('{}'); }
    else { followed++; json(res, {}); }
  });
  try { await assert.rejects(new ServiceClient(fixture.url).status(), /redirects/); assert.equal(followed, 0); }
  finally { await fixture.close(); }
  for (const declared of [false, true]) {
    const large = await server((_req, res) => {
      res.writeHead(200, { 'Content-Type': 'application/json', ...(declared ? { 'Content-Length': MAX_RESPONSE_BYTES + 1 } : {}) });
      res.end('x'.repeat(MAX_RESPONSE_BYTES + 1));
    });
    try { await assert.rejects(new ServiceClient(large.url).status(), /1 MiB/); } finally { await large.close(); }
  }
});
test('malformed response fields, source bytes/bounds and request echoes fail before display', () => {
  for (const edit of [{ source_hash: '0'.repeat(64) }, { end_line: 1 }, { path: '../outside.py' }, { truncated: 'false' }, { h2o_score: Infinity }]) {
    assert.throws(() => parseResult({ ...result(), ...edit }));
  }
  assert.throws(() => parseSearch({ ...response('/repo'), query: 'other' }, 'behavior', defaultOptions));
  const unicodeSource = 'def implementation():\n    return "\u2028"\n';
  assert.equal(parseResult(result(unicodeSource)).end_line, 2);
});
test('HTTP errors stay actionable, invalid UTF-8 is rejected and cancellation aborts pending sockets', async () => {
  const error = await server((_req, res) => json(res, { error: { code: 'source_snapshot', message: 'Saved Python is invalid' } }, 409));
  try { await assert.rejects(new ServiceClient(error.url).status(), (failure: unknown) => failure instanceof ServiceError && failure.code === 'source_snapshot'); }
  finally { await error.close(); }
  const invalid = await server((_req, res) => { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(Buffer.from([0xff])); });
  try { await assert.rejects(new ServiceClient(invalid.url).status(), /malformed/); } finally { await invalid.close(); }
  const pending = await server(() => undefined);
  try {
    const controller = new AbortController(); const promise = new ServiceClient(pending.url).status(controller.signal);
    controller.abort(); await assert.rejects(promise, /cancelled/);
    await assert.rejects(new ServiceClient(pending.url, 20).status(), /timed out/);
  } finally { await pending.close(); }
});
test('custom native HTTP agent bypasses inherited proxy variables', async () => {
  let proxyCalls = 0;
  const proxy = await server((_req, res) => { proxyCalls++; json(res, {}); });
  const local = await server((_req, res) => json(res, status('/repo')));
  try {
    const compiled = path.resolve(__dirname, '../src/client.js');
    const script = `const {ServiceClient}=require(${JSON.stringify(compiled)});new ServiceClient(${JSON.stringify(local.url)}).status().then(()=>process.stdout.write('DIRECT_OK')).catch(()=>process.exit(1));`;
    const child = await promisify(execFile)(process.execPath, ['-e', script], { timeout: 5000,
      env: { ...process.env, NODE_USE_ENV_PROXY: '1', HTTP_PROXY: proxy.url, http_proxy: proxy.url, NO_PROXY: '', no_proxy: '' } });
    assert.equal(child.stdout, 'DIRECT_OK'); assert.equal(proxyCalls, 0);
  } finally { await proxy.close(); await local.close(); }
});
