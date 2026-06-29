"""Comprehensive unit tests for the mcp module."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest import mock

from ai_brain._testing import InTempDir
from ai_brain.constants import (
    MEMPALACE_MCP_COMMAND,
    MCP_CODEBASE_MEMORY,
    MCP_MEMPALACE,
)
from ai_brain.mcp import (
    RegistrationTarget,
    _all_targets,
    _claude_code_entry,
    _claude_desktop_entry,
    _codex_entry,
    _deregister_in_file,
    _kilo_cli_entry,
    _kilo_local_entry,
    _openclaw_entry,
    _opencode_entry,
    _register_in_file,
    _stdio_server_entry,
    deregister_all,
    register_all,
)
from ai_brain.platforms import ToolPaths, get_paths


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ALL_SERVERS = (MCP_MEMPALACE, MCP_CODEBASE_MEMORY)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _minimal_paths() -> ToolPaths:
    """ToolPaths with all files pointing inside self.tmpdir so parent dirs exist."""
    base = Path.home()
    paths = get_paths()
    # Replace claude_desktop / vscode_kilo if None (Linux) with concrete paths
    return replace(
        paths,
        gemini_config=base / ".gemini" / "config" / "mcp_config.json",
        gemini_antigravity=base / ".gemini" / "antigravity" / "mcp_config.json",
        mcp_json=base / ".mcp.json",
        claude_json=base / ".claude.json",
        claude_desktop=base / ".claude_desktop.json",
        vscode_kilo=base / ".vscode_kilo.json",
        kilo_cli=base / ".config" / "kilo" / "kilo.json",
        opencode_json=base / ".config" / "opencode" / "opencode.json",
        cursor_json=base / ".cursor" / "mcp.json",
        codex_toml=base / ".codex" / "config.toml",
        openclaw_config=base / ".openclaw" / "config.json",
    )


# ---------------------------------------------------------------------------
# Entry builders
# ---------------------------------------------------------------------------

class TestStdioServerEntry(InTempDir):
    def test_mempalace(self):
        entry = _stdio_server_entry(MCP_MEMPALACE)
        self.assertEqual(entry["command"], "mempalace-mcp")
        self.assertEqual(entry["args"], [])
        self.assertEqual(entry["env"], {})

    def test_codebase_memory(self):
        entry = _stdio_server_entry(MCP_CODEBASE_MEMORY)
        self.assertIn("codebase-memory-mcp", entry["command"])
        self.assertEqual(entry["args"], [])

    def test_unknown_server(self):
        with self.assertRaises(ValueError):
            _stdio_server_entry("nonexistent-server")


class TestKiloLocalEntry(InTempDir):
    def test_mempalace(self):
        entry = _kilo_local_entry(MCP_MEMPALACE)
        expected = MEMPALACE_MCP_COMMAND()
        self.assertEqual(entry["type"], "local")
        self.assertEqual(entry["command"], expected[0])
        self.assertEqual(entry["args"], expected[1:])
        self.assertTrue(entry["enabled"])

    def test_codebase_memory(self):
        entry = _kilo_local_entry(MCP_CODEBASE_MEMORY)
        self.assertEqual(entry["type"], "local")
        self.assertIn("codebase-memory-mcp", entry["command"])
        self.assertTrue(entry["enabled"])

    def test_unknown_server(self):
        with self.assertRaises(ValueError):
            _kilo_local_entry("nonexistent")


class TestKiloCliEntry(InTempDir):
    def test_mempalace(self):
        entry = _kilo_cli_entry(MCP_MEMPALACE)
        self.assertEqual(entry["type"], "local")
        self.assertIsInstance(entry["command"], list)
        self.assertEqual(entry["command"], MEMPALACE_MCP_COMMAND())

    def test_codebase_memory(self):
        entry = _kilo_cli_entry(MCP_CODEBASE_MEMORY)
        self.assertIn("codebase-memory-mcp", entry["command"][0])

    def test_unknown_server(self):
        with self.assertRaises(ValueError):
            _kilo_cli_entry("bogus")


class TestClaudeDesktopEntry(InTempDir):
    def test_mempalace(self):
        entry = _claude_desktop_entry(MCP_MEMPALACE)
        expected = MEMPALACE_MCP_COMMAND()
        self.assertNotIn("type", entry)
        self.assertEqual(entry["command"], expected[0])
        self.assertEqual(entry["args"], expected[1:])
        self.assertEqual(entry["env"], {})

    def test_codebase_memory(self):
        entry = _claude_desktop_entry(MCP_CODEBASE_MEMORY)
        self.assertIn("codebase-memory-mcp", entry["command"])

    def test_unknown_server(self):
        with self.assertRaises(ValueError):
            _claude_desktop_entry("unknown")


class TestClaudeCodeEntry(InTempDir):
    def test_mempalace(self):
        entry = _claude_code_entry(MCP_MEMPALACE)
        self.assertEqual(entry["type"], "stdio")
        self.assertEqual(entry["command"], "mempalace-mcp")
        self.assertEqual(entry["args"], [])
        self.assertEqual(entry["env"], {})

    def test_codebase_memory(self):
        entry = _claude_code_entry(MCP_CODEBASE_MEMORY)
        self.assertEqual(entry["type"], "stdio")
        self.assertIn("codebase-memory-mcp", entry["command"])

    def test_unknown_server(self):
        with self.assertRaises(ValueError):
            _claude_code_entry("bad-server")


class TestOpencodeEntry(InTempDir):
    def test_mempalace(self):
        entry = _opencode_entry(MCP_MEMPALACE)
        self.assertEqual(entry["type"], "local")
        self.assertIsInstance(entry["command"], list)
        self.assertTrue(entry["enabled"])

    def test_codebase_memory(self):
        entry = _opencode_entry(MCP_CODEBASE_MEMORY)
        self.assertEqual(entry["type"], "local")
        self.assertTrue(entry["enabled"])

    def test_unknown_server(self):
        with self.assertRaises(ValueError):
            _opencode_entry("bad")


class TestCodexEntry(InTempDir):
    def test_mempalace(self):
        entry = _codex_entry(MCP_MEMPALACE)
        self.assertEqual(entry["type"], "stdio")
        self.assertEqual(entry["command"], "mempalace-mcp")
        self.assertEqual(entry["args"], [])

    def test_codebase_memory(self):
        entry = _codex_entry(MCP_CODEBASE_MEMORY)
        self.assertEqual(entry["type"], "stdio")
        self.assertIn("codebase-memory-mcp", entry["command"])

    def test_unknown_server(self):
        with self.assertRaises(ValueError):
            _codex_entry("unknown")


class TestOpenClawEntry(InTempDir):
    def test_mempalace(self):
        entry = _openclaw_entry(MCP_MEMPALACE)
        expected = MEMPALACE_MCP_COMMAND()
        self.assertEqual(entry["type"], "local")
        self.assertIsInstance(entry["command"], list)
        self.assertTrue(entry["enabled"])
        self.assertEqual(entry["command"], expected)

    def test_codebase_memory(self):
        from ai_brain.constants import GLOBAL_CODEBASE_MEMORY_MCP
        entry = _openclaw_entry(MCP_CODEBASE_MEMORY)
        self.assertEqual(entry["type"], "local")
        self.assertTrue(entry["enabled"])
        self.assertEqual(str(GLOBAL_CODEBASE_MEMORY_MCP()), entry["command"][0])

    def test_unknown_server(self):
        with self.assertRaises(ValueError):
            _openclaw_entry("unknown-server")


# ---------------------------------------------------------------------------
# _all_targets
# ---------------------------------------------------------------------------

class TestAllTargets(InTempDir):
    def test_returns_eleven_targets(self):
        paths = _minimal_paths()
        targets = _all_targets(paths)
        self.assertEqual(len(targets), 11)

    def test_all_are_registration_target(self):
        paths = _minimal_paths()
        for t in _all_targets(paths):
            self.assertIsInstance(t, RegistrationTarget)

    def test_labels_are_unique(self):
        paths = _minimal_paths()
        labels = [t.label for t in _all_targets(paths)]
        self.assertEqual(len(labels), len(set(labels)))


# ---------------------------------------------------------------------------
# _register_in_file (JSON)
# ---------------------------------------------------------------------------

class TestRegisterInFileJSON(InTempDir):
    def test_creates_new_file(self):
        target = Path.home() / "cfg.json"
        rt = RegistrationTarget(
            "Test", target, "mcpServers", ALL_SERVERS, _stdio_server_entry,
        )
        result = _register_in_file(rt)
        self.assertTrue(result)
        data = _read_json(target)
        self.assertIn("mcpServers", data)
        self.assertIn(MCP_MEMPALACE, data["mcpServers"])
        self.assertIn(MCP_CODEBASE_MEMORY, data["mcpServers"])

    def test_skips_when_command_matches(self):
        """When the existing command is the same, don't rewrite."""
        target = Path.home() / "cfg.json"
        existing_entry = _stdio_server_entry(MCP_MEMPALACE)
        _write_json(target, {"mcpServers": {MCP_MEMPALACE: existing_entry}})

        rt = RegistrationTarget(
            "Test", target, "mcpServers", (MCP_MEMPALACE,), _stdio_server_entry,
        )
        _register_in_file(rt)
        data = _read_json(target)
        # Still there and identical
        self.assertEqual(data["mcpServers"][MCP_MEMPALACE], existing_entry)

    def test_updates_when_command_differs(self):
        target = Path.home() / "cfg.json"
        _write_json(target, {"mcpServers": {MCP_MEMPALACE: {"command": "old-path", "args": []}}})

        rt = RegistrationTarget(
            "Test", target, "mcpServers", (MCP_MEMPALACE,), _stdio_server_entry,
        )
        _register_in_file(rt)
        data = _read_json(target)
        self.assertNotEqual(data["mcpServers"][MCP_MEMPALACE]["command"], "old-path")

    def test_cleans_up_obsolete_graphify_key(self):
        target = Path.home() / "cfg.json"
        _write_json(target, {"mcpServers": {"graphify": {"command": "old"}}})

        rt = RegistrationTarget(
            "Test", target, "mcpServers", (MCP_MEMPALACE,), _stdio_server_entry,
        )
        _register_in_file(rt)
        data = _read_json(target)
        self.assertNotIn("graphify", data["mcpServers"])


