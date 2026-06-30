"""Top-level command implementations.

Each public function here maps 1:1 to a CLI subcommand. They orchestrate
the lower-level managers (registry, git_hooks, mcp, verifier, cron) and
delegate I/O. Keep functions small; favour calling helpers over inline
logic.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import git_hooks, registry
from .constants import (
    APP_EMOJI,
    CODEBASE_MEMORY_OUT_DIR,
    COGNITIVE_PRINCIPLES_BLOCK,
    COGNITIVE_PRINCIPLES_MARKER,
    GC_BACKGROUND_GAP_SECONDS,
    HOOK_BEGIN_MARKER,
    HOOKS_CONFIG,
    LAST_GC_FILE,
    LAST_SWEEP_FILE,
    LOCAL_CLAUDE_MD_TEMPLATE,
    LOCAL_CODEBASE_MEMORY_SKILL,
    PROJECT_CLAUDE_MD,
    PROJECT_CONFIG_FILE,
    PROJECT_MEMPALACE_FILES,
    SWEEP_BACKGROUND_GAP_SECONDS,
    TOOL_CODEBASE_MEMORY,
    TOOL_MEMPALACE,
)
from .mcp import deregister_all, register_all
from .ui import BLUE, GREEN, NC, RED, YELLOW, blue, green, red, yellow

# --- Global serialisation lock --------------------------------------------------

# Re-entrance tracking: if the current thread already holds the lock, subsequent
# _acquire_brain_lock() calls from within the same thread return the same fd
# without re-issuing flock (which would deadlock via LOCK_NB).
# We use RLock so that the same thread can re-enter _acquire_brain_lock safely.
import threading as _thr
_lock_re = _thr.RLock()
_lock_state = {"depth": 0, "fd": None}


def _acquire_brain_lock() -> int | None:
    """Acquire exclusive flock on ~/.claude/ai_brain.lock. Returns fd on success, None on failure.
    Re-entrant: if the current thread already holds the lock, returns the existing fd."""
    import fcntl
    with _lock_re:
        if _lock_state["depth"] > 0:
            _lock_state["depth"] += 1
            return _lock_state["fd"]

        lock_path = Path.home() / ".claude" / "ai_brain.lock"
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        try:
            fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        except OSError as e:
            print(yellow(f"[ WARN ] 無法建立全域鎖檔 ({e})，跳過序列化"))
            return None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _lock_state["depth"] = 1
            _lock_state["fd"] = fd
            return fd
        except (BlockingIOError, OSError):
            os.close(fd)
            return None


def _release_brain_lock(fd: int | None) -> None:
    """Release flock and close fd. No-op if fd is None.
    Re-entrant: decrements depth; only actually unlocks when depth hits 0."""
    if fd is None:
        return
    import fcntl
    with _lock_re:
        if _lock_state["depth"] > 1:
            _lock_state["depth"] -= 1
            return
        _lock_state["depth"] = 0
        _lock_state["fd"] = None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        except OSError:
            pass


# --- Cron collision guard -------------------------------------------------------

def _stop_pid_path() -> Path:
    return Path.home() / ".claude" / "ai_brain_stop.pid"


def _another_stop_running() -> bool:
    pid_path = _stop_pid_path()
    if not pid_path.is_file():
        return False
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        pid_path.unlink(missing_ok=True)
        return False
    # Check if process is alive
    try:
        os.kill(pid, 0)
    except OSError:
        pid_path.unlink(missing_ok=True)
        return False
    return True


# --- init / clean / full-init ---------------------------------------------------

def init_brain() -> bool:
    print(blue(f"====== {APP_EMOJI} 開始初始化專案 AI 大腦配置 ======"))

    _ensure_codebase_memory_ignored()

    if not _run_mempalace_init():
        return False

    if not _run_codebase_memory_init():
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

    _remove_path(Path(CODEBASE_MEMORY_OUT_DIR), is_dir=True, message="--> 移除 Codebase-Memory 輸出目錄...")
    _remove_path(Path(LOCAL_CODEBASE_MEMORY_SKILL), is_dir=True, message="--> 移除 .claude 中的 Codebase-Memory 技能...")

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

    for name in ("ai-brain",):
        target = Path.home() / ".local" / "bin" / name
        if target.exists():
            try:
                target.unlink()
            except Exception as e:
                print(red(f"❌ 移除 {name} 失敗 ({e})"))

    deregister_all(paths)

    from . import plugins
    plugins.uninstall_opencode_plugins()
    plugins.uninstall_kilo_skill()

    _remove_global_cognitive_principles()

    print(green("====== 🎉 全域 AI 大腦配置已完成解除安裝！ ======"))
    print("💡 提示: 若要完全移除相關 CLI 套件，您可以手動執行以下指令：")
    print("  uv tool uninstall mempalace")
    print("  uv tool uninstall claude-mem")
    print("  uv tool uninstall codebase-memory-mcp")
    return True


# --- start / stop / status ------------------------------------------------------

def start_day(fast: bool = False) -> bool:
    """Morning routine: kick off a background sweep if stale, then refresh graph."""
    lock_fd = _acquire_brain_lock()
    if lock_fd is None:
        print(yellow("[ WARN ] 另一個 ai-brain 正在執行，跳過 start_day"))
        return False
    try:
        _ensure_codebase_memory_ignored()
        if _should_run_background_sweep():
            print(yellow("--> 偵測到大於 12 小時未進行對話歸檔，正在背景自動沉澱昨日對話記憶..."))
            proc = None
            try:
                proc = subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve()), "stop"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as e:
                print(red(f"警告：背景歸檔啟動失敗 ({e})"))
            else:
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                except Exception:
                    pass

        # Idle-based GC: trigger background GC if >7 days since last one
        if _should_run_gc():
            print(yellow("--> 偵測到距上次 GC 已超过 7 天，正在背景執行記憶宮殿垃圾回收..."))
            try:
                subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve()), "gc", "--apply"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as e:
                print(red(f"警告：背景 GC 啟動失敗 ({e})"))

        print(blue("====== 🌅 晨間啟動：建立/更新最新代碼地圖 ======"))
        if not _run_codebase_memory_index():
            return False
        print(green("✅ 代碼圖譜更新完成！AI 代理們現在能使用最新、最省 Token 的全景地圖了。"))
        return True
    finally:
        _release_brain_lock(lock_fd)


def stop_day() -> bool:
    """Evening routine: archive today's conversations to the long-term palace."""
    lock_fd = _acquire_brain_lock()
    if lock_fd is None:
        print(yellow("[ WARN ] 另一個 ai-brain 正在執行，跳過 stop_day"))
        return False
    try:
        print(blue("====== 🌇 下班收尾：記憶封存與去衝突歸檔 ======"))
        print(yellow("正在掃描今日所有工具產生的對話歷史，並安全打包存入長期記憶宮殿..."))
        _run_archive_sweep(silent=False)
        _record_sweep_timestamp()
        print(green("✅ 長期記憶歸檔完成！無死鎖風險，您可以安全關閉所有終端機。"))
        return True
    finally:
        _release_brain_lock(lock_fd)


