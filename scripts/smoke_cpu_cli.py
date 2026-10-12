#!/usr/bin/env python3
"""Exercise the installed lexical CLI on an exclusive mixed-language CPU toy.

No model, source-retrieval quality, latency, or provider-token claim is made.
Requires installed project/languages dependencies and an already verified local
cl100k tokenizer cache. Never downloads, installs, or changes user configuration.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CACHE_KEY = "9b5ad71b2ce5302211f9c61530b329a4922fc6a4"
RANKS_SHA = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
QUERY = "cpu_label cpuUnique cpuClamp"
SOURCES = {
    "src/labels.py": 'def cpu_label(value):\n    return "café😀\u2028".join(value.split()).lower()\n',
    "src/unique.js": '\ufeffexport function cpuUnique(values) {\r\n  return [...new Set(values)];\r\n}\r\n',
    "src/clamp.ts": 'export function cpuClamp(value: number): number {\r\n  return Math.max(0, value); // 日本語😀\r\n}\r\n',
}
GOLD = {"src/labels.py": ("cpu_label", 1, 2),
        "src/unique.js": ("cpuUnique", 1, 3), "src/clamp.ts": ("cpuClamp", 1, 3)}
EXCLUSION = "src/excluded.py"
GUARD_SOURCE = '''import importlib.abc, json, os, socket, sys
def record(event, **details):
    with open(os.environ["CPU_CLI_GUARD_LOG"], "a", encoding="utf-8") as out:
        out.write(json.dumps({"event": event, **details}) + "\\n")
class NoModels(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = ("mlx", "mlx_embeddings", "mlx_lm", "semantic_search.embeddings", "semantic_search.qwen_worker")
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            record("blocked_model_import", module=fullname)
            raise ImportError("CPU CLI smoke forbids model imports: " + fullname)
sys.meta_path.insert(0, NoModels())
def no_connection(*args, **kwargs):
    record("blocked_socket_connection")
    raise RuntimeError("CPU CLI smoke forbids socket connections")
socket.socket.connect = no_connection
socket.socket.connect_ex = no_connection
record("guard_installed")
'''


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def physical_lines(text):
    return [line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text) if line]


def compact(response):
    fields = ("path", "symbol", "start_line", "end_line", "source", "source_hash", "truncated")
    rows = [{**{key: row[key] for key in fields}, "origin": row["origin"], "related_to": row["related_to"]}
            for row in response["results"]]
    return {"query": response["query"], "match_status": response["match_status"], "results": rows,
            "budget_exhausted": response["budget_exhausted"], "token_usage": response["token_usage"],
            "scope": response["scope"]}


def validate_response(response, sources, gold, encoder):
    require(response["query"] == QUERY and response["retrieval"] == "bm25" and response["rerank"] is False,
            "Installed CLI selected the wrong lexical route")
    require(response["indexstats"]["model_fingerprint"] is None
            and response["timings"]["query_embedding_s"] == 0
            and response["timings"]["h2o_s"] == response["timings"]["qwen_s"] == 0,
            "Lexical CLI used a model path")
    require(response["scope"]["source_root"] == "src"
            and response["scope"]["source_exclusions"] == [EXCLUSION], "Configured source scope differs")
    rows = response["results"]
    require(len(rows) == len(gold) and {row["path"] for row in rows} == set(gold),
            "Tracked eligible/excluded source membership differs")
    summaries = []
    for row in rows:
        symbol, low, high = gold[row["path"]]
        require(type(row["start_line"]) is type(row["end_line"]) is int
                and (row["symbol"], row["start_line"], row["end_line"]) == (symbol, low, high),
                "Exact symbol or physical source bounds differ")
        text = "".join(physical_lines(sources[row["path"]])[low - 1:high])
        require(row["source"] == text and row["source_hash"] == row["content_hash"] == sha(text.encode()),
                "Literal UTF-8 source/hash differs")
        require(row["truncated"] is False and row["origin"] == "retrieval"
                and row["related_to"] is None and row["h2o_score"] is row["qwen_score"] is None,
                "Unexpected clipping, dependency, or model score")
        summaries.append({"path": row["path"], "symbol": symbol, "start_line": low,
                          "end_line": high, "source_hash": row["source_hash"]})
    require(response["total_source_chars"] == sum(len(row["source"]) for row in rows)
            and response["total_source_chars"] <= 12000 and response["budget_exhausted"] is False,
            "Source character budget/accounting differs")
    payload = json.dumps(compact(response), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    tokens = len(encoder.encode(payload, disallowed_special=()))
    usage = response["token_usage"]
    require(usage["encoding"] == "cl100k_base" and type(usage["tokens"]) is int
            and usage["tokens"] == tokens <= 4096 and usage["budget"] == 4096
            and usage["truncated"] is False, "Exact compact token cap/accounting differs")
    return {"excerpts": summaries, "compact_tokens": tokens,
            "indexed_windows": response["indexstats"]["indexed_windows"],
            "reused_windows": response["indexstats"]["reused_windows"],
            "total_windows": response["indexstats"]["total_windows"]}


def cli_command(cli, repo, indexes):
    return [str(cli), "search", QUERY, "--repo", str(repo), "--source-root", "src",
            "--retrieval", "bm25", "--lexical-only", "--max-results", "10",
            "--exclude-source", EXCLUSION, "--token-budget", "4096", "--json",
            "--index-cache", str(indexes)]


def isolated_environment(base=None):
    """Keep the user's ordinary environment, replacing inherited Git controls."""
    env = {name: value for name, value in (os.environ if base is None else base).items()
           if not name.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_SYSTEM=os.devnull, GIT_ATTR_NOSYSTEM="1")
    return env


