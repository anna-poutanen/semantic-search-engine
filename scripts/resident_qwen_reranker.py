#!/usr/bin/env python3
"""Experimental local resident Qwen reranker; --preflight never imports MLX."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.metadata
import json
import os
from pathlib import Path
import secrets
import signal
import time

import prototype_qwen_reranker as prototype

WARMUP = {"query": "Locate addition of two values.", "source": "def add(left, right):\n    return left + right\n"}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write(path, value):
    def publish(item):
        if isinstance(item, str):
            return item.replace(str(Path(__file__).resolve().parents[1]), "<PROJECT>").replace(str(Path.home()), "<HOME>")
        if isinstance(item, list):
            return [publish(part) for part in item]
        if isinstance(item, dict):
            return {key: publish(part) for key, part in item.items()}
        return item
    path.write_text(json.dumps(publish(value), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def pairs_digest(pairs):
    return sha(json.dumps(pairs, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


class ScoringEngine:
    """Observation wrapper around an injected evaluator; useful without model imports."""

    def __init__(self, builder, scorer, verify, emit):
        self.builder, self.scorer, self.verify, self.emit = builder, scorer, verify, emit
        self.attempted = self.completed = self.requests = 0

    def score(self, request_id, pairs, *, warmup=False):
        require(isinstance(request_id, str) and 0 < len(request_id) <= 200, "Invalid request identity")
        require(isinstance(pairs, list) and 1 <= len(pairs) <= 50, "Expected one to fifty pairs")
        for pair in pairs:
            require(isinstance(pair, dict) and set(pair) == {"query", "source"}, "Only literal query/source enter scorer")
        started = time.perf_counter()
        self.verify()
        results, tokenization_s, forward_s = [], 0.0, 0.0
        self.requests += 1
        for index, pair in enumerate(pairs):
            phase = time.perf_counter()
            built = self.builder.build(pair["query"], pair["source"])
            tokenization_s += time.perf_counter() - phase
            identity = {"request_id": request_id, "pair_index": index, "warmup": warmup,
                        "query_sha256": built["query_sha256"], "source_sha256": built["source_sha256"],
                        "delivered_sha256": built["delivered_sha256"], "total_tokens": built["total_tokens"],
                        "input_ids_sha256": sha(json.dumps(built["input_ids"], separators=(",", ":")).encode()),
                        "truncated": built["truncated"], "delivered_lines": built["delivered_lines"]}
            self.attempted += 1
            self.emit(identity | {"event": "started", "forward_number": self.attempted})
            phase = time.perf_counter()
            try:
                score = self.scorer(built)
                elapsed = time.perf_counter() - phase
            except BaseException as error:
                self.emit(identity | {"event": "failed", "elapsed_s": time.perf_counter() - phase,
                                      "error_type": type(error).__name__, "error": str(error)})
                raise
            self.completed += 1
            forward_s += elapsed
            result = {key: value for key, value in built.items() if key not in ("input_ids", "source")} | score
            results.append(result)
            self.emit(identity | {"event": "completed", "forward_number": self.attempted,
                                  "elapsed_s": elapsed, "score": score})
        self.verify()
        return {"request_id": request_id, "pairs_sha256": pairs_digest(pairs), "results": results,
                "tokenization_s": tokenization_s, "forward_s": forward_s,
                "server_scoring_s": time.perf_counter() - started,
                "counts": {"requests": self.requests, "attempted_forwards": self.attempted,
                           "completed_forwards": self.completed}, "warmup": warmup}


def load_engine(checkpoint, emit):
    started = time.perf_counter()
    runtime = prototype.runtime_preflight()
    require(not runtime["errors"], "; ".join(runtime["errors"]))
    identity = prototype.checkpoint_preflight(checkpoint, verify_weights=True)
    verified_s = time.perf_counter() - started
    qwen3 = prototype.verified_qwen3_module(runtime)
    import mlx.core as mx
    from tokenizers import Tokenizer

    root = Path(identity["directory"])
    builder = prototype.PairBuilder(Tokenizer.from_file(str(root / "tokenizer.json")))
    model = weights = None
    try:
        phase = time.perf_counter()
        model = qwen3.Model(qwen3.ModelArgs.from_dict(json.loads((root / "config.json").read_text())))
        weights = model.sanitize(mx.load(str(root / "model.safetensors")))
        model.load_weights(list(weights.items()), strict=True)
        model.eval()
        mx.eval(model.parameters())
        prototype.verify_witnesses(identity)
        load_s = time.perf_counter() - phase
        engine = ScoringEngine(builder, lambda built, resident_model=model: prototype.score_pair(
            resident_model, built, builder.yes_id[0], builder.no_id[0], mx.array, mx.eval),
            lambda: prototype.verify_witnesses(identity), emit)
        provenance = {"model": identity, "runtime": runtime, "instruction": prototype.INSTRUCTION,
                      "max_total_tokens": prototype.MAX_TOKENS, "sequential_unpadded": True,
                      "verification_s": verified_s, "load_s": load_s,
                      "packages": dict(sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions()))}
        return engine, provenance, (model, weights, mx)
    except BaseException:
        del model, weights
        mx.clear_cache()
        raise


def serve(checkpoint, output, ready, token_path, frozen_plan):
    require(not (output / "reranker-start.json").exists(), "No resume/retry of an attempted resident run")
    plan = json.loads(frozen_plan.read_text())
    for name, expected in plan["implementation_sha256"].items():
        if name.startswith("scripts/"):
            require(sha((Path(__file__).resolve().parents[1] / name).read_bytes()) == expected,
                    "Frozen script changed before resident startup: " + name)
    require(plan["reranker"]["prototype_sha256"] == sha(Path(prototype.__file__).read_bytes()), "Prototype changed")
    token = token_path.read_text().strip()
    require(len(token) >= 32, "Local authentication token too short")
    packages = dict(sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions()))
    require(packages == plan["reranker"]["packages"], "Resident package closure differs before model load")
    write(output / "reranker-start.json", {"started_at_utc": datetime.now(timezone.utc).isoformat(),
          "frozen_plan_sha256": sha(frozen_plan.read_bytes()), "weights_loaded": False})
    server = resources = None
    engine = None
    complete = False
    with gzip.open(output / "forwards.jsonl.gz", "xt", encoding="utf-8") as ledger:
        def emit(value):
            ledger.write(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
            ledger.flush()

        try:
            engine, provenance, resources = load_engine(checkpoint, emit)
            require({key: provenance["runtime"][key] for key in ("packages", "qwen3_source_sha256")}
                    == plan["reranker"]["runtime"], "Resident runtime differs from freeze")
            write(output / "reranker-provenance.json", provenance)
            warmup = engine.score("generic-reranker-warmup", [WARMUP], warmup=True)
            write(output / "reranker-warmup.json", warmup)
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_POST(self):
                    if self.path != "/score" or not secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                        self.send_error(403)
                        return
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                        require(0 < length <= 4 * 1024 * 1024, "Bounded request body required")
                        payload = json.loads(self.rfile.read(length))
                        require(set(payload) == {"request_id", "pairs"}, "Unexpected request fields")
                        result = engine.score(payload["request_id"], payload["pairs"])
                        raw = json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(raw)))
                        self.end_headers()
                        self.wfile.write(raw)
                    except BaseException as error:
                        write(output / "reranker-failure.json", {"error_type": type(error).__name__, "error": str(error),
                              "attempted_forwards": engine.attempted, "completed_forwards": engine.completed})
                        self.send_error(500, type(error).__name__)
                        raise

            server = HTTPServer(("127.0.0.1", 0), Handler)
            server.timeout = 0.25
            stopping = False

            def stop(*_):
                nonlocal stopping
                stopping = True

            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            write(ready, {"url": "http://127.0.0.1:" + str(server.server_port),
                          "resident_ready": True, "generic_warmup_completed": True,
                          "pid": os.getpid(), "process_group": os.getpgrp(),
                          "frozen_plan_sha256": sha(frozen_plan.read_bytes())})
            while not stopping:
                server.handle_request()
                require(not (output / "reranker-failure.json").exists(), "Scoring failure; resident run will not resume")
            complete = not (output / "reranker-failure.json").exists()
        except BaseException as error:
            if not (output / "reranker-failure.json").exists():
                write(output / "reranker-failure.json", {"error_type": type(error).__name__, "error": str(error),
                      "attempted_forwards": engine.attempted if engine else 0,
                      "completed_forwards": engine.completed if engine else 0})
            raise
        finally:
            if server is not None:
                server.server_close()
            counts = {"attempted_forwards": engine.attempted if engine else 0,
                      "completed_forwards": engine.completed if engine else 0,
                      "requests": engine.requests if engine else 0}
            engine = None  # Release the callback that captures the resident model.
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
        result = {"runtime": prototype.runtime_preflight(), "checkpoint": prototype.checkpoint_preflight(args.checkpoint),
                  "prototype_sha256": sha(Path(prototype.__file__).read_bytes()), "model_calls": 0,
                  "weights_loaded": False, "network": False,
                  "packages": dict(sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions()))}
        print(json.dumps(result, indent=2))
        return 0
    if any(value is None for value in (args.output, args.ready, args.token_file, args.frozen_plan)):
        parser.error("--run requires --output, --ready, --token-file and --frozen-plan")
    serve(args.checkpoint, args.output, args.ready, args.token_file, args.frozen_plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
