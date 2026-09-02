"""Pinned, unquantized Qwen3 embeddings on Apple Silicon.

The query formatting, last-token pooling, and 8192-token example follow
https://huggingface.co/Qwen/Qwen3-Embedding-0.6B . Documents get no instruction.
MLX is imported and the checkpoint loaded only on the first nonempty request.
"""
from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path

import numpy as np

MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
MODEL_REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
RUNTIME_VERSION = "0.1.0"
MAX_TOKENS = 8192
DIMENSIONS = 1024
QUERY_INSTRUCTION = (
    "Given a description of code behavior, retrieve the code implementation that performs it."
)
QUERY_PROMPT = f"Instruct: {QUERY_INSTRUCTION}\nQuery:"
PROVENANCE_FILE = "semantic_search_checkpoint.json"
DEFAULT_MODEL_PATH = (
    Path(__file__).resolve().parents[2] / ".cache/models/qwen3-embedding-0.6b"
)


def normalize_rows(vectors: np.ndarray) -> np.ndarray:
    """Return finite unit vectors in float32; reject unusable model outputs."""
    vectors = np.asarray(vectors, dtype=np.float32)
    if vectors.ndim != 2 or not np.isfinite(vectors).all():
        raise ValueError("Embedding output must be a finite matrix")
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if np.any(norms == 0):
        raise ValueError("Model returned a zero embedding")
    return vectors / norms


