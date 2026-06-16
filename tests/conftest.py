"""Backwards-compatible stub for legacy `from tests.conftest import …` callers.

The authoritative test mixin lives at `ai_brain._testing.InTempDir`. This
file exists so older imports of `tests.conftest` still resolve at runtime;
production code never imports from here.
"""
