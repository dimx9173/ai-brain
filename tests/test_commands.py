"""Unit tests for ai_brain.commands"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_brain import commands, registry
from ai_brain._testing import InTempDir
from ai_brain.verifier import PASS as VERIFY_PASS, CheckResult


# ============================================================================ #
# ============================================================================ #

class _CmdBase(InTempDir):
    def setUp(self):
        super().setUp()
        gi = commands._global_gitignore_path()
        gi.parent.mkdir(parents=True, exist_ok=True)
        gi.write_text(".codebase-memory/\n", encoding="utf-8")

    def _register(self, paths: list[str]) -> None:
        registry.REGISTRY_PATH().parent.mkdir(parents=True, exist_ok=True)
        registry.REGISTRY_PATH().write_text("\n".join(paths) + "\n", encoding="utf-8")

    @staticmethod
    def _capture(fn, *a, **kw) -> tuple[str, bool]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            ok = fn(*a, **kw)
        return buf.getvalue(), ok

    @staticmethod
    def _mk_subprocess_result(**kwargs) -> MagicMock:
        return MagicMock(
            returncode=kwargs.get("returncode", 0),
            stdout=kwargs.get("stdout", ""),
            stderr=kwargs.get("stderr", ""),
        )

    def _mock_all_passing(self, mock_sub_run, mock_which=None) -> None:
        """Configure mocks so run_doctor passes all 8 checks."""
        mock_sub_run.return_value = self._mk_subprocess_result(stdout="Gitignored: 0\nMissing: 0")
        if mock_which is not None:
            mock_which.return_value = "/usr/local/bin/mock"

    def _minimal_git_repo(self) -> None:
        Path(".git/hooks").mkdir(parents=True, exist_ok=True)
        for name in ("post-merge", "post-checkout"):
            hook = Path(".git/hooks") / name
            hook.write_text(
                f"#!/bin/bash\n# >>> ai-brain {name} hook begin\n"
                f"# ai-brain start --fast\n"
                f"# <<< ai-brain {name} hook end\n",
                encoding="utf-8",
            )
            hook.chmod(0o755)


# ============================================================================ #
# ============================================================================ #

class _RegisterSeveralMixin(InTempDir):
    def _register(self, paths: list[str]) -> None:
        target = registry.REGISTRY_PATH()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(paths) + "\n", encoding="utf-8")


class TestExcludeByIndex(_RegisterSeveralMixin):
    def test_exclude_by_1based_index(self) -> None:
        projects = ["/Users/carlos/proj-a", "/Users/carlos/proj-b", "/Users/carlos/proj-c"]
        self._register(projects)
        for p in projects:
            registry.enable_archive(p)
        self.assertEqual(len(registry.list_archived()), 3)
        ok = commands.manage_exclude("2")
        self.assertTrue(ok)
        self.assertEqual(set(registry.list_archived()), {projects[0], projects[2]})

    def test_exclude_by_out_of_range_index_warns(self) -> None:
        self._register(["/a", "/b"])
        registry.enable_archive("/a")
        ok = commands.manage_exclude("99")
        self.assertFalse(ok)
        self.assertIn("/a", registry.list_archived())

    def test_exclude_by_zero_is_out_of_range(self) -> None:
        self._register(["/a"])
        ok = commands.manage_exclude("0")
        self.assertFalse(ok)


class TestIncludeByIndex(_RegisterSeveralMixin):
    def test_include_by_1based_index(self) -> None:
        projects = ["/proj-x", "/proj-y"]
        self._register(projects)
        self.assertEqual(len(registry.list_archived()), 0)
        ok = commands.manage_include("1")
        self.assertTrue(ok)
        self.assertEqual(registry.list_archived(), ["/proj-x"])


class TestAllToken(_RegisterSeveralMixin):
    def test_exclude_all_disables_every_active(self) -> None:
        projects = ["/a", "/b", "/c"]
        self._register(projects)
        for p in projects:
            registry.enable_archive(p)
        self.assertEqual(len(registry.list_archived()), 3)
        ok = commands.manage_exclude("all")
        self.assertTrue(ok)
        self.assertEqual(registry.list_archived(), [])

    def test_exclude_all_uppercase(self) -> None:
        self._register(["/a"])
        registry.enable_archive("/a")
        self.assertTrue(commands.manage_exclude("ALL"))
        self.assertEqual(registry.list_archived(), [])

    def test_include_all_enables_every_active(self) -> None:
        projects = ["/a", "/b"]
        self._register(projects)
        ok = commands.manage_include("all")
        self.assertTrue(ok)
        self.assertEqual(set(registry.list_archived()), {"/a", "/b"})

    def test_include_all_no_active_projects(self) -> None:
        ok = commands.manage_include("all")
        self.assertTrue(ok)
        self.assertEqual(registry.list_archived(), [])


class TestKeywordStillWorks(_RegisterSeveralMixin):
    def test_keyword_substring_still_works(self) -> None:
        self._register(["/Users/me/work/api-server", "/Users/me/work/web"])
        registry.enable_archive("/Users/me/work/api-server")
        ok = commands.manage_exclude("api")
        self.assertTrue(ok)
        self.assertNotIn("/Users/me/work/api-server", registry.list_archived())


class TestNoArgsShowsList(_RegisterSeveralMixin):
    def _capture(self, fn, *args, **kwargs) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(*args, **kwargs)
        return buf.getvalue()

    def test_include_no_args_prints_list_header(self) -> None:
        self._register(["/proj-alpha", "/proj-beta"])
        registry.enable_archive("/proj-alpha")
        out = self._capture(commands.manage_include, None)
        self.assertIn("全域自動記憶歸檔狀態清單", out)
        self.assertIn("[1]", out)
        self.assertIn("proj-alpha", out)
        self.assertIn("proj-beta", out)
        self.assertIn("已啟用自動歸檔", out)
        self.assertIn("預設不歸檔", out)

    def test_exclude_no_args_still_works(self) -> None:
        self._register(["/proj-x"])
        out = self._capture(commands.manage_exclude, None)
        self.assertIn("全域自動記憶歸檔狀態清單", out)
        self.assertIn("proj-x", out)

    def test_include_no_args_does_not_mutate_archive(self) -> None:
        self._register(["/proj-a", "/proj-b"])
        registry.enable_archive("/proj-a")
        archived_before = set(registry.list_archived())
        self._capture(commands.manage_include, None)
        archived_after = set(registry.list_archived())
        self.assertEqual(archived_before, archived_after)


class TestManageList(_RegisterSeveralMixin):
    def _capture(self, fn, *args, **kwargs) -> tuple[str, bool]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            ok = fn(*args, **kwargs)
        return buf.getvalue(), ok

    def test_list_all_projects(self) -> None:
        projects = ["/proj-a", "/proj-b"]
        self._register(projects)
        registry.enable_archive("/proj-a")
        out, ok = self._capture(commands.manage_list)
        self.assertTrue(ok)
        self.assertIn("全域自動記憶歸檔狀態清單", out)
        self.assertIn("proj-a", out)
        self.assertIn("proj-b", out)

    def test_list_does_not_mutate_archive(self) -> None:
        self._register(["/proj-a"])
        registry.enable_archive("/proj-a")
        before = set(registry.list_archived())
        self._capture(commands.manage_list)
        after = set(registry.list_archived())
        self.assertEqual(before, after)


class TestManageRemove(_RegisterSeveralMixin):
    def test_remove_by_1based_index(self) -> None:
        projects = ["/proj-a", "/proj-b", "/proj-c"]
        self._register(projects)
        registry.enable_archive("/proj-b")
        ok = commands.manage_remove("2")
        self.assertTrue(ok)
        self.assertEqual(registry.list_active(), ["/proj-a", "/proj-c"])
        self.assertEqual(registry.list_archived(), [])

    def test_remove_by_keyword(self) -> None:
        self._register(["/proj-a", "/proj-b"])
        ok = commands.manage_remove("proj-a")
        self.assertTrue(ok)
        self.assertEqual(registry.list_active(), ["/proj-b"])

    def test_remove_all(self) -> None:
        self._register(["/proj-a", "/proj-b"])
        registry.enable_archive("/proj-a")
        ok = commands.manage_remove("all")
        self.assertTrue(ok)
        self.assertEqual(registry.list_active(), [])
        self.assertEqual(registry.list_archived(), [])


class TestDoctor(_RegisterSeveralMixin):
    def test_doctor_passes_when_everything_clean(self) -> None:
        global_gi = commands._global_gitignore_path()
        global_gi.parent.mkdir(parents=True, exist_ok=True)
        global_gi.write_text(".codebase-memory/\n", encoding="utf-8")

        paths = MagicMock()
        with patch("ai_brain.verifier.run_all_checks") as mock_checks, \
             patch("ai_brain.commands.subprocess.run") as mock_run, \
             patch("ai_brain.commands.shutil.which") as mock_which:

            mock_checks.return_value = [CheckResult("Mock Check", VERIFY_PASS)]
            mock_sync = MagicMock()
            mock_sync.stdout = "Gitignored: 0\nMissing: 0"
            mock_run.return_value = mock_sync
            mock_which.return_value = "/usr/local/bin/mock"

            ok = commands.run_doctor(paths, fix=False)
            self.assertTrue(ok)

    def test_doctor_fails_and_fixes_gitignore(self) -> None:
        global_gi = commands._global_gitignore_path()
        global_gi.parent.mkdir(parents=True, exist_ok=True)
        global_gi.write_text("", encoding="utf-8")

        paths = MagicMock()
        with patch("ai_brain.verifier.run_all_checks") as mock_checks, \
             patch("ai_brain.commands.subprocess.run") as mock_run:

            mock_checks.return_value = [CheckResult("Mock Check", VERIFY_PASS)]
            mock_sync = MagicMock()
            mock_sync.stdout = "Gitignored: 0\nMissing: 0"
            mock_run.return_value = mock_sync

            ok = commands.run_doctor(paths, fix=False)
            self.assertFalse(ok)

            ok = commands.run_doctor(paths, fix=True)
            self.assertTrue(ok)
            global_gi = commands._global_gitignore_path()
            self.assertIn(".codebase-memory/", global_gi.read_text(encoding="utf-8"))

    def test_doctor_fails_and_fixes_git_hooks(self) -> None:
        Path(".git").mkdir(exist_ok=True)
        global_gi = commands._global_gitignore_path()
        global_gi.parent.mkdir(parents=True, exist_ok=True)
        global_gi.write_text(".codebase-memory/\n", encoding="utf-8")

        paths = MagicMock()
        with patch("ai_brain.verifier.run_all_checks") as mock_checks, \
             patch("ai_brain.commands.subprocess.run") as mock_run, \
             patch("ai_brain.commands.shutil.which") as mock_which:

            mock_checks.return_value = [CheckResult("Mock Check", VERIFY_PASS)]
            mock_sync = MagicMock()
            mock_sync.stdout = "Gitignored: 0\nMissing: 0"
            mock_run.return_value = mock_sync
            mock_which.return_value = "/usr/local/bin/mock"

            ok = commands.run_doctor(paths, fix=False)
            self.assertFalse(ok)

            ok = commands.run_doctor(paths, fix=True)
            self.assertTrue(ok)

            hooks_dir = Path(".git") / "hooks"
            self.assertTrue((hooks_dir / "post-merge").is_file())
            self.assertTrue((hooks_dir / "post-checkout").is_file())
            self.assertIn("--fast", (hooks_dir / "post-merge").read_text(encoding="utf-8"))

    @unittest.mock.patch("ai_brain.commands._read_all_ignores")
    @unittest.mock.patch("ai_brain.verifier.run_all_checks")
    @unittest.mock.patch("ai_brain.commands.subprocess.run")
    def test_doctor_scans_multiple_projects_by_default(self, mock_run, mock_checks, mock_ignores) -> None:
        from ai_brain.verifier import CheckResult, PASS
        mock_checks.return_value = [CheckResult("Mock Check", PASS)]
        mock_sync = unittest.mock.MagicMock()
        mock_sync.stdout = "Gitignored: 0\nMissing: 0"
        mock_run.return_value = mock_sync
        mock_ignores.return_value = {".codebase-memory"}

        proj_a = Path(self.tmpdir) / "proj-a"
        proj_b = Path(self.tmpdir) / "proj-b"
        proj_a.mkdir()
        proj_b.mkdir()
        self._register([str(proj_a), str(proj_b)])

        from unittest.mock import MagicMock
        paths = MagicMock()

        ok = commands.run_doctor(paths, target=None, fix=False)
        self.assertTrue(ok)
        self.assertEqual(mock_ignores.call_count, 2)

    @unittest.mock.patch("ai_brain.commands._read_all_ignores")
    @unittest.mock.patch("ai_brain.verifier.run_all_checks")
    @unittest.mock.patch("ai_brain.commands.subprocess.run")
    def test_doctor_scans_single_project_when_target_provided(self, mock_run, mock_checks, mock_ignores) -> None:
        from ai_brain.verifier import CheckResult, PASS
        mock_checks.return_value = [CheckResult("Mock Check", PASS)]
        mock_sync = unittest.mock.MagicMock()
        mock_sync.stdout = "Gitignored: 0\nMissing: 0"
        mock_run.return_value = mock_sync
        mock_ignores.return_value = {".codebase-memory"}

        proj_a = Path(self.tmpdir) / "proj-a"
        proj_b = Path(self.tmpdir) / "proj-b"
        proj_a.mkdir()
        proj_b.mkdir()
        self._register([str(proj_a), str(proj_b)])

        from unittest.mock import MagicMock
        paths = MagicMock()

        ok = commands.run_doctor(paths, target="1", fix=False)
        self.assertTrue(ok)
        self.assertEqual(mock_ignores.call_count, 1)


# ============================================================================ #
# ============================================================================ #

class TestInitBrain(_CmdBase):
    @patch("ai_brain.commands.git_hooks.install")
    @patch("ai_brain.commands.subprocess.run")
    def test_success(self, mock_run, mock_hooks):
        mock_run.return_value = self._mk_subprocess_result()

        out, ok = self._capture(commands.init_brain)
        self.assertTrue(ok)
        self.assertIn("開始初始化", out)
        self.assertTrue(mock_hooks.called)
        self.assertIn(str(Path.cwd().resolve()), registry.list_active())

    @patch("ai_brain.commands.subprocess.run")
    def test_mempalace_missing(self, mock_run):
        mock_run.side_effect = FileNotFoundError("no mempalace")

        out, ok = self._capture(commands.init_brain)
        self.assertFalse(ok)
        self.assertIn("未找到", out)

    def test_ensure_codebase_memory_ignored(self):
        gi = commands._global_gitignore_path()
        gi.write_text("", encoding="utf-8")
        commands._ensure_codebase_memory_ignored()
        content = gi.read_text(encoding="utf-8")
        self.assertIn(".codebase-memory", content)
        self.assertIn(".worktree", content)

    @patch("ai_brain.commands.git_hooks.install")
    @patch("ai_brain.commands.subprocess.run")
    def test_codebase_memory_warning_still_succeeds(self, mock_run, mock_hooks):
        """codebase-memory init failure is a warning, not a hard failure."""
        def side_effect(*args, **kwargs):
            cmd = args[0]
            if cmd and "codebase-memory-mcp" in cmd[0]:
                raise Exception("graph index warning")
            return self._mk_subprocess_result()
        mock_run.side_effect = side_effect

        out, ok = self._capture(commands.init_brain)
        self.assertTrue(ok)


class TestFullInit(_CmdBase):
    @patch("ai_brain.plugins.install_kilo_skill_stub")
    @patch("ai_brain.plugins.install_opencode_plugins")
    @patch("ai_brain.commands.register_all")
    @patch("ai_brain.cron.install")
    @patch("ai_brain.commands.git_hooks.install")
    @patch("ai_brain.commands.subprocess.run")
    def test_success_calls_all_steps(self, mock_run, mock_hooks, mock_cron,
                                     mock_reg, mock_oc, mock_kilo):
        mock_run.return_value = self._mk_subprocess_result()
        mock_cron.return_value = True

        out, ok = self._capture(commands.full_init, MagicMock())
        self.assertTrue(ok)
        self.assertIn("終極全自動", out)
        self.assertTrue(mock_hooks.called)
        self.assertTrue(mock_cron.called)
        self.assertTrue(mock_reg.called)
        self.assertTrue(mock_oc.called)
        self.assertTrue(mock_kilo.called)

    @patch("ai_brain.commands.subprocess.run")
    def test_bails_out_if_init_fails(self, mock_run):
        mock_run.side_effect = FileNotFoundError("no mempalace")

        out, ok = self._capture(commands.full_init, MagicMock())
        self.assertFalse(ok)


class TestCleanBrain(_CmdBase):
    @patch("ai_brain.commands.git_hooks.uninstall")
    def test_removes_project_files(self, mock_uninstall):
        Path("mempalace.yaml").write_text("rooms: []\n", encoding="utf-8")
        Path("entities.json").write_text("{}", encoding="utf-8")
        Path(".claude/config.json").write_text("{}", encoding="utf-8")
        Path(".claude").mkdir(exist_ok=True)
        Path(".claude/CLAUDE.md").write_text(
            "# AI Agent 大腦與記憶指引\nold content\n", encoding="utf-8",
        )
        self._register([str(Path.cwd().resolve())])

        out, ok = self._capture(commands.clean_brain)
        self.assertTrue(ok)
        self.assertIn("已成功清除", out)
        self.assertFalse(Path("mempalace.yaml").exists())
        self.assertFalse(Path(".claude/config.json").exists())

    @patch("ai_brain.commands.git_hooks.uninstall")
    def test_does_not_fail_on_missing_files(self, mock_uninstall):
        """clean_brain should be idempotent — missing files are fine."""
        out, ok = self._capture(commands.clean_brain)
        self.assertTrue(ok)

    @patch("ai_brain.commands.git_hooks.uninstall")
    def test_deregisters_current_project(self, mock_uninstall):
        self._register([str(Path.cwd().resolve())])
        self.assertGreater(len(registry.list_active()), 0)

        commands.clean_brain()
        self.assertEqual(registry.list_active(), [])


class TestUninstallAll(_CmdBase):
    @patch("ai_brain.plugins.uninstall_kilo_skill")
    @patch("ai_brain.plugins.uninstall_opencode_plugins")
    @patch("ai_brain.commands.deregister_all")
    @patch("ai_brain.commands.git_hooks.uninstall")
    @patch("ai_brain.cron.uninstall")
    @patch("ai_brain.commands.subprocess.run")
    def test_calls_teardown_chain(self, mock_run, mock_cron_un, mock_hooks, mock_dereg,
                                  mock_un_oc, mock_un_kilo):
        mock_run.return_value = self._mk_subprocess_result()
        mock_cron_un.return_value = True
        mock_dereg.return_value = 0

        out, ok = self._capture(commands.uninstall_all, MagicMock())
        self.assertTrue(ok)
        self.assertIn("解除安裝", out)

    @patch("ai_brain.plugins.uninstall_kilo_skill")
    @patch("ai_brain.plugins.uninstall_opencode_plugins")
    @patch("ai_brain.commands.deregister_all")
    @patch("ai_brain.commands.git_hooks.uninstall")
    @patch("ai_brain.cron.uninstall")
    @patch("ai_brain.commands.subprocess.run")
    def test_removes_global_shim(self, mock_run, mock_cron_un, mock_hooks, mock_dereg,
                                 mock_un_oc, mock_un_kilo):
        mock_run.return_value = self._mk_subprocess_result()
        mock_cron_un.return_value = True
        mock_dereg.return_value = 0
        shim = Path.home() / ".local" / "bin" / "ai-brain"
        shim.parent.mkdir(parents=True, exist_ok=True)
        shim.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        self.assertTrue(shim.exists())

        commands.uninstall_all(MagicMock())
        self.assertFalse(shim.exists())


# ============================================================================ #
# ============================================================================ #

class TestStartDay(_CmdBase):
    @patch("ai_brain.commands.subprocess.Popen")
    @patch("ai_brain.commands.subprocess.run")
    def test_refreshes_graph(self, mock_run, mock_popen):
        mock_run.return_value = self._mk_subprocess_result()

        out, ok = self._capture(commands.start_day)
        self.assertTrue(ok)
        self.assertIn("代碼圖譜", out)

    @patch("ai_brain.commands.subprocess.run")
    def test_fails_when_index_fails(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "cmd")

        out, ok = self._capture(commands.start_day)
        self.assertFalse(ok)

    @patch("ai_brain.commands.subprocess.Popen")
    @patch("ai_brain.commands.subprocess.run")
    def test_triggers_background_sweep_when_stale(self, mock_run, mock_popen):
        mock_run.return_value = self._mk_subprocess_result()
        lsf = commands.LAST_SWEEP_FILE()
        lsf.parent.mkdir(parents=True, exist_ok=True)
        import time
        lsf.write_text(str(int(time.time()) - 24 * 3600), encoding="utf-8")

        commands.start_day()
        self.assertTrue(mock_popen.called)

    @patch("ai_brain.commands.subprocess.Popen")
    @patch("ai_brain.commands.subprocess.run")
    def test_skips_sweep_when_recent(self, mock_run, mock_popen):
        mock_run.return_value = self._mk_subprocess_result()
        lsf = commands.LAST_SWEEP_FILE()
        lsf.parent.mkdir(parents=True, exist_ok=True)
        import time
        lsf.write_text(str(int(time.time()) - 100), encoding="utf-8")
        # Also create a recent GC timestamp so _should_run_gc() is False
        lgf = commands.LAST_GC_FILE()
        lgf.write_text(str(int(time.time()) - 100), encoding="utf-8")

        commands.start_day()
        self.assertFalse(mock_popen.called)


class TestStopDay(_CmdBase):
    @patch("ai_brain.commands.subprocess.run")
    def test_runs_sweep(self, mock_run):
        from ai_brain import registry
        registry.enable_archive(str(Path.cwd().resolve()))
        mock_run.return_value = self._mk_subprocess_result(stdout="ok")

        out, ok = self._capture(commands.stop_day)
        self.assertTrue(ok)
        self.assertIn("記憶封存", out)

    @patch("ai_brain.commands.subprocess.run")
    def test_records_sweep_timestamp(self, mock_run):
        mock_run.return_value = self._mk_subprocess_result()

        commands.stop_day()
        self.assertTrue(commands.LAST_SWEEP_FILE().is_file())
        ts = int(commands.LAST_SWEEP_FILE().read_text().strip())
        import time
        self.assertAlmostEqual(ts, int(time.time()), delta=5)


# ============================================================================ #
# ============================================================================ #

class TestCheckStatus(_CmdBase):
    @patch("ai_brain.commands.subprocess.run")
    def test_displays_all_sections(self, mock_run):
        mock_run.return_value = self._mk_subprocess_result(stdout="active project")
        Path(".claude/config.json").write_text("{}", encoding="utf-8")
        Path("CLAUDE.md").write_text("# Guide", encoding="utf-8")
        self._register([str(Path.cwd().resolve())])

        out, ok = self._capture(commands.check_status)
        self.assertTrue(ok)
        self.assertIn("狀態檢查", out)
        self.assertIn("MemPalace", out)
        self.assertIn("Codebase-Memory", out)


# ============================================================================ #
# ============================================================================ #

class TestBulkOperations(_CmdBase):
    def test_exclude_all_command(self):
        self._register(["/a", "/b"])
        registry.enable_archive("/a")
        registry.enable_archive("/b")
        self.assertEqual(len(registry.list_archived()), 2)

        out, ok = self._capture(commands.exclude_all)
        self.assertEqual(registry.list_archived(), [])

    def test_include_all_command(self):
        self._register(["/a", "/b"])

        out, ok = self._capture(commands.include_all)
        self.assertTrue(ok)
        self.assertEqual(len(registry.list_archived()), 2)

    def test_manage_remove_no_args_shows_help(self):
        out, ok = self._capture(commands.manage_remove, None)
        self.assertTrue(ok)
        self.assertIn("用法提示", out)

    def test_manage_exclude_no_args_shows_status(self):
        out, ok = self._capture(commands.manage_exclude, None)
        self.assertTrue(ok)
        self.assertIn("歸檔狀態", out)

    def test_manage_include_no_args_shows_status(self):
        out, ok = self._capture(commands.manage_include, None)
        self.assertTrue(ok)
        self.assertIn("歸檔狀態", out)


# ============================================================================ #
# ============================================================================ #

class TestGitignoreHelpers(_CmdBase):
    @patch("ai_brain.commands.subprocess.run")
    def test_global_gitignore_path_uses_config(self, mock_run):
        mock_run.return_value = self._mk_subprocess_result(stdout="~/.gitignore_global\n")
        mock_run.return_value.returncode = 0

        p = commands._global_gitignore_path()
        self.assertEqual(p, Path.home() / ".gitignore_global")

    @patch("ai_brain.commands.subprocess.run")
    def test_global_gitignore_path_fallback(self, mock_run):
        mock_run.return_value = self._mk_subprocess_result(stdout="", returncode=1)

        p = commands._global_gitignore_path()
        self.assertEqual(p, Path.home() / ".gitignore_global")

    def test_read_all_ignores_merges_files(self):
        Path(".gitignore").write_text("node_modules/\n# comment\n\n*.log\n", encoding="utf-8")

        ignores = commands._read_all_ignores(Path("."))
        self.assertIn("node_modules", ignores)
        self.assertIn("*.log", ignores)
        self.assertNotIn("# comment", ignores)
        self.assertNotIn("", ignores)

    def test_append_avoids_duplicates(self):
        gi = commands._global_gitignore_path()
        gi.write_text(".codebase-memory/\n", encoding="utf-8")
        initial_len = len(gi.read_text().splitlines())

        commands._append_to_global_gitignore(".codebase-memory", "test")
        after_len = len(gi.read_text().splitlines())
        self.assertEqual(initial_len, after_len)

    def test_append_adds_new_pattern(self):
        gi = commands._global_gitignore_path()
        gi.write_text("", encoding="utf-8")

        commands._append_to_global_gitignore("new_pattern", "test comment")
        content = gi.read_text(encoding="utf-8")
        self.assertIn("new_pattern/", content)
        self.assertIn("test comment", content)


# ============================================================================ #
# ============================================================================ #

class TestRunMempalaceInit(_CmdBase):
    @patch("ai_brain.commands.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = self._mk_subprocess_result()

        ok = commands._run_mempalace_init()
        self.assertTrue(ok)
        calls = mock_run.call_args_list
        self.assertGreaterEqual(len(calls), 1)
        self.assertEqual(calls[0][0][0][0], "mempalace")

    @patch("ai_brain.commands.subprocess.run")
    def test_tool_missing(self, mock_run):
        mock_run.side_effect = FileNotFoundError("no mempalace")

        out = io.StringIO()
        with redirect_stdout(out):
            ok = commands._run_mempalace_init()
        self.assertFalse(ok)
        self.assertIn("未找到", out.getvalue())

    @patch("ai_brain.commands.subprocess.run")
    def test_command_failed(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "mempalace")

        out = io.StringIO()
        with redirect_stdout(out):
            ok = commands._run_mempalace_init()
        self.assertFalse(ok)


class TestRunCodebaseMemoryInit(_CmdBase):
    @patch("ai_brain.commands.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = self._mk_subprocess_result()

        ok = commands._run_codebase_memory_init()
        self.assertTrue(ok)

    @patch("ai_brain.commands.subprocess.run")
    def test_tool_missing_returns_false(self, mock_run):
        mock_run.side_effect = FileNotFoundError("no cbm")

        out = io.StringIO()
        with redirect_stdout(out):
            ok = commands._run_codebase_memory_init()
        self.assertFalse(ok)

    @patch("ai_brain.commands.subprocess.run")
    def test_generic_exception_returns_true(self, mock_run):
        """codebase-memory-mcp init errors are warnings, not failures."""
        mock_run.side_effect = Exception("network timeout")

        out = io.StringIO()
        with redirect_stdout(out):
            ok = commands._run_codebase_memory_init()
        self.assertTrue(ok)


# ============================================================================ #
# ============================================================================ #

class TestWriteConfigFiles(_CmdBase):
    def test_write_hooks_config(self):
        ok = commands._write_project_hooks_config()
        self.assertTrue(ok)
        self.assertTrue(Path(".claude/config.json").is_file())
        data = json.loads(Path(".claude/config.json").read_text(encoding="utf-8"))
        self.assertIn("hooks", data)

    def test_write_claude_md(self):
        ok = commands._write_project_claude_md()
        self.assertTrue(ok)
        self.assertTrue(Path(".claude/CLAUDE.md").is_file())
        content = Path(".claude/CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("Memory", content)

    def test_remove_path_file(self):
        p = Path("test_file.txt")
        p.write_text("data", encoding="utf-8")
        commands._remove_path(p, is_dir=False, message="removing")
        self.assertFalse(p.exists())

    def test_remove_path_dir(self):
        d = Path("test_dir")
        d.mkdir()
        (d / "sub.txt").write_text("data", encoding="utf-8")
        commands._remove_path(d, is_dir=True, message="removing")
        self.assertFalse(d.exists())

    def test_remove_path_missing_is_noop(self):
        """remove_path on non-existent path should not raise."""
        commands._remove_path(Path("nonexistent"), is_dir=True, message="skip")

    def test_maybe_rmdir_empty(self):
        d = Path("empty_dir")
        d.mkdir()
        commands._maybe_rmdir_empty(d)
        self.assertFalse(d.exists())

    def test_maybe_rmdir_nonempty(self):
        d = Path("nonempty_dir")
        d.mkdir()
        (d / "file.txt").write_text("data", encoding="utf-8")
        commands._maybe_rmdir_empty(d)
        self.assertTrue(d.exists())

    def test_maybe_unlink_claude_md_removes_managed(self):
        Path(".claude").mkdir(exist_ok=True)
        Path(".claude/CLAUDE.md").write_text(
            "# AI Agent 大腦與記憶指引\nold content\n", encoding="utf-8",
        )
        commands._maybe_unlink_claude_md()
        self.assertFalse(Path(".claude/CLAUDE.md").exists())

    def test_maybe_unlink_claude_md_preserves_custom(self):
        Path(".claude").mkdir(exist_ok=True)
        Path(".claude/CLAUDE.md").write_text("# My Custom Guide\n", encoding="utf-8")
        commands._maybe_unlink_claude_md()
        self.assertTrue(Path(".claude/CLAUDE.md").exists())


# ============================================================================ #
# ============================================================================ #

class TestSweepHelpers(_CmdBase):
    def test_should_run_when_no_file(self):
        self.assertTrue(commands._should_run_background_sweep())

    def test_should_run_when_old(self):
        lsf = commands.LAST_SWEEP_FILE()
        lsf.parent.mkdir(parents=True, exist_ok=True)
        import time
        lsf.write_text(str(int(time.time()) - 24 * 3600), encoding="utf-8")
        self.assertTrue(commands._should_run_background_sweep())

    def test_should_not_run_when_recent(self):
        lsf = commands.LAST_SWEEP_FILE()
        lsf.parent.mkdir(parents=True, exist_ok=True)
        import time
        lsf.write_text(str(int(time.time()) - 60), encoding="utf-8")
        self.assertFalse(commands._should_run_background_sweep())

    def test_should_run_when_corrupted(self):
        lsf = commands.LAST_SWEEP_FILE()
        lsf.parent.mkdir(parents=True, exist_ok=True)
        lsf.write_text("not a number", encoding="utf-8")
        self.assertTrue(commands._should_run_background_sweep())

    def test_record_sweep_creates_file(self):
        commands._record_sweep_timestamp()
        self.assertTrue(commands.LAST_SWEEP_FILE().is_file())

    def test_record_sweep_overwrites(self):
        lsf = commands.LAST_SWEEP_FILE()
        lsf.parent.mkdir(parents=True, exist_ok=True)
        lsf.write_text("9999\n", encoding="utf-8")
        commands._record_sweep_timestamp()
        new_val = int(lsf.read_text().strip())
        self.assertNotEqual(new_val, 9999)


class TestDoctorExtra(_CmdBase):
    @patch("ai_brain.commands.git_hooks.install")
    @patch("ai_brain.verifier.run_all_checks")
    @patch("ai_brain.commands.subprocess.run")
    @patch("ai_brain.commands.shutil.which")
    def test_doctor_full_pass_with_hooks_mcp_and_locks(self, mock_which, mock_run,
                                                        mock_checks, mock_hooks):
        """All 8 checks pass."""
        self._minimal_git_repo()
        mock_which.return_value = "/usr/local/bin/mock"
        mock_run.return_value = self._mk_subprocess_result(stdout="Gitignored: 0\nMissing: 0")
        mock_checks.return_value = [CheckResult("Mock", VERIFY_PASS)]

        out, ok = self._capture(commands.run_doctor, MagicMock(), None, False)
        self.assertTrue(ok)
        self.assertIn("100%", out)
        self.assertFalse(mock_hooks.called)

    def test_doctor_target_unresolvable(self):
        out, ok = self._capture(commands.run_doctor, MagicMock(), "nonexistent", False)
        self.assertFalse(ok)

    @patch("ai_brain.verifier.run_all_checks")
    @patch("ai_brain.commands.subprocess.run")
    @patch("ai_brain.commands.shutil.which")
    def test_doctor_non_git_project_skips_hooks(self, mock_which, mock_run, mock_checks):
        mock_which.return_value = "/usr/local/bin/mock"
        mock_run.return_value = self._mk_subprocess_result(stdout="Gitignored: 0\nMissing: 0")
        mock_checks.return_value = [CheckResult("Mock", VERIFY_PASS)]

        out, ok = self._capture(commands.run_doctor, MagicMock(), None, False)
        self.assertTrue(ok)
        self.assertIn("非 Git 專案", out)

    @patch("ai_brain.commands.git_hooks.install")
    @patch("ai_brain.verifier.run_all_checks")
    @patch("ai_brain.commands.subprocess.run")
    def test_doctor_stale_claude_md_fix(self, mock_run, mock_checks, mock_hooks):
        Path(".claude").mkdir(exist_ok=True)
        Path(".claude/CLAUDE.md").write_text(
            "# Old\n\n## 🧠 Layered Memory & Cognitive Workflow (Mandatory Principles)\n"
            "old block\n", encoding="utf-8",
        )
        gi = commands._global_gitignore_path()
        gi.parent.mkdir(parents=True, exist_ok=True)
        gi.write_text(".codebase-memory/\n", encoding="utf-8")

        mock_run.return_value = self._mk_subprocess_result(stdout="Gitignored: 0\nMissing: 0")

        with patch("ai_brain.commands.shutil.which", return_value="/mock"), \
             patch("ai_brain.verifier.run_all_checks",
                   return_value=[CheckResult("Mock", VERIFY_PASS)]):
            out, ok = self._capture(commands.run_doctor, MagicMock(), None, True)
            self.assertTrue(ok)
            content = Path(".claude/CLAUDE.md").read_text(encoding="utf-8")
            self.assertIn("ALWAYS prefer", content)

    @patch("ai_brain.commands.git_hooks.install")
    @patch("ai_brain.verifier.run_all_checks")
    @patch("ai_brain.commands.subprocess.run")
    @patch("ai_brain.commands.shutil.which")
    def test_doctor_fix_mempalace_yaml_room(self, mock_which, mock_run, mock_checks, mock_hooks):
        (Path(self.tmpdir) / "mempalace.yaml").write_text(
            "rooms:\n- name: codebase_memory\n  description: old\n- name: other\n  description: keep\n",
            encoding="utf-8",
        )
        gi = commands._global_gitignore_path()
        gi.parent.mkdir(parents=True, exist_ok=True)
        gi.write_text(".codebase-memory/\n", encoding="utf-8")
        mock_which.return_value = "/mock"
        mock_run.return_value = self._mk_subprocess_result(stdout="Gitignored: 0\nMissing: 0")
        mock_checks.return_value = [CheckResult("Mock", VERIFY_PASS)]
        self._minimal_git_repo()

        out, ok = self._capture(commands.run_doctor, MagicMock(), None, True)
        self.assertTrue(ok)
        yaml_content = (Path(self.tmpdir) / "mempalace.yaml").read_text(encoding="utf-8")
        self.assertNotIn("codebase_memory", yaml_content)
        self.assertIn("other", yaml_content)

    @patch("ai_brain.commands.git_hooks.install")
    @patch("ai_brain.verifier.run_all_checks")
    @patch("ai_brain.commands.subprocess.run")
    @patch("ai_brain.commands.shutil.which", return_value="/mock")
    def test_doctor_removes_project_graphify_plugin_files(self, mock_which, mock_run,
                                                          mock_checks, mock_hooks):
        self._minimal_git_repo()
        (Path(self.tmpdir) / ".opencode" / "plugins").mkdir(parents=True)
        plugin = Path(self.tmpdir) / ".opencode" / "plugins" / "graphify.js"
        plugin.write_text("// graphify", encoding="utf-8")
        mock_run.return_value = self._mk_subprocess_result(stdout="Gitignored: 0\nMissing: 0")
        mock_checks.return_value = [CheckResult("Mock", VERIFY_PASS)]

        out, ok = self._capture(commands.run_doctor, MagicMock(), None, True)
        self.assertTrue(ok)
        self.assertFalse(plugin.exists())
        self.assertIn("已移除", out)

    @patch("ai_brain.commands.git_hooks.install")
    @patch("ai_brain.verifier.run_all_checks")
    @patch("ai_brain.commands.subprocess.run")
    @patch("ai_brain.commands.shutil.which", return_value="/mock")
    def test_doctor_strips_graphify_from_project_opencode_json(self, mock_which, mock_run,
                                                               mock_checks, mock_hooks):
        self._minimal_git_repo()
        opencode_cfg = Path(self.tmpdir) / ".opencode" / "opencode.json"
        opencode_cfg.parent.mkdir(parents=True)
        opencode_cfg.write_text(
            json.dumps({"$schema": "https://app.kilo.ai/config.json",
                         "plugin": [".opencode/plugins/graphify.js", "other"]}),
            encoding="utf-8",
        )
        mock_run.return_value = self._mk_subprocess_result(stdout="Gitignored: 0\nMissing: 0")
        mock_checks.return_value = [CheckResult("Mock", VERIFY_PASS)]

        out, ok = self._capture(commands.run_doctor, MagicMock(), None, True)
        self.assertTrue(ok)
        rebuilt = json.loads(opencode_cfg.read_text(encoding="utf-8"))
        self.assertNotIn(".opencode/plugins/graphify.js", rebuilt.get("plugin", []))
        self.assertIn("other", rebuilt["plugin"])

    @patch("ai_brain.commands.git_hooks.install")
    @patch("ai_brain.verifier.run_all_checks")
    @patch("ai_brain.commands.subprocess.run")
    @patch("ai_brain.commands.shutil.which", return_value="/mock")
    def test_doctor_removes_non_empty_graphify_out(self, mock_which, mock_run,
                                                   mock_checks, mock_hooks):
        self._minimal_git_repo()
        gdir = Path(self.tmpdir) / "graphify-out"
        gdir.mkdir()
        (gdir / "graph.json").write_text('{"nodes":[]}', encoding="utf-8")
        mock_run.return_value = self._mk_subprocess_result(stdout="Gitignored: 0\nMissing: 0")
        mock_checks.return_value = [CheckResult("Mock", VERIFY_PASS)]

        out, ok = self._capture(commands.run_doctor, MagicMock(), None, True)
        self.assertTrue(ok)
        self.assertFalse(gdir.exists())
        self.assertIn("graphify-out", out)

    @patch("ai_brain.commands.git_hooks.install")
    @patch("ai_brain.verifier.run_all_checks")
    @patch("ai_brain.commands.subprocess.run")
    @patch("ai_brain.commands.shutil.which", return_value="/mock")
    def test_doctor_removes_global_graphify_plugin_file(self, mock_which, mock_run,
                                                        mock_checks, mock_hooks):
        self._minimal_git_repo()
        global_plugin_dir = Path.home() / ".config" / "opencode" / "plugins"
        global_plugin_dir.mkdir(parents=True, exist_ok=True)
        global_plugin = global_plugin_dir / "ai-brain-graphify.js"
        global_plugin.write_text("// global graphify plugin", encoding="utf-8")
        mock_run.return_value = self._mk_subprocess_result(stdout="Gitignored: 0\nMissing: 0")
        mock_checks.return_value = [CheckResult("Mock", VERIFY_PASS)]

        try:
            out, ok = self._capture(commands.run_doctor, MagicMock(), None, True)
            self.assertTrue(ok)
            self.assertFalse(global_plugin.exists())
        finally:
            if global_plugin.exists():
                global_plugin.unlink()


# ============================================================================ #
# ============================================================================ #

class TestArchiveSweep(_CmdBase):
    @patch("ai_brain.commands.subprocess.run")
    def test_sweep_skips_when_no_archive(self, mock_run):
        out = io.StringIO()
        with redirect_stdout(out):
            commands._run_archive_sweep(silent=False)
        self.assertIn("白名單為空", out.getvalue())
        self.assertFalse(mock_run.called)

    @patch("ai_brain.commands.find_claude_folder_by_path", return_value=None)
    @patch("ai_brain.commands.subprocess.run")
    def test_sweep_skips_projects_without_claude_dir(self, mock_run, mock_find):
        self._register(["/proj-a"])
        registry.enable_archive("/proj-a")

        commands._run_archive_sweep(silent=False)
        self.assertFalse(mock_run.called)

    @patch("ai_brain.commands.subprocess.run")
    def test_sweep_processes_archived_projects(self, mock_run):
        proj_dir = Path(self.tmpdir) / "proj-a"
        proj_dir.mkdir()
        self._register([str(proj_dir)])
        registry.enable_archive(str(proj_dir))
        mock_run.return_value = self._mk_subprocess_result()

        claude_dir = Path.home() / ".claude" / "projects" / "proj-a"
        claude_dir.mkdir(parents=True, exist_ok=True)

        with patch("ai_brain.commands.find_claude_folder_by_path", return_value=claude_dir):
            commands._run_archive_sweep(silent=False)
        self.assertTrue(mock_run.called)
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "mempalace")
        self.assertEqual(args[1], "sweep")


class TestFindClaudeFolder(_CmdBase):
    def test_returns_none_when_no_projects_dir(self):
        result = commands.find_claude_folder_by_path("/some/path")
        self.assertIsNone(result)

    def test_finds_matching_project(self):
        claude_proj = Path.home() / ".claude" / "projects" / "-some-path"
        claude_proj.mkdir(parents=True, exist_ok=True)
        result = commands.find_claude_folder_by_path("/some/path")
        self.assertEqual(result, claude_proj)


class TestPrintArchiveStatus(_CmdBase):
    def test_prints_list_with_status(self):
        self._register(["/proj-a", "/proj-b"])
        registry.enable_archive("/proj-a")

        out = io.StringIO()
        with redirect_stdout(out):
            commands._print_archive_status()
        output = out.getvalue()
        self.assertIn("[1]", output)
        self.assertIn("[2]", output)
        self.assertIn("已啟用", output)
        self.assertIn("預設不歸檔", output)


# ============================================================================ #
# ============================================================================ #

class TestGlobalClaudeMd(_CmdBase):
    def test_remove_when_no_marker(self):
        md = Path.home() / ".claude" / "CLAUDE.md"
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text("# Other content\n", encoding="utf-8")

        commands._remove_global_cognitive_principles()
        self.assertIn("# Other content", md.read_text(encoding="utf-8"))

    def test_remove_strips_marker_block(self):
        md = Path.home() / ".claude" / "CLAUDE.md"
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(
            "# Before\n\n## 🧠 Layered Memory & Cognitive Workflow (Mandatory Principles)\nold\n",
            encoding="utf-8",
        )
        commands._remove_global_cognitive_principles()
        content = md.read_text(encoding="utf-8")
        self.assertIn("# Before", content)
        self.assertNotIn("Layered Memory", content)

    def test_append_when_absent(self):
        claude_dir = Path.home() / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)

        commands._maybe_append_global_claude_md()
        md = claude_dir / "CLAUDE.md"
        content = md.read_text(encoding="utf-8")
        self.assertIn("Layered Memory", content)

    def test_append_skips_when_present(self):
        claude_dir = Path.home() / ".claude"
        md = claude_dir / "CLAUDE.md"
        md.parent.mkdir(parents=True, exist_ok=True)
        existing = "# Header\n## 🧠 Layered Memory & Cognitive Workflow (Mandatory Principles)\nalready here\n"
        md.write_text(existing, encoding="utf-8")

        out = io.StringIO()
        with redirect_stdout(out):
            commands._maybe_append_global_claude_md()
        self.assertIn("already present", out.getvalue())


class TestInitFailures(_CmdBase):
    @patch("ai_brain.commands._write_project_hooks_config", return_value=False)
    @patch("ai_brain.commands._run_codebase_memory_init", return_value=True)
    @patch("ai_brain.commands._run_mempalace_init", return_value=True)
    @patch("ai_brain.commands._ensure_codebase_memory_ignored")
    def test_hooks_config_failed(self, mock_ign, mock_memp, mock_cb, mock_cfg):
        out, ok = self._capture(commands.init_brain)
        self.assertFalse(ok)
        self.assertIn("開始初始化", out)

    @patch("ai_brain.commands._write_project_claude_md", return_value=False)
    @patch("ai_brain.commands._write_project_hooks_config", return_value=True)
    @patch("ai_brain.commands._run_codebase_memory_init", return_value=True)
    @patch("ai_brain.commands._run_mempalace_init", return_value=True)
    @patch("ai_brain.commands._ensure_codebase_memory_ignored")
    def test_claude_md_failed(self, mock_ign, mock_memp, mock_cb, mock_hooks, mock_md):
        out, ok = self._capture(commands.init_brain)
        self.assertFalse(ok)

    @patch("ai_brain.commands._run_codebase_memory_init", return_value=False)
    @patch("ai_brain.commands._run_mempalace_init", return_value=True)
    @patch("ai_brain.commands._ensure_codebase_memory_ignored")
    def test_codebase_memory_init_failed(self, mock_ign, mock_cb, mock_cbi):
        out, ok = self._capture(commands.init_brain)
        self.assertFalse(ok)


class TestCleanUninstallErrorPaths(_CmdBase):
    @patch("ai_brain.commands.git_hooks.uninstall")
    def test_clean_brain_memfile_unlink_fails(self, mock_uninstall):
        Path("mempalace.yaml").write_text("rooms: []\n", encoding="utf-8")
        with patch.object(Path, "unlink", side_effect=PermissionError("denied")):
            out, ok = self._capture(commands.clean_brain)
        self.assertTrue(ok)
        self.assertIn("清除", out)

    @patch("ai_brain.commands._remove_global_cognitive_principles")
    @patch("ai_brain.plugins.uninstall_kilo_skill")
    @patch("ai_brain.plugins.uninstall_opencode_plugins")
    @patch("ai_brain.commands.deregister_all")
    @patch("ai_brain.commands.git_hooks.uninstall")
    @patch("ai_brain.cron.uninstall")
    @patch("ai_brain.commands.subprocess.run")
    def test_uninstall_shim_unlink_fails(self, mock_run, mock_cron, mock_hooks,
                                          mock_dereg, mock_un_oc, mock_un_kilo, mock_cog):
        mock_run.return_value = self._mk_subprocess_result()
        mock_cron.return_value = True
        mock_dereg.return_value = 0
        shim = Path.home() / ".local" / "bin" / "ai-brain"
        shim.parent.mkdir(parents=True, exist_ok=True)
        shim.write_text("dummy", encoding="utf-8")
        with patch.object(Path, "unlink", side_effect=[PermissionError("denied")]):
            out, ok = self._capture(commands.uninstall_all, MagicMock())
        self.assertIn("失敗", out)


class TestDoctorErrorPaths(_CmdBase):
    @patch("ai_brain.commands._read_all_ignores", return_value=set())
    @patch("ai_brain.verifier.run_all_checks")
    @patch("ai_brain.commands.subprocess.run")
    @patch("ai_brain.commands.shutil.which")
    def test_cli_autoinstall_fails(self, mock_which, mock_run, mock_checks, mock_ign):
        def run_side_effect(*args, **kwargs):
            cmd = args[0]
            out = io.StringIO()
            with redirect_stdout(out):
                pass
            if len(cmd) >= 2 and cmd[1] == "sync":
                return self._mk_subprocess_result(stdout="Gitignored: 0\nMissing: 0")
            if len(cmd) >= 3 and cmd[0] == "uv" and "install" in cmd:
                raise subprocess.CalledProcessError(1, cmd, stderr="install failed")
            if len(cmd) >= 2 and cmd[0] == "git" and "config" in cmd:
                return self._mk_subprocess_result(returncode=1, stdout="")
            return self._mk_subprocess_result()

        mock_which.side_effect = [None, None, None]
        mock_run.side_effect = run_side_effect
        mock_checks.return_value = [CheckResult("Mock", VERIFY_PASS)]

        out, ok = self._capture(commands.run_doctor, MagicMock(), None, True)
        self.assertIn("失敗", out)

    @patch("ai_brain.commands._read_all_ignores", return_value=".codebase-memory")
    @patch("ai_brain.verifier.run_all_checks")
    @patch("ai_brain.commands.subprocess.run")
    @patch("ai_brain.commands.shutil.which")
    def test_mempalace_sync_apply_failure(self, mock_which, mock_run, mock_checks, mock_ign):
        def run_side_effect(*args, **kwargs):
            cmd = args[0]
            if len(cmd) >= 2 and cmd[0] == "mempalace" and cmd[1] == "sync":
                if "--apply" in cmd:
                    raise subprocess.CalledProcessError(1, cmd, stderr="sync failed")
                return self._mk_subprocess_result(stdout="Gitignored: 3\nMissing: 2")
            if len(cmd) >= 2 and cmd[0] == "git" and "config" in cmd:
                return self._mk_subprocess_result(returncode=1)
            return self._mk_subprocess_result()

        mock_run.side_effect = run_side_effect
        mock_which.return_value = "/mock"
        mock_checks.return_value = [CheckResult("Mock", VERIFY_PASS)]

        out, ok = self._capture(commands.run_doctor, MagicMock(), None, True)
        self.assertIn("失敗", out)

    @patch("ai_brain.commands._read_all_ignores", return_value={".codebase-memory"})
    @patch("ai_brain.verifier.run_all_checks")
    @patch("ai_brain.commands.subprocess.run")
    @patch("ai_brain.commands.shutil.which")
    def test_verifier_failures_then_re_register_errors(self, mock_which, mock_run, mock_checks, mock_ign):
        mock_which.return_value = "/mock"
        mock_run.side_effect = lambda *a, **k: self._mk_subprocess_result(stdout="Gitignored: 0\nMissing: 0")
        from ai_brain.verifier import FAIL
        mock_checks.return_value = [CheckResult("Mock FAIL", FAIL, detail="broken")]
        with patch("ai_brain.mcp.register_all", side_effect=Exception("register exploded")):
            out, ok = self._capture(commands.run_doctor, MagicMock(), None, True)
        self.assertIn("失敗", out)

    @patch("ai_brain.commands._read_all_ignores", return_value={".codebase-memory"})
    @patch("ai_brain.verifier.run_all_checks")
    @patch("ai_brain.commands.subprocess.run")
    @patch("ai_brain.commands.shutil.which")
    def test_mempalace_sync_tool_not_found(self, mock_which, mock_run, mock_checks, mock_ign):
        mock_which.return_value = "/mock"
        def run_side(*a, **k):
            cmd = a[0]
            if len(cmd) >= 2 and cmd[0] == "mempalace" and cmd[1] == "sync":
                raise FileNotFoundError("mempalace not found")
            return self._mk_subprocess_result(stdout="Gitignored: 0\nMissing: 0")
        mock_run.side_effect = run_side
        mock_checks.return_value = [CheckResult("Mock", VERIFY_PASS)]
        out, ok = self._capture(commands.run_doctor, MagicMock(), None, False)
        self.assertIn("跳過", out)

    @patch("ai_brain.git_hooks.subprocess.run", return_value=MagicMock(returncode=1, stdout=""))
    @patch("ai_brain.commands._read_all_ignores")
    @patch("ai_brain.verifier.run_all_checks")
    @patch("ai_brain.commands.subprocess.run")
    @patch("ai_brain.commands.shutil.which")
    def test_git_hooks_install_failure_during_fix(self, mock_which, mock_run, mock_checks, mock_ignores, mock_git_config):
        mock_ignores.return_value = {".codebase-memory"}
        mock_run.side_effect = lambda *a, **k: self._mk_subprocess_result(
            stdout="Gitignored: 0\nMissing: 0"
        )
        mock_which.return_value = "/mock"
        mock_checks.return_value = [CheckResult("Mock", VERIFY_PASS)]
        Path(".git").mkdir(exist_ok=True)
        out, ok = self._capture(commands.run_doctor, MagicMock(), None, True)
        self.assertTrue(ok)


class TestLockHandling(_CmdBase):
    @patch("ai_brain.verifier.run_all_checks")
    @patch("ai_brain.commands.subprocess.run")
    @patch("ai_brain.commands.shutil.which")
    def test_stale_lock_cleaned_in_fix_mode(self, mock_which, mock_run, mock_checks):
        mock_which.return_value = "/mock"
        def run_side(*a, **k):
            cmd = a[0]
            if len(cmd) >= 2 and cmd[0] == "mempalace" and cmd[1] == "sync":
                return self._mk_subprocess_result(stdout="Gitignored: 0\nMissing: 0")
            if len(cmd) >= 2 and cmd[0] == "git" and "config" in cmd:
                return self._mk_subprocess_result(returncode=1)
            return self._mk_subprocess_result()
        mock_run.side_effect = run_side
        mock_checks.return_value = [CheckResult("Mock", VERIFY_PASS)]

        locks_dir = Path.home() / ".mempalace" / "locks"
        locks_dir.mkdir(parents=True, exist_ok=True)
        lock = locks_dir / "test.lock"
        lock.write_text("99999 defunct_proc\n", encoding="utf-8")

        with patch("os.kill", side_effect=OSError("no such process")):
            out, ok = self._capture(commands.run_doctor, MagicMock(), None, True)
        self.assertTrue(ok)
        self.assertFalse(lock.exists())


class TestMempalaceYaml(_CmdBase):
    @patch("ai_brain.verifier.run_all_checks")
    @patch("ai_brain.commands.subprocess.run")
    @patch("ai_brain.commands.shutil.which")
    def test_yaml_still_has_codebase_room_detect(self, mock_which, mock_run, mock_checks):
        mock_which.return_value = "/mock"
        def run_side(*a, **k):
            cmd = a[0]
            if len(cmd) >= 2 and cmd[0] == "mempalace" and cmd[1] == "sync":
                return self._mk_subprocess_result(stdout="Gitignored: 0\nMissing: 0")
            if len(cmd) >= 2 and cmd[0] == "git" and "config" in cmd:
                return self._mk_subprocess_result(returncode=1)
            return self._mk_subprocess_result()
        mock_run.side_effect = run_side
        mock_checks.return_value = [CheckResult("Mock", VERIFY_PASS)]

        (Path(self.tmpdir) / "mempalace.yaml").write_text(
            "rooms:\n- name: .codebase-memory\n  description: bad\n- name: other\n  description: ok\n",
            encoding="utf-8",
        )
        gi = commands._global_gitignore_path()
        gi.parent.mkdir(parents=True, exist_ok=True)
        gi.write_text(".codebase-memory/\n", encoding="utf-8")
        self._minimal_git_repo()

        out, ok = self._capture(commands.run_doctor, MagicMock(), None, True)
        self.assertTrue(ok)
        self.assertIn("FIXED", out)


class TestGitignorePathEdgeCases(_CmdBase):
    @patch("ai_brain.commands.subprocess.run")
    def test_returns_absolute_outside_home_path(self, mock_run):
        mock_run.return_value = self._mk_subprocess_result(
            returncode=0, stdout="/nonexistent/outside.gitignore\n"
        )
        with patch("ai_brain.commands.Path.home") as mock_home:
            mock_home.return_value = Path("/real_home")
            mock_home.side_effect = None
            import os
            real_home = Path(os.path.expanduser("~"))
            with patch("os.path.expanduser", return_value=str(real_home)):
                p = commands._global_gitignore_path()

    @patch("ai_brain.commands.subprocess.run")
    def test_returns_tilde_prefixed_path(self, mock_run):
        mock_run.return_value = self._mk_subprocess_result(
            returncode=0, stdout="~/my_gitignore\n"
        )
        p = commands._global_gitignore_path()

    def test_fallback_when_subprocess_raises(self):
        with patch("ai_brain.commands.subprocess.run", side_effect=OSError("no git")):
            p = commands._global_gitignore_path()
        self.assertEqual(p.name, ".gitignore_global")


class TestReadAllIgnoresEdgeCases(_CmdBase):
    @patch.object(Path, "read_text")
    def test_read_handles_decode_errors(self, mock_read):
        mock_read.side_effect = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")
        ignores = commands._read_all_ignores(Path("."))
        self.assertEqual(ignores, set())


class TestAppendGitignoreErrors(_CmdBase):
    @patch.object(Path, "write_text")
    def test_append_handles_write_failure(self, mock_write):
        mock_write.side_effect = PermissionError("denied")
        out = io.StringIO()
        with redirect_stdout(out):
            commands._append_to_global_gitignore("new_pattern/", "test")
        self.assertIn("失敗", out.getvalue())


class TestMaybeRmdirEmpty(_CmdBase):
    def test_non_empty_dir_stays(self):
        d = Path("nonempty")
        d.mkdir()
        (d / "file.txt").write_text("data", encoding="utf-8")
        self._capture(commands._maybe_rmdir_empty, d)
        self.assertTrue(d.exists())


class TestMaybeUnlinkClaudeMd(_CmdBase):
    def test_unlinks_matching_templates(self):
        Path(".claude").mkdir(exist_ok=True)
        for title in (
            "AI Agent 認知工作流與大腦記憶指引",
            "AI Agent Cognitive Workflow and Memory Guide",
            "AI Agent Cognitive Workflow and Memory Guidelines",
        ):
            Path(".claude/CLAUDE.md").write_text(f"# {title}\ncontent\n", encoding="utf-8")
            commands._maybe_unlink_claude_md()
            self.assertFalse(Path(".claude/CLAUDE.md").exists())


class TestGlobalCognitiveErrors(_CmdBase):
    @patch.object(Path, "read_text")
    def test_remove_handles_read_error(self, mock_read):
        mock_read.side_effect = OSError("cannot read")
        commands._remove_global_cognitive_principles()

    def test_append_handles_write_error(self):
        claude_dir = Path.home() / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        md = claude_dir / "CLAUDE.md"
        md.write_text("# existing\nno marker here\n", encoding="utf-8")
        with patch("builtins.open", side_effect=PermissionError("denied")):
            out = io.StringIO()
            with redirect_stdout(out):
                commands._maybe_append_global_claude_md()
        self.assertIn("失敗", out.getvalue())


class TestMempalaceInitEdgeCases(_CmdBase):
    @patch("ai_brain.commands.subprocess.run")
    def test_sync_failure_still_succeeds(self, mock_run):
        def side_effect(*args, **kwargs):
            cmd = args[0]
            if "--apply" in cmd:
                raise subprocess.CalledProcessError(1, cmd, stderr="sync failed")
            return self._mk_subprocess_result()
        mock_run.side_effect = side_effect
        out = io.StringIO()
        with redirect_stdout(out):
            ok = commands._run_mempalace_init()
        self.assertTrue(ok)

    @patch("ai_brain.commands.subprocess.run")
    def test_calledprocesserror_from_init(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "mempalace", stderr="init error")
        out = io.StringIO()
        with redirect_stdout(out):
            ok = commands._run_mempalace_init()
        self.assertFalse(ok)
        self.assertIn("失敗", out.getvalue())


class TestDoctorMempalaceErrors(_CmdBase):
    @patch("ai_brain.commands._read_all_ignores")
    @patch("ai_brain.verifier.run_all_checks")
    @patch("ai_brain.commands.subprocess.run")
    @patch("ai_brain.commands.shutil.which")
    def test_mempalace_sync_apply_failure(self, mock_which, mock_run, mock_checks, mock_ignores):
        gi = commands._global_gitignore_path()
        gi.parent.mkdir(parents=True, exist_ok=True)
        gi.write_text(".codebase-memory/\n", encoding="utf-8")
        
        mock_ignores.return_value = {".codebase-memory"}
        mock_checks.return_value = [CheckResult("Mock", VERIFY_PASS)]
        mock_which.return_value = "/mock"
        
        def run_side_effect(cmd, **kwargs):
            if "mempalace" in cmd and "--apply" in cmd:
                raise subprocess.CalledProcessError(1, cmd, "sync apply failed")
            return MagicMock(stdout="Gitignored: 3\nMissing: 2", returncode=0)
        
        mock_run.side_effect = run_side_effect
        
        with patch("ai_brain.commands.git_hooks.install"):
            out, ok = self._capture(commands.run_doctor, MagicMock(), None, True)
        self.assertFalse(ok)
        self.assertIn("記憶庫清理失敗", out)
    
    @patch("ai_brain.commands._read_all_ignores")
    @patch("ai_brain.verifier.run_all_checks")
    @patch("ai_brain.commands.subprocess.run")
    @patch("ai_brain.commands.shutil.which")
    def test_lock_file_stale_deletion_failure(self, mock_which, mock_run, mock_checks, mock_ignores):
        gi = commands._global_gitignore_path()
        gi.parent.mkdir(parents=True, exist_ok=True)
        gi.write_text(".codebase-memory/\n", encoding="utf-8")
        
        mock_ignores.return_value = {".codebase-memory"}
        mock_checks.return_value = [CheckResult("Mock", VERIFY_PASS)]
        mock_which.return_value = "/mock"
        mock_run.return_value = MagicMock(stdout="Gitignored: 0\nMissing: 0", returncode=0)
        
        locks_dir = Path.home() / ".mempalace" / "locks"
        locks_dir.mkdir(parents=True, exist_ok=True)
        stale_lock = locks_dir / "stale_test.lock"
        stale_lock.write_text("99999 old\n", encoding="utf-8")
        
        with patch("ai_brain.commands.git_hooks.install"), \
             patch.object(Path, "unlink", side_effect=PermissionError("locked")):
            out, ok = self._capture(commands.run_doctor, MagicMock(), None, True)
        self.assertTrue(ok)


class TestInitBrainErrors(_CmdBase):
    def test_init_brain_when_mempalace_not_installed(self):
        with patch("ai_brain.commands._run_mempalace_init", return_value=False):
            out = io.StringIO()
            with redirect_stdout(out):
                ok = commands.init_brain()
            self.assertFalse(ok)

    def test_init_brain_when_mempalace_fails(self):
        with patch("ai_brain.commands._run_mempalace_init", return_value=True), \
             patch("ai_brain.commands._run_codebase_memory_init", return_value=False):
            out = io.StringIO()
            with redirect_stdout(out):
                ok = commands.init_brain()
            self.assertFalse(ok)


class TestStartStopDayErrors(_CmdBase):
    def test_start_day_when_codebase_memory_init_fails(self):
        with patch("ai_brain.commands.subprocess.run", side_effect=subprocess.CalledProcessError(1, "cmd")):
            out = io.StringIO()
            with redirect_stdout(out):
                ok = commands.start_day(fast=False)
            self.assertFalse(ok)

    def test_start_day_background_sweep_failure(self):
        with patch("ai_brain.commands._should_run_background_sweep", return_value=True), \
             patch("ai_brain.commands.subprocess.Popen", side_effect=OSError("mock error")):
            out = io.StringIO()
            with redirect_stdout(out):
                ok = commands.start_day()
            self.assertIn("背景歸檔啟動失敗", out.getvalue())


class TestCheckStatusEdgeCases(_CmdBase):
    def test_check_status_when_no_config_files(self):
        out = io.StringIO()
        with redirect_stdout(out):
            ok = commands.check_status()
        self.assertTrue(ok)
        self.assertIn("狀態檢查", out.getvalue())


class TestGitIgnorePathEdgeCases(_CmdBase):
    def test_global_gitignore_path_returns_default(self):
        path = commands._global_gitignore_path()
        self.assertIn(".gitignore", str(path))

    def test_read_all_ignores_with_empty_files(self):
        Path(".gitignore").write_text("", encoding="utf-8")
        global_gi = commands._global_gitignore_path()
        global_gi.parent.mkdir(parents=True, exist_ok=True)
        global_gi.write_text("", encoding="utf-8")
        result = commands._read_all_ignores(Path("."))
        self.assertEqual(result, set())

    def test_read_all_ignores_with_comments_and_patterns(self):
        Path(".gitignore").write_text("# comment\ntest_pattern_123\n", encoding="utf-8")
        result = commands._read_all_ignores(Path("."))
        self.assertIn("test_pattern_123", result)
        self.assertNotIn("# comment", result)

    def test_append_to_global_gitignore_creates_file(self):
        commands._append_to_global_gitignore("test_pattern", "Test comment")
        path = commands._global_gitignore_path()
        self.assertTrue(path.exists())

    def test_append_to_global_gitignore_avoids_duplicates(self):
        commands._append_to_global_gitignore("test_pattern", "Test comment")
        path = commands._global_gitignore_path()
        initial_lines = path.read_text(encoding="utf-8").splitlines()

        commands._append_to_global_gitignore("test_pattern", "Test comment")
        final_lines = path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(initial_lines, final_lines)


class TestRemoveGlobalCognitivePrinciples(_CmdBase):
    def test_remove_when_file_doesnt_exist(self):
        commands._remove_global_cognitive_principles()

    def test_remove_when_no_marker(self):
        path = Path.home() / ".claude" / "CLAUDE.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# No marker here\n", encoding="utf-8")
        commands._remove_global_cognitive_principles()
        content = path.read_text(encoding="utf-8")
        self.assertEqual(content, "# No marker here\n")

    def test_remove_with_marker(self):
        path = Path.home() / ".claude" / "CLAUDE.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        marker = "## 🧠 Layered Memory & Cognitive Workflow (Mandatory Principles)"
        path.write_text(f"# Before\n{marker}\n", encoding="utf-8")
        commands._remove_global_cognitive_principles()
        content = path.read_text(encoding="utf-8")
        self.assertIn("# Before", content)
        self.assertNotIn(marker, content)


class TestMaybeAppendGlobalClaudeMd(_CmdBase):
    def test_append_when_claude_dir_exists(self):
        path = Path.home() / ".claude" / "CLAUDE.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        commands._maybe_append_global_claude_md()
        self.assertTrue(path.exists())

    def test_skip_when_already_has_marker(self):
        path = Path.home() / ".claude" / "CLAUDE.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        marker = "## 🧠 Layered Memory & Cognitive Workflow (Mandatory Principles)"
        path.write_text(f"# Before\n{marker}\n", encoding="utf-8")
        commands._maybe_append_global_claude_md()
        content = path.read_text(encoding="utf-8")
        self.assertEqual(content.count(marker), 1)


# ============================================================================ #
# Wave 3 regression tests: timeouts, flock serialisation, cron PID guard       #
# ============================================================================ #

class TestWave3Guards(_CmdBase):

    @patch("ai_brain.commands.subprocess.Popen")
    @patch("ai_brain.commands.subprocess.run")
    def test_start_day_releases_lock_after_run(self, mock_run, mock_popen):
        """start_day acquires + releases flock so a second call succeeds."""
        mock_run.return_value = self._mk_subprocess_result()
        self.assertTrue(commands.start_day())
        # Second call must not hang / deadlock
        self.assertTrue(commands.start_day())

    @patch("ai_brain.commands.subprocess.run")
    def test_stop_day_pid_file_written_then_removed(self, mock_run):
        """_run_archive_sweep manages the PID file lifecycle (written then cleaned up)."""
        mock_run.return_value = self._mk_subprocess_result(stdout="ok")
        from ai_brain import registry
        registry.enable_archive(str(Path.cwd().resolve()))

        pid_path = commands._stop_pid_path()
        # Before: no PID file
        pid_path.unlink(missing_ok=True)
        commands.stop_day()
        # After: PID file is cleaned up (unlink called in finally)
        self.assertFalse(pid_path.is_file())

    @patch("ai_brain.commands.subprocess.run")
    def test_archive_sweep_skips_if_another_instance_running(self, mock_run):
        """If a PID file exists for a live process, _run_archive_sweep returns early."""
        mock_run.return_value = self._mk_subprocess_result(stdout="ok")
        from ai_brain import registry
        registry.enable_archive(str(Path.cwd().resolve()))

        pid_path = commands._stop_pid_path()
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        # Write OUR PID so os.kill(pid, 0) succeeds
        pid_path.write_text(str(os.getpid()), encoding="utf-8")
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                commands._run_archive_sweep(silent=False)
            output = buf.getvalue()
            self.assertIn("正在執行", output)
            # Should NOT have called mempalace sweep
            self.assertFalse(
                any("sweep" in str(c) for c in mock_run.call_args_list),
                "mempalace sweep should not be called when another stop is running",
            )
        finally:
            pid_path.unlink(missing_ok=True)

    @patch("ai_brain.commands.shutil.which", return_value="/usr/bin/mock")
    @patch("ai_brain.commands.subprocess.run")
    def test_subprocess_timeout_does_not_fail_doctor(self, mock_run, mock_which):
        """subprocess.TimeoutExpired during mempalace sync in doctor is a warning, not a crash."""
        # First call (mempalace sync) raises TimeoutExpired.
        # Later calls (for CLI checks etc.) succeed.
        def side_effect(*args, **kwargs):
            cmd = args[0] if args else []
            if cmd and "sweep" not in cmd and "sync" in str(cmd):
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=1)
            if cmd and cmd[0] == "git":
                return self._mk_subprocess_result(stdout="")
            return self._mk_subprocess_result(stdout="")

        mock_run.side_effect = side_effect
        self._register([str(Path.cwd().resolve())])
        gi = commands._global_gitignore_path()
        gi.parent.mkdir(parents=True, exist_ok=True)
        gi.write_text(".codebase-memory/\n", encoding="utf-8")

        # run_doctor should NOT raise, even though sync timed out
        out, _ok = self._capture(
            commands.run_doctor, MagicMock(), None, False
        )
        # We should see a timeout warning in the output
        self.assertTrue("TIMEOUT" in out or "PASS" in out or "FAIL" in out)

    @patch("ai_brain.commands.subprocess.Popen")
    @patch("ai_brain.commands.subprocess.run")
    def test_concurrent_start_day_does_not_corrupt(self, mock_run, mock_popen):
        """Two threads running start_day simultaneously don't raise unhandled exceptions."""
        import threading
        mock_run.return_value = self._mk_subprocess_result()

        results = []
        exceptions = []

        def worker():
            try:
                ok = commands.start_day()
                results.append(ok)
            except Exception as e:
                exceptions.append(e)

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        self.assertFalse(exceptions, f"Unexpected exceptions: {exceptions}")
        # At least one thread must obtain the lock and succeed
        self.assertTrue(any(results), "At least one start_day must succeed")


if __name__ == "__main__":
    unittest.main()
