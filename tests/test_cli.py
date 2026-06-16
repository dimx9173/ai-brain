"""Smoke tests for the CLI dispatch table."""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from ai_brain import cli
from ai_brain.constants import APP_NAME, VERSION


class TestCli(unittest.TestCase):
    def test_help_prints_version(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main([])
        self.assertEqual(rc, 0)
        self.assertIn(APP_NAME, buf.getvalue())
        self.assertIn(VERSION, buf.getvalue())

    def test_version_subcommand(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["version"])
        self.assertEqual(rc, 0)
        self.assertIn(VERSION, buf.getvalue())

    def test_unknown_subcommand_exits_nonzero(self) -> None:
        with self.assertRaises(SystemExit):
            cli.main(["bogus"])
