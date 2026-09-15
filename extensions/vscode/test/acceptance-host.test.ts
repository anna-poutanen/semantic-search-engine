import { strict as assert } from 'node:assert';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import * as os from 'node:os';
import * as http from 'node:http';
import { createHash } from 'node:crypto';
import { test, TestContext } from 'node:test';
import { parseSearch, parseStatus, defaultOptions } from '../src/schema';
const suite = require('../../scripts/acceptance-host.cjs');
const config = require('../../scripts/host-config.cjs');
const sha = (data: Buffer | string): string => createHash('sha256').update(data).digest('hex');
async function temporary(t: TestContext): Promise<string> {
  const directory = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'editor-acceptance-cpu-')));
  t.after(() => fs.rm(directory, { recursive: true, force: true })); return directory;
}
async function request(url: string, endpoint: string, method = 'GET', body?: unknown, token?: string, origin?: string, host?: string): Promise<{ status: number; body: any }> {
  const address = new URL(url);
  return new Promise((resolve, reject) => {
    const headers: Record<string, string> = { Host: host ?? address.host };
    if (token) { headers['X-Acceptance-Control'] = token; } if (origin) { headers.Origin = origin; }
    const call = http.request({ hostname: address.hostname, port: address.port, path: endpoint, method, headers, agent: false }, reply => {
      const buffers: Buffer[] = []; reply.on('data', (part: Buffer) => buffers.push(part));
      reply.on('end', () => { try { resolve({ status: reply.statusCode!, body: JSON.parse(Buffer.concat(buffers).toString('utf8')) }); } catch (error) { reject(error); } });
    });
    call.on('error', reject); call.setTimeout(2000, () => call.destroy(new Error('CPU fixture timeout')));
    call.end(body === undefined ? undefined : JSON.stringify(body));
  });
}
async function harness(t: TestContext, mode = 'trusted'): Promise<Record<string, any>> {
  const directory = await temporary(t), raw = Buffer.from('fake VSIX, no native host or model');
  const manifest = JSON.parse(await fs.readFile(path.join(config.root, 'package.json'), 'utf8'));
  const archive = { sha256: sha(raw), bytes: raw.length, manifest, vsix_manifest: Buffer.from('<VSIX/>'), payload: {
    'package.json': Buffer.from(JSON.stringify(manifest)), 'dist/src/extension.js': Buffer.from('exports.activate = () => {};'),
  } };
  const vsix = path.join(directory, 'fixture.vsix'); await fs.writeFile(vsix, raw); await fs.writeFile(path.join(directory, 'neighbor.txt'), 'untouched');
  const state: Record<string, any> = { directory, archive, calls: [], mode, service: undefined,
    options: { run: true, mode, vsix, 'expected-sha256': archive.sha256, output: path.join(directory, 'result.json') } };
  state.dependencies = {
    readVsix: async () => archive,
    prepareHost: async () => ({ executable: '/fake/Code', record: { fake: true, model_loaded: false } }),
    createRunDirectory: async () => { state.owned = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'ssaccept-cpu-'))); return state.owned; },
    resolveCli: () => ['/fake/code'],
    createRepositories: (owned: string) => suite.createRepositories(owned, async () => ({ code: 0 })),
    startFixtureService: async (repository: string) => { state.service = await suite.startFixtureService(repository); return state.service; },
    runCommand: async (command: string, args: string[], options: Record<string, any>) => {
      state.calls.push(command);
      if (command === '/fake/code') {
        const extensions = args.find(value => value.startsWith('--extensions-dir='))!.slice('--extensions-dir='.length);
        assert.equal(path.dirname(extensions), state.owned); const installed = path.join(extensions, `${manifest.publisher}.${manifest.name}-${manifest.version}`);
        for (const [name, data] of Object.entries(archive.payload)) {
          await fs.mkdir(path.dirname(path.join(installed, name)), { recursive: true }); await fs.writeFile(path.join(installed, name), data as Buffer);
        }
        await fs.writeFile(path.join(installed, '.vsixmanifest'), archive.vsix_manifest);
        await fs.writeFile(path.join(installed, 'package.json'), JSON.stringify({ ...manifest, __metadata: { fixture: true } }));
      } else {
        assert.equal(command, '/fake/Code'); assert.equal(options.timeoutMs, 175000);
        assert.ok(args.some(value => value.startsWith('--extensionDevelopmentPath=')));
        assert.equal(args.includes('--disable-workspace-trust'), mode === 'trusted');
        assert.equal(options.env.SEMANTIC_ACCEPTANCE_MODE, mode);
        if (mode === 'trusted') {
          await request(state.service.url, '/status'); await request(state.service.url, '/search', 'POST', { ...defaultOptions, query: 'authored behavior' });
          await request(state.service.url, '/__acceptance_stop', 'POST', undefined, state.service.token);
        }
        await fs.writeFile(options.env.SEMANTIC_ACCEPTANCE_RESULT, JSON.stringify({ passed: true, observed_trusted: mode === 'trusted',
          checks: { cpu_fake_not_editor: true }, extension_path: options.env.SEMANTIC_ACCEPTANCE_EXTENSION }));
      }
      return { code: 0, signal: null, stdout: Buffer.from('fake only'), stderr: Buffer.alloc(0), owned_process_group_stopped: true };
    },
  }; return state;
}
test('acceptance requires explicit run/exact mode/frozen VSIX and has no external backend options', () => {
  const args = ['--run', '--mode', 'restricted', '--vsix', '/local.vsix', '--expected-sha256', 'a'.repeat(64), '--output', '/out'];
  assert.equal(suite.argumentsFrom(args).mode, 'restricted'); assert.throws(() => suite.argumentsFrom(args.slice(1)), /Explicit/);
  assert.throws(() => suite.argumentsFrom([...args, '--url', 'http://127.0.0.1:1']), /Unknown/);
  assert.throws(() => suite.argumentsFrom([...args, '--mode', 'trusted']), /Duplicate/);
  const bad = [...args]; bad[bad.indexOf('--mode') + 1] = 'override'; assert.throws(() => suite.argumentsFrom(bad), /trusted or restricted/);
});
test('restricted settings/arguments preserve trust; only trusted mode gets explicit disable flag', () => {
  const restricted = suite.launchArguments('restricted', '/owned/workspace', '/owned/installed', '/owned/user', '/owned/extensions');
  const trusted = suite.launchArguments('trusted', '/owned/workspace', '/owned/installed', '/owned/user', '/owned/extensions');
  assert.equal(restricted.includes('--disable-workspace-trust'), false); assert.equal(trusted.includes('--disable-workspace-trust'), true);
  assert.ok(restricted.includes('--extensionDevelopmentPath=/owned/installed'));
  assert.equal(suite.settings('restricted')['security.workspace.trust.enabled'], true);
  assert.equal(suite.settings('restricted')['security.workspace.trust.startupPrompt'], 'never');
  assert.equal(Object.hasOwn(suite.settings('restricted'), 'extensions.supportUntrustedWorkspaces'), false);
});
test('two owned fixtures use exact literal bytes and stable first-root order without user git config', async t => {
  const root = await temporary(t), commands: string[][] = [];
  const repo = await suite.createRepositories(root, async (command: string, args: string[], options: Record<string, any>) => {
    commands.push([command, ...args]); assert.equal(options.env.GIT_CONFIG_GLOBAL, '/dev/null'); assert.equal(options.env.GIT_CONFIG_NOSYSTEM, '1');
    assert.equal(Object.hasOwn(options.env, 'GIT_DIR'), false); return { code: 0 }; });
  assert.deepEqual(JSON.parse(await fs.readFile(repo.workspaceFile, 'utf8')).folders.map((item: any) => item.path), [repo.b, repo.a]);
  assert.equal(await fs.readFile(path.join(repo.a, 'src/acceptance.py'), 'utf8'), suite.SOURCE);
  assert.equal(await fs.readFile(path.join(repo.b, 'src/acceptance.py'), 'utf8'), suite.SOURCE);
  assert.equal(commands.length, 4); assert.ok(commands.every(args => args[0] === 'git' && ['init', '-C'].includes(args[1]!)));
  assert.ok(commands.filter(args => args[1] === 'init').every(args => args.includes(`--template=${path.join(root, 'empty-git-template')}`)));
});
test('real CPU Git preparation tracks only authored source without inherited templates/hooks', { skip: process.platform === 'win32' }, async t => {
  const root = await temporary(t), repositories = await suite.createRepositories(root);
  const { runCommand } = require('../../scripts/package-host.cjs');
  for (const repository of [repositories.a, repositories.b]) {
    const files = await runCommand('git', ['-C', repository, 'ls-files', '-z']);
    assert.equal(files.code, 0); assert.equal(files.stdout.toString('utf8'), 'src/acceptance.py\0');
    await assert.rejects(fs.lstat(path.join(repository, '.git/hooks')), { code: 'ENOENT' });
  }
});
test('authored status/search satisfy frozen frontend schema and hash exact two physical lines', () => {
  assert.equal(parseStatus(suite.status('/owned/a')).repo, '/owned/a');
  const result = parseSearch(suite.searchResponse('/owned/a', { ...defaultOptions, query: 'fixture' }), 'fixture', defaultOptions);
  assert.equal(result.results[0]?.source, suite.SOURCE); assert.equal(result.results[0]?.source_hash, sha(suite.SOURCE));
  assert.equal(result.total_source_chars, suite.SOURCE.length); assert.equal(result.results[0]?.window_end_line, 2);
  assert.throws(() => suite.searchResponse('/owned/a', { ...defaultOptions, query: 'fixture', overlays: [{}] }), /saved-source/);
});
test('real owned fixture listener separates product/control receipts, requires capability and closes offline', async t => {
  const root = await temporary(t), service = await suite.startFixtureService(root); t.after(() => service.close());
  assert.equal((await request(service.url, '/__acceptance_receipts')).status, 403);
  assert.equal((await request(service.url, '/status', 'GET', undefined, undefined, 'https://example.invalid')).status, 403);
  assert.equal((await request(service.url, '/status', 'GET', undefined, undefined, undefined, 'bad-host.invalid')).status, 403);
  assert.equal((await request(service.url, '/__acceptance_receipts', 'GET', undefined, service.token)).body.product_requests, 3);
  assert.equal((await request(service.url, '/status')).body.repo, root);
  const found = await request(service.url, '/search', 'POST', { ...defaultOptions, query: 'fixture' }); assert.equal(found.status, 200);
  assert.equal(found.body.results[0].source, suite.SOURCE);
  const receipts = await request(service.url, '/__acceptance_receipts', 'GET', undefined, service.token); assert.equal(receipts.body.product_requests, 5);
  assert.equal(receipts.body.records.filter((row: any) => row.status === 403).length, 3);
  assert.ok(!JSON.stringify(receipts.body).includes(service.token));
  assert.equal((await request(service.url, '/__acceptance_stop', 'POST', undefined, service.token)).body.closing, true);
  assert.equal(service.closed, true); await assert.rejects(request(service.url, '/status'), (error: any) => error.code === 'ECONNREFUSED');
});
test('fake trusted and restricted launches bind installed payload, keep sources and clean owned roots', async t => {
  for (const mode of ['trusted', 'restricted']) {
    const state = await harness(t, mode), record = await suite.acceptanceHost(state.options, state.dependencies);
    assert.equal(record.passed, true); assert.equal(record.model_loaded, false); assert.equal(record.source_unchanged, true);
    assert.equal(record.owned_listener_closed, true); assert.equal(record.owned_profile_removed, true);
    assert.equal(record.fixture_requests.length, mode === 'trusted' ? 2 : 0); assert.deepEqual(state.calls, ['/fake/code', '/fake/Code']);
    await assert.rejects(fs.lstat(state.owned), { code: 'ENOENT' }); assert.equal(await fs.readFile(path.join(state.directory, 'neighbor.txt'), 'utf8'), 'untouched');
    const published = await fs.readFile(state.options.output, 'utf8'); assert.ok(!published.includes(state.owned));
    assert.ok(!published.includes(state.service.token)); assert.ok(published.includes('<acceptance-run>'));
  }
});
test('wrong observed trust or restricted product HTTP fails and still closes only owned service/profile', async t => {
  for (const cause of ['trusted', 'http']) {
    const state = await harness(t, 'restricted'), original = state.dependencies.runCommand;
    state.dependencies.runCommand = async (...args: any[]) => {
      const result = await original(...args);
      if (args[0] === '/fake/Code') {
        if (cause === 'http') { await request(state.service.url, '/status'); }
        if (cause === 'trusted') { const output = args[2].env.SEMANTIC_ACCEPTANCE_RESULT;
          const host = JSON.parse(await fs.readFile(output, 'utf8')); host.observed_trusted = true; await fs.writeFile(output, JSON.stringify(host)); }
      }
      return result;
    };
    const record = await suite.acceptanceHost(state.options, state.dependencies);
    assert.equal(record.passed, false); assert.match(record.error, /requested gates/); assert.equal(record.owned_listener_closed, true);
    await assert.rejects(fs.lstat(state.owned), { code: 'ENOENT' });
  }
});
test('failed installation or modified runtime cannot reach fixture server/host', async t => {
  for (const cause of ['install', 'bytes']) {
    const state = await harness(t), original = state.dependencies.runCommand;
    state.dependencies.runCommand = async (...args: any[]) => {
      const result = await original(...args);
      if (cause === 'install') { return { ...result, code: 1 }; }
      const extensions = args[1].find((value: string) => value.startsWith('--extensions-dir=')).slice('--extensions-dir='.length);
      await fs.writeFile(path.join(extensions, (await fs.readdir(extensions))[0]!, 'dist/src/extension.js'), 'changed'); return result;
    };
    const record = await suite.acceptanceHost(state.options, state.dependencies);
    assert.equal(record.passed, false); assert.equal(state.service, undefined); assert.deepEqual(state.calls, ['/fake/code']);
    await assert.rejects(fs.lstat(state.owned), { code: 'ENOENT' });
  }
});
test('receipt overwrite and wrong frozen SHA refuse before any preparation/install', async t => {
  const state = await harness(t); state.options['expected-sha256'] = 'b'.repeat(64);
  await assert.rejects(suite.acceptanceHost(state.options, state.dependencies), /hash mismatch/); assert.deepEqual(state.calls, []);
  state.options['expected-sha256'] = state.archive.sha256; await fs.writeFile(state.options.output, 'existing');
  await assert.rejects(suite.acceptanceHost(state.options, state.dependencies), /Refusing/); assert.deepEqual(state.calls, []);
});
