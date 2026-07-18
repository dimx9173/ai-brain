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
VERSION = "2.6.7"
APP_NAME = "AI Brain Orchestrator"
APP_EMOJI = "🧠"

# --- CLI tool identifiers -------------------------------------------------------
TOOL_MEMPALACE = "mempalace"
TOOL_CODEBASE_MEMORY = "codebase-memory-mcp"

# --- MCP server identifiers -----------------------------------------------------
MCP_MEMPALACE = "mempalace"
MCP_CODEBASE_MEMORY = "codebase-memory-mcp"
MCP_REQUIRED_SERVERS = (MCP_MEMPALACE, MCP_CODEBASE_MEMORY)

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


def LAST_GC_FILE() -> Path:
    return Path.home() / ".claude" / "last_gc_timestamp"


def GLOBAL_AI_BRAIN() -> Path:
    return Path.home() / ".local" / "bin" / "ai-brain"


def GLOBAL_MEMPALACE_MCP() -> Path:
    return Path.home() / ".local" / "bin" / "mempalace-mcp"


# --- MemPalace MCP command auto-detection ---------------------------------------
# Prefer pip-installed version (`python3 -m mempalace.mcp_server`) over the
# `uv tool install` binary shim. The uv shim (v3.5.0) hangs on large 3.2GB
# chroma DBs under certain spawn modes (e.g. opencode/kilo `type: "local"`).
# The pip version (typically 3.3.4) responds in <2s.
# Cached per-process: `ai-brain` CLI calls this a few times per invocation,
# and the pip-availability answer won't change mid-run.
_MEMPALACE_MCP_COMMAND: list[str] | None = None


