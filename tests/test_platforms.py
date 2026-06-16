"""Unit tests for platform-specific path resolution."""
from __future__ import annotations

import platform
import unittest

from ai_brain.platforms import get_paths


class TestPlatforms(unittest.TestCase):
    def test_get_paths_returns_dataclass_with_expected_fields(self) -> None:
        paths = get_paths()
        self.assertTrue(hasattr(paths, "claude_json"))
        self.assertTrue(hasattr(paths, "mcp_json"))
        self.assertTrue(hasattr(paths, "kilo_cli"))

    def test_darwin_has_claude_desktop(self) -> None:
        orig_system = platform.system
        platform.system = lambda: "Darwin"  # type: ignore[assignment]
        try:
            paths = get_paths()
            self.assertIsNotNone(paths.claude_desktop)
            self.assertIn("Claude", str(paths.claude_desktop))
        finally:
            platform.system = orig_system  # type: ignore[assignment]

    def test_linux_has_no_claude_desktop(self) -> None:
        orig_system = platform.system
        platform.system = lambda: "Linux"  # type: ignore[assignment]
        try:
            paths = get_paths()
            self.assertIsNone(paths.claude_desktop)
            self.assertIsNotNone(paths.vscode_kilo)
            self.assertIn(".config", str(paths.vscode_kilo))
        finally:
            platform.system = orig_system  # type: ignore[assignment]