# ---------------------------------------------------------------------------
# _register_in_file (TOML)
# ---------------------------------------------------------------------------

class TestRegisterInFileTOML(InTempDir):
    def test_creates_new_toml_file(self):
        target = Path.home() / "config.toml"
        rt = RegistrationTarget(
            "Codex", target, "mcp_servers", ALL_SERVERS, _codex_entry,
        )
        result = _register_in_file(rt)
        self.assertTrue(result)
        self.assertTrue(target.is_file())
        content = target.read_text(encoding="utf-8")
        self.assertIn("mcp_servers", content)
        self.assertIn("mempalace", content)

    def test_updates_existing_toml(self):
        target = Path.home() / "config.toml"
        target.write_text(
            '[mcp_servers.mempalace]\ncommand = "old-mempalace"\n',
            encoding="utf-8",
        )
        rt = RegistrationTarget(
            "Codex", target, "mcp_servers", (MCP_MEMPALACE,), _codex_entry,
        )
        _register_in_file(rt)
        content = target.read_text(encoding="utf-8")
        self.assertIn("mempalace-mcp", content)


# ---------------------------------------------------------------------------
# _deregister_in_file
# ---------------------------------------------------------------------------

class TestDeregisterInFileJSON(InTempDir):
    def test_removes_servers(self):
        target = Path.home() / "cfg.json"
        _write_json(target, {
            "mcpServers": {
                MCP_MEMPALACE: {"command": "x"},
                MCP_CODEBASE_MEMORY: {"command": "y"},
                "other": {"command": "keep"},
            },
        })
        rt = RegistrationTarget(
            "Test", target, "mcpServers", ALL_SERVERS, _stdio_server_entry,
        )
        result = _deregister_in_file(rt)
        self.assertTrue(result)
        data = _read_json(target)
        self.assertNotIn(MCP_MEMPALACE, data["mcpServers"])
        self.assertNotIn(MCP_CODEBASE_MEMORY, data["mcpServers"])
        self.assertIn("other", data["mcpServers"])

    def test_no_server_key_does_nothing(self):
        target = Path.home() / "cfg.json"
        _write_json(target, {"other_stuf": "keep"})
        rt = RegistrationTarget(
            "Test", target, "mcpServers", ALL_SERVERS, _stdio_server_entry,
        )
        _deregister_in_file(rt)
        data = _read_json(target)
        self.assertNotIn("mcpServers", data)

    def test_cleans_obsolete_graphify_key(self):
        target = Path.home() / "cfg.json"
        _write_json(target, {
            "mcpServers": {
                "graphify": {"command": "old"},
                MCP_MEMPALACE: {"command": "x"},
            },
        })
        rt = RegistrationTarget(
            "Test", target, "mcpServers", ALL_SERVERS, _stdio_server_entry,
        )
        _deregister_in_file(rt)
        data = _read_json(target)
        self.assertNotIn("graphify", data.get("mcpServers", {}))


