import * as path from 'node:path';
import { defaultOptions, SearchOptions, validateOptions, validateQuery, ValidationError } from './schema';

export interface ConnectRequest { workspacePath: string; serviceUrl: string; }
export interface FindRequest { query: string; options?: Partial<SearchOptions>; includeUnsaved?: boolean; }

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) { throw new ValidationError('Expected command arguments'); }
  return value as Record<string, unknown>;
}
function keys(value: Record<string, unknown>, allowed: string[]): void {
  if (Object.keys(value).some(key => !allowed.includes(key))) { throw new ValidationError('Unknown command argument'); }
}
export function parseConnectRequest(value: unknown): ConnectRequest | null {
  if (value === undefined) { return null; }
  const row = record(value); keys(row, ['workspacePath', 'serviceUrl']);
  if (typeof row.workspacePath !== 'string' || !path.isAbsolute(row.workspacePath)
      || typeof row.serviceUrl !== 'string' || !row.serviceUrl) {
    throw new ValidationError('Supply an absolute open workspace path and loopback service URL');
  }
  return { workspacePath: row.workspacePath, serviceUrl: row.serviceUrl };
}
export function parseFindRequest(value: unknown, defaults: SearchOptions): { query: string; options: SearchOptions; includeUnsaved?: boolean } | null {
  if (value === undefined) { return null; }
  const row = record(value); keys(row, ['query', 'options', 'includeUnsaved']);
  if (row.includeUnsaved !== undefined && typeof row.includeUnsaved !== 'boolean') { throw new ValidationError('includeUnsaved must be a boolean'); }
  if (typeof row.query !== 'string') { throw new ValidationError('Supply a search query'); }
  validateQuery(row.query);
  const overrides = row.options === undefined ? {} : record(row.options);
  keys(overrides, Object.keys(defaultOptions));
  const options = { ...defaults, ...overrides } as SearchOptions;
  validateOptions(options);
  return { query: row.query, options, ...(row.includeUnsaved === undefined ? {} : { includeUnsaved: row.includeUnsaved }) };
}
export function resultIndex(value: number, count: number): number {
  if (!Number.isSafeInteger(value) || value < 0 || value >= count) { throw new ValidationError('Select a current result index'); }
  return value;
}
