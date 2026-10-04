import { defaultOptions, SearchOptions, validateOptions } from './schema';

export interface SearchConfiguration { get<T>(name: string, fallback: T): T; }

/** Called with the selected folder's resource-scoped VS Code configuration. */
export function searchOptions(configuration: SearchConfiguration): SearchOptions {
  return validateOptions({
    retrieval: configuration.get('retrieval', defaultOptions.retrieval),
    rerank: configuration.get('rerank', defaultOptions.rerank),
    rerank_provider: configuration.get('rerankProvider', defaultOptions.rerank_provider ?? 'h2o'),
    candidates: configuration.get('candidates', defaultOptions.candidates),
    max_results: configuration.get('maxResults', defaultOptions.max_results),
    source_budget: configuration.get('sourceBudget', defaultOptions.source_budget),
    token_budget: configuration.get('tokenBudget', defaultOptions.token_budget),
    include_dependencies: configuration.get('includeDependencies', defaultOptions.include_dependencies),
  });
}
