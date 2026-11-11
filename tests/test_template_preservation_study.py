"""Invented end-to-end terminal decisions, immutable operands and quality gates."""
import base64
from copy import deepcopy
from dataclasses import asdict
import gzip
import json

import pytest

from experiments.dependency_context import study_inputs as inputs
from experiments.template_preservation import audit, study
from experiments.template_preservation.guard import apply_template_guard
from semantic_search import context
from semantic_search.corpus import extract_snippets


def invented_prepared(tmp_path):
    sources = {
        "src/render.py": 'def render(value):\n    return f"café {format_value(value)}"\n',
        "src/value.py": "def format_value(value):\n    return str(value)\n",
    }
    keys = {}
    snippets = extract_snippets(tmp_path, "src", 160, 40, sources=sources, definition_keys=keys)
    keys = {name: list(key) for name, key in keys.items()}
    files = {path: {"raw_base64": base64.b64encode(source.encode()).decode(),
        "bytes": len(source.encode()), "sha256": inputs.sha(source.encode()), "encoding": "utf-8"}
        for path, source in sources.items()}
    identity = {"dataset": "invented", "source_sha256": {path: row["sha256"] for path, row in files.items()},
                "definition_keys": keys}
    bundle = {"id": "sources_" + inputs.sha(inputs.canonical(identity)), "dataset": "invented",
        "sources": sources, "files": files, "inventory": [asdict(item) for item in snippets],
        "definition_keys": keys, "original_graph_edges": [], "original_revision": None}
    inputs.validate_bundle(bundle)
    rows = [{"id": item.id, "path": item.path, "symbol": item.symbol, "start_line": item.start_line,
        "end_line": item.end_line, "window_end_line": item.end_line, "source": item.text,
        "source_hash": item.content_hash, "content_hash": item.content_hash, "truncated": False,
        "origin": "retrieval", "related_to": None, "source_origin": "saved", "retrieval_score": .3}
        for item in snippets]
    rows.sort(key=lambda row: row["path"])

    def response(selected):
        result = {"query": "invented formatter and implementation", "results": deepcopy(selected),
            "match_status": "candidates", "total_source_chars": sum(len(row["source"]) for row in selected),
            "budget_exhausted": False, "token_usage": {"encoding": context.ENCODING,
                "tokens": 0, "budget": 4096, "truncated": False}}
        context.count_context(result)
        return result

    reference, proposal = response(rows), response(rows[1:])
    scoring = {"kind": "present", "qualified": True, "multi_file": True,
        "groups": [[{"path": row["path"], "symbol": row["symbol"],
                     "evidence_lines": [row["start_line"], row["end_line"]],
                     "evidence_sha256": inputs.sha(row["source"].encode())}] for row in rows]}
    records, operands = [], []
    for repeat in (1, 2, 3):
        operand = {"id": f"invented_{repeat}", "reference": deepcopy(reference), "proposal": deepcopy(proposal),
            "definition_keys": deepcopy(keys), "source_bundle_id": bundle["id"], "raw_rows": deepcopy(rows),
            "precoalesce_rows": deepcopy(rows), "limits": dict(inputs.LIMITS)}
        record = {**deepcopy(operand), "dataset": "invented", "query_id": "formatter_parts", "mode": "hybrid",
            "repetition": repeat, "quality": repeat == 1, "legacy": deepcopy(reference), "scoring": deepcopy(scoring)}
        operands.append(operand)
        records.append(record)
    return {"records": records, "operands": operands, "source_bundles": {bundle["id"]: bundle}}


def outcomes(path):
    with gzip.open(path / "outcomes.jsonl.gz", "rt") as stream:
        return [json.loads(line) for line in stream]


def test_reordered_operands_fail_before_any_candidate_call_or_attempt(tmp_path):
    prepared = invented_prepared(tmp_path)
    prepared["operands"].reverse()
    output = tmp_path / "reordered"

    def forbidden(*args, **kwargs):
        raise AssertionError("Candidate must not see a reordered snapshot")

    with pytest.raises(ValueError, match="membership/order"):
        study.replay(prepared, output, binding={}, guard=forbidden)
    assert not output.exists()


