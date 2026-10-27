"""Synthetic source-allocation protocol checks; no observed or fresh outcomes."""
from copy import deepcopy
from contextlib import nullcontext
from datetime import datetime, timezone
import gzip
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("source_allocation_replay", ROOT / "scripts/replay_source_allocation.py")
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)


def case(identifier="q"):
    return {"subset": "synthetic", "dataset": "toy", "query_id": identifier, "condition": "dense",
            "kind": "present", "multi_file": True,
            "groups": [[{"path": "a.py", "evidence_lines": [1, 1]}],
                       [{"path": "b.py", "evidence_lines": [1, 1]}]]}


def response(paths=("a.py",)):
    return {"results": [{"id": path, "path": path, "start_line": 1, "end_line": 1, "source": "pass\n"}
                        for path in paths], "total_source_chars": len(paths) * 5,
            "match_status": "candidates", "token_usage": {"tokens": 100, "truncated": False}}


def saved_rows(cases, names, *, paths=("a.py",)):
    return {(name, *replay.identity_tuple(value)): {"response": response(paths),
            "reference": {"line_number": number, "record_sha256": "record", "response_sha256": "response", "compact_sha256": "compact"}}
            for number, value in enumerate(cases, 1) for name in names}


def test_profile_is_scoped_with_two_new_candidates_and_two_immutable_negatives():
    before, candidates = replay.windows.CONDITIONS, replay.windows.CANDIDATES
    with replay.profile():
        assert tuple(replay.windows.CONDITIONS) == (*replay.CONTROLS, *replay.NEGATIVES, *replay.CANDIDATES)
        assert replay.windows.CANDIDATES == ("source_graph", "source_graph_all")
    assert replay.windows.CONDITIONS is before
    assert replay.windows.CANDIDATES is candidates


def test_profile_restores_helpers_after_failure():
    before = replay.windows.CONDITIONS
    with pytest.raises(RuntimeError), replay.profile():
        raise RuntimeError("stop")
    assert replay.windows.CONDITIONS is before


def test_negative_manifest_contains_references_only_and_exact_case_membership():
    cases = [case("first"), case("second")]
    negatives = saved_rows(cases, replay.NEGATIVES)
    document = replay.negative_manifest(cases, negatives)
    assert document["path"] == replay.NEGATIVE_PARENT + "/replay.jsonl.gz"
    assert all(tuple(value["negatives"]) == replay.NEGATIVES for value in document["cases"])
    assert all("response" not in reference for value in document["cases"] for reference in value["negatives"].values())
    assert list(negatives)[0] == ("aspect_fill", *replay.identity_tuple(cases[0]))
    del negatives[("aspect_balanced", *replay.identity_tuple(cases[1]))]
    with pytest.raises(ValueError, match="exactly the declared"):
        replay.negative_manifest(cases, negatives)


def test_negative_reader_preserves_canonical_record_hashes_without_stages(tmp_path):
    folder = tmp_path / replay.NEGATIVE_PARENT
    folder.mkdir(parents=True)
    originals = []
    with gzip.open(folder / "replay.jsonl.gz", "wt", encoding="utf-8") as stream:
        for number in range(365):
            for name in replay.NEGATIVES:
                value = {**replay.identity(case(str(number))), "selector": name,
                         "response": {"results": []}, "compact_payload": '{"results":[]}',
                         "compact_sha256": replay.sha(b'{"results":[]}'), "kind": "present", "multi_file": True,
                         "evidence_groups": [], "evidence_ranks": [], "stages": {"ranked": ["unchanged historical data"]}}
                originals.append(value)
                stream.write(json.dumps(value) + "\n")
    values = replay.negative_rows(tmp_path)
    assert len(values) == 730
    value = values[("aspect_balanced", *replay.identity_tuple(case("1")))]
    assert value["reference"]["line_number"] == 4
    assert value["reference"]["record_sha256"] == replay.sha(replay.canonical(originals[3]))
    assert "stages" not in value


def test_negative_reader_rejects_new_unfrozen_arm(tmp_path):
    folder = tmp_path / replay.NEGATIVE_PARENT
    folder.mkdir(parents=True)
    with gzip.open(folder / "replay.jsonl.gz", "wt", encoding="utf-8") as stream:
        stream.write(json.dumps({"selector": "invented"}) + "\n")
    with pytest.raises(ValueError, match="Unexpected previous"):
        replay.negative_rows(tmp_path)


