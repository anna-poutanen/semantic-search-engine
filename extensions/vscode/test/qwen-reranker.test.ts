import { test } from 'node:test';
import * as assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import * as http from 'node:http';
import { once } from 'node:events';
import { ServiceClient, ServiceError } from '../src/client';
import { defaultOptions, hash, parseResult, parseSearch, SearchOptions, SearchResponse, validateOptions } from '../src/schema';
import { SearchConfiguration, searchOptions } from '../src/settings';
import { parseFindRequest } from '../src/commands';
import { BufferOverride } from '../src/overlays';
import { response, source } from './fixtures';

const qwen: SearchOptions = { ...defaultOptions, retrieval: 'hybrid', rerank: true, rerank_provider: 'qwen', candidates: 24 };
const configuration = (values: Record<string, unknown> = {}): SearchConfiguration => ({
  get<T>(name: string, fallback: T): T { return (Object.hasOwn(values, name) ? values[name] : fallback) as T; },
});
function qwenResponse(): SearchResponse {
  return { ...response('/repo'), retrieval: 'hybrid', rerank: true, rerank_provider: 'qwen',
    results: [{ ...response('/repo').results[0]!, qwen_score: -1.25 }] };
}
async function service(reply: (payload: Record<string, unknown>) => { body: unknown; status?: number }) {
  const requests: Record<string, unknown>[] = [];
  const instance = http.createServer((request, result) => {
    assert.equal(request.url, '/search');
    let data = ''; request.on('data', chunk => data += chunk);
    request.on('end', () => {
      const payload = JSON.parse(data) as Record<string, unknown>; requests.push(payload);
      const response = reply(payload);
      result.writeHead(response.status ?? 200, { 'Content-Type': 'application/json' }); result.end(JSON.stringify(response.body));
    });
  });
  instance.listen(0, '127.0.0.1'); await once(instance, 'listening');
  const address = instance.address(); assert.ok(address && typeof address === 'object');
  return { client: new ServiceClient(`http://127.0.0.1:${address.port}`), requests,
    close: () => new Promise<void>(resolve => { instance.closeAllConnections(); instance.close(() => resolve()); }) };
}

