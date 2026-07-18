"""CLI entrypoint — argparse + dispatch table.

Each subcommand maps to a single callable. Adding a new command means:
1. add an entry to `COMMANDS`,
2. add an argparse subparser,
3. implement the function in `commands.py` / `installer.py`.

No more `if cmd == "init": ... elif cmd == "start": ...` chain.
"""
from __future__ import annotations

import argparse
import sys
from typing import Callable

from . import commands, installer
from .constants import APP_EMOJI, APP_NAME, VERSION
from .platforms import ensure_path_has_local_bin, get_paths
from .ui import blue, green, red, yellow
from .verifier import print_results, run_all_checks


def _show_help() -> None:
    print(blue(f"{APP_EMOJI} {APP_NAME} v{VERSION}"))
    print("這款工具幫您一鍵搞定 Claude Code / Cowork / OpenClaw / OpenCode 與 Gemini/Antigravity IDE 的架構地圖與記憶同步。\n")
    print(yellow("用法:"))
    print("  ai-brain [指令]\n")
    print(yellow("可用指令:"))
    rows = [
        ("init", "[一次性] 全自動初始化（✅ Git Hook 背景更新 + 註冊深夜自動歸檔，⚠️ 預設不啟用歸檔）"),
        ("init -a", "[一次性] 全自動初始化 + 自動啟用排程歸檔（✅ 推薦）"),
        ("init -m", "[一次性] 標準初始化（❌ 需手動：後續需手動執行 start/stop）"),
        ("mine <target>", "[選擇性歸檔] 歸檔高價值內容至 L2 記憶宮殿（對話、文件、重要檔案）"),
        ("install / update", "[全域安裝] 複製/更新 ai-brain 指令至全域路徑並驗證 PATH"),
        ("version", "[顯示版本] 顯示目前安裝的 ai-brain 工具版本"),
        ("start", "[每日晨間] 更新最新的代碼地圖圖譜（讓 AI 開發時不迷路且省 Token）"),
        ("stop", "[下班收尾] 安全掃描並歸檔一整天的對話到長期記憶中樞（防死鎖）"),
        ("status", "[狀態檢查] 查看目前專案的大腦配置狀態（含 Palace 容量）"),
        ("verify", "[一鍵驗證] 檢測所有 AI 記憶套件與守護行程是否正確配置且運行正常"),
        ("clean", "[專案清理] 移除目前專案的 AI 大腦與記憶配置"),
        ("uninstall", "[全域移除] 清理目前的專案配置、全域排程、MCP 註冊與全域軟連結"),
        ("stop-cron", "[停止排程] 僅移除定時自動歸檔的 Cron Job"),
        ("exclude [key]", "[停用歸檔] 停用指定專案自動歸檔，或查看歸檔狀態清單"),
        ("include [key]", "[啟用歸檔] 啟用指定專案定時自動歸檔"),
        ("exclude-all", "[全部停用] 一鍵停用所有專案的自動歸檔"),
        ("include-all", "[全部啟用] 一鍵啟用所有註冊專案的自動歸檔"),
        ("list", "[查詢狀態] 顯示所有已註冊專案的自動歸檔狀態列表"),
        ("remove [key]", "[註銷專案] 自大腦活躍專案清單中移除指定專案的註冊"),
        ("doctor", "[全面診斷] 檢查專案配置、資料庫鎖定、MCP 路徑與垃圾清理"),
        ("doctor --fix", "[診斷修復] 全面檢查並自動修正所有配置問題"),
        ("gc", "[垃圾回收] 清理 drift 備份、同步記憶庫、壓縮 ChromaDB"),
        ("gc --apply", "[實際執行] 執行垃圾回收並實際修改資料庫"),
        ("gc --purge-wing <name>", "[清除 wing] 直接刪除指定 wing 的所有 embeddings（快速釋放空間）"),
        ("mcp-sync", "[MCP 同步] 檢查所有 IDE 的 MCP 指令路徑是否為最新"),
        ("mcp-sync --fix", "[MCP 修復] 自動同步所有 MCP 指令路徑至最快可用版本"),
        ("completions <action>", "[Tab 補完] 安裝/移除 bash|zsh|fish 的指令補完腳本"),
        ("config global", "[全域配置] 顯示或設定 AI 大腦全域偏好與衝突覆寫規則"),
    ]
    for name, desc in rows:
        print(f"  {green(name):<24} - {desc}")

    print()
    print(yellow("💡 核心工作流指引 (Core Workflows):"))
    print(f"  {blue('1. 專案初始化 (一次性)')}")
    print("     在新專案根目錄下執行以下指令，以完成大腦空間、規則檔及 Git Hook 配置：")
    print(f"     ➔ {green('ai-brain init -a')}      (全自動初始化 + 自動啟用排程歸檔 ─ ✅ 推薦)")
    print(f"     ➔ {green('ai-brain init')}         (全自動初始化 ─ ⚠️ 預設不啟用自動歸檔)")
    print(f"     ➔ {green('ai-brain init -m')}      (標準初始化 ─ ❌ 後續需手動執行 start/stop)")
    print()
    print(f"  {blue('2. 每日開發上工 (⚠️ 僅在使用 -m 標準初始化時，才需要手動執行)')}")
    print("     每天早上開始工作時，在專案目錄下執行：")
    print(f"     ➔ {green('ai-brain start')}        (自動掃描 docs/ 文件，並建立/更新最新代碼地圖)")
    print()
    print(f"  {blue('3. 每日下班收尾 (⚠️ 僅在使用 -m 標準初始化時，才需要手動執行)')}")
    print("     工作結束要關閉終端機前，在專案目錄下執行：")
    print(f"     ➔ {green('ai-brain stop')}         (安全將今日對話與調試經驗打包歸檔至長期記憶宮殿)")
    print()
    print(f"  {blue('4. 當大腦異常或發生錯誤時 (排查修復)')}")
    print("     當代理程式出現怪異行為、遺失規則檔、連線逾時或檔案被鎖定時：")
    print(f"     ➔ {green('ai-brain status')}       (查看專案大腦健康狀態與 Palace 容量)")
    print(f"     ➔ {green('ai-brain doctor --fix')} (全面健康診斷，並自動修正所有配置與衝突問題)")
    print()


