"""Tests for the shell completion-script generator (`ai_brain.completions`).

The completion module is pure-stdlib on purpose (the project ships with
zero runtime deps). These tests cover:

* Subcommand enumeration is sourced from `cli.COMMANDS` (single source
  of truth — adding a new subcommand to cli.py automatically extends
  completion candidates).
* Each generated script contains the expected shell hooks.
* The `__complete-patterns` helper returns static tokens + active
  project basenames + 1-based indices.
* The install / uninstall cycle writes and removes the right file in
  the user's home directory.
"""
from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from ai_brain import completions
from ai_brain._testing import InTempDir


class TestSubcommandEnumeration(unittest.TestCase):
    """The completion candidates must track `cli.COMMANDS` automatically."""

    def test_visible_commands_excludes_version_aliases(self) -> None:
        names = completions._visible_commands()
        # Real subcommands only — no '-v' / '--version'.
        self.assertNotIn("-v", names)
        self.assertNotIn("--version", names)
        # Sanity: the known commands are present.
        for required in ("init", "include", "exclude", "completions", "version"):
            self.assertIn(required, names)


class TestPatternHelper(InTempDir):
    """`__complete-patterns` is what the shell calls at <TAB> time."""

    def test_static_tokens_always_present(self) -> None:
        out = completions.complete_patterns()
        for token in ("all", "current", "."):
            self.assertIn(token, out)

    def test_indices_match_active_count(self) -> None:
        from ai_brain import registry
        registry.register_current()
        out = completions.complete_patterns()
        n = len(registry.list_active())
        for i in range(1, n + 1):
            self.assertIn(str(i), out)
        # And nothing past the end.
        self.assertNotIn(str(n + 1), out)

    def test_basenames_of_active_projects_included(self) -> None:
        from ai_brain import registry
        registry.register_current()
        out = completions.complete_patterns()
        # The current project is something like 'tmpXXXX' (basename of the
        # tempdir); we just check the pattern candidates contain a basename
        # of every active path.
        for proj in registry.list_active():
            self.assertIn(Path(proj).name, out)

    def test_dedupes_basename(self) -> None:
        from ai_brain import registry
        # Two projects with the same basename → only one candidate.
        target = registry.REGISTRY_PATH()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("/foo/myapp\n/bar/myapp\n", encoding="utf-8")
        out = completions.complete_patterns()
        # 'myapp' should appear exactly once.
        self.assertEqual(out.count("myapp"), 1)

    def test_handles_registry_failure_gracefully(self) -> None:
        # If list_active() raises, the helper must still return the
        # static tokens so completion never silently disappears.
        with mock.patch(
            "ai_brain.registry.list_active",
            side_effect=OSError("home dir missing"),
        ):
            out = completions.complete_patterns()
        self.assertEqual(out, ["all", "current", "."])


class TestScriptRendering(unittest.TestCase):
    """Each shell script must contain the expected hooks."""

    def test_bash_uses_complete_builtin(self) -> None:
        script = completions.bash_script()
        self.assertIn("complete -F _ai_brain ai-brain", script)
        # Should include the live-candidates helper call.
        self.assertIn("__complete-patterns", script)

    def test_zsh_uses_compdef(self) -> None:
        script = completions.zsh_script()
        self.assertIn("#compdef ai-brain", script)
        self.assertIn("__complete-patterns", script)

    def test_fish_uses_complete_builtin(self) -> None:
        script = completions.fish_script()
        self.assertIn("complete -c ai-brain", script)
        self.assertIn("__ai_brain_patterns", script)

    def test_render_dispatches_by_shell(self) -> None:
        self.assertIn("complete -F", completions.render("bash"))
        self.assertIn("#compdef", completions.render("zsh"))
        self.assertIn("complete -c", completions.render("fish"))

    def test_render_rejects_unknown_shell(self) -> None:
        with self.assertRaises(ValueError):
            completions.render("powershell")

    def test_all_subcommands_appear_in_bash_script(self) -> None:
        script = completions.bash_script()
        for cmd in completions._visible_commands():
            self.assertIn(cmd, script)


class TestInstallUninstall(InTempDir):
    """Install writes the file; uninstall removes it; both are idempotent."""

    def _target(self, shell: str) -> Path:
        return completions._install_targets()[shell][0]

    def test_install_bash_then_uninstall(self) -> None:
        target = self._target("bash")
        # InTempDir stubs Path.home() — so we can predict the target path.
        self.assertFalse(target.exists())
        written = completions.install("bash")
        self.assertEqual(written, [target])
        self.assertTrue(target.exists())
        self.assertIn("complete -F _ai_brain", target.read_text(encoding="utf-8"))

        removed = completions.uninstall("bash")
        self.assertEqual(removed, [target])
        self.assertFalse(target.exists())

    def test_uninstall_when_nothing_installed_is_noop(self) -> None:
        # No file present; uninstall should not raise.
        target = self._target("zsh")
        self.assertFalse(target.exists())
        removed = completions.uninstall("zsh")
        self.assertEqual(removed, [])

    def test_install_all_shells(self) -> None:
        written = completions.install()  # shell=None → all
        self.assertEqual(len(written), 3)  # bash, zsh, fish
        for p in written:
            self.assertTrue(p.exists())
        # Clean up so we don't pollute the real home dir.
        for p in written:
            p.unlink()


class TestCompletionsMainEntryPoint(unittest.TestCase):
    """The CLI subcommand `ai-brain completions ...` delegates here."""

    def test_show_bash_prints_to_stdout(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = completions.main(["show", "bash"])
        self.assertEqual(rc, 0)
        self.assertIn("complete -F _ai_brain", buf.getvalue())

    def test_show_requires_shell(self) -> None:
        import sys
        buf = io.StringIO()
        with redirect_stdout(buf), mock.patch("sys.stderr", io.StringIO()) as fake_err:
            rc = completions.main(["show"])
        self.assertEqual(rc, 2)
        self.assertIn("error", fake_err.getvalue())

    def test_internal_complete_patterns_prints_one_per_line(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = completions.main(["__complete-patterns"])
        self.assertEqual(rc, 0)
        lines = [l for l in buf.getvalue().splitlines() if l]
        # At minimum the three static tokens are present.
        for token in ("all", "current", "."):
            self.assertIn(token, lines)

    def test_unknown_action_returns_error(self) -> None:
        rc = completions.main(["bogus"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
