"""Actual command/context file transactions, rollback and cleanup error ordering."""

import io
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from common import checkout, run_tests

checkout("click", "src")
import click
from click.testing import CliRunner


class AtomicTransactions(unittest.TestCase):
    def invoke(self, path, *, lazy, action="return", atomic=True):
        observed = []

        @click.command()
        @click.option("--output", type=click.File("w", lazy=lazy, atomic=atomic))
        @click.pass_context
        def command(ctx, output):
            if action != "unused":
                output.write("replacement\n")
                output.flush()
                observed.append(path.read_text() if path.exists() else None)
            if action == "error":
                raise click.ClickException("callback failed")
            if action == "exit0":
                ctx.exit(0)
            if action == "exit2":
                ctx.exit(2)

        return CliRunner().invoke(command, ["--output", str(path)]), observed

    def test_command_success_commits_and_failure_rolls_back_for_eager_lazy(self):
        for lazy in (False, True, None):
            for action in ("return", "error", "exit0", "exit2"):
                with (
                    self.subTest(lazy=lazy, action=action),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    path = Path(directory) / "out.txt"
                    path.write_text("original\n")
                    result, during = self.invoke(path, lazy=lazy, action=action)
                    self.assertEqual(during, ["original\n"])
                    success = action in ("return", "exit0")
                    self.assertEqual(
                        result.exit_code,
                        0 if success else (2 if action == "exit2" else 1),
                    )
                    self.assertEqual(
                        path.read_text(), "replacement\n" if success else "original\n"
                    )
                    self.assertEqual(
                        sorted(p.name for p in Path(directory).iterdir()), ["out.txt"]
                    )

    def test_new_destination_absent_on_rollback_and_unused_lazy_absent(self):
        for lazy in (False, True):
            with self.subTest(lazy=lazy), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "new.txt"
                result, _ = self.invoke(path, lazy=lazy, action="error")
                self.assertEqual(result.exit_code, 1)
                self.assertFalse(path.exists())
                self.assertEqual(list(Path(directory).iterdir()), [])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unused.txt"
            result, _ = self.invoke(path, lazy=True, action="unused")
            self.assertEqual(result.exit_code, 0)
            self.assertFalse(path.exists())

    def test_explicit_file_contexts_rollback_and_close_binary_handles(self):
        for lazy in (False, True):
            with self.subTest(lazy=lazy), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "out.bin"
                path.write_bytes(b"original")
                marker = RuntimeError("primary callback")
                with self.assertRaises(RuntimeError) as raised:
                    with click.open_file(path, "wb", atomic=True, lazy=lazy) as handle:
                        handle.write(b"new\x00bytes")
                        handle.flush()
                        raise marker
                self.assertIs(raised.exception, marker)
                self.assertTrue(handle.closed)
                self.assertEqual(path.read_bytes(), b"original")
                self.assertEqual(
                    sorted(p.name for p in Path(directory).iterdir()), ["out.bin"]
                )

    def test_explicit_success_preserves_permissions_and_close_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "out"
            path.write_bytes(b"old")
            path.chmod(0o640)
            with click.open_file(path, "w", atomic=True) as handle:
                handle.write("new")
            handle.close()
            self.assertEqual(path.read_text(), "new")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)

    def test_commit_failure_is_never_reported_as_success(self):
        for lazy in (False, True):
            with self.subTest(lazy=lazy), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "out"
                path.write_text("old")

                @click.command()
                @click.option("--output", type=click.File("w", atomic=True, lazy=lazy))
                def command(output):
                    output.write("new")
                    output.flush()
                    for temporary in Path(directory).iterdir():
                        if temporary != path:
                            temporary.unlink()

                result = CliRunner().invoke(command, ["--output", str(path)])
                self.assertEqual(result.exit_code, 1)
                self.assertIsInstance(result.exception, FileNotFoundError)
                self.assertEqual(path.read_text(), "old")

    def test_missing_temp_during_abort_preserves_primary_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "out"
            path.write_text("old")
            marker = ValueError("primary")
            with self.assertRaises(ValueError) as raised:
                with click.open_file(path, "w", atomic=True) as handle:
                    handle.write("new")
                    handle.flush()
                    for temporary in Path(directory).iterdir():
                        if temporary != path:
                            temporary.unlink()
                    raise marker
            self.assertIs(raised.exception, marker)
            self.assertEqual(path.read_text(), "old")

    def test_abort_cleanup_error_preserves_primary_with_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "out"
            path.write_text("old")
            marker = ValueError("primary")
            real_unlink = os.unlink

            def deny(candidate, *args, **kwargs):
                target = Path(os.fsdecode(candidate)).resolve()
                if (
                    target.parent == Path(directory).resolve()
                    and target != path.resolve()
                ):
                    raise PermissionError("cannot remove temporary")
                return real_unlink(candidate, *args, **kwargs)

            with self.assertRaises(ValueError) as raised:
                with (
                    patch("os.unlink", side_effect=deny),
                    patch("os.remove", side_effect=deny),
                ):
                    with click.open_file(path, "w", atomic=True) as handle:
                        handle.write("new")
                        handle.flush()
                        raise marker
            self.assertIs(raised.exception, marker)
            notes = " ".join(getattr(marker, "__notes__", []))
            self.assertTrue(
                "cannot remove temporary" in notes
                or marker.__cause__ is not None
                or marker.__context__ is not None
            )
            self.assertEqual(path.read_text(), "old")

    def test_context_resources_receive_error_and_context_stack_is_restored(self):
        events = []

        class Resource:
            def __enter__(self):
                return self

            def __exit__(self, typ, value, tb):
                events.append((typ, value))
                return False

        marker = ValueError("resource callback")
        with self.assertRaises(ValueError):
            with click.Context(click.Command("sample")) as ctx:
                ctx.with_resource(Resource())
                raise marker
        self.assertEqual(events, [(ValueError, marker)])
        self.assertIsNone(click.get_current_context(silent=True))

    def test_nonatomic_and_stdout_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ordinary"
            path.write_text("old")
            result, _ = self.invoke(path, lazy=True, action="error", atomic=False)
            self.assertEqual(result.exit_code, 1)
            self.assertEqual(path.read_text(), "replacement\n")
        stream = io.StringIO()
        with patch("sys.stdout", stream):
            with click.open_file("-", "w", atomic=True) as handle:
                handle.write("hello")
            self.assertFalse(stream.closed)
        self.assertEqual(stream.getvalue(), "hello")


if __name__ == "__main__":
    run_tests()
