"""Validate CLI path constraints and their permissive negative controls."""

from pathlib import Path
import tempfile
import unittest

from common import checkout, run_tests

checkout("click", "src")
import click
from click.testing import CliRunner


class FileOnlyPaths(unittest.TestCase):
    def invoke(self, value, *, dir_okay=False, exists=True):
        callbacks = []

        @click.command()
        @click.argument("input", type=click.Path(exists=exists, file_okay=True, dir_okay=dir_okay))
        def command(input):
            callbacks.append(input)

        result = CliRunner().invoke(command, [str(value)])
        return result, callbacks

    def test_existing_directory_is_rejected_before_callback(self):
        with tempfile.TemporaryDirectory() as directory:
            result, callbacks = self.invoke(Path(directory))
            self.assertEqual(result.exit_code, 2)
            self.assertIn("is a directory", result.output)
            self.assertEqual(callbacks, [])

    def test_existing_file_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            value = Path(directory) / "input.txt"
            value.write_text("fixture")
            result, callbacks = self.invoke(value)
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(callbacks, [str(value)])

    def test_explicitly_allowed_directory_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            result, callbacks = self.invoke(Path(directory), dir_okay=True)
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(callbacks, [directory])

    def test_optional_missing_path_remains_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            value = Path(directory) / "missing.txt"
            result, callbacks = self.invoke(value, exists=False)
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(callbacks, [str(value)])


if __name__ == "__main__":
    run_tests()
