#!/usr/bin/env python3
"""Independently audit retained native guard receipts; never launch a host or model."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from datetime import datetime
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = 'def acceptance_message():\n    return "authored fixture ready"\n'
HASH = re.compile(r"[0-9a-f]{64}\Z")
PROTOCOL = (
    "scripts/acceptance-host.cjs", "test/host/acceptance.ts", "dist/test/host/acceptance.js",
    "scripts/package-host.cjs", "scripts/host-config.cjs", "scripts/prepare-host.cjs",
    "scripts/smoke-host.cjs",
)
SETTINGS = {
    "telemetry.telemetryLevel": "off", "extensions.autoCheckUpdates": False,
    "extensions.autoUpdate": False, "update.mode": "none", "workbench.startupEditor": "none",
    "security.workspace.trust.enabled": True, "security.workspace.trust.startupPrompt": "never",
    "security.workspace.trust.banner": "never",
}
COMMON_CHECKS = {
    "two_authored_roots", "requested_trust_observed_before_commands", "no_initial_product_http",
    "installed_payload_path", "activation",
}
MODE_CHECKS = {
    "restricted": {"api_guard_refused", "product_command_guards_refused", "no_http_after_restricted_guards"},
    "trusted": {"wrong_repository_refused", "correct_repository_connected", "literal_saved_result",
                "connected_folder_removal_disconnected", "no_http_after_removed_folder_commands",
                "owned_listener_refusal_observed", "offline_operations_settled_with_error"},
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00").replace(" UTC", "+00:00"))


def inventory(contents):
    return {name: {"sha256": digest(raw), "bytes": len(raw)} for name, raw in contents.items()}


def archive_proof(vsix, frozen):
    raw = vsix.read_bytes()
    if digest(raw) != frozen["vsix_sha256"] or len(raw) != frozen["vsix_bytes"]:
        raise ValueError("Frozen VSIX identity")
    with zipfile.ZipFile(vsix) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or any(PurePosixPath(name).is_absolute()
                or ".." in PurePosixPath(name).parts or "\\" in name for name in names):
            raise ValueError("Unsafe VSIX membership")
        contents = {name: archive.read(name) for name in names if not name.endswith("/")}
    if {name: digest(value) for name, value in contents.items()} != frozen["members_sha256"]:
        raise ValueError("Exact frozen VSIX members")
    manifest = json.loads(contents["extension/package.json"])
    if manifest != frozen["extension_manifest"] or manifest["license"] != "UNLICENSED":
        raise ValueError("Frozen manifest identity")
    payload = {name.removeprefix("extension/"): value for name, value in contents.items()
               if name.startswith("extension/")}
    if ".vsixmanifest" in payload:
        raise ValueError("Container collision")
    payload[".vsixmanifest"] = contents["extension.vsixmanifest"]
    return {"vsix": {"sha256": digest(raw), "bytes": len(raw)}, "manifest": manifest,
            "installed": inventory(payload), "archive_members": len(contents)}


def audit_mode(record, mode, package, protocol, executable_sha):
    errors = []

    def require(condition, label):
        if not condition:
            errors.append(label)

    try:
        require(mode in MODE_CHECKS and record["mode"] == mode and record["passed"] is True
                and "error" not in record and record["model_loaded"] is False, "Successful model-free mode")
        require(record["vsix"] == package["vsix"] and record["vsix_unchanged"] is True,
                "Exact frozen asset")
        require(record["protocol_before"] == record["protocol_after"] == protocol
                and set(protocol) == set(PROTOCOL) and record["protocol_unchanged"] is True,
                "Protocol before/after/source byte binding")
        require(record["settings"] == SETTINGS and record["test_electron_version"] == "3.1.0",
                "Owned settings and pinned SDK")
        prepare = record["host_preparation"]
        require(prepare["version"] == "1.141.0" and prepare["platform"] == "darwin-arm64"
                and prepare["commit"] == "2a59476c9bfcb90b3ddc372c36762471b7dfad1c"
                and prepare["executable_sha256"] == executable_sha
                and prepare["test_electron_version"] == "3.1.0", "Pinned native executable")
        for field in ("install_receipt", "host_exit"):
            receipt = record[field]
            require(type(receipt["code"]) is int and receipt["code"] == 0
                    and receipt["signal"] is None and receipt["owned_process_group_stopped"] is True,
                    field + " exit and owned-group cleanup")
        require(record["owned_profile_removed"] is True and record["owned_listener_closed"] is True,
                "Owned profile/listener cleanup")
        manifest, expected = package["manifest"], package["installed"]
        extension_id = manifest["publisher"] + "." + manifest["name"]
        installed = "<acceptance-run>/extensions/" + extension_id + "-" + manifest["version"]
        require(record["installed_extension_path"] == installed
                and manifest["capabilities"]["untrustedWorkspaces"]["supported"] is False,
                "Installed identity and manifest trust capability")
        install = record["install_arguments"]
        require(install[1:] == ["--user-data-dir=<acceptance-run>/user-data",
                "--extensions-dir=<acceptance-run>/extensions", "--install-extension", "<release-vsix>",
                "--force", "--do-not-sync"], "Exclusive local installer arguments")
        launch = record["launch_arguments"]
        wanted = ["<acceptance-run>/acceptance.code-workspace", "--extensionDevelopmentPath=" + installed,
                  "--extensionTestsPath=<extension-source>/dist/test/host/acceptance.js",
                  "--user-data-dir=<acceptance-run>/user-data", "--extensions-dir=<acceptance-run>/extensions",
                  "--disable-extensions"]
        if mode == "trusted":
            wanted.append("--disable-workspace-trust")
        wanted += ["--disable-updates", "--skip-welcome", "--skip-release-notes", "--no-cached-data"]
        require(launch[1:] == wanted, "Mode-specific trust/installed-path launch arguments")
        require(record["payload_before_host"] == record["payload_after_host"]
                and record["installed_inventory_before_validation"] == record["payload_before_host"]["files"],
                "Unchanged installed payload and before-validation inventory")
        proof = record["payload_before_host"]
        require(set(proof["files"]) == set(expected), "Exact installed membership")
        for name, wanted_file in expected.items():
            if name != "package.json":
                require(proof["files"].get(name) == wanted_file, "Installed byte identity: " + name)
        meta = proof["manifest"]
        raw_manifest = proof["files"]["package.json"]
        require(meta["normalized"] == meta["archive_normalized"] == manifest
                and "__metadata" not in meta["normalized"]
                and meta["id"] == extension_id and meta["version"] == manifest["version"]
                and meta["main"] == manifest["main"] and meta["sha256"] == raw_manifest["sha256"]
                and bool(HASH.fullmatch(raw_manifest["sha256"])) and type(raw_manifest["bytes"]) is int
                and raw_manifest["bytes"] > 0 and type(meta["installer_metadata_present"]) is bool
                and meta["archive_sha256"] == expected["package.json"]["sha256"]
                and meta["matches_except_installer_metadata"] is True, "Installed semantic manifest/hash binding")
        require(proof["container_metadata"] == {".vsixmanifest": {"archive_member": "extension.vsixmanifest",
                **expected[".vsixmanifest"], "matches_exact_bytes": True}}, "Exact mapped container metadata")
        require(proof["compiled_files"] == {name: value for name, value in expected.items()
                if name.startswith("dist/src/")}, "All compiled runtime bytes")
        fixture, source_hash = record["fixture"], digest(SOURCE.encode())
        require(fixture["source"] == SOURCE and fixture["source_sha256"] == source_hash
                and fixture["model_loaded"] is False, "Independent authored fixture bytes")
        require(record["source_before"] == record["source_after"] == {"a": source_hash, "b": source_hash}
                and record["source_unchanged"] is True, "Both saved repositories stayed unchanged")
        state = record["host"]
        require(state["mode"] == mode and state["passed"] is True and "error" not in state
                and state["observed_trusted"] is (mode == "trusted")
                and state["ordinary_profile_enablement_tested"] is False and state["model_loaded"] is False
                and state["vscode_version"] == "1.141.0", "Actual trust/host identity and explicit limitations")
        require(set(state["checks"]) == COMMON_CHECKS | MODE_CHECKS[mode]
                and all(value is True for value in state["checks"].values()), "All scoped executed guard assertions")
        require(state["extension_path"] == installed and state["initial_workspace_folders"]
                == [fixture["b"], fixture["a"]], "Actual installed path/two-root order")
        start, finish = timestamp(record["started_utc"]), timestamp(record["finished_utc"])
        require(start <= timestamp(state["started_utc"]) <= timestamp(state["finished_utc"]) <= finish
                and 0 < (finish - start).total_seconds() < 180, "Bounded ordered observer timestamps")
        requests, controls = record["fixture_requests"], record["fixture_controls"]
        for request in requests:
            require(timestamp(state["started_utc"]) <= timestamp(request["observed_utc"])
                    <= timestamp(state["finished_utc"]) and request["status"] == 200, "Received request interval/status")
        receipt_control = {"endpoint": "/__acceptance_receipts", "method": "GET"}
        if mode == "restricted":
            require(requests == [] and controls == [receipt_control] * 2, "Zero restricted product HTTP attempts")
            for key in ("connect_refusal", "search_refusal", "status_refusal"):
                require("trusted local desktop workspace" in state[key], "Restricted refusal: " + key)
        else:
            require([(row["method"], row["endpoint"]) for row in requests]
                    == [("GET", "/status"), ("GET", "/status"), ("POST", "/search"), ("GET", "/status")],
                    "Exact trusted received HTTP attempt sequence")
            require(controls == [receipt_control] * 4 + [{"endpoint": "/__acceptance_stop", "method": "POST"}],
                    "Owned observer controls and host-driven stop")
            for key, phase in (("wrong_repository_state", "error"), ("after_folder_removal", "disconnected")):
                view = state[key]
                require(view["phase"] == phase and view["status"] is None
                        and view["entries"] == [] and view["response"] is None, "Cleared state: " + key)
            require("match" in state["wrong_repository_state"]["message"], "Wrong-root refusal message")
            require("Connect to the selected repository" in state["removed_search_refusal"]
                    and "open local workspace folder" in state["removed_connect_refusal"], "Removed-folder guards")
            for key in ("offline_state", "offline_search_state"):
                view = state[key]
                require(view["phase"] == "error" and view["entries"] == [] and view["response"] is None
                        and "local search service" in view["message"]
                        and view["status"]["repo"] == fixture["a"], "Offline operation without result: " + key)
            call = requests[2]
            request, response = call["request"], call["response"]
            require(all(type(request[key]) is int for key in ("candidates", "max_results", "source_budget", "token_budget"))
                    and request["rerank"] is False and request["include_dependencies"] is False,
                    "Strict request option types")
            require({key: request[key] for key in ("query", "retrieval", "rerank", "candidates", "max_results",
                    "source_budget", "token_budget", "include_dependencies")} == {
                    "query": "authored behavior", "retrieval": "dense", "rerank": False, "candidates": 4,
                    "max_results": 8, "source_budget": 12000, "token_budget": 4096, "include_dependencies": False}
                    and not request.get("overlays"), "Exact saved-source fixture search settings")
            require(response["repo"] == fixture["a"] and response["source_root"] == "src"
                    and response["query"] == request["query"] and response["retrieval"] == "dense"
                    and response["rerank"] is False and response["acceptance_fixture"] is True
                    and len(response["results"]) == 1 and response["total_source_chars"] == len(SOURCE)
                    and response["source_budget"] == 12000 and response["budget_exhausted"] is False
                    and response["match_status"] == "candidates", "Authored saved response identity/counts")
            hit = response["results"][0]
            require(all(type(hit[key]) is int for key in ("start_line", "end_line", "window_end_line")),
                    "Strict physical line types")
            require(hit == {"id": "src/acceptance.py:acceptance_message:1-2", "path": "src/acceptance.py",
                    "symbol": "acceptance_message", "start_line": 1, "end_line": 2, "window_end_line": 2,
                    "source": SOURCE, "source_hash": source_hash, "content_hash": source_hash,
                    "retrieval_score": None, "h2o_score": None, "truncated": False, "origin": "retrieval",
                    "related_to": None}, "Literal source, physical bounds and source hashes")
            require(response["token_usage"] == {"encoding": "cl100k_base", "tokens": 128, "budget": 4096,
                    "truncated": False} and response["indexstats"] == {"total_windows": 1, "source_manifest": source_hash},
                    "Explicit fixture placeholder metadata")
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as error:
        require(False, "Malformed acceptance receipt: " + type(error).__name__)
    return {"passed": not errors, "errors": errors, "actual_trust": record.get("host", {}).get("observed_trusted"),
            "received_product_requests": len(record.get("fixture_requests", [])),
            "authenticated_control_requests": len(record.get("fixture_controls", [])),
            "installed_members_checked": len(package["installed"]), "protocol_files_checked": len(protocol)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--package-freeze", type=Path, required=True)
    parser.add_argument("--vsix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    raw_plan = (args.directory / "frozen-plan.json").read_bytes()
    plan = json.loads(raw_plan)
    frozen_raw = args.package_freeze.read_bytes()
    package = archive_proof(args.vsix, json.loads(frozen_raw))
    if plan["vsix_sha256"] != package["vsix"]["sha256"] or plan["vsix_bytes"] != package["vsix"]["bytes"]:
        raise ValueError("Acceptance/package plan asset differs")
    if plan["modes_in_order"] != ["restricted", "trusted"] or plan["retries"] != 0 or plan["maximum_host_seconds_per_mode"] != 175:
        raise ValueError("Prospective two-mode settings differ")
    ref, protocol = plan["source_ref"], {}
    for name in PROTOCOL:
        path = ROOT / "extensions/vscode" / name
        raw = path.read_bytes()
        if not name.startswith("dist/"):
            original = subprocess.check_output(["git", "-C", str(ROOT), "show", f"{ref}:extensions/vscode/{name}"], timeout=10)
            if raw != original:
                raise ValueError("Current/source-reference protocol differs: " + name)
        protocol[name] = {"sha256": digest(raw), "bytes": len(raw)}
    records, raw_records = {}, {}
    for mode in plan["modes_in_order"]:
        raw_records[mode] = (args.directory / (mode + ".json")).read_bytes()
        records[mode] = json.loads(raw_records[mode])
    executable = ROOT / "extensions/vscode" / records["restricted"]["host_preparation"]["executable_relative"]
    executable_sha = digest(executable.read_bytes())
    audits = {mode: audit_mode(records[mode], mode, package, protocol, executable_sha) for mode in records}
    correction_raw = (args.directory / "chronology-correction.json").read_bytes()
    correction = json.loads(correction_raw)
    commit_time = subprocess.check_output(["git", "-C", str(ROOT), "show", "-s", "--format=%cI", ref], timeout=10).decode().strip()
    ordered = (timestamp(commit_time) <= timestamp(correction["plan_filesystem_write_utc"])
               <= timestamp(records["restricted"]["started_utc"])
               < timestamp(records["restricted"]["finished_utc"])
               < timestamp(records["trusted"]["started_utc"]) < timestamp(records["trusted"]["finished_utc"]))
    corrected = (correction["original_declared_frozen_utc"] == plan["frozen_utc"]
                 and correction["source_commit"] == ref and ordered
                 and timestamp(plan["frozen_utc"]) > timestamp(records["trusted"]["finished_utc"]))
    result = {"classification": "Independent CPU audit of authored HTTP native guard acceptance",
              "passed": all(row["passed"] for row in audits.values()) and corrected,
              "modes": audits, "asset": package["vsix"], "vsix_members_checked": package["archive_members"],
              "source_reference": ref, "tracked_protocol_files_rechecked_against_git": 6,
              "compiled_host_entry_rechecked_against_current_bytes": 1,
              "chronology": {"correction_consistent": corrected, "source_commit_utc": commit_time,
                             "original_declared_time_preserved": plan["frozen_utc"],
                             "reported_plan_write_utc": correction["plan_filesystem_write_utc"],
                             "runtime_order_consistent": ordered},
              "artifact_sha256": {mode: digest(raw) for mode, raw in raw_records.items()},
              "plan_sha256": digest(raw_plan), "chronology_correction_sha256": digest(correction_raw),
              "package_freeze_sha256": digest(frozen_raw), "auditor_sha256": digest(Path(__file__).read_bytes()),
              "limits": [
                  "Authored deterministic HTTP fixture, no real backend/model/retrieval quality or performance claim. The 128-token field is a placeholder, not an independent encoding or provider usage.",
                  "Actual native Development Host guards with trust=false are observed. Ordinary installed-profile Restricted Mode enablement is not established.",
                  "Received attempt totals/sequence are retained independently of host assertions. Per-action no-extra-HTTP checks, TCP refusal, source reads and cleanup rely additionally on the frozen executed assertions; per-action observer snapshots were not retained.",
                  "Exact VSIX bytes and all captured non-manifest installed member hashes match. Installed raw package.json __metadata values are withheld, so its captured raw SHA is not reconstructed; retained normalized manifests match exactly.",
                  "Six tracked protocol files match the frozen Git revision; the ignored compiled test entry matches retained before/after hashes and current bytes, without recompiling or claiming independent build reproduction.",
                  "The original approximate plan time is later than execution. A separate correction reports earlier filesystem creation; commit and runtime order are consistent, but that filesystem-time observation is not independently signed.",
              ]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