def test_summary_keeps_historical_negatives_but_qualifies_only_new_arms():
    cases = [case("new"), case("partial")]
    controls = saved_rows(cases, replay.CONTROLS)
    negatives = saved_rows(cases, replay.NEGATIVES, paths=("b.py",))
    before = deepcopy(negatives)
    outcomes = []
    for value in cases:
        references = {name: controls[(name, *replay.identity_tuple(value))]["reference"] for name in replay.CONTROLS}
        responses = {(name, *replay.identity_tuple(value)): controls[(name, *replay.identity_tuple(value))]["response"]
                     for name in replay.CONTROLS}
        for name in replay.CANDIDATES:
            paths = ("a.py", "b.py") if value["query_id"] == "new" else (("b.py",) if name == "source_graph_all" else ("a.py",))
            outcomes.append(replay.reported_row(value, name, {"response": response(paths)}, responses, references))
    summary = replay.summarize(outcomes, controls, negatives, cases)
    assert tuple(summary["qualification"]) == replay.CANDIDATES
    assert summary["qualification"]["source_graph"]["qualified"]
    assert not summary["qualification"]["source_graph_all"]["qualified"]
    assert summary["qualification"]["source_graph_all"]["against_controls"]["coverage_control"]["lost_groups"] == 1
    assert set(summary["all_changes"]) == set(replay.CONDITIONS)
    assert summary["referenced_negative_rows"] == 4 and summary["stored_new_candidate_rows"] == 4
    assert summary["historical_negative_conditions"] == list(replay.NEGATIVES)
    assert negatives == before


@pytest.mark.parametrize("lost_control", replay.CONTROLS)
def test_any_individual_control_group_loss_blocks_candidate(lost_control):
    value = case()
    controls = saved_rows([value], replay.CONTROLS)
    controls[(lost_control, *replay.identity_tuple(value))]["response"] = response(("a.py", "b.py"))
    negatives = saved_rows([value], replay.NEGATIVES)
    responses = {(name, *replay.identity_tuple(value)): controls[(name, *replay.identity_tuple(value))]["response"]
                 for name in replay.CONTROLS}
    rows = [replay.reported_row(value, name, {"response": response(("b.py",))}, responses, {}) for name in replay.CANDIDATES]
    summary = replay.summarize(rows, controls, negatives, [value])
    assert all(not condition["qualified"] for condition in summary["qualification"].values())


def test_prior_known_coverage_gain_does_not_count_as_new():
    value = case()
    controls = saved_rows([value], replay.CONTROLS)
    controls[("coverage_control", *replay.identity_tuple(value))]["response"] = response(("a.py", "b.py"))
    negatives = saved_rows([value], replay.NEGATIVES)
    responses = {(name, *replay.identity_tuple(value)): controls[(name, *replay.identity_tuple(value))]["response"]
                 for name in replay.CONTROLS}
    rows = [replay.reported_row(value, name, {"response": response(("a.py", "b.py"))}, responses, {}) for name in replay.CANDIDATES]
    summary = replay.summarize(rows, controls, negatives, [value])
    assert all(not condition["qualified"] and condition["gained_additional_distinct_complete_queries"] == 0
               for condition in summary["qualification"].values())


def test_graph_snapshot_binds_all_extractors_and_preserves_edge_target_order(tmp_path):
    root = tmp_path / "src/semantic_search"
    root.mkdir(parents=True)
    for name in ("source", "languages", "javascript", "relations", "javascript_relations"):
        (root / (name + ".py")).write_text("# " + name + "\n")
    graph = SimpleNamespace(edges={("z.py", "f"): [("b.py", "g"), ("a.py", "h")], ("a.py", "h"): []})
    fixtures = {("toy", "signature"): {"source_sha256": {"z.py": "frozen"}, "graph": graph}}
    before = deepcopy(graph.edges)
    document = replay.graph_snapshot(tmp_path, fixtures)
    assert len(document["extractor_sha256"]) == 5
    assert document["fixtures"]["toy/signature"]["edges"] == [
        {"caller": ["a.py", "h"], "targets": []},
        {"caller": ["z.py", "f"], "targets": [["b.py", "g"], ["a.py", "h"]]}]
    assert graph.edges == before


def literal_row(*, origin="retrieval", related_to=None, identifier="one", **extra):
    return {"id": identifier, "path": "a.py", "start_line": 1, "end_line": 1,
            "source": "pass\n", "source_hash": replay.sha(b"pass\n"),
            "origin": origin, "related_to": related_to, **extra}


