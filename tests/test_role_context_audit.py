"""Invented independent CPU role audit contracts; no corpus or model reads."""
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("independent_role_audit_test", SCRIPTS / "audit_role_context.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class Counter:
    def encode(self, text, **kwargs):
        return list(text)


def row(source="def read():\n    return state.value\n", *, path="invented/read.py", name="read", start=10):
    high = start + len(audit.context.physical_lines(source)) - 1
    sha = audit.digest(source.encode())
    return {"id": name, "path": path, "symbol": name, "start_line": start, "end_line": high,
        "window_end_line": high, "source": source, "source_hash": sha, "content_hash": sha,
        "truncated": False, "retrieval_score": 0.5, "h2o_score": None, "qwen_score": None,
        "origin": "retrieval", "related_to": None}


def keys(*rows):
    return {item["id"]: [item["path"], item["symbol"], item["start_line"], item["end_line"]] for item in rows}


def classify(value, **kwargs):
    return audit.classify_row(value, definition_keys=keys(value), **kwargs)


def response(rows):
    result = {"query": "Find invented setup and delivery", "results": deepcopy(rows), "match_status": "candidates",
              "budget_exhausted": False, "total_source_chars": 0,
              "scope": {"revision": "invented", "paths": ["invented/"]}}
    audit.context.recount(result, 4096, Counter())
    return result


def fixture(conditions=("dense", "hybrid")):
    cases, proposals, controls, complementary = [], [], [], []
    for number, condition in enumerate(conditions):
        a, b = row(), row("def publish():\n    return value\n", path="invented/publish.py", name="publish")
        baseline, proposed = response([a]), response([a, b])
        identity = {"subset": "invented", "dataset": "invented", "query_id": "two_steps", "condition": condition}
        key = [identity[name] for name in ("subset", "dataset", "query_id", "condition")]
        refs = {arm: {"path": "invented/control.jsonl", "line_number": 2 * number + offset,
                    "record_sha256": "a" * 64, "response_sha256": "b" * 64, "compact_sha256": "c" * 64}
                for offset, arm in enumerate(("legacy", "coverage_control"), 1)}
        case = {"case_id": f"invented_{number}", "key": key, **identity, "kind": "present", "multi_file": True,
            "qualified": False, "groups": [[{"path": a["path"], "evidence_lines": [10, 11]}],
                                           [{"path": b["path"], "evidence_lines": [10, 11]}]],
            "reference_response": baseline, "legacy_control": deepcopy(baseline), "limits": dict(audit.LIMITS),
            "definition_keys": keys(a, b), "control_references": refs}
        payload = audit.context.compact_payload(proposed)
        saved = {"case_id": case["case_id"], "key": key, **identity, "reference_response": deepcopy(baseline),
            "definition_keys": deepcopy(case["definition_keys"]), "limits": dict(audit.LIMITS), "response": proposed,
            "compact_payload": payload, "compact_sha256": audit.digest(payload.encode())}
        cases.append(case)
        proposals.append(saved)
        controls.append({"case_id": case["case_id"], **identity, "controls": refs})
        complementary.append({**identity, "response": deepcopy(baseline), "compact_sha256": "d" * 64})
    negatives = [{"path": "invented/negative.jsonl", "line_number": 1, "condition": "old_negative",
                  "record_sha256": "e" * 64, "response_sha256": "f" * 64, "compact_sha256": "0" * 64}]
    return cases, proposals, controls, negatives, complementary


def outcome(case, saved, reference):
    chosen, receipt = audit.reconstruct(case["reference_response"], saved["response"], definition_keys=case["definition_keys"])
    payload = audit.context.compact_payload(chosen)
    return {name: deepcopy(case[name]) for name in
            ("case_id", "key", "subset", "dataset", "query_id", "condition", "kind", "multi_file", "qualified")} | {
        "parent_proposal_ref": deepcopy(reference), "input_sha256": {"case": audit.digest(audit.canonical(case)),
            "coverage": audit.digest(audit.canonical(case["reference_response"])),
            "saved_v1_proposal": audit.digest(audit.canonical(saved["response"]))},
        "response": chosen, "role_guard_receipt": receipt, "compact_payload": payload,
        "compact_sha256": audit.digest(payload.encode()), "actual_compact_tokens": len(Counter().encode(payload)),
        "calls": dict(audit.CALLS), "role_guard_s": 0.01, **audit.score(case, chosen),
        "comparisons": {name: audit.compare(case, control, chosen) for name, control in
            (("legacy", case["legacy_control"]), ("coverage_control", case["reference_response"]),
             ("semantic_final_v1", saved["response"]))}}


@pytest.mark.parametrize("source,kinds", [
    ("def run():\n    target.value = 1\n", ["attribute_store"]),
    ("def run():\n    target[key] += 1\n", ["subscript_store"]),
    ("def run():\n    del target.value, target[key]\n", ["attribute_delete", "subscript_delete"]),
    ("def run():\n    target.value, target[key] = pair\n", ["attribute_store", "subscript_store"]),
    ("async def run():\n    def nested():\n        target.value = 1\n", ["attribute_store"]),
    ("def run():\n    local = target.value\n    return target[key]\n", []),
    ("def run():\n    # target.value = 1\n    return 'target[key] = 1'\n", []),
    ("def run():\n    target.append(value)\n", []),
])
def test_python_syntax_targets_anywhere(source, kinds):
    fact = classify(row(source))
    assert [item["kind"] for item in fact["nodes"]] == kinds
    assert fact["reasons"] == (["explicit_write"] if kinds else [])
    assert fact["full_definition"] is True


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_python_dedent_physical_lines_unicode(newline):
    value = row(newline.join(["    def café():", "        target.value = 'é'", ""]), start=31)
    fact = classify(value)
    assert fact["nodes"] == [{"kind": "attribute_store", "node_type": "Attribute", "start_line": 32, "end_line": 32}]
    assert fact["source_sha256"] == audit.digest(value["source"].encode())


@pytest.mark.parametrize("source,kinds", [
    ("function run(){ target.value = 1; target[key] += 1; }", ["attribute_store", "subscript_store"]),
    ("function run(){ target.value++; --target[key]; }", ["attribute_store", "subscript_store"]),
    ("function run(){ delete target.value; delete target[key]; }", ["attribute_delete", "subscript_delete"]),
    ("function run(){ ({x: target.value, y: [target[key]]} = input); }", ["attribute_store", "subscript_store"]),
    ("function run(){ ({x = other.value, [other.key]: local} = input); }", []),
    ("function run(){ for(target.value of input){} for(target[key] in input){} }", ["attribute_store", "subscript_store"]),
    ("class Run { initialized = 1; empty; run(){ target.value = 2; } }", ["attribute_store", "initialized_field"]),
    ("function run(){ function nested(){ target.value = 1; } }", ["attribute_store"]),
    ("const run = () => { return target.value + target[key]; };", []),
    ("export function run(){ return 'target.value = 1'; /* x[y]++ */ }", []),
])
def test_javascript_direct_lvalue_patterns(source, kinds):
    fact = classify(row(source, path="invented/run.js"))
    assert [item["kind"] for item in fact["nodes"]] == kinds
    assert fact["reasons"] == (["explicit_write"] if kinds else [])


@pytest.mark.parametrize("path,source", [
    ("invented/run.ts", "export class Run { public value: number = 1; }"),
    ("invented/run.tsx", "const run = () => { target.value = <div>ok</div>; };"),
])
def test_typescript_and_tsx_are_direct_pinned_grammars(path, source):
    fact = classify(row(source, path=path))
    assert fact["reasons"] == ["explicit_write"]
    assert len(fact["nodes"]) == 1


@pytest.mark.parametrize("target", ["(obj.value as any)", "obj.value!", "(obj.value satisfies any)", "(<any>obj.value)",
                                    "((obj.value! as any) satisfies any)"])
@pytest.mark.parametrize("statement", ["TARGET = 1;", "delete TARGET;", "TARGET++;", "for(TARGET of values){}"])
def test_ts_transparent_lvalue_wrappers_preserve_write_targets(target, statement):
    source = "function run(){" + statement.replace("TARGET", target) + "}"
    fact = classify(row(source, path="invented/run.ts"))
    assert fact["reasons"] == ["explicit_write"]
    assert fact["nodes"] == [{"kind": "attribute_delete" if statement.startswith("delete") else "attribute_store",
                               "node_type": "member_expression", "start_line": 10, "end_line": 10}]


def test_ts_type_and_default_read_syntax_is_not_a_target():
    source = "function run(){ ({x = (other.value as any)} = input); return (other.value as typeof types.value); }"
    assert classify(row(source, path="invented/run.ts"))["reasons"] == []


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_js_unicode_spans_count_literal_physical_lines(newline):
    value = row(newline.join(["function café(){", " target['é'] = 1;", "}"]), path="invented/run.js", start=51)
    assert classify(value)["nodes"] == [{"kind": "subscript_store", "node_type": "subscript_expression", "start_line": 52, "end_line": 52}]


@pytest.mark.parametrize("path,source,reason", [
    ("invented/run.py", "def run(:\n", "parse_error"),
    ("invented/run.js", "function run( {", "parse_error"),
    ("invented/run.py", "return value\n", "parse_context_uncertain"),
    ("invented/run.py", "local = value\n", "parse_context_uncertain"),
    ("invented/run.py", "class Run:\n    value = 1\n", "parse_context_uncertain"),
    ("invented/run.js", "target.value = 1;", "parse_context_uncertain"),
    ("invented/run.js", "function run(){} function other(){}", "parse_context_uncertain"),
    ("invented/run.rs", "fn run() {}", "unsupported_grammar"),
])
def test_uncertain_sources_protected(path, source, reason):
    assert classify(row(source, path=path))["reasons"] == [reason]


@pytest.mark.parametrize("change,expected", [
    ("missing", ["definition_unverified"]), ("mapping", ["definition_unverified"]),
    ("symbol", ["definition_unverified"]), ("partial", ["partial_definition"]),
    ("clipped", ["clipped", "partial_definition"]), ("literal", ["invalid_literal"]),
])
def test_exact_definition_and_literal_authority(change, expected):
    value = row()
    mapping = keys(value)
    if change == "missing":
        mapping = {}
    elif change == "mapping":
        mapping = ["not", "mapping"]
    elif change == "symbol":
        mapping[value["id"]][1] = "different"
    elif change == "partial":
        mapping[value["id"]][3] += 1
    elif change == "clipped":
        value.update(truncated=True, window_end_line=value["end_line"] + 1, content_hash="a" * 64)
    else:
        value["source_hash"] = "0" * 64
    fact = audit.classify_row(value, definition_keys=mapping)
    assert fact["reasons"] == expected
    assert fact["full_definition"] is False


def test_missing_and_changed_parser_dependency_fail_closed(monkeypatch):
    value = row("function run(){ return value; }", path="invented/run.js")
    monkeypatch.setattr(audit.importlib.metadata, "version", lambda _: "changed")
    assert classify(value)["reasons"] == ["parser_unavailable"]


@pytest.mark.parametrize("error", [SyntaxError, RecursionError, OverflowError])
def test_parser_resource_or_syntax_uncertainty_is_protected(error):
    def fail(_):
        raise error("invented parser failure")

    value = row("function run(){ return value; }", path="invented/run.js")
    assert classify(value, parser_factory=fail)["reasons"] == ["parse_error"]


def test_ordinal_is_bound_and_provenance_preserves_presence():
    value = row()
    value.update(source_origin="overlay", buffer_version=0, buffer_hash="b" * 64)
    mapping = keys(value)
    mapping[value["id"]].append(0)
    fact = audit.classify_row(value, definition_keys=mapping)
    assert fact["definition_key"] == mapping[value["id"]]
    assert fact["provenance"] == {name: value[name] for name in audit.PROVENANCE}
    assert fact["reasons"] == []
    assert fact["row_sha256"] == audit.digest(audit.canonical(value))


@pytest.mark.parametrize("wrong", ["nan", "tuple", "bool_lines", "origin", "buffer", "hash", "tail"])
def test_invalid_literals_fail_closed(wrong):
    value = row()
    if wrong == "nan":
        value["retrieval_score"] = float("nan")
    elif wrong == "tuple":
        value["extra"] = ("tuple",)
    elif wrong == "bool_lines":
        value["start_line"] = True
    elif wrong == "origin":
        value["source_origin"] = "unknown"
    elif wrong == "buffer":
        value["buffer_version"] = 1
    elif wrong == "hash":
        value["content_hash"] = "bad"
    else:
        value["window_end_line"] += 1
    assert classify(value)["reasons"] == ["invalid_literal"]


def test_terminal_guard_defensive_exact_retention_and_whole_fallback():
    protected = row("def setup():\n    target.value = 1\n", name="setup")
    ordinary, extra = row(), row("def extra():\n    return 1\n", name="extra")
    baseline, proposal = response([protected, ordinary]), response([ordinary, extra])
    original = deepcopy((baseline, proposal))
    result, receipt = audit.reconstruct(baseline, proposal, definition_keys=keys(protected, ordinary, extra))
    assert result == baseline
    assert receipt["protected_ids"] == receipt["violated_ids"] == ["setup"]
    assert receipt["fallback_reason"] == "protected_role_row_changed"
    assert (baseline, proposal) == original
    result["results"][0]["source"] = "mutated"
    assert baseline == original[0]


@pytest.mark.parametrize("change", ["scalar_type", "source", "score", "provenance", "field_presence"])
def test_protected_dictionary_changes_are_not_containment_equivalence(change):
    value = row("def setup():\n    target.value = 1\n")
    baseline = response([value])
    proposed = deepcopy(baseline)
    chosen = proposed["results"][0]
    if change == "scalar_type":
        chosen["start_line"] = float(chosen["start_line"])
    elif change == "source":
        chosen["source"] += "# appended\n"
    elif change == "score":
        chosen["retrieval_score"] += 0.1
    elif change == "provenance":
        chosen["source_origin"] = "saved"
    else:
        chosen.pop("related_to")
    result, receipt = audit.reconstruct(baseline, proposed, definition_keys=keys(value))
    assert result == baseline
    assert receipt["accepted"] is False


def test_invalid_reference_and_proposal_shapes_are_rejected():
    chosen, receipt = audit.reconstruct(None, {})
    assert chosen is None and receipt["fallback_reason"] == "invalid_reference"
    value = row()
    chosen, receipt = audit.reconstruct(response([value]), {"results": [value, value]}, definition_keys=keys(value))
    assert chosen == response([value]) and receipt["fallback_reason"] == "invalid_proposal"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), ("tuple",), {1: "key"}, object()])
