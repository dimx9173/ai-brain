"""Generic MCP server verification.

The original `verify_brain` had 5 near-identical "check JSON file → look for
mempalace/codebase-memory-mcp → run `shutil.which` on command" blocks. This module
replaces them with one generic checker, a list of targets, and a small
set of *optional* checks for tools like openclaw/opencode/gemini that
we probe with `command -v` first.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .constants import HOME, MCP_CODEBASE_MEMORY, MCP_REQUIRED_SERVERS, MEMPALACE_MCP_COMMAND
from .ui import green, red, yellow

PASS = "OK"
WARN = "WARN"
FAIL = "FAIL"
INFO = "INFO"


@dataclass
class CheckResult:
    name: str
    status: str  # PASS / WARN / FAIL / INFO
    detail: str = ""


# --- Generic JSON-config checker ------------------------------------------------

def check_mcp_config(
    label: str,
    config_path: Optional[Path],
    required_servers: tuple[str, ...] = MCP_REQUIRED_SERVERS,
    server_key: str = "mcpServers",
) -> CheckResult:
    """Verify that *config_path* declares every server in *required_servers*."""
    name = f"檢查 {label} MCP 記憶載入與內容正確性"
    if not config_path:
        return CheckResult(name, INFO, "(此環境未安裝，略過)")

    if not config_path.is_file():
        return CheckResult(name, INFO, "(此環境未設定，略過)")

    try:
        if config_path.suffix == ".toml":
            from .config import parse_toml
            data = parse_toml(config_path.read_text(encoding="utf-8"))
        else:
            with open(config_path, encoding="utf-8") as f:
                data = json.load(f)
    except Exception as e:
        fmt = "TOML" if config_path.suffix == ".toml" else "JSON"
        return CheckResult(name, FAIL, f"({fmt} 格式損壞 ({e}))")

    mcp_servers = data.get(server_key, {})
    for server in required_servers:
        if server not in mcp_servers:
            return CheckResult(name, FAIL, f"(未註冊 {server})")

        info = mcp_servers[server]
        cmd = info.get("command")
        if not cmd:
            return CheckResult(name, FAIL, f"({server} command 缺失)")

        # CLI tools have a list-shaped `command` field (e.g. Kilo).
        if isinstance(cmd, list):
            cmd = cmd[0]

        # If the command is python, we have to introspect the args to know
        # what we're actually validating.
        args = info.get("args", [])
        if not args and "command" in info and isinstance(info["command"], list):
            args = info["command"][1:]

        if server == MCP_CODEBASE_MEMORY:
            if "codebase-memory-mcp" not in str(cmd):
                return CheckResult(name, FAIL, f"({server} 未配置為使用 codebase-memory-mcp)")

        if not shutil.which(cmd) and not (cmd.startswith("/") and os.access(cmd, os.X_OK)):
            return CheckResult(name, FAIL, f"({server} 執行檔 ({cmd}) 無效或不可執行)")

    return CheckResult(name, PASS)


def check_cli_available(
    label: str,
    cmd: str,
    fallback_paths: tuple[Path, ...] = (),
    info_message: str = "(此環境未安裝，略過)",
) -> CheckResult:
    """Check if a CLI is reachable via PATH or any *fallback_paths*."""
    name = f"檢查 {label} CLI 工具"
    resolved = cmd
    if not shutil.which(cmd):
        for fb in fallback_paths:
            if fb.is_file():
                resolved = str(fb)
                os.environ["PATH"] = str(fb.parent) + os.path.pathsep + os.environ.get("PATH", "")
                break
        else:
            return CheckResult(name, INFO, info_message)

    if not (shutil.which(resolved) or (resolved.startswith("/") and os.access(resolved, os.X_OK))):
        return CheckResult(name, INFO, info_message)

    return CheckResult(name, PASS, f"(使用 {resolved})")


def check_openclaw_daemon() -> CheckResult:
    """Check that openclaw's daemon is running (best-effort)."""
    name = "檢查 OpenClaw 運行狀態"
    oc_cmd = "openclaw"
    if not shutil.which(oc_cmd):
        nvm_dir = HOME() / ".nvm" / "versions" / "node"
        if nvm_dir.is_dir():
            for root, _dirs, files in os.walk(nvm_dir):
                if "openclaw" in files:
                    oc_cmd = str(Path(root) / "openclaw")
                    os.environ["PATH"] = str(Path(root)) + os.path.pathsep + os.environ.get("PATH", "")
                    break
    if not (shutil.which(oc_cmd) or (oc_cmd.startswith("/") and os.access(oc_cmd, os.X_OK))):
        return CheckResult(name, INFO, "(此環境未安裝 OpenClaw CLI，跳過檢查)")
    try:
        result = subprocess.run([oc_cmd, "daemon", "status"], capture_output=True, text=True, timeout=5)
        if "running" in result.stdout:
            return CheckResult(name, PASS, "(已啟動)")
        return CheckResult(name, WARN, "(OpenClaw 已安裝但未啟動)")
    except Exception:
        return CheckResult(name, WARN, "(無法偵測 OpenClaw 運行狀態)")


