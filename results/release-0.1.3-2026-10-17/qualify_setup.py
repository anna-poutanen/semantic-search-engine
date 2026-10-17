"""Exercise the exact release ZIP in a new, initially cache-free Mac directory.

Run from the release checkout with its developer Python. The actual launcher
downloads and uses its own Python, packages and weights; nothing is preseeded.
This retains the owned extraction for subsequent interactive/restart checks.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).resolve().parent
ASSETS = ROOT / ".cache/releases/v0.1.3"


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def main(*, archive_directory=None, output_directory=None):
    global ASSETS, OUTPUT
    if archive_directory is not None:
        ASSETS = Path(archive_directory).resolve()
    if output_directory is not None:
        OUTPUT = Path(output_directory).resolve()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    archive = ASSETS / "semantic-search-mac-0.1.3.zip"
    owner = Path(tempfile.mkdtemp(prefix="semantic-search-v013-", dir="/private/tmp"))
    (owner / "OWNED_RELEASE_QUALIFICATION").write_text("v0.1.3 temporary setup qualification\n")
    destination = owner / "Download With Spaces"
    subprocess.run(["/usr/bin/unzip", "-q", str(archive), "-d", str(destination)], check=True)
    app = next(destination.iterdir())
    assert " " in str(app) and not (app / ".cache").exists()
    assert subprocess.run(["git", "-C", str(app), "rev-parse", "--is-inside-work-tree"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0
    manifest = json.loads((app / "BUNDLE_MANIFEST.json").read_text())
    assert manifest["version"] == "0.1.3"
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            relative = entry.filename.split("/", 1)[1]
            assert (app / relative).read_bytes() == bundle.read(entry)
    for name, row in manifest["files"].items():
        assert digest(app / name) == row["sha256"]
    assert (app / "Get Started.command").stat().st_mode & 0o111
    fixture = owner / "Tracked Source With Spaces"
    (fixture / "src").mkdir(parents=True)
    (fixture / "src/good.py").write_text("def normalize(value):\n    return value.strip().lower()\n")
    (fixture / "src/template.js").write_text("function template(){return {{ invalid_template }};}\n")
    (fixture / "src/vendor.min.js").write_text('function vendor(){return "' + "x" * 33_000 + '";}\n')
    subprocess.run(["git", "init", "--quiet", str(fixture)], check=True)
    subprocess.run(["git", "-C", str(fixture), "add", "src"], check=True)
    environment = dict(os.environ, PATH="/usr/bin:/bin:/usr/sbin:/sbin",
                       GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    removed = ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN",
               "GH_TOKEN", "GITHUB_TOKEN", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "UV_OFFLINE")
    for name in removed:
        environment.pop(name, None)
    state = {"owner": str(owner), "app": str(app), "fixture": str(fixture),
             "zip_sha256": digest(archive), "version": "0.1.3", "started_at": datetime.now(timezone.utc).isoformat(),
             "initial_cache_absent": True, "outside_git": True, "path_contains_spaces": True,
             "archive_extracted_bytes_verified": True, "manifest_files_verified": len(manifest["files"]),
             "launcher_executable": True, "preseeded_runtime_or_model": False,
             "child_path": environment["PATH"], "child_inherited_python_and_auth_settings_removed": list(removed),
             "steps": []}
    state_path = ASSETS / "qualification-state.json"

    def save():
        state_path.write_text(json.dumps(state, indent=2) + "\n")

    def run(name, arguments, expected, timeout):
        log = OUTPUT / (name + ".log")
        started = time.perf_counter()
        with log.open("xb") as stream:
            process = subprocess.Popen(["/bin/bash", "Get Started.command", *arguments], cwd=app,
                                       env=environment, stdout=stream, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            try:
                returned = process.wait(timeout=timeout)
            except BaseException:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                raise
        row = {"name": name, "arguments": arguments, "returncode": returned,
               "elapsed_s": time.perf_counter() - started, "log_sha256": digest(log)}
        state["steps"].append(row)
        save()
        assert returned == expected, f"{name} exit {returned}, expected {expected}; see {log}"
        print(json.dumps(row), flush=True)

    save()
    run("doctor-before-setup", ["--doctor"], 1, 30)
    assert not (app / ".cache").exists()
    run("cold-invalid-preflight", ["--preflight", "--repo", str(fixture)], 1, 1200)
    assert not (app / ".cache/models").exists()
    assert not (app / ".cache/quickstart/indexes").exists()
    invalid_text = (OUTPUT / "cold-invalid-preflight.log").read_text()
    assert 'source does not parse' in invalid_text and 'template.js' in invalid_text
    run("explicit-exclusion-preflight", ["--preflight", "--repo", str(fixture),
                                        "--exclude-source", "src/template.js"], 0, 120)
    assert not (app / ".cache/models").exists()
    text = (OUTPUT / "explicit-exclusion-preflight.log").read_text()
    assert 'likely minified' in text and 'nothing was excluded automatically' in text
    assert 'Source preflight complete' in text
    run("first-model-download-and-check", ["--check"], 0, 1800)
    receipts = list((app / ".cache/demos").glob("*/.semantic-search/demo.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text())
    assert len(receipt["http"]) == len(receipt["mcp"]["searches"]) == 3
    assert receipt["shutdown"] == {"returncode": 0, "forced_kill": False}
    (OUTPUT / "toy-check.json").write_text(json.dumps(receipt, indent=2) + "\n")
    provenance = app / ".cache/models/qwen3-embedding-0.6b/semantic_search_checkpoint.json"
    checkpoint = json.loads(provenance.read_text())
    assert digest(provenance.parent / "model.safetensors") == checkpoint["weights_etag"]
    assert checkpoint["quantized"] is False
    inventory = json.loads((app / ".cache/quickstart/runtime.json").read_text())["inventory"]
    state.update(checkpoint=checkpoint, runtime=inventory, cold_setup_passed=True,
                 model_weights_downloaded=True, exact_toy_http_and_mcp_source_checks_passed=True,
                 platform=subprocess.check_output(["/usr/bin/sw_vers"], text=True))
    save()
    (OUTPUT / "cold-setup.json").write_text(json.dumps(state, indent=2) + "\n")
    print("Full fresh-folder launcher setup and real HTTP/MCP checks passed.", flush=True)


if __name__ == "__main__":
    main()
