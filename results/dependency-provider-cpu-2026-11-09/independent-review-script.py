"""Read-only stdlib inspection; never imports or reruns either provider."""
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.metadata
import json
import marshal
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "results/dependency-provider-cpu-2026-11-09"
NATIVE = ROOT / "results/role-native-holdout-v2-2026-11-03"
FREEZE = "8b17573decb0e1ae306c3eb375d4fa0a1b7d1fa3"
EXECUTION = "9e0d00d2ef18fe483d41249b72736e87c47586d6"
PUBLICATION = "b08c784162c4a89192c1cd0a1bf94f6d35edddcb"
SOURCES = (
    "experiments/__init__.py", "experiments/dependency_context/__init__.py",
    "experiments/dependency_context/provider_profile.py", "experiments/dependency_context/fast_provider.py",
    "tests/test_dependency_provider_profile.py", "tests/test_dependency_fast_provider.py",
)


def require(value, message):
    if not value:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def blob_sha(commit, name):
    process = subprocess.Popen(["git", "-C", str(ROOT), "show", commit + ":" + name], stdout=subprocess.PIPE)
    digest = hashlib.sha256()
    for block in iter(lambda: process.stdout.read(1024 * 1024), b""):
        digest.update(block)
    require(process.wait() == 0, "Missing Git authority " + name)
    return digest.hexdigest()


def ancestor(first, second):
    require(subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", first, second],
        stdout=subprocess.DEVNULL).returncode == 0, "Commit chronology differs")


def zero_work(value):
    require(set(value) == {"new_model_forwards", "query_encodings", "document_encodings", "sqlite_accesses",
        "retrieval_calls", "network_calls"} and all(type(item) is int and item == 0 for item in value.values()),
        "Reported CPU diagnostic work differs")


def duration(value):
    require(type(value) is float and math.isfinite(value) and value > 0, "Positive finite CPU interval required")


