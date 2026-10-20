import { createHash } from 'node:crypto';
import { supportedSourcePath } from './source';
import type { BufferOverride, BufferBinding } from './overlays';

export class ValidationError extends Error {}
export type Retrieval = 'dense' | 'bm25' | 'hybrid' | 'multifile';
export type RerankProvider = 'h2o' | 'qwen';
export interface SearchOptions {
  retrieval: Retrieval; rerank: boolean; candidates: number; max_results: number;
  source_budget: number; token_budget: number; include_dependencies: boolean;
  rerank_provider?: RerankProvider;
}
export const defaultOptions: SearchOptions = {
  retrieval: 'dense', rerank: false, candidates: 4, max_results: 8,
  source_budget: 12000, token_budget: 4096, include_dependencies: true,
  rerank_provider: 'h2o',
};
export interface Result {
  id: string; path: string; symbol: string; start_line: number; end_line: number;
  window_end_line: number; source: string; content_hash: string; source_hash: string;
  retrieval_score: number | null; h2o_score: number | null; truncated: boolean;
  qwen_score?: number | null;
  origin: 'retrieval' | 'dependency'; related_to: string | null;
  source_origin?: 'saved' | 'overlay'; buffer_version?: number; buffer_hash?: string;
}
export interface Status {
  repo: string; source_root: string; closed: boolean; ready: boolean; windows: number;
  last_error: string | null; source_manifest: string | null;
  watcher: { running: boolean; pending: boolean; last_error: string | null } | null;
  source_exclusions?: string[];
}
export interface SearchResponse {
  repo: string; source_root: string; query: string; retrieval: Retrieval; rerank: boolean;
  rerank_provider?: RerankProvider;
  results: Result[]; match_status: 'candidates' | 'no_candidates' | 'insufficient_evidence';
  source_budget: number; total_source_chars: number; budget_exhausted: boolean;
  token_usage: { encoding: 'cl100k_base'; tokens: number; budget: number; truncated: boolean };
  indexstats: { total_windows: number; source_manifest: string };
  scope?: { source_state: 'overlay'; saved_source_manifest: string; overlays: BufferBinding[] };
}

