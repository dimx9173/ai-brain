"""Git hook installation/cleanup for `post-merge` and `post-checkout`.

`post-merge` stays synchronous because after a pull you generally want the
fresh graph before you start working.

`post-checkout` is kept non-blocking: it runs the graph rebuild in a
background subshell so branch switches stay fast.

When a hook already exists (e.g. a user-installed hook), we append an
ai-brain managed section between markers instead of overwriting the whole
file.  `uninstall()` removes only those managed sections.
"""
from __future__ import annotations

from pathlib import Path

from .constants import (
    HOOK_BEGIN_MARKER,
    HOOK_CHAIN,
    HOOK_END_MARKER,
    HOOK_MARKER_BODY,
    POST_CHECKOUT_TEMPLATE,
    POST_MERGE_TEMPLATE,
)
from .ui import print_red as red

HOOK_NAMES = ("post-merge", "post-checkout")


def _hooks_dir() -> Path:
    return Path(".git") / "hooks"


def _render_hook(name: str, template: str) -> str:
    """Return the managed hook section for *name* using *template*."""
    return template.format(
        begin=HOOK_BEGIN_MARKER.format(name=name),
        end=HOOK_END_MARKER.format(name=name),
        marker_body=HOOK_MARKER_BODY,
        chain=HOOK_CHAIN,
    )


def _has_managed_section(content: str, name: str) -> bool:
    return HOOK_BEGIN_MARKER.format(name=name) in content


def _inject_managed_section(existing: str, name: str, template: str) -> str:
    """Insert or replace the ai-brain managed section for *name*."""
    section = _render_hook(name, template)
    begin = HOOK_BEGIN_MARKER.format(name=name)
    end = HOOK_END_MARKER.format(name=name)

    if begin in existing:
        # Replace the existing managed section in place.
        before, _, after = existing.partition(begin)
        _, _, after = after.partition(end)
        return before + section + after

    # No managed section yet — append it while keeping the user's content.
    if existing.endswith("\n"):
        return existing + "\n" + section
    return existing + "\n\n" + section


def _remove_managed_section(content: str, name: str) -> str | None:
    """Remove the managed section for *name*; return None if nothing left."""
    begin = HOOK_BEGIN_MARKER.format(name=name)
    end = HOOK_END_MARKER.format(name=name)

    if begin not in content:
        return content

    before, _, after = content.partition(begin)
    _, _, after = after.partition(end)

    cleaned_lines = (before + after).splitlines()
    # Drop trailing blank lines and a leftover shebang if no real content remains.
    while cleaned_lines and cleaned_lines[-1].strip() == "":
        cleaned_lines.pop()
    if not cleaned_lines or (len(cleaned_lines) == 1 and cleaned_lines[0].startswith("#!")):
        return None

    return "\n".join(cleaned_lines) + "\n"


def install() -> bool:
    """Install or update hooks.  Never overwrite user-installed hooks."""
    if not Path(".git").is_dir():
        return True

    hooks_dir = _hooks_dir()
    try:
        hooks_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        red(f"警告：建立 hooks 目錄失敗 ({e})")
        return False

    pairs = (
        ("post-merge", POST_MERGE_TEMPLATE),
        ("post-checkout", POST_CHECKOUT_TEMPLATE),
    )
    for name, template in pairs:
        path = hooks_dir / name
        try:
            if path.is_file():
                existing = path.read_text(encoding="utf-8")
                new_content = _inject_managed_section(existing, name, template)
            else:
                new_content = _render_hook(name, template)
            path.write_text(new_content, encoding="utf-8")
            path.chmod(0o755)
        except Exception as e:
            red(f"警告：寫入 {path.name} 失敗 ({e})")
            return False
    return True


def uninstall() -> bool:
    """Remove our managed sections.  Delete the file only if it becomes empty."""
    hooks_dir = _hooks_dir()
    if not hooks_dir.is_dir():
        return True

    for name in HOOK_NAMES:
        hook = hooks_dir / name
        if not hook.is_file():
            continue
        try:
            content = hook.read_text(encoding="utf-8")
        except Exception:
            continue

        cleaned = _remove_managed_section(content, name)
        if cleaned is None:
            try:
                hook.unlink()
            except Exception:
                pass
        elif cleaned != content:
            try:
                hook.write_text(cleaned + "\n", encoding="utf-8")
                hook.chmod(0o755)
            except Exception:
                pass
    return True
