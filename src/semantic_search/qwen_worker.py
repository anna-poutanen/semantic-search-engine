"""Explicit local worker for the pinned original BF16 Qwen B1 scorer.

Run only through an owned isolated interpreter. No generation, Hub loader,
input/KV/result cache, or batched inference is used; native compiled kernels may
be reused by the warm MLX runtime.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
import json
import os
from pathlib import Path
import secrets
import signal
import sys
import time

from semantic_search import qwen_reranker_core as core
from semantic_search.client import _object, _finite_float, _constant

MAX_BODY_BYTES = 4 * 1024 * 1024
WARMUP = {"query": "Locate addition of two values.",
          "source": "def add(left, right):\n    return left + right\n"}
SUPPORTING_SOURCE_SHA256 = {
    "base.py": "b782b2ac0f55ed8fa9c1afce1f36307d5d8714aad2c0991d63998047f7507174",
    "rope_utils.py": "06466d57dfe1ccc1e61ba595c0a8f2dd3a14bcb5c6a1d06c67591dc03128606b",
}


def runtime_preflight():
    """Original version/Qwen3 check plus the native support pins from the B1 study."""
    runtime = core.runtime_preflight()
    runtime["supporting_source_sha256"] = {}
    if runtime["qwen3_source_path"] is not None:
        directory = Path(runtime["qwen3_source_path"]).parent
        for name, expected in SUPPORTING_SOURCE_SHA256.items():
            path = directory / name
            actual = core.file_hash(path) if path.is_file() else None
            runtime["supporting_source_sha256"][name] = actual
            if actual != expected:
                runtime["errors"].append("Pinned native support source differs: " + name)
    return runtime


def verify_supporting_modules(runtime):
    directory = Path(runtime["qwen3_source_path"]).parent
    for name, expected in SUPPORTING_SOURCE_SHA256.items():
        module = sys.modules.get("mlx_lm.models." + name.removesuffix(".py"))
        actual = getattr(module, "__file__", None)
        if (actual is None or Path(actual).resolve(strict=True) != (directory / name).resolve(strict=True)
                or core.file_hash(Path(actual)) != expected):
            raise ValueError("Imported native support origin/bytes differ: " + name)


def private_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
    os.replace(temporary, path)


class ScoringEngine:
    """One literal pair and one native backbone evaluation per score request."""

    def __init__(self, builder, scorer, verify=lambda: None, *, provenance=None, emit=lambda value: None):
        self.builder, self.scorer, self.verify = builder, scorer, verify
        self.provenance, self.emit = provenance or {}, emit
        self.requests = self.attempted = self.completed = 0
        self.closed = False

    def close(self):
        self.closed = True
        self.scorer = None  # Handler closures may retain the engine after server_close.

    def counters(self):
        return {"requests": self.requests, "attempted_pairs": self.attempted,
                "completed_pairs": self.completed, "attempted_forwards": self.attempted,
                "completed_forwards": self.completed}

    def score(self, request_id, query, source, *, warmup=False):
        if self.closed:
            raise RuntimeError("Qwen scoring engine is closed")
        if (not isinstance(request_id, str) or not 1 <= len(request_id) <= 200
                or not isinstance(query, str) or not query.strip() or len(query) > 8192
                or not isinstance(source, str) or not source):
            raise ValueError("Expected a bounded request identity and nonempty literal query/source")
        started = time.perf_counter()
        self.verify()
        self.requests += 1
        phase = time.perf_counter()
        built = self.builder.build(query, source)
        tokenization_s = time.perf_counter() - phase
        identity = {"request_id": request_id, "warmup": warmup, "batch_size": 1,
                    "source_sha256": built["source_sha256"], "query_sha256": built["query_sha256"],
                    "delivered_sha256": built["delivered_sha256"], "total_tokens": built["total_tokens"],
                    "input_ids_sha256": core.sha256(json.dumps(built["input_ids"], separators=(",", ":")).encode())}
        self.attempted += 1
        self.emit(identity | {"event": "started", "forward_number": self.attempted})
        phase = time.perf_counter()
        try:
            observed = self.scorer(built)
            expected = core.binary_score(observed["yes_logit"], observed["no_logit"])
            if observed != expected:
                raise ValueError("Qwen score differs from original binary arithmetic")
        except BaseException as error:
            self.emit(identity | {"event": "failed", "error_type": type(error).__name__})
            if isinstance(error, Exception):
                raise RuntimeError("Native Qwen evaluation failed") from error
            raise
        forward_s = time.perf_counter() - phase
        self.completed += 1
        self.emit(identity | {"event": "completed", "forward_number": self.attempted,
                              "forward_s": forward_s, "score": observed})
        self.verify()
        result = {key: value for key, value in built.items() if key not in {"source", "input_ids"}} | observed
        return {"request_id": request_id, "batch_size": 1, "warmup": warmup, "result": result,
                "tokenization_s": tokenization_s, "forward_s": forward_s,
                "server_scoring_s": time.perf_counter() - started, "counts": self.counters()}


def load_engine(checkpoint, emit, *, owner_check=lambda: None):
    owner_check()
    started = time.perf_counter()
    runtime = runtime_preflight()
    if runtime["errors"]:
        raise ValueError("; ".join(runtime["errors"]))
    identity = core.checkpoint_preflight(checkpoint, verify_weights=True)
    verified_s = time.perf_counter() - started
    owner_check()
    qwen3 = core.verified_qwen3_module(runtime)
    verify_supporting_modules(runtime)
    import mlx.core as mx
    from tokenizers import Tokenizer

    root = Path(identity["directory"])
    builder = core.PairBuilder(Tokenizer.from_file(str(root / "tokenizer.json")))
    model = weights = None
    try:
        phase = time.perf_counter()
        owner_check()
        model = qwen3.Model(qwen3.ModelArgs.from_dict(json.loads((root / "config.json").read_text())))
        weights = model.sanitize(mx.load(str(root / "model.safetensors")))
        model.load_weights(list(weights.items()), strict=True)
        model.eval()
        mx.eval(model.parameters())
        core.verify_witnesses(identity)
        owner_check()
        provenance = {"model": identity, "runtime": runtime, "instruction": core.INSTRUCTION,
                      "max_total_tokens": core.MAX_TOKENS, "batch_size": 1,
                      "sequential_unpadded": True, "verification_s": verified_s,
                      "load_s": time.perf_counter() - phase,
                      "scoring_core_sha256": core.file_hash(Path(core.__file__))}
        def verify():
            try:
                core.verify_witnesses(identity)
            except (ValueError, OSError) as error:
                raise RuntimeError("Pinned checkpoint changed after loading") from error

        engine = ScoringEngine(builder, lambda built, resident_model=model: core.score_pair(
            resident_model, built, builder.yes_id[0], builder.no_id[0], mx.array, mx.eval),
            verify, provenance=provenance, emit=emit)
        return engine, (model, weights, mx)
    except BaseException:
        del model, weights
        mx.clear_cache()
        raise


def create_server(engine, token):
    """Return an unstarted authenticated loopback server; CPU injection is supported."""
    if not isinstance(token, str) or len(token) < 32 or any(char.isspace() for char in token):
        raise ValueError("Worker requires a private authentication token")

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *_):
            pass

        def guard(self):
            hosts = self.headers.get_all("Host", [])
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            auth = self.headers.get_all("Authorization", [])
            if (not ipaddress.ip_address(self.client_address[0]).is_loopback or len(hosts) != 1
                    or hosts[0].lower() not in allowed or len(auth) != 1
                    or not secrets.compare_digest(auth[0], "Bearer " + token)
                    or self.headers.get_all("Origin") or self.headers.get_all("Referer")
                    or any(name.lower().startswith("sec-fetch-") for name in self.headers)):
                self.reply(403, {"error": "Local authenticated request required"})
                return False
            return True

        def reply(self, code, value):
            raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            self.wfile.write(raw)

        def do_GET(self):
            if not self.guard():
                return
            if self.path != "/status":
                return self.reply(404, {"error": "Unknown endpoint"})
            self.reply(200, {"ready": not engine.closed, "model_id": core.MODEL_ID,
                            "revision": core.REVISION, "batch_size": 1, "pid": os.getpid(),
                            "counts": engine.counters(), "provenance": engine.provenance})

        def do_POST(self):
            if not self.guard():
                return
            if self.path != "/score":
                return self.reply(404, {"error": "Unknown endpoint"})
            try:
                lengths, types = self.headers.get_all("Content-Length", []), self.headers.get_all("Content-Type", [])
                if (self.headers.get_all("Transfer-Encoding") or len(lengths) != 1
                        or not lengths[0].isascii() or not lengths[0].isdigit() or len(lengths[0]) > 20
                        or not 0 < int(lengths[0]) <= MAX_BODY_BYTES or len(types) != 1
                        or types[0].split(";", 1)[0].strip().lower() != "application/json"):
                    raise ValueError("One bounded JSON body is required")
                raw = self.rfile.read(int(lengths[0]))
                if len(raw) != int(lengths[0]):
                    raise ValueError("Incomplete request body")
                payload = json.loads(raw.decode(), object_pairs_hook=_object,
                                     parse_float=_finite_float, parse_constant=_constant)
                if not isinstance(payload, dict) or set(payload) != {"request_id", "query", "source"}:
                    raise ValueError("Only request identity and literal query/source are accepted")
                result = engine.score(**payload)
            except (ValueError, UnicodeError, RecursionError, TimeoutError):
                return self.reply(400, {"error": "Invalid literal scoring request"})
            except Exception:
                return self.reply(503, {"error": "Local Qwen scoring failed"})
            self.reply(200, result)

    return HTTPServer(("127.0.0.1", 0), Handler)


def serve(checkpoint, state_dir, *, loader=load_engine, owner_pid=None):
    if owner_pid is not None and (type(owner_pid) is not int or owner_pid < 1):
        raise ValueError("Worker requires a positive explicitly bound owner process ID")
    token_path = state_dir / "token.secret"
    if state_dir.stat().st_mode & 0o077 or token_path.stat().st_mode & 0o077:
        raise ValueError("Qwen worker state and auth token must be private")
    token = token_path.read_text().strip()
    server = engine = resources = None
    stopping = False
    stop_reason = "signal"

    def check_owner():
        nonlocal stop_reason
        if owner_pid is not None and os.getppid() != owner_pid:
            stop_reason = "owner_exited"
            raise InterruptedError("Bound owner exited before model/readiness")

    started_at = datetime.now(timezone.utc).isoformat()

    def stop(*_):
        nonlocal stopping
        stopping = True

    previous = {signum: signal.signal(signum, stop) for signum in (signal.SIGTERM, signal.SIGINT)}
    complete = False
    with (state_dir / "forwards.jsonl").open("x", encoding="utf-8") as ledger:
        (state_dir / "forwards.jsonl").chmod(0o600)

        def emit(value):
            ledger.write(json.dumps(value, sort_keys=True, allow_nan=False) + "\n")
            ledger.flush()

        try:
            check_owner()
            engine, resources = loader(checkpoint, emit, owner_check=check_owner)
            warmup = engine.score("generic-warmup", **WARMUP, warmup=True)
            check_owner()
            if stopping:
                raise InterruptedError("Owned worker stopped during startup")
            server = create_server(engine, token)
            server.timeout = .1
            private_json(state_dir / "ready.json", {"url": f"http://127.0.0.1:{server.server_port}",
                "ready": True, "batch_size": 1, "pid": os.getpid(), "process_group": os.getpgrp(),
                "owner_pid": owner_pid, "started_at_utc": started_at,
                "provenance": engine.provenance, "warmup": warmup})
            while not stopping:
                if owner_pid is not None and os.getppid() != owner_pid:
                    stop_reason = "owner_exited"
                    stopping = True
                    break
                server.handle_request()
            complete = True
        finally:
            if server is not None:
                server.server_close()
            counts = engine.counters() if engine else {}
            if engine is not None:
                engine.close()
            engine = None  # Release the closure capturing model before allocator cleanup.
            if resources is not None:
                model, weights, mx = resources
                resources = None
                del model, weights
                mx.clear_cache()
                counts["mlx_active_memory_after_cleanup"] = int(mx.get_active_memory())
                counts["mlx_process_lifetime_peak_bytes"] = int(mx.get_peak_memory())
            token_error = None
            try:
                token_path.unlink(missing_ok=True)
            except OSError as error:
                token_error = type(error).__name__
            private_json(state_dir / "stop.json", {"clean_stop": complete, "counts": counts,
                        "listener_closed": server is not None, "stop_reason": stop_reason,
                        "auth_token_removed": not token_path.exists(), "auth_cleanup_error": token_error,
                        "stopped_at_utc": datetime.now(timezone.utc).isoformat()})
            for signum, handler in previous.items():
                signal.signal(signum, handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--owner-pid", type=int)
    parser.add_argument("--preflight", action="store_true", help="Verify local metadata only; no MLX import or weights read")
    args = parser.parse_args(argv)
    if args.preflight:
        print(json.dumps({"runtime": runtime_preflight(),
              "checkpoint": core.checkpoint_preflight(args.checkpoint),
              "weights_loaded": False, "model_calls": 0, "network": False, "batch_size": 1}, allow_nan=False))
        return
    if args.state_dir is None:
        parser.error("Explicit worker startup requires --state-dir")
    serve(args.checkpoint.resolve(strict=True), args.state_dir.resolve(strict=True), owner_pid=args.owner_pid)


if __name__ == "__main__":
    main()
