"""Verify the regular public v0.1.4 release using bounded anonymous downloads."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
import re
import stat
import tomllib
from urllib.request import ProxyHandler, Request, build_opener
import zipfile

REPO = "anna-poutanen/semantic-search-engine"
VERSION = "0.1.4"
TAG = "v" + VERSION
ZIP_NAME = f"semantic-search-mac-{VERSION}.zip"
VSIX_NAME = f"semantic-search-local-{VERSION}.vsix"
NAMES = (ZIP_NAME, VSIX_NAME, "SHA256SUMS.txt")
ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("qualified_bundle", ROOT / "scripts/build_try_it_bundle.py")
bundle = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(bundle)
# Do not inherit proxies or install authentication handlers. Requests below have
# only public headers; GitHub credentials and environment tokens are never read.
OPENER = build_opener(ProxyHandler({}))


def fetch(url, maximum):
    request = Request(url, headers={
        "User-Agent": "semantic-search-release-qualification/" + VERSION,
        "Cache-Control": "no-cache",
    })
    assert not request.has_header("Authorization")
    with OPENER.open(request, timeout=60) as response:
        data, status = response.read(maximum + 1), response.status
    assert status == 200 and len(data) <= maximum
    return data, status


def api(path):
    return json.loads(fetch(f"https://api.github.com/repos/{REPO}/{path}", 2_000_000)[0])


def verify_checksums(data, assets):
    lines = data.decode("ascii").splitlines()
    assert len(lines) == 2
    recorded = {}
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9.-]+)", line)
        assert match is not None
        digest, name = match.groups()
        assert name not in recorded
        recorded[name] = digest
    assert recorded == {name: bundle.digest(assets[name]) for name in (ZIP_NAME, VSIX_NAME)}
    return recorded


def verify_bundle(data, standalone_vsix):
    """Check the production builder's safe inventory and exact payload hashes."""
    assert len(data) <= bundle.MAX_ARCHIVE_BYTES
    prefix = f"semantic-search-mac-{VERSION}/"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        assert len(names) == len(set(names)) and names == sorted(names)
        # Bound decompression before reading any member. The qualified release is
        # byte-bound below; this also rejects an unexpectedly expanded fixture.
        assert len(entries) <= 1000
        assert sum(entry.file_size for entry in entries) <= 10 * bundle.MAX_ARCHIVE_BYTES
        for entry in entries:
            bundle.safe_name(entry.filename)
            assert entry.filename.startswith(prefix) and not entry.is_dir()
            relative = entry.filename.removeprefix(prefix)
            bundle.safe_name(relative)
            maximum = bundle.MAX_VSIX_BYTES if relative == bundle.VSIX_NAME else bundle.MAX_SOURCE_BYTES
            assert 0 <= entry.file_size <= maximum
            mode = entry.external_attr >> 16
            assert stat.S_ISREG(mode)
            assert stat.S_IMODE(mode) == (0o755 if relative == "Get Started.command" else 0o644)
        assert archive.testzip() is None
        manifest = json.loads(archive.read(prefix + bundle.MANIFEST_NAME))
        assert manifest["schema_version"] == 1
        assert manifest["version"] == VERSION and manifest["license"] == "MIT"
        inventory = manifest["files"]
        assert isinstance(inventory, dict)
        assert set(inventory) == {name.removeprefix(prefix) for name in names} - {bundle.MANIFEST_NAME}
        fixed = set(bundle.FIXED_FILES + bundle.NOTICE_FILES + ("README.md", bundle.VSIX_NAME))
        assert fixed <= set(inventory)
        source_names = set(inventory) - fixed
        assert "src/semantic_search/__init__.py" in source_names
        for name in source_names:
            path = Path(bundle.safe_name(name))
            assert name.startswith("src/semantic_search/") and name.endswith(".py")
            assert not set(path.parts[2:-1]) & bundle.SOURCE_OMITTED_DIRS
        for relative, row in inventory.items():
            bundle.safe_name(relative)
            payload = archive.read(prefix + relative)
            assert type(row["bytes"]) is int and row["bytes"] == len(payload)
            assert row["sha256"] == bundle.digest(payload)
            assert row["mode"] == ("0755" if relative == "Get Started.command" else "0644")
        app_inventory = {name: inventory[name] for name in sorted(source_names)}
        assert manifest["application_source_sha256"] == bundle.digest(bundle.canonical_json(app_inventory))
        assert manifest["readme_source"] == {
            "path": bundle.GUIDE, "sha256": inventory["README.md"]["sha256"]}
        embedded = archive.read(prefix + bundle.VSIX_NAME)
        assert embedded == standalone_vsix
        assert manifest["extension"] == {
            "id": "anna-poutanen.semantic-search-local", "sha256": bundle.digest(embedded),
            "bytes": len(embedded)}
        project = tomllib.loads(archive.read(prefix + "pyproject.toml").decode())["project"]
        assert project["version"] == VERSION
        with zipfile.ZipFile(io.BytesIO(embedded)) as extension_archive:
            extension = json.loads(extension_archive.read("extension/package.json"))
        bundle.validate_vsix(embedded, bundle.digest(standalone_vsix), project, extension,
                             archive.read(prefix + "LICENSE"))
        return {"version": VERSION, "application_source_sha256": manifest["application_source_sha256"],
                "payload_files": len(inventory), "safe_inventory_verified": True,
                "all_payload_hashes_sizes_modes_verified": True, "embedded_vsix_matches_standalone": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--download-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert re.fullmatch(r"[0-9a-f]{40}", args.target)
    assert not args.output.exists()
    limits = {ZIP_NAME: bundle.MAX_ARCHIVE_BYTES, VSIX_NAME: bundle.MAX_VSIX_BYTES,
              "SHA256SUMS.txt": 1000}
    local = {name: bundle.read_regular(args.assets / name, limit=limits[name]) for name in NAMES}
    expected_bundle = verify_bundle(local[ZIP_NAME], local[VSIX_NAME])
    expected_checksums = verify_checksums(local["SHA256SUMS.txt"], local)
    args.download_dir.mkdir(parents=True, exist_ok=False)
    release = api(f"releases/tags/{TAG}")
    latest = api("releases/latest")
    assert release["tag_name"] == latest["tag_name"] == TAG
    for metadata in (release, latest):
        assert metadata["draft"] is False and metadata["prerelease"] is False
    assert release["id"] == latest["id"]
    remote = api(f"git/ref/tags/{TAG}")["object"]
    for _ in range(4):
        if remote["type"] == "commit":
            break
        assert remote["type"] == "tag" and re.fullmatch(r"[0-9a-f]{40}", remote["sha"])
        remote = api(f"git/tags/{remote['sha']}")["object"]
    assert remote["type"] == "commit" and remote["sha"] == args.target
    listed_assets = release["assets"]
    assert len(listed_assets) == len(NAMES)
    assets = {asset["name"]: asset for asset in listed_assets}
    assert set(assets) == set(NAMES)
    downloaded, results = {}, []
    for name in NAMES:
        asset = assets[name]
        url = f"https://github.com/{REPO}/releases/download/{TAG}/{name}"
        assert asset["browser_download_url"] == url and asset["state"] == "uploaded"
        data, status = fetch(url, limits[name])
        digest = bundle.digest(data)
        assert data == local[name]
        assert len(data) == asset["size"] and asset["digest"] == "sha256:" + digest
        (args.download_dir / name).write_bytes(data)
        downloaded[name] = data
        results.append({"name": name, "public_url": url, "bytes": len(data), "sha256": digest,
                        "http_status": status, "anonymous_download_verified": True,
                        "github_digest_matches": True, "local_qualified_asset_matches": True})
    public_bundle = verify_bundle(downloaded[ZIP_NAME], downloaded[VSIX_NAME])
    assert public_bundle == expected_bundle
    assert verify_checksums(downloaded["SHA256SUMS.txt"], downloaded) == expected_checksums
    report = {"captured_at_utc": datetime.now(timezone.utc).isoformat(), "release_url": release["html_url"],
              "tag": TAG, "release_target": args.target, "remote_tag_points_to_qualified_target": True,
              "tag_target_verification": "Anonymous GitHub git/ref API with bounded annotated-tag dereference",
              "prerelease": False, "draft": False, "latest": True, "assets": results,
              "download_classification": "One synthetic anonymous verification download per asset; not adoption.",
              "authorization_headers_sent": False, "proxy_environment_disabled": True,
              "public_zip_manifest_verified": True, "bundle": public_bundle,
              "expected_application_digest_source": str(args.assets / ZIP_NAME),
              "sha256sums_exact_zip_vsix_inventory_verified": True}
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