@pytest.mark.parametrize("change", ["origin", "owner", "identity", "overlay"])
def test_protected_operand_validation_retains_origin_owner_and_overlay(change):
    old = literal_row(origin="dependency", related_to="parent")
    current = deepcopy(old)
    if change == "origin":
        current["origin"] = "retrieval"
    elif change == "owner":
        current["related_to"] = "other"
    elif change == "identity":
        current["id"] = "other"
    else:
        current["buffer_version"] = 2
    assert not replay.protected_preserved([old], {"results": [current]}, str.splitlines)


def synthetic_source_case():
    from semantic_search import context, corpus, relations, selection
    path, lines, snippets, keys = "core.py", [], [], {}
    for number in range(1, 11):
        start = len(lines) + 1
        text = f"def fn{number}():\n    return " + ("fn1()\n" if number == 9 else str(number) + "\n")
        lines.extend(text.splitlines(keepends=True))
        snippet = corpus.Snippet(f"fn{number}", path, f"fn{number}", start, start + 1, text, replay.sha(text.encode()))
        snippets.append(snippet)
        keys[snippet.id] = (path, snippet.symbol, snippet.start_line, snippet.end_line)
    sources = {path: "".join(lines)}
    value = {**case(), "pairs": [(item, float(11 - number)) for number, item in enumerate(snippets, 1)],
             "sources": sources, "inventory": {item.id: item for item in snippets},
             "definition_keys": keys, "ambiguous_symbols": set(),
             "graph": relations.ReferenceGraph(sources, snippets),
             "original": {"query": "find loading records and prepare valid responses", "scope": {},
                          "match_status": "candidates", "results": []}}
    return value, {"selection": selection, "context": context, "corpus": corpus}


@pytest.mark.parametrize("name,accepted", [("source_graph", True), ("source_graph_all", False)])
def test_actual_new_evaluator_captures_one_planner_and_guard_without_public_metadata(name, accepted):
    value, modules = synthetic_source_case()
    before = deepcopy(value["pairs"])
    actual = replay.evaluate_candidate(value, name, modules)
    assert actual["selection_plan"] == actual["source_allocation_receipt"]
    assert actual["selection_plan"]["added_ids"] == ["fn9"]
    assert actual["guard_calls"] == actual["reference_calls"] == 1
    assert actual["source_guard_receipt"]["accepted"] is accepted
    assert actual["response"] == actual["proposal_response" if accepted else "reference_response"]
    assert all("source_guard" not in key and "selection_plan" not in key for key in actual["response"])
    assert value["pairs"] == before


def test_actual_inactive_plan_returns_exact_reference_even_when_source_is_preserved():
    value, modules = synthetic_source_case()
    value["original"]["query"] = "find loading records"
    actual = replay.evaluate_candidate(value, "source_graph", modules)
    receipt = actual["source_guard_receipt"]
    assert receipt["source_preserved"] is True and receipt["accepted"] is False
    assert receipt["fallback_reason"] == "inactive_plan"
    assert actual["response"] == actual["reference_response"]


@pytest.mark.parametrize("tamper", ["accepted", "protected_rows", "returned_response"])
def test_evaluator_rejects_tampered_actual_guard_decision(monkeypatch, tamper):
    from semantic_search import source_allocation
    value, modules = synthetic_source_case()
    original = source_allocation.guard_source_plan
    def changed(*args):
        chosen, receipt = original(*args)
        if tamper == "accepted":
            receipt["accepted"] = not receipt["accepted"]
        elif tamper == "protected_rows":
            receipt["protected_rows"] = []
        else:
            chosen["results"] = []
        return chosen, receipt
    monkeypatch.setattr(source_allocation, "guard_source_plan", changed)
    with pytest.raises(ValueError, match="guard receipt differs|exact proposal or reference"):
        replay.evaluate_candidate(value, "source_graph", modules)


@pytest.mark.parametrize("calls", [0, 2])
def test_evaluator_requires_one_actual_postpack_guard_and_reference(monkeypatch, calls):
    from semantic_search import source_allocation
    value, modules = synthetic_source_case()
    original = source_allocation.guard_source_context
    def changed(*args, **kwargs):
        result = deepcopy(args[0])
        for _ in range(calls):
            result = original(*args, **kwargs)
        return result
    monkeypatch.setattr(source_allocation, "guard_source_context", changed)
    with pytest.raises(ValueError, match="one actual reference"):
        replay.evaluate_candidate(value, "source_graph", modules)