def check_status() -> bool:
    _ensure_codebase_memory_ignored()
    print(blue("====== 📊 專案 AI 大腦狀態檢查 ======"))
    from .ui import GREEN, NC, RED

    def status_line(label: str, ok: bool, color_ok: str = GREEN, missing_msg: str = "未初始化") -> None:
        marker = f"{color_ok}已配置 (Active){NC}" if ok else f"{RED}{missing_msg}{NC}"
        print(f"{label}: {marker}")

    mempalace_ok = any(Path(p).exists() for p in (".mempalace", "mempalace.json", "mempalace.yaml"))
    status_line("MemPalace 狀態", mempalace_ok, missing_msg="未初始化")

    proj_name = str(Path.cwd().resolve()).replace("/", "-").replace("_", "-").lower().strip("-")
    try:
        res = subprocess.run(
            ["codebase-memory-mcp", "cli", "list_projects"],
            capture_output=True, text=True, timeout=30,
        )
        codebase_memory_ok = proj_name in res.stdout.lower() or "active" in res.stdout.lower()
    except subprocess.TimeoutExpired:
        codebase_memory_ok = False
    except Exception:
        codebase_memory_ok = False
    status_line("Codebase-Memory 地圖", codebase_memory_ok, color_ok=GREEN,
                missing_msg="尚未生成地圖 (請執行 ai-brain start)")

    claude_md_ok = Path(PROJECT_CLAUDE_MD).is_file()
    status_line("CLAUDE.md 指南", claude_md_ok, missing_msg="遺失")

    hooks_ok = Path(PROJECT_CONFIG_FILE).is_file()
    status_line("記憶生命週期鉤子", hooks_ok, missing_msg="未配置")

    enabled = registry.is_archived(registry.current_project_path())
    color = GREEN if enabled else RED
    suffix = "已啟用 (Active)" if enabled else "停用中 (預設不歸檔)"
    print(f"定時自動歸檔: {color}{suffix}{NC}")

    # Palace capacity section
    print()
    print(blue("====== 📊 Palace 容量 ======"))
    palace_dir = Path.home() / ".mempalace" / "palace"
    needs_maintenance = False
    needs_urgent = False

    # Chroma DB size
    chroma_db = palace_dir / "chroma.sqlite3"
    if chroma_db.is_file():
        try:
            db_bytes = chroma_db.stat().st_size
            db_gb = db_bytes / (1024 ** 3)
            db_mb = db_bytes / (1024 ** 2)
            if db_gb > 2:
                db_display = f"{RED}{db_gb:.2f} GB{NC}"
                needs_urgent = True
            elif db_gb > 1:
                db_display = f"{YELLOW}{db_gb:.2f} GB{NC}"
                needs_maintenance = True
            else:
                db_display = f"{GREEN}{db_mb:.0f} MB{NC}"
            print(f"ChromaDB 大小: {db_display}")
        except Exception:
            print(f"ChromaDB 大小: {RED}無法讀取{NC}")
    else:
        print(f"ChromaDB 大小: {NC}未初始化")

    # HNSW index size
    hnsw_found = False
    hnsw_total = 0
    if palace_dir.is_dir():
        for level0 in palace_dir.rglob("data_level0.bin"):
            try:
                hnsw_total += level0.stat().st_size
                hnsw_found = True
            except Exception:
                pass
    if hnsw_found:
        hnsw_mb = hnsw_total / (1024 ** 2)
        if hnsw_mb > 500:
            hnsw_display = f"{YELLOW}{hnsw_mb:.0f} MB{NC}"
            needs_maintenance = True
        else:
            hnsw_display = f"{GREEN}{hnsw_mb:.0f} MB{NC}"
        print(f"HNSW 索引大小: {hnsw_display}")
    else:
        print("HNSW 索引大小: 未建立")

    # Embedding count
    if chroma_db.is_file():
        try:
            import sqlite3
            conn = sqlite3.connect(str(chroma_db))
            cursor = conn.execute("SELECT COUNT(*) FROM embeddings")
            embed_count = cursor.fetchone()[0]
            conn.close()
            if embed_count > 500000:
                embed_display = f"{YELLOW}{embed_count:,}{NC}"
                needs_maintenance = True
            else:
                embed_display = f"{GREEN}{embed_count:,}{NC}"
            print(f"Embedding 數量: {embed_display}")
        except Exception:
            print(f"Embedding 數量: {RED}無法查詢{NC}")
    else:
        print("Embedding 數量: 無資料庫")

    # Drift backup presence
    drift_dirs = list(palace_dir.glob("*.drift-*")) if palace_dir.is_dir() else []
    if drift_dirs:
        drift_display = f"{YELLOW}{len(drift_dirs)} 個{NC}"
        needs_maintenance = True
    else:
        drift_display = f"{GREEN}無{NC}"
    print(f"Drift 備份: {drift_display}")

    # Health assessment
    if needs_urgent:
        health = f"{RED}🔴 需要立即維護{NC}"
    elif needs_maintenance:
        health = f"{YELLOW}⚠️ 建議維護{NC}"
    else:
        health = f"{GREEN}OK{NC}"
    print(f"健康評估: {health}")

    return True


def run_gc(apply: bool = False) -> bool:
    """Garbage-collect the MemPalace: clean drift backups, sync, compress."""
    lock_fd = _acquire_brain_lock()
    if lock_fd is None:
        print(yellow("[ WARN ] 另一個 ai-brain 正在執行，無法執行 gc"))
        return False

    try:
        print(blue("====== 🗑️ 開始 Palace 垃圾回收 ======"))
        palace_dir = Path.home() / ".mempalace" / "palace"

        # -- Step 1: Clean drift backups --
        print(blue("1. 掃描 drift 備份..."))
        drift_dirs = sorted(palace_dir.glob("*.drift-*")) if palace_dir.is_dir() else []
        if drift_dirs:
            total_drift = 0
            for d in drift_dirs:
                try:
                    size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                    total_drift += size
                    size_mb = size / (1024 ** 2)
                    print(f"  {d.name}: {size_mb:.1f} MB")
                except Exception:
                    print(f"  {d.name}: (無法計算大小)")
            drift_mb = total_drift / (1024 ** 2)
            if apply:
                for d in drift_dirs:
                    try:
                        shutil.rmtree(d)
                    except Exception as e:
                        print(red(f"  [ ERROR ] 無法移除 {d.name} ({e})"))
                print(green(f"  已清除 {len(drift_dirs)} 個 drift 備份 (釋放 {drift_mb:.1f} MB)"))
            else:
                print(yellow(f"  發現 {len(drift_dirs)} 個 drift 備份 (共 {drift_mb:.1f} MB)，使用 --apply 清除"))
        else:
            print(green("  無 drift 備份"))

        print()

        # -- Step 2: mempalace sync --
        print(blue("2. 執行記憶庫同步..."))
        sync_cmd = [TOOL_MEMPALACE, "sync"]
        if apply:
            sync_cmd.append("--apply")
        sync_cmd.append(".")
        try:
            res = subprocess.run(sync_cmd, capture_output=True, text=True, timeout=120)
            if res.returncode == 0:
                print(green("  記憶庫同步完成"))
            else:
                print(yellow(f"  記憶庫同步結束 (exit code {res.returncode})"))
                if res.stderr:
                    print(yellow(f"  {res.stderr.strip()}"))
        except subprocess.TimeoutExpired:
            print(yellow("  [ TIMEOUT ] mempalace sync 已逾時 (>120s)"))
        except FileNotFoundError:
            print(yellow("  [ WARN ] 未找到 mempalace CLI，跳過"))
        except Exception as e:
            print(red(f"  [ ERROR ] mempalace sync 失敗 ({e})"))

        print()

        # -- Step 3: Show chroma DB size before --
        chroma_db = palace_dir / "chroma.sqlite3"
        size_before = 0
        if chroma_db.is_file():
            try:
                size_before = chroma_db.stat().st_size
                size_before_mb = size_before / (1024 ** 2)
                print(blue(f"3. 壓縮前 ChromaDB 大小: {size_before_mb:.1f} MB"))
            except Exception:
                print(blue("3. 壓縮前 ChromaDB 大小: 無法讀取"))
        else:
            print(blue("3. ChromaDB 尚未初始化"))

        print()

        # -- Step 4: mempalace compress --
        print(blue("4. 執行 ChromaDB 壓縮..."))
        compress_cmd = [TOOL_MEMPALACE, "compress"]
        if not apply:
            compress_cmd.append("--dry-run")
        try:
            res = subprocess.run(compress_cmd, capture_output=True, text=True, timeout=300)
            if res.returncode == 0:
                if apply:
                    print(green("  ChromaDB 壓縮完成"))
                else:
                    print(green("  ChromaDB 壓縮預演完成（未實際修改）"))
                    print(yellow("  使用 --apply 以實際執行壓縮"))
            else:
                print(yellow(f"  壓縮結束 (exit code {res.returncode})"))
                if res.stderr:
                    print(yellow(f"  {res.stderr.strip()}"))
        except subprocess.TimeoutExpired:
            print(yellow("  [ TIMEOUT ] mempalace compress 已逾時 (>300s)"))
        except FileNotFoundError:
            print(yellow("  [ WARN ] 未找到 mempalace CLI，跳過"))
        except Exception as e:
            print(red(f"  [ ERROR ] mempalace compress 失敗 ({e})"))

        print()

        # -- Step 5: Show chroma DB size after --
        size_after = 0
        if chroma_db.is_file():
            try:
                size_after = chroma_db.stat().st_size
                size_after_mb = size_after / (1024 ** 2)
                print(blue(f"5. 壓縮後 ChromaDB 大小: {size_after_mb:.1f} MB"))
            except Exception:
                print(blue("5. 壓縮後 ChromaDB 大小: 無法讀取"))
        else:
            print(blue("5. ChromaDB 不存在"))

        # -- Summary --
        print()
        print(blue("====== 📊 GC 摘要 ======"))
        if size_before > 0 and size_after > 0:
            delta = size_before - size_after
            delta_mb = delta / (1024 ** 2)
            if delta > 0:
                print(green(f"  釋放空間: {delta_mb:.1f} MB"))
            elif delta == 0:
                print(yellow("  資料庫大小無變化"))
            else:
                print(yellow(f"  資料庫增長: {abs(delta_mb):.1f} MB"))
        elif size_before == 0 and size_after == 0:
            print(yellow("  無 ChromaDB 需要處理"))

        if not apply:
            print()
            print(yellow("💡 以上為預演模式。確認無誤後請執行: ai-brain gc --apply"))

        if apply:
            _record_gc_timestamp()

        print(green("====== 🎉 垃圾回收結束 ======"))
        return True
    finally:
        _release_brain_lock(lock_fd)


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


