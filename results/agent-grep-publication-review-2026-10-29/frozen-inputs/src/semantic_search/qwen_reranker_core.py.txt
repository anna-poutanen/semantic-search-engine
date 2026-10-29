"""Pinned original B1 scoring primitives, promoted without model/input changes.

Importing this module loads no model and makes no network requests.
"""

import hashlib
import importlib
import importlib.machinery
import importlib.metadata
import math
from pathlib import Path
import re
import sys

MODEL_ID = "Qwen/Qwen3-Reranker-0.6B"
REVISION = "e61197ed45024b0ed8a2d74b80b4d909f1255473"
MAX_TOKENS = 8192
INSTRUCTION = (
    "Given a requested code behavior, retrieve executable code that directly "
    "implements the behavior or any necessary supporting part of that behavior. "
    "Names, comments, or related topics alone are insufficient."
)
PREFIX = (
    '<|im_start|>system\nJudge whether the Document meets the requirements based '
    'on the Query and the Instruct provided. Note that the answer can only be '
    '"yes" or "no".<|im_end|>\n<|im_start|>user\n'
)
SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
PINS = {
    "config.json": (727, "d479c427a9ca5295218063d4f9aca4f297ab4ac27487cca7af42c84643d51ef0"),
    "tokenizer_config.json": (9706, "253153d0738ceb4c668d2eff957714dd2bea0b56de772a9fdccd96cbf517e6a0"),
    "tokenizer.json": (11422654, "aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4"),
    "model.safetensors": (1191588280, "27cd75a405b9c1b46b59abfd88aaa209e6fed2a1972cde9b70e7659537c5e65b"),
}
RUNTIME_PINS = {"mlx": "0.32.3", "tokenizers": "0.23.3", "mlx-lm": "0.32.0"}
QWEN3_SOURCE_SHA = "7e29b7055d8e526a0ede6e2740a3038ad24d0cef6a1abb446c411d3fbd0efa2d"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def runtime_preflight():
    """Read distribution metadata and source bytes, without importing MLX."""
    packages = {}
    errors = []
    for name, expected in RUNTIME_PINS.items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual = None
        packages[name] = actual
        if actual != expected:
            errors.append(f"requires {name}=={expected}; found {actual or 'absent'}")
    source_sha = None
    source_path = None
    if packages["mlx-lm"] == RUNTIME_PINS["mlx-lm"]:
        path = importlib.metadata.distribution("mlx-lm").locate_file("mlx_lm/models/qwen3.py")
        source_path = str(Path(path).resolve(strict=True))
        source_sha = file_hash(Path(path))
        if source_sha != QWEN3_SOURCE_SHA:
            errors.append("mlx-lm Qwen3 source differs from inspected primary release")
    return {"packages": packages, "qwen3_source_sha256": source_sha,
            "qwen3_source_path": source_path, "errors": errors}


def verified_qwen3_module(runtime):
    """Refuse shadow packages before import, then verify the imported leaf too."""
    expected = Path(runtime["qwen3_source_path"]).resolve(strict=True)
    package_paths = [("mlx_lm", expected.parent.parent / "__init__.py"),
                     ("mlx_lm.models", expected.parent / "__init__.py"),
                     ("mlx_lm.models.qwen3", expected)]
    search = sys.path
    for name, path in package_paths:
        if name in sys.modules:
            actual = getattr(sys.modules[name], "__file__", None)
            locations = getattr(sys.modules[name], "__path__", None)
        else:
            spec = importlib.machinery.PathFinder.find_spec(name, search)
            actual = spec.origin if spec is not None else None
            locations = spec.submodule_search_locations if spec is not None else None
        if actual is None or Path(actual).resolve(strict=True) != path.resolve(strict=True):
            raise ValueError(f"shadowed runtime module origin: {name}")
        search = locations
    if file_hash(expected) != QWEN3_SOURCE_SHA:
        raise ValueError("Qwen3 implementation changed before import")
    module = importlib.import_module("mlx_lm.models.qwen3")
    actual = Path(module.__file__).resolve(strict=True)
    if actual != expected or file_hash(actual) != QWEN3_SOURCE_SHA:
        raise ValueError("imported Qwen3 implementation differs from pinned source")
    runtime["imported_qwen3_source_path"] = str(actual)
    runtime["imported_qwen3_source_sha256"] = QWEN3_SOURCE_SHA
    return module


def checkpoint_preflight(directory, *, verify_weights=False):
    """No weight bytes are opened unless the explicit run path requests it."""
    directory = Path(directory).resolve(strict=True)
    if not directory.is_dir():
        raise ValueError("checkpoint must be an existing local directory")
    verified = {}
    witnesses = {}
    for name, (size, expected) in PINS.items():
        path = directory / name
        if not path.is_file() or path.stat().st_size != size:
            raise ValueError(f"missing or wrong-size pinned checkpoint file: {name}")
        before = file_witness(path)
        if name == "model.safetensors" and not verify_weights:
            verified[name] = {"size": size, "sha256": None, "weight_bytes_read": False}
            witnesses[name] = before
            continue
        actual = file_hash(path)
        if actual != expected:
            raise ValueError(f"pinned checkpoint hash mismatch: {name}")
        if file_witness(path) != before:
            raise ValueError(f"checkpoint changed while verifying: {name}")
        witnesses[name] = before
        verified[name] = {"size": size, "sha256": actual}
    # Exact config/tokenizer hashes above reject custom architectures and Hub code.
    extras = list(directory.glob("*.safetensors"))
    if {path.name for path in extras} != {"model.safetensors"}:
        raise ValueError("only the original single BF16 weight file is accepted")
    return {"model_id": MODEL_ID, "revision": REVISION, "directory": str(directory),
            "files": verified, "weight_identity_verified": verify_weights,
            "file_witnesses": witnesses}


