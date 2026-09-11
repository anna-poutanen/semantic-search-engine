#!/usr/bin/env python3
"""Recheck retained editor-host receipts without a backend, model, or Electron."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
HASH = re.compile(r"[0-9a-f]{64}\Z")


def digest(value):
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def compact(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def ecma_serializations(values):
    """Use the same specified JSON.stringify representation as the Node recorder."""
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node is required to verify the recorder's JSON.stringify hashes")
    code = "const fs=require('node:fs');process.stdout.write(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(v=>JSON.stringify(v))));"
    completed = subprocess.run([node, "-e", code], input=compact(values), text=True,
                               capture_output=True, check=True, timeout=30)
    return json.loads(completed.stdout)


def fixture_sources(mode="mixed"):
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node is required to read the frozen authored fixture exports")
    helper = ROOT / "extensions/vscode/scripts/host-fixture.cjs"
    code = "const f=require(process.argv[1]);process.stdout.write(JSON.stringify(f.mixedSources));"
    result = subprocess.run([node, "-e", code, str(helper)], text=True,
                            capture_output=True, check=True, timeout=30)
    values = json.loads(result.stdout)
    return values if mode == "mixed" else {"src/inventory.py": values["src/inventory.py"]}


def physical_lines(text):
    return re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text)[:-1]


def context_payload(response):
    fields = ("path", "symbol", "start_line", "end_line", "source", "source_hash", "truncated")
    rows = []
    for original in response["results"]:
        row = {key: original[key] for key in fields}
        row.update(origin=original.get("origin", "retrieval"), related_to=original.get("related_to"))
        for key in ("source_origin", "buffer_version", "buffer_hash"):
            if key in original:
                row[key] = original[key]
        rows.append(row)
    value = {"query": response["query"], "match_status": response.get("match_status", "candidates"),
             "results": rows, "budget_exhausted": response.get("budget_exhausted", False),
             "token_usage": response["token_usage"]}
    if "scope" in response:
        value["scope"] = response["scope"]
    return value


def token_count(text):
    os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(ROOT / ".cache/tokenizers"))
    import tiktoken
    return len(tiktoken.get_encoding("cl100k_base").encode(text, disallowed_special=()))


def relative(path):
    return (isinstance(path, str) and bool(path) and not any(ord(c) < 32 or ord(c) == 127 for c in path)
            and "\\" not in path and ":" not in path and not PurePosixPath(path).is_absolute()
            and all(part not in ("", ".", "..") for part in path.split("/")))


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return parsed


def fixture_manifest_value(sources):
    return {"entries": sorted(PurePosixPath(name).name for name in sources),
            "files": {name: digest(sources[name]) for name in sorted(sources)}}


def saved_edit_variants(sources):
    """Reviewed host edit recipe; returned excerpts never define the expected source."""
    outside = {**sources, "src/inventory.py": sources["src/inventory.py"] +
               '\n# saved edit outside the returned implementation\n'}
    semantic = {**outside, "src/delta.ts":
                'export function calculate_saved_delta(before: number, after: number): number {\n'
                '  return after - before + 1;\n}\n'}
    renamed = {name: text for name, text in semantic.items() if name != "src/decorate.js"}
    renamed["src/decorate-renamed.js"] = sources["src/decorate.js"]
    deleted = {name: text for name, text in renamed.items() if name != "src/delta.ts"}
    invalid = {**deleted, "src/inventory.py": "def broken(:\n"}
    return [("initial", sources), ("outside_excerpt_edit", outside), ("semantic_typescript_edit", semantic),
            ("tracked_javascript_rename", renamed), ("typescript_delete", deleted), ("invalid_python", invalid)]


def audit_host(record, sources, *, serializer=ecma_serializations, count_tokens=token_count):
    """Verify fixture bytes independently; observer/UI booleans are not substitutes."""
    errors = []
    def require(condition, message):
        if not condition:
            errors.append(message)
    receipts = record.get("receipts", [])
    counts = {"receipts": len(receipts), "original_request_hashes": 0,
              "normalized_redacted_response_hashes": 0, "exact_excerpts": 0,
              "saved_excerpts": 0, "overlay_excerpts": 0, "successful_searches": 0,
              "overlay_searches": 0, "token_payloads": 0}
    tokens, overlay_calls, phase_calls = [], [], []
    controlled_edits = record.get("saved_edit_smoke") is True
    source_hashes = {name: digest(text) for name, text in sources.items()}
    manifest_value = fixture_manifest_value(sources)
    try:
        serialized = serializer([manifest_value, *[r.get("request") for r in receipts],
                                 *[r.get("response") for r in receipts]])
        require(len(serialized) == 1 + 2 * len(receipts), "JSON serializer output length")
        manifest = digest(serialized[0])
        variants = saved_edit_variants(sources) if controlled_edits else [("initial", sources)]
        variant_manifests = serializer([fixture_manifest_value(value) for _, value in variants])
        snapshots = {digest(encoded): (name, value) for (name, value), encoded in zip(variants, variant_manifests, strict=True)}
    except Exception as error:
        return {"passed": False, "errors": [f"Cannot serialize retained JSON: {error}"], "counts": counts}
    require(record.get("passed") is True and record.get("exit", {}).get("code") == 0
            and record.get("timed_out") is False, "Host did not complete successfully")
    host = record.get("host", {})
    required_checks = {"connect", "search", "plaintext_preview", "verified_navigation",
                       "cancel_command", "replacement_and_late_suppression"}
    checks = host.get("checks", {})
    require(host.get("passed") is True and required_checks <= checks.keys()
            and all(value is True for value in checks.values()), "Missing/failed executed host assertions")
    require(record.get("source_before_manifest_sha256") == record.get("source_after_manifest_sha256") == manifest,
            "Fixture membership/byte manifest differs or changed")
    require(record.get("source_before_sha256") == record.get("source_after_sha256") == source_hashes.get("src/inventory.py"),
            "Saved Python fixture bytes differ or changed")
    try:
        start, finish = timestamp(record["started_utc"]), timestamp(record["finished_utc"])
        require(start <= finish, "Invalid overall observer timestamps")
    except (KeyError, TypeError, ValueError):
        start = finish = None
        require(False, "Missing/invalid overall observer timestamps")
    for position, receipt in enumerate(receipts):
        label = f"Receipt {position}"
        try:
            require(not receipt.get("transport_error"), label + " transport error")
            require(bool(HASH.fullmatch(receipt.get("response_sha256", ""))), label + " captured original response hash format")
            payload = receipt.get("request")
            request_bytes = b"" if payload is None else serialized[1 + position].encode("utf-8")
            request_matches = digest(request_bytes) == receipt.get("request_sha256")
            require(request_matches, label + " original request hash")
            counts["original_request_hashes"] += int(request_matches)
            response = receipt.get("response")
            response_matches = digest(serialized[1 + len(receipts) + position]) == receipt.get("redacted_response_sha256")
            require(response_matches,
                    label + " normalized redacted response hash")
            counts["normalized_redacted_response_hashes"] += int(response_matches)
            began, ended = timestamp(receipt["started_utc"]), timestamp(receipt["finished_utc"])
            require(start is not None and start <= began <= ended <= finish, label + " observer timestamp order")
            require(began <= timestamp(receipt["backend_finished_utc"]) <= ended, label + " backend observer timestamp order")
            if receipt.get("endpoint") != "/search":
                continue
            snapshot_digest = receipt.get("before_source_manifest_sha256")
            phase, saved_sources = snapshots.get(snapshot_digest, ("unknown", sources))
            require(phase != "unknown" and receipt.get("after_source_manifest_sha256") == snapshot_digest,
                    label + " saved source manifest changed or is not a reviewed fixture phase")
            require(receipt.get("before_source_sha256") == receipt.get("after_source_sha256") == digest(saved_sources["src/inventory.py"]),
                    label + " saved source bytes changed or differ from reviewed phase")
            phase_calls.append({"receipt": position, "phase": phase, "status": receipt.get("status"),
                                "source_manifest_sha256": snapshot_digest})
            if receipt.get("status") != 200:
                require(not response.get("results"), label + " failed search returned stale results")
                if controlled_edits and phase == "invalid_python":
                    require(receipt.get("status") == 409 and response.get("error", {}).get("code") == "source_snapshot",
                            label + " invalid source was not rejected with a snapshot error")
                else:
                    require(receipt.get("status") == 409 and response.get("error", {}).get("code") == "request_cancelled",
                            label + " unexpected failed search")
                continue
            counts["successful_searches"] += 1
            require(receipt.get("method") == "POST", label + " search method")
            require(response["query"] == payload["query"] and response["retrieval"] == payload["retrieval"]
                    and response["rerank"] == payload["rerank"], label + " request settings echo")
            require(response["source_budget"] == payload["source_budget"], label + " source budget echo")
            overrides = {}
            entries = payload.get("overlays", [])
            require(isinstance(entries, list) and len(entries) <= 16, label + " overlay input count")
            total_bytes = 0
            for item in entries:
                require(set(item) == {"path", "text", "version", "content_hash"}, label + " overlay input keys")
                require(relative(item["path"]) and item["path"] in saved_sources, label + " overlay path membership")
                encoded = item["text"].encode("utf-8")
                total_bytes += len(encoded)
                require(len(encoded) <= 256 * 1024 and len(item["path"].encode()) <= 512,
                        label + " overlay input byte cap")
                require(type(item["version"]) is int and 0 <= item["version"] <= 2**53 - 1,
                        label + " overlay version")
                require(item["content_hash"] == digest(encoded), label + " literal overlay input hash")
                require(item["path"] not in overrides, label + " duplicate overlay input")
                overrides[item["path"]] = item
            require(total_bytes <= 1024 * 1024 and len(request_bytes) <= (2 * 1024 * 1024 if entries else 65536),
                    label + " encoded request/input budget")
            if overrides:
                counts["overlay_searches"] += 1
                overlay_calls.append((payload, response))
                scope = response["scope"]
                require(scope.get("source_state") == "overlay" and bool(HASH.fullmatch(scope.get("saved_source_manifest", ""))),
                        label + " overlay scope")
                expected = [{key: value[key] for key in ("path", "version", "content_hash")} for value in overrides.values()]
                require(sorted(scope["overlays"], key=lambda r: r["path"]) == sorted(expected, key=lambda r: r["path"]),
                        label + " all input provenance bindings")
            else:
                require(response.get("scope", {}).get("source_state") != "overlay", label + " unexpected overlay scope")
            results, observed = response["results"], receipt["returned_source_checks"]
            require(bool(results) and len(results) == len(observed) and len(results) <= payload["max_results"],
                    label + " nonempty bounded excerpts/observer inventory")
            for index, row in enumerate(results):
                previous_errors = len(errors)
                require(relative(row["path"]) and row["path"] in saved_sources, label + " result path membership")
                override = overrides.get(row["path"])
                if override:
                    require(row.get("source_origin") == "overlay" and row.get("buffer_version") == override["version"]
                            and row.get("buffer_hash") == override["content_hash"], label + " overlay result provenance")
                else:
                    require(row.get("source_origin") in (None, "saved") and "buffer_version" not in row and "buffer_hash" not in row
                            and (not overrides or row.get("source_origin") == "saved"), label + " saved result provenance")
                lines = physical_lines(override["text"] if override else saved_sources[row["path"]])
                a, b, end = row["start_line"], row["end_line"], row["window_end_line"]
                require(all(type(value) is int for value in (a, b, end)) and 1 <= a <= b <= end <= len(lines),
                        label + " physical bounds")
                excerpt, window = "".join(lines[a - 1:b]), "".join(lines[a - 1:end])
                require(row["source"] == excerpt and row["source_hash"] == digest(excerpt)
                        and row["content_hash"] == digest(window), label + " exact input/saved excerpt and full-window hash")
                require(len(physical_lines(row["source"])) == b - a + 1, label + " physical line count")
                require(type(row["truncated"]) is bool and row["truncated"] == (b < end), label + " truncated window flag")
                flag = observed[index]
                require(all(flag.get(key) == row[key] for key in ("path", "symbol", "start_line", "end_line", "source_hash"))
                        and flag.get("exact_source", flag.get("exact_saved_source")) is True, label + " observer flags disagree")
                if len(errors) == previous_errors:
                    counts["exact_excerpts"] += 1
                    counts["overlay_excerpts" if override else "saved_excerpts"] += 1
            require(response["total_source_chars"] == sum(len(row["source"]) for row in results)
                    and response["total_source_chars"] <= payload["source_budget"], label + " actual source characters")
            usage = response["token_usage"]
            actual = count_tokens(compact(context_payload(response)))
            require(usage["encoding"] == "cl100k_base" and usage["budget"] == payload["token_budget"]
                    and type(usage["tokens"]) is int and usage["tokens"] == actual <= usage["budget"], label + " actual compact token count/budget")
            tokens.append(actual)
            counts["token_payloads"] += 1
        except (KeyError, TypeError, ValueError, IndexError, UnicodeError, OverflowError) as error:
            require(False, label + f" malformed retained fields: {error}")
    cancels = [r for r in receipts if r.get("endpoint") == "/cancel" and r.get("status") == 200]
    searches = [r for r in receipts if r.get("endpoint") == "/search"]
    linked = [r for c in cancels for r in searches if (c.get("request") or {}).get("request_id")
              == (r.get("request") or {}).get("request_id") and (r.get("request") or {}).get("request_id")]
    require(bool(linked), "No independently linked cancellation/search ID")
    for cancel in cancels:
        require(cancel["response"].get("request_id") == cancel["request"].get("request_id")
                and cancel["response"].get("cancelled") is True, "Cancellation acknowledgement mismatch")
    require(counts["successful_searches"] >= 2 and counts["exact_excerpts"] > 0, "Insufficient successful retained source evidence")
    saved_workflow = None
    if controlled_edits:
        saved_workflow = audit_saved_edits(record, sources, phase_calls, source_hashes)
        errors.extend(saved_workflow["errors"])
    if record.get("overlay_smoke"):
        try:
            workflow = host["overlays"]
            required = {"two_dirty_inputs_and_literal_bom_unicode_provenance", "exact_plaintext_dirty_preview",
                        "navigation_uses_same_dirty_document", "saved_result_in_overlay_response_navigates",
                        "changed_other_input_blocks_navigation_and_retains_preview", "new_search_rebinds_current_versions",
                        "saved_status_not_replaced_by_overlay_index", "later_saved_search_uses_disk_and_blocks_dirty_target",
                        "no_files_saved_or_modified"}
            require(workflow.get("passed") is True and required <= workflow["checks"].keys()
                    and all(flag is True for flag in workflow["checks"].values()), "Overlay UI assertion inventory failed")
            require(workflow["source_before_sha256"] == workflow["source_after_sha256"] == source_hashes,
                    "Overlay host fixture bytes changed")
            require(len(overlay_calls) == 2, "Expected two retained overlay workflow searches")
            first, second = overlay_calls[0], overlay_calls[1]
            require(first[1]["scope"]["overlays"] == workflow["input_buffers"]
                    and second[1]["scope"]["overlays"] == workflow["later_buffer_versions"], "UI buffer bindings are not linked to HTTP responses")
            a, b = ({row["path"]: row for row in call[0]["overlays"]} for call in (first, second))
            require(set(a) == set(b) == {"src/decorate.js", "src/delta.ts"}, "Expected mixed dirty buffer inventory")
            expected_js = sources["src/decorate.js"].replace("decorate_code", "decorate_unsaved", 1).replace(' + "]"', ' + "]🧪"', 1)
            expected_ts = 'export function calculate_unsaved_ratio(before: number, after: number): number {\n  return after / before;\n}\n'
            require(a["src/decorate.js"]["text"] == b["src/decorate.js"]["text"] == expected_js,
                    "Dirty JS Unicode/BOM text differs from reviewed host edit")
            require(a["src/delta.ts"]["text"] == expected_ts and b["src/delta.ts"]["text"] == expected_ts + '// edit after the response\n'
                    and a["src/decorate.js"]["version"] == b["src/decorate.js"]["version"]
                    and b["src/delta.ts"]["version"] > a["src/delta.ts"]["version"], "Only-other-input edit/research provenance differs")
            last = searches[-1]
            require(last.get("status") == 200 and not last["request"].get("overlays")
                    and any(row["symbol"] == "decorate_code" for row in last["response"]["results"])
                    and all(row["symbol"] not in ("decorate_unsaved", "calculate_unsaved_ratio") for row in last["response"]["results"]),
                    "Final saved fallback does not reflect disk source")
            base = record["backend_status"]
            statuses = [r["response"] for r in receipts if r.get("endpoint") == "/status" and r.get("status") == 200]
            require(all(s["windows"] == base["windows"] and s["source_manifest"] == base["source_manifest"] for s in statuses),
                    "Saved status snapshot was replaced during overlay workflow")
        except (KeyError, TypeError, ValueError, IndexError) as error:
            require(False, f"Malformed overlay workflow inventory: {error}")
    return {"passed": not errors, "errors": errors, "counts": counts, "compact_token_counts": tokens,
            "source_manifest_sha256": manifest, "linked_cancelled_search_statuses": [r.get("status") for r in linked],
            "source_write_mode": "controlled_edits_restored" if controlled_edits else "unchanged_authored_fixture",
            **({"saved_edit_workflow": saved_workflow} if saved_workflow is not None else {}),
            "limits": ["Original response byte hashes are captured but cannot be independently reconstructed from parsed/redacted JSON.",
                       "Request hashes and normalized redacted-response hashes are recomputed using JSON.stringify.",
                       "Source, bindings, budgets and callback IDs are recomputed; UI navigation/staleness behavior relies on executed host assertions and reviewed test source.",
                       "Observer intervals include the relay; cancellation does not prove interruption of a Metal forward.",
                       "Authored trusted fixture only; no relevance, coding-correctness, Restricted Mode or cold-install claim."]}


def audit_saved_edits(record, sources, phase_calls, source_hashes):
    """Cross-link saved phases, watcher publication, deletion and explicit restoration."""
    errors = []
    def require(condition, message):
        if not condition:
            errors.append(message)
    watcher_receipts = []
    try:
        host, receipts = record["host"], record["receipts"]
        workflow = host["saved_edits"]
        required = {"saved_edit_outside_excerpt_blocks_old_navigation",
                    "new_search_updates_whole_file_witness_without_changing_excerpt",
                    "watcher_observes_saved_typescript_before_search", "saved_semantic_edit_has_current_navigation",
                    "tracked_rename_updates_paths", "saved_delete_removes_active_windows",
                    "invalid_saved_source_never_delivers_stale_results", "tracked_source_and_git_state_restored"}
        require(workflow.get("passed") is True and required <= workflow["checks"].keys()
                and all(value is True for value in workflow["checks"].values()), "Saved edit UI assertion inventory failed")
        require(workflow["source_before_sha256"] == workflow["source_after_sha256"] == source_hashes,
                "Controlled fixture edits were not restored byte-exactly")
        require(record["independent_receipt_checks"].get("source_restored_after_controlled_edits") is True
                and record["independent_receipt_checks"].get("source_unchanged") is not True,
                "Saved edit proof conflates restoration with unchanged source")
        phases = [value["phase"] for value in phase_calls]
        compressed = [value for i, value in enumerate(phases) if i == 0 or phases[i - 1] != value]
        expected = [name for name, _ in saved_edit_variants(sources)] + ["initial"]
        require(compressed == expected, "Missing, reordered or unreviewed controlled source phases")
        calls = {phase: receipts[next(c["receipt"] for c in phase_calls if c["phase"] == phase)]
                 for phase in expected[:-1]}
        outside, semantic = calls["outside_excerpt_edit"], calls["semantic_typescript_edit"]
        before = next(row for row in calls["initial"]["response"]["results"] if row["symbol"] == "normalize_item")
        after = next(row for row in outside["response"]["results"] if row["symbol"] == "normalize_item")
        require(before["source"] == after["source"] and before["source_hash"] == after["source_hash"]
                and outside["before_source_sha256"] != calls["initial"]["before_source_sha256"],
                "Outside-excerpt edit did not preserve the excerpt while changing the file witness")
        require(any(row["symbol"] == "calculate_saved_delta" and row["path"] == "src/delta.ts"
                    for row in semantic["response"]["results"]), "Saved semantic edit is absent from current results")
        moved, deleted = calls["tracked_javascript_rename"], calls["typescript_delete"]
        require(any(row["path"] == "src/decorate-renamed.js" for row in moved["response"]["results"])
                and all(row["path"] != "src/decorate.js" for row in moved["response"]["results"]),
                "Tracked rename did not replace the old active path")
        require(deleted["response"]["indexstats"]["total_windows"] == 4
                and all(row["path"] != "src/delta.ts" for row in deleted["response"]["results"]),
                "Saved delete did not remove the active windows")
        last = receipts[phase_calls[-1]["receipt"]]
        require(last["status"] == 200 and last["response"]["indexstats"]["total_windows"] == 5,
                "Final restored source was not reindexed/searchable")
        observations = workflow["observations"]
        previous = outside["response"]["indexstats"]["source_manifest"]
        changed = semantic["response"]["indexstats"]["source_manifest"]
        require(observations["previous_manifest"] == previous and observations["changed_manifest"] == changed
                and previous != changed, "Watcher manifest observation is not linked to saved searches")
        watcher_receipts = [i for i, receipt in enumerate(receipts) if receipt.get("endpoint") == "/status"
                            and receipt.get("status") == 200 and receipt["response"].get("source_manifest") == changed
                            and timestamp(outside["finished_utc"]) <= timestamp(receipt["started_utc"])
                            <= timestamp(receipt["finished_utc"]) <= timestamp(semantic["started_utc"])]
        require(bool(watcher_receipts), "No changed watcher status was retained before the semantic search")
        for i in watcher_receipts:
            status = receipts[i]["response"]
            require(status["watcher"]["running"] is True and status["ready"] is True
                    and status["last_error"] is None and status["windows"] == 5,
                    "Watcher changed status was not ready and current")
        require(not any(receipt.get("endpoint") in ("/search", "/index")
                        and timestamp(outside["finished_utc"]) < timestamp(receipt["started_utc"])
                        < timestamp(receipts[watcher_receipts[0]]["finished_utc"])
                        for receipt in receipts) if watcher_receipts else False,
                "Watcher observation was preceded by a query/index refresh")
    except (KeyError, TypeError, ValueError, IndexError, StopIteration) as error:
        require(False, f"Malformed saved edit workflow: {type(error).__name__}")
    return {"passed": not errors, "errors": errors, "search_phases": phase_calls,
            "watcher_status_receipts_before_semantic_search": watcher_receipts,
            "limits": ["Controlled saved edits occurred; final byte equality establishes restoration, not unchanged files throughout.",
                       "Old-navigation rejection, preview retention, clean Git state and navigation execution rely on reviewed host assertions."]}


def audit_runtime_proof(proof, artifact_bytes, *, project_root=ROOT, source_ref=None, source_snapshot=None):
    """Check the companion identity and reviewed source bytes, never model weights."""
    errors, checked = [], []
    def require(condition, message):
        if not condition:
            errors.append(message)
    try:
        expected = proof.get("host_artifact_sha256", proof.get("successful_host_artifact_sha256"))
        require(expected == digest(artifact_bytes), "Runtime proof is not bound to this host artifact")
        require(proof.get("checks_passed") is True, "Runtime capture did not pass")
        status = proof["backend_status_after"]
        require(status["embedding_initialized"] is True and status["dense_ready"] is True
                and status["closed"] is False, "Resident model/dense status was not captured")
        model = status["store"]["embedding"]
        require(model["id"] == "Qwen/Qwen3-Embedding-0.6B"
                and model["revision"] == "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
                and model["quantized"] is False and model["weights_dtype"] == "bfloat16"
                and model["dimensions"] == 1024, "Qwen checkpoint/precision/dimensions differ")
        require(status["extractor"]["installed_parsers"] == status["extractor"]["parser_pins"]
                == {"tree-sitter": "0.26.0", "tree-sitter-javascript": "0.25.0", "tree-sitter-typescript": "0.23.2"},
                "Runtime parser pins differ")
        def prefix(path):
            return path.startswith(("src/", "scripts/", "docs/", "test/"))
        for group, base in (("backend_implementation_sha256", Path(project_root)),
                            ("extension_source_sha256", Path(project_root) / "extensions/vscode")):
            values = proof[group]
            require(bool(values), f"Empty {group} inventory")
            for filename, expected in values.items():
                safe = relative(filename) and (prefix(filename) or filename.startswith("tests/")
                       or filename in ("requirements-languages.lock", "requirements-mac.lock", "pyproject.toml"))
                require(safe and bool(HASH.fullmatch(expected)), "Unsafe or malformed source hash entry")
                if not safe:
                    continue
                full = base / filename
                require(full.resolve().is_relative_to(base.resolve()), "Source hash path leaves checkout")
                if not full.resolve().is_relative_to(base.resolve()):
                    continue
                archived = None
                if source_snapshot:
                    snapshot_root = Path(source_snapshot)
                    candidates = [snapshot_root / full.relative_to(project_root)]
                    if group == "extension_source_sha256":
                        candidates.append(snapshot_root / filename)
                    archived = next((p for p in candidates if p.is_file()), None)
                if archived and archived.is_file():
                    require(archived.resolve().is_relative_to(Path(source_snapshot).resolve()), "Archived source leaves snapshot")
                    if not archived.resolve().is_relative_to(Path(source_snapshot).resolve()):
                        continue
                    raw = archived.read_bytes()
                elif source_ref:
                    path = str(full.relative_to(project_root))
                    raw = subprocess.check_output(["git", "-C", str(project_root), "show", f"{source_ref}:{path}"], timeout=10)
                else:
                    raw = full.read_bytes()
                require(digest(raw) == expected, f"Reviewed source bytes differ: {filename}")
                checked.append(str(full.relative_to(project_root)))
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError) as error:
        require(False, f"Malformed or unavailable runtime proof: {type(error).__name__}")
    return {"passed": not errors, "errors": errors, "source_files_checked": len(checked),
            "source_reference": "preserved snapshot plus checkout" if source_snapshot else source_ref or "current checkout bytes",
            "limits": ["Resident/pinned-model evidence is captured status plus launch/source provenance; this auditor loads no model or weights."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--fixture-json", type=Path, help="Optional source mapping instead of the checked-in fixture exports")
    parser.add_argument("--runtime-proof", type=Path)
    parser.add_argument("--source-checkout", type=Path, default=ROOT)
    parser.add_argument("--source-ref", help="Historical Git revision for runtime source hashes; current bytes by default")
    parser.add_argument("--source-snapshot", type=Path, help="Preserved executed source paths overriding later checkout edits")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    record = json.loads(args.artifact.read_text())
    sources = json.loads(args.fixture_json.read_text()) if args.fixture_json else fixture_sources(record.get("fixture_mode", "mixed"))
    result = audit_host(record, sources)
    if args.runtime_proof:
        result["runtime_proof"] = audit_runtime_proof(json.loads(args.runtime_proof.read_text()), args.artifact.read_bytes(),
                                                    project_root=args.source_checkout.resolve(), source_ref=args.source_ref,
                                                    source_snapshot=args.source_snapshot)
        result["errors"].extend("Runtime proof: " + error for error in result["runtime_proof"]["errors"])
        result["passed"] = result["passed"] and result["runtime_proof"]["passed"]
    result["artifact_sha256"] = digest(args.artifact.read_bytes())
    result["auditor_sha256"] = digest(Path(__file__).read_bytes())
    output = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output)
    else:
        print(output, end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
