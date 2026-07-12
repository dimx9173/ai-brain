"""Unit tests for global config and unstructured document pipeline"""
from __future__ import annotations

import json
import os
import shutil
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_brain import commands, registry
from ai_brain._testing import InTempDir
from ai_brain.config import ensure_global_config, modify_toml_file, parse_toml, serialize_toml
from ai_brain.platforms import ToolPaths


class TestGlobalConfigAndPipeline(InTempDir):
    def setUp(self):
        super().setUp()
        self.tmpdir_path = Path(self.tmpdir)
        # Mock HOME to be inside our temp dir
        self._home_patch = patch("ai_brain.constants.HOME", return_value=self.tmpdir_path)
        self._home_patch.start()
        
        self.global_config_path = self.tmpdir_path / ".config" / "ai-brain" / "config.toml"
        self.mock_paths = ToolPaths(
            gemini_config=self.tmpdir_path / "gemini.json",
            gemini_antigravity=self.tmpdir_path / "antigravity.json",
            mcp_json=self.tmpdir_path / "mcp.json",
            claude_json=self.tmpdir_path / "claude.json",
            claude_desktop=self.tmpdir_path / "claude_desktop.json",
            vscode_kilo=self.tmpdir_path / "kilo.json",
            kilo_cli=self.tmpdir_path / "kilo_cli.json",
            opencode_json=self.tmpdir_path / "opencode.json",
            cursor_json=self.tmpdir_path / "cursor.json",
            codex_toml=self.tmpdir_path / "codex.toml",
            openclaw_config=self.tmpdir_path / "openclaw.json",
            global_config=self.global_config_path,
        )

    def tearDown(self):
        self._home_patch.stop()
        super().tearDown()

    def test_ensure_global_config_creates_default(self):
        # 1. Config doesn't exist
        self.assertFalse(self.global_config_path.is_file())
        data = ensure_global_config(self.global_config_path)
        
        # 2. Created default
        self.assertTrue(self.global_config_path.is_file())
        self.assertIn("preferences", data)
        self.assertIn("coding_style", data["preferences"])
        self.assertEqual(data["preferences"]["coding_style"], "PEP 8 for Python, standard formatting for Go")

    def test_run_config_set_and_get(self):
        # Initialize
        ensure_global_config(self.global_config_path)
        
        # 1. Test set coding style
        args = MagicMock(action="global", config_list=False, config_set="coding_style=Google Python Style")
        res = commands.run_config(self.mock_paths, args)
        self.assertTrue(res)
        
        # Verify
        data = ensure_global_config(self.global_config_path)
        self.assertEqual(data["preferences"]["coding_style"], "Google Python Style")

        # 2. Test set frameworks (list parsing)
        args = MagicMock(action="global", config_list=False, config_set="preferred_frameworks=['Flask', 'Django']")
        res = commands.run_config(self.mock_paths, args)
        self.assertTrue(res)

        data = ensure_global_config(self.global_config_path)
        self.assertEqual(data["preferences"]["preferred_frameworks"], ["Flask", "Django"])

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_parse_unstructured_documents(self, mock_which, mock_run):
        # Mock markitdown as available
        def which_mock(cmd):
            if cmd == "markitdown":
                return "/usr/local/bin/markitdown"
            return None
        mock_which.side_effect = which_mock

        # Setup mock document
        docs_dir = self.tmpdir_path / "docs"
        docs_dir.mkdir()
        pdf_file = docs_dir / "design.pdf"
        pdf_file.write_text("dummy pdf contents", encoding="utf-8")

        # Run parser
        commands._parse_unstructured_documents()

        # Check that subprocess.run was called with markitdown
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "markitdown")
        self.assertEqual(Path(args[1]).resolve(), pdf_file.resolve())
        self.assertEqual(args[2], "-o")
        self.assertEqual(Path(args[3]).resolve(), (self.tmpdir_path / ".ai-brain" / "parsed-docs" / "docs" / "design.pdf.md").resolve())

    def test_fix_claude_md_preference_injection(self):
        # Setup global config preferences
        ensure_global_config(self.global_config_path)
        
        # Custom rule
        args = MagicMock(action="global", config_list=False, config_set="custom_rules=['Rule A', 'Rule B']")
        commands.run_config(self.mock_paths, args)

        # Create target CLAUDE.md
        claude_md = self.tmpdir_path / "CLAUDE.md"
        claude_md.write_text("## Initial Section\nSome text\n", encoding="utf-8")

        # Run fix
        res = commands._fix_claude_md("Test CLAUDE.md", claude_md, self.mock_paths, fix=True)
        self.assertTrue(res)

        # Verify
        content = claude_md.read_text(encoding="utf-8")
        self.assertIn("## 🧠 Layered Memory & Cognitive Workflow", content)
        self.assertIn("## 🎨 Global Developer Preferences", content)
        self.assertIn("Rule A", content)
        self.assertIn("Rule B", content)
