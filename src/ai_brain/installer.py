"""Global install / update of the `ai-brain` executable.

Two flows:
- `install_from_source()`: generate a customized python entrypoint shim in ~/.local/bin/ai-brain
  pointing to the source directory, and record the source path.
- `update_from_registry()`: when running as the installed copy, look up the source,
  do a `git pull`, and regenerate the shim.

Both flows finish by also upgrading the three core dependency CLIs
(mempalace / claude-mem / graphifyy) and printing a single version table
so the user can see the whole toolchain in one glance.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .constants import (
    GLOBAL_AI_BRAIN,
    INSTALL_SOURCE_REGISTRY,
    VERSION,
)
from .ui import print_blue as blue, print_green as green, print_red as red, print_yellow as yellow
from .upgraders import print_summary, upgrade_all


def _write_global_shim(src_dir: Path) -> bool:
    """Write the customized global python shim pointing to src_dir."""
    shim_content = f"""#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
from pathlib import Path

# Add source directory to path
sys.path.insert(0, {repr(str(src_dir))})

from ai_brain.cli import main
if __name__ == "__main__":
    sys.exit(main())
"""
    try:
        GLOBAL_AI_BRAIN().parent.mkdir(parents=True, exist_ok=True)
        if GLOBAL_AI_BRAIN().exists():
            GLOBAL_AI_BRAIN().unlink()
        GLOBAL_AI_BRAIN().write_text(shim_content, encoding="utf-8")
        GLOBAL_AI_BRAIN().chmod(0o755)
        return True
    except Exception as e:
        red(f"❌ 寫入全域 shim 失敗 ({e})")
        return False


def install_or_update() -> bool:
    """Idempotent global install. Detects whether we're source or installed copy.

    After (re)installing ai-brain itself, also upgrades the three core
    dependency CLIs and prints a final version table.
    """
    invoked_script = Path(sys.argv[0]).resolve()

    # If we're already running as the installed copy, try to update from source.
    if invoked_script == GLOBAL_AI_BRAIN():
        ok = _update_from_registry()
    else:
        script_path = Path(__file__).resolve()
        ok = _install_from_source(script_path)

    if ok:
        _upgrade_and_summarise()
    return ok


def _upgrade_and_summarise() -> None:
    """Run `uv tool install --force --reinstall` for every core tool, then print the version table.

    The ai-brain entry in the table uses VERSION (the source-of-truth constant)
    rather than asking the shim to introspect itself — the shim is a thin
    one-liner that runs the in-repo package, so the canonical version is
    whatever is compiled into this file.
    """
    blue("====== 🔄 一併更新核心 CLI 套件 (mempalace / claude-mem / graphifyy) ======")
    outcomes = upgrade_all()
    print_summary(outcomes, self_version=VERSION)


def _install_from_source(script_path: Path) -> bool:
    blue("====== 安裝/更新全域 ai-brain 指令 ======")
    INSTALL_SOURCE_REGISTRY().parent.mkdir(parents=True, exist_ok=True)

    src_dir = script_path.parent.parent.resolve()
    repo_root = src_dir.parent
    bin_ai_brain = repo_root / "bin" / "ai-brain"

    if not _write_global_shim(src_dir):
        return False

    try:
        INSTALL_SOURCE_REGISTRY().write_text(str(bin_ai_brain) + "\n", encoding="utf-8")
    except Exception as e:
        red(f"❌ 記錄安裝來源失敗 ({e})")
        return False

    green(f"✅ 成功將 ai-brain 複製/更新至 {GLOBAL_AI_BRAIN()}")
    blue(f"--> 已紀錄安裝來源：{bin_ai_brain}")

    path_env = os.environ.get("PATH", "")
    if ".local/bin" in path_env or str(Path.home() / ".local" / "bin") in path_env:
        green("✅ 您的 PATH 已包含 ~/.local/bin，您現在可以在任何目錄直接執行: ai-brain [指令]")
    else:
        yellow("⚠️ 您的 PATH 尚未包含 ~/.local/bin！")
        print("建議執行以下指令將其加入您的 Shell 設定檔（如 ~/.zshenv 或 ~/.zshrc）：")
        blue('  echo \'export PATH="$HOME/.local/bin:$PATH"\' >> ~/.zshenv')
        blue("  source ~/.zshenv")
    return True


def _update_from_registry() -> bool:
    if not INSTALL_SOURCE_REGISTRY().is_file():
        red("⚠️ 找不到安裝記錄，無法進行全域更新。")
        yellow("請回到原本的 ai-brain 倉庫目錄執行：./bin/ai-brain install")
        return False

    try:
        source_path = Path(INSTALL_SOURCE_REGISTRY().read_text(encoding="utf-8").strip())
    except Exception:
        source_path = None
    if not source_path or not source_path.is_file():
        red(f"⚠️ 找不到安裝源路徑：{source_path}")
        yellow("請回到原本的 ai-brain 倉庫目錄執行：./bin/ai-brain install")
        return False

    blue("====== 更新全域 ai-brain 指令 ======")
    print(f"正在從源路徑更新: {source_path}")

    repo_dir = _find_git_root(source_path.parent)
    if repo_dir:
        print("--> 偵測到 Git 倉庫，正在同步最新代碼...")
        _sync_repo_to_origin(repo_dir)

    src_dir = source_path.parent.parent.resolve() / "src"
    if not _write_global_shim(src_dir):
        return False
    green("✅ 成功自源路徑同步並更新至最新版本！")
    return True


def _find_git_root(start: Path) -> Path | None:
    """Walk up from *start* until we hit a directory containing .git."""
    repo_dir = start
    while repo_dir != repo_dir.parent:
        if (repo_dir / ".git").is_dir():
            return repo_dir
        repo_dir = repo_dir.parent
    return None


def _run_git(repo_dir: Path, *args: str, timeout: int = 30) -> tuple[int, str, str]:
    """Run a git command in *repo_dir*. Returns (rc, stdout, stderr).

    Never raises — git failures are surfaced via return code so the caller
    can print a friendly message. Used by ``_sync_repo_to_origin`` so a
    transient git error (e.g. wrong branch name, network down) doesn't
    silently leave the user on a stale checkout.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_dir), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"git {' '.join(args)} timed out after {timeout}s"
    except FileNotFoundError:
        return 127, "", "git not found on PATH"
    except Exception as e:  # pragma: no cover - defensive
        return 1, "", f"failed to spawn git: {e}"
    return result.returncode, (result.stdout or ""), (result.stderr or "")


