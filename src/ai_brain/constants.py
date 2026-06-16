"""Project-wide constants for ai-brain.

Centralizes magic strings, file paths, and tool identifiers so they can be
referenced consistently across the CLI and changed in a single place.

NOTE on path constants: most are functions (callable) rather than pre-resolved
Path objects, so tests that stub `Path.home()` see the stub. Modules that need
a Path *at import time* (e.g. for type annotations) can still call these
factories lazily.
"""
from __future__ import annotations

from pathlib import Path

# --- Version & metadata ---------------------------------------------------------
VERSION = "1.2.0"
APP_NAME = "AI Brain Orchestrator"
APP_EMOJI = "🧠"

# --- CLI tool identifiers -------------------------------------------------------
TOOL_MEMPALACE = "mempalace"
TOOL_MEMPALACE_MCP = "mempalace-mcp"
TOOL_GRAPHIFY = "graphify"
TOOL_CLAUDE_MEM = "claude-mem"

# --- MCP server identifiers -----------------------------------------------------
MCP_MEMPALACE = "mempalace"
MCP_GRAPHIFY = "graphify"
MCP_REQUIRED_SERVERS = (MCP_MEMPALACE, MCP_GRAPHIFY)

# --- LLM providers --------------------------------------------------------------
PROVIDER_MINIMAX = "minimax"
MINIMAX_PROVIDER_CONFIG = {
    "base_url": "https://api.minimax.io/v1",
    "default_model": "minimax-m2.5",
    "env_key": "MINIMAX_API_KEY",
    "temperature": 0,
}


# --- State files (lazy: re-resolve Path.home() on each access) ----------------
# Using functions so test fixtures that stub Path.home() actually take effect.
def HOME() -> Path:
    return Path.home()


def CLAUDE_HOME() -> Path:
    return Path.home() / ".claude"


def REGISTRY_PATH() -> Path:
    return Path.home() / ".claude" / "ai_brain_active_projects.txt"


def AUTO_ARCHIVE_PATH() -> Path:
    return Path.home() / ".claude" / "ai_brain_auto_archive.txt"


def INSTALL_SOURCE_REGISTRY() -> Path:
    return Path.home() / ".claude" / "ai_brain_install_source.txt"


def LAST_SWEEP_FILE() -> Path:
    return Path.home() / ".claude" / "last_sweep_timestamp"


def GLOBAL_AI_BRAIN() -> Path:
    return Path.home() / ".local" / "bin" / "ai-brain"


def GLOBAL_MEMPALACE_MCP() -> Path:
    return Path.home() / ".local" / "bin" / "mempalace-mcp"


# --- Tools supported by full-init -----------------------------------------------
GRAPHIFY_TOOLS = ("antigravity", "kilo", "cursor", "claude", "opencode")

# The `uv tool install` package names ai-brain orchestrates. Keep this in sync
# with `upgraders.CORE_TOOLS` — both are the source of truth for the same set.
# (Belt-and-braces: `upgraders` carries binary ↔ package mapping, this list
# is used by the install / uninstall messaging.)
UV_TOOL_PACKAGES = ("mempalace", "claude-mem", "graphifyy")

# --- Time & thresholds ----------------------------------------------------------
SWEEP_BACKGROUND_GAP_SECONDS = 12 * 60 * 60
CRON_SCHEDULE = "30 23 * * *"
CRON_CMD = '$HOME/.local/bin/ai-brain stop > /dev/null 2>&1'

# --- Project artifacts ---------------------------------------------------------
PROJECT_CONFIG_FILE = ".claude/config.json"
PROJECT_CLAUDE_MD = "CLAUDE.md"
PROJECT_MEMPALACE_FILES = ("mempalace.yaml", "entities.json")
GRAPHIFY_OUT_DIR = "graphify-out"
LOCAL_GRAPHIFY_SKILL = ".claude/skills/graphify"

# --- Hook markers & text snippets -----------------------------------------------
COGNITIVE_PRINCIPLES_MARKER = "## 🧠 AI 代理自我約束與主動思維模式 (Mandatory Cognitive Principles)"
COGNITIVE_PRINCIPLES_BLOCK = """
## 🧠 AI 代理自我約束與主動思維模式 (Mandatory Cognitive Principles)
1. **主動記憶檢索（不需用戶指定）**：在回答任何關於「系統設計」、「過去的問題/除錯記錄」、「環境配置」、「特定業務邏輯」的提問前，**你必須在第一步主動調用 `mempalace_search` 工具**，檢索相關歷史記憶。嚴禁完全依賴內建知識庫憑空猜測。
2. **架構變更防線**：在修改任何代碼檔案或進行重構前，**你必須主動調用 `query_graph` 或查閱 `./graphify-out/` 索引**，以確保理解模組之間的上下游相依性。
3. **短期記憶遵循**：遵循 `claude-mem` 注入的局部 Checkpoint 與開發習慣."""

LOCAL_CLAUDE_MD_TEMPLATE = """# AI Agent 認知工作流與大腦記憶指引

## 🧠 AI 代理自我約束與主動思維模式 (Mandatory Cognitive Principles)
1. **主動記憶檢索（不需用戶指定）**：在回答任何關於「系統設計」、「過去的問題/除錯記錄」、「環境配置」、「特定業務邏輯」的提問前，**你必須在第一步主動調用 `mempalace_search` 工具**，檢索相關歷史記憶。嚴禁完全依賴內建知識庫憑空猜測。
2. **架構變更防線**：在修改任何代碼檔案或進行重構前，**你必須主動調用 `query_graph` 或查閱 `./graphify-out/` 索引**，以確保理解模組之間的上下游相依性。
3. **短期記憶遵循**：遵循 `claude-mem` 注入的局部 Checkpoint 與開發習慣。

## 🗺️ Graphify 技能與指令整合
- **`/graphify` 快捷指令**：當用戶在對話中輸入 `/graphify` 時，請在執行 any 其他動作前，優先調用 Skill 工具並指定 `skill: "graphify"`。
"""

HOOKS_CONFIG = {
    "hooks": {
        "preCompact": "claude-mem compress --target local",
        "onStop": "claude-mem checkpoint",
    }
}

# --- Git hook templates ---------------------------------------------------------
POST_MERGE_TEMPLATE = """#!/bin/bash
echo -e "\\033[0;34m====== 🌅 Git Pull 偵測：自動更新代碼架構圖譜 ======\\033[0m"
%s
"""

POST_CHECKOUT_TEMPLATE = """#!/bin/bash
# 只在切換分支時觸發（引數 $3 為 1 代表切換分支，0 代表檢出單一檔案）
if [ "$3" -eq 1 ]; then
    echo -e "\\033[0;34m====== 🌅 Git Branch 切換偵測：自動更新代碼架構圖譜 ======\\033[0m"
    %s
fi
"""

HOOK_CHAIN = """if command -v ai-brain &> /dev/null; then
    ai-brain start
elif [ -f "./bin/ai-brain" ]; then
    ./bin/ai-brain start
elif [ -f "./PC/Knowhow/ai-brain.sh" ]; then
    ./PC/Knowhow/ai-brain.sh start
elif [ -f "./ai-brain.sh" ]; then
    ./ai-brain.sh start
else
    command -v graphify &> /dev/null && graphify .
fi"""