@pytest.mark.parametrize("arm", ["reference", "proposal"])
def test_whole_response_nonliteral_metadata_fails_closed(bad, arm):
    value = row()
    reference, proposal = response([value]), response([value])
    target = reference if arm == "reference" else proposal
    target["diagnostic"] = bad
    _, receipt = audit.reconstruct(reference, proposal, definition_keys=keys(value))
    assert receipt["fallback_reason"] == "invalid_" + arm


def test_recursive_metadata_fails_closed_without_parsing():
    reference = response([row()])
    reference["cycle"] = reference
    _, receipt = audit.reconstruct(reference, {})
    assert receipt["fallback_reason"] == "invalid_reference"


def test_complete_matrix_rebuild_and_gate_deduplicates_modes():
    cases, proposals, controls, negatives, complementary = fixture()
    before = deepcopy((cases, proposals, controls, negatives, complementary))
    values = audit.rebuild_inputs(cases, proposals, controls, negatives, complementary,
                                 expected_rows=2, expected_units=1, expected_old_negatives=1)
    assert (cases, proposals, controls, negatives, complementary) == before
    assert len(values["negative-references.json"]) == 5
    refs = {item["case_id"]: item for item in values["parent-outcome-references.json"]}
    rows = [outcome(case, saved, refs[case["case_id"]]) for case, saved in zip(cases, proposals)]
    gate = audit.audit_outcomes(cases, rows, {item["case_id"]: item for item in proposals}, refs,
                               counter=Counter(), expected_rows=2, expected_units=1)
    assert gate["qualified"] is True
    assert gate["occurrences"] == 2 and gate["distinct_dataset_query_units"] == 1
    assert gate["additional_distinct_complete_multi_queries_vs_coverage"] == [["invented", "two_steps"]]


