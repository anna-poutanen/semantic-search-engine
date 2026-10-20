"""Audit receipts independently of the benchmark, retriever and native models."""
from copy import deepcopy
import math
from pathlib import Path
import struct
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import report_multifile as audit


class Encoder:
    def encode(self, text, **kwargs):
        # Deliberately fake tokens for these source/counter contract tests.
        return range(math.ceil(len(text.encode()) / 4))


def fixture(count=9):
    inventory, sources, raw = {}, {}, []
    for number in range(count):
        path = f"src/file{number}.py"
        text = f"def function{number}():\r\n    return {number}\r"
        identifier = f"{path}:function{number}:1-2"
        digest = audit.sha(text.encode())
        inventory[identifier] = dict(id=identifier, path=path, symbol=f"function{number}",
            start_line=1, end_line=2, text=text, content_hash=digest)
        sources[path] = audit.physical_lines(text)
        raw.append(dict(id=identifier, path=path, symbol=f"function{number}",
            start_line=1, end_line=2, window_end_line=2, source=text, source_hash=digest,
            content_hash=digest, truncated=False, origin="retrieval", related_to=None,
            retrieval_score=1. - number / 100, h2o_score=None))
    stages = {"retrieved": deepcopy(raw), "ranked": deepcopy(raw),
              "deduplicated": deepcopy(raw), "eligible": deepcopy(raw[:8]),
              "anchors": deepcopy(raw[:5]), "expanded": deepcopy(raw[:8]),
              "character_packed": deepcopy(raw[:8]), "token_packed": deepcopy(raw[:8])}
    query = dict(id="test", kind="present", query="Find the implementation",
                 targets=[dict(path=raw[0]["path"], symbol="an enclosing label is fine",
                               evidence_lines=[1, 2])])
    response = dict(query=query["query"], retrieval="dense", rerank=False,
        timings=dict(total_s=.09, query_embedding_s=.05, retrieval_s=.01, h2o_s=0, qwen_s=0),
        match_evidence=dict(threshold=None), match_status="candidates", source_budget=12000,
        results=deepcopy(raw[:8]), token_usage=dict(encoding="cl100k_base", budget=4096,
        tokens=0, truncated=False), budget_exhausted=False,
        total_source_chars=sum(len(r["source"]) for r in raw[:8]),
        candidate_windows=count, retrieval_candidate_windows=count, available_symbols=count)
    for _ in range(12):
        tokens = len(Encoder().encode(audit.compact_text(response)))
        if tokens == response["token_usage"]["tokens"]:
            break
        response["token_usage"]["tokens"] = tokens
    vector = [1., 0.]
    row = dict(query_id=query["id"], kind="present", repetition=1, retrieval="dense",
        multi_file=False, query_embedding_calls=1, query_vector=vector,
        query_vector_sha256=audit.sha(struct.pack("<ff", *vector)), elapsed_backend_s=.1,
        response=response, stages=stages, candidate_ranks=[1], delivered_ranks=[1],
        group_diagnostics=audit.group_diagnostics(stages, audit.groups(query)))
    return row, query, dict(embedding=dict(dimensions=2)), inventory, sources


def check(bundle):
    row, query, binding, inventory, sources = bundle
    return audit.audit_row(row, query, binding, inventory, sources, Encoder())


def test_literal_physical_sources_and_enclosing_symbol_credit_pass():
    bundle = fixture()
    assert check(bundle)[0]["outcome"] == "delivered"
    assert len(bundle[-1]["src/file0.py"]) == 2


@pytest.mark.parametrize("change,match", [
    (lambda row: row["response"]["results"][0].update(source=row["response"]["results"][0]["source"].replace("return", "broken")), "Delivered excerpts"),
    (lambda row: row["stages"]["retrieved"][0].update(source_hash="0" * 64), "source/hash"),
    (lambda row: row.update(query_vector_sha256="0" * 64), "query vector/hash"),
    (lambda row: row.update(query_embedding_calls=2), "one fresh"),
    (lambda row: row.update(delivered_ranks=[None]), "evidence ranks"),
    (lambda row: row.update(multi_file=True), "denominator"),
    (lambda row: row["response"]["token_usage"].update(tokens=1), "Exact compact"),
    (lambda row: row["group_diagnostics"][0].update(outcome="upstream"), "stage attribution"),
])
def test_tampered_receipt_is_rejected(change, match):
    bundle = fixture()
    change(bundle[0])
    with pytest.raises(ValueError, match=match):
        check(bundle)


