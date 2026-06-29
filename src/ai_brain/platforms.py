"""Platform-specific path resolution.

Each IDE / config file lives in a different place on macOS vs Linux vs Windows.
This module centralises the lookup so the rest of the codebase can work with
plain Path objects without scattering `if system == "Darwin"` checks.
"""
from __future__ import annotations

import platform
from dataclasses import dataclass
from pathlib import Path

from .constants import HOME


@dataclass(frozen=True)
class ToolPaths:
    """All filesystem locations the ai-brain tool touches."""

    gemini_config: Path
    gemini_antigravity: Path
    mcp_json: Path
    claude_json: Path
    graphify_providers: Path
    claude_desktop: Path | None
    vscode_kilo: Path | None
    kilo_cli: Path
    opencode_json: Path
    cursor_json: Path
    codex_toml: Path
    openclaw_config: Path


def get_paths() -> ToolPaths:
    """Resolve platform-specific config file paths."""
    system = platform.system()

    if system == "Darwin":
        claude_desktop = (
            HOME() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        )
        vscode_kilo = (
            HOME() / "Library" / "Application Support" / "Code" / "User" / "globalStorage"
            / "kilocode.kilo-code" / "settings" / "mcp_settings.json"
        )
    elif system == "Linux":
        claude_desktop = None
        vscode_kilo = (
            HOME() / ".config" / "Code" / "User" / "globalStorage"
            / "kilocode.kilo-code" / "settings" / "mcp_settings.json"
        )
    else:
        claude_desktop = None
        vscode_kilo = None

    return ToolPaths(
        gemini_config=HOME() / ".gemini" / "config" / "mcp_config.json",
        gemini_antigravity=HOME() / ".gemini" / "antigravity" / "mcp_config.json",
        mcp_json=HOME() / ".mcp.json",
        claude_json=HOME() / ".claude.json",
        graphify_providers=HOME() / ".graphify" / "providers.json",
        claude_desktop=claude_desktop,
        vscode_kilo=vscode_kilo,
        kilo_cli=HOME() / ".config" / "kilo" / "kilo.json",
        opencode_json=HOME() / ".config" / "opencode" / "opencode.json",
        cursor_json=HOME() / ".cursor" / "mcp.json",
        codex_toml=HOME() / ".codex" / "config.toml",
        openclaw_config=HOME() / ".openclaw" / "config.json",
    )


def ensure_path_has_local_bin() -> None:
    """Make sure ~/.local/bin and common system paths are in $PATH.

    Needed for cron / SSH / non-interactive shells where the user's
    .zshenv / .bashrc isn't sourced.
    """
    import os

    path_env = os.environ.get("PATH", "")
    extra_paths = [
        str(HOME() / ".local" / "bin"),
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    for p in extra_paths:
        if p not in path_env.split(os.path.pathsep):
            path_env = p + os.path.pathsep + path_env
    os.environ["PATH"] = path_env


# find_graphify_python is no longer used.