def test_evaluator_rejects_multiple_actual_planner_calls(monkeypatch):
    from semantic_search import source_allocation
    value, modules = synthetic_source_case()
    original = source_allocation.source_graph_context
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        original(*args, **kwargs)
        return result
    monkeypatch.setattr(source_allocation, "source_graph_context", changed)
    with pytest.raises(ValueError, match="planner exactly once"):
        replay.evaluate_candidate(value, "source_graph", modules)


def test_protected_preservation_validates_actual_bytes_hash_and_owner_survival():
    from semantic_search.source import physical_lines
    owner = literal_row(identifier="parent")
    old = literal_row(origin="dependency", related_to="parent", content_hash="full")
    assert replay.protected_preserved([old], {"results": [old, owner]}, physical_lines)
    assert not replay.protected_preserved([old], {"results": [old]}, physical_lines)
    corrupt = {**old, "content_hash": "changed"}
    assert not replay.protected_preserved([old], {"results": [corrupt, owner]}, physical_lines)
    corrupt = {**old, "source_hash": "changed"}
    assert not replay.protected_preserved([old], {"results": [corrupt, owner]}, physical_lines)
    corrupt = {**owner}
    del corrupt["related_to"]
    assert not replay.protected_preserved([owner], {"results": [corrupt]}, physical_lines)


def test_candidate_evaluator_rejects_historical_arms_before_importing_new_rule():
    with pytest.raises(ValueError, match="Only new"):
        replay.evaluate_candidate({}, "aspect_fill", {})


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def prepared_freeze(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Synthetic source-allocation test")
    output = tmp_path / "results/synthetic"
    output.mkdir(parents=True)
    (tmp_path / "producer.txt").write_text("frozen source\n")
    bindings = {"producer.txt": replay.file_sha(tmp_path / "producer.txt")}
    monkeypatch.setattr(replay, "own_bindings", lambda root: bindings)
    plan = {"study": "source_allocation", "conditions": replay.CONDITIONS, "settings": replay.SETTINGS,
            "producer_sha256": bindings, "input_sha256": {}, "historical_parent": {"artifacts": {}},
            "negative_parent": {"artifacts": {}}, "artifacts": {}}
    for name in replay.FROZEN_FILES:
        (output / name).write_text(json.dumps(plan if name == "frozen-plan.json" else {}))
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "Prospective synthetic source freeze")
    return output, plan


def test_new_freeze_requires_own_committed_current_ancestry_and_all_graph_manifests(tmp_path, monkeypatch):
    output, plan = prepared_freeze(tmp_path, monkeypatch)
    actual = replay.validate_run(tmp_path, output, plan, datetime.now(timezone.utc).isoformat())
    assert actual["commit"] == git(tmp_path, "rev-parse", "HEAD")
    assert actual["bound_blob_count"] == len(replay.FROZEN_FILES) + 1
    assert "graph-fixtures.json" in replay.FROZEN_FILES
    assert "negative-references.json" in replay.FROZEN_FILES


@pytest.mark.parametrize("filename", ["producer.txt", "results/synthetic/negative-references.json", "results/synthetic/graph-fixtures.json"])
def test_uncommitted_bound_change_fails_before_candidate_work(tmp_path, monkeypatch, filename):
    output, plan = prepared_freeze(tmp_path, monkeypatch)
    with (tmp_path / filename).open("a") as stream:
        stream.write("changed")
    with pytest.raises(ValueError, match="Commit unchanged|changed after freeze"):
        replay.validate_run(tmp_path, output, plan, datetime.now(timezone.utc).isoformat())


def test_attempt_marker_prevents_resume_even_with_unchanged_freeze(tmp_path, monkeypatch):
    output, plan = prepared_freeze(tmp_path, monkeypatch)
    (output / "failure.json").write_text("{}")
    with pytest.raises(ValueError, match="Never overwrite or resume"):
        replay.validate_run(tmp_path, output, plan, datetime.now(timezone.utc).isoformat())


def test_prospective_freeze_cannot_follow_execution_timestamp(tmp_path, monkeypatch):
    output, plan = prepared_freeze(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="follows run start"):
        replay.validate_run(tmp_path, output, plan, "2000-01-01T00:00:00+00:00")


