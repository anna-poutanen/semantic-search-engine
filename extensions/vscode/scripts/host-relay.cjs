'use strict';
// Test-only loopback receipts. The shipped extension has no request/source log.
const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const { TextDecoder } = require('node:util');
const { localAddress } = require('../dist/src/client.js');
const { physicalLines, parseStrictJson, relativePath } = require('../dist/src/schema.js');
const { decodeSavedSource, supportedSourcePath } = require('../dist/src/source.js');
const { contained } = require('../dist/src/evidence.js');
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const MAX = 1024 * 1024;
async function snapshot(repo) {
  const files = {}, entries = (await fs.readdir(path.join(repo, 'src'))).sort();
  for (const entry of entries) {
    const filename = 'src/' + entry;
    if (!supportedSourcePath(filename) || !(await fs.lstat(path.join(repo, filename))).isFile()) { continue; }
    const bytes = await fs.readFile(path.join(repo, filename));
    files[filename] = { source: decodeSavedSource(filename, bytes), sha256: sha(bytes) };
  }
  const inventory = files['src/inventory.py'];
  if (!inventory) { throw new Error('Expected inventory fixture source'); }
  return { source: inventory.source, sha256: inventory.sha256, files, entries,
    source_manifest_sha256: sha(JSON.stringify({ entries, files: Object.fromEntries(Object.entries(files).map(([filename, data]) => [filename, data.sha256])) })) };
}
function overlayBindings(input, response) {
  const entries = input?.overlays ?? [], byPath = new Map(); let bytes = 0;
  if (!Array.isArray(entries) || entries.length > 16) { return { valid: false, byPath }; }
  for (const entry of entries) {
    try {
      if (!entry || Object.keys(entry).sort().join(',') !== 'content_hash,path,text,version') { return { valid: false, byPath }; }
      relativePath(entry.path);
      const size = Buffer.byteLength(entry.text, 'utf8'); bytes += size;
      if (typeof entry.text !== 'string' || Buffer.byteLength(entry.path) > 512 || size > 256 * 1024 || bytes > 1024 * 1024
          || !Number.isSafeInteger(entry.version) || entry.version < 0
          || sha(entry.text) !== entry.content_hash || byPath.has(entry.path)) { return { valid: false, byPath }; }
      byPath.set(entry.path, entry);
    } catch { return { valid: false, byPath }; }
  }
  if (!entries.length) { return { valid: response?.scope?.source_state !== 'overlay', byPath }; }
  const returned = response?.scope?.overlays;
  if (response?.scope?.source_state !== 'overlay' || !Array.isArray(returned) || returned.length !== entries.length) { return { valid: false, byPath }; }
  const seen = new Set();
  for (const binding of returned) {
    const entry = byPath.get(binding?.path);
    if (!entry || seen.has(binding.path) || Object.keys(binding).sort().join(',') !== 'content_hash,path,version'
        || binding.version !== entry.version || binding.content_hash !== entry.content_hash) { return { valid: false, byPath }; }
    seen.add(binding.path);
  }
  return { valid: true, byPath };
}
async function sourceChecks(repo, response, before, after, input = null) {
  if (!response || !Array.isArray(response.results)) { return []; }
  repo = await fs.realpath(repo);
  const checks = [], overlays = overlayBindings(input, response);
  for (const row of response.results) {
    let valid = false;
    try {
      relativePath(row.path);
      const target = await fs.realpath(path.resolve(repo, row.path));
      const expected = await fs.realpath(path.join(repo, row.path));
      const saved = after.files[row.path], previous = before.files[row.path];
      const overlay = overlays.byPath.get(row.path);
      const lines = physicalLines(overlay ? overlay.text : saved.source);
      const excerpt = lines.slice(row.start_line - 1, row.end_line).join('');
      const window = lines.slice(row.start_line - 1, row.window_end_line).join('');
      const origin = overlay ? row.source_origin === 'overlay' && row.buffer_version === overlay.version && row.buffer_hash === overlay.content_hash
        : !overlays.byPath.size || row.source_origin === 'saved';
      valid = overlays.valid && origin && target === expected && contained(repo, target) && contained(path.join(repo, 'src'), target)
        && previous.sha256 === saved.sha256 && before.source_manifest_sha256 === after.source_manifest_sha256
        && Number.isSafeInteger(row.start_line) && row.start_line >= 1
        && Number.isSafeInteger(row.end_line) && row.end_line >= row.start_line
        && Number.isSafeInteger(row.window_end_line) && row.window_end_line >= row.end_line && row.window_end_line <= lines.length
        && row.source === excerpt && sha(excerpt) === row.source_hash && sha(window) === row.content_hash;
    } catch { /* A failed proof is recorded, never silently dropped. */ }
    checks.push({ path: row.path, symbol: row.symbol, start_line: row.start_line, end_line: row.end_line,
      source_hash: row.source_hash, source_origin: overlays.byPath.has(row.path) ? 'overlay' : 'saved',
      exact_source: valid, exact_saved_source: valid && !overlays.byPath.has(row.path) });
  }
  return checks;
}
function forward(url, endpoint, method, body, { signal } = {}) {
  const address = localAddress(url);
  return new Promise((resolve, reject) => {
    const agent = new http.Agent({ keepAlive: false });
    const request = http.request({ hostname: '127.0.0.1', port: address.port, path: endpoint, method, agent, signal,
      headers: { Host: `${address.hostname}:${address.port}`, Accept: 'application/json',
        ...(body.length ? { 'Content-Type': 'application/json', 'Content-Length': body.length } : {}) } }, response => {
      const chunks = []; let size = 0;
      response.on('data', chunk => { size += chunk.length; if (size > MAX) { response.destroy(new Error('Response cap')); } else { chunks.push(chunk); } });
      response.on('error', error => { agent.destroy(); reject(error); });
      response.on('end', () => {
        agent.destroy();
        const status = response.statusCode ?? 0;
        const contentType = response.headers['content-type'] ?? '';
        if (status >= 300 && status < 400 || !/^application\/json(?:\s*;|$)/i.test(contentType)) { reject(new Error('Unexpected backend transport response')); return; }
        resolve({ status, body: Buffer.concat(chunks), contentType });
      });
    });
    request.on('error', error => { agent.destroy(); reject(error); });
    request.setTimeout(180000, () => request.destroy(new Error('Backend transport timeout')));
    request.end(body);
  });
}
async function startRelay(url, repo, options = {}) {
  localAddress(url);
  repo = await fs.realpath(repo);
  const records = [], startedSearches = [], cancellationWaiters = new Map(), cancelled = new Set(); let port;
  // Only this relay's handlers/connections are owned. Closing gives existing
  // work a short receipt drain, then records unfinished work before aborting it.
  const closeGraceMs = options.closeGraceMs ?? 500;
  if (!Number.isSafeInteger(closeGraceMs) || closeGraceMs < 0 || closeGraceMs > 5000) { throw new Error('Invalid relay close grace'); }
  const pending = new Set(); let closing = false, closePromise;
  const bounded = async (promises, milliseconds) => {
    let timer;
    try { await Promise.race([Promise.allSettled(promises), new Promise(resolve => { timer = setTimeout(resolve, milliseconds); })]); }
    finally { clearTimeout(timer); }
  };
  const unfinished = (state, message) => ({ endpoint: state.endpoint, method: state.method, started_utc: state.started_utc,
    finished_utc: new Date().toISOString(), incomplete: true, closure_stage: state.stage,
    request: state.input, request_sha256: state.requestHash, request_body_complete: state.requestHash !== null,
    request_bytes_received: state.requestBytes, received_prefix_sha256: sha(Buffer.concat(state.requestChunks)),
    backend_finished_utc: state.backendFinished ?? null, backend_status: state.backendStatus ?? null,
    before_source_sha256: state.before?.sha256 ?? null,
    before_source_manifest_sha256: state.before?.source_manifest_sha256 ?? null, transport_error: message });
  const releaseHeldResponses = () => {
    for (const state of pending) {
      const release = cancellationWaiters.get(state.input?.request_id);
      if (release && !cancelled.has(state.input?.request_id)) { state.holdReleasedByClose = true; release(); }
    }
  };
  const server = http.createServer((request, response) => {
    const reject = (code, message) => { response.writeHead(code, { 'Content-Type': 'application/json' }); response.end(JSON.stringify({ error: { code: 'test_relay', message } })); };
    if (closing) { reject(503, 'Receipt relay is closing'); return; }
    if (request.headers.host !== `127.0.0.1:${port}` || request.headers.origin || request.headers['sec-fetch-site']) { reject(403, 'Test relay rejects browser/foreign hosts'); return; }
    if (!['/status', '/index', '/search', '/cancel'].includes(request.url) || !['GET', 'POST'].includes(request.method)) { reject(404, 'Unknown endpoint'); return; }
    const started = new Date().toISOString();
    const state = { endpoint: request.url, method: request.method, started_utc: started,
      stage: 'reading_request', controller: new AbortController(), settled: false, input: null, requestHash: null,
      requestBytes: 0, requestChunks: [] };
    const settle = record => { if (!state.settled) { state.settled = true; records.push(record); } };
    state.promise = (async () => {
    try {
      const chunks = []; let size = 0;
      for await (const chunk of request) { size += chunk.length; if (size > (request.url === '/search' ? 2 * 1024 * 1024 : 65536)) { reject(413, 'Request cap'); return; } chunks.push(chunk); state.requestBytes = size; state.requestChunks = chunks; }
      const body = Buffer.concat(chunks);
      const input = body.length ? parseStrictJson(new TextDecoder('utf-8', { fatal: true }).decode(body)) : null;
      state.input = input; state.requestHash = sha(body); state.stage = 'request_parsed';
      state.controller.signal.throwIfAborted();
      if (size > 65536 && !input?.overlays?.length) { reject(413, 'Saved-source request cap'); return; }
      const hold = request.url === '/search' && input?.query === options.holdQuery && !cancelled.has(input?.request_id)
        ? new Promise(resolve => { cancellationWaiters.set(input?.request_id, resolve); }) : null;
      if (request.url === '/search') {
        startedSearches.push({ request_id: input?.request_id, query_sha256: sha(input?.query ?? ''), started_utc: started });
        state.stage = 'recording_search_start';
        if (options.onSearchStart) { await options.onSearchStart(startedSearches); }
      }
      if (request.url === '/cancel') { cancelled.add(input?.request_id); cancellationWaiters.get(input?.request_id)?.(); }
      state.controller.signal.throwIfAborted(); state.stage = 'snapshot_before';
      const before = request.url === '/search' ? await snapshot(repo) : null;
      state.before = before; state.controller.signal.throwIfAborted(); state.stage = 'forwarding_backend';
      const result = await forward(url, request.url, request.method, body, { signal: state.controller.signal });
      const backendFinished = new Date().toISOString();
      state.backendFinished = backendFinished; state.backendStatus = result.status;
      state.controller.signal.throwIfAborted();
      if (hold) {
        state.stage = 'holding_for_cancel';
        let holdTimer;
        try { await Promise.race([hold, new Promise(resolve => { holdTimer = setTimeout(resolve, 10000); })]); }
        finally { clearTimeout(holdTimer); cancellationWaiters.delete(input?.request_id); }
      }
      state.controller.signal.throwIfAborted(); state.stage = 'verifying_response_source';
      const parsed = parseStrictJson(new TextDecoder('utf-8', { fatal: true }).decode(result.body));
      const after = before ? await snapshot(repo) : null;
      settle({ endpoint: request.url, method: request.method, started_utc: started, finished_utc: new Date().toISOString(),
        backend_finished_utc: backendFinished, response_held_for_cancel_test: !!hold,
        ...(state.holdReleasedByClose ? { hold_released_by_close: true } : {}),
        status: result.status, request: input, request_sha256: sha(body), response_sha256: sha(result.body),
        response: parsed, before_source_sha256: before?.sha256 ?? null, after_source_sha256: after?.sha256 ?? null,
        before_source_manifest_sha256: before?.source_manifest_sha256 ?? null,
        after_source_manifest_sha256: after?.source_manifest_sha256 ?? null,
        overlay_input_bindings_verified: request.url === '/search' && result.status === 200 ? overlayBindings(input, parsed).valid : null,
        returned_source_checks: before ? await sourceChecks(repo, parsed, before, after, input) : [] });
      if (!response.destroyed) { response.writeHead(result.status, { 'Content-Type': result.contentType }); response.end(result.body); }
    } catch (error) {
      settle(closing ? unfinished(state, error.message) : { endpoint: request.url, method: request.method, started_utc: started,
        finished_utc: new Date().toISOString(), transport_error: error.message });
      if (!response.destroyed && !response.headersSent) { reject(502, 'Test relay backend error'); }
      else { response.destroy(); }
    }
    })();
    pending.add(state);
    void state.promise.finally(() => pending.delete(state));
  });
  server.on('clientError', (_, socket) => socket.destroy());
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  port = server.address().port;
  return { url: `http://127.0.0.1:${port}`, records, server,
    close: () => closePromise ??= (async () => {
      closing = true;
      const serverClosed = new Promise(resolve => server.close(resolve));
      server.closeAllConnections();
      releaseHeldResponses();
      const initial = [...pending];
      await bounded(initial.map(state => state.promise), closeGraceMs);
      let incomplete = 0;
      for (const state of pending) {
        if (state.settled) { continue; }
        // Freeze this row first: a later handler completion cannot append or
        // replace evidence after the caller publishes records and removes files.
        state.settled = true; incomplete++;
        records.push(unfinished(state, 'Receipt relay closed before handler completed'));
        state.controller.abort(new Error('Receipt relay closed before handler completed'));
      }
      releaseHeldResponses();
      await bounded([...pending].map(state => state.promise), 100);
      await serverClosed;
      return { close_grace_ms: closeGraceMs, handlers_at_close: initial.length,
        unfinished_receipts: records.filter(record => record.incomplete).length, forced_incomplete_receipts: incomplete,
        records_stable: [...pending].every(state => state.settled) };
    })() };
}
module.exports = { startRelay, snapshot, sourceChecks, overlayBindings, forward };
