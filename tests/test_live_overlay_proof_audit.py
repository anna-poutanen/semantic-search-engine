"""Proof corruption checks use saved authored fixtures, never a model or HTTP."""
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("overlay_proof_audit", ROOT / "scripts/audit_live_overlay_proof.py")
auditor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(auditor)
SOURCE = ROOT / "results/live-overlays-2026-10-13/probe-revision02-reviewed.py.txt"


@pytest.fixture
def saved_proof(tmp_path):
    source = ROOT / "results/live-overlays-2026-10-13/proof.json"
    path = tmp_path / "proof.json"
    path.write_bytes(source.read_bytes())
    return path


def test_sorted_receipt_restores_original_wire_order_without_mutation(saved_proof):
    before = saved_proof.read_bytes()
    result = auditor.audit(saved_proof, SOURCE)
    assert [row["tokens"] for row in result["response_proofs"]] == [850, 846, 847, 441, 442]
    assert result["saved_cache_membership"] == ["saved.sqlite3"]
    assert len(result["nonreturned_inputs_in_narrow_response"]) == 2
    assert result["observed_model_api_deltas"]["document_texts"] == 15
    assert saved_proof.read_bytes() == before


@pytest.mark.parametrize("change,expected", [
    ("counter", "Independent compact token"),
    ("binding", "input binding"),
    ("saved_hash", "Saved-file raw byte"),
    ("cache_mtime", "Saved DB/sidecar"),
    ("model_calls", "model API counter"),
    ("old_revision", "Probe execution checks"),
    ("reopen", "Saved reopen"),
])
def test_receipt_self_reports_do_not_hide_contradictory_evidence(saved_proof, change, expected):
    proof = json.loads(saved_proof.read_text())
    if change == "counter":
        proof["responses"][0]["token_usage"]["tokens"] += 1
    elif change == "binding":
        proof["responses"][3]["scope"]["overlays"].pop()
    elif change == "saved_hash":
        proof["source_before_sha256"]["src/value.py"] = "0" * 64
        proof["source_after_sha256"] = dict(proof["source_before_sha256"])
    elif change == "cache_mtime":
        proof["saved_cache_after"]["saved.sqlite3"]["mtime_ns"] += 1
    elif change == "model_calls":
        proof["embedding_after_overlay_requests"]["completed_query_calls"] -= 1
    elif change == "old_revision":
        proof["probe_revision"] = 1
    else:
        proof["persistent_reopen"]["embedding_calls"]["completed_document_calls"] = 1
    # All ten probe check flags remain True; the independent audit must fail.
    saved_proof.write_text(json.dumps(proof, sort_keys=True, ensure_ascii=False))
    with pytest.raises(ValueError, match=expected):
        auditor.audit(saved_proof, SOURCE)
