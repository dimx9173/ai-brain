"""Bulk upgrade of the `ai-brain` dependency CLIs (mempalace, claude-mem, graphifyy).

These are the tools that *ai-brain orchestrates* — they live in `~/.local/bin`
as `uv tool` installs. Keeping them current is part of `ai-brain update`
because the installer already does its own `git pull` + resync, so doing all
four at once is the natural "update everything" experience.

Design notes
------------
- Package names (the `uv tool install <name>` names) and the binary names
  they expose on $PATH are *different* (e.g. PyPI package `graphifyy`
  installs a `graphify` binary). Keep both explicit.
- `get_version(cmd)` tries `<cmd> --version` first (Linux/POSIX convention),
  then `<cmd> version` as a fallback. Returns the first line of stdout
  stripped, or "(unknown)" if neither flag works.
- `upgrade(pkg, binary)` runs `uv tool install <pkg> --force --reinstall`
  and returns (ok, message). It never raises — installation failures are
  surfaced as `(False, "...")` so the caller can show a friendly summary.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from importlib import metadata as importlib_metadata

from .ui import print_red as red


# --- Package registry -----------------------------------------------------------
# Maps a *binary* (what users call on $PATH) to the *PyPI package name*
# that `uv tool install` should fetch. Both sides are needed because:
# - `mempalace` and `mempalace-mcp` both come from the `mempalace` package.
# - The `graphifyy` package installs a `graphify` binary (double-y on PyPI).
@dataclass(frozen=True)
class UpgradableTool:
    label: str            # human-readable, for log lines
    binary: str           # the on-PATH command name
    package: str          # the `uv tool install` package name


# The canonical list — keep this short. We do not auto-discover; the user
# picked "不列其他工具" (no OpenClaw / GitHub Action etc.) in the design Q&A.
CORE_TOOLS: tuple[UpgradableTool, ...] = (
    UpgradableTool("MemPalace", "mempalace", "mempalace"),
    UpgradableTool("claude-mem", "claude-mem", "claude-mem"),
    UpgradableTool("codebase-memory-mcp", "codebase-memory-mcp", "codebase-memory-mcp"),
)


# --- Version parsing ------------------------------------------------------------

# Some tools print "name 1.2.3", "name v1.2.3", "v1.2.3", or "1.2.3".
# We pull the first version-looking token (x.y.z[-prerelease]).
_VERSION_RE = re.compile(
    r"v?\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9.\-]+)?"
)


def _version_from_metadata(packages: tuple[str, ...]) -> str | None:
    """Try to read the version of any of *packages* from importlib.metadata.

    This is the strongest signal because it doesn't depend on the CLI
    implementing `--version` — which several tools (e.g. `claude-mem`)
    don't. We pass a tuple so the same tool row can fall back to either
    its binary name (some tools' entry-point name == binary) or its
    distribution name.

    PyPI distribution names are normalised (dashes → underscores, lowercased),
    so we try both the raw and the normalised form. We also fall back to
    scanning the uv tool's `site-packages` for any `.dist-info` whose name
    starts with the package name — this catches cases like
    binary=`claude-mem` vs distribution=`claude_mem` without forcing the
    caller to know the precise name.
    """
    # 1. Direct lookup against the candidate names.
    for pkg in packages:
        base_pkg = pkg.split("[")[0]
        for candidate in {base_pkg, base_pkg.lower().replace("-", "_")}:
            try:
                return importlib_metadata.version(candidate)
            except importlib_metadata.PackageNotFoundError:
                continue
            except Exception:
                continue

    # 2. Scan installed distributions. Some tools (e.g. claude-mem) ship
    # under a distribution name that differs from the binary; importlib
    # can't disambiguate without help, so we look for any installed dist
    # whose normalised name *starts with* the binary's normalised name.
    try:
        base_pkg0 = packages[0].split("[")[0]
        target = base_pkg0.lower().replace("-", "_").replace("_", "")
        for dist in importlib_metadata.distributions():
            normalised = dist.metadata["Name"].lower().replace("-", "").replace("_", "")
            if normalised.startswith(target) or target.startswith(normalised):
                return dist.version
    except Exception:
        pass
    return None


# Cache the output of `uv tool list` for the duration of one `ai-brain update`
# call, so we don't shell out 3 times for the same data.
_UV_TOOL_LIST_CACHE: list[str] | None = None


def _uv_tool_list() -> list[str] | None:
    """Return the lines of `uv tool list`, or None if uv is missing / errors out."""
    global _UV_TOOL_LIST_CACHE
    if _UV_TOOL_LIST_CACHE is not None:
        return _UV_TOOL_LIST_CACHE
    if not shutil.which("uv"):
        return None
    try:
        result = subprocess.run(
            ["uv", "tool", "list"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    _UV_TOOL_LIST_CACHE = result.stdout.splitlines()
    return _UV_TOOL_LIST_CACHE


def _version_from_uv_list(binary: str, package: str) -> str | None:
    """Parse `uv tool list` for the version of *package* (or *binary* as fallback).

    `uv tool list` output looks like::
        mempalace v3.4.1
        - mempalace
        - mempalace-mcp
        claude-mem v1.0.3
        - claude-mem
        ...
    The first line for each tool is "<name> v<version>".
    """
    lines = _uv_tool_list()
    if not lines:
        return None
    # Normalise — uv prints the distribution name, which may differ from
    # the binary name (e.g. graphifyy vs graphify).
    base_pkg = package.split("[")[0]
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("-"):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        name, version = parts[0], parts[1]
        if name in (base_pkg, base_pkg.lower(), binary, binary.lower()):
            return version
    return None


def get_version(binary: str, *, package: str | None = None) -> str:
    """Return the version string for *binary*, or "(unknown)" if unparseable.

    Resolution order:
    1. `<binary> --version` (POSIX convention)
    2. `<binary> version` (ai-brain's own subcommand style)
    3. importlib.metadata — read either the *package* (preferred) or the
       *binary*'s distribution. This catches tools that don't bother
       implementing --version (e.g. claude-mem).
    """
    if not shutil.which(binary):
        return "(not installed)"

    # --- Step 1 & 2: ask the binary itself ---
    for argv in (("--version",), ("version",)):
        try:
            result = subprocess.run(
                [binary, *argv],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue
        # Some tools print version on stderr (e.g. Python's --version).
        raw = (result.stdout or "") + (result.stderr or "")
        if not raw.strip():
            continue
        match = _VERSION_RE.search(raw)
        if match:
            return match.group(0)
        first_line = raw.strip().splitlines()[0]
        # If the first line didn't look like a version (e.g. argparse usage
        # text), keep trying — don't return garbage as "the version".
        if any(t in first_line.lower() for t in ("usage:", "error:", "unknown")):
            continue
        return first_line

    # --- Step 3: ask the Python package metadata ---
    metadata = _version_from_metadata((package,) if package else (binary,))
    if metadata:
        return metadata

    # --- Step 4: ask uv directly. The current Python's importlib won't
    # see tools that were installed into their own uv-managed venv, but
    # `uv tool list` knows about every installed tool. ---
    if package:
        uv_ver = _version_from_uv_list(binary, package)
        if uv_ver:
            return uv_ver

    return "(unknown)"


# --- Upgrade one tool -----------------------------------------------------------

def upgrade(tool: UpgradableTool) -> tuple[bool, str]:
    """Run `uv tool install <pkg> --force --reinstall` for *tool*.

    Returns (success, message). Never raises.
    """
    if not shutil.which("uv"):
        return False, "uv not on PATH — install it from https://docs.astral.sh/uv/"
    try:
        result = subprocess.run(
            ["uv", "tool", "install", tool.package, "--force", "--reinstall"],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after 5 min while installing {tool.package}"
    except Exception as e:
        return False, f"failed to spawn uv: {e}"

    if result.returncode == 0:
        return True, f"upgraded to {get_version(tool.binary, package=tool.package)}"
    # uv writes errors to stderr; surface a one-line digest.
    err = (result.stderr or result.stdout or "").strip().splitlines()
    tail = err[-1] if err else f"exit code {result.returncode}"
    return False, tail


# --- Bulk driver ----------------------------------------------------------------

@dataclass
class UpgradeOutcome:
    tool: UpgradableTool
    upgraded: bool
    message: str
    version_after: str


def upgrade_all() -> list[UpgradeOutcome]:
    """Upgrade every tool in CORE_TOOLS, returning one outcome per tool.

    Used by `ai-brain update`. We deliberately iterate *all* tools even if
    one fails — the user wants to see the full picture, not bail on the
    first error.
    """
    outcomes: list[UpgradeOutcome] = []
    for tool in CORE_TOOLS:
        if not shutil.which(tool.binary):
            # Don't try to upgrade a binary that isn't there — uv will just
            # do a fresh install, but the user already opted in, so let it.
            pass
        ok, msg = upgrade(tool)
        outcomes.append(UpgradeOutcome(
            tool=tool,
            upgraded=ok,
            message=msg,
            version_after=get_version(tool.binary, package=tool.package),
        ))
    return outcomes


# --- Pretty summary -------------------------------------------------------------

def print_summary(outcomes: list[UpgradeOutcome], self_version: str) -> None:
    """Print the post-upgrade version table.

    Columns: name, version (after), result.

    Imported lazily by installer.update() so we don't have a circular import
    with `ui`. Pure stdout — no exceptions raised.
    """
    from .ui import print_blue as blue
    from .ui import print_green as green

    print()
    blue("====== 📦 安裝完成！以下是所有套件目前版本 ======")
    rows: list[tuple[str, str, str]] = [
        ("ai-brain", self_version, "(self)"),
        *((o.tool.label, o.version_after, "✅ upgraded" if o.upgraded else f"❌ {o.message}")
          for o in outcomes),
    ]
    # Pick column widths from the data so the table stays tidy.
    name_w = max(len(r[0]) for r in rows)
    ver_w = max(len(r[1]) for r in rows)
    print(f"  {'Component'.ljust(name_w)}  {'Version'.ljust(ver_w)}  Status")
    print(f"  {'-' * name_w}  {'-' * ver_w}  ------")
    for name, ver, status in rows:
        line = f"  {name.ljust(name_w)}  {ver.ljust(ver_w)}  {status}"
        if status.startswith("✅") or status == "(self)":
            green(line)
        elif status.startswith("❌"):
            red(line)
        else:
            print(line)
    print()