def manage_remove(pattern: str | None) -> bool:
    """Remove a project from the active registry list."""
    if not pattern:
        print(blue("====== ❌ 註銷 AI 大腦活躍專案 ======"))
        _print_archive_status()
        print(yellow("用法提示: "), end="")
        print(f"請指定要移除 the 專案，例如: {green('ai-brain remove [編號|關鍵字|current|all]')}")
        return True

    if pattern.lower() == "all":
        print(blue("====== ❌ 一鍵註銷所有 AI 大腦活躍專案 ======"))
        return registry.deregister_all_projects()

    target = _resolve_target(pattern)
    if not target:
        return False
    return registry.deregister_project(target)


def manage_list() -> bool:
    """Show the auto-archive status of all registered projects."""
    _print_archive_status()
    return True


def sync_mcp_paths(paths, fix: bool = False) -> bool:
    """Synchronise all MCP server command paths to the fastest available binary."""
    from .mcp import sync_all_mcp_commands
    from .constants import MEMPALACE_MCP_COMMAND

    expected = MEMPALACE_MCP_COMMAND()
    print(blue("====== 🔗 MCP 指令路徑同步 ======"))
    print(f"偵測到最快指令: {green(' '.join(expected))}")
    print()

    stale_count, msgs = sync_all_mcp_commands(paths, fix=False)

    if not msgs:
        print(green("未找到任何 MCP 伺服器配置。"))
        return True

    for msg in msgs:
        print(msg)

    print()
    if stale_count == 0:
        print(green("🎉 所有 MCP 指令路徑均為最新，無需更新。"))
        return True

    print(yellow(f"發現 {stale_count} 個過時路徑。"))
    if not fix:
        print(yellow("執行 `ai-brain mcp-sync --fix` 自動修正。"))
        return True

    fixed, fix_msgs = sync_all_mcp_commands(paths, fix=True)
    for msg in fix_msgs:
        print(msg)
    print()
    if fixed > 0:
        print(green(f"✅ 已同步 {fixed} 個 MCP 指令路徑。"))
        print(yellow("💡 請重啟 IDE / Claude Code 使設定生效。"))
    else:
        print(red("❌ 同步失敗，請檢查檔案權限。"))
    return fixed > 0


