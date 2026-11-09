"""Invented snapshots only: no observed guard decision, graph or model work."""
from copy import deepcopy
import ast
import gzip
import json
from pathlib import Path
import subprocess

import pytest

from experiments.dependency_context import study_inputs as inputs


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def saved(response, **fields):
    payload = inputs.compact_payload(response)
    return {**fields, "response": deepcopy(response), "compact_payload": payload,
            "compact_sha256": inputs.sha(payload.encode())}


def toy(tmp_path, *, dataset="toy", text="def work():\n    return 1\n"):
    corpus = tmp_path / "corpora"
    directory = corpus / inputs.CORPUS_FOLDERS.get(dataset, dataset)
    directory.mkdir(parents=True)
    (directory / "module.py").write_bytes(text.encode())
    end = len(inputs.physical_lines(text))
    inventory = [{"id": f"module.py:work:1-{end}", "path": "module.py", "symbol": "work",
                  "start_line": 1, "end_line": end, "text": text, "content_hash": inputs.sha(text.encode())}]
    keys = {inventory[0]["id"]: ["module.py", "work", 1, end]}
    spec = {"source_sha256": {"module.py": inputs.sha(text.encode())}, "inventory": inventory,
            "definition_keys": keys, "graph_edges": []}
    bundles = inputs.build_bundles({dataset: spec}, corpus)
    bundle = next(iter(bundles.values()))
    value = {**{name: inventory[0][name] for name in ("id", "path", "symbol", "start_line", "end_line")},
             "window_end_line": end, "source": text, "content_hash": inventory[0]["content_hash"],
             "source_hash": inventory[0]["content_hash"], "truncated": False, "retrieval_score": 0.25,
             "h2o_score": None, "qwen_score": None, "origin": "retrieval", "related_to": None}
    response = {"query": "invented work", "scope": {"include": ["module.py"], "exclude": []},
                "match_status": "candidates", "results": [value], "total_source_chars": len(text),
                "budget_exhausted": False,
                "token_usage": {"encoding": "cl100k_base", "tokens": 85, "budget": 4096, "truncated": False}}
    parent = saved(response)
    ref = inputs.record_ref("saved.jsonl.gz", 1, parent, inputs.sha(b"invented line\n"))
    key = ["invented", dataset, "toy_query", "hybrid"]
    record = {"id": inputs.opaque_id("historical_role", key), "dataset": dataset, "query_id": "toy_query",
              "mode": "hybrid", "repetition": 1, "quality": True, "reference": deepcopy(response),
              "proposal": deepcopy(response), "legacy": deepcopy(response), "definition_keys": keys,
              "raw_rows": [deepcopy(value)], "precoalesce_rows": [deepcopy(value)], "limits": deepcopy(inputs.LIMITS),
              "scoring": {"kind": "present", "multi_file": False, "qualified": True,
                          "groups": [[{"path": "module.py", "symbol": "work"}]]},
              "original_identity": {"origin": "historical_role", "key": key,
                                    "case_id": "case_" + inputs.sha(inputs.canonical(key)), "retrieval": "hybrid"},
              "parent_refs": {name: deepcopy(ref) for name in ("legacy", "coverage_control", "prior_role")},
              "source_bundle_id": bundle["id"]}
    counts = {"decisions": 1, "quality_occurrences": 1, "distinct_quality_query_units": 1,
              "historical_decisions": 1, "native_decisions": 0, "quality_native_decisions": 0,
              "paired_controls": 2, "quality_control_occurrences": 2, "source_datasets": 1, "source_files": 1}
    return corpus, spec, bundles, record, counts


def test_bundle_is_self_contained_and_corpus_code_is_never_executed(tmp_path):
    text = "def work():\n    import module_that_does_not_exist\n    raise RuntimeError('must never run')\n"
    corpus, spec, bundles, _, _ = toy(tmp_path, text=text)
    bundle = next(iter(bundles.values()))
    assert bundle["sources"]["module.py"] == text
    assert bundle["inventory"] == spec["inventory"]
    (corpus / "toy" / "module.py").unlink()
    assert inputs.validate_bundle(bundle) == bundle


