"""JSON config file read/merge/write utility.

Most AI tooling stores settings as JSON; ai-brain touches ~7 such files. They
all share the same needs: read → mutate → atomic write, with the option to
silently recover from a malformed file (the cost of a corrupted IDE config is
higher than the cost of resetting it).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .ui import print_red as red


def modify_json_file(
    path: Path | None,
    modifier_fn: Callable[[dict[str, Any]], dict[str, Any]],
) -> bool:
    """Read JSON, pass through modifier, write back.

    - If *path* is None (e.g. unsupported on this OS), returns False silently.
    - If the file is missing or unreadable, starts from an empty dict.
    - If the file is malformed JSON, warns and resets to empty (one-shot).
    - Returns True on successful write, False on any error.
    """
    if not path:
        return False

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, Any] = {}
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            red(f"警告：讀取 {path} 時發生 JSON 格式錯誤 ({e})，將會重置該檔案。")
            data = {}

    data = modifier_fn(data)

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        red(f"錯誤：無法寫入 {path} ({e})")
        return False
