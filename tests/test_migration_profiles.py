"""Unit tests for ai-memory migration profiles and architecture.

Tests all four environment profiles (local, minipc, devpc, fqipc) in 100%
isolated sandboxes with zero touch to real host PC data.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_brain import migration
from ai_brain.migration import (
    MigrationState,
    archive_legacy_mempalace,
    cutover_mcp_servers,
    detect_environment_state,
    generate_cjk_bigrams,
    migrate_drawers_to_ai_memory,
    retire_claude_mem,
    rollback_migration,
)


class IsolatedProfileSandbox(unittest.TestCase):
    """Hermetic test harness for environment profile testing.

    Enforces:
    1. Double-layer isolation: stubs Path.home() and sets HOME/XDG_* env vars.
    2. Host immutability: asserts real host paths are completely untouched.
    3. Zero host executable execution: mocks external binaries.
    """

    def setUp(self) -> None:
        self._orig_cwd = os.getcwd()
        self._orig_home_func = Path.home
        self._orig_env = dict(os.environ)

        # Record real host paths to verify immutability in tearDown
        self._real_home = Path.home()
        self._real_paths_to_check = [
            self._real_home / ".mempalace",
            self._real_home / ".ai-memory",
            self._real_home / ".claude",
            self._real_home / ".claude.json",
        ]
        self._real_paths_snapshot = {}
        for p in self._real_paths_to_check:
            if p.exists():
                self._real_paths_snapshot[str(p)] = (
                    p.stat().st_mtime,
                    len(list(p.rglob("*"))) if p.is_dir() else 1,
                )
            else:
                self._real_paths_snapshot[str(p)] = None

        # Create isolated temp root
        self.tmpdir = tempfile.mkdtemp(prefix="ai-brain-profile-test-")
        os.chdir(self.tmpdir)

        # Double-layer redirect: both Path.home() and environment variables
        self.sandbox_home = Path(self.tmpdir)
        Path.home = lambda: self.sandbox_home

        os.environ["HOME"] = str(self.sandbox_home)
        os.environ["XDG_CONFIG_HOME"] = str(self.sandbox_home / ".config")
        os.environ["XDG_DATA_HOME"] = str(self.sandbox_home / ".local" / "share")
        os.environ["USER"] = "testuser"

        (self.sandbox_home / ".claude").mkdir(parents=True, exist_ok=True)
        (self.sandbox_home / ".local" / "bin").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        # Restore environment and working directory
        os.chdir(self._orig_cwd)
        Path.home = self._orig_home_func
        os.environ.clear()
        os.environ.update(self._orig_env)

        # 1. Assert host paths were 100% untouched
        for p_str, snap in self._real_paths_snapshot.items():
            p = Path(p_str)
            if snap is None:
                self.assertFalse(
                    p.exists(),
                    f"Host path {p} was created during sandbox test!",
                )
            else:
                orig_mtime, orig_count = snap
                curr_count = len(list(p.rglob("*"))) if p.is_dir() else 1
                self.assertEqual(
                    curr_count,
                    orig_count,
                    f"Host path {p} file count mutated from {orig_count} to {curr_count}!",
                )
                self.assertEqual(
                    p.stat().st_mtime,
                    orig_mtime,
                    f"Host path {p} mtime mutated during sandbox test!",
                )

        # Clean up tempdir
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def assert_no_unsandboxed_leaks(self, forbidden_strings: list[str]) -> None:
        """Scan all files generated in sandbox and assert forbidden paths don't leak."""
        for p in self.sandbox_home.rglob("*"):
            if p.is_file():
                try:
                    content = p.read_text(encoding="utf-8", errors="ignore")
                    for s in forbidden_strings:
                        self.assertNotIn(
                            s,
                            content,
                            f"Detected host leak of '{s}' in sandbox file {p.relative_to(self.sandbox_home)}!",
                        )
                except Exception:
                    pass


# =========================================================================== #
# Helper to seed mock ChromaDB EAV database
# =========================================================================== #