@pytest.mark.parametrize("text,lines", [("a\r\nb\rc\n", ["a\r\n", "b\r", "c\n"]),
                                       ("é\u2028x\v\f", ["é\u2028x\v\f"]), ("", []),
                                       ("\n\n", ["\n", "\n"]), ("a\r", ["a\r"])])
def test_physical_lines_only_cr_and_lf(text, lines):
    assert inputs.physical_lines(text) == lines


@pytest.mark.parametrize("text", ["def work():\r\n    return 'é'\r\n", "def work():\r    return 1\r"])
def test_source_bundles_bind_crlf_cr_and_unicode_literal_bytes(tmp_path, text):
    _, _, bundles, _, _ = toy(tmp_path, text=text)
    inputs.validate_bundle(next(iter(bundles.values())))


def test_changed_whole_module_rejected_even_when_window_is_unchanged(tmp_path):
    corpus, spec, _, _, _ = toy(tmp_path)
    with (corpus / "toy" / "module.py").open("ab") as stream:
        stream.write(b"# modified outside old window\n")
    with pytest.raises(ValueError, match="Pinned original module bytes changed"):
        inputs.build_bundles({"toy": spec}, corpus)


@pytest.mark.parametrize("change", ["raw", "text", "window", "key", "alias", "path"])
def test_self_contained_source_tampering_rejected(tmp_path, change):
    _, _, bundles, _, _ = toy(tmp_path)
    bundle = deepcopy(next(iter(bundles.values())))
    row = bundle["inventory"][0]
    if change == "raw":
        bundle["files"]["module.py"]["raw_base64"] = "eA=="
    elif change == "text":
        bundle["sources"]["module.py"] += "# changed"
    elif change == "window":
        row["text"] = "def other():\n"
    elif change == "key":
        bundle["definition_keys"][row["id"]][0] = "other.py"
    elif change == "alias":
        bundle["id"] = "sources_" + "0" * 64
    else:
        bundle["files"]["../escape.py"] = bundle["files"].pop("module.py")
        bundle["sources"]["../escape.py"] = bundle["sources"].pop("module.py")
    with pytest.raises(ValueError):
        inputs.validate_bundle(bundle)


def test_original_git_blob_is_authority_not_self_consistent_working_hash(tmp_path):
    root = tmp_path / "git"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "parent.json").write_text("original\n")
    subprocess.run(["git", "-C", str(root), "add", "parent.json"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Invented", "-c", "user.email=invented@example.invalid",
                    "commit", "-qm", "invented parent"], check=True)
    commit = inputs.git(root, "rev-parse", "HEAD").decode().strip()
    inputs.verify_blobs(root, {"parent.json": inputs.file_sha(root / "parent.json")}, commit=commit)
    (root / "parent.json").write_text("self-rebound\n")
    with pytest.raises(ValueError, match="working/Git bytes differ"):
        inputs.verify_blobs(root, {"parent.json": inputs.file_sha(root / "parent.json")}, commit=commit)


@pytest.mark.parametrize("name", ["../escape", "/absolute", 9])
def test_invalid_parent_path_rejected_before_git(tmp_path, name):
    with pytest.raises(ValueError, match="path/hash"):
        inputs.verify_blobs(tmp_path, {name: "0" * 64})


def test_selector_operands_are_label_free_and_include_certificate_inputs(tmp_path):
    _, _, bundles, record, counts = toy(tmp_path)
    values = inputs.snapshot_values([record], bundles, expected_counts=counts)
    operand = values["operands"][0]
    assert set(operand) == set(inputs.OPERAND_FIELDS)
    assert not ({"scoring", "groups", "kind", "query_id", "dataset", "quality", "legacy", "parent_refs"} & set(operand))
    assert operand["raw_rows"] == record["raw_rows"]
    assert operand["precoalesce_rows"] == record["precoalesce_rows"]
    assert operand["limits"] == inputs.LIMITS
    assert values["manifest"]["records"][0]["proposal_sha256"] == inputs.sha(inputs.canonical(record["proposal"]))


@pytest.mark.parametrize("change", ["duplicate", "alias", "identity", "missing_control", "control_hash", "score",
                                   "source", "provenance", "limit", "budget", "typed", "quality", "raw"])
