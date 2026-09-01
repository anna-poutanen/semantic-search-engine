#!/usr/bin/env python3
"""Exercise the actual Qwen service process, saved edits, and restart reuse."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

from semantic_search.client import SearchClient
from semantic_search.shortlist import memory_footprints

SOURCE = '''import json

def normalize_path(path):
    return path.strip().rstrip("/")

def backoff_delay(attempt):
    return min(2 ** attempt, 60)

def decode_response(data):
    return json.loads(data)
'''
QUERY = "Calculate an exponential retry delay with a maximum wait."


def stop(process):
    process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise RuntimeError("Search service did not stop within 20 seconds")
    if process.returncode != 0:
        raise RuntimeError(f"Search service exited with {process.returncode}")


def start(repo, cache, log):
    command = [sys.executable, "-m", "semantic_search.cli", "serve", "--repo", str(repo),
               "--source-root", "src", "--index-cache", str(cache), "--port", "0"]
    began = time.perf_counter()
    with log.open("w") as output:
        process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT)
    try:
        while time.perf_counter() - began < 60:
            output = log.read_text()
            match = re.search(r"Ready: (http://127\.0\.0\.1:\d+)", output)
            if match:
                return process, SearchClient(match[1]), time.perf_counter() - began
            if process.poll() is not None:
                raise RuntimeError(f"Service failed to start:\n{output}")
            time.sleep(.1)
        raise RuntimeError("Search service was not ready within 60 seconds")
    except BaseException:
        if process.poll() is None:
            stop(process)
        raise


def verify(response, source):
    lines = source.splitlines(keepends=True)
    assert response["total_source_chars"] == sum(len(r["source"]) for r in response["results"])
    assert response["total_source_chars"] <= response["source_budget"]
    for row in response["results"]:
        assert row["source"] == "".join(lines[row["start_line"] - 1:row["end_line"]])
        assert row["source_hash"] == hashlib.sha256(row["source"].encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=Path(".cache/service-smoke"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=False)
    if args.output.exists():
        raise ValueError("Output already exists; choose a new file")
    repo = (args.work_dir / "repo").resolve()
    (repo / "src").mkdir(parents=True)
    file = repo / "src/sample.py"
    file.write_text(SOURCE)
    def git(*arguments):
        return subprocess.check_output(["git", "-C", str(repo), *arguments], stderr=subprocess.STDOUT)
    git("init", "-b", "main")
    git("add", "src/sample.py")
    git("-c", "user.name=Local smoke test", "-c", "user.email=smoke@example.invalid",
        "commit", "-m", "Create three implementation windows")
    steps = []
    process, client, startup_s = start(repo, args.work_dir / "indexes", args.work_dir / "first.log")
    try:
        steps.append({"phase": "prewarmed", "status": client.status(), "startup_s": startup_s})
        def search(phase, source, **settings):
            started = time.perf_counter()
            response = client.search(QUERY, **settings)
            elapsed = time.perf_counter() - started
            verify(response, source)
            steps.append({"phase": phase, "elapsed_http_s": elapsed, "response": response})
            return response
        warm = search("unchanged", SOURCE)
        assert warm["indexstats"]["indexed_windows"] == 0
        assert warm["indexstats"]["total_windows"] == 3
        changed = SOURCE.replace("attempt, 60", "attempt, 90")
        file.write_text(changed)
        edit = search("one_saved_edit", changed)
        assert edit["indexstats"]["indexed_windows"] == 1
        assert edit["indexstats"]["reused_windows"] == 2
        assert any(r["symbol"] == "backoff_delay" and "90" in r["source"] for r in edit["results"])
        bounded = search("bounded_excerpt", changed, source_budget=60)
        assert bounded["budget_exhausted"]
        assert any(r["truncated"] for r in bounded["results"])
        file.write_text(changed + "\ndef incomplete(\n")
        try:
            client.search(QUERY)
        except RuntimeError as error:
            assert "HTTP 409" in str(error)
            steps.append({"phase": "invalid_source_rejected", "error": str(error)})
        else:
            raise AssertionError("Invalid source was served")
        removed = changed.replace('def normalize_path(path):\n    return path.strip().rstrip("/")\n\n', "")
        file.write_text(removed)
        deletion = search("deletion_and_line_shift", removed)
        assert deletion["indexstats"]["indexed_windows"] == 0
        assert deletion["indexstats"]["reused_windows"] == 2
        assert deletion["indexstats"]["deleted_windows"] >= 1
        assert all(r["symbol"] != "normalize_path" for r in deletion["results"])
        memory = memory_footprints({"search_service": process.pid})
    finally:
        stop(process)
    process, client, restart_s = start(repo, args.work_dir / "indexes", args.work_dir / "restart.log")
    try:
        status = client.status()
        assert status["indexstats"]["indexed_windows"] == 0
        assert status["indexstats"]["reused_windows"] == 2
        response = client.search(QUERY)
        verify(response, removed)
        steps.append({"phase": "restart_reuses_durable_vectors", "startup_s": restart_s,
                      "status": status, "response": response})
    finally:
        stop(process)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"steps": steps, "memory": memory,
        "termination": "Both CLI processes handled SIGTERM and exited with status 0.",
        "limits": "Integration smoke test on three toy implementations; not retrieval-quality evidence."}, indent=2) + "\n")
    print(f"Live service checks passed; saved {args.output}")


if __name__ == "__main__":
    main()
