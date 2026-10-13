"""Source-only gold and delivered-prefix corruption tests; no models or labels."""
import copy
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("context_packing_report", ROOT / "scripts/report_context_packing.py")
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)
SOURCE = '\ufeffexport function outer() {\r\n  return "😀\u2028literal";\r\n}\r\n'


class Counter:
    def encode(self, text, **kwargs):
        return range(len(text))


def fixture():
    digest = report.sha(SOURCE.encode())
    full = {"id": "window", "path": "src/a.ts", "symbol": "outer", "start_line": 1,
            "end_line": 3, "source_hash": digest}
    row = {**full, "source": SOURCE, "window_end_line": 3, "content_hash": digest,
           "retrieval_score": .7, "h2o_score": None, "origin": "retrieval", "related_to": None, "truncated": False}
    response = {"query": "generic", "scope": {"source_root": "src"}, "match_status": "candidates",
                "results": [row], "budget_exhausted": False, "total_source_chars": len(SOURCE),
                "token_usage": {"encoding": "cl100k_base", "budget": 4096, "tokens": 0, "truncated": False}}
    for _ in range(10):
        response["token_usage"]["tokens"] = len(report.compact_text(response))
    return response, {full["id"]: full}, {"src/a.ts": report.physical_lines(SOURCE)}, [{**full, "retrieval_score": .7}]


def target(path="src/a.ts", bounds=(1, 3)):
    return {"path": path, "symbol": "a different nested symbol", "evidence_lines": list(bounds),
            "definition_lines": [1, 3], "source_file_sha256": report.sha(SOURCE.encode()),
            "evidence_sha256": report.sha("".join(report.physical_lines(SOURCE)[bounds[0]-1:bounds[1]]).encode())}


def test_literal_bom_unicode_physical_crlf_and_enclosing_window_credit_are_preserved():
    response, inventory, sources, candidates = fixture()
    before = copy.deepcopy(response)
    assert report.verify_response(response, response, inventory, sources, Counter(), candidates) == 1
    assert response == before and len(report.physical_lines(SOURCE)) == 3
    gold = report.gold_groups({"kind": "present", "targets": [target()]}, sources, {"src/a.ts": report.sha(SOURCE.encode())})
    assert report.evidence_ranks(response["results"], gold) == [1]


def test_or_alternatives_and_all_and_groups_require_one_whole_span_per_group():
    row = {"path": "src/a.ts", "symbol": "enclosing", "start_line": 1, "end_line": 3}
    other = {"path": "src/b.ts", "symbol": "callee", "start_line": 1, "end_line": 3}
    groups = [[target("src/b.ts"), target()], [target("src/b.ts")]]
    assert report.evidence_ranks([row], groups) == [1, None]
    assert report.evidence_ranks([row, other], groups) == [1, 2]
    adjacent = [{**row, "end_line": 1}, {**row, "start_line": 2}]
    assert report.evidence_ranks(adjacent, [[target()]]) == [None]


@pytest.mark.parametrize("change,reason", [
    ("source", "bytes/hash"), ("bounds", "physical bounds"), ("full_hash", "bytes/hash"),
    ("tokens", "token count"), ("chars", "character count"), ("origin", "anchor provenance"),
    ("duplicate", "dedup"), ("window", "First raw symbol"),
])
def test_resigned_delivered_corruption_is_rejected_before_any_gold_credit(change, reason):
    response, inventory, sources, candidates = fixture()
    baseline = copy.deepcopy(response)
    row = response["results"][0]
    if change == "source":
        row["source"] = "wrong\n"
        row["source_hash"] = report.sha(row["source"].encode())
    elif change == "bounds":
        row["end_line"] = True
    elif change == "full_hash":
        row["content_hash"] = "a" * 64
    elif change == "tokens":
        response["token_usage"]["tokens"] = 0
    elif change == "chars":
        response["total_source_chars"] = 1
    elif change == "origin":
        row.update(origin="dependency", related_to="not-a-raw-anchor")
    elif change == "duplicate":
        response["results"].append(copy.deepcopy(row))
    else:
        candidates[0]["id"] = "different-first-window"
    with pytest.raises(ValueError, match=reason):
        report.verify_response(response, baseline, inventory, sources, Counter(), candidates)


