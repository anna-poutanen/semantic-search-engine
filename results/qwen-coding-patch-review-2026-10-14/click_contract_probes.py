#!/usr/bin/env python3
"""Independent CPU-only Click diagnostics; frozen workflow grades stay unchanged.

Cases are fixed before any of this study's six Click trials finishes. The
suppression/commit interaction is recorded as a scope-review candidate rather
than silently extending the formal checker. No candidate sources are edited.
"""
from __future__ import annotations

import argparse
from contextlib import suppress
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.dont_write_bytecode = True


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def sources(repo):
    return {str(path.relative_to(repo)): sha(path.read_bytes()) for path in sorted((repo / "src/click").glob("*.py"))}


def error_info(error):
    return None if error is None else {"type": type(error).__name__, "message": str(error),
        "notes": list(getattr(error, "__notes__", [])),
        "cause_type": type(error.__cause__).__name__ if error.__cause__ else None,
        "context_type": type(error.__context__).__name__ if error.__context__ else None}


def diagnostic(primary, cleanup):
    pending, seen = [primary], set()
    while pending and len(seen) < 32:
        error = pending.pop()
        if id(error) in seen:
            continue
        seen.add(id(error))
        if error is cleanup or getattr(error, "__notes__", []):
            return True
        pending.extend(child for child in (error.__cause__, error.__context__, *getattr(error, "exceptions", ()))
                       if isinstance(child, BaseException))
    return False


def case_names():
    result = []
    for mode in ("eager", "lazy"):
        result += [f"direct_{name}_{mode}" for name in ("system_exit0", "click_exit0", "keyboard_interrupt")]
        result += [f"nested_{outcome}_{mode}" for outcome in ("success", "failure")]
        result += [f"explicit_close_then_later_error_{mode}", f"clean_commit_failure_older_suppressor_{mode}"]
    result += ["unused_lazy_explicit_close"]
    for mode in ("eager", "lazy", "default_lazy"):
        result += [f"registered_{kind}_baseexception_{mode}" for kind in ("callback", "resource")]
        result += [f"caller_owned_binary_stdout_parameter_{mode}"]
    return result


