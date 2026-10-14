#!/usr/bin/env python3
"""Prepare a tiny local demo; --run explicitly starts Qwen and an owned service."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "src/labels.py": 'def normalize_label(value):\n    return " ".join(value.split()).lower()\n',
    "src/unique.js": 'export function uniqueValues(values) {\n  return [...new Set(values)];\n}\n',
    "src/clamp.ts": 'export function clamp(value: number, minimum: number, maximum: number): number {\n  return Math.min(maximum, Math.max(minimum, value));\n}\n',
}
QUERIES = (
    "Normalize a label by collapsing whitespace and lowercasing it.",
    "Remove duplicate values while keeping their first occurrence order.",
    "Clamp a number between a minimum and maximum.",
)
SETTINGS = {"retrieval": "dense", "rerank": False, "candidates": 4,
            "max_results": 3, "source_budget": 4000, "token_budget": 4096,
            "include_dependencies": False}


def fresh_destination():
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    return ROOT / ".cache/demos" / f"toy-{stamp}-{uuid.uuid4().hex[:8]}"


def cache_paths(repo):
    directory = Path(repo) / ".semantic-search/indexes"
    identity = str(Path(repo).resolve()) + "\nsrc"
    return directory, directory / (hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".sqlite3")


def prepare(destination):
    """Exclusive creation; never reset, reuse, commit into, or delete another repo."""
    target = Path(destination).expanduser().absolute()
    if os.path.lexists(target):
        raise ValueError(f"Refusing to overwrite existing path: {target}; choose a fresh --destination")
    target = target.parent.resolve() / target.name
    if shutil.which("git") is None:
        raise ValueError("Git is required; install the macOS command line tools with: xcode-select --install")
    if target.is_relative_to(ROOT):
        relative = target.relative_to(ROOT).as_posix()
        checkout = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--is-inside-work-tree"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        ignored = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "--quiet", "--", relative],
                                 stderr=subprocess.DEVNULL, check=False) if checkout.returncode == 0 else None
        if ((ignored is not None and ignored.returncode != 0)
                or (ignored is None and not target.is_relative_to(ROOT / ".cache/demos"))):
            raise ValueError("A demo inside this project must use an ignored destination such as .cache/demos/My Demo")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir()  # Exclusive even if a competing creator appeared after the check.
    repo = target.resolve()
    (repo / "src").mkdir()
    for name, source in SOURCES.items():
        (repo / name).write_bytes(source.encode("utf-8"))
    (repo / ".gitignore").write_text(".semantic-search/\n", encoding="utf-8")
    # Ambient templates, hooks, aliases and GIT_DIR must not alter the authored toy.
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    with tempfile.TemporaryDirectory(prefix="semantic-search-empty-git-template-") as template:
        subprocess.run(["git", "-C", str(repo), "init", "--quiet", "--initial-branch=main", f"--template={template}"],
                       env=environment, check=True)
        subprocess.run(["git", "-C", str(repo), "add", "--", "src", ".gitignore"], env=environment, check=True)
    return repo


def command(*parts):
    return shlex.join(str(part) for part in parts)


def instructions(repo, python, port=8750):
    cache, database = cache_paths(repo)
    cli = [python, "-m", "semantic_search.cli"]
    url = f"http://127.0.0.1:{port}"
    payload = json.dumps({"query": QUERIES[0], **SETTINGS}, ensure_ascii=False)
    lines = [
        f"Toy repository: {repo}", "Source root: src; three tracked implementations; no commit identity required.",
        f"Persistent SQLite index: {database}", f"Runtime Python: {python}",
        "If setup is missing, run these MANUALLY from the project (the demo never installs or downloads):",
        command("cd", ROOT),
        command("python3", ROOT / "scripts/setup_mac.py", "--runtime-only", "--venv-path", Path(python).parent.parent),
        command(python, ROOT / "scripts/setup_embeddings.py", "--venv-path", Path(python).parent.parent),
        "Terminal 1 — foreground resident Qwen service; H2O is unused; Ctrl-C closes this service:",
        command(*cli, "serve", "--repo", repo, "--source-root", "src", "--index-cache", cache, "--port", port),
        "Terminal 2 — saved CLI searches after Ready (no second model is loaded):",
        command(*cli, "status", "--server", url),
        *[command(*cli, "search", query, "--server", url, "--retrieval", "dense", "--no-rerank",
                  "--max-results", "3", "--source-budget", "4000", "--token-budget", "4096", "--json") for query in QUERIES],
        "Native HTTP example:",
        command("curl", "--noproxy", "*", "--fail-with-body", "-H", "Content-Type: application/json", "--data-binary", payload, url + "/search"),
        "MCP stdio adapter (an MCP client launches this command; it does not load another model):",
        command(*cli, "mcp", "--server", url),
        "Tool: search_code; arguments:", payload,
        "VS Code: open this folder with the reviewed extension installed:", command("code", repo),
        f"Connect / Check Service → select this folder → {url}; Find Implementation → use a query above → Saved tracked source.",
        "Preview a captured excerpt, then Open Verified Source. Edit a tracked source file without saving; repeat Find Implementation and choose Include eligible dirty tracked buffers.",
        "After another edit, the old result's navigation is stale; its captured preview remains available. Run a new dirty search or save and run a saved search.",
        "Refresh Index refreshes saved files only. New/untracked, deleted, unsupported and excluded files do not enter dirty searches.",
        "Automatic demo (starts one service, uses HTTP + official MCP stdio, then stops its own service):",
        command(python, ROOT / "scripts/demo_mac.py", "--run", "--python", python),
        "Automatic mode creates another fresh toy directory. Existing folders are never reused. Fixture/index/log/receipt remain for inspection; process cleanup is automatic.",
    ]
    return "\n".join(lines)


def verify_response(value, query, repo):
    """Check toy delivery, never scores, rank order, or held-out retrieval labels."""
    if value.get("query") != query or not value.get("results"):
        raise ValueError("Demo returned no matching request envelope/excerpts")
    if value.get("scope", {}).get("source_root") != "src" or len(value["results"]) > SETTINGS["max_results"]:
        raise ValueError("Demo response left its source scope or result limit")
    usage = value.get("token_usage", {})
    if usage.get("encoding") != "cl100k_base" or usage.get("budget") != SETTINGS["token_budget"] or type(usage.get("tokens")) is not int or not 0 <= usage["tokens"] <= SETTINGS["token_budget"]:
        raise ValueError("Demo compact token budget was not respected")
    output, total = [], 0
    for row in value["results"]:
        name, start, end = row.get("path"), row.get("start_line"), row.get("end_line")
        if name not in SOURCES or type(start) is not int or type(end) is not int:
            raise ValueError("Demo response contains an unknown source or line range")
        text = (Path(repo) / name).read_bytes().decode("utf-8")
        lines = re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text)
        lines = [line for line in lines if line]
        delivered = row.get("source")
        if not 1 <= start <= end <= len(lines) or delivered != "".join(lines[start - 1:end]):
            raise ValueError("Demo excerpt differs from saved physical source lines")
        if row.get("source_hash") != hashlib.sha256(delivered.encode("utf-8")).hexdigest():
            raise ValueError("Demo source hash differs from saved source")
        total += len(delivered)
        output.append({key: row[key] for key in ("path", "symbol", "start_line", "end_line", "source_hash")})
    if total > SETTINGS["source_budget"]:
        raise ValueError("Demo source character budget was exceeded")
    return {"query": query, "excerpts": output, "compact_tokens": usage["tokens"]}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def exercise_http(url, repo, timeout):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    results = []
    for query in QUERIES:
        request = urllib.request.Request(url + "/search", json.dumps({"query": query, **SETTINGS}, ensure_ascii=False).encode("utf-8"),
                                         {"Content-Type": "application/json"}, method="POST")
        with opener.open(request, timeout=timeout) as response:
            data = response.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ValueError("Demo response exceeded 1 MiB")
        results.append(verify_response(json.loads(data.decode("utf-8")), query, repo))
    return results


async def exercise_mcp(python, url, repo, timeout):
    from mcp import Client, StdioServerParameters  # Official optional SDK; --run only.
    from semantic_search.context import encoding, serialize_context
    parameters = StdioServerParameters(command=str(python), args=["-m", "semantic_search.mcp_server", "--server", url], cwd=ROOT)
    async with Client(parameters, mode="legacy", read_timeout_seconds=timeout) as client:
        tools = (await client.list_tools()).tools
        if [tool.name for tool in tools] != ["search_code"]:
            raise ValueError("Demo MCP adapter exposed unexpected tools")
        results = []
        for query in QUERIES:
            result = await client.call_tool("search_code", {"query": query, **SETTINGS})
            if result.is_error or not isinstance(result.structured_content, dict):
                raise ValueError("Demo MCP search failed or omitted structured content")
            text = serialize_context(result.structured_content)
            if (len(result.content) != 1 or result.content[0].type != "text" or result.content[0].text != text
                    or len(encoding().encode(text, disallowed_special=())) != result.structured_content.get("token_usage", {}).get("tokens")):
                raise ValueError("Demo MCP text/structured content or exact compact token count differs")
            results.append(verify_response(result.structured_content, query, repo))
        return {"protocol_version": client.protocol_version, "searches": results}


def run_mcp(python, url, repo, timeout):
    import anyio

    async def guarded():
        interrupted = False
        with anyio.fail_after(timeout * 4) as scope:
            def cancel(signum, frame):
                nonlocal interrupted
                interrupted = True
                scope.cancel()
            previous = {signum: signal.signal(signum, cancel) for signum in (signal.SIGINT, signal.SIGTERM)}
            try:
                result = await exercise_mcp(python, url, repo, timeout)
            finally:
                for signum, handler in previous.items():
                    signal.signal(signum, handler)
        if interrupted:
            raise KeyboardInterrupt
        return result

    # Cancel through AnyIO scopes, not native task.cancel()/killing a worker:
    # the pinned SDK shields its bounded stdio adapter/process-tree shutdown.
    return anyio.run(guarded)


def stop_owned(process):
    def group_signal(signum):
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass
    group_signal(signal.SIGTERM)
    forced = False
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        forced = True
        group_signal(signal.SIGKILL)
        process.wait(timeout=5)
    if not forced:
        deadline = time.monotonic() + 2
        while True:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                break
            if time.monotonic() >= deadline:
                forced = True
                group_signal(signal.SIGKILL)
                break
            time.sleep(.02)
    return {"returncode": process.returncode, "forced_kill": forced}


def run_demo(repo, python, timeout=120):
    cache, database = cache_paths(repo)
    artifacts = Path(repo) / ".semantic-search"
    artifacts.mkdir(exist_ok=True)
    log, receipt = artifacts / "service.log", artifacts / "demo.json"
    arguments = [str(python), "-m", "semantic_search.cli", "serve", "--repo", str(repo), "--source-root", "src",
                 "--index-cache", str(cache), "--port", "0", "--no-watch"]
    process = None
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    previous_term = signal.signal(signal.SIGTERM, interrupt)
    try:
        with log.open("wb") as stream:
            process = subprocess.Popen(arguments, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        began = time.monotonic()
        while time.monotonic() - began < timeout:
            with log.open("rb") as stream:
                text = stream.read(2 * 1024 * 1024).decode("utf-8", errors="replace")
            match = re.search(r"^Ready: (http://127\.0\.0\.1:[0-9]+)", text, re.MULTILINE)
            if match:
                url = match[1]
                break
            if process.poll() is not None:
                raise RuntimeError(f"Service could not start; inspect {log}. Run the manual runtime/checkpoint setup printed above.")
            time.sleep(.1)
        else:
            raise RuntimeError(f"Service readiness timed out; inspect {log}. No other process is stopped by this demo.")
        http = exercise_http(url, repo, timeout)
        mcp = run_mcp(python, url, repo, timeout)
    finally:
        try:
            shutdown = stop_owned(process) if process is not None else None
        finally:
            signal.signal(signal.SIGTERM, previous_term)
    if shutdown != {"returncode": 0, "forced_kill": False}:
        raise RuntimeError(f"Owned service did not stop cleanly: {shutdown}; inspect {log}")
    report = {"kind": "toy integration demonstration; not a retrieval benchmark", "repo": str(repo), "source_root": "src",
              "runtime_python": str(python), "model": "default pinned Qwen3-Embedding-0.6B; H2O off", "service_url": url,
              "database": str(database), "http": http, "mcp": mcp, "shutdown": shutdown}
    receipt.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"HTTP + official MCP demo completed; owned service closed. Receipt: {receipt}")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, help="Fresh directory; default: ignored .cache/demos/toy-<timestamp>-<id>")
    parser.add_argument("--python", type=Path, default=ROOT / ".venv/bin/python", help="Installed project runtime Python; never installed by this script")
    parser.add_argument("--port", type=int, default=8750, help="Port in printed manual instructions; automatic run uses an available port")
    parser.add_argument("--run", action="store_true", help="Explicitly run real Qwen, HTTP and official MCP stdio, then stop the owned service")
    parser.add_argument("--quiet", action="store_true", help="Print only the toy path and check outcome instead of manual setup instructions")
    parser.add_argument("--timeout", type=float, default=120, help="Positive readiness/request timeout in seconds")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("Require port 1–65535 and a finite positive timeout")
    python = args.python.expanduser().absolute()
    if args.run and (platform.system() != "Darwin" or platform.machine() != "arm64"):
        parser.error("Real Qwen demo requires Apple Silicon macOS; default preparation is model-free")
    if args.run and not python.is_file():
        parser.error(f"Runtime Python missing at {python}. Run python3 scripts/setup_mac.py --runtime-only; the demo installs nothing")
    if args.run:
        import importlib.metadata
        try:
            installed = importlib.metadata.version("mcp")
        except importlib.metadata.PackageNotFoundError:
            installed = None
        if installed != "2.3.0":
            parser.error("Run this script with the installed project runtime Python and pinned mcp==2.3.0. "
                         f"Use {command(python, ROOT / 'scripts/demo_mac.py', '--run', '--python', python)} after the manual runtime-only setup")
    try:
        repo = prepare(args.destination or fresh_destination())
        print(f"Toy repository: {repo}" if args.quiet else instructions(repo, python, args.port), flush=True)
        if args.run:
            run_demo(repo, python, args.timeout)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Demo failed: {error}\n")
    except KeyboardInterrupt:
        parser.exit(130, "Demo interrupted; owned service cleanup completed\n")


if __name__ == "__main__":
    main()
