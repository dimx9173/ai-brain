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


if __name__ == "__main__":
    unittest.main()