def test_valid_rehashed_source_fabrication_is_still_rejected():
    bundle = fixture()
    window = bundle[0]["stages"]["retrieved"][0]
    window["source"] = "wrong source\r\nwrong source\r"
    window["source_hash"] = audit.sha(window["source"].encode())
    with pytest.raises(ValueError, match="pinned physical lines"):
        check(bundle)


def test_result_cap_loss_and_physical_or_evidence_are_independently_scored():
    row, query, binding, inventory, sources = fixture()
    query.pop("targets")
    query["target_groups"] = [[dict(path="absent.py", evidence_lines=[1, 2]),
                               dict(path="src/file0.py", evidence_lines=[1, 2])],
                              [dict(path="src/file8.py", evidence_lines=[1, 2])]]
    row["multi_file"] = True
    row["candidate_ranks"], row["delivered_ranks"] = [1, 9], [1, None]
    row["group_diagnostics"] = audit.group_diagnostics(row["stages"], audit.groups(query))
    assert check((row, query, binding, inventory, sources))[1]["outcome"] == "result_cap"


def test_dependency_recovery_then_token_loss_uses_latest_covering_stage():
    stages = {name: [] for name in audit.STAGES}
    target = dict(path="src/helper.py", start_line=4, end_line=8)
    stages["expanded"] = [target]
    stages["character_packed"] = [target]
    stages["token_packed"] = [dict(target, end_line=5)]
    gold = [[dict(path=target["path"], evidence_lines=[6, 8])]]
    result = audit.group_diagnostics(stages, gold)[0]
    assert result["outcome"] == "token_clipping"
    assert result["ranks"]["retrieved"] is None


def metric_rows(*, single_loss=False, overhead=1.25):
    rows = []
    for repetition in range(1, 4):
        for mode in audit.CONDITIONS:
            for qid, kind, multi, delivered in (
                ("compound", "present", True, [1, None] if mode == "dense" else [1, 2]),
                ("single", "present", False, [None] if single_loss and mode == "multifile" else [1]),
                ("negative", "absent", False, []),
            ):
                rows.append(dict(query_id=qid, kind=kind, multi_file=multi,
                    delivered_ranks=delivered, candidate_ranks=[1, 2] if multi else [1] if kind == "present" else [],
                    repetition=repetition, retrieval=mode,
                    elapsed_backend_s=overhead if mode == "multifile" else 1.,
                    response=dict(results=[{}], token_usage=dict(tokens=123)),
                    group_diagnostics=[dict(outcome="delivered" if rank is not None else "result_cap")
                                       for rank in delivered]))
    return rows


def test_quality_counts_unique_queries_while_latency_uses_all_repetitions():
    rows = metric_rows()
    metrics = audit.summarize(rows)
    assert metrics["dense"]["present"] == 2
    assert metrics["dense"]["multi_file"] == 1
    assert metrics["dense"]["required_groups"] == 3
    assert metrics["dense"]["warm_latency_samples"] == 9
    assert metrics["dense"]["absent_with_candidates"] == 1
    assert metrics["paired"]["complete_gains"] == ["compound"]
    assert audit.gate_result(rows, metrics)["passed"] is True


def test_gate_rejects_individual_single_loss_and_excess_latency():
    rows = metric_rows(single_loss=True)
    gate = audit.gate_result(rows, audit.summarize(rows))
    assert gate["net_multi_file_complete_gain"] == 1
    assert gate["complete_single_file_losses"] == ["single"]
    assert gate["all_group_regressions"] == ["single"]
    assert gate["passed"] is False
    rows = metric_rows(overhead=1.250001)
    assert audit.gate_result(rows, audit.summarize(rows))["latency_passed"] is False


def test_rotated_schedule_has_exact_query_condition_repeat_inventory():
    queries = [dict(id="one"), dict(id="two")]
    result = audit.schedule(queries)
    assert len(result) == len(set(result)) == 18
    assert result[:6] == [("one", "dense", 1), ("one", "multifile", 1), ("one", "hybrid", 1),
                          ("two", "multifile", 1), ("two", "hybrid", 1), ("two", "dense", 1)]
    assert result[6:9] == [("one", "multifile", 2), ("one", "hybrid", 2), ("one", "dense", 2)]


