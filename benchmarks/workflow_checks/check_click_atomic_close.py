"""Exercise automatic lazy output cleanup through a CLI invocation."""

from pathlib import Path
import tempfile
import unittest

from common import checkout, run_tests

checkout("click", "src")
import click
from click.testing import CliRunner


class AtomicOutput(unittest.TestCase):
    def invoke(self, destination, write=True, atomic=True):
        handles = []
        observed_during_callback = []

        @click.command()
        @click.option("--output", type=click.File("w", atomic=atomic))
        def command(output):
            handles.append(output)
            if write:
                output.write("replacement\n")
                output.flush()
                observed_during_callback.append(destination.read_text() if destination.exists() else None)

        result = CliRunner().invoke(command, ["--output", str(destination)])
        self.assertEqual(result.exit_code, 0, repr(result.exception))
        return handles[0], observed_during_callback

    def test_existing_atomic_file_commits_after_successful_command(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "output.txt"
            destination.write_text("original\n")
            handle, during = self.invoke(destination)
            self.assertEqual(during, ["original\n"])
            self.assertEqual(destination.read_text(), "replacement\n")
            self.assertTrue(handle.closed)
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()), ["output.txt"])

    def test_new_atomic_file_is_created_and_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "new.txt"
            handle, during = self.invoke(destination)
            self.assertEqual(during, [None])
            self.assertEqual(destination.read_text(), "replacement\n")
            self.assertTrue(handle.closed)

    def test_unused_lazy_output_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "unused.txt"
            self.invoke(destination, write=False)
            self.assertFalse(destination.exists())

    def test_non_atomic_lazy_output_is_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "ordinary.txt"
            handle, _ = self.invoke(destination, atomic=False)
            self.assertTrue(handle.closed)
            self.assertEqual(destination.read_text(), "replacement\n")


if __name__ == "__main__":
    run_tests()
