"""Invented end-to-end CPU study, anti-regression gates and freeze contracts."""
from copy import deepcopy
import gzip
import json
import socket
import subprocess
import sys
import types
from unittest.mock import patch

import pytest

from experiments.dependency_context import study
from experiments.dependency_context import study_inputs as inputs
from semantic_search import context
from semantic_search.corpus import extract_snippets


def fixture(tmp_path):
    path = "src/pkg/api.py"
    source = ("class Engine:\n    def finish(self):\n        return 'done'\n\n"
              "def use(value: Engine):\n    return value.finish()\n\n"
              "def other():\n    return 'other'\n")
    keys = {}
    snippets = extract_snippets(tmp_path, source_root="src", sources={path: source}, definition_keys=keys)
    keys = {name: list(value) for name, value in keys.items()}
    rows = [{"id": s.id, "path": s.path, "symbol": s.symbol, "start_line": s.start_line,
        "end_line": s.end_line, "window_end_line": s.end_line, "source": s.text, "source_hash": s.content_hash,
        "content_hash": s.content_hash, "truncated": False, "origin": "retrieval", "related_to": None,
        "retrieval_score": .3} for s in snippets]

    def response(selected):
        value = {"query": "invented call and implementation", "results": deepcopy(selected),
            "total_source_chars": sum(len(r["source"]) for r in selected), "match_status": "candidates",
            "budget_exhausted": False, "token_usage": {"encoding": context.ENCODING,
                "tokens": 0, "budget": 4096, "truncated": False}}
        context.count_context(value)
        return value

    reference, proposal = response(rows[:2]), response(rows[1:])
    scoring = {"kind": "present", "qualified": False, "multi_file": True, "groups": [
        [{"path": path, "evidence_lines": [s.start_line, s.end_line]}] for s in snippets[:2]]}
    operands, records = [], []
    for repetition in (1, 2):
        operand = {"id": f"invented_{repetition}", "source_bundle_id": "invented_bundle",
            "reference": deepcopy(reference), "proposal": deepcopy(proposal), "definition_keys": deepcopy(keys),
            "raw_rows": deepcopy(rows), "precoalesce_rows": deepcopy(reference["results"]), "limits": dict(inputs.LIMITS)}
        record = {**deepcopy(operand), "dataset": "invented", "query_id": "two_parts", "mode": "dense",
            "repetition": repetition, "quality": repetition == 1, "legacy": deepcopy(reference), "scoring": deepcopy(scoring)}
        operands.append(operand)
        records.append(record)
    from dataclasses import asdict
    return {"operands": operands, "records": records, "source_bundles": {"invented_bundle": {
        "sources": {path: source}, "inventory": [asdict(s) for s in snippets], "definition_keys": keys}}}


def test_full_invented_run_restores_pair_and_scores_only_first_repetition(tmp_path):
    prepared = fixture(tmp_path)
    before = deepcopy(prepared)
    seen = []

    def guarded(reference, proposal, **kwargs):
        assert set(kwargs) == {"dependencies", "definition_keys"}
        seen.append(reference["query"])
        return study.apply_dependency_guard(reference, proposal, **kwargs)

    output = tmp_path / "outcomes"
    with study.cpu_only():
        summary = study.replay(prepared, output, binding={"invented": True}, guard=guarded)
    assert summary["decisions"] == 2 and summary["quality_occurrences"] == 1
    assert summary["distinct_units"] == 1 and summary["quality_gate"]["qualified"]
    assert summary["metrics"]["candidate"]["complete"] == 1
    assert summary["metrics"]["prior_role"]["complete"] == 0
    assert summary["quality_gate"]["distinct_complete_multi_gains_vs_prior_role"] == [["invented", "two_parts"]]
    assert summary["activity"]["changed_vs_prior_role"] == 2
    assert all(value == 0 for value in summary["new_work"].values())
    assert len(seen) == 2 and prepared == before
    assert sorted(p.name for p in output.iterdir()) == ["graphs.jsonl.gz", "outcomes.jsonl.gz", "run-start.json", "summary.json"]
    with gzip.open(output/"outcomes.jsonl.gz", "rt") as stream:
        rows = [json.loads(line) for line in stream]
    assert [r["response"] for r in rows] == [record["reference"] for record in prepared["records"]]
    assert all(context.serialize_context(context.compact_result(r["response"])) == r["compact_payload"] for r in rows)
    for name, digest in summary["artifacts"].items():
        assert inputs.file_sha(output/name) == digest


def test_guard_cannot_repack_or_return_a_third_operand(tmp_path):
    prepared = fixture(tmp_path)

    def bad(reference, proposal, **kwargs):
        reference["new_field"] = "not an exact operand"
        return reference, {}

    output = tmp_path/"failed"
    with pytest.raises(ValueError, match="exact frozen operand"):
        study.replay(prepared, output, binding={}, guard=bad)
    assert inputs.read(output/"failure.json")["stored_outcomes"] == 0
    assert not (output/"summary.json").exists()


def test_bad_skip_proof_fails_instead_of_hiding_changed_context(tmp_path):
    prepared = fixture(tmp_path)
    output = tmp_path/"failed"

    def unchanged_parent(reference, proposal, **kwargs):
        return deepcopy(proposal), {"accepted": True}

    with pytest.raises(ValueError, match="Skip proof disagrees"):
        study.replay(prepared, output, binding={}, guard=unchanged_parent, certificate=lambda *a, **k: {"eligible": True})
    assert (output/"failure.json").exists()


def test_exact_operand_still_must_respect_real_token_count(tmp_path):
    prepared = fixture(tmp_path)
    prepared["operands"][0]["reference"]["token_usage"]["tokens"] += 1
    with pytest.raises(ValueError, match="caps differ"):
        study.replay(prepared, tmp_path/"bad_tokens", binding={})