def test_control_only_freeze_never_evaluates_candidates_and_writes_only_prospective_data(tmp_path, monkeypatch):
    value = case()
    controls, negatives = saved_rows([value], replay.CONTROLS), saved_rows([value], replay.NEGATIVES)
    parent = {"artifacts": {replay.PARENT + "/source-fixtures.json": "fixture"}, "proof": {"original": "proof"}}
    negative_parent = {"artifacts": {}, "proof": {"negative": "proof"}}
    frozen_folder = tmp_path / replay.PARENT
    frozen_folder.mkdir(parents=True)
    (frozen_folder / "source-fixtures.json").write_text('{"fixtures": {}}')
    monkeypatch.setattr(replay.prior, "runtime", lambda root: ({"cpu": "pinned"}, b"ranks"))
    monkeypatch.setattr(replay.prior, "offline_cpu_guard", nullcontext)
    monkeypatch.setattr(replay.prior, "cpu_modules", lambda *args: nullcontext({}))
    monkeypatch.setattr(replay.previous, "historical_parent", lambda root: parent)
    monkeypatch.setattr(replay, "historical_negatives", lambda root: negative_parent)
    monkeypatch.setattr(replay.previous, "control_rows", lambda root: controls)
    monkeypatch.setattr(replay, "negative_rows", lambda root: negatives)
    monkeypatch.setattr(replay.windows, "load_inputs", lambda *args: ([value], {}, {}, {}))
    monkeypatch.setattr(replay.windows, "control_barrier", lambda *args: [{"legacy": "exact"}])
    monkeypatch.setattr(replay.windows, "coverage_barrier", lambda *args: [{"coverage": "exact"}])
    monkeypatch.setattr(replay.windows, "fixture_snapshot", lambda *args: {"fixtures": {}})
    monkeypatch.setattr(replay, "graph_snapshot", lambda *args: {"schema": "source_graph_fixtures_v1", "fixtures": {}})
    monkeypatch.setattr(replay, "own_bindings", lambda root: {"producer.txt": "frozen"})
    def forbidden(*args, **kwargs):
        pytest.fail("A control-only freeze evaluated a candidate")
    monkeypatch.setattr(replay, "evaluate_candidate", forbidden)
    output = tmp_path / "results/prospective"
    result = replay.freeze(tmp_path, tmp_path / "unused-corpus", output)
    assert result["new_variant_outcomes_computed"] == 0
    assert set(path.name for path in output.iterdir()) == set(replay.FROZEN_FILES)
    plan = replay.read(output / "frozen-plan.json")
    assert plan["barriers"]["new_variant_outcomes_computed"] == 0
    assert plan["negative_parent"] == negative_parent
    assert not any((output / name).exists() for name in replay.ATTEMPT_FILES)
    with pytest.raises(ValueError, match="new freeze directory"):
        replay.freeze(tmp_path, tmp_path / "unused-corpus", output)


def test_claimed_run_failure_is_retained_and_cannot_be_restarted(tmp_path, monkeypatch):
    output, plan = prepared_freeze(tmp_path, monkeypatch)
    monkeypatch.setattr(replay.prior, "runtime", lambda root: (_ for _ in ()).throw(RuntimeError("synthetic interrupted runtime")))
    with pytest.raises(RuntimeError, match="interrupted runtime"):
        replay.run(tmp_path, tmp_path / "unused", output)
    started = replay.read(output / "run-start.json")
    failed = replay.read(output / "failure.json")
    assert started["resume_allowed"] is False and failed["resume_allowed"] is False
    assert failed["progress"]["new_variant_rows"] == 0
    assert failed["progress"]["stage"] == "runtime"
    before = {name: (output / name).read_bytes() for name in ("run-start.json", "failure.json")}
    with pytest.raises(ValueError, match="Never overwrite or resume"):
        replay.run(tmp_path, tmp_path / "unused", output)
    assert all((output / name).read_bytes() == value for name, value in before.items())


