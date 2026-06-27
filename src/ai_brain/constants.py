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
VERSION = "2.0.12"
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


def GLOBAL_GRAPHIFY_MCP_WRAPPER() -> Path:
    return Path.home() / ".local" / "bin" / "graphify-mcp-wrapper"


# --- Tools supported by full-init -----------------------------------------------
GRAPHIFY_TOOLS = ("antigravity", "kilo", "cursor", "claude", "opencode", "codex", "aider", "trae", "claw")

# The `uv tool install` package names ai-brain orchestrates. Keep this in sync
# with `upgraders.CORE_TOOLS` — both are the source of truth for the same set.
# (Belt-and-braces: `upgraders` carries binary ↔ package mapping, this list
# is used by the install / uninstall messaging.)
UV_TOOL_PACKAGES = ("mempalace", "claude-mem", "graphifyy[mcp]")

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
COGNITIVE_PRINCIPLES_MARKER = "## 🧠 Layered Memory & Cognitive Workflow (Mandatory Principles)"
COGNITIVE_PRINCIPLES_BLOCK = """
## 🧠 Layered Memory & Cognitive Workflow (Mandatory Principles)
You must actively traverse and respect the three cognitive memory layers before reasoning or executing commands:

1. **L0: Working Memory (Session & Developer Context)**
   - **Action**: Always respect developer habits and session checkpoints injected via `claude-mem`. If `claude-mem` is unavailable (e.g. in Kilo, OpenClaw, OpenCode, Claude Desktop), fall back to active chat session history and local scratchpads (e.g. active notes or current document) to maintain short-term state.
   - **Purpose**: Maintain task continuity and follow local guidelines for the active coding session.

2. **L1: Structural Memory (Codebase Topology)**
   - **Action**: Before modifying any source files or proposing refactors, proactively query the codebase map (via `query_graph`, `codegraph_*` tools, or checking `./graphify-out/`).
   - **Purpose**: Map out upstream/downstream module dependencies and community structures to prevent architectural regressions.

3. **L2: Long-Term Memory (Historical Memory Palace)**
   - **Action**: Before answering queries about system design, past debugging history, environment setups, or business logic, proactively query the memory database (via `mempalace_search` or `mempalace_kg_query`).
   - **Purpose**: Leverage persistent historical context to avoid repeating past errors or reinventing existing patterns."""

LOCAL_CLAUDE_MD_TEMPLATE = """# AI Agent Cognitive Workflow and Memory Guide

## 🧠 Layered Memory & Cognitive Workflow (Mandatory Principles)
You must actively traverse and respect the three cognitive memory layers before reasoning or executing commands:

1. **L0: Working Memory (Session & Developer Context)**
   - **Action**: Always respect developer habits and session checkpoints injected via `claude-mem`. If `claude-mem` is unavailable (e.g. in Kilo, OpenClaw, OpenCode, Claude Desktop), fall back to active chat session history and local scratchpads (e.g. active notes or current document) to maintain short-term state.
   - **Purpose**: Maintain task continuity and follow local guidelines for the active coding session.

2. **L1: Structural Memory (Codebase Topology)**
   - **Action**: Before modifying any source files or proposing refactors, proactively query the codebase map (via `query_graph`, `codegraph_*` tools, or checking `./graphify-out/`).
   - **Purpose**: Map out upstream/downstream module dependencies and community structures to prevent architectural regressions.

3. **L2: Long-Term Memory (Historical Memory Palace)**
   - **Action**: Before answering queries about system design, past debugging history, environment setups, or business logic, proactively query the memory database (via `mempalace_search` or `mempalace_kg_query`).
   - **Purpose**: Leverage persistent historical context to avoid repeating past errors or reinventing existing patterns.

## 🗺️ Graphify Skill and Command Integration
- **`/graphify` Shortcut**: When the user enters `/graphify` in the chat, **you must prioritize calling the Skill tool and specifying `skill: "graphify"`** before executing any other actions.
"""

HOOKS_CONFIG = {
    "hooks": {
        "preCompact": "claude-mem compress --target local",
        "onStop": "claude-mem checkpoint",
    }
}

# --- Git hook templates ---------------------------------------------------------
# Marker used to identify ai-brain managed sections inside user hooks.
HOOK_BEGIN_MARKER = "# >>> ai-brain {name} hook begin"
HOOK_END_MARKER = "# <<< ai-brain {name} hook end"
HOOK_MARKER_BODY = "# (auto-managed by ai-brain; do not edit between markers)"

POST_MERGE_TEMPLATE = """#!/bin/bash
{begin}
{marker_body}
echo -e "\\033[0;34m====== 🌅 Git Pull 偵測：自動更新代碼架構圖譜 ======\\033[0m"
{chain}
{end}
"""

# post-checkout runs on every branch switch; keep it non-blocking by running
# the heavy graph rebuild in a subshell in the background.  A PID-based lock
# prevents concurrent / back-to-back branch switches from stacking graphify runs.
POST_CHECKOUT_TEMPLATE = """#!/bin/bash
{begin}
{marker_body}
# 只在切換分支時觸發（引數 $3 為 1 代表切換分支，0 代表檢出單一檔案）
if [ "$3" -eq 1 ]; then
    (
        LOCK_DIR=".git/ai-brain-checkout.lock"
        if [ -d "$LOCK_DIR" ]; then
            PID_FILE="$LOCK_DIR/pid"
            if [ -f "$PID_FILE" ]; then
                OLD_PID=$(cat "$PID_FILE" 2>/dev/null)
                if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
                    exit 0
                fi
            fi
            rm -rf "$LOCK_DIR"
        fi
        mkdir "$LOCK_DIR" 2>/dev/null || exit 0
        echo $$ > "$LOCK_DIR/pid"
        trap 'rm -rf "$LOCK_DIR"' EXIT

        echo -e "\\033[0;34m====== 🌅 Git Branch 切換偵測：背景更新代碼架構圖譜 ======\\033[0m"
        {chain}
    ) >/dev/null 2>&1 &
fi
{end}
"""

HOOK_CHAIN = """if command -v ai-brain &> /dev/null; then
    ai-brain start --fast
elif [ -f "./bin/ai-brain" ]; then
    ./bin/ai-brain start --fast
elif [ -f "./PC/Knowhow/ai-brain.sh" ]; then
    ./PC/Knowhow/ai-brain.sh start --fast
elif [ -f "./ai-brain.sh" ]; then
    ./ai-brain.sh start --fast
else
    command -v graphify &> /dev/null && graphify update . --no-cluster
fi"""
