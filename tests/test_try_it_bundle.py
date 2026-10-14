"""Real small ZIP fixtures; no Git, model, installation, service or network."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys
from unittest.mock import patch
import zipfile

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build_try_it_bundle.py"
SPEC = importlib.util.spec_from_file_location("try_it_bundle", SCRIPT)
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)


def make_vsix(root, *, license_value="MIT", version="0.1.2", extra=None):
    manifest = {"name": "semantic-search-local", "publisher": "anna-poutanen", "version": version,
                "license": license_value, "main": "./dist/src/extension.js"}
    path = root.parent / "tested product.vsix"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("extension/package.json", json.dumps(manifest))
        archive.writestr("extension/LICENSE.txt", (root / "LICENSE").read_bytes())
        archive.writestr("extension/THIRD_PARTY_NOTICES.md", "Upstream licenses remain their own.\n")
        archive.writestr("extension/dist/src/extension.js", "'use strict';\n")
        archive.writestr("extension.vsixmanifest", f'''<PackageManifest xmlns="{bundle.VSIX_NAMESPACE}">
<Metadata><Identity Id="semantic-search-local" Publisher="anna-poutanen" Version="{version}"/>
<License>extension/LICENSE.txt</License></Metadata>
<Assets><Asset Type="Microsoft.VisualStudio.Services.Content.License" Path="extension/LICENSE.txt"/></Assets>
</PackageManifest>''')
        if extra:
            for name, data in extra.items():
                archive.writestr(name, data)
    return path


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "source project without Git"
    root.mkdir()
    for name in bundle.FIXED_FILES + bundle.NOTICE_FILES:
        write(root / name, f"Fixture for {name}\n")
    write(root / "Get Started.command", "#!/bin/zsh\nprintf 'hello'\n")
    write(root / "LICENSE", (SCRIPT.parents[1] / "LICENSE").read_bytes())
    write(root / "pyproject.toml", '[project]\nname="semantic-search-engine"\nversion="0.1.2"\nlicense="MIT"\n')
    write(root / bundle.GUIDE, "# Try Semantic Search\n\nOpen Get Started.command.\n")
    write(root / "src/semantic_search/__init__.py", '"""A tiny application fixture."""\n')
    write(root / "src/semantic_search/search.py", "def search():\n    return 'unicode café'\n")
    write(root / "src/semantic_search/nested/module.py", "value = 3\n")
    manifest = {"name": "semantic-search-local", "publisher": "anna-poutanen", "version": "0.1.2",
                "license": "MIT", "main": "./dist/src/extension.js"}
    write(root / "extensions/vscode/package.json", json.dumps(manifest))
    vsix = make_vsix(root)
    output = tmp_path / "release with spaces.zip"
    return root, vsix, output


def run(project):
    root, vsix, output = project
    return bundle.build_bundle(root, vsix, bundle.digest(vsix.read_bytes()), output)


def payload(output):
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        prefix = names[0].split("/", 1)[0] + "/"
        return {name.removeprefix(prefix): archive.read(name) for name in names}


def test_real_archive_has_exact_source_readme_licenses_vsix_and_launch_mode(project):
    root, vsix, output = project
    result = run(project)
    members = payload(output)
    assert members["README.md"] == (root / bundle.GUIDE).read_bytes()
    assert members[bundle.VSIX_NAME] == vsix.read_bytes()
    assert "extensions/vscode/package.json" not in members
    assert bundle.GUIDE not in members
    for name in bundle.FIXED_FILES + bundle.NOTICE_FILES + tuple(bundle.python_sources(root)):
        assert members[name] == (root / name).read_bytes()
    manifest = json.loads(members[bundle.MANIFEST_NAME])
    assert manifest["version"] == "0.1.2" and manifest["license"] == "MIT"
    assert manifest["readme_source"]["sha256"] == bundle.digest(members["README.md"])
    assert result["sha256"] == bundle.digest(output.read_bytes())
    assert result["files"] == len(members)
    inventory = manifest["files"]
    for name, data in members.items():
        if name != bundle.MANIFEST_NAME:
            assert inventory[name]["sha256"] == bundle.digest(data)
            assert inventory[name]["bytes"] == len(data)
    app = {name: inventory[name] for name in bundle.python_sources(root)}
    assert manifest["application_source_sha256"] == bundle.digest(bundle.canonical_json(app))
    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == sorted(archive.namelist())
        for entry in archive.infolist():
            mode = entry.external_attr >> 16
            assert stat.S_ISREG(mode)
            assert stat.S_IMODE(mode) == (0o755 if entry.filename.endswith("/Get Started.command") else 0o644)
            assert entry.date_time == bundle.ZIP_TIME


def test_fixed_bytes_make_identical_zips_despite_source_mtimes(project):
    root, vsix, first = project
    run(project)
    import os
    for name in bundle.python_sources(root):
        os.utime(root / name, (1234567890, 1234567890))
    second = first.with_name("second.zip")
    bundle.build_bundle(root, vsix, bundle.digest(vsix.read_bytes()), second)
    assert first.read_bytes() == second.read_bytes()


def test_allowlist_excludes_private_state_corpora_and_unrelated_assets(project):
    root, _, output = project
    forbidden = (".git/config", ".venv/bin/python", ".cache/auth.json", "node_modules/a.js",
                 "results/private.json", "benchmarks/queries.json", "src/semantic_search/model.safetensors",
                 "src/semantic_search/__pycache__/module.pyc", "scripts/unrelated.py", ".env",
                 "src/semantic_search/.cache/private.py", "src/semantic_search/node_modules/private.py",
                 "src/semantic_search/.venv/private.py")
    for name in forbidden:
        write(root / name, "PRIVATE")
    run(project)
    members = payload(output)
    assert not set(forbidden).intersection(members)
    assert all(b"PRIVATE" not in data for data in members.values())


def test_downloaded_feedback_collector_runs_without_runtime_packages_or_repository(project, tmp_path):
    """Exercise the real shipped collector from a ZIP, rather than a fixture stub."""
    root, _, output = project
    source = SCRIPT.parents[1] / "scripts/collect_beta_feedback.py"
    write(root / "scripts/collect_beta_feedback.py", source.read_bytes())
    run(project)
    extracted = tmp_path / "download with spaces"
    extracted.mkdir()
    with zipfile.ZipFile(output) as archive:
        archive.extractall(extracted)
    packaged = next(extracted.iterdir())
    record = tmp_path / "local feedback.json"
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(packaged / "scripts/collect_beta_feedback.py"),
         "--output", str(record), "--attempts", "4", "--full", "2", "--partial", "1", "--errors", "1",
         "--interface", "mcp", "--note", "partial_implementation"],
        cwd=tmp_path, text=True, capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(record.read_text())
    assert value["counts"] == {"attempts": 4, "full_implementation": 2,
                               "partial_implementation": 1, "operational_errors": 1}
    assert value["session"]["interface"] == "mcp"
    assert value["local_status"] == {"collection": "not_requested"}
    assert value["environment"]["packages"] == {name: None for name in ("semantic-search-engine", "mcp", "tiktoken", "sqlite-vec")}
    assert str(packaged) not in record.read_text()
    assert not (packaged / ".cache").exists()
    repeated = subprocess.run(
        [sys.executable, "-I", "-S", str(packaged / "scripts/collect_beta_feedback.py"),
         "--output", str(record)], cwd=tmp_path, text=True, capture_output=True, timeout=15,
    )
    assert repeated.returncode != 0
    assert "already exists" in repeated.stderr
    assert json.loads(record.read_text()) == value


@pytest.mark.parametrize("cause", ["sha", "vsix-license", "vsix-version", "project-version", "project-license", "source-license", "entrypoint"])
def test_wrong_hash_or_manifest_scope_never_creates_output(project, cause):
    root, vsix, output = project
    expected = bundle.digest(vsix.read_bytes())
    if cause == "sha":
        expected = "0" * 64
    elif cause.startswith("vsix-"):
        make_vsix(root, license_value="UNLICENSED" if cause == "vsix-license" else "MIT",
                  version="9.0.0" if cause == "vsix-version" else "0.1.2")
        expected = bundle.digest(vsix.read_bytes())
    elif cause.startswith("project-"):
        text = (root / "pyproject.toml").read_text()
        text = text.replace('"0.1.2"', '"0.1.3"') if cause == "project-version" else text.replace('"MIT"', '"BSD-3-Clause"')
        write(root / "pyproject.toml", text)
    else:
        path = root / "extensions/vscode/package.json"
        manifest = json.loads(path.read_text())
        manifest["license" if cause == "source-license" else "main"] = "UNLICENSED" if cause == "source-license" else "./changed.js"
        write(path, json.dumps(manifest))
    with pytest.raises(ValueError):
        bundle.build_bundle(root, vsix, expected, output)
    assert not output.exists()


def test_wrong_license_bytes_are_rejected(project):
    root, vsix, output = project
    with zipfile.ZipFile(vsix) as archive:
        original = {name: archive.read(name) for name in archive.namelist()}
    original["extension/LICENSE.txt"] = b"Different grant"
    with zipfile.ZipFile(vsix, "w") as archive:
        for name, data in original.items():
            archive.writestr(name, data)
    with pytest.raises(ValueError, match="license text"):
        run(project)
    assert not output.exists()


@pytest.mark.parametrize("name", ["../escaped.txt", "/absolute.txt", "extension/../evil.js", "extension/x\n.js", "extension\\evil.js"])
def test_unsafe_vsix_archive_names_are_rejected(project, name):
    root, _, output = project
    make_vsix(root, extra={name: b"bad"})
    with pytest.raises(ValueError, match="Unsafe"):
        run(project)
    assert not output.exists()


@pytest.mark.parametrize("name", ["scripts/demo_mac.py", "scripts/beta_tools.py", "scripts/collect_beta_feedback.py", "docs/getting-started.md", "Get Started.command"])
def test_missing_root_owned_file_is_an_error_not_a_placeholder(project, name):
    root, _, output = project
    (root / name).unlink()
    with pytest.raises(FileNotFoundError):
        run(project)
    assert not output.exists()


@pytest.mark.parametrize("cause", ["file", "ancestor", "source-directory", "fifo"])
def test_symlinks_and_special_inputs_are_rejected(project, cause):
    root, _, output = project
    if cause in ("file", "fifo"):
        path = root / "scripts/demo_mac.py"
        original = path.read_bytes()
        path.unlink()
        if cause == "file":
            other = root.parent / "outside.py"
            other.write_bytes(original)
            path.symlink_to(other)
        else:
            import os
            os.mkfifo(path)
    elif cause == "ancestor":
        source = root / "docs"
        moved = root.parent / "outside-docs"
        source.rename(moved)
        source.symlink_to(moved, target_is_directory=True)
    else:
        (root / "src/semantic_search/linked").symlink_to(root / "scripts", target_is_directory=True)
    with pytest.raises(ValueError, match="directory|regular"):
        run(project)
    assert not output.exists()


@pytest.mark.parametrize("cause", ["edit", "new-source", "vsix"])
def test_changes_during_build_are_detected_before_publication(project, cause):
    root, vsix, output = project
    original = bundle.verify_snapshot
    def change_then_verify(*args):
        if cause == "edit":
            write(root / "scripts/demo_mac.py", "changed")
        elif cause == "new-source":
            write(root / "src/semantic_search/added.py", "changed")
        else:
            vsix.write_bytes(b"changed")
        original(*args)
    with patch.object(bundle, "verify_snapshot", side_effect=change_then_verify):
        with pytest.raises(ValueError, match="changed"):
            run(project)
    assert not output.exists()
    assert not list(output.parent.glob(".try-it-*.zip"))


def test_existing_output_and_publication_race_preserve_user_file(project):
    _, _, output = project
    output.write_bytes(b"user file")
    with pytest.raises(FileExistsError):
        run(project)
    assert output.read_bytes() == b"user file"
    output.unlink()
    original = bundle.os.link
    def race(source, target):
        Path(target).write_bytes(b"created during build")
        return original(source, target)
    with patch.object(bundle.os, "link", side_effect=race):
        with pytest.raises(FileExistsError):
            run(project)
    assert output.read_bytes() == b"created during build"
    assert not list(output.parent.glob(".try-it-*.zip"))


def test_missing_output_directory_is_not_created(project):
    root, vsix, output = project
    output = output.parent / "missing" / output.name
    with pytest.raises(ValueError, match="parent"):
        bundle.build_bundle(root, vsix, bundle.digest(vsix.read_bytes()), output)
    assert not output.parent.exists()


@pytest.mark.parametrize("cause", ["identity", "version", "license-path", "entity"])
def test_container_identity_and_declared_license_are_bound_not_only_package_json(project, cause):
    _, vsix, output = project
    with zipfile.ZipFile(vsix) as archive:
        original = {name: archive.read(name) for name in archive.namelist()}
    container = original["extension.vsixmanifest"].decode()
    if cause == "identity":
        container = container.replace('Id="semantic-search-local"', 'Id="wrong"')
    elif cause == "version":
        container = container.replace('Version="0.1.2"', 'Version="0.1.3"')
    elif cause == "license-path":
        container = container.replace("extension/LICENSE.txt", "../outside.txt")
    else:
        container = '<!DOCTYPE data [<!ENTITY test "test">]>' + container
    original["extension.vsixmanifest"] = container.encode()
    with zipfile.ZipFile(vsix, "w") as archive:
        for name, data in original.items():
            archive.writestr(name, data)
    with pytest.raises(ValueError, match="container"):
        run(project)
    assert not output.exists()
