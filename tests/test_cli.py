"""Smoke tests for the CLI dispatch table."""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch, MagicMock

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

    def test_help_flag_h(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["-h"])
        self.assertEqual(rc, 0)
        self.assertIn(APP_NAME, buf.getvalue())

    def test_help_flag_long(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["--help"])
        self.assertEqual(rc, 0)

    def test_help_subcommand(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["help"])
        self.assertEqual(rc, 0)

    @patch("ai_brain.cli.print_results", return_value=0)
    @patch("ai_brain.cli.run_all_checks", return_value=[])
    def test_verify_all_pass_returns_zero(self, mock_rc, mock_pr) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["verify"])
        self.assertEqual(rc, 0)
        self.assertIn("全部通過", buf.getvalue())

    @patch("ai_brain.cli.print_results", return_value=2)
    @patch("ai_brain.cli.run_all_checks", return_value=[MagicMock()])
    def test_verify_failures_returns_one(self, mock_rc, mock_pr) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["verify"])
        self.assertEqual(rc, 1)
        self.assertIn("2", buf.getvalue())

    @patch("ai_brain.cli.commands.full_init", return_value=True)
    def test_full_init_dispatch_returns_zero(self, mock_fn) -> None:
        self.assertEqual(cli.main(["full-init"]), 0)

    @patch("ai_brain.cli.commands.full_init", return_value=False)
    def test_full_init_dispatch_returns_one_on_fail(self, mock_fn) -> None:
        self.assertEqual(cli.main(["full-init"]), 1)

    @patch("ai_brain.cli.commands.full_init")
    @patch("ai_brain.cli.commands.init_brain", return_value=True)
    def test_init_manual_calls_init_brain(self, mock_ib, mock_fi) -> None:
        self.assertEqual(cli.main(["init", "-m"]), 0)
        mock_ib.assert_called_once()
        mock_fi.assert_not_called()

    @patch("ai_brain.cli.commands.full_init")
    @patch("ai_brain.cli.commands.init_brain", return_value=True)
    def test_init_manual_long_calls_init_brain(self, mock_ib, mock_fi) -> None:
        self.assertEqual(cli.main(["init", "--manual"]), 0)
        mock_ib.assert_called_once()
        mock_fi.assert_not_called()

    @patch("ai_brain.cli.commands.full_init", return_value=True)
    @patch("ai_brain.cli.commands.init_brain")
    def test_init_default_calls_full_init(self, mock_ib, mock_fi) -> None:
        self.assertEqual(cli.main(["init"]), 0)
        mock_fi.assert_called_once()
        mock_ib.assert_not_called()

    @patch("ai_brain.cli.commands.uninstall_all", return_value=True)
    def test_uninstall_dispatch_returns_zero(self, mock_fn) -> None:
        self.assertEqual(cli.main(["uninstall"]), 0)

    @patch("ai_brain.cli.commands.uninstall_all", return_value=False)
    def test_uninstall_dispatch_returns_one_on_fail(self, mock_fn) -> None:
        self.assertEqual(cli.main(["uninstall"]), 1)

    @patch("ai_brain.cli.commands.manage_exclude", return_value=True)
    def test_exclude_dispatch_returns_zero(self, mock_fn) -> None:
        self.assertEqual(cli.main(["exclude"]), 0)

    @patch("ai_brain.cli.commands.manage_exclude", return_value=False)
    def test_exclude_dispatch_returns_one_on_fail(self, mock_fn) -> None:
        self.assertEqual(cli.main(["exclude"]), 1)

    @patch("ai_brain.cli.commands.manage_include", return_value=True)
    def test_include_dispatch_returns_zero(self, mock_fn) -> None:
        self.assertEqual(cli.main(["include"]), 0)

    @patch("ai_brain.cli.commands.manage_include", return_value=False)
    def test_include_dispatch_returns_one_on_fail(self, mock_fn) -> None:
        self.assertEqual(cli.main(["include"]), 1)

    @patch("ai_brain.cli.commands.manage_remove", return_value=True)
    def test_remove_dispatch_returns_zero(self, mock_fn) -> None:
        self.assertEqual(cli.main(["remove", "my-proj"]), 0)

    @patch("ai_brain.cli.commands.manage_remove", return_value=False)
    def test_remove_dispatch_returns_one_on_fail(self, mock_fn) -> None:
        self.assertEqual(cli.main(["remove", "my-proj"]), 1)

    @patch("ai_brain.cli.commands.run_doctor", return_value=True)
    def test_doctor_dispatch_returns_zero(self, mock_fn) -> None:
        self.assertEqual(cli.main(["doctor"]), 0)

    @patch("ai_brain.cli.commands.run_doctor", return_value=False)
    def test_doctor_dispatch_returns_one_on_fail(self, mock_fn) -> None:
        self.assertEqual(cli.main(["doctor"]), 1)

    @patch("ai_brain.completions.main", return_value=0)
    def test_completions_with_shell(self, mock_main) -> None:
        self.assertEqual(cli.main(["completions", "show", "bash"]), 0)
        mock_main.assert_called_once_with(["show", "bash"])

    @patch("ai_brain.completions.main", return_value=0)
    def test_completions_without_shell(self, mock_main) -> None:
        self.assertEqual(cli.main(["completions", "install"]), 0)
        mock_main.assert_called_once_with(["install"])

    @patch("ai_brain.cron.uninstall", return_value=True)
    def test_stop_cron_dispatch_returns_zero(self, mock_fn) -> None:
        self.assertEqual(cli.main(["stop-cron"]), 0)

    @patch("ai_brain.cron.uninstall", return_value=False)
    def test_stop_cron_dispatch_returns_one_on_fail(self, mock_fn) -> None:
        self.assertEqual(cli.main(["stop-cron"]), 1)
