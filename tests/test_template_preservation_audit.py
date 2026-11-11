"""Invented literal sources and records; no observed candidate outcomes."""
import ast
import base64
from contextlib import nullcontext
from copy import deepcopy
import importlib
import json
import socket
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from experiments.dependency_context import study_inputs as inputs
from experiments.template_preservation import audit


@pytest.mark.parametrize("body,count", [
    ('return f"value {run()}"', 1),
    ('return f"{first()}:{second()}"', 2),
    ('return f"{run()!r:20}"', 1),
    ('return f"{name}"', 0),
    ('return "value {run()}"', 0),
    ('return "".join([f"{run()}"])', 0),
    ('text = f"{run()}"\nreturn text', 0),
    ('return f"{object.call}"', 0),
    ('return f"{(run(), 1)}"', 0),
    ('return f"{run() + other()}"', 0),
    ('return f"{name:{width()}}"', 0),
    ('if flag:\n    return f"{run()}"\nreturn f"{other()}"', 2),
    ('def inner():\n    return f"{run()}"\nreturn "plain"', 0),
    ('class Nested:\n    def inner(self):\n        return f"{run()}"\nreturn "plain"', 0),
    ('deferred = lambda: f"{run()}"\nreturn "plain"', 0),
    ("'''return f\"{run()}\"'''\n# return f\"{run()}\"\nreturn \"plain\"", 0),
])
def test_independent_literal_template_grammar_and_lexical_scope(body, count):
    source = "def renamed(value=None):\n" + "".join("    " + line + "\n" for line in body.splitlines())
    node = ast.parse(source).body[0]
    assert len(audit.template_nodes(node)) == count


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_canonical_spans_and_utf8_witnesses_respect_literal_physical_bytes(newline):
    source = newline.join(['class Outer:', '    @decorate', '    async def rénder(self):',
                           '        return f"préfix {other()}"', ''])
    definitions = audit.canonical_definitions(source, "unicode.py")
    key = ("unicode.py", "Outer.rénder", 2, 4)
    assert key in definitions
    node, template, field, call = audit.template_nodes(definitions[key])[0]
    witness = audit.literal_witness(source, "unicode.py", call)
    assert witness["source"] == "other()"
    raw = source.encode()[witness["start_byte"]:witness["end_byte"]]
    assert raw == b"other()"
    assert witness["sha256"] == inputs.sha(raw)
    assert audit.literal_witness(source, "unicode.py", node)["source"] == 'return f"préfix {other()}"'
    assert audit.literal_witness(source, "unicode.py", template)["source"] == 'f"préfix {other()}"'
    assert audit.literal_witness(source, "unicode.py", field)["source"] == '{other()}'


def test_duplicate_names_keep_distinct_canonical_source_identities():
    source = 'def render():\n    return f"{first()}"\ndef render():\n    return f"{second()}"\n'
    definitions = audit.canonical_definitions(source, "module.py")
    assert set(definitions) == {("module.py", "render", 1, 2), ("module.py", "render", 3, 4)}


@pytest.mark.parametrize("module", ["numpy", "torch", "mlx", "transformers",
                                   "experiments.template_preservation.guard",
                                   "experiments.template_preservation.study", "semantic_search.backend"])
def test_clean_cpu_cli_blocks_model_candidate_and_producer_imports(monkeypatch, module):
    for name in list(sys.modules):
        if name.split(".")[0] in audit.BLOCKED or any(name == bad or name.startswith(bad + ".") for bad in audit.FORBIDDEN):
            monkeypatch.delitem(sys.modules, name)
    with audit.cpu_only(), pytest.raises(RuntimeError, match="forbidden"):
        importlib.import_module(module)


