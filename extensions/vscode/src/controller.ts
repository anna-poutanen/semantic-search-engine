import { randomUUID } from 'node:crypto';
import { SearchApi, ServiceClient } from './client';
import { bindRepository, RepositoryBinding, SavedEvidence, StaleEvidenceError, verifySavedResult } from './evidence';
import { Result, SearchOptions, SearchResponse, Status, validateOptions, validateQuery, ValidationError } from './schema';

export interface Entry { result: Result; generation: number; saved: SavedEvidence | null; staleReason: string | null; }
export interface ViewState {
  phase: 'disconnected' | 'checking' | 'ready' | 'searching' | 'refreshing' | 'error';
  status: Status | null; entries: Entry[]; response: SearchResponse | null; message: string;
}
export class SearchController {
  private generation = 0;
  private api: SearchApi | null = null;
  private binding: RepositoryBinding | null = null;
  private operation: { controller: AbortController; requestId: string | null } | null = null;
  private disposed = false;
  state: ViewState = { phase: 'disconnected', status: null, entries: [], response: null, message: 'Connect to a service indexing this workspace repository' };
  constructor(private readonly trust: () => boolean, private readonly changed: (state: ViewState) => void,
    private readonly apiFactory: (url: string) => SearchApi = url => new ServiceClient(url)) {}

