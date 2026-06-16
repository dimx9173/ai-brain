"""Unit tests for the project registry + auto-archive whitelist."""
from __future__ import annotations

from pathlib import Path

from ai_brain import registry
from ai_brain._testing import InTempDir


class TestRegistry(InTempDir):
    def test_register_current_is_idempotent(self) -> None:
        self.assertTrue(registry.register_current())
        self.assertFalse(registry.register_current())
        self.assertEqual(len(registry.list_active()), 1)

    def test_enable_then_disable_archive(self) -> None:
        proj = registry.current_project_path()
        registry.register_current()
        self.assertTrue(registry.enable_archive(proj))
        self.assertTrue(registry.is_archived(proj))
        self.assertTrue(registry.disable_archive(proj))
        self.assertFalse(registry.is_archived(proj))

    def test_enable_archive_warns_when_already_enabled(self) -> None:
        proj = registry.current_project_path()
        registry.register_current()
        registry.enable_archive(proj)
        registry.enable_archive(proj)  # second call: no-op + warn
        # We don't capture stdout here, just confirm we don't double-add.
        self.assertEqual(len(registry.list_archived()), 1)

    def test_find_active_by_keyword(self) -> None:
        registry.register_current()
        found = registry.find_active_by_keyword(Path(self.tmpdir).name)
        self.assertEqual(found, registry.current_project_path())

    def test_find_active_by_index(self) -> None:
        registry.register_current()
        # 1-based: first project
        self.assertEqual(registry.find_active_by_index(1), registry.current_project_path())
        # Out of range returns None
        self.assertIsNone(registry.find_active_by_index(99))
        # Zero / negative is invalid (1-based)
        self.assertIsNone(registry.find_active_by_index(0))
        self.assertIsNone(registry.find_active_by_index(-1))

    def test_clear_then_archive_all(self) -> None:
        registry.register_current()
        self.assertTrue(registry.archive_all_active())
        self.assertEqual(len(registry.list_archived()), 1)
        self.assertTrue(registry.clear_archive())
        self.assertEqual(len(registry.list_archived()), 0)
