"""Unit tests for the include / exclude resolution logic.

We test the resolution layer (`_resolve_target`) by exercising the public
`manage_exclude` / `manage_include` functions, which is what CLI users hit.
"""
from __future__ import annotations

import unittest

from ai_brain import commands, registry
from ai_brain._testing import InTempDir


class _RegisterSeveralMixin(InTempDir):
    """Register N fake project paths so we can index into them."""

    def _register(self, paths: list[str]) -> None:
        # Manually populate REGISTRY_PATH so we don't depend on cwd.
        target = registry.REGISTRY_PATH()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(paths) + "\n", encoding="utf-8")


class TestExcludeByIndex(_RegisterSeveralMixin):
    def test_exclude_by_1based_index(self) -> None:
        projects = [
            "/Users/carlos/proj-a",
            "/Users/carlos/proj-b",
            "/Users/carlos/proj-c",
        ]
        self._register(projects)

        # Archive everything so we have something to disable.
        for p in projects:
            registry.enable_archive(p)
        self.assertEqual(len(registry.list_archived()), 3)

        ok = commands.manage_exclude("2")  # 1-based: project-b
        self.assertTrue(ok)
        # Only project-b is excluded from auto-archive; the other two stay on.
        self.assertEqual(
            set(registry.list_archived()),
            {projects[0], projects[2]},
        )

    def test_exclude_by_out_of_range_index_warns(self) -> None:
        self._register(["/a", "/b"])
        registry.enable_archive("/a")
        ok = commands.manage_exclude("99")
        self.assertFalse(ok)
        # /a is still archived since the resolve failed
        self.assertIn("/a", registry.list_archived())

    def test_exclude_by_zero_is_out_of_range(self) -> None:
        self._register(["/a"])
        ok = commands.manage_exclude("0")
        self.assertFalse(ok)


class TestIncludeByIndex(_RegisterSeveralMixin):
    def test_include_by_1based_index(self) -> None:
        projects = ["/proj-x", "/proj-y"]
        self._register(projects)
        # nothing archived yet
        self.assertEqual(len(registry.list_archived()), 0)

        ok = commands.manage_include("1")  # 1-based: proj-x
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
        projects = ["/a"]
        self._register(projects)
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
        # Empty active list — should not error
        ok = commands.manage_include("all")
        self.assertTrue(ok)
        self.assertEqual(registry.list_archived(), [])


class TestKeywordStillWorks(_RegisterSeveralMixin):
    """The new index/all support must not break the existing keyword path."""

    def test_keyword_substring_still_works(self) -> None:
        self._register(["/Users/me/work/api-server", "/Users/me/work/web"])
        registry.enable_archive("/Users/me/work/api-server")
        ok = commands.manage_exclude("api")
        self.assertTrue(ok)
        self.assertNotIn("/Users/me/work/api-server", registry.list_archived())


class TestNoArgsShowsList(_RegisterSeveralMixin):
    """Both `ai-brain include` and `ai-brain exclude` (no pattern) show the list.

    Regression test for the requirement that users can always see
    "which projects are auto-archived right now" without first having
    to remember a subcommand.
    """

    def _capture(self, fn, *args, **kwargs) -> str:
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(*args, **kwargs)
        return buf.getvalue()

    def test_include_no_args_prints_list_header(self) -> None:
        self._register(["/proj-alpha", "/proj-beta"])
        registry.enable_archive("/proj-alpha")
        out = self._capture(commands.manage_include, None)
        # Header line + at least one numbered project line.
        self.assertIn("全域自動記憶歸檔狀態清單", out)
        self.assertIn("[1]", out)
        self.assertIn("proj-alpha", out)
        self.assertIn("proj-beta", out)
        # The active project should be tagged as enabled.
        self.assertIn("已啟用自動歸檔", out)
        # The inactive one should be tagged as not enabled.
        self.assertIn("預設不歸檔", out)

    def test_exclude_no_args_still_works(self) -> None:
        # Smoke test: the previous behaviour is preserved.
        self._register(["/proj-x"])
        out = self._capture(commands.manage_exclude, None)
        self.assertIn("全域自動記憶歸檔狀態清單", out)
        self.assertIn("proj-x", out)

    def test_include_no_args_does_not_mutate_archive(self) -> None:
        # Showing the list must not enable or disable anything.
        self._register(["/proj-a", "/proj-b"])
        registry.enable_archive("/proj-a")
        archived_before = set(registry.list_archived())
        self._capture(commands.manage_include, None)
        archived_after = set(registry.list_archived())
        self.assertEqual(archived_before, archived_after)


