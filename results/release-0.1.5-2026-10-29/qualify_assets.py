"""Bind exact v0.1.5 assets to committed source and install the VSIX in isolation.

CPU/file/ordinary Code CLI qualification only. No service, model, GUI host,
network download or user extension/settings directory is used.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
VERSION = '0.1.5'
SPEC = importlib.util.spec_from_file_location('release015_public', Path(__file__).with_name('verify_public_release.py'))
public = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(public)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git_bytes(commit, name):
    return subprocess.check_output(['git', '-C', str(ROOT), 'show', f'{commit}:{name}'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--candidate-commit', required=True)
    parser.add_argument('--output-directory', type=Path, required=True)
    parser.add_argument('--code', type=Path, default=Path('/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code'))
    args = parser.parse_args()
    assert re.fullmatch('[0-9a-f]{40}', args.candidate_commit)
    assert subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip() == args.candidate_commit
    output = args.output_directory.resolve(); output.mkdir(parents=True, exist_ok=False)
    limits = {public.ZIP_NAME: public.bundle.MAX_ARCHIVE_BYTES,
              public.VSIX_NAME: public.bundle.MAX_VSIX_BYTES, 'SHA256SUMS.txt': 1000}
    assets = {name: public.bundle.read_regular(args.assets / name, limit=limit) for name, limit in limits.items()}
    bundle = public.verify_bundle(assets[public.ZIP_NAME], assets[public.VSIX_NAME])
    checksums = public.verify_checksums(assets['SHA256SUMS.txt'], assets)
    source = {}
    with zipfile.ZipFile(io.BytesIO(assets[public.ZIP_NAME])) as archive:
        prefix = f'semantic-search-mac-{VERSION}/'
        manifest = json.loads(archive.read(prefix + 'BUNDLE_MANIFEST.json'))
        for name in manifest['files']:
            if name == public.bundle.VSIX_NAME:
                continue
            source_name = public.bundle.GUIDE if name == 'README.md' else name
            data = archive.read(prefix + name)
            assert data == git_bytes(args.candidate_commit, source_name), source_name
            source[source_name] = digest(data)
    compiled, packaged = {}, {}
    with zipfile.ZipFile(io.BytesIO(assets[public.VSIX_NAME])) as archive:
        assert archive.testzip() is None
        package = json.loads(archive.read('extension/package.json'))
        assert package == json.loads(git_bytes(args.candidate_commit, 'extensions/vscode/package.json'))
        assert package['version'] == VERSION
        for name in archive.namelist():
            if not name.startswith('extension/'):
                continue
            relative = name.removeprefix('extension/'); data = archive.read(name)
            packaged[relative] = data
            if relative.startswith('dist/'):
                assert data == (ROOT / 'extensions/vscode' / relative).read_bytes(), relative
                compiled[relative] = digest(data)
        assert compiled and all(name.endswith(('.js', '.js.map')) for name in compiled)
        for relative, source_name in (('readme.md', 'extensions/vscode/README.md'),
                                      ('LICENSE.txt', 'extensions/vscode/LICENSE'),
                                      ('THIRD_PARTY_NOTICES.md', 'extensions/vscode/THIRD_PARTY_NOTICES.md')):
            assert packaged[relative] == git_bytes(args.candidate_commit, source_name), relative
        ts_source = {}
        for path in sorted((ROOT / 'extensions/vscode/src').rglob('*.ts')):
            relative = path.relative_to(ROOT).as_posix()
            assert path.read_bytes() == git_bytes(args.candidate_commit, relative), relative
            ts_source[relative] = digest(path.read_bytes())
    commands = []

    def run(label, command):
        result = subprocess.run([str(value) for value in command], capture_output=True, text=True, timeout=120)
        (output / (label + '.log')).write_text(result.stdout + result.stderr)
        commands.append({'name': label, 'command': [str(value) for value in command], 'returncode': result.returncode})
        assert result.returncode == 0, label
        return result.stdout

    code_version = run('code-version', [args.code, '--version']).splitlines()
    with tempfile.TemporaryDirectory(prefix='semantic-search-v015-code-') as temporary:
        owner = Path(temporary); user_data = owner / 'user-data'; extensions = owner / 'extensions'
        base = [args.code, '--user-data-dir', user_data, '--extensions-dir', extensions]
        run('isolated-cli-install', [*base, '--install-extension', (args.assets / public.VSIX_NAME).resolve(), '--force', '--do-not-sync'])
        listed = run('isolated-cli-list', [*base, '--list-extensions', '--show-versions'])
        expected = f'anna-poutanen.semantic-search-local@{VERSION}'
        assert expected in listed.splitlines(), listed
        installed = extensions / f'anna-poutanen.semantic-search-local-{VERSION}'
        assert installed.is_dir()
        for relative, data in packaged.items():
            actual = (installed / relative).read_bytes()
            if relative == 'package.json':
                value = json.loads(actual); value.pop('__metadata', None)
                assert value == json.loads(data)
            else:
                assert actual == data, relative
    receipt = {'version': VERSION, 'candidate_commit': args.candidate_commit,
               'captured_at': datetime.now(timezone.utc).isoformat(), 'assets': {
                   name: {'bytes': len(data), 'sha256': digest(data)} for name, data in assets.items()},
               'safe_bundle': bundle, 'checksums': checksums, 'committed_payload_sha256': source,
               'typescript_source_sha256': ts_source, 'compiled_member_sha256': compiled,
               'packaged_extension_members_verified_after_install': len(packaged),
               'isolated_ordinary_cli_install': True, 'owned_profile_removed_after_check': True,
               'code_version': code_version, 'commands': commands,
               'limits': 'Exact assets/source/compiled members and isolated ordinary Code CLI installation; no new GUI, trust or model evidence.'}
    (output / 'asset-qualification.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'passed': True, 'version': VERSION, 'payload_files_bound': len(source),
                      'compiled_members': len(compiled), 'installed_members_verified': len(packaged)}))


if __name__ == '__main__':
    main()
