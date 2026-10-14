"""Keep each distributable's license grant and shipped notice in agreement."""
from __future__ import annotations

import fnmatch
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/vscode"


def test_python_package_declares_spdx_mit_and_ships_the_license_and_notices():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert metadata["project"]["license"] == "MIT"
    assert metadata["project"]["license-files"] == ["LICENSE", "THIRD_PARTY_NOTICES.md"]
    for name in metadata["project"]["license-files"]:
        assert (ROOT / name).is_file()
    # PEP 639 license expressions and license-files require setuptools 77+.
    requirement = next(item for item in metadata["build-system"]["requires"] if item.startswith("setuptools"))
    minimum = re.fullmatch(r"setuptools>=(\d+)\.(\d+)\.(\d+)", requirement)
    assert minimum and tuple(map(int, minimum.groups())) >= (77, 0, 3)


def test_extension_and_lock_agree_and_packaging_keeps_license_checks():
    package = json.loads((EXTENSION / "package.json").read_text())
    lock = json.loads((EXTENSION / "package-lock.json").read_text())
    assert package["license"] == lock["packages"][""]["license"] == "MIT"
    assert "--skip-license" not in package["scripts"]["package"]
    assert "--no-dependencies" in package["scripts"]["package"]


def test_extension_ships_the_same_copyright_and_license_grant():
    license_bytes = (ROOT / "LICENSE").read_bytes()
    assert (EXTENSION / "LICENSE").read_bytes() == license_bytes
    text = license_bytes.decode()
    assert text.startswith("MIT License\n\nCopyright (c) 2026 Anna Poutanen\n")
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in text
    ignored = [line.strip() for line in (EXTENSION / ".vscodeignore").read_text().splitlines()
               if line.strip() and not line.startswith("#")]
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        assert (EXTENSION / name).is_file()
        assert not any(fnmatch.fnmatchcase(name, pattern) for pattern in ignored)


def test_separately_installed_dependency_keeps_its_upstream_license():
    for path in (ROOT / "THIRD_PARTY_NOTICES.md", EXTENSION / "THIRD_PARTY_NOTICES.md"):
        text = path.read_text()
        assert "GPL-3.0-or-later" in text
        assert "MIT license does not" in text
    # Do not change licenses of development dependencies in the lockfile.
    lock = json.loads((EXTENSION / "package-lock.json").read_text())
    assert lock["packages"]["node_modules/@vscode/vsce"]["license"] == "MIT"