def check_mempalace_connectivity() -> CheckResult:
    """Probe mempalace-mcp via JSON-RPC initialize and measure response time."""
    name = "檢查 MemPalace MCP 連線能力"
    cmd = MEMPALACE_MCP_COMMAND()

    # If the resolved command is the uv shim that doesn't exist, bail early.
    exe = cmd[0]
    if not (shutil.which(exe) or (exe.startswith("/") and os.access(exe, os.X_OK))):
        return CheckResult(name, INFO, "(mempalace-mcp 未安裝，略過)")

    init_request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "ai-brain", "version": "1.0"},
        },
    }

    proc = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        start = time.monotonic()
        assert proc.stdin is not None
        proc.stdin.write((json.dumps(init_request) + "\n").encode("utf-8"))
        proc.stdin.flush()

        # Read one response line, waiting up to 30 seconds.
        assert proc.stdout is not None
        line = b""
        for _ in range(300):  # 300 * 0.1s = 30s
            ch = proc.stdout.read(1)
            if not ch:
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
                continue
            if ch == b"\n":
                break
            line += ch

        elapsed_ms = int((time.monotonic() - start) * 1000)

        if not line:
            return CheckResult(
                name, FAIL,
                f"(30 秒內無回應 ({elapsed_ms} ms)；建議執行 ai-brain gc --apply 或 ai-brain doctor --fix)",
            )

        return CheckResult(name, PASS, f"({elapsed_ms} ms)")

    except Exception as e:
        return CheckResult(
            name, FAIL,
            f"(連線失敗: {e}；建議執行 ai-brain gc --apply 或 ai-brain doctor --fix)",
        )
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()


# --- Pretty printer -------------------------------------------------------------

_STATUS_COLOR = {
    PASS: green,
    WARN: yellow,
    FAIL: red,
    INFO: lambda s: s,
}


def print_results(results: List[CheckResult]) -> int:
    """Print results with consistent [STATUS] formatting. Returns failure count."""
    failures = 0
    for i, r in enumerate(results, 1):
        color_fn = _STATUS_COLOR.get(r.status, lambda s: s)
        tag = color_fn(f"[ {r.status} ]")
        detail = f" {r.detail}" if r.detail else ""
        print(f"{i}. {r.name}... {tag}{detail}")
        if r.status == FAIL:
            failures += 1
    return failures


# --- Top-level verification pipeline -------------------------------------------

