"""Offline beta support contracts; no model inference, download or network."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("beta_tools_under_test", ROOT / "scripts/beta_tools.py")
beta = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(beta)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value.encode() if isinstance(value, str) else value)


def runtime(root):
    write(root / "requirements-mac.lock", "mlx==0.32.3\nmcp==2.3.0\n")
    write(root / "requirements-languages.lock", "# comment\ntree-sitter==0.26.0\n")
    write(root / "pyproject.toml", '[project]\nversion = "0.1.1"\n')
    python = root / ".cache/quickstart/venv/bin/python"
    write(python, "fake dedicated interpreter")
    write(python.parent.parent / "pyvenv.cfg", "home = managed runtime")
    packages = {"mlx": "0.32.3", "mcp": "2.3.0", "tree-sitter": "0.26.0", "semantic-search-engine": "0.1.1"}
    probe = {"python": "3.12.13", "prefix_matches": True, "metal": True, "packages": packages}
    return python, probe


def checkpoint(root):
    directory = root / ".cache/models/qwen3-embedding-0.6b"
    for name in beta.MODEL_FILES:
        write(directory / name, "fixture only")
    write(directory / "model.safetensors", "tiny weights")
    digest = hashlib.sha256((directory / "model.safetensors").read_bytes()).hexdigest()
    write(directory / "config.json", json.dumps({"model_type": "qwen3", "hidden_size": 1024}))
    write(directory / ".cache/huggingface/download/model.safetensors.metadata", f"{beta.MODEL_REVISION}\n{digest}\n0\n")
    write(directory / "semantic_search_checkpoint.json", json.dumps({"id": beta.MODEL_ID,
        "revision": beta.MODEL_REVISION, "weights_etag": digest, "quantized": False}))
    return directory, digest


def completed(stdout=b"", returncode=0, stderr=b"PRIVATE error and /Users/private/repo and TOKEN"):
    if isinstance(stdout, dict):
        stdout = json.dumps(stdout).encode()
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_doctor_missing_runtime_and_model_is_offline_read_only_and_actionable(tmp_path):
    original = set(tmp_path.rglob("*"))
    with patch.object(beta.shutil, "which", return_value=None), \
            patch.object(beta.platform, "system", return_value="Darwin"), \
            patch.object(beta.platform, "machine", return_value="arm64"), \
            patch.object(beta.platform, "mac_ver", return_value=("15.4.1", (), "")), \
            patch.object(beta, "_run") as run:
        report = beta.diagnose(tmp_path)
    run.assert_not_called()
    assert report["offline"] is True and report["checks_passed"] is False
    assert {key: report["machine"][key] for key in ("os", "os_version", "architecture", "supported")} == {"os": "Darwin", "os_version": "15.4.1", "architecture": "arm64", "supported": True}
    assert report["machine"]["logical_cpu_count"] == os.cpu_count()
    assert {item["code"] for item in report["issues"]} == {"git_unavailable", "runtime_missing", "model_missing"}
    assert all(item["action"] for item in report["issues"])
    assert str(tmp_path) not in json.dumps(report)
    assert set(tmp_path.rglob("*")) == original


def test_doctor_success_requires_actual_runtime_inventory_and_physical_checkpoint(tmp_path):
    _, probe = runtime(tmp_path)
    _, sha = checkpoint(tmp_path)
    write(tmp_path / "src/implementation.py", "PRIVATE implementation")
    responses = [completed(b"git version 2.51.0\n"), completed(probe),
                 completed(b"true\n"), completed(b"src/implementation.py\0")]
    with patch.object(beta, "_run", side_effect=responses), \
            patch.object(beta, "WEIGHTS_SHA", sha), \
            patch.object(beta.shutil, "which", return_value="/usr/bin/git"), \
            patch.object(beta.platform, "system", return_value="Darwin"), \
            patch.object(beta.platform, "machine", return_value="arm64"), \
            patch.object(beta.shutil, "disk_usage", return_value=SimpleNamespace(free=10 * 1024**3)):
        report = beta.diagnose(tmp_path, tmp_path)
    assert report["checks_passed"] is True and report["issues"] == []
    assert report["runtime"]["state"] == report["model"]["state"] == report["repository"]["state"] == "checked"
    assert report["repository"]["supported_tracked_files"] == 1
    assert "PRIVATE" not in json.dumps(report) and str(tmp_path) not in json.dumps(report)


def test_runtime_probe_is_bounded_isolated_and_discards_extra_private_fields(tmp_path):
    python, probe = runtime(tmp_path)
    probe.update(prefix="/Users/private/repo", auth="PRIVATE", source="PRIVATE", packages={**probe["packages"], "private-repo": "PRIVATE"})
    with patch.object(beta, "_run", return_value=completed(probe)) as run:
        value, issue = beta.runtime_summary(tmp_path)
    assert issue is None and value["state"] == "checked"
    assert value["versions_match"] and value["metal"]
    call = run.call_args
    assert call.args[0][0] == python and call.args[0][1:3] == ("-I", "-c")
    assert call.kwargs["timeout"] == 20
    assert "PRIVATE" not in json.dumps(value) and "private-repo" not in json.dumps(value)
    assert "mlx.core" in call.args[0][3] and "semantic_search" not in call.args[0][3]


@pytest.mark.parametrize("failure,state,code", [
    ("timeout", "timeout", "runtime_timeout"), ("failed", "unavailable", "runtime_unavailable"),
    ("malformed", "unavailable", "runtime_unavailable"), ("python", "version_mismatch", "runtime_version_mismatch"),
    ("prefix", "version_mismatch", "runtime_version_mismatch"), ("package", "version_mismatch", "runtime_version_mismatch"),
    ("metal", "metal_unavailable", "metal_unavailable"),
])
def test_runtime_failures_do_not_echo_raw_errors_or_repair_anything(tmp_path, failure, state, code):
    _, probe = runtime(tmp_path)
    if failure == "python":
        probe["python"] = "3.13.0"
    elif failure == "prefix":
        probe["prefix_matches"] = False
    elif failure == "package":
        probe["packages"]["mlx"] = "/Users/private/TOKEN"
    elif failure == "metal":
        probe["metal"] = False
    response = completed(probe)
    if failure == "failed":
        response.returncode = 1
    elif failure == "malformed":
        response.stdout = b"PRIVATE source and TOKEN"
    effect = subprocess.TimeoutExpired("PRIVATE command", 20, output=b"PRIVATE") if failure == "timeout" else None
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    with patch.object(beta, "_run", return_value=response, side_effect=effect):
        value, issue = beta.runtime_summary(tmp_path)
    assert value["state"] == state and issue == code
    assert "PRIVATE" not in json.dumps(value) and "TOKEN" not in json.dumps(value)
    assert before == {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


def test_runtime_parent_symlink_is_refused_without_executing_the_external_interpreter(tmp_path):
    owned, outside = tmp_path / "owned", tmp_path / "outside"
    runtime(outside)
    owned.mkdir()
    (owned / ".cache").symlink_to(outside / ".cache", target_is_directory=True)
    with patch.object(beta, "_run") as run:
        value, issue = beta.runtime_summary(owned)
    run.assert_not_called()
    assert issue == "runtime_missing" and value["state"] == "missing"


def test_checkpoint_checks_configuration_provenance_and_actual_bytes_without_inference(tmp_path):
    _, sha = checkpoint(tmp_path)
    with patch.object(beta, "WEIGHTS_SHA", sha), patch.object(beta, "_run") as run:
        value, issue = beta.model_summary(tmp_path)
    run.assert_not_called()
    assert issue is None and value["state"] == "checked"
    assert value["weights_sha256"] == "matches"
    assert value["config_matches"] and value["provenance_matches"]


@pytest.mark.parametrize("failure", ["weights", "configuration", "metadata", "marker", "symlink"])
def test_invalid_checkpoint_never_gets_a_checked_claim_or_echoes_metadata(tmp_path, failure):
    directory, sha = checkpoint(tmp_path)
    if failure == "weights":
        write(directory / "model.safetensors", "PRIVATE changed weights")
    elif failure == "configuration":
        write(directory / "config.json", '{"model_type":"PRIVATE","hidden_size":1024}')
    elif failure == "metadata":
        write(directory / ".cache/huggingface/download/model.safetensors.metadata", "PRIVATE revision\nPRIVATE token\n")
    elif failure == "marker":
        write(directory / "semantic_search_checkpoint.json", '{"source":"PRIVATE"}')
    elif failure == "symlink":
        source = directory / "model.safetensors"
        source.rename(directory / "outside.bin")
        source.symlink_to(directory / "outside.bin")
    with patch.object(beta, "WEIGHTS_SHA", sha):
        value, issue = beta.model_summary(tmp_path)
    assert issue in ("model_invalid", "model_missing")
    assert value["state"] in ("invalid", "missing")
    assert "PRIVATE" not in json.dumps(value)


def test_skip_weight_check_is_explicit_metadata_only(tmp_path):
    _, sha = checkpoint(tmp_path)
    with patch.object(beta, "WEIGHTS_SHA", sha), patch.object(beta, "_digest") as digest:
        value, issue = beta.model_summary(tmp_path, verify_weights=False)
    digest.assert_not_called()
    assert issue is None and value["state"] == "metadata_checked"
    assert value["weights_sha256"] == "not_checked"


def test_repository_diagnostics_count_membership_without_reading_code_or_emitting_names(tmp_path):
    for name in ("src/private.py", "src/private.ts", "src/types.d.ts", "src/dist/generated.js",
                 "src/build/preserved.py", "src/readme.md", "src/untracked.py"):
        write(tmp_path / name, "PRIVATE_CODE_MUST_NOT_BE_READ")
    listing = b"src/private.py\0src/private.ts\0src/types.d.ts\0src/dist/generated.js\0src/build/preserved.py\0src/readme.md\0"
    with patch.object(beta, "_run", side_effect=[completed(b"true\n"), completed(listing)]) as run, \
            patch.object(Path, "read_text", side_effect=AssertionError("No source reads")):
        value, issue = beta.repository_summary(tmp_path, "src", "/usr/bin/git")
    assert issue is None
    assert value["tracked_files_in_scope"] == 6 and value["supported_tracked_files"] == 3
    assert "private" not in json.dumps(value).lower() and str(tmp_path) not in json.dumps(value)
    assert all(call.kwargs["timeout"] == 10 for call in run.call_args_list)
    assert "--" in run.call_args_list[-1].args[0]
    assert all(call.kwargs["env"]["GIT_OPTIONAL_LOCKS"] == "0" for call in run.call_args_list)


@pytest.mark.parametrize("source", ["../outside", "/private/path", "src\nPRIVATE", "", "src/../../"])
def test_outside_or_control_character_source_scope_is_refused_without_git(tmp_path, source):
    with patch.object(beta, "_run") as run:
        value, issue = beta.repository_summary(tmp_path, source, "/usr/bin/git")
    run.assert_not_called()
    assert issue == "source_root_invalid"
    assert "PRIVATE" not in json.dumps(value)


def test_git_failure_and_unsafe_inherited_git_configuration_are_not_copied(tmp_path):
    (tmp_path / "src").mkdir()
    with patch.dict(os.environ, {"GIT_DIR": "PRIVATE", "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "PRIVATE", "GIT_CONFIG_VALUE_0": "TOKEN"}), \
            patch.object(beta, "_run", return_value=completed(b"PRIVATE repo data", returncode=128)) as run:
        value, issue = beta.repository_summary(tmp_path, "src", "/usr/bin/git")
    assert issue == "repository_not_git" and "PRIVATE" not in json.dumps(value)
    env = run.call_args.kwargs["env"]
    assert not any(name in env for name in ("GIT_DIR", "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"))
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull and env["GIT_CONFIG_NOSYSTEM"] == "1"


def test_disk_error_and_unknown_machine_only_produce_fixed_categories(tmp_path):
    with patch.object(beta.platform, "system", return_value="PRIVATE"), \
            patch.object(beta.platform, "machine", return_value="TOKEN"), \
            patch.object(beta.platform, "release", return_value="/Users/private/repo"), \
            patch.object(beta.shutil, "disk_usage", side_effect=OSError("PRIVATE")), \
            patch.object(beta.shutil, "which", return_value=None):
        report = beta.diagnose(tmp_path)
    assert {key: report["machine"][key] for key in ("os", "os_version", "architecture", "supported")} == {"os": "other", "os_version": None, "architecture": "other", "supported": False}
    assert {item["code"] for item in report["issues"]} >= {"unsupported_machine", "disk_unavailable"}
    assert "PRIVATE" not in json.dumps(report) and "TOKEN" not in json.dumps(report)


def test_doctor_recommends_six_gib_free_space(tmp_path):
    with patch.object(beta.shutil, "disk_usage", return_value=SimpleNamespace(free=5 * 1024**3)), \
            patch.object(beta.shutil, "which", return_value=None):
        report = beta.diagnose(tmp_path)
    assert report["disk"]["recommended_free_bytes"] == 6 * 1024**3
    issue = next(item for item in report["issues"] if item["code"] == "disk_space_low")
    assert "6 GiB" in issue["action"]


def test_live_mcp_snippets_keep_virtualenv_command_with_spaces_and_the_actual_port(tmp_path):
    python = tmp_path / "package with spaces/.cache/quickstart/venv/bin/python"
    write(python, "fake interpreter")
    session = tmp_path / "session with spaces"
    session.mkdir()
    files = beta.write_mcp_config(session, python, "http://127.0.0.1:54321/")
    generic, vscode = json.loads(files["generic"].read_text()), json.loads(files["vscode"].read_text())
    definition = generic["mcpServers"]["semantic-search"]
    assert definition["command"] == str(python)
    assert definition["args"] == ["-m", "semantic_search.mcp_server", "--server", "http://127.0.0.1:54321"]
    assert vscode == {"servers": {"semantic-search": {"type": "stdio", **definition}}}
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in files.values())
    assert set(session.iterdir()) == set(files.values())


def test_mcp_does_not_resolve_the_python_symlink_out_of_its_virtualenv(tmp_path):
    python = tmp_path / "venv/bin/python"
    base = tmp_path / "managed-python"
    write(base, "fake interpreter")
    python.parent.mkdir(parents=True)
    python.symlink_to(base)
    session = tmp_path / "session"
    session.mkdir()
    files = beta.write_mcp_config(session, python, "http://127.0.0.1:49312")
    assert json.loads(files["generic"].read_text())["mcpServers"]["semantic-search"]["command"] == str(python)


def test_mcp_binds_warmed_tokenizer_cache_for_clients_without_the_launcher_environment(tmp_path):
    python = tmp_path / "venv/bin/python"
    write(python, "fake interpreter")
    session = tmp_path / "session"
    session.mkdir()
    cache = tmp_path / "cache with spaces/tiktoken"
    cache.mkdir(parents=True)
    files = beta.write_mcp_config(session, python, "http://127.0.0.1:49152", tokenizer_cache=cache)
    for name, wrapper in (("generic", "mcpServers"), ("vscode", "servers")):
        definition = json.loads(files[name].read_text())[wrapper]["semantic-search"]
        assert definition["env"] == {"TIKTOKEN_CACHE_DIR": str(cache)}
        assert definition["args"][-1] == "http://127.0.0.1:49152"


@pytest.mark.parametrize("kind", ["relative", "missing", "file", "symlink", "parent_symlink", "control"])
def test_mcp_refuses_invalid_cache_without_creating_snippets(tmp_path, kind):
    python = tmp_path / "venv/bin/python"
    write(python, "fake interpreter")
    session = tmp_path / "session"
    session.mkdir()
    cache = tmp_path / "cache/tiktoken"
    cache.mkdir(parents=True)
    if kind == "relative":
        cache = Path("cache/tiktoken")
    elif kind == "missing":
        cache = tmp_path / "missing"
    elif kind == "file":
        cache = python
    elif kind == "symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(cache, target_is_directory=True)
        cache = alias
    elif kind == "parent_symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(cache.parent, target_is_directory=True)
        cache = alias / "tiktoken"
    elif kind == "control":
        cache = tmp_path / "cache\nwith control"
        cache.mkdir()
    with pytest.raises(ValueError, match="tokenizer cache"):
        beta.write_mcp_config(session, python, "http://127.0.0.1:49152", tokenizer_cache=cache)
    assert not list(session.iterdir())


@pytest.mark.parametrize("url", ["http://localhost:8750", "https://127.0.0.1:8750", "http://127.0.0.1", "http://127.0.0.1:0",
    "http://example.com:8750", "http://0.0.0.0:8750", "http://user:TOKEN@127.0.0.1:8750", "http://127.0.0.1:8750/search",
    "http://127.0.0.1:8750?token=PRIVATE", "http://127.0.0.1:8750\n"])
def test_mcp_refuses_non_live_loopback_configuration_without_writing_files(tmp_path, url):
    python = tmp_path / "python"
    write(python, "fake interpreter")
    with pytest.raises(ValueError, match="loopback"):
        beta.write_mcp_config(tmp_path, python, url)
    assert set(tmp_path.iterdir()) == {python}


@pytest.mark.parametrize("name", ["mcp.json", "vscode-mcp.json"])
def test_mcp_preflights_both_files_and_preserves_existing_config(tmp_path, name):
    python = tmp_path / "python"
    write(python, "fake interpreter")
    original = tmp_path / name
    write(original, "PRIVATE user settings")
    with pytest.raises(FileExistsError):
        beta.write_mcp_config(tmp_path, python, "http://127.0.0.1:54321")
    assert original.read_text() == "PRIVATE user settings"
    assert set(tmp_path.iterdir()) == {python, original}


def test_mcp_rejects_broken_output_symlink_and_directory_symlink(tmp_path):
    python = tmp_path / "python"
    write(python, "fake interpreter")
    session = tmp_path / "session"
    session.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(session, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        beta.write_mcp_config(alias, python, "http://127.0.0.1:54321")
    (session / "mcp.json").symlink_to(tmp_path / "missing")
    with pytest.raises(FileExistsError):
        beta.write_mcp_config(session, python, "http://127.0.0.1:54321")
    assert (session / "mcp.json").is_symlink() and not (session / "vscode-mcp.json").exists()


def test_mcp_second_name_race_removes_only_the_first_owned_reservation(tmp_path):
    python = tmp_path / "python"
    write(python, "fake interpreter")
    session = tmp_path / "session"
    session.mkdir()
    original_open = os.open

    def racing_open(path, flags, mode=0o777):
        if Path(path).name == "vscode-mcp.json":
            write(Path(path), "racing user configuration")
        return original_open(path, flags, mode)

    with patch.object(beta.os, "open", side_effect=racing_open), pytest.raises(FileExistsError):
        beta.write_mcp_config(session, python, "http://127.0.0.1:54321")
    assert not (session / "mcp.json").exists()
    assert (session / "vscode-mcp.json").read_text() == "racing user configuration"


def test_doctor_cli_is_machine_readable_and_returns_failure_when_action_needed(tmp_path, capsys):
    with patch.object(beta, "ROOT", tmp_path), patch.object(beta.shutil, "which", return_value=None):
        code = beta.main(["--doctor", "--skip-weight-check"])
    report = json.loads(capsys.readouterr().out)
    assert code == 1 and report["offline"] is True
    assert "runtime_missing" in {item["code"] for item in report["issues"]}
    assert str(tmp_path) not in json.dumps(report)
