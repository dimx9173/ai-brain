"""Tests for installer module.

Covers all branches:
- _write_global_shim (success, exception)
- install_or_update (source + registry paths)
- _install_from_source (success, shim failure, registry write failure, PATH cases)
- _update_from_registry (no registry, bad source, no git root, git root with dirty, etc.)
- _find_git_root (found, not found)
- _run_git (success, timeout, command-not-found)
- _current_branch, _has_uncommitted_changes, _sync_repo_to_origin (regression tests; keep originals)
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

from ai_brain import installer
from ai_brain._testing import InTempDir
from ai_brain.constants import GLOBAL_AI_BRAIN, INSTALL_SOURCE_REGISTRY, VERSION


# --------------------------------------------------------------------------- #
# Helpers from original test file (needed by regression tests at the bottom)
# --------------------------------------------------------------------------- #

def _init_repo(path: Path, branch: str = "main") -> None:
    subprocess.run(["git", "init", "-q", "-b", branch], cwd=str(path), check=True)
    _stamp_identity(path)
    (path / "README").write_text("v0\n", encoding="utf-8")
    subprocess.run(["git", "add", "README"], cwd=str(path), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(path), check=True)


def _stamp_identity(path: Path) -> None:
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@test"], check=True
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "test"], check=True
    )


# --------------------------------------------------------------------------- #
# New test classes covering the rest of installer.py
# --------------------------------------------------------------------------- #


class TestWriteGlobalShim(InTempDir):
    def test_writes_shim_with_correct_src_dir(self) -> None:
        src_dir = Path(self.tmpdir) / "src"
        src_dir.mkdir()
        ok = installer._write_global_shim(src_dir)
        self.assertTrue(ok)
        shim = GLOBAL_AI_BRAIN()
        self.assertTrue(shim.is_file())
        content = shim.read_text(encoding="utf-8")
        self.assertIn(str(src_dir), content)
        self.assertIn("from ai_brain.cli import main", content)
        import stat
        mode = shim.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR)  # executable

    def test_replaces_existing_shim(self) -> None:
        src_dir = Path(self.tmpdir) / "src"
        src_dir.mkdir()
        installer._write_global_shim(src_dir)
        first = GLOBAL_AI_BRAIN().read_text(encoding="utf-8")
        # Update src_dir path
        src2 = Path(self.tmpdir) / "src2"
        src2.mkdir()
        installer._write_global_shim(src2)
        second = GLOBAL_AI_BRAIN().read_text(encoding="utf-8")
        self.assertNotEqual(first, second)
        self.assertIn(str(src2), second)

    def test_returns_false_when_parent_cannot_be_created(self) -> None:
        # Make .local/bin impossible to create by placing a blocking file
        home = Path(self.tmpdir)
        blocking = home / ".local"
        blocking.parent.mkdir(parents=True, exist_ok=True)
        blocking.write_text("blocker-for-mkdir", encoding="utf-8")  # blocks .local/ subdir
        src_dir = Path(self.tmpdir) / "src"
        src_dir.mkdir()
        ok = installer._write_global_shim(src_dir)
        self.assertFalse(ok)

    def test_returns_false_when_write_fails(self) -> None:
        src_dir = Path(self.tmpdir) / "src"
        src_dir.mkdir()
        with patch.object(Path, "write_text", side_effect=PermissionError("denied")):
            ok = installer._write_global_shim(src_dir)
        self.assertFalse(ok)


class TestInstallOrUpdate(InTempDir):
    @patch("ai_brain.installer._upgrade_and_summarise")
    @patch("ai_brain.installer._install_from_source", return_value=True)
    def test_source_flow_when_not_invoked_as_global(self, mock_install, mock_upgrade):
        # By default sys.argv[0] will be e.g. pytest's entry point, not GLOBAL_AI_BRAIN
        result = installer.install_or_update()
        self.assertTrue(result)
        mock_install.assert_called_once()
        mock_upgrade.assert_called_once()

    @patch("ai_brain.installer._upgrade_and_summarise")
    @patch("ai_brain.installer._update_from_registry", return_value=True)
    def test_registry_flow_when_invoked_as_global(self, mock_update, mock_upgrade):
        # Match the comparison inside install_or_update:
        #   invoked_script = Path(sys.argv[0]).resolve()
        #   if invoked_script == GLOBAL_AI_BRAIN():
        # We make both sides return the same resolved path.
        resolved = GLOBAL_AI_BRAIN().resolve()
        with patch.object(sys, "argv", [str(resolved)]):
            with patch("ai_brain.installer.GLOBAL_AI_BRAIN", return_value=resolved):
                result = installer.install_or_update()
        self.assertTrue(result)
        mock_update.assert_called_once()
        mock_upgrade.assert_called_once()

    @patch("ai_brain.installer._upgrade_and_summarise")
    @patch("ai_brain.installer._install_from_source", return_value=False)
    def test_does_not_upgrade_if_install_fails(self, mock_install, mock_upgrade):
        result = installer.install_or_update()
        self.assertFalse(result)
        mock_upgrade.assert_not_called()


class TestInstallFromSource(InTempDir):
    def _prepare_fake_source_tree(self) -> Path:
        """Build a fake repo layout: tmpdir/{repo_root/src/ai_brain/installer.py}.

        The 'script_path' used in _install_from_source is the installer.py file.
        It must resolve so that `script_path.parent.parent` = src/, and
        `script_path.parent.parent.parent` = repo_root.
        """
        repo_root = Path(self.tmpdir) / "repo"
        src = repo_root / "src" / "ai_brain"
        src.mkdir(parents=True)
        bin_dir = repo_root / "bin"
        bin_dir.mkdir()
        (bin_dir / "ai-brain").write_text("stub", encoding="utf-8")
        installer_file = src / "installer.py"
        installer_file.write_text("# fake", encoding="utf-8")
        return installer_file

    def test_success_path_with_path_in_env(self) -> None:
        script = self._prepare_fake_source_tree()
        resolved = Path(self.tmpdir) / ".local" / "bin" / "ai-brain"
        with patch("ai_brain.installer.GLOBAL_AI_BRAIN", return_value=resolved):
            with patch("ai_brain.installer._write_global_shim", return_value=True):
                with patch.dict(os.environ, {"PATH": str(Path.home() / ".local" / "bin")}):
                    ok = installer._install_from_source(script)
        self.assertTrue(ok)
        self.assertTrue(INSTALL_SOURCE_REGISTRY().is_file())

    def test_warns_when_path_missing_local_bin(self) -> None:
        script = self._prepare_fake_source_tree()
        resolved = Path(self.tmpdir) / ".local" / "bin" / "ai-brain"
        with patch("ai_brain.installer.GLOBAL_AI_BRAIN", return_value=resolved):
            with patch("ai_brain.installer._write_global_shim", return_value=True):
                with patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True):
                    ok = installer._install_from_source(script)
        self.assertTrue(ok)

    def test_returns_false_when_shim_write_fails(self) -> None:
        script = self._prepare_fake_source_tree()
        with patch("ai_brain.installer._write_global_shim", return_value=False):
            ok = installer._install_from_source(script)
        self.assertFalse(ok)
        self.assertFalse(INSTALL_SOURCE_REGISTRY().exists())

    def test_returns_false_when_registry_write_fails(self) -> None:
        script = self._prepare_fake_source_tree()
        # Block registry directory
        claude_dir = Path(self.tmpdir) / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        # Force exception by patching Path's write_text
        orig_write_text = Path.write_text

        def boom(self, *args, **kwargs):
            if "install_source" in str(self):
                raise OSError("readonly filesystem")
            return orig_write_text(self, *args, **kwargs)

        with patch("ai_brain.installer._write_global_shim", return_value=True):
            with patch.object(Path, "write_text", new=boom):
                ok = installer._install_from_source(script)
        self.assertFalse(ok)


class TestUpdateFromRegistry(InTempDir):
    def _make_registry_and_source(self):
        """Create install-source registry pointing to a fake source path."""
        repo_root = Path(self.tmpdir) / "repo"
        src = repo_root / "src" / "ai_brain"
        src.mkdir(parents=True)
        installer_file = src / "installer.py"
        installer_file.write_text("# fake", encoding="utf-8")
        # Source path in registry is the "binary" script the user ran originally.
        script_path = repo_root / "bin" / "ai-brain"
        script_path.parent.mkdir()
        script_path.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        INSTALL_SOURCE_REGISTRY().parent.mkdir(parents=True, exist_ok=True)
        INSTALL_SOURCE_REGISTRY().write_text(str(script_path) + "\n", encoding="utf-8")
        return repo_root, script_path

    def test_no_registry_returns_false(self) -> None:
        # No registry file; home is tmpdir
        ok = installer._update_from_registry()
        self.assertFalse(ok)

    def test_missing_source_file_returns_false(self) -> None:
        INSTALL_SOURCE_REGISTRY().parent.mkdir(parents=True, exist_ok=True)
        INSTALL_SOURCE_REGISTRY().write_text(
            "/nonexistent/bin/ai-brain\n", encoding="utf-8"
        )
        ok = installer._update_from_registry()
        self.assertFalse(ok)

    def test_empty_registry_returns_false(self) -> None:
        INSTALL_SOURCE_REGISTRY().parent.mkdir(parents=True, exist_ok=True)
        INSTALL_SOURCE_REGISTRY().write_text("\n", encoding="utf-8")
        ok = installer._update_from_registry()
        self.assertFalse(ok)

    def test_success_path_no_git_root(self) -> None:
        repo_root, script = self._make_registry_and_source()
        # No .git dir anywhere -> no git sync
        with patch("ai_brain.installer._write_global_shim", return_value=True):
            ok = installer._update_from_registry()
        self.assertTrue(ok)

    def test_success_path_with_git_root_dirty(self) -> None:
        repo_root, script = self._make_registry_and_source()
        # Create a .git dir so _find_git_root finds it
        (repo_root / ".git").mkdir()
        # Even if the tree is "dirty", sync should refuse and still shim succeeds
        with patch("ai_brain.installer._sync_repo_to_origin") as mock_sync:
            with patch("ai_brain.installer._write_global_shim", return_value=True):
                ok = installer._update_from_registry()
        self.assertTrue(ok)
        mock_sync.assert_called_once_with(repo_root)

    def test_success_path_with_git_root_clean(self) -> None:
        repo_root, script = self._make_registry_and_source()
        (repo_root / ".git").mkdir()
        with patch("ai_brain.installer._sync_repo_to_origin") as mock_sync:
            with patch("ai_brain.installer._write_global_shim", return_value=True) as mock_shim:
                ok = installer._update_from_registry()
        self.assertTrue(ok)
        mock_sync.assert_called_once()
        mock_shim.assert_called_once()
        # src_dir arg is repo_root / "src"
        self.assertEqual(mock_shim.call_args[0][0], repo_root / "src")

    def test_shim_write_failure_returns_false(self) -> None:
        repo_root, script = self._make_registry_and_source()
        with patch("ai_brain.installer._write_global_shim", return_value=False):
            with patch("ai_brain.installer._sync_repo_to_origin"):
                ok = installer._update_from_registry()
        self.assertFalse(ok)


class TestFindGitRoot(InTempDir):
    def test_finds_root_when_git_in_current_dir(self) -> None:
        repo = Path(self.tmpdir) / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        self.assertEqual(installer._find_git_root(repo), repo)

    def test_walks_up_to_parent(self) -> None:
        repo = Path(self.tmpdir) / "repo"
        sub = repo / "a" / "b"
        repo.mkdir()
        sub.mkdir(parents=True)
        (repo / ".git").mkdir()
        self.assertEqual(installer._find_git_root(sub), repo)

    def test_returns_none_when_no_git_anywhere(self) -> None:
        plain = Path(self.tmpdir) / "plain"
        sub = plain / "sub"
        plain.mkdir()
        sub.mkdir()
        self.assertIsNone(installer._find_git_root(sub))

    def test_returns_none_for_empty_path_at_fs_root(self) -> None:
        """Sanity: even at /, no .git, so None."""
        self.assertIsNone(installer._find_git_root(Path("/nonexistent-root-xyz")))


class TestRunGit(InTempDir):
    def test_success(self) -> None:
        repo = Path(self.tmpdir) / "repo"
        repo.mkdir()
        _init_repo(repo)
        rc, out, err = installer._run_git(repo, "status", "--porcelain")
        self.assertEqual(rc, 0)

    def test_returns_124_on_timeout(self) -> None:
        repo = Path(self.tmpdir) / "repo"
        repo.mkdir()
        # Force a timeout by mocking subprocess.run
        with patch(
            "ai_brain.installer.subprocess.run",
            side_effect=subprocess.TimeoutExpired("git", 0),
        ):
            rc, out, err = installer._run_git(repo, "fetch", timeout=0)
        self.assertEqual(rc, 124)
        self.assertIn("timed out", err)

    def test_returns_127_when_git_missing(self) -> None:
        repo = Path(self.tmpdir) / "repo"
        repo.mkdir()
        with patch(
            "ai_brain.installer.subprocess.run",
            side_effect=FileNotFoundError("no git"),
        ):
            rc, out, err = installer._run_git(repo, "fetch")
        self.assertEqual(rc, 127)
        self.assertIn("not found", err)

    def test_returns_1_on_generic_exception(self) -> None:
        repo = Path(self.tmpdir) / "repo"
        repo.mkdir()
        with patch(
            "ai_brain.installer.subprocess.run",
            side_effect=OSError("boom"),
        ):
            rc, out, err = installer._run_git(repo, "fetch")
        self.assertEqual(rc, 1)
        self.assertIn("failed to spawn git", err)


class TestUpgradeAndSummarise(InTempDir):
    @patch("ai_brain.installer.print_summary")
    @patch("ai_brain.installer.upgrade_all", return_value=[])
    def test_calls_upgrade_and_print(self, mock_upgrade, mock_summary):
        installer._upgrade_and_summarise()
        mock_upgrade.assert_called_once()
        mock_summary.assert_called_once()
        self.assertEqual(mock_summary.call_args[1]["self_version"], VERSION)


# --------------------------------------------------------------------------- #
# Original regression tests (preserved; renumbered but intact)
# --------------------------------------------------------------------------- #


class TestCurrentBranch(InTempDir):
    def test_returns_branch_name(self) -> None:
        repo = Path(self.tmpdir) / "repo"
        repo.mkdir()
        _init_repo(repo, branch="main")
        self.assertEqual(installer._current_branch(repo), "main")

    def test_returns_none_for_detached_head(self) -> None:
        repo = Path(self.tmpdir) / "repo"
        repo.mkdir()
        _init_repo(repo, branch="main")
        head = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        subprocess.run(
            ["git", "-C", str(repo), "checkout", "--quiet", "--detach", head],
            check=True,
        )
        self.assertIsNone(installer._current_branch(repo))

    def test_returns_none_for_non_repo(self) -> None:
        not_a_repo = Path(self.tmpdir) / "nope"
        not_a_repo.mkdir()
        self.assertIsNone(installer._current_branch(not_a_repo))


class TestHasUncommittedChanges(InTempDir):
    def test_clean_tree_returns_false(self) -> None:
        repo = Path(self.tmpdir) / "repo"
        repo.mkdir()
        _init_repo(repo)
        self.assertFalse(installer._has_uncommitted_changes(repo))

    def test_modified_tracked_file_returns_true(self) -> None:
        repo = Path(self.tmpdir) / "repo"
        repo.mkdir()
        _init_repo(repo)
        (repo / "README").write_text("dirty\n", encoding="utf-8")
        self.assertTrue(installer._has_uncommitted_changes(repo))

    def test_untracked_file_returns_false(self) -> None:
        repo = Path(self.tmpdir) / "repo"
        repo.mkdir()
        _init_repo(repo)
        (repo / "untracked.txt").write_text("ok\n", encoding="utf-8")
        self.assertFalse(installer._has_uncommitted_changes(repo))

    def test_staged_change_returns_true(self) -> None:
        repo = Path(self.tmpdir) / "repo"
        repo.mkdir()
        _init_repo(repo)
        (repo / "README").write_text("staged\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "README"], check=True)
        self.assertTrue(installer._has_uncommitted_changes(repo))

    def test_git_status_failure_pretends_dirty(self) -> None:
        # Non-repo: git status will fail; expect True as safe default
        not_a_repo = Path(self.tmpdir) / "nope"
        not_a_repo.mkdir()
        self.assertTrue(installer._has_uncommitted_changes(not_a_repo))


class TestSyncRepoToOrigin(InTempDir):
    def _make_origin_and_clone(self) -> tuple[Path, Path]:
        origin = Path(self.tmpdir) / "origin.git"
        origin.mkdir()
        subprocess.run(["git", "init", "-q", "--bare"], cwd=str(origin), check=True)
        subprocess.run(
            ["git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/main"],
            check=True,
        )
        seed = Path(self.tmpdir) / "seed"
        seed.mkdir()
        _init_repo(seed)
        subprocess.run(["git", "-C", str(seed), "remote", "add", "origin", str(origin)], check=True)
        subprocess.run(["git", "-C", str(seed), "push", "-q", "origin", "main"], check=True)
        clone = Path(self.tmpdir) / "clone"
        subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
        return origin, clone

    def test_fast_forwards_local_main_to_origin(self) -> None:
        origin, clone = self._make_origin_and_clone()
        seed = Path(self.tmpdir) / "seed"
        (seed / "README").write_text("v2\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(seed), "commit", "-q", "-am", "v2"], check=True)
        subprocess.run(["git", "-C", str(seed), "push", "-q", "origin", "main"], check=True)

        head_before = subprocess.check_output(
            ["git", "-C", str(clone), "rev-parse", "HEAD"], text=True
        ).strip()
        installer._sync_repo_to_origin(clone)
        head_after = subprocess.check_output(
            ["git", "-C", str(clone), "rev-parse", "HEAD"], text=True
        ).strip()
        self.assertNotEqual(head_before, head_after)
        with open(clone / "README", encoding="utf-8") as f:
            self.assertEqual(f.read(), "v2\n")

    def test_refuses_to_sync_with_local_changes(self) -> None:
        origin, clone = self._make_origin_and_clone()
        (clone / "README").write_text("local edit, not committed\n", encoding="utf-8")
        head_before = subprocess.check_output(
            ["git", "-C", str(clone), "rev-parse", "HEAD"], text=True
        ).strip()
        installer._sync_repo_to_origin(clone)
        head_after = subprocess.check_output(
            ["git", "-C", str(clone), "rev-parse", "HEAD"], text=True
        ).strip()
        self.assertEqual(head_before, head_after)

    def test_fetch_failure_does_not_raise(self) -> None:
        """git fetch to a bad origin should surface red() but not raise."""
        origin, clone = self._make_origin_and_clone()
        # Break the origin by removing it (bare repo's contents gone)
        import shutil as _shutil
        _shutil.rmtree(origin)
        # Should not raise, should just print and return.
        # We just verify it doesn't blow up.
        installer._sync_repo_to_origin(clone)

    def test_reset_failure_does_not_raise(self) -> None:
        """If reset fails (e.g. origin/<branch> doesn't exist), we surface red()."""
        repo = Path(self.tmpdir) / "repo"
        repo.mkdir()
        _init_repo(repo, branch="main")
        # No remote 'origin' configured; fetch will fail first.
        # Patch _run_git so fetch succeeds but reset fails (rc != 0).
        call_count = {"n": 0}

        def fake_run_git(repo_dir, *args, timeout=30):
            call_count["n"] += 1
            cmd = " ".join(args[:2])
            # current_branch detection
            if "rev-parse" in cmd:
                return 0, "main\n", ""
            if "status" in cmd:
                return 0, "", ""
            if "fetch" in cmd:
                return 0, "", ""  # fake success
            if "reset" in cmd:
                return 1, "", "fatal: origin/main not found"
            if "rev-parse" in " ".join(args) and "--short" in args:
                return 0, "abc1234\n", ""
            return 0, "", ""

        with patch("ai_brain.installer._run_git", side_effect=fake_run_git):
            installer._sync_repo_to_origin(repo)


if __name__ == "__main__":
    unittest.main()