export function unicode(value: string): boolean {
  for (let i = 0; i < value.length; i++) {
    const unit = value.charCodeAt(i);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(++i);
      if (!(next >= 0xdc00 && next <= 0xdfff)) { return false; }
    } else if (unit >= 0xdc00 && unit <= 0xdfff) { return false; }
  }
  return true;
}
export function hash(text: string): string { return createHash('sha256').update(text, 'utf8').digest('hex'); }
export function physicalLines(text: string): string[] { return (text.match(/[^\r\n]*(?:\r\n|\r|\n|$)/g) ?? []).filter(Boolean); }
export function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) { throw new ValidationError('Expected a JSON object'); }
  return value as Record<string, unknown>;
}
export function string(value: unknown, max = 1000000): string {
  if (typeof value !== 'string' || value.length > max || !unicode(value)) { throw new ValidationError('Invalid service text'); }
  return value;
}
export function integer(value: unknown, min: number, max = Number.MAX_SAFE_INTEGER): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < min || value > max) { throw new ValidationError('Invalid integer'); }
  return value;
}
export function bool(value: unknown): boolean {
  if (typeof value !== 'boolean') { throw new ValidationError('Invalid boolean'); }
  return value;
}
function nullableText(value: unknown): string | null { return value === null ? null : string(value); }
function sha(value: unknown): string {
  const result = string(value, 64);
  if (!/^[a-f0-9]{64}$/.test(result)) { throw new ValidationError('Invalid source hash'); }
  return result;
}
function score(value: unknown): number | null {
  if (value === null) { return null; }
  if (typeof value !== 'number' || !Number.isFinite(value)) { throw new ValidationError('Invalid retrieval score'); }
  return value;
}
export function relativePath(value: unknown, allowRoot = false): string {
  const result = string(value, 4096);
  if (allowRoot && result === '.') { return result; }
  if (!result || /[\\:\x00-\x1f\x7f]/.test(result) || result.startsWith('/')
      || result.split('/').some(part => part === '' || part === '.' || part === '..')) {
    throw new ValidationError('Service result path must stay inside the repository');
  }
  return result;
}
export function validateOptions(options: SearchOptions): SearchOptions {
  if (!['dense', 'bm25', 'hybrid', 'multifile'].includes(options.retrieval)) { throw new ValidationError('Invalid retrieval mode'); }
  bool(options.rerank); bool(options.include_dependencies);
  if (options.rerank_provider !== undefined && !['h2o', 'qwen'].includes(options.rerank_provider)) {
    throw new ValidationError('Choose H2O or Qwen as the optional reranking provider');
  }
  integer(options.candidates, 1, 50); integer(options.max_results, 1, 20);
  integer(options.source_budget, 1, 100000); integer(options.token_budget, 512, 32000);
  return options;
}
export function validateQuery(query: string): string {
  if (!unicode(query) || !query.trim() || [...query].length > 8192) { throw new ValidationError('Enter a behavior of 1–8192 Unicode characters'); }
  return query;
}
export function parseStatus(value: unknown): Status {
  const row = object(value);
  let watcher: Status['watcher'] = null;
  if (row.watcher !== null) {
    const w = object(row.watcher);
    watcher = { running: bool(w.running), pending: bool(w.pending), last_error: nullableText(w.last_error) };
  }
  if (row.source_exclusions !== undefined && (!Array.isArray(row.source_exclusions) || row.source_exclusions.length > 1024)) {
    throw new ValidationError('Invalid source exclusions');
  }
  return { ...(row.source_exclusions === undefined ? {} : { source_exclusions: (row.source_exclusions as unknown[]).map(value => string(value, 4096)) }),
    repo: string(row.repo, 4096), source_root: relativePath(row.source_root, true),
    closed: bool(row.closed), ready: bool(row.ready), windows: integer(row.windows, 0),
    last_error: nullableText(row.last_error), source_manifest: row.source_manifest === null ? null : sha(row.source_manifest), watcher };
}
export function parseResult(value: unknown): Result {
  const row = object(value);
  const result: Result = { id: string(row.id, 8192), path: relativePath(row.path), symbol: string(row.symbol, 4096),
    start_line: integer(row.start_line, 1), end_line: integer(row.end_line, 1), window_end_line: integer(row.window_end_line, 1),
    source: string(row.source), content_hash: sha(row.content_hash), source_hash: sha(row.source_hash),
    retrieval_score: score(row.retrieval_score), h2o_score: score(row.h2o_score), truncated: bool(row.truncated),
    origin: row.origin === 'retrieval' ? 'retrieval' : row.origin === 'dependency' ? 'dependency' : (() => { throw new ValidationError('Invalid context origin'); })(),
    related_to: nullableText(row.related_to) };
  if (row.qwen_score !== undefined) { result.qwen_score = score(row.qwen_score); }
  if (row.source_origin !== undefined) {
    if (row.source_origin !== 'saved' && row.source_origin !== 'overlay') { throw new ValidationError('Invalid source origin'); }
    result.source_origin = row.source_origin;
  }
  if (row.buffer_version !== undefined) { result.buffer_version = integer(row.buffer_version, 0); }
  if (row.buffer_hash !== undefined) { result.buffer_hash = sha(row.buffer_hash); }
  if (!supportedSourcePath(result.path) || !result.id || !result.symbol || !result.source
      || result.end_line < result.start_line || result.window_end_line < result.end_line
      || result.end_line !== result.start_line + physicalLines(result.source).length - 1
      || hash(result.source) !== result.source_hash
      || (!result.truncated && (result.window_end_line !== result.end_line || result.content_hash !== result.source_hash))) {
    throw new ValidationError('Service returned inconsistent saved source evidence');
  }
  return result;
}
export function validateResponseBindings(response: SearchResponse, overrides: readonly BufferOverride[] = []): void {
  if (!overrides.length) {
    if (response.scope || response.results.some(result => result.source_origin === 'overlay' || result.buffer_version !== undefined || result.buffer_hash !== undefined)) {
      throw new ValidationError('Unexpected overlay response to a saved search');
    }
    return;
  }
  const expected = new Map(overrides.map(item => [item.path, item]));
  if (!response.scope || response.scope.overlays.length !== expected.size) { throw new ValidationError('Service omitted dirty buffer bindings'); }
  const seen = new Set<string>();
  for (const item of response.scope.overlays) {
    const sent = expected.get(item.path);
    if (!sent || seen.has(item.path) || sent.version !== item.version || sent.content_hash !== item.content_hash) {
      throw new ValidationError('Service returned a different dirty buffer version or hash');
    }
    seen.add(item.path);
  }
  for (const result of response.results) {
    const sent = expected.get(result.path);
    if (sent ? result.source_origin !== 'overlay' || result.buffer_version !== sent.version || result.buffer_hash !== sent.content_hash
      : result.source_origin !== 'saved' || result.buffer_version !== undefined || result.buffer_hash !== undefined) {
      throw new ValidationError('Service result origin differs from requested buffer scope');
    }
  }
}
export function parseSearch(value: unknown, query: string, options: SearchOptions, overrides: readonly BufferOverride[] = []): SearchResponse {
  const row = object(value);
  const requestedProvider = options.rerank_provider ?? 'h2o';
  if (row.rerank_provider !== undefined && row.rerank_provider !== 'h2o' && row.rerank_provider !== 'qwen') {
    throw new ValidationError('Invalid service reranking provider');
  }
  // Older services omit the implicit H2O provider. An enabled Qwen request must
  // explicitly echo Qwen so a service cannot silently deliver another scorer.
  if (options.rerank && (requestedProvider === 'qwen' ? row.rerank_provider !== 'qwen'
    : row.rerank_provider !== undefined && row.rerank_provider !== 'h2o')) {
    throw new ValidationError('Service response differs from the requested reranking provider');
  }
  if (!Array.isArray(row.results) || row.results.length > options.max_results) { throw new ValidationError('Invalid result count'); }
  const results = row.results.map(parseResult);
  const token = object(row.token_usage), index = object(row.indexstats);
  if (row.query !== query || row.retrieval !== options.retrieval || row.rerank !== options.rerank
      || row.source_budget !== options.source_budget || token.encoding !== 'cl100k_base'
      || token.budget !== options.token_budget || !['candidates', 'no_candidates', 'insufficient_evidence'].includes(String(row.match_status))) {
    throw new ValidationError('Service response differs from requested search settings');
  }
  const total = integer(row.total_source_chars, 0, options.source_budget);
  if (total !== results.reduce((sum, result) => sum + [...result.source].length, 0)) { throw new ValidationError('Invalid source budget accounting'); }
  let scope: SearchResponse['scope'];
  if (row.scope !== undefined && object(row.scope).source_state === 'overlay') {
    const source = object(row.scope);
    if (!Array.isArray(source.overlays) || source.overlays.length > 16) { throw new ValidationError('Invalid dirty buffer bindings'); }
    scope = { source_state: 'overlay', saved_source_manifest: sha(source.saved_source_manifest), overlays: source.overlays.map(value => {
      const buffer = object(value); return { path: relativePath(buffer.path), version: integer(buffer.version, 0), content_hash: sha(buffer.content_hash) };
    }) };
  }
  const response: SearchResponse = { ...(scope ? { scope } : {}),
    ...(row.rerank_provider === undefined ? {} : { rerank_provider: row.rerank_provider as RerankProvider }),
    repo: string(row.repo, 4096), source_root: relativePath(row.source_root, true), query,
    retrieval: options.retrieval, rerank: options.rerank, results, match_status: row.match_status as SearchResponse['match_status'],
    source_budget: options.source_budget, total_source_chars: total, budget_exhausted: bool(row.budget_exhausted),
    token_usage: { encoding: 'cl100k_base', tokens: integer(token.tokens, 0, options.token_budget), budget: options.token_budget, truncated: bool(token.truncated) },
    indexstats: { total_windows: integer(index.total_windows, 0), source_manifest: sha(index.source_manifest) } };
  validateResponseBindings(response, overrides); return response;
}

