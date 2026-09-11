import { strict as assert } from 'node:assert';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import * as vscode from 'vscode';
import { ExtensionApi } from '../../src/extension';
import { ViewState } from '../../src/controller';
import { hash, physicalLines } from '../../src/schema';
import { sourceTextFromEditor, sourceTextFromPreview } from '../../src/source';

async function replace(document: vscode.TextDocument, text: string): Promise<void> {
  const edit = new vscode.WorkspaceEdit();
  edit.replace(document.uri, new vscode.Range(document.positionAt(0), document.positionAt(document.getText().length)), text);
  assert.equal(await vscode.workspace.applyEdit(edit), true);
  assert.equal(document.getText(), text); assert.equal(document.isDirty, true);
}
async function changed(api: ExtensionApi): Promise<void> {
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    if (api.getState().entries.some(entry => entry.staleReason)) { return; }
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  throw new Error('Dirty document change did not expire the captured results');
}

export async function verifyOverlays(api: ExtensionApi, repo: string): Promise<Record<string, unknown>> {
  const checks: Record<string, boolean> = {};
  const names = ['src/inventory.py', 'src/decorate.js', 'src/delta.ts'];
  const before = Object.fromEntries(await Promise.all(names.map(async name => [name, hash(await fs.readFile(path.join(repo, name), 'utf8'))])));
  const js = await vscode.workspace.openTextDocument(vscode.Uri.file(path.join(repo, 'src/decorate.js')));
  const ts = await vscode.workspace.openTextDocument(vscode.Uri.file(path.join(repo, 'src/delta.ts')));
  assert.equal(js.isDirty, false); assert.equal(ts.isDirty, false);
  await replace(js, js.getText().replace('decorate_code', 'decorate_unsaved').replace(' + "]"', ' + "]🧪"'));
  await replace(ts, 'export function calculate_unsaved_ratio(before: number, after: number): number {\n  return after / before;\n}\n');
  const options = { retrieval: 'dense', rerank: false, max_results: 5, token_budget: 2000, include_dependencies: false };
  const search = async (query: string, includeUnsaved: boolean) => {
    const state = await vscode.commands.executeCommand<ViewState>('semanticSearch.find', { query, includeUnsaved, options });
    assert.equal(state.phase, 'ready', state.message); assert.ok(state.response); return state;
  };
  const state = await search('Find the implementation that divides an after value by a before value.', true);
  const scope = state.response!.scope; assert.equal(scope?.source_state, 'overlay'); assert.equal(scope?.overlays?.length, 2);
  const jsIndex = state.entries.findIndex(entry => entry.result.symbol === 'decorate_unsaved');
  const tsIndex = state.entries.findIndex(entry => entry.result.symbol === 'calculate_unsaved_ratio');
  assert.ok(jsIndex >= 0 && tsIndex >= 0);
  const jsEntry = state.entries[jsIndex]!, tsEntry = state.entries[tsIndex]!;
  for (const [entry, document] of [[jsEntry, js], [tsEntry, ts]] as const) {
    assert.ok(entry.overlay && entry.saved); assert.equal(entry.result.source_origin, 'overlay');
    assert.equal(entry.result.buffer_version, document.version);
    assert.equal(entry.result.buffer_hash, hash(sourceTextFromEditor(entry.saved, document.getText())));
    assert.equal(entry.staleReason, null);
  }
  checks.two_dirty_inputs_and_literal_bom_unicode_provenance = true;
  const previewUri = await vscode.commands.executeCommand<vscode.Uri>('semanticSearch.preview', jsIndex);
  const preview = await vscode.workspace.openTextDocument(previewUri); assert.equal(preview.languageId, 'plaintext');
  assert.equal(sourceTextFromPreview(jsEntry.result.source, preview.getText()), jsEntry.result.source);
  assert.equal(preview.isDirty, false); checks.exact_plaintext_dirty_preview = true;
  const opened = await vscode.commands.executeCommand<vscode.Uri>('semanticSearch.open', jsIndex);
  assert.equal(opened.toString(), js.uri.toString());
  const editor = vscode.window.activeTextEditor; assert.ok(editor); assert.equal(editor.document, js);
  assert.equal(editor.document.isDirty, true);
  assert.equal(editor.selection.start.line, jsEntry.result.start_line - 1);
  const selected = physicalLines(sourceTextFromEditor(jsEntry.saved!, js.getText()))
    .slice(jsEntry.result.start_line - 1, jsEntry.result.end_line).join('');
  assert.equal(selected, jsEntry.result.source); checks.navigation_uses_same_dirty_document = true;
  const savedIndex = state.entries.findIndex(entry => entry.result.symbol === 'normalize_item');
  assert.ok(savedIndex >= 0); assert.equal(state.entries[savedIndex]!.result.source_origin, 'saved');
  await vscode.commands.executeCommand('semanticSearch.open', savedIndex);
  assert.equal(vscode.window.activeTextEditor?.document.isDirty, false); checks.saved_result_in_overlay_response_navigates = true;
  const savedWindowCount = state.status?.windows, savedManifest = state.status?.source_manifest;
  assert.equal(savedWindowCount, 5);
  // Only the other input changes: the JS excerpt and document stay identical.
  await replace(ts, ts.getText() + '// edit after the response\n');
  await changed(api);
  await assert.rejects(async () => await vscode.commands.executeCommand('semanticSearch.open', jsIndex), /expired|changed|stale|new search/i);
  const stalePreviewUri = await vscode.commands.executeCommand<vscode.Uri>('semanticSearch.preview', jsIndex);
  const stalePreview = await vscode.workspace.openTextDocument(stalePreviewUri);
  assert.equal(sourceTextFromPreview(jsEntry.result.source, stalePreview.getText()), jsEntry.result.source);
  checks.changed_other_input_blocks_navigation_and_retains_preview = true;
  const fresh = await search('Find the implementation that uppercases an input and wraps it in brackets.', true);
  const freshJs = fresh.entries.findIndex(entry => entry.result.symbol === 'decorate_unsaved'); assert.ok(freshJs >= 0);
  const freshTsBinding = fresh.response!.scope?.overlays?.find(binding => binding.path === 'src/delta.ts');
  assert.equal(freshTsBinding?.version, ts.version);
  await vscode.commands.executeCommand('semanticSearch.open', freshJs);
  assert.equal(vscode.window.activeTextEditor?.document, js); checks.new_search_rebinds_current_versions = true;
  assert.equal(fresh.status?.windows, savedWindowCount); assert.equal(fresh.status?.source_manifest, savedManifest);
  checks.saved_status_not_replaced_by_overlay_index = true;
  const saved = await search('Find the implementation that uppercases a code value and encloses it in square brackets.', false);
  assert.equal(saved.response!.scope?.source_state, undefined);
  assert.ok(saved.entries.some(entry => entry.result.symbol === 'decorate_code'));
  assert.ok(saved.entries.every(entry => entry.result.symbol !== 'decorate_unsaved' && entry.result.symbol !== 'calculate_unsaved_ratio'));
  const savedDirtyIndex = saved.entries.findIndex(entry => entry.result.symbol === 'decorate_code');
  await assert.rejects(async () => await vscode.commands.executeCommand('semanticSearch.open', savedDirtyIndex), /unsaved|changed|dirty|new search/i);
  checks.later_saved_search_uses_disk_and_blocks_dirty_target = true;
  assert.equal(js.isDirty, true); assert.equal(ts.isDirty, true);
  const after = Object.fromEntries(await Promise.all(names.map(async name => [name, hash(await fs.readFile(path.join(repo, name), 'utf8'))])));
  assert.deepEqual(after, before); checks.no_files_saved_or_modified = true;
  return { passed: Object.values(checks).every(Boolean), checks, source_before_sha256: before, source_after_sha256: after,
    input_buffers: state.response!.scope?.overlays, later_buffer_versions: fresh.response!.scope?.overlays,
    limits: ['Explicit trusted authored fixture, not general relevance or coding correctness.',
      'Dirty documents remain in the disposable test host until it exits; no save occurs.',
      'File/buffer proofs do not atomically lock edits after final validation.'] };
}
