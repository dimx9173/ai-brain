"""Tests for `installer._sync_repo_to_origin` and the related git helpers.

These are the regression tests for the bug where `ai-brain update` did
`git pull origin master` — the wrong branch name, and stderr was
swallowed, so a stale local checkout could pass the update step without
actually moving to the new version.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from ai_brain import installer
from ai_brain._testing import InTempDir


def _init_repo(path: Path, branch: str = "main") -> None:
    """Create a git repo at *path* on *branch* with one initial commit."""
    subprocess.run(["git", "init", "-q", "-b", branch], cwd=str(path), check=True)
    _stamp_identity(path)
    (path / "README").write_text("v0\n", encoding="utf-8")
    subprocess.run(["git", "add", "README"], cwd=str(path), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(path), check=True)


def _stamp_identity(path: Path) -> None:
    """Set user.email + user.name on an existing git repo at *path*.

    Needed for repos created by `git clone` — cloning doesn't inherit
    the source repo's identity, and our tests disable the global git
    config, so the working tree would otherwise have no committer.
    """
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@test"], check=True
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "test"], check=True
    )


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
        # Untracked files don't block reset --hard; helper must ignore them.
        self.assertFalse(installer._has_uncommitted_changes(repo))

    def test_staged_change_returns_true(self) -> None:
        repo = Path(self.tmpdir) / "repo"
        repo.mkdir()
        _init_repo(repo)
        (repo / "README").write_text("staged\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "README"], check=True)
        self.assertTrue(installer._has_uncommitted_changes(repo))


class TestSyncRepoToOrigin(InTempDir):
    def _make_origin_and_clone(self) -> tuple[Path, Path]:
        """Create a bare 'origin' repo with one commit, plus a working clone.

        Returns (origin_dir, clone_dir). The clone is on `main`, one commit
        behind the origin (the origin has a v2 commit the clone doesn't
        know about).
        """
        origin = Path(self.tmpdir) / "origin.git"
        origin.mkdir()
        subprocess.run(["git", "init", "-q", "--bare"], cwd=str(origin), check=True)
        # A bare repo created with `git init --bare` has no HEAD symbolic
        # ref until the first push; without it `git clone` of this bare
        # repo warns and lands in an unborn state. Set it explicitly.
        subprocess.run(
            ["git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/main"],
            check=True,
        )

        seed = Path(self.tmpdir) / "seed"
        seed.mkdir()
        _init_repo(seed)
        subprocess.run(
            ["git", "-C", str(seed), "remote", "add", "origin", str(origin)], check=True
        )
        subprocess.run(["git", "-C", str(seed), "push", "-q", "origin", "main"], check=True)

        clone = Path(self.tmpdir) / "clone"
        subprocess.run(
            ["git", "clone", "-q", str(origin), str(clone)], check=True
        )
        # Sanity: clone is on main and at v0.
        return origin, clone

    def test_fast_forwards_local_main_to_origin(self) -> None:
        origin, clone = self._make_origin_and_clone()

        # Add a v2 commit to origin directly via the seed pattern.
        seed = Path(self.tmpdir) / "seed"
        (seed / "README").write_text("v2\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(seed), "commit", "-q", "-am", "v2"], check=True)
        subprocess.run(["git", "-C", str(seed), "push", "-q", "origin", "main"], check=True)

        # Sanity: clone still has v0.
        head_before = subprocess.check_output(
            ["git", "-C", str(clone), "rev-parse", "HEAD"], text=True
        ).strip()
        with open(clone / "README", encoding="utf-8") as f:
            self.assertEqual(f.read(), "v0\n")

        installer._sync_repo_to_origin(clone)

        head_after = subprocess.check_output(
            ["git", "-C", str(clone), "rev-parse", "HEAD"], text=True
        ).strip()
        self.assertNotEqual(head_before, head_after, "HEAD should have moved forward")
        with open(clone / "README", encoding="utf-8") as f:
            self.assertEqual(f.read(), "v2\n")

    def test_refuses_to_sync_with_local_changes(self) -> None:
        origin, clone = self._make_origin_and_clone()
        # Make a local edit that hasn't been committed.
        (clone / "README").write_text("local edit, not committed\n", encoding="utf-8")

        head_before = subprocess.check_output(
            ["git", "-C", str(clone), "rev-parse", "HEAD"], text=True
        ).strip()

        installer._sync_repo_to_origin(clone)

        head_after = subprocess.check_output(
            ["git", "-C", str(clone), "rev-parse", "HEAD"], text=True
        ).strip()
        # HEAD must NOT have moved; we explicitly refuse to destroy local work.
        self.assertEqual(head_before, head_after)
        # And the local edit is preserved.
        with open(clone / "README", encoding="utf-8") as f:
            self.assertIn("local edit", f.read())

    def test_uses_current_branch_not_hardcoded_master(self) -> None:
        """Regression: old code did `git pull origin master` regardless of
        the actual branch. Even if origin is `main`, the old code would
        have failed silently. Verify the new code uses the detected
        branch (here: a non-default branch named `develop`).
        """
        origin = Path(self.tmpdir) / "origin.git"
        origin.mkdir()
        subprocess.run(["git", "init", "-q", "--bare"], cwd=str(origin), check=True)
        subprocess.run(
            ["git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/main"],
            check=True,
        )

        seed = Path(self.tmpdir) / "seed"
        seed.mkdir()
        _init_repo(seed, branch="main")
        subprocess.run(
            ["git", "-C", str(seed), "remote", "add", "origin", str(origin)], check=True
        )
        subprocess.run(["git", "-C", str(seed), "push", "-q", "origin", "main"], check=True)

        clone = Path(self.tmpdir) / "clone"
        subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
        # Create the develop branch locally AND on origin so the test is
        # self-contained (we can't push a branch the remote doesn't have
        # yet on a bare repo with no UI for ref creation).
        subprocess.run(["git", "-C", str(clone), "checkout", "-q", "-b", "develop"], check=True)
        subprocess.run(["git", "-C", str(clone), "push", "-q", "-u", "origin", "develop"], check=True)

        # Now seed a new commit on develop in a parallel worktree-style
        # repo and push to origin's develop branch.
        seed2 = Path(self.tmpdir) / "seed2"
        subprocess.run(["git", "clone", "-q", str(origin), str(seed2)], check=True)
        _stamp_identity(seed2)
        subprocess.run(["git", "-C", str(seed2), "checkout", "-q", "develop"], check=True)
        (seed2 / "README").write_text("from develop\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(seed2), "commit", "-q", "-am", "dev"], check=True)
        subprocess.run(["git", "-C", str(seed2), "push", "-q", "origin", "develop"], check=True)

        installer._sync_repo_to_origin(clone)

        with open(clone / "README", encoding="utf-8") as f:
            self.assertEqual(f.read(), "from develop\n")


if __name__ == "__main__":
    unittest.main()