def review():
    paired, original = BASE / "paired", BASE / "original-profile"
    ps, po = read(paired / "summary.json"), read(original / "summary.json")
    rs, ro = read(paired / "run-start.json"), read(original / "run-start.json")
    head = git("rev-parse", "HEAD").decode().strip()
    ancestor(FREEZE, EXECUTION)
    ancestor(EXECUTION, head)
    frozen = rs["source_freeze"]
    require(frozen["commit"] == FREEZE and frozen["execution_commit"] == EXECUTION
        and frozen == ps["source_freeze"] and set(frozen["source_sha256"]) == set(SOURCES), "Exact source freeze differs")
    for name, value in frozen["source_sha256"].items():
        require(file_sha(ROOT/name) == value == blob_sha(FREEZE, name) == blob_sha(EXECUTION, name) == blob_sha(head, name),
            "Frozen executing source differs " + name)
    require(ro["git_head"] == EXECUTION and ro["profiler_sha256"] == frozen["source_sha256"][SOURCES[2]],
        "Original profiler execution differs")
    expected_runtime = {"python": platform.python_version(), "executable": sys.executable,
        "platform": platform.platform(), "numpy": importlib.metadata.version("numpy")}
    require(rs["runtime"] == ro["runtime"] == expected_runtime, "Recorded installed runtime differs")
    commit_time = datetime.fromisoformat(git("show", "-s", "--format=%cI", EXECUTION).decode().strip())
    for start in (rs, ro):
        require(commit_time <= datetime.fromisoformat(start["created_at_utc"]) <= datetime.now(timezone.utc),
            "Diagnostic started before execution commit")
        require("both factory constructors" in start["timing_boundary"] and "fast clone setup" in start["timing_boundary"],
            "Provider timer setup exclusions absent")
    require(ps["status"] == po["status"] == "passed" and ps["binding"] == po["binding"] == rs["binding"],
        "Study/result binding differs")
    require(ro["binding"] == ps["binding"], "Original profiler binding differs")
    binding = ps["binding"]
    require(binding["publication_commit"] == PUBLICATION and binding["published_blobs_verified"] is True,
        "Original publication authority absent")
    for name, value in {**binding["artifact_sha256"], **binding["original_source_sha256"]}.items():
        require(file_sha(ROOT/name) == value == blob_sha(PUBLICATION, name), "Original native authority differs " + name)
    require(binding["archive_path"] == (NATIVE/"attempt/searches.jsonl.gz").relative_to(ROOT).as_posix()
        and binding["archive_sha256"] == file_sha(ROOT/binding["archive_path"]), "Native compressed archive differs")
    audit = read(NATIVE/"independent-audit.json")
    require(audit["status"] == "passed" and audit["checks_passed"] is True
        and audit["verified_requests"] == 252 and audit["verified_warmups"] == 6,
        "Original native audit not complete")
    saved, first_selection, selected_counts = [], [], Counter()
    with gzip.open(ROOT/binding["archive_path"], "rb") as stream:
        for line, raw in enumerate(stream, 1):
            row = json.loads(raw)
            if row["arm"] != "candidate":
                continue
            capture = row["candidate_capture"]
            active = bool(capture["provider_receipts"])
            require((active and len(capture["provider_receipts"]) == 1 and capture["provider_receipts"][0]["status"] == "active")
                or (not active and capture["features"] is None and row["resident_reads"] == []), "Unexpected original inactive state")
            identity = [row["query_id"], row["retrieval"], row["repetition"], "candidate"]
            source = {"identity": identity, "jsonl_line": line, "line_sha256": sha(raw), "canonical_record_sha256": sha(canonical(row))}
            if active:
                require(len(row["resident_reads"]) == 1, "Original active resident lookup absent")
                require(len(capture["features"]["windows"]) == len(row["resident_reads"][0]["returned_vectors"]),
                    "Original document-vector membership differs")
            item = {"identity": identity, "source": source, "active": active,
                "features_sha256": sha(canonical(capture["features"])),
                "receipts_sha256": sha(canonical(capture["provider_receipts"])),
                "raw_rows": len(capture["ranked_candidates"]),
                "windows": len(capture["features"]["windows"]) if active else 0,
                "facets": len(capture["features"]["facets"]) if active else 0}
            saved.append(item)
            key = row["retrieval"], active
            if row["repetition"] == 1 and selected_counts[key] < (2 if active else 1):
                first_selection.append(item)
                selected_counts[key] += 1
    identities = [tuple(row["identity"]) for row in saved]
    queries = {row[0] for row in identities}
    require(len(saved) == len(set(identities)) == 84 and len(queries) == 14
        and set(identities) == {(query, mode, repeat, "candidate") for query in queries for mode in ("dense", "hybrid")
                              for repeat in (1, 2, 3)}, "Original candidate matrix differs")
    require(len(first_selection) == 6 and len(ps["records"]) == 84, "Record membership differs")
    require({path.name for path in paired.iterdir()} == {"run-start.json", "summary.json",
        *(f"record-{number:02d}.json" for number in range(1, 85))}, "Paired directory artifact closure differs")
    timings = {mode: {name: [] for name in ("original", "fast")} for mode in ("dense", "hybrid")}
    orders, row_counts, replay_calls = Counter(), Counter(), Counter()
    window_counts = Counter()
    for ordinal, (expected, row) in enumerate(zip(saved, ps["records"], strict=True)):
        require(read(paired/f"record-{ordinal+1:02d}.json") == row, "Paired raw record differs from summary")
        require(row["identity"] == expected["identity"] and row["source_record"] == expected["source"]
            and row["bypassed"] is (not expected["active"]) and row["exact_equivalence"] is True
            and row["raw_rows"] == expected["raw_rows"] and row["full_vector_windows"] == expected["windows"],
            "Saved request identity/source/equivalence binding differs")
        mode = row["identity"][1]
        row_counts[mode, "active" if expected["active"] else "bypass"] += 1
        window_counts[expected["windows"]] += 1
        require(len(row["passes"]) == 3, "Three CPU repetitions required")
        per_record = {name: [] for name in ("original", "fast")}
        for repeat, entry in enumerate(row["passes"]):
            order = ["original", "fast"] if (ordinal+repeat) % 2 == 0 else ["fast", "original"]
            require(entry["repetition"] == repeat+1 and entry["order"] == order, "Alternating measurement order differs")
            orders[mode, order[0], "active" if expected["active"] else "bypass"] += 1
            for name in ("original", "fast"):
                result = entry[name]
                require(result["features_sha256"] == expected["features_sha256"]
                    and result["receipts_sha256"] == expected["receipts_sha256"], "Recorded digest differs from original exact saved features/receipts")
                count = 1 if expected["active"] else 0
                require(result["calls"] == {"saved_clause_vector_replay": count, "saved_resident_vector_replay": count,
                    "saved_snapshot_confirmation": 3*count}, "Saved-data replay call boundary differs")
                for key, value in result["calls"].items():
                    replay_calls[key] += value
                if expected["active"]:
                    duration(result["wall_s"])
                    require(result["execution"] == name + "_provider_with_saved_float32_arrays", "Implementation identity differs")
                    timings[mode][name].append(result["wall_s"])
                    per_record[name].append(result["wall_s"])
                else:
                    require(result["wall_s"] is None and result["execution"] == "native_adapter_bypassed_provider",
                        "Native bypass invented a provider measurement")
        expected_medians = {name: statistics.median(value) if value else None for name, value in per_record.items()}
        require(row["median_provider_call_s"] == expected_medians, "Per-request CPU medians differ")
        require(all(type(row[name]) is int and row[name] == 0 for name in ("new_model_forwards", "retrieval_calls", "sqlite_accesses")),
            "Unexpected reported actual work")
    by_mode = {}
    for mode in ("dense", "hybrid"):
        values = timings[mode]
        medians = {name: statistics.median(value) for name, value in values.items()}
        expected = {"saved_requests": 42, "active_requests": 39, "adapter_bypasses": 3,
            "cpu_provider_call_median_s": medians, "measured_cpu_calls": {name: 117 for name in values}}
        require(row_counts[mode, "active"] == 39 and row_counts[mode, "bypass"] == 3
            and all(len(value) == 117 for value in values.values()) and ps["by_mode"][mode] == expected,
            "Mode totals or independently recomputed medians differ")
        by_mode[mode] = {**expected, "ratio_of_cpu_medians_original_over_fast": medians["original"]/medians["fast"],
            "cpu_median_reduction_percent": 100*(1-medians["fast"]/medians["original"])}
    zero_work(ps["actual_work"])
    zero_work(po["actual_work"])
    require(ps["every_feature_receipt_and_call_count_exact"] is True, "Exact result status absent")
    require({path.name for path in original.iterdir()} == {"run-start.json", "summary.json",
        *(f"record-{number:02d}.json" for number in range(1, 7)), *(f"record-{number:02d}.pstats" for number in range(1, 7))},
        "Original profile artifact closure differs")
    profile_rows = []
    require(len(po["records"]) == 6, "Original profile selection count differs")
    for ordinal, (expected, row) in enumerate(zip(first_selection, po["records"], strict=True), 1):
        require(read(original/f"record-{ordinal:02d}.json") == row and row["source_record"] == expected["source"]
            and row["identity"] == expected["identity"] and row["raw_rows"] == expected["raw_rows"]
            and row["full_vector_windows"] == expected["windows"], "Fixed original-profile selection differs")
        require(row["equivalence"] == {"features_sha256": expected["features_sha256"],
            "provider_receipts_sha256": expected["receipts_sha256"], "all_passes_exact": True}, "Profile equivalence differs")
        stats_path = original/f"record-{ordinal:02d}.pstats"
        require(file_sha(stats_path) == row["pstats_sha256"], "Saved cProfile bytes differ")
        with stats_path.open("rb") as stream:
            stats = marshal.load(stream)
            require(stream.read() == b"" and type(stats) is dict, "Unexpected cProfile transport")
        table = [{"file": str(Path(file).relative_to(ROOT)) if Path(file).is_relative_to(ROOT) else file,
            "line": line, "function": function, "primitive_calls": value[0], "total_calls": value[1],
            "self_s": value[2], "cumulative_s": value[3]} for (file, line, function), value in stats.items()]
        table.sort(key=lambda value: (-value["cumulative_s"], value["file"], value["line"]))
        require(table == row["cprofile_functions"], "Published cProfile function table differs from raw pstats")
        count = 1 if expected["active"] else 0
        require(row["replay_calls_per_pass"] == {"saved_clause_vector_replay": count, "saved_resident_vector_replay": count,
            "saved_snapshot_confirmation": 3*count}, "Original-profile replay calls differ")
        if expected["active"]:
            require(len(row["uninstrumented_replay_wall_s"]) == 3, "Original profile needs three uninstrumented passes")
            for value in [*row["uninstrumented_replay_wall_s"], row["stage_instrumented_wall_s"], row["cprofile_wall_s"]]:
                duration(value)
            require(statistics.median(row["uninstrumented_replay_wall_s"]) == row["uninstrumented_replay_median_s"],
                "Original uninstrumented median differs")
            vector = [value for value in table if value["function"] == "_vector" and value["file"] == "src/semantic_search/semantic_features.py"]
            provider = [value for value in table if value["function"] == "provider" and value["file"] == "src/semantic_search/semantic_provider.py"]
            features = [value for value in table if value["function"] == "make_features" and value["file"] == "src/semantic_search/semantic_features.py"]
            require(len(vector) == len(provider) == len(features) == 1 and vector[0]["total_calls"] == 4+2*(expected["facets"]+expected["windows"])
                and features[0]["total_calls"] == 2, "Original provider vector-validation count differs")
            stage_counts = Counter(event["stage"] for event in row["stage_events"])
            require(stage_counts["_vector"] == vector[0]["total_calls"] and stage_counts["make_features"] == 2,
                "Stage call counts differ from raw cProfile")
            for event in row["stage_events"]:
                require(type(event["inclusive_s"]) is float and type(event["exclusive_s"]) is float
                    and math.isfinite(event["inclusive_s"]) and 0 <= event["exclusive_s"] <= event["inclusive_s"],
                    "Invalid nested stage intervals")
            require(sum(event["exclusive_s"] for event in row["stage_events"]) <= row["stage_instrumented_wall_s"]+1e-8,
                "Exclusive stage intervals exceed provider timer")
            profile_rows.append({"identity": expected["identity"], "windows": expected["windows"],
                "vector_calls": vector[0]["total_calls"], "make_features_calls": 2,
                "uninstrumented_provider_median_s": row["uninstrumented_replay_median_s"],
                "instrumented_provider_cumulative_s": provider[0]["cumulative_s"],
                "instrumented_vector_cumulative_s": vector[0]["cumulative_s"],
                "instrumented_vector_fraction_of_provider": vector[0]["cumulative_s"]/provider[0]["cumulative_s"]})
        else:
            require(row["uninstrumented_replay_wall_s"] == row["stage_events"] == [] and stats == {}
                and row["uninstrumented_replay_median_s"] is row["cprofile_wall_s"] is row["stage_instrumented_wall_s"] is None,
                "Original inactive request invented profile work")
    input_paths = [*paired.iterdir(), *original.iterdir(), BASE/"paired.log", BASE/"original-profile.log"]
    artifact_hashes = {path.relative_to(ROOT).as_posix(): file_sha(path) for path in sorted(input_paths)}
    limits = [
        "No producer helper or provider was imported or rerun by this review; only stdlib parsing, hashing, Git-blob checks, counts and arithmetic were performed.",
        "Reported output digests were checked against independently hashed original saved features and receipts; digests alone cannot independently reconstruct omitted runtime values.",
        "The saved CPU wall intervals cannot independently prove physical clock behavior or external process/GPU state. Zero new model/index/retrieval/network work is supported by inspected frozen replay/stub boundaries and recorded counters, not a new hardware trace.",
        "Historical native forward events were copied for receipt equivalence; no historical inference duration is treated as a newly performed forward.",
        "Only provider-call CPU time is timed: imports, both factory constructors, fast function clone setup, fixture reconstruction, real metadata generation, SQLite validation/I/O, source scans, tokenization/inference, retrieval/selection/packing and MCP work are excluded.",
        "The 84 saved native requests contain repeated original queries and three additional CPU replay repetitions. These are diagnostic timings on observed inputs, not independent quality samples, a native warm-latency result or an end-to-end speedup.",
        "cProfile materially changes elapsed time. Vector cumulative times overlap make_features/provider cumulative times and must not be added or compared directly with uninstrumented/native medians.",
    ]
    result = {"schema": "dependency_provider_cpu_independent_review_v1", "status": "passed", "checks_passed": True,
        "reviewed_at_utc": datetime.now(timezone.utc).isoformat(), "review_execution_commit": head,
        "source_freeze": frozen, "runtime": expected_runtime, "input_artifact_sha256": artifact_hashes,
        "original_native_archive_sha256": binding["archive_sha256"], "original_publication_commit": PUBLICATION,
        "paired": {"saved_candidate_requests": 84, "distinct_query_ids": 14, "active": 78, "actual_adapter_bypasses": 6,
            "cpu_repetitions_per_saved_request": 3, "timed_provider_calls_total": 468,
            "labelled_bypass_slots_total": 36, "exact_saved_feature_receipt_digest_checks": 504,
            "alternating_order_checks": 252, "by_mode": by_mode,
            "order_first_counts": [{"mode": key[0], "first": key[1], "stratum": key[2], "count": count}
                for key, count in sorted(orders.items())],
            "saved_replay_stub_calls": dict(replay_calls),
            "full_vector_window_count_distribution": {str(key): value for key, value in sorted(window_counts.items())}},
        "original_profile": {"fixed_records": 6, "active_50_raw_window_requests": 4, "actual_adapter_bypasses": 2,
            "per_active_request_passes": {"uninstrumented": 3, "nested_stages": 1, "cprofile": 1}, "attribution": profile_rows},
        "reported_new_work_verified_zero": ps["actual_work"], "limitations": limits,
        "review_script_sha256": file_sha(__file__)}
    destination = BASE/"independent-review.json"
    with destination.open("xb") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode()+b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(BASE, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    print(json.dumps({"status": "passed", "paired": result["paired"], "profile": profile_rows}, indent=2))


if __name__ == "__main__":
    review()
