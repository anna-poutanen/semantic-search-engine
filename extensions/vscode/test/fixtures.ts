import { defaultOptions, hash, Result, SearchResponse, Status } from '../src/schema';
export const source = 'def implementation():\n    return "ready"\n';
export function result(text = source): Result {
  return { id: 'src/app.py:implementation:1-2', path: 'src/app.py', symbol: 'implementation', start_line: 1,
    end_line: 2, window_end_line: 2, source: text, source_hash: hash(text), content_hash: hash(text),
    retrieval_score: .8, h2o_score: null, truncated: false, origin: 'retrieval', related_to: null };
}
export function status(repo: string): Status {
  return { repo, source_root: 'src', closed: false, ready: true, windows: 1, last_error: null,
    source_manifest: 'a'.repeat(64), watcher: { running: true, pending: false, last_error: null } };
}
export function response(repo: string, query = 'behavior', text = source): SearchResponse {
  return { repo, source_root: 'src', query, retrieval: defaultOptions.retrieval, rerank: false, results: [result(text)],
    match_status: 'candidates', source_budget: defaultOptions.source_budget, total_source_chars: [...text].length, budget_exhausted: false,
    token_usage: { encoding: 'cl100k_base', tokens: 128, budget: defaultOptions.token_budget, truncated: false },
    indexstats: { total_windows: 1, source_manifest: 'a'.repeat(64) } };
}
