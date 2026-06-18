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

    _ensure_graphify_out_ignored()

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

    for name in ("ai-brain", "graphify-mcp-wrapper"):
        target = Path.home() / ".local" / "bin" / name
        if target.exists():
            try:
                target.unlink()
            except Exception as e:
                print(red(f"❌ 移除 {name} 失敗 ({e})"))

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
    _ensure_graphify_out_ignored()
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
    _ensure_graphify_out_ignored()
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


def run_doctor(paths, fix: bool = False) -> bool:
    import os
    print(blue(f"====== {APP_EMOJI} 開始執行 AI 大腦全面健康診斷 ======"))
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
                with open(filepath, "r") as f:
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
            except IOError:
                return True

    all_pass = True

    # 1. Check .gitignore
    print(blue("1. 檢查 .gitignore 排除設定..."))
    gitignore = Path(".gitignore")
    gi_ok = True
    content = ""
    if gitignore.is_file():
        try:
            content = gitignore.read_text(encoding="utf-8")
        except Exception:
            pass
    lines = content.splitlines()
    normalized = [line.strip().rstrip("/") for line in lines if line.strip() and not line.strip().startswith("#")]
    
    if "graphify-out" not in normalized:
        gi_ok = False
        print(red("  [ FAIL ] .gitignore 中未忽略 graphify-out/ 目錄"))
        if fix:
            _ensure_graphify_out_ignored()
            print(green("    [ FIXED ] 已自動將 graphify-out/ 加入 .gitignore"))
            gi_ok = True
        else:
            all_pass = False
    else:
        print(green("  [ PASS ] .gitignore 已正確排除 graphify-out/"))

    # Check other massive unignored folders
    for folder in ("node_modules", "venv", ".venv"):
        if Path(folder).is_dir() and folder not in normalized:
            print(yellow(f"  [ WARN ] 偵測到本機存在 {folder}/ 但未在 .gitignore 中排除。這可能會導致 MemPalace 索引耗時。"))
            if fix:
                print_yellow(f"    --> 建議您手動將 {folder}/ 加入 .gitignore 排除名單。")

    print()

    # 2. Check mempalace.yaml
    print(blue("2. 檢查 mempalace.yaml 房間配置..."))
    my_yaml = Path("mempalace.yaml")
    yaml_ok = True
    if my_yaml.is_file():
        try:
            yaml_content = my_yaml.read_text(encoding="utf-8")
        except Exception:
            yaml_content = ""
        
        if "graphify_out" in yaml_content or "graphify-out" in yaml_content:
            yaml_ok = False
            print(red("  [ FAIL ] mempalace.yaml 中仍包含已廢棄之 graphify_out 房間"))
            if fix:
                try:
                    lines = yaml_content.splitlines()
                    new_lines = []
                    skip_mode = False
                    for line in lines:
                        if line.strip().startswith("- name: graphify_out") or line.strip().startswith("- name: graphify-out"):
                            skip_mode = True
                            continue
                        if skip_mode:
                            if line.startswith("- name:"):
                                skip_mode = False
                            else:
                                continue
                        new_lines.append(line)
                    my_yaml.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                    print(green("    [ FIXED ] 已自動自 mempalace.yaml 中移除 graphify_out 房間"))
                    yaml_ok = True
                except Exception as e:
                    print(red(f"    [ ERROR ] 無法修復 mempalace.yaml ({e})"))
            else:
                all_pass = False
        else:
            print(green("  [ PASS ] mempalace.yaml 配置正常"))
    else:
        print(green("  [ PASS ] mempalace.yaml 未初始化 (略過)"))

    print()

    # 3. Check locks
    print(blue("3. 檢查 MemPalace 鎖定鎖狀態..."))
    locks_dir = Path.home() / ".mempalace" / "locks"
    lock_ok = True
    if locks_dir.is_dir():
        for lock_file in locks_dir.glob("*.lock"):
            active = is_file_locked(lock_file)
            
            if not active:
                # Dormant lock file — harmless. Clean it up quietly in fix mode.
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

    # 4. Check stale drawers in mempalace (sync check)
    print(blue("4. 檢查 MemPalace 冗餘/過期記憶..."))
    sync_ok = True
    try:
        res = subprocess.run([TOOL_MEMPALACE, "sync"], capture_output=True, text=True)
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
            sync_ok = False
            print(red(f"  [ FAIL ] 發現 {gitignored} 個已忽略與 {missing} 個已遺失檔案的抽屜殘留在記憶庫中"))
            if fix:
                print(yellow("    --> 正在自動執行 mempalace sync --apply . 清理記憶庫..."))
                try:
                    subprocess.run([TOOL_MEMPALACE, "sync", "--apply", "."], check=True)
                    print(green("    [ FIXED ] 記憶庫清理完成！"))
                    sync_ok = True
                except Exception as e:
                    print(red(f"    [ ERROR ] 記憶庫清理失敗 ({e})"))
            else:
                all_pass = False
        else:
            print(green("  [ PASS ] 記憶庫無冗餘抽屜，狀態正常"))
    except FileNotFoundError:
        print(yellow("  [ WARN ] 未檢測到 mempalace CLI，跳過此項檢查"))
    except Exception as e:
        print(red(f"  [ ERROR ] 檢查記憶庫時發生錯誤 ({e})"))

    print()

    # 5. Check System CLIs
    print(blue("5. 檢查系統相依 CLI 工具可用性..."))
    cli_ok = True
    for tool_name, pkg in (("mempalace", "mempalace"), ("graphify", "graphifyy"), ("claude-mem", "claude-mem")):
        if shutil.which(tool_name):
            print(green(f"  [ PASS ] 工具 {tool_name} 已安裝"))
        else:
            cli_ok = False
            print(red(f"  [ FAIL ] 未找到 {tool_name} 指令"))
            if fix:
                print(yellow(f"    --> 正在嘗試自動安裝 {pkg}..."))
                try:
                    subprocess.run(["uv", "tool", "install", pkg, "--force"], check=True)
                    print(green(f"    [ FIXED ] 已自動安裝 {pkg}！"))
                    cli_ok = True
                except Exception as e:
                    print(red(f"    [ ERROR ] 自動安裝 {pkg} 失敗 ({e})，請手動執行: uv tool install {pkg} --force"))
            else:
                all_pass = False
    
    print()

    # 6. Check MCP verifies
    print(blue("6. 檢查 IDE MCP 大腦配置與伺服器載入..."))
    from .verifier import run_all_checks, PASS as VERIFY_PASS, FAIL as VERIFY_FAIL
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
            print(yellow("    --> 正在重新註冊所有 MCP 服務..."))
            try:
                from .mcp import register_all
                register_all(paths)
                print(green("    [ FIXED ] 已成功重新配置所有 IDE 的 MCP 大腦！"))
            except Exception as e:
                print(red(f"    [ ERROR ] 重新註冊 MCP 服務失敗 ({e})"))
        else:
            all_pass = False
    else:
        print(green("  [ PASS ] MCP 大腦配置與伺服器載入完全正常"))

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

