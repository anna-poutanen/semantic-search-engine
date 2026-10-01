import { strict as assert } from 'node:assert';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import { createHash } from 'node:crypto';
import * as vscode from 'vscode';
import { ViewState } from '../../src/controller';
import { hash, physicalLines } from '../../src/schema';

/** Prospective actual-host case. It only runs with the explicit smoke flag. */
export async function verifyQwenReranker(folder: vscode.WorkspaceFolder): Promise<Record<string, unknown>> {
  const bytesHash = (bytes: Buffer) => createHash('sha256').update(bytes).digest('hex');
  const checks: Record<string, boolean> = {};
  const settingsDirectory = path.join(folder.uri.fsPath, '.vscode');
  const settingsFile = path.join(settingsDirectory, 'settings.json');
  let beforeSettings: Buffer | undefined, directoryExisted = true;
  try { beforeSettings = await fs.readFile(settingsFile); } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') { throw error; } }
  try { await fs.stat(settingsDirectory); } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') { throw error; } directoryExisted = false; }
  const names = ['src/inventory.py', 'src/decorate.js', 'src/delta.ts'];
  const sourceBefore = Object.fromEntries(await Promise.all(names.map(async name => [name, hash(await fs.readFile(path.join(folder.uri.fsPath, name), 'utf8'))])));
  const values = { retrieval: 'hybrid', rerank: true, rerankProvider: 'qwen', candidates: 24,
    maxResults: 5, sourceBudget: 12000, tokenBudget: 2000, includeDependencies: false };
  const record: Record<string, unknown> = { passed: false, checks, selected_workspace_settings: values,
    source_before_sha256: sourceBefore, settings_before_sha256: beforeSettings ? bytesHash(beforeSettings) : null,
    limits: ['Tiny authored fixture and normal folder settings; not a retrieval quality or coding benchmark.',
      'Actual native counters and owned service cleanup must be verified separately by the service caller.',
      'Temporary fixture workspace settings are restored and no indexed source is saved.'] };
  const configuration = vscode.workspace.getConfiguration('semanticSearch', folder.uri);
  let dirty: vscode.TextDocument | undefined;
  try {
    for (const [name, value] of Object.entries(values)) { await configuration.update(name, value, vscode.ConfigurationTarget.WorkspaceFolder); }
    const selected = vscode.workspace.getConfiguration('semanticSearch', folder.uri);
    const persisted = JSON.parse(await fs.readFile(settingsFile, 'utf8')) as Record<string, unknown>;
    for (const [name, value] of Object.entries(values)) {
      const inspection = selected.inspect(name);
      // A single-folder window presents its .vscode settings as workspace
      // settings. The persisted selected-folder file also proves the target.
      assert.equal(inspection?.workspaceFolderValue ?? inspection?.workspaceValue, value);
      assert.equal(selected.get(name), value); assert.equal(persisted[`semanticSearch.${name}`], value);
    }
    checks.normal_folder_settings = true;
    const saved = await vscode.commands.executeCommand<ViewState>('semanticSearch.find', {
      query: 'Find the implementation that strips whitespace and converts an inventory item name to lowercase.'
    });
    assert.equal(saved.phase, 'ready', saved.message); assert.equal(saved.response?.rerank, true);
    assert.equal(saved.response?.rerank_provider, 'qwen'); assert.equal(saved.response?.retrieval, 'hybrid');
    assert.ok(saved.entries.some(entry => entry.result.symbol === 'normalize_item' && entry.saved && entry.staleReason === null));
    assert.ok(saved.entries.some(entry => typeof entry.result.qwen_score === 'number' && Number.isFinite(entry.result.qwen_score)));
    checks.saved_search_uses_configured_qwen = true;

    await selected.update('rerank', false, vscode.ConfigurationTarget.WorkspaceFolder);
    const fast = await vscode.commands.executeCommand<ViewState>('semanticSearch.find', {
      query: 'Find the implementation that computes the numeric difference between an after value and a before value.'
    });
    assert.equal(fast.phase, 'ready', fast.message); assert.equal(fast.response?.rerank, false);
    assert.equal(vscode.workspace.getConfiguration('semanticSearch', folder.uri).get('rerankProvider'), 'qwen');
    assert.ok(fast.entries.every(entry => entry.result.qwen_score === null || entry.result.qwen_score === undefined));
    checks.rerank_off_retains_fast_search = true;

    await selected.update('rerank', true, vscode.ConfigurationTarget.WorkspaceFolder);
    dirty = await vscode.workspace.openTextDocument(vscode.Uri.file(path.join(folder.uri.fsPath, 'src/delta.ts')));
    assert.equal(dirty.isDirty, false);
    const text = 'export function calculate_unsaved_ratio(before: number, after: number): number {\n  return after / before;\n}\n';
    const edit = new vscode.WorkspaceEdit();
    edit.replace(dirty.uri, new vscode.Range(dirty.positionAt(0), dirty.positionAt(dirty.getText().length)), text);
    assert.equal(await vscode.workspace.applyEdit(edit), true); assert.equal(dirty.isDirty, true);
    const overlay = await vscode.commands.executeCommand<ViewState>('semanticSearch.find', {
      query: 'Find the implementation that divides an after value by a before value.', includeUnsaved: true
    });
    assert.equal(overlay.phase, 'ready', overlay.message); assert.equal(overlay.response?.rerank_provider, 'qwen');
    assert.equal(overlay.response?.rerank, true); assert.equal(overlay.response?.scope?.overlays.length, 1);
    const index = overlay.entries.findIndex(entry => entry.result.symbol === 'calculate_unsaved_ratio'); assert.ok(index >= 0);
    const entry = overlay.entries[index]!; assert.ok(entry.overlay && entry.saved); assert.equal(entry.result.source_origin, 'overlay');
    assert.equal(entry.result.buffer_version, dirty.version); assert.equal(entry.result.buffer_hash, hash(text));
    await vscode.commands.executeCommand('semanticSearch.open', index);
    const editor = vscode.window.activeTextEditor; assert.ok(editor); assert.equal(editor.document, dirty);
    const excerpt = physicalLines(dirty.getText()).slice(entry.result.start_line - 1, entry.result.end_line).join('');
    assert.equal(excerpt, entry.result.source); assert.equal(hash(excerpt), entry.result.source_hash);
    checks.dirty_search_uses_configured_qwen_and_same_buffer = true;
    const sourceAfter = Object.fromEntries(await Promise.all(names.map(async name => [name, hash(await fs.readFile(path.join(folder.uri.fsPath, name), 'utf8'))])));
    assert.deepEqual(sourceAfter, sourceBefore); checks.no_indexed_source_saved_or_modified = true;
    Object.assign(record, { source_after_sha256: sourceAfter, saved_provider: saved.response?.rerank_provider,
      dirty_provider: overlay.response?.rerank_provider, buffer_bindings: overlay.response?.scope?.overlays });
  } finally {
    try {
      if (dirty?.isDirty && !dirty.isClosed) {
        await vscode.window.showTextDocument(dirty);
        await vscode.commands.executeCommand('workbench.action.revertAndCloseActiveEditor');
      }
    } finally {
      if (beforeSettings) { await fs.writeFile(settingsFile, beforeSettings); }
      else { await fs.rm(settingsFile, { force: true }); }
      if (!directoryExisted) { await fs.rmdir(settingsDirectory); }
      if (beforeSettings) {
        const restored = await fs.readFile(settingsFile); assert.deepEqual(restored, beforeSettings);
        record.settings_after_sha256 = bytesHash(restored);
      } else {
        await assert.rejects(fs.lstat(settingsFile), { code: 'ENOENT' }); record.settings_after_sha256 = null;
      }
      checks.workspace_settings_restored_exactly = true;
    }
  }
  record.passed = Object.values(checks).every(Boolean); return record;
}