def _show_version() -> None:
    print(blue(f"{APP_EMOJI} {APP_NAME} v{VERSION}"))


def _cmd_verify(_args, _paths) -> int:
    print(blue("====== 🔍 AI 協作大腦一鍵自我驗證流程 ======"))
    results = run_all_checks(_paths)
    failures = print_results(results)
    print()
    if failures == 0:
        print(green("🎉 恭喜！一鍵驗證全部通過！您的 AI 多代理協作大腦與記憶套件處於完美狀態！"))
        return 0
    print(red(f"⚠️ 驗證未完全通過，共有 {failures} 項錯誤。請參考上述 FAIL 提示進行排查。"))
    return 1


def _cmd_init(args, paths) -> int:
    from . import registry
    ok = commands.init_brain() if args.manual else commands.full_init(paths)
    if not ok:
        return 1
    if args.auto_archive:
        registry.enable_archive(registry.current_project_path())
    return 0


def _cmd_full_init(_args, paths) -> int:
    return 0 if commands.full_init(paths) else 1


def _cmd_uninstall(_args, paths) -> int:
    return 0 if commands.uninstall_all(paths) else 1


def _cmd_exclude(args, _paths) -> int:
    return 0 if commands.manage_exclude(args.pattern) else 1


def _cmd_include(args, _paths) -> int:
    return 0 if commands.manage_include(args.pattern) else 1


def _cmd_remove(args, _paths) -> int:
    return 0 if commands.manage_remove(args.pattern) else 1


def _cmd_doctor(args, paths) -> int:
    return 0 if commands.run_doctor(paths, target=args.target, fix=args.fix) else 1


def _cmd_gc(args, _paths) -> int:
    return 0 if commands.run_gc(apply=args.apply, purge_wing=args.purge_wing) else 1


def _cmd_mine(args, _paths) -> int:
    return 0 if commands.mine_to_palace(
        target=args.target or ".",
        mode=args.mine_mode or "default",
        wing=args.wing,
    ) else 1


def _cmd_mcp_sync(args, paths) -> int:
    return 0 if commands.sync_mcp_paths(paths, fix=args.fix) else 1


def _cmd_config(args, paths) -> int:
    return 0 if commands.run_config(paths, args) else 1


