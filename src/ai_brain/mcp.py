"""MCP server registration across IDEs.

Different IDEs use different JSON layouts (`mcpServers` vs `mcp`) and slightly
different command structures. Rather than copy-pasting 6 IDE-specific
modifier closures (the original bug), we declare a small list of *targets*
and let the registry do the rest.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import modify_json_file
from .constants import (
    GLOBAL_CODEBASE_MEMORY_MCP,
    MCP_CODEBASE_MEMORY,
    MCP_MEMPALACE,
    MEMPALACE_MCP_COMMAND,
)
from .ui import print_blue as blue

# --- Server descriptors ---------------------------------------------------------
# Each descriptor produces the IDE-specific entry for an MCP server. Keeping the
# logic in one place stops subtle drift between targets (e.g. one having
# "type":"stdio" and another not).

def _stdio_server_entry(server: str) -> dict[str, Any]:
    """Default stdio entry: command + args + env."""
    if server == MCP_MEMPALACE:
        cmd = MEMPALACE_MCP_COMMAND()
        return {
            "command": cmd[0],
            "args": cmd[1:],
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
            "command": MEMPALACE_MCP_COMMAND()[0],
            "args": MEMPALACE_MCP_COMMAND()[1:],
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
            "command": MEMPALACE_MCP_COMMAND(),
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
        cmd = MEMPALACE_MCP_COMMAND()
        return {
            "command": cmd[0],
            "args": cmd[1:],
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
        cmd = MEMPALACE_MCP_COMMAND()
        return {
            "type": "stdio",
            "command": cmd[0],
            "args": cmd[1:],
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
            "command": MEMPALACE_MCP_COMMAND(),
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
        cmd = MEMPALACE_MCP_COMMAND()
        return {
            "type": "stdio",
            "command": cmd[0],
            "args": cmd[1:],
        }
    if server == MCP_CODEBASE_MEMORY:
        return {
            "type": "stdio",
            "command": str(GLOBAL_CODEBASE_MEMORY_MCP()),
            "args": [],
        }
    raise ValueError(f"Unknown MCP server: {server}")


def _openclaw_entry(server: str) -> dict[str, Any]:
    """OpenClaw canonical stdio MCP server shape.

    Schema reference (OpenClaw 2026.6+ ``openclaw.json`` ``mcp.servers`` block):
    ``command`` is validated by Zod as a single executable string, ``args`` is an
    optional string array. The ``type`` field is reserved for HTTP transports
    (``sse`` / ``http`` / ``streamable-http``) and is not valid on stdio entries;
    including ``type: "local"`` triggers
    ``mcp.servers.<name>.command: Invalid input`` and stops the Gateway.
    """
    if server == MCP_MEMPALACE:
        cmd = MEMPALACE_MCP_COMMAND()
        return {
            "command": cmd[0],
            "args": cmd[1:],
        }
    if server == MCP_CODEBASE_MEMORY:
        return {
            "command": str(GLOBAL_CODEBASE_MEMORY_MCP()),
            "args": [],
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
        RegistrationTarget("OpenClaw", paths.openclaw_config, "mcp.servers",
                           (MCP_MEMPALACE, MCP_CODEBASE_MEMORY), _openclaw_entry),
    ]


# --- Public API -----------------------------------------------------------------

def register_all(paths) -> int:
    """Register mempalace + codebase-memory-mcp across every relevant IDE config.

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
    """Remove mempalace + codebase-memory-mcp from every relevant IDE config."""
    touched = 0
    for target in _all_targets(paths):
        if not target.path or not target.path.is_file():
            continue
        blue(f"---> 自 {target.label} 註銷 MemPalace / Codebase-Memory MCP 伺服器...")
        _deregister_in_file(target)
        touched += 1
    return touched


# --- Internals ------------------------------------------------------------------

def _register_in_file(target: RegistrationTarget) -> bool:
    def _modifier(data: dict[str, Any]) -> dict[str, Any]:
        parts = target.server_key.split('.')
        curr = data
        for p in parts[:-1]:
            curr = curr.setdefault(p, {})
        servers = curr.setdefault(parts[-1], {})
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