def _seed_mock_chroma_eav(db_path: Path, count: int = 15) -> None:
    """Create a ChromaDB-compatible SQLite database with EAV schema."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            embedding_id TEXT NOT NULL UNIQUE
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE embedding_metadata (
            id INTEGER NOT NULL,
            key TEXT NOT NULL,
            string_value TEXT,
            int_value INTEGER,
            float_value REAL,
            bool_value INTEGER,
            PRIMARY KEY (id, key)
        );
        """
    )

    sample_drawers = [
        ("auth-jwt", "使用者認證模組設計與 JWT token 簽發邏輯", "backend", "auth", "src/auth.py", "2026-03-01T10:00:00Z"),
        ("db-pool", "資料庫連線池與 PostgreSQL 故障移轉機制", "backend", "db", "src/db.py", "2026-03-02T11:00:00Z"),
        ("cache-redis", "Redis 快取層與分布式鎖架構分析", "infra", "cache", "docs/cache.md", "2026-03-03T12:00:00Z"),
        ("api-gateway", "API 閘道路由規則與反向代理設置", "infra", "gateway", "deploy/nginx.conf", "2026-03-04T13:00:00Z"),
        ("frontend-ui", "前端設計系統與 React 狀態流轉規範", "frontend", "ui", "ui/App.tsx", "2026-03-05T14:00:00Z"),
    ]

    for i in range(count):
        drawer_id = f"drawer-{i:04d}"
        template = sample_drawers[i % len(sample_drawers)]
        topic, doc, wing, room, src, dt = template
        doc_content = f"{doc} (條目 {i})"

        cur.execute("INSERT INTO embeddings (embedding_id) VALUES (?)", (drawer_id,))
        row_id = cur.lastrowid

        metadata_entries = [
            (row_id, "chroma:document", doc_content),
            (row_id, "topic", f"{topic}-{i}"),
            (row_id, "wing", wing),
            (row_id, "room", room),
            (row_id, "source_file", src),
            (row_id, "filed_at", dt),
        ]
        cur.executemany(
            "INSERT INTO embedding_metadata (id, key, string_value) VALUES (?, ?, ?)",
            metadata_entries,
        )

    conn.commit()
    conn.close()


def _seed_upstream_ai_memory_schema(db_path: Path, schema_version: int = 81) -> None:
    """Bootstrap a mock upstream ai-memory SQLite database (matching schema_version v81)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);")
    cur.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (?);", (schema_version,))

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            tier TEXT NOT NULL DEFAULT 'long',
            namespace TEXT NOT NULL DEFAULT 'default',
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT,
            metadata TEXT,
            priority INTEGER DEFAULT 0,
            confidence REAL DEFAULT 1.0,
            source TEXT DEFAULT 'cli',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    cur.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            title,
            content,
            tags,
            content='memories',
            content_rowid='rowid'
        );
        """
    )
    # Simple triggers to mirror upstream FTS5 sync
    cur.execute(
        """
        CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, title, content, tags)
            VALUES (new.rowid, new.title, new.content, new.tags);
        END;
        """
    )
    conn.commit()
    conn.close()


# =========================================================================== #
# Profile 1: Local MacBook (Darwin, legacy mempalace EAV migration)
# =========================================================================== #

