"""Read-only Django extraction preflight; no index construction or model imports."""
from __future__ import annotations

import collections
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys


class ForbidModelImports:
    def find_spec(self, fullname, path=None, target=None):
        forbidden = ("mlx", "mlx_embeddings", "transformers", "semantic_search.embeddings",
                     "semantic_search.model", "semantic_search.backend", "semantic_search.qwen")
        if any(fullname == name or fullname.startswith(name + ".") for name in forbidden):
            raise AssertionError(f"Model import forbidden during source preflight: {fullname}")
        return None


sys.meta_path.insert(0, ForbidModelImports())
from semantic_search.corpus import _source_files, extract_snippets  # noqa: E402
from semantic_search.languages import decode_source, extractor_fingerprint  # noqa: E402
from semantic_search.source import physical_lines  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / ".cache/corpora/django-5.2-index-profile"
OUTPUT = Path(__file__).resolve().parent
EXPECTED_COMMIT = "9e7cc2b628fe8fd3895986af9b7fc9525034c1b0"
EXCLUSIONS = ["django/**/*." + ext for ext in ("js", "jsx", "mjs", "cjs", "ts", "mts", "cts", "tsx")]


def git(*args):
    return subprocess.check_output(["git", "-C", str(CORPUS), *args])


def digest(value):
    return hashlib.sha256(value).hexdigest()


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def write_new(path, data):
    with path.open("xb") as stream:
        stream.write(data)


def document(snippet):
    # The production DOCUMENT_FORMAT is frozen in the source manifest below;
    # render it independently without importing NumPy/model-facing modules.
    return f"Path: {snippet.path}\nSymbol: {snippet.symbol}\nSource:\n{snippet.text}"


def main():
    assert git("rev-parse", "HEAD").decode().strip() == EXPECTED_COMMIT
    assert not git("status", "--porcelain=v1")
    tracked = [name.decode() for name in git("ls-files", "-z").split(b"\0") if name]
    production_tracked = [path for path in tracked if path.startswith("django/")]
    paths = _source_files(CORPUS, CORPUS / "django", EXCLUSIONS)
    assert all(path.suffix == ".py" for path in paths)
    raw = {path.relative_to(CORPUS).as_posix(): path.read_bytes() for path in paths}
    sources = {path: decode_source(path, data) for path, data in raw.items()}
    snippets = extract_snippets(CORPUS, "django", max_lines=160, overlap_lines=40,
                                sources=sources, source_exclusions=EXCLUSIONS)
    assert len(snippets) > 1802
    assert len({snippet.id for snippet in snippets}) == len(snippets)
    for snippet in snippets:
        assert snippet.text == "".join(physical_lines(sources[snippet.path])[
            snippet.start_line - 1:snippet.end_line])
        assert snippet.content_hash == digest(snippet.text.encode())
    files = [{"path": path, "sha256": digest(data), "bytes": len(data),
              "physical_lines": len(physical_lines(sources[path]))} for path, data in raw.items()]
    windows = [{"id": snippet.id, "path": snippet.path, "symbol": snippet.symbol,
                "start_line": snippet.start_line, "end_line": snippet.end_line,
                "content_sha256": snippet.content_hash,
                "document_sha256": digest(document(snippet).encode())} for snippet in snippets]
    files_bytes, windows_bytes = encode(files), encode(windows)
    write_new(OUTPUT / "source-inventory.json", files_bytes)
    write_new(OUTPUT / "window-inventory.json.gz", gzip.compress(windows_bytes, mtime=0))
    counts = collections.Counter(snippet.path for snippet in snippets)
    implementation_counts = collections.Counter((snippet.path, snippet.symbol) for snippet in snippets)
    no_windows = [path for path in raw if not counts[path]]
    non_python = [path for path in production_tracked if not path.endswith(".py")]
    policy_paths = ["src/semantic_search/" + name for name in
                    ("__init__.py", "corpus.py", "languages.py", "source.py", "index.py",
                     "store.py", "backend.py", "retrieval.py", "persistent_retrieval.py",
                     "embeddings.py", "context.py", "relations.py")]
    receipt = {
        "kind": "prospective_real_corpus_source_preflight", "measurements_started": False,
        "native_inference": False, "index_constructed": False,
        "model_clients_constructed": False, "model_import_guard": True,
        "repository": {"url": "https://github.com/django/django.git", "tag": "5.2",
                       "annotated_tag_object": git("rev-parse", "refs/tags/5.2").decode().strip(),
                       "commit": EXPECTED_COMMIT, "tree": git("rev-parse", "HEAD^{tree}").decode().strip(),
                       "clean": True, "local_path": ".cache/corpora/django-5.2-index-profile",
                       "license_sha256": digest((CORPUS / "LICENSE").read_bytes())},
        "extraction": {"source_root": "django", "source_exclusions": EXCLUSIONS,
                       "max_lines": 160, "overlap_lines": 40,
                       "fingerprint": extractor_fingerprint()},
        "counts": {"all_tracked_files": len(tracked), "production_root_tracked_files": len(production_tracked),
                   "tracked_production_python_files": len(raw), "excluded_non_python_production_files": len(non_python),
                   "python_source_bytes": sum(len(data) for data in raw.values()),
                   "python_physical_lines": sum(row["physical_lines"] for row in files),
                   "source_windows": len(snippets), "distinct_implementation_symbols": len(implementation_counts),
                   "unique_rendered_documents": len({row["document_sha256"] for row in windows}),
                   "window_source_bytes_with_overlap": sum(len(snippet.text.encode()) for snippet in snippets),
                   "largest_previous_real_corpus_windows": 1802,
                   "windows_to_previous_largest_ratio": len(snippets) / 1802},
        "production_python_files_without_implementation_windows": no_windows,
        "exclusions": {"outside_production_root": "Tests, docs and scripts outside django/ are absent by root membership.",
                       "non_python": "Supported JavaScript/TypeScript extensions are explicitly excluded; other extensions are ineligible.",
                       "python": "No eligible production Python file is excluded. Empty, docstring-only, placeholder and overload-only definitions do not yield windows.",
                       "parse_errors": [], "symlink_or_outside_root_files": []},
        "inventories": {"source-inventory.json": {"sha256": digest(files_bytes)},
                        "window-inventory.json.gz": {"sha256": digest((OUTPUT / "window-inventory.json.gz").read_bytes()),
                                                     "uncompressed_sha256": digest(windows_bytes)}},
        "producer_source_sha256": {path: digest((ROOT / path).read_bytes()) for path in policy_paths},
        "python": sys.version, "preflight_script_sha256": digest(Path(__file__).read_bytes()),
        "limitations": ["Source extraction counts are preparation, not build or search timing measurements.",
                        "No Django code is imported or executed.",
                        "No Qwen document/query embeddings, database, or retrieval result exists yet."]}
    write_new(OUTPUT / "source-preflight.json", encode(receipt))
    print(json.dumps(receipt["counts"], indent=2))


if __name__ == "__main__":
    main()