def test_actual_guard_replay_restores_exact_coverage_without_gold_or_input_mutation(tmp_path):
    prepared = invented_prepared(tmp_path)
    before, seen = deepcopy(prepared), []

    def guarded(reference, proposal, **kwargs):
        assert set(kwargs) == {"source_bundle", "definition_keys"}
        assert not {"scoring", "groups", "quality", "legacy", "raw_rows", "precoalesce_rows"}.intersection(kwargs)
        seen.append(deepcopy(kwargs))
        return apply_template_guard(reference, proposal, **kwargs)

    output = tmp_path / "run"
    binding = {"invented": True}
    summary = study.replay(prepared, output, binding=binding, guard=guarded)
    assert prepared == before and len(seen) == 3
    assert {path.name for path in output.iterdir()} == {"run-start.json", "outcomes.jsonl.gz", "summary.json"}
    start = inputs.read(output / "run-start.json")
    assert all(inputs.same(start[name], value) for name, value in binding.items())
    saved = outcomes(output)
    assert [row["id"] for row in saved] == [row["id"] for row in prepared["records"]]
    for row, operand in zip(saved, prepared["operands"]):
        assert inputs.same(row["response"], operand["reference"])
        assert row["compact_payload"] == inputs.compact_payload(operand["reference"])
        assert row["compact_sha256"] == inputs.sha(row["compact_payload"].encode())
        assert row["actual_compact_tokens"] == len(context.encoding().encode(row["compact_payload"], disallowed_special=()))
        assert row["actual_compact_tokens"] == row["response"]["token_usage"]["tokens"] <= 4096
        assert row["guard_receipt"]["protected_ids"] == [operand["reference"]["results"][0]["id"]]
    assert summary["decisions"] == 3 and summary["quality_occurrences"] == summary["distinct_units"] == 1
    assert summary["metrics"]["candidate"]["complete"] == 1
    assert summary["metrics"]["prior_role"]["complete"] == 0
    assert summary["quality_gate"]["qualified"] is True
    assert summary["quality_gate"]["distinct_complete_multi_gains_vs_prior_role"] == [["invented", "formatter_parts"]]
    assert summary["activity"]["changed_vs_prior_role"] == 3
    assert summary["activity"]["protected_rows"] == 3
    assert summary["restorations"] == [{"id": "invented_1", "dataset": "invented", "query_id": "formatter_parts",
                                       "mode": "hybrid", "multi_file": True}]
    assert all(value == 0 for value in summary["new_work"].values())
    for name, digest in summary["artifacts"].items():
        assert inputs.file_sha(output / name) == digest
    independently_reconstructed = audit.audit_outcomes(prepared, saved, context.encoding())
    assert all(inputs.same(summary[name], value) for name, value in independently_reconstructed.items())


def test_default_guard_matches_injected_actual_guard(tmp_path):
    prepared = invented_prepared(tmp_path)
    study.replay(prepared, tmp_path / "default", binding={})
    study.replay(prepared, tmp_path / "injected", binding={}, guard=apply_template_guard)
    assert outcomes(tmp_path / "default") == outcomes(tmp_path / "injected")


def test_complete_non_template_definition_keeps_exact_prior_role_gain(tmp_path):
    prepared = invented_prepared(tmp_path)
    for operand, record in zip(prepared["operands"], prepared["records"]):
        operand["reference"] = deepcopy(operand["proposal"])
        record["reference"] = deepcopy(operand["reference"])
    summary = study.replay(prepared, tmp_path / "plain", binding={})
    assert summary["activity"]["accepted"] == 3
    assert summary["activity"]["changed_vs_prior_role"] == summary["activity"]["protected_rows"] == 0
    assert summary["quality_gate"]["qualified"] is False and not summary["restorations"]


def test_valid_partial_definition_does_not_gain_protection_or_admit_raw_rows(tmp_path):
    prepared = invented_prepared(tmp_path)
    for operand, record in zip(prepared["operands"], prepared["records"]):
        row = operand["reference"]["results"][0]
        row["source"] = row["source"].splitlines(keepends=True)[0]
        row["end_line"], row["truncated"] = row["start_line"], True
        row["source_hash"] = inputs.sha(row["source"].encode())
        operand["reference"]["total_source_chars"] = sum(len(item["source"]) for item in operand["reference"]["results"])
        context.count_context(operand["reference"])
        record["reference"] = deepcopy(operand["reference"])
    output = tmp_path / "partial_definition"
    summary = study.replay(prepared, output, binding={})
    saved = outcomes(output)
    assert summary["activity"]["accepted"] == 3 and summary["activity"]["protected_rows"] == 0
    assert all(row["guard_receipt"]["reference_facts"][0]["status"] == "partial_definition" for row in saved)
    assert all(inputs.same(row["response"], operand["proposal"]) for row, operand in zip(saved, prepared["operands"]))
    assert all(len(row["response"]["results"]) == 1 for row in saved)
    recomputed = audit.audit_outcomes(prepared, saved, context.encoding())
    assert all(inputs.same(summary[name], value) for name, value in recomputed.items())