def test_clean_cpu_cli_blocks_network_and_restores_socket(monkeypatch):
    for name in list(sys.modules):
        if name.split(".")[0] in audit.BLOCKED or any(name == bad or name.startswith(bad + ".") for bad in audit.FORBIDDEN):
            monkeypatch.delitem(sys.modules, name)
    before = socket.create_connection
    with audit.cpu_only():
        with pytest.raises(RuntimeError, match="Network forbidden"):
            socket.create_connection(("example.invalid", 443))
        with pytest.raises(RuntimeError, match="Network forbidden"):
            socket.getaddrinfo("example.invalid", 443)
    assert socket.create_connection is before


class InventedCounter:
    def encode(self, text, disallowed_special=()):
        return [0] * 85


def invented(tmp_path, *, template=True, newline="\n"):
    source = newline.join(["@decorate", "def produce(value):", '    return f"réponse {value.make()}"' if template else '    return value', ""])
    other = 'def accompany():\n    return "spare"\n'
    corpus = tmp_path / "corpus" / "invented"
    corpus.mkdir(parents=True)
    (corpus / "module.py").write_bytes(source.encode())
    (corpus / "other.py").write_bytes(other.encode())
    inventory, keys, rows = [], {}, []
    for path, symbol, text, low, high in (("module.py", "produce", source, 1, 3), ("other.py", "accompany", other, 1, 2)):
        identifier = f"{path}:{symbol}:{low}-{high}"
        inventory.append({"id": identifier, "path": path, "symbol": symbol, "start_line": low, "end_line": high,
                          "text": text, "content_hash": inputs.sha(text.encode())})
        keys[identifier] = [path, symbol, low, high]
        rows.append({"id": identifier, "path": path, "symbol": symbol, "start_line": low, "end_line": high,
            "window_end_line": high, "source": text, "source_hash": inputs.sha(text.encode()),
            "content_hash": inputs.sha(text.encode()), "truncated": False, "origin": "retrieval", "related_to": None,
            "retrieval_score": 0.4, "h2o_score": None, "qwen_score": None, "source_origin": "saved"})
    spec = {"source_sha256": {"module.py": inputs.sha(source.encode()), "other.py": inputs.sha(other.encode())},
            "inventory": inventory, "definition_keys": keys, "graph_edges": []}
    bundles = inputs.build_bundles({"invented": spec}, tmp_path / "corpus")
    bundle = next(iter(bundles.values()))

    def response(values):
        return {"query": "produce useful data and accompany it", "scope": {"include": ["module.py", "other.py"], "exclude": []},
            "match_status": "candidates", "results": deepcopy(values), "total_source_chars": sum(len(row["source"]) for row in values),
            "budget_exhausted": False, "token_usage": {"encoding": "cl100k_base", "tokens": 85, "budget": 4096, "truncated": False},
            "timings": {"search_s": 1.25}, "indexstats": {"elapsed_s": 0.01}}

    reference, proposal = response(rows), response(rows[1:])
    record = {"id": "invented-occurrence", "dataset": "invented", "query_id": "produce", "mode": "hybrid",
        "repetition": 1, "quality": True, "reference": reference, "proposal": proposal, "legacy": deepcopy(reference),
        "definition_keys": keys, "raw_rows": deepcopy(rows), "precoalesce_rows": deepcopy(rows),
        "limits": deepcopy(inputs.LIMITS), "source_bundle_id": bundle["id"],
        "scoring": {"kind": "present", "multi_file": True, "qualified": True,
            "groups": [[{"path": row["path"], "symbol": row["symbol"], "evidence_lines": [row["end_line"], row["end_line"]],
                "evidence_sha256": inputs.sha(inputs.physical_lines(row["source"])[-1].encode())}] for row in rows]}}
    operand = {name: deepcopy(record[name]) for name in inputs.OPERAND_FIELDS}
    return record, operand, bundle