class TestManageList(_RegisterSeveralMixin):
    """`ai-brain list` shows the current project's auto-archive status."""

    def _capture(self, fn, *args, **kwargs) -> str:
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            ok = fn(*args, **kwargs)
        return buf.getvalue(), ok

    def test_list_inactive(self) -> None:
        # _RegisterSeveralMixin set the cwd to a fresh tempdir; the
        # current directory isn't in the active list we wrote, so we
        # have to register it explicitly to exercise the active case.
        registry.register_current()
        out, ok = self._capture(commands.manage_list)
        self.assertTrue(ok)
        self.assertIn("當前專案自動歸檔狀態", out)
        self.assertIn("預設不歸檔", out)
        self.assertIn("若要啟用", out)
        self.assertIn("ai-brain include current", out)

    def test_list_active(self) -> None:
        registry.register_current()
        registry.enable_archive(registry.current_project_path())
        out, ok = self._capture(commands.manage_list)
        self.assertTrue(ok)
        self.assertIn("已啟用自動歸檔", out)
        self.assertIn("若要停用", out)
        self.assertIn("ai-brain exclude current", out)

    def test_list_unregistered_cwd_fails(self) -> None:
        # Switch to a *different* temp dir that was never registered.
        import os, tempfile
        other = tempfile.mkdtemp(prefix="ai-brain-test-other-")
        old_cwd = os.getcwd()
        try:
            os.chdir(other)
            out, ok = self._capture(commands.manage_list)
            self.assertFalse(ok)
            self.assertIn("未在 AI 大腦活躍清單中註冊", out)
        finally:
            os.chdir(old_cwd)

    def test_list_does_not_mutate_archive(self) -> None:
        # Showing the list is a read-only operation.
        registry.register_current()
        registry.enable_archive(registry.current_project_path())
        before = set(registry.list_archived())
        self._capture(commands.manage_list)
        after = set(registry.list_archived())
        self.assertEqual(before, after)


class TestDoctor(_RegisterSeveralMixin):
    def test_doctor_passes_when_everything_clean(self) -> None:
        from pathlib import Path
        global_gi = commands._global_gitignore_path()
        global_gi.parent.mkdir(parents=True, exist_ok=True)
        global_gi.write_text("graphify-out/\n", encoding="utf-8")
        
        from unittest.mock import MagicMock, patch
        paths = MagicMock()
        from ai_brain.verifier import CheckResult, PASS
        
        with patch("ai_brain.verifier.run_all_checks") as mock_checks, \
             patch("ai_brain.commands.subprocess.run") as mock_run, \
             patch("ai_brain.commands.shutil.which") as mock_which:

            mock_checks.return_value = [
                CheckResult("Mock Check", PASS)
            ]

            mock_sync = MagicMock()
            mock_sync.stdout = "Gitignored: 0\nMissing: 0"
            mock_run.return_value = mock_sync
            mock_which.return_value = "/usr/local/bin/mock"

            ok = commands.run_doctor(paths, fix=False)
            self.assertTrue(ok)

    def test_doctor_fails_and_fixes_gitignore(self) -> None:
        from pathlib import Path
        global_gi = commands._global_gitignore_path()
        global_gi.parent.mkdir(parents=True, exist_ok=True)
        global_gi.write_text("", encoding="utf-8")
        
        from unittest.mock import MagicMock, patch
        paths = MagicMock()
        from ai_brain.verifier import CheckResult, PASS
        
        with patch("ai_brain.verifier.run_all_checks") as mock_checks, \
             patch("ai_brain.commands.subprocess.run") as mock_run:
             
            mock_checks.return_value = [
                CheckResult("Mock Check", PASS)
            ]
            mock_sync = MagicMock()
            mock_sync.stdout = "Gitignored: 0\nMissing: 0"
            mock_run.return_value = mock_sync
            
            ok = commands.run_doctor(paths, fix=False)
            self.assertFalse(ok)
            
            ok = commands.run_doctor(paths, fix=True)
            self.assertTrue(ok)
            global_gi = commands._global_gitignore_path()
            self.assertIn("graphify-out/", global_gi.read_text(encoding="utf-8"))

    def test_doctor_fails_and_fixes_git_hooks(self) -> None:
        from pathlib import Path
        # Initialize Git layout and clean gitignore
        Path(".git").mkdir(exist_ok=True)
        global_gi = commands._global_gitignore_path()
        global_gi.parent.mkdir(parents=True, exist_ok=True)
        global_gi.write_text("graphify-out/\n", encoding="utf-8")
        
        from unittest.mock import MagicMock, patch
        paths = MagicMock()
        from ai_brain.verifier import CheckResult, PASS
        
        with patch("ai_brain.verifier.run_all_checks") as mock_checks, \
             patch("ai_brain.commands.subprocess.run") as mock_run, \
             patch("ai_brain.commands.shutil.which") as mock_which:
             
            mock_checks.return_value = [
                CheckResult("Mock Check", PASS)
            ]
            mock_sync = MagicMock()
            mock_sync.stdout = "Gitignored: 0\nMissing: 0"
            mock_run.return_value = mock_sync
            mock_which.return_value = "/usr/local/bin/mock"
            
            # 1. Runs without fix: should fail since Git Hooks are missing
            ok = commands.run_doctor(paths, fix=False)
            self.assertFalse(ok)
            
            # 2. Runs with fix: should create Git Hooks and return True
            ok = commands.run_doctor(paths, fix=True)
            self.assertTrue(ok)
            
            # 3. Verify hooks were created and optimized
            hooks_dir = Path(".git") / "hooks"
            self.assertTrue((hooks_dir / "post-merge").is_file())
            self.assertTrue((hooks_dir / "post-checkout").is_file())
            self.assertIn("--fast", (hooks_dir / "post-merge").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
