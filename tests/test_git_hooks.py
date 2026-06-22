"""Unit tests for git_hooks.py."""
from __future__ import annotations

import unittest
from pathlib import Path

from ai_brain import git_hooks
from ai_brain._testing import InTempDir
from ai_brain.constants import (
    HOOK_BEGIN_MARKER,
    HOOK_CHAIN,
    HOOK_END_MARKER,
    POST_CHECKOUT_TEMPLATE,
)


class TestHookInstallation(InTempDir):
    def setUp(self) -> None:
        super().setUp()
        Path(".git").mkdir(exist_ok=True)

    def test_install_creates_hooks_when_none_exist(self) -> None:
        self.assertTrue(git_hooks.install())

        hooks_dir = Path(".git") / "hooks"
        self.assertTrue((hooks_dir / "post-merge").is_file())
        self.assertTrue((hooks_dir / "post-checkout").is_file())

        post_merge = (hooks_dir / "post-merge").read_text(encoding="utf-8")
        self.assertIn("Git Pull 偵測", post_merge)
        self.assertIn(HOOK_CHAIN, post_merge)

        post_checkout = (hooks_dir / "post-checkout").read_text(encoding="utf-8")
        self.assertIn("Git Branch 切換偵測", post_checkout)
        self.assertIn(HOOK_CHAIN, post_checkout)

    def test_install_chains_into_existing_user_hook(self) -> None:
        hooks_dir = Path(".git") / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        existing = "#!/bin/bash\necho 'user hook'\n"
        (hooks_dir / "post-checkout").write_text(existing, encoding="utf-8")

        self.assertTrue(git_hooks.install())

        content = (hooks_dir / "post-checkout").read_text(encoding="utf-8")
        self.assertIn("echo 'user hook'", content)
        self.assertIn(HOOK_BEGIN_MARKER.format(name="post-checkout"), content)
        self.assertIn(HOOK_CHAIN, content)
        self.assertIn(HOOK_END_MARKER.format(name="post-checkout"), content)

    def test_install_updates_existing_managed_section(self) -> None:
        hooks_dir = Path(".git") / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)

        old_section = POST_CHECKOUT_TEMPLATE.format(
            begin=HOOK_BEGIN_MARKER.format(name="post-checkout"),
            end=HOOK_END_MARKER.format(name="post-checkout"),
            marker_body="# (auto-managed by ai-brain; do not edit between markers)",
            chain="echo old chain",
        )
        user_part = "#!/bin/bash\necho 'user hook'\n"
        (hooks_dir / "post-checkout").write_text(user_part + old_section, encoding="utf-8")

        self.assertTrue(git_hooks.install())

        content = (hooks_dir / "post-checkout").read_text(encoding="utf-8")
        self.assertIn("echo 'user hook'", content)
        self.assertNotIn("echo old chain", content)
        self.assertIn(HOOK_CHAIN, content)


class TestHookUninstallation(InTempDir):
    def test_uninstall_removes_only_managed_section(self) -> None:
        hooks_dir = Path(".git") / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)

        section = POST_CHECKOUT_TEMPLATE.format(
            begin=HOOK_BEGIN_MARKER.format(name="post-checkout"),
            end=HOOK_END_MARKER.format(name="post-checkout"),
            marker_body="# (auto-managed by ai-brain; do not edit between markers)",
            chain=HOOK_CHAIN,
        )
        user_part = "#!/bin/bash\necho 'user hook'\n"
        (hooks_dir / "post-checkout").write_text(user_part + section, encoding="utf-8")

        self.assertTrue(git_hooks.uninstall())

        content = (hooks_dir / "post-checkout").read_text(encoding="utf-8")
        self.assertIn("echo 'user hook'", content)
        self.assertNotIn(HOOK_BEGIN_MARKER.format(name="post-checkout"), content)
        self.assertNotIn(HOOK_CHAIN, content)

    def test_uninstall_deletes_hook_when_only_managed_content(self) -> None:
        hooks_dir = Path(".git") / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)

        section = POST_CHECKOUT_TEMPLATE.format(
            begin=HOOK_BEGIN_MARKER.format(name="post-checkout"),
            end=HOOK_END_MARKER.format(name="post-checkout"),
            marker_body="# (auto-managed by ai-brain; do not edit between markers)",
            chain=HOOK_CHAIN,
        )
        (hooks_dir / "post-checkout").write_text(section, encoding="utf-8")

        self.assertTrue(git_hooks.uninstall())
        self.assertFalse((hooks_dir / "post-checkout").exists())


class TestHookTemplates(unittest.TestCase):
    def test_post_checkout_runs_chain_in_background_with_lock(self) -> None:
        content = POST_CHECKOUT_TEMPLATE.format(
            begin=HOOK_BEGIN_MARKER.format(name="post-checkout"),
            end=HOOK_END_MARKER.format(name="post-checkout"),
            marker_body="# (auto-managed by ai-brain; do not edit between markers)",
            chain=HOOK_CHAIN,
        )
        self.assertIn(">/dev/null 2>&1 &", content)
        self.assertIn('if [ "$3" -eq 1 ]; then', content)
        self.assertIn("ai-brain-checkout.lock", content)
        self.assertIn('mkdir "$LOCK_DIR"', content)
        self.assertIn("trap 'rm -rf", content)


if __name__ == "__main__":
    unittest.main()
