#!/usr/bin/env python3
"""Owned experimental loopback worker for frozen Qwen batching studies."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.metadata
import json
import os
from pathlib import Path
import secrets
import signal

import qwen_reranker_batch as batch
from resident_qwen_reranker import require, sha, write

ROOT = Path(__file__).resolve().parents[1]
WARMUPS = [{"query": "Locate addition of two values.",
            "source": "def add(left, right):\n" + "    # fixed warmup\n" * index + "    return left + right\n"}
           for index in range(8)]


def serve(checkpoint, output, ready, token_path, frozen_plan):
    require(not (output / "reranker-start.json").exists(), "No resume/overwrite of attempted worker")
    plan = json.loads(frozen_plan.read_text())
    for name, expected in plan["implementation_sha256"].items():
        require(sha((ROOT / name).read_bytes()) == expected, "Frozen implementation changed: " + name)
    packages = dict(sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions()))
    require(packages == plan["reranker"]["packages"], "Resident package closure differs")
    token = token_path.read_text().strip()
    require(len(token) >= 32, "Local authentication token too short")
    write(output / "reranker-start.json", {"started_at_utc": datetime.now(timezone.utc).isoformat(),
          "frozen_plan_sha256": sha(frozen_plan.read_bytes()), "weights_loaded": False})
    server = resources = engine = None
    complete = False
    with gzip.open(output / "forwards.jsonl.gz", "xt", encoding="utf-8") as ledger:
        def emit(value):
            ledger.write(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
            ledger.flush()
        try:
            engine, provenance, resources = batch.load_engine(checkpoint, emit)
            require(provenance["runtime"]["packages"] == plan["reranker"]["runtime"]["packages"]
                    and provenance["runtime"]["qwen3_source_sha256"]
                    == plan["reranker"]["runtime"]["qwen3_source_sha256"], "Resident runtime differs")
            write(output / "reranker-provenance.json", provenance)
            warmups = [engine.score("generic-warmup-b" + str(size), WARMUPS, batch_size=size, warmup=True)
                       for size in batch.BATCH_SIZES]
            write(output / "reranker-warmup.json", warmups)
            seen = set()
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_POST(self):
                    if self.path != "/score" or not secrets.compare_digest(
                            self.headers.get("Authorization", ""), "Bearer " + token):
                        self.send_error(403)
                        return
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                        require(0 < length <= 4 * 1024 * 1024, "Bounded body required")
                        payload = json.loads(self.rfile.read(length))
                        require(set(payload) == {"request_id", "pairs", "batch_size"}, "Unexpected payload fields")
                        require(payload["request_id"] not in seen, "Duplicate request; no score caching/retries")
                        seen.add(payload["request_id"])
                        result = engine.score(payload["request_id"], payload["pairs"], batch_size=payload["batch_size"])
                        raw = json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(raw)))
                        self.end_headers()
                        self.wfile.write(raw)
                    except BaseException as error:
                        write(output / "reranker-failure.json", {"error_type": type(error).__name__,
                              "error": str(error), "counts": engine.counters()})
                        self.send_error(500, type(error).__name__)
                        raise
            server = HTTPServer(("127.0.0.1", 0), Handler)
            server.timeout = .25
            stopping = False
            def stop(*_):
                nonlocal stopping
                stopping = True
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            write(ready, {"url": "http://127.0.0.1:" + str(server.server_port), "resident_ready": True,
                          "generic_warmup_completed": True, "pid": os.getpid(), "process_group": os.getpgrp(),
                          "frozen_plan_sha256": sha(frozen_plan.read_bytes())})
            while not stopping:
                server.handle_request()
                require(not (output / "reranker-failure.json").exists(), "Failed worker cannot resume")
            complete = True
        except BaseException as error:
            if not (output / "reranker-failure.json").exists():
                write(output / "reranker-failure.json", {"error_type": type(error).__name__, "error": str(error),
                      "counts": engine.counters() if engine else {}})
            raise
        finally:
            if server is not None:
                server.server_close()
            counts = engine.counters() if engine else {}
            engine = None  # Release scorer closure before measuring post-cleanup allocation.
            if resources is not None:
                model, weights, mx = resources
                resources = None
                del model, weights
                mx.clear_cache()
                counts["mlx_active_memory_after_cleanup"] = int(mx.get_active_memory())
                counts["mlx_process_lifetime_peak_bytes"] = int(mx.get_peak_memory())
            write(output / "reranker-stop.json", counts | {"clean_stop": complete,
                  "stopped_at_utc": datetime.now(timezone.utc).isoformat(), "listener_closed": server is not None,
                  "memory_scope": "MLX allocator, not OS RSS or simultaneous combined memory"})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--ready", type=Path)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--frozen-plan", type=Path)
    args = parser.parse_args(argv)
    if not args.run:
        result = {"runtime": batch.runtime_preflight(),
                  "checkpoint": batch.prototype.checkpoint_preflight(args.checkpoint), "model_calls": 0,
                  "weights_loaded": False, "network": False, "batch_sizes": list(batch.BATCH_SIZES),
                  "max_padded_tokens": batch.MAX_PADDED_TOKENS,
                  "packages": dict(sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions()))}
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    if any(value is None for value in (args.output, args.ready, args.token_file, args.frozen_plan)):
        parser.error("--run requires --output, --ready, --token-file and --frozen-plan")
    serve(args.checkpoint, args.output, args.ready, args.token_file, args.frozen_plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
