"""Unit tests for the generic MCP config checker and the rest of verifier."""
from __future__ import annotations

import json
import os
import stat
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_brain._testing import InTempDir
from ai_brain.constants import MCP_CODEBASE_MEMORY, MCP_MEMPALACE, MCP_REQUIRED_SERVERS
from ai_brain.verifier import (
    FAIL,
    INFO,
    PASS,
    WARN,
    CheckResult,
    check_cli_available,
    check_mcp_config,
    check_openclaw_daemon,
    print_results,
    run_all_checks,
)


def _write(cfg: Path, payload: dict) -> None:
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(payload))


# --------------------------------------------------------------------------- #
# check_mcp_config
# --------------------------------------------------------------------------- #


class TestCheckMcpConfigInfoPaths(unittest.TestCase):
    def test_returns_info_when_path_is_none(self) -> None:
        r = check_mcp_config("Test", None)
        self.assertEqual(r.status, INFO)

    def test_returns_info_when_file_missing(self) -> None:
        r = check_mcp_config("Test", Path("/nonexistent/cfg.json"))
        self.assertEqual(r.status, INFO)
        self.assertIn("未設定", r.detail)


class TestCheckMcpConfigMalformed(InTempDir):
    def test_returns_fail_when_malformed_json(self) -> None:
        cfg = Path(self.tmpdir) / "bad.json"
        cfg.write_text("{ broken")
        r = check_mcp_config("Test", cfg)
        self.assertEqual(r.status, FAIL)
        self.assertIn("JSON 格式損壞", r.detail)

    def test_returns_fail_when_malformed_toml(self) -> None:
        """parse_toml is very lenient and never raises, so the 'TOML 格式損壞'
        branch is essentially unreachable via normal content. Force it by
        mocking parse_toml to raise."""
        cfg = Path(self.tmpdir) / "bad.toml"
        cfg.write_text("[section]\nkey = val\n", encoding="utf-8")
        with patch(
            "ai_brain.config.parse_toml",
            side_effect=ValueError("bad toml"),
        ):
            r = check_mcp_config("Test", cfg, server_key="mcp_servers")
        self.assertEqual(r.status, FAIL)
        self.assertIn("TOML 格式損壞", r.detail)


class TestCheckMcpConfigMissingServer(InTempDir):
    def test_returns_fail_when_server_missing(self) -> None:
        cfg = Path(self.tmpdir) / "partial.json"
        _write(cfg, {"mcpServers": {MCP_MEMPALACE: {"command": "mempalace-mcp", "args": []}}})
        r = check_mcp_config("Test", cfg)
        self.assertEqual(r.status, FAIL)
        self.assertIn(MCP_CODEBASE_MEMORY, r.detail)

    def test_returns_fail_when_command_field_missing(self) -> None:
        cfg = Path(self.tmpdir) / "no_cmd.json"
        _write(cfg, {
            "mcpServers": {
                MCP_MEMPALACE: {"args": []},  # no command
                MCP_CODEBASE_MEMORY: {"command": "codebase-memory-mcp", "args": []},
            }
        })
        with patch("ai_brain.verifier.shutil.which", return_value="/bin/mock"):
            r = check_mcp_config("Test", cfg)
        self.assertEqual(r.status, FAIL)
        self.assertIn("command 缺失", r.detail)


class TestCheckMcpConfigCommandVariants(InTempDir):
    def test_command_as_list_cli_style(self) -> None:
        """Kilo etc. may encode `command: ["cmd", "arg1"]`; we should handle that."""
        cfg = Path(self.tmpdir) / "cli.json"
        _write(cfg, {
            "mcpServers": {
                MCP_MEMPALACE: {"command": ["mempalace-mcp"], "args": []},
                MCP_CODEBASE_MEMORY: {"command": "codebase-memory-mcp", "args": []},
            }
        })
        with patch("ai_brain.verifier.shutil.which", return_value="/bin/mock"):
            r = check_mcp_config("Test", cfg)
        self.assertEqual(r.status, PASS)

    def test_args_extracted_from_command_list_when_no_args_field(self) -> None:
        cfg = Path(self.tmpdir) / "nested.json"
        _write(cfg, {
            "mcpServers": {
                MCP_MEMPALACE: {"command": ["mempalace-mcp"]},
                MCP_CODEBASE_MEMORY: {"command": "codebase-memory-mcp"},
            }
        })
        with patch("ai_brain.verifier.shutil.which", return_value="/bin/mock"):
            r = check_mcp_config("Test", cfg)
        self.assertEqual(r.status, PASS)