@pytest.mark.parametrize("change", ["case", "proposal", "negative_overlap", "complementary", "payload", "control"])
def test_rebuild_rejects_incomplete_or_self_rebound_references(change):
    cases, proposals, controls, negatives, complementary = fixture()
    if change == "case":
        cases.pop()
    elif change == "proposal":
        proposals[0]["definition_keys"] = {}
    elif change == "negative_overlap":
        negatives[0].update(path=audit.PARENT + "/replay/outcomes.jsonl.gz", line_number=1)
    elif change == "complementary":
        complementary[0]["query_id"] = "different"
    elif change == "payload":
        proposals[0]["compact_payload"] += " "
    else:
        controls[0]["controls"] = {"legacy": {}}
    with pytest.raises(ValueError):
        audit.rebuild_inputs(cases, proposals, controls, negatives, complementary,
                             expected_rows=2, expected_units=1, expected_old_negatives=1)


@pytest.mark.parametrize("change", ["source", "decision", "facts", "model", "v1", "bool_call", "score", "comparison", "hash", "tokens", "input", "label", "duration", "scope_order"])
def test_exact_record_audit_rejects_changed_source_decisions_accounting_and_bytes(change):
    cases, proposals, controls, negatives, complementary = fixture()
    documents = audit.rebuild_inputs(cases, proposals, controls, negatives, complementary,
                                    expected_rows=2, expected_units=1, expected_old_negatives=1)
    case, saved, reference = cases[0], proposals[0], documents["parent-outcome-references.json"][0]
    actual = outcome(case, saved, reference)
    if change == "source":
        actual["response"]["results"][0]["source"] += "# changed\n"
    elif change == "decision":
        actual["role_guard_receipt"]["accepted"] = False
    elif change == "facts":
        actual["role_guard_receipt"]["reference_facts"][0]["full_definition"] = False
    elif change in ("model", "v1", "bool_call"):
        actual["calls"]["current_v1" if change == "v1" else "model"] = False if change == "bool_call" else 1
    elif change == "score":
        actual["complete"] = False
    elif change == "comparison":
        actual["comparisons"]["coverage_control"]["group_losses"] = [1]
    elif change == "hash":
        actual["compact_sha256"] = "0" * 64
    elif change == "tokens":
        actual["actual_compact_tokens"] += 1
    elif change == "input":
        actual["input_sha256"]["case"] = "0" * 64
    elif change == "label":
        actual["qualified"] = True
    elif change == "duration":
        actual["role_guard_s"] = float("nan")
    else:
        actual["response"]["scope"] = dict(reversed(list(actual["response"]["scope"].items())))
    with pytest.raises(ValueError):
        audit.verify_record(actual, case=case, parent_proposal=saved, parent_proposal_ref=reference, counter=Counter())


