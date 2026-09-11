"""Installed package receipt corruption tests; never install or launch a host."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("editor_package_audit", ROOT / "scripts/audit_editor_package.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def fixture():
    manifest = {"publisher": "generic", "name": "fixture", "version": "0.1.0",
                "main": "./dist/src/extension.js", "license": "UNLICENSED"}
    contents = {"extension/package.json": json.dumps(manifest).encode(),
                "extension/dist/src/extension.js": b"exports.activate=()=>{};\n",
                "extension.vsixmanifest": b"<generic/>"}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    raw = buffer.getvalue()
    frozen = {"vsix_sha256": audit.digest(raw), "vsix_bytes": len(raw),
              "members_sha256": {name: audit.digest(data) for name, data in contents.items()}, "extension_manifest": manifest}
    payload = {name.removeprefix("extension/"): data for name, data in contents.items() if name.startswith("extension/")}
    payload[".vsixmanifest"] = contents["extension.vsixmanifest"]
    wanted = audit.inventory(payload)
    files = copy.deepcopy(wanted)
    installed = json.dumps({**manifest, "__metadata": {"installedTimestamp": 1}}).encode()
    files["package.json"] = {"sha256": audit.digest(installed), "bytes": len(installed)}
    proof = {"files": files, "manifest": {"id": "generic.fixture", "version": "0.1.0", "main": manifest["main"],
             "sha256": audit.digest(installed), "archive_sha256": wanted["package.json"]["sha256"],
             "matches_except_installer_metadata": True, "installer_metadata_present": True,
             "normalized": manifest, "archive_normalized": manifest},
             "container_metadata": {".vsixmanifest": {"archive_member": "extension.vsixmanifest",
                                     **wanted[".vsixmanifest"], "matches_exact_bytes": True}},
             "compiled_files": {name: data for name, data in wanted.items() if name.startswith("dist/src/")}}
    moment = "2026-10-13T12:00:00Z"
    record = {"passed": True, "vsix_unchanged": True, "owned_profile_removed": True,
              "vsix": {"sha256": audit.digest(raw), "bytes": len(raw)}, "expected_extension": {"id": "generic.fixture", "version": "0.1.0"},
              "install_receipt": {"code": 0, "signal": None, "owned_process_group_stopped": True,
                                  "stdout": "CPU", "stdout_bytes": 3, "stderr": "", "stderr_bytes": 0},
              "install_command": ["<cli>", "--user-data-dir=<package-run>/user-data", "--extensions-dir=<package-run>/extensions",
                                  "--install-extension", "<release-vsix>", "--force", "--do-not-sync"],
              "installed_extension_path": "<package-run>/extensions/generic.fixture-0.1.0",
              "expected_installed_inventory": wanted, "installed_inventory_before_validation": copy.deepcopy(files),
              "payload_before_host": copy.deepcopy(proof), "payload_after_host": copy.deepcopy(proof),
              "started_utc": moment, "finished_utc": moment,
              "smoke": {"overlay_smoke": True, "saved_edit_smoke": False, "source_write_policy": "No fixture source writes",
                        "started_utc": moment, "finished_utc": moment,
                        "host": {"checks": {"packaged_extension_path": True}, "extension_path": "<extension>", "expected_extension_path": "<extension>"},
                        "launch_arguments": ["--extensionDevelopmentPath=<extension>", "--extensionTestsPath=<extension-source>/dist/test/host/index.js"]}}
    return record, frozen, contents, raw


def check(values, *, nested_passed=True):
    return audit.audit_package(*values, {}, host_auditor=lambda *args: {"passed": nested_passed})


def test_synthetic_package_retains_manifest_metadata_exception_and_exact_container_only():
    values = fixture()
    before = copy.deepcopy(values)
    result = check(values)
    assert result["passed"] and result["installed_members"] == {"before": 3, "after": 3}
    assert values == before
    assert any("Raw __metadata values are withheld" in limit for limit in result["limits"])


@pytest.mark.parametrize("change,reason", [
    ("extra", "installed membership"), ("container", "exact installed bytes"),
    ("manifest", "semantic installed manifest"), ("compiled", "compiled payload"),
    ("path", "Actual host"), ("launch", "Host launch"), ("cleanup", "Installer exit/group"),
    ("profile", "profile is not exclusively"), ("source_mode", "overlay-only"),
])
def test_positive_receipt_flags_cannot_hide_payload_path_cleanup_or_policy_corruption(change, reason):
    values = fixture()
    record = values[0]
    before = record["payload_before_host"]
    if change == "extra":
        before["files"]["dist/src/injected.js"] = {"bytes": 1, "sha256": "a" * 64}
    elif change == "container":
        before["files"][".vsixmanifest"]["sha256"] = "a" * 64
    elif change == "manifest":
        before["manifest"]["normalized"] = {**before["manifest"]["normalized"], "dependencies": {"unexpected": "1"}}
    elif change == "compiled":
        before["compiled_files"] = {}
    elif change == "path":
        record["smoke"]["host"]["extension_path"] = "<extension-source>"
    elif change == "launch":
        record["smoke"]["launch_arguments"][0] = "--extensionDevelopmentPath=<extension-source>"
    elif change == "cleanup":
        record["install_receipt"]["owned_process_group_stopped"] = False
    elif change == "profile":
        record["install_command"][1] = "--user-data-dir=<home>/user-data"
    else:
        record["smoke"]["saved_edit_smoke"] = True
    result = check(values)
    assert not result["passed"] and any(reason in error for error in result["errors"])


def test_nested_receipt_failure_and_corrupt_archive_are_not_overridden_by_package_pass_flag():
    values = fixture()
    assert not check(values, nested_passed=False)["passed"]
    values[1]["members_sha256"]["extension.vsixmanifest"] = "b" * 64
    assert not check(values)["passed"]


def test_real_cpu_zip_reader_rejects_duplicate_and_escape_members(tmp_path):
    _, _, contents, _ = fixture()
    path = tmp_path / "generic.vsix"
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    assert audit.archive_contents(path) == contents
    with zipfile.ZipFile(path, "a") as archive, pytest.warns(UserWarning, match="Duplicate name"):
        archive.writestr("extension.vsixmanifest", b"duplicate")
    with pytest.raises(ValueError, match="Duplicate"):
        audit.archive_contents(path)
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("extension/../outside", b"escape")
    with pytest.raises(ValueError, match="unsafe"):
        audit.archive_contents(path)


PROOF = ROOT / "results/editor-package-2026-10-13/runtime-proof.json"


@pytest.mark.skipif(not PROOF.is_file(), reason="Retained package runtime companion required")
@pytest.mark.parametrize("change,reason", [
    ("artifact", "artifact/package binding"), ("precision", "checkpoint/precision"),
    ("source", "source SHA differs"), ("package", "package versions differ"),
])
def test_retained_runtime_companion_rejects_identity_precision_source_and_package_corruption(change, reason):
    proof = json.loads(PROOF.read_bytes())
    raw = (PROOF.parent / "packaged-host.json").read_bytes()
    if change == "artifact":
        raw += b" "
    elif change == "precision":
        proof["backend_status_after"]["store"]["embedding"]["quantized"] = True
    elif change == "source":
        proof["backend_implementation_sha256"]["src/semantic_search/backend.py"] = "b" * 64
    else:
        proof["packages"]["mlx"] = "different-runtime"
    result = audit.audit_runtime(proof, raw, proof["vsix_sha256"])
    assert not result["passed"] and any(reason in error for error in result["errors"])
