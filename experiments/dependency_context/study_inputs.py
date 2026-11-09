"""Immutable observed inputs for the terminal annotated-dependency experiment.

Preparation reads and binds saved decisions and original source bytes. It never
imports corpus modules, executes a selector/guard, constructs a new graph, loads
models, indexes, retrieves, scores evidence, or computes a new outcome. Attrs is
already observed development. Labels live apart from selector operands.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tokenize


ROOT = Path(__file__).resolve().parents[2]
PARENT_COMMIT = "b25dceb8652bb20b836156ab49fb2cfe817116e3"
SCHEMA = "dependency_context_study_inputs_v1"
HISTORICAL = "results/semantic-model-development-2026-11-02/prepared-v3/operands.json"
ROLE = "results/role-context-development-2026-11-03"
NATIVE = "results/role-native-holdout-v2-2026-11-03"
WINDOWS = "results/window-development-2026-10-21/source-fixtures.json"
GRAPHS = "results/source-allocation-development-2026-10-27/graph-fixtures.json"
TANSTACK = "results/flow-native-holdout-2026-10-29"
LIMITS = {"max_results": 8, "source_budget": 12000, "token_budget": 4096}
ARTIFACTS = ("records.jsonl.gz", "operands.jsonl.gz", "source-bundles.jsonl.gz", "control-references.json", "input-manifest.json")
SOURCE_FIELDS = ("query", "scope", "match_status", "results", "total_source_chars", "budget_exhausted", "token_usage")
PROVENANCE = ("source_origin", "buffer_version", "buffer_hash")
CORPUS_FOLDERS = {"jinja": "jinja-multifile-holdout", "urllib3": "urllib3-selection-holdout",
                  "tanstack": "tanstack-query-window-holdout", "attrs": "attrs-complementary-holdout"}


def require(value, message):
    if not value:
        raise ValueError(message)


def literal_json(value):
    if value is None or type(value) in (str, bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) is list:
        return all(literal_json(item) for item in value)
    if type(value) is dict:
        return all(type(key) is str and literal_json(item) for key, item in value.items())
    return False


def same(a, b):
    if type(a) is not type(b):
        return False
    if type(a) is dict:
        return a.keys() == b.keys() and all(same(a[key], b[key]) for key in a)
    if type(a) is list:
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def canonical(value):
    require(literal_json(value), "Typed finite literal JSON required")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def file_sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read(path):
    value = json.loads(Path(path).read_bytes())
    require(literal_json(value), "Saved JSON contains unsupported or nonfinite values")
    return value


def rows(path):
    opener = gzip.open if Path(path).suffix == ".gz" else open
    with opener(path, "rb") as stream:
        for number, raw in enumerate(stream, 1):
            value = json.loads(raw)
            require(literal_json(value), "Saved row contains unsupported or nonfinite values")
            yield number, value, sha(raw)


def unique(values, key):
    require(type(values) is list and all(type(row) is dict and type(row.get(key)) is str and row[key] for row in values),
            "Explicit nonempty record identities required")
    indexed = {row[key]: row for row in values}
    require(len(indexed) == len(values), "Duplicate or aliased record identity")
    return indexed


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def verify_blobs(root, hashes, *, commit=PARENT_COMMIT):
    root = Path(root).resolve()
    require(type(commit) is str and re.fullmatch(r"[0-9a-f]{40}", commit), "Full immutable parent Git SHA required")
    for name, expected in hashes.items():
        require(type(name) is str and not Path(name).is_absolute() and ".." not in Path(name).parts
                and type(expected) is str and re.fullmatch(r"[0-9a-f]{64}", expected),
                "Parent binding path/hash is invalid")
        path = (root / name).resolve()
        require(path.is_relative_to(root), "Parent binding path escapes repository")
        require(file_sha(path) == expected == sha(git(root, "show", commit + ":" + name)),
                "Immutable parent working/Git bytes differ: " + name)


def parent_binding(root):
    """Bind all original source closures and complete tracked study evidence."""
    root = Path(root).resolve()
    native_plan = read(root / NATIVE / "plan.json")
    binding = native_plan["binding"]
    hashes = {**binding["implementation_sha256"], **binding["development_parent"]["artifacts"]}
    for parent in ("results/semantic-model-development-2026-11-02", ROLE, NATIVE,
                   "results/role-native-preflight-2026-11-03"):
        for raw in git(root, "ls-tree", "-r", "--name-only", PARENT_COMMIT, "--", parent).decode().splitlines():
            hashes[raw] = sha(git(root, "show", PARENT_COMMIT + ":" + raw))
    # The saved fixture/graph authorities are part of the old 147-blob closure;
    # name them explicitly so a missing/omitted transitive parent cannot hide.
    for name in (WINDOWS, GRAPHS, TANSTACK + "/plan.json", TANSTACK + "/source-inventory.json", HISTORICAL):
        expected = sha(git(root, "show", PARENT_COMMIT + ":" + name))
        require(name not in hashes or hashes[name] == expected, "Conflicting original source authority")
        hashes[name] = expected
    verify_blobs(root, hashes)
    role_audit = read(root / ROLE / "independent-audit.json")
    native_audit = read(root / NATIVE / "independent-audit.json")
    require(role_audit.get("status") == native_audit.get("status") == "passed"
            and role_audit.get("checks_passed") is native_audit.get("checks_passed") is True
            and role_audit.get("verified_candidate_rows") == 393 and role_audit.get("exact_control_rows") == 786
            and role_audit.get("retained_negative_references") == 3734 and role_audit.get("distinct_query_units") == 201
            and native_audit.get("verified_requests") == 252 and native_audit.get("verified_warmups") == 6,
            "Complete independently audited saved parents required")
    require(role_audit["independently_recomputed_qualification"]["qualified"] is True
            and native_audit["independently_recomputed_metrics"]["dense_promotion_gate_passed"] is False,
            "Preserve original development pass and failed native qualification")
    return {"publication_commit": PARENT_COMMIT, "sha256": dict(sorted(hashes.items())),
        "bound_blobs": len(hashes), "historical_decisions": 393, "native_saved_candidate_decisions": 84,
        "retained_historical_negative_references": 3734,
        "classification": "All parents are observed development; the attrs native failure remains a failed qualification"}


def source_specs(root):
    root = Path(root)
    fixtures = read(root / WINDOWS)["fixtures"]
    graphs = read(root / GRAPHS)["fixtures"]
    result = {}
    for signature, value in fixtures.items():
        dataset = value["dataset"]
        require(dataset not in result and signature in graphs
                and same(value["source_sha256"], graphs[signature]["source_sha256"]), "Historical source/graph fixture identity differs")
        result[dataset] = {"source_sha256": value["source_sha256"], "inventory": value["inventory"],
                           "definition_keys": value["definition_keys"], "graph_edges": graphs[signature]["edges"]}
    for dataset, folder in (("tanstack", TANSTACK), ("attrs", NATIVE)):
        require(dataset not in result, "Duplicate source dataset")
        plan, document = read(root / folder / "plan.json"), read(root / folder / "source-inventory.json")
        result[dataset] = {"source_sha256": plan["binding"]["source_sha256"], "inventory": document["inventory"],
            "definition_keys": document["definition_keys"], "graph_edges": document["graph_edges"], "revision": plan["binding"]["revision"]}
    require(len(result) == 13 and sum(len(value["source_sha256"]) for value in result.values()) == 417,
            "Complete original 13-dataset/417-file source membership required")
    return result


def physical_lines(text):
    return [value for value in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text) if value]


def validate_bundle(bundle):
    require(type(bundle) is dict and type(bundle.get("id")) is str and type(bundle.get("dataset")) is str,
            "Source bundle identity required")
    sources, files = bundle["sources"], bundle["files"]
    require(type(sources) is dict and sources.keys() == files.keys(), "Complete source byte/text membership required")
    for path, record in files.items():
        require(type(path) is str and not Path(path).is_absolute() and ".." not in Path(path).parts,
                "Captured module path escapes source bundle")
        raw = base64.b64decode(record["raw_base64"], validate=True)
        require(type(record.get("bytes")) is int and len(raw) == record["bytes"] and sha(raw) == record["sha256"],
                "Captured original module bytes differ")
        encoding = tokenize.detect_encoding(io.BytesIO(raw).readline)[0] if path.endswith(".py") else "utf-8"
        require(record["encoding"] == encoding and raw.decode(encoding) == sources[path], "Original decoded source differs")
    inventory = unique(bundle["inventory"], "id")
    keys = bundle["definition_keys"]
    require(type(keys) is dict and inventory.keys() == keys.keys(), "Complete source window/definition map required")
    for identifier, row in inventory.items():
        key = keys[identifier]
        require(set(row) == {"id", "path", "symbol", "start_line", "end_line", "text", "content_hash"}
                and type(row["path"]) is str and row["path"] in sources and type(row["symbol"]) is str
                and type(row["start_line"]) is int and type(row["end_line"]) is int and 1 <= row["start_line"] <= row["end_line"],
                "Invalid original source window")
        literal = "".join(physical_lines(sources[row["path"]])[row["start_line"]-1:row["end_line"]])
        require(row["text"] == literal and row["content_hash"] == sha(literal.encode())
                and len(physical_lines(literal)) == row["end_line"]-row["start_line"]+1,
                "Original source window does not bind full module bytes")
        require(type(key) is list and len(key) in (4, 5) and key[:2] == [row["path"], row["symbol"]]
                and type(key[2]) is int and type(key[3]) is int and 1 <= key[2] <= row["start_line"] <= row["end_line"] <= key[3]
                and (len(key) == 4 or type(key[4]) is int and key[4] >= 0), "Original canonical definition key differs")
    expected_id = "sources_" + sha(canonical({"dataset": bundle["dataset"],
        "source_sha256": {name: value["sha256"] for name, value in files.items()}, "definition_keys": keys}))
    require(bundle["id"] == expected_id, "Aliased source bundle identity")
    return bundle


def build_bundles(specs, corpus_root):
    corpus_root = Path(corpus_root).resolve()
    result = {}
    for dataset, spec in sorted(specs.items()):
        corpus = (corpus_root / CORPUS_FOLDERS.get(dataset, dataset)).resolve()
        require(corpus.is_relative_to(corpus_root) and corpus.is_dir(), "Original corpus source directory missing")
        sources, files = {}, {}
        for name, expected in sorted(spec["source_sha256"].items()):
            path = (corpus / name).resolve()
            require(not Path(name).is_absolute() and ".." not in Path(name).parts and path.is_relative_to(corpus),
                    "Pinned source module escapes original corpus")
            raw = path.read_bytes()
            require(sha(raw) == expected, "Pinned original module bytes changed: " + dataset + "/" + name)
            encoding = tokenize.detect_encoding(io.BytesIO(raw).readline)[0] if name.endswith(".py") else "utf-8"
            sources[name] = raw.decode(encoding)
            files[name] = {"bytes": len(raw), "sha256": expected, "encoding": encoding,
                           "raw_base64": base64.b64encode(raw).decode("ascii")}
        identifier = "sources_" + sha(canonical({"dataset": dataset, "source_sha256": spec["source_sha256"],
                                               "definition_keys": spec["definition_keys"]}))
        bundle = {"id": identifier, "dataset": dataset, "sources": sources, "files": files,
            "inventory": deepcopy(spec["inventory"]), "definition_keys": deepcopy(spec["definition_keys"]),
            "original_graph_edges": deepcopy(spec.get("graph_edges", [])), "original_revision": spec.get("revision")}
        validate_bundle(bundle)
        require(identifier not in result, "Duplicate source bundle identity")
        result[identifier] = bundle
    return result


def source_view(response):
    require(type(response) is dict and literal_json(response), "Literal saved response required")
    return {name: deepcopy(response[name]) for name in (*SOURCE_FIELDS, "_selection_backfill") if name in response}


def compact_payload(response):
    """Independent public compact-envelope specification; preserve nested order."""
    result = []
    for original in response["results"]:
        value = {name: original[name] for name in
                 ("path", "symbol", "start_line", "end_line", "source", "source_hash", "truncated")}
        value.update(origin=original.get("origin", "retrieval"), related_to=original.get("related_to"))
        for name in PROVENANCE:
            if name in original:
                value[name] = original[name]
        result.append(value)
    value = {"query": response["query"], "match_status": response.get("match_status", "candidates"),
             "results": result, "budget_exhausted": response.get("budget_exhausted", False),
             "token_usage": response.get("token_usage", {"encoding": "cl100k_base", "tokens": None,
                                                         "budget": None, "truncated": False})}
    if "scope" in response:
        value["scope"] = response["scope"]
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def saved_payload(row):
    payload = compact_payload(row["response"])
    require(row.get("compact_payload") == payload and row.get("compact_sha256") == sha(payload.encode()),
            "Saved response/compact payload bytes differ")
    return payload


def record_ref(path, number, value, raw_sha):
    return {"path": path, "line_number": number, "record_sha256": sha(canonical(value)),
            "raw_line_sha256": raw_sha, "response_sha256": sha(canonical(value["response"])),
            "source_view_sha256": sha(canonical(source_view(value["response"]))),
            "compact_sha256": sha(compact_payload(value["response"]).encode())}


def original_controls(root, cases):
    """Resolve exact original ledger rows, not self-reported copied references."""
    wanted = {}
    for case in cases:
        for arm in ("legacy", "coverage_control"):
            reference = case["control_references"][arm]
            name, number = reference["path"], reference["line_number"]
            require(type(name) is str and not Path(name).is_absolute() and ".." not in Path(name).parts
                    and type(number) is int and number >= 1, "Original control ledger reference invalid")
            wanted.setdefault(name, set()).add(number)
    found = {}
    for name, numbers in wanted.items():
        path = (Path(root) / name).resolve()
        require(path.is_relative_to(Path(root).resolve()), "Original control ledger escapes repository")
        for number, value, raw_sha in rows(path):
            if number in numbers:
                found[name, number] = (value, record_ref(name, number, value, raw_sha))
        require(all((name, number) in found for number in numbers), "Missing original control ledger row")
    return found


def validate_response(response, bundle, *, bounded=True):
    """Literal source and score dictionaries remain unchanged, including clipped rows."""
    require(type(response) is dict and literal_json(response) and type(response.get("query")) is str
            and type(response.get("results")) is list, "Literal response schema required")
    values = unique(response["results"], "id")
    inventory = {row["id"]: row for row in bundle["inventory"]}
    for identifier, row in values.items():
        require(identifier in inventory, "Delivered source window absent from original inventory")
        original = inventory[identifier]
        require(row.get("path") == original["path"] and row.get("symbol") == original["symbol"]
                and type(row.get("start_line")) is int and type(row.get("end_line")) is int
                and type(row.get("window_end_line")) is int and type(row.get("truncated")) is bool
                and row["start_line"] == original["start_line"]
                and row["window_end_line"] == original["end_line"]
                and row["start_line"] <= row["end_line"] <= row["window_end_line"],
                "Saved source window identity/interval differs")
        text = "".join(physical_lines(bundle["sources"][row["path"]])[row["start_line"]-1:row["end_line"]])
        require(row.get("source") == text and row.get("source_hash") == sha(text.encode())
                and row.get("content_hash") == original["content_hash"]
                and len(physical_lines(text)) == row["end_line"]-row["start_line"]+1
                and (row["truncated"] or row["end_line"] == row["window_end_line"]),
                "Saved delivered source does not bind exact original module")
        require(("source_origin" not in row or row["source_origin"] == "saved")
                and "buffer_version" not in row and "buffer_hash" not in row,
                "Observed saved-file corpus provenance differs")
    if bounded:
        require(len(values) <= LIMITS["max_results"]
                and sum(len(row["source"]) for row in values.values()) <= LIMITS["source_budget"]
                and response.get("total_source_chars") == sum(len(row["source"]) for row in values.values()),
                "Saved response source caps/counts differ")
        usage = response.get("token_usage")
        require(type(usage) is dict and usage.get("encoding") == "cl100k_base"
                and type(usage.get("tokens")) is int and 0 <= usage["tokens"] <= LIMITS["token_budget"]
                and usage.get("budget") == LIMITS["token_budget"], "Saved compact token cap metadata differs")


def opaque_id(origin, identity):
    return "record_" + sha(canonical({"origin": origin, "identity": identity}))


def historical_records(root, bundles, *, expected_count=393):
    cases = read(Path(root) / HISTORICAL)["cases"]
    case_map = unique(cases, "case_id")
    require(len(cases) == expected_count, "Exact historical decision count required")
    path = ROLE + "/replay/outcomes.jsonl.gz"
    proposals = list(rows(Path(root) / path))
    proposal_map = unique([row for _, row, _ in proposals], "case_id")
    require(case_map.keys() == proposal_map.keys(), "Historical saved proposals missing or extra")
    positions = {row["case_id"]: (number, digest) for number, row, digest in proposals}
    controls = original_controls(root, cases)
    by_dataset = {value["dataset"]: value for value in bundles.values()}
    result = []
    for case in sorted(cases, key=lambda row: row["key"]):
        key, proposal = case["key"], proposal_map[case["case_id"]]
        require(type(key) is list and len(key) == 4 and all(type(value) is str and value for value in key)
                and case["case_id"] == "case_" + sha(canonical(key))
                and key == [case["subset"], case["dataset"], case["query_id"], case["condition"]]
                and all(same(proposal.get(name), case.get(name)) for name in
                        ("key", "subset", "dataset", "query_id", "condition", "kind", "multi_file", "qualified")),
                "Historical proposal identity/labels differ")
        saved_payload(proposal)
        bundle = by_dataset[case["dataset"]]
        require(same(case["definition_keys"], bundle["definition_keys"]) and same(case["limits"], LIMITS),
                "Historical definition map/limits differ from original source")
        refs = {}
        for arm, field in (("legacy", "legacy_control"), ("coverage_control", "reference_response")):
            old = case["control_references"][arm]
            original, reference = controls[old["path"], old["line_number"]]
            replay_identity = (original.get("dataset") == case["dataset"]
                and original.get("condition") == case["condition"] and original.get("selector") == arm)
            native_identity = ("selector" not in original and "dataset" not in original
                and original.get("arm") == original.get("condition") == arm
                and original.get("retrieval") == case["retrieval"]
                and type(original.get("repetition")) is int and original["repetition"] == 1)
            require(all(same(old.get(name), reference.get(name)) for name in
                        ("path", "line_number", "record_sha256", "response_sha256", "compact_sha256"))
                    and same(source_view(original["response"]), case[field])
                    and original.get("query_id") == case["query_id"]
                    and (replay_identity or native_identity),
                    "Original exact historical control differs")
            saved_payload(original)
            refs[arm] = reference
        number, digest = positions[case["case_id"]]
        refs["prior_role"] = record_ref(path, number, proposal, digest)
        record = {"id": opaque_id("historical_role", key), "dataset": case["dataset"],
            "query_id": case["query_id"], "mode": case["condition"], "repetition": 1, "quality": True,
            "reference": deepcopy(case["reference_response"]), "proposal": deepcopy(proposal["response"]),
            "legacy": deepcopy(case["legacy_control"]), "definition_keys": deepcopy(case["definition_keys"]),
            "raw_rows": deepcopy(case["raw_rows"]), "precoalesce_rows": deepcopy(case["precoalesce_rows"]),
            "limits": deepcopy(case["limits"]), "scoring": {name: deepcopy(case[name]) for name in
                                                            ("kind", "multi_file", "qualified", "groups")},
            "original_identity": {"origin": "historical_role", "key": key, "case_id": case["case_id"],
                                  "retrieval": case["retrieval"]},
            "parent_refs": refs, "source_bundle_id": bundle["id"]}
        result.append(record)
    return result


def native_records(root, bundles, *, expected_queries=14):
    path = NATIVE + "/attempt/searches.jsonl.gz"
    saved = list(rows(Path(root) / path))
    require(len(saved) == expected_queries * 18, "Exact native saved arm decision count required")
    paired = {}
    for number, value, raw_sha in saved:
        key = (value.get("query_id"), value.get("retrieval"), value.get("repetition"), value.get("arm"))
        require(type(key[0]) is str and key[1] in ("dense", "hybrid") and type(key[2]) is int
                and key[2] in (1, 2, 3) and key[3] in ("legacy", "coverage_control", "candidate")
                and key not in paired, "Missing, duplicate or unknown native control pair identity")
        saved_payload(value)
        paired[key] = (value, record_ref(path, number, value, raw_sha))
    plan = read(Path(root) / NATIVE / "plan.json")
    dataset_path = plan["binding"]["dataset_path"]
    require(dataset_path == "benchmarks/attrs_complementary_holdout_queries.json", "Original observed attrs labels required")
    labels = unique(read(Path(root) / dataset_path)["queries"], "id")
    require(len(labels) == expected_queries and set(paired) == {(identifier, mode, repeat, arm)
            for identifier in labels for mode in ("dense", "hybrid") for repeat in (1, 2, 3)
            for arm in ("legacy", "coverage_control", "candidate")}, "Complete native paired matrix required")
    by_dataset = {value["dataset"]: value for value in bundles.values()}
    bundle = by_dataset["attrs"]
    result = []
    for identifier, mode, repeat in sorted({key[:3] for key in paired}):
        candidate, prior_ref = paired[identifier, mode, repeat, "candidate"]
        coverage, coverage_ref = paired[identifier, mode, repeat, "coverage_control"]
        legacy, legacy_ref = paired[identifier, mode, repeat, "legacy"]
        capture, label = candidate["candidate_capture"], labels[identifier]
        require(type(capture) is dict and same(candidate["response"], capture["response"])
                and same(candidate["reference_response"], capture["reference_response"])
                and same(source_view(capture["reference_response"]), source_view(coverage["response"]))
                and compact_payload(capture["reference_response"]) == compact_payload(coverage["response"])
                and all(row["response"]["query"] == label["query"] for row in (candidate, coverage, legacy))
                and all(row["kind"] == label["kind"] and row["multi_file"] is label["multi_file"]
                        for row in (candidate, coverage, legacy)), "Native exact saved control/reference pair differs")
        require(same(capture["definition_keys"], bundle["definition_keys"]) and same(capture["limits"], LIMITS),
                "Native definition map/limits differ from original source")
        result.append({"id": opaque_id("attrs_native_observed", [identifier, mode, repeat]),
            "dataset": "attrs", "query_id": identifier, "mode": mode, "repetition": repeat, "quality": repeat == 1,
            "reference": deepcopy(capture["reference_response"]), "proposal": deepcopy(candidate["response"]),
            "legacy": deepcopy(legacy["response"]), "definition_keys": deepcopy(capture["definition_keys"]),
            "raw_rows": deepcopy(capture["ranked_candidates"]), "precoalesce_rows": deepcopy(capture["precoalesce_rows"]),
            "limits": deepcopy(capture["limits"]), "scoring": {"kind": label["kind"], "multi_file": label["multi_file"],
                "qualified": True, "groups": deepcopy(label["target_groups"])},
            "original_identity": {"origin": "attrs_native_observed", "query_id": identifier, "retrieval": mode,
                                  "repetition": repeat},
            "parent_refs": {"legacy": legacy_ref, "coverage_control": coverage_ref, "prior_role": prior_ref},
            "source_bundle_id": bundle["id"]})
    return result


OPERAND_FIELDS = ("id", "reference", "proposal", "definition_keys", "source_bundle_id",
                  "raw_rows", "precoalesce_rows", "limits")
COUNTS = {"decisions": 477, "quality_occurrences": 421, "distinct_quality_query_units": 215,
          "historical_decisions": 393, "native_decisions": 84, "quality_native_decisions": 28,
          "paired_controls": 954, "quality_control_occurrences": 842, "source_datasets": 13, "source_files": 417}
ZERO_CALLS = {"guard": 0, "graph_construction": 0, "selector": 0, "model": 0, "index": 0,
              "retrieval": 0, "reranker": 0, "network": 0, "corpus_modules_imported_or_executed": 0,
              "new_outcomes_computed": 0, "evidence_scoring": 0}


def source_hashes(root):
    """Read executing/new study and unchanged product source; import none of it."""
    root = Path(root).resolve()
    paths = set((root / "experiments" / "dependency_context").rglob("*.py"))
    paths.update((root / "scripts").rglob("*.py"))
    paths.update((root / "src").rglob("*.py"))
    paths.update((root / "tests").glob("test_dependency*.py"))
    paths.update(root / name for name in ("experiments/__init__.py", "tests/test_annotated_dependencies.py",
                                         "docs/dependency-context-protocol.md",
                                         "docs/dependency-context-continuation-protocol.md", "pyproject.toml", "uv.lock")
                 if (root / name).is_file())
    return {str(path.relative_to(root)): file_sha(path) for path in sorted(paths)}


def snapshot_values(records, bundles, *, expected_counts=None):
    """Validate literal immutable operands and isolate all evaluator-only labels."""
    expected_counts = COUNTS if expected_counts is None else expected_counts
    indexed = unique(records, "id")
    require(type(bundles) is dict and all(key == value.get("id") for key, value in bundles.items()),
            "Source bundle map identity differs")
    for bundle in bundles.values():
        validate_bundle(bundle)
    require(len({bundle["dataset"] for bundle in bundles.values()}) == len(bundles), "Duplicate source dataset alias")
    occurrences, units, controls, manifests, operands = set(), set(), [], [], []
    historical = native = native_quality = quality = 0
    for record in records:
        require(literal_json(record) and type(record.get("repetition")) is int and record["repetition"] >= 1
                and type(record.get("quality")) is bool and type(record.get("dataset")) is str
                and type(record.get("query_id")) is str and type(record.get("mode")) is str,
                "Evaluator record identity schema differs")
        origin = record["original_identity"].get("origin")
        identity = (record["original_identity"].get("key") if origin == "historical_role" else
                    [record["query_id"], record["mode"], record["repetition"]])
        occurrence = (origin, sha(canonical(identity)))
        require(origin in ("historical_role", "attrs_native_observed")
                and record["id"] == opaque_id(origin, identity)
                and occurrence not in occurrences, "Duplicate or aliased evaluator occurrence")
        occurrences.add(occurrence)
        if origin == "historical_role":
            historical += 1
            require(type(identity) is list and len(identity) == 4
                    and identity[1:] == [record["dataset"], record["query_id"], record["mode"]]
                    and record["original_identity"].get("case_id") == "case_" + sha(canonical(identity))
                    and record["quality"] is True and record["repetition"] == 1, "Historical quality occurrence changed")
        else:
            native += 1
            require(record["dataset"] == "attrs" and record["quality"] is (record["repetition"] == 1),
                    "Native first-repetition quality boundary changed")
            native_quality += int(record["quality"])
        if record["quality"]:
            quality += 1
            units.add((record["dataset"], record["query_id"]))
        bundle = bundles.get(record["source_bundle_id"])
        require(bundle is not None and bundle["dataset"] == record["dataset"]
                and same(record["definition_keys"], bundle["definition_keys"])
                and same(record["limits"], LIMITS), "Record source/definition/limit binding differs")
        scoring = record["scoring"]
        require(set(scoring) == {"kind", "multi_file", "qualified", "groups"}
                and scoring["kind"] in ("present", "absent") and type(scoring["multi_file"]) is bool
                and type(scoring["qualified"]) is bool and type(scoring["groups"]) is list,
                "Separate evaluator labels malformed")
        require(record["reference"]["query"] == record["proposal"]["query"] == record["legacy"]["query"],
                "Saved control query strings differ")
        for name in ("reference", "proposal", "legacy"):
            validate_response(record[name], bundle)
        for name in ("raw_rows", "precoalesce_rows"):
            validate_response({"query": record["reference"]["query"], "results": record[name]}, bundle, bounded=False)
        refs = record["parent_refs"]
        require(set(refs) == {"legacy", "coverage_control", "prior_role"}, "All three exact saved response references required")
        for arm, field in (("legacy", "legacy"), ("coverage_control", "reference"), ("prior_role", "proposal")):
            reference = refs[arm]
            require(type(reference) is dict and type(reference.get("line_number")) is int and reference["line_number"] > 0
                    and type(reference.get("path")) is str and not Path(reference["path"]).is_absolute()
                    and ".." not in Path(reference["path"]).parts
                    and reference.get("source_view_sha256") == sha(canonical(source_view(record[field])))
                    and reference.get("compact_sha256") == sha(compact_payload(record[field]).encode())
                    and all(type(reference.get(key)) is str and re.fullmatch(r"[0-9a-f]{64}", reference[key])
                            for key in ("record_sha256", "raw_line_sha256", "response_sha256")),
                    "Saved three-control reference bytes differ")
            if arm != "prior_role":
                controls.append({"id": record["id"], "arm": arm, "quality": record["quality"],
                                 "reference": deepcopy(reference)})
        operand = {name: deepcopy(record[name]) for name in OPERAND_FIELDS}
        operands.append(operand)
        manifests.append({"id": record["id"], "record_sha256": sha(canonical(record)),
            "operand_sha256": sha(canonical(operand)), "source_bundle_id": bundle["id"],
            "reference_sha256": sha(canonical(record["reference"])),
            "proposal_sha256": sha(canonical(record["proposal"])), "legacy_sha256": sha(canonical(record["legacy"])),
            "parent_refs_sha256": sha(canonical(refs))})
    counts = {"decisions": len(indexed), "quality_occurrences": quality, "distinct_quality_query_units": len(units),
              "historical_decisions": historical, "native_decisions": native, "quality_native_decisions": native_quality,
              "paired_controls": len(controls), "quality_control_occurrences": sum(value["quality"] for value in controls),
              "source_datasets": len(bundles), "source_files": sum(len(value["files"]) for value in bundles.values())}
    require(same(counts, expected_counts), "Exact decision/control/source/quality counts differ")
    return {"records": records, "operands": operands, "source_bundles": bundles, "controls": controls,
            "manifest": {"schema": SCHEMA, "records": manifests, "counts": counts}, "counts": counts}


def fsync_dir(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_json(path, value):
    with Path(path).open("xb") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode() + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    fsync_dir(Path(path).parent)


def write_rows(path, values):
    """Durable gzip rows, finalized before any artifact digest is returned."""
    raw, stream, primary, cleanup = Path(path).open("xb"), None, None, []
    try:
        stream = gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0)
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                    separators=(",", ":")).encode() + b"\n")
            stream.flush()
            raw.flush()
            os.fsync(raw.fileno())
    except BaseException as error:
        primary = error
    try:
        if stream is not None:
            stream.close()
        raw.flush()
        os.fsync(raw.fileno())
    except BaseException as error:
        if primary is None:
            primary = error
        else:
            cleanup.append(error)
    try:
        raw.close()
    except BaseException as error:
        if primary is None:
            primary = error
        else:
            cleanup.append(error)
    if primary is not None:
        for previous, following in zip(cleanup, cleanup[1:]):
            following.__cause__ = previous
        if cleanup:
            raise primary from cleanup[-1]
        raise primary
    fsync_dir(Path(path).parent)
    return file_sha(path)


def prepare(root, output, *, corpus_root, source_hashes_override=None):
    """Create one exclusive observed input snapshot. No candidate is evaluated."""
    root, output = Path(root).resolve(), Path(output).resolve()
    require(output.parent.is_dir(), "Snapshot parent must already exist")
    output.mkdir()
    fsync_dir(output.parent)
    try:
        sources = source_hashes(root) if source_hashes_override is None else deepcopy(source_hashes_override)
        require(type(sources) is dict and sources, "Complete prospective source bindings required")
        for name, expected in sources.items():
            path = (root / name).resolve()
            require(path.is_relative_to(root) and file_sha(path) == expected, "Prospective source bytes changed")
        parents = parent_binding(root)
        bundles = build_bundles(source_specs(root), corpus_root)
        records = historical_records(root, bundles) + native_records(root, bundles)
        values = snapshot_values(records, bundles)
        require(all(reference["path"] in parents["sha256"] for record in records
                    for reference in record["parent_refs"].values()), "Original referenced ledger missing from parent closure")
        write_rows(output / "records.jsonl.gz", values["records"])
        write_rows(output / "operands.jsonl.gz", values["operands"])
        write_rows(output / "source-bundles.jsonl.gz", [bundles[key] for key in sorted(bundles)])
        write_json(output / "control-references.json", {"schema": SCHEMA, "controls": values["controls"]})
        write_json(output / "input-manifest.json", values["manifest"])
        require(same(sources, source_hashes(root)) if source_hashes_override is None else
                all(file_sha(root / name) == digest for name, digest in sources.items()),
                "Source closure changed during preparation")
        # Recheck immutable authority after all reads, before publishing success.
        verify_blobs(root, parents["sha256"])
        plan = {"schema": SCHEMA, "status": "prepared", "classification": "observed_development_including_attrs",
            "parent_binding": parents, "source_sha256": sources, "source_bound_blob_count": len(sources),
            "source_closure_override": source_hashes_override is not None,
            "artifact_sha256": {name: file_sha(output / name) for name in ARTIFACTS}, "counts": values["counts"],
            "calls": deepcopy(ZERO_CALLS), "selector_operand_fields": list(OPERAND_FIELDS),
            "limits": deepcopy(LIMITS), "corpus_root_at_preparation": str(Path(corpus_root).resolve()),
            "native_warm_latency_measured": False, "automatic_default_change": False,
            "gate": {"zero_individual_losses_against": ["legacy", "coverage_control", "prior_role"],
                     "at_least_one_distinct_new_complete_multi_positive_vs": "prior_role"}}
        write_json(output / "frozen-plan.json", plan)
        return plan
    except BaseException as exc:
        write_json(output / "prepare-failure.json", {"schema": SCHEMA, "status": "failed",
            "exception_type": type(exc).__name__, "exception": str(exc), "new_outcomes_computed": 0})
        raise


def load_prepared(output, *, root=ROOT, verify=True):
    """Load self-contained operands; verify saved authority, never read the corpus."""
    output, root = Path(output).resolve(), Path(root).resolve()
    plan = read(output / "frozen-plan.json")
    require(plan.get("schema") == SCHEMA and plan.get("status") == "prepared"
            and same(plan.get("calls"), ZERO_CALLS) and same(plan.get("counts"), COUNTS)
            and same(plan.get("limits"), LIMITS)
            and plan.get("selector_operand_fields") == list(OPERAND_FIELDS), "Snapshot plan schema/counts/calls differ")
    require(set(path.name for path in output.iterdir()) == {*ARTIFACTS, "frozen-plan.json"}
            and set(plan["artifact_sha256"]) == set(ARTIFACTS), "Complete exclusive snapshot artifact membership required")
    for name, digest in plan["artifact_sha256"].items():
        require(file_sha(output / name) == digest, "Snapshot artifact bytes changed: " + name)
    if verify:
        require(type(plan.get("source_closure_override")) is bool and same(plan["source_sha256"], source_hashes(root))
                and plan["source_bound_blob_count"] == len(plan["source_sha256"]), "Prospective executing source closure changed")
        require(same(plan["parent_binding"], parent_binding(root)), "Immutable original parent authority changed")
    bundle_rows = [value for _, value, _ in rows(output / "source-bundles.jsonl.gz")]
    bundles = unique(bundle_rows, "id")
    records = [value for _, value, _ in rows(output / "records.jsonl.gz")]
    if verify:
        specs = source_specs(root)
        require({bundle["dataset"] for bundle in bundles.values()} == set(specs), "Original source dataset membership changed")
        for bundle in bundles.values():
            spec = specs[bundle["dataset"]]
            require(same({name: value["sha256"] for name, value in bundle["files"].items()}, spec["source_sha256"])
                    and same(bundle["inventory"], spec["inventory"])
                    and same(bundle["definition_keys"], spec["definition_keys"])
                    and same(bundle["original_graph_edges"], spec.get("graph_edges", []))
                    and same(bundle["original_revision"], spec.get("revision")),
                    "Self-contained source bundle differs from original frozen authority")
        require(same(records, historical_records(root, bundles) + native_records(root, bundles)),
                "Prepared decisions differ from exact original saved three-control authority")
    values = snapshot_values(records, bundles)
    operands = [value for _, value, _ in rows(output / "operands.jsonl.gz")]
    require(same(operands, values["operands"]) and same(read(output / "input-manifest.json"), values["manifest"])
            and same(read(output / "control-references.json"), {"schema": SCHEMA, "controls": values["controls"]}),
            "Snapshot evaluator/operand/control/manifest cross-binding differs")
    return {"plan": plan, "records": records, "operands": operands, "source_bundles": bundles,
            "controls": values["controls"], "parents": plan["parent_binding"]}
