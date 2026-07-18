"""End-to-end tests for the ai-brain CLI.

These tests invoke the *real* CLI (``python -m ai_brain.cli <args>``) as a
subprocess against an isolated ``$HOME`` and a ``$PATH`` whose first entries
are fake shim binaries. The goal is to validate the wired-together behaviour
(argparse → dispatch → commands → registry → filesystem) that the heavily
mocked unit tests in ``test_commands.py`` / ``test_cli.py`` cannot catch.

Scope (kept intentionally small per the Simplicity-First rule):

  - CLI dispatch: ``version``, ``-v``, ``--version``, no-arg help
  - Auto-archive whitelist CRUD: ``list``, ``include``, ``exclude``,
    ``include-all``, ``exclude-all``, ``remove``
  - ``stop`` → ``_run_archive_sweep`` dirty-skip behaviour (new feature)

Out of scope (covered by existing mock-heavy unit tests; not E2E-tested
here because each requires shimming a different heavy external service):
``init``, ``start``, ``status``, ``doctor``, ``gc``, ``mine``, ``mcp-sync``,
``install`` / ``update``, ``uninstall``, ``clean``, ``stop-cron``,
``config``, ``completions``, ``verify``.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path


# --------------------------------------------------------------------------- #
# Test fixture: isolated HOME + fake shim bin + subprocess CLI runner         #
# --------------------------------------------------------------------------- #

class _IsolatedHome(unittest.TestCase):
    """Per-test isolated HOME with optional pre-seeded state files.

    Subclasses set the class-level ``seed`` dict to pre-populate files under
    ``$HOME/.claude/`` before the CLI runs (e.g. ``{"ai_brain_active_projects.txt":
    "/proj-a\n"}``). For tests that exercise the sweep, also seed
    ``last_sweep_timestamp`` and ``projects/<key>/*.jsonl`` files.
    """

    seed: dict[str, str] = {}  # path under $HOME/.claude → content
    extra_env: dict[str, str] = {}

    def setUp(self) -> None:
        self._orig_home = os.environ.get("HOME")
        self._orig_path = os.environ.get("PATH")

        # --- Isolated HOME ---------------------------------------------------
        # On macOS, /var/folders is a symlink to /private/var/folders. Resolving
        # at setup time means the absolute paths we seed (and that subprocesses
        # see via Path.cwd().resolve()) are all in the same canonical form.
        self.home = Path(tempfile.mkdtemp(prefix="ai-brain-e2e-home-")).resolve()
        (self.home / ".claude").mkdir(parents=True)
        for rel, content in self.seed.items():
            target = self.home / ".claude" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        os.environ["HOME"] = str(self.home)

        # --- Fake shim bin (PATH-prepended) ----------------------------------
        self.shim_bin = Path(tempfile.mkdtemp(prefix="ai-brain-e2e-bin-"))
        self._install_shims()
        os.environ["PATH"] = f"{self.shim_bin}:{self._orig_path or ''}"

        for k, v in self.extra_env.items():
            os.environ[k] = v

    def tearDown(self) -> None:
        if self._orig_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._orig_home
        if self._orig_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = self._orig_path
        for k in self.extra_env:
            os.environ.pop(k, None)
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.shim_bin, ignore_errors=True)

    # -- Helpers -------------------------------------------------------------

    def cli(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        """Run the real CLI as a subprocess and capture output."""
        return subprocess.run(
            [sys.executable, "-m", "ai_brain.cli", *args],
            cwd=str(cwd or self.home),
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=30,
        )

    def claude_file(self, rel: str) -> Path:
        return self.home / ".claude" / rel

    def write_jsonl(self, project_key: str, name: str = "session.jsonl",
                    content: str = "{}", mtime: float | None = None) -> Path:
        d = self.claude_file("projects") / project_key
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        p.write_text(content, encoding="utf-8")
        if mtime is not None:
            os.utime(p, (mtime, mtime))
        return p

    # -- Shim installers (overridden by subclasses as needed) ---------------

    def _install_shims(self) -> None:
        """Default shims: mempalace + codebase-memory-mcp + git + crontab.

        Overrides can call ``self._install_shims()`` then add their own.
        """
        self._shim_mempalace()
        self._shim_codebase_memory()
        self._shim_git()
        self._shim_crontab()

    def _shim_script(self, name: str, body: str) -> None:
        path = self.shim_bin / name
        # Shebang is mandatory: PATH lookup uses ``execve``, which requires
        # the file to be either a real binary or a script starting with `#!`.
        path.write_text("#!/usr/bin/env python3\n"
                        + textwrap.dedent(body).lstrip("\n") + "\n",
                        encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    def _shim_mempalace(self) -> None:
        # Log every invocation to $MEMPALACE_CALL_LOG so tests can assert
        # whether sweep was actually called.
        self._shim_script("mempalace", f"""
            import json, os, sys
            log = os.environ.get("MEMPALACE_CALL_LOG", "")
            cmd = sys.argv[1] if len(sys.argv) > 1 else ""
            args = sys.argv[2:]
            if log:
                with open(log, "a", encoding="utf-8") as f:
                    f.write(json.dumps({{"cmd": cmd, "args": args}}) + "\\n")
            # Mimic the real CLI surface enough to not crash downstream.
            if cmd == "sweep":
                print("{{\\"swept\\": true}}")
                sys.exit(0)
            if cmd == "list_projects":
                print(json.dumps({{"projects": []}}))
                sys.exit(0)
            # Unknown subcommand: print empty JSON and succeed.
            print("{{}}")
        """)

    def _shim_codebase_memory(self) -> None:
        self._shim_script("codebase-memory-mcp", """
            import json, sys
            # Always return an empty project list with valid JSON so status/list
            # branches that call list_projects don't crash.
            if len(sys.argv) >= 3 and sys.argv[1] == "cli":
                if sys.argv[2] == "list_projects":
                    print(json.dumps({"projects": []}))
                    sys.exit(0)
                if sys.argv[2] == "index_repository":
                    print("ok")
                    sys.exit(0)
            print("{}")
        """)

    def _shim_git(self) -> None:
        # Minimal git stub: ``status --porcelain`` returns clean.
        self._shim_script("git", """
            import sys
            # Pretend every repo is clean: ``status --porcelain`` → no output.
            if sys.argv[1:3] == ["status", "--porcelain"]:
                sys.exit(0)
            # branch lookup
            if "--abbrev-ref" in sys.argv:
                print("main")
                sys.exit(0)
            sys.exit(0)
        """)

    def _shim_crontab(self) -> None:
        # Empty crontab; reading and writing both no-ops. Returning 0 keeps
        # ai_brain.cron happy.
        self._shim_script("crontab", """
            import sys
            if sys.argv[1] == "-l":
                # No current crontab.
                sys.exit(1)
            # ``crontab -`` writes from stdin; just exit 0.
            sys.exit(0)
        """)


# --------------------------------------------------------------------------- #
# CLI dispatch                                                                #
# --------------------------------------------------------------------------- #

class TestCliDispatch(_IsolatedHome):
    def test_no_args_shows_help_and_returns_zero(self) -> None:
        r = self.cli()
        self.assertEqual(r.returncode, 0)
        self.assertIn("用法", r.stdout)
        self.assertIn("ai-brain", r.stdout)

    def test_version_flag(self) -> None:
        r = self.cli("--version")
        self.assertEqual(r.returncode, 0)
        self.assertIn("v", r.stdout)
        # The version constant is "2.6.9".
        self.assertIn("2.6.9", r.stdout)

    def test_short_version_flag(self) -> None:
        r = self.cli("-v")
        self.assertEqual(r.returncode, 0)
        self.assertIn("2.6.9", r.stdout)

    def test_help_flag(self) -> None:
        r = self.cli("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("可用指令", r.stdout)

    def test_unknown_command_returns_nonzero(self) -> None:
        r = self.cli("definitely-not-a-real-command")
        self.assertNotEqual(r.returncode, 0)


# --------------------------------------------------------------------------- #
# Auto-archive whitelist CRUD                                                 #
# --------------------------------------------------------------------------- #

class TestWhitelistCRUD(_IsolatedHome):
    """E2E coverage for include / exclude / list / remove / include-all."""

    def _register(self, paths: list[str]) -> None:
        """Seed the active-project registry file directly."""
        f = self.claude_file("ai_brain_active_projects.txt")
        f.write_text("\n".join(paths) + "\n", encoding="utf-8")

    def _chdir_project(self, proj: Path) -> None:
        # Make a real directory and chdir so the ``current`` / ``.`` patterns
        # resolve to it.
        proj.mkdir(parents=True, exist_ok=True)

    # -- list ---------------------------------------------------------------

    def test_list_empty_when_no_active_projects(self) -> None:
        r = self.cli("list")
        self.assertEqual(r.returncode, 0)
        # Empty registry still produces a header.
        self.assertIn("自動記憶歸檔狀態清單", r.stdout)

    def test_list_shows_active_projects_and_archive_status(self) -> None:
        proj = self.home / "proj-a"
        self._register([str(proj)])
        # Archive whitelist is empty → "預設不歸檔".
        r = self.cli("list")
        self.assertEqual(r.returncode, 0)
        self.assertIn("proj-a", r.stdout)
        self.assertIn("預設不歸檔", r.stdout)

    def test_list_shows_enabled_status_when_archived(self) -> None:
        proj = self.home / "proj-a"
        self._register([str(proj)])
        self.claude_file("ai_brain_auto_archive.txt").write_text(
            f"{proj}\n", encoding="utf-8"
        )
        r = self.cli("list")
        self.assertEqual(r.returncode, 0)
        self.assertIn("已啟用自動歸檔", r.stdout)

    # -- include ------------------------------------------------------------

    def test_include_current_enables_archive(self) -> None:
        proj = self.home / "proj-current"
        self._chdir_project(proj)
        self._register([str(proj)])

        # Bare `ai-brain include` shows the status list (no pattern); need
        # `include current` / `include .` to actually mutate state.
        r = self.cli("include", "current", cwd=proj)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("已成功將專案", r.stdout)

        archive_file = self.claude_file("ai_brain_auto_archive.txt")
        self.assertTrue(archive_file.is_file())
        self.assertEqual(archive_file.read_text(encoding="utf-8").strip(),
                         str(proj))

    def test_include_all_enables_every_active_project(self) -> None:
        a = self.home / "proj-a"
        b = self.home / "proj-b"
        self._register([str(a), str(b)])

        r = self.cli("include-all")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        archive = self.claude_file("ai_brain_auto_archive.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn(str(a), archive)
        self.assertIn(str(b), archive)

    # -- exclude ------------------------------------------------------------

    def test_exclude_current_disables_archive(self) -> None:
        proj = self.home / "proj-current"
        self._chdir_project(proj)
        self._register([str(proj)])
        self.claude_file("ai_brain_auto_archive.txt").write_text(
            f"{proj}\n", encoding="utf-8"
        )

        r = self.cli("exclude", "current", cwd=proj)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        archive = self.claude_file("ai_brain_auto_archive.txt").read_text(
            encoding="utf-8"
        )
        self.assertNotIn(str(proj), archive)

    def test_exclude_all_disables_every_project(self) -> None:
        a = self.home / "proj-a"
        b = self.home / "proj-b"
        self._register([str(a), str(b)])
        self.claude_file("ai_brain_auto_archive.txt").write_text(
            f"{a}\n{b}\n", encoding="utf-8"
        )

        r = self.cli("exclude-all")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        archive = self.claude_file("ai_brain_auto_archive.txt").read_text(
            encoding="utf-8"
        )
        self.assertEqual(archive.strip(), "")

    # -- remove -------------------------------------------------------------

    def test_remove_drops_project_from_active_list(self) -> None:
        a = self.home / "proj-a"
        b = self.home / "proj-b"
        self._register([str(a), str(b)])

        r = self.cli("remove", str(a))
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        active = self.claude_file("ai_brain_active_projects.txt").read_text(
            encoding="utf-8"
        )
        self.assertNotIn(str(a), active)
        self.assertIn(str(b), active)

    def test_remove_all_clears_active_list(self) -> None:
        a = self.home / "proj-a"
        b = self.home / "proj-b"
        self._register([str(a), str(b)])

        r = self.cli("remove", "all")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        active = self.claude_file("ai_brain_active_projects.txt").read_text(
            encoding="utf-8"
        )
        self.assertEqual(active.strip(), "")


# --------------------------------------------------------------------------- #
# Sweep dirty-skip via `stop`                                                 #
# --------------------------------------------------------------------------- #

class TestStopSweepDirtySkip(_IsolatedHome):
    """E2E for the new dirty-skip behaviour added to ``_run_archive_sweep``.

    Each test seeds:
      - active + archive registry containing one project (key derived from path)
      - a ``$HOME/.claude/projects/<key>/session.jsonl`` file with controlled mtime
      - optionally a ``last_sweep_timestamp`` with a controlled cutoff

    Then invokes ``ai-brain stop`` and asserts whether the mempalace shim
    was called (recorded via ``$MEMPALACE_CALL_LOG``).
    """

    def setUp(self) -> None:
        super().setUp()
        # Each subprocess invocation appends to this log.
        self.call_log = self.home / "mempalace_calls.jsonl"
        os.environ["MEMPALACE_CALL_LOG"] = str(self.call_log)

    def _setup_project(self, name: str = "proj-a"):
        """Register an active project + whitelist it for archive."""
        proj = self.home / name
        proj.mkdir(parents=True, exist_ok=True)
        self.claude_file("ai_brain_active_projects.txt").write_text(
            f"{proj}\n", encoding="utf-8"
        )
        self.claude_file("ai_brain_auto_archive.txt").write_text(
            f"{proj}\n", encoding="utf-8"
        )
        # Pre-create the corresponding claude project dir so
        # `find_claude_folder_by_path` can resolve it during the sweep.
        proj_key = str(proj).replace("/", "-").replace("_", "-").lower().strip("-")
        (self.claude_file("projects") / proj_key).mkdir(parents=True, exist_ok=True)
        return proj, proj_key

    def _sweep_calls(self) -> list[dict]:
        if not self.call_log.is_file():
            return []
        return [json.loads(line) for line in self.call_log.read_text(
            encoding="utf-8"
        ).splitlines() if line.strip()]

    # -- Cases --------------------------------------------------------------

    def test_stop_skips_when_no_transcripts(self) -> None:
        proj, _ = self._setup_project()
        # No session.jsonl created → helper sees empty dir → skip.
        r = self.cli("stop", cwd=proj)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertEqual(self._sweep_calls(), [])

    def test_stop_skips_when_transcript_older_than_cutoff(self) -> None:
        proj, proj_key = self._setup_project()
        # Set sweep timestamp to NOW.
        self.claude_file("last_sweep_timestamp").write_text(
            str(int(time.time())), encoding="utf-8"
        )
        # Transcript written 1 hour ago → older than cutoff → skip.
        self.write_jsonl(proj_key, mtime=time.time() - 3600)

        r = self.cli("stop", cwd=proj)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertEqual(self._sweep_calls(), [])

    def test_stop_runs_sweep_when_transcript_newer_than_cutoff(self) -> None:
        proj, proj_key = self._setup_project()
        # Cutoff = 1 hour ago.
        self.claude_file("last_sweep_timestamp").write_text(
            str(int(time.time()) - 3600), encoding="utf-8"
        )
        # Fresh transcript → sweep runs.
        self.write_jsonl(proj_key, mtime=time.time())

        r = self.cli("stop", cwd=proj)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        calls = self._sweep_calls()
        self.assertTrue(calls, msg=f"expected mempalace sweep, got: {calls}")
        self.assertEqual(calls[0]["cmd"], "sweep")
        self.assertTrue(any(proj_key in a for a in calls[0]["args"]),
                        msg=f"sweep args missing project key: {calls[0]}")

    def test_stop_runs_sweep_when_no_baseline_file(self) -> None:
        """First-ever run with no last_sweep_timestamp ⇒ cutoff=0 ⇒ always sweep."""
        proj, proj_key = self._setup_project()
        # Ensure no baseline.
        lsf = self.claude_file("last_sweep_timestamp")
        if lsf.is_file():
            lsf.unlink()
        self.write_jsonl(proj_key, mtime=time.time() - 86400)  # old file, but cutoff=0

        r = self.cli("stop", cwd=proj)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertTrue(self._sweep_calls(),
                        msg="first sweep should run regardless of transcript age")

    def test_stop_only_sweeps_projects_with_new_transcripts(self) -> None:
        """Mixed scenario: one project has new transcript, the other doesn't.

        Verifies the dirty-skip filter applies *per project*, not globally.
        """
        proj_dir = self.home / "proj-mixed"
        proj_dir.mkdir(parents=True, exist_ok=True)
        a_proj = self.home / "proj-a"
        b_proj = self.home / "proj-b"
        # Both project dirs must exist on disk — ``_run_archive_sweep``
        # silently skips projects whose directory is missing.
        a_proj.mkdir(parents=True, exist_ok=True)
        b_proj.mkdir(parents=True, exist_ok=True)
        # Derive the same project keys the CLI's `find_claude_folder_by_path`
        # will look up — they come from the *resolved* project path with `/`
        # and `_` replaced by `-`, lowercased, with edge dashes stripped.
        a_key = str(a_proj).replace("/", "-").replace("_", "-").lower().strip("-")
        b_key = str(b_proj).replace("/", "-").replace("_", "-").lower().strip("-")
        # Both active, both archived.
        self.claude_file("ai_brain_active_projects.txt").write_text(
            f"{a_proj}\n{b_proj}\n", encoding="utf-8"
        )
        self.claude_file("ai_brain_auto_archive.txt").write_text(
            f"{a_proj}\n{b_proj}\n", encoding="utf-8"
        )
        # Pre-create the corresponding claude project dirs.
        (self.claude_file("projects") / a_key).mkdir(parents=True, exist_ok=True)
        (self.claude_file("projects") / b_key).mkdir(parents=True, exist_ok=True)
        # Cutoff = now.
        self.claude_file("last_sweep_timestamp").write_text(
            str(int(time.time())), encoding="utf-8"
        )
        # A: transcript older than cutoff → skip. B: fresh → sweep.
        self.write_jsonl(a_key, mtime=time.time() - 3600)
        self.write_jsonl(b_key, mtime=time.time())

        r = self.cli("stop", cwd=proj_dir)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        calls = self._sweep_calls()
        self.assertEqual(len(calls), 1,
                         msg=f"expected exactly 1 sweep call, got: {calls}")
        # The single call must be for B's key.
        self.assertTrue(any(b_key in a for a in calls[0]["args"]),
                        msg=f"sweep should target proj-b only: {calls[0]}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()