"""Shell completion script generator for `ai-brain`.

This module produces static completion scripts for bash, zsh, and fish so the
user can press <TAB> to:

* complete subcommand names (`ai-brain <TAB>`)
* complete the `pattern` positional for `include` / `exclude` with the
  actual active project list (`ai-brain include <TAB>`)
* complete the static tokens `all`, `current`, `.`
* complete flags like `-h`, `--help`, `-v`, `--version`

Design constraint: the project ships with **zero third-party runtime deps**
(`pyproject.toml: dependencies = []`), so we deliberately avoid
`argcomplete` / `shtab`. Static scripts run entirely in the shell with
near-zero overhead, and only fall back to a tiny `__complete-patterns`
helper invocation to fetch the live active-project list.

Usage (after `ai-brain completions install`):

    ai-brain include <TAB>          # → 'all' 'current' '.' '1' '2' '3' 'api-server' ...
    ai-brain <TAB>                  # → init full-init install update ...

Run `python3 -m ai_brain.completions show bash` to print a single script.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List

# Reuse the single source of truth for subcommand names.
from .cli import COMMANDS


# --------------------------------------------------------------------------- #
# Static tokens accepted as the `pattern` positional for include/exclude.
# --------------------------------------------------------------------------- #
_PATTERN_TOKENS: List[str] = ["all", "current", "."]
# Flags accepted at the top level (no value, no subcommand).
_TOP_FLAGS: List[str] = ["-h", "--help", "-v", "--version"]


# --------------------------------------------------------------------------- #
# Subcommand enumeration
# --------------------------------------------------------------------------- #
def _visible_commands() -> List[str]:
    """Return user-visible subcommand names sorted alphabetically.

    The COMMANDS table in cli.py also contains version aliases (`-v`,
    `--version`) and internal entries that aren't real subcommands — we
    surface only the names a user would type after `ai-brain `.
    """
    hidden = {"-v", "--version"}
    return sorted(name for name in COMMANDS if name not in hidden)


# --------------------------------------------------------------------------- #
# Live project completion (called by the shell at <TAB> time)
# --------------------------------------------------------------------------- #
def complete_patterns() -> List[str]:
    """Return completion candidates for `include` / `exclude` positional.

    Always includes the static tokens first, then the 1-based numeric
    indices, then the basename of every active project so the user can
    type a fragment of a name and hit <TAB>.

    Imports are local to keep this module importable even when the
    registry side-effects (Path.home stubbing, etc.) are not in play.
    """
    candidates: List[str] = list(_PATTERN_TOKENS)
    try:
        from . import registry  # local import: see docstring
        active = registry.list_active()
    except Exception:
        # If the registry can't be read (no home dir, permission error,
        # etc.) we still return the static tokens so completion doesn't
        # silently break.
        return candidates

    # 1-based indices — matches what the status list prints.
    candidates.extend(str(i) for i in range(1, len(active) + 1))
    # Project basenames (last path segment) — short, memorable, and
    # matches what `ai-brain exclude` (no args) shows.
    seen: set = set()
    for proj in active:
        base = Path(proj).name
        if base and base not in seen:
            candidates.append(base)
            seen.add(base)
    return candidates


# --------------------------------------------------------------------------- #
# Script generators
# --------------------------------------------------------------------------- #
def bash_script() -> str:
    """Generate a bash completion script.

    Uses the standard `complete -F` mechanism; the function reads
    `${COMP_WORDS[1]}` to decide whether the cursor is on the subcommand
    position or the pattern position.
    """
    cmds = " ".join(_visible_commands())
    return f"""# bash completion for ai-brain — generated, do not edit by hand.
# Source via:  source <(ai-brain completions show bash)
_ai_brain() {{
    local cur prev words cword
    if declare -F _init_completion >/dev/null 2>&1; then
        _init_completion || return
    else
        # Fallback for systems without bash-completion installed.
        COMPREPLY=()
        cur="${{COMP_WORDS[COMP_CWORD]}}"
        prev="${{COMP_WORDS[COMP_CWORD-1]}}"
        words=("${{COMP_WORDS[@]}}")
        cword=$COMP_CWORD
    fi

    local subcommands="{cmds}"
    local top_flags="-h --help -v --version"

    # First word after `ai-brain` → subcommand completion.
    if [[ $cword -eq 1 ]]; then
        if [[ $cur == -* ]]; then
            COMPREPLY=( $(compgen -W "$top_flags" -- "$cur") )
        else
            COMPREPLY=( $(compgen -W "$subcommands $top_flags" -- "$cur") )
        fi
        return 0
    fi

    local subcmd="${{words[1]}}"

    # include / exclude take a single positional `pattern`.
    if [[ $subcmd == "include" || $subcmd == "exclude" ]] && [[ $cword -eq 2 ]]; then
        # Ask the Python tool itself for live candidates.
        local patterns
        patterns=$(ai-brain completions __complete-patterns 2>/dev/null)
        COMPREPLY=( $(compgen -W "$patterns" -- "$cur") )
        return 0
    fi

    # All other subcommands take no positionals — only -h/--help if at all.
    if [[ $cur == -* ]]; then
        COMPREPLY=( $(compgen -W "-h --help" -- "$cur") )
    fi
    return 0
}}
complete -F _ai_brain ai-brain
"""


def zsh_script() -> str:
    """Generate a zsh completion script using `#compdef ai-brain`."""
    cmds = " ".join(_visible_commands())
    return f"""#compdef ai-brain
# zsh completion for ai-brain — generated, do not edit by hand.
# Place in any directory in $fpath, or source via:  source <(ai-brain completions show zsh)

