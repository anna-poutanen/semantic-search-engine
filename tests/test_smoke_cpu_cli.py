"""Fault checks for the installed lexical smoke's independent source verifier."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/smoke_cpu_cli.py"
spec = importlib.util.spec_from_file_location("cpu_installed_cli_smoke", SCRIPT)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class CharacterCounter:
    def encode(self, text, *, disallowed_special):
        assert disallowed_special == ()
        return list(text)


def fixture_response():
    rows = []
    for name, (symbol, low, high) in smoke.GOLD.items():
        text = "".join(smoke.physical_lines(smoke.SOURCES[name])[low - 1:high])
        rows.append({"path": name, "symbol": symbol, "start_line": low, "end_line": high,
                     "source": text, "source_hash": smoke.sha(text.encode()), "content_hash": smoke.sha(text.encode()),
                     "truncated": False, "origin": "retrieval", "related_to": None, "h2o_score": None, "qwen_score": None})
    response = {"query": smoke.QUERY, "retrieval": "bm25", "rerank": False,
                "indexstats": {"model_fingerprint": None, "indexed_windows": 3, "reused_windows": 0, "total_windows": 3},
                "timings": {"query_embedding_s": 0., "h2o_s": 0., "qwen_s": 0.},
                "scope": {"source_root": "src", "source_exclusions": [smoke.EXCLUSION]},
                "results": rows, "match_status": "candidates", "budget_exhausted": False,
                "total_source_chars": sum(len(row["source"]) for row in rows),
                "token_usage": {"encoding": "cl100k_base", "budget": 4096, "tokens": 0, "truncated": False}}
    for _ in range(5):
        response["token_usage"]["tokens"] = len(json.dumps(smoke.compact(response), ensure_ascii=False, separators=(",", ":")))
    return response


def test_independent_verifier_retains_unicode_bom_crlf_and_physical_lines():
    response = fixture_response()
    assert response["results"][1]["source"].startswith("\ufeff")
    assert "\r\n" in response["results"][1]["source"]
    assert "\u2028" in response["results"][0]["source"]
    assert len(smoke.physical_lines(response["results"][0]["source"])) == 2
    proof = smoke.validate_response(response, smoke.SOURCES, smoke.GOLD, CharacterCounter())
    assert len(proof["excerpts"]) == 3 and proof["compact_tokens"] <= 4096


@pytest.mark.parametrize("damage", ["source", "hash", "line", "route", "token", "membership"])
def test_faulty_delivered_json_cannot_qualify(damage):
    response = copy.deepcopy(fixture_response())
    if damage == "source":
        response["results"][1]["source"] = response["results"][1]["source"].replace("\r\n", "\n")
    elif damage == "hash":
        response["results"][0]["source_hash"] = "0" * 64
    elif damage == "line":
        response["results"][0]["start_line"] = True
    elif damage == "route":
        response["indexstats"]["model_fingerprint"] = "unexpected-model"
    elif damage == "token":
        response["token_usage"]["tokens"] += 1
    else:
        response["results"][0]["path"] = "src/untracked.py"
    with pytest.raises(ValueError):
        smoke.validate_response(response, smoke.SOURCES, smoke.GOLD, CharacterCounter())


@pytest.mark.parametrize("statement,event", [("import semantic_search.embeddings", "blocked_model_import"),
                                             ("import socket; socket.socket().connect(('127.0.0.1', 9))", "blocked_socket_connection")])
def test_child_guard_blocks_before_model_import_or_socket_connection(tmp_path, statement, event):
    env, log = smoke.guard_environment(tmp_path / "guard", tmp_path)
    child = subprocess.run([sys.executable, "-c", statement], env=env, capture_output=True, timeout=10)
    assert child.returncode != 0
    events = [json.loads(line) for line in log.read_text().splitlines()]
    assert [row["event"] for row in events] == ["guard_installed", event]
