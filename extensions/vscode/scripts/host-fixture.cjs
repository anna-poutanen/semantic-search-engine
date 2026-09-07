'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const source = `def normalize_item(value):
    return value.strip().lower()


def sum_quantities(values):
    return sum(values)


def render_literal():
    return "<script>this is plain source, never HTML</script>"
`;
const query = 'Find the implementation that strips whitespace and converts an inventory item name to lowercase.';
const javascriptSource = '\ufeffexport function decorate_code(value) {\r\n  return "[" + value.toUpperCase() + "]";\r\n}\r\n';
const typescriptSource = 'export function calculate_delta(before: number, after: number): number {\n  return after - before;\n}\n';
const mixedSources = { 'src/inventory.py': source, 'src/decorate.js': javascriptSource, 'src/delta.ts': typescriptSource };
const cases = [
  { language: 'python', path: 'src/inventory.py', symbol: 'normalize_item', query },
  { language: 'javascript', path: 'src/decorate.js', symbol: 'decorate_code',
    query: 'Find the implementation that uppercases a code value and encloses it in square brackets.' },
  { language: 'typescript', path: 'src/delta.ts', symbol: 'calculate_delta',
    query: 'Find the implementation that computes the numeric difference between an after value and a before value.' }
];
async function createFixture(directory, options = {}) {
  const repo = path.resolve(directory);
  // A fresh path is required so this helper never changes an existing repository.
  await fs.mkdir(repo, { recursive: false });
  await fs.mkdir(path.join(repo, 'src'));
  const sources = options.mixed ? mixedSources : { 'src/inventory.py': source };
  for (const [filename, text] of Object.entries(sources)) { await fs.writeFile(path.join(repo, filename), text); }
  for (const args of [['init', '-q', '-b', 'main'], ['add', 'src'],
    ['-c', 'user.name=Editor CPU Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'editor fixture']]) {
    const result = spawnSync('git', ['-C', repo, ...args], { encoding: 'utf8' });
    if (result.status !== 0) { throw new Error(`Fixture Git preparation failed: ${result.stderr}`); }
  }
  return { repo, source_root: 'src', mode: options.mixed ? 'mixed' : 'python',
    source_path: 'src/inventory.py', source_sha256: crypto.createHash('sha256').update(source).digest('hex'),
    source_files: Object.fromEntries(Object.entries(sources).map(([filename, text]) => [filename, crypto.createHash('sha256').update(text).digest('hex')])),
    query, expected_symbol: 'normalize_item', cases: options.mixed ? cases : cases.slice(0, 1),
    functions: options.mixed ? 5 : 3, model_loaded: false, service_started: false };
}
module.exports = { createFixture, source, query, mixedSources, cases };
if (require.main === module) {
  const directory = process.argv[2];
  if (!directory || process.argv.length > 4 || process.argv[3] && process.argv[3] !== '--mixed') { console.error('Usage: node scripts/host-fixture.cjs /absolute/new-fixture-directory [--mixed]'); process.exitCode = 1; }
  else {
    fs.mkdir(path.dirname(path.resolve(directory)), { recursive: true }).then(() => createFixture(directory, { mixed: process.argv[3] === '--mixed' }))
      .then(record => console.log(JSON.stringify(record, null, 2))).catch(error => { console.error(error.message); process.exitCode = 1; });
  }
}
