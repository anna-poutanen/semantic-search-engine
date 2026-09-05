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
    record = {"version": 1, "classification": "Post-output supplemental resource interaction; frozen formal grades unchanged",
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
    print(json.dumps(record, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