def test_individual_group_loss_fails_even_when_other_groups_improve():
    cases, proposals, *_ = fixture()
    case = cases[0]
    new = response([proposals[0]["response"]["results"][1]])
    gate = audit.prior.quality_gate([case], {case["case_id"]: new})
    assert gate["qualified"] is False
    assert gate["against_controls"]["coverage_control"]["group_losses"][0]["groups"] == [1]


def test_each_control_and_single_file_first_hit_remain_hard_gates():
    cases, proposals, *_ = fixture()
    case = cases[0]
    case["multi_file"] = False
    chosen = response([proposals[0]["response"]["results"][1]])
    gate = audit.prior.quality_gate([case], {case["case_id"]: chosen})
    assert gate["qualified"] is False
    assert all(value["single_file_first_hit_losses"] for value in gate["against_controls"].values())
    # Repairing a v1 negative is permitted; v1 is diagnostic, not a third control.
    preserved = response([*case["reference_response"]["results"], proposals[0]["response"]["results"][1]])
    case["multi_file"] = True
    assert audit.prior.quality_gate([case], {case["case_id"]: preserved})["qualified"] is True


def test_qualified_evidence_checks_symbol_and_literal_hash_not_just_path():
    cases, proposals, *_ = fixture()
    case = cases[0]
    case["qualified"] = True
    target = case["groups"][0][0]
    first = case["reference_response"]["results"][0]
    target.update(symbol=first["symbol"], evidence_sha256=audit.digest(first["source"].encode()))
    second = proposals[0]["response"]["results"][1]
    case["groups"][1][0].update(symbol=second["symbol"], evidence_sha256=audit.digest(second["source"].encode()))
    assert audit.score(case, proposals[0]["response"])["complete"] is True
    changed = deepcopy(proposals[0]["response"])
    changed["results"][0]["symbol"] = "different"
    assert audit.score(case, changed)["evidence_ranks"][0] is None


