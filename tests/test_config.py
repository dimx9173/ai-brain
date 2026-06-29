"""Unit tests for the ConfigManager (JSON read/modify/write)."""
from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path
from unittest.mock import patch

from ai_brain.config import _atomic_write_text, modify_json_file
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


class TestTomlConfigManager(InTempDir):
    def test_parse_toml_basic(self) -> None:
        from ai_brain.config import parse_toml
        toml_str = """
        model_provider = "custom"
        disable_response_storage = true
        [model_providers.custom]
        name = "minimax_en"
        requires_openai_auth = false
        [mcp_servers.node_repl]
        args = []
        """
        parsed = parse_toml(toml_str)
        self.assertEqual(parsed.get("model_provider"), "custom")
        self.assertEqual(parsed.get("disable_response_storage"), True)
        self.assertEqual(parsed.get("model_providers", {}).get("custom", {}).get("name"), "minimax_en")
        self.assertEqual(parsed.get("model_providers", {}).get("custom", {}).get("requires_openai_auth"), False)
        self.assertEqual(parsed.get("mcp_servers", {}).get("node_repl", {}).get("args"), [])

    def test_serialize_toml_basic(self) -> None:
        from ai_brain.config import serialize_toml
        data = {
            "model_provider": "custom",
            "disable_response_storage": True,
            "model_providers": {
                "custom": {
                    "name": "minimax_en",
                }
            },
            "mcp_servers": {
                "node_repl": {
                    "args": []
                }
            }
        }
        serialized = serialize_toml(data)
        self.assertIn('model_provider = "custom"', serialized)
        self.assertIn('disable_response_storage = true', serialized)
        self.assertIn('[model_providers.custom]', serialized)
        self.assertIn('name = "minimax_en"', serialized)
        self.assertIn('[mcp_servers.node_repl]', serialized)
        self.assertIn('args = []', serialized)

    def test_modify_toml_file(self) -> None:
        from ai_brain.config import modify_toml_file, parse_toml
        target = Path(self.tmpdir) / "cfg.toml"
        
        # Test creation
        def modifier1(d: dict) -> dict:
            d["key"] = "value"
            return d
        
        result = modify_toml_file(target, modifier1)
        self.assertTrue(result)
        parsed = parse_toml(target.read_text())
        self.assertEqual(parsed, {"key": "value"})

        # Test merge
        def modifier2(d: dict) -> dict:
            d["another"] = 42
            return d
        
        result = modify_toml_file(target, modifier2)
        self.assertTrue(result)
        parsed = parse_toml(target.read_text())
        self.assertEqual(parsed, {"key": "value", "another": 42})


class TestAtomicWriteText(InTempDir):
    def test_no_leftover_tmp_after_write(self) -> None:
        target = Path(self.tmpdir) / "cfg.json"
        _atomic_write_text(target, '{"hello": "world"}')
        leftovers = list(Path(self.tmpdir).glob(".*.tmp"))
        self.assertEqual(leftovers, [])
        self.assertEqual(target.read_text(), '{"hello": "world"}')

    def test_preserves_file_permissions(self) -> None:
        target = Path(self.tmpdir) / "cfg.json"
        target.write_text("{}")
        os.chmod(str(target), 0o644)
        original_mode = stat.S_IMODE(os.stat(str(target)).st_mode)

        _atomic_write_text(target, '{"updated": true}')

        new_mode = stat.S_IMODE(os.stat(str(target)).st_mode)
        self.assertEqual(new_mode, original_mode)

    def test_cleans_up_tmp_on_failure(self) -> None:
        target = Path(self.tmpdir) / "cfg.json"

        with patch("os.replace", side_effect=PermissionError("denied during replace")):
            with self.assertRaises(PermissionError):
                _atomic_write_text(target, "content")

        leftovers = list(Path(self.tmpdir).glob(".*.tmp"))
        self.assertEqual(leftovers, [])

    def test_concurrent_writes_produce_valid_json(self) -> None:
        target = Path(self.tmpdir) / "concurrent.json"
        target.write_text("{}")
        errors = []
        iterations = 50

        def writer(i: int) -> None:
            try:
                for _ in range(iterations):
                    modify_json_file(target, lambda d, i=i: {**d, f"t{i}": i})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        final = json.loads(target.read_text())
        self.assertIsInstance(final, dict)
