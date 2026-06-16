"""Top-level command implementations.

Each public function here maps 1:1 to a CLI subcommand. They orchestrate
the lower-level managers (registry, git_hooks, mcp, verifier, cron) and
delegate I/O. Keep functions small; favour calling helpers over inline
logic.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import git_hooks
from . import registry
from .constants import (
    APP_EMOJI,
    APP_NAME,
    GRAPHIFY_OUT_DIR,
    GRAPHIFY_TOOLS,
    HOOKS_CONFIG,
    LAST_SWEEP_FILE,
    LOCAL_CLAUDE_MD_TEMPLATE,
    LOCAL_GRAPHIFY_SKILL,
    PROJECT_CLAUDE_MD,
    PROJECT_CONFIG_FILE,
    PROJECT_MEMPALACE_FILES,
    SWEEP_BACKGROUND_GAP_SECONDS,
    TOOL_GRAPHIFY,
    TOOL_MEMPALACE,
)
from .mcp import configure_minimax_provider, deregister_all, register_all
from .ui import blue, green, red, yellow


# --- init / clean / full-init ---------------------------------------------------

def init_brain() -> bool:
    print(blue(f"====== {APP_EMOJI} 開始初始化專案 AI 大腦配置 ======"))

    if not _run_mempalace_init():
        return False

    if not _run_graphify_install():
        return False

    if not _write_project_hooks_config():
        return False

    if not _write_project_claude_md():
        return False

    git_hooks.install()

    print(green("====== 🎉 專案 AI 大腦配置成功！ ======"))
    print("💡 提示: 已自動配置 Git Hook，當您進行 git pull (merge) 或 git checkout 時，圖譜將自動在背景更新！")
    print("💡 提示: 手動重建圖譜只需執行: ai-brain start")
    registry.register_current()
    return True


def full_init(paths) -> bool:
    if not init_brain():
        return False

    print()
    from . import cron
    cron.install()

    register_all(paths)
    configure_minimax_provider(paths)

    from . import plugins
    plugins.install_opencode_plugins()
    plugins.install_kilo_skill_stub()

    _maybe_append_global_claude_md()
    print(green("====== 🎉 終極全自動大腦配置完成！ ======"))
    return True


def clean_brain() -> bool:
    print(blue("====== 🗑️ 開始清除此專案的 AI 大腦配置 ======"))

    for filename in PROJECT_MEMPALACE_FILES:
        p = Path(filename)
        if p.is_file():
            print("--> 移除 MemPalace 專案配置文件...")
            try:
                p.unlink()
            except Exception:
                pass

    _remove_path(Path(GRAPHIFY_OUT_DIR), is_dir=True, message="--> 移除 Graphify 輸出目錄...")
    _remove_path(Path(LOCAL_GRAPHIFY_SKILL), is_dir=True, message="--> 移除 .claude 中的 Graphify 技能...")

    _uninstall_graphify_tool_configs()

    _remove_path(Path(PROJECT_CONFIG_FILE), is_dir=False, message="--> 移除 .claude/config.json 記憶生命週期鉤子...")
    _remove_path(Path(".claude/settings.json"), is_dir=False, message="--> 移除 .claude/settings.json 中的 Hook 註冊...")

    _maybe_rmdir_empty(Path(".claude"))

    _maybe_unlink_claude_md()

    git_hooks.uninstall()
    registry.deregister_current()
    print(green("====== 🎉 專案 AI 大腦配置已成功清除！ ======"))
    return True


def uninstall_all(paths) -> bool:
    clean_brain()
    print()
    print(blue("====== 🗑️ 開始解除安裝全域 AI 大腦配置與定時排程 ======"))

    from . import cron
    cron.uninstall()

    target = Path.home() / ".local" / "bin" / "ai-brain"
    if target.exists():
        try:
            target.unlink()
        except Exception as e:
            print(red(f"❌ 移除失敗 ({e})"))

    from .mcp import remove_minimax_provider
    remove_minimax_provider(paths)
    deregister_all(paths)

    from . import plugins
    plugins.uninstall_opencode_plugins()
    plugins.uninstall_kilo_skill()

    _remove_global_cognitive_principles()

    print(green("====== 🎉 全域 AI 大腦配置已完成解除安裝！ ======"))
    print("💡 提示: 若要完全移除相關 CLI 套件，您可以手動執行以下指令：")
    print("  uv tool uninstall mempalace")
    print("  uv tool uninstall claude-mem")
    print("  uv tool uninstall graphifyy")
    return True


# --- start / stop / status ------------------------------------------------------

def start_day() -> bool:
    """Morning routine: kick off a background sweep if stale, then refresh graph."""
    if _should_run_background_sweep():
        print(yellow("--> 偵測到大於 12 小時未進行對話歸檔，正在背景自動沉澱昨日對話記憶..."))
        try:
            subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "stop"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            print(red(f"警告：背景歸檔啟動失敗 ({e})"))

    print(blue("====== 🌅 晨間啟動：建立/更新最新代碼地圖 ======"))
    if not _run_graphify_extract():
        return False
    print(green("✅ 代碼圖譜更新完成！AI 代理們現在能使用最新、最省 Token 的全景地圖了。"))
    return True


def stop_day() -> bool:
    """Evening routine: archive today's conversations to the long-term palace."""
    print(blue("====== 🌇 下班收尾：記憶封存與去衝突歸檔 ======"))
    print(yellow("正在掃描今日所有工具產生的對話歷史，並安全打包存入長期記憶宮殿..."))
    _run_archive_sweep(silent=False)
    _record_sweep_timestamp()
    print(green("✅ 長期記憶歸檔完成！無死鎖風險，您可以安全關閉所有終端機。"))
    return True


