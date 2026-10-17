"""Verify the regular public release and download each asset without credentials."""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
from urllib.request import Request, urlopen
import zipfile

REPO = "anna-poutanen/semantic-search-engine"
TAG = "v0.1.3"


def fetch(url, maximum):
    request = Request(url, headers={"User-Agent": "semantic-search-release-qualification/0.1.3",
                                    "Cache-Control": "no-cache"})
    assert not request.has_header("Authorization")
    with urlopen(request, timeout=60) as response:
        data, status = response.read(maximum + 1), response.status
    assert status == 200 and len(data) <= maximum
    return data, status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--download-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert not args.output.exists()
    args.download_dir.mkdir(parents=True, exist_ok=False)
    release = json.loads(fetch(f"https://api.github.com/repos/{REPO}/releases/tags/{TAG}", 2_000_000)[0])
    latest = json.loads(fetch(f"https://api.github.com/repos/{REPO}/releases/latest", 2_000_000)[0])
    assert release["tag_name"] == latest["tag_name"] == TAG
    assert release["draft"] is False and release["prerelease"] is False
    remote = subprocess.check_output(["git", "ls-remote", f"https://github.com/{REPO}.git", f"refs/tags/{TAG}"], text=True)
    assert remote.split()[0] == args.target
    names = ("semantic-search-mac-0.1.3.zip", "semantic-search-local-0.1.3.vsix", "SHA256SUMS.txt")
    assets = {asset["name"]: asset for asset in release["assets"]}
    assert set(assets) == set(names)
    results = []
    for name in names:
        asset = assets[name]
        url = f"https://github.com/{REPO}/releases/download/{TAG}/{name}"
        assert asset["browser_download_url"] == url
        data, status = fetch(url, 5_000_000)
        digest = hashlib.sha256(data).hexdigest()
        assert data == (args.assets / name).read_bytes()
        assert len(data) == asset["size"] and asset["digest"] == "sha256:" + digest
        (args.download_dir / name).write_bytes(data)
        results.append({"name": name, "public_url": url, "bytes": len(data), "sha256": digest,
                        "http_status": status, "anonymous_download_verified": True,
                        "github_digest_matches": True, "local_qualified_asset_matches": True})
        if name.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                assert archive.testzip() is None
                prefix = archive.namelist()[0].split("/", 1)[0] + "/"
                manifest = json.loads(archive.read(prefix + "BUNDLE_MANIFEST.json"))
                assert manifest["version"] == "0.1.3"
                assert manifest["application_source_sha256"] == "17a6214ba5d21e8de949bc02547073f252a232460cf5f84ccd042d6998ca937c"
                for relative, row in manifest["files"].items():
                    payload = archive.read(prefix + relative)
                    assert hashlib.sha256(payload).hexdigest() == row["sha256"] and len(payload) == row["bytes"]
    report = {"captured_at_utc": datetime.now(timezone.utc).isoformat(), "release_url": release["html_url"],
              "tag": TAG, "release_target": args.target, "remote_tag_points_to_qualified_target": True,
              "prerelease": False, "draft": False, "latest": True, "assets": results,
              "download_classification": "One synthetic anonymous verification download per asset; not adoption.",
              "public_zip_manifest_verified": True}
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