def test_gzip_roundtrip_preserves_exact_nested_compact_order(tmp_path):
    cases, proposals, controls, negatives, complementary = fixture()
    documents = audit.rebuild_inputs(cases, proposals, controls, negatives, complementary,
                                    expected_rows=2, expected_units=1, expected_old_negatives=1)
    actual = outcome(cases[0], proposals[0], documents["parent-outcome-references.json"][0])
    path = tmp_path / "outcomes.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write(json.dumps(actual, ensure_ascii=False) + "\n")
    value = audit.jsonl(path)[0]
    audit.verify_record(value, case=cases[0], parent_proposal=proposals[0],
                        parent_proposal_ref=documents["parent-outcome-references.json"][0], counter=Counter())


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args]).decode().strip()


def test_freeze_requires_exact_committed_source_and_input_bytes(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "invented@example.test")
    git(tmp_path, "config", "user.name", "Invented")
    (tmp_path / "source.py").write_text("# invented\n")
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    artifact = prepared / "input.json"
    artifact.write_text("{}\n")
    plan = {"source_sha256": {"source.py": audit.file_sha(tmp_path / "source.py")}, "parent_binding": {"sha256": {}},
            "artifact_sha256": {"input.json": audit.file_sha(artifact)}}
    (prepared / "frozen-plan.json").write_text(json.dumps(plan))
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "invented freeze")
    freeze = git(tmp_path, "rev-parse", "HEAD")
    receipt = {"commit": freeze, "execution_commit": freeze, "bound_blob_count": 3,
        "plan_sha256": audit.file_sha(prepared / "frozen-plan.json"), "new_guard_outcomes_before_freeze": 0}
    binding = {"freeze": receipt, "execution_commit": freeze}
    audit.verify_freeze(tmp_path, prepared, {"plan": plan}, binding, datetime.now(timezone.utc).isoformat())
    with pytest.raises(ValueError, match="precede"):
        audit.verify_freeze(tmp_path, prepared, {"plan": plan}, binding, "2000-01-01T00:00:00+00:00")
    (tmp_path / "source.py").write_text("# changed\n")
    with pytest.raises(ValueError, match="bytes differ"):
        audit.verify_freeze(tmp_path, prepared, {"plan": plan}, binding, datetime.now(timezone.utc).isoformat())