class TestCheckMcpConfigCodebaseMemorySpecific(InTempDir):
    def test_wrong_command_for_codebase_memory_returns_fail(self) -> None:
        cfg = Path(self.tmpdir) / "wrong.json"
        _write(cfg, {
            "mcpServers": {
                MCP_MEMPALACE: {"command": "mempalace-mcp", "args": []},
                MCP_CODEBASE_MEMORY: {"command": "totally-wrong-cmd", "args": []},
            }
        })
        with patch("ai_brain.verifier.shutil.which", return_value="/bin/mock"):
            r = check_mcp_config("Test", cfg)
        self.assertEqual(r.status, FAIL)
        self.assertIn("codebase-memory-mcp", r.detail)

    def test_command_is_absolute_executable(self) -> None:
        cfg = Path(self.tmpdir) / "abs.json"
        exe_dir = Path(self.tmpdir) / "bin"
        exe_dir.mkdir()
        exe = exe_dir / "codebase-memory-mcp"
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        exe.chmod(0o755)
        _write(cfg, {
            "mcpServers": {
                MCP_MEMPALACE: {"command": "mempalace-mcp", "args": []},
                MCP_CODEBASE_MEMORY: {"command": str(exe), "args": []},
            }
        })

        def _which(cmd):
            if cmd == "mempalace-mcp":
                return "/bin/mock"
            return None

        with patch("ai_brain.verifier.shutil.which", side_effect=_which):
            r = check_mcp_config("Test", cfg)
        self.assertEqual(r.status, PASS)

    def test_binary_command_not_in_path_is_fail(self) -> None:
        cfg = Path(self.tmpdir) / "missing.json"
        _write(cfg, {
            "mcpServers": {
                MCP_MEMPALACE: {"command": "mempalace-mcp", "args": []},
                MCP_CODEBASE_MEMORY: {"command": "codebase-memory-mcp", "args": []},
            }
        })
        with patch("ai_brain.verifier.shutil.which", return_value=None):
            r = check_mcp_config("Test", cfg)
        self.assertEqual(r.status, FAIL)
        self.assertIn("無效或不可執行", r.detail)


class TestCheckMcpConfigToml(InTempDir):
    def test_returns_pass_for_well_formed_toml(self) -> None:
        from ai_brain.config import serialize_toml
        cfg = Path(self.tmpdir) / "good.toml"
        payload = {
            "mcp_servers": {
                MCP_MEMPALACE: {"command": "mempalace-mcp", "args": []},
                MCP_CODEBASE_MEMORY: {"command": "/usr/local/mock/codebase-memory-mcp", "args": []},
            }
        }
        cfg.write_text(serialize_toml(payload), encoding="utf-8")
        with patch("ai_brain.verifier.shutil.which", return_value="/usr/local/bin/mock"):
            r = check_mcp_config("Test", cfg, server_key="mcp_servers")
        self.assertEqual(r.status, PASS)


# --------------------------------------------------------------------------- #
# check_cli_available
# --------------------------------------------------------------------------- #


class TestCheckCliAvailable(InTempDir):
    def test_pass_when_found_on_path(self) -> None:
        with patch("ai_brain.verifier.shutil.which", return_value="/usr/local/bin/foo"):
            r = check_cli_available("Foo", "foo")
        self.assertEqual(r.status, PASS)

    def test_pass_via_fallback_path(self) -> None:
        exe = Path(self.tmpdir) / "fallback" / "mycli"
        exe.parent.mkdir()
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        exe.chmod(0o755)
        with patch("ai_brain.verifier.shutil.which", side_effect=[None, str(exe)]):
            r = check_cli_available("MyCLI", "mycli", fallback_paths=(exe,))
        self.assertEqual(r.status, PASS)

    def test_info_when_not_found_anywhere(self) -> None:
        with patch("ai_brain.verifier.shutil.which", return_value=None):
            r = check_cli_available("Ghost", "ghost-cli")
        self.assertEqual(r.status, INFO)

    def test_info_when_fallback_path_is_not_a_file(self) -> None:
        bogus = Path(self.tmpdir) / "does-not-exist"
        with patch("ai_brain.verifier.shutil.which", return_value=None):
            r = check_cli_available("Ghost", "ghost", fallback_paths=(bogus,))
        self.assertEqual(r.status, INFO)

    def test_custom_info_message_used(self) -> None:
        with patch("ai_brain.verifier.shutil.which", return_value=None):
            r = check_cli_available(
                "Claude-mem", "claude-mem",
                info_message="(未安裝，僅 Claude Code 需要)",
            )
        self.assertEqual(r.status, INFO)
        self.assertIn("僅 Claude Code", r.detail)

    def test_info_when_resolved_not_executable(self) -> None:
        exe = Path(self.tmpdir) / "fallback" / "mycli"
        exe.parent.mkdir(exist_ok=True)
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        with patch("ai_brain.verifier.shutil.which", return_value=None):
            r = check_cli_available("MyCLI", "mycli", fallback_paths=(exe,))
        self.assertEqual(r.status, INFO)