def actual_invented_outcome(record, operand, bundle):
    from experiments.template_preservation.guard import apply_template_guard

    response, receipt = apply_template_guard(operand["reference"], operand["proposal"], source_bundle=bundle,
                                              definition_keys=operand["definition_keys"])
    payload = inputs.compact_payload(response)
    scores = {arm: audit.prior_audit.score(record["scoring"], value) for arm, value in
        (("legacy", record["legacy"]), ("coverage", operand["reference"]), ("prior_role", operand["proposal"]), ("candidate", response))}
    return {"id": record["id"], **{name: record[name] for name in ("dataset", "query_id", "mode", "repetition", "quality")},
        "multi_file": record["scoring"]["multi_file"], "operand_sha256": inputs.sha(inputs.canonical(operand)),
        "source_bundle_sha256": inputs.sha(inputs.canonical(bundle)), "response": response, "guard_receipt": receipt,
        "compact_payload": payload, "compact_sha256": inputs.sha(payload.encode()), "actual_compact_tokens": 85,
        "changed_vs_prior_role": not audit.exact_operand(response, operand["proposal"]), "scores": scores,
        "comparisons": {arm: audit.prior_audit.comparison(value, scores["candidate"]) for arm, value in scores.items() if arm != "candidate"}}


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_real_candidate_receipt_independent_reconstruction_roundtrip_and_restoration(tmp_path, newline):
    record, operand, bundle = invented(tmp_path, newline=newline)
    outcome = json.loads(json.dumps(actual_invented_outcome(record, operand, bundle), ensure_ascii=False))
    before = deepcopy((record, operand, bundle, outcome))
    assert audit.verify_record(record, operand, bundle, outcome, InventedCounter()) == outcome
    prepared = {"records": [record], "operands": [operand], "source_bundles": {bundle["id"]: bundle}}
    result = audit.audit_outcomes(prepared, [outcome], InventedCounter())
    assert result["quality_gate"]["qualified"] is True
    assert result["activity"]["protected_rows"] == 1
    assert result["restorations"] == [{"id": record["id"], "dataset": "invented", "query_id": "produce", "mode": "hybrid", "multi_file": True}]
    assert (record, operand, bundle, outcome) == before


@pytest.mark.parametrize("change", ["response", "score", "provenance", "typed", "signedzero", "decision", "calls", "protected",
    "witness", "scope", "quality", "identity", "payload", "tokens", "extra", "comparison", "bundlehash", "timings", "indexstats", "hidden_order"])
def test_independent_actual_outcome_rejects_material_field_type_witness_and_envelope_tampering(tmp_path, change):
    record, operand, bundle = invented(tmp_path)
    if change == "signedzero":
        operand["reference"]["results"][0]["retrieval_score"] = 0.0
        record["reference"] = deepcopy(operand["reference"])
    if change == "hidden_order":
        operand["reference"]["results"][0]["hidden_metadata"] = {"first": 1, "second": 2}
        record["reference"] = deepcopy(operand["reference"])
    outcome = actual_invented_outcome(record, operand, bundle)
    if change == "response":
        outcome["response"] = deepcopy(operand["proposal"])
    elif change == "score":
        outcome["response"]["results"][0]["retrieval_score"] = 0.75
    elif change == "provenance":
        del outcome["response"]["results"][0]["source_origin"]
    elif change == "typed":
        outcome["response"]["results"][0]["retrieval_score"] = 1
    elif change == "signedzero":
        outcome["response"]["results"][0]["retrieval_score"] = -0.0
    elif change == "decision":
        outcome["guard_receipt"]["accepted"] = True
    elif change == "calls":
        outcome["guard_receipt"]["calls"]["model"] = 1
    elif change == "protected":
        outcome["guard_receipt"]["protected_ids"] = []
    elif change == "witness":
        outcome["guard_receipt"]["reference_facts"][0]["witnesses"][0]["call"]["start_byte"] += 1
    elif change == "scope":
        outcome["response"]["scope"]["include"].reverse()
    elif change == "quality":
        outcome["quality"] = False
    elif change == "identity":
        outcome["id"] = "other"
    elif change == "payload":
        outcome["compact_payload"] += " "
    elif change == "tokens":
        outcome["actual_compact_tokens"] = 84
    elif change == "extra":
        outcome["unexpected"] = None
    elif change == "comparison":
        outcome["comparisons"]["prior_role"]["complete_gain"] = False
    elif change == "bundlehash":
        outcome["source_bundle_sha256"] = "0" * 64
    elif change == "timings":
        outcome["response"]["timings"]["search_s"] = 2.0
    elif change == "indexstats":
        outcome["response"]["indexstats"]["elapsed_s"] = 2.0
    elif change == "hidden_order":
        outcome["response"]["results"][0]["hidden_metadata"] = {"second": 2, "first": 1}
    with pytest.raises(ValueError):
        audit.verify_record(record, operand, bundle, outcome, InventedCounter())


