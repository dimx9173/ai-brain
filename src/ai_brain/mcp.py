"""MCP server registration across IDEs.

Different IDEs use different JSON layouts (`mcpServers` vs `mcp`) and slightly
different command structures. Rather than copy-pasting 6 IDE-specific
modifier closures (the original bug), we declare a small list of *targets*
and let the registry do the rest.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import modify_json_file
from .constants import (
    GLOBAL_CODEBASE_MEMORY_MCP,
    GLOBAL_MEMPALACE_MCP,
    MCP_CODEBASE_MEMORY,
    MCP_MEMPALACE,
    TOOL_MEMPALACE_MCP,
)
from .ui import print_blue as blue

# --- Server descriptors ---------------------------------------------------------
# Each descriptor produces the IDE-specific entry for an MCP server. Keeping the
# logic in one place stops subtle drift between targets (e.g. one having
# "type":"stdio" and another not).

def _stdio_server_entry(server: str) -> dict[str, Any]:
    """Default stdio entry: command + args + env."""
    if server == MCP_MEMPALACE:
        return {
            "command": TOOL_MEMPALACE_MCP,
            "args": [],
            "env": {},
        }
    if server == MCP_CODEBASE_MEMORY:
        return {
            "command": str(GLOBAL_CODEBASE_MEMORY_MCP()),
            "args": [],
        }
    raise ValueError(f"Unknown MCP server: {server}")


def _kilo_local_entry(server: str) -> dict[str, Any]:
    """Kilo uses 'type':'local' and absolute path for mempalace."""
    if server == MCP_MEMPALACE:
        return {
            "type": "local",
            "command": str(GLOBAL_MEMPALACE_MCP()),
            "args": [],
            "enabled": True,
        }
    if server == MCP_CODEBASE_MEMORY:
        return {
            "type": "local",
            "command": str(GLOBAL_CODEBASE_MEMORY_MCP()),
            "args": [],
            "enabled": True,
        }
    raise ValueError(f"Unknown MCP server: {server}")


def _kilo_cli_entry(server: str) -> dict[str, Any]:
    """~/.config/kilo/kilo.json nests under `mcp` and uses a list command."""
    if server == MCP_MEMPALACE:
        return {
            "type": "local",
            "command": [str(GLOBAL_MEMPALACE_MCP())],
        }
    if server == MCP_CODEBASE_MEMORY:
        return {
            "type": "local",
            "command": [str(GLOBAL_CODEBASE_MEMORY_MCP())],
        }
    raise ValueError(f"Unknown MCP server: {server}")


def _claude_desktop_entry(server: str) -> dict[str, Any]:
    """Claude Desktop app on macOS uses absolute path (no `type` field)."""
    if server == MCP_MEMPALACE:
        return {
            "command": str(GLOBAL_MEMPALACE_MCP()),
            "args": [],
            "env": {},
        }
    if server == MCP_CODEBASE_MEMORY:
        return {
            "command": str(GLOBAL_CODEBASE_MEMORY_MCP()),
            "args": [],
        }
    raise ValueError(f"Claude Desktop does not register server: {server}")


def _claude_code_entry(server: str) -> dict[str, Any]:
    """~/.claude.json uses the modern stdio shape with a `type` field."""
    if server == MCP_MEMPALACE:
        return {
            "type": "stdio",
            "command": TOOL_MEMPALACE_MCP,
            "args": [],
            "env": {},
        }
    if server == MCP_CODEBASE_MEMORY:
        return {
            "type": "stdio",
            "command": str(GLOBAL_CODEBASE_MEMORY_MCP()),
            "args": [],
            "env": {},
        }
    raise ValueError(f"Unknown MCP server: {server}")


def _opencode_entry(server: str) -> dict[str, Any]:
    """~/.config/opencode/opencode.json uses local type and array command."""
    if server == MCP_MEMPALACE:
        return {
            "type": "local",
            "command": [str(GLOBAL_MEMPALACE_MCP())],
            "enabled": True,
        }
    if server == MCP_CODEBASE_MEMORY:
        return {
            "type": "local",
            "command": [str(GLOBAL_CODEBASE_MEMORY_MCP())],
            "enabled": True,
        }
    raise ValueError(f"Unknown MCP server: {server}")


def _codex_entry(server: str) -> dict[str, Any]:
    """Codex config.toml uses stdio type with string command."""
    if server == MCP_MEMPALACE:
        return {
            "type": "stdio",
            "command": TOOL_MEMPALACE_MCP,
            "args": [],
        }
    if server == MCP_CODEBASE_MEMORY:
        return {
            "type": "stdio",
            "command": str(GLOBAL_CODEBASE_MEMORY_MCP()),
            "args": [],
        }
    raise ValueError(f"Unknown MCP server: {server}")


def _openclaw_entry(server: str) -> dict[str, Any]:
    """OpenClaw uses mcpServers key with local type and array command."""
    if server == MCP_MEMPALACE:
        return {
            "type": "local",
            "command": [str(GLOBAL_MEMPALACE_MCP())],
            "enabled": True,
        }
    if server == MCP_CODEBASE_MEMORY:
        return {
            "type": "local",
            "command": [str(GLOBAL_CODEBASE_MEMORY_MCP())],
            "enabled": True,
        }
    raise ValueError(f"Unknown MCP server: {server}")


# --- Target declarations --------------------------------------------------------

@dataclass(frozen=True)
class RegistrationTarget:
    """A single IDE config file we want to (un)register servers in."""

    label: str  # human-readable, for log lines
    path: Path | None  # None = skip on this OS
    server_key: str  # JSON key holding the server map ("mcpServers" or "mcp")
    servers: tuple[str, ...]  # which servers to (un)register
    entry_builder: Callable[[str], dict[str, Any]]


def _all_targets(paths) -> list[RegistrationTarget]:
    """List every IDE target relevant to the current platform."""
    return [
        RegistrationTarget("Gemini", paths.gemini_config, "mcpServers",
                           (MCP_MEMPALACE, MCP_CODEBASE_MEMORY), _stdio_server_entry),
        RegistrationTarget("Gemini/Antigravity", paths.gemini_antigravity, "mcpServers",
                           (MCP_MEMPALACE, MCP_CODEBASE_MEMORY), _stdio_server_entry),
        RegistrationTarget("OpenCode", paths.opencode_json, "mcp",
                           (MCP_MEMPALACE, MCP_CODEBASE_MEMORY), _opencode_entry),
        RegistrationTarget("~/.mcp.json", paths.mcp_json, "mcpServers",
                           (MCP_MEMPALACE, MCP_CODEBASE_MEMORY), _stdio_server_entry),
        RegistrationTarget("~/.claude.json", paths.claude_json, "mcpServers",
                           (MCP_MEMPALACE, MCP_CODEBASE_MEMORY), _claude_code_entry),
        RegistrationTarget("Claude Desktop", paths.claude_desktop, "mcpServers",
                           (MCP_MEMPALACE, MCP_CODEBASE_MEMORY), _claude_desktop_entry),
        RegistrationTarget("Kilo VS Code", paths.vscode_kilo, "mcpServers",
                           (MCP_MEMPALACE, MCP_CODEBASE_MEMORY), _kilo_local_entry),
        RegistrationTarget("Kilo CLI", paths.kilo_cli, "mcp",
                           (MCP_MEMPALACE, MCP_CODEBASE_MEMORY), _kilo_cli_entry),
        RegistrationTarget("Cursor", paths.cursor_json, "mcpServers",
                           (MCP_MEMPALACE, MCP_CODEBASE_MEMORY), _stdio_server_entry),
        RegistrationTarget("Codex", paths.codex_toml, "mcp_servers",
                           (MCP_MEMPALACE, MCP_CODEBASE_MEMORY), _codex_entry),
        RegistrationTarget("OpenClaw", paths.openclaw_config, "mcpServers",
                           (MCP_MEMPALACE, MCP_CODEBASE_MEMORY), _openclaw_entry),
    ]


# --- Public API -----------------------------------------------------------------

def register_all(paths) -> int:
    """Register mempalace + graphify across every relevant IDE config.

    Returns the number of files we successfully touched.
    """
    touched = 0
    for target in _all_targets(paths):
        if not target.path or not target.path.parent.is_dir():
            continue
        blue(f"---> 偵測到 {target.label} 環境，自動寫入 MCP 設定: {target.path.name}")
        _register_in_file(target)
        touched += 1
    return touched


def deregister_all(paths) -> int:
    """Remove mempalace + graphify from every relevant IDE config."""
    touched = 0
    for target in _all_targets(paths):
        if not target.path or not target.path.is_file():
            continue
        blue(f"---> 自 {target.label} 註銷 MemPalace / Graphify MCP 伺服器...")
        _deregister_in_file(target)
        touched += 1
    return touched


# --- Internals ------------------------------------------------------------------

def _register_in_file(target: RegistrationTarget) -> bool:
    def _modifier(data: dict[str, Any]) -> dict[str, Any]:
        servers = data.setdefault(target.server_key, {})
        if "graphify" in servers:
            del servers["graphify"]
            print(f"Successfully cleaned up obsolete server graphify from {target.label}")
        for server in target.servers:
            if server in servers:
                current_entry = servers[server]
                expected_entry = target.entry_builder(server)
                if current_entry.get("command") == expected_entry.get("command"):
                    continue
            servers[server] = target.entry_builder(server)
            print(f"Successfully registered {server} in {target.label}")
        return data

    if target.path and target.path.suffix == ".toml":
        from .config import modify_toml_file
        return modify_toml_file(target.path, _modifier)
    return modify_json_file(target.path, _modifier)


def _deregister_in_file(target: RegistrationTarget) -> bool:
    def _modifier(data: dict[str, Any]) -> dict[str, Any]:
        if target.server_key in data:
            if "graphify" in data[target.server_key]:
                del data[target.server_key]["graphify"]
            for server in target.servers:
                if server in data[target.server_key]:
                    del data[target.server_key][server]
            print(f"Successfully deregistered servers from {target.label}")
        return data

    if target.path and target.path.suffix == ".toml":
        from .config import modify_toml_file
        return modify_toml_file(target.path, _modifier)
    return modify_json_file(target.path, _modifier)
