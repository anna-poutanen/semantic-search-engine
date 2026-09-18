#!/usr/bin/env python3
"""Opt-in bounded Qwen3 reranker batching; import and preflight do no inference.

Right padding leaves all real positions unchanged. The pinned native backbone's
causal mask prevents every retained real query from attending padded future keys.
Only each row's last real hidden state is projected; padded query rows are ignored.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import sys
import time

import prototype_qwen_reranker as prototype

BATCH_SIZES = (1, 2, 4, 8)
MAX_PADDED_TOKENS = 16_384
SUPPORTING_SOURCE_SHA256 = {
    "base.py": "b782b2ac0f55ed8fa9c1afce1f36307d5d8714aad2c0991d63998047f7507174",
    "rope_utils.py": "06466d57dfe1ccc1e61ba595c0a8f2dd3a14bcb5c6a1d06c67591dc03128606b",
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_configuration(batch_size, max_padded_tokens):
    require(type(batch_size) is int and batch_size in BATCH_SIZES, "Batch size must be 1, 2, 4 or 8")
    require(type(max_padded_tokens) is int and 1 <= max_padded_tokens <= MAX_PADDED_TOKENS,
            "Padded-token budget must be between 1 and 16384")


def validate_built_pair(pair):
    ids = pair.get("input_ids") if isinstance(pair, dict) else None
    require(isinstance(ids, list) and 1 <= len(ids) <= prototype.MAX_TOKENS,
            "Each built pair must contain between 1 and 8192 tokens")
    require(all(type(token) is int and token >= 0 for token in ids), "Token IDs must be nonnegative integers")


def plan_batches(pairs, batch_size, *, max_padded_tokens=MAX_PADDED_TOKENS):
    """Return original pair indexes, stably length-sorted, without dropping rows."""
    validate_configuration(batch_size, max_padded_tokens)
    require(isinstance(pairs, list) and 1 <= len(pairs) <= 50, "Expected one to fifty built pairs")
    for pair in pairs:
        validate_built_pair(pair)
        require(len(pair["input_ids"]) <= max_padded_tokens,
                "A sequence exceeds the padded-token budget; no sequence is dropped")
    ordered = (list(range(len(pairs))) if batch_size == 1 else
               sorted(range(len(pairs)), key=lambda index: (len(pairs[index]["input_ids"]), index)))
    groups, current = [], []
    for index in ordered:
        # Ascending lengths make the proposed last row the new padded length.
        proposed_tokens = (len(current) + 1) * len(pairs[index]["input_ids"])
        if current and (len(current) == batch_size or proposed_tokens > max_padded_tokens):
            groups.append(current)
            current = []
        current.append(index)
    if current:
        groups.append(current)
    return groups


def batch_layout(pairs, *, pad_id=0):
    """CPU-only layout metadata. The IDs at real positions are never altered."""
    require(isinstance(pairs, list) and 1 <= len(pairs) <= max(BATCH_SIZES), "Expected one to eight built pairs")
    require(type(pad_id) is int and pad_id >= 0, "Padding token ID must be a nonnegative integer")
    for pair in pairs:
        validate_built_pair(pair)
    lengths = [len(pair["input_ids"]) for pair in pairs]
    width = max(lengths)
    real_tokens = sum(lengths)
    allocated_tokens = len(pairs) * width
    require(allocated_tokens <= MAX_PADDED_TOKENS, "Forward exceeds the 16384 padded-token budget")
    return {"actual_batch_size": len(pairs), "sequence_lengths": lengths,
            "padded_sequence_length": width, "real_input_tokens": real_tokens,
            "allocated_input_tokens": allocated_tokens, "padding_tokens": allocated_tokens - real_tokens,
            "padding_ratio": (allocated_tokens - real_tokens) / allocated_tokens,
            "pad_token_id": pad_id, "padding_side": "right", "position_offset": 0,
            "attention_mask": "native_causal", "last_token_positions": [length - 1 for length in lengths],
            "singleton_original_path": len(pairs) == 1}


def score_batch(model, pairs, yes_id, no_id, array, evaluate=None, *, pad_id=0):
    """One native backbone forward, gathering only last real tokens for the head.

    No input/KV/result caching, left padding, offset manipulation, full-vocabulary
    sequence head, or sequence packing is used. Native compiled-kernel reuse is
    allowed for the warm runtime. A singleton takes
    the original unpadded scorer's exact code path, including final-token slicing.
    """
    layout = batch_layout(pairs, pad_id=pad_id)
    if len(pairs) == 1:
        return [prototype.score_pair(model, pairs[0], yes_id, no_id, array, evaluate)]
    width = layout["padded_sequence_length"]
    inputs = array([pair["input_ids"] + [pad_id] * (width - len(pair["input_ids"])) for pair in pairs])
    hidden = model.model(inputs)  # Native pinned backbone supplies causal mask and RoPE offset 0.
    require(tuple(hidden.shape[:2]) == (len(pairs), width), "Unexpected batched backbone shape")
    rows = array(list(range(len(pairs))))
    positions = array(layout["last_token_positions"])
    final = hidden[rows, positions, :][:, None, :]
    require(tuple(final.shape[:2]) == (len(pairs), 1), "Must gather exactly one last real token per row")
    logits = (model.model.embed_tokens.as_linear(final) if model.args.tie_word_embeddings else model.lm_head(final))
    require(tuple(logits.shape[:2]) == (len(pairs), 1), "Head must project only the selected final tokens")
    if evaluate is not None:
        evaluate(logits)
    return [prototype.binary_score(logits[index, 0, yes_id].item(), logits[index, 0, no_id].item())
            for index in range(len(pairs))]


def checked_scores(scores, count):
    require(isinstance(scores, list) and len(scores) == count, "Scorer must return one result per real pair")
    expected = {"yes_logit", "no_logit", "logit_difference", "yes_rank_signal"}
    for score in scores:
        require(isinstance(score, dict) and set(score) == expected, "Only four binary-score fields are accepted")
        require(all(type(value) in (int, float) and math.isfinite(value) for value in score.values()),
                "All score scalars must be finite numbers")
        calculated = prototype.binary_score(score["yes_logit"], score["no_logit"])
        require(score == calculated, "Score fields disagree with the original stable binary score math")
    return scores


class BatchScoringEngine:
    """Injected scorer/clock/receipt seam; no imports of model libraries here."""

    def __init__(self, builder, scorer, verify, emit, *, pad_id=0, clock=time.perf_counter):
        require(type(pad_id) is int and pad_id >= 0, "Padding token ID must be a nonnegative integer")
        self.builder, self.scorer, self.verify, self.emit = builder, scorer, verify, emit
        self.pad_id, self.clock = pad_id, clock
        self.requests = self.attempted = self.completed = 0
        self.attempted_pairs = self.completed_pairs = 0
        self.real_tokens = self.allocated_tokens = 0

    def counts(self):
        return {"requests": self.requests, "attempted_forwards": self.attempted,
                "completed_forwards": self.completed, "attempted_batch_forwards": self.attempted,
                "completed_batch_forwards": self.completed, "attempted_pairs": self.attempted_pairs,
                "completed_pairs": self.completed_pairs, "completed_real_input_tokens": self.real_tokens,
                "completed_allocated_input_tokens": self.allocated_tokens,
                "completed_padding_tokens": self.allocated_tokens - self.real_tokens}

    def counters(self):
        return self.counts()

    def score(self, request_id, pairs, *, batch_size=1, max_padded_tokens=MAX_PADDED_TOKENS, warmup=False):
        require(isinstance(request_id, str) and 0 < len(request_id) <= 200, "Invalid request identity")
        require(isinstance(pairs, list) and 1 <= len(pairs) <= 50, "Expected one to fifty literal pairs")
        validate_configuration(batch_size, max_padded_tokens)
        require(type(warmup) is bool, "Warmup flag must be boolean")
        for pair in pairs:
            require(isinstance(pair, dict) and set(pair) == {"query", "source"},
                    "Only literal query/source enter scorer")
        started = self.clock()
        self.verify()
        token_start = self.clock()
        built = [self.builder.build(pair["query"], pair["source"]) for pair in pairs]
        tokenization_s = self.clock() - token_start
        preparation_start = self.clock()
        groups = plan_batches(built, batch_size, max_padded_tokens=max_padded_tokens)
        layouts = [batch_layout([built[index] for index in group], pad_id=self.pad_id) for group in groups]
        preparation_s = self.clock() - preparation_start
        self.requests += 1
        results, receipts = [None] * len(pairs), []
        forward_s = 0.0
        for batch_index, (group, layout) in enumerate(zip(groups, layouts, strict=True)):
            identities = [{"pair_index": index, "query_sha256": built[index]["query_sha256"],
                           "source_sha256": built[index]["source_sha256"],
                           "delivered_sha256": built[index]["delivered_sha256"],
                           "input_ids_sha256": digest(built[index]["input_ids"]),
                           "total_tokens": len(built[index]["input_ids"]), "truncated": built[index]["truncated"],
                           "delivered_lines": built[index]["delivered_lines"]} for index in group]
            self.attempted += 1
            self.attempted_pairs += len(group)
            identity = {"request_id": request_id, "warmup": warmup, "batch_index": batch_index,
                        "batch_forward_number": self.attempted, "batch_size_requested": batch_size,
                        "max_padded_tokens": max_padded_tokens, "pairs": identities, "layout": layout}
            self.emit(identity | {"event": "started"})
            phase = self.clock()
            try:
                scores = checked_scores(self.scorer([built[index] for index in group]), len(group))
                elapsed = self.clock() - phase
            except BaseException as error:
                self.emit(identity | {"event": "failed", "elapsed_s": self.clock() - phase,
                                      "error_type": type(error).__name__, "error": str(error)})
                raise
            self.completed += 1
            self.completed_pairs += len(group)
            self.real_tokens += layout["real_input_tokens"]
            self.allocated_tokens += layout["allocated_input_tokens"]
            forward_s += elapsed
            receipt = identity | {"event": "completed", "elapsed_s": elapsed, "scores": scores}
            receipts.append(receipt)
            self.emit(receipt)
            for index, score in zip(group, scores, strict=True):
                results[index] = {key: value for key, value in built[index].items() if key not in ("input_ids", "source")} | score
        self.verify()
        return {"request_id": request_id, "pairs_sha256": digest(pairs), "results": results,
                "batch_size": batch_size, "batch_size_requested": batch_size, "max_padded_tokens": max_padded_tokens,
                "tokenization_s": tokenization_s, "batch_preparation_s": preparation_s,
                "forward_s": forward_s, "server_scoring_s": self.clock() - started,
                "batches": receipts, "batch_receipts": receipts, "counts": self.counts(), "warmup": warmup}


def runtime_preflight():
    """Inspect pinned supporting source bytes without importing native runtime."""
    result = prototype.runtime_preflight()
    supporting = {}
    if result["qwen3_source_path"] is not None:
        root = Path(result["qwen3_source_path"]).parent
        for name, expected in SUPPORTING_SOURCE_SHA256.items():
            path = root / name
            actual = prototype.file_hash(path) if path.is_file() else None
            supporting[name] = {"path": str(path), "sha256": actual}
            if actual != expected:
                result["errors"].append("Batching supporting source differs from inspected release: " + name)
    result["batching_supporting_sources"] = supporting
    return result


def verified_qwen3_module(runtime):
    for name, expected in SUPPORTING_SOURCE_SHA256.items():
        item = runtime["batching_supporting_sources"].get(name)
        require(item is not None and item["sha256"] == expected, "Supporting source preflight is incomplete: " + name)
        require(prototype.file_hash(Path(item["path"])) == expected, "Supporting source changed before import: " + name)
    qwen3 = prototype.verified_qwen3_module(runtime)
    for name, expected in SUPPORTING_SOURCE_SHA256.items():
        module = sys.modules.get("mlx_lm.models." + name[:-3])
        actual = getattr(module, "__file__", None)
        path = Path(runtime["batching_supporting_sources"][name]["path"]).resolve(strict=True)
        require(actual is not None and Path(actual).resolve(strict=True) == path,
                "Imported supporting source origin differs: " + name)
        require(prototype.file_hash(path) == expected, "Imported supporting source changed: " + name)
    return qwen3


def load_engine(checkpoint, emit):
    """Explicit caller only; original BF16 checkpoint and pinned local runtime."""
    started = time.perf_counter()
    runtime = runtime_preflight()
    require(not runtime["errors"], "; ".join(runtime["errors"]))
    identity = prototype.checkpoint_preflight(checkpoint, verify_weights=True)
    verified_s = time.perf_counter() - started
    qwen3 = verified_qwen3_module(runtime)
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
        engine = BatchScoringEngine(builder, lambda pairs, resident_model=model: score_batch(
            resident_model, pairs, builder.yes_id[0], builder.no_id[0], mx.array, mx.eval),
            lambda: prototype.verify_witnesses(identity), emit)
        provenance = {"model": identity, "runtime": runtime, "instruction": prototype.INSTRUCTION,
                      "max_total_tokens": prototype.MAX_TOKENS, "batch_sizes": list(BATCH_SIZES),
                      "max_padded_tokens": MAX_PADDED_TOKENS, "padding_side": "right", "position_offset": 0,
                      "attention_mask": "native_causal", "cache": False,
                      "length_sort": "B1_original_order_else_ascending_then_original_index",
                      "verification_s": verified_s, "load_s": load_s,
                      "packages": dict(sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions()))}
        return engine, provenance, (model, weights, mx)
    except BaseException:
        del model, weights
        mx.clear_cache()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path)
    args = parser.parse_args(argv)
    result = {"runtime": runtime_preflight(), "model_calls": 0, "weights_loaded": False,
              "network": False, "batch_sizes": list(BATCH_SIZES), "max_padded_tokens": MAX_PADDED_TOKENS,
              "batch_module_sha256": prototype.file_hash(Path(__file__))}
    if args.checkpoint is not None:
        result["checkpoint"] = prototype.checkpoint_preflight(args.checkpoint)
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
