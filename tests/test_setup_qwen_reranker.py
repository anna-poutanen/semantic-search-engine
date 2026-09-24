"""CPU-only installer contracts: no actual package install/download/model call."""
import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("setup_qwen_reranker_cpu", ROOT / "scripts/setup_qwen_reranker.py")
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


def prepared(tmp_path):
    shutil.copyfile(ROOT / setup.LOCK_NAME, tmp_path / setup.LOCK_NAME)
    return tmp_path / ".cache/reranker/.venv", tmp_path / ".cache/models/reranker"


def metadata(environment):
    site = environment / "lib/python3.12/site-packages"
    return {"python": setup.PYTHON_VERSION, "observed_prefix": "/base/python", "observed_base_prefix": "/base/python",
            "executable": str(environment / "bin/python"), "metadata_directory": str(site),
            "packages": [{"name": name, "version": version, "root": str(site)} for name, version in setup.lock_pins().items()],
            "native": {name: {"path": str(site / "mlx_lm/models" / name), "sha256": value} for name, value in setup.NATIVE_PINS.items()},
            "model_calls": 0, "mlx_imported": False}


def fake_venv(environment):
    (environment / "bin").mkdir(parents=True)
    (environment / "bin/python").symlink_to(sys.executable)
    (environment / "pyvenv.cfg").write_text("include-system-site-packages = false\nhome = " + str(Path(sys.executable).resolve().parent) + "\n")
    (environment / "lib/python3.12/site-packages").mkdir(parents=True)


def test_lock_and_model_native_constants_match_production_without_importing_it():
    assert len(setup.lock_pins()) == 34
    core = ast.parse((ROOT / "src/semantic_search/qwen_reranker_core.py").read_text())
    constants = {node.targets[0].id: ast.literal_eval(node.value) for node in core.body
                 if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                 and node.targets[0].id in {"MODEL_ID", "REVISION", "PINS", "QWEN3_SOURCE_SHA"}}
    assert setup.MODEL_ID == constants["MODEL_ID"] and setup.REVISION == constants["REVISION"]
    assert setup.PINS == constants["PINS"] and setup.NATIVE_PINS["qwen3.py"] == constants["QWEN3_SOURCE_SHA"]


def test_missing_setup_preflight_makes_no_subprocess_network_or_filesystem_writes(tmp_path):
    environment, checkpoint = prepared(tmp_path)
    before = list(tmp_path.rglob("*"))
    def prohibited(*args, **kwargs):
        raise AssertionError("No executable exists; do not launch a subprocess")
    result = setup.preflight(environment, checkpoint, root=tmp_path, run=prohibited)
    assert result["runtime"]["status"] == result["checkpoint"]["status"] == result["bootstrap"]["status"] == "missing"
    assert result["model_calls"] == result["network_calls"] == result["writes"] == 0
    assert not result["weights_loaded"] and not result["ready"]
    assert before == list(tmp_path.rglob("*"))


def test_real_cpu_venv_symlink_keeps_invocation_prefix_and_imports_no_mlx(tmp_path):
    environment = tmp_path / "isolated"
    subprocess.run([sys.executable, "-I", "-B", "-m", "venv", "--without-pip", environment], check=True)
    result = setup.inspect_runtime(environment, {}, run=setup.command, root=tmp_path)
    assert Path(result["python_invocation"]) == environment / "bin/python"
    assert Path(result["metadata"]["executable"]) == environment / "bin/python"
    assert result["metadata"]["observed_prefix"] == result["metadata"]["observed_base_prefix"]
    assert result["python_invocation"] != result["resolved_binary"]
    assert not result["metadata"]["mlx_imported"] and result["metadata"]["model_calls"] == 0
    assert "Pinned native source differs: qwen3.py" in result["errors"]


