'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const smoke = require('../extensions/vscode/scripts/smoke-host.cjs');
const acceptance = require('../extensions/vscode/scripts/acceptance-host.cjs');
const packaged = require('../extensions/vscode/scripts/package-host.cjs');
const config = require('../extensions/vscode/scripts/host-config.cjs');
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
test('future smoke host isolates all three storage roots in owned profile without changing Dev Host/trust scope', () => {
  const argv = smoke.launchArguments('/fixture', '/installed', '/owned/run/user-data', '/owned/run/extensions');
  assert.ok(argv.includes('--user-data-dir=/owned/run/user-data')); assert.ok(argv.includes('--extensions-dir=/owned/run/extensions'));
  assert.ok(argv.includes('--shared-data-dir=/owned/run/shared-data')); assert.ok(argv.includes('--extensionDevelopmentPath=/installed'));
  assert.ok(argv.includes('--disable-workspace-trust'));
});
test('future trusted/restricted acceptance launches use owned shared storage and preserve trust distinction', () => {
  for (const mode of ['trusted', 'restricted']) {
    const argv = acceptance.launchArguments(mode, '/fixture.code-workspace', '/installed', '/owned/run/user-data', '/owned/run/extensions');
    assert.ok(argv.includes('--shared-data-dir=/owned/run/shared-data'));
    assert.equal(argv.includes('--disable-workspace-trust'), mode === 'trusted');
    assert.ok(argv.includes('--extensionDevelopmentPath=/installed'));
  }
});
async function fixture(t, kind, prefix = []) {
  const outer = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'shared-host-cpu-')));
  t.after(() => fs.rm(outer, {recursive: true, force: true}));
  const owned = path.join(outer, 'owned'); await fs.mkdir(owned);
  const manifest = JSON.parse(await fs.readFile(path.join(config.root, 'package.json'))), raw = Buffer.from('CPU fake archive, never installed by Code');
  const archive = {sha256: sha(raw), bytes: raw.length, manifest, vsix_manifest: Buffer.from('fake container'),
    payload: {'package.json': Buffer.from(JSON.stringify(manifest)), 'dist/src/extension.js': Buffer.from('fake CPU bytes')}};
  const vsix = path.join(outer, 'fake.vsix'); await fs.writeFile(vsix, raw);
  const calls = [], options = {run: true, vsix, 'expected-sha256': archive.sha256, output: path.join(outer, 'result.json'),
    ...(kind === 'acceptance' ? {mode: 'trusted'} : {repo: outer, url: 'http://127.0.0.1:1'})};
  const dependencies = {readVsix: async () => archive, prepareHost: async () => ({executable: '/fake/Code', record: {cpu_fake: true}}),
    createRunDirectory: async () => owned, resolveCli: () => ['/fake/code', ...prefix],
    runCommand: async (command, argv, extra) => {
      calls.push({command, argv});
      const shared = argv.find(arg => arg.startsWith('--shared-data-dir='));
      assert.equal(shared, '--shared-data-dir=' + path.join(owned, 'shared-data'));
      if (command === '/fake/code') {
        const directory = path.join(owned, 'extensions', manifest.publisher + '.' + manifest.name + '-' + manifest.version);
        for (const [name, bytes] of Object.entries(archive.payload)) { await fs.mkdir(path.dirname(path.join(directory, name)), {recursive: true}); await fs.writeFile(path.join(directory, name), bytes); }
        await fs.writeFile(path.join(directory, '.vsixmanifest'), archive.vsix_manifest);
      } else {
        assert.equal(command, '/fake/Code');
        await fs.writeFile(extra.env.SEMANTIC_ACCEPTANCE_RESULT, JSON.stringify({passed: true, observed_trusted: true}));
      }
      return {code: 0, signal: null, stdout: Buffer.from('CPU fake'), stderr: Buffer.alloc(0), owned_process_group_stopped: true};
    },
    smoke: async request => { calls.push({command: 'fake-smoke', request}); await fs.writeFile(request.output, JSON.stringify({passed: true, host: {checks: {packaged_extension_path: true}}})); },
    createRepositories: directory => acceptance.createRepositories(directory, async () => ({code: 0})),
    startFixtureService: async () => ({url: 'http://127.0.0.1:1', token: 'CPU fake', records: [], controls: [], requested_stop: true, closed: true, close: async () => {}})};
  return {outer, owned, calls, options, dependencies};
}
for (const kind of ['acceptance', 'package']) {
  test('future ' + kind + ' installation routes pass owned shared-data flag before fake launch', async t => {
    const f = await fixture(t, kind);
    const row = await (kind === 'acceptance' ? acceptance.acceptanceHost : packaged.packageHost)(f.options, f.dependencies);
    assert.equal(row.passed, true); assert.equal(f.calls[0].command, '/fake/code');
    assert.ok(f.calls[0].argv.includes('--shared-data-dir=' + path.join(f.owned, 'shared-data')));
    assert.equal(f.calls.length, 2); await assert.rejects(fs.lstat(f.owned), {code: 'ENOENT'});
  });
  for (const prefix of [['--shared-data-dir=/foreign/user-storage'], ['--shared-data-dir', '/foreign/user-storage']]) {
    test('future ' + kind + ' rejects SDK shared-data override before any fake native command: ' + prefix[0], async t => {
      const f = await fixture(t, kind, prefix);
      const row = await (kind === 'acceptance' ? acceptance.acceptanceHost : packaged.packageHost)(f.options, f.dependencies);
      assert.equal(row.passed, false); assert.match(row.error, /CLI profile/); assert.deepEqual(f.calls, []);
      assert.equal(row.owned_profile_removed, true);
    });
  }
}
