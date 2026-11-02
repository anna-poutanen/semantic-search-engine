#!/usr/bin/env python3
"""Record one invented CPU backend request; never load real embedding weights."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import importlib.abc
import importlib.util
import json
from pathlib import Path
import socket
import sys
import tempfile
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="New attempt directory; its parent must already exist")
    parser.add_argument("--tokenizer-cache", type=Path, default=ROOT / ".cache/tokenizers")
    args = parser.parse_args()
    sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
    attempts = {"forbidden_model_imports": 0, "socket_attempts": 0}

    class DenyModels(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if any(fullname == name or fullname.startswith(name + ".") for name in
                   ("mlx", "mlx_embeddings", "torch", "transformers")):
                attempts["forbidden_model_imports"] += 1
                raise RuntimeError("Invented CPU check forbids model import: " + fullname)

    def deny_network(*args, **kwargs):
        attempts["socket_attempts"] += 1
        raise RuntimeError("Invented CPU check forbids socket connection/name resolution")

    def deny_checkpoint(*args, **kwargs):
        raise RuntimeError("Invented CPU check forbids a real checkpoint")

    guard = DenyModels()
    sys.meta_path.insert(0, guard)
    try:
        with patch.object(socket.socket, "connect", deny_network), \
             patch.object(socket, "create_connection", deny_network), \
             patch.object(socket, "getaddrinfo", deny_network):
            from audit_selection import offline_encoding, TOKENIZER_SHA
            from semantic_search import context
            from semantic_search.backend import SearchBackend
            from semantic_search.embeddings import QwenEmbedder

            fixture_path = ROOT / "tests/test_semantic_request_capture.py"
            spec = importlib.util.spec_from_file_location("invented_capture_fixture", fixture_path)
            fixture = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(fixture)
            counter = offline_encoding(args.tokenizer_cache.resolve())
            bindings = {"classification": "invented CPU end-to-end preparation; not real Qwen outputs",
                "fixture_source_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
                "readiness_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "tokenizer_ranks_sha256": TOKENIZER_SHA,
                "trusted_source_control_bindings": False}
            capture = fixture.SemanticRequestCapture(args.output_dir, bindings=bindings)
            with tempfile.TemporaryDirectory(prefix="semantic-live-cpu-", dir="/tmp") as temp, \
                 patch.object(QwenEmbedder, "_ensure_loaded", deny_checkpoint), \
                 patch.object(context, "encoding", return_value=counter), \
                 patch.object(fixture.capture_module, "encoding", return_value=counter):
                repo = Path(temp)
                (repo / "src").mkdir()
                (repo / "src/code.py").write_text("\n".join(
                    f"def invented_{number}():\n    return {number}\n" for number in range(1, 12)))
                embedder = fixture.InventedEmbedder()
                with closing(SearchBackend(repo, "src", repo / "invented.sqlite3", embedder=embedder,
                        research_postprocessor=capture.postprocessor,
                        research_forward_observer=capture.forward_observer)) as backend:
                    capture.run(backend, **fixture.KWARGS)
                audit = json.loads((capture.output_dir / "audit.json").read_text())
                assert audit["status"] == "passed"
                assert audit["forward_counts"]["reported_total_model_forward_calls"] == len(embedder.actual_forwards)
            assert not any(attempts.values())
            summary = {"classification": bindings["classification"], "passed": True,
                "guard_attempts": attempts,
                "guard_scope": "Specified model imports and socket entry points only; not a generic sandbox",
                "activity_scope": "Constructed source, CPU index and stub forwards; no observed benchmark or reserved corpus read",
                "actual_invented_forwards": len(embedder.actual_forwards),
                "actual_compact_tokens": audit["actual_compact_tokens"],
                "forward_counts": audit["forward_counts"], "independent_audit": True,
                "json_roundtrip_audit": True, "source_binding_unchanged": True}
            capture._write_json("readiness.json", summary)
            print(json.dumps(summary, sort_keys=True))
    finally:
        sys.meta_path.remove(guard)


if __name__ == "__main__":
    main()