def file_witness(path):
    stat = path.stat()
    return [str(path.resolve(strict=True)), stat.st_dev, stat.st_ino,
            stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def verify_witnesses(identity):
    root = Path(identity["directory"])
    for name, witness in identity["file_witnesses"].items():
        if file_witness(root / name) != witness:
            raise ValueError(f"checkpoint changed after verification: {name}")


def physical_lines(source):
    """Only CR, CRLF and LF delimit source lines; Unicode separators remain literal."""
    return re.findall(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$", source)


class PairBuilder:
    """Injected tokenizer constructor does no I/O or model initialization."""

    def __init__(self, tokenizer, *, max_tokens=MAX_TOKENS):
        if not isinstance(max_tokens, int) or not 1 <= max_tokens <= MAX_TOKENS:
            raise ValueError("total token cap must be between 1 and 8192")
        self.tokenizer = tokenizer
        self.max_tokens = max_tokens
        self.prefix = self.encode(PREFIX)
        self.suffix = self.encode(SUFFIX)
        self.yes_id = self.encode("yes")
        self.no_id = self.encode("no")
        if len(self.yes_id) != 1 or len(self.no_id) != 1 or self.yes_id == self.no_id:
            raise ValueError("yes/no must be distinct single tokenizer tokens")

    def encode(self, text):
        result = self.tokenizer.encode(text, add_special_tokens=False)
        return list(result.ids if hasattr(result, "ids") else result)

    def build(self, query, source):
        if not isinstance(query, str) or not query.strip() or not isinstance(source, str) or not source:
            raise ValueError("nonempty query and source text required")
        # Strict Unicode encoding rejects lone surrogates; no file-cookie decoding.
        source_bytes = source.encode("utf-8")
        query.encode("utf-8")
        header = f"<Instruct>: {INSTRUCTION}\n<Query>: {query}\n<Document>: "
        header_tokens = len(self.prefix) + len(self.encode(header)) + len(self.suffix)
        lines = physical_lines(source)
        delivered = source
        ids = self.prefix + self.encode(header + delivered) + self.suffix
        # Tokenization can be non-monotone at joins; every selected exact prefix is
        # encoded and checked. Never decode a cut token sequence into invented text.
        count = len(lines)
        while len(ids) > self.max_tokens and count:
            count -= 1
            delivered = "".join(lines[:count])
            ids = self.prefix + self.encode(header + delivered) + self.suffix
        if not delivered:
            reason = ("instruction/query leaves no document token budget" if header_tokens >= self.max_tokens
                      else "no complete physical source line fits the token budget")
            raise ValueError(reason)
        return {
            "input_ids": ids, "source": delivered, "source_sha256": sha256(source_bytes),
            "delivered_sha256": sha256(delivered.encode("utf-8")),
            "source_lines": len(lines), "delivered_lines": count,
            "source_utf8_bytes": len(source_bytes),
            "delivered_utf8_bytes": len(delivered.encode("utf-8")),
            "truncated": delivered != source, "total_tokens": len(ids),
            "query_sha256": sha256(query.encode("utf-8")),
        }


def binary_score(yes_logit, no_logit):
    yes_logit, no_logit = float(yes_logit), float(no_logit)
    if not math.isfinite(yes_logit) or not math.isfinite(no_logit):
        raise ValueError("non-finite yes/no logits")
    difference = yes_logit - no_logit
    if not math.isfinite(difference):
        raise ValueError("non-finite logit difference")
    # Stable two-class softmax; no temperature, generation or decision threshold.
    probability = (1 / (1 + math.exp(-difference)) if difference >= 0
                   else math.exp(difference) / (1 + math.exp(difference)))
    return {"yes_logit": yes_logit, "no_logit": no_logit,
            "logit_difference": difference, "yes_rank_signal": probability}


def score_pair(model, pair, yes_id, no_id, array, evaluate=None):
    """One unpadded sequence, normalized backbone, then final-token projection."""
    inputs = array([pair["input_ids"]])
    hidden = model.model(inputs)
    if tuple(hidden.shape[:2]) != (1, len(pair["input_ids"])):
        raise ValueError("unexpected backbone shape")
    final = hidden[:, -1:, :]
    logits = (model.model.embed_tokens.as_linear(final) if model.args.tie_word_embeddings
              else model.lm_head(final))
    if tuple(logits.shape[:2]) != (1, 1):
        raise ValueError("head must project exactly one final token")
    if evaluate is not None:
        # Evaluate the shared final-token graph once before reading two scalars.
        evaluate(logits)
    return binary_score(logits[0, 0, yes_id].item(), logits[0, 0, no_id].item())