def run(repo, only_case):
    before = sources(repo)
    sys.path.insert(0, str(repo / "src"))
    import click
    from click.testing import CliRunner
    if Path(click.__file__).resolve() != repo / "src/click/__init__.py":
        raise RuntimeError("Diagnostic imported another Click checkout")
    cases = {}
    for lazy, mode in ((False, "eager"), (True, "lazy")):
        for factory, name in ((lambda: SystemExit(0), "system_exit0"),
                              (lambda: click.exceptions.Exit(0), "click_exit0"),
                              (lambda: KeyboardInterrupt("controlled direct body failure"), "keyboard_interrupt")):
            if only_case != f"direct_{name}_{mode}":
                continue
            with TemporaryDirectory(prefix="click-review-direct-") as directory:
                path = Path(directory) / "output.txt"
                path.write_text("original")
                primary, outer = factory(), None
                handle = None
                try:
                    with click.open_file(path, "w", atomic=True, lazy=lazy) as handle:
                        handle.write("updated")
                        raise primary
                except BaseException as error:
                    outer = error
                cases[f"direct_{name}_{mode}"] = {"passed": outer is primary and path.read_text() == "original"
                    and handle is not None and handle.closed and len(list(Path(directory).iterdir())) == 1,
                    "primary_preserved": outer is primary, "outer_error": error_info(outer),
                    "destination": path.read_text(), "owned_handle_closed": bool(handle and handle.closed),
                    "temporary_files_remaining": len(list(Path(directory).iterdir())) - 1}

        for fail in (False, True):
            if only_case != f"nested_{'failure' if fail else 'success'}_{mode}":
                continue
            with TemporaryDirectory(prefix="click-review-nesting-") as directory:
                path = Path(directory) / "output.txt"
                path.write_text("original")
                primary, outer, events = ValueError("controlled outer callback failure"), None, []
                during, deferred = None, False
                class Resource:
                    def __enter__(self):
                        return self
                    def __exit__(self, typ, value, tb):
                        events.append({"type": typ.__name__ if typ else None, "primary": value is primary,
                                       "traceback_present": tb is not None})
                        return False
                ctx = click.Context(click.Command("sample"))
                try:
                    with ctx:
                        ctx.with_resource(Resource())
                        output = ctx.with_resource(click.open_file(path, "w", atomic=True, lazy=lazy))
                        with ctx:
                            output.write("updated")
                        deferred = not events and not output.closed and click.get_current_context() is ctx
                        during = path.read_text()
                        if fail:
                            raise primary
                except BaseException as error:
                    outer = error
                expected_events = [{"type": "ValueError" if fail else None, "primary": fail,
                                    "traceback_present": fail}]
                cases[f"nested_{'failure' if fail else 'success'}_{mode}"] = {
                    "passed": deferred and during == "original" and events == expected_events
                    and outer is (primary if fail else None) and path.read_text() == ("original" if fail else "updated")
                    and click.get_current_context(silent=True) is None and len(list(Path(directory).iterdir())) == 1,
                    "cleanup_deferred": deferred, "destination_during_outer": during, "events": events,
                    "outer_error": error_info(outer), "destination_after": path.read_text(),
                    "context_stack_restored": click.get_current_context(silent=True) is None}

        if only_case == f"explicit_close_then_later_error_{mode}":
            with TemporaryDirectory(prefix="click-review-explicit-") as directory:
                path = Path(directory) / "output.txt"
                path.write_text("original")
                primary, outer = ValueError("later body failure after explicit commit"), None
                try:
                    with click.open_file(path, "w", atomic=True, lazy=lazy) as handle:
                        handle.write("updated")
                        handle.close()
                        handle.close()
                        raise primary
                except BaseException as error:
                    outer = error
                cases[f"explicit_close_then_later_error_{mode}"] = {"passed": outer is primary
                    and path.read_text() == "updated" and handle.closed and len(list(Path(directory).iterdir())) == 1,
                    "primary_preserved": outer is primary, "outer_error": error_info(outer),
                    "destination": path.read_text(), "owned_handle_closed": handle.closed}

        # The contract explicitly forbids a clean commit failure becoming a CLI
        # success. A separate older public suppressor can expose an interaction
        # outside the frozen fourteen checks; classify it only after scope review.
        if only_case == f"clean_commit_failure_older_suppressor_{mode}":
            with TemporaryDirectory(prefix="click-review-commit-") as directory:
                path = Path(directory) / "output.txt"
                path.write_text("original")
                @click.command()
                @click.pass_context
                def command(ctx):
                    ctx.with_resource(suppress(FileNotFoundError))
                    output = ctx.with_resource(click.open_file(path, "w", atomic=True, lazy=lazy))
                    output.write("updated")
                    output.flush()
                    for temporary in Path(directory).iterdir():
                        if temporary != path:
                            temporary.unlink()
                result = CliRunner().invoke(command, [])
                cases[f"clean_commit_failure_older_suppressor_{mode}"] = {
                    "scope": "Candidate supplemental contract interpretation; frozen grades unchanged",
                    "passed": result.exit_code != 0 and isinstance(result.exception, FileNotFoundError)
                    and path.read_text() == "original" and click.get_current_context(silent=True) is None,
                    "exit_code": result.exit_code, "outer_error": error_info(result.exception),
                    "destination": path.read_text(), "context_stack_restored": click.get_current_context(silent=True) is None}

    if only_case == "unused_lazy_explicit_close":
        with TemporaryDirectory(prefix="click-review-unused-") as directory:
            path = Path(directory) / "unused.txt"
            outer = None
            try:
                with click.open_file(path, "w", atomic=True, lazy=True) as output:
                    output.close()
                    output.close()
            except BaseException as error:
                outer = error
            # Do not inspect lazy.closed: attribute lookup could itself open it.
            cases["unused_lazy_explicit_close"] = {"passed": outer is None and not path.exists()
                and not list(Path(directory).iterdir()), "outer_error": error_info(outer), "destination_exists": path.exists()}

    for lazy, mode in ((False, "eager"), (True, "lazy"), (None, "default_lazy")):
        for kind in ("callback", "resource"):
            if only_case != f"registered_{kind}_baseexception_{mode}":
                continue
            with TemporaryDirectory(prefix="click-review-baseexception-") as directory:
                path = Path(directory) / "output.txt"
                path.write_text("original")
                primary = ValueError("controlled callback primary")
                cleanup = SystemExit(0) if kind == "callback" else KeyboardInterrupt("controlled registered cleanup")
                def fail_cleanup():
                    raise cleanup
                class Resource:
                    def __enter__(self):
                        return self
                    def __exit__(self, *_):
                        raise cleanup
                @click.command()
                @click.argument("output", type=click.File("w", atomic=True, lazy=lazy))
                @click.pass_context
                def command(ctx, output):
                    if kind == "callback":
                        ctx.call_on_close(fail_cleanup)
                    else:
                        ctx.with_resource(Resource())
                    output.write("updated")
                    raise primary
                result = CliRunner().invoke(command, [str(path)])
                cases[f"registered_{kind}_baseexception_{mode}"] = {
                    "passed": result.exit_code != 0 and result.exception is primary and diagnostic(primary, cleanup)
                    and path.read_text() == "original" and click.get_current_context(silent=True) is None
                    and len(list(Path(directory).iterdir())) == 1,
                    "exit_code": result.exit_code, "primary_preserved": result.exception is primary,
                    "outer_error": error_info(result.exception), "cleanup_type": type(cleanup).__name__,
                    "cleanup_diagnostic": diagnostic(primary, cleanup), "destination": path.read_text(),
                    "context_stack_restored": click.get_current_context(silent=True) is None,
                    "temporary_files_remaining": len(list(Path(directory).iterdir())) - 1}

        if only_case != f"caller_owned_binary_stdout_parameter_{mode}":
            continue
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding="utf-8")
        outer = None
        try:
            with patch("sys.stdout", stream):
                with click.Context(click.Command("sample")) as ctx:
                    output = click.File("wb", atomic=True, lazy=lazy).convert("-", None, ctx)
                    output.write(b"owned by caller")
                stream.flush()
        except BaseException as error:
            outer = error
        cases[f"caller_owned_binary_stdout_parameter_{mode}"] = {"passed": outer is None
            and not stream.closed and not buffer.closed and buffer.getvalue() == b"owned by caller",
            "outer_error": error_info(outer), "stream_closed": stream.closed, "buffer_closed": buffer.closed,
            "value": buffer.getvalue().decode() if not buffer.closed else None}
        if not stream.closed:
            stream.detach()
        buffer.close()

    after = sources(repo)
    return {"version": 1, "classification": "Independent supplemental CPU diagnostics; frozen formal grades unchanged",
            "model_calls": 0, "hosted_calls": 0, "source_unchanged": before == after,
            "source_sha256": before, "source_after_sha256": after,
            "probe_sha256": sha(Path(__file__).read_bytes()), "cases": cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--case", choices=case_names())
    args = parser.parse_args()
    repo = args.repo.resolve()
    if args.case:
        result = run(repo, args.case)
    else:
        # A leaking context stack or BaseException in one case must not poison
        # later evidence. Each case gets a fresh CPU-only Python interpreter.
        before = sources(repo)
        cases, executions = {}, []
        for name in case_names():
            try:
                process = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                    "--repo", str(repo), "--case", name], capture_output=True, text=True, timeout=15)
                receipt_error = None
                try:
                    receipt = json.loads(process.stdout) if process.returncode == 0 else None
                except (ValueError, TypeError) as error:
                    receipt, receipt_error = None, type(error).__name__ + ": " + str(error)
                valid = (receipt is not None and set(receipt["cases"]) == {name} and receipt["source_unchanged"] is True
                         and receipt["source_sha256"] == before and receipt["probe_sha256"] == sha(Path(__file__).read_bytes()))
                cases[name] = receipt["cases"][name] if valid else {"passed": None, "diagnostic_execution_failed": True}
                executions.append({"case": name, "exit_code": process.returncode, "receipt_valid": valid,
                                   "stderr": process.stderr, "receipt_error": receipt_error})
            except subprocess.TimeoutExpired:
                cases[name] = {"passed": None, "diagnostic_execution_failed": True, "timed_out": True}
                executions.append({"case": name, "exit_code": None, "receipt_valid": False, "timed_out": True})
        after = sources(repo)
        result = {"version": 1, "classification": "Independent supplemental CPU diagnostics; frozen formal grades unchanged",
                  "model_calls": 0, "hosted_calls": 0, "source_unchanged": before == after,
                  "source_sha256": before, "source_after_sha256": after,
                  "probe_sha256": sha(Path(__file__).read_bytes()), "cases": cases,
                  "isolation": "One fresh Python process per case; no private context stack reset", "executions": executions}
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
