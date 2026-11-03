"""Invented tests of the explicitly post-outcome diagnostic-only audit correction."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("observed_recheck_test_subject", SCRIPTS / "recheck_observed_model_study.py")
recheck = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recheck)
FIXTURE_SPEC = importlib.util.spec_from_file_location("invented_recheck_fixture", Path(__file__).with_name("test_semantic_study_audit.py"))
fixture = importlib.util.module_from_spec(FIXTURE_SPEC)
FIXTURE_SPEC.loader.exec_module(fixture)


def inactive_fixture():
    case, outcome, capture, requests = fixture.case_fixture(active=True)
    case.update(condition="bm25", retrieval="bm25", feature_request_id=None, inactive_reason="lexical_only")
    case["key"][-1] = "bm25"
    requests["cases"][0].update(active=False, reason="lexical_only", query_request_id=None, window_documents={}, window_bindings={})
    response, receipt = recheck.study.context.reconstruct(case["reference_response"], case["raw_rows"], None,
        definition_keys=case["definition_keys"], precoalesce_rows=case["precoalesce_rows"], counter=fixture.Counter(), **case["limits"])
    assert receipt["fallback_reason"] == "invalid_features"
    receipt["feature_error"] = "Feature schema fields differ"
    payload = recheck.study.context.compact_payload(response)
    outcome.update(condition="bm25", key=case["key"], response=response, semantic_receipt=receipt, feature_calls=0,
                   query_request_id=None, window_documents={}, features_sha256=recheck.study.sha(b"null"),
                   compact_payload=payload, compact_sha256=recheck.study.sha(payload.encode()), actual_compact_tokens=len(payload.encode()))
    return case, outcome, capture, requests


def run_fixture(values, *, recorder=None):
    case, outcome, capture, requests = values
    return recheck.recheck_outcomes([case], [outcome], capture, requests, counter=fixture.Counter(),
                                     expected_rows=1, expected_units=1, recorder=recorder)


def test_only_inactive_diagnostic_wording_is_normalized_on_a_copy():
    values = inactive_fixture()
    before = deepcopy(values)
    recorded = []
    result = run_fixture(values, recorder=recorded)
    assert values == before
    assert not result["qualified"]
    assert len(recorded) == 1 and recorded[0]["case_id"] == "case"
    assert recorded[0]["actual_message"] == "Feature schema fields differ"
    assert recorded[0]["independent_message"] == "Semantic feature schema"
    assert recorded[0]["modified_path"] == "semantic_receipt.feature_error"


def test_identical_invalid_feature_diagnostic_needs_no_normalization():
    values = inactive_fixture()
    values[1]["semantic_receipt"]["feature_error"] = "Semantic feature schema"
    recorded = []
    run_fixture(values, recorder=recorded)
    assert recorded == []


@pytest.mark.parametrize("message", ["", None, 1, True, [], {}])
def test_empty_or_nonstring_actual_diagnostics_are_rejected(message):
    values = inactive_fixture()
    values[1]["semantic_receipt"]["feature_error"] = message
    with pytest.raises(ValueError, match="nonempty strings"):
        run_fixture(values)


def test_independent_diagnostic_must_also_be_nonempty(monkeypatch):
    values = inactive_fixture()
    actual = recheck.study.context.reconstruct

    def invalid(*args, **kwargs):
        response, receipt = actual(*args, **kwargs)
        receipt["feature_error"] = ""
        return response, receipt

    monkeypatch.setattr(recheck.study.context, "reconstruct", invalid)
    with pytest.raises(ValueError, match="nonempty strings"):
        run_fixture(values)


@pytest.mark.parametrize("change", ["actual_reason", "independent_reason", "active_request", "active_case",
                                   "source", "decision", "counter", "feature_hash", "compact_payload"])
def test_correction_cannot_hide_any_material_change(change):
    values = inactive_fixture()
    case, outcome, _, requests = values
    if change == "actual_reason":
        outcome["semantic_receipt"]["fallback_reason"] = "other_reason"
    elif change == "independent_reason":
        case["reference_response"]["query"] = "find implementation"
    elif change == "active_request":
        requests["cases"][0]["active"] = True
    elif change == "active_case":
        case["feature_request_id"] = "active"
    elif change == "source":
        outcome["response"] = deepcopy(outcome["response"])
        outcome["response"]["results"][0]["source"] = "changed implementation\n"
    elif change == "decision":
        outcome["semantic_receipt"]["new_decision"] = True
    elif change == "counter":
        outcome["semantic_calls"] = 2
    elif change == "feature_hash":
        outcome["features_sha256"] = "0" * 64
    elif change == "compact_payload":
        outcome["compact_payload"] += " "
    with pytest.raises(ValueError):
        run_fixture(values)


def test_active_invalid_features_are_never_normalized():
    values = fixture.case_fixture(active=True)
    values[1]["semantic_receipt"] = {"fallback_reason": "invalid_features", "feature_error": "invented error"}
    with pytest.raises(ValueError, match="intentionally inactive"):
        run_fixture(values)


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True).stdout


def freeze_fixture(tmp_path):
    for name in ("scripts/audit_semantic_study.py", recheck.PRECEDENT, *recheck.SOURCES):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# invented source; not executed\n")
    failure_name = "results/semantic-model-development-2026-11-02/audit-attempt-1/audit.log"
    failure_path = tmp_path / failure_name
    failure_path.parent.mkdir(parents=True)
    failure_path.write_text("invented original failed audit\n")
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "invented@example.invalid")
    git(tmp_path, "config", "user.name", "Invented Fixture")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "invented original study")
    original = git(tmp_path, "rev-parse", "HEAD").decode().strip()
    plan = {"schema": recheck.SCHEMA, "original_study_freeze": original,
            "source_sha256": {name: recheck.sha((tmp_path / name).read_bytes()) for name in recheck.SOURCES},
            "original_audit_failure": {"path": failure_name, "sha256": recheck.sha(failure_path.read_bytes())},
            "expected_normalizations": 1}
    (tmp_path / recheck.PLAN).write_text(json.dumps(plan))
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "invented correction freeze")
    correction = git(tmp_path, "rev-parse", "HEAD").decode().strip()
    return original, correction, datetime.now(timezone.utc).isoformat(), failure_name


def test_correction_sources_tests_plan_and_original_failure_are_frozen_before_recheck(tmp_path):
    original, correction, started, _ = freeze_fixture(tmp_path)
    proof = recheck.verify_recheck_freeze(tmp_path, correction, started_at=started, original_freeze=original)
    assert proof["audit_freeze"] == correction and proof["original_study_freeze"] == original
    assert set(proof["source_sha256"]) == set(recheck.SOURCES)


@pytest.mark.parametrize("change", ["wrapper", "tests", "plan", "failure", "original_auditor", "precedent", "new_committed_revision"])
def test_any_correction_or_original_revision_tamper_fails(tmp_path, change):
    original, correction, started, failure = freeze_fixture(tmp_path)
    name = {"wrapper": recheck.SOURCES[0], "tests": recheck.SOURCES[1], "plan": recheck.PLAN,
            "failure": failure, "original_auditor": "scripts/audit_semantic_study.py", "precedent": recheck.PRECEDENT,
            "new_committed_revision": recheck.SOURCES[0]}[change]
    (tmp_path / name).write_text("changed after correction freeze\n")
    if change == "new_committed_revision":
        git(tmp_path, "add", ".")
        git(tmp_path, "commit", "-qm", "unapproved correction revision")
    with pytest.raises(ValueError):
        recheck.verify_recheck_freeze(tmp_path, correction, started_at=started, original_freeze=original)


@pytest.mark.parametrize("revision", ["short", "", "a" * 39, "a" * 41, None])
def test_audit_freeze_requires_full_git_sha(tmp_path, revision):
    with pytest.raises(ValueError, match="full Git SHA"):
        recheck.verify_recheck_freeze(tmp_path, revision, started_at=datetime.now(timezone.utc).isoformat())


def test_recheck_cannot_predate_correction_commit(tmp_path):
    original, correction, _, _ = freeze_fixture(tmp_path)
    with pytest.raises(ValueError, match="precede"):
        recheck.verify_recheck_freeze(tmp_path, correction, started_at="2000-01-01T00:00:00+00:00", original_freeze=original)


def test_wrapper_lives_outside_original_source_binding_globs():
    assert not Path(recheck.SOURCES[0]).match("scripts/semantic*.py")
    assert not Path(recheck.SOURCES[0]).match("scripts/audit_semantic*.py")
    assert not Path(recheck.SOURCES[1]).match("tests/test_semantic*.py")
    source = (SCRIPTS / "recheck_observed_model_study.py").read_text()
    assert "semantic_receipt.feature_error" in source and "verify_record" in source
    assert "import semantic_search" not in source and "import numpy" not in source


def test_cli_refuses_to_bind_an_unexecuted_checkout(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Wrong checkout must fail before freeze proof or audit")

    monkeypatch.setattr(recheck, "verify_recheck_freeze", forbidden)
    with pytest.raises(ValueError, match="bound project"):
        recheck.main(["--audit-freeze", "a" * 40, "--root", str(tmp_path)])


def test_cli_requires_the_full_correction_commit_before_any_audit():
    with pytest.raises(SystemExit):
        recheck.main([])
