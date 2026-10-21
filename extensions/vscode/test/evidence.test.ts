import { test } from 'node:test';
import * as assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import * as os from 'node:os';
import * as path from 'node:path';
import { bindRepository, verifySavedResult } from '../src/evidence';
import { result, source } from './fixtures';
import { hash, parseResult } from '../src/schema';

async function repo(): Promise<string> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'semantic-editor-'));
  await fs.mkdir(path.join(root, 'src')); await fs.writeFile(path.join(root, 'src/app.py'), source); return root;
}
test('canonical repository matching accepts aliases and rejects the other multi-root repository', async () => {
  const root = await repo(), other = await repo();
  try {
    const alias = path.join(other, 'alias'); await fs.symlink(root, alias, 'dir');
    assert.equal((await bindRepository(alias, root, 'src')).root, await fs.realpath(root));
    await assert.rejects(bindRepository(root, other, 'src'), /does not match/);
    await assert.rejects(bindRepository(root, root, '../'), /inside/);
  } finally { await fs.rm(root, { recursive: true }); await fs.rm(other, { recursive: true }); }
});
test('saved source and full window hashes are checked; changes outside the excerpt still expire a captured proof', async () => {
  const root = await repo();
  try {
    const binding = await bindRepository(root, root, 'src');
    const proof = await verifySavedResult(binding, result());
    await fs.writeFile(path.join(root, 'src/app.py'), source + '\n# unrelated saved edit\n');
    await assert.rejects(verifySavedResult(binding, result(), proof), /changed after/);
    const fresh = await verifySavedResult(binding, result()); assert.notEqual(fresh.fileHash, proof.fileHash);
    await fs.writeFile(path.join(root, 'src/app.py'), source.replace('ready', 'edited'));
    await assert.rejects(verifySavedResult(binding, result()), /no longer matches/);
  } finally { await fs.rm(root, { recursive: true }); }
});
test('symlinks and traversal cannot navigate outside repository or configured source root', async () => {
  const root = await repo(), outside = await repo();
  try {
    const binding = await bindRepository(root, root, 'src');
    await fs.symlink(path.join(outside, 'src/app.py'), path.join(root, 'src/external.py'));
    await assert.rejects(verifySavedResult(binding, { ...result(), path: 'src/external.py' }), /outside/);
    await fs.mkdir(path.join(root, 'tests')); await fs.writeFile(path.join(root, 'tests/app.py'), source);
    await fs.symlink(path.join(root, 'tests/app.py'), path.join(root, 'src/other-scope.py'));
    await assert.rejects(verifySavedResult(binding, { ...result(), path: 'src/other-scope.py' }), /outside/);
    await assert.rejects(verifySavedResult(binding, { ...result(), path: '../outside.py' }), /inside/);
    await fs.symlink(path.join(root, 'src/app.py'), path.join(root, 'src/inside.py'));
    assert.ok((await verifySavedResult(binding, { ...result(), path: 'src/inside.py' })).fileHash);
  } finally { await fs.rm(root, { recursive: true }); await fs.rm(outside, { recursive: true }); }
});
test('CRLF, Unicode separators, UTF-8 BOM, Latin-1 and truncated full-window evidence preserve physical lines', async () => {
  const root = await repo();
  try {
    const binding = await bindRepository(root, root, 'src');
    const crlf = 'def implementation():\r\n    return "\u2028"\r\n';
    await fs.writeFile(path.join(root, 'src/app.py'), Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), Buffer.from(crlf)]));
    await verifySavedResult(binding, parseResult(result(crlf)));
    const latin = '# coding: latin-1\ndef implementation():\n    return "café"\n';
    await fs.writeFile(path.join(root, 'src/app.py'), Buffer.from(latin, 'latin1'));
    const full = latin.slice(latin.indexOf('def implementation'));
    await verifySavedResult(binding, { ...result(full), start_line: 2, end_line: 3, window_end_line: 3 });
    await fs.writeFile(path.join(root, 'src/app.py'), source);
    const first = 'def implementation():\n';
    const partial = { ...result(first), end_line: 1, window_end_line: 2, source_hash: hash(first), content_hash: hash(source), truncated: true };
    await verifySavedResult(binding, parseResult(partial));
    await assert.rejects(verifySavedResult(binding, { ...partial, content_hash: '0'.repeat(64) }), /no longer matches/);
  } finally { await fs.rm(root, { recursive: true }); }
});
test('Python-produced composite windows preserve strict parsing and saved full-window verification', async () => {
  const fixturePath = path.resolve(__dirname, '../../../../tests/fixtures/window_union.json');
  const fixture = JSON.parse(await fs.readFile(fixturePath, 'utf8'));
  const root = await repo();
  try {
    await fs.writeFile(path.join(root, 'src/code.py'), fixture.source);
    const binding = await bindRepository(root, root, 'src');
    for (const value of [fixture.full, fixture.truncated]) {
      const parsed = parseResult(value);
      assert.match(parsed.id, /^context:connected_windows:/);
      assert.equal(parsed.window_end_line, 6);
      await verifySavedResult(binding, parsed);
      assert.equal('assembly' in parsed, false);
      const counterfeited = { ...parsed, content_hash: value.assembly.members[0].content_hash };
      await assert.rejects(verifySavedResult(binding, counterfeited), /no longer matches/);
    }
  } finally { await fs.rm(root, { recursive: true }); }
});