class TestDeregisterInFileTOML(InTempDir):
    def test_removes_servers_from_toml(self):
        target = Path.home() / "config.toml"
        target.write_text(
            '[mcp_servers.mempalace]\ncommand = "x"\n'
            '[mcp_servers.codebase-memory-mcp]\ncommand = "y"\n',
            encoding="utf-8",
        )
        rt = RegistrationTarget(
            "Codex", target, "mcp_servers", ALL_SERVERS, _codex_entry,
        )
        result = _deregister_in_file(rt)
        self.assertTrue(result)
        content = target.read_text(encoding="utf-8")
        self.assertNotIn("mempalace", content)
        self.assertNotIn("codebase-memory-mcp", content)


# ---------------------------------------------------------------------------
# register_all / deregister_all
# ---------------------------------------------------------------------------

class TestRegisterAll(InTempDir):
    def test_touches_all_files(self):
        paths = _minimal_paths()
        # Create parent dirs for every non-None path
        for field_name in paths.__dataclass_fields__:
            p = getattr(paths, field_name)
            if p is not None:
                p.parent.mkdir(parents=True, exist_ok=True)
        touched = register_all(paths)
        self.assertGreaterEqual(touched, 1)

    def test_skips_when_parent_missing(self):
        """If a target's parent dir doesn't exist, it should be skipped."""
        paths = _minimal_paths()
        # Remove all target files' parent dirs by using paths under a nonexistent dir
        orphan_path = Path.home() / "nonexistent" / "dir" / "cfg.json"
        paths = replace(
            paths,
            gemini_config=orphan_path,
            gemini_antigravity=orphan_path,
            mcp_json=orphan_path,
            claude_json=orphan_path,
            claude_desktop=orphan_path,
            vscode_kilo=orphan_path,
            kilo_cli=orphan_path,
            opencode_json=orphan_path,
            cursor_json=orphan_path,
            codex_toml=orphan_path,
        )
        touched = register_all(paths)
        self.assertEqual(touched, 0)

    def test_skips_none_paths(self):
        paths = _minimal_paths()
        paths = replace(paths, claude_desktop=None, vscode_kilo=None)
        # Should still work without errors
        touched = register_all(paths)
        self.assertIsInstance(touched, int)


