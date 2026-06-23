"""Unit tests for the upgraders module.

We mock out `subprocess.run` and `shutil.which` so the tests stay fast and
side-effect free — the real `uv tool install` is exercised manually via
`ai-brain update`.
"""
from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from ai_brain.upgraders import (
    CORE_TOOLS,
    UpgradableTool,
    UpgradeOutcome,
    get_version,
    print_summary,
    upgrade,
    upgrade_all,
)


class TestCoreToolsRegistry(unittest.TestCase):
    def test_core_tools_cover_the_three_documented_packages(self) -> None:
        packages = {t.package for t in CORE_TOOLS}
        self.assertEqual(packages, {"mempalace", "claude-mem", "graphifyy[mcp]"})

    def test_binaries_are_path_safe(self) -> None:
        for tool in CORE_TOOLS:
            self.assertTrue(tool.binary)
            self.assertTrue(tool.package)
            # binary must not be the same as the package name with double-y
            # trick — graphifyy installs `graphify`, etc. Just check that
            # at least one of them is non-empty.
            self.assertNotEqual(tool.binary, "")

    def test_no_duplicate_binaries(self) -> None:
        bins = [t.binary for t in CORE_TOOLS]
        self.assertEqual(len(bins), len(set(bins)))


class TestGetVersion(unittest.TestCase):
    def test_extracts_version_from_dash_dash_version(self) -> None:
        fake = subprocess.CompletedProcess(args=[], returncode=0,
                                            stdout="graphify 1.2.3\n", stderr="")
        with mock.patch("shutil.which", return_value="/usr/bin/graphify"), \
             mock.patch("subprocess.run", return_value=fake) as run:
            v = get_version("graphify")
        self.assertEqual(v, "1.2.3")
        # First call should have used --version
        self.assertEqual(run.call_args.args[0], ["graphify", "--version"])

    def test_falls_back_to_version_subcommand(self) -> None:
        empty = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        ok = subprocess.CompletedProcess(args=[], returncode=0,
                                          stdout="mempalace v0.4.1\n", stderr="")
        with mock.patch("shutil.which", return_value="/usr/bin/mempalace"), \
             mock.patch("subprocess.run", side_effect=[empty, ok]) as run:
            v = get_version("mempalace")
        # The regex keeps the leading "v" because that's exactly what the
        # tool prints — `v0.4.1` is a more truthful rendering than `0.4.1`.
        self.assertEqual(v, "v0.4.1")
        # Second attempt should drop the --version form.
        self.assertEqual(run.call_args_list[1].args[0], ["mempalace", "version"])

    def test_returns_not_installed_when_binary_missing(self) -> None:
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(get_version("ghost"), "(not installed)")

    def test_returns_unknown_when_no_version_token(self) -> None:
        noisy = subprocess.CompletedProcess(args=[], returncode=0,
                                             stdout="no version here\n", stderr="")
        with mock.patch("shutil.which", return_value="/bin/whatever"), \
             mock.patch("subprocess.run", return_value=noisy):
            v = get_version("whatever")
        # We return the first line as-is when no version regex matches.
        self.assertEqual(v, "no version here")


class TestUpgrade(unittest.TestCase):
    def test_returns_failure_when_uv_not_on_path(self) -> None:
        with mock.patch("shutil.which", return_value=None):
            ok, msg = upgrade(UpgradableTool("X", "x", "x"))
        self.assertFalse(ok)
        self.assertIn("uv not on PATH", msg)

    def test_returns_success_on_zero_exit(self) -> None:
        success = subprocess.CompletedProcess(args=[], returncode=0,
                                              stdout="Installed", stderr="")
        with mock.patch("shutil.which", return_value="/usr/bin/uv"), \
             mock.patch("subprocess.run", return_value=success), \
             mock.patch("ai_brain.upgraders.get_version", return_value="1.2.3"):
            ok, msg = upgrade(UpgradableTool("X", "x", "x"))
        self.assertTrue(ok)
        self.assertIn("1.2.3", msg)

    def test_returns_failure_on_nonzero_exit(self) -> None:
        failure = subprocess.CompletedProcess(args=[], returncode=1,
                                              stdout="", stderr="boom: bad things")
        with mock.patch("shutil.which", return_value="/usr/bin/uv"), \
             mock.patch("subprocess.run", return_value=failure):
            ok, msg = upgrade(UpgradableTool("X", "x", "x"))
        self.assertFalse(ok)
        # Should surface the last line of stderr.
        self.assertIn("boom", msg)


class TestUpgradeAll(unittest.TestCase):
    def test_collects_one_outcome_per_tool(self) -> None:
        success = subprocess.CompletedProcess(args=[], returncode=0,
                                              stdout="ok", stderr="")
        with mock.patch("shutil.which", side_effect=lambda b: f"/bin/{b}"), \
             mock.patch("subprocess.run", return_value=success), \
             mock.patch("ai_brain.upgraders.get_version", return_value="9.9.9"):
            outcomes = upgrade_all()
        self.assertEqual(len(outcomes), len(CORE_TOOLS))
        for o in outcomes:
            self.assertIsInstance(o, UpgradeOutcome)
            self.assertTrue(o.upgraded)
            self.assertEqual(o.version_after, "9.9.9")


class TestPrintSummary(unittest.TestCase):
    def test_print_summary_renders_table(self) -> None:
        outcomes = [
            UpgradeOutcome(tool=CORE_TOOLS[0], upgraded=True,
                            message="upgraded to 1.0", version_after="1.0"),
            UpgradeOutcome(tool=CORE_TOOLS[1], upgraded=False,
                            message="network error", version_after="(unknown)"),
        ]
        # Just make sure it doesn't raise; we don't capture stdout in detail
        # because it depends on TTY (colors).
        print_summary(outcomes, self_version="1.2.0")


if __name__ == "__main__":
    unittest.main()
