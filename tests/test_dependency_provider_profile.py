"""Invented native-shaped receipts only; no checkpoint, corpus, index or network."""
from copy import deepcopy
import gzip
import hashlib
import importlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
from unittest.mock import patch

import numpy as np
import pytest

from experiments.dependency_context import provider_profile as profile
from semantic_search import semantic_features as sf
from semantic_search import semantic_provider as sp


QUERY = "Find shared requests for running transports, and publish successful values to active observers"


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def clean_process(code, *arguments):
    """Strict CLI contracts run without modules imported by unrelated tests."""
    environment = {**os.environ, "PYTHONPATH": str(profile.ROOT / "src") + os.pathsep + str(profile.ROOT)}
    result = subprocess.run([sys.executable, "-c", code, *map(str, arguments)], cwd=profile.ROOT,
        env=environment, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def vector(index=0):
    value = np.zeros(1024, dtype=np.float32)
    value[index] = np.float32(0.5)
    value[-1] = np.float32(-0.0)
    value.flags.writeable = False
    return value


def events(texts):
    lengths = [len(text) for text in texts]
    event = {"schema": "qwen_embedding_forward_v1", "status": "started", "forward_index": 0,
        "input_count": len(texts), "batch_input_count": len(texts), "input_indices": list(range(len(texts))),
        "input_sha256": [sha(text) for text in texts], "token_ids_sha256": [sha("invented " + text) for text in texts],
        "token_lengths": lengths, "padded_tokens": len(texts) * max(lengths), "padded_shape": [len(texts), max(lengths)],
        "expected_output_shape": [len(texts), 1024], "elapsed_seconds": 0.0}
    return [event, {**deepcopy(event), "status": "completed", "elapsed_seconds": 0.02,
        "duration_seconds": 0.01, "output_shape": [len(texts), 1024], "output_dtype": "float32"}]


def invented_record(mode="dense", windows=3, *, bypass=False):
    query = "Find an implementation" if bypass else QUERY
    rows = []
    for index in range(windows):
        source = f"def invented_{index}():\n    return {index}\n"
        rows.append({"id": f"invented-{index}", "path": f"src/invented_{index}.py", "symbol": f"invented_{index}",
            "start_line": 1, "end_line": 2, "window_end_line": 2, "source": source,
            "source_hash": sha(source), "content_hash": sha(source), "truncated": False, "source_origin": "saved",
            "origin": "retrieval", "related_to": None, "retrieval_score": 0.5, "qwen_score": None, "h2o_score": None})
    reference = {"query": query, "retrieval": mode, "results": deepcopy(rows[:2]), "match_status": "candidates"}
    model = {"id": sf.MODEL_ID, "revision": sf.MODEL_REVISION, "dimensions": 1024, "query_prompt": sf.QUERY_PROMPT,
        "normalization": "L2, float32", "batch_size": 4, "packages": {"invented": "0.0"}}
    binding = {"query": query, "retrieval": mode, "source_snapshot": "invented-source-snapshot",
        "model_metadata": model, "model_fingerprint": sha(json.dumps(model, sort_keys=True, separators=(",", ":")))}
    record = {"query_id": "invented-active" if not bypass else "invented-inactive", "arm": "candidate", "repetition": 1,
        "retrieval": mode, "response": deepcopy(reference), "query_vector": vector().tolist(),
        "query_vector_sha256": hashlib.sha256(vector().tobytes()).hexdigest(), "resident_reads": [],
        "candidate_capture": {"reference_response": reference, "ranked_candidates": rows,
            "request_binding": binding, "provider_receipts": [], "features": None}}
    if bypass:
        assert sf.expected_facets(query) == []
        return record
    facets = sf.expected_facets(query)
    vectors = {row["id"]: vector(index % 100) for index, row in enumerate(rows)}
    request = {"requested_rows": deepcopy(rows), "model_metadata": deepcopy(model), "returned_vectors": {
        key: {"vector": value.tolist(), "float32_le_sha256": hashlib.sha256(value.tobytes()).hexdigest()}
        for key, value in vectors.items()}}
    index = profile.ReplayIndex(request, vectors)
    extra = profile.ReplayEmbedder(model, facets, [vector(index + 1) for index in range(len(facets)-1)],
        events([sf.QUERY_PROMPT + value for value in facets[1:]]))
    captures = []
    context = {**binding, "query_vector": vector(), "query_forward_events": events([sf.QUERY_PROMPT + query]),
        "index": index, "embedder": extra, "confirm_snapshot": lambda: None,
        "source_provenance": lambda path: {"source_origin": "saved"}}
    record["candidate_capture"]["features"] = sp.make_live_feature_provider(context, capture=captures.append)(reference, rows)
    record["candidate_capture"]["provider_receipts"] = captures
    record["resident_reads"] = [request]
    return record


@pytest.mark.parametrize("mode", ["dense", "hybrid"])
def test_saved_native_representation_and_receipts_replay_exactly(mode):
    record = invented_record(mode, windows=50)
    original = deepcopy(record)
    prepared = profile.prepare_record(record)
    assert len(prepared["vectors"]) == 50
    assert all(type(value) is np.ndarray and value.dtype == np.float32 and not value.flags.writeable
        for value in prepared["vectors"].values())
    result = profile.one_replay(prepared)
    assert profile.canonical(result["features"]) == profile.canonical(record["candidate_capture"]["features"])
    assert profile.canonical(result["receipts"]) == profile.canonical(record["candidate_capture"]["provider_receipts"])
    assert result["calls"] == {"saved_clause_vector_replay": 1, "saved_resident_vector_replay": 1,
        "saved_snapshot_confirmation": 3}
    assert record == original
    assert struct.pack("<1024f", *result["features"]["facet_vectors"][0]) == prepared["query_vector"].tobytes()


@pytest.mark.parametrize("mode", ["dense", "hybrid"])
def test_actual_adapter_bypass_does_not_invent_provider_work(mode):
    record = invented_record(mode, bypass=True)
    with patch.object(sp, "make_live_feature_provider", side_effect=AssertionError("No provider for bypass")):
        result, _ = profile.profile_record(record)
    assert result["execution"] == "native_adapter_bypassed_provider"
    assert result["uninstrumented_replay_wall_s"] == []
    assert result["equivalence"]["all_passes_exact"]
    assert all(value == 0 for value in result["replay_calls_per_pass"].values())


def test_profile_attributes_original_validation_and_restores_original_globals():
    record = invented_record()
    originals = {name: getattr(sp, name) for name in ("make_features", "_vector", "_model", "_required_rows")}
    result, stats = profile.profile_record(record, repetitions=2)
    assert all(getattr(sp, name) is value for name, value in originals.items())
    assert len(result["uninstrumented_replay_wall_s"]) == 2
    assert any(key[2] == "_vector" for key in stats.stats)
    assert sum(event["stage"] == "make_features" for event in result["stage_events"]) == 2
    assert all(event["inclusive_s"] >= event["exclusive_s"] >= 0 for event in result["stage_events"])
    assert result["new_model_forward_calls"] == result["sqlite_index_calls"] == result["retrieval_calls"] == 0


@pytest.mark.parametrize("change", ["query_hash", "query_value", "source", "metadata", "row_order", "receipt", "missing_vector"])
def test_mutated_native_bindings_fail_closed(change):
    record = invented_record()
    if change == "query_hash":
        record["query_vector_sha256"] = "0" * 64
    elif change == "query_value":
        record["query_vector"][0] = 0.1
    elif change == "source":
        record["candidate_capture"]["ranked_candidates"][0]["source"] += "# mutated\n"
    elif change == "metadata":
        record["resident_reads"][0]["model_metadata"]["batch_size"] = 3
    elif change == "row_order":
        record["resident_reads"][0]["requested_rows"].reverse()
    elif change == "receipt":
        record["candidate_capture"]["provider_receipts"][0]["windows"]["invented-0"]["float32_le_sha256"] = "f" * 64
    else:
        del record["resident_reads"][0]["returned_vectors"]["invented-0"]
    with pytest.raises(ValueError):
        profile.prepare_record(record)


@pytest.mark.parametrize("value", [[1.0] * 1023, [float("nan")] * 1024, [1] * 1024, [0.0] * 1024,
    [0.1] * 1024, [1e100] * 1024, tuple([1.0] * 1024)])
def test_saved_vectors_require_original_exact_float32_values(value):
    with pytest.raises(ValueError):
        profile.exact_vector(value, "0" * 64)


def test_saved_query_and_clause_stubs_forbid_inference_and_enforce_saved_lookup():
    prepared = profile.prepare_record(invented_record())
    request = prepared["record"]["resident_reads"][0]
    index = profile.ReplayIndex(request, prepared["vectors"])
    with pytest.raises(ValueError):
        index.read_semantic_vectors([], model_metadata=prepared["metadata"], cancel_check=lambda: None)
    embedder = profile.ReplayEmbedder(prepared["metadata"], [], [], [])
    with pytest.raises(AssertionError):
        embedder.embed_query("never")
    with pytest.raises(AssertionError):
        embedder.embed_documents(["never"])
    with pytest.raises(ValueError):
        embedder.embed_queries(["changed"], cancel_check=lambda: None, forward_observer=lambda event: None)


def test_cpu_guard_blocks_network_and_new_model_imports_and_restores():
    clean_process('''
import importlib
import socket
import sys
from experiments.dependency_context import provider_profile as profile
original = socket.create_connection
with profile.cpu_only_guard(strict=True):
    try:
        socket.create_connection(("example.invalid", 443))
    except RuntimeError as error:
        assert "network" in str(error)
    else:
        raise AssertionError("CPU guard allowed network")
    try:
        importlib.import_module("semantic_search.qwen_worker")
    except RuntimeError as error:
        assert "model/index" in str(error)
    else:
        raise AssertionError("CPU guard allowed model import")
assert socket.create_connection is original
assert not any(type(finder).__name__ == "Guard" and type(finder).__module__ == profile.__name__ for finder in sys.meta_path)
''')


def test_stage_instrumentation_restored_when_original_provider_raises():
    prepared = profile.prepare_record(invented_record())
    old = sp._vector
    prepared["provenances"]["src/invented_0.py"] = {}
    with pytest.raises(ValueError, match="provenance"):
        profile.one_replay(prepared, stage_trace=True)
    assert sp._vector is old


def test_stream_selection_binds_original_compressed_line_and_avoids_quality_selection(tmp_path):
    records = [invented_record(mode, bypass=bypass) for mode in ("dense", "hybrid") for bypass in (False, True)]
    path = tmp_path / "invented.jsonl.gz"
    raw_lines = [profile.canonical(row) + b"\n" for row in records]
    with gzip.open(path, "wb") as stream:
        stream.writelines(raw_lines)
    selected = profile.select_records(path, active_per_mode=1)
    assert len(selected) == 4
    for ordinal, item in enumerate(selected):
        assert item["source"]["jsonl_line"] == ordinal + 1
        assert item["source"]["line_sha256"] == profile.digest(raw_lines[ordinal])
        assert item["source"]["canonical_record_sha256"] == profile.digest(profile.canonical(records[ordinal]))


def test_write_new_is_exclusive_and_fsyncs_file_and_directory(tmp_path):
    path = tmp_path / "receipt.json"
    with patch.object(profile.os, "fsync", wraps=profile.os.fsync) as fsync:
        profile.write_new(path, {"explicit_new_inference": 0})
    assert fsync.call_count == 2
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        profile.write_new(path, {"mutated": True})
    assert path.read_bytes() == before


def test_provider_module_authority_rejects_other_checkouts(tmp_path):
    with pytest.raises(ValueError, match="escapes"):
        profile.provider_modules(Path(tmp_path))


@pytest.mark.parametrize("name", profile.SOURCE_NAMES)
def test_all_executing_original_source_modules_must_use_bound_checkout(name, tmp_path):
    module = importlib.import_module("semantic_search" if name == "__init__" else "semantic_search." + name)
    with patch.object(module, "__file__", str(tmp_path / (name + ".py"))):
        with pytest.raises(ValueError, match="original provider source escapes"):
            profile.provider_modules()


@pytest.mark.parametrize("name", ["experiments", "experiments.dependency_context"])
def test_profiler_package_initializers_are_bound_and_executing_paths_checked(name, tmp_path):
    module = importlib.import_module(name)
    expected = "experiments/__init__.py" if name == "experiments" else "experiments/dependency_context/__init__.py"
    assert expected in profile.PROFILE_SOURCES
    with patch.object(module, "__file__", str(tmp_path / "unbound.py")):
        with pytest.raises(ValueError, match="profiler package escapes"):
            profile.executing_profiler()


def test_supplied_root_and_profiler_own_path_cannot_escape_binding(tmp_path):
    with patch.object(profile, "__file__", str(tmp_path / "unbound.py")):
        with pytest.raises(ValueError, match="Executing profiler escapes"):
            profile.provider_modules()
    with patch.object(profile, "bind_native_study", side_effect=AssertionError("No input read before authority")):
        for paired in (False, True):
            with pytest.raises(ValueError, match="Executing profiler escapes"):
                if paired:
                    profile.run_paired(tmp_path / "unopened", tmp_path / "new-output", freeze="0"*40, root=tmp_path)
                else:
                    profile.run(tmp_path / "unopened", tmp_path / "new-output", root=tmp_path)
    assert not (tmp_path / "new-output").exists()


@pytest.mark.parametrize("ordinal", [0, 1])
def test_paired_invented_requests_alternate_and_preserve_exact_outputs(ordinal):
    record = invented_record(windows=5)
    result = profile.paired_record(record, repetitions=2, ordinal=ordinal)
    assert result["exact_equivalence"]
    assert result["passes"][0]["order"] == (["original", "fast"] if ordinal == 0 else ["fast", "original"])
    assert result["passes"][1]["order"] == list(reversed(result["passes"][0]["order"]))
    for entry in result["passes"]:
        assert entry["original"]["features_sha256"] == entry["fast"]["features_sha256"]
        assert entry["original"]["receipts_sha256"] == entry["fast"]["receipts_sha256"]
        assert entry["original"]["calls"] == entry["fast"]["calls"]
    assert result["new_model_forwards"] == result["retrieval_calls"] == result["sqlite_accesses"] == 0
    assert "both factory constructors" in result["timing_boundary"]
    assert "fast clone setup" in result["timing_boundary"]


def test_paired_invented_adapter_bypass_has_no_measured_or_invented_provider():
    result = profile.paired_record(invented_record(bypass=True), repetitions=2)
    assert result["median_provider_call_s"] == {"original": None, "fast": None}
    for entry in result["passes"]:
        assert entry["original"]["wall_s"] is entry["fast"]["wall_s"] is None
        assert all(value == 0 for value in entry["fast"]["calls"].values())


def test_fast_stage_instrumentation_never_mutates_original_namespaces():
    prepared = profile.prepare_record(invented_record())
    old_provider, old_features = dict(sp.__dict__), dict(sf.__dict__)
    result = profile.one_replay(prepared, implementation="fast", stage_trace=True)
    assert all(sp.__dict__[name] is value for name, value in old_provider.items())
    assert all(sf.__dict__[name] is value for name, value in old_features.items())
    assert sum(event["stage"] == "make_features" for event in result["stage_events"]) == 2
    assert sum(event["stage"] == "_vector" for event in result["stage_events"]) > 10


def test_full_saved_schedule_requires_exact_84_distinct_candidate_occurrences(tmp_path):
    path = tmp_path / "schedule.gz"
    inactive = invented_record(bypass=True)
    with gzip.open(path, "wb") as stream:
        for query in range(14):
            for mode in ("dense", "hybrid"):
                for repetition in (1, 2, 3):
                    stream.write(profile.canonical({**inactive, "query_id": f"invented-{query}",
                        "retrieval": mode, "repetition": repetition}) + b"\n")
    rows = profile.all_candidate_records(path)
    assert len(rows) == 84 and rows[-1]["source"]["jsonl_line"] == 84
    with gzip.open(path, "ab") as stream:
        stream.write(profile.canonical(rows[0]["record"]) + b"\n")
    with pytest.raises(ValueError, match="Duplicate"):
        profile.all_candidate_records(path)


def test_paired_freeze_requires_exact_committed_code_and_tests(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True, stderr=subprocess.DEVNULL).strip()
    git("init", "-q")
    for name in profile.PROFILE_SOURCES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# invented source freeze\n")
    git("add", ".")
    git("-c", "user.name=Invented", "-c", "user.email=invented@example.invalid", "commit", "-qm", "invented freeze")
    commit = git("rev-parse", "HEAD")
    binding = profile.verify_freeze(tmp_path, commit)
    assert binding["commit"] == binding["execution_commit"] == commit
    assert set(binding["source_sha256"]) == set(profile.PROFILE_SOURCES)
    with pytest.raises(ValueError, match="explicit full"):
        profile.verify_freeze(tmp_path, commit[:12])
    (tmp_path / profile.PROFILE_SOURCES[0]).write_text("# post-freeze mutation\n")
    with pytest.raises(ValueError, match="changed after freeze"):
        profile.verify_freeze(tmp_path, commit)


def test_paired_cli_preflight_refuses_unfrozen_source_before_reading_archive(tmp_path):
    clean_process('''
from pathlib import Path
import sys
from experiments.dependency_context import provider_profile as profile
folder = Path(sys.argv[1])
def forbidden_reader(*args, **kwargs):
    raise AssertionError("Must not read data before freeze")
profile.bind_native_study = forbidden_reader
try:
    profile.run_paired(folder / "unopened-study", folder / "new-output", freeze=None)
except ValueError as error:
    assert "explicit full" in str(error)
else:
    raise AssertionError("CLI allowed missing source freeze")
''', tmp_path)
    assert not (tmp_path / "new-output").exists()


def invented_binding(tmp_path):
    source_hashes = {}
    for name in profile.SOURCE_NAMES:
        path = tmp_path / "src/semantic_search" / (name + ".py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# invented original source\n")
        source_hashes[path.relative_to(tmp_path).as_posix()] = profile.file_sha(path)
    study = tmp_path / "invented-study"
    (study / "attempt").mkdir(parents=True)
    archive = study / "attempt/searches.jsonl.gz"
    with gzip.open(archive, "wb") as stream:
        stream.write(b"{\"invented\":true}\n")
    plan = {"binding": {"implementation_sha256": source_hashes,
        "runtime_manifest": {"model_binding": {"fingerprint": "a"*64}}}}
    (study / "plan.json").write_bytes(profile.canonical(plan))
    plan_hash, archive_hash = profile.file_sha(study / "plan.json"), profile.file_sha(archive)
    summary = {"complete": True, "plan_sha256": plan_hash, "artifact_sha256": {"searches.jsonl.gz": archive_hash}}
    audit = {"schema": "role_native_audit_v1", "status": "passed", "checks_passed": True,
        "verified_requests": 252, "verified_warmups": 6, "artifact_sha256": {"searches.jsonl.gz": archive_hash}}
    start = {"plan_sha256": plan_hash, "execution_commit": "b"*40}
    for path, value in (("independent-audit.json", audit), ("attempt/summary.json", summary), ("attempt/run-started.json", start)):
        (study / path).write_bytes(profile.canonical(value))
    return study


@pytest.mark.parametrize("mutation", [None, "archive", "source", "plan", "audit"])
def test_original_binding_hashes_each_input_and_reports_unpublished_test_mode(tmp_path, mutation):
    study = invented_binding(tmp_path)
    if mutation == "archive":
        with (study / "attempt/searches.jsonl.gz").open("ab") as stream:
            stream.write(b"altered")
    elif mutation == "source":
        (tmp_path / "src/semantic_search/semantic_features.py").write_text("# altered\n")
    elif mutation == "plan":
        with (study / "plan.json").open("ab") as stream:
            stream.write(b" ")
    elif mutation == "audit":
        path = study / "independent-audit.json"
        value = json.loads(path.read_bytes())
        value["verified_requests"] = 251
        path.write_bytes(profile.canonical(value))
    if mutation:
        with pytest.raises(ValueError):
            profile.bind_native_study(study, root=tmp_path, verify_git=False)
    else:
        binding = profile.bind_native_study(study, root=tmp_path, verify_git=False)
        assert binding["publication_commit"] is None and binding["published_blobs_verified"] is False
        assert binding["archive_sha256"] == profile.file_sha(study / "attempt/searches.jsonl.gz")
