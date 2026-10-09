"""Offline quickstart contracts; harmless owned CPU children replace inference."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/quickstart_mac.py"
LAUNCHER = ROOT / "Get Started.command"


@pytest.fixture
def quick():
    spec = importlib.util.spec_from_file_location("quickstart_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(ROOT / "scripts"), *sys.path]), patch.dict(sys.modules):
        for name in ("demo_mac", "setup_embeddings", "setup_mac"):
            sys.modules.pop(name, None)
        spec.loader.exec_module(module)
    with patch.dict(os.environ):
        yield module


@pytest.fixture
def mac(quick):
    with patch.object(quick.platform, "system", return_value="Darwin"), \
            patch.object(quick.platform, "machine", return_value="arm64"):
        yield


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)


def checkpoint(quick, path):
    for name in quick.MODEL_FILES:
        write(path / name, "small test file")
    write(path / "model.safetensors", "tiny fake weights")
    sha = hashlib.sha256((path / "model.safetensors").read_bytes()).hexdigest()
    write(path / "config.json", json.dumps({"model_type": "qwen3", "hidden_size": 1024}))
    write(path / ".cache/huggingface/download/model.safetensors.metadata", quick.MODEL_REVISION + "\n" + sha + "\n0\n")
    return sha


def test_model_checks_the_physical_weight_hash_and_revision_without_loading_it(quick, tmp_path):
    sha = checkpoint(quick, tmp_path)
    with patch.object(quick, "WEIGHTS_SHA", sha):
        value = quick.check_model(tmp_path)
    assert value["revision"] == quick.MODEL_REVISION
    assert value["weights_etag"] == sha
    assert value["weights_dtype"] == "bfloat16" and value["quantized"] is False


@pytest.mark.parametrize("cause, message", [("missing", "incomplete"), ("corrupt", "SHA-256"),
                                           ("revision", "revision/weights"), ("quantized", "unquantized"),
                                           ("dimension", "configuration")])
def test_incomplete_or_changed_checkpoint_never_gets_a_ready_claim(quick, tmp_path, cause, message):
    sha = checkpoint(quick, tmp_path)
    if cause == "missing":
        (tmp_path / "tokenizer.json").unlink()
    elif cause == "corrupt":
        (tmp_path / "model.safetensors").write_bytes(b"changed weights")
    elif cause == "revision":
        write(tmp_path / ".cache/huggingface/download/model.safetensors.metadata", "wrong\n" + sha)
    else:
        config = {"model_type": "qwen3", "hidden_size": 1024}
        config["quantization" if cause == "quantized" else "hidden_size"] = {"bits": 8} if cause == "quantized" else 512
        write(tmp_path / "config.json", json.dumps(config))
    with patch.object(quick, "WEIGHTS_SHA", sha), pytest.raises(ValueError, match=message):
        quick.check_model(tmp_path)
    assert not (tmp_path / "semantic_search_checkpoint.json").exists()


def test_existing_verified_model_is_reused_without_download_and_gets_current_provenance(quick, tmp_path):
    model = tmp_path / ".cache/models/qwen3-embedding-0.6b"
    sha = checkpoint(quick, model)
    with patch.object(quick, "ROOT", tmp_path), patch.object(quick, "WEIGHTS_SHA", sha), patch.object(quick, "run") as run:
        quick.setup_model(tmp_path / ".cache/quickstart/venv/bin/python")
    run.assert_not_called()
    marker = json.loads((model / "semantic_search_checkpoint.json").read_text())
    assert marker["id"] == quick.MODEL_ID and marker["revision"] == quick.MODEL_REVISION


def test_runtime_cache_always_probes_actual_dedicated_interpreter(quick, tmp_path):
    work = tmp_path / ".cache/quickstart"
    python = work / "venv/bin/python"
    write(python, "fake interpreter")
    write(work / "venv/pyvenv.cfg", "home = managed python")
    inputs = {"input": "checked"}
    write(work / "runtime.json", json.dumps({"inputs": inputs, "inventory": {"stale": True}}))
    fresh = {"prefix": str(work / "venv"), "metal": True}
    with patch.object(quick, "runtime_fingerprint", return_value=inputs), \
            patch.object(quick, "runtime_probe", return_value=fresh) as probe, patch.object(quick, "run") as run:
        actual, inventory = quick.setup_runtime(tmp_path / "uv", work)
    probe.assert_called_once_with(python)
    run.assert_not_called()
    assert actual == python and inventory == fresh
    assert actual != tmp_path / ".venv/bin/python"


def test_failed_runtime_probe_repairs_only_its_dedicated_environment(quick, tmp_path):
    work = tmp_path / ".cache/quickstart"
    python = work / "venv/bin/python"
    write(python, "fake interpreter")
    write(work / "venv/pyvenv.cfg", "home = managed python")
    inputs = {"input": "checked"}
    write(work / "runtime.json", json.dumps({"inputs": inputs}))
    write(tmp_path / ".venv/keep.txt", "existing developer environment")
    good = {"prefix": str(work / "venv"), "metal": True}
    with patch.object(quick, "ROOT", tmp_path), patch.object(quick, "runtime_fingerprint", return_value=inputs), \
            patch.object(quick, "runtime_probe", side_effect=[ValueError("stale version"), good]), patch.object(quick, "run") as run:
        actual, _ = quick.setup_runtime(tmp_path / "uv", work)
    commands = [tuple(str(item) for item in call.args) for call in run.call_args_list]
    assert len(commands) == 2
    assert all(command[:3] == (str(tmp_path / "uv"), "pip", "install") for command in commands)
    assert all(command[command.index("--python") + 1] == str(python) for command in commands)
    assert "--no-config" in commands[0] and "--no-config" in commands[1]
    assert "--no-deps" in commands[1]
    assert actual == python
    assert (tmp_path / ".venv/keep.txt").read_text() == "existing developer environment"
    assert json.loads((work / "runtime.json").read_text())["inventory"] == good


def test_runtime_probe_rejects_interpreter_prefix_outside_dedicated_environment(quick, tmp_path):
    python = tmp_path / "venv/bin/python"
    with patch.object(quick, "run", return_value=SimpleNamespace(stdout=json.dumps({"prefix": str(tmp_path / "other")}))) as run:
        with pytest.raises(ValueError, match="outside"):
            quick.runtime_probe(python)
    assert run.call_args.args[:2] == (python, "-c")
    code = run.call_args.args[2]
    assert "m.version(name) == version" in code and "encoding()" in code and "mx.metal.is_available()" in code


def test_unrecognized_environment_is_never_installed_into(quick, tmp_path):
    work = tmp_path / "quickstart"
    write(work / "venv/my-files.txt", "user files")
    with patch.object(quick, "runtime_fingerprint", return_value={}), patch.object(quick, "run") as run:
        with pytest.raises(ValueError, match="unrecognized"):
            quick.setup_runtime(tmp_path / "uv", work)
    run.assert_not_called()
    assert (work / "venv/my-files.txt").read_text() == "user files"


def test_non_tty_interactive_mode_stops_before_installation_or_inference(quick, tmp_path, mac):
    with patch.object(quick, "ROOT", tmp_path), patch.object(quick.sys.stdin, "isatty", return_value=False), \
            patch.object(quick, "setup_runtime") as runtime, patch.object(quick, "setup_model") as model, \
            patch.object(quick, "interactive") as interactive:
        with pytest.raises(SystemExit) as error:
            quick.main(["--uv-path", str(tmp_path / "uv")])
    assert error.value.code == 2
    runtime.assert_not_called()
    model.assert_not_called()
    interactive.assert_not_called()
    assert not (tmp_path / ".cache").exists()


@pytest.mark.parametrize("mode", ["--setup-only", "--check"])
def test_explicit_noninteractive_modes_use_the_separate_runtime_and_correct_dispatch(quick, tmp_path, mac, mode):
    python = tmp_path / ".cache/quickstart/venv/bin/python"
    with patch.object(quick, "ROOT", tmp_path), patch.object(quick.sys.stdin, "isatty", return_value=False), \
            patch.object(quick, "setup_runtime", return_value=(python, {})) as runtime, \
            patch.object(quick, "setup_model") as model, patch.object(quick, "run") as run, \
            patch.object(quick, "prepare") as prepare, patch.object(quick, "interactive") as interactive:
        quick.main(["--uv-path", str(tmp_path / "uv"), mode])
    runtime.assert_called_once_with(tmp_path / "uv", tmp_path / ".cache/quickstart")
    model.assert_called_once_with(python)
    prepare.assert_not_called()
    interactive.assert_not_called()
    if mode == "--check":
        run.assert_called_once_with(python, tmp_path / "scripts/demo_mac.py", "--run", "--python", python)
    else:
        run.assert_not_called()


def test_main_platform_guard_stops_before_setup(quick, tmp_path):
    with patch.object(quick.platform, "system", return_value="Linux"), patch.object(quick, "setup_runtime") as runtime:
        with pytest.raises(SystemExit) as error:
            quick.main(["--uv-path", str(tmp_path / "uv"), "--setup-only"])
    assert error.value.code == 2
    runtime.assert_not_called()


@pytest.mark.parametrize("source_root", ["../private", "/absolute"])
def test_repo_source_scope_guard_precedes_setup(quick, tmp_path, mac, source_root):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    with patch.object(quick.sys.stdin, "isatty", return_value=True), patch.object(quick, "setup_runtime") as runtime:
        with pytest.raises(SystemExit) as error:
            quick.main(["--uv-path", str(tmp_path / "uv"), "--repo", str(repo), "--source-root", source_root])
    assert error.value.code == 2
    runtime.assert_not_called()


@pytest.mark.parametrize("cause", ["normal", "keyboard", "identity"])
def test_interactive_owns_an_available_port_and_cleans_actual_cpu_child_on_every_exit(quick, tmp_path, cause):
    """A real tiny worker emits readiness only; it opens no socket or model."""
    real_popen = subprocess.Popen
    spawned, commands, requests = [], [], []
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    original_handler = signal.getsignal(signal.SIGTERM)
    worker = "import signal,sys,time\nsignal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\nprint('Ready: http://127.0.0.1:43210',flush=True)\nwhile True: time.sleep(60)\n"
    def start(arguments, **kwargs):
        commands.append((arguments, kwargs))
        process = real_popen([sys.executable, "-c", worker], **kwargs)
        spawned.append(process)
        return process
    def response(url, payload=None, timeout=120):
        requests.append((url, payload))
        if url.endswith("/status"):
            return {"repo": str(repo if cause != "identity" else tmp_path / "wrong"), "source_root": "src", "ready": True}
        return {"results": []}
    inputs = ["1", "q"] if cause == "normal" else KeyboardInterrupt()
    try:
        with patch.object(quick, "ROOT", tmp_path), patch.object(quick.subprocess, "Popen", side_effect=start), \
                patch.object(quick, "request_json", side_effect=response), patch("builtins.input", side_effect=inputs):
            if cause == "normal":
                quick.interactive(Path(sys.executable), repo, "src", tmp_path / "work", timeout=3)
            else:
                expected = KeyboardInterrupt if cause == "keyboard" else ValueError
                with pytest.raises(expected):
                    quick.interactive(Path(sys.executable), repo, "src", tmp_path / "work", timeout=3)
        assert len(spawned) == 1 and spawned[0].returncode == 0
        with pytest.raises(ProcessLookupError):
            os.killpg(spawned[0].pid, 0)
        arguments, options = commands[0]
        assert arguments[arguments.index("--port") + 1] == "0"
        assert options["start_new_session"] is True and "--no-watch" in arguments
        assert arguments[arguments.index("--repo") + 1] == str(repo)
        assert arguments[arguments.index("--source-root") + 1] == "src"
        assert requests[0] == ("http://127.0.0.1:43210/status", None)
        if cause == "normal":
            assert requests[1][1] == {"query": quick.QUERIES[0], **quick.SETTINGS}
        else:
            assert len(requests) == 1
        session = next((tmp_path / "work/sessions").glob("*/session.json"))
        assert json.loads(session.read_text())["shutdown"] == {"returncode": 0, "forced_kill": False}
        assert signal.getsignal(signal.SIGTERM) == original_handler
    finally:
        for process in spawned:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)


def test_http_client_disables_proxy_and_redirect_and_bounds_response(quick):
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, size):
            assert size == 1024 * 1024 + 1
            return b'{"ready":true}'
    with patch.object(quick.urllib.request, "build_opener") as build:
        build.return_value.open.return_value = Response()
        assert quick.request_json("http://127.0.0.1:1234/status") == {"ready": True}
        assert build.call_args.args[0].proxies == {}
        assert build.call_args.args[1].redirect_request(None, None, 302, "redirect", {}, "http://example.com") is None
        assert build.return_value.open.call_args.args[0].get_method() == "GET"


def shell_fixture(tmp_path):
    """Copy production Bash, changing only absolute platform and curl commands."""
    root = tmp_path / "download with spaces"
    root.mkdir()
    tools = tmp_path / "fake-tools"
    tools.mkdir()
    uname = tools / "uname"
    write(uname, '#!/bin/sh\ncase "$1" in -s) printf "%s\\n" "$FAKE_SYSTEM";; -m) printf "%s\\n" "$FAKE_ARCH";; esac\n')
    curl = tools / "curl"
    write(curl, '#!/bin/sh\nprintf "unexpected network attempt\\n" >&2\nexit 97\n')
    git = tools / "git"
    write(git, '#!/bin/sh\nexit "${FAKE_GIT_EXIT:-0}"\n')
    for tool in (uname, curl, git):
        tool.chmod(0o755)
    text = LAUNCHER.read_text().replace("/usr/bin/uname", shlex.quote(str(uname))).replace("/usr/bin/curl", shlex.quote(str(curl)))
    launcher = root / "Get Started.command"
    write(launcher, text)
    environment = {**os.environ, "PATH": str(tools) + os.pathsep + os.environ.get("PATH", ""),
                   "FAKE_SYSTEM": "Darwin", "FAKE_ARCH": "arm64"}
    return root, launcher, environment


def test_real_bash_syntax_and_help_require_no_prerequisites_or_network(tmp_path):
    syntax = subprocess.run(["bash", "-n", LAUNCHER], capture_output=True, text=True, timeout=5)
    assert syntax.returncode == 0, syntax.stderr
    root, launcher, environment = shell_fixture(tmp_path)
    environment.update({"FAKE_SYSTEM": "Linux", "FAKE_GIT_EXIT": "13"})
    result = subprocess.run(["bash", launcher, "--help"], env=environment, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0 and "--check" in result.stdout and "--setup-only" in result.stdout
    assert not (root / ".cache").exists()


@pytest.mark.parametrize("cause, message", [("platform", "Apple Silicon"), ("git", "xcode-select --install")])
def test_launcher_platform_and_working_git_guards_precede_any_install(tmp_path, cause, message):
    root, launcher, environment = shell_fixture(tmp_path)
    environment["FAKE_SYSTEM" if cause == "platform" else "FAKE_GIT_EXIT"] = "Linux" if cause == "platform" else "12"
    result = subprocess.run(["bash", launcher, "--setup-only"], env=environment, capture_output=True, text=True, timeout=5)
    assert result.returncode == 1 and message in result.stderr
    assert "unexpected network attempt" not in result.stderr
    assert not (root / ".cache").exists()


def test_launcher_refuses_changed_cached_bootstrap_before_execution(tmp_path):
    root, launcher, environment = shell_fixture(tmp_path)
    uv = root / ".cache/quickstart/bootstrap/uv"
    write(uv, '#!/bin/sh\nprintf "MUST_NOT_EXECUTE\\n"\n')
    uv.chmod(0o755)
    write(uv.with_name("uv.sha256"), "0" * 64 + "\n")
    result = subprocess.run(["bash", launcher, "--setup-only"], env=environment, capture_output=True, text=True, timeout=5)
    assert result.returncode == 1 and "Cached uv changed" in result.stderr
    assert "MUST_NOT_EXECUTE" not in result.stdout + result.stderr
    assert "unexpected network attempt" not in result.stderr


@pytest.mark.parametrize("raw", ["{", "[]", "null", "false"])
def test_malformed_completion_marker_repairs_runtime_instead_of_trusting_or_looping(quick, tmp_path, raw):
    work = tmp_path / "quickstart"
    python = work / "venv/bin/python"
    write(python, "interpreter")
    write(work / "venv/pyvenv.cfg", "environment")
    write(work / "runtime.json", raw)
    with patch.object(quick, "runtime_fingerprint", return_value={"pinned": "inputs"}), \
            patch.object(quick, "runtime_probe", return_value={"metal": True}), patch.object(quick, "run") as run:
        quick.setup_runtime(tmp_path / "uv", work)
    assert run.call_count == 2
    assert json.loads((work / "runtime.json").read_text())["inputs"] == {"pinned": "inputs"}


def test_runtime_marker_symlink_is_refused_without_changing_other_file(quick, tmp_path):
    work = tmp_path / "quickstart"
    work.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("user file")
    (work / "runtime.json").symlink_to(outside)
    with patch.object(quick, "runtime_fingerprint", return_value={}), patch.object(quick, "run") as run:
        with pytest.raises(ValueError, match="symlink"):
            quick.setup_runtime(tmp_path / "uv", work)
    run.assert_not_called()
    assert outside.read_text() == "user file"


@pytest.mark.parametrize("cause", ["directory", "config", "provenance", "metadata", "nested-cache"])
def test_checkpoint_symlinks_are_refused_before_download_or_provenance_write(quick, tmp_path, cause):
    model = tmp_path / ".cache/models/qwen3-embedding-0.6b"
    sha = checkpoint(quick, model)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("user file")
    if cause == "directory":
        moved = outside / "model"
        model.rename(moved)
        model.symlink_to(moved, target_is_directory=True)
    elif cause == "nested-cache":
        moved = outside / "cache"
        (model / ".cache").rename(moved)
        (model / ".cache").symlink_to(moved, target_is_directory=True)
    else:
        name = {"config": "config.json", "provenance": "semantic_search_checkpoint.json",
                "metadata": ".cache/huggingface/download/model.safetensors.metadata"}[cause]
        path = model / name
        if path.exists():
            path.unlink()
        path.symlink_to(sentinel)
    with patch.object(quick, "ROOT", tmp_path), patch.object(quick, "WEIGHTS_SHA", sha), patch.object(quick, "run") as run:
        with pytest.raises(ValueError, match="symlink"):
            quick.setup_model(tmp_path / "venv/bin/python")
    run.assert_not_called()
    assert sentinel.read_text() == "user file"
    assert not (outside / "model/semantic_search_checkpoint.json").exists()


def test_corrupt_complete_checkpoint_forces_refetch_and_reverifies_before_provenance(quick, tmp_path):
    model = tmp_path / ".cache/models/qwen3-embedding-0.6b"
    sha = checkpoint(quick, model)
    original = (model / "model.safetensors").read_bytes()
    (model / "model.safetensors").write_bytes(b"corruption with apparently valid Hub metadata")
    python = tmp_path / "dedicated/bin/python"
    def download(*args):
        assert args[:5] == (python.parent / "hf", "download", quick.MODEL_ID, "--revision", quick.MODEL_REVISION)
        assert "--force-download" in args
        (model / "model.safetensors").write_bytes(original)
    with patch.object(quick, "ROOT", tmp_path), patch.object(quick, "WEIGHTS_SHA", sha), \
            patch.object(quick, "run", side_effect=download) as run:
        quick.setup_model(python)
    run.assert_called_once()
    assert json.loads((model / "semantic_search_checkpoint.json").read_text())["weights_etag"] == sha


def test_interrupted_setup_download_never_writes_provenance(quick, tmp_path):
    model = tmp_path / ".cache/models/qwen3-embedding-0.6b"
    with patch.object(quick, "ROOT", tmp_path), patch.object(quick, "run", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            quick.setup_model(tmp_path / "dedicated/bin/python")
    assert not (model / "semantic_search_checkpoint.json").exists()


def test_interrupted_owned_setup_subprocess_is_actually_reaped(quick, tmp_path):
    real_popen = subprocess.Popen
    spawned = []
    worker = "import signal,sys,time\nsignal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\nprint('ready',flush=True)\nwhile True: time.sleep(60)\n"
    def start(arguments, **options):
        assert options["start_new_session"] is True
        process = real_popen([sys.executable, "-c", worker], **options)
        assert process.stdout.readline().strip() == "ready"
        process.communicate = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
        spawned.append(process)
        return process
    try:
        with patch.object(quick, "ROOT", tmp_path), patch.object(quick.subprocess, "Popen", side_effect=start):
            with pytest.raises(KeyboardInterrupt):
                quick.run("fake installer", capture=True)
        assert spawned[0].returncode == 0
        with pytest.raises(ProcessLookupError):
            os.killpg(spawned[0].pid, 0)
    finally:
        for process in spawned:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)


def test_scoped_cache_variables_and_signal_handlers_survive_check_interruption(quick, tmp_path, mac):
    python = tmp_path / ".cache/quickstart/venv/bin/python"
    previous = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGHUP)}
    def inspect_then_interrupt(*args):
        for name in ("UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR", "HF_HOME", "HF_HUB_CACHE", "HF_XET_CACHE", "TIKTOKEN_CACHE_DIR"):
            assert Path(os.environ[name]).is_relative_to(tmp_path / ".cache/quickstart")
        assert os.environ["UV_NO_CONFIG"] == "1"
        signal.raise_signal(signal.SIGHUP)
    with patch.object(quick, "ROOT", tmp_path), patch.object(quick, "setup_runtime", return_value=(python, {})), \
            patch.object(quick, "setup_model"), patch.object(quick, "run", side_effect=inspect_then_interrupt):
        with pytest.raises(SystemExit) as error:
            quick.main(["--uv-path", str(tmp_path / "uv"), "--check"])
    assert error.value.code == 130
    for number, handler in previous.items():
        assert signal.getsignal(number) == handler


def test_concurrent_setup_lock_stops_before_runtime_or_model(quick, tmp_path, mac):
    work = tmp_path / ".cache/quickstart"
    work.mkdir(parents=True)
    with (work / "setup.lock").open("a") as lock:
        quick.fcntl.flock(lock, quick.fcntl.LOCK_EX | quick.fcntl.LOCK_NB)
        with patch.object(quick, "ROOT", tmp_path), patch.object(quick, "setup_runtime") as runtime, \
                patch.object(quick, "setup_model") as model:
            with pytest.raises(SystemExit) as error:
                quick.main(["--uv-path", str(tmp_path / "uv"), "--setup-only"])
    assert error.value.code == 1
    runtime.assert_not_called()
    model.assert_not_called()


@pytest.mark.parametrize("name", ["bootstrap", "python", "uv-cache"])
def test_launcher_refuses_nested_reserved_symlinks_without_touching_target(tmp_path, name):
    root, launcher, environment = shell_fixture(tmp_path)
    work = root / ".cache/quickstart"
    work.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    write(outside / "keep", "user files")
    (work / name).symlink_to(outside, target_is_directory=True)
    result = subprocess.run(["bash", launcher, "--setup-only"], env=environment, capture_output=True, text=True, timeout=5)
    assert result.returncode == 1 and "symlink" in result.stderr
    assert list(outside.iterdir()) == [outside / "keep"]
    assert "unexpected network attempt" not in result.stderr


@pytest.mark.parametrize("locale", ["C", "C.UTF-8"])
def test_checked_cached_launcher_forwards_space_containing_arguments_without_system_python(tmp_path, locale):
    root, launcher, environment = shell_fixture(tmp_path)
    uv = root / ".cache/quickstart/bootstrap/uv"
    python = tmp_path / "managed test python"
    trace = tmp_path / "python-arguments.json"
    write(python, f'#!{sys.executable}\nimport json,os,sys\nfrom pathlib import Path\nPath({str(trace)!r}).write_text(json.dumps({{"arguments":sys.argv[1:],"cache":os.environ["UV_CACHE_DIR"],"python":os.environ["UV_PYTHON_INSTALL_DIR"],"config":os.environ["UV_NO_CONFIG"]}}))\n')
    python.chmod(0o755)
    write(uv, '#!/bin/sh\ncase "$1 $2" in "--version ") printf "uv 0.13.0 (test build)\\n";; "python install") exit 0;; "python find") printf "%s\\n" "$FAKE_MANAGED_PYTHON";; *) exit 99;; esac\n')
    uv.chmod(0o755)
    write(uv.with_name("uv.sha256"), hashlib.sha256(uv.read_bytes()).hexdigest() + "\n")
    environment["FAKE_MANAGED_PYTHON"] = str(python)
    environment.update({"LC_ALL": locale, "LC_CTYPE": locale, "LANG": locale})
    result = subprocess.run(["bash", launcher, "--repo", "/a repo with spaces", "--source-root", "source files"],
                            env=environment, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    record = json.loads(trace.read_text())
    assert record["arguments"] == [str(root / "scripts/quickstart_mac.py"), "--uv-path", str(uv),
                                    "--repo", "/a repo with spaces", "--source-root", "source files"]
    assert record["cache"] == str(root / ".cache/quickstart/uv-cache")
    assert record["python"] == str(root / ".cache/quickstart/python")
    assert record["config"] == "1"
    assert "unexpected network attempt" not in result.stderr
    assert "Setting locale failed" not in result.stderr


def test_launcher_checks_tarball_digest_and_removes_only_owned_download_directory(tmp_path):
    root, launcher, environment = shell_fixture(tmp_path)
    curl = tmp_path / "fake-tools/curl"
    write(curl, '#!/bin/sh\nwhile [ "$#" -gt 0 ]; do if [ "$1" = --output ]; then shift; printf "wrong tarball" > "$1"; exit 0; fi; shift; done\nexit 98\n')
    curl.chmod(0o755)
    result = subprocess.run(["bash", launcher, "--setup-only"], env=environment, capture_output=True, text=True, timeout=5)
    assert result.returncode == 1 and "checksum differs" in result.stderr
    bootstrap = root / ".cache/quickstart/bootstrap"
    assert not (bootstrap / "uv").exists()
    assert not list(bootstrap.glob("download.*"))


def test_launcher_refuses_symlinked_cached_checksum_before_reading_or_writing_it(tmp_path):
    root, launcher, environment = shell_fixture(tmp_path)
    uv = root / ".cache/quickstart/bootstrap/uv"
    write(uv, "unused")
    outside = tmp_path / "outside-checksum"
    outside.write_text("user file")
    uv.with_name("uv.sha256").symlink_to(outside)
    result = subprocess.run(["bash", launcher, "--setup-only"], env=environment, capture_output=True, text=True, timeout=5)
    assert result.returncode == 1 and "checksum must not be a symlink" in result.stderr
    assert outside.read_text() == "user file"


def test_cold_runtime_creates_only_reserved_environment_before_locked_installs(quick, tmp_path):
    work = tmp_path / ".cache/quickstart"
    work.mkdir(parents=True)
    existing = tmp_path / ".venv/user-file"
    write(existing, "unchanged")
    with patch.object(quick, "runtime_fingerprint", return_value={"source": "hash"}), \
            patch.object(quick, "runtime_probe", return_value={"metal": True}), patch.object(quick, "run") as run:
        python, _ = quick.setup_runtime(tmp_path / "uv", work)
    commands = [tuple(str(item) for item in call.args) for call in run.call_args_list]
    assert commands[0] == (str(tmp_path / "uv"), "venv", "--python", sys.executable, str(work / "venv"), "--no-config")
    assert len(commands) == 3 and python == work / "venv/bin/python"
    assert all(command[command.index("--python") + 1] == str(python) for command in commands[1:])
    assert "requirements-mac.lock" in commands[1] and "requirements-languages.lock" in commands[1]
    assert existing.read_text() == "unchanged"


def test_failed_forced_download_still_refuses_corrupt_checkpoint_and_no_new_marker(quick, tmp_path):
    model = tmp_path / ".cache/models/qwen3-embedding-0.6b"
    sha = checkpoint(quick, model)
    (model / "model.safetensors").write_bytes(b"still corrupt")
    with patch.object(quick, "ROOT", tmp_path), patch.object(quick, "WEIGHTS_SHA", sha), patch.object(quick, "run") as run:
        with pytest.raises(ValueError, match="SHA-256"):
            quick.setup_model(tmp_path / "dedicated/bin/python")
    assert "--force-download" in run.call_args.args
    assert not (model / "semantic_search_checkpoint.json").exists()


def test_missing_small_tokenizer_file_preserves_verified_large_weights_during_resume(quick, tmp_path):
    model = tmp_path / ".cache/models/qwen3-embedding-0.6b"
    sha = checkpoint(quick, model)
    missing = model / "tokenizer.json"
    missing.unlink()
    def resume(*args):
        assert "--force-download" not in args
        missing.write_text("resumed tokenizer")
    with patch.object(quick, "ROOT", tmp_path), patch.object(quick, "WEIGHTS_SHA", sha), patch.object(quick, "run", side_effect=resume):
        quick.setup_model(tmp_path / "dedicated/bin/python")
    assert json.loads((model / "semantic_search_checkpoint.json").read_text())["weights_etag"] == sha


def test_valid_json_nonobject_configuration_has_actionable_refusal(quick, tmp_path):
    sha = checkpoint(quick, tmp_path)
    (tmp_path / "config.json").write_text("[]")
    with patch.object(quick, "WEIGHTS_SHA", sha), pytest.raises(ValueError, match="configuration"):
        quick.check_model(tmp_path)
