"""Structured exception types for ai-brain.

Replaces bare `except Exception: pass` with typed, traceable errors.
"""
from __future__ import annotations


class AiBrainError(Exception):
    """Base for all ai-brain errors."""


class ToolNotFoundError(AiBrainError):
    """An external CLI tool (mempalace, codebase-memory-mcp, etc.) is missing."""

    def __init__(self, tool: str, install_hint: str = ""):
        self.tool = tool
        self.install_hint = install_hint
        msg = f"Tool '{tool}' not found"
        if install_hint:
            msg += f" — install with: {install_hint}"
        super().__init__(msg)


class ConfigWriteError(AiBrainError):
    """Failed to write/modify a configuration file."""

    def __init__(self, path: str, reason: str = ""):
        self.path = path
        self.reason = reason
        msg = f"Failed to write config: {path}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)


class RegistryError(AiBrainError):
    """Failed to read/write the project registry."""

    def __init__(self, operation: str, path: str, reason: str = ""):
        self.operation = operation
        self.path = path
        self.reason = reason
        msg = f"Registry {operation} failed for {path}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


class SubprocessError(AiBrainError):
    """Wrapped subprocess call failure."""

    def __init__(self, cmd: list[str], returncode: int = -1, stderr: str = ""):
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        msg = f"Command failed: {' '.join(cmd)}"
        if returncode >= 0:
            msg += f" (exit {returncode})"
        if stderr:
            msg += f": {stderr.strip()}"
        super().__init__(msg)
