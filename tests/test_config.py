"""Unit tests for the ConfigManager (JSON read/modify/write)."""
from __future__ import annotations

import json
from pathlib import Path

from ai_brain.config import modify_json_file
from ai_brain._testing import InTempDir


class TestConfigManager(InTempDir):
    def test_creates_file_with_modified_contents(self) -> None:
        target = Path(self.tmpdir) / "nested" / "cfg.json"
        result = modify_json_file(target, lambda d: {**d, "k": "v"})
        self.assertTrue(result)
        self.assertEqual(json.loads(target.read_text()), {"k": "v"})

    def test_merges_into_existing_dict(self) -> None:
        target = Path(self.tmpdir) / "cfg.json"
        target.write_text(json.dumps({"a": 1, "b": 2}))

        def modifier(data: dict) -> dict:
            data["c"] = 3
            return data

        modify_json_file(target, modifier)
        self.assertEqual(json.loads(target.read_text()), {"a": 1, "b": 2, "c": 3})

    def test_recovers_from_malformed_json(self) -> None:
        target = Path(self.tmpdir) / "cfg.json"
        target.write_text("{not valid json")

        result = modify_json_file(target, lambda d: {"reset": True})

        self.assertTrue(result)
        self.assertEqual(json.loads(target.read_text()), {"reset": True})

    def test_returns_false_when_path_is_none(self) -> None:
        self.assertFalse(modify_json_file(None, lambda d: d))
