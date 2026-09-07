'use strict';
const path = require('node:path');
const root = path.resolve(__dirname, '..');
module.exports = Object.freeze({
  root,
  cache: path.join(root, '.cache', 'vscode-host'),
  version: '1.141.0',
  commit: '2a59476c9bfcb90b3ddc372c36762471b7dfad1c',
  platform: 'darwin-arm64',
  expected_archive_sha256: '9b4ae386e3cf738d7ba990237ddf0a18092c924a1bcae465ed4697964745f42d',
  expected_executable_sha256: 'e9760eca0a62d96ff2bb334ef5c8d3b677a6275a2dd0cf5a63c356b7a19435a2',
  official_metadata_url: 'https://update.code.visualstudio.com/api/update/darwin-arm64/stable/latest',
  metadata_checked_utc: '2026-10-13',
  test_electron_version: '3.1.0'
});
