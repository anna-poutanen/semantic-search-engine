'use strict';
// Explicit local VSIX installation into owned temporary profiles. Reuses the
// Development Host smoke; this does not activate a user's ordinary installation.
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const config = require('./host-config.cjs');
const { redact, createRunDirectory } = require('./smoke-host.cjs');
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const MAX_BYTES = 8 * 1024 * 1024;

function assert(ok, message) { if (!ok) { throw new Error(message); } }
function validateOptions(options) {
  assert(options.run === true, 'Explicit --run is required for isolated local package installation and Electron');
  for (const key of ['vsix', 'repo', 'output']) { assert(options[key] && path.isAbsolute(options[key]), 'Absolute VSIX/fixture/output paths are required'); }
  assert(options.url && /^[0-9a-f]{64}$/.test(options['expected-sha256']), 'Loopback URL and frozen SHA-256 are required');
  const url = new URL(options.url);
  assert(url.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
    && !url.username && !url.password && !url.search && !url.hash && url.pathname === '/', 'Explicit loopback HTTP endpoint required');
  assert(!options.qwenReranker || !options.overlays && !options.savedEdits, 'Qwen settings smoke is separate from overlay/saved-edit modes');
  return options;
}
function argumentsFrom(argv) {
  assert(argv.includes('--run'), 'Explicit --run is required for isolated local package installation and Electron');
  const options = { run: true };
  for (let index = 0; index < argv.length; index++) {
    const flag = argv[index]; if (flag === '--run') { continue; }
    if (flag === '--overlays' || flag === '--saved-edits' || flag === '--qwen-reranker') {
      const key = flag === '--overlays' ? 'overlays' : flag === '--saved-edits' ? 'savedEdits' : 'qwenReranker';
      assert(!options[key], 'Duplicate package-host flag'); options[key] = true; continue;
    }
    assert(['--vsix', '--expected-sha256', '--repo', '--url', '--output'].includes(flag) && argv[index + 1], 'Unknown or incomplete package-host argument');
    const key = flag.slice(2); assert(!Object.hasOwn(options, key), 'Duplicate package-host argument'); options[key] = argv[++index];
  }
  return validateOptions(options);
}
function cliEnvironment(base) {
  return Object.fromEntries(Object.entries(base).filter(([name]) => !name.startsWith('VSCODE_')
    && !['ELECTRON_RUN_AS_NODE', 'NODE_OPTIONS'].includes(name)));
}
async function cleanupGroup(pid, graceMs = 300, killMs = 1500) {
  if (!pid) { return; }
  const alive = () => {
    try { process.kill(-pid, 0); return true; }
    catch (error) { if (error.code === 'ESRCH') { return false; } throw error; }
  };
  const send = signal => { try { process.kill(-pid, signal); } catch (error) { if (error.code !== 'ESRCH') { throw error; } } };
  const wait = async milliseconds => {
    const deadline = Date.now() + milliseconds;
    while (alive() && Date.now() < deadline) { await new Promise(resolve => setTimeout(resolve, 20)); }
    return !alive();
  };
  send('SIGTERM'); if (await wait(graceMs)) { return; }
  send('SIGKILL'); assert(await wait(killMs), 'Owned CLI process group did not terminate after escalation');
}
async function runCommand(command, args, options = {}) {
  const timeoutMs = options.timeoutMs ?? 60000; const limit = options.limit ?? MAX_BYTES;
  let child, timer, killTimer, timedOut = false, oversized = false;
  const stdout = [], stderr = []; let outBytes = 0, errBytes = 0;
  const stop = signal => { if (child?.pid) { try { process.kill(-child.pid, signal); } catch {} } };
  const terminate = () => { stop('SIGTERM'); if (!killTimer) { killTimer = setTimeout(() => stop('SIGKILL'), 300); } };
  try {
    const ended = await new Promise((resolve, reject) => {
      child = spawn(command, args, { cwd: options.cwd ?? config.root, env: options.env ?? process.env,
        detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
      timer = setTimeout(() => { timedOut = true; terminate(); }, timeoutMs);
      const receive = (buffers, key) => bytes => {
        if (key === 'out') { outBytes += bytes.length; } else { errBytes += bytes.length; }
        if ((key === 'out' ? outBytes : errBytes) <= limit) { buffers.push(bytes); }
        else { oversized = true; terminate(); }
      };
      child.stdout.on('data', receive(stdout, 'out')); child.stderr.on('data', receive(stderr, 'err'));
      child.once('error', reject); child.once('close', (code, signal) => resolve({ code, signal }));
    });
    assert(!timedOut && !oversized, 'Owned CLI process exceeded its bounded timeout/output cap');
    return { ...ended, stdout: Buffer.concat(stdout), stderr: Buffer.concat(stderr), stdout_bytes: outBytes, stderr_bytes: errBytes,
      owned_process_group_stopped: true };
  } finally { clearTimeout(timer); clearTimeout(killTimer); await cleanupGroup(child?.pid); }
}
function validateManifest(manifest, expected) {
  assert(manifest && typeof manifest === 'object' && !Array.isArray(manifest), 'Invalid package manifest');
  assert(`${manifest.publisher}.${manifest.name}` === expected.id && manifest.version === expected.version, 'Extension ID/version differs from reviewed package');
  assert(manifest.main === './dist/src/extension.js', 'Extension entry point differs from reviewed package');
  assert(!manifest.extensionDependencies?.length && !manifest.extensionPack?.length, 'Package must not request other marketplace extensions');
}
async function readVsix(vsix) {
  const stat = await fs.lstat(vsix); assert(stat.isFile() && !stat.isSymbolicLink() && stat.size <= MAX_BYTES, 'VSIX must be a bounded regular file');
  const bytes = await fs.readFile(vsix);
  const listing = await runCommand('/usr/bin/unzip', ['-Z1', vsix], { timeoutMs: 10000, limit: 1024 * 1024 });
  assert(listing.code === 0, 'Cannot read VSIX directory');
  const members = listing.stdout.toString('utf8').split('\n').filter(Boolean);
  assert(new Set(members).size === members.length && members.length <= 256, 'Duplicate/excessive VSIX members');
  assert(members.every(name => ['extension.vsixmanifest', '[Content_Types].xml'].includes(name)
    || /^extension\/[A-Za-z0-9_.\/-]+$/.test(name) && !name.split('/').includes('..') && !name.includes('//')), 'Unsafe/unknown VSIX member path');
  assert(members.includes('extension.vsixmanifest'), 'VSIX omitted container manifest');
  const payload = Object.create(null); let total = 0, vsixManifest;
  for (const name of members.filter(name => name === 'extension.vsixmanifest' || name.startsWith('extension/') && !name.endsWith('/'))) {
    const read = await runCommand('/usr/bin/unzip', ['-p', vsix, name], { timeoutMs: 10000, limit: MAX_BYTES });
    assert(read.code === 0, 'Cannot read VSIX member'); total += read.stdout.length;
    assert(total <= MAX_BYTES, 'Uncompressed package exceeds bounded fixture limit');
    if (name === 'extension.vsixmanifest') { vsixManifest = read.stdout; }
    else { payload[name.slice('extension/'.length)] = read.stdout; }
  }
  assert(Object.hasOwn(payload, 'package.json'), 'VSIX omitted package manifest');
  assert(!Object.hasOwn(payload, '.vsixmanifest'), 'VSIX payload collides with its extracted container manifest');
  assert(sha(await fs.readFile(vsix)) === sha(bytes), 'VSIX changed while reading its members');
  return { sha256: sha(bytes), bytes: bytes.length, payload, vsix_manifest: vsixManifest,
    manifest: JSON.parse(payload['package.json'].toString('utf8')) };
}
async function fileInventory(directory) {
  const files = Object.create(null);
  async function visit(current) {
    for (const name of await fs.readdir(current)) {
      const full = path.join(current, name), stat = await fs.lstat(full);
      assert(!stat.isSymbolicLink(), 'Installed payload must not contain symlinks');
      if (stat.isDirectory()) { await visit(full); }
      else { assert(stat.isFile(), 'Unexpected installed file type'); const bytes = await fs.readFile(full);
        files[path.relative(directory, full).split(path.sep).join('/')] = { sha256: sha(bytes), bytes: bytes.length }; }
    }
  }
  await visit(directory); return files;
}
function canonical(value) {
  if (Array.isArray(value)) { return value.map(canonical); }
  if (value && typeof value === 'object') { return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])); }
  return value;
}
function installedMembers(archive) {
  assert(Buffer.isBuffer(archive.vsix_manifest) && !Object.hasOwn(archive.payload, '.vsixmanifest'), 'Expected frozen container manifest bytes');
  // Pinned VS Code extracts with /^extension/, so the top-level container
  // extension.vsixmanifest becomes .vsixmanifest. Bind only its exact bytes.
  return { ...archive.payload, '.vsixmanifest': archive.vsix_manifest };
}
function memberInventory(members) {
  return Object.fromEntries(Object.entries(members).map(([name, bytes]) => [name, { sha256: sha(bytes), bytes: bytes.length }]));
}
async function verifyInstalled(directory, archive, expected) {
  const info = await fs.lstat(directory); assert(info.isDirectory() && !info.isSymbolicLink(), 'Installed extension is not an owned ordinary directory');
  const installed = JSON.parse(await fs.readFile(path.join(directory, 'package.json'), 'utf8'));
  validateManifest(installed, expected);
  const normalized = { ...installed }; delete normalized.__metadata;
  assert(JSON.stringify(canonical(normalized)) === JSON.stringify(canonical(archive.manifest)), 'Installed manifest changed beyond installer metadata');
  const actual = await fileInventory(directory), members = installedMembers(archive), expectedNames = Object.keys(members).sort();
  assert(JSON.stringify(Object.keys(actual).sort()) === JSON.stringify(expectedNames), 'Installed payload membership differs from VSIX');
  for (const name of expectedNames.filter(name => name !== 'package.json')) {
    assert(actual[name].sha256 === sha(members[name]) && actual[name].bytes === members[name].length, `Installed payload bytes differ: ${name}`);
  }
  return { manifest: { id: expected.id, version: installed.version, main: installed.main,
                      sha256: actual['package.json'].sha256, archive_sha256: sha(archive.payload['package.json']),
                      matches_except_installer_metadata: true, installer_metadata_present: Object.hasOwn(installed, '__metadata'),
                      normalized: canonical(normalized), archive_normalized: canonical(archive.manifest) },
           container_metadata: { '.vsixmanifest': { archive_member: 'extension.vsixmanifest', ...actual['.vsixmanifest'], matches_exact_bytes: true } },
           files: actual, compiled_files: Object.fromEntries(Object.entries(actual).filter(([name]) => name.startsWith('dist/src/'))) };
}
async function packageHost(options, dependencies = {}) {
  validateOptions(options);
  try { await fs.lstat(options.output); throw new Error('Refusing to overwrite an existing package-host receipt'); }
  catch (error) { if (error.code !== 'ENOENT') { throw error; } }
  const expectedPackage = JSON.parse(await fs.readFile(path.join(config.root, 'package.json'), 'utf8'));
  const expected = { id: `${expectedPackage.publisher}.${expectedPackage.name}`, version: expectedPackage.version };
  const archive = await (dependencies.readVsix ?? readVsix)(options.vsix);
  assert(archive.sha256 === options['expected-sha256'], 'VSIX differs from frozen SHA-256'); validateManifest(archive.manifest, expected);
  const prepared = await (dependencies.prepareHost ?? require('./prepare-host.cjs').prepareHost)();
  const directory = await (dependencies.createRunDirectory ?? createRunDirectory)();
  const userData = path.join(directory, 'user-data'), extensions = path.join(directory, 'extensions');
  const record = { classification: 'Isolated local VSIX install then Extension Development Host using installed payload bytes',
    started_utc: new Date().toISOString(), passed: false, vsix: { path: options.vsix, sha256: archive.sha256, bytes: archive.bytes },
    expected_extension: expected, host_preparation: prepared.record,
    limits: ['This remains an Extension Development Host; ordinary user-profile activation is not established.',
      'Only the caller-provided local VSIX is requested; no marketplace extension ID or extension dependency is installed.',
      'Owned user-data/extensions directories are removed; no user VS Code installation/configuration is modified.'] };
  const smokeOutput = path.join(directory, 'smoke.json');
  try {
    await fs.mkdir(path.join(userData, 'User'), { recursive: true }); await fs.mkdir(extensions);
    await fs.writeFile(path.join(userData, 'User/settings.json'), JSON.stringify({ 'telemetry.telemetryLevel': 'off',
      'extensions.autoCheckUpdates': false, 'extensions.autoUpdate': false, 'update.mode': 'none' }));
    if (!dependencies.resolveCli) {
      assert(require('@vscode/test-electron/package.json').version === config.test_electron_version, 'Host SDK differs from its reviewed pin');
    }
    record.test_electron_version = config.test_electron_version;
    const resolveCli = dependencies.resolveCli ?? require('@vscode/test-electron').resolveCliArgsFromVSCodeExecutablePath;
    // Skip SDK default cache profiles; explicit owned directories are mandatory.
    const [cli, ...prefix] = resolveCli(prepared.executable, { reuseMachineInstall: true });
    assert(path.isAbsolute(cli) && !prefix.some(arg => arg.startsWith('--user-data-dir') || arg.startsWith('--extensions-dir') || arg.startsWith('--shared-data-dir')), 'Unexpected SDK CLI profile arguments');
    const args = [...prefix, `--user-data-dir=${userData}`, `--extensions-dir=${extensions}`, `--shared-data-dir=${path.join(directory, 'shared-data')}`,
      '--install-extension', options.vsix, '--force', '--do-not-sync'];
    record.install_command = [cli, ...args];
    const installed = await (dependencies.runCommand ?? runCommand)(cli, args, { env: cliEnvironment(process.env), timeoutMs: 60000, limit: 256 * 1024 });
    record.install_receipt = { code: installed.code, signal: installed.signal, stdout: installed.stdout.toString('utf8'),
      stderr: installed.stderr.toString('utf8'), stdout_bytes: installed.stdout_bytes, stderr_bytes: installed.stderr_bytes,
      owned_process_group_stopped: installed.owned_process_group_stopped ?? null };
    assert(installed.code === 0, 'Owned local VSIX installation failed');
    const candidates = [];
    for (const name of await fs.readdir(extensions)) {
      const candidate = path.join(extensions, name); const info = await fs.lstat(candidate);
      if (info.isDirectory() && !info.isSymbolicLink()) { candidates.push(candidate); }
    }
    assert(candidates.length === 1, 'Expected exactly one installed extension directory');
    const extensionPath = await fs.realpath(candidates[0]);
    assert(path.dirname(extensionPath) === await fs.realpath(extensions), 'Installed extension escaped its owned directory');
    record.installed_extension_path = extensionPath;
    // Keep exact names/hashes even when the strict gate refuses this install.
    record.expected_installed_inventory = memberInventory(installedMembers(archive));
    record.installed_inventory_before_validation = await fileInventory(extensionPath);
    record.payload_before_host = await verifyInstalled(extensionPath, archive, expected);
    await (dependencies.smoke ?? require('./smoke-host.cjs').smoke)({ repo: options.repo, url: options.url,
      output: smokeOutput, extensionPath, overlays: options.overlays === true, savedEdits: options.savedEdits === true,
      qwenReranker: options.qwenReranker === true });
    record.smoke = JSON.parse(await fs.readFile(smokeOutput, 'utf8'));
    assert(record.smoke.passed === true && record.smoke.host?.checks?.packaged_extension_path === true,
      'Host did not confirm installed package activation/path');
    record.payload_after_host = await verifyInstalled(extensionPath, archive, expected);
    assert(JSON.stringify(record.payload_before_host.compiled_files) === JSON.stringify(record.payload_after_host.compiled_files), 'Compiled package bytes changed during host run');
    record.vsix_unchanged = sha(await fs.readFile(options.vsix)) === archive.sha256;
    assert(record.vsix_unchanged, 'Frozen VSIX changed during package check'); record.passed = true;
  } catch (error) { record.error = error.message; if (!dependencies.smoke) { process.exitCode = 1; } }
  finally {
    record.finished_utc = new Date().toISOString();
    await fs.rm(directory, { recursive: true, force: true }); record.owned_profile_removed = true;
    const publicRecord = redact(record, [[options.repo, '<fixture>'], [directory, '<package-run>'],
      [options.vsix, '<release-vsix>'], [config.root, '<extension-source>'], [os.homedir(), '<home>']]);
    await fs.mkdir(path.dirname(options.output), { recursive: true });
    await fs.writeFile(options.output, JSON.stringify(publicRecord, null, 2) + '\n');
  }
  return record;
}
module.exports = { argumentsFrom, cliEnvironment, runCommand, validateManifest, readVsix, verifyInstalled, fileInventory, packageHost };
if (require.main === module) { packageHost(argumentsFrom(process.argv.slice(2))).then(record => {
  console.log(JSON.stringify({ passed: record.passed, output: process.argv[process.argv.indexOf('--output') + 1], vsix_sha256: record.vsix.sha256 }));
}).catch(error => { console.error(error.message); process.exitCode = 1; }); }
