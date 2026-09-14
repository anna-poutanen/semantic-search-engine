#!/usr/bin/env python3
"""Audit retained installed-VSIX receipts without installing or starting anything."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile

import audit_editor_host as host

HASH = re.compile(r"[0-9a-f]{64}\Z")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def archive_contents(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(set(names)) != len(names) or any(PurePosixPath(name).is_absolute()
                or ".." in PurePosixPath(name).parts or "\\" in name for name in names):
            raise ValueError("Duplicate or unsafe VSIX member")
        return {name: archive.read(name) for name in names if not name.endswith("/")}


def inventory(contents):
    return {name: {"sha256": digest(raw), "bytes": len(raw)} for name, raw in contents.items()}


def audit_runtime(proof, artifact_bytes, vsix_sha256, *, root=host.ROOT, source_ref=None):
    errors, count = [], 0

    def require(condition, message):
        if not condition:
            errors.append(message)

    try:
        require(proof["host_artifact_sha256"] == digest(artifact_bytes)
                and proof["vsix_sha256"] == vsix_sha256, "Runtime companion artifact/package binding")
        require(proof["architecture"] == "arm64" and proof["python"].startswith("3.12."), "Mac runtime architecture/Python")
        status = proof["backend_status_after"]
        require(status["embedding_initialized"] is True and status["dense_ready"] is True
                and status["closed"] is False and status["ready"] is True and status["windows"] == 5,
                "Resident model/dense snapshot was not captured")
        model = status["store"]["embedding"]
        require(model["id"] == "Qwen/Qwen3-Embedding-0.6B"
                and model["revision"] == "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
                and model["quantized"] is False and model["weights_dtype"] == "bfloat16"
                and model["dimensions"] == 1024, "Qwen checkpoint/precision/dimensions")
        require(all(proof["packages"].get(name) == value for name, value in model["packages"].items()), "Captured model/runtime package versions differ")
        fingerprint = digest(json.dumps(model, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
        require(fingerprint == status["store"]["model_fingerprint"], "Captured embedding metadata fingerprint")
        require(status["extractor"]["installed_parsers"] == status["extractor"]["parser_pins"]
                == {"tree-sitter": "0.26.0", "tree-sitter-javascript": "0.25.0", "tree-sitter-typescript": "0.23.2"}, "Parser pins")
        for field, base in (("backend_implementation_sha256", Path(root)),
                            ("extension_source_sha256", Path(root) / "extensions/vscode")):
            values = proof[field]
            require(bool(values), "Empty runtime source inventory")
            for name, expected in values.items():
                safe = host.relative(name) and name.startswith(("src/", "scripts/", "docs/", "test/", "tests/"))
                require(safe and isinstance(expected, str) and bool(HASH.fullmatch(expected)), "Unsafe runtime source entry")
                if not safe:
                    continue
                path = base / name
                require(path.resolve().is_relative_to(base.resolve()), "Runtime source path leaves checkout")
                if not path.resolve().is_relative_to(base.resolve()):
                    continue
                relative = str(path.relative_to(root))
                raw = (subprocess.check_output(["git", "-C", str(root), "show", f"{source_ref}:{relative}"], timeout=10)
                       if source_ref else path.read_bytes())
                require(digest(raw) == expected, "Runtime source SHA differs: " + relative)
                count += 1
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError) as error:
        require(False, "Malformed runtime companion: " + type(error).__name__)
    return {"passed": not errors, "errors": errors, "source_files_checked": count,
            "source_reference": source_ref or "current checkout bytes",
            "limits": ["Runtime identity is contemporaneous captured status/source provenance; no model/weights are loaded by this auditor."]}


def audit_package(record, frozen, contents, vsix_bytes, sources, *, host_auditor=host.audit_host):
    errors, member_counts = [], {}
    nested = None

    def require(condition, message):
        if not condition:
            errors.append(message)

    try:
        actual = inventory(contents)
        require(digest(vsix_bytes) == frozen["vsix_sha256"] == record["vsix"]["sha256"]
                and len(vsix_bytes) == frozen["vsix_bytes"] == record["vsix"]["bytes"], "Frozen VSIX byte identity")
        require({name: row["sha256"] for name, row in actual.items()} == frozen["members_sha256"],
                "Exact frozen VSIX member inventory")
        manifest = json.loads(contents["extension/package.json"])
        require(manifest == frozen["extension_manifest"] and manifest.get("license") == "UNLICENSED",
                "Frozen manifest identity/license")
        expected_id = {"id": manifest["publisher"] + "." + manifest["name"], "version": manifest["version"]}
        require(record["expected_extension"] == expected_id, "Recorded extension ID/version")
        require(record.get("passed") is True and not record.get("error") and record.get("vsix_unchanged") is True,
                "Package attempt was not successful")
        require(record.get("owned_profile_removed") is True, "Owned profiles were not removed")
        receipt = record["install_receipt"]
        require(receipt["code"] == 0 and receipt.get("signal") is None
                and receipt.get("owned_process_group_stopped") is True, "Installer exit/group cleanup")
        for stream in ("stdout", "stderr"):
            require(len(receipt[stream].encode("utf-8")) == receipt[stream + "_bytes"], "Installer output byte accounting")
        command = record["install_command"]
        require(command.count("--install-extension") == 1
                and command[command.index("--install-extension") + 1] == "<release-vsix>"
                and "--force" in command and "--do-not-sync" in command, "Only frozen local asset requested")
        for flag, suffix in (("--user-data-dir=", "user-data"), ("--extensions-dir=", "extensions")):
            require([arg for arg in command if arg.startswith(flag)] == [flag + "<package-run>/" + suffix],
                    "Installer profile is not exclusively owned")
        expected_path = "<package-run>/extensions/" + expected_id["id"] + "-" + expected_id["version"]
        require(record["installed_extension_path"] == expected_path, "Installed extension escaped the owned profile")
        payload = {name.removeprefix("extension/"): raw for name, raw in contents.items() if name.startswith("extension/")}
        require(".vsixmanifest" not in payload, "Container/payload name collision")
        payload[".vsixmanifest"] = contents["extension.vsixmanifest"]
        expected = inventory(payload)
        require(record["expected_installed_inventory"] == expected, "Expected installed inventory is not frozen payload plus exact container")
        require(record["installed_inventory_before_validation"] == record["payload_before_host"]["files"],
                "Before-validation inventory differs from the validated snapshot")
        for phase in ("before", "after"):
            proof = record["payload_" + phase + "_host"]
            files, meta = proof["files"], proof["manifest"]
            require(set(files) == set(expected), phase + " installed membership differs")
            for name, wanted in expected.items():
                if name != "package.json":
                    require(files.get(name) == wanted, phase + " exact installed bytes differ: " + name)
            installed_manifest = files["package.json"]
            require(type(installed_manifest["bytes"]) is int and installed_manifest["bytes"] > 0
                    and bool(HASH.fullmatch(installed_manifest["sha256"])), phase + " installed manifest capture")
            require(meta["normalized"] == meta["archive_normalized"] == manifest
                    and "__metadata" not in meta["normalized"], phase + " semantic installed manifest differs")
            require({key: meta[key] for key in ("id", "version")} == expected_id
                    and meta["main"] == manifest["main"] and meta["sha256"] == installed_manifest["sha256"]
                    and meta["archive_sha256"] == expected["package.json"]["sha256"]
                    and meta["matches_except_installer_metadata"] is True
                    and type(meta["installer_metadata_present"]) is bool, phase + " manifest identity/hash binding")
            container = proof["container_metadata"]
            require(container == {".vsixmanifest": {"archive_member": "extension.vsixmanifest",
                    **expected[".vsixmanifest"], "matches_exact_bytes": True}}, phase + " container metadata differs")
            require(proof["compiled_files"] == {name: row for name, row in expected.items() if name.startswith("dist/src/")},
                    phase + " compiled payload differs")
            member_counts[phase] = len(files)
        require(record["payload_before_host"] == record["payload_after_host"], "Installed files/manifest changed during host")
        smoke = record["smoke"]
        require(smoke.get("overlay_smoke") is True and smoke.get("saved_edit_smoke") is False
                and smoke.get("source_write_policy") == "No fixture source writes", "Prospective package phase is not overlay-only")
        state = smoke["host"]
        require(state["checks"].get("packaged_extension_path") is True and state["extension_path"]
                == state["expected_extension_path"] == "<extension>", "Actual host did not assert installed extension path")
        require([arg for arg in smoke["launch_arguments"] if arg.startswith("--extensionDevelopmentPath=")]
                == ["--extensionDevelopmentPath=<extension>"], "Host launch did not use installed payload")
        require([arg for arg in smoke["launch_arguments"] if arg.startswith("--extensionTestsPath=")]
                == ["--extensionTestsPath=<extension-source>/dist/test/host/index.js"], "Test entry is not explicitly external to the shipped payload")
        require(host.timestamp(record["started_utc"]) <= host.timestamp(smoke["started_utc"])
                <= host.timestamp(smoke["finished_utc"]) <= host.timestamp(record["finished_utc"]), "Package/host observer timestamp order")
        nested = host_auditor(copy.deepcopy(smoke), sources)
        require(nested["passed"] is True, "Nested source/token/binding audit failed")
    except (KeyError, TypeError, ValueError, IndexError, UnicodeError) as error:
        require(False, "Malformed package proof: " + type(error).__name__)
    return {"passed": not errors, "errors": errors, "vsix_members": len(contents),
            "installed_members": member_counts, "host_audit": nested,
            "limits": ["All VSIX bytes/member hashes are recomputed; captured installed member hashes are compared with exact archive bytes. Installed files were removed by the wrapper.",
                       "Installed manifest semantic equality is checked from retained normalized objects. Raw __metadata values are withheld, so its captured raw SHA cannot be reconstructed independently.",
                       "Exact installed path and process/profile cleanup also rely on executed, source-reviewed assertions.",
                       "Extension Development Host on an authored fixture; no ordinary user-profile activation, Restricted Mode, relevance, billing or performance claim."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--vsix", type=Path, required=True)
    parser.add_argument("--runtime-proof", type=Path)
    parser.add_argument("--source-ref", help="Historical Git revision for runtime source rechecks")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    raw = args.artifact.read_bytes()
    frozen_raw = args.freeze.read_bytes()
    result = audit_package(json.loads(raw), json.loads(frozen_raw), archive_contents(args.vsix),
                           args.vsix.read_bytes(), host.fixture_sources("mixed"))
    if args.runtime_proof:
        result["runtime_proof"] = audit_runtime(json.loads(args.runtime_proof.read_bytes()), raw,
                                                 json.loads(frozen_raw)["vsix_sha256"], source_ref=args.source_ref)
        result["errors"].extend(result["runtime_proof"]["errors"])
        result["passed"] = result["passed"] and result["runtime_proof"]["passed"]
    result.update(artifact_sha256=digest(raw), frozen_package_sha256=digest(frozen_raw),
                  auditor_sha256=digest(Path(__file__).read_bytes()),
                  host_auditor_sha256=digest(Path(host.__file__).read_bytes()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
