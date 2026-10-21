#!/usr/bin/env python3
"""Freeze then compare one qualified context selector on real resident Qwen calls.

Freeze reads source/checkpoint bytes and metadata only. Native model/index work
is confined to --run, after all bindings and the plan are committed unchanged.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import time
from unittest.mock import patch

import benchmark_multifile as shared
import replay_selection as replay
from semantic_search import context, selection
from semantic_search.context_backfill import coalesce_context
from semantic_search.corpus import extract_snippets

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = dict(replay.SETTINGS)
CONDITIONS = dict(replay.CONDITIONS)
RETRIEVALS = ("dense", "hybrid")
REPETITIONS = 3
WARM_QUERY = "Find code that handles input and returns a result"
GATE = {"minimum_net_multi_file_complete_gain": 1, "maximum_lost_groups": 0,
        "maximum_lost_complete_positives": 0, "maximum_lost_single_file_first_hits": 0,
        "maximum_median_wall_ratio": 1.25, "promotion_mode": "dense",
        "hybrid_role": "separate diagnostic control; cannot rescue dense"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                       separators=(",", ":")) + "\n").encode()


def read(path):
    return json.loads(path.read_bytes())


def write(path, value):
    with path.open("xb") as stream:
        stream.write(canonical(value))


def relative(path, root=None):
    root = ROOT if root is None else root
    path = Path(path).resolve()
    require(path.is_relative_to(root.resolve()), "Bound artifact must be inside the project: " + str(path))
    return path.relative_to(root.resolve()).as_posix()


def hooks(condition):
    require(condition in CONDITIONS, "Unknown frozen selector condition")
    return (getattr(selection, CONDITIONS[condition]),
            coalesce_context if condition.endswith("_coverage_backfill") else selection.unchanged_context)


def parent_binding(args):
    if args.development_study is None:
        require(args.role != "repository_holdout", "Fresh holdout requires a qualified committed development parent")
        return None
    folder = args.development_study
    summary = read(folder / "summary.json")
    require(summary.get("qualification", {}).get(args.candidate, {}).get("qualified") is True,
            "Candidate did not qualify in the frozen development replay")
    require(summary.get("model_calls") == summary.get("retrieval_calls") == 0
            and summary.get("baseline_exact_rows") == 337,
            "Development parent must retain all exact controls without model/retrieval calls")
    plan = read(folder / "frozen-plan.json")
    require(summary["plan_sha256"] == file_sha(folder / "frozen-plan.json")
            and summary["replay_sha256"] == file_sha(folder / "replay.jsonl.gz")
            and plan["conditions"] == CONDITIONS and plan["settings"] == SETTINGS,
            "Development parent artifact/configuration mismatch")
    names = ("frozen-plan.json", "baseline-gate.json", "control-gate.json", "summary.json", "replay.jsonl.gz")
    return {"artifacts": {relative(folder / name): file_sha(folder / name) for name in names},
            "qualification": summary["qualification"][args.candidate],
            "condition_description": plan["selection"][args.candidate],
            "exposure": "All parent retrieval/reranker outcomes are observed development evidence."}


def checkpoint_binding(model):
    from semantic_search.embeddings import DIMENSIONS, MODEL_ID, MODEL_REVISION, PROVENANCE_FILE
    provenance = read(model / PROVENANCE_FILE)
    config = read(model / "config.json")
    require(provenance.get("id") == MODEL_ID and provenance.get("revision") == MODEL_REVISION,
            "Checkpoint provenance differs from the pinned embedding model")
    require(config.get("model_type") == "qwen3" and config.get("hidden_size") == DIMENSIONS
            and not config.get("quantization") and not config.get("quantization_config"),
            "Original unquantized Qwen checkpoint required")
    files = sorted(path for path in model.rglob("*") if path.is_file()
                   and ".cache" not in path.relative_to(model).parts)
    require(any(path.suffix == ".safetensors" for path in files), "Checkpoint has no weight files")
    return {path.relative_to(model).as_posix(): file_sha(path) for path in files}


def identity(args):
    """No search, embedding, index or model loading is allowed in this function."""
    require(args.repetitions == REPETITIONS, "Exactly three rotated repetitions are predeclared")
    require(args.candidate in CONDITIONS, "Unknown selector condition")
    data = read(args.dataset)
    for query in data["queries"]:
        require(query.get("kind") in ("present", "absent") and isinstance(query.get("query"), str)
                and query["query"].strip(), "Malformed query")
        replay.groups(query)  # Reject positive evidence accidentally attached to an absent query.
    data, sources, inventory, binding = shared.identity(args)
    source_files = {name: {"bytes": len((args.corpus / name).read_bytes()), "sha256": digest}
                    for name, digest in binding["source_sha256"].items()}
    if "source_files" in data:
        require(source_files == data["source_files"]
                and sha(canonical(source_files)) == data["source_inventory_sha256"],
                "Complete source membership/bytes differ from the authored inventory")
    for query in data["queries"]:
        for alternatives in replay.groups(query):
            for target in alternatives:
                low, high = target["evidence_lines"]
                require(type(low) is type(high) is int
                        and 1 <= low <= high <= len(shared.physical_lines(sources[target["path"]])),
                        "Evidence interval must be complete inclusive physical lines")
    definition_keys = {}
    actual = extract_snippets(args.corpus, data["source_root"], 160, 40, sources=sources,
                              source_exclusions=data.get("source_exclusions", ()),
                              definition_keys=definition_keys)
    require([asdict(item) for item in actual] == inventory
            and len(definition_keys) == len(inventory), "Definition metadata changed the historical snippet inventory")
    with patch.dict(os.environ, {"TIKTOKEN_CACHE_DIR": str(args.tokenizer_cache)}):
        tokenizer, _ = replay.runtime(ROOT)
    binding.update(retrieval=list(RETRIEVALS), candidate=args.candidate,
                   arms={"baseline": "legacy", "candidate": args.candidate},
                   dataset_path=relative(args.dataset),
                   source_review_path=relative(args.source_review) if args.source_review else None,
                   source_files=source_files, definition_keys_sha256=sha(canonical(definition_keys)),
                   model_files_sha256=checkpoint_binding(args.model),
                   tokenizer=tokenizer, development_parent=parent_binding(args), gate=GATE,
                   exposure={"role": args.role, "labels_and_source_only_before_fresh_outcomes": args.role == "repository_holdout",
                             "freshness_limit": "Repository evaluation independence within this project; no model-pretraining isolation claim."})
    extras = ["scripts/benchmark_selection.py", "tests/test_selection_benchmark.py", "scripts/replay_selection.py",
              "docs/selection-native-protocol.md", "docs/selection-development-protocol.md", "pyproject.toml",
              "requirements-mac.lock", binding["dataset_path"]]
    if args.source_review:
        extras.append(binding["source_review_path"])
    if data.get("source_review_protocol"):
        extras.append(data["source_review_protocol"])
    for name in extras:
        binding["implementation_sha256"][name] = file_sha(ROOT / name)
    return data, sources, {"inventory": inventory, "definition_keys": definition_keys}, binding


def freeze(args):
    require(not args.output.exists(), "Use a new freeze directory")
    data, _, inventory, binding = identity(args)
    args.output.mkdir(parents=True)
    write(args.output / "source-inventory.json", inventory)
    plan = {"version": 1, "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
            "frozen_from_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "classification": args.role, "binding": binding, "quality_repetition": 1,
            "candidate_reason": "One development-qualified condition fixed before fresh outcomes; "
                "retain its declared dependency allocation. Qualification applies to the full condition, "
                "not an independent causal claim for definition identity or coverage.",
            "queries": [{"id": query["id"], "query": query["query"], "kind": query["kind"]} for query in data["queries"]],
            "artifacts": {"source-inventory.json": file_sha(args.output / "source-inventory.json")},
            "schedule": "For each query and repetition, rotate baseline/dense,candidate/dense,baseline/hybrid,candidate/hybrid by (query_index+repetition-1)%4.",
            "timing": "Actual monotonic wall time around each resident backend.search, including fresh query embedding; setup/warmup and receipt serialization excluded. Lightweight hook capture is included equally in both arms.",
            "labels": "Selectors/postprocessors receive source, query, caps, graph and ephemeral identity only; evidence is scored after the native response returns.",
            "execution": "Commit unchanged producer/test/protocol/dataset/review/parent files, source inventory and this plan. One claimed attempt; no resume or overwrite.",
            "limitations": "Small source-authored sample; source-complete coverage is not coding correctness, billing, transport latency or demonstrated agent-token savings."}
    write(args.output / "plan.json", plan)
    return plan


def committed(paths):
    """Verify each bound file is unchanged in the working tree, index and HEAD."""
    for name, expected in paths.items():
        actual = file_sha(ROOT / name)
        head = subprocess.check_output(["git", "show", "HEAD:" + name], cwd=ROOT)
        require(actual == sha(head) == expected, "Uncommitted or changed frozen file: " + name)
    status = subprocess.check_output(["git", "status", "--porcelain", "--", *paths], cwd=ROOT, text=True)
    require(not status.strip(), "Bound files have staged or unstaged changes")


def verify_plan(args, binding):
    plan = read(args.output / "plan.json")
    require(plan["version"] == 1 and plan["binding"] == binding,
            "Source, labels, parent, model, code or configuration changed after freeze")
    paths = dict(binding["implementation_sha256"])
    if binding["development_parent"]:
        paths.update(binding["development_parent"]["artifacts"])
    for name, digest in plan["artifacts"].items():
        paths[relative(args.output / name)] = digest
    paths[relative(args.output / "plan.json")] = file_sha(args.output / "plan.json")
    committed(paths)
    return plan


def schedule(queries):
    modes = (("baseline", "dense"), ("candidate", "dense"),
             ("baseline", "hybrid"), ("candidate", "hybrid"))
    for repetition in range(1, REPETITIONS + 1):
        for number, query in enumerate(queries):
            offset = (number + repetition - 1) % len(modes)
            for arm, retrieval in modes[offset:] + modes[:offset]:
                yield repetition, query, arm, retrieval


def pair_rows(pairs):
    result = [{"snippet": asdict(item), "score": float(score)} for item, score in pairs]
    require(all(math.isfinite(row["score"]) for row in result), "Non-finite ranked score")
    return result


@contextmanager
def capture(condition):
    """Capture live stages without replaying legacy dependency assumptions."""
    selector, postprocessor = hooks(condition)
    state = {"backfill_candidates": []}

    def selected(pairs, **kwargs):
        result = selector(pairs, **kwargs)
        state.update(ranked=list(pairs), selected=list(result[0]), eligible=list(result[1]),
                     origins=dict(result[2]), definition_keys=kwargs["definition_keys"],
                     ambiguous_symbols=kwargs.get("ambiguous_symbols"))
        return result

    def postprocessed(response, candidates, **kwargs):
        state["pre_postprocess_response"] = response
        def recorded():
            for candidate in candidates:
                state["backfill_candidates"].append(candidate)
                yield candidate
        state["candidate_iterator"] = recorded()
        return postprocessor(response, state["candidate_iterator"], **kwargs)

    with patch("semantic_search.backend.select_context", selected), \
            patch("semantic_search.backend.postprocess_context", postprocessed):
        yield state


def checked_response(response, sources, inventory):
    shared.checked_response(response, sources)
    require(response["token_usage"]["encoding"] == context.ENCODING
            and response["token_usage"]["budget"] == SETTINGS["token_budget"], "Token encoding/budget metadata differs")
    require(len({row["id"] for row in response["results"]}) == len(response["results"]), "Duplicate delivered ID")
    by_id = {item["id"]: item for item in inventory}
    for row in response["results"]:
        full = by_id[row["id"]]
        require(all(row[key] == full[key] for key in ("path", "symbol", "start_line", "content_hash"))
                and row["window_end_line"] == full["end_line"] >= row["end_line"]
                and row["truncated"] == (row["source"] != full["text"]), "Delivered window/hash/truncation identity differs")
        require(row.get("origin") in ("retrieval", "dependency")
                and (row.get("related_to") is None if row["origin"] == "retrieval"
                     else row.get("related_to") in by_id), "Invalid dependency provenance")


def receipt(query, repetition, retrieval, arm, condition, response, captured, elapsed, vector):
    """Called after the search timer stops; only this reporting step reads labels."""
    list(captured.pop("candidate_iterator"))  # Drain unconsumed rows outside the measured call.
    stages = {name: pair_rows(captured[name]) for name in ("ranked", "selected", "eligible")}
    stages["origins"] = deepcopy(captured["origins"])
    stages["definition_keys"] = deepcopy(captured["definition_keys"])
    stages["ambiguous_symbols"] = sorted(captured["ambiguous_symbols"]) if captured["ambiguous_symbols"] is not None else None
    text = context.serialize_context(context.compact_result(response))
    gold = replay.groups(query)
    ranked = [row["snippet"] for row in stages["ranked"]]
    require(len(ranked) <= SETTINGS["candidates"] and len({row["id"] for row in ranked}) == len(ranked),
            "Ranked candidate depth/identity differs")
    require(math.isfinite(elapsed) and elapsed > 0, "Measured wall time must be finite and positive")
    return {"query_id": query["id"], "kind": query["kind"], "repetition": repetition,
            "retrieval": retrieval, "arm": arm, "condition": condition, "elapsed_backend_s": elapsed,
            "multi_file": shared.is_multifile(gold), "candidate_ranks": shared.ranks(ranked, gold),
            "delivered_ranks": shared.ranks(response["results"], gold), "stages": stages,
            "pre_postprocess_response": deepcopy(captured["pre_postprocess_response"]),
            "backfill_candidates": deepcopy(captured["backfill_candidates"]), "response": deepcopy(response),
            "compact_payload": text, "compact_sha256": sha(text.encode()),
            "actual_compact_tokens": len(context.encoding().encode(text, disallowed_special=())),
            "query_embedding_calls": 1, "query_vector": vector.tolist(), "query_vector_sha256": sha(vector.tobytes())}


def summarize(rows):
    result = {}
    for retrieval in RETRIEVALS:
        mode = [row for row in rows if row["retrieval"] == retrieval]
        first = {arm: {row["query_id"]: row for row in mode if row["repetition"] == 1 and row["arm"] == arm}
                 for arm in ("baseline", "candidate")}
        gains, losses, complete_losses, first_losses, group_gains = [], [], [], [], []
        multi_gain = multi_loss = 0
        for identifier, baseline in first["baseline"].items():
            candidate = first["candidate"][identifier]
            if baseline["kind"] == "absent":
                continue
            before, after = baseline["delivered_ranks"], candidate["delivered_ranks"]
            newly_complete, lost_complete = None in before and None not in after, None not in before and None in after
            if newly_complete:
                gains.append(identifier)
                multi_gain += bool(baseline["multi_file"])
            if lost_complete:
                complete_losses.append(identifier)
                multi_loss += bool(baseline["multi_file"])
            for number, (prior, current) in enumerate(zip(before, after), 1):
                item = {"query_id": identifier, "group": number, "baseline_rank": prior, "candidate_rank": current}
                if prior is not None and current is None:
                    losses.append(item)
                if prior is None and current is not None:
                    group_gains.append(item)
            if not baseline["multi_file"] and before and before[0] is not None and after[0] is None:
                first_losses.append(identifier)
        metrics = {}
        for arm in ("baseline", "candidate"):
            arm_rows = [row for row in mode if row["arm"] == arm]
            times = sorted(row["elapsed_backend_s"] for row in arm_rows)
            positives = [row for row in first[arm].values() if row["kind"] == "present"]
            metrics[arm] = {"measured_calls": len(times), "median_backend_s": statistics.median(times),
                "p95_backend_s": times[max(0, math.ceil(.95 * len(times)) - 1)],
                "present_queries": len(positives), "multi_file_queries": sum(row["multi_file"] for row in positives),
                "complete_positives": sum(None not in row["delivered_ranks"] for row in positives),
                "multi_file_complete": sum(row["multi_file"] and None not in row["delivered_ranks"] for row in positives),
                "covered_groups": sum(sum(rank is not None for rank in row["delivered_ranks"]) for row in positives),
                "absent_inventory": [{"query_id": row["query_id"], "match_status": row["response"]["match_status"],
                                      "excerpt_ids": [item["id"] for item in row["response"]["results"]]}
                                     for row in first[arm].values() if row["kind"] == "absent"],
                "coalesced_rows": sum(len(row["response"].get("_selection_backfill", {}).get("coalesced_ids", [])) for row in first[arm].values()),
                "appended_rows": sum(len(row["response"].get("_selection_backfill", {}).get("appended_ids", [])) for row in first[arm].values())}
        ratio = metrics["candidate"]["median_backend_s"] / metrics["baseline"]["median_backend_s"]
        base_times = {(row["query_id"], row["repetition"]): row["elapsed_backend_s"] for row in mode if row["arm"] == "baseline"}
        paired = [row["elapsed_backend_s"] - base_times[(row["query_id"], row["repetition"])] for row in mode if row["arm"] == "candidate"]
        result[retrieval] = {"arms": metrics, "complete_gains": gains, "complete_losses": complete_losses,
            "group_gains": group_gains, "group_losses": losses, "single_file_first_hit_losses": first_losses,
            "net_multi_file_complete_gain": multi_gain - multi_loss, "median_wall_ratio": ratio,
            "median_paired_wall_delta_s": statistics.median(paired),
            "passed": multi_gain - multi_loss >= 1 and not losses and not complete_losses and not first_losses and ratio <= 1.25}
    return {"per_retrieval": result, "dense_promotion_gate_passed": result["dense"]["passed"],
            "automatic_default_change": False}


def run(args):
    # Claim an attempt before model/index work. Even a setup failure cannot resume.
    require(not any((args.output / name).exists() for name in
                    ("run-started.json", "searches.jsonl.gz", "summary.json", "failure.json")), "Attempt already exists; use a new frozen directory")
    data, sources, inventory, binding = identity(args)
    plan = verify_plan(args, binding)
    write(args.output / "run-started.json", {"started_at_utc": datetime.now(timezone.utc).isoformat(),
          "plan_sha256": file_sha(args.output / "plan.json"),
          "execution_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()})
    rows, phase = [], "setup"
    try:
        from semantic_search.backend import SearchBackend
        from semantic_search.embeddings import QwenEmbedder
        class RecordingEmbedder(QwenEmbedder):
            query_calls = document_calls = 0
            def embed_query(self, query):
                value = super().embed_query(query)
                self.query_calls += 1
                self.last_query = value.copy()
                return value
            def embed_documents(self, texts, *, cancel_check=None, progress_callback=None, batch_callback=None):
                self.document_calls += 1
                return super().embed_documents(texts, cancel_check=cancel_check,
                    progress_callback=progress_callback, batch_callback=batch_callback)
        rank_bytes = (args.tokenizer_cache / replay.CACHE_KEY).read_bytes()
        require(sha(rank_bytes) == replay.CACHE_SHA256, "Tokenizer bytes changed")
        import tiktoken.load
        def local_rank_bytes(url, expected_hash=None):
            require(url == replay.CACHE_URL and expected_hash == replay.CACHE_SHA256, "Unexpected tokenizer resource")
            return rank_bytes
        with patch.object(tiktoken.load, "read_file_cached", local_rank_bytes):
            context.encoding()
        embedder = RecordingEmbedder(args.model, progress=True)
        db = ROOT / ".cache/indexes" / ("selection-" + sha(relative(args.output).encode())[:20] + ".sqlite3")
        setup_started = time.perf_counter()
        with closing(SearchBackend(args.corpus, data["source_root"], db, embedder=embedder,
                                   source_exclusions=data.get("source_exclusions", ()))) as backend:
            for condition in ("legacy", args.candidate):
                selector, postprocessor = hooks(condition)
                with patch("semantic_search.backend.select_context", selector), patch("semantic_search.backend.postprocess_context", postprocessor):
                    for retrieval in RETRIEVALS:
                        backend.search(WARM_QUERY, retrieval=retrieval, **SETTINGS)
            setup = {"index_model_and_explicit_warmup_s": time.perf_counter() - setup_started,
                     "query_calls": embedder.query_calls, "document_calls": embedder.document_calls,
                     "status": backend.status()}
            require(backend._definition_keys == {key: tuple(value) for key, value in inventory["definition_keys"].items()}, "Live definition map differs from freeze")
            phase = "measured_calls"
            rankings, payloads = {}, {}
            with gzip.open(args.output / "searches.jsonl.gz", "xt", encoding="utf-8") as stream:
                for repetition, query, arm, retrieval in schedule(data["queries"]):
                    condition = "legacy" if arm == "baseline" else args.candidate
                    before, documents = embedder.query_calls, embedder.document_calls
                    with capture(condition) as captured:
                        started = time.perf_counter()
                        response = backend.search(query["query"], retrieval=retrieval, **SETTINGS)
                        elapsed = time.perf_counter() - started
                    require(embedder.query_calls == before + 1 and embedder.document_calls == documents,
                            "Measured requests need exactly one fresh query embedding and no document work")
                    checked_response(response, sources, inventory["inventory"])
                    checked_response(captured["pre_postprocess_response"], sources, inventory["inventory"])
                    shared.checked_candidates([asdict(item) for item, _ in captured["ranked"]], sources)
                    by_id = {item["id"]: item for item in inventory["inventory"]}
                    for stage in ("ranked", "selected", "eligible"):
                        require(all(asdict(item) == by_id.get(item.id) for item, _ in captured[stage]),
                                "Live stage window differs from frozen inventory")
                    if condition.endswith("_coverage_backfill"):
                        replay.require_source_preserved(captured["pre_postprocess_response"], response, shared.physical_lines)
                    outcome = receipt(query, repetition, retrieval, arm, condition, response, captured, elapsed, embedder.last_query)
                    rows.append(outcome)
                    stream.write(json.dumps(outcome, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
                    stream.flush()
                    rank_key, payload_key = (query["id"], retrieval), (query["id"], retrieval, arm)
                    fingerprint = sha(canonical(outcome["stages"]["ranked"]))
                    require(rankings.setdefault(rank_key, fingerprint) == fingerprint, "Ranked snippets/scores changed across paired arms or repetitions")
                    require(payloads.setdefault(payload_key, outcome["compact_sha256"]) == outcome["compact_sha256"], "Actual compact payload changed across repetitions")
                    print(f"r{repetition} {query['id']} {retrieval} {arm} {elapsed:.3f}s", flush=True)
        phase = "final_bindings"
        require(identity(args)[3] == binding, "Frozen bindings changed during native execution")
        verify_plan(args, binding)
        metrics = summarize(rows)
        write(args.output / "summary.json", {"complete": True, "classification": args.role,
            "candidate": args.candidate, "requests": len(rows), "unique_queries": len(data["queries"]),
            "quality_repetition": 1, "setup": setup, "gate": plan["binding"]["gate"], "metrics": metrics,
            "ranked_repeat_changes": 0, "compact_repeat_changes": 0,
            "measured_query_embeddings": len(rows), "total_query_embeddings": embedder.query_calls,
            "raw_sha256": file_sha(args.output / "searches.jsonl.gz"),
            "plan_sha256": file_sha(args.output / "plan.json")})
        return metrics
    except BaseException as error:
        write(args.output / "failure.json", {"phase": phase, "completed_requests": len(rows),
              "error_type": type(error).__name__, "error": str(error),
              "plan_sha256": file_sha(args.output / "plan.json")})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "corpus", "model", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--candidate", choices=tuple(CONDITIONS), required=True)
    parser.add_argument("--source-review", type=Path)
    parser.add_argument("--development-study", type=Path)
    parser.add_argument("--role", choices=("repository_holdout", "development_warm_timing"), default="repository_holdout")
    parser.add_argument("--tokenizer-cache", type=Path, default=Path(os.environ.get("TIKTOKEN_CACHE_DIR", ROOT / ".cache/tokenizers")))
    parser.add_argument("--repetitions", type=int, default=REPETITIONS)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--freeze", action="store_true")
    action.add_argument("--run", action="store_true")
    args = parser.parse_args()
    for name in ("dataset", "corpus", "model", "output", "tokenizer_cache", "source_review", "development_study"):
        if getattr(args, name) is not None:
            setattr(args, name, getattr(args, name).resolve())
    if args.freeze:
        plan = freeze(args)
        result = {"action": "freeze", "classification": args.role, "candidate": args.candidate,
                  "plan_sha256": file_sha(args.output / "plan.json"), "quality_repetition": plan["quality_repetition"]}
    else:
        result = run(args)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
