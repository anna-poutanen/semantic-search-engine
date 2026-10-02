"""Portable CPU fault tests; no downloads, models or existing tokenizer cache."""
import hashlib
import importlib.util
import io
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/prepare_ci_tokenizer_cache.py"
spec = importlib.util.spec_from_file_location("ci_tokenizer_cache", SCRIPT)
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)


@pytest.fixture
def toy_ranks(monkeypatch):
    raw = b"invented CPU-only tokenizer rank bytes\n"
    monkeypatch.setattr(cache, "CONTENT_SHA256", hashlib.sha256(raw).hexdigest())
    return raw


def test_public_url_and_exact_tiktoken_filename_are_pinned():
    assert hashlib.sha1(cache.URL.encode()).hexdigest() == cache.CACHE_KEY
    assert cache.CONTENT_SHA256 == "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"


def test_missing_cache_publishes_only_hash_verified_bytes(tmp_path, toy_ranks):
    calls = []
    def fetch(url):
        calls.append(url)
        return toy_ranks
    result = cache.prepare(tmp_path / "tokenizers", fetch=fetch)
    assert calls == [cache.URL]
    assert result["status"] == "created_verified"
    assert result["content_sha256"] == hashlib.sha256(toy_ranks).hexdigest()
    assert (tmp_path / "tokenizers" / cache.CACHE_KEY).read_bytes() == toy_ranks
    assert len(list((tmp_path / "tokenizers").iterdir())) == 1


def test_existing_valid_cache_never_downloads(tmp_path, toy_ranks):
    (tmp_path / cache.CACHE_KEY).write_bytes(toy_ranks)
    def forbidden(url):
        raise AssertionError("Existing verified ranks must remain offline")
    result = cache.prepare(tmp_path, fetch=forbidden)
    assert result["status"] == "already_verified"
    assert (tmp_path / cache.CACHE_KEY).read_bytes() == toy_ranks


def test_corrupt_existing_cache_fails_without_deleting_or_fetching(tmp_path, toy_ranks):
    target = tmp_path / cache.CACHE_KEY
    target.write_bytes(b"corrupt")
    def forbidden(url):
        raise AssertionError("Unexpected repair of a corrupt existing cache")
    with pytest.raises(ValueError, match="SHA256"):
        cache.prepare(tmp_path, fetch=forbidden)
    assert target.read_bytes() == b"corrupt"


@pytest.mark.parametrize("raw", [b"wrong", b"", "not bytes"])
def test_unverified_download_never_creates_cache_file(tmp_path, toy_ranks, raw):
    with pytest.raises(ValueError):
        cache.prepare(tmp_path / "new", fetch=lambda url: raw)
    assert not (tmp_path / "new" / cache.CACHE_KEY).exists()


def test_oversized_response_is_bounded_before_publication(monkeypatch):
    monkeypatch.setattr(cache, "MAX_BYTES", 4)
    def fetch(url, timeout):
        assert url == cache.URL and timeout == 30
        return io.BytesIO(b"12345")
    monkeypatch.setattr(cache, "urlopen", fetch)
    with pytest.raises(ValueError, match="bounded"):
        cache.download(cache.URL)


def test_download_failure_does_not_publish_partial_bytes(tmp_path):
    def failed(url):
        raise TimeoutError("invented CPU timeout")
    with pytest.raises(TimeoutError):
        cache.prepare(tmp_path, fetch=failed)
    assert not (tmp_path / cache.CACHE_KEY).exists()


def test_failed_atomic_publication_removes_temporary_file(tmp_path, toy_ranks, monkeypatch):
    def failed(source, destination):
        raise OSError("invented CPU publication failure")
    monkeypatch.setattr(cache.os, "replace", failed)
    with pytest.raises(OSError):
        cache.prepare(tmp_path, fetch=lambda url: toy_ranks)
    assert list(tmp_path.iterdir()) == []
