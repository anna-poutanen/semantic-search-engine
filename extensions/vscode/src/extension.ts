import * as vscode from 'vscode';
import * as fs from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { Entry, SearchController, ViewState } from './controller';
import { hash, physicalLines, SearchOptions, ValidationError } from './schema';
import { StaleEvidenceError } from './evidence';
import { parseConnectRequest, parseFindRequest, resultIndex } from './commands';
import { sourceTextFromEditor } from './source';
import { EditorBuffer, verifyEditorBuffer } from './overlays';
import { searchOptions } from './settings';

export interface ExtensionApi {
  getState(): Readonly<ViewState>;
  onDidChangeState(listener: (state: Readonly<ViewState>) => unknown): vscode.Disposable;
}

class ResultsView implements vscode.TreeDataProvider<Entry | string> {
  private readonly emitter = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this.emitter.event;
  private state: ViewState | null = null;
  update(state: ViewState): void { this.state = state; this.emitter.fire(); }
  getChildren(): (Entry | string)[] { return this.state?.entries.length ? this.state.entries : [this.state?.message ?? 'Connect to the local service']; }
  getTreeItem(row: Entry | string): vscode.TreeItem {
    if (typeof row === 'string') { return new vscode.TreeItem(row); }
    const result = row.result, item = new vscode.TreeItem(result.symbol);
    item.description = `${result.path}:${result.start_line}–${result.end_line}${result.source_origin === 'overlay' ? ' · dirty buffer' : ''}${result.truncated ? ' · truncated' : ''}${result.origin === 'dependency' ? ' · context reference' : ''}${row.staleReason ? ' · preview only' : ''}`;
    item.tooltip = result.origin === 'dependency' ? 'Static related context, not a verified query match' : 'Preview the captured source excerpt';
    item.contextValue = 'semanticSearch.result';
    item.command = { command: 'semanticSearch.preview', title: 'Preview Captured Source', arguments: [row] };
    return item;
  }
  dispose(): void { this.emitter.dispose(); }
}
class CapturedPreviews implements vscode.TextDocumentContentProvider {
  private readonly texts = new Map<string, string>();
  add(entry: Entry): vscode.Uri {
    // Only generated IDs resolve; result paths never become file-system requests.
    const uri = vscode.Uri.from({ scheme: 'semantic-search-preview', path: `/${randomUUID()}/${entry.result.path.split('/').at(-1) ?? 'source.py'}` });
    this.texts.set(uri.toString(), entry.result.source);
    while (this.texts.size > 64) { const first = this.texts.keys().next().value; if (first) { this.texts.delete(first); } }
    return uri;
  }
  provideTextDocumentContent(uri: vscode.Uri): string {
    const source = this.texts.get(uri.toString());
    if (source === undefined) { throw new StaleEvidenceError('Captured preview expired; run a new search'); }
    return source;
  }
  close(uri: vscode.Uri): void { this.texts.delete(uri.toString()); }
  dispose(): void { this.texts.clear(); }
}
let active: SearchController | undefined;

