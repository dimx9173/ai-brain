"""Make src/ importable for tests that don't go through a runner.

We append to sys.path at package import time so any test module that does
`import ai_brain` works whether discovered by `python -m unittest`,
`run_tests.py`, or invoked directly.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