def test_guard_cannot_return_third_operand_and_cannot_mutate_frozen_inputs(tmp_path):
    prepared = invented_prepared(tmp_path)
    before = deepcopy(prepared)

    def malicious(reference, proposal, **kwargs):
        reference["extra"] = "third operand"
        proposal["extra"] = "another third operand"
        kwargs["definition_keys"].clear()
        kwargs["source_bundle"]["sources"].clear()
        return reference, {"accepted": True, "protected_ids": [], "fallback_reason": None}

    output = tmp_path / "third"
    with pytest.raises(ValueError, match="exact frozen operand"):
        study.replay(prepared, output, binding={}, guard=malicious)
    assert prepared == before
    assert inputs.read(output / "failure.json")["stored_outcomes"] == 0
    assert outcomes(output) == [] and not (output / "summary.json").exists()


@pytest.mark.parametrize("corruption", ["tokens", "source", "source_count", "token_budget"])
def test_exact_operand_still_requires_literal_source_and_real_compact_caps(tmp_path, corruption):
    prepared = invented_prepared(tmp_path)
    reference = prepared["operands"][0]["reference"]
    if corruption == "tokens":
        reference["token_usage"]["tokens"] += 1
    elif corruption == "source":
        reference["results"][0]["source"] += "# fabricated\n"
        reference["results"][0]["source_hash"] = inputs.sha(reference["results"][0]["source"].encode())
        reference["total_source_chars"] = sum(len(row["source"]) for row in reference["results"])
        context.count_context(reference)
    elif corruption == "source_count":
        reference["total_source_chars"] += 1
    else:
        reference["token_usage"]["budget"] += 1
        context.count_context(reference)
    prepared["records"][0]["reference"] = deepcopy(reference)

    def choose_reference(reference, proposal, **kwargs):
        return reference, {"accepted": False, "protected_ids": [], "fallback_reason": "invented"}

    output = tmp_path / corruption
    with pytest.raises(ValueError):
        study.replay(prepared, output, binding={}, guard=choose_reference)
    assert inputs.read(output / "failure.json")["stored_outcomes"] == 0
    assert not (output / "summary.json").exists()


def test_guard_error_preserves_finalized_partial_ledger_and_original_exception(tmp_path):
    prepared = invented_prepared(tmp_path)
    original = RuntimeError("invented second guard failure")
    calls = []

    def fail_second(reference, proposal, **kwargs):
        calls.append(reference)
        if len(calls) == 2:
            raise original
        return apply_template_guard(reference, proposal, **kwargs)

    output = tmp_path / "partial"
    with pytest.raises(RuntimeError) as caught:
        study.replay(prepared, output, binding={}, guard=fail_second)
    assert caught.value is original
    assert [row["id"] for row in outcomes(output)] == ["invented_1"]
    failure = inputs.read(output / "failure.json")
    assert failure["stored_outcomes"] == 1 and failure["resume_allowed"] is False
    assert not (output / "summary.json").exists()
    with pytest.raises(FileExistsError):
        study.replay(prepared, output, binding={})
    assert [row["id"] for row in outcomes(output)] == ["invented_1"]


def test_final_authority_drift_check_preserves_all_finalized_rows_without_success_summary(tmp_path):
    prepared = invented_prepared(tmp_path)
    output = tmp_path / "final_drift"
    original = ValueError("invented post-execution source/input drift")
    checks = []

    def final_check():
        checks.append(True)
        assert [row["id"] for row in outcomes(output)] == [row["id"] for row in prepared["records"]]
        assert not (output / "summary.json").exists()
        raise original

    with pytest.raises(ValueError) as caught:
        study.replay(prepared, output, binding={}, final_check=final_check)
    assert caught.value is original and checks == [True]
    assert len(outcomes(output)) == 3 and not (output / "summary.json").exists()
    failure = inputs.read(output / "failure.json")
    assert failure["stored_outcomes"] == 3 and failure["resume_allowed"] is False
    assert failure["error_type"] == "ValueError" and failure["error"] == str(original)
    assert set(failure["artifacts"]) == {"run-start.json", "outcomes.jsonl.gz"}
    assert all(inputs.file_sha(output / name) == digest for name, digest in failure["artifacts"].items())