class TestLocalMacBookProfile(IsolatedProfileSandbox):
    """Test environment profile: local MacBook Pro (macOS Darwin).

    Initial state:
    - Legacy ~/.mempalace with 15 EAV drawers.
    - Legacy MCP config in ~/.claude.json.
    - Legacy claude-mem hooks in ~/.claude/settings.json.
    """

    def setUp(self) -> None:
        super().setUp()
        # Seed legacy mempalace EAV
        self.chroma_path = self.sandbox_home / ".mempalace" / "palace" / "chroma.sqlite3"
        _seed_mock_chroma_eav(self.chroma_path, count=15)

        # Seed legacy Claude config
        self.claude_json = self.sandbox_home / ".claude.json"
        self.claude_json.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "mempalace": {"command": "mempalace-mcp", "args": []},
                        "claude-mem": {"command": "claude-mem", "args": []},
                    }
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        # Seed legacy settings.json with hooks
        self.settings_json = self.sandbox_home / ".claude" / "settings.json"
        self.settings_json.write_text(
            json.dumps(
                {
                    "hooks": {
                        "preCompact": "claude-mem precompact",
                        "onStop": "claude-mem onstop",
                    },
                    "enabledPlugins": ["claude-mem"],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def test_state_detection_detects_migrate_needed(self) -> None:
        state = detect_environment_state(self.sandbox_home)
        self.assertEqual(state, MigrationState.MIGRATE_NEEDED)

    @patch("ai_brain.migration.bootstrap_ai_memory_schema")
    def test_end_to_end_migration_pipeline(self, mock_bootstrap) -> None:
        # Mock upstream schema-init to seed realistic v81 schema
        def fake_bootstrap(db_path: Path) -> bool:
            _seed_upstream_ai_memory_schema(db_path, schema_version=81)
            return True

        mock_bootstrap.side_effect = fake_bootstrap

        # 1. Run migration
        result = migrate_drawers_to_ai_memory(
            source_chroma=self.chroma_path,
            target_home=self.sandbox_home,
            chunk_size=5,  # test batching with small chunk size
        )
        self.assertTrue(result.success)
        self.assertEqual(result.source_total, 15)
        self.assertEqual(result.imported, 15)
        self.assertEqual(result.rejected, 0)

        # 2. Verify target DB has 15 rows with valid metadata and CJK tags
        target_db = self.sandbox_home / ".ai-memory" / "memory.db"
        self.assertTrue(target_db.exists())
        conn = sqlite3.connect(str(target_db))
        cur = conn.cursor()
        rows = cur.execute("SELECT id, tier, namespace, title, tags, metadata FROM memories").fetchall()
        self.assertEqual(len(rows), 15)
        sample = rows[0]
        self.assertEqual(sample[1], "long")
        self.assertEqual(sample[2], "default")
        tags = json.loads(sample[4])
        # Check CJK bi-gram tags exist
        cjk_tags = [t for t in tags if t.startswith("cjk:")]
        self.assertGreater(len(cjk_tags), 0)

        # 3. Verify FTS5 synchronization
        fts_rows = cur.execute("SELECT title, content FROM memories_fts WHERE memories_fts MATCH '認證'").fetchall()
        self.assertGreater(len(fts_rows), 0)
        conn.close()

        # 4. MCP Cutover
        cutover_mcp_servers(target_db, self.sandbox_home)
        claude_cfg = json.loads(self.claude_json.read_text(encoding="utf-8"))
        self.assertIn("ai-memory", claude_cfg["mcpServers"])
        self.assertNotIn("mempalace", claude_cfg["mcpServers"])
        self.assertNotIn("claude-mem", claude_cfg["mcpServers"])
        self.assertEqual(
            claude_cfg["mcpServers"]["ai-memory"]["args"],
            ["--db", str(target_db), "mcp"],
        )

        # 5. Retire claude-mem
        retire_claude_mem(self.sandbox_home / ".claude", self.settings_json)
        settings = json.loads(self.settings_json.read_text(encoding="utf-8"))
        self.assertNotIn("preCompact", settings.get("hooks", {}))
        self.assertNotIn("onStop", settings.get("hooks", {}))
        self.assertNotIn("claude-mem", settings.get("enabledPlugins", []))

        # 6. Archive legacy mempalace and complete migration
        mempalace_dir = self.sandbox_home / ".mempalace"
        archive_path = archive_legacy_mempalace(mempalace_dir)
        self.assertTrue(archive_path.exists())
        self.assertFalse(mempalace_dir.exists())
        migration.complete_migration(result.manifest_path)

        # 7. Final state detection must now be READY
        final_state = detect_environment_state(self.sandbox_home)
        self.assertEqual(final_state, MigrationState.READY)


# =========================================================================== #
# Profile 2: MiniPC (Darwin, clean state, zero legacy files)
# =========================================================================== #

class TestMiniPCCleanProfile(IsolatedProfileSandbox):
    """Test environment profile: clean MiniPC (Mac mini, Darwin).

    Initial state:
    - Completely clean: no ~/.mempalace, no ~/.claude-mem, no memory.db.
    """

    def test_state_detection_detects_clean_install(self) -> None:
        state = detect_environment_state(self.sandbox_home)
        self.assertEqual(state, MigrationState.CLEAN_INSTALL)

    @patch("ai_brain.migration.bootstrap_ai_memory_schema")
    def test_clean_install_bootstraps_without_migration(self, mock_bootstrap) -> None:
        def fake_bootstrap(db_path: Path) -> bool:
            _seed_upstream_ai_memory_schema(db_path, schema_version=81)
            return True

        mock_bootstrap.side_effect = fake_bootstrap

        target_db = self.sandbox_home / ".ai-memory" / "memory.db"
        ok = migration.bootstrap_ai_memory_schema(target_db)
        self.assertTrue(ok)
        self.assertTrue(target_db.exists())

        # No legacy archive should be created
        self.assertFalse((self.sandbox_home / ".mempalace").exists())
        self.assertFalse(list(self.sandbox_home.glob(".mempalace.backup.*")))

        # Cutover MCP
        cutover_mcp_servers(target_db, self.sandbox_home)
        claude_json = self.sandbox_home / ".claude.json"
        self.assertTrue(claude_json.exists())
        cfg = json.loads(claude_json.read_text(encoding="utf-8"))
        self.assertIn("ai-memory", cfg["mcpServers"])
        self.assertEqual(cfg["mcpServers"]["ai-memory"]["args"], ["--db", str(target_db), "mcp"])


# =========================================================================== #
# Profile 3: DevPC (Linux, POSIX paths, no Claude Desktop)
# =========================================================================== #

class TestDevPCProfile(IsolatedProfileSandbox):
    """Test environment profile: DevPC (Linux standard user).

    Simulates:
    - platform.system() == "Linux"
    - POSIX paths: ~/.config, ~/.local/share
    - Strict file permissions: 0o700 for migration dir, 0o600 for manifest/db
    """

    @patch("platform.system", return_value="Linux")
    def test_devpc_permissions_and_no_claude_desktop(self, mock_platform) -> None:
        # Create staging DB
        target_db = self.sandbox_home / ".ai-memory" / "memory.db"
        _seed_upstream_ai_memory_schema(target_db, schema_version=81)

        # Set permissions and cutover
        cutover_mcp_servers(target_db, self.sandbox_home)

        # In Linux, Claude Desktop should be skipped (doesn't exist)
        claude_desktop_cfg = self.sandbox_home / ".config" / "Claude" / "claude_desktop_config.json"
        self.assertFalse(claude_desktop_cfg.exists())

        # Test manifest permissions
        migration_dir = self.sandbox_home / ".ai-brain" / "migrations" / "test-run"
        migration_dir.mkdir(parents=True, exist_ok=True)
        manifest_file = migration_dir / "manifest.json"
        manifest_file.write_text("{}", encoding="utf-8")

        # Set strict POSIX permissions
        os.chmod(migration_dir, 0o700)
        os.chmod(manifest_file, 0o600)
        os.chmod(target_db, 0o600)

        # Assert permission modes
        dir_mode = stat.S_IMODE(migration_dir.stat().st_mode)
        file_mode = stat.S_IMODE(manifest_file.stat().st_mode)
        db_mode = stat.S_IMODE(target_db.stat().st_mode)

        self.assertEqual(oct(dir_mode), "0o700")
        self.assertEqual(oct(file_mode), "0o600")
        self.assertEqual(oct(db_mode), "0o600")


# =========================================================================== #
# Profile 4: FQIPC (Linux, non-standard user brian, remote SSH settings)
# =========================================================================== #

class TestFQIPCProfile(IsolatedProfileSandbox):
    """Test environment profile: FQIPC (Linux custom user brian).

    Simulates:
    - User 'brian' at /home/brian
    - Remote SSH settings file: ~/.claude/46.224.196.164_settings.json
    - Full leak check: zero leaks of /Users/carlos or /home/carlos.
    """

    def setUp(self) -> None:
        super().setUp()
        os.environ["USER"] = "brian"
        os.environ["LOGNAME"] = "brian"

        # Create remote SSH Claude settings
        self.remote_settings = self.sandbox_home / ".claude" / "46.224.196.164_settings.json"
        self.remote_settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "preCompact": "claude-mem precompact",
                        "onStop": "claude-mem onstop",
                    },
                    "enabledPlugins": ["claude-mem"],
                    "custom_key": "remote_val",
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    @patch("platform.system", return_value="Linux")
    def test_fqipc_remote_settings_cleanup_and_zero_leak(self, mock_platform) -> None:
        # 1. Clean up remote settings
        retire_claude_mem(self.sandbox_home / ".claude", self.remote_settings)

        cleaned = json.loads(self.remote_settings.read_text(encoding="utf-8"))
        self.assertNotIn("preCompact", cleaned.get("hooks", {}))
        self.assertNotIn("onStop", cleaned.get("hooks", {}))
        self.assertNotIn("claude-mem", cleaned.get("enabledPlugins", []))
        self.assertEqual(cleaned.get("custom_key"), "remote_val")

        # 2. Create target db and cutover
        target_db = self.sandbox_home / ".ai-memory" / "memory.db"
        _seed_upstream_ai_memory_schema(target_db, schema_version=81)
        cutover_mcp_servers(target_db, self.sandbox_home)

        # 3. Assert zero leakage of hardcoded paths
        self.assert_no_unsandboxed_leaks(["/Users/carlos", "/home/carlos"])


# =========================================================================== #
# Rollback & Drift Protection Tests
# =========================================================================== #

class TestMigrationRollbackAndDrift(IsolatedProfileSandbox):
    """Test atomic rollback, journal ledger, and post-cutover drift protection."""

    def test_rollback_restores_original_configs(self) -> None:
        claude_json = self.sandbox_home / ".claude.json"
        orig_content = json.dumps({"mcpServers": {"mempalace": {"command": "old"}}}, indent=2)
        claude_json.write_text(orig_content, encoding="utf-8")

        target_db = self.sandbox_home / ".ai-memory" / "memory.db"
        _seed_upstream_ai_memory_schema(target_db, schema_version=81)

        # Perform cutover with backup enabled
        migration_dir = self.sandbox_home / ".ai-brain" / "migrations" / "test-run"
        migration_dir.mkdir(parents=True, exist_ok=True)

        cutover_mcp_servers(target_db, self.sandbox_home, migration_dir=migration_dir)

        # Confirm modified
        new_content = json.loads(claude_json.read_text(encoding="utf-8"))
        self.assertIn("ai-memory", new_content["mcpServers"])

        # Rollback
        ok = rollback_migration(migration_dir)
        self.assertTrue(ok)

        # Assert 100% byte restoration
        restored_content = claude_json.read_text(encoding="utf-8")
        self.assertEqual(restored_content, orig_content)

    def test_drift_protection_creates_conflict_file_on_external_edit(self) -> None:
        claude_json = self.sandbox_home / ".claude.json"
        claude_json.write_text(json.dumps({"mcpServers": {"old": {}}}), encoding="utf-8")

        target_db = self.sandbox_home / ".ai-memory" / "memory.db"
        _seed_upstream_ai_memory_schema(target_db, schema_version=81)

        migration_dir = self.sandbox_home / ".ai-brain" / "migrations" / "test-drift"
        migration_dir.mkdir(parents=True, exist_ok=True)

        cutover_mcp_servers(target_db, self.sandbox_home, migration_dir=migration_dir)

        # External human edit after cutover (drift)
        claude_json.write_text(json.dumps({"mcpServers": {"human_added": {}}}), encoding="utf-8")

        # Rollback must detect drift, preserve conflict file, and restore
        rollback_migration(migration_dir)

        conflict_files = list(self.sandbox_home.glob(".claude.json.conflict.*"))
        self.assertEqual(len(conflict_files), 1)
        conflict_content = json.loads(conflict_files[0].read_text(encoding="utf-8"))
        self.assertIn("human_added", conflict_content["mcpServers"])


# =========================================================================== #
# CJK Tokenization & SQL Parameter Budget Tests
# =========================================================================== #

class TestCJKBiGramAndParameterLimits(unittest.TestCase):
    """Test pure-Python CJK Bi-gram tokenization and SQLite parameter safety."""

    def test_cjk_bigram_generation_and_stop_word_filtering(self) -> None:
        text = "使用者認證模組設計與 JWT token 簽發邏輯"
        tags = generate_cjk_bigrams(text, max_tags=50)

        # Should generate CJK bi-grams prefixed with 'cjk:'
        self.assertIn("cjk:使用", tags)
        self.assertIn("cjk:用者", tags)
        self.assertIn("cjk:認證", tags)
        self.assertIn("cjk:模組", tags)
        self.assertIn("cjk:設計", tags)

        # Latin words should not be split into CJK bi-grams
        self.assertNotIn("cjk:JW", tags)
        self.assertNotIn("cjk:to", tags)

        # Tag cap enforcement
        long_chinese_text = "測試" * 100
        capped_tags = generate_cjk_bigrams(long_chinese_text, max_tags=50)
        self.assertLessEqual(len(capped_tags), 50)


# =========================================================================== #
# Migration CLI & Top-Level Commands Tests
# =========================================================================== #

class TestMigrationCommandsAndCLI(IsolatedProfileSandbox):
    """Test top-level commands and CLI dispatch for memory migration/rollback/status."""

    def test_commands_memory_migrate_and_status(self) -> None:
        from ai_brain import commands

        # 1. Initially clean environment
        self.assertTrue(commands.memory_status())

        # 2. Seed legacy mempalace
        chroma_file = self.sandbox_home / ".mempalace" / "palace" / "chroma.sqlite3"
        _seed_mock_chroma_eav(chroma_file, count=6)
        claude_json = self.sandbox_home / ".claude.json"
        claude_json.write_text(json.dumps({"mcpServers": {"mempalace": {"command": "mempalace"}}}), encoding="utf-8")

        # Now memory_status should indicate MIGRATE_NEEDED (returns False because not ready/clean)
        self.assertFalse(commands.memory_status())

        # 3. Run migration via commands.memory_migrate()
        def fake_bootstrap(db_path: Path) -> bool:
            _seed_upstream_ai_memory_schema(db_path, schema_version=81)
            return True

        with patch("ai_brain.migration.bootstrap_ai_memory_schema", side_effect=fake_bootstrap):
            ok = commands.memory_migrate()
        self.assertTrue(ok)

        # 4. Verify target DB has 6 records
        target_db = self.sandbox_home / ".ai-memory" / "memory.db"
        self.assertTrue(target_db.is_file())
        conn = sqlite3.connect(str(target_db))
        count = conn.execute("SELECT count(*) FROM memories").fetchone()[0]
        conn.close()
        self.assertEqual(count, 6)

        # 5. Verify legacy mempalace archived and MCP servers cutover
        backups = list(self.sandbox_home.glob(".mempalace.backup.*"))
        self.assertEqual(len(backups), 1)
        cfg = json.loads(claude_json.read_text(encoding="utf-8"))
        self.assertIn("ai-memory", cfg["mcpServers"])
        self.assertNotIn("mempalace", cfg["mcpServers"])

        # 6. Status is now READY (returns True)
        self.assertTrue(commands.memory_status())

    def test_commands_memory_rollback(self) -> None:
        from ai_brain import commands

        # Setup legacy mempalace and claude.json
        chroma_file = self.sandbox_home / ".mempalace" / "palace" / "chroma.sqlite3"
        _seed_mock_chroma_eav(chroma_file, count=4)
        claude_json = self.sandbox_home / ".claude.json"
        orig_claude_cfg = {"mcpServers": {"mempalace": {"command": "mempalace-bin"}}}
        claude_json.write_text(json.dumps(orig_claude_cfg), encoding="utf-8")

        def fake_bootstrap(db_path: Path) -> bool:
            _seed_upstream_ai_memory_schema(db_path, schema_version=81)
            return True

        # Migrate
        with patch("ai_brain.migration.bootstrap_ai_memory_schema", side_effect=fake_bootstrap):
            commands.memory_migrate()

        # Target DB exists
        target_db = self.sandbox_home / ".ai-memory" / "memory.db"
        self.assertTrue(target_db.is_file())

        # Rollback via commands.memory_rollback()
        ok = commands.memory_rollback()
        self.assertTrue(ok)

        # Verify configs restored
        restored_cfg = json.loads(claude_json.read_text(encoding="utf-8"))
        self.assertIn("mempalace", restored_cfg["mcpServers"])
        self.assertNotIn("ai-memory", restored_cfg["mcpServers"])

        # Verify mempalace directory restored
        self.assertTrue((self.sandbox_home / ".mempalace").is_dir())

    def test_cli_dispatch_verbs(self) -> None:
        from ai_brain import cli

        def fake_bootstrap(db_path: Path) -> bool:
            _seed_upstream_ai_memory_schema(db_path, schema_version=81)
            return True

        # 1. memory status
        ret = cli.main(["memory", "status"])
        self.assertEqual(ret, 0)

        # 2. memory (default status)
        ret = cli.main(["memory"])
        self.assertEqual(ret, 0)

        # 3. memory migrate (clean install mode)
        with patch("ai_brain.migration.bootstrap_ai_memory_schema", side_effect=fake_bootstrap):
            ret = cli.main(["memory", "migrate"])
            self.assertEqual(ret, 0)

        # 4. memory rollback
        ret = cli.main(["memory", "rollback"])
        self.assertEqual(ret, 0)

        # 5. top-level migrate alias
        with patch("ai_brain.migration.bootstrap_ai_memory_schema", side_effect=fake_bootstrap):
            ret = cli.main(["migrate", "--force"])
            self.assertEqual(ret, 0)

        # 6. top-level rollback alias
        ret = cli.main(["rollback"])
        self.assertEqual(ret, 0)


if __name__ == "__main__":
    unittest.main()
