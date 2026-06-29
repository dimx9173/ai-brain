"""Crontab management for the daily auto-archive job.

`crontab -l` reads, `crontab -` writes. We append a marker line containing
"ai-brain stop" so we can reliably detect / clean up our entry later
without touching other users' cron jobs.
"""
from __future__ import annotations

import shutil
import subprocess
from typing import List

from .constants import CRON_CMD, CRON_SCHEDULE
from .ui import print_blue as blue
from .ui import print_green as green
from .ui import print_red as red
from .ui import print_yellow as yellow

CRON_MARKER = "ai-brain stop"
CRON_LINE = f"{CRON_SCHEDULE} {CRON_CMD}"


def _read_crontab() -> List[str]:
    """Return current crontab lines, or [] if no crontab / tool missing / timeout."""
    if not shutil.which("crontab"):
        return []
    try:
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return result.stdout.splitlines()


def _write_crontab(lines: List[str]) -> bool:
    """Pipe *lines* into `crontab -`."""
    try:
        p = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE, text=True)
        p.communicate(input="\n".join(lines) + "\n", timeout=10)
        return p.returncode == 0
    except subprocess.TimeoutExpired:
        p.kill()
        red("❌ 寫入 crontab 逾時（>10 秒）")
        return False
    except Exception as e:
        red(f"❌ 寫入 crontab 失敗 ({e})")
        return False


def install() -> bool:
    """Register the daily 23:30 auto-archive cron (idempotent)."""
    blue("====== ⏰ 開始配置全自動定時歸檔 Cron Job ======")

    if not shutil.which("crontab"):
        yellow("⚠️ 未找到 crontab 指令，將跳過 Cron Job 配置。")
        return False

    lines = _read_crontab()
    if any(CRON_MARKER in line for line in lines):
        green("✅ 全域 Cron Job 已經配置過，跳過。")
        return True

    lines.append(CRON_LINE)
    if _write_crontab(lines):
        green("✅ 全域 Cron Job 註冊成功！每天 23:30 將自動執行全域記憶歸檔。")
        return True
    return False


def uninstall() -> bool:
    """Remove any ai-brain-owned cron lines."""
    blue("====== ⏰ 開始移除全自動定時歸檔 Cron Job ======")
    if not shutil.which("crontab"):
        yellow("⚠️ 未找到 crontab 指令，將跳過。")
        return False

    lines = _read_crontab()
    if not any(CRON_MARKER in line for line in lines):
        yellow("⚠️ 未偵測到 ai-brain 相關 Cron Job，無須移除。")
        return True

    remaining = [line for line in lines if CRON_MARKER not in line]
    if _write_crontab(remaining):
        green("✅ Cron Job 已成功移除！已停止自動記憶歸檔。")
        return True
    red("❌ 移除 Cron Job 失敗")
    return False