@pytest.mark.parametrize("field", ["retrieval_score", "source_origin", "origin", "buffer_version", "added_metadata"])
def test_any_protected_dictionary_change_is_independently_rejected(tmp_path, field):
    _, operand, bundle = invented(tmp_path)
    operand["proposal"] = deepcopy(operand["reference"])
    operand["proposal"]["results"][0][field] = None
    response, receipt = audit.reconstruct_guard(operand, bundle)
    assert response == operand["reference"]
    assert receipt["fallback_reason"] == "protected_template_row_changed"


@pytest.mark.parametrize("change,reason", [("file", "invalid_source_inventory"), ("keys", "invalid_source_inventory"),
    ("row", "invalid_reference_facts"), ("duplicate", "invalid_reference"), ("proposal", "invalid_proposal")])
def test_source_and_operand_invalidities_fail_closed_with_independent_exact_receipt(tmp_path, change, reason):
    _, operand, bundle = invented(tmp_path)
    if change == "file":
        bundle["sources"]["module.py"] += "# drift\n"
    elif change == "keys":
        operand["definition_keys"][operand["reference"]["results"][0]["id"]][2] = True
    elif change == "row":
        operand["reference"]["results"][0]["source"] += "# drift\n"
    elif change == "duplicate":
        operand["reference"]["results"].append(deepcopy(operand["reference"]["results"][0]))
    else:
        operand["proposal"] = []
    from experiments.template_preservation.guard import apply_template_guard
    response, receipt = apply_template_guard(operand["reference"], operand["proposal"], source_bundle=bundle,
                                              definition_keys=operand["definition_keys"])
    assert audit.reconstruct_guard(operand, bundle) == (response, receipt)
    assert receipt["fallback_reason"] == reason
    assert response == operand["reference"]


def test_valid_partial_python_window_adds_no_template_protection_or_forced_fallback(tmp_path):
    _, operand, bundle = invented(tmp_path)
    row = operand["reference"]["results"][0]
    row["end_line"], row["truncated"] = 2, True
    row["source"] = "".join(inputs.physical_lines(row["source"])[:2])
    row["source_hash"] = inputs.sha(row["source"].encode())
    operand["reference"]["total_source_chars"] = sum(len(row["source"]) for row in operand["reference"]["results"])
    from experiments.template_preservation.guard import apply_template_guard
    expected = apply_template_guard(operand["reference"], operand["proposal"], source_bundle=bundle,
                                     definition_keys=operand["definition_keys"])
    assert audit.reconstruct_guard(operand, bundle) == expected
    response, receipt = expected
    assert receipt["reference_facts"][0]["status"] == "partial_definition"
    assert receipt["protected_ids"] == []
    assert receipt["accepted"] is True
    assert response == operand["proposal"]