def test_record_pairing_provenance_and_manifest_fail_closed(tmp_path, change):
    _, _, bundles, record, counts = toy(tmp_path)
    records = [record]
    if change == "duplicate":
        records.append(deepcopy(record))
    elif change == "alias":
        record["id"] = "record_" + "0" * 64
    elif change == "identity":
        record["query_id"] = "changed_label"
    elif change == "missing_control":
        del record["parent_refs"]["coverage_control"]
    elif change == "control_hash":
        record["parent_refs"]["prior_role"]["compact_sha256"] = "0" * 64
    elif change == "score":
        record["proposal"]["results"][0]["retrieval_score"] = float("nan")
    elif change == "source":
        record["proposal"]["results"][0]["source"] = "changed\n"
    elif change == "provenance":
        record["proposal"]["results"][0]["source_origin"] = "overlay"
    elif change == "limit":
        record["limits"]["max_results"] = 9
    elif change == "budget":
        record["proposal"]["token_usage"]["tokens"] = 4097
    elif change == "typed":
        record["repetition"] = True
    elif change == "quality":
        record["quality"] = False
    else:
        record["raw_rows"][0]["source_hash"] = "0" * 64
    with pytest.raises(ValueError):
        inputs.snapshot_values(records, bundles, expected_counts=counts)


def historical_fixture(root, record):
    key = record["original_identity"]["key"]
    identity = {"case_id": record["original_identity"]["case_id"], "key": key, "subset": key[0],
                "dataset": record["dataset"], "query_id": record["query_id"], "condition": record["mode"],
                "kind": "present", "multi_file": False, "qualified": True}
    originals = [saved(record[field], **{**identity, "selector": arm})
                 for arm, field in (("legacy", "legacy"), ("coverage_control", "reference"))]
    control_path = root / "saved.jsonl.gz"
    inputs.write_rows(control_path, originals)
    refs = {row["selector"]: inputs.record_ref("saved.jsonl.gz", number, row, raw_sha)
            for number, row, raw_sha in inputs.rows(control_path)}
    case = {**identity, "query": record["reference"]["query"], "retrieval": "hybrid", "groups": record["scoring"]["groups"],
            "reference_response": record["reference"], "legacy_control": record["legacy"],
            "definition_keys": record["definition_keys"], "raw_rows": record["raw_rows"],
            "precoalesce_rows": record["precoalesce_rows"], "limits": record["limits"], "control_references": refs}
    dump(root / inputs.HISTORICAL, {"cases": [case]})
    role_path = root / inputs.ROLE / "replay/outcomes.jsonl.gz"
    role_path.parent.mkdir(parents=True)
    inputs.write_rows(role_path, [saved(record["proposal"], **identity)])
    return case


