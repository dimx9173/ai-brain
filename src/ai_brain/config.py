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


def parse_toml(content: str) -> dict[str, Any]:
    """Simple TOML parser tailored for config.toml."""
    data: dict[str, Any] = {}
    current_table = data
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            header = line[1:-1].strip()
            # Split by dot, but handle quoted parts (like projects."/path")
            parts = []
            current_part = ""
            in_quotes = False
            for char in header:
                if char == '"':
                    in_quotes = not in_quotes
                elif char == '.' and not in_quotes:
                    parts.append(current_part.strip().strip('"'))
                    current_part = ""
                else:
                    current_part += char
            if current_part:
                parts.append(current_part.strip().strip('"'))
            
            curr = data
            for part in parts:
                curr = curr.setdefault(part, {})
            current_table = curr
        elif "=" in line:
            k, v = line.split("=", 1)
            k = k.strip().strip('"')
            v = v.strip()
            parsed_v: Any = v
            if v.startswith('"') and v.endswith('"'):
                parsed_v = v[1:-1].replace('\\"', '"')
            elif v == "true":
                parsed_v = True
            elif v == "false":
                parsed_v = False
            elif v.isdigit():
                parsed_v = int(v)
            elif v.startswith("[") and v.endswith("]"):
                inside = v[1:-1].strip()
                if not inside:
                    parsed_v = []
                else:
                    parsed_v = [item.strip().strip('"') for item in inside.split(",") if item.strip()]
            elif v == "{}":
                parsed_v = {}
            current_table[k] = parsed_v
    return data


def _escape_key(k: str) -> str:
    if "." in k or "/" in k or " " in k or "-" in k:
        return f'"{k}"'
    return k


def _serialize_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    elif isinstance(v, int):
        return str(v)
    elif isinstance(v, str):
        escaped = v.replace('"', '\\"')
        return f'"{escaped}"'
    elif isinstance(v, list):
        escaped_items = []
        for item in v:
            escaped_item = item.replace('"', '\\"')
            escaped_items.append(f'"{escaped_item}"')
        return f"[{', '.join(escaped_items)}]"
    elif isinstance(v, dict) and not v:
        return "{}"
    return str(v)


def serialize_toml(data: dict[str, Any]) -> str:
    """Simple TOML serializer."""
    lines = []
    # Write flat key-values first
    for k, v in sorted(data.items()):
        if not isinstance(v, dict):
            lines.append(f"{_escape_key(k)} = {_serialize_value(v)}")
    
    def _write_tables(prefix: str, table: dict[str, Any]):
        for k, v in sorted(table.items()):
            if isinstance(v, dict):
                full_prefix = f"{prefix}.{_escape_key(k)}" if prefix else _escape_key(k)
                lines.append("")
                lines.append(f"[{full_prefix}]")
                # Write flat key-values of this table
                for sub_k, sub_v in sorted(v.items()):
                    if not isinstance(sub_v, dict):
                        lines.append(f"{_escape_key(sub_k)} = {_serialize_value(sub_v)}")
                # Recursively write nested tables
                _write_tables(full_prefix, v)

    _write_tables("", data)
    return "\n".join(lines) + "\n"


def modify_toml_file(
    path: Path | None,
    modifier_fn: Callable[[dict[str, Any]], dict[str, Any]],
) -> bool:
    """Read TOML, pass through modifier, serialize and write back."""
    if not path:
        return False

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    content = ""
    if path.is_file():
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            red(f"警告：讀取 {path} 失敗 ({e})，將會重置該檔案。")

    data: dict[str, Any] = {}
    if content:
        try:
            data = parse_toml(content)
        except Exception as e:
            red(f"警告：解析 {path} 失敗 ({e})，將會重置該檔案。")
            data = {}

    data = modifier_fn(data)

    try:
        new_content = serialize_toml(data)
        path.write_text(new_content, encoding="utf-8")
        return True
    except Exception as e:
        red(f"錯誤：無法寫入 {path} ({e})")
        return False
