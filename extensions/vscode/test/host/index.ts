import { strict as assert } from 'node:assert';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import * as vscode from 'vscode';
import { ExtensionApi } from '../../src/extension';
import { hash, physicalLines } from '../../src/schema';
import { ViewState } from '../../src/controller';

function required(name: string): string {
  const value = process.env[name]; if (!value) { throw new Error(`Missing test-runner environment: ${name}`); } return value;
}
async function limit<T>(operation: PromiseLike<T>, label: string, timeout = 180000): Promise<T> {
  let timer: NodeJS.Timeout | undefined;
  try { return await Promise.race([Promise.resolve(operation), new Promise<T>((_, reject) => { timer = setTimeout(() => reject(new Error(`${label} timed out`)), timeout); })]); }
  finally { if (timer) { clearTimeout(timer); } }
}
export async function run(): Promise<void> {
  const output = required('SEMANTIC_SEARCH_HOST_RESULT');
  const record: Record<string, unknown> = { started_utc: new Date().toISOString(), vscode_version: vscode.version,
    host_kind: 'actual VS Code Extension Development Host', workspace_trust: vscode.workspace.isTrusted,
    trust_note: 'Fresh explicit fixture host uses --disable-workspace-trust; Restricted Mode remains a separate pending UI case.',
    service_transport: 'Explicit local HTTP endpoint; model provenance is recorded from backend status and the parent service launch.',
    checks: {}, passed: false };
  const checks = record.checks as Record<string, boolean>;
  try {
    assert.equal(vscode.workspace.isTrusted, true);
    assert.equal(vscode.env.remoteName, undefined);
    const folder = vscode.workspace.workspaceFolders?.[0]; assert.ok(folder);
    assert.equal(await fs.realpath(folder.uri.fsPath), await fs.realpath(required('SEMANTIC_SEARCH_HOST_REPO')));
    const extension = vscode.extensions.getExtension<ExtensionApi>('anna-poutanen.semantic-search-local'); assert.ok(extension);
    const api = await limit(extension.activate(), 'extension activation');
    assert.equal(typeof api.getState, 'function'); assert.equal(typeof api.onDidChangeState, 'function'); checks.activation = true;
    const catalog = await vscode.commands.getCommands(true);
    for (const name of ['connect', 'find', 'status', 'refresh', 'cancel', 'preview', 'open']) { assert.ok(catalog.includes(`semanticSearch.${name}`)); }
    checks.command_registration = true;
    const connected = await limit(vscode.commands.executeCommand<ViewState>('semanticSearch.connect', {
      workspacePath: folder.uri.fsPath, serviceUrl: required('SEMANTIC_SEARCH_HOST_URL')
    }), 'connect');
    assert.equal(connected.phase, 'ready'); assert.ok(connected.status); assert.equal(connected.status.closed, false);
    checks.connect = true;
    const status = await limit(vscode.commands.executeCommand<ViewState>('semanticSearch.status'), 'status');
    assert.equal(status.phase, 'ready'); assert.ok(status.status?.ready); checks.status = true;
    const query = required('SEMANTIC_SEARCH_HOST_QUERY');
    const response = await limit(vscode.commands.executeCommand<ViewState>('semanticSearch.find', { query,
      options: { retrieval: 'dense', rerank: false, max_results: 5, token_budget: 2000, include_dependencies: false }
    }), 'dense search');
    assert.equal(response.phase, 'ready'); assert.equal(response.response?.query, query); assert.ok(response.entries.length);
    assert.equal(response.entries[0]?.result.symbol, 'normalize_item');
    const entry = response.entries[0]; assert.ok(entry?.saved); assert.equal(entry.staleReason, null);
    checks.search = true; checks.expected_first_symbol = true; checks.saved_evidence = true;
    const previewUri = await vscode.commands.executeCommand<vscode.Uri>('semanticSearch.preview', 0);
    assert.equal(previewUri.scheme, 'semantic-search-preview');
    const preview = await vscode.workspace.openTextDocument(previewUri);
    assert.equal(preview.languageId, 'plaintext'); assert.equal(preview.getText(), entry.result.source);
    assert.equal(hash(preview.getText()), entry.result.source_hash); assert.equal(preview.isDirty, false);
    checks.plaintext_preview = true; checks.exact_preview = true;
    const literalIndex = response.entries.findIndex(row => row.result.symbol === 'render_literal');
    assert.ok(literalIndex >= 0);
    const literalUri = await vscode.commands.executeCommand<vscode.Uri>('semanticSearch.preview', literalIndex);
    const literalDocument = await vscode.workspace.openTextDocument(literalUri);
    assert.equal(literalDocument.languageId, 'plaintext');
    assert.match(literalDocument.getText(), /<script>this is plain source, never HTML<\/script>/);
    checks.literal_html_plaintext = true;
    const sourceUri = await vscode.commands.executeCommand<vscode.Uri>('semanticSearch.open', 0);
    assert.equal(sourceUri.scheme, 'file');
    const editor = vscode.window.activeTextEditor; assert.ok(editor);
    assert.equal(await fs.realpath(editor.document.uri.fsPath), entry.saved.canonicalPath);
    assert.equal(editor.selection.start.line, entry.result.start_line - 1);
    assert.equal(editor.selection.end.line, entry.result.end_line - 1);
    const excerpt = physicalLines(editor.document.getText()).slice(entry.result.start_line - 1, entry.result.end_line).join('');
    assert.equal(excerpt, entry.result.source); assert.equal(hash(excerpt), entry.result.source_hash);
    checks.verified_navigation = true; checks.exact_selection = true;
    await vscode.commands.executeCommand('workbench.view.explorer');
    await vscode.commands.executeCommand('semanticSearch.results.focus');
    checks.results_view_focus = true;
    const refreshed = await limit(vscode.commands.executeCommand<ViewState>('semanticSearch.refresh'), 'refresh');
    assert.equal(refreshed.phase, 'ready'); assert.equal(refreshed.entries.length, 0); checks.refresh = true;
    // Observe the real controller transition before cancellation. No sleep or
    // inference-speed assumption determines whether the command reached search.
    let subscription: vscode.Disposable | undefined;
    const searching = new Promise<void>(resolve => { subscription = api.onDidChangeState(state => {
      if (state.phase === 'searching') { subscription?.dispose(); resolve(); }
    }); });
    const cancelledQuery = query + ' Cancellation transport smoke.';
    const pending = vscode.commands.executeCommand('semanticSearch.find', { query: cancelledQuery,
      options: { retrieval: 'dense', rerank: false, max_results: 5, token_budget: 2000, include_dependencies: false } });
    try { await limit(searching, 'search transition', 10000); }
    finally { subscription?.dispose(); }
    await limit((async () => {
      const deadline = Date.now() + 10000;
      while (Date.now() < deadline) {
        try {
          const starts: { query_sha256: string }[] = JSON.parse(await fs.readFile(required('SEMANTIC_SEARCH_HOST_RECEIPT_STARTS'), 'utf8'));
          if (starts.some(row => row.query_sha256 === hash(cancelledQuery))) { return; }
        } catch { /* First receipt or an in-progress file update may not yet be readable. */ }
        await new Promise(resolve => setTimeout(resolve, 10));
      }
      throw new Error('Independent search dispatch was not observed');
    })(), 'independent search dispatch receipt', 10000);
    assert.equal(api.getState().phase, 'searching');
    await vscode.commands.executeCommand('semanticSearch.cancel');
    await limit(pending, 'cancelled command settlement', 10000);
    const cancelled = api.getState();
    assert.equal(cancelled.response, null); assert.equal(cancelled.entries.length, 0); assert.match(cancelled.message, /cancelled/);
    checks.cancel_command = true;
    const replacement = await limit(vscode.commands.executeCommand<ViewState>('semanticSearch.find', { query,
      options: { retrieval: 'dense', rerank: false, max_results: 5, token_budget: 2000, include_dependencies: false }
    }), 'replacement search');
    assert.equal(replacement.phase, 'ready'); assert.equal(replacement.response?.query, query);
    assert.equal(api.getState().response?.query, query); assert.ok(replacement.entries.length);
    checks.replacement_and_late_suppression = true;
    record.result = { first_symbol: entry.result.symbol, path: entry.result.path, start_line: entry.result.start_line,
      end_line: entry.result.end_line, source_hash: entry.result.source_hash, source_chars: [...entry.result.source].length,
      token_usage: response.response?.token_usage, returned_results: response.entries.length,
      store_windows: connected.status.windows, watcher_running: connected.status.watcher?.running ?? null };
    record.cancellation_note = 'Command/transport and late-result suppression only; a running Metal forward is not proven interrupted.';
    record.passed = true;
  } catch (error) {
    record.error = error instanceof Error ? error.message : String(error);
    throw error;
  } finally {
    record.finished_utc = new Date().toISOString();
    await fs.mkdir(path.dirname(output), { recursive: true });
    await fs.writeFile(output, JSON.stringify(record, null, 2) + '\n');
  }
}
