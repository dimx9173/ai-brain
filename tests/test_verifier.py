"""Unit tests for the generic MCP config checker."""
from __future__ import annotations

import json
import unittest
import unittest.mock
from pathlib import Path

from ai_brain.constants import MCP_GRAPHIFY, MCP_MEMPALACE
from ai_brain.verifier import check_mcp_config, FAIL, INFO, PASS


def _write(cfg: Path, payload: dict) -> None:
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(payload))


class TestMcpConfigChecker(unittest.TestCase):
    def test_returns_info_when_path_is_none(self) -> None:
        r = check_mcp_config("Test", None)
        self.assertEqual(r.status, INFO)

    def test_returns_fail_when_file_missing(self) -> None:
        r = check_mcp_config("Test", Path("/nonexistent/cfg.json"))
        self.assertEqual(r.status, FAIL)
        self.assertIn("找不到", r.detail)

    def test_returns_fail_when_malformed_json(self) -> None:
        target = Path("/tmp/_ai_brain_test_bad.json")
        target.write_text("{ broken")
        try:
            r = check_mcp_config("Test", target)
            self.assertEqual(r.status, FAIL)
            self.assertIn("JSON 格式損壞", r.detail)
        finally:
            target.unlink(missing_ok=True)

    def test_returns_fail_when_server_missing(self) -> None:
        target = Path("/tmp/_ai_brain_test_partial.json")
        _write(target, {"mcpServers": {MCP_MEMPALACE: {"command": "echo", "args": []}}})
        try:
            r = check_mcp_config("Test", target)
            self.assertEqual(r.status, FAIL)
            self.assertIn(MCP_GRAPHIFY, r.detail)
        finally:
            target.unlink(missing_ok=True)

    @unittest.mock.patch("ai_brain.verifier.subprocess.run")
    def test_returns_pass_for_well_formed_config(self, mock_run) -> None:
        mock_run.return_value = unittest.mock.MagicMock(returncode=0)
        target = Path("/tmp/_ai_brain_test_good.json")
        _write(target, {
            "mcpServers": {
                MCP_MEMPALACE: {"command": "mempalace-mcp", "args": []},
                MCP_GRAPHIFY: {"command": "python3", "args": ["-m", "graphify.serve"]},
            }
        })
        try:
            r = check_mcp_config("Test", target)
            self.assertEqual(r.status, PASS)
        finally:
            target.unlink(missing_ok=True)
