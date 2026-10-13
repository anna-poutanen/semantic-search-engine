#!/usr/bin/env python3
"""Post-output CPU probe of callback failure plus a suppressing Click resource.

This supplemental case was selected from terminal 05 source inspection. It uses
public APIs and a standard context manager; frozen study grades remain unchanged.
"""
from __future__ import annotations

import argparse
from contextlib import suppress
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory


def has_cleanup_diagnostic(primary, cleanup):
    pending, seen = [(primary, 0)], set()
    while pending and len(seen) < 32:
        error, depth = pending.pop()
        if id(error) in seen or depth > 8:
            continue
        seen.add(id(error))
        if error is cleanup or getattr(error, "__notes__", None):
            return True
        pending.extend((child, depth + 1) for child in
                       (error.__cause__, error.__context__, *getattr(error, "exceptions", ()))
                       if isinstance(child, BaseException))
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    sys.path.insert(0, str(repo / "src"))
    import click
    from click.testing import CliRunner
    if Path(click.__file__).resolve() != repo / "src/click/__init__.py":
        raise RuntimeError("Probe imported a different Click checkout")
    record = {"version": 3, "classification": "Post-output supplemental resource interactions; frozen formal grades unchanged",
              "revision": "All case inputs unchanged from v2; after independent scope review accept bounded recursive cleanup cause/context/ExceptionGroup chains or notes, still requiring the original outer primary exception",
              "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "implementation_neutral": "Real CliRunner command, public atomic File parameter and with_resource(contextlib.suppress); no private field names or source mutation",
              "contract": "A failing callback must preserve its primary exception and roll back the atomic destination, even when another resource returns a truthy suppression result",
              "cases": {}}
    for lazy, name in ((False, "eager"), (True, "lazy"), (None, "default_lazy")):
        with TemporaryDirectory(prefix="click-resource-edge-") as directory:
            target = Path(directory) / "output.txt"
            target.write_text("original")
            primary = ValueError("controlled callback failure")

            @click.command()
            @click.argument("output", type=click.File("w", atomic=True, lazy=lazy))
            @click.pass_context
            def command(ctx, output):
                ctx.with_resource(suppress(ValueError))
                output.write("updated")
                raise primary

            result = CliRunner().invoke(command, [str(target)])
            value = target.read_text()
            record["cases"][name] = {"passed": result.exit_code != 0 and result.exception is primary and value == "original",
                                      "exit_code": result.exit_code,
                                      "primary_exception_preserved": result.exception is primary,
                                      "exception_type": type(result.exception).__name__ if result.exception else None,
                                      "destination": value,
                                      "remaining_temporary_files": len(list(Path(directory).iterdir())) - 1}
    for kind in ("callback", "resource"):
        with TemporaryDirectory(prefix="click-cleanup-edge-") as directory:
            target = Path(directory) / "output.txt"
            target.write_text("original")
            primary = ValueError("controlled primary callback failure")
            cleanup = OSError("controlled registered cleanup failure")

            def fail_cleanup():
                raise cleanup

            class Resource:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc_value, tb):
                    raise cleanup

            @click.command()
            @click.argument("output", type=click.File("w", atomic=True))
            @click.pass_context
            def command(ctx, output):
                if kind == "callback":
                    ctx.call_on_close(fail_cleanup)
                else:
                    ctx.with_resource(Resource())
                output.write("updated")
                raise primary

            result = CliRunner().invoke(command, [str(target)])
            diagnostic = has_cleanup_diagnostic(primary, cleanup)
            value = target.read_text()
            record["cases"][f"registered_{kind}_cleanup_preserves_primary"] = {
                "passed": result.exit_code != 0 and result.exception is primary and diagnostic and value == "original",
                "exit_code": result.exit_code, "primary_exception_preserved": result.exception is primary,
                "exception_type": type(result.exception).__name__ if result.exception else None,
                "destination": value, "cleanup_diagnostic_on_primary": diagnostic,
                "context_stack_restored": click.get_current_context(silent=True) is None}
    print(json.dumps(record, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
