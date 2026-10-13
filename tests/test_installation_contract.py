"""Installer choices without network, plus an explicit fresh-env inventory gate."""
from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import platform
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]


def script(name):
    spec = importlib.util.spec_from_file_location("installation_" + name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    # setup_embeddings imports the adjacent stdlib-only setup_mac helper.
    with patch.object(sys, "path", [str(ROOT / "scripts"), *sys.path]):
        spec.loader.exec_module(module)
    return module


def pinned_requirements(path):
    items = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, version = line.partition("==")
        assert separator and name and version and ";" not in version, line
        assert name not in items, name
        items[name] = version
    return items


def pinned_uv_version(output):
    # Official binaries append commit/date/target metadata after this version.
    return output.strip().split()[:2] == ["uv", "0.13.0"]


@pytest.mark.parametrize("output,expected", [
    ("uv 0.13.0", True),
    ("uv 0.13.0 (4ecd0eff2 2026-10-09 aarch64-apple-darwin)", True),
    ("uv 0.13.1", False),
    ("uv 0.13.0rc1", False),
    ("other 0.13.0", False),
])
def test_uv_build_metadata_preserves_exact_release_version(output, expected):
    assert pinned_uv_version(output) is expected


def prepared(root, environment=".venv"):
    for path in (root / ".cache/bootstrap/bin/uv", root / environment / "bin/python"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


@pytest.fixture
def mac():
    with patch.object(platform, "system", return_value="Darwin"), patch.object(platform, "machine", return_value="arm64"):
        yield


def test_clean_install_bootstrap_and_all_runtime_pins_before_editable(tmp_path, mac):
    setup = script("setup_mac")
    with patch.object(setup, "ROOT", tmp_path), patch.object(setup, "run") as run:
        setup.main(["--runtime-only"])
    commands = [tuple(str(value) for value in call.args) for call in run.call_args_list]
    assert commands[0][-1] == "uv==0.13.0"
    assert commands[0][:4] == (sys.executable, "-m", "pip", "install")
    assert commands[1][-3:] == ("--python", "3.12", str(tmp_path / ".venv"))
    locks = next(i for i, command in enumerate(commands) if "requirements-mac.lock" in command)
    editable = next(i for i, command in enumerate(commands) if "-e" in command)
    assert commands[locks][-4:] == ("-r", "requirements-mac.lock", "-r", "requirements-languages.lock")
    assert commands[editable][-1] == ".[mcp,languages]"
    assert locks < editable
    assert not any("download" in command or command[0] == "git" for command in commands)


@pytest.mark.parametrize("relative", [True, False])
def test_alternate_env_only_receives_pip_commands_and_quoted_next_step(tmp_path, mac, capsys, relative):
    setup = script("setup_mac")
    chosen = tmp_path / ".cache/install validation/env"
    prepared(tmp_path, chosen.relative_to(tmp_path))
    value = chosen.relative_to(tmp_path) if relative else chosen
    with patch.object(setup, "ROOT", tmp_path), patch.object(setup, "run") as run:
        setup.main(["--runtime-only", "--venv-path", str(value)])
    commands = [tuple(str(item) for item in call.args) for call in run.call_args_list]
    for command in commands:
        if "--python" in command:
            assert command[command.index("--python") + 1] == str(chosen / "bin/python")
        if "-c" in command:
            assert command[0] == str(chosen / "bin/python")
    next_step = capsys.readouterr().out.split("Download Qwen with: ", 1)[1].strip()
    assert shlex.split(next_step) == [str(chosen / "bin/python"), "scripts/setup_embeddings.py", "--venv-path", str(chosen)]
    assert not (tmp_path / ".venv").exists()


@pytest.mark.parametrize("destination", [".", "..", "file"])
def test_unsafe_env_target_stops_before_bootstrap(tmp_path, mac, destination):
    setup = script("setup_mac")
    (tmp_path / "file").write_text("keep")
    with patch.object(setup, "ROOT", tmp_path), patch.object(setup, "run") as run, pytest.raises(SystemExit):
        setup.main(["--runtime-only", "--venv-path", destination])
    run.assert_not_called()
    assert (tmp_path / "file").read_text() == "keep"


def test_existing_python_version_checked_before_package_mutation(tmp_path, mac):
    setup = script("setup_mac")
    prepared(tmp_path)
    failure = subprocess.CalledProcessError(1, "interpreter version check")
    with patch.object(setup, "ROOT", tmp_path), patch.object(setup, "run", side_effect=failure) as run, pytest.raises(subprocess.CalledProcessError):
        setup.main(["--runtime-only"])
    assert run.call_count == 1
    assert run.call_args.args[0] == tmp_path / ".venv/bin/python"
    assert "sys.version_info[:2] == (3, 12)" in run.call_args.args[-1]
    assert "sys.exit(" in run.call_args.args[-1]


def test_default_h2o_uses_chosen_python_and_exact_original_pins(tmp_path, mac, capsys):
    setup = script("setup_mac")
    chosen = tmp_path / "alternate"
    prepared(tmp_path, "alternate")
    with patch.object(setup, "ROOT", tmp_path), patch.object(setup, "run") as run:
        setup.main(["--venv-path", "alternate"])
    commands = [tuple(str(item) for item in call.args) for call in run.call_args_list]
    download = next(command for command in commands if "download" in command)
    assert download[:5] == (str(chosen / "bin/hf"), "download", "netprojects/h2o-lightning-4b-mlx-q8", "--revision", "a1048d02d9e713ba9f7481efa5c3df7ec8a2adaa")
    assert any(command[-2:] == ("--detach", "0fc8e0299608867746682a0552c906b03a284a47") for command in commands)
    launch = capsys.readouterr().out.split("Start with: ", 1)[1].strip()
    assert shlex.split(launch) == [str(chosen / "bin/python"), "scripts/serve_mac.py"]


def test_qwen_alternate_env_marker_and_revision_checks_without_download(tmp_path, mac):
    setup = script("setup_embeddings")
    chosen = tmp_path / "alternative"
    prepared(tmp_path, "alternative")
    model = tmp_path / "model"
    metadata = model / ".cache/huggingface/download/model.safetensors.metadata"
    calls = []

    def fake_run(*args):
        calls.append(tuple(str(item) for item in args))
        if "download" in args:
            metadata.parent.mkdir(parents=True)
            metadata.write_text(setup.MODEL_REVISION + "\nweights-etag\n0\n")
            (model / "config.json").write_text("{}")

    with patch.object(setup, "ROOT", tmp_path), patch.object(setup, "MODEL_PATH", model), patch.object(setup, "run", side_effect=fake_run):
        setup.main(["--venv-path", str(chosen)])
    assert calls[0][-2:] == ("--no-deps", "mlx-embeddings==0.1.0")
    assert calls[0][calls[0].index("--python") + 1] == str(chosen / "bin/python")
    assert calls[1][:5] == (str(chosen / "bin/hf"), "download", "Qwen/Qwen3-Embedding-0.6B", "--revision", "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3")
    marker = json.loads((model / "semantic_search_checkpoint.json").read_text())
    assert marker["revision"] == setup.MODEL_REVISION
    assert marker["weights_dtype"] == "bfloat16" and marker["quantized"] is False
    assert marker["weights_etag"] == "weights-etag"


@pytest.mark.parametrize("revision,config,message", [("wrong", {}, "expected Hub revision"),
                          ("expected", {"quantization": {"bits": 8}}, "unquantized original")])
def test_qwen_bad_checkpoint_does_not_write_ready_marker(tmp_path, mac, revision, config, message):
    setup = script("setup_embeddings")
    prepared(tmp_path)
    model = tmp_path / "model"
    metadata = model / ".cache/huggingface/download/model.safetensors.metadata"
    metadata.parent.mkdir(parents=True)
    metadata.write_text((setup.MODEL_REVISION if revision == "expected" else revision) + "\netag\n0\n")
    (model / "config.json").write_text(json.dumps(config))
    with patch.object(setup, "ROOT", tmp_path), patch.object(setup, "MODEL_PATH", model), patch.object(setup, "run"), pytest.raises(SystemExit, match=message):
        setup.main([])
    assert not (model / "semantic_search_checkpoint.json").exists()


def test_parser_pins_agree_across_lock_project_and_dispatch():
    from semantic_search.languages import PARSER_PINS

    declared = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["optional-dependencies"]["languages"]
    assert {name: version for name, version in (item.split("==") for item in declared)} == PARSER_PINS
    assert pinned_requirements(ROOT / "requirements-languages.lock") == PARSER_PINS
    mac = pinned_requirements(ROOT / "requirements-mac.lock")
    assert mac["mlx-embeddings"] == "0.1.0"
    assert mac["mcp"] == "2.3.0"
    assert mac["sqlite-vec"] == "0.1.9"
    assert mac["tiktoken"] == "0.14.0"


@pytest.mark.skipif(os.environ.get("SEMANTIC_INSTALLER_VERIFY_MAC") != "1", reason="Explicit fresh Apple Silicon environment inventory only")
def test_fresh_runtime_distribution_inventory():
    # Enable only after an authorized installation into the declared destination.
    expected = os.environ.get("SEMANTIC_INSTALLATION_VENV")
    assert expected and Path(sys.prefix).resolve() == Path(expected).resolve()
    assert platform.system() == "Darwin" and platform.machine() == "arm64"
    assert sys.version_info[:2] == (3, 12)
    pins = pinned_requirements(ROOT / "requirements-mac.lock") | pinned_requirements(ROOT / "requirements-languages.lock")
    for name, version in pins.items():
        assert importlib.metadata.version(name) == version, name
    assert pinned_uv_version(subprocess.check_output([ROOT / ".cache/bootstrap/bin/uv", "--version"], text=True))
    assert importlib.metadata.version("semantic-search-engine") == "0.1.0"
    assert (Path(expected) / "bin/semantic-search").is_file()
