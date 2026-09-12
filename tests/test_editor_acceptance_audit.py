"""Corruption checks on public receipts; no VSIX installation, HTTP or native app."""
import copy
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("acceptance_audit", ROOT / "scripts/audit_editor_acceptance.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def receipt_fixture(mode):
    directory = ROOT / "results/editor-acceptance-2026-10-13"
    record = json.loads((directory / (mode + ".json")).read_text())
    frozen = json.loads((ROOT / "results/editor-package-2026-10-13/frozen-package.json").read_text())
    # Core validator tests use published archive hashes and captured byte sizes.
    # The CLI independently recomputes both from the actual ZIP, separately.
    expected = copy.deepcopy(record["payload_before_host"]["files"])
    for name, value in expected.items():
        member = "extension.vsixmanifest" if name == ".vsixmanifest" else "extension/" + name
        value["sha256"] = frozen["members_sha256"][member]
    package = {"vsix": {"sha256": frozen["vsix_sha256"], "bytes": frozen["vsix_bytes"]},
               "manifest": frozen["extension_manifest"], "installed": expected}
    return record, package, copy.deepcopy(record["protocol_before"]), record["host_preparation"]["executable_sha256"]


@pytest.mark.parametrize("mode", ["restricted", "trusted"])
def test_retained_native_receipt_core_is_consistent(mode):
    record, package, protocol, executable = receipt_fixture(mode)
    result = audit.audit_mode(record, mode, package, protocol, executable)
    assert result["passed"], result["errors"]
    assert result["received_product_requests"] == (0 if mode == "restricted" else 4)


def corrupt(record, field):
    if field == "trust":
        record["host"]["observed_trusted"] = not record["host"]["observed_trusted"]
    elif field == "trust_override":
        record["launch_arguments"].append("--disable-workspace-trust")
    elif field == "unexpected_restricted_http":
        record["fixture_requests"].append({"endpoint": "/status", "method": "GET", "status": 200,
            "observed_utc": record["host"]["started_utc"]})
    elif field == "untrusted_support_override":
        record["settings"]["extensions.supportUntrustedWorkspaces"] = {"anna-poutanen.semantic-search-local": True}
    elif field == "protocol":
        record["protocol_before"]["scripts/acceptance-host.cjs"]["sha256"] = "0" * 64
        record["protocol_after"] = copy.deepcopy(record["protocol_before"])
    elif field == "manifest":
        record["payload_before_host"]["manifest"]["normalized"]["main"] = "./injected.js"
        record["payload_after_host"] = copy.deepcopy(record["payload_before_host"])
    elif field == "installed_extra":
        record["payload_before_host"]["files"]["injected.js"] = {"sha256": "0" * 64, "bytes": 1}
        record["payload_after_host"] = copy.deepcopy(record["payload_before_host"])
        record["installed_inventory_before_validation"] = copy.deepcopy(record["payload_before_host"]["files"])
    elif field == "container":
        record["payload_before_host"]["files"][".vsixmanifest"]["sha256"] = "0" * 64
        record["payload_after_host"] = copy.deepcopy(record["payload_before_host"])
    elif field == "cleanup":
        record["host_exit"]["owned_process_group_stopped"] = False
    elif field == "closed_listener":
        record["owned_listener_closed"] = False
    elif field == "saved_changed":
        record["source_before"]["a"] = record["source_after"]["a"] = "0" * 64
    elif field == "path":
        record["host"]["extension_path"] = "<acceptance-run>/unverified-extension"
    elif field == "extra_http":
        record["fixture_requests"].append(copy.deepcopy(record["fixture_requests"][-1]))
    elif field == "source":
        hit = record["fixture_requests"][2]["response"]["results"][0]
        hit["source"] = 'def injected():\n    return "other"\n'
        hit["source_hash"] = hit["content_hash"] = audit.digest(hit["source"].encode())
    elif field == "boolean_line":
        record["fixture_requests"][2]["response"]["results"][0]["start_line"] = True
    elif field == "boolean_option":
        record["fixture_requests"][2]["request"]["candidates"] = True
    elif field == "bounds":
        record["fixture_requests"][2]["response"]["results"][0]["end_line"] = 3
    elif field == "token_claim":
        record["fixture_requests"][2]["response"]["token_usage"]["tokens"] = 4096
    elif field == "wrong_root_residue":
        record["host"]["wrong_repository_state"]["status"] = {"repo": "wrong"}
    elif field == "removed_residue":
        record["host"]["after_folder_removal"]["entries"] = ["old result"]
    elif field == "offline_residue":
        record["host"]["offline_search_state"]["response"] = {"results": []}
    elif field == "guard_claim":
        record["host"]["checks"]["no_http_after_removed_folder_commands"] = False
    elif field == "host_stop":
        record["fixture_controls"].pop()
    else:
        raise AssertionError(field)


@pytest.mark.parametrize("mode,field", [
    ("restricted", "trust"), ("restricted", "trust_override"), ("restricted", "unexpected_restricted_http"),
    ("restricted", "untrusted_support_override"), ("restricted", "protocol"), ("restricted", "manifest"),
    ("restricted", "installed_extra"), ("restricted", "container"), ("restricted", "cleanup"),
    ("restricted", "closed_listener"), ("restricted", "saved_changed"), ("restricted", "path"),
    ("trusted", "extra_http"), ("trusted", "source"), ("trusted", "boolean_line"),
    ("trusted", "boolean_option"), ("trusted", "bounds"), ("trusted", "token_claim"),
    ("trusted", "wrong_root_residue"), ("trusted", "removed_residue"), ("trusted", "offline_residue"),
    ("trusted", "guard_claim"), ("trusted", "host_stop"),
])
def test_audit_rejects_corrupted_evidence(mode, field):
    record, package, protocol, executable = receipt_fixture(mode)
    corrupt(record, field)
    result = audit.audit_mode(record, mode, package, protocol, executable)
    assert result["passed"] is False
    assert result["errors"]


def test_archive_proof_checks_actual_member_bytes_and_exact_membership(tmp_path):
    contents = {"extension/package.json": json.dumps({"license": "UNLICENSED"}).encode(),
                "extension/main.js": b"'use strict';\n", "extension.vsixmanifest": b"container"}
    asset = tmp_path / "toy.vsix"
    with zipfile.ZipFile(asset, "w") as archive:
        for name, raw in contents.items():
            archive.writestr(name, raw)
    frozen = {"vsix_sha256": audit.digest(asset.read_bytes()), "vsix_bytes": asset.stat().st_size,
              "members_sha256": {name: audit.digest(raw) for name, raw in contents.items()},
              "extension_manifest": {"license": "UNLICENSED"}}
    result = audit.archive_proof(asset, frozen)
    assert result["installed"][".vsixmanifest"] == {"sha256": audit.digest(b"container"), "bytes": 9}
    assert result["installed"]["main.js"]["bytes"] == len(contents["extension/main.js"])
    frozen["members_sha256"]["extension/main.js"] = "0" * 64
    with pytest.raises(ValueError, match="Exact frozen VSIX members"):
        audit.archive_proof(asset, frozen)
