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
        ("init", "[一次性] 初始化專案 AI 大腦配置（MemPalace、Graphify、記憶鉤子與引導指南）"),
        ("full-init", "[一次性] 初始化配置，並一併在系統中註冊深夜 23:30 自動記憶歸檔 Cron Job"),
        ("install / update", "[全域安裝] 複製/更新 ai-brain 指令至全域路徑並驗證 PATH"),
        ("version", "[顯示版本] 顯示目前安裝的 ai-brain 工具版本"),
        ("start", "[每日晨間] 更新最新的代碼地圖圖譜（讓 AI 開發時不迷路且省 Token）"),
        ("stop", "[下班收尾] 安全掃描並歸檔一整天的對話到長期記憶中樞（防死鎖）"),
        ("status", "[狀態檢查] 查看目前專案的大腦配置狀態"),
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
        ("doctor", "[全面診斷] 檢查專案配置、資料庫鎖定與垃圾清理"),
        ("doctor --fix", "[診斷修復] 全面檢查並自動修正所有配置問題"),
        ("completions <action>", "[Tab 補完] 安裝/移除 bash|zsh|fish 的指令補完腳本"),
    ]
    for name, desc in rows:
        print(f"  {green(name):<24} - {desc}")


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
    "init": lambda a, p: 0 if commands.init_brain() else 1,
    "full-init": _cmd_full_init,
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
    "completions": lambda a, p: _cmd_completions(a),
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

    _add_common("init", "Initialize local AI brain configuration")
    _add_common("full-init", "Initialize and register global cron + MCP")
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


if __name__ == "__main__":
    sys.exit(main())