export function activate(context: vscode.ExtensionContext): ExtensionApi {
  const view = new ResultsView(), previews = new CapturedPreviews();
  const states = new vscode.EventEmitter<Readonly<ViewState>>();
  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 10);
  let folder: vscode.WorkspaceFolder | undefined;
  const bufferKeys = new WeakMap<vscode.TextDocument, string>();
  const snapshot = (document: vscode.TextDocument): EditorBuffer => {
    let key = bufferKeys.get(document); if (!key) { key = randomUUID(); bufferKeys.set(document, key); }
    return { key, path: document.uri.scheme === 'file' ? document.uri.fsPath : document.uri.toString(),
      version: document.version, text: document.getText(), dirty: document.isDirty, closed: document.isClosed };
  };
  const buffers = () => vscode.workspace.textDocuments.filter(document => document.isDirty).map(snapshot);
  const controller = new SearchController(() => vscode.workspace.isTrusted && !vscode.env.remoteName, state => {
    view.update(state);
    const loading = ['checking', 'searching', 'refreshing'].includes(state.phase);
    status.text = `${loading ? '$(loading~spin)' : '$(search)'} Semantic Search: ${state.status ? `${state.status.windows} windows` : state.phase}`;
    const watcher = state.status?.watcher;
    status.tooltip = `${state.message}\nSaved searches use tracked source. Explicit dirty-buffer searches use disposable request overrides.\nWatcher: ${watcher ? watcher.running ? 'running' : 'stopped' : 'unavailable'}${watcher?.pending ? ' (pending)' : ''}${watcher?.last_error ? `\nWatcher error: ${watcher.last_error}` : ''}`;
    status.command = 'semanticSearch.status';
    status.show();
    states.fire(structuredClone(state));
  }, undefined, buffers);
  active = controller; view.update(controller.state);
  const guard = () => {
    if (!vscode.workspace.isTrusted || vscode.env.remoteName) { throw new ValidationError('Use a trusted local desktop workspace for the local service'); }
    if (folder && !vscode.workspace.workspaceFolders?.some(candidate => candidate.uri.toString() === folder?.uri.toString())) {
      controller.disconnect(); folder = undefined; throw new ValidationError('Selected workspace folder was removed; reconnect');
    }
  };
  const wrap = (action: (...args: unknown[]) => Promise<unknown> | unknown) => async (...args: unknown[]) => {
    try { guard(); return await action(...args); }
    catch (error) {
      // Supplied command arguments support trusted automation without waiting
      // for a notification dismissal. Palette and tree actions keep normal UI.
      const argument = args[0];
      if (typeof argument === 'number' || (argument && typeof argument === 'object' && !('result' in argument))) { throw error; }
      await vscode.window.showErrorMessage(error instanceof Error ? error.message : 'Semantic Search failed'); return undefined;
    }
  };
  const chooseFolder = async (): Promise<vscode.WorkspaceFolder | undefined> => {
    const folders = vscode.workspace.workspaceFolders?.filter(candidate => candidate.uri.scheme === 'file') ?? [];
    if (!folders.length) { throw new ValidationError('Open the repository as a local workspace folder first'); }
    if (folders.length === 1) { return folders[0]; }
    const chosen = await vscode.window.showQuickPick(folders.map(candidate => ({ label: candidate.name, description: candidate.uri.fsPath, folder: candidate })),
      { placeHolder: 'Select the repository indexed by your local service' });
    return chosen?.folder;
  };
  const connect = async (argument?: unknown) => {
    const request = parseConnectRequest(argument);
    const selected = request ? (vscode.workspace.workspaceFolders ?? []).find(candidate =>
      candidate.uri.scheme === 'file' && candidate.uri.fsPath === request.workspacePath) : await chooseFolder();
    if (!selected) { if (request) { throw new ValidationError('Command workspace path must name an open local workspace folder'); } return; }
    const configured = vscode.workspace.getConfiguration('semanticSearch', selected.uri).get<string>('serviceUrl', 'http://127.0.0.1:8750');
    const url = request?.serviceUrl ?? await vscode.window.showInputBox({ prompt: 'Local service URL; repository must match the selected folder', value: configured, ignoreFocusOut: true });
    if (url === undefined) { return; }
    if (await controller.connect(selected.uri.fsPath, url)) { folder = selected; }
    return structuredClone(controller.state);
  };
  const options = (): SearchOptions => {
    if (!folder) { throw new ValidationError('Connect to the selected repository first'); }
    return searchOptions(vscode.workspace.getConfiguration('semanticSearch', folder.uri));
  };
  const resolveEntry = (row: unknown): Entry => {
    const entry = typeof row === 'number' ? controller.state.entries[resultIndex(row, controller.state.entries.length)] : row as Entry;
    if (!entry?.result || !controller.state.entries.includes(entry)) { throw new ValidationError('Select a current search result'); }
    return entry;
  };
  const preview = async (row: unknown) => {
    const entry = resolveEntry(row);
    const document = await vscode.workspace.openTextDocument(previews.add(entry));
    await vscode.languages.setTextDocumentLanguage(document, 'plaintext');
    await vscode.window.showTextDocument(document, { preview: true, viewColumn: vscode.ViewColumn.Beside });
    return document.uri;
  };
  const dirty = async (canonical: string): Promise<boolean> => {
    for (const document of vscode.workspace.textDocuments) {
      if (!document.isDirty || document.uri.scheme !== 'file') { continue; }
      try { if (await fs.realpath(document.uri.fsPath) === canonical) { return true; } } catch { /* Deleted saved file is checked separately. */ }
    }
    return false;
  };
  const open = async (row: unknown) => {
    const entry = resolveEntry(row);
    try {
      if (!entry?.result) { throw new ValidationError('Select a current result'); }
      const filename = await controller.navigation(entry, dirty);
      const document = entry.overlay ? vscode.workspace.textDocuments.find(candidate => bufferKeys.get(candidate) === entry.overlay?.bufferKey)
        : await vscode.workspace.openTextDocument(vscode.Uri.file(filename));
      if (!document) { throw new StaleEvidenceError('Captured dirty editor was closed; run a new search'); }
      const editor = await vscode.window.showTextDocument(document, { preview: true });
      await controller.navigation(entry, dirty);
      const fullText = entry.overlay ? verifyEditorBuffer(entry.overlay, snapshot(document)) : sourceTextFromEditor(entry.saved!, document.getText());
      const source = physicalLines(fullText).slice(entry.result.start_line - 1, entry.result.end_line).join('');
      if ((!entry.overlay && document.isDirty) || source !== entry.result.source || hash(source) !== entry.result.source_hash) {
        throw new StaleEvidenceError('The editor text changed; run a new search');
      }
      const range = new vscode.Range(entry.result.start_line - 1, 0, entry.result.end_line - 1,
        document.lineAt(entry.result.end_line - 1).text.length);
      editor.selection = new vscode.Selection(range.start, range.end);
      editor.revealRange(range, vscode.TextEditorRevealType.InCenter);
      return document.uri;
    } catch (error) {
      if (!(error instanceof StaleEvidenceError)) { throw error; }
      if (typeof row === 'number') { throw error; }
      const choice = await vscode.window.showWarningMessage(error.message, 'Captured Preview', 'New Search');
      if (choice === 'Captured Preview') { await preview(entry); }
      else if (choice === 'New Search') { await vscode.commands.executeCommand('semanticSearch.find'); }
    }
  };
  context.subscriptions.push(view, previews, states, status, { dispose: () => controller.dispose() },
    vscode.window.registerTreeDataProvider('semanticSearch.results', view),
    vscode.workspace.registerTextDocumentContentProvider('semantic-search-preview', previews),
    vscode.workspace.onDidCloseTextDocument(document => {
      if (document.uri.scheme === 'semantic-search-preview') { previews.close(document.uri); }
      else if (document.uri.scheme === 'file' && (document.isDirty || bufferKeys.has(document))) { controller.buffersChanged(); }
    }),
    vscode.workspace.onDidChangeTextDocument(event => { if (event.document.uri.scheme === 'file') { controller.buffersChanged(); } }),
    vscode.workspace.onDidSaveTextDocument(document => { if (document.uri.scheme === 'file') { controller.buffersChanged(); } }),
    vscode.workspace.onDidOpenTextDocument(document => { if (document.isDirty) { controller.buffersChanged(); } }),
    vscode.workspace.onDidChangeWorkspaceFolders(() => {
      if (folder && !vscode.workspace.workspaceFolders?.some(candidate => candidate.uri.toString() === folder?.uri.toString())) {
        controller.disconnect(); folder = undefined;
      }
    }),
    vscode.commands.registerCommand('semanticSearch.connect', wrap(connect)),
    vscode.commands.registerCommand('semanticSearch.find', wrap(async (argument?: unknown) => {
      if (!controller.repository) {
        if (argument !== undefined) { throw new ValidationError('Connect to the selected repository before running a programmatic search'); }
        await connect();
      }
      if (!controller.repository) { return; }
      const request = parseFindRequest(argument, options());
      const query = request?.query ?? await vscode.window.showInputBox({ prompt: 'Describe the implementation behavior', ignoreFocusOut: true });
      let includeUnsaved = request?.includeUnsaved ?? false;
      if (query !== undefined && !request && buffers().length) {
        const choice = await vscode.window.showQuickPick([
          { label: 'Saved tracked source', includeUnsaved: false },
          { label: 'Include eligible dirty tracked buffers', description: 'Request only; no files are saved', includeUnsaved: true },
        ], { placeHolder: 'Choose this search source scope' });
        if (!choice) { return; } includeUnsaved = choice.includeUnsaved;
      }
      if (query !== undefined) { await controller.search(query, request?.options ?? options(), includeUnsaved); }
      return structuredClone(controller.state);
    })),
    vscode.commands.registerCommand('semanticSearch.status', wrap(async () => { if (!controller.repository) { await connect(); } else { await controller.status(); } return structuredClone(controller.state); })),
    vscode.commands.registerCommand('semanticSearch.refresh', wrap(async () => { await controller.refresh(); return structuredClone(controller.state); })),
    vscode.commands.registerCommand('semanticSearch.cancel', wrap(() => controller.cancel())),
    vscode.commands.registerCommand('semanticSearch.preview', wrap(preview)),
    vscode.commands.registerCommand('semanticSearch.open', wrap(open)));
  status.text = '$(search) Semantic Search: Disconnected'; status.command = 'semanticSearch.connect'; status.show();
  return {
    getState: () => { guard(); return structuredClone(controller.state); },
    onDidChangeState: listener => { guard(); return states.event(listener); }
  };
}
export function deactivate(): void { active?.dispose(); active = undefined; }