def run_doctor(paths, target: str | None = None, fix: bool = False) -> bool:
    # 1. Resolve which projects to check
    projects_to_check: list[Path] = []
    if target:
        resolved = _resolve_target(target)
        if not resolved:
            return False
        projects_to_check.append(Path(resolved))
    else:
        for p in registry.list_active():
            path_obj = Path(p)
            if path_obj.is_dir():
                projects_to_check.append(path_obj)
        if not projects_to_check:
            # If active registry is empty, default to checking current workspace directory
            projects_to_check.append(Path.cwd().resolve())

    # Build simple names helper
    multiple = len(projects_to_check) > 1

    print(blue(f"====== {APP_EMOJI} 開始執行 AI 大腦全面健康診斷 ======"))
    if target:
        print(yellow(f"🎯 目標模式：檢查單一專案 \"{projects_to_check[0].name}\" ({projects_to_check[0]})"))
    else:
        print(yellow(f"🎯 全域模式：檢查所有註冊活躍專案 (共 {len(projects_to_check)} 個專案)"))
    if fix:
        print(yellow("💡 診斷修復模式已啟用，將會自動修正可修復之問題。"))
    print()

    try:
        import fcntl
        has_fcntl = True
    except ImportError:
        has_fcntl = False

    def is_file_locked(filepath: Path) -> bool:
        if has_fcntl:
            try:
                with open(filepath) as f:
                    try:
                        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        fcntl.flock(f, fcntl.LOCK_UN)
                        return False
                    except (BlockingIOError, PermissionError):
                        return True
            except Exception:
                return False
        else:
            try:
                with open(filepath, "r+") as f:
                    return False
            except OSError:
                return True

    all_pass = True

    # 1. Check gitignores
    print(blue("1. 檢查 gitignore 排除設定..."))
    for proj in projects_to_check:
        prefix = f"  [{proj.name}] " if multiple else "  "
        ignores = _read_all_ignores(proj)

        if ".codebase-memory" not in ignores:
            print(red(f"{prefix}[ FAIL ] gitignore 中未忽略 .codebase-memory/ 目錄"))
            if fix:
                # We can write into global excludes or local gitignore.
                # To be consistent with existing command, write into global gitignore.
                _ensure_codebase_memory_ignored()
                print(green(f"{prefix}  [ FIXED ] 已自動將 .codebase-memory/ 加入全域 gitignore"))
            else:
                all_pass = False
        else:
            print(green(f"{prefix}[ PASS ] gitignore 已正確排除 .codebase-memory/"))

        # Check other massive unignored folders
        for folder in ("node_modules", "venv", ".venv", ".worktree", "graphify-out", "target", "build"):
            if (proj / folder).is_dir() and folder not in ignores:
                print(yellow(f"{prefix}[ WARN ] 偵測到本機存在 {folder}/ 但未在 gitignore 中排除。這可能會導致 MemPalace 索引耗時。"))
                if fix:
                    _append_to_global_gitignore(folder, "auto-excluded heavy/noisy directory")
                    print(green(f"{prefix}  [ FIXED ] 已自動將 {folder}/ 加入全域 gitignore"))

    print()

    # 2. Check mempalace.yaml
    print(blue("2. 檢查 mempalace.yaml 房間配置..."))
    for proj in projects_to_check:
        prefix = f"  [{proj.name}] " if multiple else "  "
        my_yaml = proj / "mempalace.yaml"
        if my_yaml.is_file():
            try:
                yaml_content = my_yaml.read_text(encoding="utf-8")
            except Exception:
                yaml_content = ""

            if "codebase_memory" in yaml_content or ".codebase-memory" in yaml_content:
                print(red(f"{prefix}[ FAIL ] mempalace.yaml 中仍包含已廢棄之 codebase_memory 房間"))
                if fix:
                    try:
                        lines = yaml_content.splitlines()
                        new_lines = []
                        skip_mode = False
                        for line in lines:
                            if line.strip().startswith("- name: codebase_memory") or line.strip().startswith("- name: .codebase-memory"):
                                skip_mode = True
                                continue
                            if skip_mode:
                                if line.startswith("- name:"):
                                    skip_mode = False
                                else:
                                    continue
                            new_lines.append(line)
                        my_yaml.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                        print(green(f"{prefix}  [ FIXED ] 已自動自 mempalace.yaml 中移除 codebase_memory 房間"))
                    except Exception as e:
                        print(red(f"{prefix}  [ ERROR ] 無法修復 mempalace.yaml ({e})"))
                        all_pass = False
                else:
                    all_pass = False
            else:
                print(green(f"{prefix}[ PASS ] mempalace.yaml 配置正常"))
        else:
            print(green(f"{prefix}[ PASS ] mempalace.yaml 未初始化 (略過)"))

    print()

    # 3. Check locks (Global Check - run once)
    print(blue("3. 檢查 MemPalace 鎖定鎖狀態..."))
    locks_dir = Path.home() / ".mempalace" / "locks"
    lock_ok = True
    if locks_dir.is_dir():
        for lock_file in locks_dir.glob("*.lock"):
            active = is_file_locked(lock_file)

            if not active:
                if fix:
                    try:
                        lock_file.unlink()
                    except Exception:
                        pass
                continue

            pid = None
            cmd = ""
            content = ""
            try:
                content = lock_file.read_text(encoding="utf-8").strip()
            except Exception:
                pass

            if content:
                parts = content.split(maxsplit=1)
                if parts and parts[0].isdigit():
                    pid = int(parts[0])
                    if len(parts) > 1:
                        cmd = parts[1]

            if pid:
                try:
                    os.kill(pid, 0)
                    print(yellow(f"  [ INFO ] 背景記憶寫入任務正在執行中 (PID: {pid}) {cmd}"))
                except OSError:
                    lock_ok = False
                    print(red(f"  [ FAIL ] 發現正在佔用但 PID {pid} 已不存在的死鎖 {lock_file.name}"))
                    if fix:
                        try:
                            lock_file.unlink()
                            print(green(f"    [ FIXED ] 已自動刪除死鎖 {lock_file.name}"))
                            lock_ok = True
                        except Exception as e:
                            print(red(f"    [ ERROR ] 無法刪除鎖定檔案 {lock_file.name} ({e})"))
                    else:
                        all_pass = False
            else:
                print(yellow(f"  [ INFO ] 佔用鎖定檔案 {lock_file.name} 中"))
        if lock_ok:
            print(green("  [ PASS ] MemPalace 鎖定狀態正常"))
    else:
        print(green("  [ PASS ] 無任何鎖定檔案"))

    print()

    # 3b. Check orphaned MCP processes (Global Check - run once)
    print(blue("3b. 檢查 MemPalace MCP 幽靈進程..."))
    orphaned_pids: list[int] = []
    try:
        ps_proc = subprocess.run(
            ["ps", "aux"],
            capture_output=True, text=True, timeout=10,
        )
        for line in ps_proc.stdout.splitlines():
            if "mempalace-mcp" not in line:
                continue
            # Skip the grep process itself
            if "grep" in line:
                continue
            parts = line.split()
            if len(parts) < 8:
                continue
            try:
                pid = int(parts[1])
            except (ValueError, IndexError):
                continue
            terminal = parts[7] if len(parts) > 7 else ""
            if terminal == "??":
                orphaned_pids.append(pid)
    except Exception as e:
        print(yellow(f"  [ WARN ] 無法掃描進程列表 ({e})"))

    if orphaned_pids:
        # Check which orphans are referenced in lock files
        lock_pids: set[int] = set()
        if locks_dir.is_dir():
            for lock_file in locks_dir.glob("*.lock"):
                try:
                    content = lock_file.read_text(encoding="utf-8").strip()
                    if content:
                        parts = content.split(maxsplit=1)
                        if parts and parts[0].isdigit():
                            lock_pids.add(int(parts[0]))
                except Exception:
                    pass

        orphaned_with_lock = [p for p in orphaned_pids if p in lock_pids]
        orphaned_no_lock = [p for p in orphaned_pids if p not in lock_pids]

        if orphaned_with_lock:
            print(red(f"  [ FAIL ] 發現 {len(orphaned_with_lock)} 個幽靈 MCP 進程持有鎖檔 (PID: {', '.join(str(p) for p in orphaned_with_lock)})"))
        if orphaned_no_lock:
            print(yellow(f"  [ WARN ] 發現 {len(orphaned_no_lock)} 個無終端機 MCP 進程 (PID: {', '.join(str(p) for p in orphaned_no_lock)})"))

        if fix:
            killed = 0
            for pid in orphaned_pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                    killed += 1
                except OSError:
                    pass
            if killed:
                print(green(f"  [ FIXED ] 已終止 {killed} 個幽靈 MCP 進程"))
            else:
                print(yellow("  [ WARN ] 無法終止任何幽靈進程（可能已結束）"))
        else:
            all_pass = False
    else:
        print(green("  [ PASS ] 無幽靈 MCP 進程"))

    print()

    # 3c. Check ChromaDB database size (Global Check - run once)
    print(blue("3c. 檢查 ChromaDB 資料庫大小..."))
    chroma_db_path = Path.home() / ".mempalace" / "palace" / "chroma.sqlite3"
    if chroma_db_path.is_file():
        try:
            db_size_bytes = chroma_db_path.stat().st_size
            db_size_gb = db_size_bytes / (1024 ** 3)
            if db_size_gb > 2:
                print(red(f"  [ FAIL ] ChromaDB 資料庫過大: {db_size_gb:.2f} GB (> 2 GB)"))
                if fix:
                    print(yellow("  --> 建議執行: ai-brain gc --apply 清理資料庫"))
                else:
                    all_pass = False
            elif db_size_gb > 1:
                print(yellow(f"  [ WARN ] ChromaDB 資料庫偏大: {db_size_gb:.2f} GB (> 1 GB)"))
            else:
                print(green(f"  [ PASS ] ChromaDB 資料庫大小正常: {db_size_gb:.2f} GB"))
        except Exception as e:
            print(yellow(f"  [ WARN ] 無法讀取 ChromaDB 大小 ({e})"))
    else:
        print(green("  [ PASS ] ChromaDB 尚未初始化（略過）"))

    print()

    # 4. Check stale drawers in mempalace (sync check)
    print(blue("4. 檢查 MemPalace 冗餘/過期記憶..."))
    doctor_lock_fd = _acquire_brain_lock()
    if doctor_lock_fd is None:
        print(yellow("  [ WARN ] 無法取得全域鎖檔，跳過步驟 4（另一個 ai-brain 正在執行）"))
    else:
        try:
            for proj in projects_to_check:
                prefix = f"  [{proj.name}] " if multiple else "  "
                try:
                    res = subprocess.run(
                        [TOOL_MEMPALACE, "sync", str(proj)],
                        capture_output=True, text=True, timeout=120,
                    )
                    gitignored = 0
                    missing = 0
                    for line in res.stdout.splitlines():
                        if "Gitignored:" in line:
                            parts = line.split(":")
                            if len(parts) > 1:
                                num = "".join(filter(str.isdigit, parts[1]))
                                if num:
                                    gitignored = int(num)
                        elif "Missing:" in line:
                            parts = line.split(":")
                            if len(parts) > 1:
                                num = "".join(filter(str.isdigit, parts[1]))
                                if num:
                                    missing = int(num)

                    if gitignored > 0 or missing > 0:
                        print(red(f"{prefix}[ FAIL ] 發現 {gitignored} 個已忽略與 {missing} 個已遺失檔案的抽屜殘留在記憶庫中"))
                        if fix:
                            print(yellow(f"{prefix}  --> 正在自動執行 mempalace sync --apply {proj} 清理記憶庫..."))
                            try:
                                subprocess.run(
                                    [TOOL_MEMPALACE, "sync", "--apply", str(proj)],
                                    check=True, timeout=120,
                                )
                                print(green(f"{prefix}  [ FIXED ] 記憶庫清理完成！"))
                            except subprocess.TimeoutExpired:
                                print(yellow(f"{prefix}[ TIMEOUT ] mempalace sync --apply 已逾時 (>120s)，繼續處理其他專案"))
                            except Exception as e:
                                print(red(f"{prefix}  [ ERROR ] 記憶庫清理失敗 ({e})"))
                                all_pass = False
                        else:
                            all_pass = False
                    else:
                        print(green(f"{prefix}[ PASS ] 記憶庫無冗餘抽屜，狀態正常"))
                except subprocess.TimeoutExpired:
                    print(yellow(f"{prefix}[ TIMEOUT ] mempalace sync 已逾時 (>120s)，繼續處理其他專案"))
                except FileNotFoundError:
                    print(yellow(f"{prefix}[ WARN ] 未檢測到 mempalace CLI，跳過此項檢查"))
                except Exception as e:
                    print(red(f"{prefix}[ ERROR ] 檢查記憶庫時發生錯誤 ({e})"))
        finally:
            _release_brain_lock(doctor_lock_fd)

    print()

    # 5. Check System CLIs (Global Check - run once)
    print(blue("5. 檢查系統相依 CLI 工具可用性..."))
    cli_ok = True
    for tool_name, pkg in (("mempalace", "mempalace"), ("codebase-memory-mcp", "codebase-memory-mcp"), ("claude-mem", "claude-mem")):
        if shutil.which(tool_name):
            print(green(f"  [ PASS ] 工具 {tool_name} 已安裝"))
        else:
            cli_ok = False
            print(red(f"  [ FAIL ] 未找到 {tool_name} 指令"))
            if fix:
                print(yellow(f"    --> 正在嘗試自動安裝 {pkg}..."))
                try:
                    subprocess.run(
                        ["uv", "tool", "install", pkg, "--force"],
                        check=True, timeout=300,
                    )
                    print(green(f"    [ FIXED ] 已自動安裝 {pkg}！"))
                    cli_ok = True
                except subprocess.TimeoutExpired:
                    print(yellow(f"    [ TIMEOUT ] uv tool install {pkg} 已逾時 (>300s)"))
                except Exception as e:
                    print(red(f"    [ ERROR ] 自動安裝 {pkg} 失敗 ({e})，請手動執行: uv tool install {pkg} --force"))
            else:
                all_pass = False

    print()

    # 6. Check MCP verifies (Global Check - run once)
    print(blue("6. 檢查 IDE MCP 大腦配置與伺服器載入..."))
    from .verifier import FAIL as VERIFY_FAIL
    from .verifier import run_all_checks
    mcp_results = run_all_checks(paths)
    mcp_failures = 0
    for r in mcp_results:
        if r.status == VERIFY_FAIL:
            mcp_failures += 1
            print(red(f"  [ FAIL ] {r.name}: {r.detail}"))
        else:
            print(green(f"  [ PASS ] {r.name}{' ' + r.detail if r.detail else ''}"))

    if mcp_failures > 0:
        print(red(f"  [ FAIL ] 共有 {mcp_failures} 項 MCP 配置錯誤"))
        if fix:
            try:
                import shutil as _shutil
                _oc_cmd = "openclaw"
                _oc_found = _shutil.which(_oc_cmd)
                if not _oc_found:
                    _nvm_dir = Path.home() / ".nvm" / "versions" / "node"
                    if _nvm_dir.is_dir():
                        for _root, _dirs, _files in os.walk(_nvm_dir):
                            if "openclaw" in _files:
                                _oc_found = _root
                                break
                if _oc_found:
                    _oc_dir = paths.openclaw_config.parent
                    if not _oc_dir.is_dir():
                        _oc_dir.mkdir(parents=True, exist_ok=True)
                        print(yellow(f"    --> 偵測到 OpenClaw 已安裝，建立配置目錄: {_oc_dir}"))
            except Exception:
                pass
            print(yellow("    --> 正在重新註冊所有 MCP 服務..."))
            try:
                from .mcp import register_all
                register_all(paths)
                print(green("    [ FIXED ] 已成功重新配置所有 IDE 的 MCP 大腦！"))
            except Exception as e:
                print(red(f"    [ ERROR ] 重新註冊 MCP 服務失敗 ({e})"))
                all_pass = False
        else:
            all_pass = False
    else:
        print(green("  [ PASS ] MCP 大腦配置與伺服器載入完全正常"))

    print()

    # 6b. MCP connectivity probe (Global Check - run once)
    print(blue("6b. 檢查 MemPalace MCP 連線可用性..."))
    mcp_probe_ok = False
    mcp_probe_time = 0.0
    mcp_proc = None
    try:
        mcp_proc = subprocess.Popen(
            [TOOL_MEMPALACE, "mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        init_msg = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ai-brain-doctor", "version": "1.0.0"},
            },
        }) + "\n"
        probe_start = time.monotonic()
        assert mcp_proc.stdin is not None
        mcp_proc.stdin.write(init_msg)
        mcp_proc.stdin.flush()

        # Read response with timeout
        import select as _select
        ready = _select.select([mcp_proc.stdout], [], [], 30)
        if ready[0]:
            response_line = mcp_proc.stdout.readline()
            probe_elapsed = time.monotonic() - probe_start
            if response_line.strip():
                resp = json.loads(response_line)
                if "result" in resp or "id" in resp:
                    mcp_probe_ok = True
                    mcp_probe_time = probe_elapsed
        else:
            probe_elapsed = time.monotonic() - probe_start
    except json.JSONDecodeError:
        pass
    except Exception as e:
        print(yellow(f"  [ WARN ] MCP 連線探測異常 ({e})"))
    finally:
        if mcp_proc is not None:
            try:
                mcp_proc.terminate()
                mcp_proc.wait(timeout=5)
            except Exception:
                try:
                    mcp_proc.kill()
                except Exception:
                    pass

    if mcp_probe_ok:
        print(green(f"  [ PASS ] MemPalace MCP 連線正常 (回應時間: {mcp_probe_time:.2f}s)"))
    else:
        print(yellow("  [ WARN ] MemPalace MCP 連線探測未通過（無回應或逾時 30s），可稍後手動驗證"))

    print()

    # 6c. Check MCP command path consistency
    print(blue("6c. 檢查 MCP 指令路徑一致性..."))
    from .mcp import sync_all_mcp_commands
    stale_count, stale_msgs = sync_all_mcp_commands(paths, fix=False)
    if stale_msgs:
        for msg in stale_msgs:
            print(msg)
    if stale_count > 0:
        print(red(f"  [ FAIL ] 發現 {stale_count} 個過時 MCP 指令路徑"))
        if fix:
            from .mcp import sync_all_mcp_commands as _sync
            fixed, fix_msgs = _sync(paths, fix=True)
            for msg in fix_msgs:
                print(msg)
            if fixed > 0:
                print(green(f"  [ FIXED ] 已同步 {fixed} 個 MCP 指令路徑"))
            print(yellow("  💡 請重啟 IDE / Claude Code 使設定生效"))
        else:
            all_pass = False
            print(yellow("  💡 執行 `ai-brain doctor --fix` 或 `ai-brain mcp-sync --fix` 自動修正"))
    else:
        print(green("  [ PASS ] 所有 MCP 指令路徑均為最新"))

    print()

    # 7. Check Git Hooks
    print(blue("7. 檢查 Git Hooks 配置與速度優化版本..."))
    for proj in projects_to_check:
        prefix = f"  [{proj.name}] " if multiple else "  "
        if not (proj / ".git").is_dir():
            print(green(f"{prefix}[ PASS ] 非 Git 專案，跳過 Git Hooks 檢查"))
        else:
            hooks_dir = proj / ".git" / "hooks"
            hooks_ok = True
            for name in ("post-merge", "post-checkout"):
                hook_file = hooks_dir / name
                installed = False
                up_to_date = False

                if hook_file.is_file():
                    try:
                        content = hook_file.read_text(encoding="utf-8")
                        begin_marker = HOOK_BEGIN_MARKER.format(name=name)
                        if begin_marker in content:
                            installed = True
                            if "--fast" in content:
                                up_to_date = True
                    except Exception:
                        pass

                if not installed:
                    hooks_ok = False
                    print(red(f"{prefix}[ FAIL ] Git Hook '{name}' 未安裝"))
                elif not up_to_date:
                    hooks_ok = False
                    print(yellow(f"{prefix}[ FAIL ] Git Hook '{name}' 已安裝但版本過舊 (未啟用速度優化 --fast)"))
                else:
                    print(green(f"{prefix}[ PASS ] Git Hook '{name}' 已安裝且啟用速度優化"))

            if not hooks_ok:
                if fix:
                    print(yellow(f"{prefix}  --> 正在重新安裝/更新 Git Hooks..."))
                    if git_hooks.install(proj):
                        print(green(f"{prefix}  [ FIXED ] 已成功更新 Git Hooks 至最新速度優化版本！"))
                    else:
                        print(red(f"{prefix}  [ ERROR ] 自動更新 Git Hooks 失敗，請手動執行 `ai-brain init` 重試。"))
                        all_pass = False
                else:
                    all_pass = False

    print()

    # 8. Check CLAUDE.md AI 工具使用規則版本
    print(blue("8. 檢查 CLAUDE.md AI 工具使用規則版本..."))
    rules_ok = True
    freshness_marker = "ALWAYS prefer `codebase-memory-mcp` graph tools"

    def _strip_graphify_lines(content: str) -> tuple[str, bool]:
        """Remove lines referencing graphify. Returns (new_content, was_changed)."""
        new_lines = []
        skip_block = False
        changed = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("## ") and "graphify" in stripped.lower():
                skip_block = True
                changed = True
                continue
            if skip_block and (stripped.startswith("## ") or stripped.startswith("# ")):
                skip_block = False
            if skip_block:
                changed = True
                continue
            if "graphify" in line.lower() and not any(t in line for t in ("codebase-memory-mcp", "graph tools")):
                changed = True
                continue
            new_lines.append(line)
        while new_lines and not new_lines[-1].strip():
            new_lines.pop()
        return "\n".join(new_lines) + "\n" if new_lines else "", changed

    def _fix_claude_md(label: str, md_path: Path) -> bool:
        """Return True if file is OK (or was fixed), False if needs user action."""
        try:
            content = md_path.read_text(encoding="utf-8")
        except Exception:
            return True

        has_marker = COGNITIVE_PRINCIPLES_MARKER in content
        has_freshness = freshness_marker in content

        if has_marker and has_freshness:
            stripped, had_graphify = _strip_graphify_lines(content)
            if had_graphify:
                print(yellow(f"  [ WARN ] {label} 含有過時的 graphify 殘留內容（認知規則已是最新）"))
                if fix:
                    md_path.write_text(stripped, encoding="utf-8")
                    print(green(f"    [ FIXED ] 已清除 {label} 中的 graphify 殘留"))
                    content = stripped
                else:
                    return False
            print(green(f"  [ PASS ] {label} 工具規則為最新版本"))
            return True

        if has_marker:
            # Stale block — update it
            print(yellow(f"  [ WARN ] {label} 工具規則版本過舊"))
            if fix:
                try:
                    lines = content.splitlines()
                    new_lines: list[str] = []
                    skip = False
                    for line in lines:
                        if line.strip() == COGNITIVE_PRINCIPLES_MARKER.strip():
                            skip = True
                            new_lines.extend(COGNITIVE_PRINCIPLES_BLOCK.splitlines())
                            continue
                        if skip:
                            if line.startswith("## ") and line.strip() != COGNITIVE_PRINCIPLES_MARKER.strip():
                                skip = False
                                new_lines.append(line)
                        else:
                            new_lines.append(line)
                    updated = "\n".join(new_lines) + "\n"
                    updated, _ = _strip_graphify_lines(updated)
                    md_path.write_text(updated, encoding="utf-8")
                    print(green(f"    [ FIXED ] 已更新 {label} 至最新工具規則版本"))
                    return True
                except Exception as e:
                    print(red(f"    [ ERROR ] 更新 {label} 失敗 ({e})"))
                    return False
            return False  # WARN counts as failure in check-only mode

        # No cognitive block — check for stale graphify content
        if "graphify" in content.lower():
            print(yellow(f"  [ WARN ] {label} 含有過時的 graphify 引導內容"))
            if fix:
                try:
                    new_lines = [l for l in content.splitlines() if "graphify" not in l.lower()]
                    while new_lines and not new_lines[-1].strip():
                        new_lines.pop()
                    new_lines.append("")
                    new_lines.extend(COGNITIVE_PRINCIPLES_BLOCK.splitlines())
                    md_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                    print(green(f"    [ FIXED ] 已清除 {label} 中的舊 graphify 引導並補上最新工具規則"))
                    return True
                except Exception as e:
                    print(red(f"    [ ERROR ] 更新 {label} 失敗 ({e})"))
                    return False
            return False

        print(green(f"  [ INFO ] {label} 中無 ai-brain 管理的認知規則區塊，略過"))
        return True


    global_md = Path.home() / ".claude" / "CLAUDE.md"
    if not target and global_md.is_file():
        if not _fix_claude_md("全域 ~/.claude/CLAUDE.md", global_md):
            rules_ok = False
            if not fix:
                all_pass = False

    for proj in projects_to_check:
        short = proj.name
        for rel in (".claude/CLAUDE.md",):
            md_path = proj / rel
            if md_path.is_file():
                if not _fix_claude_md(f"[{short}] {rel}", md_path):
                    rules_ok = False
                    if not fix:
                        all_pass = False

    print()

    # 9. Scan for legacy graphify artifacts
    print(blue("9. 掃描舊有 graphify 遺留物..."))
    _GRAPHIFY_PATHS_TO_CHECK = (
        ".cursor/rules/graphify.mdc",
        ".claude/skills/graphify",
        ".kilo/command/graphify.md",
    )
    _PROJECT_GRAPHIFY_PLUGINS = (
        ".opencode/plugins/graphify.js",
        ".opencode/plugins/ai-brain-graphify.js",
        ".opencode/plugins/ai-brain-graphify.ts",
        ".kilo/plugins/graphify.js",
        ".kilo/plugins/ai-brain-graphify.js",
    )
    _PROJECT_OPENCODE_CONFIGS = (
        "opencode.json",
        ".opencode/opencode.json",
    )
    graphify_problems = 0

    def _has_graphify_plugins(data: dict) -> bool:
        plugins = data.get("plugin")
        if not isinstance(plugins, list):
            return False
        return any(isinstance(p, str) and "graphify" in p.lower() for p in plugins)

    def _strip_graphify_plugins(data: dict) -> None:
        plugins = data.get("plugin")
        if not isinstance(plugins, list):
            return
        cleaned = [p for p in plugins if not (isinstance(p, str) and "graphify" in p.lower())]
        if cleaned:
            data["plugin"] = cleaned
        else:
            data.pop("plugin", None)

    for proj in projects_to_check:
        prefix = f"  [{proj.name}] " if multiple else "  "
        proj_had_problem = False

        graphify_dir = proj / "graphify-out"
        if graphify_dir.is_dir():
            is_empty = not any(graphify_dir.iterdir())
            proj_had_problem = True
            if is_empty:
                print(red(f"{prefix}[ FAIL ] 偵測到空的 graphify-out/ 目錄殘留"))
            else:
                print(red(f"{prefix}[ FAIL ] 偵測到含資料的 graphify-out/ 目錄殘留"))
            if fix:
                try:
                    shutil.rmtree(graphify_dir, ignore_errors=True)
                    print(green(f"{prefix}  [ FIXED ] 已移除 graphify-out/ 目錄"))
                except Exception as e:
                    print(red(f"{prefix}  [ ERROR ] 移除 graphify-out/ 失敗 ({e})"))
                    all_pass = False

        for dotfile in sorted(proj.glob(".graphify_*")):
            if not dotfile.is_file():
                continue
            proj_had_problem = True
            print(red(f"{prefix}[ FAIL ] 偵測到舊有 graphify 暫存檔: {dotfile.name}"))
            if fix:
                try:
                    dotfile.unlink()
                    print(green(f"{prefix}  [ FIXED ] 已移除 {dotfile.name}"))
                except Exception as e:
                    print(red(f"{prefix}  [ ERROR ] 移除 {dotfile.name} 失敗 ({e})"))
                    all_pass = False

        for rel_path in _GRAPHIFY_PATHS_TO_CHECK:
            artifact = proj / rel_path
            if artifact.is_file() or artifact.is_dir():
                is_graphify_artifact = artifact.is_dir()
                if not is_graphify_artifact:
                    try:
                        txt = artifact.read_text(encoding="utf-8", errors="ignore")
                        is_graphify_artifact = (
                            "graphify" in txt.lower()
                            and "codebase-memory-mcp" not in txt.lower()
                        )
                    except Exception:
                        pass

                if is_graphify_artifact:
                    proj_had_problem = True
                    print(red(f"{prefix}[ FAIL ] 偵測到舊有 graphify 配置: {rel_path}"))
                    if fix:
                        try:
                            if artifact.is_dir():
                                shutil.rmtree(artifact)
                            else:
                                artifact.unlink()
                            print(green(f"{prefix}  [ FIXED ] 已移除 {rel_path}"))
                        except Exception as e:
                            print(red(f"{prefix}  [ ERROR ] 移除 {rel_path} 失敗 ({e})"))
                            all_pass = False

        for rel_path in _PROJECT_GRAPHIFY_PLUGINS:
            plugin_file = proj / rel_path
            if plugin_file.is_file():
                proj_had_problem = True
                print(red(f"{prefix}[ FAIL ] 偵測到舊有 graphify plugin 檔案: {rel_path}"))
                if fix:
                    try:
                        plugin_file.unlink()
                        print(green(f"{prefix}  [ FIXED ] 已移除 {rel_path}"))
                    except Exception as e:
                        print(red(f"{prefix}  [ ERROR ] 移除 {rel_path} 失敗 ({e})"))
                        all_pass = False

        for rel in _PROJECT_OPENCODE_CONFIGS:
            cfg = proj / rel
            if not cfg.is_file():
                continue
            try:
                data = json.loads(cfg.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not _has_graphify_plugins(data):
                continue
            proj_had_problem = True
            print(red(f"{prefix}[ FAIL ] {rel} plugin 陣列引用過時 graphify"))
            if fix:
                try:
                    _strip_graphify_plugins(data)
                    cfg.write_text(
                        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8",
                    )
                    print(green(f"{prefix}  [ FIXED ] 已清除 {rel} 中的 graphify 引用"))
                except Exception as e:
                    print(red(f"{prefix}  [ ERROR ] 修正 {rel} 失敗 ({e})"))
                    all_pass = False

        if proj_had_problem:
            graphify_problems += 1

    global_graphify_problems = 0
    global_oc_cfg = Path.home() / ".config" / "opencode" / "opencode.json"
    global_plugin_paths = sorted(
        p for p in (Path.home() / ".config" / "opencode" / "plugins").glob("*.js")
        if "graphify" in p.name.lower()
    ) if (Path.home() / ".config" / "opencode" / "plugins").is_dir() else []

    if global_oc_cfg.is_file():
        try:
            gdata = json.loads(global_oc_cfg.read_text(encoding="utf-8"))
        except Exception:
            gdata = None
        if gdata and _has_graphify_plugins(gdata):
            global_graphify_problems += 1
            print(red(f"  [ FAIL ] 全域 opencode.json plugin 陣列引用過時 graphify"))
            if fix:
                try:
                    _strip_graphify_plugins(gdata)
                    global_oc_cfg.write_text(
                        json.dumps(gdata, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8",
                    )
                    print(green("    [ FIXED ] 已清除全域 opencode.json 中的 graphify 引用"))
                except Exception as e:
                    print(red(f"    [ ERROR ] 修正全域 opencode.json 失敗 ({e})"))
                    all_pass = False

    for pf in global_plugin_paths:
        graphify_problems += 1
        print(red(f"  [ FAIL ] 偵測到全域舊有 graphify plugin 檔: {pf.name}"))
        if fix:
            try:
                pf.unlink()
                print(green(f"    [ FIXED ] 已移除 {pf.name}"))
            except Exception as e:
                print(red(f"    [ ERROR ] 移除 {pf.name} 失敗 ({e})"))
                all_pass = False

    if graphify_problems == 0 and global_graphify_problems == 0:
        print(green("  [ PASS ] 無 graphify 遺留物"))

    print()
    print(blue("====== 🏁 診斷結束 ======"))

    if all_pass:
        print(green("🎉 完美！您的 AI 大腦環境一切正常，健康指數 100%！"))
        return True
    else:
        if fix:
            print(yellow("⚠️ 診斷中發現了問題，已嘗試修復。請再次執行 `ai-brain doctor` 驗證。"))
        else:
            print(red("⚠️ 發現部分配置問題！請執行 `ai-brain doctor --fix` 自動修正。"))
        return False


# --- Internal helpers -----------------------------------------------------------

def _global_gitignore_path() -> Path:
    real_home = Path(os.path.expanduser("~")).resolve()
    stubbed = Path.home().resolve() != real_home

    if not stubbed:
        try:
            res = subprocess.run(
                ["git", "config", "--global", "core.excludesfile"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if res.returncode == 0:
                path_str = res.stdout.strip()
                if path_str:
                    if path_str.startswith("~"):
                        return Path.home() / path_str[1:].lstrip("/")
                    p = Path(path_str).expanduser()
                    try:
                        if p.is_relative_to(real_home):
                            return Path.home() / p.relative_to(real_home)
                    except AttributeError:
                        try:
                            p.relative_to(real_home)
                            return Path.home() / p.relative_to(real_home)
                        except ValueError:
                            pass
                    return p
        except Exception:
            pass
    return Path.home() / ".gitignore_global"


def _read_all_ignores(base_dir: Path = Path(".")) -> set[str]:
    """Read normalized patterns from both local .gitignore and global excludesfile."""
    patterns = set()
    paths_to_read = [base_dir / ".gitignore", _global_gitignore_path()]
    for p in paths_to_read:
        if p.is_file():
            try:
                content = p.read_text(encoding="utf-8")
                for line in content.splitlines():
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        patterns.add(stripped.rstrip("/"))
            except Exception:
                pass
    return patterns


def _append_to_global_gitignore(pattern: str, comment: str) -> None:
    """Append a single gitignore pattern to the global gitignore file."""
    gitignore = _global_gitignore_path()
    content = ""
    if gitignore.is_file():
        try:
            content = gitignore.read_text(encoding="utf-8")
        except Exception:
            pass
    lines = content.splitlines()
    normalized = [line.strip().rstrip("/") for line in lines if line.strip() and not line.strip().startswith("#")]
    if pattern.rstrip("/") not in normalized:
        new_lines = list(lines)
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append(f"# {comment}")
        new_lines.append(f"{pattern.rstrip('/')}/")
        try:
            gitignore.parent.mkdir(parents=True, exist_ok=True)
            gitignore.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            if gitignore == Path.home() / ".gitignore_global":
                try:
                    subprocess.run(
                        ["git", "config", "--global", "core.excludesfile", "~/.gitignore_global"],
                        timeout=10,
                    )
                except subprocess.TimeoutExpired:
                    print(yellow("[ WARN ] git config 設定已逾時 (>10s)"))
        except Exception as e:
            print(red(f"警告：更新全域 gitignore 失敗 ({e})"))


def _ensure_codebase_memory_ignored() -> None:
    _append_to_global_gitignore(".codebase-memory", "codebase-memory-mcp cache and graphs")
    _append_to_global_gitignore(".worktree", "git worktree checkouts")


def _run_mempalace_init() -> bool:
    try:
        subprocess.run(
            [TOOL_MEMPALACE, "init", "--yes", "--auto-mine", "--no-llm", "."],
            check=True, timeout=180,
        )
        try:
            subprocess.run(
                [TOOL_MEMPALACE, "sync", "--apply", "."],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            print(yellow("[ TIMEOUT ] mempalace sync --apply 已逾時 (>120s)，繼續處理"))
        except Exception:
            pass
        return True
    except FileNotFoundError:
        print(red("錯誤：未找到 mempalace 工具，請先執行: uv tool install mempalace --force"))
        return False
    except subprocess.TimeoutExpired:
        print(yellow("[ TIMEOUT ] mempalace init 已逾時 (>180s)"))
        return False
    except subprocess.CalledProcessError as e:
        print(red(f"錯誤：mempalace 初始化失敗 ({e})"))
        return False


def _run_codebase_memory_init() -> bool:
    print_yellow("--> 初始化 Codebase-Memory 圖譜索引...")
    try:
        subprocess.run(
            [TOOL_CODEBASE_MEMORY, "cli", "index_repository",
             json.dumps({"repo_path": str(Path.cwd().resolve())})],
            check=True, timeout=600,
        )
        return True
    except FileNotFoundError:
        print(red("錯誤：未找到 codebase-memory-mcp 工具，請先執行: uv tool install codebase-memory-mcp --force"))
        return False
    except subprocess.TimeoutExpired:
        print(yellow("[ TIMEOUT ] codebase-memory-mcp index_repository 已逾時 (>600s)"))
        return True  # partial install/init is still considered success
    except Exception as e:
        print(yellow(f"--> 初始化 Codebase-Memory 圖譜時發生警告 ({e})"))
        return True  # partial install/init is still considered success


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
    print_yellow("--> 建立 .claude/CLAUDE.md 大腦引導指南...")
    try:
        target = Path(PROJECT_CLAUDE_MD)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(LOCAL_CLAUDE_MD_TEMPLATE, encoding="utf-8")
        return True
    except Exception as e:
        print(red(f"錯誤：建立 .claude/CLAUDE.md 失敗 ({e})"))
        return False


# graphify tool configs are no longer used.


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
    if any(title in content for title in (
        "AI Agent 認知工作流與大腦記憶指引",
        "AI Agent 大腦與記憶指引",
        "AI Agent Cognitive Workflow and Memory Guide",
        "AI Agent Cognitive Workflow and Memory Guidelines"
    )):
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


def _should_run_gc() -> bool:
    """Return True if more than GC_BACKGROUND_GAP_SECONDS since last GC."""
    if not LAST_GC_FILE().is_file():
        return True
    try:
        last = int(LAST_GC_FILE().read_text().strip())
    except Exception:
        return True
    return (int(time.time()) - last) > GC_BACKGROUND_GAP_SECONDS


def _record_gc_timestamp() -> None:
    try:
        LAST_GC_FILE().parent.mkdir(parents=True, exist_ok=True)
        LAST_GC_FILE().write_text(str(int(time.time())) + "\n")
    except Exception:
        pass


def _run_codebase_memory_index() -> bool:
    try:
        subprocess.run([
            "codebase-memory-mcp",
            "cli",
            "index_repository",
            json.dumps({"repo_path": str(Path.cwd().resolve())})
        ], check=True, timeout=600)
        return True
    except FileNotFoundError:
        print(red("錯誤：未找到 codebase-memory-mcp，請先執行: ai-brain install"))
        return False
    except subprocess.TimeoutExpired:
        print(yellow("[ TIMEOUT ] codebase-memory-mcp index_repository 已逾時 (>600s)"))
        return False
    except Exception as e:
        print(red(f"錯誤：更新代碼地圖圖譜失敗 ({e})"))
        return False


def _run_archive_sweep(*, silent: bool) -> None:
    """Sweep every project in the auto-archive whitelist."""
    # Cron skip-if-running guard
    if _another_stop_running():
        if not silent:
            print(yellow("另一個 ai-brain stop 正在執行，跳過本次執行"))
        return

    pid_path = _stop_pid_path()
    try:
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass

    lock_fd = None
    try:
        lock_fd = _acquire_brain_lock()
        if lock_fd is None:
            if not silent:
                print(yellow("[ WARN ] 無法取得全域鎖檔，跳過 _run_archive_sweep"))
            return

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
                subprocess.run(
                    [TOOL_MEMPALACE, "sweep", str(target)],
                    stdout=stdout, stderr=stderr, timeout=300,
                )
            except subprocess.TimeoutExpired:
                if not silent:
                    print(yellow(f"[ TIMEOUT ] mempalace sweep {target.name} 已逾時 (>300s)，繼續處理其他專案"))
            except Exception as e:
                if not silent:
                    print(red(f"警告：歸檔 {target.name} 失敗 ({e})"))
    finally:
        if lock_fd is not None:
            _release_brain_lock(lock_fd)
        try:
            pid_path.unlink(missing_ok=True)
        except OSError:
            pass


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
    from .ui import GREEN, NC, RED
    enabled = set(registry.list_archived())
    for i, proj_path in enumerate(registry.list_active(), 1):
        base = Path(proj_path).name
        if proj_path in enabled:
            print(f"  [{i}] {GREEN}[ 已啟用自動歸檔 (Active) ]{NC} {base} ({proj_path})")
        else:
            print(f"  [{i}] {RED}[ 預設不歸檔 (Inactive) ]{NC} {base} ({proj_path})")
    print()
    print(yellow("用法提示:"))
    from .ui import GREEN as G
    from .ui import NC as RST
    print(f"  啟用當前專案自動歸檔: {G}ai-brain include{RST}")
    print(f"  停用當前專案自動歸檔: {G}ai-brain exclude current{RST} 或 {G}ai-brain exclude .{RST}")
    print(f"  啟用指定專案自動歸檔: {G}ai-brain include [專案關鍵字|編號|all]{RST}")
    print(f"  停用指定專案自動歸檔: {G}ai-brain exclude [專案關鍵字|編號|all]{RST}")
    print(f"  全部啟用:             {G}ai-brain include all{RST}  (同 {G}include-all{RST})")
    print(f"  全部停用:             {G}ai-brain exclude all{RST}  (同 {G}exclude-all{RST})")
    print(f"  查看所有註冊專案:     {G}ai-brain list{RST}")
    print(f"  註銷指定專案:         {G}ai-brain remove [專案關鍵字|編號|current|all]{RST}\n")


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