/** Reject duplicate keys, nonfinite numbers, invalid Unicode and excessive depth. */
export function parseStrictJson(text: string): unknown {
  let cursor = 0;
  const whitespace = () => { while (/[ \t\r\n]/.test(text[cursor] ?? '') && cursor < text.length) { cursor++; } };
  const quoted = (): string => {
    const start = cursor++;
    while (cursor < text.length) {
      const char = text[cursor++];
      if (char === '\\') { cursor++; }
      else if (char === '"') { const result: unknown = JSON.parse(text.slice(start, cursor)); return string(result); }
    }
    throw new ValidationError('Invalid service JSON string');
  };
  const value = (depth: number): unknown => {
    if (depth > 64) { throw new ValidationError('Service JSON nesting exceeds limit'); }
    whitespace(); const char = text[cursor];
    if (char === '"') { return quoted(); }
    if (char === '{') {
      cursor++; whitespace(); const row: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
      if (text[cursor] === '}') { cursor++; return row; }
      for (;;) {
        whitespace(); if (text[cursor] !== '"') { throw new ValidationError('Invalid service JSON object'); }
        const key = quoted(); whitespace();
        if (Object.hasOwn(row, key) || text[cursor++] !== ':') { throw new ValidationError('Duplicate key or invalid service JSON'); }
        row[key] = value(depth + 1); whitespace();
        const separator = text[cursor++]; if (separator === '}') { return row; }
        if (separator !== ',') { throw new ValidationError('Invalid service JSON object'); }
      }
    }
    if (char === '[') {
      cursor++; whitespace(); const list: unknown[] = [];
      if (text[cursor] === ']') { cursor++; return list; }
      for (;;) { list.push(value(depth + 1)); whitespace(); const separator = text[cursor++];
        if (separator === ']') { return list; } if (separator !== ',') { throw new ValidationError('Invalid service JSON array'); } }
    }
    for (const [literal, parsed] of [['true', true], ['false', false], ['null', null]] as const) {
      if (text.startsWith(literal, cursor)) { cursor += literal.length; return parsed; }
    }
    const match = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(text.slice(cursor));
    if (!match || !Number.isFinite(Number(match[0]))) { throw new ValidationError('Invalid service JSON value'); }
    cursor += match[0].length; return Number(match[0]);
  };
  try { const result = value(0); whitespace(); if (cursor !== text.length) { throw new ValidationError('Trailing service JSON'); } return result; }
  catch (error) { if (error instanceof ValidationError) { throw error; } throw new ValidationError('Invalid service JSON'); }
}
