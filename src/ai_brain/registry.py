"""Project registry + auto-archive whitelist.

Two plain text files under ~/.claude/ hold the active project list and the
auto-archive whitelist. Both are line-delimited absolute paths; we use
`set` semantics to keep dedup simple.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional

from .constants import AUTO_ARCHIVE_PATH, REGISTRY_PATH
from .ui import print_green as green, print_red as red, print_yellow as yellow


# --- File helpers ---------------------------------------------------------------

def _read_lines(path: Path) -> List[str]:
    if not path.is_file():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except Exception:
        return []


def _write_lines(path: Path, lines: Iterable[str]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
        return True
    except Exception as e:
        red(f"錯誤：無法寫入 {path} ({e})")
        return False


def _append_line(path: Path, line: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        return True
    except Exception as e:
        red(f"錯誤：無法寫入 {path} ({e})")
        return False


# --- Public API -----------------------------------------------------------------

def current_project_path() -> str:
    return str(Path.cwd().resolve())


def register_current() -> bool:
    """Add the current directory to the active project list (idempotent)."""
    proj_path = current_project_path()
    if proj_path in _read_lines(REGISTRY_PATH()):
        return False
    if _append_line(REGISTRY_PATH(), proj_path):
        green("--> 已將此專案註冊至 AI 大腦活躍清單中！")
        return True
    return False


def deregister_project(proj_path: str) -> bool:
    """Remove the specified directory from the active + whitelist lists."""
    removed = False
    for path in (REGISTRY_PATH(), AUTO_ARCHIVE_PATH()):
        lines = _read_lines(path)
        if proj_path in lines:
            lines.remove(proj_path)
            if _write_lines(path, lines) and path == REGISTRY_PATH():
                yellow(f"--> 已將專案 \"{Path(proj_path).name}\" 自 AI 大腦活躍清單註銷。")
                removed = True
    return removed


def deregister_current() -> bool:
    """Remove the current directory from the active + whitelist lists."""
    return deregister_project(current_project_path())


def deregister_all_projects() -> bool:
    """Clear all registered projects from active and whitelist lists."""
    ok1 = _write_lines(REGISTRY_PATH(), [])
    ok2 = _write_lines(AUTO_ARCHIVE_PATH(), [])
    if ok1 and ok2:
        green("--> 已成功清空 AI 大腦活躍專案註冊表！")
        return True
    return False


def list_active() -> List[str]:
    return _read_lines(REGISTRY_PATH())


def list_archived() -> List[str]:
    return _read_lines(AUTO_ARCHIVE_PATH())


def find_active_by_keyword(keyword: str) -> Optional[str]:
    """Return the first registered project path whose substring matches."""
    for proj in list_active():
        if keyword in proj:
            return proj
    return None


def find_active_by_index(index_1based: int) -> Optional[str]:
    """Return the project at the given 1-based position in the active list.

    Used by `ai-brain exclude 3` / `ai-brain include 3` so the user can
    refer to a project by the number they saw in `ai-brain exclude` (no args).
    Returns None for out-of-range indices.
    """
    if index_1based < 1:
        return None
    active = list_active()
    if not active or index_1based > len(active):
        return None
    return active[index_1based - 1]


def is_archived(proj_path: str) -> bool:
    return proj_path in list_archived()


def enable_archive(proj_path: str) -> bool:
    if proj_path in list_archived():
        yellow(f'⚠️ 專案 "{Path(proj_path).name}" 已經在自動歸檔白名單中。')
        return True
    if _append_line(AUTO_ARCHIVE_PATH(), proj_path):
        green(f'✅ 已成功將專案 "{Path(proj_path).name}" 加入自動歸檔白名單！')
        print("該專案現在起將會定時進行自動記憶歸檔。")
        return True
    return False


def disable_archive(proj_path: str) -> bool:
    lines = list_archived()
    if proj_path not in lines:
        yellow(f'⚠️ 專案 "{Path(proj_path).name}" 本就未啟用自動歸檔。')
        return True
    lines.remove(proj_path)
    if _write_lines(AUTO_ARCHIVE_PATH(), lines):
        green(f'✅ 已成功將專案 "{Path(proj_path).name}" 自自動歸檔白名單中移除！')
        return True
    return False


def clear_archive() -> bool:
    """Empty the auto-archive whitelist (keeps active list intact)."""
    if _write_lines(AUTO_ARCHIVE_PATH(), []):
        green("✅ 已成功停用所有專案的定時自動歸檔！自動歸檔白名單已清空。")
        return True
    return False


def archive_all_active() -> bool:
    """Copy the entire active list into the whitelist."""
    active = list_active()
    if not active:
        yellow("⚠️ 活躍專案註冊表為空，無專案可啟用。")
        return _write_lines(AUTO_ARCHIVE_PATH(), [])
    if _write_lines(AUTO_ARCHIVE_PATH(), active):
        green("✅ 已成功將所有活躍註冊專案加入自動歸檔白名單！")
        return True
    return False