  private allowed(): void { if (this.disposed || !this.trust()) { throw new ValidationError('Trust the local workspace before connecting or searching'); } }
  private publish(patch: Partial<ViewState>): void { this.state = { ...this.state, ...patch }; this.changed(this.state); }
  private begin(requestId: string | null): { generation: number; controller: AbortController } {
    this.stopOperation(); const controller = new AbortController();
    this.operation = { controller, requestId }; return { generation: ++this.generation, controller };
  }
  private stopOperation(): void {
    const current = this.operation; this.operation = null;
    if (!current) { return; } current.controller.abort();
    if (current.requestId && this.api) { void this.api.cancel(current.requestId).catch(() => undefined); }
  }
  private current(generation: number): boolean { return generation === this.generation && !this.disposed && this.trust(); }
  private failure(error: unknown, generation: number): void {
    if (this.current(generation)) { this.operation = null; this.publish({ phase: 'error', message: error instanceof Error ? error.message : 'Local service request failed' }); }
  }
  async connect(workspace: string, url: string): Promise<boolean> {
    this.allowed(); const op = this.begin(null); this.api = null; this.binding = null;
    this.publish({ phase: 'checking', status: null, entries: [], response: null, message: 'Checking local service and selected repository…' });
    try {
      const api = this.apiFactory(url), status = await api.status(op.controller.signal);
      if (!this.current(op.generation)) { return false; }
      const binding = await bindRepository(workspace, status.repo, status.source_root);
      if (status.closed) { throw new ValidationError('The local service is closed'); }
      if (!this.current(op.generation)) { return false; }
      this.api = api; this.binding = binding; this.operation = null;
      this.publish({ phase: 'ready', status, message: status.last_error ?? (status.ready ? 'Saved tracked Python source' : 'Connected; index is not ready yet') });
      return true;
    } catch (error) { this.failure(error, op.generation); return false; }
  }
  async status(): Promise<void> {
    this.allowed(); if (!this.api || !this.binding) { throw new ValidationError('Connect to the local service first'); }
    const api = this.api, binding = this.binding, op = this.begin(null);
    this.publish({ phase: 'checking', message: 'Waiting for local service status…' });
    try {
      const status = await api.status(op.controller.signal);
      if (!this.current(op.generation)) { return; }
      const echoed = await bindRepository(binding.root, status.repo, status.source_root);
      if (echoed.sourceRoot !== binding.sourceRoot || status.closed) { throw new ValidationError('Service scope changed or closed; reconnect'); }
      if (!this.current(op.generation)) { return; } this.operation = null;
      for (const entry of this.state.entries) { entry.generation = op.generation; }
      this.publish({ phase: 'ready', status, message: status.last_error ?? 'Saved tracked Python source' });
    } catch (error) { this.failure(error, op.generation); }
  }
  async refresh(): Promise<void> {
    this.allowed(); if (!this.api || !this.binding) { throw new ValidationError('Connect to the local service first'); }
    const api = this.api, op = this.begin(null);
    this.publish({ phase: 'refreshing', entries: [], response: null, message: 'Refreshing saved tracked Python source…' });
    try {
      const windows = await api.refresh(op.controller.signal);
      if (!this.current(op.generation)) { return; } this.operation = null;
      this.publish({ phase: 'ready', status: this.state.status ? { ...this.state.status, windows, ready: true, last_error: null } : null,
        message: 'Index refreshed; run a new search' });
    } catch (error) { this.failure(error, op.generation); }
  }
  async search(query: string, options: SearchOptions): Promise<void> {
    this.allowed(); validateQuery(query); validateOptions(options);
    if (!this.api || !this.binding) { throw new ValidationError('Connect to the local service first'); }
    const api = this.api, binding = this.binding, requestId = randomUUID(), op = this.begin(requestId);
    this.publish({ phase: 'searching', entries: [], response: null, message: 'Searching saved Python source…' });
    try {
      const response = await api.search(query, options, requestId, op.controller.signal);
      if (!this.current(op.generation)) { return; }
      const echoed = await bindRepository(binding.root, response.repo, response.source_root);
      if (echoed.sourceRoot !== binding.sourceRoot) { throw new ValidationError('Service source root changed; reconnect'); }
      if (!this.current(op.generation)) { return; }
      const entries: Entry[] = [];
      for (const result of response.results) {
        if (!this.current(op.generation)) { return; }
        let saved: SavedEvidence | null = null, staleReason: string | null = null;
        try { saved = await verifySavedResult(binding, result); }
        catch (error) { staleReason = error instanceof Error ? error.message : 'Source could not be verified'; }
        entries.push({ result, generation: op.generation, saved, staleReason });
      }
      if (!this.current(op.generation)) { return; } this.operation = null;
      const message = response.match_status === 'no_candidates' ? 'No candidates returned in this saved Python scope'
        : response.match_status === 'insufficient_evidence' ? 'The configured policy found insufficient evidence; this is not proof of absence'
        : !entries.length ? 'No excerpt fit the requested output budgets' : 'Saved Python results; previews contain captured source';
      this.publish({ phase: 'ready', entries, response, message, status: this.state.status ? { ...this.state.status,
        windows: response.indexstats.total_windows, source_manifest: response.indexstats.source_manifest, ready: true, last_error: null } : null });
    } catch (error) { this.failure(error, op.generation); }
  }
  cancel(): void {
    this.allowed(); if (!this.operation?.requestId) { return; }
    this.stopOperation(); this.generation++;
    this.publish({ phase: this.binding ? 'ready' : 'disconnected', entries: [], response: null,
      message: 'Search cancelled; a running model forward may finish before cancellation is observed' });
  }
  async navigation(entry: Entry, isDirty: (canonicalPath: string) => Promise<boolean>): Promise<string> {
    this.allowed(); if (!this.binding || !this.state.entries.includes(entry) || entry.generation !== this.generation) { throw new StaleEvidenceError('Result expired; run a new search'); }
    if (!entry.saved) { throw new StaleEvidenceError(entry.staleReason ?? 'Source was not verified'); }
    if (await isDirty(entry.saved.canonicalPath)) { throw new StaleEvidenceError('This editor has unsaved changes; use captured preview, or save and search again'); }
    const saved = await verifySavedResult(this.binding, entry.result, entry.saved);
    if (!this.state.entries.includes(entry) || entry.generation !== this.generation || await isDirty(saved.canonicalPath)) {
      throw new StaleEvidenceError('Source or search changed during navigation');
    }
    return saved.canonicalPath;
  }
  get repository(): string | null { return this.binding?.root ?? null; }
  disconnect(): void { this.stopOperation(); this.generation++; this.api = null; this.binding = null;
    this.publish({ phase: 'disconnected', status: null, entries: [], response: null, message: 'Disconnected' }); }
  dispose(): void { this.stopOperation(); this.generation++; this.disposed = true; this.api = null; this.binding = null;
    this.state = { phase: 'disconnected', status: null, entries: [], response: null, message: 'Disposed' }; }
}
