'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const { downloadAndUnzipVSCode } = require('@vscode/test-electron');
const config = require('./host-config.cjs');

async function prepareHost() {
  if (process.platform !== 'darwin' || process.arch !== 'arm64') { throw new Error('This recorded test-host pin is macOS arm64'); }
  await fs.mkdir(config.cache, { recursive: true });
  // Download/unzip only. No executable, CLI, Electron, service or model starts.
  const executable = await downloadAndUnzipVSCode({ version: config.version,
    platform: config.platform, cachePath: config.cache, timeout: 30000 });
  const resources = path.resolve(path.dirname(executable), '../Resources/app');
  const [product, pkg, bytes] = await Promise.all([
    fs.readFile(path.join(resources, 'product.json'), 'utf8').then(JSON.parse),
    fs.readFile(path.join(resources, 'package.json'), 'utf8').then(JSON.parse),
    fs.readFile(executable)
  ]);
  if (product.commit !== config.commit || pkg.version !== config.version) { throw new Error('Downloaded test host differs from the pinned official build'); }
  const executableHash = crypto.createHash('sha256').update(bytes).digest('hex');
  if (executableHash !== config.expected_executable_sha256) { throw new Error('Test-host executable differs from the verified official archive extraction'); }
  const record = { version: pkg.version, commit: product.commit, platform: config.platform,
    executable_relative: path.relative(config.root, executable),
    executable_sha256: executableHash,
    expected_official_archive_sha256: config.expected_archive_sha256,
    archive_hash_note: 'Expected hash recorded from official update metadata; downloader separately validates its SHA-256 response header.',
    official_metadata_url: config.official_metadata_url, metadata_checked_utc: config.metadata_checked_utc,
    test_electron_version: config.test_electron_version, prepared_utc: new Date().toISOString(),
    launched: false, service_started: false };
  await fs.writeFile(path.join(config.cache, 'prepared.json'), JSON.stringify(record, null, 2) + '\n');
  return { executable, record };
}
module.exports = { prepareHost };
if (require.main === module) {
  prepareHost().then(({ record }) => console.log(JSON.stringify(record, null, 2))).catch(error => {
    console.error(error.message); process.exitCode = 1;
  });
}
