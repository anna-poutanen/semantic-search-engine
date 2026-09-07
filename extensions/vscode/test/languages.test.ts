import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import * as os from 'node:os';
import { defaultOptions, hash, parseSearch, physicalLines, Result } from '../src/schema';
import { bindRepository, verifySavedResult } from '../src/evidence';
import { decodeSavedSource, editorBom, sourceTextFromEditor, sourceTextFromPreview, supportedExtensions, supportedSourcePath } from '../src/source';
import { response } from './fixtures';

function result(filename: string, source: string, start = 1, end = physicalLines(source).length): Result {
  const text = physicalLines(source).slice(start - 1, end).join('');
  return { id: `${filename}:run:${start}-${end}`, path: filename, symbol: 'run', source: text,
    start_line: start, end_line: end, window_end_line: end, source_hash: hash(text), content_hash: hash(text),
    retrieval_score: 1, h2o_score: null, origin: 'retrieval', related_to: null, truncated: false };
}
async function repo(t: { after(fn: () => Promise<void>): void }): Promise<string> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'editor-language-cpu-'));
  await fs.mkdir(path.join(root, 'src'));
  t.after(() => fs.rm(root, { recursive: true, force: true })); return root;
}
test('result schema accepts only explicit saved-language suffixes and keeps arbitrary future metadata', () => {
  for (const extension of supportedExtensions) {
    const filename = 'src/code' + extension;
    assert.equal(supportedSourcePath(filename), true);
    const row = result(filename, extension === '.py' ? 'def run():\n    return 1\n' : 'function run(){\nreturn 1;\n}\n');
    const raw = { ...response('/repo'), results: [{ ...row, language_metadata: 'future optional field' }], total_source_chars: [...row.source].length };
    assert.equal(parseSearch(raw, 'behavior', defaultOptions).results[0]?.path, filename);
  }
  for (const extension of ['.pyc', '.json', '.vue', '.md', '.JS', '']) {
    assert.equal(supportedSourcePath('src/code' + extension), false);
    const row = result('src/code' + extension, 'function run(){return 1;}');
    assert.throws(() => parseSearch({ ...response('/repo'), results: [row], total_source_chars: [...row.source].length }, 'behavior', defaultOptions));
  }
});
test('saved JS/TS verification uses strict UTF-8 including BOM, astral text and physical separators', async t => {
  const root = await repo(t), binding = await bindRepository(root, root, 'src');
  const code = '\ufeffexport function run(){\r\n return "🪁left\u2028middle\u2029right";\r\n}\r\n';
  for (const extension of supportedExtensions.filter(value => value !== '.py')) {
    const filename = 'src/code' + extension;
    await fs.writeFile(path.join(root, filename), code);
    const proof = await verifySavedResult(binding, result(filename, code));
    assert.equal(proof.editorUtf8Bom, true);
    assert.equal(decodeSavedSource(filename, Buffer.from(code)), code);
    assert.equal(sourceTextFromEditor(proof, code.slice(1)), code);
    assert.equal(sourceTextFromPreview(code, code.slice(1)), code);
    assert.equal(physicalLines(code).length, 3);
  }
});
test('JS/TS encoding cookies never bypass strict UTF-8 and malformed sequences cannot navigate', async t => {
  const root = await repo(t), binding = await bindRepository(root, root, 'src');
  const text = '// coding: latin-1\nfunction run(){return "ÿ";}\n';
  for (const extension of ['.js', '.ts', '.cjs', '.mts']) {
    const filename = 'src/code' + extension;
    await fs.writeFile(path.join(root, filename), Buffer.from(text, 'latin1'));
    assert.throws(() => decodeSavedSource(filename, Buffer.from(text, 'latin1')), /UTF-8/);
    await assert.rejects(verifySavedResult(binding, result(filename, text)), /UTF-8/);
  }
  assert.throws(() => decodeSavedSource('src/code.ts', Buffer.from([0xf0, 0x28, 0x8c, 0xbc])), /UTF-8/);
});
test('Python cookie/BOM semantics remain distinct, including utf-8-sig and explicit Latin-1', async t => {
  const root = await repo(t), binding = await bindRepository(root, root, 'src');
  const text = '# coding: utf_8_sig\ndef run():\n    return "café"\n';
  const bytes = Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), Buffer.from(text)]);
  await fs.writeFile(path.join(root, 'src/code.py'), bytes);
  assert.equal(decodeSavedSource('src/code.py', bytes), text);
  assert.equal(decodeSavedSource('src/code.py', Buffer.from(text)), text);
  assert.equal(editorBom('src/code.py', bytes), false);
  const proof = await verifySavedResult(binding, result('src/code.py', text, 2, 3));
  assert.equal(proof.editorUtf8Bom, false);
  assert.equal(sourceTextFromEditor(proof, text), text);
  const latin = '# coding: latin-1\ndef run():\n    return "\u0080café"\n';
  assert.equal(decodeSavedSource('src/code.py', Buffer.from(latin, 'latin1')), latin);
  assert.throws(() => decodeSavedSource('src/code.py', Buffer.from('# coding: ascii\n# café\n', 'latin1')), /encoding/);
});
test('BOM reconstruction preserves an additional literal marker and rejects a saved BOM removal', async t => {
  const root = await repo(t), binding = await bindRepository(root, root, 'src');
  const filename = 'src/code.ts', code = '\ufeff\ufeffexport function run(){return 1;}\n';
  await fs.writeFile(path.join(root, filename), code);
  const proof = await verifySavedResult(binding, result(filename, code));
  assert.equal(sourceTextFromEditor(proof, code.slice(1)), code);
  assert.equal(sourceTextFromPreview(code, code.slice(1)), code);
  await fs.writeFile(path.join(root, filename), code.slice(1));
  await assert.rejects(verifySavedResult(binding, result(filename, code), proof), /changed/);
});
test('JS/TS saved edits outside the excerpt and symlink escapes fail current navigation proof', async t => {
  const root = await repo(t), outside = await repo(t), binding = await bindRepository(root, root, 'src');
  const code = 'function run(){\nreturn 1;\n}\n// unrelated tail\n';
  for (const extension of ['.js', '.ts']) {
    const filename = 'src/code' + extension;
    await fs.writeFile(path.join(root, filename), code);
    const row = result(filename, code, 1, 3), proof = await verifySavedResult(binding, row);
    await fs.writeFile(path.join(root, filename), code.replace('unrelated tail', 'saved edit'));
    await assert.rejects(verifySavedResult(binding, row, proof), /changed/);
    await fs.writeFile(path.join(outside, filename), code);
    const linked = 'src/linked' + extension;
    await fs.symlink(path.join(outside, filename), path.join(root, linked));
    await assert.rejects(verifySavedResult(binding, { ...row, path: linked }), /outside/);
  }
});
