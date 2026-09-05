#!/usr/bin/env python3
"""Source-only independent semantic review record; no parser/ranking/model calls.

Behavior judgments below were made by reading pinned executable definitions and
nearby callers/helpers. Byte checks establish provenance, not semantic truth.
Future actual extractor eligibility and retrieval/runtime freeze remain separate.
"""
from __future__ import annotations

from collections import Counter
import fnmatch
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmarks/language_quality_manifest.json"
OUTPUT = ROOT / "results/language-preflight-2026-10-13/semantic-review.json"
SUFFIXES = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}
JUDGMENTS = {
    "express_case_insensitive_header_alias": "The same chained get/header implementation validates a nonempty string, lowercases it and special-cases referer/referrer.",
    "express_jsonp_callback_safety": "The nonempty-string branch sanitizes callback characters, escapes U+2028/U+2029 and builds a guarded callback invocation before send.",
    "express_cookie_signing_and_age": "cookie copies options, requires the signing secret, serializes object/scalar values and converts milliseconds with Date.now and Math.floor(age/1000).",
    "express_clear_cookie_ignores_age": "clearCookie overrides expires with Date(1), deletes maxAge and emits an empty cookie through the ordinary helper.",
    "express_append_preserves_headers": "append reads the previous header and concatenates scalar/array combinations before set.",
    "express_content_negotiation_fallback": "format excludes default from negotiable keys, varies Accept, dispatches a match/default or forwards a406 error.",
    "express_redirect_html_and_head": "redirect chooses text or escaped HTML, sets byte length and sends no body for HEAD.",
    "express_mounted_app_prototype_restore": "use's app-like branch mounts the child and restores the parent request/response prototypes before next(err).",
    "express_configured_query_parser": "set compiles the setting, compileQueryParser selects custom/simple/extended/disabled mode, and the query getter invokes it or returns a null-prototype empty object.",
    "express_trusted_forwarded_protocol": "set/compileTrust construct a trust predicate; protocol checks socket remoteAddress before using the first forwarded protocol token.",
    "express_view_cache_and_lookup": "render merges options and reuses/populates cache; lookup searches roots and resolve tries direct file then index.ext.",
    "express_callable_app_dispatch_context": "createApplication creates a callable listener and app-specific prototypes; handle installs them, cross-links req/res and initializes response locals before routing.",
    "express_absent_builtin_rate_limit": "All six production lib files contain middleware registration and proxy policies, with no built-in token-bucket store/enforcement or retry-after path. User middleware is outside scope.",
    "express_absent_outbound_retry": "The production core creates an inbound server and dispatches requests; it does not implement an outbound retry/backoff client. Dependencies/applications are outside scope.",
    "express_absent_cookie_encryption_rotation": "cookie delegates optional signing using the supplied secret. No production encryption or automatic rotating-key machinery implements the queried behavior.",
    "zod_sync_rejects_async": "The nested parser runs validation with async:false, rejects native Promise results, finalizes raw issues/stack or returns the parsed value; factory and nested fn cover the same required body.",
    "zod_flatten_errors": "The concrete overload groups messages by the first nonempty path segment, otherwise into formErrors. Declaration-only overloads contain no required loop.",
    "zod_tree_error_paths": "The walker recursively expands union/key/element errors and builds properties/items nodes. Parent and nested walker both contain the required executable loops.",
    "zod_decimal_multiple_tolerance": "floatSafeRemainder compares the quotient to its rounded integer using4*EPSILON*max(abs(ratio),1), returning zero inside that tolerance.",
    "zod_error_parameter_normalization": "normalizeParams wraps strings, migrates/deletes legacy message, rejects simultaneous message/error and wraps a string-valued error.",
    "zod_numeric_enum_reverse_entries": "getEnumValues collects numeric values and filters their numeric reverse-map keys before returning remaining values.",
    "zod_urlsafe_base64_bytes": "base64urlToUint8Array restores the alphabet and modulo-four padding before the byte decoder call.",
    "zod_local_json_schema_refs": "resolveRef rejects external refs, dispatches root or version-specific definition namespace, decodes the selected pointer segment and rejects failed definition lookup; nearby decoder handles~1 then~0.",
    "zod_safe_parse_lazy_error_redaction": "safeParse dispatches failure; the error getter constructs/caches lazily and releases captured raw issues/context; finalizeIssue chooses message precedence and copies input only when reportInput is requested.",
    "zod_custom_refinement_issue_context": "The public wrapper delegates to the collector; addIssue supports string/structured issues, default custom code/input and fatal continuation behavior before invoking the callback.",
    "zod_nested_deep_partial": "deepPartial supplies the object handler; mapInner rewrites shape/catchall children, and run explicitly calls mapInner before the handler. All three required groups now show the promised ordering.",
    "zod_metadata_attachment_registry": "meta merges attached metadata; add stores metadata and ID associations. Class field initializers explicitly create WeakMap and Map, so the weak-storage requirement is executable evidence.",
    "zod_absent_remote_ref_fetch": "The converter's resolver explicitly rejects nonlocal references, and indexed production source has no external HTTP reference-fetch/cache implementation.",
    "zod_absent_async_retry_backoff": "Async parsers await one result. The separate numeric-string key fallback retries a transformed key once; it is not asynchronous exponential backoff with an attempt limit.",
    "zod_absent_issue_encryption": "finalizeIssue omits input or copies it under reportInput; no production encryption/key-rotation machinery implements the queried error-reporting behavior.",
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def git(repo, *args):
    return subprocess.check_output(["git", "--no-optional-locks", "-C", str(repo), *args], stderr=subprocess.PIPE)


def lines(text):
    return [line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text) if line]


