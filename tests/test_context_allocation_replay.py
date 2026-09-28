"""CPU tests of bounded allocation, source fidelity, fail-closed gates and isolation."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("context_allocation_replay", ROOT / "scripts/replay_context_allocation.py")
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)


def snippet(identifier, *, symbol=None, text="return 1\n"):
    return SimpleNamespace(id=identifier, path=f"src/{symbol or identifier}.py", symbol=symbol or identifier,
        start_line=10, end_line=9 + len(replay.physical_lines(text)), text=text, content_hash=replay.sha(text.encode()))


class Graph:
    def __init__(self, mapping=None):
        self.mapping = mapping or {}
        self.calls = []

    def dependencies(self, item, *, limit):
        self.calls.append((item.id, limit))
        return self.mapping.get(item.id, [])[:limit]


def test_dependencies_preserve_first_five_and_consume_later_anchor_slots():
    items = [snippet(str(number)) for number in range(24)]
    helper_a, helper_b, helper_c = [snippet(name) for name in ("helper_a", "helper_b", "helper_c")]
    graph = Graph({"0": [helper_a, helper_b], "1": [helper_c]})
    original = [(item, float(24 - index)) for index, item in enumerate(items)]
    result = replay.selected_stream(original, graph, max_results=8, include_dependencies=True)
    assert [item.id for item, _, _, _ in result] == ["0", "1", "2", "3", "4", "helper_a", "helper_b", "helper_c"]
    assert result[-1][2:] == ("dependency", "1")
    assert [item.id for item, _, _, _ in replay.selected_stream(original, graph, max_results=12, include_dependencies=True)][-4:] == ["5", "6", "7", "8"]
    assert [item.id for item, _, _, _ in replay.selected_stream(original, graph, max_results=20, include_dependencies=True)][-1] == "16"


def test_disabling_dependencies_never_calls_graph_and_never_appends_unscored_tail():
    items = [snippet(str(number)) for number in range(24)]
    graph = Graph({"0": [snippet("outside_scored24")]})
    pairs = [(item, 1.) for item in items]
    result = replay.selected_stream(pairs, graph, max_results=20, include_dependencies=False)
    assert [item.id for item, _, _, _ in result] == [str(i) for i in range(20)]
    assert graph.calls == [] and all(origin == "retrieval" and parent is None for _, _, origin, parent in result)


def test_first_scored_window_is_kept_even_when_later_duplicate_contains_more_source():
    first, later = snippet("first", symbol="same"), snippet("later", symbol="same", text="return 1\nreturn 2\n")
    others = [snippet(str(i)) for i in range(10)]
    result = replay.selected_stream([(first, 5.), (later, 4.), *[(item, 3.) for item in others]],
                                    Graph(), max_results=8, include_dependencies=False)
    assert result[0][0].id == "first" and len(result) == 8
    assert "later" not in [item.id for item, _, _, _ in result]


@pytest.mark.parametrize("count,dependencies", [(True, True), (9, True), (12., False), (8, 1), (20, None)])
def test_undeclared_or_coerced_allocation_rejected(count, dependencies):
    with pytest.raises(ValueError, match="six fixed"):
        replay.selected_stream([], Graph(), max_results=count, include_dependencies=dependencies)


def test_compound_group_requires_one_whole_excerpt_per_group_not_union_or_alternative_flattening():
    windows = [{"path": "a.py", "start_line": 1, "end_line": 3},
               {"path": "a.py", "start_line": 4, "end_line": 6},
               {"path": "b.py", "start_line": 8, "end_line": 10}]
    groups = [[{"path": "a.py", "evidence_lines": [1, 6]}],
              [{"path": "b.py", "evidence_lines": [8, 10]}, {"path": "c.py", "evidence_lines": [2, 3]}]]
    assert replay.evidence_ranks(windows, groups) == [None, 3]


def test_gate_rejects_lost_baseline_behavior_even_with_net_quality_gain():
    baseline = {"development": {"complete_case_ids": ["a", "b"], "multi_file_complete": 1, "covered_groups": 3},
                "httpx_observed": {"complete_case_ids": ["c"], "multi_file_complete": 0, "covered_groups": 1}}
    variant = deepcopy(baseline)
    variant["development"]["complete_case_ids"] = ["a", "new1", "new2"]
    assert replay.gate(baseline, variant)["qualified"] is False
    assert replay.gate(baseline, variant)["reasons"][0]["lost"] == ["b"]
    assert replay.gate(baseline, baseline)["qualified"] is False
    variant = deepcopy(baseline)
    variant["development"]["complete_case_ids"].append("new")
    assert replay.gate(baseline, variant)["qualified"] is True
    variant["httpx_observed"]["covered_groups"] = 0
    assert replay.gate(baseline, variant)["qualified"] is False


def test_all_control_cases_must_pass_before_caller_can_compute_variants(monkeypatch):
    seen = []
    minimal = {"query": "behavior", "scope": {"source_root": "src", "membership": "saved", "source_exclusions": [], "exclusions_omitted": 0},
               "token_usage": {"encoding": "cl100k_base", "tokens": 1, "budget": 4096, "truncated": False}}
    cases = [{"case_id": name, "subset": "development", "original": minimal, "pairs": [], "graph": Graph()} for name in ("first", "second")]
    monkeypatch.setattr(replay, "pack", lambda *args: seen.append(args[-1]["id"]) or {"ok": True})
    calls = []
    def require_exact(*args):
        calls.append(1)
        if len(calls) == 2:
            raise ValueError("Original control differs")
    monkeypatch.setattr(replay, "require_exact", require_exact)
    monkeypatch.setattr(replay, "verify_response", lambda *args: "exact")
    with pytest.raises(ValueError, match="control differs"):
        replay.control_barrier(cases, object())
    assert seen == [replay.BASELINE, replay.BASELINE]


def test_existing_artifact_is_never_replaced(tmp_path):
    target = tmp_path / "evidence.json"
    target.write_text("original")
    with pytest.raises(FileExistsError):
        replay.write_new(target, {"replacement": True})
    assert target.read_text() == "original"


def test_missing_or_corrupt_tokenizer_cache_refused_without_download(tmp_path, monkeypatch):
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="offline cl100k"):
        replay.cache_runtime(ROOT)
    (tmp_path / replay.CACHE_KEY).write_bytes(b"not immutable ranks")
    with pytest.raises(ValueError, match="offline cl100k"):
        replay.cache_runtime(ROOT)


def test_real_cpu_packer_preserves_unicode_physical_lines_tokens_and_blocks_models_network():
    # Clean subprocess avoids importing live production modules or numerical
    # modules already loaded by unrelated tests during collection.
    code = r'''
import importlib.util, json, socket
from pathlib import Path
from types import SimpleNamespace
root=Path.cwd()
spec=importlib.util.spec_from_file_location("allocation",root/"scripts/replay_context_allocation.py")
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
old=r.read(root/"results/qwen-shortlist-development-2026-10-14/frozen-plan.json")
plan={"frozen_helpers":{ "src/semantic_search/"+name+".py": {
 "archive":"results/qwen-shortlist-development-2026-10-14/frozen-sources/src/semantic_search/"+name+".py.gz",
 "source_sha256":old["implementation_sha256"]["src/semantic_search/"+name+".py"]} for name in r.MODULES}}
runtime,raw=r.cache_runtime(root)
with r.offline_cpu_guard(),r.frozen_modules(root,plan,raw) as (context,Graph):
 for name in ("mlx.core","mlx_vlm","semantic_search.backend","semantic_search.qwen_reranker","transformers","numpy"):
  try:__import__(name)
  except RuntimeError:pass
  else:raise AssertionError("native/model import accepted")
 try:socket.create_connection(("127.0.0.1",1))
 except RuntimeError:pass
 else:raise AssertionError("network accepted")
 udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
 try:
  try:udp.sendto(b"invented",("127.0.0.1",1))
  except RuntimeError:pass
  else:raise AssertionError("UDP network accepted")
 finally:udp.close()
 from semantic_search.javascript import extract_javascript
 import semantic_search.corpus as corpus
 assert Path(corpus.__file__).resolve()!=root/"src/semantic_search/corpus.py"
 js="function helper(){ return 1; }\nfunction main(){ return helper(); }\n"
 windows=extract_javascript("src/toy.js",js,160,40)
 graph=Graph({"src/toy.js":js},windows)
 main=next(item for item in windows if item.symbol=="main")
 assert [item.symbol for item in graph.dependencies(main,limit=2)]==["helper"]
 class Empty:
  def dependencies(self,*args,**kwargs):return []
 text=("literal='a!b@c#d$e%f^g&h*i(j)k_l+m=n?o:p;q,r.s/tv\u2028'\r\n")*160
 item=SimpleNamespace(id="toy",path="toy.py",symbol="toy",start_line=7,end_line=166,text=text,content_hash=r.sha(text.encode()))
 scope={"source_root":"src","membership":"saved","source_exclusions":[],"exclusions_omitted":0}
 response=r.pack("Find behavior",scope,[(item,1.)],Empty(),context,r.CONDITIONS[1])
 row=response["results"][0]
 assert row["source"]=="".join(r.physical_lines(text)[:row["end_line"]-6])
 assert row["source_hash"]==r.sha(row["source"].encode()) and row["end_line"]<166
 assert response["token_usage"]["tokens"]<=4096 and response["token_usage"]["truncated"]
 assert len(context.encoding().encode(context.serialize_context(context.compact_result(response)),disallowed_special=()))==response["token_usage"]["tokens"]
 observed=json.loads(json.dumps(response,sort_keys=True));r.require_exact(response,observed,context)
 observed["results"][0]["end_line"]+=1
 try:r.require_exact(response,observed,context)
 except ValueError:pass
 else:raise AssertionError("corrupt original accepted")
print("synthetic CPU isolation/source/token/control checks passed")
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "synthetic CPU" in result.stdout


def test_exclusive_attempt_preserves_control_failure_and_refuses_same_directory_retry(tmp_path, monkeypatch):
    (tmp_path / "frozen-plan.json").write_text("{}")
    monkeypatch.setattr(replay, "validate_run", lambda *args: None)
    monkeypatch.setattr(replay, "own_bindings", lambda *args: {})
    attempts = []
    def fail(root, output, plan, state):
        attempts.append(1)
        state.update(stage="all_original_controls", controls_verified=2)
        raise ValueError("invented third control differs")
    monkeypatch.setattr(replay, "run_attempt", fail)
    with pytest.raises(ValueError, match="invented"):
        replay.run(tmp_path, tmp_path)
    failure = replay.read(tmp_path / "failure.json")
    assert failure["progress"]["controls_verified"] == 2
    assert failure["progress"]["condition_contexts_attempted"] == 0
    assert failure["resume_allowed"] is False and not (tmp_path / "replay.jsonl").exists()
    original = (tmp_path / "failure.json").read_bytes()
    with pytest.raises(FileExistsError):
        replay.run(tmp_path, tmp_path)
    assert attempts == [1] and (tmp_path / "failure.json").read_bytes() == original


def test_control_failure_progress_records_only_completed_controls(monkeypatch):
    minimal = {"query": "invented", "scope": {"source_root": "src", "membership": "saved", "source_exclusions": [], "exclusions_omitted": 0},
               "token_usage": {"encoding": "cl100k_base", "tokens": 1, "budget": 4096, "truncated": False}}
    cases = [{"case_id": str(i), "subset": "development", "original": minimal, "pairs": [], "graph": Graph()} for i in range(3)]
    monkeypatch.setattr(replay, "pack", lambda *args: {})
    calls = []
    def exact(*args):
        calls.append(1)
        if len(calls) == 3:
            raise ValueError("invented final mismatch")
    monkeypatch.setattr(replay, "require_exact", exact)
    monkeypatch.setattr(replay, "verify_response", lambda *args: "invented exact text")
    progress = {"controls_verified": 0}
    with pytest.raises(ValueError, match="final mismatch"):
        replay.control_barrier(cases, object(), progress)
    assert progress["controls_verified"] == 2