def test_publication_authority_rejects_self_consistent_old_parent_rebinding(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "invented@example.test")
    git(tmp_path, "config", "user.name", "Invented")
    path = tmp_path / "old-parent.json"
    path.write_text('{"old":"immutable"}\n')
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "invented publication")
    monkeypatch.setattr(audit, "PUBLICATION", git(tmp_path, "rev-parse", "HEAD"))
    original = {"old-parent.json": audit.file_sha(path)}
    audit.verify_published_parents(tmp_path, original)
    path.write_text('{"old":"self-consistently rebound"}\n')
    rebound = {"old-parent.json": audit.file_sha(path)}
    with pytest.raises(ValueError, match="Committed bound bytes"):
        audit.verify_published_parents(tmp_path, rebound)


def test_publication_exception_cannot_be_changed_to_an_old_result(tmp_path):
    with pytest.raises(ValueError, match="Exact new parent recheck exemption"):
        audit.verify_published_parents(tmp_path, {}, recheck="old-result.json")


def test_archive_rejects_duplicate_member_names_even_if_self_rehashed(tmp_path):
    folder = tmp_path / audit.PARENT
    folder.mkdir(parents=True)
    archive = folder / "native-capture.tar.gz"
    with tarfile.open(archive, "w:gz") as stream:
        for number in range(6):
            member = tarfile.TarInfo("capture/duplicate.json")
            member.size = 2
            stream.addfile(member, io.BytesIO(b"{}"))
    manifest = {"status": "passed_lossless_archive", "archive_bytes": archive.stat().st_size,
        "archive_sha256": audit.file_sha(archive), "members": {f"capture/{i}.json": {"bytes": 2,
            "sha256": audit.digest(b"{}")} for i in range(6)}}
    with pytest.raises(ValueError, match="six native"):
        audit.verify_archive(tmp_path, manifest)


