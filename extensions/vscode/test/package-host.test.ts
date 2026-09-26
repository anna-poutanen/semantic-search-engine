import { strict as assert } from 'node:assert';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import * as os from 'node:os';
import { createHash } from 'node:crypto';
import { test, TestContext } from 'node:test';
const { argumentsFrom, cliEnvironment, validateManifest, verifyInstalled, readVsix, runCommand, packageHost } = require('../../scripts/package-host.cjs');
const config = require('../../scripts/host-config.cjs');
const sha = (bytes: Buffer): string => createHash('sha256').update(bytes).digest('hex');
type Archive = { manifest: Record<string, any>; payload: Record<string, Buffer>; vsix_manifest: Buffer; sha256: string; bytes: number };
type Fixture = { outer: string; options: Record<string, any>; archive: Archive; dependencies: Record<string, any>; calls: string[]; owned?: string };
async function writePayload(directory: string, archive: Archive): Promise<void> {
  for (const [name, bytes] of Object.entries(archive.payload)) {
    await fs.mkdir(path.dirname(path.join(directory, name)), { recursive: true }); await fs.writeFile(path.join(directory, name), bytes);
  }
  await fs.writeFile(path.join(directory, '.vsixmanifest'), archive.vsix_manifest);
  await fs.writeFile(path.join(directory, 'package.json'), JSON.stringify({ ...archive.manifest, __metadata: { installedTimestamp: 1 } }));
}
async function fixture(t: TestContext): Promise<Fixture> {
  const outer = await fs.mkdtemp(path.join(os.tmpdir(), 'package-host-cpu-'));
  t.after(() => fs.rm(outer, { recursive: true, force: true }));
  const manifest = JSON.parse(await fs.readFile(path.join(config.root, 'package.json'), 'utf8'));
  const bytes = Buffer.from('CPU fake VSIX; never passed to real installer');
  const archive: Archive = { manifest, sha256: sha(bytes), bytes: bytes.length, vsix_manifest: Buffer.from('<PackageManifest/>'), payload: {
    'package.json': Buffer.from(JSON.stringify(manifest)), 'dist/src/extension.js': Buffer.from("'use strict'; exports.activate = () => {};\n"),
    'dist/src/extension.js.map': Buffer.from('{"version":3,"sources":["src/extension.ts"]}'), 'README.md': Buffer.from('CPU fixture only.\n'),
  } };
  const options = { run: true, vsix: path.join(outer, 'local.vsix'), 'expected-sha256': archive.sha256,
    repo: path.join(outer, 'repo'), output: path.join(outer, 'receipt.json'), url: 'http://127.0.0.1:1', overlays: true };
  await fs.writeFile(options.vsix, bytes); await fs.mkdir(options.repo); await fs.writeFile(path.join(outer, 'neighbor.txt'), 'unrelated');
  const f: Fixture = { outer, options, archive, dependencies: {}, calls: [] };
  f.dependencies = {
    readVsix: async () => { f.calls.push('archive'); return archive; },
    prepareHost: async () => { f.calls.push('prepare'); return { executable: '/fake/Code', record: { prepared: true, network: false } }; },
    createRunDirectory: async () => { f.owned = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'sspackage-cpu-'))); return f.owned; },
    resolveCli: (executable: string, settings: Record<string, any>) => {
      assert.equal(executable, '/fake/Code'); assert.deepEqual(settings, { reuseMachineInstall: true }); return ['/fake/code'];
    },
    runCommand: async (command: string, args: string[], settings: Record<string, any>) => {
      f.calls.push('install'); assert.equal(command, '/fake/code'); assert.equal(settings.timeoutMs, 60000);
      const extensions = args.find(arg => arg.startsWith('--extensions-dir='))!.slice('--extensions-dir='.length);
      const userData = args.find(arg => arg.startsWith('--user-data-dir='))!.slice('--user-data-dir='.length);
      assert.equal(path.dirname(extensions), f.owned); assert.equal(path.dirname(userData), f.owned);
      assert.deepEqual(args.slice(-4), ['--install-extension', options.vsix, '--force', '--do-not-sync']);
      await writePayload(path.join(extensions, `${manifest.publisher}.${manifest.name}-${manifest.version}`), archive);
      return { code: 0, signal: null, stdout: Buffer.from(`Installed ${options.vsix} into ${extensions}`), stderr: Buffer.alloc(0), stdout_bytes: 20, stderr_bytes: 0 };
    },
    smoke: async (settings: Record<string, any>) => {
      f.calls.push('smoke'); assert.equal(path.dirname(settings.extensionPath), path.join(f.owned!, 'extensions'));
      assert.equal(settings.repo, options.repo); assert.equal(settings.overlays, true);
      assert.equal(await fs.readFile(path.join(settings.extensionPath, 'dist/src/extension.js'), 'utf8'), archive.payload['dist/src/extension.js']!.toString('utf8'));
      await fs.writeFile(settings.output, JSON.stringify({ passed: true, host: { extension_path: settings.extensionPath,
        expected_extension_path: settings.extensionPath, checks: { packaged_extension_path: true } } }));
    },
  }; return f;
}
const expected = (a: Archive): Record<string, string> => ({ id: `${a.manifest.publisher}.${a.manifest.name}`, version: a.manifest.version });
async function clean(f: Fixture): Promise<void> {
  assert.ok(f.owned); await assert.rejects(fs.lstat(f.owned!), { code: 'ENOENT' });
  assert.equal(await fs.readFile(path.join(f.outer, 'neighbor.txt'), 'utf8'), 'unrelated');
}
test('package launch requires explicit run/frozen SHA/absolute paths and loopback only', () => {
  const args = ['--run', '--vsix', '/local.vsix', '--expected-sha256', 'a'.repeat(64), '--repo', '/fixture', '--url', 'http://127.0.0.1:1', '--output', '/out'];
  assert.equal(argumentsFrom(args).run, true); assert.throws(() => argumentsFrom(args.slice(1)), /Explicit --run/);
  assert.throws(() => argumentsFrom([...args, '--url', 'http://127.0.0.1:2']), /Duplicate/);
  assert.throws(() => argumentsFrom([...args, '--unknown', 'x']), /Unknown/);
  for (const url of ['https://127.0.0.1:1', 'http://example.invalid', 'http://localhost/private', 'http://u:p@localhost/', 'http://localhost/?secret=x']) {
    const changed = [...args]; changed[changed.indexOf('--url') + 1] = url; assert.throws(() => argumentsFrom(changed));
  }
  const relative = [...args]; relative[relative.indexOf('--vsix') + 1] = 'publisher.marketplace-id'; assert.throws(() => argumentsFrom(relative), /Absolute/);
});
test('Qwen package flag reaches the isolated host and rejects mixed smoke modes before install', async t => {
  const args = ['--run', '--vsix', '/local.vsix', '--expected-sha256', 'a'.repeat(64), '--repo', '/fixture', '--url', 'http://127.0.0.1:1', '--output', '/out'];
  assert.equal(argumentsFrom([...args, '--qwen-reranker']).qwenReranker, true);
  for (const mode of ['--overlays', '--saved-edits']) { assert.throws(() => argumentsFrom([...args, '--qwen-reranker', mode]), /separate/); }
  const f = await fixture(t); f.options.qwenReranker = true;
  await assert.rejects(packageHost(f.options, f.dependencies), /separate/); assert.equal(f.calls.length, 0);
  f.options.overlays = false;
  f.dependencies.smoke = async (settings: Record<string, any>) => {
    assert.equal(settings.qwenReranker, true); assert.equal(settings.overlays, false);
    await fs.writeFile(settings.output, JSON.stringify({ passed: true, host: { checks: { packaged_extension_path: true } } }));
    f.calls.push('qwen-smoke');
  };
  const receipt = await packageHost(f.options, f.dependencies); assert.equal(receipt.passed, true);
  assert.ok(f.calls.includes('qwen-smoke')); await clean(f);
});
test('CLI removes running-editor routing and node injection but retains normal home/auth', () => {
  assert.deepEqual(cliEnvironment({ HOME: '/user', CODEX_HOME: '/normal', PATH: '/bin', VSCODE_IPC_HOOK_CLI: '/socket',
    VSCODE_CWD: '/private', ELECTRON_RUN_AS_NODE: '1', NODE_OPTIONS: '--require private.js' }), { HOME: '/user', CODEX_HOME: '/normal', PATH: '/bin' });
});
test('manifest rejects wrong identity/version/main and requests for marketplace dependencies', async t => {
  const f = await fixture(t); validateManifest(f.archive.manifest, expected(f.archive));
  for (const change of [{ publisher: 'other' }, { version: '9.9.9' }, { main: './other.js' }, { extensionDependencies: ['other.ext'] }, { extensionPack: ['other.ext'] }]) {
    assert.throws(() => validateManifest({ ...f.archive.manifest, ...change }, expected(f.archive)));
  }
});
test('fake CLI installation passes exact payload to host, records all hashes and cleans owned profile', async t => {
  const f = await fixture(t), record = await packageHost(f.options, f.dependencies);
  assert.equal(record.passed, true); assert.equal(record.vsix_unchanged, true); assert.equal(record.owned_profile_removed, true);
  assert.deepEqual(f.calls, ['archive', 'prepare', 'install', 'smoke']);
  assert.equal(record.payload_before_host.manifest.installer_metadata_present, true);
  assert.deepEqual(Object.keys(record.payload_before_host.files).sort(), [...Object.keys(f.archive.payload), '.vsixmanifest'].sort());
  assert.deepEqual(record.payload_before_host.manifest.normalized, record.payload_before_host.manifest.archive_normalized);
  assert.equal(record.payload_before_host.container_metadata['.vsixmanifest'].sha256, sha(f.archive.vsix_manifest));
  assert.deepEqual(Object.keys(record.payload_before_host.compiled_files).sort(), ['dist/src/extension.js', 'dist/src/extension.js.map']);
  assert.equal(record.payload_before_host.compiled_files['dist/src/extension.js'].sha256, sha(f.archive.payload['dist/src/extension.js']!));
  assert.deepEqual(record.payload_before_host, record.payload_after_host);
  const publicText = await fs.readFile(f.options.output, 'utf8'); assert.ok(publicText.includes('<package-run>/extensions/'));
  assert.ok(publicText.includes('<release-vsix>')); assert.ok(!publicText.includes(f.owned!)); assert.ok(!publicText.includes(f.options.vsix));
  assert.ok(!publicText.includes(f.options.repo)); assert.match(record.classification, /Development Host/); await clean(f);
});
test('archive SHA or manifest mismatch stops before preparing/installing any host', async t => {
  const f = await fixture(t); f.options['expected-sha256'] = 'b'.repeat(64);
  await assert.rejects(packageHost(f.options, f.dependencies), /frozen SHA/); assert.deepEqual(f.calls, ['archive']);
  f.options['expected-sha256'] = f.archive.sha256; f.archive.manifest.version = 'incorrect';
  await assert.rejects(packageHost(f.options, f.dependencies), /ID\/version/); assert.equal(f.calls.includes('prepare'), false);
});
test('existing output including a symlink is refused before installation', async t => {
  const f = await fixture(t); await fs.symlink(path.join(f.outer, 'neighbor.txt'), f.options.output);
  await assert.rejects(packageHost(f.options, f.dependencies), /Refusing to overwrite/); assert.deepEqual(f.calls, []);
  assert.equal(await fs.readFile(path.join(f.outer, 'neighbor.txt'), 'utf8'), 'unrelated');
});
test('SDK profile arguments or failed CLI install stop before host and clean owned directories', async t => {
  for (const cause of ['profile', 'install']) {
    const f = await fixture(t);
    if (cause === 'profile') { f.dependencies.resolveCli = () => ['/fake/code', '--user-data-dir=/user']; }
    else { f.dependencies.runCommand = async () => { f.calls.push('install'); return { code: 1, signal: null,
      stdout: Buffer.alloc(0), stderr: Buffer.from('failure'), stdout_bytes: 0, stderr_bytes: 7 }; }; }
    const record = await packageHost(f.options, f.dependencies); assert.equal(record.passed, false);
    assert.equal(f.calls.includes('smoke'), false); await clean(f);
  }
});
test('corrupt installed bytes fail before the host while preserving a failed receipt', async t => {
  const f = await fixture(t), install = f.dependencies.runCommand;
  f.dependencies.runCommand = async (...args: any[]) => { const receipt = await install(...args);
    const extensions = args[1].find((arg: string) => arg.startsWith('--extensions-dir=')).slice('--extensions-dir='.length);
    await fs.writeFile(path.join(extensions, (await fs.readdir(extensions))[0]!, 'dist/src/extension.js'), 'wrong'); return receipt; };
  const record = await packageHost(f.options, f.dependencies); assert.equal(record.passed, false);
  assert.match(record.error, /payload bytes differ/); assert.equal(f.calls.includes('smoke'), false); await clean(f);
});
test('installed manifest permits only installer metadata and refuses extra/missing/symlink members', async t => {
  const f = await fixture(t), directory = path.join(f.outer, 'installed'); await writePayload(directory, f.archive);
  await verifyInstalled(directory, f.archive, expected(f.archive));
  await fs.writeFile(path.join(directory, 'extra.txt'), 'unexpected'); await assert.rejects(verifyInstalled(directory, f.archive, expected(f.archive)), /membership/);
  await fs.unlink(path.join(directory, 'extra.txt')); await fs.unlink(path.join(directory, 'README.md'));
  await assert.rejects(verifyInstalled(directory, f.archive, expected(f.archive)), /membership/);
  await fs.symlink(path.join(f.outer, 'neighbor.txt'), path.join(directory, 'README.md'));
  await assert.rejects(verifyInstalled(directory, f.archive, expected(f.archive)), /symlinks/); await fs.unlink(path.join(directory, 'README.md'));
  await fs.writeFile(path.join(directory, 'README.md'), f.archive.payload['README.md']!);
  await fs.writeFile(path.join(directory, 'package.json'), JSON.stringify({ ...f.archive.manifest, privateModification: true }));
  await assert.rejects(verifyInstalled(directory, f.archive, expected(f.archive)), /changed beyond/);
});
test('only exact frozen .vsixmanifest is admitted, with mismatch inventory retained before refusal', async t => {
  for (const cause of ['container-bytes', 'missing-container', 'runtime-extra']) {
    const f = await fixture(t), install = f.dependencies.runCommand;
    f.dependencies.runCommand = async (...args: any[]) => {
      const receipt = await install(...args);
      const extensions = args[1].find((arg: string) => arg.startsWith('--extensions-dir=')).slice('--extensions-dir='.length);
      const installed = path.join(extensions, (await fs.readdir(extensions))[0]!);
      if (cause === 'container-bytes') { await fs.writeFile(path.join(installed, '.vsixmanifest'), 'different metadata'); }
      if (cause === 'missing-container') { await fs.unlink(path.join(installed, '.vsixmanifest')); }
      if (cause === 'runtime-extra') { await fs.writeFile(path.join(installed, 'dist/src/injected.js'), 'unexpected executable'); }
      return receipt;
    };
    const record = await packageHost(f.options, f.dependencies);
    assert.equal(record.passed, false); assert.equal(f.calls.includes('smoke'), false);
    assert.ok(record.expected_installed_inventory['.vsixmanifest']);
    assert.ok(record.installed_inventory_before_validation['dist/src/extension.js']);
    const publicRecord = JSON.parse(await fs.readFile(f.options.output, 'utf8'));
    assert.deepEqual(publicRecord.installed_inventory_before_validation, { ...record.installed_inventory_before_validation });
    await clean(f);
  }
});
test('host must prove installed path; changed compiled bytes or archive fail after host', async t => {
  for (const cause of ['missing-path', 'modified-code', 'modified-vsix']) {
    const f = await fixture(t), smoke = f.dependencies.smoke;
    f.dependencies.smoke = async (options: Record<string, any>) => { await smoke(options);
      if (cause === 'missing-path') { await fs.writeFile(options.output, JSON.stringify({ passed: true, host: { checks: {} } })); }
      if (cause === 'modified-code') { await fs.writeFile(path.join(options.extensionPath, 'dist/src/extension.js'), 'changed'); }
      if (cause === 'modified-vsix') { await fs.writeFile(f.options.vsix, 'changed'); }
    };
    const record = await packageHost(f.options, f.dependencies); assert.equal(record.passed, false);
    assert.match(record.error, cause === 'missing-path' ? /path/ : cause === 'modified-code' ? /payload bytes differ/ : /VSIX changed/); await clean(f);
  }
});
test('bounded command runner records output and refuses timeout/overflow without a model or host', async () => {
  const result = await runCommand(process.execPath, ['-e', 'process.stdout.write("CPU"); process.stderr.write("only");'], { timeoutMs: 5000 });
  assert.equal(result.code, 0); assert.equal(result.stdout.toString('utf8'), 'CPU'); assert.equal(result.stderr_bytes, 4);
  await assert.rejects(runCommand(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { timeoutMs: 100 }), /bounded timeout/);
  await assert.rejects(runCommand(process.execPath, ['-e', 'process.stdout.write("0123456789");'], { timeoutMs: 5000, limit: 2 }), /output cap/);
});
test('command cleanup escalates a same-group descendant that ignores TERM after its leader exits', { skip: process.platform === 'win32' }, async t => {
  const outer = await fs.mkdtemp(path.join(os.tmpdir(), 'package-descendant-cpu-'));
  t.after(() => fs.rm(outer, { recursive: true, force: true }));
  const pidFile = path.join(outer, 'descendant.pid'); let descendant: number | undefined;
  const childSource = 'process.on("SIGTERM", () => {}); require("node:fs").writeFileSync(process.argv[1], String(process.pid)); setInterval(() => {}, 1000);';
  const parentSource = 'const fs=require("node:fs"); require("node:child_process").spawn(process.execPath,["-e",process.argv[1],process.argv[2]],{stdio:"ignore"}); const timer=setInterval(()=>{if(fs.existsSync(process.argv[2])){clearInterval(timer);process.exit(0);}},10);';
  try {
    const result = await runCommand(process.execPath, ['-e', parentSource, childSource, pidFile], { timeoutMs: 5000 });
    descendant = Number(await fs.readFile(pidFile, 'utf8')); assert.ok(descendant > 0);
    assert.equal(result.code, 0); assert.equal(result.owned_process_group_stopped, true);
    assert.throws(() => process.kill(descendant!, 0), (error: any) => error.code === 'ESRCH');
  } finally {
    if (!descendant) { try { descendant = Number(await fs.readFile(pidFile, 'utf8')); } catch {} }
    if (descendant) { try { process.kill(descendant, 'SIGKILL'); } catch {} }
  }
});
// A minimal stored ZIP exercises real byte extraction without a VS Code installation.
function zip(members: [string, Buffer][]): Buffer {
  const locals: Buffer[] = [], centrals: Buffer[] = []; let offset = 0;
  const crc32 = (bytes: Buffer): number => { let crc = 0xffffffff;
    for (const byte of bytes) { crc ^= byte; for (let bit = 0; bit < 8; bit++) { crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0); } } return (crc ^ 0xffffffff) >>> 0; };
  for (const [name, bytes] of members) {
    const names = Buffer.from(name), local = Buffer.alloc(30), central = Buffer.alloc(46), crc = crc32(bytes);
    local.writeUInt32LE(0x04034b50); local.writeUInt16LE(20, 4); local.writeUInt32LE(crc, 14); local.writeUInt32LE(bytes.length, 18);
    local.writeUInt32LE(bytes.length, 22); local.writeUInt16LE(names.length, 26);
    central.writeUInt32LE(0x02014b50); central.writeUInt16LE(20, 4); central.writeUInt16LE(20, 6); central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(bytes.length, 20); central.writeUInt32LE(bytes.length, 24); central.writeUInt16LE(names.length, 28); central.writeUInt32LE(offset, 42);
    locals.push(local, names, bytes); centrals.push(central, names); offset += local.length + names.length + bytes.length;
  }
  const centralBytes = Buffer.concat(centrals), end = Buffer.alloc(22); end.writeUInt32LE(0x06054b50); end.writeUInt16LE(members.length, 8);
  end.writeUInt16LE(members.length, 10); end.writeUInt32LE(centralBytes.length, 12); end.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, centralBytes, end]);
}
test('CPU archive reader binds all ZIP bytes and rejects duplicate/unsafe paths and symlink archive', async t => {
  try { await fs.access('/usr/bin/unzip'); } catch { t.skip('CPU unzip reader unavailable'); return; }
  const f = await fixture(t), members: [string, Buffer][] = [['extension.vsixmanifest', f.archive.vsix_manifest],
    ...Object.entries(f.archive.payload).map(([name, bytes]): [string, Buffer] => [`extension/${name}`, bytes])];
  const target = path.join(f.outer, 'reader.vsix'); await fs.writeFile(target, zip(members));
  const archive = await readVsix(target); assert.equal(archive.sha256, sha(await fs.readFile(target)));
  assert.deepEqual(Object.keys(archive.payload).sort(), Object.keys(f.archive.payload).sort());
  assert.deepEqual(archive.vsix_manifest, f.archive.vsix_manifest);
  for (const [name, bytes] of Object.entries(f.archive.payload)) { assert.deepEqual(archive.payload[name], bytes); }
  await fs.writeFile(target, zip([...members, members[0]!])); await assert.rejects(readVsix(target), /Duplicate/);
  await fs.writeFile(target, zip([...members, ['extension/../outside.txt', Buffer.from('unsafe')]])); await assert.rejects(readVsix(target), /Unsafe/);
  await fs.unlink(target); await fs.symlink(f.options.vsix, target); await assert.rejects(readVsix(target), /regular file/);
});