_ai_brain() {{
    local -a subcommands
    subcommands=({cmds})

    local -a top_flags
    top_flags=(-h --help -v --version)

    _arguments -C \\
        '1: :->cmd' \\
        '*::arg:->args'

    case $state in
        cmd)
            if [[ $words[CURRENT] == -* ]]; then
                compadd -- $top_flags
            else
                compadd -- $subcommands $top_flags
            fi
            ;;
        args)
            local subcmd=$words[2]
            case $subcmd in
                include|exclude)
                    local -a patterns
                    local line
                    while IFS= read -r line; do
                        patterns+=("$line")
                    done < <(ai-brain completions __complete-patterns 2>/dev/null)
                    compadd -- $patterns
                    ;;
                *)
                    # Other subcommands: only -h/--help.
                    _arguments '--help[show help]'
                    ;;
            esac
            ;;
    esac
}}

_ai_brain "$@"
"""


def fish_script() -> str:
    """Generate a fish completion script."""
    # Fish uses a line-per-completion format. We emit a complete file that
    # uses `ai-brain completions __complete-patterns` for the dynamic part.
    lines = [
        "# fish completion for ai-brain — generated, do not edit by hand.",
        "# Place in ~/.config/fish/completions/ or source via:",
        "#   ai-brain completions show fish | source",
        "",
        "function __ai_brain_patterns",
        "    ai-brain completions __complete-patterns 2>/dev/null",
        "end",
        "",
        # Disable file completion for our subcommands.
        "complete -c ai-brain -f",
    ]
    # Subcommand list.
    lines.append(
        "complete -c ai-brain -n '__fish_use_subcommand' -a '{}'".format(
            " ".join(_visible_commands())
        )
    )
    # Top-level flags.
    for flag, desc in [
        ("-h", "show help"),
        ("--help", "show help"),
        ("-v", "show version"),
        ("--version", "show version"),
    ]:
        lines.append(f"complete -c ai-brain -n '__fish_use_subcommand' -l '{flag.lstrip('-')}' -d '{desc}' 2>/dev/null")
        lines.append(f"complete -c ai-brain -n '__fish_use_subcommand' -s '{flag.lstrip('-')}' -d '{desc}'")
    # Pattern completion for include/exclude.
    for cmd in ("include", "exclude"):
        lines.append(
            f"complete -c ai-brain -n '__fish_seen_subcommand_from {cmd}' "
            f"-f -a '(__ai_brain_patterns)'"
        )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #
def render(shell: str) -> str:
    """Return the completion script for the requested shell."""
    shell = shell.lower()
    if shell == "bash":
        return bash_script()
    if shell == "zsh":
        return zsh_script()
    if shell == "fish":
        return fish_script()
    raise ValueError(f"unsupported shell: {shell!r} (expected: bash, zsh, fish)")


# --------------------------------------------------------------------------- #
# Installation targets
# --------------------------------------------------------------------------- #
def _install_targets() -> dict:
    """Map shell → (file path, executable mode?) for the user's home."""
    home = Path.home()
    return {
        "bash": (home / ".local" / "share" / "bash-completion" / "completions" / "ai-brain", False),
        "zsh":  (home / ".local" / "share" / "zsh" / "site-functions" / "_ai-brain", False),
        "fish": (home / ".config" / "fish" / "completions" / "ai-brain.fish", False),
    }


def install(shell: str | None = None) -> List[Path]:
    """Write completion scripts to the user's standard locations.

    Returns the list of files written. If `shell` is None, all supported
    shells are installed.
    """
    targets = _install_targets()
    shells = [shell] if shell else list(targets)
    written: List[Path] = []
    for sh in shells:
        if sh not in targets:
            raise ValueError(f"unsupported shell: {sh!r}")
        path, _ = targets[sh]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(sh), encoding="utf-8")
        written.append(path)
    return written


def uninstall(shell: str | None = None) -> List[Path]:
    """Remove previously installed completion files. Missing files are OK."""
    targets = _install_targets()
    shells = [shell] if shell else list(targets)
    removed: List[Path] = []
    for sh in shells:
        if sh not in targets:
            continue
        path, _ = targets[sh]
        if path.exists():
            path.unlink()
            removed.append(path)
    return removed


# --------------------------------------------------------------------------- #
# CLI entrypoint (called from cli.py: `ai-brain completions ...`)
# --------------------------------------------------------------------------- #
def main(argv: List[str] | None = None) -> int:
    """Handle `ai-brain completions <action> [shell]`.

    Actions:
        show <shell>    — print the script to stdout
        install [shell] — write to the user's standard location
        uninstall [shell] — remove the file
        __complete-patterns — internal: print pattern candidates (one per line)
    """
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: ai-brain completions {show,install,uninstall} [bash|zsh|fish]")
        return 0

    action = argv[0]
    shell = argv[1] if len(argv) > 1 else None

    if action == "__complete-patterns":
        # Internal: invoked by the completion script itself.
        for cand in complete_patterns():
            print(cand)
        return 0

    if action == "show":
        if not shell:
            print("error: `show` requires a shell (bash, zsh, or fish)", file=sys.stderr)
            return 2
        sys.stdout.write(render(shell))
        return 0

    if action == "install":
        try:
            written = install(shell)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        for p in written:
            print(f"installed: {p}")
        if not shell:
            print()
            print("Reload your shell (or `source` the file) for completions to activate.")
        return 0

    if action == "uninstall":
        removed = uninstall(shell)
        for p in removed:
            print(f"removed: {p}")
        if not removed:
            print("(nothing to remove)")
        return 0

    print(f"error: unknown action {action!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
