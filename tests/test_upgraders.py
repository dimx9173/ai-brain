"""Comprehensive unit tests for the upgraders module."""
from __future__ import annotations

import importlib.metadata as _ilm
import subprocess
import unittest
from unittest import mock

from ai_brain._testing import InTempDir
from ai_brain.upgraders import (
    CORE_TOOLS,
    UpgradableTool,
    UpgradeOutcome,
    _VERSION_RE,
    _uv_tool_list,
    _version_from_metadata,
    _version_from_uv_list,
    get_version,
    print_summary,
    upgrade,
    upgrade_all,
)
import ai_brain.upgraders as _upg_mod


def _reset_uv_cache():
    _upg_mod._UV_TOOL_LIST_CACHE = None


# ---------- Version regex ----------

class TestVersionRegex(unittest.TestCase):
    def test_simple_version(self):
        m = _VERSION_RE.search("mempalace 3.4.1")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(), "3.4.1")

    def test_v_prefix(self):
        m = _VERSION_RE.search("v1.2.3")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(), "v1.2.3")

    def test_prerelease_suffix(self):
        m = _VERSION_RE.search("2.0.0-rc.1")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(), "2.0.0-rc.1")

    def test_two_component_version(self):
        m = _VERSION_RE.search("tool 1.2")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(), "1.2")

    def test_no_match_on_pure_text(self):
        m = _VERSION_RE.search("no version here")
        self.assertIsNone(m)

    def test_plus_build_metadata(self):
        m = _VERSION_RE.search("1.0.0+build.42")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(), "1.0.0+build.42")


# ---------- _version_from_metadata ----------

class TestVersionFromMetadata(unittest.TestCase):
    def test_direct_lookup(self):
        with mock.patch("ai_brain.upgraders.importlib_metadata.version", return_value="4.5.6"):
            result = _version_from_metadata(("mempalace",))
        self.assertEqual(result, "4.5.6")

    def test_normalised_lookup(self):
        # package name with dashes gets normalised to underscores
        def fake_version(name):
            if name == "claude_mem":
                return "1.0.0"
            raise _ilm.PackageNotFoundError(name)

        with mock.patch("ai_brain.upgraders.importlib_metadata.version", side_effect=fake_version):
            result = _version_from_metadata(("claude-mem",))
        self.assertEqual(result, "1.0.0")

    def test_strips_extras(self):
        def fake_version(name):
            if name == "mypackage":
                return "2.0.0"
            raise _ilm.PackageNotFoundError(name)

        with mock.patch("ai_brain.upgraders.importlib_metadata.version", side_effect=fake_version):
            result = _version_from_metadata(("mypackage[extra]",))
        self.assertEqual(result, "2.0.0")

    def test_returns_none_when_not_found(self):
        def fake_version(name):
            raise _ilm.PackageNotFoundError(name)

        fake_dist = mock.MagicMock()
        fake_dist.metadata = {"Name": "unrelated"}
        fake_dist.version = "0.0.1"

        with mock.patch("ai_brain.upgraders.importlib_metadata.version", side_effect=fake_version), \
             mock.patch("ai_brain.upgraders.importlib_metadata.distributions", return_value=[fake_dist]):
            result = _version_from_metadata(("nonexistent",))
        self.assertIsNone(result)

    def test_scan_fallback_matches_prefix(self):
        """When direct lookup fails, scan installed dists for prefix match."""
        def fake_version(name):
            raise _ilm.PackageNotFoundError(name)

        fake_dist = mock.MagicMock()
        fake_dist.metadata = {"Name": "mempalace-mcp"}
        fake_dist.version = "7.8.9"

        with mock.patch("ai_brain.upgraders.importlib_metadata.version", side_effect=fake_version), \
             mock.patch("ai_brain.upgraders.importlib_metadata.distributions", return_value=[fake_dist]):
            result = _version_from_metadata(("mempalace",))
        self.assertEqual(result, "7.8.9")

    def test_handles_generic_exception_in_metadata(self):
        def fake_version(name):
            raise RuntimeError("corrupted metadata")

        with mock.patch("ai_brain.upgraders.importlib_metadata.version", side_effect=fake_version), \
             mock.patch("ai_brain.upgraders.importlib_metadata.distributions", side_effect=Exception("fail")):
            result = _version_from_metadata(("broken",))
        self.assertIsNone(result)

    def test_empty_packages_tuple(self):
        # with an empty tuple the for-loop body never runs; returns None
        with mock.patch("ai_brain.upgraders.importlib_metadata.distributions", return_value=[]):
            result = _version_from_metadata(())
        self.assertIsNone(result)