def _cmd_completions(args) -> int:
    """Dispatch to ai_brain.completions.main for the completions subcommand."""
    from . import completions as _completions
    sub_argv = [args.action]
    if getattr(args, "shell", None):
        sub_argv.append(args.shell)
    return _completions.main(sub_argv)


# --- Dispatch table: name -> (callable, takes_args_and_paths) ------------------
# Using a dict + dict of factory functions so the argparse binding stays explicit.
COMMANDS: dict[str, Callable[[argparse.Namespace, object], int]] = {
    "init": _cmd_init,
    "full-init": _cmd_full_init,
    "mine": _cmd_mine,
    "install": lambda a, p: 0 if installer.install_or_update() else 1,
    "update": lambda a, p: 0 if installer.install_or_update() else 1,
    "version": lambda a, p: (_show_version(), 0)[1],
    "-v": lambda a, p: (_show_version(), 0)[1],
    "--version": lambda a, p: (_show_version(), 0)[1],
    "start": lambda a, p: 0 if commands.start_day(fast=a.fast) else 1,
    "stop": lambda a, p: 0 if commands.stop_day() else 1,
    "status": lambda a, p: (commands.check_status(), 0)[1],
    "verify": _cmd_verify,
    "clean": lambda a, p: (commands.clean_brain(), 0)[1],
    "uninstall": _cmd_uninstall,
    "stop-cron": lambda a, p: 0,  # cron-only command — handled in main() since we lazy-import
    "exclude": _cmd_exclude,
    "include": _cmd_include,
    "remove": _cmd_remove,
    "deregister": _cmd_remove,
    "exclude-all": lambda a, p: (commands.exclude_all(), 0)[1],
    "include-all": lambda a, p: (commands.include_all(), 0)[1],
    "list": lambda a, p: (commands.manage_list(), 0)[1],
    "doctor": _cmd_doctor,
    "gc": _cmd_gc,
    "mcp-sync": _cmd_mcp_sync,
    "completions": lambda a, p: _cmd_completions(a),
    "config": _cmd_config,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-brain",
        add_help=False,  # we render our own coloured help
        description=f"{APP_EMOJI} {APP_NAME} v{VERSION}",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    def _add_common(name: str, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text, add_help=False)
        return p

    p = sub.add_parser("init", help="Initialize local AI brain configuration", add_help=False)
    p.add_argument("-m", "--manual", action="store_true", help="Manual (standard) initialization without cron registration")
    p.add_argument("-a", "--auto-archive", action="store_true", help="Automatically whitelist/enable auto-archiving for this project")
    _add_common("full-init", "Initialize and register global cron + MCP")
    p = sub.add_parser("mine", help="Selectively mine high-value content into L2 memory palace", add_help=False)
    p.add_argument("target", nargs="?", default=".",
                   help="file or directory to mine (default: current directory)")
    p.add_argument("--mode", dest="mine_mode",
                   choices=["default", "convos", "extract", "file"],
                   default="default",
                   help="mining mode: default=selective files, convos=conversations, extract=PDFs/docs, file=single file")
    p.add_argument("--wing", default=None,
                   help="palace wing to file under (default: auto-detect from project)")
    _add_common("install", "Install/update the global ai-brain command")
    _add_common("update", "Alias for install (auto-pulls from git source)")
    _add_common("version", "Show installed version")
    p = sub.add_parser("start", help="Refresh codebase architecture map", add_help=False)
    p.add_argument("--fast", action="store_true", help="Incremental update without clustering")
    p.add_argument("--no-cluster", action="store_true", help="Incremental update without clustering (alias)")
    _add_common("stop", "Archive today's conversations to long-term memory")
    _add_common("status", "Show current project brain status")
    _add_common("verify", "Run 9-point health check")
    p = sub.add_parser("doctor", help="Run comprehensive diagnostics on AI brain", add_help=False)
    p.add_argument("target", nargs="?", default=None,
                   help="project keyword, 1-based index, '.', 'current', or omitted to check all registered projects")
    p.add_argument("--fix", action="store_true", help="Automatically fix detected issues")
    p = sub.add_parser("gc", help="Garbage-collect palace drift backups and compress ChromaDB", add_help=False)
    p.add_argument("--apply", action="store_true", help="Actually perform changes (default is dry-run)")
    p.add_argument("--purge-wing", default=None,
                   help="Delete all embeddings for a specific wing (bypasses slow sync/compress)")
    p = sub.add_parser("mcp-sync", help="Sync all MCP server command paths to fastest binary", add_help=False)
    p.add_argument("--fix", action="store_true", help="Actually update stale paths")
    p = sub.add_parser("config", help="Manage global configuration settings", add_help=False)
    p.add_argument("action", choices=["global"], help="scope: global")
    p.add_argument("--set", dest="config_set", help="set parameter (key=value or section.key=value)")
    p.add_argument("--list", dest="config_list", action="store_true", help="list all configurations")
    _add_common("clean", "Remove local brain configuration")
    _add_common("uninstall", "Global removal of all configs and crons")
    _add_common("stop-cron", "Remove the daily auto-archive cron job")

    for name, help_text in (
        ("exclude", "Disable auto-archive (pattern can be a keyword, 1-based index, or 'all')"),
        ("include", "Enable auto-archive (pattern can be a keyword, 1-based index, or 'all')"),
        ("remove", "Remove project from registered active list (pattern can be a keyword, 1-based index, or 'all')"),
        ("deregister", "Alias for remove"),
    ):
        p = _add_common(name, help_text)
        p.add_argument("pattern", nargs="?", default=None,
                       help="project keyword, 1-based index, '.', 'current', 'all', or omitted for list")

    for name, help_text in (
        ("exclude-all", "Disable auto-archive for all projects"),
        ("include-all", "Enable auto-archive for all registered projects"),
        ("list", "Show auto-archive status of all registered projects"),
    ):
        _add_common(name, help_text)

    # --- completions subcommand (with sub-actions) ----------------------------
    p = sub.add_parser(
        "completions",
        help="Manage shell tab-completion scripts (bash/zsh/fish)",
        add_help=False,
    )
    p.add_argument("action", choices=["show", "install", "uninstall"], help="what to do")
    p.add_argument("shell", nargs="?", choices=["bash", "zsh", "fish"],
                   help="target shell (default: all)")

    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_path_has_local_bin()
    paths = get_paths()
    argv = argv if argv is not None else sys.argv[1:]

    if not argv:
        _show_help()
        return 0

    # Help flag
    if argv[0] in ("-h", "--help", "help"):
        _show_help()
        return 0

    # Resolve alias forms before argparse (version flags, update alias).
    cmd = argv[0]
    if cmd in COMMANDS:
        # Lazy cron import keeps `stop-cron` and other sub-modules self-contained.
        if cmd == "stop-cron":
            from . import cron
            return 0 if cron.uninstall() else 1
        return COMMANDS[cmd](_Namespace_for(cmd, argv[1:]), paths)

    # Unknown command — fall through to argparse for a useful error.
    parser = _build_parser()
    parser.parse_args(argv)  # raises SystemExit on error
    _show_help()
    return 0


# Tiny shim so command functions that don't read args still receive a Namespace.
class _Namespace_for:
    def __init__(self, cmd: str, rest: list[str]) -> None:
        self.cmd = cmd
        self.target = rest[0] if rest and not rest[0].startswith("-") else None
        self.pattern = rest[0] if rest else None
        # `completions` subcommand uses action + optional shell positional.
        # We only populate these when relevant; other commands ignore them.
        self.action = rest[0] if rest else None
        self.shell = rest[1] if len(rest) > 1 else None
        self.fix = "--fix" in rest
        self.fast = "--fast" in rest or "--no-cluster" in rest
        self.apply = "--apply" in rest
        self.manual = "-m" in rest or "--manual" in rest
        self.auto_archive = "-a" in rest or "--auto-archive" in rest
        # `mine` subcommand args
        self.mine_mode = None
        self.wing = None
        self.purge_wing = None
        # `config` subcommand args
        self.config_list = "--list" in rest
        self.config_set = None
        for i, arg in enumerate(rest):
            if arg == "--mode" and i + 1 < len(rest):
                self.mine_mode = rest[i + 1]
            elif arg == "--wing" and i + 1 < len(rest):
                self.wing = rest[i + 1]
            elif arg == "--purge-wing" and i + 1 < len(rest):
                self.purge_wing = rest[i + 1]
            elif arg == "--set" and i + 1 < len(rest):
                self.config_set = rest[i + 1]


if __name__ == "__main__":
    sys.exit(main())