def _ensure_graphify_out_ignored() -> None:
    gitignore = Path(".gitignore")
    content = ""
    if gitignore.is_file():
        try:
            content = gitignore.read_text(encoding="utf-8")
        except Exception:
            pass

    lines = content.splitlines()
    normalized = [line.strip().rstrip("/") for line in lines if line.strip() and not line.strip().startswith("#")]
    
    if "graphify-out" not in normalized:
        print_yellow("--> 自動將 graphify-out/ 加入 .gitignore 避免記憶庫膨脹...")
        new_lines = []
        replaced = False
        for line in lines:
            stripped = line.strip().rstrip("/")
            if stripped in ("graphify-out/cache", "graphify-out/cache/"):
                new_lines.append("# Graphify output directory (regenerated on demand)")
                new_lines.append("graphify-out/")
                replaced = True
            else:
                new_lines.append(line)
        
        if not replaced:
            if new_lines and new_lines[-1].strip():
                new_lines.append("")
            new_lines.append("# Graphify output directory (regenerated on demand)")
            new_lines.append("graphify-out/")
            
        try:
            gitignore.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        except Exception as e:
            print(red(f"警告：更新 .gitignore 失敗 ({e})"))


def _run_mempalace_init() -> bool:
    try:
        subprocess.run([TOOL_MEMPALACE, "init", "--yes", "--auto-mine", "--no-llm", "."], check=True)
        try:
            # Sync to apply prune on newly ignored files like graphify-out/
            subprocess.run([TOOL_MEMPALACE, "sync", "--apply", "."], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
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