# ---------- _uv_tool_list ----------

class TestUvToolList(unittest.TestCase):
    def setUp(self):
        _reset_uv_cache()

    def tearDown(self):
        _reset_uv_cache()

    def test_returns_none_when_uv_missing(self):
        with mock.patch("ai_brain.upgraders.shutil.which", return_value=None):
            result = _uv_tool_list()
        self.assertIsNone(result)

    def test_returns_none_on_nonzero_exit(self):
        fake = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="err")
        with mock.patch("ai_brain.upgraders.shutil.which", return_value="/bin/uv"), \
             mock.patch("ai_brain.upgraders.subprocess.run", return_value=fake):
            result = _uv_tool_list()
        self.assertIsNone(result)

    def test_returns_lines_on_success(self):
        fake = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="mempalace v3.4.1\n- mempalace\n", stderr="",
        )
        with mock.patch("ai_brain.upgraders.shutil.which", return_value="/bin/uv"), \
             mock.patch("ai_brain.upgraders.subprocess.run", return_value=fake):
            result = _uv_tool_list()
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], "mempalace v3.4.1")

    def test_returns_none_on_exception(self):
        with mock.patch("ai_brain.upgraders.shutil.which", return_value="/bin/uv"), \
             mock.patch("ai_brain.upgraders.subprocess.run", side_effect=OSError("spawn failed")):
            result = _uv_tool_list()
        self.assertIsNone(result)

    def test_caches_result(self):
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="x\n", stderr="")
        with mock.patch("ai_brain.upgraders.shutil.which", return_value="/bin/uv"), \
             mock.patch("ai_brain.upgraders.subprocess.run", return_value=fake) as run_mock:
            _uv_tool_list()
            _uv_tool_list()
        self.assertEqual(run_mock.call_count, 1)


# ---------- _version_from_uv_list ----------

class TestVersionFromUvList(unittest.TestCase):
    def setUp(self):
        _reset_uv_cache()

    def tearDown(self):
        _reset_uv_cache()

    def test_parses_version_by_package_name(self):
        lines = ["mempalace v3.4.1", "- mempalace", "- mempalace-mcp"]
        with mock.patch("ai_brain.upgraders._uv_tool_list", return_value=lines):
            result = _version_from_uv_list("mempalace", "mempalace")
        self.assertEqual(result, "v3.4.1")

    def test_parses_version_by_binary_name(self):
        lines = ["graphifyy v1.0.0", "- graphify"]
        with mock.patch("ai_brain.upgraders._uv_tool_list", return_value=lines):
            result = _version_from_uv_list("graphify", "graphifyy")
        self.assertEqual(result, "v1.0.0")

    def test_returns_none_when_not_in_list(self):
        lines = ["other-tool v1.0.0"]
        with mock.patch("ai_brain.upgraders._uv_tool_list", return_value=lines):
            result = _version_from_uv_list("mempalace", "mempalace")
        self.assertIsNone(result)

    def test_returns_none_when_uv_list_empty(self):
        with mock.patch("ai_brain.upgraders._uv_tool_list", return_value=None):
            result = _version_from_uv_list("mempalace", "mempalace")
        self.assertIsNone(result)

    def test_skips_bullet_lines(self):
        lines = ["mempalace v3.4.1", "- mempalace"]
        with mock.patch("ai_brain.upgraders._uv_tool_list", return_value=lines):
            result = _version_from_uv_list("mempalace", "mempalace")
        self.assertEqual(result, "v3.4.1")

    def test_skips_short_lines(self):
        lines = ["justname", "mempalace v3.4.1"]
        with mock.patch("ai_brain.upgraders._uv_tool_list", return_value=lines):
            result = _version_from_uv_list("mempalace", "mempalace")
        self.assertEqual(result, "v3.4.1")

    def test_handles_extras_in_package_name(self):
        lines = ["mypackage v2.0.0"]
        with mock.patch("ai_brain.upgraders._uv_tool_list", return_value=lines):
            result = _version_from_uv_list("mybin", "mypackage[extra]")
        self.assertEqual(result, "v2.0.0")


# ---------- get_version ----------