@pytest.mark.parametrize("mutation", ["signed_zero_top", "signed_zero_score", "top_mapping_order", "row_mapping_order"])
def test_guard_cannot_rewrite_signed_zero_or_mapping_order_in_exact_operand(tmp_path, mutation):
    prepared = invented_prepared(tmp_path)
    for operand, record in zip(prepared["operands"], prepared["records"]):
        for name in ("reference", "proposal"):
            operand[name]["native_zero"] = 0.0
            operand[name]["results"][-1]["retrieval_score"] = 0.0
            record[name] = deepcopy(operand[name])
    before = deepcopy(prepared)

    def malicious(reference, proposal, **kwargs):
        if mutation == "signed_zero_top":
            reference["native_zero"] = -0.0
        elif mutation == "signed_zero_score":
            reference["results"][-1]["retrieval_score"] = -0.0
        elif mutation == "top_mapping_order":
            reference = dict(reversed(list(reference.items())))
        else:
            reference["results"][-1] = dict(reversed(list(reference["results"][-1].items())))
        assert inputs.same(reference, before["operands"][0]["reference"])
        return reference, {"accepted": False, "protected_ids": [], "fallback_reason": "invented"}

    output = tmp_path / mutation
    with pytest.raises(ValueError, match="exact frozen operand"):
        study.replay(prepared, output, binding={}, guard=malicious)
    assert prepared == before and outcomes(output) == []
    assert inputs.read(output / "failure.json")["stored_outcomes"] == 0
    assert not (output / "summary.json").exists()


def test_failure_receipt_write_error_does_not_hide_original_guard_error(tmp_path, monkeypatch):
    prepared = invented_prepared(tmp_path)
    original, receipt_error = RuntimeError("original guard"), OSError("failure receipt")
    write = study.durable.write_new

    def fail_guard(*args, **kwargs):
        raise original

    def fail_receipt(path, value):
        if path.name == "failure.json":
            raise receipt_error
        return write(path, value)

    monkeypatch.setattr(study.durable, "write_new", fail_receipt)
    with pytest.raises(RuntimeError) as caught:
        study.replay(prepared, tmp_path / "receipt", binding={}, guard=fail_guard)
    assert caught.value is original and caught.value.__cause__ is receipt_error


def test_existing_attempt_and_missing_parent_are_never_created_or_overwritten(tmp_path):
    prepared = invented_prepared(tmp_path)
    output = tmp_path / "kept"
    output.mkdir()
    marker = output / "marker"
    marker.write_text("preserve this")
    with pytest.raises(FileExistsError):
        study.replay(prepared, output, binding={})
    assert marker.read_text() == "preserve this" and list(output.iterdir()) == [marker]
    missing = tmp_path / "missing" / "attempt"
    with pytest.raises(ValueError, match="parent must.*exist"):
        study.replay(prepared, missing, binding={})
    assert not missing.parent.exists()


def summary_row(identifier="gain", *, quality=True, mode="dense", query="parts"):
    scores = {arm: {"covered_groups": 1, "complete": False, "single_file_first_hit": False}
              for arm in ("legacy", "coverage", "prior_role", "candidate")}
    scores["candidate"].update(covered_groups=2, complete=True)
    changes = {arm: {"group_gains": [2], "group_losses": [], "complete_gain": True,
        "complete_loss": False, "single_file_first_hit_loss": False} for arm in ("legacy", "coverage", "prior_role")}
    return {"id": identifier, "dataset": "invented", "query_id": query, "mode": mode, "repetition": 1,
        "quality": quality, "multi_file": True, "scores": scores, "comparisons": changes,
        "changed_vs_prior_role": True, "guard_receipt": {"accepted": False, "protected_ids": ["template"],
                                                           "fallback_reason": "protected_template_row_changed"}}


@pytest.mark.parametrize("arm", ["legacy", "coverage", "prior_role"])
@pytest.mark.parametrize("field,value", [("group_losses", [1]), ("complete_loss", True),
                                         ("single_file_first_hit_loss", True)])
