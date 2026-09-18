"""CPU contracts for prospective experimental retrieval; no model execution."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("full_reranker_test_subject", ROOT / "scripts/run_qwen_reranker_benchmark.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_schedule_all_actual_conditions_repetitions_and_rotation():
    catalog = [{"dataset": "first", "query_id": "a"}, {"dataset": "first", "query_id": "b"},
               {"dataset": "second", "query_id": "c"}]
    schedule = benchmark.schedule(catalog)
    assert len(schedule) == 36
    assert len({tuple(row.items()) for row in schedule}) == 36
    assert [row["condition"] for row in schedule[:4]] == list(benchmark.CONDITIONS)
    assert [row["condition"] for row in schedule[4:8]] == list(benchmark.CONDITIONS[1:] + benchmark.CONDITIONS[:1])
    assert all(row["dataset"] == "first" for row in schedule[:24])


def test_rerank_uses_raw_difference_and_original_rank_for_ties():
    pairs = [(SimpleNamespace(id=name, text=name), 0.1) for name in ("a", "b", "c")]
    scores = [{"source_sha256": benchmark.sha(name.encode()), "logit_difference": difference}
              for name, difference in (("a", 9.0), ("b", 10.0), ("c", 10.0))]
    assert benchmark.qwen_order(pairs, scores) == [1, 2, 0]


def test_rerank_rejects_missing_scores_or_changed_source():
    pairs = [(SimpleNamespace(id="a", text="source"), 0.5)]
    with pytest.raises(ValueError, match="All candidates"):
        benchmark.qwen_order(pairs, [])
    with pytest.raises(ValueError, match="Scored source differs"):
        benchmark.qwen_order(pairs, [{"source_sha256": "wrong", "logit_difference": 1}])


def test_original_span_unchanged_enriched_only_hash_and_symbol_compatibility():
    sources = {"module.py": "def outer():\n    return 1\n"}
    query = {"kind": "present", "targets": [{"path": "module.py", "symbol": "outer", "evidence_lines": [2, 2]}]}
    groups = benchmark.normalized_groups(query, sources)
    assert groups[0][0]["evidence_lines"] == [2, 2]
    assert "evidence_sha256" not in query["targets"][0]
    window = {"path": "module.py", "symbol": "enclosing", "start_line": 1, "end_line": 2,
              "source": sources["module.py"], "source_hash": benchmark.sha(sources["module.py"].encode())}
    assert benchmark.evidence_ranks([window], groups, sources) == [1]
    assert benchmark.evidence_ranks([window], groups, sources, symbol_compatibility=True) == [None]


def test_partial_span_never_counts_and_absent_has_no_positive_group():
    sources = {"module.py": "line1\nline2\nline3\n"}
    query = {"kind": "present", "targets": [{"path": "module.py", "symbol": "f", "evidence_lines": [1, 3]}]}
    groups = benchmark.normalized_groups(query, sources)
    window = {"path": "module.py", "symbol": "f", "start_line": 1, "end_line": 2,
              "source": "line1\nline2\n", "source_hash": benchmark.sha(b"line1\nline2\n")}
    assert benchmark.evidence_ranks([window], groups, sources) == [None]
    assert benchmark.normalized_groups({"kind": "absent", "targets": []}, sources) == []


def test_existing_evidence_hash_drift_fails():
    query = {"kind": "present", "targets": [{"path": "x.py", "symbol": "f", "evidence_lines": [1, 1], "evidence_sha256": "bad"}]}
    with pytest.raises(ValueError, match="Existing evidence hash"):
        benchmark.normalized_groups(query, {"x.py": "return 1\n"})


def test_local_opener_explicitly_disables_proxy_and_redirects(monkeypatch):
    captured = []
    monkeypatch.setattr(benchmark, "build_opener", lambda *handlers: captured.extend(handlers))
    benchmark.loopback_opener()
    assert captured[0].proxies == {}
    with pytest.raises(ValueError, match="must not redirect"):
        captured[1].redirect_request(None, None, None, None, None, None)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000@evil.example", "http://localhost:8000",
                               "https://127.0.0.1:8000", "http://127.0.0.1:8000/score",
                               "http://127.0.0.1:8000?redirect=1", "http://127.0.0.1:8000#fragment"])
def test_authenticated_request_requires_exact_owned_loopback_url(url):
    with pytest.raises(ValueError):
        benchmark.validate_loopback_url(url)


def test_valid_loopback_url():
    assert benchmark.validate_loopback_url("http://127.0.0.1:43210") == "http://127.0.0.1:43210"
