#!/usr/bin/env python3
"""Audit authored-overlay saved receipts without backend imports or HTTP calls."""
from __future__ import annotations

import argparse
import ast
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CHECKS = {
    "client_delivery_callback_rejects_changed_buffer", "later_saved_search_uses_saved_source",
    "literal_unicode_cookie_bom_crlf_and_saved_context", "parent_status_unchanged",
    "provenance_in_small_token_and_source_caps", "real_qwen_three_language_overlays",
    "saved_database_sidecars_bytes_mtime_membership_unchanged",
    "saved_restart_reuses_all_vectors_without_inference", "saved_sources_unchanged",
    "unreturned_buffer_change_invalidates_response",
}


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def physical_lines(source):
    return [line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", source) if line]


def compact_text(response):
    # Independently reproduce the declared wire schema and field order; never
    # call production compact_result/count_context/serialize_context helpers.
    fields = ("path", "symbol", "start_line", "end_line", "source", "source_hash", "truncated")
    rows = []
    for original in response["results"]:
        row = {**{key: original[key] for key in fields},
               "origin": original.get("origin", "retrieval"), "related_to": original.get("related_to")}
        row.update({key: original[key] for key in ("source_origin", "buffer_version", "buffer_hash") if key in original})
        rows.append(row)
    # The saved proof sorts JSON keys. Restore the original runtime insertion
    # order of these nested objects from the reviewed fixed wire schema.
    usage = {key: response["token_usage"][key] for key in ("encoding", "tokens", "budget", "truncated")}
    scope = {key: response["scope"][key] for key in ("source_root", "membership", "source_exclusions",
                                                       "exclusions_omitted", "source_state", "saved_source_manifest")}
    scope["overlays"] = [{key: binding[key] for key in ("path", "version", "content_hash")}
                         for binding in response["scope"]["overlays"]]
    envelope = {"query": response["query"], "match_status": response["match_status"], "results": rows,
                "budget_exhausted": response["budget_exhausted"], "token_usage": usage, "scope": scope}
    return json.dumps(envelope, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def fixture_literals(probe_source):
    tree = ast.parse(probe_source)
    output = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in ("saved", "texts"):
                require(name not in output, "Ambiguous fixture declaration")
                output[name] = ast.literal_eval(node.value)
    require(set(output) == {"saved", "texts"}, "Fixture literals unavailable")
    require(all(isinstance(value, bytes) for value in output["saved"].values()), "Saved fixture is not raw bytes")
    require(all(isinstance(value, str) for value in output["texts"].values()), "Overlay fixture is not Unicode text")
    return output


def hash_file(path):
    digest = hashlib.sha256()
    before = path.stat()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    after = path.stat()
    require((before.st_size, before.st_mtime_ns, before.st_ino)
            == (after.st_size, after.st_mtime_ns, after.st_ino), "Checkpoint changed during read-only hashing")
    return digest.hexdigest()


def audit_runtime(runtime_path, proof_path, installer_root, *, project_root=ROOT):
    """CPU metadata/path/checksum checks; the target interpreter imports no model."""
    runtime, proof = (json.loads(path.read_bytes()) for path in (runtime_path, proof_path))
    require(runtime["checks_passed"] and runtime["probe_sha256"] == sha(proof_path.read_bytes()),
            "Runtime companion is not bound to this proof")
    require("Post-inference" in runtime["capture_timing"] and "PYTHONPATH=<main>/src" in runtime["source_selection"],
            "Runtime capture/source-selection qualification missing")
    interpreter = installer_root / ".cache/install-validation/.venv/bin/python"
    code = """import importlib.metadata as m, importlib.util as u, json, platform, sys, sysconfig
names = json.loads(sys.argv[1])
installed = next(d for d in m.distributions(path=[sysconfig.get_paths()['purelib']])
 if d.metadata['Name'].replace('_', '-').lower() == 'semantic-search-engine')
print(json.dumps({'python': platform.python_version(), 'architecture': platform.machine(),
 'platform': platform.platform(), 'executable': sys.executable, 'prefix': sys.prefix,
 'packages': {d.metadata['Name']: d.version for d in m.distributions()},
 'paths': {name: u.find_spec(name).origin for name in names},
 'editable': json.loads(installed.read_text('direct_url.json'))}))
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(project_root / "src")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run([str(interpreter), "-c", code, json.dumps(list(runtime["imported_modules"]))],
                               env=environment, cwd=project_root, capture_output=True, text=True, check=True)
    observed = json.loads(completed.stdout)
    require(all(observed[key] == runtime[key] for key in ("python", "architecture", "platform", "packages")),
            "Current target runtime metadata differs from companion snapshot")
    require(Path(observed["executable"]) == interpreter and Path(observed["prefix"]) == interpreter.parent.parent,
            "Target interpreter/prefix differs")
    modules = {}
    for name, info in runtime["imported_modules"].items():
        expected_path = project_root / info["path"].removeprefix("<main>/")
        require(info["path"].startswith("<main>/src/") and Path(observed["paths"][name]) == expected_path,
                "Explicit source path resolution differs")
        digest = sha(expected_path.read_bytes())
        require(digest == info["sha256"] == proof["implementation_sha256"][str(expected_path.relative_to(project_root))],
                "Resolved implementation bytes differ")
        modules[name] = {"path": info["path"], "sha256": digest}
    require(observed["editable"]["dir_info"]["editable"] is True
            and observed["editable"]["url"] == installer_root.as_uri(), "Editable distribution origin differs")
    checkpoint = installer_root / ".cache/models/qwen3-embedding-0.6b"
    marker_path = checkpoint / "semantic_search_checkpoint.json"
    config_path = checkpoint / "config.json"
    metadata_path = checkpoint / ".cache/huggingface/download/model.safetensors.metadata"
    weight_path = checkpoint / "model.safetensors"
    marker, config = (json.loads(path.read_bytes()) for path in (marker_path, config_path))
    metadata = metadata_path.read_text().splitlines()
    weights_sha = hash_file(weight_path)
    require(marker["id"] == runtime["checkpoint"]["id"] == proof["embedding"]["id"]
            and marker["revision"] == runtime["checkpoint"]["revision"] == proof["embedding"]["revision"] == metadata[0],
            "Checkpoint identity/revision differs")
    require(weights_sha == runtime["checkpoint"]["weights_sha256"] == metadata[1] == marker["weights_etag"]
            and weight_path.stat().st_size == runtime["checkpoint"]["weights_bytes"], "Pinned weight byte/etag proof differs")
    require(not config.get("quantization") and not config.get("quantization_config")
            and config.get("model_type") == "qwen3" and config.get("hidden_size") == 1024
            and config.get("torch_dtype", config.get("dtype")) == "bfloat16", "Original Qwen configuration differs")
    for path, field in ((marker_path, "marker_sha256"), (config_path, "config_sha256"),
                        (metadata_path, "download_metadata_sha256")):
        require(sha(path.read_bytes()) == runtime["checkpoint"][field], "Checkpoint metadata bytes differ")
    require(sha((installer_root / "scripts/setup_embeddings.py").read_bytes()) == runtime["helper_sha256"],
            "Pinned download helper differs")
    require(sha((runtime_path.parent / "qwen-setup.log").read_bytes()) == runtime["qwen_setup_log_sha256"],
            "Published setup log differs")
    require(all(runtime["packages"][name] == version for name, version in proof["embedding"]["packages"].items()),
            "Observed model runtime packages differ")
    return {"checks_passed": True, "runtime_companion_sha256": sha(runtime_path.read_bytes()),
            "target_interpreter": runtime["interpreter"], "capture_timing": runtime["capture_timing"],
            "source_selection": runtime["source_selection"], "python": observed["python"],
            "architecture": observed["architecture"], "package_count": len(observed["packages"]),
            "editable_distribution_origin": "<installer-checkout>", "resolved_modules": modules,
            "checkpoint_weights_sha256": weights_sha, "checkpoint_weights_bytes": weight_path.stat().st_size,
            "checkpoint_revision": metadata[0], "reader_model_calls": 0, "reader_http_calls": 0,
            "limits": ["Companion and independent metadata resolution were captured after inference; probe-time interpreter/module paths were not recorded inline.",
                       "Fresh dependency environment and a newly populated local model directory use current main source via explicit PYTHONPATH; user-level caches were available.",
                       "No fresh-machine download timing, performance improvement or additional model inference is established."]}


def audit(proof_path, probe_source_path, *, encoder=None, project_root=ROOT):
    raw_proof = proof_path.read_bytes()
    proof = json.loads(raw_proof)
    before = deepcopy(proof)
    source = probe_source_path.read_bytes()
    require(sha(source) == proof["implementation_sha256"]["scripts/verify_live_overlays.py"],
            "Reviewed probe source does not match executed proof")
    require(proof["probe_revision"] == 2 and proof["passed"] is True
            and set(proof["checks"]) == CHECKS and all(proof["checks"].values()), "Probe execution checks incomplete")
    fixture = fixture_literals(source.decode())
    saved, texts = fixture["saved"], fixture["texts"]
    expected_saved_hashes = {name: sha(raw) for name, raw in saved.items()}
    require(proof["source_before_sha256"] == proof["source_after_sha256"] == expected_saved_hashes,
            "Saved-file raw byte hashes changed")
    # UTF-8 buffer bytes ignore the Python coding cookie. The sole saved-only
    # fixture context is ASCII, so no product decoder is needed for this audit.
    decoded = {name: texts.get(name, raw.decode("latin-1" if name.endswith("value.py") else "utf-8"))
               for name, raw in saved.items()}
    bindings = [{"path": name, "version": number + 7, "content_hash": sha(text.encode())}
                for number, (name, text) in enumerate(texts.items())]
    expected_bindings = sorted(bindings, key=lambda item: item["path"])
    require(proof["saved_cache_before"] == proof["saved_cache_after"], "Saved DB/sidecar snapshots changed")
    membership = set(proof["saved_cache_before"])
    require("saved.sqlite3" in membership and membership <= {"saved.sqlite3", "saved.sqlite3-wal", "saved.sqlite3-shm"},
            "Unexpected saved cache membership")
    for info in proof["saved_cache_before"].values():
        require(set(info) == {"sha256", "bytes", "mtime_ns", "inode"}
                and len(info["sha256"]) == 64 and all(type(info[key]) is int and info[key] >= 0
                                                       for key in ("bytes", "mtime_ns", "inode")), "Invalid cache byte/stat evidence")
    require(len(proof["responses"]) == 5, "Successful retained response count changed")
    if encoder is None:
        os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(project_root / ".cache/tokenizers"))
        import tiktoken
        encoder = tiktoken.get_encoding("cl100k_base")
    response_proofs = []
    for number, response in enumerate(proof["responses"], 1):
        require(response["repo"] == "<fixture>" and response["source_root"] == "src" and response["rerank"] is False,
                "Fixture source/reranker scope changed")
        require(response["scope"]["source_root"] == "src" and response["scope"]["source_state"] == "overlay"
                and response["scope"]["overlays"] == expected_bindings, "An input binding is missing or changed")
        require(response["scope"]["source_exclusions"] == [], "Fixture membership narrowed")
        usage = response["token_usage"]
        require(usage["encoding"] == "cl100k_base" and type(usage["tokens"]) is int
                and usage["budget"] == (512 if number == 5 else 4096), "Token metadata changed")
        counted = len(encoder.encode(compact_text(response), disallowed_special=()))
        require(counted == usage["tokens"] <= usage["budget"], "Independent compact token check failed")
        require(response["source_budget"] == (80 if number == 5 else 12000)
                and response["total_source_chars"] == sum(len(row["source"]) for row in response["results"])
                <= response["source_budget"], "Source cap/counter changed")
        require(len(response["results"]) <= (1 if number == 4 else 8), "Excerpt cap changed")
        for row in response["results"]:
            require(row["path"] in saved, "An excerpt escaped fixture membership")
            lines = physical_lines(decoded[row["path"]])
            lo, hi, full_hi = row["start_line"], row["end_line"], row["window_end_line"]
            require(all(type(value) is int for value in (lo, hi, full_hi)) and 1 <= lo <= hi <= full_hi <= len(lines),
                    "Physical source bounds changed")
            literal = "".join(lines[lo - 1:hi])
            full = "".join(lines[lo - 1:full_hi])
            require(row["source"] == literal and row["source_hash"] == sha(literal.encode())
                    and row["content_hash"] == sha(full.encode()), "Literal/full-window UTF-8 hash changed")
            require(type(row["truncated"]) is bool and row["truncated"] == (hi < full_hi), "Truncation flag changed")
            binding = next((item for item in expected_bindings if item["path"] == row["path"]), None)
            if binding:
                require(row["source_origin"] == "overlay" and row["buffer_version"] == binding["version"]
                        and row["buffer_hash"] == binding["content_hash"], "Returned overlay origin/version/hash changed")
            else:
                require(row["source_origin"] == "saved" and "buffer_version" not in row and "buffer_hash" not in row,
                        "Saved context falsely carries buffer provenance")
        response_proofs.append({"number": number, "query": response["query"], "retrieval": response["retrieval"],
                                "tokens": counted, "budget": usage["budget"], "source_chars": response["total_source_chars"],
                                "returned_excerpts": len(response["results"]), "budget_exhausted": response["budget_exhausted"]})
    for response, symbol in zip(proof["responses"][:3], ("overlay_value", "overlayJavascript", "overlayTypeScript")):
        require(response["retrieval"] == "dense" and any(row["symbol"] == symbol for row in response["results"]),
                "An authored language function was not delivered")
    narrow = proof["responses"][3]
    require(narrow["retrieval"] == "bm25" and len(narrow["results"]) == 1, "Nonreturned-binding case changed")
    omitted = [item["path"] for item in expected_bindings if item["path"] not in {row["path"] for row in narrow["results"]}]
    require(len(omitted) == 2, "Stale nonreturned-buffer scenario missing")
    clipped = proof["responses"][4]
    require(clipped["source_budget"] == 80 and clipped["token_usage"]["budget"] == 512
            and clipped["budget_exhausted"] and any(row["truncated"] for row in clipped["results"]),
            "Small-cap provenance scenario did not exercise clipping")
    require(proof["embedding_after_saved_warm"] == {"completed_document_calls": 1, "completed_query_calls": 1, "document_texts": 4}
            and proof["embedding_after_overlay_requests"] == {"completed_document_calls": 6, "completed_query_calls": 6, "document_texts": 19},
            "Observed model API counter inventory differs")
    reopened = proof["persistent_reopen"]
    require(reopened["indexstats"]["indexed_windows"] == 0
            and reopened["indexstats"]["reused_windows"] == reopened["indexstats"]["total_windows"] == 4
            and all(value == 0 for value in reopened["embedding_calls"].values()), "Saved reopen did not reuse all vectors")
    embedding = proof["embedding"]
    require(embedding["id"] == "Qwen/Qwen3-Embedding-0.6B"
            and embedding["revision"] == "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
            and embedding["quantized"] is False and embedding["dimensions"] == 1024, "Pinned model metadata differs")
    require(proof == before and proof_path.read_bytes() == raw_proof, "Audit mutated the recorded proof")
    implementation = {name: sha((project_root / name).read_bytes()) for name in proof["implementation_sha256"]
                      if name != "scripts/verify_live_overlays.py"}
    unchanged = implementation == {key: value for key, value in proof["implementation_sha256"].items()
                                   if key != "scripts/verify_live_overlays.py"}
    return {"classification": "Independent CPU-only saved-receipt audit of authored overlays; no inference rerun",
            "checked_at_utc": datetime.now(timezone.utc).isoformat(), "checks_passed": True,
            "proof_sha256": sha(raw_proof), "executed_probe_sha256": sha(source),
            "auditor_sha256": sha(Path(__file__).read_bytes()), "current_implementation_matches_recorded": unchanged,
            "fixture_source_sha256": expected_saved_hashes, "literal_utf8_buffer_bindings": expected_bindings,
            "response_proofs": response_proofs, "saved_cache_membership": sorted(membership),
            "sidecars_absent_in_real_probe": sorted({"saved.sqlite3-wal", "saved.sqlite3-shm"} - membership),
            "nonreturned_inputs_in_narrow_response": omitted, "observed_model_api_deltas": {
                "completed_document_calls": 5, "completed_query_calls": 5, "document_texts": 15},
            "reviewer_model_calls": 0, "reviewer_http_calls": 0,
            "limits": ["Token serializer/count and source hashes are independent; no production helpers are imported.",
                       "Sorted proof JSON does not retain original nested key order; exact compact counts restore the reviewed fixed runtime field order. Compact wire text itself was not retained.",
                       "Stale callback, parent status and later saved search are recorded executed assertions in the hash-matched probe, not separately retained HTTP responses.",
                       "The real fixture had no WAL/SHM; existing live-sidecar behavior is established separately by CPU tests.",
                       "This original proof records intended ROOT source hashes but not imported module paths, Python executable or package versions.",
                       "Recorded model ID/revision/config and executed load guard establish intended pinned provenance; this receipt does not independently hash checkpoint shards.",
                       "Repeated authored buffers, counters and timings do not establish retrieval quality, speedups, fresh-machine timing or cross-process SQLite isolation."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proof", type=Path, default=ROOT / "results/live-overlays-2026-10-13/proof.json")
    parser.add_argument("--probe-source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--runtime", type=Path, help="Optional post-run interpreter/checkpoint companion")
    parser.add_argument("--installer-root", type=Path, help="Installer checkout for read-only CPU metadata/checksum checks")
    args = parser.parse_args()
    snapshot = args.proof.parent / "probe-revision02-reviewed.py.txt"
    probe_source = args.probe_source or (snapshot if snapshot.exists() else ROOT / "scripts/verify_live_overlays.py")
    result = audit(args.proof, probe_source)
    if args.runtime:
        require(args.installer_root is not None, "--runtime requires --installer-root")
        result["runtime_companion_audit"] = audit_runtime(args.runtime, args.proof, args.installer_root.resolve())
    output = args.output or args.proof.parent / "audit.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n")
    print(json.dumps({"checks_passed": True, "responses": len(result["response_proofs"]),
                      "current_implementation_matches_recorded": result["current_implementation_matches_recorded"]}))


if __name__ == "__main__":
    main()
