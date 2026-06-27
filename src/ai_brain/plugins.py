"""Global plugin installation for OpenCode & Kilo.

`ai-brain` ships its own helper plugins (e.g. `graphify.js`) that remind the
agent to consult the knowledge graph before grepping. We *could* register
them at the project level (`.opencode/opencode.json`), but that path is
relative to the project root and other developers' machines won't have the
same absolute path — leading to the `Cannot find module ... .opencode/.opencode/...`
error we hit on 2026-06-16.

The fix is to copy the plugin to a *global* per-user directory that doesn't
depend on the project path, then register it from there. The user-level
configs are:

- OpenCode: `~/.config/opencode/plugins/<name>.{ts,js}` referenced from
  `~/.config/opencode/opencode.json`'s `"plugin"` array.
- Kilo: `~/.config/kilo/command/<name>.md` (skill). The current `graphify.md`
  is already there, so this is mostly a no-op for Kilo today; left as a
  hook for future plugins.

We never *overwrite* a user-edited plugin — if the destination file already
exists and differs from the source, we skip the copy and warn.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .ui import print_blue as blue, print_green as green, print_yellow as yellow


# --- Locations -----------------------------------------------------------------

def opencode_global_dir() -> Path:
    return Path.home() / ".config" / "opencode"


def opencode_global_config() -> Path:
    return opencode_global_dir() / "opencode.json"


def kilo_global_dir() -> Path:
    return Path.home() / ".config" / "kilo"


def kilo_command_dir() -> Path:
    return kilo_global_dir() / "command"


# --- Plugin descriptors ---------------------------------------------------------
# A descriptor names a plugin we want to ship globally. We keep the
# `source` relative to the *ai-brain* package (so other machines don't have
# to clone the same repo at the same path) and a stable `target_name`.

@dataclass(frozen=True)
class OpenCodePlugin:
    """A JS/TS plugin to copy into `~/.config/opencode/plugins/`."""
    target_name: str          # e.g. "ai-brain-graphify.js"
    source: Path              # absolute path to the file we ship


# codebase-memory-mcp does not use custom OpenCode plugins.
DEFAULT_OPENCODE_PLUGINS: tuple[OpenCodePlugin, ...] = ()


# --- Per-IDE installers ---------------------------------------------------------

def _copy_plugin(plugin: OpenCodePlugin) -> Path | None:
    """Copy *plugin*.source into `~/.config/opencode/plugins/<target_name>`.

    Returns the destination path on success, None if skipped.
    """
    dest_dir = opencode_global_dir() / "plugins"
    dest = dest_dir / plugin.target_name

    if not plugin.source.is_file():
        yellow(f"⚠️ 找不到 plugin 源檔: {plugin.source}，跳過")
        return None

    dest_dir.mkdir(parents=True, exist_ok=True)

    # Idempotent: if the user has a customised version, don't clobber it.
    if dest.is_file():
        if dest.read_bytes() == plugin.source.read_bytes():
            return dest  # already up to date
        yellow(f"⚠️ {dest} 已存在且與內建版本不同,保留你的版本（強制覆寫請刪除後重跑）")
        return None

    try:
        shutil.copy(plugin.source, dest)
    except Exception as e:
        yellow(f"⚠️ 複製 {plugin.target_name} 失敗 ({e})")
        return None
    return dest


def _register_in_opencode_config(plugin_name: str) -> bool:
    """Add `"./plugins/<name>"` to `~/.config/opencode/opencode.json`'s plugin array.

    The JSON shape is `{"plugin": ["pkg-name", "./plugins/local.js", ...]}`.
    We preserve all existing entries (both string and list forms), and
    only append the local plugin if it isn't already registered.
    """
    cfg_path = opencode_global_config()
    if not cfg_path.is_file():
        yellow(f"⚠️ 找不到全域 {cfg_path}，請先啟動 OpenCode 一次讓它生成")
        return False

    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception as e:
        yellow(f"⚠️ {cfg_path} JSON 損壞 ({e})，跳過 plugin 註冊")
        return False

    plugins: list = data.get("plugin", [])
    # Normalise to list-of-strings so we can dedupe uniformly.
    if isinstance(plugins, str):
        plugins = [plugins]
    elif not isinstance(plugins, list):
        plugins = []

    target_entry = f"./plugins/{plugin_name}"
    if target_entry in plugins or any(
        isinstance(p, str) and p.endswith(plugin_name) for p in plugins
    ):
        return True  # already registered

    plugins.append(target_entry)
    data["plugin"] = plugins

    try:
        cfg_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except Exception as e:
        yellow(f"⚠️ 寫回 {cfg_path} 失敗 ({e})")
        return False
    return True


def install_opencode_plugins(plugins: Iterable[OpenCodePlugin] = DEFAULT_OPENCODE_PLUGINS) -> int:
    """Copy + register every OpenCode plugin we ship. Returns count installed."""
    blue("---> 註冊 OpenCode 全域 plugin (graphify 提示器)...")
    installed = 0
    for plugin in plugins:
        dest = _copy_plugin(plugin)
        if dest and _register_in_opencode_config(plugin.target_name):
            green(f"  ✅ {dest}")
            installed += 1
    return installed


def install_kilo_skill_stub() -> bool:
    """Best-effort: write a tiny `~/.config/kilo/command/ai-brain.md` skill.

    Kilo doesn't have a JS plugin system the way OpenCode does, but a
    `command/<name>.md` file is auto-discovered as a skill. We keep this
    minimal so the user has something to extend.
    """
    cmd_dir = kilo_command_dir()
    target = cmd_dir / "ai-brain.md"
    content = (
        "---\n"
        "description: Reminders for using the ai-brain toolchain (mempalace + codebase-memory-mcp)\n"
        "---\n\n"
        "When answering questions in this workspace:\n"
        "1. If the request is about past decisions, prior bugs, or environment config,\n"
        "   call the `mempalace` MCP tool (mempalace_search) before reading files.\n"
        "2. If the request is about code architecture or cross-file relationships,\n"
        "   prefer codebase-memory-mcp MCP tools (search_graph, trace_path, etc.) over grep / find.\n"
    )
    try:
        cmd_dir.mkdir(parents=True, exist_ok=True)
        if target.is_file() and target.read_text(encoding="utf-8") == content:
            return True  # up to date
        if target.is_file():
            yellow(f"⚠️ {target} 已存在,保留你的版本")
            return False
        target.write_text(content, encoding="utf-8")
        green(f"  ✅ {target}")
        return True
    except Exception as e:
        yellow(f"⚠️ 寫入 Kilo skill 失敗 ({e})")
        return False


# --- Uninstall -----------------------------------------------------------------

def uninstall_opencode_plugins(plugins: Iterable[OpenCodePlugin] = DEFAULT_OPENCODE_PLUGINS) -> int:
    """Remove the plugins we previously installed (best effort)."""
    removed = 0
    plugins_dir = opencode_global_dir() / "plugins"
    for plugin in plugins:
        target = plugins_dir / plugin.target_name
        if target.is_file():
            try:
                target.unlink()
                removed += 1
            except Exception:
                pass

    # Strip our entries from the opencode.json `plugin` array.
    cfg_path = opencode_global_config()
    if not cfg_path.is_file():
        return removed
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return removed
    plugins_list = data.get("plugin", [])
    if isinstance(plugins_list, list):
        data["plugin"] = [
            p for p in plugins_list
            if not (isinstance(p, str) and any(
                p.endswith(p_.target_name) for p_ in plugins
            ))
        ]
        try:
            cfg_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except Exception:
            pass
    return removed


def uninstall_kilo_skill() -> bool:
    target = kilo_command_dir() / "ai-brain.md"
    if not target.is_file():
        return False
    try:
        target.unlink()
        return True
    except Exception:
        return False