def test_every_individual_loss_against_each_control_blocks_qualification(arm, field, value):
    row = summary_row()
    row["comparisons"][arm][field] = value
    summary = study.summarize([row])
    assert summary["quality_gate"]["qualified"] is False
    assert summary["quality_gate"]["individual_losses"][arm][0]["id"] == row["id"]


def test_equal_aggregate_groups_do_not_hide_a_required_group_exchange_loss():
    row = summary_row()
    row["scores"]["coverage"]["covered_groups"] = row["scores"]["candidate"]["covered_groups"]
    row["comparisons"]["coverage"].update(group_gains=[2], group_losses=[1])
    summary = study.summarize([row])
    assert summary["metrics"]["coverage"]["groups"] == summary["metrics"]["candidate"]["groups"]
    assert summary["quality_gate"]["qualified"] is False
    assert summary["quality_gate"]["individual_losses"]["coverage"][0]["group_losses"] == [1]


def test_only_quality_occurrences_count_and_modes_share_one_distinct_gain():
    first, second, repeated = summary_row(), summary_row("hybrid", mode="hybrid"), summary_row("repeat", quality=False)
    repeated["query_id"], repeated["repetition"] = "unscored_only", 3
    repeated["comparisons"]["legacy"]["group_losses"] = [1]
    summary = study.summarize([first, second, repeated])
    assert summary["decisions"] == 3 and summary["quality_occurrences"] == 2 and summary["distinct_units"] == 1
    assert summary["quality_gate"]["qualified"] is True
    assert summary["quality_gate"]["distinct_complete_multi_gains_vs_prior_role"] == [["invented", "parts"]]
    assert [row["id"] for row in summary["restorations"]] == ["gain", "hybrid"]
    assert summary["activity"]["changed_vs_prior_role"] == summary["activity"]["protected_rows"] == 3


def test_preserving_an_old_gain_is_not_incremental_and_single_file_gain_is_insufficient():
    row = summary_row()
    row["comparisons"]["prior_role"]["complete_gain"] = False
    assert study.summarize([row])["quality_gate"]["qualified"] is False
    row["comparisons"]["prior_role"]["complete_gain"] = True
    row["multi_file"] = False
    summary = study.summarize([row])
    assert summary["quality_gate"]["qualified"] is False
    assert summary["restorations"][0]["multi_file"] is False
    assert summary["quality_gate"]["fresh_native_required"] is True
    assert summary["quality_gate"]["default_change"] is False


@pytest.mark.parametrize("source_freeze", [None, True, 0, "short", "a" * 39, "A" * 40, "g" * 40])
def test_prepare_requires_full_source_freeze_before_parent_or_prepared_data_reads(tmp_path, monkeypatch, source_freeze):
    events = []
    monkeypatch.setattr(study, "source_authority", lambda root: events.append("source_authority"))

    def denied(*args, **kwargs):
        pytest.fail("Invalid source-freeze must not read source, parents, runtime or prepared operands")

    for name in ("source_hashes", "parent_binding", "prepared_binding", "verify_blobs"):
        monkeypatch.setattr(study, name, denied)
    monkeypatch.setattr(study.inputs, "load_prepared", denied)
    monkeypatch.setattr(study.authority, "runtime", denied)
    output = tmp_path / "plan"
    with pytest.raises(ValueError, match="Full prospective source-freeze SHA required"):
        study.prepare(output, source_freeze, root=tmp_path)
    assert events == ["source_authority"] and not output.exists()


def test_prepare_verifies_committed_source_before_loading_any_observed_inputs(tmp_path, monkeypatch):
    events, original = [], ValueError("invented frozen source mismatch")
    monkeypatch.setattr(study, "source_authority", lambda root: events.append("authority"))
    monkeypatch.setattr(study, "source_hashes", lambda root: {"experiments/invented.py": "1" * 64})

    def verify(hashes, commit, **kwargs):
        events.append(commit)
        raise original

    def denied(*args, **kwargs):
        pytest.fail("Unverified committed source must not load observed parents or prepared operands")

    monkeypatch.setattr(study, "verify_blobs", verify)
    for name in ("parent_binding", "prepared_binding"):
        monkeypatch.setattr(study, name, denied)
    monkeypatch.setattr(study.inputs, "load_prepared", denied)
    output = tmp_path / "plan"
    with pytest.raises(ValueError) as caught:
        study.prepare(output, "a" * 40, root=tmp_path)
    assert caught.value is original and events == ["authority", "a" * 40] and not output.exists()