class TestGetVersion(InTempDir):
    def test_binary_not_found(self):
        with mock.patch("ai_brain.upgraders.shutil.which", return_value=None):
            self.assertEqual(get_version("ghost"), "(not installed)")

    def test_cli_version_stdout(self):
        fake = subprocess.CompletedProcess(args=[], returncode=0,
                                           stdout="tool 1.2.3\n", stderr="")
        with mock.patch("ai_brain.upgraders.shutil.which", return_value="/bin/tool"), \
             mock.patch("ai_brain.upgraders.subprocess.run", return_value=fake):
            v = get_version("tool")
        self.assertEqual(v, "1.2.3")

    def test_cli_version_stderr(self):
        fake = subprocess.CompletedProcess(args=[], returncode=0,
                                           stdout="", stderr="tool v0.9.1\n")
        with mock.patch("ai_brain.upgraders.shutil.which", return_value="/bin/tool"), \
             mock.patch("ai_brain.upgraders.subprocess.run", return_value=fake):
            v = get_version("tool")
        self.assertEqual(v, "v0.9.1")

    def test_fallback_to_version_subcommand(self):
        empty = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        ok = subprocess.CompletedProcess(args=[], returncode=0,
                                         stdout="mempalace v0.4.1\n", stderr="")
        with mock.patch("ai_brain.upgraders.shutil.which", return_value="/bin/mp"), \
             mock.patch("ai_brain.upgraders.subprocess.run", side_effect=[empty, ok]):
            v = get_version("mempalace")
        self.assertEqual(v, "v0.4.1")

    def test_returns_first_line_when_no_version_match(self):
        noisy = subprocess.CompletedProcess(args=[], returncode=0,
                                            stdout="no version here\n", stderr="")
        with mock.patch("ai_brain.upgraders.shutil.which", return_value="/bin/w"), \
             mock.patch("ai_brain.upgraders.subprocess.run", return_value=noisy):
            v = get_version("whatever")
        self.assertEqual(v, "no version here")

    def test_skips_usage_output(self):
        """If both --version and version produce 'usage:', fall through to metadata."""
        usage = subprocess.CompletedProcess(args=[], returncode=0,
                                            stdout="usage: tool [opts]\n", stderr="")
        with mock.patch("ai_brain.upgraders.shutil.which", return_value="/bin/t"), \
             mock.patch("ai_brain.upgraders.subprocess.run", return_value=usage), \
             mock.patch("ai_brain.upgraders._version_from_metadata", return_value="3.2.1"):
            v = get_version("tool", package="tool")
        self.assertEqual(v, "3.2.1")

    def test_skips_error_output(self):
        error = subprocess.CompletedProcess(args=[], returncode=0,
                                            stdout="Error: unknown flag\n", stderr="")
        with mock.patch("ai_brain.upgraders.shutil.which", return_value="/bin/t"), \
             mock.patch("ai_brain.upgraders.subprocess.run", return_value=error), \
             mock.patch("ai_brain.upgraders._version_from_metadata", return_value=None), \
             mock.patch("ai_brain.upgraders._version_from_uv_list", return_value=None):
            v = get_version("tool", package="tool")
        self.assertEqual(v, "(unknown)")

    def test_falls_through_to_metadata(self):
        """When CLI returns nothing useful, use importlib.metadata."""
        empty = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with mock.patch("ai_brain.upgraders.shutil.which", return_value="/bin/t"), \
             mock.patch("ai_brain.upgraders.subprocess.run", return_value=empty), \
             mock.patch("ai_brain.upgraders._version_from_metadata", return_value="5.5.5"):
            v = get_version("tool", package="toolpkg")
        self.assertEqual(v, "5.5.5")

    def test_falls_through_to_uv_list(self):
        empty = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with mock.patch("ai_brain.upgraders.shutil.which", return_value="/bin/t"), \
             mock.patch("ai_brain.upgraders.subprocess.run", return_value=empty), \
             mock.patch("ai_brain.upgraders._version_from_metadata", return_value=None), \
             mock.patch("ai_brain.upgraders._version_from_uv_list", return_value="6.6.6"):
            v = get_version("tool", package="toolpkg")
        self.assertEqual(v, "6.6.6")

    def test_returns_unknown_when_everything_fails(self):
        empty = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with mock.patch("ai_brain.upgraders.shutil.which", return_value="/bin/t"), \
             mock.patch("ai_brain.upgraders.subprocess.run", return_value=empty), \
             mock.patch("ai_brain.upgraders._version_from_metadata", return_value=None), \
             mock.patch("ai_brain.upgraders._version_from_uv_list", return_value=None):
            v = get_version("tool", package="toolpkg")
        self.assertEqual(v, "(unknown)")

    def test_returns_unknown_no_package(self):
        empty = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with mock.patch("ai_brain.upgraders.shutil.which", return_value="/bin/t"), \
             mock.patch("ai_brain.upgraders.subprocess.run", return_value=empty), \
             mock.patch("ai_brain.upgraders._version_from_metadata", return_value=None):
            v = get_version("tool")  # no package kwarg
        self.assertEqual(v, "(unknown)")

    def test_timeout_on_cli(self):
        """subprocess.TimeoutExpired on --version should fall through to 'version'."""
        ok = subprocess.CompletedProcess(args=[], returncode=0,
                                         stdout="t v1.0.0\n", stderr="")
        with mock.patch("ai_brain.upgraders.shutil.which", return_value="/bin/t"), \
             mock.patch("ai_brain.upgraders.subprocess.run",
                        side_effect=[subprocess.TimeoutExpired("t", 10), ok]):
            v = get_version("t")
        self.assertEqual(v, "v1.0.0")

    def test_oserror_on_cli(self):
        ok = subprocess.CompletedProcess(args=[], returncode=0,
                                         stdout="t v2.0.0\n", stderr="")
        with mock.patch("ai_brain.upgraders.shutil.which", return_value="/bin/t"), \
             mock.patch("ai_brain.upgraders.subprocess.run",
                        side_effect=[OSError("fail"), ok]):
            v = get_version("t")
        self.assertEqual(v, "v2.0.0")


