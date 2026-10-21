#!/usr/bin/env python3
"""Freeze then compare complementary-window selection against two native controls.

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
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import time
from unittest.mock import patch

import benchmark_multifile as shared
import replay_selection as replay
from semantic_search import context, selection
from semantic_search.context_backfill import coalesce_context
from semantic_search.corpus import _source_files, extract_snippets
from semantic_search.embeddings import QwenEmbedder
from semantic_search.languages import decode_source

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = dict(replay.SETTINGS)
CONDITIONS = {
    "legacy": {"selector": "ranked_context", "postprocessor": "unchanged_context"},
    "coverage_control": {"selector": "definition_context", "postprocessor": "coalesce_context"},
    "connected_union": {"selector": "connected_window_context", "postprocessor": "coalesce_context"},
    "guarded_continuation": {"selector": "definition_context", "postprocessor": "continuation_context"},
    "guarded_connected_union": {"selector": "connected_window_context", "postprocessor": "guarded_connected_context"},
}
CANDIDATES = ("connected_union", "guarded_continuation", "guarded_connected_union")
ARMS = ("legacy", "coverage_control", "candidate")
RETRIEVALS = ("dense", "hybrid")
REPETITIONS = 3
WARM_QUERY = "Find code that handles input and returns a result"
GATE = {"minimum_net_multi_file_complete_gain": 1, "maximum_lost_groups": 0,
        "maximum_lost_complete_positives": 0, "maximum_lost_single_file_first_hits": 0,
        "maximum_median_wall_ratio": 1.25, "promotion_mode": "dense",
        "hybrid_role": "separate diagnostic control; cannot rescue dense",
        "preservation_controls": ["legacy", "coverage_control"],
        "incremental_gain_control": "coverage_control"}


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
    postprocessor = (selection.unchanged_context if condition == "legacy" else
                     selection.continuation_context if condition == "guarded_continuation" else
                     selection.guarded_connected_context if condition == "guarded_connected_union" else coalesce_context)
    return getattr(selection, CONDITIONS[condition]["selector"]), postprocessor


def parent_binding(args):
    if args.development_study is None:
        require(args.role != "repository_holdout", "Fresh holdout requires a qualified committed development parent")
        return None
    folder = args.development_study
    summary = read(folder / "summary.json")
    require(summary.get("qualification", {}).get(args.candidate, {}).get("qualified") is True,
            "Candidate did not qualify in the frozen development replay")
    require(summary.get("model_calls") == summary.get("retrieval_calls") == 0
            and summary.get("network_calls") == 0 and summary.get("baseline_exact_rows") == 365
            and summary.get("prior_coverage_exact_rows") == 337,
            "Development parent must retain all exact controls without model/retrieval calls")
    qualification = summary["qualification"][args.candidate]
    require(set(qualification.get("against_controls", {})) == {"legacy", "coverage_control"}
            and all(set(loss) == {"lost_groups", "lost_complete_rows", "lost_single_file_first_hits"}
                    and all(type(value) is int and value == 0 for value in loss.values())
                    for loss in qualification["against_controls"].values()), "Candidate loses evidence against a control")
    gained = qualification.get("additional_distinct_complete_queries_vs_coverage_control", [])
    require(isinstance(gained, list) and len(set(gained)) == len(gained) >= 1
            and qualification.get("gained_additional_distinct_complete_queries") == len(gained),
            "Candidate needs a new distinct complete query beyond prior coverage")
    plan = read(folder / "frozen-plan.json")
    require(summary["plan_sha256"] == file_sha(folder / "frozen-plan.json")
            and summary["replay_sha256"] == file_sha(folder / "replay.jsonl.gz")
            and plan["conditions"] == CONDITIONS and plan["settings"] == SETTINGS,
            "Development parent artifact/configuration mismatch")
    control_gate = read(folder / "control-gate.json")
    require(control_gate.get("exact_legacy_rows") == 365 and control_gate.get("exact_prior_coverage_rows") == 337
            and control_gate.get("new_variant_rows_before_gate") == 0, "Development control barrier differs")
    producer = plan["producer_sha256"]
    require(all(file_sha(ROOT / name) == digest for name, digest in producer.items()),
            "Qualified development producer changed before native validation")
    audit = read(folder / "independent-audit.json")
    require(audit.get("status") == "passed" and audit.get("checks_passed") is True
            and audit.get("mode") == "development" and audit.get("production_or_producer_modules_imported") is False
            and audit.get("exact_legacy_controls") == 365 and audit.get("exact_prior_coverage_controls") == 337
            and audit.get("independently_recomputed_summary") == summary
            and all(audit.get("calls", {}).get(name) == 0 for name in ("model", "retrieval", "index", "network")),
            "Development parent requires a successful independent audit")
    require(all(file_sha(folder / name) == digest for name, digest in audit["artifact_sha256"].items())
            and all(file_sha(ROOT / name) == digest for name, digest in audit["auditor_sha256"].items()),
            "Development audit bindings changed")
    names = ("frozen-plan.json", "baseline-gate.json", "prior-coverage-gate.json", "source-fixtures.json",
             "run-start.json", "control-gate.json", "summary.json", "replay.jsonl.gz", "findings.md", "independent-audit.json")
    artifacts = {relative(folder / name): file_sha(folder / name) for name in names}
    artifacts.update(producer)
    artifacts.update(audit["auditor_sha256"])
    return {"artifacts": artifacts,
            "qualification": summary["qualification"][args.candidate],
            "condition_description": plan["selection"][args.candidate],
            "exposure": "All parent retrieval/reranker outcomes are observed development evidence."}


def definition_preflight(args, data, sources, inventory, definition_keys, source_files):
    path = getattr(args, "definition_inventory", None)
    if path is None:
        require(args.role != "repository_holdout", "Fresh holdout requires the complete source-only definition inventory")
        return None
    preflight = read(path)
    require(preflight.get("format_version") == 1 and preflight.get("status") == "passed_source_only_definition_inventory"
            and preflight["dataset"]["sha256"] == file_sha(args.dataset)
            and preflight["protocol"]["sha256"] == file_sha(ROOT / data["source_review_protocol"])
            and preflight["source_review"]["sha256"] == file_sha(args.source_review)
            and preflight["corpus"] == {"revision": data["revision"], "source_root": data["source_root"],
                                       "source_exclusions": data.get("source_exclusions", [])}
            and preflight["settings"] == {"max_lines": 160, "overlap_lines": 40}
            and preflight["inventory"] == inventory
            and {name: tuple(key) for name, key in preflight["definition_keys"].items()} == definition_keys
            and preflight["source_files"] == source_files,
            "Complete source-only definition inventory does not match frozen extraction")
    require(all(value == 0 for value in preflight["calls"].values())
            and all(file_sha(ROOT / name) == digest for name, digest in preflight["source_implementations_sha256"].items()),
            "Source-only preflight observed outcomes or changed extraction code")
    included = set(sources)
    excluded = {item.relative_to(args.corpus).as_posix(): {"bytes": len(item.read_bytes()), "sha256": file_sha(item)}
                for item in _source_files(args.corpus, args.corpus / data["source_root"])
                if item.relative_to(args.corpus).as_posix() not in included}
    require(excluded == preflight["excluded_supported_files"] == data.get("excluded_supported_files", {}),
            "Excluded supported source inventory differs")
    receipt_path = path.with_name("definition-inventory-receipt.json")
    receipt = read(receipt_path)
    validator_path = path.with_name("definition-inventory-validator.py.txt")
    require(receipt.get("status") == preflight["status"] and receipt["inventory"]["sha256"] == file_sha(path)
            and receipt["validator"]["sha256"] == file_sha(validator_path)
            and receipt.get("all_source_ast_boundary_checks_passed") is True, "Definition preflight receipt differs")
    return {relative(item): file_sha(item) for item in (path, receipt_path, validator_path)}


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
    require(args.candidate in CANDIDATES, "Only a prequalified complementary-window condition may advance")
    data = read(args.dataset)
    require(len({query["id"] for query in data["queries"]}) == len(data["queries"]), "Duplicate query IDs")
    for query in data["queries"]:
        require(query.get("kind") in ("present", "absent") and isinstance(query.get("query"), str)
                and query["query"].strip(), "Malformed query")
        replay.groups(query)  # Reject positive evidence accidentally attached to an absent query.
    revision = subprocess.check_output(["git", "-C", str(args.corpus), "rev-parse", "HEAD"], text=True).strip()
    require(revision == data["revision"] and not subprocess.check_output(
        ["git", "-C", str(args.corpus), "status", "--porcelain"], text=True).strip(),
        "A clean corpus at the dataset revision is required")
    paths = _source_files(args.corpus, args.corpus / data["source_root"], data.get("source_exclusions", ()))
    sources = {path.relative_to(args.corpus).as_posix(): decode_source(str(path), path.read_bytes()) for path in paths}
    definition_keys = {}
    actual = extract_snippets(args.corpus, data["source_root"], 160, 40, sources=sources,
                              source_exclusions=data.get("source_exclusions", ()), definition_keys=definition_keys)
    inventory = [asdict(item) for item in actual]
    require(len({item["id"] for item in inventory}) == len(inventory) == len(definition_keys),
            "Extraction inventory/definition identity differs")
    binding = {"dataset_sha256": file_sha(args.dataset), "revision": revision, "classification": args.role,
        "source_sha256": {path.relative_to(args.corpus).as_posix(): file_sha(path) for path in paths},
        "inventory_sha256": sha(json.dumps(inventory, sort_keys=True).encode()),
        "implementation_sha256": {path.relative_to(ROOT).as_posix(): file_sha(path)
                                  for path in sorted((ROOT / "src/semantic_search").glob("*.py"))},
        "embedding": QwenEmbedder(args.model, progress=False).metadata, "settings": SETTINGS,
        "repetitions": args.repetitions,
        "runtime": {name: importlib.metadata.version(name) for name in
                    ("tiktoken", "sqlite-vec", "tree-sitter", "tree-sitter-javascript", "tree-sitter-typescript")},
        "machine": {"system": platform.system(), "machine": platform.machine(), "python": platform.python_version()}}
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
                literal = "".join(shared.physical_lines(sources[target["path"]])[low - 1:high])
                require(sha(literal.encode()) == target["evidence_sha256"]
                        and binding["source_sha256"][target["path"]] == target["source_file_sha256"]
                        and any(item["path"] == target["path"] and item["symbol"] == target["symbol"]
                                and item["start_line"] <= low <= high <= item["end_line"] for item in inventory),
                        "Evidence source hashes or symbol-qualified extraction eligibility differ")
    review_hash = None
    if args.source_review:
        review = read(args.source_review)
        require(review.get("status") == "passed_source_review_before_holdout_retrieval"
                and review.get("dataset", {}).get("sha256") == binding["dataset_sha256"]
                and review.get("corpus", {}).get("commit") == revision
                and review.get("corpus", {}).get("source_inventory_sha256") == sha(canonical(source_files))
                and review.get("protocol", {}).get("sha256") == file_sha(ROOT / data["source_review_protocol"])
                and all(review.get("calls", {}).get(name) == 0 for name in
                        ("model", "index", "retrieval", "selector", "postprocessor", "rule_activation", "gold_coverage_scoring")),
                "Independent source-review receipt does not bind this unobserved dataset/source")
        review_hash = file_sha(args.source_review)
    else:
        require(args.role != "repository_holdout", "Repository holdout requires an independent source-review receipt")
    with patch.dict(os.environ, {"TIKTOKEN_CACHE_DIR": str(args.tokenizer_cache)}):
        tokenizer, _ = replay.runtime(ROOT)
    binding.update(retrieval=list(RETRIEVALS), candidate=args.candidate,
                   arms={"legacy": "legacy", "coverage_control": "coverage_control", "candidate": args.candidate},
                   dataset_path=relative(args.dataset),
                   source_review_path=relative(args.source_review) if args.source_review else None,
                   source_review_sha256=review_hash, source_files=source_files,
                   definition_keys_sha256=sha(canonical(definition_keys)),
                   model_files_sha256=checkpoint_binding(args.model),
                   tokenizer=tokenizer, development_parent=parent_binding(args), gate=GATE,
                   definition_preflight=definition_preflight(args, data, sources, inventory, definition_keys, source_files),
                   exposure={"role": args.role, "labels_and_source_only_before_fresh_outcomes": args.role == "repository_holdout",
                             "freshness_limit": "Repository evaluation independence within this project; no model-pretraining isolation claim."})
    extras = ["scripts/benchmark_windows.py", "tests/test_window_benchmark.py", "scripts/replay_windows.py",
              "scripts/benchmark_multifile.py", "scripts/replay_selection.py", "scripts/diagnose_multifile.py",
              "scripts/report_language_quality.py", "tests/test_window_assembly.py", "tests/test_window_replay.py",
              "docs/window-native-protocol.md", "docs/window-development-protocol.md", "pyproject.toml",
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
            "candidate_reason": "One complementary-window condition qualified against BOTH legacy and prior coverage controls, "
                "including a new complete behavior beyond prior coverage, fixed before any fresh outcomes.",
            "queries": [{"id": query["id"], "query": query["query"], "kind": query["kind"]} for query in data["queries"]],
            "artifacts": {"source-inventory.json": file_sha(args.output / "source-inventory.json")},
            "schedule": "For each query and repetition, rotate legacy/dense,coverage_control/dense,candidate/dense,legacy/hybrid,coverage_control/hybrid,candidate/hybrid by (query_index+repetition-1)%6.",
            "timing": "Actual monotonic wall time around each resident backend.search, including fresh query embedding; setup and six arm/mode warmups plus receipt serialization/draining excluded. Lightweight hook capture is included equally in all three arms.",
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
    if binding.get("definition_preflight"):
        paths.update(binding["definition_preflight"])
    for name, digest in plan["artifacts"].items():
        paths[relative(args.output / name)] = digest
    paths[relative(args.output / "plan.json")] = file_sha(args.output / "plan.json")
    committed(paths)
    return plan


def schedule(queries):
    modes = tuple((arm, retrieval) for retrieval in RETRIEVALS for arm in ARMS)
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
    state = {"backfill_candidates": [], "ranked_candidates": [], "reference_response": None,
             "proposal_response": None, "guard_calls": 0}
    original_guard = selection._preserves_context

    def guard(reference, proposal):
        state["guard_calls"] += 1
        state["proposal_response"] = proposal
        return original_guard(reference, proposal)

    def selected(pairs, **kwargs):
        result = selector(pairs, **kwargs)
        state.update(ranked=list(pairs), selected=list(result[0]), eligible=list(result[1]),
                     origins=dict(result[2]), definition_keys=kwargs["definition_keys"],
                     ambiguous_symbols=kwargs.get("ambiguous_symbols"))
        return result

    def postprocessed(response, candidates, **kwargs):
        state["pre_postprocess_response"] = response
        def recorded(iterator, name):
            for candidate in iterator:
                state[name].append(candidate)
                yield candidate
        state["candidate_iterator"] = recorded(candidates, "backfill_candidates")
        state["ranked_iterator"] = recorded(kwargs.pop("ranked_candidates", ()), "ranked_candidates")
        kwargs["ranked_candidates"] = state["ranked_iterator"]
        reference_factory = kwargs.get("reference_factory")
        if reference_factory is not None:
            def reference():
                value = reference_factory()
                state["reference_response"] = value
                return value
            kwargs["reference_factory"] = reference
        return postprocessor(response, state["candidate_iterator"], **kwargs)

    with patch("semantic_search.backend.select_context", selected), \
            patch("semantic_search.backend.postprocess_context", postprocessed), \
            patch.object(selection, "_preserves_context", guard):
        yield state


def checked_assembly(row, sources, inventory, definition_keys, ranked=None):
    """Verify composite receipts against original indexed windows, never a union helper."""
    assembly = row.get("assembly", {})
    require(assembly.get("kind") == "connected_windows" and len(assembly.get("members", ())) == 2,
            "Composite requires two connected original members")
    by_id = {item["id"]: item for item in inventory}
    members = assembly["members"]
    used, keys = [], []
    for member in members:
        require(member.get("id") in by_id, "Composite member is not an indexed window")
        original = by_id[member["id"]]
        require(all(member.get(key) == original[key] for key in
                    ("id", "path", "symbol", "start_line", "end_line", "content_hash")),
                "Composite member identity differs from frozen original")
        low, high = member.get("used_start_line"), member.get("used_end_line")
        require(type(low) is type(high) is int and original["start_line"] <= low <= high <= original["end_line"],
                "Composite used segment is outside its member")
        literal = "".join(shared.physical_lines(sources[original["path"]])[low - 1:high])
        require(member.get("used_source_hash") == sha(literal.encode()), "Composite used segment hash differs")
        used.append((low, high))
        keys.append(tuple(definition_keys[member["id"]]))
    require(keys[0] == keys[1] == tuple(assembly.get("definition_key", ()))
            and tuple(keys[0][:2]) == (row["path"], row["symbol"]), "Composite mixed actual definitions")
    (low, high), (other_low, other_high) = sorted(used)
    end = max(high, other_high)
    require(other_low <= high + 1 and row["start_line"] == low
            and row.get("window_end_line", row["end_line"]) == end,
            "Composite has a gap or incorrect full bounds")
    full = "".join(shared.physical_lines(sources[row["path"]])[low - 1:end])
    require(row["content_hash"] == sha(full.encode()), "Composite full-window hash differs")
    binding = {"kind": "connected_windows", "definition_key": list(keys[0]),
        "members": sorted(members, key=lambda item: (item["id"], item["used_start_line"],
                                                    item["used_end_line"], item["used_source_hash"])),
        "start_line": low, "end_line": end, "content_hash": sha(full.encode())}
    expected_id = "context:connected_windows:" + sha(json.dumps(binding, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode())
    require(row["id"] == expected_id and row["id"] not in by_id, "Composite deterministic ID differs")
    require(assembly.get("score_anchor_id") == members[0]["id"], "Composite score owner differs")
    if ranked is not None:
        ranks = {item["snippet"]["id"]: (number, item["score"]) for number, item in enumerate(ranked, 1)}
        require(all(member["id"] in ranks for member in members), "Composite member was not retrieved")
        require(assembly.get("anchor_rank") == ranks[members[0]["id"]][0]
                and assembly.get("companion_rank") == ranks[members[1]["id"]][0], "Composite ranks differ")
        if "retrieval_score" in row:
            require(row["retrieval_score"] == ranks[members[0]["id"]][1], "Composite inherited ranking scalar differs")
    return full


def checked_snippet(item, sources, inventory, definition_keys, ranked=None):
    shared.checked_candidates([item], sources)
    if "assembly" in item:
        return checked_assembly(item, sources, inventory, definition_keys, ranked)
    require(item == next((original for original in inventory if original["id"] == item["id"]), None),
            "Live stage window differs from frozen inventory")
    return item["text"]


def checked_response(response, sources, inventory, definition_keys=None, ranked=None, composite_ids=()):
    shared.checked_response(response, sources)
    require(response["token_usage"]["encoding"] == context.ENCODING
            and response["token_usage"]["budget"] == SETTINGS["token_budget"], "Token encoding/budget metadata differs")
    require(len({row["id"] for row in response["results"]}) == len(response["results"]), "Duplicate delivered ID")
    by_id = {item["id"]: item for item in inventory}
    for row in response["results"]:
        if "assembly" in row:
            full = checked_assembly(row, sources, inventory, definition_keys or {}, ranked)
            require(row.get("h2o_score") is None and row.get("qwen_score") is None
                    and row.get("origin") == "retrieval", "Composite claims its own reranking or dependency score")
        else:
            require(row["id"] in by_id, "Delivered row is neither indexed nor a verified composite")
            original = by_id[row["id"]]
            require(all(row[key] == original[key] for key in ("path", "symbol", "start_line", "content_hash"))
                    and row["window_end_line"] == original["end_line"], "Delivered window identity differs")
            full = original["text"]
        require(row["window_end_line"] >= row["end_line"]
                and row["truncated"] == (row["source"] != full), "Delivered hash/truncation identity differs")
        require(row.get("origin") in ("retrieval", "dependency")
                and (row.get("related_to") is None if row["origin"] == "retrieval"
                     else row.get("related_to") in set(by_id) | set(composite_ids)), "Invalid dependency provenance")


def evidence_ranks(windows, gold):
    """This holdout additionally requires the authored qualified symbol identity."""
    result = []
    for alternatives in gold:
        found = None
        for rank, row in enumerate(windows, 1):
            for target in alternatives:
                low, high = target["evidence_lines"]
                if (row["path"] == target["path"] and row["symbol"] == target["symbol"]
                        and row["start_line"] <= low <= high <= row["end_line"]):
                    source = row.get("source", row.get("text"))
                    literal = "".join(shared.physical_lines(source)[low - row["start_line"]:high - row["start_line"] + 1])
                    if sha(literal.encode()) == target["evidence_sha256"]:
                        found = rank
                        break
            if found is not None:
                break
        result.append(found)
    return result


def checked_preservation(before, after, *, allow_reassembly=False):
    originals = {row["id"]: row for row in before["results"]}
    mapping = after.get("_window_assembly", {}).get("old_to_new_ids", {})
    for old in originals.values():
        require(any(row["path"] == old["path"]
                    and row["start_line"] <= old["start_line"] <= old["end_line"] <= row["end_line"]
                    and all((field in row) == (field in old) and row.get(field) == old.get(field)
                            for field in ("source_origin", "buffer_version", "buffer_hash"))
                    and "".join(shared.physical_lines(row["source"])[old["start_line"] - row["start_line"]:
                               old["end_line"] - row["start_line"] + 1]) == old["source"]
                    for row in after["results"]), "Postprocessor lost original actual delivered source")
    if allow_reassembly:
        return
    for row in after["results"]:
        if row["id"] in originals:
            expected = deepcopy(originals[row["id"]])
            if expected.get("related_to") in mapping:
                expected["related_to"] = mapping[expected["related_to"]]
            require(row == expected, "Postprocessor changed a retained row beyond its composite parent ID")


def receipt(query, repetition, retrieval, arm, condition, response, captured, elapsed, vector):
    """Called after the search timer stops; only this reporting step reads labels."""
    list(captured.pop("candidate_iterator"))  # Drain unconsumed rows outside the measured call.
    list(captured.pop("ranked_iterator"))
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
            "multi_file": shared.is_multifile(gold), "candidate_ranks": evidence_ranks(ranked, gold),
            "delivered_ranks": evidence_ranks(response["results"], gold), "stages": stages,
            "pre_postprocess_response": deepcopy(captured["pre_postprocess_response"]),
            "backfill_candidates": deepcopy(captured["backfill_candidates"]),
            "ranked_candidates": deepcopy(captured["ranked_candidates"]), "response": deepcopy(response),
            "reference_response": deepcopy(captured["reference_response"]),
            "proposal_response": deepcopy(captured["proposal_response"]), "guard_calls": captured["guard_calls"],
            "compact_payload": text, "compact_sha256": sha(text.encode()),
            "actual_compact_tokens": len(context.encoding().encode(text, disallowed_special=())),
            "query_embedding_calls": 1, "query_vector": vector.tolist(), "query_vector_sha256": sha(vector.tobytes())}


def checked_candidate_rows(outcome, sources, inventory):
    ranked = outcome["stages"]["ranked"]
    require([row["id"] for row in outcome["ranked_candidates"]] == [row["snippet"]["id"] for row in ranked],
            "Raw candidate iterator did not preserve the complete ranked order")
    for name in ("ranked_candidates", "backfill_candidates"):
        for row in outcome[name]:
            require(row["window_end_line"] == row["end_line"] and not row["truncated"]
                    and row["source_hash"] == row["content_hash"] == sha(row["source"].encode())
                    and row.get("origin") == "retrieval" and row.get("related_to") is None,
                    "Candidate row is not a complete truthful retrieval window")
            item = {key: row[key] for key in ("id", "path", "symbol", "start_line", "end_line", "content_hash")}
            item["text"] = row["source"]
            if "assembly" in row:
                item["assembly"] = row["assembly"]
            checked_snippet(item, sources, inventory["inventory"], inventory["definition_keys"], ranked)


def _comparison(first, control):
    gains, losses, complete_losses, first_losses, group_gains = [], [], [], [], []
    multi_gain = multi_loss = 0
    for identifier, before_row in first[control].items():
        after_row = first["candidate"][identifier]
        if before_row["kind"] == "absent":
            continue
        before, after = before_row["delivered_ranks"], after_row["delivered_ranks"]
        if None in before and None not in after:
            gains.append(identifier)
            multi_gain += bool(before_row["multi_file"])
        if None not in before and None in after:
            complete_losses.append(identifier)
            multi_loss += bool(before_row["multi_file"])
        for number, (prior, current) in enumerate(zip(before, after), 1):
            item = {"query_id": identifier, "group": number, "control_rank": prior, "candidate_rank": current}
            if prior is not None and current is None:
                losses.append(item)
            if prior is None and current is not None:
                group_gains.append(item)
        if not before_row["multi_file"] and before and before[0] is not None and after[0] is None:
            first_losses.append(identifier)
    return {"complete_gains": gains, "complete_losses": complete_losses,
        "group_gains": group_gains, "group_losses": losses, "single_file_first_hit_losses": first_losses,
        "net_multi_file_complete_gain": multi_gain - multi_loss,
        "preserved": not losses and not complete_losses and not first_losses}


def _active(row):
    return (any("assembly" in item for item in row["response"]["results"])
            or any("assembly" in item["snippet"] for item in row.get("stages", {}).get("selected", ())))


def summarize(rows):
    result = {}
    for retrieval in RETRIEVALS:
        mode = [row for row in rows if row["retrieval"] == retrieval]
        first = {arm: {row["query_id"]: row for row in mode if row["repetition"] == 1 and row["arm"] == arm}
                 for arm in ARMS}
        require(all(set(first[arm]) == set(first["legacy"]) for arm in ARMS), "Unpaired native quality rows")
        metrics = {}
        for arm in ARMS:
            arm_rows = [row for row in mode if row["arm"] == arm]
            times = sorted(row["elapsed_backend_s"] for row in arm_rows)
            positives = [row for row in first[arm].values() if row["kind"] == "present"]
            metrics[arm] = {"measured_calls": len(times), "median_backend_s": statistics.median(times),
                "p95_backend_s": times[max(0, math.ceil(.95 * len(times)) - 1)],
                "present_queries": len(positives), "multi_file_queries": sum(row["multi_file"] for row in positives),
                "complete_positives": sum(None not in row["delivered_ranks"] for row in positives),
                "multi_file_complete": sum(row["multi_file"] and None not in row["delivered_ranks"] for row in positives),
                "required_groups": sum(len(row["delivered_ranks"]) for row in positives),
                "covered_groups": sum(sum(rank is not None for rank in row["delivered_ranks"]) for row in positives),
                "absent_inventory": [{"query_id": row["query_id"], "match_status": row["response"]["match_status"],
                                      "excerpt_ids": [item["id"] for item in row["response"]["results"]]}
                                     for row in first[arm].values() if row["kind"] == "absent"],
                "coalesced_rows": sum(len(row["response"].get("_selection_backfill", {}).get("coalesced_ids", [])) for row in first[arm].values()),
                "appended_rows": sum(len(row["response"].get("_selection_backfill", {}).get("appended_ids", [])) for row in first[arm].values()),
                "delivered_composites": sum(sum("assembly" in item for item in row["response"]["results"]) for row in first[arm].values()),
                "active_composite_calls": sum(_active(row) for row in arm_rows)}
        comparisons = {control: _comparison(first, control) for control in ARMS[:-1]}
        ratios = {control: metrics["candidate"]["median_backend_s"] / metrics[control]["median_backend_s"]
                  for control in ARMS[:-1]}
        base_times = {(row["query_id"], row["repetition"]): row["elapsed_backend_s"] for row in mode if row["arm"] == "legacy"}
        candidates = [row for row in mode if row["arm"] == "candidate"]
        paired = [row["elapsed_backend_s"] - base_times[(row["query_id"], row["repetition"])] for row in candidates]
        active = [row for row in candidates if _active(row)]
        active_delta = [row["elapsed_backend_s"] - base_times[(row["query_id"], row["repetition"])] for row in active]
        result[retrieval] = {"arms": metrics, "against_controls": comparisons,
            "median_wall_ratio_to_controls": ratios,
            "median_wall_ratio_to_legacy": ratios["legacy"], "median_paired_wall_delta_s_to_legacy": statistics.median(paired),
            "active_composite_overhead": {"criterion": "Composite in selected stage or final delivered response",
                "samples": len(active), "median_paired_wall_delta_s": statistics.median(active_delta) if active_delta else None,
                "median_wall_ratio_to_legacy": (statistics.median(row["elapsed_backend_s"] for row in active)
                    / statistics.median(base_times[(row["query_id"], row["repetition"])] for row in active)) if active else None},
            "passed": all(item["preserved"] for item in comparisons.values())
                and comparisons["coverage_control"]["net_multi_file_complete_gain"] >= 1
                and all(ratio <= GATE["maximum_median_wall_ratio"] for ratio in ratios.values())}
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
          "execution_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
          "resume_allowed": False, "arms": list(ARMS), "expected_measured_calls": len(data["queries"]) * 18})
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
        db = ROOT / ".cache/indexes" / ("windows-" + sha(relative(args.output).encode())[:20] + ".sqlite3")
        setup_started = time.perf_counter()
        with closing(SearchBackend(args.corpus, data["source_root"], db, embedder=embedder,
                                   source_exclusions=data.get("source_exclusions", ()))) as backend:
            for condition in ("legacy", "coverage_control", args.candidate):
                selector, postprocessor = hooks(condition)
                with patch("semantic_search.backend.select_context", selector), patch("semantic_search.backend.postprocess_context", postprocessor):
                    for retrieval in RETRIEVALS:
                        backend.search(WARM_QUERY, retrieval=retrieval, **SETTINGS)
            setup = {"index_model_and_explicit_warmup_s": time.perf_counter() - setup_started,
                     "query_calls": embedder.query_calls, "document_calls": embedder.document_calls,
                     "status": backend.status()}
            require(backend._definition_keys == {key: tuple(value) for key, value in inventory["definition_keys"].items()}, "Live definition map differs from freeze")
            phase = "measured_calls"
            rankings, payloads, vectors, references = {}, {}, {}, {}
            with gzip.open(args.output / "searches.jsonl.gz", "xt", encoding="utf-8") as stream:
                for repetition, query, arm, retrieval in schedule(data["queries"]):
                    condition = args.candidate if arm == "candidate" else arm
                    before, documents = embedder.query_calls, embedder.document_calls
                    with capture(condition) as captured:
                        started = time.perf_counter()
                        response = backend.search(query["query"], retrieval=retrieval, **SETTINGS)
                        elapsed = time.perf_counter() - started
                    require(embedder.query_calls == before + 1 and embedder.document_calls == documents,
                            "Measured requests need exactly one fresh query embedding and no document work")
                    ranked = pair_rows(captured["ranked"])
                    composite_ids = {item.id for stage in ("selected", "eligible") for item, _ in captured[stage]
                                     if hasattr(item, "assembly")}
                    composite_ids.update(row["id"] for row in response["results"] if "assembly" in row)
                    for value in (response, captured["pre_postprocess_response"]):
                        checked_response(value, sources, inventory["inventory"], inventory["definition_keys"], ranked, composite_ids)
                    for stage in ("ranked", "selected", "eligible"):
                        for item, score in captured[stage]:
                            checked_snippet(asdict(item), sources, inventory["inventory"], inventory["definition_keys"], ranked)
                            if hasattr(item, "assembly"):
                                anchor_id = item.assembly["score_anchor_id"]
                                require(score == next(row["score"] for row in ranked if row["snippet"]["id"] == anchor_id),
                                        "Composite stage score differs from its real anchor")
                    if condition == "guarded_connected_union":
                        reference = captured["reference_response"]
                        proposal = captured["proposal_response"]
                        require(reference is not None and proposal is not None and captured["guard_calls"] == 1,
                                "Guarded union did not evaluate its shared coverage reference/proposal exactly once")
                        checked_response(reference, sources, inventory["inventory"], inventory["definition_keys"], ranked)
                        checked_response(proposal, sources, inventory["inventory"], inventory["definition_keys"], ranked, composite_ids)
                        checked_preservation(reference, response, allow_reassembly=True)
                        try:
                            checked_preservation(reference, proposal, allow_reassembly=True)
                            accepted = True
                        except ValueError:
                            accepted = False
                        reference_hash = sha(context.serialize_context(context.compact_result(reference)).encode())
                        guard_receipt = response.get("_window_guard", {})
                        require(guard_receipt.get("accepted") is accepted
                                and guard_receipt.get("reference_compact_sha256") == reference_hash
                                and guard_receipt.get("fallback_reason") == (None if accepted else "coverage_control_source_not_preserved")
                                and context.compact_result(response) == context.compact_result(proposal if accepted else reference),
                                "Guarded union decision/fallback differs from actual source preservation")
                        reference_key = (query["id"], retrieval)
                        require(references.setdefault(reference_key, reference_hash) == reference_hash,
                                "Guarded coverage reference changed across repetitions")
                    elif condition != "legacy":
                        checked_preservation(captured["pre_postprocess_response"], response)
                    outcome = receipt(query, repetition, retrieval, arm, condition, response, captured, elapsed, embedder.last_query)
                    checked_candidate_rows(outcome, sources, inventory)
                    rows.append(outcome)
                    stream.write(json.dumps(outcome, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
                    stream.flush()
                    rank_key, payload_key = (query["id"], retrieval), (query["id"], retrieval, arm)
                    fingerprint = sha(canonical(outcome["stages"]["ranked"]))
                    require(rankings.setdefault(rank_key, fingerprint) == fingerprint, "Ranked snippets/scores changed across paired arms or repetitions")
                    require(vectors.setdefault(rank_key, outcome["query_vector_sha256"]) == outcome["query_vector_sha256"],
                            "Actual query vectors changed across paired arms or repetitions")
                    require(payloads.setdefault(payload_key, outcome["compact_sha256"]) == outcome["compact_sha256"], "Actual compact payload changed across repetitions")
                    print(f"r{repetition} {query['id']} {retrieval} {arm} {elapsed:.3f}s", flush=True)
        phase = "final_bindings"
        require(all(payloads[(query_id, retrieval, "coverage_control")] == digest
                    for (query_id, retrieval), digest in references.items()),
                "Guarded reference does not equal the measured same-query coverage control")
        require(identity(args)[3] == binding, "Frozen bindings changed during native execution")
        verify_plan(args, binding)
        metrics = summarize(rows)
        write(args.output / "summary.json", {"complete": True, "classification": args.role,
            "candidate": args.candidate, "requests": len(rows), "unique_queries": len(data["queries"]),
            "quality_repetition": 1, "setup": setup, "gate": plan["binding"]["gate"], "metrics": metrics,
            "arms": {"legacy": "legacy", "coverage_control": "coverage_control", "candidate": args.candidate},
            "ranked_repeat_changes": 0, "compact_repeat_changes": 0, "query_vector_repeat_changes": 0,
            "measured_query_embeddings": len(rows), "total_query_embeddings": embedder.query_calls,
            "expected_requests": len(data["queries"]) * len(ARMS) * len(RETRIEVALS) * REPETITIONS,
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
    parser.add_argument("--candidate", choices=CANDIDATES, required=True)
    parser.add_argument("--source-review", type=Path)
    parser.add_argument("--development-study", type=Path)
    parser.add_argument("--definition-inventory", type=Path)
    parser.add_argument("--role", choices=("repository_holdout", "development_warm_timing"), default="repository_holdout")
    parser.add_argument("--tokenizer-cache", type=Path, default=Path(os.environ.get("TIKTOKEN_CACHE_DIR", ROOT / ".cache/tokenizers")))
    parser.add_argument("--repetitions", type=int, default=REPETITIONS)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--freeze", action="store_true")
    action.add_argument("--run", action="store_true")
    args = parser.parse_args()
    for name in ("dataset", "corpus", "model", "output", "tokenizer_cache", "source_review", "development_study", "definition_inventory"):
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