class TestDeregisterAll(InTempDir):
    def test_removes_from_existing_files(self):
        paths = _minimal_paths()
        # Write a JSON config at gemini_config path
        paths.gemini_config.parent.mkdir(parents=True, exist_ok=True)
        _write_json(paths.gemini_config, {
            "mcpServers": {
                MCP_MEMPALACE: {"command": "x"},
                MCP_CODEBASE_MEMORY: {"command": "y"},
            },
        })
        touched = deregister_all(paths)
        self.assertGreaterEqual(touched, 1)
        data = _read_json(paths.gemini_config)
        self.assertNotIn(MCP_MEMPALACE, data.get("mcpServers", {}))

    def test_skips_when_file_not_exists(self):
        paths = _minimal_paths()
        # Parent dirs exist but no config files. deregister_all requires is_file().
        for field_name in paths.__dataclass_fields__:
            p = getattr(paths, field_name)
            if p is not None:
                p.parent.mkdir(parents=True, exist_ok=True)
        touched = deregister_all(paths)
        self.assertEqual(touched, 0)

    def test_skips_none_paths(self):
        paths = _minimal_paths()
        paths = replace(paths, claude_desktop=None, vscode_kilo=None)
        touched = deregister_all(paths)
        self.assertIsInstance(touched, int)


# ---------------------------------------------------------------------------
# Roundtrip: register then deregister
# ---------------------------------------------------------------------------

class TestRoundtrip(InTempDir):
    def test_register_then_deregister_json(self):
        target = Path.home() / "cfg.json"
        rt = RegistrationTarget(
            "Test", target, "mcpServers", ALL_SERVERS, _stdio_server_entry,
        )
        _register_in_file(rt)
        data = _read_json(target)
        self.assertIn(MCP_MEMPALACE, data["mcpServers"])

        _deregister_in_file(rt)
        data = _read_json(target)
        self.assertNotIn(MCP_MEMPALACE, data.get("mcpServers", {}))

    def test_register_then_deregister_toml(self):
        target = Path.home() / "config.toml"
        rt = RegistrationTarget(
            "Codex", target, "mcp_servers", ALL_SERVERS, _codex_entry,
        )
        _register_in_file(rt)
        content = target.read_text(encoding="utf-8")
        self.assertIn("mcp_servers", content)

        _deregister_in_file(rt)
        content = target.read_text(encoding="utf-8")
        self.assertNotIn("mempalace", content)