def test_second_candidate_failure_retains_first_flushed_readable_trace(tmp_path, monkeypatch):
    value = case()
    controls = saved_rows([value], replay.CONTROLS)
    negatives = saved_rows([value], replay.NEGATIVES)
    parent = {"artifacts": {replay.PARENT + "/source-fixtures.json": "fixture"}, "proof": {}}
    negative_parent = {"artifacts": {}, "proof": {}}
    original = tmp_path / replay.PARENT
    original.mkdir(parents=True)
    (original / "source-fixtures.json").write_text('{"fixtures": {}}')
    output = tmp_path / "results/interrupted-synthetic"
    output.mkdir(parents=True)
    baseline, coverage = [{"legacy": "exact"}], [{"coverage": "exact"}]
    plan = {"runtime": {"cpu": "pinned"}, "historical_parent": parent, "negative_parent": negative_parent,
            "input_sha256": {}, "counts": replay.windows.counts([value]), "source_sha256": {},
            "baseline_controls_sha256": replay.sha(replay.canonical(baseline)),
            "prior_coverage_controls_sha256": replay.sha(replay.canonical(coverage))}
    replay.write_new(output / "frozen-plan.json", plan)
    digest = replay.file_sha(output / "frozen-plan.json")
    for name, data in (("baseline-gate.json", {"cases": baseline, "plan_sha256": digest}),
                       ("prior-coverage-gate.json", {"cases": coverage, "plan_sha256": digest}),
                       ("source-fixtures.json", replay.previous.fixture_pointer(parent)),
                       ("control-references.json", replay.previous.reference_manifest([value], controls)),
                       ("negative-references.json", replay.negative_manifest([value], negatives)),
                       ("graph-fixtures.json", {})):
        replay.write_new(output / name, data)
    monkeypatch.setattr(replay, "validate_run", lambda *args: {"commit": "synthetic-frozen-commit"})
    monkeypatch.setattr(replay.subprocess, "check_output", lambda *args, **kwargs: "synthetic-execution\n")
    monkeypatch.setattr(replay.prior, "runtime", lambda root: ({"cpu": "pinned"}, b"ranks"))
    monkeypatch.setattr(replay.prior, "offline_cpu_guard", nullcontext)
    monkeypatch.setattr(replay.prior, "cpu_modules", lambda *args: nullcontext({}))
    monkeypatch.setattr(replay.previous, "historical_parent", lambda root: parent)
    monkeypatch.setattr(replay, "historical_negatives", lambda root: negative_parent)
    monkeypatch.setattr(replay.previous, "control_rows", lambda root: controls)
    monkeypatch.setattr(replay, "negative_rows", lambda root: negatives)
    monkeypatch.setattr(replay.windows, "load_inputs", lambda *args: ([value], {}, {}, {}))
    monkeypatch.setattr(replay.windows, "fixture_snapshot", lambda *args: {"fixtures": {}})
    monkeypatch.setattr(replay, "graph_snapshot", lambda *args: {})
    monkeypatch.setattr(replay.windows, "control_barrier", lambda *args: baseline)
    monkeypatch.setattr(replay.windows, "coverage_barrier", lambda *args: coverage)
    responses = {(name, *replay.identity_tuple(value)): controls[(name, *replay.identity_tuple(value))]["response"]
                 for name in replay.CONTROLS}
    monkeypatch.setattr(replay.previous, "verify_parent_controls", lambda *args: (responses, [{"control": "exact"}]))
    calls = []
    def candidate(case, name, modules, **kwargs):
        calls.append(name)
        if len(calls) == 2:
            # The first trace is already on disk before the next candidate runs.
            # Gzip has not yet received its footer, so inspect its flushed bytes.
            assert (output / "replay.jsonl.gz").stat().st_size > 10
            raise RuntimeError("synthetic second candidate interrupted")
        return {"response": response(("a.py", "b.py")), "source_allocation_receipt": {"invented": "trace"}}
    monkeypatch.setattr(replay, "evaluate_candidate", candidate)
    with pytest.raises(RuntimeError, match="second candidate interrupted"):
        replay.run(tmp_path, tmp_path / "unused-corpus", output)
    with gzip.open(output / "replay.jsonl.gz", "rt", encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream]
    assert len(rows) == 1 and rows[0]["selector"] == "source_graph"
    assert rows[0]["source_allocation_receipt"] == {"invented": "trace"}
    assert rows[0]["evidence_ranks"] == [1, 2]
    assert calls == ["source_graph", "source_graph_all"]
    failed = replay.read(output / "failure.json")
    assert failed["progress"]["attempted_new_variant_rows"] == 2
    assert failed["progress"]["new_variant_rows"] == 1
    assert failed["progress"]["current_condition"] == "source_graph_all"
    assert failed["resume_allowed"] is False
    assert replay.read(output / "control-gate.json")["new_variant_rows_before_gate"] == 0
    assert not (output / "summary.json").exists() and not (output / "findings.md").exists()


def test_cli_has_both_exclusive_modes_without_importing_models():
    process = subprocess.run([sys.executable, str(ROOT / "scripts/replay_source_allocation.py"), "--help"],
                             text=True, capture_output=True)
    assert process.returncode == 0
    assert "--freeze" in process.stdout and "--run" in process.stdout
    assert "--corpus-root" in process.stdout
