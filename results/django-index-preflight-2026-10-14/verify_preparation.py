"""Independent source-only inventory/label/mutation verification; no producer imports."""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tokenize

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).resolve().parent
CORPUS = ROOT / ".cache/corpora/django-5.2-index-profile"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(*args):
    return subprocess.check_output(["git", "-C", str(CORPUS), *args])


def main():
    manifest_path = ROOT / "benchmarks/django_index_profile.json"
    manifest = json.loads(manifest_path.read_text())
    preflight = json.loads((OUTPUT / "source-preflight.json").read_text())
    files = json.loads((OUTPUT / "source-inventory.json").read_text())
    windows = json.loads(gzip.decompress((OUTPUT / "window-inventory.json.gz").read_bytes()))
    assert git("rev-parse", "HEAD").decode().strip() == manifest["revision"]
    assert not git("status", "--porcelain=v1")
    assert git("rev-parse", "refs/tags/5.2^{}").decode().strip() == manifest["revision"]
    tracked = {row.decode() for row in git("ls-files", "-z").split(b"\0") if row}
    expected_paths = {path for path in tracked if path.startswith("django/") and path.endswith(".py")}
    assert expected_paths == {row["path"] for row in files}
    assert len(files) == 883 and len(windows) == 9097
    raw = {path: (CORPUS / path).read_bytes() for path in expected_paths}
    decoded = {}
    for row in files:
        data = raw[row["path"]]
        assert sha(data) == row["sha256"] and len(data) == row["bytes"]
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
        decoded[row["path"]] = data.decode(encoding)
        independent_lines = [line for line in re.split(r"(?<=\n)|(?<=\r)(?!\n)", decoded[row["path"]]) if line]
        assert len(independent_lines) == row["physical_lines"]
    version_tree = ast.parse(decoded["django/__init__.py"])
    versions = [ast.literal_eval(node.value) for node in version_tree.body
                if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "VERSION" for target in node.targets)]
    assert versions == [(5, 2, 0, "final", 0)]
    documents, identifiers, texts = {}, set(), {}
    for row in windows:
        assert row["id"] not in identifiers
        identifiers.add(row["id"])
        assert row["id"] == f"{row['path']}:{row['symbol']}:{row['start_line']}-{row['end_line']}"
        lines = [line for line in re.split(r"(?<=\n)|(?<=\r)(?!\n)", decoded[row["path"]]) if line]
        text = "".join(lines[row["start_line"] - 1:row["end_line"]])
        assert sha(text.encode()) == row["content_sha256"]
        document = f"Path: {row['path']}\nSymbol: {row['symbol']}\nSource:\n{text}"
        assert sha(document.encode()) == row["document_sha256"]
        documents[row["id"]] = row["document_sha256"]
        texts[row["id"]] = text
    assert len(set(documents.values())) == 9097
    assert sum(len(data) for data in raw.values()) == 5644244
    assert sum(row["physical_lines"] for row in files) == 157865
    assert len(manifest["queries"]) == 8
    for query in manifest["queries"]:
        assert len(query["target_groups"]) == 1 and len(query["target_groups"][0]) == 1
        target = query["target_groups"][0][0]
        matching = [row for row in windows if row["id"] == target["eligible_window_id"]]
        assert len(matching) == 1
        window = matching[0]
        assert target["definition_lines"] == [window["start_line"], window["end_line"]]
        assert target["evidence_lines"] == target["definition_lines"]
        assert target["evidence_sha256"] == window["content_sha256"]
        assert target["source_file_sha256"] == sha(raw[target["path"]])
        assert gzip.decompress((OUTPUT / "target-sources" / (target["path"] + ".gz")).read_bytes()) == raw[target["path"]]
    mutation = manifest["mutation"]
    original, edited = raw[mutation["path"]], gzip.decompress((OUTPUT / "mutation-sources/locmem.saved-edit.py.gz").read_bytes())
    assert sha(original) == mutation["original_file_sha256"]
    assert sha(edited) == mutation["edited_file_sha256"]
    assert original.replace((mutation["replacement_line_before"] + "\n").encode(), (mutation["replacement_line_after"] + "\n").encode(), 1) == edited
    assert mutation["renamed_path"] not in tracked
    original_lines, edited_lines = original.decode().splitlines(keepends=True), edited.decode().splitlines(keepends=True)
    changed_lines = [i + 1 for i, (a, b) in enumerate(zip(original_lines, edited_lines)) if a != b]
    assert len(original_lines) == len(edited_lines) and changed_lines == [43]
    covering = [row for row in windows if row["path"] == mutation["path"] and row["start_line"] <= 43 <= row["end_line"]]
    assert len(covering) == 1 and covering[0]["id"] == mutation["edited_window_id"]
    edited_documents = dict(documents)
    edited_documents[mutation["edited_window_id"]] = mutation["edited_document_sha256"]
    erased = {key: value for key, value in edited_documents.items() if not key.startswith(mutation["path"] + ":")}
    renamed = dict(erased)
    renamed_id_map = {}
    for row in windows:
        if row["path"] != mutation["path"]:
            continue
        text = texts[row["id"]]
        if row["id"] == mutation["edited_window_id"]:
            text = "".join(edited_lines[row["start_line"] - 1:row["end_line"]])
            assert sha(f"Path: {row['path']}\nSymbol: {row['symbol']}\nSource:\n{text}".encode()) == mutation["edited_document_sha256"]
        identifier = f"{mutation['renamed_path']}:{row['symbol']}:{row['start_line']}-{row['end_line']}"
        renamed[identifier] = sha(f"Path: {mutation['renamed_path']}\nSymbol: {row['symbol']}\nSource:\n{text}".encode())
        renamed_id_map[row["id"]] = identifier
    states = [documents, documents, edited_documents, erased, edited_documents, renamed,
              edited_documents, documents, documents]
    cache, previous, calculations = set(), set(), []
    for expected, state in zip(manifest["persistence_sequence"], states, strict=True):
        reused = sum(value in cache for value in state.values())
        new = len(set(state.values()) - cache)
        cache.update(state.values())
        actual = {"stage": expected["stage"], "total_windows": len(state),
                  "indexed_windows": len(state) - reused, "reused_windows": reused,
                  "deleted_windows": len(previous - state.keys()),
                  "new_unique_documents": new, "cached_documents_after": len(cache)}
        assert all(actual[key] == value for key, value in expected.items() if key != "forbid_document_and_query_inference")
        calculations.append(actual)
        previous = set(state)
    assert preflight["preflight_script_sha256"] == sha((OUTPUT / "prepare_source.py").read_bytes())
    for path, expected_sha in preflight["producer_source_sha256"].items():
        assert sha((ROOT / path).read_bytes()) == expected_sha
        assert gzip.decompress((OUTPUT / "frozen-producer" / (path + ".gz")).read_bytes()) == (ROOT / path).read_bytes()
    assert sha((OUTPUT / "source-preflight.json").read_bytes()) == manifest["source_preflight_sha256"]
    assert sha((OUTPUT / "source-inventory.json").read_bytes()) == manifest["source_inventory_sha256"]
    assert sha((OUTPUT / "window-inventory.json.gz").read_bytes()) == manifest["window_inventory_sha256"]
    tag = git("cat-file", "-p", "refs/tags/5.2")
    with (OUTPUT / "tag-object.txt").open("xb") as stream:
        stream.write(tag)
    receipt = {"passed": True, "kind": "independent_source_only_preparation_verification",
               "created_at_utc": datetime.now(timezone.utc).isoformat(),
               "model_imports_or_inference": False, "database_constructed": False,
               "timing_measurements": False, "tracked_python_membership_exact": True,
               "all_saved_window_source_and_document_hashes_exact": True,
               "query_target_spans_source_bound": True, "mutation_expected_counts_independently_derived": True,
               "persistence_sequence": calculations, "tag_has_pgp_signature": b"BEGIN PGP SIGNATURE" in tag,
               "tag_pgp_signature_verified": False, "tag_object_sha256": sha(tag),
               "frozen_inputs": {"benchmarks/django_index_profile.json": sha(manifest_path.read_bytes()),
                                 "docs/large-index-profile-protocol.md": sha((ROOT / "docs/large-index-profile-protocol.md").read_bytes()),
                                 "prepare_source.py": sha((OUTPUT / "prepare_source.py").read_bytes()),
                                 "author_profile.py": sha((OUTPUT / "author_profile.py").read_bytes()),
                                 "verify_preparation.py": sha(Path(__file__).read_bytes())}}
    with (OUTPUT / "preparation-verification.json").open("x") as stream:
        json.dump(receipt, stream, sort_keys=True, indent=2)
        stream.write("\n")
    print(json.dumps({"passed": True, "windows": len(windows), "stages": len(calculations),
                      "final_cached_documents": len(cache), "manifest_sha256": sha(manifest_path.read_bytes())}, indent=2))


if __name__ == "__main__":
    main()
