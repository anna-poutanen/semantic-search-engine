#!/usr/bin/env python3
"""Build the small Mac try-it ZIP from an explicit source and licensed VSIX allowlist."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import tomllib
from xml.etree import ElementTree
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FIXED_FILES = (
    "Get Started.command", "pyproject.toml", "requirements-mac.lock",
    "requirements-languages.lock", "LICENSE", "THIRD_PARTY_NOTICES.md", ".gitignore",
    "scripts/quickstart_mac.py", "scripts/setup_mac.py", "scripts/setup_embeddings.py",
    "scripts/demo_mac.py", "scripts/beta_tools.py", "scripts/collect_beta_feedback.py",
)
# Keep upstream license text and attribution, without the benchmark datasets,
# model receipts, corpus source, or any other contents of results/benchmarks.
NOTICE_FILES = (
    "benchmarks/FLASK_LICENSE.txt", "benchmarks/REQUESTS_LICENSE.txt",
    "benchmarks/CORPUS_NOTICES.md", "benchmarks/CLICK_LICENSE.txt",
    "benchmarks/STARLETTE_LICENSE.txt", "benchmarks/RICH_LICENSE.txt",
    "benchmarks/EXPRESS_LICENSE.txt", "benchmarks/ZOD_LICENSE.txt",
    "benchmarks/HTTPX_LICENSE.txt", "benchmarks/HTTPX_CORPUS_NOTICE.md",
    "results/koa-clause-preflight-2026-10-13/NOTICE.md",
    "results/django-index-preflight-2026-10-14/DJANGO_LICENSE.txt",
    "results/werkzeug-context-preflight-2026-10-14/NOTICE.md",
    "results/werkzeug-context-preflight-2026-10-14/WERKZEUG_LICENSE.txt",
)
GUIDE = "docs/getting-started.md"
VSIX_NAME = "Semantic Search.vsix"
MANIFEST_NAME = "BUNDLE_MANIFEST.json"
MAX_SOURCE_BYTES = 2_000_000
MAX_VSIX_BYTES = 10_000_000
MAX_ARCHIVE_BYTES = 5_000_000
ZIP_TIME = (2026, 1, 1, 0, 0, 0)
VSIX_NAMESPACE = "http://schemas.microsoft.com/developer/vsx-schema/2011"
SOURCE_OMITTED_DIRS = {".git", ".venv", ".cache", "node_modules", "__pycache__", "results", "benchmarks"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical_json(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def safe_name(value):
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or "\\" in value or value != path.as_posix()
            or any(part in (".", "..") for part in path.parts)
            or any(ord(character) < 32 or ord(character) == 127 for character in value)):
        raise ValueError(f"Unsafe archive path: {value!r}")
    return value


def read_regular(path, *, root=None, limit=MAX_SOURCE_BYTES):
    """Read regular files only, and reject symlinked source ancestors too."""
    path = Path(path).absolute()
    if root is not None:
        root = Path(root).absolute()
        try:
            relative = path.relative_to(root)
        except ValueError as error:
            raise ValueError("Source file escapes project root") from error
        safe_name(relative.as_posix())
        current = root
        for part in relative.parts[:-1]:
            current /= part
            if not stat.S_ISDIR(current.lstat().st_mode):
                raise ValueError(f"Source ancestor must be a real directory: {current.name}")
    observed = path.lstat()
    if not stat.S_ISREG(observed.st_mode) or observed.st_size > limit:
        raise ValueError(f"Input must be a bounded regular file: {path.name}")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError(f"Input must be a bounded regular file: {path.name}")
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if (len(data) > limit or len(data) != before.st_size
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
        raise ValueError(f"Source changed while reading: {path.name}")
    return data


def python_sources(root):
    directory = root / "src/semantic_search"
    if not stat.S_ISDIR(directory.lstat().st_mode):
        raise ValueError("Application source directory must not be a symlink")
    names = []
    for current, folders, files in os.walk(directory, followlinks=False):
        folders[:] = sorted(name for name in folders if name not in SOURCE_OMITTED_DIRS)
        for name in folders:
            if not stat.S_ISDIR((Path(current) / name).lstat().st_mode):
                raise ValueError("Application source contains a symlinked directory")
        for name in files:
            if name.endswith(".py"):
                relative = (Path(current) / name).relative_to(root).as_posix()
                names.append(safe_name(relative))
    if "src/semantic_search/__init__.py" not in names:
        raise ValueError("Application package is missing __init__.py")
    return sorted(names)


def validate_vsix(data, expected_sha256, project, extension, license_bytes):
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        raise ValueError("expected-sha256 must be a full SHA-256 hex digest")
    if digest(data) != expected_sha256.lower():
        raise ValueError("VSIX does not match expected SHA-256")
    try:
        import io
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = []
            total = 0
            for entry in archive.infolist():
                safe_name(entry.filename)
                if entry.filename in names or entry.is_dir():
                    raise ValueError("VSIX contains duplicate or directory entries")
                if stat.S_IFMT(entry.external_attr >> 16) == stat.S_IFLNK:
                    raise ValueError("VSIX contains a symlink")
                if entry.file_size > MAX_SOURCE_BYTES:
                    raise ValueError("VSIX member is too large")
                total += entry.file_size
                names.append(entry.filename)
            if total > MAX_VSIX_BYTES:
                raise ValueError("VSIX uncompressed payload is too large")
            package = json.loads(archive.read("extension/package.json"))
            if not all(isinstance(value, dict) for value in (project, extension, package)):
                raise ValueError("Project, extension and VSIX manifests must be objects")
            if (project.get("license") != "MIT" or extension.get("license") != "MIT"
                    or package.get("license") != "MIT"):
                raise ValueError("Project, extension and VSIX must all declare MIT")
            if not isinstance(package.get("version"), str) or not (package["version"] == extension.get("version") == project.get("version")):
                raise ValueError("Project, extension and VSIX versions differ")
            for field in ("name", "publisher", "main"):
                if not isinstance(package.get(field), str) or not package[field] or package[field] != extension.get(field):
                    raise ValueError(f"VSIX {field} differs from extension source manifest")
            main = package["main"]
            if not isinstance(main, str) or not main.startswith("./"):
                raise ValueError("VSIX main must be a package-relative entry point")
            archive.read("extension/" + safe_name(main[2:]))
            container_bytes = archive.read("extension.vsixmanifest")
            if b"<!DOCTYPE" in container_bytes.upper() or b"<!ENTITY" in container_bytes.upper():
                raise ValueError("VSIX container must not contain XML declarations")
            container = ElementTree.fromstring(container_bytes)
            namespace = "{" + VSIX_NAMESPACE + "}"
            identities = container.findall(namespace + "Metadata/" + namespace + "Identity")
            if (container.tag != namespace + "PackageManifest" or len(identities) != 1
                    or any(identities[0].get(attribute) != package[field]
                           for attribute, field in (("Id", "name"), ("Version", "version"), ("Publisher", "publisher")))):
                raise ValueError("VSIX container identity differs from package manifest")
            declared = container.findall(namespace + "Metadata/" + namespace + "License")
            licenses = [entry for entry in container.findall(namespace + "Assets/" + namespace + "Asset")
                        if entry.get("Type") == "Microsoft.VisualStudio.Services.Content.License"]
            if (len(declared) != 1 or len(licenses) != 1
                    or declared[0].text != licenses[0].get("Path")
                    or licenses[0].get("Path") not in ("extension/LICENSE", "extension/LICENSE.txt")):
                raise ValueError("VSIX container must bind exactly one project license asset")
            if archive.read(licenses[0].get("Path")) != license_bytes:
                raise ValueError("VSIX license text differs from project LICENSE")
            archive.read("extension/THIRD_PARTY_NOTICES.md")
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError, UnicodeDecodeError, ElementTree.ParseError) as error:
        raise ValueError("VSIX is missing or has invalid required package contents") from error
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.]+)?", package["version"]):
        raise ValueError("Version is not safe for the bundle directory name")
    return package


def verify_snapshot(root, snapshots, source_names):
    if python_sources(root) != source_names:
        raise ValueError("Application source inventory changed during build")
    for name, expected in snapshots.items():
        if read_regular(root / name, root=root) != expected:
            raise ValueError(f"Source changed during build: {name}")


def build_bundle(root, vsix, expected_sha256, output):
    root = Path(root).absolute()
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise ValueError("Project root must be a real directory")
    output = Path(output).absolute()
    if not output.parent.is_dir() or output.parent.is_symlink():
        raise ValueError("Output parent must already exist and be a real directory")
    if output.exists() or output.is_symlink():
        raise FileExistsError("Output already exists; bundles never overwrite files")
    source_names = python_sources(root)
    names = sorted(set(FIXED_FILES + NOTICE_FILES + (GUIDE, "extensions/vscode/package.json") + tuple(source_names)))
    snapshots = {name: read_regular(root / name, root=root) for name in names}
    metadata = tomllib.loads(snapshots["pyproject.toml"].decode())
    project = metadata.get("project")
    extension = json.loads(snapshots["extensions/vscode/package.json"])
    vsix_bytes = read_regular(vsix, limit=MAX_VSIX_BYTES)
    package = validate_vsix(vsix_bytes, expected_sha256, project, extension, snapshots["LICENSE"])
    payload = {name: snapshots[name] for name in names if name not in (GUIDE, "extensions/vscode/package.json")}
    payload["README.md"] = snapshots[GUIDE]
    payload[VSIX_NAME] = vsix_bytes
    inventory = {name: {"sha256": digest(data), "bytes": len(data), "mode": "0755" if name == "Get Started.command" else "0644"}
                 for name, data in sorted(payload.items())}
    app_inventory = {name: inventory[name] for name in source_names}
    manifest = {
        "schema_version": 1, "version": package["version"], "license": "MIT",
        "extension": {"id": package["publisher"] + "." + package["name"], "sha256": digest(vsix_bytes), "bytes": len(vsix_bytes)},
        "application_source_sha256": digest(canonical_json(app_inventory)),
        "files": inventory,
        "readme_source": {"path": GUIDE, "sha256": digest(snapshots[GUIDE])},
        "manifest_scope": "files excludes this manifest itself; application_source_sha256 hashes sorted canonical JSON of Python source inventory",
        "omitted_source_directories": sorted(SOURCE_OMITTED_DIRS),
    }
    payload[MANIFEST_NAME] = canonical_json(manifest)
    prefix = "semantic-search-mac-" + package["version"]
    descriptor, temporary = tempfile.mkstemp(prefix=".try-it-", suffix=".zip", dir=output.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                for name, data in sorted(payload.items()):
                    entry = zipfile.ZipInfo(safe_name(prefix + "/" + name), ZIP_TIME)
                    entry.create_system = 3
                    entry.compress_type = zipfile.ZIP_DEFLATED
                    entry.external_attr = (stat.S_IFREG | (0o755 if name == "Get Started.command" else 0o644)) << 16
                    archive.writestr(entry, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
            stream.flush()
            os.fsync(stream.fileno())
        if temporary.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ValueError("Bundle exceeds the bounded preview size")
        verify_snapshot(root, snapshots, source_names)
        if read_regular(vsix, limit=MAX_VSIX_BYTES) != vsix_bytes:
            raise ValueError("VSIX changed during build")
        # An exclusive hard link publishes the fully written archive atomically.
        # Unlike replace/rename, it cannot overwrite a concurrently created file.
        os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    data = output.read_bytes()
    return {"output": str(output), "sha256": digest(data), "bytes": len(data), "version": package["version"],
            "files": len(payload), "application_source_sha256": manifest["application_source_sha256"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="Source tree (does not require Git)")
    parser.add_argument("--vsix", type=Path, required=True, help="Already tested MIT VSIX")
    parser.add_argument("--expected-sha256", required=True, help="Previously verified VSIX SHA-256")
    parser.add_argument("--output", type=Path, required=True, help="New ZIP in an existing directory; no overwrites")
    args = parser.parse_args(argv)
    try:
        receipt = build_bundle(args.root, args.vsix, args.expected_sha256, args.output)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
        parser.exit(1, f"Bundle failed: {error}\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