def test_historical_parser_resolves_original_controls_and_raw_line_hashes(tmp_path):
    _, _, bundles, record, _ = toy(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    historical_fixture(root, record)
    actual = inputs.historical_records(root, bundles, expected_count=1)[0]
    assert actual["reference"] == record["reference"]
    assert actual["proposal"] == record["proposal"]
    assert actual["parent_refs"]["prior_role"]["raw_line_sha256"]
    assert actual["parent_refs"]["coverage_control"]["line_number"] == 2


def test_historical_parser_accepts_original_native_arm_schema_without_corpus_exception(tmp_path):
    _, _, bundles, record, _ = toy(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    case = historical_fixture(root, record)
    path = root / "saved.jsonl.gz"
    native = []
    for _, row, _ in inputs.rows(path):
        arm = row.pop("selector")
        del row["dataset"]
        row.update(arm=arm, condition=arm, retrieval=case["retrieval"], repetition=1)
        native.append(row)
    path.unlink()
    inputs.write_rows(path, native)
    case["control_references"] = {row["arm"]: inputs.record_ref("saved.jsonl.gz", number, row, digest)
                                  for number, row, digest in inputs.rows(path)}
    dump(root / inputs.HISTORICAL, {"cases": [case]})
    assert inputs.historical_records(root, bundles, expected_count=1)[0]["reference"] == record["reference"]


@pytest.mark.parametrize("change", ["missing_proposal", "duplicate_proposal", "wrong_identity", "wrong_reference"])
def test_historical_parser_rejects_bad_parent_pairs(tmp_path, change):
    _, _, bundles, record, _ = toy(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    case = historical_fixture(root, record)
    role_path = root / inputs.ROLE / "replay/outcomes.jsonl.gz"
    row = list(inputs.rows(role_path))[0][1]
    role_path.unlink()
    values = [row]
    if change == "missing_proposal":
        values = []
    elif change == "duplicate_proposal":
        values = [row, deepcopy(row)]
    elif change == "wrong_identity":
        row["query_id"] = "other"
    else:
        case["control_references"]["legacy"]["record_sha256"] = "0" * 64
        dump(root / inputs.HISTORICAL, {"cases": [case]})
    inputs.write_rows(role_path, values)
    with pytest.raises(ValueError):
        inputs.historical_records(root, bundles, expected_count=1)


def native_fixture(root, record):
    dump(root / inputs.NATIVE / "plan.json", {"binding": {"dataset_path": "benchmarks/attrs_complementary_holdout_queries.json"}})
    dump(root / "benchmarks/attrs_complementary_holdout_queries.json", {"queries": [{"id": "attrs_toy", "query": "invented work",
         "kind": "present", "multi_file": False, "target_groups": record["scoring"]["groups"]}]})
    values = []
    for mode in ("dense", "hybrid"):
        for repeat in (1, 2, 3):
            for arm in ("legacy", "coverage_control", "candidate"):
                response = deepcopy(record["reference"])
                response["timings"] = {"per_arm": arm}
                capture = None
                if arm == "candidate":
                    reference = deepcopy(record["reference"])
                    reference["timings"] = {"per_arm": "inside_candidate"}
                    capture = {"reference_response": reference, "response": response,
                               "definition_keys": record["definition_keys"], "limits": record["limits"],
                               "ranked_candidates": record["raw_rows"], "precoalesce_rows": record["precoalesce_rows"]}
                values.append(saved(response, query_id="attrs_toy", retrieval=mode, repetition=repeat, arm=arm,
                                    kind="present", multi_file=False, candidate_capture=capture,
                                    reference_response=None if capture is None else capture["reference_response"]))
    path = root / inputs.NATIVE / "attempt/searches.jsonl.gz"
    path.parent.mkdir(parents=True)
    inputs.write_rows(path, values)
    return path, values


def test_native_parser_pairs_every_repeat_and_quality_is_first_repeat(tmp_path):
    _, _, bundles, record, _ = toy(tmp_path, dataset="attrs")
    root = tmp_path / "root"
    root.mkdir()
    native_fixture(root, record)
    actual = inputs.native_records(root, bundles, expected_queries=1)
    assert len(actual) == 6
    assert sum(row["quality"] for row in actual) == 2
    assert {row["mode"] for row in actual} == {"dense", "hybrid"}
    assert all(row["parent_refs"]["prior_role"]["record_sha256"] for row in actual)


@pytest.mark.parametrize("change", ["missing", "duplicate", "repeat_type", "reference", "source", "query", "compact_order"])
def test_native_parser_rejects_missing_duplicate_or_changed_exact_pairs(tmp_path, change):
    _, _, bundles, record, _ = toy(tmp_path, dataset="attrs")
    root = tmp_path / "root"
    root.mkdir()
    path, values = native_fixture(root, record)
    if change == "missing":
        values.pop()
    elif change == "duplicate":
        values[-1] = deepcopy(values[0])
    elif change == "repeat_type":
        values[0]["repetition"] = True
    elif change == "reference":
        values[2]["candidate_capture"]["reference_response"]["results"][0]["retrieval_score"] = 0.5
    elif change == "source":
        values[2]["candidate_capture"]["response"]["results"][0]["source"] = "changed"
    elif change == "query":
        values[0] = saved({**values[0]["response"], "query": "other"},
                          **{key: value for key, value in values[0].items() if key not in
                             ("response", "compact_payload", "compact_sha256")})
    else:
        values[0]["response"]["scope"] = {"exclude": [], "include": ["module.py"]}
    path.unlink()
    inputs.write_rows(path, values)
    with pytest.raises(ValueError):
        inputs.native_records(root, bundles, expected_queries=1)


def configured_prepare(tmp_path, monkeypatch):
    corpus, spec, bundles, record, counts = toy(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    (root / "source.py").write_text("# invented prospective source\n")
    source = {"source.py": inputs.file_sha(root / "source.py")}
    parents = {"publication_commit": inputs.PARENT_COMMIT, "sha256": {"saved.jsonl.gz": "0" * 64}, "bound_blobs": 1}
    monkeypatch.setattr(inputs, "COUNTS", counts)
    monkeypatch.setattr(inputs, "source_hashes", lambda _: deepcopy(source))
    monkeypatch.setattr(inputs, "parent_binding", lambda _: deepcopy(parents))
    monkeypatch.setattr(inputs, "source_specs", lambda _: {"toy": deepcopy(spec)})
    monkeypatch.setattr(inputs, "historical_records", lambda *_: [deepcopy(record)])
    monkeypatch.setattr(inputs, "native_records", lambda *_: [])
    monkeypatch.setattr(inputs, "verify_blobs", lambda *_: None)
    return root, corpus, bundles, record


def test_prepare_load_roundtrip_is_exclusive_and_no_outcome_work(tmp_path, monkeypatch):
    root, corpus, _, record = configured_prepare(tmp_path, monkeypatch)
    output = tmp_path / "prepared"
    plan = inputs.prepare(root, output, corpus_root=corpus)
    assert plan["calls"] == inputs.ZERO_CALLS
    assert all(value == 0 for value in plan["calls"].values())
    assert plan["native_warm_latency_measured"] is False
    assert inputs.load_prepared(output, root=root)["records"] == [record]
    with pytest.raises(FileExistsError):
        inputs.prepare(root, output, corpus_root=corpus)
    for name, digest in plan["artifact_sha256"].items():
        assert inputs.file_sha(output / name) == digest


def test_load_does_not_read_external_corpus(tmp_path, monkeypatch):
    root, corpus, _, record = configured_prepare(tmp_path, monkeypatch)
    output = tmp_path / "prepared"
    inputs.prepare(root, output, corpus_root=corpus)
    (corpus / "toy/module.py").unlink()
    assert inputs.load_prepared(output, root=root)["records"] == [record]


@pytest.mark.parametrize("change", ["artifact", "source", "parent", "extra", "alias_bundle", "self_rebound_record"])
def test_snapshot_load_rejects_tampering_and_self_rebinding(tmp_path, monkeypatch, change):
    root, corpus, _, record = configured_prepare(tmp_path, monkeypatch)
    output = tmp_path / "prepared"
    inputs.prepare(root, output, corpus_root=corpus)
    plan = inputs.read(output / "frozen-plan.json")
    if change == "artifact":
        (output / "operands.jsonl.gz").write_bytes(b"changed")
    elif change == "source":
        monkeypatch.setattr(inputs, "source_hashes", lambda _: {"source.py": "1" * 64})
    elif change == "parent":
        monkeypatch.setattr(inputs, "parent_binding", lambda _: {"changed": True})
    elif change == "extra":
        (output / "unbound.json").write_text("{}")
    elif change == "alias_bundle":
        bundle = list(inputs.rows(output / "source-bundles.jsonl.gz"))[0][1]
        bundle["id"] = "sources_" + "0" * 64
        (output / "source-bundles.jsonl.gz").unlink()
        inputs.write_rows(output / "source-bundles.jsonl.gz", [bundle])
        plan["artifact_sha256"]["source-bundles.jsonl.gz"] = inputs.file_sha(output / "source-bundles.jsonl.gz")
    else:
        record["scoring"]["groups"] = []
        (output / "records.jsonl.gz").unlink()
        inputs.write_rows(output / "records.jsonl.gz", [record])
        plan["artifact_sha256"]["records.jsonl.gz"] = inputs.file_sha(output / "records.jsonl.gz")
    (output / "frozen-plan.json").write_text(json.dumps(plan))
    with pytest.raises(ValueError):
        inputs.load_prepared(output, root=root)


def test_preparation_failure_is_durable_and_cannot_be_overwritten(tmp_path, monkeypatch):
    root, corpus, _, _ = configured_prepare(tmp_path, monkeypatch)
    error = RuntimeError("invented parent failure")

    def fail(_):
        raise error

    monkeypatch.setattr(inputs, "parent_binding", fail)
    output = tmp_path / "prepared"
    with pytest.raises(RuntimeError) as raised:
        inputs.prepare(root, output, corpus_root=corpus)
    assert raised.value is error
    assert inputs.read(output / "prepare-failure.json")["new_outcomes_computed"] == 0
    assert not (output / "frozen-plan.json").exists()
    with pytest.raises(FileExistsError):
        inputs.prepare(root, output, corpus_root=corpus)


def test_gzip_ledger_preserves_nested_payload_order_and_final_trailer_hash(tmp_path):
    _, _, _, record, _ = toy(tmp_path)
    path = tmp_path / "ledger.jsonl.gz"
    value = saved(record["reference"])
    digest = inputs.write_rows(path, [value])
    assert digest == inputs.file_sha(path)
    assert json.loads(gzip.decompress(path.read_bytes())) == value
    assert inputs.saved_payload(list(inputs.rows(path))[0][1]) == value["compact_payload"]
    with pytest.raises(FileExistsError):
        inputs.write_rows(path, [value])


def test_gzip_finalization_failure_never_masks_original_iteration_exception(tmp_path, monkeypatch):
    error, closing = RuntimeError("invented original"), OSError("invented gzip close")
    opened = []
    real_open, real_gzip = Path.open, inputs.gzip.GzipFile

    def observe_open(path, *args, **kwargs):
        result = real_open(path, *args, **kwargs)
        opened.append(result)
        return result

    class BrokenClose:
        def __init__(self, **kwargs):
            self.stream = real_gzip(**kwargs)

        def write(self, data):
            return self.stream.write(data)

        def flush(self):
            return self.stream.flush()

        def close(self):
            self.stream.close()
            raise closing

    def values():
        yield {"invented": True}
        raise error

    monkeypatch.setattr(Path, "open", observe_open)
    monkeypatch.setattr(inputs.gzip, "GzipFile", BrokenClose)
    with pytest.raises(RuntimeError) as raised:
        inputs.write_rows(tmp_path / "failed.jsonl.gz", values())
    assert raised.value is error
    assert raised.value.__cause__ is closing
    assert opened and all(stream.closed for stream in opened)


@pytest.mark.parametrize("failure", ["constructor", "sync", "finalization"])
def test_gzip_raw_file_closed_on_initialization_and_finalization_errors(tmp_path, monkeypatch, failure):
    error, opened = OSError("invented failure"), []
    real_open = Path.open

    def observe_open(path, *args, **kwargs):
        result = real_open(path, *args, **kwargs)
        opened.append(result)
        return result

    monkeypatch.setattr(Path, "open", observe_open)
    if failure == "constructor":
        def fail(**_):
            raise error
        monkeypatch.setattr(inputs.gzip, "GzipFile", fail)
    elif failure == "sync":
        def fail(_):
            raise error
        monkeypatch.setattr(inputs.os, "fsync", fail)
    else:
        real_gzip = inputs.gzip.GzipFile

        class FailedClose(real_gzip):
            def close(self):
                super().close()
                raise error

        monkeypatch.setattr(inputs.gzip, "GzipFile", FailedClose)
    with pytest.raises(OSError) as raised:
        inputs.write_rows(tmp_path / "failed.jsonl.gz", [])
    assert raised.value is error
    assert opened and all(stream.closed for stream in opened)


def test_input_module_imports_only_stdlib_and_no_guard_feature_or_producer(tmp_path):
    tree = ast.parse(Path(inputs.__file__).read_text())
    modules = {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    modules.update(alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
    assert modules <= {"__future__", "base64", "copy", "gzip", "hashlib", "io", "json", "math", "os",
                       "pathlib", "re", "subprocess", "tokenize"}
    assert not {"semantic_search", "numpy", "torch", "mlx", "research_role_context", "annotated_dependencies", "guard"} & modules
