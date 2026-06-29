"""Unit tests for the project registry + auto-archive whitelist."""
from __future__ import annotations

import os
import threading
from pathlib import Path
from unittest.mock import patch

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

    def test_deregister_project(self) -> None:
        proj = registry.current_project_path()
        registry.register_current()
        registry.enable_archive(proj)
        self.assertIn(proj, registry.list_active())
        self.assertIn(proj, registry.list_archived())

        self.assertTrue(registry.deregister_project(proj))
        self.assertNotIn(proj, registry.list_active())
        self.assertNotIn(proj, registry.list_archived())

    def test_deregister_all_projects(self) -> None:
        proj = registry.current_project_path()
        registry.register_current()
        registry.enable_archive(proj)

        self.assertTrue(registry.deregister_all_projects())
        self.assertEqual(len(registry.list_active()), 0)
        self.assertEqual(len(registry.list_archived()), 0)


class TestRegistryErrorPaths(InTempDir):
    def test_read_lines_returns_empty_on_open_error(self) -> None:
        tmp = Path(self.tmpdir) / "registry.txt"
        tmp.write_text("data\n", encoding="utf-8")
        with patch("builtins.open", side_effect=PermissionError("denied")):
            result = registry._read_lines(tmp)
        self.assertEqual(result, [])

    def test_write_lines_returns_false_on_open_error(self) -> None:
        target = Path(self.tmpdir) / "new_reg.txt"
        with patch("ai_brain.registry.os.open", side_effect=PermissionError("denied")):
            result = registry._write_lines(target, ["data"])
        self.assertFalse(result)

    def test_append_line_returns_false_on_open_error(self) -> None:
        target = Path(self.tmpdir) / "new_reg.txt"
        with patch("builtins.open", side_effect=PermissionError("denied")):
            result = registry._append_line(target, "data")
        self.assertFalse(result)

    def test_register_current_append_failure_returns_false(self) -> None:
        with patch("ai_brain.registry._append_line", return_value=False):
            self.assertFalse(registry.register_current())

    def test_deregister_all_projects_write_failure(self) -> None:
        registry.register_current()
        with patch("ai_brain.registry._write_lines", return_value=False):
            self.assertFalse(registry.deregister_all_projects())

    def test_enable_archive_append_failure_returns_false(self) -> None:
        with patch("ai_brain.registry._append_line", return_value=False):
            self.assertFalse(registry.enable_archive("/some/path"))

    def test_disable_archive_not_in_list_returns_true(self) -> None:
        self.assertTrue(registry.disable_archive("/not/registered"))

    def test_disable_archive_write_failure_returns_false(self) -> None:
        proj = registry.current_project_path()
        registry.enable_archive(proj)
        with patch("ai_brain.registry._write_lines", return_value=False):
            self.assertFalse(registry.disable_archive(proj))

    def test_clear_archive_write_failure_returns_false(self) -> None:
        with patch("ai_brain.registry._write_lines", return_value=False):
            self.assertFalse(registry.clear_archive())

    def test_archive_all_active_write_failure_returns_false(self) -> None:
        registry.register_current()
        with patch("ai_brain.registry._write_lines", return_value=False):
            self.assertFalse(registry.archive_all_active())


class TestRegisterCurrentConcurrent(InTempDir):
    def test_concurrent_register_no_duplicates(self) -> None:
        errors = []
        barrier = threading.Barrier(10)

        def register_once() -> None:
            try:
                barrier.wait(timeout=5)
                registry.register_current()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=register_once) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        active = registry.list_active()
        proj = registry.current_project_path()
        count = active.count(proj)
        self.assertEqual(count, 1, f"Expected 1 entry but found {count}: {active}")

    def test_write_lines_is_atomic_no_leftover_tmp(self) -> None:
        target = Path(self.tmpdir) / "nested" / "reg.txt"
        registry._write_lines(target, ["/a", "/b"])
        leftovers = list((target.parent).glob(".*.tmp"))
        self.assertEqual(leftovers, [])
        self.assertEqual(registry._read_lines(target), ["/a", "/b"])


class TestRegistryConcurrency(InTempDir):
    """Verify that register_current and deregister_project serialize."""

    def test_concurrent_register_and_deregister(self) -> None:
        """Run 10 threads that each register_current, and 5 threads that each
        deregister_project. After all threads complete, no duplicate entries
        should appear in the registry."""
        tmp = Path(self.tmpdir).resolve()
        for i in range(5):
            (tmp / f"proj_{i}").mkdir()
            os.chdir(str(tmp / f"proj_{i}"))
            registry.register_current()

        errors: list[Exception] = []
        barrier = threading.Barrier(15)

        def do_register(idx: int) -> None:
            try:
                barrier.wait(timeout=5)
                (tmp / f"proj_new_{idx}").mkdir(exist_ok=True)
                os.chdir(str(tmp / f"proj_new_{idx}"))
                registry.register_current()
            except Exception as e:
                errors.append(e)

        def do_deregister(idx: int) -> None:
            try:
                barrier.wait(timeout=5)
                os.chdir(str(tmp / f"proj_{idx}"))
                registry.deregister_project(str(tmp / f"proj_{idx}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=do_register, args=(i,)) for i in range(10)]
        threads += [threading.Thread(target=do_deregister, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        entries = registry._read_lines(registry.REGISTRY_PATH())
        self.assertEqual(len(entries), len(set(entries)))

    def test_deregister_does_not_lose_registered_projects(self) -> None:
        """A concurrent register should survive a concurrent deregister that
        replaces the file. After register("projC") + deregister("projB")
        concurrently, projA and projC must both be present."""
        tmp = Path(self.tmpdir).resolve()
        (tmp / "projA").mkdir()
        (tmp / "projB").mkdir()
        os.chdir(str(tmp / "projA"))
        registry.register_current()
        os.chdir(str(tmp / "projB"))
        registry.register_current()

        (tmp / "projC").mkdir()
        errors: list[Exception] = []

        def register_c() -> None:
            try:
                os.chdir(str(tmp / "projC"))
                registry.register_current()
            except Exception as e:
                errors.append(e)

        def deregister_b() -> None:
            try:
                registry.deregister_project(str(tmp / "projB"))
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=register_c)
        t2 = threading.Thread(target=deregister_b)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(errors, [])
        entries = registry._read_lines(registry.REGISTRY_PATH())
        self.assertIn(str(tmp / "projA"), entries)
        self.assertIn(str(tmp / "projC"), entries)
        self.assertNotIn(str(tmp / "projB"), entries)