test('workspace setting defaults match the manifest and preserve fast dense/implicit H2O behavior', async () => {
  const manifest = JSON.parse(await fs.readFile(path.resolve(__dirname, '../../package.json'), 'utf8'));
  const properties = manifest.contributes.configuration.properties;
  const values = Object.fromEntries(Object.entries(properties).map(([name, value]: [string, any]) => [name.replace('semanticSearch.', ''), value.default]));
  assert.deepEqual(searchOptions(configuration()), defaultOptions);
  assert.deepEqual(searchOptions(configuration(values)), defaultOptions);
  assert.equal(defaultOptions.retrieval, 'dense'); assert.equal(defaultOptions.rerank, false);
  assert.equal(properties['semanticSearch.rerankProvider'].scope, 'resource');
  assert.deepEqual(properties['semanticSearch.rerankProvider'].enum, ['h2o', 'qwen']);
  assert.match(properties['semanticSearch.rerankProvider'].enumDescriptions[1], /experimental/);
});
test('Qwen is enabled explicitly and its selected shortlist does not change legacy defaults', () => {
  assert.deepEqual(searchOptions(configuration({ rerankProvider: 'qwen' })), { ...defaultOptions, rerank_provider: 'qwen' });
  assert.deepEqual(searchOptions(configuration({ retrieval: 'hybrid', rerank: true, rerankProvider: 'qwen', candidates: 24 })), qwen);
  assert.equal(searchOptions(configuration({ rerank: true })).rerank_provider, 'h2o');
  assert.equal(searchOptions(configuration({ rerankProvider: 'qwen' })).candidates, 4);
});
test('invalid provider settings and command overrides fail before any HTTP request', async () => {
  for (const provider of ['unknown', '', true, 12, null]) {
    assert.throws(() => searchOptions(configuration({ rerankProvider: provider })), /provider/);
    assert.throws(() => validateOptions({ ...defaultOptions, rerank_provider: provider } as SearchOptions), /provider/);
    assert.throws(() => parseFindRequest({ query: 'behavior', options: { rerank_provider: provider } }, defaultOptions), /provider/);
  }
  const fixture = await service(() => ({ body: response('/repo') }));
  try {
    await assert.rejects(fixture.client.search('behavior', { ...qwen, rerank_provider: 'unknown' } as unknown as SearchOptions, 'invalid'), /provider/);
    assert.equal(fixture.requests.length, 0);
  } finally { await fixture.close(); }
});
test('programmatic saved and dirty searches can override provider without changing their source scope', () => {
  const { rerank_provider: _provider, ...legacy } = defaultOptions;
  for (const includeUnsaved of [false, true]) {
    const parsed = parseFindRequest({ query: 'behavior', includeUnsaved, options: { retrieval: 'hybrid', rerank: true, rerank_provider: 'qwen', candidates: 24 } }, legacy);
    assert.deepEqual(parsed?.options, qwen); assert.equal(parsed?.includeUnsaved, includeUnsaved);
  }
  assert.throws(() => parseFindRequest({ query: 'behavior', options: { qwen_reranker_python: '/fake/python' } }, defaultOptions), /Unknown/);
});
test('fast and H2O HTTP searches retain their old wire shape even when Qwen is selected but disabled', async () => {
  const fixture = await service(payload => ({ body: { ...response('/repo'), rerank: payload.rerank } }));
  try {
    for (const options of [defaultOptions, { ...defaultOptions, rerank: true }, { ...defaultOptions, rerank_provider: 'qwen' as const }]) {
      await fixture.client.search('behavior', options, 'legacy');
      const sent = fixture.requests.at(-1)!; assert.equal(Object.hasOwn(sent, 'rerank_provider'), false);
      const { rerank_provider: _provider, ...legacy } = options;
      assert.deepEqual(sent, { query: 'behavior', ...legacy, request_id: 'legacy' });
    }
  } finally { await fixture.close(); }
});
test('enabled Qwen saved search sends the provider and retains its finite score and response identity', async () => {
  const fixture = await service(() => ({ body: qwenResponse() }));
  try {
    const result = await fixture.client.search('behavior', qwen, 'qwen-saved');
    assert.deepEqual(fixture.requests, [{ query: 'behavior', ...qwen, request_id: 'qwen-saved' }]);
    assert.equal(result.rerank_provider, 'qwen'); assert.equal(result.results[0]?.qwen_score, -1.25);
    assert.equal(result.results[0]?.source, source);
  } finally { await fixture.close(); }
});
test('enabled Qwen dirty search sends literal overrides and checks both provider and buffer bindings', async () => {
  const text = source.replace('ready', 'unsaved 😀');
  const buffer: BufferOverride = { path: 'src/app.py', text, version: 3, content_hash: hash(text) };
  const body: SearchResponse = { ...qwenResponse(), total_source_chars: [...text].length,
    scope: { source_state: 'overlay', saved_source_manifest: 'a'.repeat(64), overlays: [{ path: buffer.path, version: buffer.version, content_hash: buffer.content_hash }] },
    results: [{ ...qwenResponse().results[0]!, source: text, source_hash: hash(text), content_hash: hash(text),
      source_origin: 'overlay', buffer_version: buffer.version, buffer_hash: buffer.content_hash }] };
  const fixture = await service(() => ({ body }));
  try {
    const result = await fixture.client.search('behavior', qwen, 'qwen-dirty', undefined, [buffer]);
    assert.deepEqual(fixture.requests[0], { query: 'behavior', ...qwen, request_id: 'qwen-dirty', overlays: [buffer] });
    assert.equal(result.results[0]?.source, text); assert.equal(result.scope?.overlays[0]?.version, 3);
    assert.throws(() => parseSearch({ ...body, rerank_provider: 'h2o' }, 'behavior', qwen, [buffer]), /provider/);
    const wrongBuffer = structuredClone(body); wrongBuffer.results[0]!.buffer_version = 4;
    assert.throws(() => parseSearch(wrongBuffer, 'behavior', qwen, [buffer]), /origin/);
  } finally { await fixture.close(); }
});
test('Qwen response cannot omit or substitute its enabled provider and H2O stays backward compatible', () => {
  for (const provider of [undefined, 'h2o', 'other', null]) {
    const body = { ...qwenResponse(), rerank_provider: provider };
    assert.throws(() => parseSearch(body, 'behavior', qwen), /provider/);
  }
  const h2o = { ...defaultOptions, rerank: true };
  assert.equal(parseSearch({ ...response('/repo'), rerank: true }, 'behavior', h2o).rerank, true);
  assert.equal(parseSearch({ ...response('/repo'), rerank: true, rerank_provider: 'h2o' }, 'behavior', h2o).rerank_provider, 'h2o');
  assert.throws(() => parseSearch({ ...response('/repo'), rerank: true, rerank_provider: 'qwen' }, 'behavior', h2o), /provider/);
  assert.equal(parseSearch({ ...response('/repo'), rerank_provider: 'h2o' }, 'behavior', { ...defaultOptions, rerank_provider: 'qwen' }).rerank, false);
});
test('optional Qwen scores accept null/finite signed values and reject malformed scalars', () => {
  for (const value of [null, -9.5, 0, 8.75]) { assert.equal(parseResult({ ...response('/repo').results[0], qwen_score: value }).qwen_score, value); }
  for (const value of [Infinity, -Infinity, NaN, '1.2', true]) { assert.throws(() => parseResult({ ...response('/repo').results[0], qwen_score: value })); }
});
test('unconfigured Qwen tells the user how to enable it or return to fast retrieval', async () => {
  const original = 'Qwen reranking is not configured; start an explicitly owned local worker';
  const fixture = await service(() => ({ status: 400, body: { error: { code: 'invalid_request', message: original } } }));
  try {
    await assert.rejects(fixture.client.search('behavior', qwen, 'missing-qwen'), (error: unknown) => {
      assert.ok(error instanceof ServiceError); assert.equal(error.code, 'invalid_request'); assert.equal(error.status, 400);
      assert.match(error.message, /--enable-qwen-reranker/); assert.match(error.message, /semanticSearch.rerank/); return true;
    });
    await assert.rejects(fixture.client.search('behavior', defaultOptions, 'normal-error'), (error: unknown) => error instanceof ServiceError && error.message === original);
  } finally { await fixture.close(); }
});
test('an older service is actionable and unrelated backend/cancellation errors retain their identity', async () => {
  for (const error of [{ code: 'invalid_request', message: 'Unknown search option', status: 400 },
    { code: 'invalid_request', message: 'query must contain valid Unicode text', status: 400 },
    { code: 'backend_error', message: 'Native runtime failed', status: 503 },
    { code: 'request_cancelled', message: 'Search cancelled', status: 409 }]) {
    const fixture = await service(() => ({ status: error.status, body: { error: { code: error.code, message: error.message } } }));
    try {
      await assert.rejects(fixture.client.search('behavior', qwen, 'service-error'), (failure: unknown) => {
        assert.ok(failure instanceof ServiceError); assert.equal(failure.code, error.code); assert.equal(failure.status, error.status);
        if (error.message === 'Unknown search option') { assert.match(failure.message, /Update it/); assert.match(failure.message, /fast search/); }
        else { assert.equal(failure.message, error.message); }
        return true;
      });
    } finally { await fixture.close(); }
  }
});