def check_status() -> bool:
    print(blue("====== 📊 專案 AI 大腦狀態檢查 ======"))
    from .ui import GREEN, RED, YELLOW, NC

    def status_line(label: str, ok: bool, color_ok: str = GREEN, missing_msg: str = "未初始化") -> None:
        marker = f"{color_ok}已配置 (Active){NC}" if ok else f"{RED}{missing_msg}{NC}"
        print(f"{label}: {marker}")

    mempalace_ok = any(Path(p).exists() for p in (".mempalace", "mempalace.json", "mempalace.yaml"))
    status_line("MemPalace 狀態", mempalace_ok, missing_msg="未初始化")

    graphify_ok = Path("graphify-out").is_dir()
    status_line("Graphify 地圖", graphify_ok, color_ok=GREEN,
                missing_msg="尚未生成地圖 (請執行 ai-brain start)")

    claude_md_ok = Path(PROJECT_CLAUDE_MD).is_file()
    status_line("CLAUDE.md 指南", claude_md_ok, missing_msg="遺失")

    hooks_ok = Path(PROJECT_CONFIG_FILE).is_file()
    status_line("記憶生命週期鉤子", hooks_ok, missing_msg="未配置")

    enabled = registry.is_archived(registry.current_project_path())
    color = GREEN if enabled else RED
    suffix = "已啟用 (Active)" if enabled else "停用中 (預設不歸檔)"
    print(f"定時自動歸檔: {color}{suffix}{NC}")
    return True


# --- include / exclude ----------------------------------------------------------
# Special tokens recognised by `_resolve_target` in addition to the existing
# "." / "current" / keyword behaviour.
_ALL_TOKEN = "all"
_BULK_TOKENS = (_ALL_TOKEN,)


def manage_exclude(pattern: str | None) -> bool:
    if not pattern:
        _print_archive_status()
        return True

    if pattern.lower() in _BULK_TOKENS:
        # `exclude all` ≡ `exclude-all` — disable every archived project.
        return registry.clear_archive()

    target = _resolve_target(pattern)
    if not target:
        return False
    return registry.disable_archive(target)


def manage_include(pattern: str | None) -> bool:
    if not pattern:
        # `include` with no pattern — show the same status list as `exclude`
        # so the user can pick a target.
        _print_archive_status()
        return True

    # `include all` ≡ `include-all` — enable every active project.
    if pattern.lower() in _BULK_TOKENS:
        return registry.archive_all_active()

    target = _resolve_target(pattern)
    if not target:
        return False
    return registry.enable_archive(target)


def exclude_all() -> bool:
    print(blue("====== 🗑️ 一鍵停用所有專案自動歸檔 ======"))
    return registry.clear_archive()