def sync_all_mcp_commands(paths, fix: bool = False) -> tuple[int, list[str]]:
    """Scan all MCP configs and fix stale ``mempalace-mcp`` binary references.

    Covers both top-level ``mcpServers`` and per-project entries inside
    ``~/.claude.json`` (which ``register_all`` does not touch).

    Returns ``(fixed_count, messages)``.  When *fix* is False only reports.
    """
    from .constants import MCP_CODEBASE_MEMORY, MCP_MEMPALACE
    from .ui import GREEN, NC, RED, YELLOW

    fixed_count = 0
    messages: list[str] = []

    def _label(tag: str, path: Path) -> str:
        return f"[{tag}] {path}"

    # --- 1. Scan all top-level IDE config files ---------------------------------
    # Each target declares which servers it must register (see
    # ``RegistrationTarget.servers``).  Iterate over **all** of them so a drift
    # in ``codebase-memory-mcp`` (e.g. leftover OpenClaw shape from before the
    # canonical Zod-compliant format) is reported and fixed, not just
    # ``mempalace``.
    for target in _all_targets(paths):
        if not target.path or not target.path.is_file():
            continue
        try:
            data = json.loads(target.path.read_text(encoding="utf-8"))
        except Exception:
            continue
        parts = target.server_key.split('.')
        curr = data
        found = True
        for p in parts[:-1]:
            if isinstance(curr, dict) and p in curr and isinstance(curr[p], dict):
                curr = curr[p]
            else:
                found = False
                break
        if not found or not isinstance(curr, dict) or parts[-1] not in curr:
            continue
        servers = curr[parts[-1]]
        if not isinstance(servers, dict):
            continue

        all_ok = True
        for server in target.servers:
            entry = servers.get(server)
            if not isinstance(entry, dict):
                # Missing entry — out of scope for the stale-detector; the
                # registration path owns that case.
                continue

            # Build the expected entry for this target so we compare the exact
            # canonical shape (some IDEs use list-form "command" instead of
            # separate "command"+"args" fields, e.g. Kilo CLI, OpenClaw).
            expected_entry = target.entry_builder(server)
            if entry == expected_entry:
                continue

            all_ok = False
            # Drift on this server — always fixable when the file already
            # declares the entry.  Tag the message with the server name so the
            # user can tell which entry got rewritten.
            current_cmd = entry.get("command", "")
            tag = f"{target.label}/{server}"
            if fix:
                servers[server] = expected_entry
                try:
                    target.path.write_text(
                        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8",
                    )
                    fixed_count += 1
                    messages.append(
                        f"  {GREEN}[ FIXED ]{NC} {tag}: {current_cmd!r} → {expected_entry.get('command')!r}"
                    )
                except Exception as e:
                    messages.append(f"  {RED}[ ERROR ]{NC} {tag}: {e}")
            else:
                fixed_count += 1  # count stale for summary
                messages.append(
                    f"  {YELLOW}[ STALE ]{NC} {tag}: {current_cmd!r} (expected {expected_entry.get('command')!r})"
                )

        # Only emit the file-level ✓ if every server we *do* see is in sync.
        # Mirrors the legacy behavior, where a file with no entries was simply
        # skipped silently.
        if all_ok and isinstance(servers.get(MCP_MEMPALACE), dict):
            messages.append(f"  {GREEN}✓{NC} {_label(target.label, target.path)}")

    # --- 2. Scan per-project entries in ~/.claude.json --------------------------
    # Per-project entries use the same stdio shape as the top-level ~/.claude.json
    # target, so derive the canonical command/args once from _claude_code_entry
    # instead of copy-pasting the MEMPALACE_MCP_COMMAND() split.  Iterate over
    # both servers for the same reason as section 1.
    claude_json = paths.claude_json
    if claude_json.is_file():
        try:
            data = json.loads(claude_json.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        changed = False
        for proj_key, proj_val in data.get("projects", {}).items():
            if not isinstance(proj_val, dict):
                continue
            servers = proj_val.get("mcpServers", {})
            if not isinstance(servers, dict):
                continue

            per_project_all_ok = True
            had_any_entry = False
            for server in (MCP_MEMPALACE, MCP_CODEBASE_MEMORY):
                entry = servers.get(server)
                if not isinstance(entry, dict):
                    continue
                had_any_entry = True

                expected_entry = _claude_code_entry(server)
                expected_cmd = expected_entry["command"]
                expected_args = expected_entry.get("args", [])
                current_cmd = entry.get("command", "")
                if current_cmd == expected_cmd and entry.get("args") == expected_args:
                    continue

                per_project_all_ok = False
                tag = f"project:{proj_key}/{server}"
                if fix:
                    entry["command"] = expected_cmd
                    entry["args"] = list(expected_args)
                    changed = True
                    fixed_count += 1
                    messages.append(
                        f"  {GREEN}[ FIXED ]{NC} {tag}: {current_cmd!r} → {expected_cmd!r}"
                    )
                else:
                    messages.append(
                        f"  {YELLOW}[ STALE ]{NC} {tag}: {current_cmd!r}"
                    )
            if had_any_entry and per_project_all_ok:
                messages.append(f"  {GREEN}✓{NC} [project] {proj_key}")
        if changed:
            try:
                claude_json.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
            except Exception as e:
                messages.append(f"  {RED}[ ERROR ]{NC} writing ~/.claude.json: {e}")

    return fixed_count, messages


def _deregister_in_file(target: RegistrationTarget) -> bool:
    def _modifier(data: dict[str, Any]) -> dict[str, Any]:
        parts = target.server_key.split('.')
        curr = data
        found = True
        for p in parts[:-1]:
            if p in curr and isinstance(curr[p], dict):
                curr = curr[p]
            else:
                found = False
                break
        if found and parts[-1] in curr and isinstance(curr[parts[-1]], dict):
            servers = curr[parts[-1]]
            if "graphify" in servers:
                del servers["graphify"]
            for server in target.servers:
                if server in servers:
                    del servers[server]
            print(f"Successfully deregistered servers from {target.label}")
        return data

    if target.path and target.path.suffix == ".toml":
        from .config import modify_toml_file
        return modify_toml_file(target.path, _modifier)
    return modify_json_file(target.path, _modifier)
