"""Reproduce CPU-only source inspection on the pinned public Django checkout.

Run from this project with its language dependencies installed:
  python results/source-preflight-2026-10-16/verify.py --repo /path/to/django --output /new/output
The output directory must not exist. No checkout/download/model setup is done.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
DJANGO_REVISION = "9e7cc2b628fe8fd3895986af9b7fc9525034c1b0"
TEMPLATE = "django/views/templates/i18n_catalog.js"
GUARD = '''import os, sys
class Guard:
    def find_spec(self, fullname, path=None, target=None):
        blocked = ("semantic_search.backend", "semantic_search.embeddings", "semantic_search.context",
                   "semantic_search.store", "mlx", "mlx_embeddings", "torch", "transformers",
                   "huggingface_hub", "tiktoken", "sqlite3")
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            raise AssertionError("Forbidden preflight import: " + fullname)
sys.meta_path.insert(0, Guard())
def audit(event, args):
    if event.startswith("socket.") or event == "sqlite3.connect":
        raise AssertionError("Network/database operation: " + event)
    if event == "open" and isinstance(args[2], int) and args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
        raise AssertionError("Filesystem write")
    if event in ("os.mkdir", "os.remove", "os.rmdir", "os.rename", "os.link", "os.symlink", "os.truncate"):
        raise AssertionError("Filesystem mutation: " + event)
    if event == "subprocess.Popen" and os.path.basename(str(args[1][0])) != "git":
        raise AssertionError("Non-Git subprocess")
sys.addaudithook(audit)
'''


def git(repo, *arguments):
    return subprocess.check_output(["git", "-C", str(repo), *arguments], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    assert git(repo, "rev-parse", "HEAD") == DJANGO_REVISION
    assert not git(repo, "status", "--porcelain")
    args.output.mkdir(parents=True, exist_ok=False)
    cases = []
    with tempfile.TemporaryDirectory(prefix="source preflight guard ") as temporary:
        guard = Path(temporary)
        (guard / "sitecustomize.py").write_text(GUARD)
        environment = dict(os.environ, PYTHONPATH=os.pathsep.join((str(guard), str(ROOT / "src"))),
                           PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")

        def inspect(name, exclusions):
            arguments = [sys.executable, "-m", "semantic_search.cli", "inspect", "--repo", str(repo),
                         "--source-root", "django", "--limit", "10", "--json"]
            for pattern in exclusions:
                arguments.extend(("--exclude-source", pattern))
            started = time.perf_counter()
            result = subprocess.run(arguments, env=environment, cwd=guard, text=True,
                                    capture_output=True, timeout=120)
            elapsed = time.perf_counter() - started
            assert not result.stderr, result.stderr
            report = json.loads(result.stdout)
            assert result.returncode == (0 if report["status"] == "ready" else 1)
            assert report["stable_snapshot"] and report["membership"]["git_revision"] == DJANGO_REVISION
            assert report["files"]["eligible"] == 970
            report["repo"] = "<local public Django checkout path omitted>"
            (args.output / (name + ".json")).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
            cases.append({"name": name, "returncode": result.returncode, "status": report["status"],
                          "elapsed_s": elapsed, "source_exclusions": list(exclusions),
                          "included_files": report["files"]["included"], "parsed_files": report["files"]["parsed"],
                          "windows": report["windows"]["total"],
                          "unique_documents": report["windows"]["unique_documents"],
                          "oversized_windows": report["windows"]["over_threshold"],
                          "likely_minified_files": report["likely_minified"]["count"]})
            return report

        broad = inspect("broad", [])
        assert broad["status"] == "blocked"
        assert broad["errors"]["items"] == [{"code": "parse_error", "path": TEMPLATE, "line": 1}]
        assert broad["likely_minified"]["count"] == 3 and broad["windows"]["over_threshold"] == 853
        template_excluded = inspect("template-excluded", [TEMPLATE])
        assert template_excluded["status"] == "ready" and template_excluded["files"]["included"] == 969
        assert template_excluded["windows"]["over_threshold"] == 853
        exclusions = sorted(item["pattern"] for item in broad["exclusion_suggestions"]["items"])
        assert len(exclusions) == 4
        selected = inspect("four-files-excluded", exclusions)
        assert selected["status"] == "ready" and selected["files"]["included"] == 966
        assert selected["windows"]["unique_documents"] == 10734
        assert selected["windows"]["over_threshold"] == selected["likely_minified"]["count"] == 0

    assert not git(repo, "status", "--porcelain")
    receipt = {"recorded_at": datetime.now(timezone.utc).isoformat(),
               "django_revision": DJANGO_REVISION, "public_repo": "https://github.com/django/django",
               "python": sys.version, "cases": cases, "assertions_passed": True,
               "packages": {name: importlib.metadata.version(name) for name in
                            ("numpy", "tree-sitter", "tree-sitter-javascript", "tree-sitter-typescript")},
               "application_files_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                   for name in ("src/semantic_search/cli.py", "src/semantic_search/preflight.py",
                                "src/semantic_search/corpus.py", "src/semantic_search/languages.py",
                                "src/semantic_search/index.py")},
               "guard_sha256": hashlib.sha256(GUARD.encode()).hexdigest(),
               "report_transform": "Only repo field replaced to omit the local checkout location.",
               "limitations": ["Single existing Mac/runtime and saved public checkout; no clean-machine installation.",
                   "CPU source inspection only; no inference, retrieval-quality or warm-latency claim.",
                   "Guards cover the Python child; allowed Git subprocesses are not instrumented.",
                   "Elapsed times are one-run wall times, not a performance benchmark."]}
    (args.output / "verification.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    (args.output / "DJANGO_LICENSE.txt").write_bytes((repo / "LICENSE").read_bytes())
    print(json.dumps(cases, indent=2))


if __name__ == "__main__":
    main()