def test_foreign_source_authority_failure_precedes_all_data_reads(tmp_path, monkeypatch):
    original = ValueError("invented foreign source")

    def source_authority(root):
        raise original

    def denied(*args, **kwargs):
        pytest.fail("Foreign executing source must not read data")

    monkeypatch.setattr(study, "source_authority", source_authority)
    monkeypatch.setattr(study, "source_hashes", denied)
    monkeypatch.setattr(study, "parent_binding", denied)
    with pytest.raises(ValueError) as caught:
        study.prepare(tmp_path / "plan", "a" * 40, root=tmp_path)
    assert caught.value is original


def test_new_source_closure_includes_guard_auditor_runner_tests_and_transitive_helpers(tmp_path):
    names = ["src/semantic_search/context.py", "scripts/research_role_context.py",
        "experiments/template_preservation/guard.py", "experiments/template_preservation/audit.py",
        "experiments/template_preservation/study.py", "experiments/dependency_context/study_inputs.py",
        "tests/test_template_preservation_study.py", "tests/test_template_preservation_guard.py",
        "docs/template-preservation-protocol.md", "pyproject.toml", "requirements-workflows.lock", "requirements-mac.lock",
        "results/semantic-model-development-2026-11-02/model-binding.json"]
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("invented binding for " + name)
    bound = study.source_hashes(tmp_path)
    assert set(bound) == set(names)
    assert all(bound[name] == inputs.file_sha(tmp_path / name) for name in names)


def test_real_checkout_inventory_binds_existing_package_locks_and_complete_new_sources():
    root = study.ROOT
    bound = study.source_hashes(root)
    assert inputs.same(bound, audit.source_hashes(root))
    assert {"pyproject.toml", "requirements-mac.lock", "requirements-languages.lock",
            "docs/template-preservation-protocol.md", "experiments/template_preservation/study.py",
            "experiments/template_preservation/guard.py", "experiments/template_preservation/audit.py",
            "tests/test_template_preservation_study.py"} <= set(bound)
    assert {path.name for path in root.glob("requirements*.lock")} <= set(bound)
    assert all((root / name).is_file() and inputs.file_sha(root / name) == digest for name, digest in bound.items())


def input_barrier_fixture():
    plan = {"source_freeze": {"commit": "a" * 40}, "parent": {"artifacts": {"saved.json": "1" * 64}},
        "prepared": {"artifacts": {"records.gz": "2" * 64}}, "counts": deepcopy(inputs.COUNTS),
        "runtime": {"tokenizer": {"sha256": "3" * 64}}}
    receipt = {"schema": "template_preservation_input_audit_v1", "status": "passed", "checks_passed": True,
        "classification": study.CLASSIFICATION, "plan_sha256": "4" * 64, **deepcopy(plan),
        "source_only_calls": deepcopy(inputs.ZERO_CALLS), "new_outcomes_computed": 0, "execution_commit": "b" * 40}
    return plan, receipt


@pytest.mark.parametrize("mutation", ["status", "checks", "classification", "plan_sha", "source_freeze", "parent",
    "prepared", "counts", "runtime", "source_call", "bool_zero", "outcomes", "bool_outcomes", "execution_commit"])
def test_execution_input_barrier_rejects_tampering_nonzero_work_and_type_aliases(mutation):
    plan, receipt = input_barrier_fixture()
    study.verify_input_audit(receipt, plan, "4" * 64)
    if mutation == "status":
        receipt["status"] = "pending"
    elif mutation == "checks":
        receipt["checks_passed"] = 1
    elif mutation == "classification":
        receipt["classification"] = "fresh"
    elif mutation == "plan_sha":
        receipt["plan_sha256"] = "5" * 64
    elif mutation in ("source_freeze", "parent", "prepared", "runtime"):
        receipt[mutation] = {}
    elif mutation == "counts":
        receipt["counts"]["native_decisions"] = 336
    elif mutation == "source_call":
        receipt["source_only_calls"]["guard"] = 1
    elif mutation == "bool_zero":
        receipt["source_only_calls"]["guard"] = False
    elif mutation == "outcomes":
        receipt["new_outcomes_computed"] = 1
    elif mutation == "bool_outcomes":
        receipt["new_outcomes_computed"] = False
    else:
        receipt["execution_commit"] = "short"
    with pytest.raises(ValueError, match="independent source-only input barrier"):
        study.verify_input_audit(receipt, plan, "4" * 64)
