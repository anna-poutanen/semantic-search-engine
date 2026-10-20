import { test } from 'node:test';
import * as assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import * as http from 'node:http';
import { once } from 'node:events';
import * as path from 'node:path';
import { ServiceClient } from '../src/client';
import { parseFindRequest } from '../src/commands';
import { defaultOptions, parseSearch } from '../src/schema';
import { SearchConfiguration, searchOptions } from '../src/settings';
import { response, source } from './fixtures';

test('multifile setting and command override reach HTTP and require a matching response mode', async () => {
  const manifest = JSON.parse(await fs.readFile(path.resolve(__dirname, '../../package.json'), 'utf8'));
  const retrieval = manifest.contributes.configuration.properties['semanticSearch.retrieval'];
  assert.ok(retrieval.enum.includes('multifile'));
  assert.equal(retrieval.default, 'dense');
  assert.equal(defaultOptions.retrieval, 'dense');
  const configuration: SearchConfiguration = {
    get<T>(name: string, fallback: T): T { return (name === 'retrieval' ? 'multifile' : fallback) as T; },
  };
  const configured = searchOptions(configuration);
  const overridden = parseFindRequest({ query: 'behavior', options: { retrieval: 'multifile' } }, defaultOptions);
  assert.ok(overridden);
  assert.deepEqual(configured, { ...defaultOptions, retrieval: 'multifile' });
  assert.deepEqual(overridden.options, configured);
  const requests: Record<string, unknown>[] = [];
  const instance = http.createServer((request, result) => {
    assert.equal(request.url, '/search');
    let body = '';
    request.on('data', chunk => body += chunk);
    request.on('end', () => {
      const payload = JSON.parse(body) as Record<string, unknown>;
      requests.push(payload);
      result.writeHead(200, { 'Content-Type': 'application/json' });
      result.end(JSON.stringify({ ...response('/repo'), retrieval: payload.retrieval }));
    });
  });
  instance.listen(0, '127.0.0.1');
  await once(instance, 'listening');
  const address = instance.address();
  assert.ok(address && typeof address === 'object');
  try {
    const received = await new ServiceClient(`http://127.0.0.1:${address.port}`).search(
      overridden.query, overridden.options, 'multifile-roundtrip');
    assert.equal(received.retrieval, 'multifile');
    assert.equal(received.results[0]?.source, source);
    const { rerank_provider: _provider, ...wireOptions } = configured;
    assert.deepEqual(requests, [{ query: 'behavior', ...wireOptions, request_id: 'multifile-roundtrip' }]);
    assert.throws(() => parseSearch({ ...received, retrieval: 'dense' }, 'behavior', configured));
    assert.throws(() => searchOptions({
      get<T>(name: string, fallback: T): T { return (name === 'retrieval' ? 'unknown' : fallback) as T; },
    }));
    assert.equal(configured.rerank, false);
    assert.equal(configured.include_dependencies, defaultOptions.include_dependencies);
  } finally {
    instance.closeAllConnections();
    await new Promise<void>(resolve => instance.close(() => resolve()));
  }
});
