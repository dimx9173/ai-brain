"""Unit tests for cron.py."""
from __future__ import annotations

import unittest
from io import StringIO
from contextlib import redirect_stdout
from unittest.mock import patch, MagicMock, call

from ai_brain._testing import InTempDir
from ai_brain import cron
from ai_brain.cron import CRON_MARKER, CRON_LINE


# --- _read_crontab -------------------------------------------------------------

class TestReadCrontab(InTempDir):
    def setUp(self) -> None:
        super().setUp()

    @patch("ai_brain.cron.subprocess.run")
    @patch("ai_brain.cron.shutil.which", return_value=None)
    def test_returns_empty_when_crontab_missing(self, mock_which, mock_run):
        result = cron._read_crontab()
        self.assertEqual(result, [])
        mock_run.assert_not_called()

    @patch("ai_brain.cron.subprocess.run")
    @patch("ai_brain.cron.shutil.which", return_value="/usr/bin/crontab")
    def test_returns_lines_on_success(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="line one\nline two\n")
        result = cron._read_crontab()
        self.assertEqual(result, ["line one", "line two"])

    @patch("ai_brain.cron.subprocess.run")
    @patch("ai_brain.cron.shutil.which", return_value="/usr/bin/crontab")
    def test_returns_empty_when_crontab_l_fails(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = cron._read_crontab()
        self.assertEqual(result, [])

    @patch("ai_brain.cron.subprocess.run", side_effect=FileNotFoundError)
    @patch("ai_brain.cron.shutil.which", return_value="/usr/bin/crontab")
    def test_returns_empty_on_file_not_found_error(self, mock_which, mock_run):
        result = cron._read_crontab()
        self.assertEqual(result, [])

    @patch("ai_brain.cron.subprocess.run")
    @patch("ai_brain.cron.shutil.which", return_value="/usr/bin/crontab")
    def test_empty_crontab_returns_empty_list(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        result = cron._read_crontab()
        self.assertEqual(result, [])


# --- _write_crontab ------------------------------------------------------------

class TestWriteCrontab(InTempDir):
    @patch("ai_brain.cron.subprocess.Popen")
    def test_write_crontab_success(self, mock_popen):
        p = MagicMock()
        p.communicate.return_value = (None, None)
        mock_popen.return_value = p
        result = cron._write_crontab(["line1", "line2"])
        self.assertTrue(result)
        mock_popen.assert_called_once_with(
            ["crontab", "-"], stdin=cron.subprocess.PIPE, text=True
        )
        p.communicate.assert_called_once_with(input="line1\nline2\n")

    @patch("ai_brain.cron.subprocess.Popen", side_effect=OSError("boom"))
    def test_write_crontab_exception_returns_false(self, mock_popen):
        buf = StringIO()
        with redirect_stdout(buf):
            result = cron._write_crontab(["line1"])
        self.assertFalse(result)
        self.assertIn("失敗", buf.getvalue())

    @patch("ai_brain.cron.subprocess.Popen")
    def test_write_crontab_empty_list(self, mock_popen):
        p = MagicMock()
        p.communicate.return_value = (None, None)
        mock_popen.return_value = p
        result = cron._write_crontab([])
        self.assertTrue(result)
        p.communicate.assert_called_once_with(input="\n")


# --- install() -----------------------------------------------------------------

class TestCronInstall(InTempDir):
    @patch("ai_brain.cron._write_crontab")
    @patch("ai_brain.cron._read_crontab")
    @patch("ai_brain.cron.shutil.which", return_value=None)
    def test_install_returns_false_when_no_crontab(self, mock_which, mock_read, mock_write):
        buf = StringIO()
        with redirect_stdout(buf):
            result = cron.install()
        self.assertFalse(result)
        mock_read.assert_not_called()
        mock_write.assert_not_called()
        self.assertIn("跳過", buf.getvalue())

    @patch("ai_brain.cron._write_crontab")
    @patch("ai_brain.cron._read_crontab", return_value=["some existing line"])
    @patch("ai_brain.cron.shutil.which", return_value="/usr/bin/crontab")
    def test_install_adds_cron_line_when_not_present(self, mock_which, mock_read, mock_write):
        mock_write.return_value = True
        buf = StringIO()
        with redirect_stdout(buf):
            result = cron.install()
        self.assertTrue(result)
        mock_write.assert_called_once_with(["some existing line", CRON_LINE])
        self.assertIn("註冊成功", buf.getvalue())

    @patch("ai_brain.cron._write_crontab")
    @patch("ai_brain.cron._read_crontab", return_value=[CRON_LINE])
    @patch("ai_brain.cron.shutil.which", return_value="/usr/bin/crontab")
    def test_install_is_idempotent(self, mock_which, mock_read, mock_write):
        buf = StringIO()
        with redirect_stdout(buf):
            result = cron.install()
        self.assertTrue(result)
        mock_write.assert_not_called()
        self.assertIn("已經配置", buf.getvalue())

    @patch("ai_brain.cron._write_crontab")
    @patch("ai_brain.cron._read_crontab", return_value=["user cron line"])
    @patch("ai_brain.cron.shutil.which", return_value="/usr/bin/crontab")
    def test_install_returns_false_when_write_fails(self, mock_which, mock_read, mock_write):
        mock_write.return_value = False
        result = cron.install()
        self.assertFalse(result)

    @patch("ai_brain.cron._write_crontab")
    @patch("ai_brain.cron._read_crontab", return_value=["# marker with ai-brain stop inside"])
    @patch("ai_brain.cron.shutil.which", return_value="/usr/bin/crontab")
    def test_install_detects_marker_anywhere_in_line(self, mock_which, mock_read, mock_write):
        buf = StringIO()
        with redirect_stdout(buf):
            result = cron.install()
        self.assertTrue(result)
        mock_write.assert_not_called()

    @patch("ai_brain.cron._write_crontab")
    @patch("ai_brain.cron._read_crontab", return_value=[])
    @patch("ai_brain.cron.shutil.which", return_value="/usr/bin/crontab")
    def test_install_to_empty_crontab(self, mock_which, mock_read, mock_write):
        mock_write.return_value = True
        result = cron.install()
        self.assertTrue(result)
        mock_write.assert_called_once_with([CRON_LINE])


# --- uninstall() ---------------------------------------------------------------

class TestCronUninstall(InTempDir):
    @patch("ai_brain.cron._write_crontab")
    @patch("ai_brain.cron._read_crontab")
    @patch("ai_brain.cron.shutil.which", return_value=None)
    def test_uninstall_returns_false_when_no_crontab(self, mock_which, mock_read, mock_write):
        buf = StringIO()
        with redirect_stdout(buf):
            result = cron.uninstall()
        self.assertFalse(result)
        mock_read.assert_not_called()

    @patch("ai_brain.cron._write_crontab")
    @patch("ai_brain.cron._read_crontab", return_value=["other line"])
    @patch("ai_brain.cron.shutil.which", return_value="/usr/bin/crontab")
    def test_uninstall_nothing_to_remove(self, mock_which, mock_read, mock_write):
        buf = StringIO()
        with redirect_stdout(buf):
            result = cron.uninstall()
        self.assertTrue(result)
        mock_write.assert_not_called()
        self.assertIn("無須移除", buf.getvalue())

    @patch("ai_brain.cron._write_crontab")
    @patch("ai_brain.cron._read_crontab", return_value=["keep this", CRON_LINE, "also keep"])
    @patch("ai_brain.cron.shutil.which", return_value="/usr/bin/crontab")
    def test_uninstall_removes_marker_lines_keeps_rest(self, mock_which, mock_read, mock_write):
        mock_write.return_value = True
        buf = StringIO()
        with redirect_stdout(buf):
            result = cron.uninstall()
        self.assertTrue(result)
        mock_write.assert_called_once_with(["keep this", "also keep"])
        self.assertIn("已成功移除", buf.getvalue())

    @patch("ai_brain.cron._write_crontab")
    @patch("ai_brain.cron._read_crontab", return_value=[CRON_LINE])
    @patch("ai_brain.cron.shutil.which", return_value="/usr/bin/crontab")
    def test_uninstall_removes_all_marker_lines(self, mock_which, mock_read, mock_write):
        mock_write.return_value = True
        result = cron.uninstall()
        self.assertTrue(result)
        mock_write.assert_called_once_with([])

    @patch("ai_brain.cron._write_crontab")
    @patch("ai_brain.cron._read_crontab", return_value=[CRON_LINE])
    @patch("ai_brain.cron.shutil.which", return_value="/usr/bin/crontab")
    def test_uninstall_returns_false_when_write_fails(self, mock_which, mock_read, mock_write):
        mock_write.return_value = False
        buf = StringIO()
        with redirect_stdout(buf):
            result = cron.uninstall()
        self.assertFalse(result)
        self.assertIn("移除", buf.getvalue())
        self.assertIn("失敗", buf.getvalue())


# --- Constants sanity -----------------------------------------------------------

class TestCronConstants(InTempDir):
    def test_cron_marker_in_cron_line(self):
        self.assertIn(CRON_MARKER, CRON_LINE)

    def test_cron_line_format(self):
        self.assertTrue(CRON_LINE.startswith("30 23 * * *"))
        self.assertIn("ai-brain stop", CRON_LINE)


if __name__ == "__main__":
    unittest.main()
