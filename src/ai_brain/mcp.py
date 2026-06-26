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
    GLOBAL_GRAPHIFY_MCP_WRAPPER,
    GLOBAL_MEMPALACE_MCP,
    MCP_GRAPHIFY,
    MCP_MEMPALACE,
    MINIMAX_PROVIDER_CONFIG,
    PROVIDER_MINIMAX,
    TOOL_MEMPALACE_MCP,
)
from .ui import print_blue as blue, print_green as green


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
    if server == MCP_GRAPHIFY:
        return {
            "command": str(GLOBAL_GRAPHIFY_MCP_WRAPPER()),
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
    if server == MCP_GRAPHIFY:
        return {
            "type": "local",
            "command": str(GLOBAL_GRAPHIFY_MCP_WRAPPER()),
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
    if server == MCP_GRAPHIFY:
        return {
            "type": "local",
            "command": [str(GLOBAL_GRAPHIFY_MCP_WRAPPER())],
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
    if server == MCP_GRAPHIFY:
        return {
            "command": str(GLOBAL_GRAPHIFY_MCP_WRAPPER()),
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
    if server == MCP_GRAPHIFY:
        return {
            "type": "stdio",
            "command": str(GLOBAL_GRAPHIFY_MCP_WRAPPER()),
            "args": [],
            "env": {},
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
                           (MCP_MEMPALACE, MCP_GRAPHIFY), _stdio_server_entry),
        RegistrationTarget("Gemini/Antigravity", paths.gemini_antigravity, "mcpServers",
                           (MCP_MEMPALACE, MCP_GRAPHIFY), _stdio_server_entry),
        RegistrationTarget("~/.mcp.json", paths.mcp_json, "mcpServers",
                           (MCP_MEMPALACE, MCP_GRAPHIFY), _stdio_server_entry),
        RegistrationTarget("~/.claude.json", paths.claude_json, "mcpServers",
                           (MCP_MEMPALACE, MCP_GRAPHIFY), _claude_code_entry),
        RegistrationTarget("Claude Desktop", paths.claude_desktop, "mcpServers",
                           (MCP_MEMPALACE, MCP_GRAPHIFY), _claude_desktop_entry),
        RegistrationTarget("Kilo VS Code", paths.vscode_kilo, "mcpServers",
                           (MCP_MEMPALACE, MCP_GRAPHIFY), _kilo_local_entry),
        RegistrationTarget("Kilo CLI", paths.kilo_cli, "mcp",
                           (MCP_MEMPALACE, MCP_GRAPHIFY), _kilo_cli_entry),
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


def configure_minimax_provider(paths) -> bool:
    """Add the MiniMax LLM provider to ~/.graphify/providers.json."""
    if not paths.graphify_providers:
        return False

    def _modifier(data: dict[str, Any]) -> dict[str, Any]:
        if PROVIDER_MINIMAX not in data:
            data[PROVIDER_MINIMAX] = MINIMAX_PROVIDER_CONFIG
            print(f"Successfully configured MiniMax provider in {paths.graphify_providers}")
        return data

    blue(f"---> 自動設定 Graphify 自訂 LLM 後端 ({paths.graphify_providers})...")
    ok = modify_json_file(paths.graphify_providers, _modifier)
    if ok:
        blue("    💡 MiniMax 後端使用說明:")
        green('      1. 請先在環境中設定您的 API 金鑰: export MINIMAX_API_KEY="your-api-key"')
        green("      2. 執行 Graphify 提取時指定 minimax 後端: "
              "graphify extract . --backend minimax --model minimax-m2.5")
    return ok


def remove_minimax_provider(paths) -> bool:
    """Drop the MiniMax entry from ~/.graphify/providers.json."""
    if not paths.graphify_providers or not paths.graphify_providers.is_file():
        return False

    def _modifier(data: dict[str, Any]) -> dict[str, Any]:
        if PROVIDER_MINIMAX in data:
            del data[PROVIDER_MINIMAX]
            print("Successfully removed MiniMax provider")
        return data

    blue("---> 自 ~/.graphify/providers.json 移除 MiniMax 自訂 LLM 後端...")
    return modify_json_file(paths.graphify_providers, _modifier)


# --- Internals ------------------------------------------------------------------

def _register_in_file(target: RegistrationTarget) -> bool:
    def _modifier(data: dict[str, Any]) -> dict[str, Any]:
        servers = data.setdefault(target.server_key, {})
        for server in target.servers:
            if server in servers:
                current_entry = servers[server]
                expected_entry = target.entry_builder(server)
                if current_entry.get("command") == expected_entry.get("command"):
                    continue
            servers[server] = target.entry_builder(server)
            print(f"Successfully registered {server} in {target.label}")
        return data

    return modify_json_file(target.path, _modifier)


def _deregister_in_file(target: RegistrationTarget) -> bool:
    def _modifier(data: dict[str, Any]) -> dict[str, Any]:
        if target.server_key in data:
            for server in target.servers:
                if server in data[target.server_key]:
                    del data[target.server_key][server]
            print(f"Successfully deregistered servers from {target.label}")
        return data

    return modify_json_file(target.path, _modifier)
