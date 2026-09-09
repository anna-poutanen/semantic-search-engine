"""Corruption checks for retained editor receipts; no server, model, or editor."""
import copy
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("editor_host_audit", ROOT / "scripts/audit_editor_host.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
SOURCE = 'def implementation():\n    return "😀\u2028literal"\n'


def serialized(values):
    # These unit fixtures avoid floats; the integration audit uses real Node
    # JSON.stringify. Counting string length is a deterministic tokenizer stub.
    return [audit.compact(value) for value in values]


def sign(receipt):
    request = receipt["request"]
    receipt["request_sha256"] = audit.digest(b"" if request is None else audit.compact(request))
    receipt["response_sha256"] = "b" * 64  # Original bytes are deliberately unavailable.
    receipt["redacted_response_sha256"] = audit.digest(audit.compact(receipt["response"]))


def valid_record():
    sources = {"src/inventory.py": SOURCE}
    manifest = audit.digest(audit.compact({"entries": ["inventory.py"], "files": {"src/inventory.py": audit.digest(SOURCE)}}))
    options = {"query": "behavior", "retrieval": "dense", "rerank": False, "max_results": 5,
               "source_budget": 12000, "token_budget": 4096, "request_id": "first"}
    row = {"path": "src/inventory.py", "symbol": "implementation", "start_line": 1, "end_line": 2,
           "window_end_line": 2, "source": SOURCE, "source_hash": audit.digest(SOURCE),
           "content_hash": audit.digest(SOURCE), "truncated": False}
    response = {**options, "results": [row], "total_source_chars": len(SOURCE), "budget_exhausted": False,
                "token_usage": {"encoding": "cl100k_base", "tokens": 0, "budget": 4096, "truncated": False}}
    for _ in range(5):
        response["token_usage"]["tokens"] = len(audit.compact(audit.context_payload(response)))
    moment = "2026-10-13T10:00:00Z"

    def receipt(endpoint, request, value, status=200):
        r = {"endpoint": endpoint, "method": "GET" if request is None else "POST", "request": request,
             "response": value, "status": status, "started_utc": moment, "finished_utc": moment,
             "backend_finished_utc": moment, "returned_source_checks": []}
        if endpoint == "/search":
            r.update(before_source_sha256=audit.digest(SOURCE), after_source_sha256=audit.digest(SOURCE),
                     before_source_manifest_sha256=manifest, after_source_manifest_sha256=manifest)
            r["returned_source_checks"] = [{**{key: item[key] for key in ("path", "symbol", "start_line", "end_line", "source_hash")},
                                            "exact_saved_source": True} for item in value.get("results", [])]
        sign(r)
        return r
    cancel_id = "cancel-this"
    receipts = [receipt("/status", None, {}), receipt("/search", options, response),
                receipt("/cancel", {"request_id": cancel_id}, {"request_id": cancel_id, "cancelled": True}),
                receipt("/search", {**options, "request_id": cancel_id}, {"error": {"code": "request_cancelled"}}, 409),
                receipt("/search", {**options, "request_id": "last"}, copy.deepcopy(response))]
    record = {"passed": True, "exit": {"code": 0}, "timed_out": False, "started_utc": moment, "finished_utc": moment,
              "source_before_manifest_sha256": manifest, "source_after_manifest_sha256": manifest,
              "source_before_sha256": audit.digest(SOURCE), "source_after_sha256": audit.digest(SOURCE),
              "host": {"passed": True, "checks": {key: True for key in ("connect", "search", "plaintext_preview",
                         "verified_navigation", "cancel_command", "replacement_and_late_suppression")}}, "receipts": receipts}
    return record, sources


def check(record, sources):
    return audit.audit_host(record, sources, serializer=serialized, count_tokens=len)


def test_valid_synthetic_proof_is_read_only_and_keeps_unverifiable_original_bytes_explicit():
    record, sources = valid_record()
    before = copy.deepcopy(record)
    outcome = check(record, sources)
    assert outcome["passed"] and outcome["counts"]["exact_excerpts"] == 2
    assert outcome["counts"]["original_request_hashes"] == 5
    assert record == before
    assert any("cannot be independently reconstructed" in limit for limit in outcome["limits"])
    assert len(audit.physical_lines(SOURCE)) == 2


@pytest.mark.parametrize("mutation,reason", [
    (lambda r: r["response"]["results"][0].update(source="def different():\n return 9\n",
            source_hash=audit.digest("def different():\n return 9\n"), content_hash=audit.digest("def different():\n return 9\n")), "exact input/saved excerpt"),
    (lambda r: r["response"]["results"][0].update(end_line=1), "exact input/saved excerpt"),
    (lambda r: r["response"]["results"][0].update(buffer_version=1), "saved result provenance"),
    (lambda r: r["response"]["token_usage"].update(tokens=0), "actual compact token"),
    (lambda r: r.update(returned_source_checks=[]), "observer inventory"),
    (lambda r: r.update(after_source_manifest_sha256="0" * 64), "saved source manifest"),
    (lambda r: r["response"].update(total_source_chars=1), "actual source characters"),
])
def test_resigned_payload_corruption_is_detected_despite_positive_observer_flags(mutation, reason):
    record, sources = valid_record()
    receipt = record["receipts"][1]
    mutation(receipt)
    sign(receipt)  # A hash alone is not evidence of correct source or accounting.
    result = check(record, sources)
    assert not result["passed"]
    assert any(reason in error for error in result["errors"])


def test_hash_and_cancellation_link_corruptions_are_detected():
    record, sources = valid_record()
    record["receipts"][0]["request_sha256"] = "0" * 64
    record["receipts"][1]["redacted_response_sha256"] = "0" * 64
    record["receipts"][2]["request"]["request_id"] = "no-matching-search"
    record["receipts"][2]["response"]["request_id"] = "no-matching-search"
    sign(record["receipts"][2])
    result = check(record, sources)
    assert not result["passed"]
    assert any("original request hash" in error for error in result["errors"])
    assert any("redacted response hash" in error for error in result["errors"])
    assert any("linked cancellation" in error for error in result["errors"])


def test_incomplete_failed_or_vacuous_host_inventory_does_not_pass():
    record, sources = valid_record()
    for change in (lambda r: r.update(passed=False), lambda r: r["host"].update(checks={}),
                   lambda r: r.update(receipts=[]), lambda r: r.update(timed_out=True)):
        altered = copy.deepcopy(record)
        change(altered)
        assert not check(altered, sources)["passed"]


def test_failed_overlay_claim_cannot_reuse_saved_only_evidence():
    record, sources = valid_record()
    record["overlay_smoke"] = True
    assert not check(record, sources)["passed"]


@pytest.mark.skipif(not shutil.which("node") or not (ROOT / "results/editor-workflows-2026-10-13/overlay-host.json").is_file(),
                    reason="Retained actual host artifact and Node JSON recorder required")
@pytest.mark.parametrize("change,reason", [
    ("binding", "all input provenance bindings"),
    ("origin", "overlay result provenance"),
    ("input_hash", "literal overlay input hash"),
    ("status", "Saved status snapshot"),
])
def test_actual_retained_overlay_receipts_reject_resigned_provenance_corruption(change, reason):
    # These are retained public authored-fixture receipts, not quality labels.
    record = json.loads((ROOT / "results/editor-workflows-2026-10-13/overlay-host.json").read_text())
    sources = audit.fixture_sources("mixed")
    target = next(row for row in record["receipts"] if (row.get("request") or {}).get("overlays"))
    if change == "binding":
        target["response"]["scope"]["overlays"].pop()
    elif change == "origin":
        next(row for row in target["response"]["results"] if row.get("source_origin") == "overlay")["source_origin"] = "saved"
    elif change == "input_hash":
        target["request"]["overlays"][0]["content_hash"] = "0" * 64
    else:
        target = next(row for row in record["receipts"] if row["endpoint"] == "/status")
        target["response"]["source_manifest"] = "0" * 64
    encoded = audit.ecma_serializations([target["request"], target["response"]])
    target["request_sha256"] = audit.digest(encoded[0])
    target["redacted_response_sha256"] = audit.digest(encoded[1])
    result = audit.audit_host(record, sources)
    assert not result["passed"]
    assert any(reason in error for error in result["errors"])


def runtime_fixture(tmp_path):
    root, archive = tmp_path / "checkout", tmp_path / "executed"
    backend, extension = root / "src/model.py", root / "extensions/vscode/scripts/smoke-host.cjs"
    backend.parent.mkdir(parents=True)
    extension.parent.mkdir(parents=True)
    backend.write_bytes(b"# backend source\n")
    extension.write_bytes(b"// later host version\n")
    captured = archive / "scripts/smoke-host.cjs"
    captured.parent.mkdir(parents=True)
    captured.write_bytes(b"// actually executed host\n")
    parsers = {"tree-sitter": "0.26.0", "tree-sitter-javascript": "0.25.0", "tree-sitter-typescript": "0.23.2"}
    artifact = b'{"receipt":"fixture"}\n'
    proof = {"host_artifact_sha256": audit.digest(artifact), "checks_passed": True,
             "backend_status_after": {"embedding_initialized": True, "dense_ready": True, "closed": False,
                 "store": {"embedding": {"id": "Qwen/Qwen3-Embedding-0.6B",
                            "revision": "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3", "quantized": False,
                            "weights_dtype": "bfloat16", "dimensions": 1024}},
                 "extractor": {"installed_parsers": parsers, "parser_pins": parsers}},
             "backend_implementation_sha256": {"src/model.py": audit.digest(backend.read_bytes())},
             "extension_source_sha256": {"scripts/smoke-host.cjs": audit.digest(captured.read_bytes())}}
    return root, archive, artifact, proof


def test_historical_extension_relative_snapshot_overrides_later_checkout_bytes(tmp_path):
    root, archive, artifact, proof = runtime_fixture(tmp_path)
    before = (root / "extensions/vscode/scripts/smoke-host.cjs").read_bytes()
    outcome = audit.audit_runtime_proof(proof, artifact, project_root=root, source_snapshot=archive)
    assert outcome["passed"] and outcome["source_files_checked"] == 2
    assert (root / "extensions/vscode/scripts/smoke-host.cjs").read_bytes() == before
    assert not audit.audit_runtime_proof(proof, artifact, project_root=root)["passed"]


@pytest.mark.parametrize("mutation,reason", [
    ("artifact", "not bound"), ("snapshot", "source bytes differ"),
    ("parser", "Runtime parser pins"), ("outside", "Unsafe"), ("symlink", "leaves snapshot"),
])
def test_runtime_proof_rejects_binding_source_pin_and_escape_corruption(tmp_path, mutation, reason):
    root, archive, artifact, proof = runtime_fixture(tmp_path)
    if mutation == "artifact":
        artifact += b" "
    elif mutation == "snapshot":
        (archive / "scripts/smoke-host.cjs").write_bytes(b"// replaced\n")
    elif mutation == "parser":
        proof["backend_status_after"]["extractor"]["installed_parsers"] = {}
    elif mutation == "outside":
        proof["extension_source_sha256"] = {"../secret": "a" * 64}
    else:
        outside = tmp_path / "outside.cjs"
        outside.write_bytes(b"// actually executed host\n")
        captured = archive / "scripts/smoke-host.cjs"
        captured.unlink()
        captured.symlink_to(outside)
    outcome = audit.audit_runtime_proof(proof, artifact, project_root=root, source_snapshot=archive)
    assert not outcome["passed"] and any(reason in error for error in outcome["errors"])


SAVED_ARTIFACT = ROOT / "results/editor-workflows-2026-10-13/saved-edits-host.json"


@pytest.mark.skipif(not shutil.which("node") or not SAVED_ARTIFACT.is_file(), reason="Retained saved-edit proof required")
def test_actual_saved_edit_phases_are_reconstructed_and_restoration_is_distinct():
    record = json.loads(SAVED_ARTIFACT.read_text())
    outcome = audit.audit_host(record, audit.fixture_sources("mixed"))
    assert outcome["passed"]
    assert outcome["counts"]["exact_excerpts"] == 49
    assert outcome["source_write_mode"] == "controlled_edits_restored"
    assert outcome["saved_edit_workflow"]["watcher_status_receipts_before_semantic_search"]
    assert outcome["counts"]["successful_searches"] == 10


@pytest.mark.skipif(not shutil.which("node") or not SAVED_ARTIFACT.is_file(), reason="Retained saved-edit proof required")
@pytest.mark.parametrize("change,reason", [
    ("rename", "result path membership"), ("invalid", "failed search returned stale"),
    ("watcher", "No changed watcher status"), ("restoration", "conflates restoration"),
    ("phase", "Missing, reordered"),
])
def test_resigned_saved_workflow_corruption_cannot_pass_positive_host_flags(change, reason):
    record = json.loads(SAVED_ARTIFACT.read_text())
    target = None
    if change == "rename":
        target = next(row for row in record["receipts"] if any(result["path"] == "src/decorate-renamed.js"
                      for result in (row.get("response") or {}).get("results", [])))
        next(row for row in target["response"]["results"] if row["path"] == "src/decorate-renamed.js")["path"] = "src/decorate.js"
    elif change == "invalid":
        target = next(row for row in record["receipts"] if (row.get("response") or {}).get("error", {}).get("code") == "source_snapshot")
        target["response"]["results"] = [copy.deepcopy(record["receipts"][2]["response"]["results"][0])]
    elif change == "watcher":
        changed = record["host"]["saved_edits"]["observations"]["changed_manifest"]
        record["receipts"] = [row for row in record["receipts"] if not (row["endpoint"] == "/status"
                              and row["response"].get("source_manifest") == changed)]
    elif change == "restoration":
        record["independent_receipt_checks"]["source_unchanged"] = True
    else:
        target = next(row for row in record["receipts"] if (row.get("response") or {}).get("error", {}).get("code") == "source_snapshot")
        record["receipts"].remove(target)
        target = None
    if target:
        encoded = audit.ecma_serializations([target["request"], target["response"]])
        target["request_sha256"] = audit.digest(encoded[0])
        target["redacted_response_sha256"] = audit.digest(encoded[1])
    outcome = audit.audit_host(record, audit.fixture_sources("mixed"))
    assert not outcome["passed"] and any(reason in error for error in outcome["errors"])
