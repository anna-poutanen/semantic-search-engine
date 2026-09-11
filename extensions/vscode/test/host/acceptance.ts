import { strict as assert } from 'node:assert';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import * as http from 'node:http';
import * as net from 'node:net';
import { createHash } from 'node:crypto';
import * as vscode from 'vscode';
import type { ExtensionApi } from '../../src/extension';
import type { ViewState } from '../../src/controller';
const SOURCE = 'def acceptance_message():\n    return "authored fixture ready"\n';
function required(name: string): string { const value = process.env[name]; if (!value) { throw new Error(`Missing ${name}`); } return value; }
async function limit<T>(operation: PromiseLike<T>, label: string, ms = 20000): Promise<T> {
  let timer: NodeJS.Timeout | undefined;
  try { return await Promise.race([Promise.resolve(operation), new Promise<T>((_, reject) => { timer = setTimeout(() => reject(new Error(`${label} did not settle`)), ms); })]); }
  finally { if (timer) { clearTimeout(timer); } }
}
async function control(endpoint: string, method = 'GET'): Promise<Record<string, any>> {
  const url = new URL(required('SEMANTIC_ACCEPTANCE_URL'));
  return limit(new Promise((resolve, reject) => {
    const request = http.request({ hostname: url.hostname, port: url.port, path: endpoint, method,
      headers: { Host: url.host, 'X-Acceptance-Control': required('SEMANTIC_ACCEPTANCE_CONTROL') }, agent: false }, reply => {
      const buffers: Buffer[] = []; let bytes = 0;
      reply.on('data', (part: Buffer) => { bytes += part.length; if (bytes > 128 * 1024) { request.destroy(new Error('Control response oversized')); } else { buffers.push(part); } });
      reply.on('end', () => { try { assert.equal(reply.statusCode, 200); resolve(JSON.parse(Buffer.concat(buffers).toString('utf8'))); } catch (error) { reject(error); } });
    });
    request.on('error', reject); request.setTimeout(10000, () => request.destroy(new Error('Owned control timeout'))); request.end();
  }), 'owned fixture control');
}
async function eventually(predicate: () => boolean | Promise<boolean>, label: string): Promise<void> {
  const deadline = Date.now() + 15000;
  while (!(await predicate())) { if (Date.now() >= deadline) { throw new Error(`${label} not observed`); } await new Promise(resolve => setTimeout(resolve, 25)); }
}
async function listenerRefused(): Promise<boolean> {
  const url = new URL(required('SEMANTIC_ACCEPTANCE_URL'));
  return new Promise(resolve => {
    const socket = net.createConnection({ host: url.hostname, port: Number(url.port) });
    socket.once('connect', () => { socket.destroy(); resolve(false); });
    socket.once('error', (error: NodeJS.ErrnoException) => { socket.destroy(); resolve(error.code === 'ECONNREFUSED'); });
    socket.setTimeout(1000, () => { socket.destroy(); resolve(false); });
  });
}
async function refuse(action: () => PromiseLike<unknown>, pattern: RegExp): Promise<string> {
  try { await limit(action(), 'guarded command'); } catch (error) {
    const message = error instanceof Error ? error.message : String(error); assert.match(message, pattern); return message;
  }
  throw new Error('Guarded command unexpectedly succeeded');
}
async function changeFolders(start: number, remove: number, additions: { uri: vscode.Uri; name: string }[] = []): Promise<void> {
  let subscription: vscode.Disposable | undefined;
  const changed = new Promise<void>(resolve => { subscription = vscode.workspace.onDidChangeWorkspaceFolders(() => resolve()); });
  try { assert.equal(vscode.workspace.updateWorkspaceFolders(start, remove, ...additions), true); await limit(changed, 'owned workspace folder event'); }
  finally { subscription?.dispose(); }
}
export async function run(): Promise<void> {
  const output = required('SEMANTIC_ACCEPTANCE_RESULT'), mode = required('SEMANTIC_ACCEPTANCE_MODE');
  const record: Record<string, any> = { mode, passed: false, started_utc: new Date().toISOString(), vscode_version: vscode.version,
    observed_trusted: vscode.workspace.isTrusted, host_kind: 'Development Host using frozen installed payload',
    model_loaded: false, ordinary_profile_enablement_tested: false, checks: {} };
  const checks = record.checks as Record<string, boolean>;
  try {
    const a = await fs.realpath(required('SEMANTIC_ACCEPTANCE_A')), b = await fs.realpath(required('SEMANTIC_ACCEPTANCE_B'));
    const folders = vscode.workspace.workspaceFolders; assert.equal(folders?.length, 2);
    assert.equal(await fs.realpath(folders![0]!.uri.fsPath), b); assert.equal(await fs.realpath(folders![1]!.uri.fsPath), a);
    record.initial_workspace_folders = folders!.map(folder => folder.uri.fsPath); checks.two_authored_roots = true;
    assert.equal(vscode.env.remoteName, undefined); assert.equal(vscode.workspace.isTrusted, mode === 'trusted');
    checks.requested_trust_observed_before_commands = true;
    const initial = await control('/__acceptance_receipts'); assert.equal(initial.product_requests, 0);
    checks.no_initial_product_http = true;
    const extension = vscode.extensions.getExtension<ExtensionApi>('anna-poutanen.semantic-search-local'); assert.ok(extension);
    record.extension_path = extension.extensionPath;
    assert.equal(await fs.realpath(extension.extensionPath), await fs.realpath(required('SEMANTIC_ACCEPTANCE_EXTENSION')));
    checks.installed_payload_path = true;
    const api = await limit(extension.activate(), 'installed extension activation'); checks.activation = extension.isActive;
    if (mode === 'restricted') {
      assert.equal(vscode.workspace.isTrusted, false);
      assert.throws(() => api.getState(), /trusted local desktop workspace/); checks.api_guard_refused = true;
      record.connect_refusal = await refuse(() => vscode.commands.executeCommand('semanticSearch.connect', { workspacePath: a, serviceUrl: required('SEMANTIC_ACCEPTANCE_URL') }), /trusted local desktop workspace/);
      record.search_refusal = await refuse(() => vscode.commands.executeCommand('semanticSearch.find', { query: 'authored behavior' }), /trusted local desktop workspace/);
      record.status_refusal = await refuse(() => vscode.commands.executeCommand('semanticSearch.status', {}), /trusted local desktop workspace/);
      checks.product_command_guards_refused = true;
      const receipts = await control('/__acceptance_receipts'); assert.equal(receipts.product_requests, 0);
      checks.no_http_after_restricted_guards = true;
      record.restricted_activation_note = 'The Development Host bypasses ordinary extension enablement; product command/API guards were exercised with actual workspace.isTrusted=false.';
    } else {
      const wrong = await limit(vscode.commands.executeCommand<ViewState>('semanticSearch.connect', { workspacePath: b, serviceUrl: required('SEMANTIC_ACCEPTANCE_URL') }), 'wrong repository connection');
      assert.equal(wrong.phase, 'error'); assert.equal(wrong.status, null); assert.equal(wrong.entries.length, 0); assert.equal(wrong.response, null);
      assert.match(wrong.message, /different|match|repository/i); record.wrong_repository_state = wrong; checks.wrong_repository_refused = true;
      const wrongReceipts = await control('/__acceptance_receipts'); assert.equal(wrongReceipts.records.filter((row: any) => row.endpoint === '/search').length, 0);
      const connect = async () => {
        const state = await limit(vscode.commands.executeCommand<ViewState>('semanticSearch.connect', { workspacePath: a, serviceUrl: required('SEMANTIC_ACCEPTANCE_URL') }), 'correct repository connection');
        assert.equal(state.phase, 'ready'); assert.equal(await fs.realpath(state.status!.repo), a); return state;
      };
      await connect(); checks.correct_repository_connected = true;
      const found = await limit(vscode.commands.executeCommand<ViewState>('semanticSearch.find', { query: 'authored behavior', options: { include_dependencies: false } }), 'authored source search');
      assert.equal(found.phase, 'ready'); assert.equal(found.entries.length, 1); const hit = found.entries[0]!;
      assert.equal(hit.result.source, SOURCE); assert.equal(hit.result.source_hash, createHash('sha256').update(SOURCE).digest('hex'));
      assert.equal(await fs.readFile(path.join(a, hit.result.path), 'utf8'), SOURCE); assert.ok(hit.saved); assert.equal(hit.staleReason, null);
      checks.literal_saved_result = true;
      await changeFolders(1, 1);
      await eventually(() => vscode.workspace.workspaceFolders?.length === 1 && api.getState().phase === 'disconnected', 'connected second folder removal/disconnect');
      const removed = api.getState(); assert.equal(removed.status, null); assert.equal(removed.response, null); assert.equal(removed.entries.length, 0);
      record.after_folder_removal = removed; checks.connected_folder_removal_disconnected = true;
      const beforeRefusals = await control('/__acceptance_receipts');
      record.removed_search_refusal = await refuse(() => vscode.commands.executeCommand('semanticSearch.find', { query: 'after removal' }), /Connect to the selected repository/);
      record.removed_connect_refusal = await refuse(() => vscode.commands.executeCommand('semanticSearch.connect', { workspacePath: a, serviceUrl: required('SEMANTIC_ACCEPTANCE_URL') }), /open local workspace folder/);
      const afterRefusals = await control('/__acceptance_receipts'); assert.equal(afterRefusals.product_requests, beforeRefusals.product_requests);
      checks.no_http_after_removed_folder_commands = true;
      await changeFolders(1, 0, [{ uri: vscode.Uri.file(a), name: 'Indexed authored repository' }]);
      await eventually(() => vscode.workspace.workspaceFolders?.length === 2, 'owned indexed folder restoration'); await connect();
      const stopped = await control('/__acceptance_stop', 'POST'); assert.equal(stopped.closing, true);
      await eventually(listenerRefused, 'owned listener port closed'); checks.owned_listener_refusal_observed = true;
      const offline = await limit(vscode.commands.executeCommand<ViewState>('semanticSearch.status', {}), 'offline status');
      assert.equal(offline.phase, 'error'); assert.match(offline.message, /ECONNREFUSED|connect|socket|service|request/i); record.offline_state = offline;
      const offlineSearch = await limit(vscode.commands.executeCommand<ViewState>('semanticSearch.find', { query: 'offline authored behavior' }), 'offline search');
      assert.equal(offlineSearch.phase, 'error'); assert.equal(offlineSearch.entries.length, 0); assert.equal(offlineSearch.response, null);
      record.offline_search_state = offlineSearch; checks.offline_operations_settled_with_error = true;
    }
    assert.equal(vscode.workspace.isTrusted, mode === 'trusted'); record.passed = true;
  } catch (error) { record.error = error instanceof Error ? error.message : String(error); throw error; }
  finally { record.finished_utc = new Date().toISOString(); await fs.writeFile(output, JSON.stringify(record, null, 2) + '\n'); }
}
