#!/usr/bin/env python3
"""Prepare the pinned cl100k cache before offline CI tests or MCP children start.

This setup command downloads only the public tokenizer ranks, never a model.
The cache directory must match the repository fallback used by MCP children,
whose SDK environment allowlist does not inherit TIKTOKEN_CACHE_DIR.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.request import urlopen

URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
CACHE_KEY = "9b5ad71b2ce5302211f9c61530b329a4922fc6a4"
CONTENT_SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
MAX_BYTES = 4 * 1024 * 1024


def verified(raw):
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES:
        raise ValueError("Tokenizer ranks must be bounded nonempty bytes")
    if hashlib.sha256(raw).hexdigest() != CONTENT_SHA256:
        raise ValueError("Pinned cl100k tokenizer content SHA256 differs")
    return raw


def download(url):
    with urlopen(url, timeout=30) as response:
        return verified(response.read(MAX_BYTES + 1))


def prepare(cache_dir, fetch=download):
    if hashlib.sha1(URL.encode()).hexdigest() != CACHE_KEY:
        raise ValueError("Tokenizer URL and tiktoken SHA1 cache filename differ")
    cache = Path(cache_dir).resolve()
    destination = cache / CACHE_KEY
    if destination.exists():
        raw = verified(destination.read_bytes())
        status = "already_verified"
    else:
        raw = verified(fetch(URL))
        cache.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".cl100k-", dir=cache)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
        status = "created_verified"
    # Re-read the published file, so success validates the bytes tests will use.
    if verified(destination.read_bytes()) != raw:
        raise ValueError("Published tokenizer cache differs from verified input")
    return {"encoding": "cl100k_base", "cache_key": CACHE_KEY,
            "content_sha256": CONTENT_SHA256, "bytes": len(raw),
            "cache_directory": str(cache), "status": status}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(prepare(args.cache_dir), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