def test_offline_tokenizer_shim_accepts_positional_expected_hash(tmp_path, monkeypatch):
    import hashlib
    import tiktoken
    import tiktoken.load
    raw = b"verified existing rank bytes"
    monkeypatch.setattr(audit, "TOKENIZER_SHA256", audit.sha(raw))
    (tmp_path / hashlib.sha1(audit.TOKENIZER_URL.encode()).hexdigest()).write_bytes(raw)
    def get_encoding(name):
        assert name == "cl100k_base"
        assert tiktoken.load.read_file_cached(audit.TOKENIZER_URL, audit.TOKENIZER_SHA256) == raw
        with pytest.raises(ValueError, match="Unexpected tokenizer download"):
            tiktoken.load.read_file_cached("https://unexpected.invalid/encoding", None)
        return Encoder()
    monkeypatch.setattr(tiktoken, "get_encoding", get_encoding)
    assert isinstance(audit.load_encoder(tmp_path), Encoder)


def test_multifile_pool_may_be_shorter_after_internal_symbol_merge():
    bundle = fixture()
    row = bundle[0]
    row["stages"]["retrieved"].pop()
    row["stages"]["ranked"].pop()
    row["stages"]["deduplicated"].pop()
    row["response"].update(candidate_windows=8, retrieval_candidate_windows=8, available_symbols=8)
    with pytest.raises(ValueError, match="Dense/hybrid candidate depth"):
        check(bundle)
    row["retrieval"] = row["response"]["retrieval"] = "multifile"
    assert check(bundle)[0]["outcome"] == "delivered"


def test_scope_compares_normalized_sorted_globs_and_truthful_omitted_count():
    labels = dict(source_root="src", source_exclusions=["z", "a", "z", "b", "c", "d"])
    scope = dict(source_root="src", membership="saved implementations; Git-tracked files only in Git projects",
                 source_exclusions=["a", "b", "c", "d"], exclusions_omitted=1)
    audit.checked_scope(scope, labels)
    scope["exclusions_omitted"] = 0
    with pytest.raises(ValueError, match="source scope"):
        audit.checked_scope(scope, labels)


@pytest.fixture
def frozen_project(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], stderr=subprocess.PIPE)
    git("init", "-q")
    path = tmp_path / "src/implementation.py"
    path.parent.mkdir()
    original = b"raise RuntimeError('these bytes must never execute')\n"
    path.write_bytes(original)
    git("add", "src/implementation.py")
    git("-c", "user.name=Auditor Test", "-c", "user.email=auditor@example.invalid",
        "commit", "-qm", "Freeze fixture implementation")
    return tmp_path, path, original, git("rev-parse", "HEAD").decode().strip()


def test_frozen_resolver_uses_verified_commit_when_live_source_changes(frozen_project):
    root, path, original, commit = frozen_project
    current, proof = audit.frozen_source(root, "src/implementation.py", audit.sha(original), commit)
    assert current == original
    assert proof["bytes_read_from"] == "matching_working_tree"
    path.write_bytes(b"changed live wording\n")
    frozen, proof = audit.frozen_source(root, "src/implementation.py", audit.sha(original), commit)
    assert frozen == original
    assert proof["bytes_read_from"] == "verified_git_blob"
    assert proof["working_tree_sha256"] == audit.sha(path.read_bytes())
    assert proof["committed_blob_sha256_verified"] is True
    assert proof["working_tree_matches_frozen"] is False
    path.unlink()
    frozen, proof = audit.frozen_source(root, "src/implementation.py", audit.sha(original), commit)
    assert frozen == original
    assert proof["working_tree_sha256"] is None


def test_frozen_resolver_rejects_uncommitted_expected_bytes_even_if_live_matches(frozen_project):
    root, path, original, commit = frozen_project
    uncommitted = b"uncommitted replacement\n"
    path.write_bytes(uncommitted)
    with pytest.raises(ValueError, match="Committed implementation differs"):
        audit.frozen_source(root, "src/implementation.py", audit.sha(uncommitted), commit)
    with pytest.raises(ValueError, match="Committed implementation differs"):
        audit.frozen_source(root, "src/implementation.py", "0" * 64, commit)


@pytest.mark.parametrize("name,commit", [
    ("../outside.py", "a" * 40), ("/absolute.py", "a" * 40),
    ("src/../outside.py", "a" * 40), ("src//file.py", "a" * 40),
    ("src/file.py:other", "a" * 40), ("src/file.py", "HEAD"),
    ("src/file.py", "a" * 40 + ":other"),
])
def test_frozen_resolver_rejects_unsafe_paths_and_nonliteral_commits_before_git(tmp_path, monkeypatch, name, commit):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid source identity must fail before invoking Git")
    monkeypatch.setattr(audit.subprocess, "check_output", forbidden)
    with pytest.raises(ValueError):
        audit.frozen_source(tmp_path, name, "b" * 64, commit)
