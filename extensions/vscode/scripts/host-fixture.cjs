'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const config = require('./host-config.cjs');
const source = `def normalize_item(value):
    return value.strip().lower()


def sum_quantities(values):
    return sum(values)


def render_literal():
    return "<script>this is plain source, never HTML</script>"
`;
const query = 'Find the implementation that strips whitespace and converts an inventory item name to lowercase.';
async function createFixture(directory) {
  const repo = path.resolve(directory);
  // A fresh path is required so this helper never changes an existing repository.
  await fs.mkdir(repo, { recursive: false });
  await fs.mkdir(path.join(repo, 'src'));
  await fs.writeFile(path.join(repo, 'src', 'inventory.py'), source);
  for (const args of [['init', '-q', '-b', 'main'], ['add', 'src'],
    ['-c', 'user.name=Editor CPU Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'editor fixture']]) {
    const result = spawnSync('git', ['-C', repo, ...args], { encoding: 'utf8' });
    if (result.status !== 0) { throw new Error(`Fixture Git preparation failed: ${result.stderr}`); }
  }
  return { repo, source_root: 'src', source_path: 'src/inventory.py', source_sha256: crypto.createHash('sha256').update(source).digest('hex'),
    query, expected_symbol: 'normalize_item', functions: 3, model_loaded: false, service_started: false };
}
module.exports = { createFixture, source, query };
if (require.main === module) {
  const directory = process.argv[2];
  if (!directory) { console.error('Usage: node scripts/host-fixture.cjs /absolute/new-fixture-directory'); process.exitCode = 1; }
  else {
    fs.mkdir(path.dirname(path.resolve(directory)), { recursive: true }).then(() => createFixture(directory))
      .then(record => console.log(JSON.stringify(record, null, 2))).catch(error => { console.error(error.message); process.exitCode = 1; });
  }
}