def include_all() -> bool:
    print(blue("====== 🌅 一鍵啟用所有專案自動歸檔 ======"))
    return registry.archive_all_active()


def manage_list() -> bool:
    """Show the auto-archive status of the **current** project only.

    Differs from `ai-brain include` / `ai-brain exclude` (no args), which
    print the *full* active project list with 1-based indices for
    subsequent index-based operations. `list` is a quick at-a-glance
    check: "is THIS project on or off?".

    Returns True on success, False if the current project is not
    registered (the user is in an unregistered directory).
    """
    from .ui import GREEN, RED, NC
    RST = NC  # alias to match the style used in `_print_archive_status`
    proj_path = registry.current_project_path()
    active = registry.list_active()
    if proj_path not in active:
        print(red("⚠️ 當前目錄未在 AI 大腦活躍清單中註冊。"))
        return False

    archived = registry.is_archived(proj_path)
    base = Path(proj_path).name
    if archived:
        print(blue("====== 📋 當前專案自動歸檔狀態 ======"))
        print(f"  專案: {base} ({proj_path})")
        print(f"  狀態: {GREEN}已啟用自動歸檔 (Active){NC}")
        print()
        print(yellow("若要停用: "), end="")
        print(f"{GREEN}ai-brain exclude current{RST}")
    else:
        print(blue("====== 📋 當前專案自動歸檔狀態 ======"))
        print(f"  專案: {base} ({proj_path})")
        print(f"  狀態: {RED}預設不歸檔 (Inactive){NC}")
        print()
        print(yellow("若要啟用: "), end="")
        print(f"{GREEN}ai-brain include current{RST}")
    return True


# --- Internal helpers -----------------------------------------------------------

def _run_mempalace_init() -> bool:
    try:
        subprocess.run([TOOL_MEMPALACE, "init", "--yes", "--auto-mine", "--no-llm", "."], check=True)
        return True
    except FileNotFoundError:
        print(red("錯誤：未找到 mempalace 工具，請先執行: uv tool install mempalace --force"))
        return False
    except subprocess.CalledProcessError as e:
        print(red(f"錯誤：mempalace 初始化失敗 ({e})"))
        return False


