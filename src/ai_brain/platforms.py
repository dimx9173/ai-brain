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


def find_graphify_python() -> str:
    """Find the python interpreter that has graphify installed by reading its shebang."""
    import os
    import shutil
    graphify_path = shutil.which("graphify")
    if graphify_path:
        try:
            with open(graphify_path, "r", encoding="utf-8") as f:
                first_line = f.readline().strip()
            if first_line.startswith("#!"):
                python_path = first_line[2:].strip()
                python_path = python_path.split()[0]
                if "python" in python_path and os.path.exists(python_path):
                    return python_path
        except Exception:
            pass
    return "python3"

