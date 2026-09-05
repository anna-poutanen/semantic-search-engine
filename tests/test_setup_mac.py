"""Check setup choices without installing packages or downloading weights."""
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest


spec = importlib.util.spec_from_file_location(
    "setup_mac", Path(__file__).resolve().parents[1] / "scripts/setup_mac.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def prepared(tmp_path):
    for name in (".cache/bootstrap/bin/uv", ".venv/bin/python"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


def test_runtime_only_skips_optional_h2o_source_and_weight_download(tmp_path, capsys):
    prepared(tmp_path)
    with patch.object(setup, "ROOT", tmp_path), patch.object(setup.platform, "system", return_value="Darwin"), \
         patch.object(setup.platform, "machine", return_value="arm64"), patch.object(setup, "run") as run:
        setup.main(["--runtime-only"])
    commands = [tuple(str(x) for x in call.args) for call in run.call_args_list]
    assert any("requirements-mac.lock" in command for command in commands)
    assert not any(command[0] == "git" or "download" in command for command in commands)
    assert "setup_embeddings.py" in capsys.readouterr().out


def test_default_preserves_pinned_h2o_download(tmp_path):
    prepared(tmp_path)
    with patch.object(setup, "ROOT", tmp_path), patch.object(setup.platform, "system", return_value="Darwin"), \
         patch.object(setup.platform, "machine", return_value="arm64"), patch.object(setup, "run") as run:
        setup.main([])
    downloads = [tuple(str(x) for x in call.args) for call in run.call_args_list if "download" in call.args]
    assert len(downloads) == 1
    assert downloads[0][1:5] == ("download", setup.MODEL_ID, "--revision", setup.MODEL_REV)


def test_unsupported_host_stops_before_installing(tmp_path):
    with patch.object(setup, "ROOT", tmp_path), patch.object(setup.platform, "system", return_value="Linux"), \
         patch.object(setup, "run") as run, pytest.raises(SystemExit, match="Apple Silicon"):
        setup.main(["--runtime-only"])
    run.assert_not_called()


def test_failed_install_does_not_print_ready(tmp_path, capsys):
    prepared(tmp_path)
    with patch.object(setup, "ROOT", tmp_path), patch.object(setup.platform, "system", return_value="Darwin"), \
         patch.object(setup.platform, "machine", return_value="arm64"), \
         patch.object(setup, "run", side_effect=RuntimeError("install failed")), pytest.raises(RuntimeError):
        setup.main(["--runtime-only"])
    assert "ready" not in capsys.readouterr().out.lower()