# --------------------------------------------------------------------------- #
# check_openclaw_daemon
# --------------------------------------------------------------------------- #


class TestCheckOpenClawDaemon(InTempDir):
    def test_info_when_not_installed(self) -> None:
        with patch("ai_brain.verifier.shutil.which", return_value=None):
            r = check_openclaw_daemon()
        self.assertEqual(r.status, INFO)

    def test_pass_when_running(self) -> None:
        mock_run = MagicMock()
        mock_run.stdout = "openclaw daemon running"
        mock_run.returncode = 0
        with patch("ai_brain.verifier.shutil.which", return_value="/usr/local/bin/openclaw"):
            with patch("ai_brain.verifier.subprocess.run", return_value=mock_run):
                r = check_openclaw_daemon()
        self.assertEqual(r.status, PASS)
        self.assertIn("已啟動", r.detail)

    def test_warn_when_installed_but_not_running(self) -> None:
        mock_run = MagicMock()
        mock_run.stdout = "openclaw daemon stopped\n"
        mock_run.returncode = 0
        with patch("ai_brain.verifier.shutil.which", return_value="/usr/local/bin/openclaw"):
            with patch("ai_brain.verifier.subprocess.run", return_value=mock_run):
                r = check_openclaw_daemon()
        self.assertEqual(r.status, WARN)
        self.assertIn("未啟動", r.detail)

    def test_warn_when_subprocess_fails(self) -> None:
        with patch("ai_brain.verifier.shutil.which", return_value="/usr/local/bin/openclaw"):
            with patch("ai_brain.verifier.subprocess.run", side_effect=OSError("boom")):
                r = check_openclaw_daemon()
        self.assertEqual(r.status, WARN)
        self.assertIn("無法偵測", r.detail)

    def test_found_via_nvm_fallback(self) -> None:
        # Create fake .nvm structure
        nvm_root = Path(self.tmpdir) / ".nvm" / "versions" / "node" / "v20.1.0" / "bin"
        nvm_root.mkdir(parents=True)
        oc = nvm_root / "openclaw"
        oc.write_text("#!/bin/sh\n", encoding="utf-8")
        oc.chmod(0o755)
        # shutil.which("openclaw") returns None for initial check;
        # then returns path after fallback discovery;
        # subprocess.run simulates daemon running output
        side_effects = [None, str(oc)]  # initial which(None), then which(str(oc))=oc

        def which_side_effect(cmd):
            if cmd == "openclaw":
                return None  # first which fails → falls back to nvm walk
            return str(oc)

        mock_run = MagicMock()
        mock_run.stdout = "openclaw daemon running"
        mock_run.returncode = 0

        with patch("ai_brain.verifier.shutil.which", side_effect=which_side_effect):
            with patch("ai_brain.verifier.subprocess.run", return_value=mock_run):
                r = check_openclaw_daemon()
        self.assertEqual(r.status, PASS)


# --------------------------------------------------------------------------- #
# print_results
# --------------------------------------------------------------------------- #


class TestPrintResults(unittest.TestCase):
    def test_counts_failures_only(self) -> None:
        results = [
            CheckResult("A", PASS),
            CheckResult("B", FAIL, "missing"),
            CheckResult("C", WARN),
            CheckResult("D", FAIL, "also missing"),
            CheckResult("E", INFO),
        ]
        # Suppress stdout
        import io
        from contextlib import redirect_stdout
        f = io.StringIO()
        with redirect_stdout(f):
            failures = print_results(results)
        self.assertEqual(failures, 2)
        output = f.getvalue()
        self.assertIn("A", output)
        self.assertIn("FAIL", output)
        self.assertIn("WARN", output)

    def test_handles_unknown_status_without_crash(self) -> None:
        results = [CheckResult("X", "SOMETHING_ELSE")]
        import io
        from contextlib import redirect_stdout
        f = io.StringIO()
        with redirect_stdout(f):
            failures = print_results(results)
        self.assertEqual(failures, 0)


# --------------------------------------------------------------------------- #
# run_all_checks
# --------------------------------------------------------------------------- #


