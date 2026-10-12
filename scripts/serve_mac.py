#!/usr/bin/env python3
"""Run the unmodified official shim and pinned MLX facade on loopback."""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8741)
    parser.add_argument("--facade-port", type=int, default=8743)
    args = parser.parse_args()
    model = ROOT / ".cache/models/h2o-lightning-4b-mlx-q8"
    if not (model / "model.safetensors").exists():
        raise SystemExit("Run python3 scripts/setup_mac.py first.")
    for port in (args.port, args.facade_port):
        import socket
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                raise SystemExit(f"Port {port} is already in use. Choose another port.")
    state_dir = ROOT / ".cache/server"
    state_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "SHIM_NOUL_FLOOR": "0", "FACADE_IDLE_UNLOAD_S": "0",
           "FACADE_PREFIX_MIN": "0", "TOKENIZERS_PARALLELISM": "false"}
    processes = []
    log_files = []

    def stop(_signum=None, _frame=None):
        for process in reversed(processes):
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()

    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    commands = [
        ("facade", [sys.executable,
                    ROOT / ".cache/vendor/h2o-lightning-mlx/mlx_vllm_facade.py",
                    "--model", model, "--host", "127.0.0.1", "--port", str(args.facade_port)]),
        ("shim", [sys.executable, model / "h2o_lightning_shim.py", "--config",
                  model / "serve_config.json", "--vllm", f"http://127.0.0.1:{args.facade_port}",
                  "--host", "127.0.0.1", "--port", str(args.port)]),
    ]
    try:
        for name, command in commands:
            log = (state_dir / f"{name}.log").open("w")
            log_files.append(log)
            processes.append(subprocess.Popen([str(v) for v in command], cwd=ROOT,
                                              env=env, stdout=log, stderr=subprocess.STDOUT,
                                              start_new_session=True))
        (state_dir / "processes.json").write_text(json.dumps({
            "pids": [p.pid for p in processes], "port": args.port,
            "facade_port": args.facade_port, "prefix_cache": False, "noul_floor": 0,
        }, indent=2) + "\n")
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if any(p.poll() is not None for p in processes):
                raise RuntimeError(f"Runtime exited. Inspect {state_dir}/*.log")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{args.port}/health", timeout=2):
                    print(f"Ready: http://127.0.0.1:{args.port}/v1/systemone", flush=True)
                    break
            except OSError:
                time.sleep(1)
        else:
            raise RuntimeError(f"Runtime did not become ready. Inspect {state_dir}/*.log")
        while all(p.poll() is None for p in processes):
            time.sleep(1)
        raise RuntimeError(f"Runtime exited. Inspect {state_dir}/*.log")
    except KeyboardInterrupt:
        pass
    finally:
        stop()
        for log in log_files:
            log.close()


if __name__ == "__main__":
    main()
