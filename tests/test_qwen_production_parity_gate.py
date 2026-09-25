"""Certificate failures must reject native execution, without model loading."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/validate_production_qwen.py"
spec = importlib.util.spec_from_file_location("production_parity_gate", SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def case():
    return {"expected_input_ids_sha256": "literal-input", "expected": {
        "query_sha256": "query", "source_sha256": "source", "delivered_sha256": "delivered", "total_tokens": 12}}


def completed(number=0, **extra):
    return {"event": "completed", "warmup": False, "request_id": f"production-parity-{number}",
            "input_ids_sha256": "literal-input", **extra}


def test_duplicate_native_completions_do_not_certify_unique_calls():
    result = gate.ledger_inventory([completed(), completed()], [case()])
    assert result["input_ids_mismatches"] == []
    assert result["ledger_complete"] is False


def test_missing_unknown_and_changed_native_inputs_cannot_pass():
    result = gate.ledger_inventory([completed(input_ids_sha256="changed"), completed(2)], [case(), case()])
    assert result["input_ids_mismatches"] == [0, 1]
    assert result["ledger_complete"] is False


def test_warmup_does_not_replace_a_real_original_pair():
    result = gate.ledger_inventory([completed(warmup=True)], [case()])
    assert result["ledger_complete"] is False
    assert result["input_ids_mismatches"] == [0]


def test_reference_change_rejected_before_owner_created(tmp_path, monkeypatch):
    original = tmp_path / "reference"
    original.mkdir()
    (original / "receipt.json").write_text("changed")
    gate.write(tmp_path / "frozen-plan.json", {"reference_artifact_sha256": {"receipt.json": "old"}})
    monkeypatch.setattr(gate, "ORIGINAL", original)
    import semantic_search.qwen_reranker as owner_module
    monkeypatch.setattr(owner_module, "QwenRerankerProcess", lambda **args: pytest.fail("Unexpected model owner"))
    with pytest.raises(ValueError, match="Original reference artifact changed"):
        gate.run(tmp_path, tmp_path / "python", tmp_path / "checkpoint")


def test_missing_native_ledger_is_distinct_from_exact_input_match():
    result = gate.ledger_inventory([], [case()])
    assert result == {"ledger_complete": False, "ledger_completed_pairs": 0,
                      "input_ids_mismatches": [0], "ledger_identity_mismatches": [0]}


def test_exact_sequential_singleton_inventory_with_separate_warmup_passes():
    warm = {"request_id": "generic-warmup", "warmup": True}
    identity = completed() | case()["expected"] | {"batch_size": 1, "forward_number": 2}
    rows = [warm | {"event": "started"}, warm | {"event": "completed"},
            identity | {"event": "started"}, identity]
    assert gate.ledger_inventory(rows, [case()])["ledger_complete"] is True
    rows[2]["source_sha256"] = "mutated"
    assert gate.ledger_inventory(rows, [case()])["ledger_complete"] is False
