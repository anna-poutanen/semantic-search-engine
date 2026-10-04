import { test } from 'node:test';
import * as assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import * as os from 'node:os';
import * as path from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
const { runOwnedHost } = require('../../scripts/owned-host.cjs');

test('CPU child ownership is durable and its complete output/group cleanup is observed', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'owned-host-cpu-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const ownershipFile = path.join(directory, 'ownership.json'); const chunks: Buffer[] = [];
  const result = await runOwnedHost(process.execPath, ['-e', 'process.stdout.write("CPU_ONLY");'], {
    ownershipFile, nonce: 'cpu-proof', env: process.env, stdout: (chunk: Buffer) => chunks.push(chunk), stderr: () => undefined,
    timeoutMs: 5000, graceMs: 100, killMs: 1000 });
  assert.equal(result.completed_cleanly, true); assert.equal(result.cleanup_clean, true); assert.equal(result.output_drained, true);
  assert.equal(Buffer.concat(chunks).toString('utf8'), 'CPU_ONLY');
  const owned = JSON.parse(await fs.readFile(ownershipFile, 'utf8'));
  assert.equal(owned.pid, result.pid); assert.equal(owned.process_group, result.pid); assert.equal(owned.nonce, 'cpu-proof');
  assert.equal((await fs.stat(ownershipFile)).mode & 0o777, 0o600);
  assert.equal(JSON.parse(await fs.readFile(ownershipFile + '.stop.json', 'utf8')).cleanup.owned_group_stopped, true);
  assert.throws(() => process.kill(-owned.pid, 0), { code: 'ESRCH' });
});
test('CPU timeout preserves forced cleanup and distinguishes qualified from clean completion', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'owned-host-timeout-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const result = await runOwnedHost(process.execPath, ['-e', 'process.on("SIGTERM",()=>{});setInterval(()=>{},1000);'], {
    ownershipFile: path.join(directory, 'ownership.json'), nonce: 'cpu-timeout', env: process.env,
    stdout: () => undefined, stderr: () => undefined, timeoutMs: 300, graceMs: 100, killMs: 1000 });
  assert.equal(result.timed_out, true); assert.equal(result.cleanup_qualified, true);
  assert.equal(result.cleanup.forced_kill, true); assert.equal(result.cleanup_clean, false); assert.equal(result.completed_cleanly, false);
});
test('CPU controller SIGTERM cleans its durably recorded detached child and restores handlers', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'owned-host-signal-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const helper = path.resolve(__dirname, '../../scripts/owned-host.cjs');
  const source = `const {runOwnedHost}=require(${JSON.stringify(helper)});const before=process.listenerCount('SIGTERM');runOwnedHost(process.execPath,['-e','process.stdout.write("READY");setInterval(()=>{},1000);'],{ownershipFile:process.argv[1],nonce:'signal',env:process.env,stdout:()=>process.kill(process.pid,'SIGTERM'),stderr:()=>{},timeoutMs:5000,graceMs:100,killMs:1000}).then(r=>{if(process.listenerCount('SIGTERM')!==before)throw Error('handler leak');process.stdout.write(JSON.stringify(r));});`;
  const output = await promisify(execFile)(process.execPath, ['-e', source, path.join(directory, 'ownership.json')], { timeout: 6000 });
  const result = JSON.parse(output.stdout); assert.deepEqual(result.interrupted, ['SIGTERM']);
  assert.equal(result.cleanup_qualified, true); assert.equal(result.cleanup.owned_group_stopped, true); assert.equal(result.completed_cleanly, false);
});
test('existing ownership files refuse child creation without changing any bytes', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'owned-host-refusal-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const ownershipFile = path.join(directory, 'ownership.json'); await fs.writeFile(ownershipFile, 'EXISTING');
  await assert.rejects(runOwnedHost(process.execPath, ['-e', 'process.exit(9)'], { ownershipFile, nonce: 'cpu',
    stdout: () => undefined, stderr: () => undefined }), /Existing/);
  assert.equal(await fs.readFile(ownershipFile, 'utf8'), 'EXISTING');
});