@pytest.mark.parametrize("change", ["hash", "bool_bound", "empty", "ambiguous", "absent"])
def test_gold_hash_bounds_and_group_provenance_cannot_be_bypassed(change):
    _, _, sources, _ = fixture()
    query = {"kind": "present", "targets": [target()]}
    if change == "hash":
        query["targets"][0]["evidence_sha256"] = "b" * 64
    elif change == "bool_bound":
        query["targets"][0]["evidence_lines"][0] = True
    elif change == "empty":
        query["targets"] = []
    elif change == "ambiguous":
        query["target_groups"] = [[target()]]
    else:
        query["kind"] = "absent"
    with pytest.raises(ValueError):
        report.gold_groups(query, sources, {"src/a.ts": report.sha(SOURCE.encode())})


def test_paired_changes_include_complete_regressions_and_exclude_absent_credit():
    rows = []
    for name, kind, a, b in (("gain", "present", [1, None], [1, 2]),
                             ("loss", "present", [1], [None]), ("absent", "absent", [], [])):
        rows.append({"dataset": "toy", "query_id": name, "condition": "dense", "kind": kind,
                     "variants": {"baseline": {"ranks": a}, "backfill_only": {"ranks": b}}})
    changes = report.changes(rows, "baseline", "backfill_only")
    assert len(changes["complete_gains"]) == len(changes["complete_regressions"]) == 1
    assert len(changes["group_changes"]) == len(changes["rank_changes"]) == 2
    metrics = report.metrics([{**row, **row["variants"]["baseline"], "multi_file": len(row["variants"]["baseline"]["ranks"]) > 1,
                               "tokens": 100, "match_status": "candidates"} for row in rows])
    assert metrics["present"] == 2 and metrics["absent"] == 1 and metrics["complete"] == 1
    assert metrics["required_groups"] == 3 and metrics["covered_groups"] == 2
    assert metrics["complete_evidence_mrr"] == .5


@pytest.mark.parametrize("change", ["source", "module", "revision", "setting_type"])
def test_replay_cannot_redeclare_different_source_runtime_or_settings_under_unchanged_inputs(change):
    modules = {"src/semantic_search/" + name + ".py": "a" * 64 for name in
               ("__init__", "context", "source", "corpus", "languages", "javascript", "javascript_relations", "relations")}
    frozen = {"sources": [{"id": "toy", "source_file_sha256": {"src/a.ts": "b" * 64}}],
              "git_commit": "f" * 40, "implementation_sha256": modules, "settings": copy.deepcopy(report.CAPS)}
    plan = {"sources": copy.deepcopy(frozen["sources"]), "frozen_git_commit": frozen["git_commit"],
            "module_sha256": copy.deepcopy(modules), "settings": copy.deepcopy(report.CAPS)}
    report.verify_bindings(plan, frozen)
    if change == "source":
        plan["sources"][0]["source_file_sha256"]["src/a.ts"] = "c" * 64
    elif change == "module":
        plan["module_sha256"]["src/semantic_search/context.py"] = "c" * 64
    elif change == "revision":
        plan["frozen_git_commit"] = "c" * 40
    else:
        plan["settings"]["rerank"] = 0  # Equal to False in Python; distinct provenance type.
    with pytest.raises(ValueError):
        report.verify_bindings(plan, frozen)


def test_wrong_cache_is_rejected_before_tokenizer_can_attempt_a_download(tmp_path):
    cache = tmp_path / ".cache/tokenizers"
    cache.mkdir(parents=True)
    (cache / "9b5ad71b2ce5302211f9c61530b329a4922fc6a4").write_bytes(b"not pinned BPE data")
    with pytest.raises(ValueError, match="Tokenizer runtime/cache differs"):
        report.encoder(tmp_path, {"runtime": {"tiktoken": "0.14.0"}})


@pytest.mark.parametrize("change", ["duplicate", "missing", "reordered"])
def test_case_matrix_rejects_duplicate_missing_or_reordered_trials_even_at_nominal_length(change):
    cases = [{"dataset": "toy", "query_id": f"q{i}", "condition": condition, "repetition": 1}
             for i in range(30) for condition in ("bm25", "dense", "hybrid")]
    report.verify_cases(cases, cases, cases)
    altered = copy.deepcopy(cases)
    if change == "duplicate":
        altered[-1] = copy.deepcopy(altered[0])
    elif change == "missing":
        altered.pop()
    else:
        altered[0], altered[1] = altered[1], altered[0]
    with pytest.raises(ValueError, match="Replay case"):
        report.verify_cases(cases, altered, cases)