# ---------- upgrade ----------

class TestUpgrade(unittest.TestCase):
    def test_uv_missing(self):
        with mock.patch("ai_brain.upgraders.shutil.which", return_value=None):
            ok, msg = upgrade(UpgradableTool("X", "x", "x"))
        self.assertFalse(ok)
        self.assertIn("uv not on PATH", msg)

    def test_success(self):
        success = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")
        with mock.patch("ai_brain.upgraders.shutil.which", return_value="/bin/uv"), \
             mock.patch("ai_brain.upgraders.subprocess.run", return_value=success), \
             mock.patch("ai_brain.upgraders.get_version", return_value="1.2.3"):
            ok, msg = upgrade(UpgradableTool("X", "x", "x"))
        self.assertTrue(ok)
        self.assertIn("1.2.3", msg)

    def test_nonzero_exit(self):
        fail = subprocess.CompletedProcess(args=[], returncode=1,
                                           stdout="", stderr="boom: bad things")
        with mock.patch("ai_brain.upgraders.shutil.which", return_value="/bin/uv"), \
             mock.patch("ai_brain.upgraders.subprocess.run", return_value=fail):
            ok, msg = upgrade(UpgradableTool("X", "x", "x"))
        self.assertFalse(ok)
        self.assertIn("boom", msg)

    def test_timeout(self):
        with mock.patch("ai_brain.upgraders.shutil.which", return_value="/bin/uv"), \
             mock.patch("ai_brain.upgraders.subprocess.run",
                        side_effect=subprocess.TimeoutExpired("uv", 300)):
            ok, msg = upgrade(UpgradableTool("X", "x", "x"))
        self.assertFalse(ok)
        self.assertIn("timed out", msg)

    def test_spawn_exception(self):
        with mock.patch("ai_brain.upgraders.shutil.which", return_value="/bin/uv"), \
             mock.patch("ai_brain.upgraders.subprocess.run", side_effect=OSError("fork failed")):
            ok, msg = upgrade(UpgradableTool("X", "x", "x"))
        self.assertFalse(ok)
        self.assertIn("failed to spawn", msg)

    def test_stderr_only_error(self):
        fail = subprocess.CompletedProcess(args=[], returncode=2,
                                           stdout="", stderr="only stderr error")
        with mock.patch("ai_brain.upgraders.shutil.which", return_value="/bin/uv"), \
             mock.patch("ai_brain.upgraders.subprocess.run", return_value=fail):
            ok, msg = upgrade(UpgradableTool("X", "x", "x"))
        self.assertFalse(ok)
        self.assertIn("only stderr error", msg)

    def test_stdout_fallback_when_no_stderr(self):
        fail = subprocess.CompletedProcess(args=[], returncode=2,
                                           stdout="stdout error line", stderr="")
        with mock.patch("ai_brain.upgraders.shutil.which", return_value="/bin/uv"), \
             mock.patch("ai_brain.upgraders.subprocess.run", return_value=fail):
            ok, msg = upgrade(UpgradableTool("X", "x", "x"))
        self.assertFalse(ok)
        self.assertIn("stdout error line", msg)

    def test_empty_error_output(self):
        fail = subprocess.CompletedProcess(args=[], returncode=3,
                                           stdout="", stderr="")
        with mock.patch("ai_brain.upgraders.shutil.which", return_value="/bin/uv"), \
             mock.patch("ai_brain.upgraders.subprocess.run", return_value=fail):
            ok, msg = upgrade(UpgradableTool("X", "x", "x"))
        self.assertFalse(ok)
        self.assertIn("exit code", msg)