def MEMPALACE_MCP_COMMAND() -> list[str]:
    """Prefer pip-installed ``python3 -m mempalace.mcp_server`` (fast); fall
    back to the uv binary at ``~/.local/bin/mempalace-mcp``. Cached per-process.
    """
    global _MEMPALACE_MCP_COMMAND
    if _MEMPALACE_MCP_COMMAND is not None:
        return _MEMPALACE_MCP_COMMAND

    import shutil
    import subprocess

    try:
        python3 = shutil.which("python3")
        if python3:
            result = subprocess.run(
                [python3, "-c", "import mempalace.mcp_server"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                _MEMPALACE_MCP_COMMAND = [python3, "-m", "mempalace.mcp_server"]
                return _MEMPALACE_MCP_COMMAND
    except BaseException:
        pass

    _MEMPALACE_MCP_COMMAND = [str(GLOBAL_MEMPALACE_MCP())]
    return _MEMPALACE_MCP_COMMAND
def GLOBAL_CODEBASE_MEMORY_MCP() -> Path:
    return Path.home() / ".local" / "bin" / "codebase-memory-mcp"


# --- Tools supported by full-init -----------------------------------------------
CODEBASE_MEMORY_TOOLS = ("antigravity", "gemini", "kilo", "cursor", "claude", "opencode", "codex", "aider", "trae", "claw")

# The `uv tool install` package names ai-brain orchestrates. Keep this in sync
# with `upgraders.CORE_TOOLS` — both are the source of truth for the same set.
# (Belt-and-braces: `upgraders` carries binary ↔ package mapping, this list
# is used by the install / uninstall messaging.)
UV_TOOL_PACKAGES = ("mempalace", "claude-mem", "codebase-memory-mcp")

# --- Time & thresholds ----------------------------------------------------------
SWEEP_BACKGROUND_GAP_SECONDS = 12 * 60 * 60
GC_BACKGROUND_GAP_SECONDS = 7 * 24 * 60 * 60  # 7 days
CRON_SCHEDULE = "30 23 * * *"
CRON_CMD = '$HOME/.local/bin/ai-brain stop > /dev/null 2>&1'

# --- Project artifacts ---------------------------------------------------------
PROJECT_CONFIG_FILE = ".claude/config.json"
PROJECT_CLAUDE_MD = ".claude/CLAUDE.md"
PROJECT_MEMPALACE_FILES = ("mempalace.yaml", "entities.json")
CODEBASE_MEMORY_OUT_DIR = ".codebase-memory"
LOCAL_CODEBASE_MEMORY_SKILL = ".claude/skills/codebase-memory"

# --- Hook markers & text snippets -----------------------------------------------
COGNITIVE_PRINCIPLES_MARKER = "## 🧠 Layered Memory & Cognitive Workflow (Mandatory Principles)"
COGNITIVE_PRINCIPLES_BLOCK = """
## 🧠 Layered Memory & Cognitive Workflow (Mandatory Principles)
You must actively traverse and respect the three cognitive memory layers before reasoning or executing commands:

1. **L0: Working Memory (Session & Developer Context)** — `claude-mem`
   - **Action**: Always respect developer habits and session checkpoints injected via `claude-mem`. If `claude-mem` is unavailable (e.g. in Kilo, OpenClaw, OpenCode, Claude Desktop), fall back to active chat session history and local scratchpads (e.g. active notes or current document) to maintain short-term state.
   - **Purpose**: Maintain task continuity and follow local guidelines for the active coding session.
   - **Lifecycle**: Session-bound, auto-compacted.

2. **L1: Structural Memory (Codebase Topology)** — `codebase-memory-mcp`
   - **Action**: ALWAYS prefer `codebase-memory-mcp` graph tools over grep/glob/file-search for code discovery. Use them in this priority order:
     1. `search_graph` — find functions, classes, routes, variables by pattern
     2. `trace_path` — trace who calls a function or what it calls
     3. `get_code_snippet` — read specific function/class source code
     4. `query_graph` — run Cypher queries for complex patterns
     5. `get_architecture` — high-level project summary
   - **Fallback to grep/glob** only when: searching string literals/error messages, non-code files (Dockerfiles, shell scripts), or when MCP tools return insufficient results.
   - **Non-Text Documents**: Non-plain-text docs (PDF, Docx, Xlsx) in docs/ are parsed into markdown in `.ai-brain/parsed-docs/`. Proactively read them when analyzing specifications.
   - **Purpose**: Full code indexing — functions, classes, call graphs, dependencies.
   - **Lifecycle**: Project-bound, rebuilt on `git pull`/`checkout`. **This is where code lives.**

3. **L2: Long-Term Memory (Historical Memory Palace)** — `mempalace`
   - **Action**: Before answering queries about system design, past debugging history, environment setups, or business logic, proactively query the memory database using:
     - `mempalace_search` — semantic full-text search across all memories
     - `mempalace_kg_query` — structured knowledge graph query
     - `mempalace_traverse` — traverse connected entities
     - `mempalace_get_drawer` — retrieve a specific memory by ID
   - **Purpose**: High-value persistent knowledge — conversations, architecture decisions, debug war stories, environment configs, lessons learned.
   - **Lifecycle**: Cross-project, permanent.
   - **⚠️ IMPORTANT**: Do NOT mine entire codebases into mempalace. L2 is for curated, high-value memories only. Code indexing belongs in L1 (codebase-memory-mcp). Use `ai-brain mine` to selectively add specific content."""

LOCAL_CLAUDE_MD_TEMPLATE = """# AI Agent Cognitive Workflow and Memory Guide

## 🧠 Layered Memory & Cognitive Workflow (Mandatory Principles)
You must actively traverse and respect the three cognitive memory layers before reasoning or executing commands:

1. **L0: Working Memory (Session & Developer Context)** — `claude-mem`
   - **Action**: Always respect developer habits and session checkpoints injected via `claude-mem`. If `claude-mem` is unavailable (e.g. in Kilo, OpenClaw, OpenCode, Claude Desktop), fall back to active chat session history and local scratchpads (e.g. active notes or current document) to maintain short-term state.
   - **Purpose**: Maintain task continuity and follow local guidelines for the active coding session.
   - **Lifecycle**: Session-bound, auto-compacted.

2. **L1: Structural Memory (Codebase Topology)** — `codebase-memory-mcp`
   - **Action**: ALWAYS prefer `codebase-memory-mcp` graph tools over grep/glob/file-search for code discovery. Use them in this priority order:
     1. `search_graph` — find functions, classes, routes, variables by pattern
     2. `trace_path` — trace who calls a function or what it calls
     3. `get_code_snippet` — read specific function/class source code
     4. `query_graph` — run Cypher queries for complex patterns
     5. `get_architecture` — high-level project summary
   - **Fallback to grep/glob** only when: searching string literals/error messages, non-code files (Dockerfiles, shell scripts), or when MCP tools return insufficient results.
   - **Non-Text Documents**: Non-plain-text docs (PDF, Docx, Xlsx) in docs/ are parsed into markdown in `.ai-brain/parsed-docs/`. Proactively read them when analyzing specifications.
   - **Purpose**: Full code indexing — functions, classes, call graphs, dependencies.
   - **Lifecycle**: Project-bound, rebuilt on `git pull`/`checkout`. **This is where code lives.**

3. **L2: Long-Term Memory (Historical Memory Palace)** — `mempalace`
   - **Action**: Before answering queries about system design, past debugging history, environment setups, or business logic, proactively query the memory database using:
     - `mempalace_search` — semantic full-text search across all memories
     - `mempalace_kg_query` — structured knowledge graph query
     - `mempalace_traverse` — traverse connected entities
     - `mempalace_get_drawer` — retrieve a specific memory by ID
   - **Purpose**: High-value persistent knowledge — conversations, architecture decisions, debug war stories, environment configs, lessons learned.
   - **Lifecycle**: Cross-project, permanent.
   - **⚠️ IMPORTANT**: Do NOT mine entire codebases into mempalace. L2 is for curated, high-value memories only. Code indexing belongs in L1 (codebase-memory-mcp). Use `ai-brain mine` to selectively add specific content.
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
# Serialize concurrent `git pull` invocations: flock auto-releases on exit.
exec 9>.git/ai-brain-post-merge.lock
if ! flock -n 9; then
    echo "[ai-brain] skipping: another post-merge hook instance running"
    exit 0
fi
echo -e "\\033[0;34m====== 🌅 Git Pull 偵測：自動更新代碼架構圖譜 ======\\033[0m"
{chain}
{end}
"""

# post-checkout runs on every branch switch; keep it non-blocking by running
# the heavy graph rebuild in a subshell in the background.  flock(1) provides
# an atomic, TOCTOU-free lock — no PID files, no mkdir races, no trap cleanup.
POST_CHECKOUT_TEMPLATE = """#!/bin/bash
{begin}
{marker_body}
# 只在切換分支時觸發（引數 $3 為 1 代表切換分支，0 代表檢出單一檔案）
if [ "$3" -eq 1 ]; then
    (
        LOCK_FILE=".git/ai-brain-checkout.lock"
        exec 8>"$LOCK_FILE"
        if ! flock -n 8; then
            echo "[ai-brain] skipping: another post-checkout running"
            exit 0
        fi

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
fi"""