def test_full_module_parser_uncertainty_protects_bound_row_without_positive_template_claim(tmp_path):
    _, operand, bundle = invented(tmp_path)
    source = bundle["sources"]["module.py"] + "! invalid module syntax\n"
    raw = source.encode()
    bundle["sources"]["module.py"] = source
    bundle["files"]["module.py"].update(bytes=len(raw), sha256=inputs.sha(raw), raw_base64=base64.b64encode(raw).decode())
    identity = {"dataset": bundle["dataset"], "source_sha256": {name: value["sha256"] for name, value in bundle["files"].items()},
                "definition_keys": bundle["definition_keys"]}
    bundle["id"] = "sources_" + inputs.sha(inputs.canonical(identity))
    from experiments.template_preservation.guard import apply_template_guard
    expected = apply_template_guard(operand["reference"], operand["proposal"], source_bundle=bundle,
                                     definition_keys=operand["definition_keys"])
    assert audit.reconstruct_guard(operand, bundle) == expected
    response, receipt = expected
    assert receipt["reference_facts"][0]["status"] == "uncertain_definition"
    assert receipt["reference_facts"][0]["witnesses"] == []
    assert response == operand["reference"]
    # Keeping the uncertain row allows an unrelated valid saved proposal.
    operand["proposal"] = deepcopy(operand["reference"])
    operand["proposal"]["results"].pop()
    response, receipt = audit.reconstruct_guard(operand, bundle)
    assert receipt["accepted"] is True
    assert response == operand["proposal"]


def test_exact_signed_zero_changes_to_protected_metadata_are_not_python_numeric_equality(tmp_path):
    _, operand, bundle = invented(tmp_path)
    operand["reference"]["results"][0]["retrieval_score"] = 0.0
    operand["proposal"] = deepcopy(operand["reference"])
    operand["proposal"]["results"][0]["retrieval_score"] = -0.0
    assert inputs.same(operand["reference"], operand["proposal"]) is True
    response, receipt = audit.reconstruct_guard(operand, bundle)
    assert audit.exact_same(response, operand["reference"])
    assert receipt["fallback_reason"] == "protected_template_row_changed"


def test_parser_value_error_is_explicit_uncertainty_in_both_independent_implementations(tmp_path, monkeypatch):
    _, operand, bundle = invented(tmp_path)

    def cannot_parse(*args, **kwargs):
        raise ValueError("invented parser uncertainty")

    monkeypatch.setattr(ast, "parse", cannot_parse)
    from experiments.template_preservation.guard import apply_template_guard
    expected = apply_template_guard(operand["reference"], operand["proposal"], source_bundle=bundle,
                                     definition_keys=operand["definition_keys"])
    assert audit.reconstruct_guard(operand, bundle) == expected
    assert expected[1]["reference_facts"][0]["status"] == "uncertain_definition"
    assert expected[1]["fallback_reason"] == "protected_template_row_changed"


def input_receipt_fixture():
    frozen = "1" * 40
    plan = {"source_freeze": {"commit": frozen, "sha256": {"source.py": "2" * 64}, "bound_blob_count": 1, "committed_at_unix": 1},
        "parent": {"commit": "3" * 40, "artifacts": {"old.json": "4" * 64}, "historical_sources": []},
        "prepared": {"path": audit.PREPARED, "artifacts": {"old/prepared.json": "5" * 64}},
        "counts": deepcopy(inputs.COUNTS), "runtime": {"python": "invented", "packages": {}},
        "frozen_at_utc": "2026-01-01T00:00:00+00:00"}
    report = {"schema": "template_preservation_input_audit_v1", "status": "passed", "checks_passed": True,
        "classification": audit.CLASSIFICATION, "plan_sha256": "6" * 64,
        **{key: deepcopy(plan[key]) for key in ("source_freeze", "parent", "prepared", "counts", "runtime")},
        "execution_commit": "7" * 40, "reviewed_at_utc": "2026-01-01T00:00:01+00:00",
        "source_only_calls": deepcopy(inputs.ZERO_CALLS), "new_outcomes_computed": 0}
    return plan, report


def test_input_only_receipt_roundtrip_without_any_guard_or_quality_calls():
    plan, report = input_receipt_fixture()
    assert audit.verify_input_receipt(report, plan, "6" * 64) == datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
    assert report["source_only_calls"] == inputs.ZERO_CALLS


@pytest.mark.parametrize("change", ["status", "checks", "plan", "source", "parent", "historical", "prepared", "counts",
    "runtime", "calls", "typed_calls", "typed_outcomes", "outcomes", "execution", "before_plan", "timezone", "extra"])
