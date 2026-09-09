import * as http from 'node:http';
import { TextDecoder } from 'node:util';
import { bool, integer, object, parseSearch, parseStatus, parseStrictJson, SearchOptions, SearchResponse, Status,
  string, validateOptions, validateQuery, ValidationError } from './schema';
import { BufferOverride, MAX_OVERLAY_BODY_BYTES, validateOverrides } from './overlays';

export const MAX_RESPONSE_BYTES = 1024 * 1024;
export class ServiceError extends Error {
  constructor(message: string, readonly code = 'transport_error', readonly status: number | null = null) { super(message); }
}
export interface SearchApi {
  status(signal?: AbortSignal): Promise<Status>;
  refresh(signal?: AbortSignal): Promise<number>;
  search(query: string, options: SearchOptions, requestId: string, signal?: AbortSignal, overlays?: readonly BufferOverride[]): Promise<SearchResponse>;
  cancel(requestId: string): Promise<boolean>;
}
export function localAddress(url: string): { hostname: string; port: number; base: string } {
  const match = /^http:\/\/(127\.0\.0\.1|localhost)(?::([0-9]+))?\/?$/.exec(url);
  if (!match) { throw new ValidationError('Use a loopback HTTP URL without credentials, paths, queries or fragments'); }
  const hostname = match[1] as string, port = match[2] ? Number(match[2]) : 80;
  integer(port, 1, 65535);
  return { hostname, port, base: `http://${hostname}:${port}` };
}
export class ServiceClient implements SearchApi {
  private readonly address;
  constructor(url: string, private readonly timeoutMs = 180000) { this.address = localAddress(url); }

  private async request(endpoint: string, payload?: unknown, signal?: AbortSignal, timeout = this.timeoutMs, cap = 65536): Promise<unknown> {
    const body = payload === undefined ? undefined : Buffer.from(JSON.stringify(payload), 'utf8');
    if (body && body.length > cap) { throw new ValidationError('Encoded JSON search request exceeds service size limit'); }
    if (signal?.aborted) { throw new ServiceError('Request cancelled', 'request_cancelled'); }
    return new Promise((resolve, reject) => {
      // A private agent uses no environment proxy, including NODE_USE_ENV_PROXY.
      // localhost is sent directly to numeric loopback without DNS resolution.
      const agent = new http.Agent({ keepAlive: false });
      let finished = false;
      const finish = (error: Error | null, result?: unknown) => {
        if (finished) { return; } finished = true;
        clearTimeout(timer); signal?.removeEventListener('abort', abort); agent.destroy();
        if (error) { reject(error); } else { resolve(result); }
      };
      const request = http.request({ hostname: '127.0.0.1', port: this.address.port, path: endpoint, agent,
        method: body ? 'POST' : 'GET', headers: { Host: `${this.address.hostname}:${this.address.port}`, Accept: 'application/json',
          ...(body ? { 'Content-Type': 'application/json', 'Content-Length': body.length } : {}) } }, response => {
        const status = response.statusCode ?? 0;
        if (status >= 300 && status < 400) { response.destroy(); finish(new ServiceError('Local service redirects are rejected', 'redirect_rejected', status)); return; }
        if (!/^application\/json(?:\s*;|$)/i.test(response.headers['content-type'] ?? '')) {
          response.destroy(); finish(new ServiceError('Local service returned an unexpected content type')); return;
        }
        const length = response.headers['content-length'];
        if (length && (!/^[0-9]+$/.test(length) || Number(length) > MAX_RESPONSE_BYTES)) {
          response.destroy(); finish(new ServiceError('Local response exceeds the 1 MiB cap', 'response_limit')); return;
        }
        const chunks: Buffer[] = []; let total = 0;
        response.on('data', (chunk: Buffer) => {
          total += chunk.length;
          if (total > MAX_RESPONSE_BYTES) { response.destroy(); finish(new ServiceError('Local response exceeds the 1 MiB cap', 'response_limit')); }
          else { chunks.push(chunk); }
        });
        response.on('error', () => finish(new ServiceError('Local response was interrupted')));
        response.on('end', () => {
          if (finished) { return; }
          try {
            const parsed = parseStrictJson(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks)));
            if (status !== 200) {
              const error = object(object(parsed).error);
              finish(new ServiceError(string(error.message, 1000), string(error.code, 128), status));
            } else { finish(null, parsed); }
          } catch { finish(new ServiceError('Local service returned malformed JSON', 'invalid_response', status)); }
        });
      });
      const abort = () => { request.destroy(); finish(new ServiceError('Request cancelled', 'request_cancelled')); };
      const timer = setTimeout(() => { request.destroy(); finish(new ServiceError('Local service request timed out', 'timeout')); }, timeout);
      signal?.addEventListener('abort', abort, { once: true });
      request.on('error', () => finish(new ServiceError('Could not reach the local search service')));
      if (signal?.aborted) { abort(); return; }
      request.end(body);
    });
  }

  async status(signal?: AbortSignal): Promise<Status> { return parseStatus(await this.request('/status', undefined, signal)); }
  async refresh(signal?: AbortSignal): Promise<number> {
    const result = object(await this.request('/index', {}, signal));
    return integer(object(result.indexstats).total_windows, 0);
  }
  async search(query: string, options: SearchOptions, requestId: string, signal?: AbortSignal, overlays: readonly BufferOverride[] = []): Promise<SearchResponse> {
    validateQuery(query); validateOptions(options);
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(requestId)) { throw new ValidationError('Invalid request ID'); }
    validateOverrides(overlays);
    const payload = { query, ...options, request_id: requestId, ...(overlays.length ? { overlays } : {}) };
    return parseSearch(await this.request('/search', payload, signal, this.timeoutMs, overlays.length ? MAX_OVERLAY_BODY_BYTES : 65536), query, options, overlays);
  }
  async cancel(requestId: string): Promise<boolean> {
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(requestId)) { throw new ValidationError('Invalid request ID'); }
    const response = object(await this.request('/cancel', { request_id: requestId }, undefined, 5000));
    if (response.request_id !== requestId) { throw new ValidationError('Invalid cancellation acknowledgement'); }
    return bool(response.cancelled);
  }
}
