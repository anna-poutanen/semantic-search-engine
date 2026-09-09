import { strict as assert } from 'node:assert';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import * as vscode from 'vscode';
import { ViewState } from '../../src/controller';
import { hash } from '../../src/schema';

const execute = promisify(execFile);
export async function verifySavedEdits(repo: string): Promise<Record<string, unknown>> {
  const names = ['src/inventory.py', 'src/decorate.js', 'src/delta.ts'];
  const original = new Map(await Promise.all(names.map(async name => [name, await fs.readFile(path.join(repo, name))] as const)));
  const checks: Record<string, boolean> = {}, observations: Record<string, unknown> = {};
  const renamed = 'src/decorate-renamed.js';
  const git = async (...args: string[]) => (await execute('git', ['-C', repo, ...args])).stdout.trim();
  assert.equal(await git('status', '--porcelain'), '');
  const head = await git('rev-parse', 'HEAD');
  const options = { retrieval: 'dense', rerank: false, max_results: 5, token_budget: 2000, include_dependencies: false };
  const find = async () => {
    const state = await vscode.commands.executeCommand<ViewState>('semanticSearch.find', {
      query: 'Find the implementation that strips whitespace and changes the letter case of a value.', options
    });
    assert.equal(state.phase, 'ready', state.message); assert.ok(state.response); return state;
  };
  const waitManifest = async (previous: string) => {
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
      const state = await vscode.commands.executeCommand<ViewState>('semanticSearch.status');
      if (state.status?.source_manifest !== previous && state.status?.watcher?.running
          && !state.status.last_error && state.status.ready) { return state; }
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw new Error('Saved watcher did not publish the changed source manifest');
  };
  try {
    const initial = await find(), index = initial.entries.findIndex(entry => entry.result.symbol === 'normalize_item');
    assert.ok(index >= 0); const captured = initial.entries[index]!;
    await fs.writeFile(path.join(repo, 'src/inventory.py'), Buffer.concat([original.get('src/inventory.py')!, Buffer.from('\n# saved edit outside the returned implementation\n')]));
    await assert.rejects(async () => await vscode.commands.executeCommand('semanticSearch.open', index), /changed|stale|expired|new search/i);
    const uri = await vscode.commands.executeCommand<vscode.Uri>('semanticSearch.preview', index);
    assert.equal((await vscode.workspace.openTextDocument(uri)).getText(), captured.result.source);
    checks.saved_edit_outside_excerpt_blocks_old_navigation = true;
    const refreshed = await find(); const current = refreshed.entries.find(entry => entry.result.symbol === 'normalize_item');
    assert.ok(current?.saved && captured.saved);
    assert.notEqual(current.saved.fileHash, captured.saved.fileHash);
    assert.equal(current.result.source_hash, captured.result.source_hash);
    checks.new_search_updates_whole_file_witness_without_changing_excerpt = true;
    const beforeManifest = refreshed.response!.indexstats.source_manifest;
    await fs.writeFile(path.join(repo, 'src/delta.ts'), 'export function calculate_saved_delta(before: number, after: number): number {\n  return after - before + 1;\n}\n');
    const observed = await waitManifest(beforeManifest);
    observations.watcher = observed.status?.watcher;
    observations.previous_manifest = beforeManifest; observations.changed_manifest = observed.status?.source_manifest;
    checks.watcher_observes_saved_typescript_before_search = true;
    const semantic = await find(); const target = semantic.entries.findIndex(entry => entry.result.symbol === 'calculate_saved_delta');
    assert.ok(target >= 0); assert.match(semantic.entries[target]!.result.source, /after - before \+ 1/);
    assert.equal(semantic.entries[target]!.staleReason, null);
    await vscode.commands.executeCommand('semanticSearch.open', target);
    assert.equal(vscode.window.activeTextEditor?.document.isDirty, false);
    checks.saved_semantic_edit_has_current_navigation = true;
    await git('mv', 'src/decorate.js', renamed);
    const moved = await find(); assert.ok(moved.entries.some(entry => entry.result.path === renamed));
    assert.ok(moved.entries.every(entry => entry.result.path !== 'src/decorate.js'));
    checks.tracked_rename_updates_paths = true;
    await fs.unlink(path.join(repo, 'src/delta.ts'));
    const deleted = await find(); assert.ok(deleted.entries.every(entry => entry.result.path !== 'src/delta.ts'));
    assert.equal(deleted.response!.indexstats.total_windows, 4); checks.saved_delete_removes_active_windows = true;
    await fs.writeFile(path.join(repo, 'src/inventory.py'), 'def broken(:\n');
    const invalid = await vscode.commands.executeCommand<ViewState>('semanticSearch.find', { query: 'Normalize a value', options });
    assert.equal(invalid.phase, 'error'); assert.equal(invalid.entries.length, 0); assert.equal(invalid.response, null);
    assert.match(invalid.message, /parse|source|syntax|invalid/i); checks.invalid_saved_source_never_delivers_stale_results = true;
  } finally {
    if (await fs.stat(path.join(repo, renamed)).then(() => true, () => false)) { await git('mv', renamed, 'src/decorate.js'); }
    for (const [name, bytes] of original) { await fs.writeFile(path.join(repo, name), bytes); }
  }
  const restored = await find(); assert.equal(restored.response!.indexstats.total_windows, 5);
  assert.equal(await git('rev-parse', 'HEAD'), head); assert.equal(await git('status', '--porcelain'), '');
  checks.tracked_source_and_git_state_restored = true;
  const before = Object.fromEntries([...original].map(([name, bytes]) => [name, hash(bytes.toString('utf8'))]));
  const after = Object.fromEntries(await Promise.all(names.map(async name => [name, hash(await fs.readFile(path.join(repo, name), 'utf8'))])));
  assert.deepEqual(after, before);
  return { passed: Object.values(checks).every(Boolean), checks, observations, source_before_sha256: before, source_after_sha256: after,
    write_policy: 'Controlled edits to the exact clean toy fixture, restored in finally; before/after equality does not imply no intervening writes.',
    limits: ['Watcher observation is distinct from later search snapshot validation.', 'Saved changes are authored fixture integration, not large repository performance.'] };
}