def test_independent_input_barrier_rejects_every_authority_and_no_outcome_mutation(change):
    plan, report = input_receipt_fixture()
    if change == "status":
        report["status"] = "failed"
    elif change == "checks":
        report["checks_passed"] = 1
    elif change == "plan":
        report["plan_sha256"] = "0" * 64
    elif change == "source":
        report["source_freeze"]["sha256"]["source.py"] = "0" * 64
    elif change == "parent":
        report["parent"]["artifacts"] = {}
    elif change == "historical":
        report["parent"]["historical_sources"].append({"commit": "0" * 40, "sha256": {}})
    elif change == "prepared":
        report["prepared"]["path"] = "elsewhere"
    elif change == "counts":
        report["counts"]["decisions"] = 477.0
    elif change == "runtime":
        report["runtime"]["python"] = "other"
    elif change == "calls":
        report["source_only_calls"]["guard"] = 1
    elif change == "typed_calls":
        report["source_only_calls"]["guard"] = False
    elif change == "typed_outcomes":
        report["new_outcomes_computed"] = False
    elif change == "outcomes":
        report["new_outcomes_computed"] = 1
    elif change == "execution":
        report["execution_commit"] = "abbreviated"
    elif change == "before_plan":
        report["reviewed_at_utc"] = "2025-12-31T23:59:59+00:00"
    elif change == "timezone":
        report["reviewed_at_utc"] = "2026-01-01T00:00:01"
    else:
        report["unexpected"] = 0
    with pytest.raises((ValueError, TypeError)):
        audit.verify_input_receipt(report, plan, "6" * 64)


