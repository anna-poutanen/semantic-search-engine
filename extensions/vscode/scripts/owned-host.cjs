'use strict';
// Owned detached host capture. Import starts no process and installs no signal handler.
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
function durableNew(filename, value) {
  const descriptor = fs.openSync(filename, 'wx', 0o600);
  try { fs.writeFileSync(descriptor, JSON.stringify(value, null, 2) + '\n'); fs.fsyncSync(descriptor); }
  finally { fs.closeSync(descriptor); }
}
async function stopGroup(pid, { graceMs = 5000, killMs = 5000 } = {}) {
  const receipt = { owned_group: pid, owned_group_stopped: false, forced_kill: false, errors: [] };
  const alive = () => { try { process.kill(-pid, 0); return true; } catch (error) { if (error.code === 'ESRCH') { return false; } throw error; } };
  const send = signal => { try { process.kill(-pid, signal); } catch (error) { if (error.code !== 'ESRCH') { throw error; } } };
  const stopped = async milliseconds => { const end = Date.now() + milliseconds; while (alive() && Date.now() < end) { await wait(20); } return !alive(); };
  try {
    if (alive()) { send('SIGTERM'); if (!await stopped(graceMs)) { receipt.forced_kill = true; send('SIGKILL'); await stopped(killMs); } }
    receipt.owned_group_stopped = !alive();
  } catch (error) { receipt.errors.push({ type: error.name, message: error.message }); }
  return receipt;
}
async function runOwnedHost(executable, args, options) {
  if (!path.isAbsolute(executable) || !path.isAbsolute(options.ownershipFile) || !options.nonce) { throw new Error('Absolute owned host executable/receipt and nonce required'); }
  if (fs.existsSync(options.ownershipFile) || fs.existsSync(options.ownershipFile + '.stop.json')) { throw new Error('Existing owned host receipt refused'); }
  let child, timer, cleanup, resolveExit, interrupted = [], timedOut = false;
  const exit = new Promise(resolve => resolveExit = resolve);
  // Install before spawn. No await separates spawn/ownership publication, so a
  // queued signal cannot abandon a newly created but unrecorded process.
  const stop = () => {
    if (!child?.pid) { return; }
    const stopped = cleanup ??= stopGroup(child.pid, options);
    Promise.resolve(stopped).then(() => {
      if ((timedOut || interrupted.length) && child.exitCode === null && child.signalCode === null) {
        resolveExit({ code: null, signal: null, leader_exit_unproved: true });
      }
    });
    return stopped;
  };
  const handlers = Object.fromEntries(['SIGINT', 'SIGTERM'].map(name => [name, () => { interrupted.push(name); void stop(); }]));
  for (const [name, handler] of Object.entries(handlers)) { process.on(name, handler); }
  const restoreSignals = () => { for (const [name, handler] of Object.entries(handlers)) { process.removeListener(name, handler); } };
  let result;
  try {
    child = spawn(executable, args, { detached: true, env: options.env, stdio: ['ignore', 'pipe', 'pipe'] });
    if (!child.pid) { child.once('error', error => resolveExit({ code: null, signal: null, startup_error: error.message })); }
    else {
      durableNew(options.ownershipFile, { nonce: options.nonce, launcher_pid: process.pid, pid: child.pid, process_group: child.pid,
        executable, argv: args, created_utc: new Date().toISOString(), detached: true });
      child.once('error', error => resolveExit({ code: null, signal: null, startup_error: error.message }));
      child.once('exit', (code, signal) => resolveExit({ code, signal }));
      child.stdout.on('data', options.stdout); child.stderr.on('data', options.stderr);
      timer = setTimeout(() => { timedOut = true; void stop(); }, options.timeoutMs ?? 300000);
      if (interrupted.length) { void stop(); }
    }
    result = await exit;
  } catch (error) {
    result = { code: null, signal: null, startup_error: error.message };
  } finally {
    clearTimeout(timer);
    if (child?.pid) { cleanup = await stop(); }
    const end = Date.now() + (options.drainMs ?? 1000);
    while (child?.stdout && (!child.stdout.readableEnded || !child.stderr.readableEnded) && Date.now() < end) { await wait(20); }
    const drained = child?.stdout?.readableEnded === true && child?.stderr?.readableEnded === true;
    child?.stdout?.destroy(); child?.stderr?.destroy();
    const qualified = cleanup?.owned_group_stopped === true && drained && !result?.startup_error && !result?.leader_exit_unproved;
    result = { ...result, pid: child?.pid ?? null, ownership_file: options.ownershipFile, timed_out: timedOut,
      interrupted, cleanup: cleanup ?? null, output_drained: drained, cleanup_qualified: qualified,
      cleanup_clean: qualified && cleanup.forced_kill === false && cleanup.errors.length === 0,
      completed_cleanly: qualified && cleanup.forced_kill === false && cleanup.errors.length === 0
        && result?.code === 0 && !timedOut && interrupted.length === 0 };
    try { if (child?.pid) { durableNew(options.ownershipFile + '.stop.json', result); } }
    catch (error) { restoreSignals(); throw error; }
    if (!options.deferSignalRestoration) { restoreSignals(); }
  }
  return { ...result, restoreSignals };
}
module.exports = { runOwnedHost, stopGroup };