@pytest.mark.parametrize("corrupt", ["extra", "missing", "version", "duplicate", "native", "invocation", "python", "mlx", "native_origin", "package_origin"])
def test_runtime_rejects_changed_closure_python_origin_and_native_source(tmp_path, corrupt):
    environment = tmp_path / "env"
    fake_venv(environment)
    data = metadata(environment)
    if corrupt == "extra":
        data["packages"].append({"name": "pip", "version": "99", "root": data["metadata_directory"]})
    elif corrupt == "missing":
        data["packages"].pop()
    elif corrupt == "version":
        data["packages"][0]["version"] = "wrong"
    elif corrupt == "duplicate":
        data["packages"].append({"name": "MLX_LM", "version": "0.32.0", "root": data["metadata_directory"]})
    elif corrupt == "native":
        data["native"]["qwen3.py"]["sha256"] = "wrong"
    elif corrupt == "invocation":
        data["executable"] = str(Path(sys.executable).resolve())
    elif corrupt == "python":
        data["python"] = "3.13.0"
    elif corrupt == "mlx":
        data["mlx_imported"] = True
    elif corrupt == "native_origin":
        data["native"]["qwen3.py"]["path"] = "/outside/models/qwen3.py"
    else:
        data["packages"][0]["root"] = "/outside/site-packages"
    calls = []
    def reader(command, **kwargs):
        calls.append(command)
        return json.dumps(data)
    result = setup.inspect_runtime(environment, setup.lock_pins(), run=reader, root=tmp_path)
    assert result["status"] == "invalid" and result["errors"]
    assert calls[0][0] == environment / "bin/python" and calls[0][1:4] == ["-I", "-S", "-B"]


def test_metadata_inspection_does_not_execute_real_venv_pth_startup_code(tmp_path):
    environment = tmp_path / "isolated"
    subprocess.run([sys.executable, "-I", "-B", "-m", "venv", "--without-pip", environment], check=True)
    sentinel = tmp_path / "must-not-execute"
    site = environment / "lib/python3.12/site-packages"
    (site / "untrusted-startup.pth").write_text("import pathlib; pathlib.Path(" + repr(str(sentinel)) + ").touch()\n")
    result = setup.inspect_runtime(environment, {}, root=tmp_path)
    assert not sentinel.exists() and not result["metadata"]["mlx_imported"]
    assert result["metadata"]["executable"] == str(environment / "bin/python")


def test_system_site_packages_venv_is_rejected_before_subprocess(tmp_path):
    environment = tmp_path / "env"
    fake_venv(environment)
    cfg = environment / "pyvenv.cfg"
    cfg.write_text(cfg.read_text().replace("false", "true"))
    def prohibited(*args, **kwargs):
        raise AssertionError("System-site environment must never be invoked")
    result = setup.inspect_runtime(environment, setup.lock_pins(), root=tmp_path, run=prohibited)
    assert result["status"] == "invalid" and "exclude system site packages" in result["errors"][0]


def tiny_pins():
    values = {name: ("tiny pinned " + name + "\n").encode() for name in setup.PINS}
    return values, {name: (len(raw), hashlib.sha256(raw).hexdigest()) for name, raw in values.items()}


def test_metadata_skips_weight_reads_and_full_verification_rejects_equal_size_corruption(tmp_path):
    values, pins = tiny_pins()
    for name, raw in values.items():
        (tmp_path / name).write_bytes(raw)
    metadata_only = setup.inspect_checkpoint(tmp_path, pins=pins)
    assert metadata_only["status"] == "valid" and not metadata_only["weight_identity_verified"]
    assert metadata_only["files"]["model.safetensors"]["sha256"] is None
    assert setup.inspect_checkpoint(tmp_path, pins=pins, verify_weights=True)["weight_identity_verified"]
    raw = values["model.safetensors"]
    (tmp_path / "model.safetensors").write_bytes(b"!" + raw[1:])
    assert setup.inspect_checkpoint(tmp_path, pins=pins, verify_weights=True)["status"] == "invalid"


