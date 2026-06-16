"""ai-brain package.

Re-exports the public API for the CLI entrypoint and downstream tools.
"""
from __future__ import annotations

from .constants import VERSION

__all__ = ["VERSION"]
__version__ = VERSION