def last_token_pool(hidden: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Pool the last valid token with either left or right padding.

    This CPU helper also makes padding behavior testable without loading MLX.
    The model path applies the same indexing before materializing on the CPU.
    """
    hidden = np.asarray(hidden)
    mask = np.asarray(mask)
    if hidden.ndim != 3 or mask.shape != hidden.shape[:2]:
        raise ValueError("Hidden states and attention mask have incompatible shapes")
    if mask.shape[1] == 0 or np.any(mask.sum(axis=1) == 0):
        raise ValueError("Every embedding input must contain at least one token")
    positions = np.where(mask != 0, np.arange(mask.shape[1]), -1).max(axis=1)
    return hidden[np.arange(hidden.shape[0]), positions]


class QwenEmbedder:
    def __init__(self, model_path=None, batch_size=4, progress=True):
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
            raise ValueError("batch_size must be a positive integer")
        self.model_path = Path(model_path) if model_path is not None else DEFAULT_MODEL_PATH
        self.batch_size = batch_size
        self.progress = progress
        self._model = None
        self._tokenizer = None
        self._mx = None
        self._checkpoint_verified = False

    @property
    def metadata(self):
        versions = {}
        for package in ("mlx-embeddings", "mlx", "mlx-metal", "transformers", "numpy"):
            try:
                versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                versions[package] = None
        return {
            "id": MODEL_ID, "revision": MODEL_REVISION,
            "weights_dtype": "bfloat16", "quantized": False,
            "runtime": "mlx-embeddings", "runtime_version": RUNTIME_VERSION,
            "packages": versions, "dimensions": DIMENSIONS,
            "query_instruction": QUERY_INSTRUCTION, "query_prompt": QUERY_PROMPT,
            "document_prompt": None, "pooling": "last non-padding token",
            "normalization": "L2, float32", "max_tokens": MAX_TOKENS,
            "truncation": "right", "padding_side": "right",
            "add_special_tokens": True, "batch_size": self.batch_size,
            "batch_order": "stable ascending token length; restore input order",
            "checkpoint_validation": "pinned Hub download provenance; original unquantized config",
        }

    def _ensure_loaded(self):
        if self._model is not None:
            return
        provenance = self.model_path / PROVENANCE_FILE
        if not provenance.is_file():
            raise FileNotFoundError(
                f"Pinned embedding checkpoint missing at {self.model_path}. "
                "Run .venv/bin/python scripts/setup_embeddings.py first."
            )
        record = json.loads(provenance.read_text())
        if record.get("id") != MODEL_ID or record.get("revision") != MODEL_REVISION:
            raise ValueError("Embedding checkpoint provenance does not match the pinned model")
        config = json.loads((self.model_path / "config.json").read_text())
        if (config.get("model_type") != "qwen3" or config.get("hidden_size") != DIMENSIONS
                or config.get("quantization") or config.get("quantization_config")):
            raise ValueError("Expected original, unquantized Qwen3-Embedding-0.6B weights")
        if importlib.metadata.version("mlx-embeddings") != RUNTIME_VERSION:
            raise ValueError("Unexpected mlx-embeddings version; rerun scripts/setup_embeddings.py")
        import mlx.core as mx
        from mlx_embeddings import load

        self._model, self._tokenizer = load(
            str(self.model_path),
            tokenizer_config={"padding_side": "right", "truncation_side": "right"},
        )
        self._mx = mx
        self._checkpoint_verified = True

    def _embed_batch(self, token_ids):
        mx = self._mx
        # Right padding retains every real token's position and avoids fully
        # masked causal attention rows for leading pad tokens in this runtime.
        batch = self._tokenizer.pad({"input_ids": token_ids}, padding=True,
                                    return_attention_mask=True, return_tensors="np")
        mask = np.asarray(batch["attention_mask"])
        positions = mask.sum(axis=1).astype(np.int32) - 1
        outputs = self._model(mx.array(batch["input_ids"]), mx.array(mask))
        pooled = outputs.last_hidden_state[mx.arange(len(token_ids)), mx.array(positions)]
        pooled = pooled.astype(mx.float32)
        mx.eval(pooled)
        vectors = normalize_rows(np.asarray(pooled))
        del outputs, pooled
        return vectors

    def _embed_texts(self, texts, *, cancel_check=None):
        if not isinstance(texts, list) or any(not isinstance(t, str) or not t.strip() for t in texts):
            raise ValueError("Embedding inputs must be a list of nonempty strings")
        if not texts:
            return np.empty((0, DIMENSIONS), dtype=np.float32)
        self._ensure_loaded()
        encoded = self._tokenizer(texts, padding=False, truncation=True,
                                  max_length=MAX_TOKENS, add_special_tokens=True)
        ids = encoded["input_ids"]
        if any(not tokens for tokens in ids):
            raise ValueError("Every embedding input must contain at least one token")
        order = sorted(range(len(ids)), key=lambda i: (len(ids[i]), i))
        output = np.empty((len(texts), DIMENSIONS), dtype=np.float32)
        reported = 0
        for start in range(0, len(order), self.batch_size):
            if cancel_check is not None:
                cancel_check()
            indices = order[start:start + self.batch_size]
            vectors = self._embed_batch([ids[i] for i in indices])
            if vectors.shape != (len(indices), DIMENSIONS):
                raise ValueError(f"Unexpected Qwen embedding shape: {vectors.shape}")
            output[indices] = vectors
            # Token-length batches otherwise accumulate unused Metal allocations
            # across many shapes. Keep weights resident, release batch buffers.
            if len(texts) > 1 and self._mx is not None:
                self._mx.clear_cache()
            if cancel_check is not None:
                cancel_check()
            completed = start + len(indices)
            if (self.progress and len(texts) > 1
                    and (completed - reported >= 32 or completed == len(texts))):
                print(f"Embedded {completed}/{len(texts)} documents", flush=True)
                reported = completed
        return output

    def embed_documents(self, texts: list[str], *, cancel_check=None) -> np.ndarray:
        try:
            return self._embed_texts(texts, cancel_check=cancel_check)
        finally:
            if self._mx is not None:
                self._mx.clear_cache()

    def embed_query(self, query: str) -> np.ndarray:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Query must be a nonempty string")
        return self._embed_texts([QUERY_PROMPT + query])[0]

    def close(self):
        """Release embedding weights before running another GPU experiment."""
        self._model = self._tokenizer = None
        if self._mx is not None:
            self._mx.clear_cache()
        self._mx = None
        self._checkpoint_verified = False
