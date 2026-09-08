import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { parseConnectRequest, parseFindRequest, resultIndex } from '../src/commands';
import { defaultOptions } from '../src/schema';

test('programmatic connect requires a named open-workspace candidate and explicit URL', () => {
  assert.equal(parseConnectRequest(undefined), null);
  assert.deepEqual(parseConnectRequest({ workspacePath: '/fixture', serviceUrl: 'http://127.0.0.1:8750' }),
    { workspacePath: '/fixture', serviceUrl: 'http://127.0.0.1:8750' });
  for (const value of [null, [], { workspacePath: 'relative', serviceUrl: 'http://127.0.0.1:8750' },
    { workspacePath: '/fixture', serviceUrl: '' }, { workspacePath: '/fixture', serviceUrl: 'http://127.0.0.1:8750', execute: 'anything' }]) {
    assert.throws(() => parseConnectRequest(value));
  }
});
test('programmatic search uses normal validated defaults and rejects unknown or malformed overrides', () => {
  assert.equal(parseFindRequest(undefined, defaultOptions), null);
  const request = parseFindRequest({ query: 'Trim an inventory label', options: { token_budget: 2000, max_results: 5 } }, defaultOptions);
  assert.equal(request?.query, 'Trim an inventory label');
  assert.deepEqual(request?.options, { ...defaultOptions, token_budget: 2000, max_results: 5 });
  for (const value of [{ query: '' }, { query: 'anything', options: { rerank: 1 } },
    { query: 'anything', options: { model: 'secret' } }, { query: 'anything', options: { max_results: 21 } },
    { query: 'anything', options: [] }, { query: 17 }]) {
    assert.throws(() => parseFindRequest(value, defaultOptions));
  }
});
test('result-index commands refer only to a current bounded result', () => {
  assert.equal(resultIndex(0, 3), 0);
  assert.equal(resultIndex(2, 3), 2);
  for (const value of [-1, 3, 0.5, Infinity, NaN]) { assert.throws(() => resultIndex(value, 3)); }
  assert.throws(() => resultIndex(0, 0));
});
