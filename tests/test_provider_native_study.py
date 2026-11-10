"""Invented source preparation, fixed matrix and failure-retention contracts."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
import json
import importlib
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from semantic_search.corpus import extract_snippets
from semantic_search.relations import ReferenceGraph

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
study = importlib.import_module("experiments.provider_native.study")


@pytest.mark.parametrize("long_definition", [False, True])
def test_source_inventory_uses_actual_path_and_decoder_boundaries(tmp_path, monkeypatch, long_definition):
    project, corpus = tmp_path/"project", tmp_path/"corpus"
    (corpus/"src").mkdir(parents=True)
    source = "def implementation():\n" + ("    value = 1\n" * 180 if long_definition else "")
    source += "    return 'literal café'\n"
    (corpus/"src/api.py").write_text(source)
    (corpus/"src/excluded.py").write_text("invalid syntax !\n")
    (corpus/"src/readme.txt").write_text("unsupported source\n")
    for args in (["init", "-q"], ["add", "."], ["-c", "user.name=Invented", "-c",
        "user.email=invented@example.invalid", "commit", "-qm", "invented fixture"]):
        subprocess.run(["git", "-C", str(corpus), *args], check=True)
    revision = subprocess.check_output(["git", "-C", str(corpus), "rev-parse", "HEAD"], text=True).strip()
    data = {"source_root": "src", "source_exclusions": ["src/excluded.py"]}
    document_path = project/study.LEGACY/"source-inventory.json"
    document_path.parent.mkdir(parents=True)
    (project/"dataset.json").write_text(json.dumps(data))
    sources, keys = {"src/api.py": source}, {}
    snippets = extract_snippets(corpus, "src", 160, 40, sources=sources,
                                source_exclusions=data["source_exclusions"], definition_keys=keys)
    if long_definition:
        assert [(item.start_line, item.end_line) for item in snippets] == [(1, 160), (121, 182)]
    graph = ReferenceGraph(sources, snippets)
    document = {"inventory": [asdict(item) for item in snippets],
        "definition_keys": {key: list(value) for key, value in keys.items()},
        "graph_edges": [{"caller": list(caller), "targets": [list(target) for target in targets]}
                        for caller, targets in sorted(graph.edges.items())]}
    document_path.write_text(json.dumps(document))
    legacy = {"binding": {"dataset_path": "dataset.json", "dataset_sha256": study.file_sha(project/"dataset.json"),
        "revision": revision, "source_sha256": {"src/api.py": study.file_sha(corpus/"src/api.py")}}}
    monkeypatch.setattr(study, "ROOT", project)
    assert study.inventory(corpus, legacy) == (data, sources, document)
    (corpus/"src/api.py").write_text(source+"# changed\n")
    with pytest.raises(ValueError, match="source bytes changed"):
        study.inventory(corpus, legacy)


def test_fixed_matrix_rotates_all_eight_slots_without_changing_query_order():
    queries = [{"id": str(i)} for i in range(14)]
    rows = list(study.schedule(queries))
    assert len(rows) == 336
    order = [(arm, mode) for mode in study.MODES for arm in study.ARMS]
    for repetition in range(1, 4):
        for index, query in enumerate(queries):
            group = rows[((repetition-1)*14+index)*8:((repetition-1)*14+index+1)*8]
            offset = (index+repetition-1) % 8
            assert group == [(repetition, query, arm, mode) for arm, mode in order[offset:]+order[:offset]]


def test_metrics_uses_complete_backend_duration_and_nested_integration_fields(monkeypatch):
    rows = []
    for mode in study.MODES:
        for arm, times in (("original_role", [1., 3., 5.]), ("fast_role", [.5, 1., 2.])):
            for number, duration in enumerate(times):
                rows.append({"query_id": str(number), "repetition": 1, "arm": arm, "retrieval": mode,
                    "condition": arm, "elapsed_backend_s": duration,
                    "provider_integration": {"factory_elapsed_s": .01, "provider_elapsed_s": .1}})
    monkeypatch.setattr(study.old, "summarize", lambda selected: {"arms": [r["arm"] for r in selected]})
    value = study.metrics(rows)
    for mode in study.MODES:
        assert value["provider_comparison"][mode] == {"original_median_s": 3., "fast_median_s": 1.,
            "original_p95_s": 5., "fast_p95_s": 2., "fast_over_original_median_ratio": 1/3,
            "median_paired_delta_s": -2., "timed_requests_per_arm": 3}
        assert value["phase_medians"][mode]["fast_role"] == {"factory_setup_s": .01, "provider_call_s": .1}
    assert all(arm == "candidate" for arm in value["role_arms"]["fast_role"]["arms"])
    assert rows[0]["arm"] == "original_role"  # projection never rewrites recorded rows


def failed_setup(tmp_path, monkeypatch, error):
    args = SimpleNamespace(output=tmp_path/"result", tokenizer_cache=tmp_path/"tokens", model=tmp_path/"invented-model")
    args.output.mkdir()
    args.tokenizer_cache.mkdir()
    (args.tokenizer_cache/study.old.base.replay.CACHE_KEY).write_bytes(b"invented tokenizer")
    monkeypatch.setattr(study.old.base.replay, "CACHE_SHA256", study.sha(b"invented tokenizer"))
    plan = {"source_freeze": {"invented": True}}
    (args.output/"plan.json").write_text(json.dumps(plan))
    monkeypatch.setattr(study, "verify_plan", lambda unused: (plan, {}, {}, {}, {"committed_at_unix": 0}))
    monkeypatch.setattr(study, "git", lambda *unused: "invented execution")

    @contextmanager
    def lease(unused):
        value = {"path": "invented", "acquired_at": "before", "released_at": None}
        try:
            yield value
        finally:
            value["released_at"] = "after"

    monkeypatch.setattr(study.old.transport, "gpu_lease", lease)
    monkeypatch.setattr(study.old.context, "encoding", lambda: None)

    def constructor(*a, **k):
        raise error

    monkeypatch.setattr(study.old, "RecordingEmbedder", constructor)
    return args


def test_native_setup_failure_preserves_finalized_ledgers_and_releases_lease(tmp_path, monkeypatch):
    original = RuntimeError("invented model setup failure")
    args = failed_setup(tmp_path, monkeypatch, original)
    with pytest.raises(RuntimeError) as caught:
        study.run(args)
    assert caught.value is original
    folder = args.output/"attempt"
    failure = study.read(folder/"failure.json")
    assert failure["completed_requests"] == failure["attempted_requests"] == 0
    assert failure["resume_allowed"] is False
    assert study.read(folder/"gpu-lease-released.json")["released_at"] == "after"
    import gzip
    for repetition in range(1, 4):
        assert gzip.decompress((folder/f"searches-r{repetition}.jsonl.gz").read_bytes()) == b""
    assert gzip.decompress((folder/"warmups.jsonl.gz").read_bytes()) == b""
    with pytest.raises(ValueError, match="No overwrite"):
        study.run(args)


def test_failure_recorder_cannot_replace_original_setup_error(tmp_path, monkeypatch):
    original, recording = RuntimeError("setup"), OSError("failure receipt")
    args = failed_setup(tmp_path, monkeypatch, original)
    write = study.write

    def failing(path, value):
        if path.name == "failure.json":
            raise recording
        return write(path, value)

    with patch.object(study, "write", failing), pytest.raises(RuntimeError) as caught:
        study.run(args)
    assert caught.value is original and caught.value.__cause__ is recording


def test_partial_setup_records_actual_work_after_a_metadata_failure(tmp_path, monkeypatch):
    args = failed_setup(tmp_path, monkeypatch, RuntimeError("unused"))
    plan = {"source_freeze": {}, "runtime_manifest": {"model_binding": {"metadata": {"expected": True}}}}
    monkeypatch.setattr(study, "verify_plan", lambda unused: (plan, {}, {}, {}, {"committed_at_unix": 0}))
    closed = []
    embedder = SimpleNamespace(metadata={"wrong": True}, document_api_records=[{"status": "started", "texts": ["invented"]}],
        query_calls=0, clause_query_calls=0, clause_query_texts=0, document_calls=1, close=lambda: closed.append(True))
    monkeypatch.setattr(study.old, "RecordingEmbedder", lambda *a, **k: embedder)
    with pytest.raises(ValueError, match="metadata differs"):
        study.run(args)
    partial = study.read(args.output/"attempt/setup-partial.json")
    assert partial["document_calls"] == embedder.document_api_records
    assert partial["document_api_calls"] == 1 and partial["external_forward_events"] == []
    assert closed == [True]


@pytest.mark.parametrize("change", [None, "false_zero", "bool_call", "source", "inventory", "classification", "schema", "commit", "parent"])
def test_model_admission_requires_exact_typed_independent_input_barrier(tmp_path, monkeypatch, change):
    original = tmp_path/study.LEGACY/"input-audit.json"
    original.parent.mkdir(parents=True)
    original.write_text("{}\n")
    monkeypatch.setattr(study, "ROOT", tmp_path)
    plan = {"artifacts": {"source-inventory.json": "inventory hash"},
        "source_freeze": {"sha256": {"study.py": "source hash"}}, "parent": {"commit": study.PARENT, "artifacts": {"old": "hash"}}}
    audit = {"schema": "provider_native_input_audit_v1", "status": "passed", "checks_passed": True,
        "classification": study.CLASSIFICATION, "plan_sha256": "plan hash", "source_inventory_sha256": "inventory hash",
        "source_freeze": deepcopy(plan["source_freeze"]), "parent": deepcopy(plan["parent"]),
        "parent_verification": {"commit": study.PARENT, "bound_artifacts": 1,
            "old_native_publication": study.PUBLICATION, "old_native_requests": 252,
            "cpu_parity_integrity_passed": True, "dependency_quality_qualified": False},
        "source_sha256": deepcopy(plan["source_freeze"]["sha256"]),
        "source_only_calls": {key: 0 for key in ("model", "index", "retrieval", "selector", "postprocessor",
            "rule_activation", "gold_coverage_scoring", "new_native_outcomes")},
        "new_native_outcomes_computed": 0, "execution_commit": "a"*40,
        "original_native_input_audit_sha256": study.file_sha(original)}
    if change is None:
        study.verify_input_barrier(audit, plan, "plan hash")
        return
    if change == "false_zero":
        audit["new_native_outcomes_computed"] = False
    elif change == "bool_call":
        audit["source_only_calls"]["model"] = False
    elif change == "source":
        audit["source_sha256"]["study.py"] = "different"
    elif change == "inventory":
        audit["source_inventory_sha256"] = "different"
    elif change == "classification":
        audit["classification"] = "fresh_holdout"
    elif change == "schema":
        audit["schema"] = "another_audit"
    elif change == "commit":
        audit["execution_commit"] = "HEAD"
    else:
        audit["parent"]["commit"] = "different"
    with pytest.raises(ValueError, match="source-only input barrier"):
        study.verify_input_barrier(audit, plan, "plan hash")


@pytest.mark.parametrize("commit", [None, "HEAD", "abc123", "0"*39, "G"*40])
def test_preparation_requires_full_committed_source_identity_before_inputs(tmp_path, monkeypatch, commit):
    monkeypatch.setattr(study, "source_authority", lambda: None)
    args = SimpleNamespace(output=tmp_path/"unused", source_freeze=commit)
    with pytest.raises(ValueError, match="full source-freeze"):
        study.freeze(args)
    assert not args.output.exists()
