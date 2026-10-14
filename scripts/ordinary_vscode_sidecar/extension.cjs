'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');
const assert = require('node:assert/strict');
const ID = 'anna-poutanen.semantic-search-local';
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
// Synchronous ownership validation precedes every asynchronous operation.
function ownership(env = process.env, pid = process.pid) {
  const root = env.ORDINARY_PROFILE_ROOT, nonce = env.ORDINARY_PROFILE_NONCE;
  assert.ok(path.isAbsolute(root ?? '') && /^[a-f0-9]{48}$/.test(nonce ?? ''));
  const owner = JSON.parse(fs.readFileSync(path.join(root, 'owner.json'), 'utf8'));
  assert.equal(owner.nonce, nonce); assert.equal(fs.realpathSync(root), root);
  const launch = JSON.parse(fs.readFileSync(path.join(root, env.ORDINARY_PROFILE_MODE, 'launch-owner.json'), 'utf8'));
  assert.equal(launch.nonce, nonce); assert.ok(Number.isSafeInteger(launch.pid) && launch.pid > 1);
  assert.equal(launch.pgid, launch.pid);
  const pgid = Number(execFileSync('/bin/ps', ['-o', 'pgid=', '-p', String(pid)], { encoding: 'utf8' }).trim());
  // Electron may create another child group: independently prove an ancestor.
  let current = pid, ancestor = false;
  for (let depth = 0; depth < 20 && current > 1; depth++) {
    if (current === launch.pid) { ancestor = true; break; }
    current = Number(execFileSync('/bin/ps', ['-o', 'ppid=', '-p', String(current)], { encoding: 'utf8' }).trim());
  }
  assert.ok(pgid === launch.pgid || ancestor, 'Sidecar is outside the owned Code ancestry/group');
  const result = env.ORDINARY_PROFILE_RESULT, repository = env.ORDINARY_PROFILE_REPO;
  for (const filename of [result, repository, env.ORDINARY_PROFILE_TARGET]) {
    assert.ok(path.isAbsolute(filename ?? '') && filename.startsWith(root + path.sep), 'Probe path escaped owned root');
  }
  assert.ok(['trusted', 'restricted'].includes(env.ORDINARY_PROFILE_MODE));
  return { root, nonce, result, repository, mode: env.ORDINARY_PROFILE_MODE,
    process_pid: pid, observed_pgid: pgid, owned_code_pid: launch.pid, ancestry_verified: ancestor };
}
async function limit(operation, label, ms = 15000) {
  let timer; try { return await Promise.race([Promise.resolve(operation), new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(label + ' timed out')), ms);
  })]); } finally { clearTimeout(timer); }
}
function denialKind(error, command) {
  const message = String(error.message ?? error);
  if (message === 'Use a trusted local desktop workspace for the local service') { return 'trusted-local-guard'; }
  if (message === `command '${command}' not found` || message === `command "${command}" not found`) { return 'command-unavailable'; }
  return null;
}
function publishProof(filename, row) {
  const temporary = filename + '.publishing';
  const fd = fs.openSync(temporary, 'wx', 0o600);
  try { fs.writeFileSync(fd, JSON.stringify(row, null, 2) + '\n'); fs.fsyncSync(fd); }
  finally { fs.closeSync(fd); }
  // No-replace atomic publication of the complete, flushed proof inode.
  fs.linkSync(temporary, filename); fs.unlinkSync(temporary);
}
function requestQuitAfterProof(vscode, owned) {
  const bytes = fs.readFileSync(owned.result), proof = JSON.parse(bytes);
  assert.equal(proof.nonce, owned.nonce); assert.equal(proof.mode, owned.mode);
  assert.equal(typeof proof.passed, 'boolean');
  const request = {nonce: owned.nonce, command: 'workbench.action.quit', process_pid: owned.process_pid,
    proof_sha256: sha(bytes), after_terminal_proof: true, requested_utc: new Date().toISOString()};
  const filename = path.join(owned.root, owned.mode, 'quit-request.json');
  fs.writeFileSync(filename, JSON.stringify(request, null, 2) + '\n', {flag: 'wx', mode: 0o600});
  return Promise.resolve(vscode.commands.executeCommand(request.command)).catch(error => {
    fs.writeFileSync(path.join(owned.root, owned.mode, 'quit-error.json'), JSON.stringify({nonce: owned.nonce,
      command: request.command, error: String(error.message ?? error), observed_utc: new Date().toISOString()}, null, 2) + '\n', {flag: 'wx', mode: 0o600});
    throw error;
  });
}
async function probe(vscode, owned, env = process.env) {
  const row = { classification: 'Normally installed extensions in an isolated ordinary Code profile; authored HTTP fixture',
    nonce: owned.nonce, mode: owned.mode, passed: false, vscode_version: vscode.version,
    observed_trusted: vscode.workspace.isTrusted, remote_name: vscode.env.remoteName ?? null,
    app_name: vscode.env.appName ?? null, sidecar_extension_mode: owned.extension_mode ?? null,
    development_host_flags: false, model_calls: 0, inference_calls: 0,
    ownership: owned, started_utc: new Date().toISOString(), checks: {} };
  const checks = row.checks;
  try {
    assert.equal(vscode.env.remoteName, undefined);
    assert.equal(vscode.workspace.isTrusted, owned.mode === 'trusted');
    assert.equal(vscode.workspace.workspaceFolders.length, 1);
    assert.equal(fs.realpathSync(vscode.workspace.workspaceFolders[0].uri.fsPath), owned.repository);
    checks.actual_trust_and_folder = true;
    const target = vscode.extensions.getExtension(ID);
    row.extension_available = Boolean(target); row.target_initially_active = target?.isActive ?? false;
    if (owned.mode === 'restricted') {
      const commands = await limit(vscode.commands.getCommands(true), 'command inventory');
      row.product_commands_registered = commands.filter(name => name.startsWith('semanticSearch.'));
      const outcomes = []; row.denials = outcomes;
      for (const [command, argument] of [
        ['semanticSearch.connect', {workspacePath: owned.repository, serviceUrl: env.ORDINARY_PROFILE_URL}],
        ['semanticSearch.find', {query: 'Return the authored local message'}],
        ['semanticSearch.status', {}]]) {
        const observed_trusted_before = vscode.workspace.isTrusted;
        assert.equal(observed_trusted_before, false, 'Restricted trust changed before public command');
        try {
          const value = await limit(vscode.commands.executeCommand(command, argument), command, 8000);
          outcomes.push({command, observed_trusted_before, refused: false, returned: value ?? null});
        } catch (error) {
          const kind = denialKind(error, command);
          outcomes.push({command, observed_trusted_before, refused: kind !== null, denial_kind: kind, error: String(error.message ?? error)});
        }
      }
      row.denials = outcomes;
      assert.ok(outcomes.every(item => item.refused), 'Every guarded command must be unavailable or deny access');
      row.target_active_after_commands = target?.isActive ?? false;
      checks.ordinary_restricted_commands_denied = true;
      // Do not force extension.activate() or override trust/support enablement.
    } else {
      assert.ok(target, 'Product extension absent from ordinary profile');
      assert.equal(fs.realpathSync(target.extensionPath), fs.realpathSync(env.ORDINARY_PROFILE_TARGET));
      row.installed_target = {id: target.id, version: target.packageJSON.version, path: target.extensionPath,
        main: target.packageJSON.main, activation_events: target.packageJSON.activationEvents};
      const state = await limit(vscode.commands.executeCommand('semanticSearch.connect', {
        workspacePath: owned.repository, serviceUrl: env.ORDINARY_PROFILE_URL}), 'connect');
      assert.equal(state.phase, 'ready'); assert.ok(target.isActive);
      row.target_active_after_connect = target.isActive;
      row.activation_transition_observed = !row.target_initially_active && target.isActive;
      checks.public_commands_reached_installed_active_product = true;
      row.default_settings = Object.fromEntries(Object.entries(target.packageJSON.contributes.configuration.properties)
        .map(([name, value]) => [name, value.default]));
      const found = await limit(vscode.commands.executeCommand('semanticSearch.find', {query: 'Return the authored local message'}), 'default search');
      assert.equal(found.phase, 'ready'); assert.equal(found.entries.length, 1);
      const hit = found.entries[0]; assert.equal(hit.result.source_hash, sha(env.ORDINARY_PROFILE_SOURCE));
      assert.equal(hit.result.source, env.ORDINARY_PROFILE_SOURCE); assert.equal(hit.staleReason, null);
      row.default_search = found; checks.literal_default_saved_source = true;
      const preview = await limit(vscode.commands.executeCommand('semanticSearch.preview', 0), 'preview');
      assert.equal(preview.scheme, 'semantic-search-preview');
      assert.equal(vscode.window.activeTextEditor.document.getText(), env.ORDINARY_PROFILE_SOURCE);
      checks.public_preview_navigation = true;
      const opened = await limit(vscode.commands.executeCommand('semanticSearch.open', 0), 'open');
      assert.equal(opened.fsPath, path.join(owned.repository, 'src/ordinary.py'));
      const editor = vscode.window.activeTextEditor;
      assert.equal(editor.document.uri.fsPath, opened.fsPath); assert.equal(editor.document.isDirty, false);
      assert.equal(editor.selection.start.line, 0); assert.equal(editor.selection.end.line, 1);
      row.navigation = {uri: opened.toString(), start_line: editor.selection.start.line + 1, end_line: editor.selection.end.line + 1};
      checks.public_saved_source_navigation = true;
      const status = await limit(vscode.commands.executeCommand('semanticSearch.status', {}), 'status');
      assert.equal(status.phase, 'ready'); assert.equal(status.status.windows, 1); checks.public_status = true;
      const unavailable = await limit(vscode.commands.executeCommand('semanticSearch.find', {query: 'Return the authored local message',
        options: {rerank: true, rerank_provider: 'qwen'}}), 'fixture Qwen provider denial');
      assert.equal(unavailable.phase, 'error'); assert.match(unavailable.message, /Qwen reranking is not configured/);
      assert.match(unavailable.message, /enable-qwen-reranker/);
      row.qwen_fixture_denial = unavailable; checks.explicit_qwen_provider_and_actionable_denial = true;
    }
    assert.equal(vscode.workspace.isTrusted, owned.mode === 'trusted'); row.passed = true;
  } catch (error) { row.error = String(error.message ?? error); }
  finally {
    row.finished_utc = new Date().toISOString();
    publishProof(owned.result, row);
  }
  return row;
}
function activate(context) {
  const owned = ownership(); // No await before ownership is established.
  fs.writeFileSync(path.join(owned.root, owned.mode, 'sidecar-owner.json'), JSON.stringify(owned, null, 2) + '\n', {flag: 'wx', mode: 0o600});
  const vscode = require('vscode');
  assert.equal(context.extensionMode, vscode.ExtensionMode.Production, 'Sidecar must be normally installed in production mode');
  owned.extension_mode = context.extensionMode;
  void probe(vscode, owned).then(() => requestQuitAfterProof(vscode, owned)).catch(error => { console.error('Ordinary profile probe failed:', error.message); });
}
module.exports = {activate, ownership, probe, limit, denialKind, publishProof, requestQuitAfterProof};