def inspect(entry, contract):
    repo = ROOT / entry["corpus"]
    dataset_path = ROOT / entry["dataset"]
    raw_dataset = dataset_path.read_bytes()
    dataset = json.loads(raw_dataset)
    checks = {"dataset_hash": sha(raw_dataset) == entry["sha256"],
              "pinned_head": git(repo, "rev-parse", "HEAD").decode().strip() == entry["revision"],
              "release_tag": git(repo, "rev-parse", entry["release"] + "^{commit}").decode().strip() == entry["revision"],
              "clean_checkout": not git(repo, "status", "--porcelain").strip(),
              "shared_full_evidence_scoring_contract": dataset["source_evidence_scoring"] == contract,
              "metadata_and_role": all(dataset[key] == entry[key] for key in
                    ("repo_url", "revision", "release", "source_root", "source_exclusions", "language")) and dataset["benchmark_role"] == entry["role"]}
    license_raw = git(repo, "show", entry["revision"] + ":LICENSE")
    checks["exact_license_bytes"] = license_raw == (ROOT / entry["license_path"]).read_bytes() and sha(license_raw) == entry["license_sha256"]
    source = {}
    tree = git(repo, "ls-tree", "-r", "-z", entry["revision"], "--", entry["source_root"])
    for item in tree.split(b"\0"):
        if not item:
            continue
        info, raw_path = item.split(b"\t", 1)
        name = raw_path.decode()
        if Path(name).suffix not in SUFFIXES or any(fnmatch.fnmatchcase(name, pattern) for pattern in entry["source_exclusions"]):
            continue
        _, object_type, object_id = info.decode().split()
        raw = (repo / name).read_bytes()
        if object_type != "blob" or hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() != object_id:
            raise ValueError(f"Source bytes differ from pinned Git blob: {name}")
        source[name] = raw
    queries, targets, groups_count, multi = [], 0, 0, 0
    for query in dataset["queries"]:
        judgment = JUDGMENTS[query["id"]]
        expected_split = "holdout" if entry["role"] == "repository_holdout" else query["split"]
        if query["split"] != expected_split:
            raise ValueError("Holdout split changed")
        gold = query.get("target_groups", [query["targets"]] if query.get("targets") else [])
        paths, reviewed_targets = set(), []
        for group in gold:
            groups_count += 1
            for target in group:
                targets += 1
                raw = source[target["path"]]
                physical = lines(raw.decode("utf-8"))
                start, end = target["definition_lines"]
                lo, hi = target["evidence_lines"]
                evidence = "".join(physical[lo-1:hi])
                valid = (1 <= start <= lo <= hi <= end <= len(physical)
                    and target["declaration_anchor"] in "".join(physical[start-1:min(start+3,end)])
                    and sha(raw) == target["source_file_sha256"] and sha(evidence.encode()) == target["evidence_sha256"])
                windows = [[a, min(a+159,end)] for a in range(start,end+1,120)]
                fits = any(a <= lo <= hi <= b for a,b in windows)
                if not valid or not fits:
                    raise ValueError(f"Invalid evidence bytes/bounds: {query['id']}:{target['symbol']}")
                paths.add(target["path"])
                reviewed_targets.append({"path":target["path"],"symbol":target["symbol"],"evidence_lines":[lo,hi],
                                         "definition_lines":[start,end],"declared_160_40_feasible":True})
        multi += len(paths) > 1
        queries.append({"query_id":query["id"],"kind":query["kind"],"split":query["split"],
                        "judgment":"source_supported","reason":judgment,"required_groups":len(gold),
                        "target_rows":reviewed_targets,
                        "absence_scope_reviewed":query.get("absence_review",{}).get("scope") if query["kind"]=="absent" else None})
    kinds = Counter(row["kind"] for row in queries)
    checks["inventory_counts"] = (len(queries)==entry["queries"] and kinds["present"]==entry["present_queries"]
        and kinds["absent"]==entry["absent_queries"] and multi==entry["multi_file_and_queries"]
        and groups_count==entry["required_evidence_groups"] and targets==entry["gold_target_rows"])
    return {"id":entry["id"],"role":entry["role"],"source_files":len(source),"source_membership_sha256":sha(json.dumps(
        {name:sha(raw)for name,raw in source.items()},sort_keys=True,separators=(",",":")).encode()),
        "checks":checks,"passed":all(checks.values()),"queries":queries,"required_groups":groups_count,"target_rows":targets}


