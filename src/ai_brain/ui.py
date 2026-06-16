"""Terminal color output helpers.

Two flavors of helper:
- `colored(s)` / `green(s)` / `blue(s)` / `yellow(s)` / `red(s)` return a
  *string* (so they can be embedded in f-strings or composed with `.format`).
- `print_blue(s)` etc. print directly (so callers don't need `print(green(s))`).

All output is silent (just empty strings) when stdout is not a TTY, so the
tool behaves cleanly when piped to files or cron.
"""
from __future__ import annotations

import sys


def _init_colors() -> tuple[str, str, str, str, str]:
    """Return color codes, or empty strings if stdout is not a TTY."""
    if sys.stdout.isatty():
        return (
            "\033[0;32m",  # green
            "\033[0;34m",  # blue
            "\033[1;33m",  # yellow
            "\033[0;31m",  # red
            "\033[0m",  # reset
        )
    return ("", "", "", "", "")


GREEN, BLUE, YELLOW, RED, NC = _init_colors()


# --- Returning helpers (compose with f-strings) --------------------------------
def colored(text: str, color: str) -> str:
    return f"{color}{text}{NC}"


def green(text: str) -> str:
    return f"{GREEN}{text}{NC}"


def blue(text: str) -> str:
    return f"{BLUE}{text}{NC}"


def yellow(text: str) -> str:
    return f"{YELLOW}{text}{NC}"


def red(text: str) -> str:
    return f"{RED}{text}{NC}"


# --- Side-effect helpers (print + color) ---------------------------------------
def print_blue(text: str) -> None:
    print(blue(text))


def print_green(text: str) -> None:
    print(green(text))


def print_yellow(text: str) -> None:
    print(yellow(text))


def print_red(text: str) -> None:
    print(red(text))