class _FakeToolPaths:
    """A minimal stand-in for platforms.ToolPaths with all attributes pointing
    to tempdir paths that will either exist or not based on the test setup.
    """

    def __init__(self, base: Path):
        self.gemini_config = base / ".gemini" / "config" / "mcp_config.json"
        self.gemini_antigravity = base / ".gemini" / "antigravity" / "mcp_config.json"
        self.mcp_json = base / ".mcp.json"
        self.claude_json = base / ".claude.json"
        self.claude_desktop = base / "Claude" / "config.json"
        self.vscode_kilo = None
        self.kilo_cli = base / ".config" / "kilo" / "kilo.json"
        self.opencode_json = base / ".config" / "opencode" / "opencode.json"
        self.cursor_json = base / ".cursor" / "mcp.json"
        self.codex_toml = base / ".codex" / "config.toml"
        self.openclaw_config = base / ".openclaw" / "config.json"


class TestRunAllChecks(InTempDir):
    def test_runs_all_checks_and_returns_list(self) -> None:
        paths = _FakeToolPaths(Path(self.tmpdir))
        with patch("ai_brain.verifier.check_cli_available",
                   return_value=CheckResult("x", INFO)):
            with patch("ai_brain.verifier.check_mcp_config",
                       return_value=CheckResult("x", INFO)):
                with patch("ai_brain.verifier.check_openclaw_daemon",
                           return_value=CheckResult("x", INFO)):
                    with patch("ai_brain.verifier.shutil.which", return_value=None):
                        results = run_all_checks(paths)
        # 3 CLI + OpenClaw daemon + Claude + OpenCode + mcp.json + Gemini + Desktop + Kilo + Cursor + Codex + OpenClaw MCP = 13
        self.assertEqual(len(results), 13)

    def test_with_real_functions_and_no_configs_present(self) -> None:
        paths = _FakeToolPaths(Path(self.tmpdir))
        with patch("ai_brain.verifier.shutil.which", return_value=None):
            with patch("ai_brain.verifier.subprocess.run"):
                results = run_all_checks(paths)
        self.assertEqual(len(results), 13)
        # Most will be INFO since nothing is installed and no configs exist
        statuses = {r.status for r in results}
        self.assertTrue(statuses <= {INFO, FAIL, PASS, WARN})

    def test_openclaw_mcp_check_when_cli_exists(self) -> None:
        paths = _FakeToolPaths(Path(self.tmpdir))
        oc_config_dir = paths.openclaw_config.parent
        oc_config_dir.mkdir(parents=True, exist_ok=True)

        def which_side_effect(cmd, *args, **kwargs):
            if cmd == "openclaw":
                return "/usr/local/bin/openclaw"
            return None

        with patch("ai_brain.verifier.shutil.which", side_effect=which_side_effect):
            with patch("ai_brain.verifier.check_cli_available"):
                with patch("ai_brain.verifier.check_mcp_config"):
                    with patch("ai_brain.verifier.check_openclaw_daemon"):
                        with patch("ai_brain.verifier.subprocess.run"):
                            results = run_all_checks(paths)
        self.assertEqual(len(results), 13)

    def test_openclaw_mcp_check_when_cli_missing(self) -> None:
        paths = _FakeToolPaths(Path(self.tmpdir))
        with patch("ai_brain.verifier.shutil.which", return_value=None):
            with patch("ai_brain.verifier.check_cli_available"):
                with patch("ai_brain.verifier.check_mcp_config"):
                    with patch("ai_brain.verifier.check_openclaw_daemon"):
                        with patch("ai_brain.verifier.subprocess.run"):
                            results = run_all_checks(paths)
        self.assertEqual(len(results), 13)


# --------------------------------------------------------------------------- #
# Keep original style test for well-formed config compatibility
# --------------------------------------------------------------------------- #


class TestMcpConfigCheckerCompat(unittest.TestCase):
    @patch("ai_brain.verifier.shutil.which", return_value="/usr/local/bin/mock")
    def test_returns_pass_for_well_formed_config(self, mock_which) -> None:
        target = Path("/tmp/_ai_brain_test_good_compat.json")
        _write(target, {
            "mcpServers": {
                MCP_MEMPALACE: {"command": "mempalace-mcp", "args": []},
                MCP_CODEBASE_MEMORY: {"command": "/usr/local/bin/codebase-memory-mcp", "args": []},
            }
        })
        try:
            r = check_mcp_config("Test", target)
            self.assertEqual(r.status, PASS)
        finally:
            target.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
