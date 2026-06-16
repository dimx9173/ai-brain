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
        try:
            subprocess.run(["git", "-C", str(repo_dir), "pull", "origin", "master"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

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

