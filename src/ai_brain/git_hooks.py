"""Git hook installation/cleanup for `post-merge` and `post-checkout`.

Both hooks just call `ai-brain start` so the code map stays fresh whenever
the working tree changes via Git rather than via local edits.
"""
from __future__ import annotations

from pathlib import Path

from .constants import HOOK_CHAIN, POST_CHECKOUT_TEMPLATE, POST_MERGE_TEMPLATE
from .ui import print_red as red

HOOK_NAMES = ("post-merge", "post-checkout")


def _hooks_dir() -> Path:
    return Path(".git") / "hooks"


def install() -> bool:
    """Write both hooks if .git exists. Returns True on success or no-op."""
    if not Path(".git").is_dir():
        return True

    hooks_dir = _hooks_dir()
    try:
        hooks_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        red(f"警告：建立 hooks 目錄失敗 ({e})")
        return False

    pairs = (
        (hooks_dir / "post-merge", POST_MERGE_TEMPLATE % HOOK_CHAIN),
        (hooks_dir / "post-checkout", POST_CHECKOUT_TEMPLATE % HOOK_CHAIN),
    )
    for path, content in pairs:
        try:
            path.write_text(content, encoding="utf-8")
            path.chmod(0o755)
        except Exception as e:
            red(f"警告：寫入 {path.name} 失敗 ({e})")
            return False
    return True


def uninstall() -> bool:
    """Remove our hooks. Never touches other hook scripts."""
    hooks_dir = _hooks_dir()
    if not hooks_dir.is_dir():
        return True

    for name in HOOK_NAMES:
        hook = hooks_dir / name
        if not hook.is_file():
            continue
        # Only delete hooks that look like ours; never touch user-installed ones.
        try:
            content = hook.read_text(encoding="utf-8")
        except Exception:
            continue
        if "ai-brain start" in content or "ai-brain.sh" in content:
            try:
                hook.unlink()
            except Exception:
                pass
    return True
