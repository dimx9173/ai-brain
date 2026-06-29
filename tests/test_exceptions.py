"""Tests for ai_brain.structured exceptions."""
from __future__ import annotations

import unittest

from ai_brain.exceptions import (
    AiBrainError,
    ConfigWriteError,
    RegistryError,
    SubprocessError,
    ToolNotFoundError,
)


class TestToolNotFoundError(unittest.TestCase):
    def test_message_with_hint(self):
        e = ToolNotFoundError("mempalace", "uv tool install mempalace")
        self.assertIn("mempalace", str(e))
        self.assertIn("uv tool install", str(e))
        self.assertEqual(e.tool, "mempalace")
        self.assertEqual(e.install_hint, "uv tool install mempalace")

    def test_message_without_hint(self):
        e = ToolNotFoundError("some-tool")
        self.assertIn("some-tool", str(e))
        self.assertNotIn("install with", str(e))
        self.assertEqual("", e.install_hint)

    def test_inherits_from_aibrain_error(self):
        e = ToolNotFoundError("x")
        self.assertIsInstance(e, AiBrainError)
        self.assertIsInstance(e, Exception)


class TestConfigWriteError(unittest.TestCase):
    def test_message_with_reason(self):
        e = ConfigWriteError("/tmp/x.json", "PermissionError")
        self.assertIn("/tmp/x.json", str(e))
        self.assertIn("PermissionError", str(e))
        self.assertEqual(e.path, "/tmp/x.json")
        self.assertEqual(e.reason, "PermissionError")

    def test_message_without_reason(self):
        e = ConfigWriteError("/tmp/y.json")
        self.assertNotIn("()", str(e))


class TestRegistryError(unittest.TestCase):
    def test_message_with_reason(self):
        e = RegistryError("write", "/tmp/registry.txt", "disk full")
        self.assertIn("write", str(e))
        self.assertIn("registry.txt", str(e))
        self.assertIn("disk full", str(e))

    def test_message_without_reason(self):
        e = RegistryError("read", "/tmp/reg.txt")
        self.assertNotIn("disk full", str(e))


class TestSubprocessError(unittest.TestCase):
    def test_message_with_rc_and_stderr(self):
        e = SubprocessError(["git", "status"], returncode=128, stderr="fatal: not a repo")
        self.assertIn("git status", str(e))
        self.assertIn("128", str(e))
        self.assertIn("fatal", str(e))
        self.assertEqual(e.cmd, ["git", "status"])
        self.assertEqual(e.returncode, 128)

    def test_message_with_no_rc(self):
        e = SubprocessError(["ls"])
        self.assertEqual(e.returncode, -1)
        self.assertIn("ls", str(e))


class TestExceptionChaining(unittest.TestCase):
    def test_can_raise_and_catch_as_aibrain_error(self):
        with self.assertRaises(AiBrainError):
            raise ToolNotFoundError("test")

    def test_specific_exception_classes_catchable(self):
        for exc_cls, args in [
            (ToolNotFoundError, ("test",)),
            (ConfigWriteError, ("/x",)),
            (RegistryError, ("op", "/x")),
            (SubprocessError, (["ls"],)),
        ]:
            try:
                raise exc_cls(*args)
            except AiBrainError:
                pass
            else:
                self.fail(f"{exc_cls.__name__} should inherit from AiBrainError")


if __name__ == "__main__":
    unittest.main()
