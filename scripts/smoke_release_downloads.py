#!/usr/bin/env python3
"""Daily synthetic download checks. Does not install or execute release contents."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import time
from urllib.request import Request, urlopen
import zipfile
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "anna-poutanen/semantic-search-engine"
RELEASES_URL = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=100"
OUTPUT = ROOT / "results/local/download-tests"
MAX_DOWNLOAD_BYTES = 10_000_000
MIN_DOWNLOADS = 5
MAX_DOWNLOADS = 20


def fetch(url, limit):
    headers = {
        "User-Agent": "semantic-search-engine-synthetic-download-test/1.0",
        "Cache-Control": "no-cache",
    }
    # Authenticate API discovery on shared runners, never release/CDN downloads.
    if url == RELEASES_URL and os.environ.get("GH_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
    request = Request(url, headers=headers)
    with urlopen(request, timeout=60) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Response exceeds download test size limit")
    return data


def select_asset(releases):
    for release in sorted(releases, key=lambda item: item.get("published_at") or "", reverse=True):
        if release.get("draft") or not release.get("published_at"):
            continue
        for asset in release.get("assets", []):
            if not re.fullmatch(r"semantic-search-mac-[\w.\-]+\.zip", asset["name"]):
                continue
            url = asset["browser_download_url"]
            if not url.startswith(f"https://github.com/{REPOSITORY}/releases/download/"):
                raise ValueError("Unexpected release asset URL")
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", asset.get("digest") or ""):
                raise ValueError("Release asset has no SHA-256 digest")
            if not 0 < asset["size"] <= MAX_DOWNLOAD_BYTES:
                raise ValueError("Release asset size exceeds test limit")
            return release["tag_name"], asset
    raise ValueError("No published Mac ZIP found (preview releases are included)")


def verify(data, asset):
    if len(data) != asset["size"]:
        raise ValueError("Downloaded size differs from release metadata")
    digest = hashlib.sha256(data).hexdigest()
    if f"sha256:{digest}" != asset["digest"]:
        raise ValueError("Downloaded SHA-256 differs from release metadata")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if sum(info.file_size for info in archive.infolist()) > 50_000_000:
            raise ValueError("Uncompressed archive exceeds test limit")
        if archive.testzip() is not None:
            raise ValueError("ZIP CRC validation failed")
        names = {Path(name).name for name in archive.namelist()}
        if not {"Get Started.command", "BUNDLE_MANIFEST.json", "LICENSE"} <= names:
            raise ValueError("ZIP is missing required release files")
    return digest


def save(path, report):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.replace(path)


def run(output=OUTPUT, count=None, day=None):
    if count is not None and not MIN_DOWNLOADS <= count <= MAX_DOWNLOADS:
        raise ValueError(f"Download count must be between {MIN_DOWNLOADS} and {MAX_DOWNLOADS}")
    day = day or datetime.now(ZoneInfo("America/Toronto")).date().isoformat()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"{day}.json"
    with (output / f"{day}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            # No automatic retries: even an interrupted run must not add downloads.
            return json.loads(path.read_text())
        report = {"date": day, "timezone": "America/Toronto", "synthetic": True,
                  "started_at": datetime.now(timezone.utc).isoformat(),
                  "planned_downloads": count if count is not None else secrets.randbelow(
                      MAX_DOWNLOADS - MIN_DOWNLOADS + 1) + MIN_DOWNLOADS,
                  "attempts": [], "status": "running"}
        save(path, report)
        try:
            tag, asset = select_asset(json.loads(fetch(RELEASES_URL, 2_000_000)))
            report.update(release=tag, asset=asset["name"], url=asset["browser_download_url"])
            save(path, report)
            for number in range(1, report["planned_downloads"] + 1):
                attempt = {"number": number, "status": "running"}
                report["attempts"].append(attempt)
                save(path, report)
                started = time.monotonic()
                try:
                    data = fetch(asset["browser_download_url"], MAX_DOWNLOAD_BYTES)
                    attempt.update(sha256=verify(data, asset), bytes=len(data), status="passed")
                except Exception as error:
                    attempt.update(status="failed", error=f"{type(error).__name__}: {error}")
                attempt["seconds"] = round(time.monotonic() - started, 3)
                save(path, report)
            report["status"] = "passed" if all(
                attempt["status"] == "passed" for attempt in report["attempts"]
            ) else "failed"
        except Exception as error:
            report.update(status="failed", error=f"{type(error).__name__}: {error}")
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        save(path, report)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--count", type=int, choices=range(MIN_DOWNLOADS, MAX_DOWNLOADS + 1),
                        help=f"Default: random {MIN_DOWNLOADS}–{MAX_DOWNLOADS}")
    args = parser.parse_args()
    report = run(args.output_dir, args.count)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
