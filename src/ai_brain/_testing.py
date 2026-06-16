"""Test utilities shared across the test suite.

Re-exported at package level so test files can do
`from ai_brain._testing import InTempDir` without depending on the `tests/`
directory's import structure (which IDEs and various test runners disagree on).

This module is part of the public test surface; it is intentionally *not*
re-exported from `ai_brain.__init__` so production code never accidentally
imports it.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


def _ensure_src_on_path() -> None:
    """If `ai_brain` isn't yet importable, prepend its parent to sys.path.

    Lets the helpers be used both from `src/`-based test runners and from
    a fully installed package — without forcing test authors to know the
    difference.
    """
    if "ai_brain" in sys.modules:
        return
    try:
        import ai_brain  # noqa: F401
        return
    except ImportError:
        pass
    _here = Path(__file__).resolve()
    src_dir = _here.parent.parent
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


_ensure_src_on_path()


class InTempDir(unittest.TestCase):
    """Mixin: chdir to a fresh temp dir and stub `Path.home()` for the test.

    Stubbing `Path.home()` is what makes the registry paths testable in
    isolation: `constants.REGISTRY_PATH()` etc. call `Path.home()` on
    each access, so the stub is picked up automatically.
    """

    def setUp(self) -> None:  # noqa: D401 — unittest hook
        self._orig_cwd = os.getcwd()
        self._orig_home = Path.home
        self.tmpdir = tempfile.mkdtemp(prefix="ai-brain-test-")
        os.chdir(self.tmpdir)
        Path.home = lambda: Path(self.tmpdir)  # type: ignore[assignment]
        (Path(self.tmpdir) / ".claude").mkdir(exist_ok=True)

    def tearDown(self) -> None:  # noqa: D401
        os.chdir(self._orig_cwd)
        Path.home = self._orig_home  # type: ignore[assignment]
