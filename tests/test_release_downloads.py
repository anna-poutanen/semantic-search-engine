"""Download checks with in-memory releases; no public network traffic."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import Mock
import zipfile

import pytest

SPEC = importlib.util.spec_from_file_location(
    "release_downloads", Path(__file__).resolve().parents[1] / "scripts/test_release_downloads.py"
)
downloads = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(downloads)


@pytest.fixture
def release():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in ("Get Started.command", "BUNDLE_MANIFEST.json", "LICENSE"):
            archive.writestr(f"semantic-search/{name}", "fixture")
    data = buffer.getvalue()
    asset = {"name": "semantic-search-mac-0.1.1.zip", "size": len(data),
             "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
             "browser_download_url": f"https://github.com/{downloads.REPOSITORY}/releases/download/v1/mac.zip"}
    return {"tag_name": "v1-preview", "published_at": "2026-10-14T00:00:00Z",
            "draft": False, "prerelease": True, "assets": [asset]}, data


def test_newest_published_mac_zip_includes_previews(release):
    published, _ = release
    draft = {**published, "draft": True, "published_at": "2026-10-15T00:00:00Z"}
    no_zip = {**published, "assets": [], "published_at": "2026-10-16T00:00:00Z"}
    older = {**published, "tag_name": "old", "published_at": "2026-10-13T00:00:00Z"}
    assert downloads.select_asset([older, draft, published, no_zip])[0] == "v1-preview"


@pytest.mark.parametrize("random_value,count", [(0, 2), (13, 15)])
def test_random_bounds_and_repeated_day_does_no_downloads(tmp_path, monkeypatch, release, random_value, count):
    published, data = release
    fetch = Mock(side_effect=[json.dumps([published]).encode()] + [data] * count)
    monkeypatch.setattr(downloads, "fetch", fetch)
    monkeypatch.setattr(downloads.secrets, "randbelow", lambda bound: random_value)
    report = downloads.run(tmp_path, day="2026-10-15")
    assert report["status"] == "passed"
    assert len(report["attempts"]) == report["planned_downloads"] == count
    assert downloads.run(tmp_path, day="2026-10-15") == report
    assert fetch.call_count == count + 1


def test_failed_downloads_are_recorded_without_extra_retries(tmp_path, monkeypatch, release):
    published, data = release
    fetch = Mock(side_effect=[json.dumps([published]).encode(), OSError("offline"), data])
    monkeypatch.setattr(downloads, "fetch", fetch)
    report = downloads.run(tmp_path, count=2)
    assert report["status"] == "failed"
    assert [row["status"] for row in report["attempts"]] == ["failed", "passed"]
    assert downloads.run(tmp_path, count=15) == report
    assert fetch.call_count == 3


def test_metadata_failure_and_interrupted_run_do_not_redownload(tmp_path, monkeypatch):
    fetch = Mock(side_effect=OSError("offline"))
    monkeypatch.setattr(downloads, "fetch", fetch)
    report = downloads.run(tmp_path, count=2, day="2026-10-15")
    assert report["status"] == "failed" and not report["attempts"]
    report["status"] = "running"
    downloads.save(tmp_path / "2026-10-15.json", report)
    assert downloads.run(tmp_path, day="2026-10-15") == report
    assert fetch.call_count == 1


def test_bad_digest_and_invalid_archive_rejected(release):
    published, data = release
    asset = published["assets"][0]
    with pytest.raises(ValueError, match="SHA-256"):
        downloads.verify(data, {**asset, "digest": "sha256:" + "0" * 64})
    bad_zip = b"not a zip"
    with pytest.raises(zipfile.BadZipFile):
        downloads.verify(bad_zip, {**asset, "size": len(bad_zip),
                                  "digest": "sha256:" + hashlib.sha256(bad_zip).hexdigest()})


@pytest.mark.parametrize("count", [0, 1, 16])
def test_count_outside_daily_limit_rejected(tmp_path, count):
    with pytest.raises(ValueError, match="between 2 and 15"):
        downloads.run(tmp_path, count=count)


def test_token_is_only_sent_to_release_metadata_api(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "fixture-token")
    requests = []

    def open_response(request, timeout):
        requests.append(request)
        return io.BytesIO(b"fixture")

    monkeypatch.setattr(downloads, "urlopen", open_response)
    assert downloads.fetch(downloads.RELEASES_URL, 100) == b"fixture"
    assert downloads.fetch("https://github.com/release.zip", 100) == b"fixture"
    assert requests[0].get_header("Authorization") == "Bearer fixture-token"
    assert requests[1].get_header("Authorization") is None