def _run_graphify_install() -> bool:
    print_yellow("--> 安裝 Graphify 本地技能與各工具引導規則...")
    try:
        subprocess.run([TOOL_GRAPHIFY, "install", "--project"], check=True)
        for tool in GRAPHIFY_TOOLS:
            subprocess.run([TOOL_GRAPHIFY, tool, "install"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except FileNotFoundError:
        print(red("錯誤：未找到 graphify 工具，請先執行: uv tool install graphifyy --force"))
        return False
    except Exception as e:
        print(yellow(f"--> 安裝 Graphify 部分規則時發生警告 ({e})"))
        return True  # partial install is still considered success


def _write_project_hooks_config() -> bool:
    print_yellow("--> 設定 .claude/config.json 記憶生命週期鉤子...")
    try:
        Path(".claude").mkdir(exist_ok=True)
        with open(PROJECT_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(HOOKS_CONFIG, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(red(f"錯誤：寫入生命週期鉤子失敗 ({e})"))
        return False


def _write_project_claude_md() -> bool:
    print_yellow("--> 建立 CLAUDE.md 大腦引導指南...")
    try:
        with open(PROJECT_CLAUDE_MD, "w", encoding="utf-8") as f:
            f.write(LOCAL_CLAUDE_MD_TEMPLATE)
        return True
    except Exception as e:
        print(red(f"錯誤：建立 CLAUDE.md 失敗 ({e})"))
        return False


def _uninstall_graphify_tool_configs() -> None:
    print("--> 移除各工具的 Graphify 本地配置...")
    try:
        for tool in GRAPHIFY_TOOLS:
            subprocess.run([TOOL_GRAPHIFY, tool, "uninstall"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _remove_path(path: Path, *, is_dir: bool, message: str) -> None:
    if (path.is_dir() if is_dir else path.is_file()):
        print(message)
        try:
            if is_dir:
                shutil.rmtree(path)
            else:
                path.unlink()
        except Exception:
            pass


def _maybe_rmdir_empty(path: Path) -> None:
    if path.is_dir() and not any(path.iterdir()):
        try:
            path.rmdir()
        except Exception:
            pass


def _maybe_unlink_claude_md() -> None:
    claude_md = Path(PROJECT_CLAUDE_MD)
    if not claude_md.is_file():
        return
    print("--> 清理 CLAUDE.md 導引指南...")
    try:
        content = claude_md.read_text(encoding="utf-8")
    except Exception:
        return
    if "AI Agent 認知工作流與大腦記憶指引" in content or "AI Agent 大腦與記憶指引" in content:
        try:
            claude_md.unlink()
        except Exception:
            pass


def _remove_global_cognitive_principles() -> None:
    """Strip the global cognitive principles block from ~/.claude/CLAUDE.md."""
    from .constants import COGNITIVE_PRINCIPLES_MARKER
    global_md = Path.home() / ".claude" / "CLAUDE.md"
    if not global_md.is_file():
        return
    print("--> 自 ~/.claude/CLAUDE.md 移除全域大腦引導原則...")
    try:
        content = global_md.read_text(encoding="utf-8")
        if COGNITIVE_PRINCIPLES_MARKER not in content:
            return
        prefix = content.split(COGNITIVE_PRINCIPLES_MARKER)[0].rstrip()
        global_md.write_text(prefix + "\n", encoding="utf-8")
        print("Successfully removed global cognitive rules from ~/.claude/CLAUDE.md")
    except Exception as e:
        print(red(f"警告：更新 ~/.claude/CLAUDE.md 失敗 ({e})"))


def _maybe_append_global_claude_md() -> None:
    """Append the cognitive principles block to ~/.claude/CLAUDE.md if absent."""
    from .constants import COGNITIVE_PRINCIPLES_BLOCK, COGNITIVE_PRINCIPLES_MARKER
    claude_dir = Path.home() / ".claude"
    if not claude_dir.is_dir():
        return
    print_yellow("---> 自動設定 ~/.claude/CLAUDE.md 全域大腦引導指南...")
    global_md = claude_dir / "CLAUDE.md"
    existing = global_md.read_text(encoding="utf-8") if global_md.exists() else ""
    if COGNITIVE_PRINCIPLES_MARKER in existing:
        print("Global cognitive rules already present in ~/.claude/CLAUDE.md, skipped")
        return
    try:
        with open(global_md, "a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write(COGNITIVE_PRINCIPLES_BLOCK.strip() + "\n")
        print("Successfully appended global cognitive rules to ~/.claude/CLAUDE.md")
    except Exception as e:
        print(red(f"警告：寫入全域引導失敗 ({e})"))


def _should_run_background_sweep() -> bool:
    """Return True if more than SWEEP_BACKGROUND_GAP_SECONDS have elapsed."""
    if not LAST_SWEEP_FILE().is_file():
        return True
    try:
        last = int(LAST_SWEEP_FILE().read_text().strip())
    except Exception:
        return True
    return (int(time.time()) - last) > SWEEP_BACKGROUND_GAP_SECONDS


def _record_sweep_timestamp() -> None:
    try:
        LAST_SWEEP_FILE().parent.mkdir(parents=True, exist_ok=True)
        LAST_SWEEP_FILE().write_text(str(int(time.time())) + "\n")
    except Exception:
        pass


def _run_graphify_extract() -> bool:
    candidates = [TOOL_GRAPHIFY, "./node_modules/.bin/graphify", "/graphify"]
    for cmd in candidates:
        is_local = cmd.startswith(".")
        if is_local:
            if not Path(cmd).is_file():
                continue
        elif not shutil.which(cmd):
            continue
        try:
            subprocess.run([cmd, "."], check=True)
            return True
        except Exception:
            continue
    print(red("⚠️ 未能自動執行 graphify，請確認是否有安裝全域 graphifyy 工具。"))
    return False


def _run_archive_sweep(*, silent: bool) -> None:
    """Sweep every project in the auto-archive whitelist."""
    archived = registry.list_archived()
    if not archived:
        if not silent:
            print(yellow("--> 自動歸檔白名單為空，不執行任何自動歸檔。"))
            print("💡 提示: 若要為此專案啟用自動定時歸檔，請執行: ai-brain include")
        return

    if not silent:
        print(yellow("--> 讀取自動歸檔白名單，僅自動歸檔啟用之專案..."))

    for proj_path in archived:
        proj_dir = Path(proj_path)
        if not proj_dir.is_dir():
            continue
        target = find_claude_folder_by_path(proj_path)
        if not target:
            continue
        if not silent:
            print(green(f"--> 掃描歸檔活躍專案: {target.name}"))
        try:
            stdout = subprocess.DEVNULL if silent else None
            stderr = subprocess.DEVNULL if silent else None
            subprocess.run([TOOL_MEMPALACE, "sweep", str(target)], stdout=stdout, stderr=stderr)
        except Exception as e:
            if not silent:
                print(red(f"警告：歸檔 {target.name} 失敗 ({e})"))


def find_claude_folder_by_path(proj_path: str) -> Path | None:
    """Map a project path to its ~/.claude/projects/<key>/ entry, if any."""
    pattern = str(Path(proj_path).resolve()).replace("/", "-").replace("_", "-").lower().strip("-")
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.is_dir():
        return None
    for proj_dir in projects_dir.iterdir():
        if not proj_dir.is_dir():
            continue
        name = proj_dir.name.lower()
        if pattern in name or name in pattern:
            return proj_dir
    return None


def _print_archive_status() -> None:
    print(blue("====== 🗂️ 全域自動記憶歸檔狀態清單 ======"))
    print("目前註冊的專案與其自動歸檔狀態：\n")
    from .ui import GREEN, RED, NC
    enabled = set(registry.list_archived())
    for i, proj_path in enumerate(registry.list_active(), 1):
        base = Path(proj_path).name
        if proj_path in enabled:
            print(f"  [{i}] {GREEN}[ 已啟用自動歸檔 (Active) ]{NC} {base} ({proj_path})")
        else:
            print(f"  [{i}] {RED}[ 預設不歸檔 (Inactive) ]{NC} {base} ({proj_path})")
    print()
    print(yellow("用法提示:"))
    from .ui import GREEN as G, NC as RST
    print(f"  啟用當前專案自動歸檔: {G}ai-brain include{RST}")
    print(f"  停用當前專案自動歸檔: {G}ai-brain exclude current{RST} 或 {G}ai-brain exclude .{RST}")
    print(f"  啟用指定專案自動歸檔: {G}ai-brain include [專案關鍵字|編號|all]{RST}")
    print(f"  停用指定專案自動歸檔: {G}ai-brain exclude [專案關鍵字|編號|all]{RST}")
    print(f"  全部啟用: {G}ai-brain include all{RST}  (同 {G}include-all{RST})")
    print(f"  全部停用: {G}ai-brain exclude all{RST}  (同 {G}exclude-all{RST})\n")


def _resolve_target(pattern: str | None) -> str | None:
    """Resolve `exclude [pattern]` / `include [pattern]` to a project path.

    Accepts, in order of precedence:
    - "." / "current" → the current working directory (must be registered)
    - a positive integer (1-based) → that position in the active list,
      matching the numbers shown by `ai-brain exclude` (no args)
    - any other string → substring match against the active list
    """
    if not pattern or pattern in (".", "current"):
        proj_path = registry.current_project_path()
        if proj_path not in registry.list_active():
            print(red("⚠️ 當前目錄未在 AI 大腦活躍清單中註冊。"))
            return None
        return proj_path

    # 1-based numeric index — checked *before* keyword matching so "2"
    # never accidentally matches a project whose path contains "2".
    if pattern.isdigit():
        idx = int(pattern)
        target = registry.find_active_by_index(idx)
        if target is None:
            active_count = len(registry.list_active())
            print(red(
                f'⚠️ 編號 {idx} 超出範圍:活躍名單只有 {active_count} 個專案。'
            ))
            print(yellow("💡 執行 `ai-brain exclude` 查看編號清單。"))
            return None
        return target

    target = registry.find_active_by_keyword(pattern)
    if not target:
        print(red(f'⚠️ 活躍專案清單中找不到匹配關鍵字 "{pattern}" 的專案。'))
        return None
    return target


def print_yellow(text: str) -> None:
    """Local re-export to avoid import shadowing in this module."""
    from .ui import yellow as _yellow
    print(_yellow(text))
