"""Author source-bound examples and persistence expectations before inference."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).resolve().parent
CORPUS = ROOT / ".cache/corpora/django-5.2-index-profile"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def main():
    receipt = json.loads((OUTPUT / "source-preflight.json").read_text())
    windows = json.loads(gzip.decompress((OUTPUT / "window-inventory.json.gz").read_bytes()))
    examples = [
        ("local_cache_get", "django/core/cache/backends/locmem.py", "LocMemCache.get",
         "LocMemCache.get _has_expired pickle.loads",
         "Look up an in-memory cache key under a lock, remove expired entries and return a caller default, otherwise mark the entry as most recently used and deserialize its stored value."),
        ("validate_page_number", "django/core/paginator.py", "Paginator.validate_number",
         "Paginator.validate_number PageNotAnInteger EmptyPage",
         "Validate a one-based page number: reject a float with a fractional part, coerce an integer, distinguish non-numeric input from values below one or beyond the last page, and return the valid number."),
        ("timestamp_signature_age", "django/core/signing.py", "TimestampSigner.unsign",
         "TimestampSigner.unsign max_age SignatureExpired",
         "Verify a signed value, decode its base62 timestamp, accept a duration or numeric age limit, and raise an expiration exception when the signature is older than that limit."),
        ("commit_callback_registration", "django/db/transaction.py", "on_commit",
         "django.db.transaction.on_commit get_connection robust",
         "Delegate registration of a transaction-commit callback to the selected database connection while passing through the option to tolerate callback failures."),
    ]
    queries = []
    for name, path, symbol, literal, behavioral in examples:
        matched = [row for row in windows if (row["path"], row["symbol"]) == (path, symbol)]
        assert len(matched) == 1
        window = matched[0]
        source = (CORPUS / path).read_bytes()
        target = {"path": path, "symbol": symbol,
                  "definition_lines": [window["start_line"], window["end_line"]],
                  "evidence_lines": [window["start_line"], window["end_line"]],
                  "evidence_sha256": window["content_sha256"],
                  "source_file_sha256": digest(source), "eligible_window_id": window["id"]}
        for kind, text in (("literal", literal), ("behavioral", behavioral)):
            queries.append({"id": f"django_{name}_{kind}", "query": text,
                            "kind": "present", "query_style": kind,
                            "split": "source_authored_profile_example", "target_groups": [[target]],
                            "notes": "One full local implementation span is required; this example does not require proving its external callees' behavior."})
        write_new(OUTPUT / "target-sources" / (path + ".gz"), gzip.compress(source, mtime=0))
    edited_path = "django/core/cache/backends/locmem.py"
    original = (CORPUS / edited_path).read_bytes()
    old = b"        return pickle.loads(pickled)\n"
    new = b"        return pickle.loads(pickled)  # saved-index-profile-edit\n"
    assert original.count(old) == 1
    edited = original.replace(old, new, 1)
    assert original.count(b"\n") == edited.count(b"\n")
    edited_windows = [row for row in windows if row["path"] == edited_path]
    assert len(edited_windows) == 13
    target = next(row for row in edited_windows if row["symbol"] == "LocMemCache.get")
    changed_text = "".join(edited.decode().splitlines(keepends=True)[target["start_line"] - 1:target["end_line"]])
    changed_document = f"Path: {edited_path}\nSymbol: {target['symbol']}\nSource:\n{changed_text}"
    assert digest(changed_document.encode()) not in {row["document_sha256"] for row in windows}
    write_new(OUTPUT / "mutation-sources/locmem.saved-edit.py.gz", gzip.compress(edited, mtime=0))
    write_new(OUTPUT / "DJANGO_LICENSE.txt", (CORPUS / "LICENSE").read_bytes())
    for path in receipt["producer_source_sha256"]:
        write_new(OUTPUT / "frozen-producer" / (path + ".gz"), gzip.compress((ROOT / path).read_bytes(), mtime=0))
    manifest = {
        "version": 1, "classification": "Prospective large real-codebase persistent-index profile with source-authored examples",
        "repo_url": receipt["repository"]["url"], "source_tag": "5.2", "revision": receipt["repository"]["commit"],
        "corpus": receipt["repository"]["local_path"], "source_root": "django", "language": "python",
        "source_exclusions": receipt["extraction"]["source_exclusions"],
        "source_window_settings": {"max_lines": 160, "overlap_lines": 40},
        "source_file_count": 883, "source_window_count": 9097, "unique_document_count": 9097,
        "source_preflight": "results/django-index-preflight-2026-10-14/source-preflight.json",
        "source_preflight_sha256": digest((OUTPUT / "source-preflight.json").read_bytes()),
        "source_inventory_sha256": digest((OUTPUT / "source-inventory.json").read_bytes()),
        "window_inventory_sha256": digest((OUTPUT / "window-inventory.json.gz").read_bytes()),
        "query_authoring": "Eight unchanged texts were authored from four pinned local implementation spans before any Django embeddings, index, queries or retrieval output. Literal and behavioral text pairs share a target.",
        "target_groups_semantics": "OR within each group; AND across groups. Credit requires a verified delivered source excerpt to contain the complete specified inclusive span and exact saved bytes.",
        "query_count": len(queries), "queries": queries,
        "quality_scope": {"classification": "Source-authored examples only", "no_retrieval_tuning": True,
                          "not_general_benchmark": True, "not_pretraining_holdout": True,
                          "no_absent_query_or_abstention_claim": True},
        "embedding": {"id": "Qwen/Qwen3-Embedding-0.6B", "revision": "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
                      "dtype": "bfloat16", "quantized": False, "batch_size": 4, "dimensions": 1024,
                      "checkpoint_download_cache_allowed": True, "first_index_document_vector_cache_allowed": False,
                      "max_tokens": 8192, "truncation": "right", "native_inference_required": True},
        "retrieval": {"rerank": False, "providers": ["bm25", "dense", "hybrid"], "raw_top_k": 10,
                      "hybrid_fetch_depth": 50, "rrf_k": 60, "quality_max_results": 5,
                      "quality_source_budget": 12000, "quality_token_budget": 2000, "quality_candidates": 50,
                      "include_dependencies": False, "dense_is_exact_scan": True,
                      "sqlite_vec_version": "0.1.9", "warmup_sweeps_per_provider": 3,
                      "measured_sweeps_per_provider": 10,
                      "queries_per_sweep": 8, "percentile_method": "numpy linear"},
        "persistence_sequence": [
            {"stage": "initial", "total_windows": 9097, "indexed_windows": 9097, "reused_windows": 0, "deleted_windows": 0, "new_unique_documents": 9097, "cached_documents_after": 9097},
            {"stage": "fresh_process_reopen", "total_windows": 9097, "indexed_windows": 0, "reused_windows": 9097, "deleted_windows": 0, "new_unique_documents": 0, "cached_documents_after": 9097, "forbid_document_and_query_inference": True},
            {"stage": "saved_edit", "total_windows": 9097, "indexed_windows": 1, "reused_windows": 9096, "deleted_windows": 0, "new_unique_documents": 1, "cached_documents_after": 9098},
            {"stage": "delete_file", "total_windows": 9084, "indexed_windows": 0, "reused_windows": 9084, "deleted_windows": 13, "new_unique_documents": 0, "cached_documents_after": 9098},
            {"stage": "restore_edited_file", "total_windows": 9097, "indexed_windows": 0, "reused_windows": 9097, "deleted_windows": 0, "new_unique_documents": 0, "cached_documents_after": 9098},
            {"stage": "rename_edited_file", "total_windows": 9097, "indexed_windows": 13, "reused_windows": 9084, "deleted_windows": 13, "new_unique_documents": 13, "cached_documents_after": 9111},
            {"stage": "rename_back", "total_windows": 9097, "indexed_windows": 0, "reused_windows": 9097, "deleted_windows": 13, "new_unique_documents": 0, "cached_documents_after": 9111},
            {"stage": "restore_pristine_file", "total_windows": 9097, "indexed_windows": 0, "reused_windows": 9097, "deleted_windows": 0, "new_unique_documents": 0, "cached_documents_after": 9111},
            {"stage": "final_fresh_process_reopen", "total_windows": 9097, "indexed_windows": 0, "reused_windows": 9097, "deleted_windows": 0, "new_unique_documents": 0, "cached_documents_after": 9111, "forbid_document_and_query_inference": True}
        ],
        "mutation": {"path": edited_path, "renamed_path": "django/core/cache/backends/locmem_profile_copy.py",
                     "original_file_sha256": digest(original), "edited_file_sha256": digest(edited),
                     "edited_window_id": target["id"], "edited_document_sha256": digest(changed_document.encode()),
                     "replacement_line_before": old.decode().rstrip("\n"), "replacement_line_after": new.decode().rstrip("\n"),
                     "behavior_change": False, "tracked_rename_command": "git mv", "original_clone_must_remain_clean": True},
        "numpy_validation": {"top_k": 10, "query_count": 8, "require_exact_id_order": True,
                             "max_cosine_absolute_error": 0.000003,
                             "independent_float32_conversion_and_float64_cosine": True,
                             "verification_excluded_from_query_timings": True,
                             "near_tie_mismatches_preserved_as_mismatches": True},
        "limitations": ["Django5.2 is a fixed historical source release, not the latest patched release or a deployed service.",
                        "This experiment profiles one machine and one repository, using a cached checkpoint but a genuinely empty document index.",
                        "Eight source-authored examples are too small and selected to establish general retrieval quality or coding-agent efficiency.",
                        "SQLite dense retrieval scans all active vectors exactly; this is not ANN scaling evidence.",
                        "Renaming changes the rendered document path and requires13 new embeddings; deletion keeps old cached vectors for branch return.",
                        "Watcher/debounce, unsaved editor overlays, reranking and external database/service execution are outside this profile."]
    }
    write_new(ROOT / "benchmarks/django_index_profile.json", encoded(manifest))
    print(json.dumps({"queries": len(queries), "windows": 9097, "rename_new_documents": 13,
                      "manifest_sha256": digest((ROOT / "benchmarks/django_index_profile.json").read_bytes())}, indent=2))


if __name__ == "__main__":
    main()