@pytest.mark.parametrize("unsafe", ["../escape.json", "/absolute.json", "capture/../escape.json", "capture\\escape.json"])
def test_archive_rejects_unsafe_names_even_with_self_consistent_manifest(tmp_path, unsafe):
    folder = tmp_path / audit.PARENT
    folder.mkdir(parents=True)
    archive = folder / "native-capture.tar.gz"
    members = [unsafe, *(f"capture/{i}.json" for i in range(5))]
    with tarfile.open(archive, "w:gz") as stream:
        for name in members:
            member = tarfile.TarInfo(name)
            member.size = 2
            stream.addfile(member, io.BytesIO(b"{}"))
    manifest = {"status": "passed_lossless_archive", "archive_bytes": archive.stat().st_size,
        "archive_sha256": audit.file_sha(archive), "members": {name: {"bytes": 2, "sha256": audit.digest(b"{}")} for name in members}}
    with pytest.raises(ValueError, match="path is unsafe"):
        audit.verify_archive(tmp_path, manifest)


def test_parent_recheck_path_is_fixed_not_an_immutable_artifact_exemption(tmp_path):
    with pytest.raises(ValueError, match="Exact new parent"):
        audit.verify_parent_binding(tmp_path, {"parent_recheck_path": audit.PARENT + "/replay/summary.json"})


def test_auditor_has_no_producer_or_model_imports():
    import ast

    parsed = ast.parse((SCRIPTS / "audit_role_context.py").read_text())
    forbidden = {"semantic_search", "research_role_context", "run_role_context", "numpy", "torch", "mlx", "transformers"}
    imports = set()
    for node in ast.walk(parsed):
        if isinstance(node, ast.Import):
            imports.update(value.name.split(".")[0] for value in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add((node.module or "").split(".")[0])
    assert not imports & forbidden