def main():
    manifest_raw = MANIFEST.read_bytes()
    manifest = json.loads(manifest_raw)
    reviewed = [inspect(entry,manifest["source_evidence_scoring"])for entry in manifest["datasets"]]
    notices = {path:sha((ROOT/path).read_bytes()) for path in manifest["notice_files"]}
    checks = {"notice_hashes":notices==manifest["notice_files"],"all_source_supported":all(row["passed"]for row in reviewed),
              "all_thirty_queries_reviewed":sum(len(row["queries"])for row in reviewed)==len(JUDGMENTS)==30}
    attribution = (ROOT/".cache/corpora/zod/packages/zod/src/v4/core/visit.ts").read_text().splitlines()[0]
    checks["verbatim_zod_source_attribution_in_notices"] = attribution in (ROOT/"benchmarks/CORPUS_NOTICES.md").read_text()
    data = {"schema_version":1,"classification":"Independent semantic/source-byte review before language inference",
            "manifest_sha256":sha(manifest_raw),"script_sha256":sha(Path(__file__).read_bytes()),
            "notice_sha256":notices,"checks":checks,"passed":all(checks.values()),"datasets":reviewed,
            "summary":{"queries":30,"present":24,"absent":6,"multi_file_and_queries":8,
                       "required_groups":sum(row["required_groups"]for row in reviewed),"target_rows":sum(row["target_rows"]for row in reviewed)},
            "source_evidence_scoring":manifest["source_evidence_scoring"],"model_parser_retrieval_calls":0,
            "review_method":"Read each executable gold definition and nearby callers/helpers, compare scoped negative decoys, then independently reconcile pinned Git blobs, licenses, metadata, byte ranges and declared window feasibility.",
            "corrections":"Added bottom-up ordering and runtime WeakMap evidence, static-equivalent nested OR targets, complete enclosing-window coverage semantics and corpus/source-level notices before any extraction/ranking output.",
            "remaining_gates":["Actual generic extractor must independently return exact saved-source snippets with gold evidence eligibility, overload omission and exclusion policy.",
                               "Freeze extractor/grammar/index fingerprint, model/runtime settings, complete budgets/schedule and all labels before retrieval.",
                               "Calibrate only designated Express cases; do not tune ranking/thresholds from Zod holdout outcomes."],
            "limits":"Source-supported labels and scoped negatives are not exhaustive runtime/library correctness proofs. Absence excludes unindexed dependencies, examples, tests and user callbacks/plugins."}
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT.write_text(json.dumps(data,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"passed":data["passed"],"summary":data["summary"],"manifest_sha256":data["manifest_sha256"]},indent=2))
    return 0 if data["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