def test_existing_attempt_cannot_be_overwritten(tmp_path):
    prepared = fixture(tmp_path)
    output = tmp_path/"kept"
    output.mkdir()
    (output/"marker").write_text("keep")
    with pytest.raises(FileExistsError):
        study.replay(prepared, output, binding={})
    assert (output/"marker").read_text() == "keep"


def test_unchanged_old_gain_is_insufficient_and_each_control_loss_blocks(tmp_path):
    prepared = fixture(tmp_path)
    output = tmp_path/"base"
    study.replay(prepared, output, binding={})
    with gzip.open(output/"outcomes.jsonl.gz", "rt") as stream:
        rows = [json.loads(line) for line in stream]
    for row in rows:
        row["comparisons"]["prior_role"]["complete_gain"] = False
    assert not study.summarize(rows)["quality_gate"]["qualified"]
    for arm in ("legacy", "coverage", "prior_role"):
        for field, value in (("group_losses", [1]), ("complete_loss", True), ("single_file_first_hit_loss", True)):
            modified = deepcopy(rows)
            modified[0]["comparisons"]["prior_role"]["complete_gain"] = True
            modified[0]["comparisons"][arm][field] = value
            assert not study.summarize(modified)["quality_gate"]["qualified"]


def test_foreign_editable_source_fails_authority_check(tmp_path):
    foreign = types.ModuleType("semantic_search.foreign_test")
    foreign.__file__ = str(tmp_path/"another_checkout.py")
    with patch.dict(sys.modules, {foreign.__name__: foreign}), pytest.raises(ValueError, match="Executing source differs"):
        study.source_authority()


def test_cpu_guard_blocks_network_and_model_imports():
    with study.cpu_only():
        with pytest.raises(RuntimeError, match="Network forbidden"):
            socket.create_connection(("example.invalid", 443))
        with pytest.raises(RuntimeError, match="Model import forbidden"):
            __import__("mlx")


def test_directory_sync_failure_preserves_original_ledger_error(tmp_path):
    ledger = study.Ledger(tmp_path/"ledger.gz")
    original, sync_error = ValueError("original"), OSError("directory fsync")
    with patch.object(study, "sync_directory", side_effect=sync_error):
        with pytest.raises(ValueError) as caught:
            with ledger:
                raise original
    assert caught.value is original and caught.value.__cause__ is sync_error
    assert ledger.raw.closed


def test_failure_receipt_error_preserves_original_guard_error(tmp_path):
    prepared = fixture(tmp_path)
    original, receipt_error = RuntimeError("guard"), OSError("receipt write")
    write = study.write_new

    def record(path, value):
        if path.name == "failure.json":
            raise receipt_error
        return write(path, value)

    def guard(*args, **kwargs):
        raise original

    with patch.object(study, "write_new", record), pytest.raises(RuntimeError) as caught:
        study.replay(prepared, tmp_path/"failure", binding={}, guard=guard)
    assert caught.value is original and caught.value.__cause__ is receipt_error


def test_constructor_failure_closes_raw_and_preserves_exception(tmp_path):
    original = OSError("first directory sync")
    raw = (tmp_path/"ledger.gz").open("xb")
    with patch.object(study.Path, "open", return_value=raw), patch.object(study, "sync_directory", side_effect=original):
        with pytest.raises(OSError) as caught:
            study.Ledger(tmp_path/"ledger.gz")
    assert caught.value is original and raw.closed


def test_missing_attempt_parent_is_not_created(tmp_path):
    prepared = fixture(tmp_path)
    path = tmp_path/"missing"/"attempt"
    with pytest.raises(ValueError, match="parent must already exist"):
        study.replay(prepared, path, binding={})
    assert not path.parent.exists()


def test_freeze_verifies_real_git_bytes_and_rejects_drift(tmp_path):
    repo = tmp_path/"repo"
    folder = repo/"prepared"
    folder.mkdir(parents=True)
    (repo/"code.py").write_text("value = 1\n")
    inputs_hashes = {}
    for name in ("frozen-plan.json", "operand.json"):
        study.write_new(folder/name, {"invented": True})
        inputs_hashes[f"prepared/{name}"] = inputs.file_sha(folder/name)
    source_hashes = {"code.py": inputs.file_sha(repo/"code.py")}
    frozen = {"schema": study.SCHEMA, "new_guard_outcomes": 0, "native_calls": 0, "default_change": False,
        "sources": source_hashes, "artifacts": inputs_hashes, "runtime": {"invented": True},
        "prepared_plan_sha256": inputs.file_sha(folder/"frozen-plan.json")}
    study.write_new(study.freeze_path(folder), frozen)
    assert not (folder/study.FREEZE).exists()
    for args in (["init", "-q"], ["config", "user.name", "Invented test"], ["config", "user.email", "invented@example.invalid"],
                 ["add", "."], ["commit", "-qm", "Invented freeze"]):
        subprocess.check_call(["git", "-C", str(repo), *args])
    commit = inputs.git(repo, "rev-parse", "HEAD").decode().strip()
    with patch.object(study, "source_hashes", return_value=source_hashes), patch.object(study, "runtime", return_value={"invented": True}):
        assert study.verify_freeze(folder, commit, root=repo)["commit"] == commit
        other = repo/"other_prepared"
        other.mkdir()
        for path in folder.iterdir():
            (other/path.name).write_bytes(path.read_bytes())
        with pytest.raises(ValueError, match="Exact prepared artifact"):
            study.verify_freeze(other, commit, root=repo)
        (folder/"operand.json").write_text("changed")
        with pytest.raises(ValueError, match="Exact prepared artifact|dirty"):
            study.verify_freeze(folder, commit, root=repo)
