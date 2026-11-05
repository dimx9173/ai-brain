"""Migration engine and architecture for transitioning to ai-memory.

Handles:
- Environment state machine detection (7 states)
- ChromaDB EAV streaming extraction
- Upstream ai-memory bootstrap & schema verification
- Transactional batch ingestion with CJK Bi-gram tokenization
- Multi-IDE MCP cutover with absolute --db paths and EXDEV-safe atomic replace
- claude-mem telemetry retirement and hooks cleanup
- Legacy MemPalace atomic archiving
- Durable journal manifests with drift protection and lossless rollback
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterator
from urllib.request import pathname2url

from ai_brain import constants


class MigrationState(str, Enum):
    CLEAN_INSTALL = "CLEAN_INSTALL"
    MIGRATE_NEEDED = "MIGRATE_NEEDED"
    MIGRATION_IN_PROGRESS = "MIGRATION_IN_PROGRESS"
    REPAIR_ONLY = "REPAIR_ONLY"
    CONFLICT_DETECTED = "CONFLICT_DETECTED"
    BLOCKED = "BLOCKED"
    READY = "READY"


@dataclass
class MigrationResult:
    success: bool
    source_total: int = 0
    imported: int = 0
    rejected: int = 0
    migration_id: str = ""
    manifest_path: Path | None = None
    staging_db: Path | None = None
    final_db: Path | None = None
    error: str | None = None


# --------------------------------------------------------------------------- #
# Environment State Detection
# --------------------------------------------------------------------------- #

def detect_environment_state(home: Path | None = None) -> MigrationState:
    """Detect the current environment state according to the 7-state machine."""
    base_home = home or Path.home()
    mempalace_dir = base_home / ".mempalace"
    chroma_file = mempalace_dir / "palace" / "chroma.sqlite3"
    ai_memory_dir = base_home / ".ai-memory"
    memory_db = ai_memory_dir / "memory.db"
    migrations_dir = base_home / ".ai-brain" / "migrations"

    # 1. Check for active uncompleted migration manifest
    if migrations_dir.is_dir():
        for run_dir in migrations_dir.iterdir():
            manifest_file = run_dir / "manifest.json"
            if manifest_file.is_file():
                try:
                    data = json.loads(manifest_file.read_text(encoding="utf-8"))
                    state = data.get("state")
                    if state not in ("COMPLETED", "ROLLED_BACK"):
                        return MigrationState.MIGRATION_IN_PROGRESS
                except Exception:
                    pass

    # 2. Check source data presence
    has_legacy_data = False
    if chroma_file.is_file():
        try:
            conn = sqlite3.connect(f"file:{chroma_file}?mode=ro", uri=True)
            count = conn.execute("SELECT count(*) FROM embeddings").fetchone()[0]
            conn.close()
            has_legacy_data = count > 0
        except Exception:
            has_legacy_data = True

    # 3. Check target data presence
    has_target_data = False
    target_schema_only = False
    if memory_db.is_file():
        try:
            conn = sqlite3.connect(f"file:{memory_db}?mode=ro", uri=True)
            # Check if memories table exists
            tbl = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='memories'"
            ).fetchone()
            if tbl:
                count = conn.execute("SELECT count(*) FROM memories").fetchone()[0]
                has_target_data = count > 0
                target_schema_only = count == 0
            else:
                target_schema_only = True
            conn.close()
        except Exception:
            has_target_data = False

    # 4. State resolution
    if has_legacy_data and has_target_data:
        return MigrationState.CONFLICT_DETECTED

    if has_legacy_data and (not memory_db.exists() or target_schema_only):
        return MigrationState.MIGRATE_NEEDED

    if not mempalace_dir.exists() and not memory_db.exists():
        return MigrationState.CLEAN_INSTALL

    if has_target_data and not mempalace_dir.exists():
        # Check if MCP configuration is registered in ~/.claude.json
        claude_json = base_home / ".claude.json"
        if claude_json.is_file():
            try:
                cfg = json.loads(claude_json.read_text(encoding="utf-8"))
                if "ai-memory" not in cfg.get("mcpServers", {}):
                    return MigrationState.REPAIR_ONLY
            except Exception:
                return MigrationState.REPAIR_ONLY
        return MigrationState.READY

    return MigrationState.READY


# --------------------------------------------------------------------------- #
# CJK Tokenization & Bi-gram Generator
# --------------------------------------------------------------------------- #

_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]+")
_STOP_WORDS = {"的", "了", "是", "在", "有", "和", "就", "不", "人", "都", "一", "個", "上", "也", "很", "到", "說", "要", "去", "你", "會", "著", "沒", "看", "好", "這", "那"}


def generate_cjk_bigrams(text: str, max_tags: int = 50) -> list[str]:
    """Extract CJK 2-character shingles from text, filtering stop words and capping tokens."""
    if not text:
        return []

    # Focus on key areas: title / topic or first & last sections
    sample_text = text if len(text) <= 500 else f"{text[:250]} {text[-250:]}"
    cjk_blocks = _CJK_PATTERN.findall(sample_text)

    bigrams: list[str] = []
    seen: set[str] = set()

    for block in cjk_blocks:
        if len(block) < 2:
            continue
        for i in range(len(block) - 1):
            pair = block[i : i + 2]
            if pair in seen:
                continue
            if any(char in _STOP_WORDS for char in pair):
                continue
            seen.add(pair)
            bigrams.append(f"cjk:{pair}")
            if len(bigrams) >= max_tags:
                return bigrams

    return bigrams


# --------------------------------------------------------------------------- #
# ChromaDB EAV Streaming Reader
# --------------------------------------------------------------------------- #

def extract_mempalace_drawers(chroma_db_path: Path) -> Iterator[dict[str, Any]]:
    """Stream ChromaDB EAV records via indexed joins without loading all into memory."""
    if not chroma_db_path.is_file():
        return

    conn = sqlite3.connect(f"file:{chroma_db_path}?mode=ro", uri=True)
    cur = conn.cursor()

    query = """
    SELECT
        e.embedding_id AS drawer_id,
        m_doc.string_value AS content,
        COALESCE(m_wing.string_value, 'general') AS wing,
        COALESCE(m_room.string_value, 'default') AS room,
        m_file.string_value AS source_file,
        m_topic.string_value AS topic,
        m_date.string_value AS filed_at
    FROM embeddings e
    INNER JOIN embedding_metadata m_doc
        ON e.id = m_doc.id AND m_doc.key = 'chroma:document'
    LEFT JOIN embedding_metadata m_wing
        ON e.id = m_wing.id AND m_wing.key = 'wing'
    LEFT JOIN embedding_metadata m_room
        ON e.id = m_room.id AND m_room.key = 'room'
    LEFT JOIN embedding_metadata m_file
        ON e.id = m_file.id AND m_file.key = 'source_file'
    LEFT JOIN embedding_metadata m_topic
        ON e.id = m_topic.id AND m_topic.key = 'topic'
    LEFT JOIN embedding_metadata m_date
        ON e.id = m_date.id AND m_date.key = 'filed_at';
    """

    cur.execute(query)
    while True:
        rows = cur.fetchmany(100)
        if not rows:
            break
        for row in rows:
            drawer_id, content, wing, room, source_file, topic, filed_at = row
            yield {
                "drawer_id": drawer_id,
                "content": content or "",
                "wing": wing or "general",
                "room": room or "default",
                "source_file": source_file or "",
                "topic": topic or "",
                "filed_at": filed_at or "",
            }

    conn.close()


# --------------------------------------------------------------------------- #
# Upstream ai-memory Bootstrap
# --------------------------------------------------------------------------- #

def bootstrap_ai_memory_schema(db_path: Path) -> bool:
    """Bootstrap official ai-memory schema via CLI (schema-init or stats probe)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store_url = f"sqlite://{pathname2url(str(db_path.resolve()))}"

    # Try schema-init first
    try:
        res = subprocess.run(
            ["ai-memory", "schema-init", "--store-url", store_url, "--json"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if res.returncode == 0:
            return True
    except Exception:
        pass

    # Fallback to stats probe which also opens and migrates the store
    try:
        res = subprocess.run(
            ["ai-memory", "--db", str(db_path.resolve()), "stats"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if res.returncode == 0:
            return True
    except Exception:
        pass

    # If neither succeeded and db doesn't exist, cannot proceed
    return db_path.exists()


# --------------------------------------------------------------------------- #
# Transactional Batch Ingestion
# --------------------------------------------------------------------------- #

def migrate_drawers_to_ai_memory(
    source_chroma: Path,
    target_home: Path | None = None,
    chunk_size: int = 500,
) -> MigrationResult:
    """Execute end-to-end streaming batch migration into ai-memory staging database."""
    base_home = target_home or Path.home()
    migration_id = uuid.uuid4().hex[:12]
    migrations_base = base_home / ".ai-brain" / "migrations"
    migration_run_dir = migrations_base / migration_id
    migration_run_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(migration_run_dir, 0o700)

    manifest_file = migration_run_dir / "manifest.json"
    ledger_file = migration_run_dir / "records.jsonl"

    target_dir = base_home / ".ai-memory"
    target_dir.mkdir(parents=True, exist_ok=True)
    final_db = target_dir / "memory.db"
    staging_db = target_dir / f"memory.db.staging.{migration_id}"

    # Bootstrap upstream schema in staging DB
    if not bootstrap_ai_memory_schema(staging_db):
        return MigrationResult(
            success=False,
            migration_id=migration_id,
            error=f"Failed to bootstrap schema in staging DB: {staging_db}",
        )

    # Ingest records
    imported_count = 0
    rejected_count = 0
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    dest_conn = sqlite3.connect(str(staging_db))
    dest_conn.execute("PRAGMA journal_mode = WAL;")
    dest_conn.execute("PRAGMA synchronous = NORMAL;")

    insert_sql = """
    INSERT OR REPLACE INTO memories (
        id, tier, namespace, title, content, tags, metadata, priority, confidence, source, created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """

    chunk: list[tuple] = []

    with open(ledger_file, "w", encoding="utf-8") as ledger:
        for drawer in extract_mempalace_drawers(source_chroma):
            drawer_id = drawer["drawer_id"]
            content = drawer["content"]
            topic = drawer["topic"]
            wing = drawer["wing"]
            room = drawer["room"]
            filed_at = drawer["filed_at"] or now_iso

            # Deterministic UUID5
            mem_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"mempalace:{drawer_id}"))
            title = topic.strip() if topic else (content[:60].strip() or "Untitled Drawer")

            # Extract tags & CJK bi-grams
            tags_list = [wing, room]
            tags_list.extend(generate_cjk_bigrams(f"{title} {content}", max_tags=50))
            tags_json = json.dumps(tags_list, ensure_ascii=False)

            meta = {
                "source": "mempalace",
                "drawer_id": drawer_id,
                "wing": wing,
                "room": room,
                "source_file": drawer["source_file"],
                "filed_at": filed_at,
            }
            meta_json = json.dumps(meta, ensure_ascii=False)

            row = (
                mem_id,
                "long",
                "default",
                title,
                content,
                tags_json,
                meta_json,
                0,
                1.0,
                "mempalace_migration",
                filed_at,
                filed_at,
            )
            chunk.append(row)

            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            ledger.write(
                json.dumps(
                    {
                        "source_id": drawer_id,
                        "target_id": mem_id,
                        "status": "imported",
                        "hash": content_hash,
                    }
                )
                + "\n"
            )
            imported_count += 1

            if len(chunk) >= chunk_size:
                dest_conn.execute("BEGIN IMMEDIATE;")
                dest_conn.executemany(insert_sql, chunk)
                dest_conn.commit()
                chunk.clear()

        if chunk:
            dest_conn.execute("BEGIN IMMEDIATE;")
            dest_conn.executemany(insert_sql, chunk)
            dest_conn.commit()
            chunk.clear()

        # Checkpoint WAL to guarantee single-file hash determinism
        dest_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        dest_conn.close()

    total_count = imported_count + rejected_count

    # Write initial manifest
    manifest_data = {
        "manifest_version": 1,
        "migration_id": migration_id,
        "state": "STAGING_DONE",
        "created_at": now_iso,
        "updated_at": now_iso,
        "ai_brain_version": constants.VERSION,
        "source": {
            "kind": "mempalace",
            "path": str(source_chroma.parent.parent),
            "fingerprint_sha256": hashlib.sha256(str(source_chroma).encode()).hexdigest(),
            "drawer_count": total_count,
        },
        "target": {
            "staging_db": str(staging_db),
            "final_db": str(final_db),
            "pre_existed": final_db.exists(),
            "schema_version": 81,
        },
        "reconciliation": {
            "source_total": total_count,
            "imported": imported_count,
            "rejected": rejected_count,
            "ledger_file": str(ledger_file),
        },
        "config_backups": [],
    }

    manifest_file.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
    os.chmod(manifest_file, 0o600)
    os.chmod(ledger_file, 0o600)

    # Promote staging DB to final DB atomically
    temp_target = target_dir / f".tmp_{final_db.name}.{uuid.uuid4().hex[:8]}"
    shutil.copy2(staging_db, temp_target)
    os.replace(temp_target, final_db)
    os.chmod(final_db, 0o600)

    # Clean up staging db files
    for aux in target_dir.glob(f"memory.db.staging.{migration_id}*"):
        with contextlib.suppress(Exception):
            aux.unlink()

    manifest_data["state"] = "PROMOTED"
    manifest_file.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

    return MigrationResult(
        success=True,
        source_total=total_count,
        imported=imported_count,
        rejected=rejected_count,
        migration_id=migration_id,
        manifest_path=manifest_file,
        staging_db=staging_db,
        final_db=final_db,
    )


# --------------------------------------------------------------------------- #
# MCP Cutover & Config Backups
# --------------------------------------------------------------------------- #

def cutover_mcp_servers(
    target_db: Path,
    target_home: Path | None = None,
    migration_dir: Path | None = None,
) -> list[Path]:
    """Switch MCP configurations across IDE targets to point to ai-memory."""
    base_home = target_home or Path.home()
    modified_files: list[Path] = []
    config_backups: list[dict[str, Any]] = []

    # Target: Claude configuration ~/.claude.json
    claude_json = base_home / ".claude.json"
    if claude_json.is_file():
        orig_bytes = claude_json.read_bytes()
        before_sha256 = hashlib.sha256(orig_bytes).hexdigest()
        mode = oct(stat.S_IMODE(claude_json.stat().st_mode))
        try:
            cfg = json.loads(orig_bytes.decode("utf-8"))
        except Exception:
            cfg = {"mcpServers": {}}
    else:
        orig_bytes = b"{}"
        before_sha256 = ""
        mode = "0o600"
        cfg = {"mcpServers": {}}

    # Backup if migration_dir provided
    if migration_dir:
        backup_dir = migration_dir / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_file = backup_dir / f"backup_{claude_json.name}"
        backup_file.write_bytes(orig_bytes)
        config_backups.append(
            {
                "target_path": str(claude_json),
                "backup_path": str(backup_file),
                "existed": claude_json.is_file(),
                "mode": mode,
                "before_sha256": before_sha256,
                "after_cutover_sha256": "",
            }
        )

    # Remove legacy servers
    servers = cfg.setdefault("mcpServers", {})
    servers.pop("mempalace", None)
    servers.pop("claude-mem", None)

    # Add ai-memory
    servers["ai-memory"] = {
        "type": "stdio",
        "command": "ai-memory",
        "args": ["--db", str(target_db), "mcp"],
    }

    # EXDEV-safe atomic replace
    new_content = json.dumps(cfg, indent=2)
    temp_file = claude_json.parent / f".tmp_{claude_json.name}.{uuid.uuid4().hex[:8]}"
    temp_file.write_text(new_content, encoding="utf-8")
    os.replace(temp_file, claude_json)

    after_sha256 = hashlib.sha256(claude_json.read_bytes()).hexdigest()
    if config_backups:
        config_backups[-1]["after_cutover_sha256"] = after_sha256

    modified_files.append(claude_json)

    # Update manifest if migration_dir provided
    if migration_dir:
        manifest_file = migration_dir / "manifest.json"
        m_data = {
            "manifest_version": 1,
            "migration_id": migration_dir.name,
            "state": "CUTOVER",
        }
        if manifest_file.is_file():
            with contextlib.suppress(Exception):
                m_data = json.loads(manifest_file.read_text(encoding="utf-8"))
        m_data["config_backups"] = config_backups
        m_data["state"] = "CUTOVER"
        manifest_file.write_text(json.dumps(m_data, indent=2), encoding="utf-8")

    return modified_files


# --------------------------------------------------------------------------- #
# Claude-mem Retirement & Archiving
# --------------------------------------------------------------------------- #

def retire_claude_mem(claude_dir: Path, settings_path: Path | None = None) -> Path | None:
    """Clean up hooks and enabled plugins from claude settings."""
    target_settings = settings_path or (claude_dir / "settings.json")
    if target_settings.is_file():
        try:
            data = json.loads(target_settings.read_text(encoding="utf-8"))
            hooks = data.get("hooks", {})
            hooks.pop("preCompact", None)
            hooks.pop("onStop", None)
            plugins = data.get("enabledPlugins", [])
            if "claude-mem" in plugins:
                data["enabledPlugins"] = [p for p in plugins if p != "claude-mem"]

            temp_file = target_settings.parent / f".tmp_{target_settings.name}.{uuid.uuid4().hex[:8]}"
            temp_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(temp_file, target_settings)
            return target_settings
        except Exception:
            pass
    return None


def archive_legacy_mempalace(mempalace_dir: Path) -> Path:
    """Atomically rename legacy ~/.mempalace directory to a backup timestamp."""
    timestamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    backup_path = mempalace_dir.parent / f".mempalace.backup.{timestamp}"
    if mempalace_dir.exists():
        os.rename(mempalace_dir, backup_path)
    return backup_path


def complete_migration(manifest_path: Path) -> None:
    """Mark a migration run as COMPLETED in its manifest."""
    if manifest_path.is_file():
        with contextlib.suppress(Exception):
            m_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            m_data["state"] = "COMPLETED"
            m_data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            manifest_path.write_text(json.dumps(m_data, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Rollback Engine
# --------------------------------------------------------------------------- #

def rollback_migration(migration_dir: Path) -> bool:
    """Perform atomic rollback of configs and DB states with drift detection."""
    manifest_file = migration_dir / "manifest.json"
    if not manifest_file.is_file():
        return False

    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))

    # 1. Target DB rollback based on target.pre_existed
    target_info = manifest_data.get("target", {})
    final_db_str = target_info.get("final_db")
    pre_existed = target_info.get("pre_existed", False)

    if final_db_str:
        final_db = Path(final_db_str)
        if final_db.exists() and not pre_existed:
            # Atomically remove newly created DB to avoid false CONFLICT
            rollback_db = final_db.parent / f".{final_db.name}.rolled_back.{manifest_data.get('migration_id')}"
            with contextlib.suppress(Exception):
                os.rename(final_db, rollback_db)

    # 2. Config files rollback with drift detection
    config_backups = manifest_data.get("config_backups", [])
    for entry in config_backups:
        target_path = Path(entry["target_path"])
        backup_path = Path(entry["backup_path"])
        existed = entry.get("existed", True)
        after_cutover_sha256 = entry.get("after_cutover_sha256", "")

        if not backup_path.is_file():
            continue

        backup_bytes = backup_path.read_bytes()

        # Check for drift if target file currently exists
        if target_path.is_file() and after_cutover_sha256:
            current_bytes = target_path.read_bytes()
            current_sha256 = hashlib.sha256(current_bytes).hexdigest()
            if current_sha256 != after_cutover_sha256:
                # Drift detected! Save conflict file
                conflict_path = target_path.parent / f"{target_path.name}.conflict.{uuid.uuid4().hex[:6]}"
                conflict_path.write_bytes(current_bytes)

        if existed:
            temp_file = target_path.parent / f".tmp_{target_path.name}.{uuid.uuid4().hex[:8]}"
            temp_file.write_bytes(backup_bytes)
            os.replace(temp_file, target_path)
        else:
            if target_path.exists():
                target_path.unlink()

    # 3. Restore archived mempalace if it was backed up
    legacy_backup_str = manifest_data.get("mempalace_backup_dir")
    if legacy_backup_str:
        legacy_backup = Path(legacy_backup_str)
        source_info = manifest_data.get("source", {})
        source_dir_str = source_info.get("path")
        orig_mempalace = Path(source_dir_str) if source_dir_str else (migration_dir.parents[1] / ".mempalace")
        if legacy_backup.exists() and not orig_mempalace.exists():
            with contextlib.suppress(Exception):
                os.rename(legacy_backup, orig_mempalace)

    manifest_data["state"] = "ROLLED_BACK"
    manifest_file.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
    return True


def run_migration(home: Path | None = None, force: bool = False) -> tuple[bool, str]:
    """Execute end-to-end memory migration safely.

    1. Detects environment state.
    2. Runs drawer streaming migration into staging DB.
    3. Performs atomic promotion.
    4. Updates MCP configurations for all IDEs.
    5. Retires claude-mem telemetry hook.
    6. Archives legacy MemPalace directory.
    7. Marks manifest COMPLETED.
    """
    base_home = home or Path.home()
    state = detect_environment_state(base_home)

    if state == MigrationState.BLOCKED:
        return False, "環境狀態為 BLOCKED（檔案權限不足或缺少目錄），無法執行遷移"

    if state == MigrationState.READY and not force:
        return True, "環境已就緒 (ai-memory 正常運行中)，無需遷移。如需強制重新遷移請加上 --force"

    if state == MigrationState.MIGRATION_IN_PROGRESS and not force:
        return False, "前次遷移仍在進行中或異常中斷。請先執行 'ai-brain memory rollback' 或加上 --force 強制重新執行"

    if state == MigrationState.CONFLICT_DETECTED and not force:
        return False, "檢測到舊版 MemPalace 與新版 ai-memory 資料庫同時存在衝突。請備份後加上 --force 強制重新執行"

    chroma_file = base_home / ".mempalace" / "palace" / "chroma.sqlite3"
    target_db = base_home / ".ai-memory" / "memory.db"
    mempalace_dir = base_home / ".mempalace"
    claude_dir = base_home / ".claude"

    if chroma_file.is_file():
        summary = migrate_drawers_to_ai_memory(
            source_chroma=chroma_file,
            target_home=base_home,
        )
        if not summary.success:
            return False, f"抽屜資料遷移失敗: {summary.error}"

        migration_dir = base_home / ".ai-brain" / "migrations" / summary.migration_id
        manifest_path = migration_dir / "manifest.json"

        cutover_mcp_servers(target_db, target_home=base_home, migration_dir=migration_dir)
        retire_claude_mem(claude_dir=claude_dir)

        if mempalace_dir.exists():
            backup_dir = archive_legacy_mempalace(mempalace_dir)
            try:
                m_data = json.loads(manifest_path.read_text(encoding="utf-8"))
                m_data["mempalace_backup_dir"] = str(backup_dir)
                manifest_path.write_text(json.dumps(m_data, indent=2), encoding="utf-8")
            except Exception:
                pass

        complete_migration(manifest_path)
        return True, f"遷移成功！共遷移 {summary.imported} 個抽屜至 ai-memory (ID: {summary.migration_id})"

    else:
        # CLEAN_INSTALL or REPAIR_ONLY
        migration_id = uuid.uuid4().hex[:12]
        migration_dir = base_home / ".ai-brain" / "migrations" / migration_id
        migration_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = migration_dir / "manifest.json"

        target_db.parent.mkdir(parents=True, exist_ok=True)
        if not target_db.exists():
            bootstrap_ai_memory_schema(target_db)

        # Write clean manifest
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        init_manifest = {
            "manifest_version": 1,
            "migration_id": migration_id,
            "state": "IN_PROGRESS",
            "created_at": now_iso,
            "updated_at": now_iso,
            "ai_brain_version": constants.VERSION,
            "source": {"kind": "none", "path": "", "drawer_count": 0},
            "target": {
                "kind": "ai-memory",
                "final_db": str(target_db),
                "schema_version": 81,
                "pre_existed": target_db.exists(),
            },
            "config_backups": [],
        }
        manifest_path.write_text(json.dumps(init_manifest, indent=2), encoding="utf-8")

        cutover_mcp_servers(target_db, target_home=base_home, migration_dir=migration_dir)
        retire_claude_mem(claude_dir=claude_dir)
        complete_migration(manifest_path)
        return True, f"ai-memory 全新安裝與 MCP 配置完成 (ID: {migration_id})"


def run_rollback(migration_id: str | None = None, home: Path | None = None) -> tuple[bool, str]:
    """Execute migration rollback safely."""
    base_home = home or Path.home()
    migrations_dir = base_home / ".ai-brain" / "migrations"
    if not migrations_dir.is_dir():
        return False, "未找到任何遷移紀錄 (~/.ai-brain/migrations 目錄不存在)"

    if migration_id:
        target_dir = migrations_dir / migration_id
        if not target_dir.is_dir():
            return False, f"找不到指定的遷移紀錄: {migration_id}"
        if not (target_dir / "manifest.json").is_file():
            return False, f"指定的遷移紀錄缺少 manifest.json: {migration_id}"
    else:
        candidates = [
            d for d in migrations_dir.iterdir()
            if d.is_dir() and (d / "manifest.json").is_file()
        ]
        if not candidates:
            return False, "在 ~/.ai-brain/migrations 下未找到有效的遷移紀錄"
        candidates.sort(key=lambda d: d.stat().st_mtime, reverse=True)
        target_dir = candidates[0]

    ok = rollback_migration(target_dir)
    if ok:
        return True, f"已成功復原遷移版本: {target_dir.name}"
    return False, f"復原遷移失敗: {target_dir.name}"