def initialize_repository(repo, template, tracked, env):
    template.mkdir()  # A fresh owned empty template supplies no user hooks/config.
    subprocess.run(["git", "-C", str(repo), "init", "--quiet", "--template=" + str(template)],
                   env=env, check=True, capture_output=True, timeout=10)
    subprocess.run(["git", "-C", str(repo), "add", "--", *tracked],
                   env=env, check=True, capture_output=True, timeout=10)


def guard_environment(directory, cache):
    directory.mkdir()
    (directory / "sitecustomize.py").write_text(GUARD_SOURCE, encoding="utf-8")
    log = directory / "events.jsonl"
    env = isolated_environment()
    env.update(PYTHONPATH=str(directory), PYTHONDONTWRITEBYTECODE="1",
               CPU_CLI_GUARD_LOG=str(log), TIKTOKEN_CACHE_DIR=str(cache))
    return env, log


def run_smoke(cli, cache):
    require(sha((cache / CACHE_KEY).read_bytes()) == RANKS_SHA, "Prepare the verified offline tokenizer cache first")
    os.environ["TIKTOKEN_CACHE_DIR"] = str(cache)
    import tiktoken
    encoder = tiktoken.get_encoding("cl100k_base")
    observations = []
    with tempfile.TemporaryDirectory(prefix="semantic-search-cpu-cli-") as owned:
        directory = Path(owned)
        repo, indexes = directory / "repo", directory / "indexes"
        (repo / "src/dist").mkdir(parents=True)
        sources, gold = dict(SOURCES), dict(GOLD)
        for name, text in sources.items():
            (repo / name).write_bytes(text.encode())
        ignored = {EXCLUSION: "def malformed(\n", "src/dist/broken.js": "export function {",
                   "src/broken.d.ts": "declare ???"}
        for name, text in ignored.items():
            (repo / name).write_text(text, encoding="utf-8")
        initialize_repository(repo, directory / "empty-git-template", [*sources, *ignored],
                              isolated_environment())
        (repo / "src/untracked.py").write_text("def malformed_untracked(\n", encoding="utf-8")
        env, log = guard_environment(directory / "guard", cache)
        command = cli_command(cli, repo, indexes)

        def successful(phase):
            child = subprocess.run(command, env=env, cwd=directory, capture_output=True, timeout=30)
            require(child.returncode == 0, "Installed CLI failed during " + phase + ": "
                    + child.stderr.decode(errors="replace").replace(str(directory), "<OWNED>")[-1200:])
            response = json.loads(child.stdout)
            proof = validate_response(response, sources, gold, encoder)
            observations.append({"phase": phase, "exit_code": child.returncode, **proof})
            return proof

        initial = successful("initial")
        require(initial["indexed_windows"] == 3 and initial["reused_windows"] == 0, "Initial authored source inventory differs")
        reopened = successful("reopened")
        require(reopened["indexed_windows"] == 0 and reopened["reused_windows"] == 3, "Reopened lexical source was not reused")
        sources["src/labels.py"] = "\n\n" + SOURCES["src/labels.py"].replace("lower()", "upper()")
        gold["src/labels.py"] = ("cpu_label", 3, 4)
        (repo / "src/labels.py").write_bytes(sources["src/labels.py"].encode())
        edited = successful("saved_edit")
        require(edited["indexed_windows"] == 1 and edited["reused_windows"] == 2, "Saved-edit lexical accounting differs")
        databases = list(indexes.glob("*.sqlite3"))
        require(len(databases) == 1, "Unexpected owned index inventory")
        before = sha(databases[0].read_bytes())
        (repo / "src/labels.py").write_text("def cpu_label(\n", encoding="utf-8")
        rejected = subprocess.run(command, env=env, cwd=directory, capture_output=True, timeout=30)
        require(rejected.returncode != 0 and not rejected.stdout.strip()
                and b"Cannot parse saved source src/labels.py" in rejected.stderr,
                "Malformed tracked source delivered stale CLI output")
        require(sha(databases[0].read_bytes()) == before, "Malformed source changed the previous durable index")
        observations.append({"phase": "malformed_saved_source", "exit_code": rejected.returncode,
                             "no_source_delivered": True, "previous_index_bytes_unchanged": True})
        (repo / "src/labels.py").write_bytes(sources["src/labels.py"].encode())
        recovery = successful("recovery")
        require(recovery["indexed_windows"] == 0 and recovery["reused_windows"] == 3, "Source recovery did not reuse the preserved index")
        events = [json.loads(line) for line in log.read_text().splitlines()]
        require(events == [{"event": "guard_installed"}] * 5,
                "Every CLI child must install the guard and attempt no model imports or socket connections")
    return {"checks_passed": True, "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "platform": platform.system(), "cli_processes": 5, "guard_installations": 5,
            "model_import_attempts": 0, "socket_connection_attempts": 0,
            "git_isolation": {"inherited_git_variables_removed": True,
                              "global_config": os.devnull, "system_config": os.devnull,
                              "no_system_config": True, "no_system_attributes": True,
                              "explicit_empty_owned_template": True},
            "owned_temporary_directory_removed": not directory.exists(),
            "helper_sha256": sha(Path(__file__).read_bytes()),
            "gold_source_sha256": {name: sha(text.encode()) for name, text in SOURCES.items()},
            "tokenizer_sha256": RANKS_SHA, "cli_command": cli_command("<INSTALLED-CLI>", "<OWNED-REPO>", "<OWNED-INDEXES>"),
            "observations": observations,
            "limits": ["Authored lexical CPU integration only; no model/relevance/latency/provider-token claim",
                       "Python, JavaScript and TypeScript extras are already installed; no installation or download is performed"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    require(args.output is None or not args.output.exists(), "Refuse to overwrite a smoke receipt")
    adjacent = Path(sys.executable).parent / "semantic-search"
    cli = args.cli or (adjacent if adjacent.is_file() else shutil.which("semantic-search"))
    require(cli is not None and Path(cli).is_file(), "Install semantic-search with its languages extra first")
    cache = Path(os.environ.get("TIKTOKEN_CACHE_DIR", ROOT / ".cache/tokenizers")).resolve()
    result = run_smoke(Path(cli).absolute(), cache)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: result[key] for key in ("checks_passed", "platform", "cli_processes", "model_import_attempts", "socket_connection_attempts")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
