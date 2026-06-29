"""Unit tests for git_hooks.py."""
from __future__ import annotations

import threading
import unittest
from pathlib import Path

from ai_brain import git_hooks
from ai_brain._testing import InTempDir
from ai_brain.constants import (
    HOOK_BEGIN_MARKER,
    HOOK_CHAIN,
    HOOK_END_MARKER,
    POST_CHECKOUT_TEMPLATE,
    POST_MERGE_TEMPLATE,
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
    def _format(self, template: str, name: str) -> str:
        return template.format(
            begin=HOOK_BEGIN_MARKER.format(name=name),
            end=HOOK_END_MARKER.format(name=name),
            marker_body="# (auto-managed by ai-brain; do not edit between markers)",
            chain=HOOK_CHAIN,
        )

    def test_post_checkout_uses_flock_instead_of_mkdir(self) -> None:
        content = self._format(POST_CHECKOUT_TEMPLATE, "post-checkout")
        self.assertIn(">/dev/null 2>&1 &", content)
        self.assertIn('if [ "$3" -eq 1 ]; then', content)
        self.assertIn("ai-brain-checkout.lock", content)
        self.assertIn("flock -n 8", content)
        self.assertNotIn("mkdir", content)
        self.assertNotIn("kill -0", content)
        self.assertNotIn("trap 'rm -rf", content)

    def test_post_merge_uses_flock(self) -> None:
        content = self._format(POST_MERGE_TEMPLATE, "post-merge")
        self.assertIn("ai-brain-post-merge.lock", content)
        self.assertIn("flock -n 9", content)
        self.assertIn(HOOK_CHAIN, content)
        self.assertIn("Git Pull 偵測", content)


class TestHooksPathClash(InTempDir):
    def setUp(self) -> None:
        super().setUp()
        Path(".git").mkdir(exist_ok=True)

    def test_install_warns_and_returns_false_when_custom_hooks_path(self) -> None:
        import unittest.mock as mock

        fake_result = mock.Mock(returncode=0, stdout=".husky/hooks\n")
        with mock.patch("ai_brain.git_hooks.subprocess.run", return_value=fake_result):
            with mock.patch("ai_brain.git_hooks.print_yellow") as warn:
                result = git_hooks.install()

        self.assertFalse(result)
        self.assertTrue(warn.called)
        self.assertIn("core.hooksPath", warn.call_args[0][0])
        self.assertIn(".husky/hooks", warn.call_args[0][0])

    def test_install_succeeds_when_hooks_path_unset(self) -> None:
        import unittest.mock as mock

        fake_result = mock.Mock(returncode=1, stdout="")
        with mock.patch("ai_brain.git_hooks.subprocess.run", return_value=fake_result):
            result = git_hooks.install()

        self.assertTrue(result)
        self.assertTrue((Path(".git") / "hooks" / "post-merge").is_file())

    def test_install_succeeds_when_hooks_path_equals_dotgit_hooks(self) -> None:
        import unittest.mock as mock

        fake_result = mock.Mock(returncode=0, stdout=".git/hooks\n")
        with mock.patch("ai_brain.git_hooks.subprocess.run", return_value=fake_result):
            result = git_hooks.install()

        self.assertTrue(result)


class TestAtomicHookInstall(InTempDir):
    def setUp(self) -> None:
        super().setUp()
        Path(".git").mkdir(exist_ok=True)

    def test_no_leftover_tmp_after_install(self) -> None:
        import unittest.mock as mock

        fake_result = mock.Mock(returncode=1, stdout="")
        with mock.patch("ai_brain.git_hooks.subprocess.run", return_value=fake_result):
            self.assertTrue(git_hooks.install())

        hooks_dir = Path(".git") / "hooks"
        leftovers = list(hooks_dir.glob(".*.tmp"))
        self.assertEqual(leftovers, [])

    def test_user_hook_preserved_under_concurrent_install(self) -> None:
        hooks_dir = Path(".git") / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        user_content = "#!/bin/bash\necho 'my user hook'\n"
        (hooks_dir / "post-checkout").write_text(user_content, encoding="utf-8")

        import unittest.mock as mock

        errors = []
        barrier = threading.Barrier(5)
        fake_result = mock.Mock(returncode=1, stdout="")

        with mock.patch("ai_brain.git_hooks.subprocess.run", return_value=fake_result):

            def do_install() -> None:
                try:
                    barrier.wait(timeout=5)
                    git_hooks.install()
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=do_install) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        self.assertEqual(errors, [])

        content = (hooks_dir / "post-checkout").read_text(encoding="utf-8")
        self.assertIn("echo 'my user hook'", content)
        self.assertIn(HOOK_BEGIN_MARKER.format(name="post-checkout"), content)
        occurrences = content.count(HOOK_BEGIN_MARKER.format(name="post-checkout"))
        self.assertEqual(occurrences, 1, "Managed section should appear exactly once")

    def test_install_appends_managed_section_via_atomic_write(self) -> None:
        hooks_dir = Path(".git") / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        (hooks_dir / "post-merge").write_text("#!/bin/bash\necho old\n", encoding="utf-8")

        import unittest.mock as mock

        fake_result = mock.Mock(returncode=1, stdout="")
        with mock.patch("ai_brain.git_hooks.subprocess.run", return_value=fake_result):
            self.assertTrue(git_hooks.install())

        content = (hooks_dir / "post-merge").read_text(encoding="utf-8")
        self.assertIn("echo old", content)
        self.assertIn(HOOK_CHAIN, content)
        leftovers = list(hooks_dir.glob(".*.tmp"))
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