def invented_source_commit(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Invented Audit"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "invented@example.invalid"], check=True)
    (tmp_path / "source.py").write_text("# invented frozen source\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "source.py"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "Freeze invented source"], check=True)
    commit = inputs.git(tmp_path, "rev-parse", "HEAD").decode().strip()
    when = int(inputs.git(tmp_path, "show", "-s", "--format=%ct", commit))
    hashes = {"source.py": inputs.file_sha(tmp_path / "source.py")}
    monkeypatch.setattr(audit, "PARENT", commit)
    monkeypatch.setattr(audit, "source_hashes", lambda root: {"source.py": inputs.file_sha(root / "source.py")})
    monkeypatch.setattr(audit, "source_authority", lambda root, authority: None)
    plan = {"source_freeze": {"commit": commit, "sha256": hashes, "bound_blob_count": 1, "committed_at_unix": when},
            "frozen_from_commit": commit}
    return plan, commit


@pytest.mark.parametrize("change", [None, "source_drift", "commit", "missing", "float_count", "bool_time", "time", "execution"])
def test_source_first_commit_barrier_rejects_uncommitted_or_incomplete_executing_source(tmp_path, monkeypatch, change):
    plan, commit = invented_source_commit(tmp_path, monkeypatch)
    if change == "source_drift":
        (tmp_path / "source.py").write_text("# uncommitted drift\n")
    elif change == "commit":
        plan["source_freeze"]["commit"] = "0" * 40
    elif change == "missing":
        plan["source_freeze"]["sha256"] = {}
    elif change == "float_count":
        plan["source_freeze"]["bound_blob_count"] = 1.0
    elif change == "bool_time":
        plan["source_freeze"]["committed_at_unix"] = True
    elif change == "time":
        plan["source_freeze"]["committed_at_unix"] += 1
    elif change == "execution":
        plan["frozen_from_commit"] = "0" * 40
    if change is None:
        assert audit.verify_source_freeze(plan, root=tmp_path) == (plan["source_freeze"], commit)
    else:
        with pytest.raises(ValueError):
            audit.verify_source_freeze(plan, root=tmp_path)


def test_source_authority_failure_precedes_every_observed_parent_or_runtime_read(tmp_path, monkeypatch):
    (tmp_path / "plan.json").write_text(json.dumps({"source_freeze": {}}))

    def forbidden(*args, **kwargs):
        pytest.fail("Observed parent/runtime read before complete source authority")

    monkeypatch.setattr(audit, "parent_binding", forbidden)
    monkeypatch.setattr(audit.prior_audit, "runtime", forbidden)
    with pytest.raises(ValueError, match="source-first"):
        audit.verify_plan(tmp_path, root=tmp_path)


@pytest.mark.parametrize("conflict", [False, True])
def test_input_freeze_closure_deduplicates_identical_paths_and_refuses_conflicting_hashes(tmp_path, conflict):
    results = tmp_path / "results"
    results.mkdir()
    for name in ("plan.json", "input-audit.json"):
        (results / name).write_text("{}\n")
    digest = "1" * 64
    plan = {"source_freeze": {"sha256": {"same.py": digest}},
        "parent": {"artifacts": {"same.py": "2" * 64 if conflict else digest, "prior.json": "3" * 64}},
        "prepared": {"artifacts": {"prior.json": "3" * 64}}}
    if conflict:
        with pytest.raises(ValueError, match="Conflicting"):
            audit.input_closure(plan, results, root=tmp_path)
    else:
        assert len(audit.input_closure(plan, results, root=tmp_path)) == 4


@pytest.mark.parametrize("input_only", [False, True])
def test_audit_cli_preserves_exclusive_sibling_failure_with_original_exception(tmp_path, monkeypatch, input_only):
    output = tmp_path / "independent-audit.json"
    failure = tmp_path / "independent-audit-failure.json"
    args = ["audit", "--results", str(tmp_path), "--output", str(output)] + (["--check-inputs"] if input_only else [])
    monkeypatch.setattr(sys, "argv", args)
    monkeypatch.setattr(audit, "cpu_only", nullcontext)
    error = RuntimeError("invented independent audit rejection")

    def reject(*args, **kwargs):
        raise error

    monkeypatch.setattr(audit, "audit_inputs" if input_only else "audit", reject)
    with pytest.raises(RuntimeError) as caught:
        audit.main()
    assert caught.value is error
    assert not output.exists()
    receipt = inputs.read(failure)
    assert receipt["error"] == str(error)
    assert receipt["audit_kind"] == ("input" if input_only else "cpu_replay")
    assert receipt["resume_allowed"] is False
    assert receipt["calls"] == {"model": 0, "index": 0, "retrieval": 0, "network": 0}
    assert "new_outcomes_computed" not in receipt


def test_audit_cli_failure_recorder_error_keeps_original_exception_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["audit", "--results", str(tmp_path), "--output", str(tmp_path / "audit.json")])
    monkeypatch.setattr(audit, "cpu_only", nullcontext)
    error, recorder = ValueError("invented audit failure"), OSError("invented receipt failure")

    def reject(*args, **kwargs):
        raise error

    def cannot_record(*args, **kwargs):
        raise recorder

    monkeypatch.setattr(audit, "audit", reject)
    monkeypatch.setattr(inputs, "write_json", cannot_record)
    with pytest.raises(ValueError) as caught:
        audit.main()
    assert caught.value is error
    assert caught.value.__cause__ is recorder


@pytest.mark.parametrize("existing", ["audit.json", "audit-failure.json"])
def test_audit_cli_never_overwrites_or_retries_existing_success_or_failure(tmp_path, monkeypatch, existing):
    path = tmp_path / existing
    path.write_text("preserved original bytes\n")
    monkeypatch.setattr(sys, "argv", ["audit", "--results", str(tmp_path), "--output", str(tmp_path / "audit.json")])

    def forbidden(*args, **kwargs):
        pytest.fail("An existing audit/failure must refuse before any new audit")

    monkeypatch.setattr(audit, "audit", forbidden)
    with pytest.raises(ValueError, match="exclusive new"):
        audit.main()
    assert path.read_text() == "preserved original bytes\n"