# ---------- upgrade_all ----------

class TestUpgradeAll(unittest.TestCase):
    def test_one_outcome_per_tool(self):
        success = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")
        with mock.patch("ai_brain.upgraders.shutil.which", side_effect=lambda b: f"/bin/{b}"), \
             mock.patch("ai_brain.upgraders.subprocess.run", return_value=success), \
             mock.patch("ai_brain.upgraders.get_version", return_value="9.9.9"):
            outcomes = upgrade_all()
        self.assertEqual(len(outcomes), len(CORE_TOOLS))
        for o in outcomes:
            self.assertIsInstance(o, UpgradeOutcome)
            self.assertTrue(o.upgraded)
            self.assertEqual(o.version_after, "9.9.9")

    def test_continues_on_failure(self):
        fail = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="err")
        success = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")

        def side_effect(cmd, **kw):
            if cmd[0] == "uv" and len(cmd) > 2 and cmd[2] == "install":
                if cmd[3] == "mempalace":
                    return fail
            return success

        with mock.patch("ai_brain.upgraders.shutil.which", side_effect=lambda b: f"/bin/{b}"), \
             mock.patch("ai_brain.upgraders.subprocess.run", side_effect=side_effect), \
             mock.patch("ai_brain.upgraders.get_version", return_value="1.0.0"):
            outcomes = upgrade_all()
        self.assertEqual(len(outcomes), len(CORE_TOOLS))
        failed = [o for o in outcomes if not o.upgraded]
        self.assertTrue(len(failed) >= 1)

    def test_binary_not_installed_still_attempts_upgrade(self):
        """Even when the binary is missing, uv can install it fresh."""
        success = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")
        which_sides = {"uv": "/bin/uv", "mempalace": None,
                       "claude-mem": "/bin/claude-mem",
                       "codebase-memory-mcp": None}
        with mock.patch("ai_brain.upgraders.shutil.which",
                        side_effect=lambda b: which_sides.get(b)), \
             mock.patch("ai_brain.upgraders.subprocess.run", return_value=success), \
             mock.patch("ai_brain.upgraders.get_version", return_value="0.1.0"):
            outcomes = upgrade_all()
        # All three tools should be attempted.
        self.assertEqual(len(outcomes), 3)


# ---------- print_summary ----------

class TestPrintSummary(unittest.TestCase):
    def test_prints_table_without_error(self):
        outcomes = [
            UpgradeOutcome(tool=CORE_TOOLS[0], upgraded=True,
                           message="ok", version_after="1.0"),
            UpgradeOutcome(tool=CORE_TOOLS[1], upgraded=False,
                           message="network error", version_after="(unknown)"),
        ]
        print_summary(outcomes, self_version="1.2.0")

    def test_empty_outcomes(self):
        print_summary([], self_version="0.0.1")

    def test_all_success(self):
        outcomes = [
            UpgradeOutcome(tool=t, upgraded=True, message="ok", version_after="1.0")
            for t in CORE_TOOLS
        ]
        print_summary(outcomes, self_version="1.0.0")

    def test_all_failure(self):
        outcomes = [
            UpgradeOutcome(tool=t, upgraded=False, message="fail", version_after="(unknown)")
            for t in CORE_TOOLS
        ]
        print_summary(outcomes, self_version="1.0.0")


# ---------- dataclass sanity ----------

class TestDataclasses(unittest.TestCase):
    def test_upgradable_tool_frozen(self):
        t = UpgradableTool("X", "x", "x")
        self.assertEqual(t.label, "X")
        with self.assertRaises(AttributeError):
            t.label = "Y"

    def test_upgrade_outcome_mutable(self):
        t = UpgradableTool("X", "x", "x")
        o = UpgradeOutcome(tool=t, upgraded=True, message="ok", version_after="1.0")
        o.message = "changed"
        self.assertEqual(o.message, "changed")


if __name__ == "__main__":
    unittest.main()