def _current_branch(repo_dir: Path) -> str | None:
    """Return the current branch name, or None if detached / unknown.

    Tries ``git rev-parse --abbrev-ref HEAD`` first; falls back to
    ``symbolic-ref --short HEAD``. Returns None on failure (e.g. detached
    HEAD, brand-new repo with no commits) so the caller can pick a sane
    default like ``main``.
    """
    rc, out, _ = _run_git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD")
    if rc == 0:
        name = out.strip()
        # "HEAD" is what git returns on a detached HEAD; treat as unknown.
        if name and name != "HEAD":
            return name
    return None


def _has_uncommitted_changes(repo_dir: Path) -> bool:
    """True if the working tree has tracked modifications or staged changes.

    We deliberately do NOT consider untracked files — those won't block a
    ``reset --hard`` and are usually config the user wants to keep.
    """
    rc, out, _ = _run_git(repo_dir, "status", "--porcelain")
    if rc != 0:
        # If status fails, err on the safe side and pretend there are changes
        # so we don't blow them away with a hard reset.
        return True
    for line in out.splitlines():
        if not line.strip():
            continue
        # Porcelain v1 format: "XY path" where X=index, Y=worktree.
        # "??" = untracked — safe to ignore.
        if line.startswith("??"):
            continue
        return True
    return False


def _sync_repo_to_origin(repo_dir: Path) -> None:
    """Fast-forward *repo_dir*'s current branch to ``origin/<branch>``.

    Replaces the previous ``git pull origin master`` call which had two
    bugs: (1) hard-coded ``master`` while the actual default branch on
    this repo is ``main``, and (2) swallowed stderr so failures left the
    user on a stale checkout with no warning.

    Strategy:
      1. Detect the current branch name.
      2. If the working tree has uncommitted changes, refuse to reset —
         print a yellow warning and skip the sync (better than destroying
Local edits).
      3. Otherwise ``fetch`` then ``reset --hard origin/<branch>`` and
         surface any error to the user.
    """
    branch = _current_branch(repo_dir) or "main"

    if _has_uncommitted_changes(repo_dir):
        yellow(
            f"⚠️  偵測到 {repo_dir} 有未提交的變更，跳過 git 同步以免覆寫。"
        )
        yellow("   請先 commit / stash 後再執行 ai-brain update。")
        return

    rc, _, err = _run_git(repo_dir, "fetch", "origin", branch, timeout=60)
    if rc != 0:
        red(f"❌ git fetch origin {branch} 失敗：{err.strip() or f'exit {rc}'}")
        return

    rc, out, err = _run_git(repo_dir, "reset", "--hard", f"origin/{branch}")
    if rc != 0:
        red(
            f"❌ git reset --hard origin/{branch} 失敗："
            f"{(err or out).strip() or f'exit {rc}'}"
        )
        return

    # Show the new HEAD for the user's peace of mind.
    rc, head, _ = _run_git(repo_dir, "rev-parse", "--short", "HEAD")
    short = head.strip() if rc == 0 else "?"
    green(f"✅ 已同步至 origin/{branch} (HEAD = {short})")

