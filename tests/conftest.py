"""Shared test setup: stub Path.home() and chdir to a temp dir per test.

We deliberately use stdlib `unittest` only — no pytest dependency — because
the project ships with zero third-party runtime deps and tests should match.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


class InTempDir(unittest.TestCase):
    """Mixin: chdir to a fresh temp dir and stub Path.home() for the test."""

    def setUp(self) -> None:
        self._orig_cwd = os.getcwd()
        self._orig_home = Path.home
        self.tmpdir = tempfile.mkdtemp(prefix="ai-brain-test-")
        os.chdir(self.tmpdir)
        # Redirect Path.home() to the temp dir so registry files are isolated.
        Path.home = lambda: Path(self.tmpdir)  # type: ignore[assignment]
        (Path(self.tmpdir) / ".claude").mkdir(exist_ok=True)

    def tearDown(self) -> None:
        os.chdir(self._orig_cwd)
        Path.home = self._orig_home  # type: ignore[assignment]