def run_all_checks(paths) -> List[CheckResult]:
    """Run the full 9-point health check and return all results."""
    results: List[CheckResult] = []

    # 1. mempalace CLI
    results.append(check_cli_available("MemPalace", "mempalace"))
    # 2. claude-mem CLI
    results.append(check_cli_available("claude-mem", "claude-mem",
                                       info_message="(未安裝，僅 Claude Code 需要)"))
    # 3. codebase-memory-mcp CLI
    results.append(check_cli_available("codebase-memory-mcp", "codebase-memory-mcp"))
    # 4. Claude Code ~/.claude.json
    claude_json = HOME() / ".claude.json"
    if not claude_json.is_file() and Path(".claude.json").is_file():
        claude_json = Path(".claude.json")
    results.append(check_mcp_config("Claude Code", claude_json))

    # 5. OpenClaw daemon
    results.append(check_openclaw_daemon())

    # 6. OpenCode ~/.config/opencode/opencode.json
    results.append(check_mcp_config("OpenCode", paths.opencode_json, server_key="mcp"))

    # Generic MCP ~/.mcp.json
    results.append(check_mcp_config("Generic MCP", paths.mcp_json))

    # 7. Gemini / Antigravity
    gemini_paths = [p for p in (paths.gemini_config, paths.gemini_antigravity) if p]
    if gemini_paths:
        first_fail = None
        for p in gemini_paths:
            r = check_mcp_config("Gemini / Antigravity IDE", p)
            if r.status != PASS:
                first_fail = r
                break
        results.append(first_fail or CheckResult("檢查 Gemini / Antigravity IDE MCP 記憶載入與內容正確性", PASS))
    else:
        results.append(CheckResult("檢查 Gemini / Antigravity IDE MCP 記憶載入與內容正確性", INFO,
                                   "(此環境未安裝 Gemini CLI，跳過檢查)"))

    # 8. Claude Desktop
    results.append(check_mcp_config("Claude Desktop App", paths.claude_desktop))

    # 9. Kilo
    kilo_paths: List[Path] = []
    if HOME().joinpath(".config", "kilo").is_dir():
        kilo_paths.append(HOME() / ".config" / "kilo" / "kilo.json")
    if paths.vscode_kilo and paths.vscode_kilo.parent.is_dir():
        kilo_paths.append(paths.vscode_kilo)
    if kilo_paths:
        first_fail = None
        for p in kilo_paths:
            # Kilo config has the same content but may nest under `mcp` instead of `mcpServers`.
            try:
                with open(p, encoding="utf-8") as f:
                    raw = json.load(f)
                server_key = "mcpServers" if "mcpServers" in raw else "mcp"
            except Exception:
                server_key = "mcpServers"
            r = check_mcp_config("Kilo", p, server_key=server_key)
            if r.status != PASS:
                first_fail = r
                break
        results.append(first_fail or CheckResult("檢查 Kilo MCP 記憶載入與內容正確性", PASS))
    else:
        results.append(CheckResult("檢查 Kilo MCP 記憶載入與內容正確性", INFO,
                                   "(此環境未安裝 Kilo，跳過檢查)"))

    # 10. Cursor
    results.append(check_mcp_config("Cursor", paths.cursor_json))

    # 11. Codex
    results.append(check_mcp_config("Codex", paths.codex_toml, server_key="mcp_servers"))

    # 12. OpenClaw MCP config (only check if openclaw CLI exists)
    _oc_cmd = "openclaw"
    _oc_found = shutil.which(_oc_cmd)
    if not _oc_found:
        _nvm_dir = HOME() / ".nvm" / "versions" / "node"
        if _nvm_dir.is_dir():
            for _root, _dirs, _files in os.walk(_nvm_dir):
                if "openclaw" in _files:
                    _oc_found = _root
                    break
    if _oc_found:
        results.append(check_mcp_config("OpenClaw", paths.openclaw_config))
    else:
        results.append(CheckResult("檢查 OpenClaw MCP 記憶載入與內容正確性", INFO,
                                   "(此環境未安裝 OpenClaw CLI，跳過檢查)"))

    return results