def test_private_paths_reject_main_env_escape_symlinks_and_regular_files(tmp_path):
    (tmp_path / ".cache").mkdir()
    assert setup.private_destination(".cache/isolated", tmp_path) == tmp_path / ".cache/isolated"
    for value in (".venv", ".", "..", ".cache/../src", ".cache"):
        with pytest.raises(ValueError):
            setup.private_destination(value, tmp_path)
    (tmp_path / ".cache/outside").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        setup.private_destination(".cache/outside/environment", tmp_path)
    (tmp_path / ".cache/file").touch()
    with pytest.raises(ValueError, match="directory"):
        setup.private_destination(".cache/file", tmp_path)


def test_explicit_install_can_bootstrap_without_preinstalled_python312_and_checks_every_download(tmp_path, monkeypatch):
    environment, checkpoint = prepared(tmp_path)
    values, pins = tiny_pins()
    monkeypatch.setattr(setup, "PINS", pins)
    monkeypatch.setattr(setup.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(setup.platform, "machine", lambda: "arm64")
    commands = []
    def runner(command, **kwargs):
        command = [str(item) for item in command]
        commands.append(command)
        if "--version" in command:
            return "uv 0.13.0 (verified build metadata)\n"
        if "--target" in command:
            uv = tmp_path / ".cache/bootstrap/bin/uv"
            uv.parent.mkdir()
            uv.write_text("injected uv executable")
        elif "venv" in command:
            fake_venv(environment)
        elif "-c" in command:
            return json.dumps(metadata(environment))
        elif "download" in command:
            staging = Path(command[command.index("--local-dir") + 1])
            for name, raw in values.items():
                (staging / name).write_bytes(raw)
        return ""
    result = setup.install(environment, checkpoint, root=tmp_path, run=runner)
    assert result["checks_passed"] and result["model_calls"] == 0 and not result["weights_loaded"]
    assert result["after"]["checkpoint"]["weight_identity_verified"]
    venv = next(row for row in commands if "venv" in row)
    assert venv[-3:] == ["--python", "3.12.15", str(environment)] and "--managed-python" in venv
    assert "--allow-existing" in venv and environment.stat().st_mode & 0o777 == 0o700
    installs = [row for row in commands if "install" in row]
    assert len(installs) == 2 and "--no-deps" in installs[1] and "--no-build" in installs[1]
    assert any("check" in row for row in commands)
    download = next(row for row in commands if "download" in row)
    assert download[3:7] == list(pins) and download[download.index("--revision") + 1] == setup.REVISION
    assert all((checkpoint / name).read_bytes() == raw for name, raw in values.items())
    assert all((checkpoint / name).stat().st_mode & 0o777 == 0o600 for name in values)
    assert not any("semantic_search.qwen_worker" in " ".join(row) or "import mlx" in " ".join(row) for row in commands)


def test_existing_wrong_setup_refuses_all_mutations_before_install(tmp_path, monkeypatch):
    monkeypatch.setattr(setup.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(setup.platform, "machine", lambda: "arm64")
    environment, checkpoint = prepared(tmp_path)
    environment.mkdir(parents=True)
    (environment / "keep.txt").write_text("untouched")
    calls = []
    def reader(command, **kwargs):
        calls.append(command)
        raise AssertionError("Incomplete runtime must not be invoked")
    with pytest.raises(ValueError, match="Refusing"):
        setup.install(environment, checkpoint, root=tmp_path, run=reader)
    assert not calls and (environment / "keep.txt").read_text() == "untouched"
    assert not checkpoint.exists() and not (tmp_path / ".cache/bootstrap").exists()


def test_atomic_publication_never_overwrites_a_concurrently_created_file(tmp_path):
    staged, target = tmp_path / "staged", tmp_path / "target"
    staged.write_bytes(b"new correct bytes")
    target.write_bytes(b"other existing bytes")
    with pytest.raises(FileExistsError):
        setup.publish_file(staged, target)
    assert staged.read_bytes() == b"new correct bytes" and target.read_bytes() == b"other existing bytes"


def test_setup_environment_removes_implicit_overwrite_and_import_settings(monkeypatch):
    for name in ("UV_VENV_CLEAR", "UV_SYSTEM_PYTHON", "PYTHONPATH", "PIP_TARGET", "HF_TOKEN", "VIRTUAL_ENV"):
        monkeypatch.setenv(name, "unsafe-inherited-setting")
    result = setup.environment_for_setup()
    assert not any(name in result for name in ("UV_VENV_CLEAR", "UV_SYSTEM_PYTHON", "PYTHONPATH", "PIP_TARGET", "HF_TOKEN", "VIRTUAL_ENV"))
    assert result["PYTHONDONTWRITEBYTECODE"] == "1" and result["HF_HUB_DISABLE_TELEMETRY"] == "1"


def test_wrong_bootstrap_version_is_not_reinstalled(tmp_path, monkeypatch):
    monkeypatch.setattr(setup.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(setup.platform, "machine", lambda: "arm64")
    environment, checkpoint = prepared(tmp_path)
    uv = tmp_path / ".cache/bootstrap/bin/uv"
    uv.parent.mkdir(parents=True)
    uv.write_text("keep wrong executable")
    calls = []
    def reader(command, **kwargs):
        calls.append(command)
        return "uv 99.0.0\n"
    with pytest.raises(ValueError, match="Refusing"):
        setup.install(environment, checkpoint, root=tmp_path, run=reader)
    assert len(calls) == 1 and calls[0][-1] == "--version"
    assert uv.read_text() == "keep wrong executable"


def test_runtime_valid_metadata_has_exact_distribution_closure(tmp_path):
    environment = tmp_path / "env"
    fake_venv(environment)
    data = metadata(environment)
    result = setup.inspect_runtime(environment, setup.lock_pins(), root=tmp_path,
                                   run=lambda *args, **kwargs: json.dumps(copy.deepcopy(data)))
    assert result["status"] == "valid" and not result["errors"]


def test_concurrent_runtime_creation_fails_exclusive_reservation_before_uv(tmp_path, monkeypatch):
    environment, checkpoint = prepared(tmp_path)
    uv = tmp_path / ".cache/bootstrap/bin/uv"
    uv.parent.mkdir(parents=True)
    uv.write_text("injected verified bootstrap")
    monkeypatch.setattr(setup.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(setup.platform, "machine", lambda: "arm64")
    mkdir = Path.mkdir
    def concurrent_creation(path, *args, **kwargs):
        if path == environment and not environment.exists():
            mkdir(environment, parents=True)
            (environment / "keep.txt").write_text("concurrently owned runtime")
        return mkdir(path, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", concurrent_creation)
    calls = []
    def reader(command, **kwargs):
        calls.append([str(item) for item in command])
        assert command[-1] == "--version", "No uv creation/install may touch a concurrently appeared directory"
        return "uv 0.13.0\n"
    with pytest.raises(FileExistsError):
        setup.install(environment, checkpoint, root=tmp_path, run=reader)
    assert (environment / "keep.txt").read_text() == "concurrently owned runtime"
    assert len(calls) == 1 and not checkpoint.exists()


@pytest.mark.skipif(not (ROOT / ".cache/bootstrap/bin/uv").is_file(), reason="Optional local pinned uv unavailable")
def test_actual_uv_offline_preserves_exclusively_reserved_directory_without_packages(tmp_path):
    environment = tmp_path / "reserved"
    environment.mkdir(mode=0o700)
    before = environment.stat()
    subprocess.run([ROOT / ".cache/bootstrap/bin/uv", "--no-config", "venv", "--no-project", "--allow-existing",
                    "--offline", "--no-python-downloads", "--python", Path(sys.executable).resolve(), environment],
                   env=setup.environment_for_setup(tmp_path), check=True, capture_output=True)
    after = environment.stat()
    assert (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
    assert after.st_mode & 0o777 == 0o700
    assert (environment / "bin/python").is_file() and (environment / "pyvenv.cfg").is_file()
    result = setup.inspect_runtime(environment, {}, root=tmp_path)
    assert result["metadata"]["packages"] == [] and not result["metadata"]["mlx_imported"]
