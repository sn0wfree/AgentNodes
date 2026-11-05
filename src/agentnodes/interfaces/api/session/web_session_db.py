"""web_session DB layer: schema, migrations, connection, row mappers.

Extracted from api/routers/web_session.py in the Phase 2 split
(docs/design/phase2-web-session-projector-design.md §2.2). DDL and
migration order is copied verbatim — order-sensitive blocks must
not be rearranged. The connection lifecycle lives here; callers
must close explicitly (WinError-32 flake family).
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)


def _get_db_path() -> Path:
    """Unified session DB path (delegates to resolve_session_db_path).

    Both web_session and EventStore route through the same resolver so
    they can never point at different files. See
    ``core.agent.memory_manager.resolve_session_db_path`` for the
    resolution order (AN_SESSIONS_DB > AN_WORKSPACE_PATH > cwd >
    ~/.quantnodes fallback).
    """
    from agentnodes.kernel.agent.memory_manager import resolve_session_db_path
    return resolve_session_db_path()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create all tables + indexes + FTS5 triggers (idempotent)."""
    # Enable foreign key constraints (Level 2, Phase 2 commit 2).
    # Required for the new message_parts.session_id / .message_id
    # FOREIGN KEY ... ON DELETE CASCADE to actually fire. The existing
    # messages.session_id FK has been declared since the original
    # schema, but was inert without this PRAGMA — see comments on
    # the message_parts table below.
    #
    # B4 hotfix: restore the caller's setting on the way out. PRAGMA
    # foreign_keys is connection-scoped; leaving it ON permanently changed
    # every shared connection (e.g. SQLiteStore's, now that _init_schema
    # delegates here) and started enforcing FKs on paths that predate this
    # schema -- raw event_log inserts broke.
    _fk_before = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=ON")
    # Stale FTS triggers must go before ANY DML on messages: the sync
    # triggers reference messages_fts, so a database whose FTS table was
    # dropped (or never rebuilt) makes even the message_type UPDATE below
    # fail with "no such table: main.messages_fts". The rebuild block later
    # recreates triggers, so dropping early is safe and idempotent.
    for trig in ("messages_ai", "messages_ad", "messages_au"):
        try:
            conn.execute(f"DROP TRIGGER IF EXISTS {trig}")
        except sqlite3.OperationalError as exc:
            logger.warning("drop trigger %s: %s", trig, exc)
    # Main sessions table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '新会话',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            starred INTEGER NOT NULL DEFAULT 0,
            tags_json TEXT NOT NULL DEFAULT '[]',
            message_count INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0
        )
    """)

    # Backfill new columns for pre-existing databases (idempotent via try/except)
    _add_column(conn, "sessions", "starred", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "sessions", "tags_json", "TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, "sessions", "message_count", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "sessions", "archived", "INTEGER NOT NULL DEFAULT 0")

    # Messages table (FK cascade).
    #
    # Note: parts_json and tool_call_id columns were dropped in
    # Level 2 / Phase 2 commit 6 — see cleanup block below. The schema
    # here reflects the post-cleanup state. For pre-commit-6 databases,
    # the drop happens via the cleanup block; for fresh databases,
    # the table is created with the new schema directly.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            metadata_json TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_session_created "
        "ON messages(session_id, created_at)"
    )

    # Per-session monotonic sequence number (Level 1, opencode-aligned).
    # Default 0 keeps existing rows valid; backfill script assigns real
    # values from created_at order. The UNIQUE INDEX is created in
    # scripts/backfill_seq.py after backfill to avoid constraint
    # conflicts on legacy rows.
    _add_column(conn, "messages", "seq", "INTEGER NOT NULL DEFAULT 0")
    # No index here — backfill creates UNIQUE INDEX uq_messages_session_seq
    # which is strictly stronger than a non-unique index on the same cols.

    # message_parts (Level 2, opencode-aligned, Phase 2 commit 1)
    # Splits parts out of the messages.parts_json JSON column into a
    # dedicated table. Each part is a row; the parent message_id links
    # back. opencode's PartTable model.
    #
    # Fields:
    #   id          : stable per-part UUID (independent of message_id)
    #   message_id  : FK to messages.id (CASCADE delete)
    #   session_id  : FK to sessions.id (CASCADE delete) — denormalized
    #                 so list_messages can fetch all parts in one JOIN
    #   type        : part type (text | tool_call | tool_result | thinking
    #                 | file_edit | table | chart | image)
    #   data_json   : full part data as JSON (preserves opencode shape)
    #   seq         : part ordering within the message (Level 1 invariant)
    #   time_created: when this part was created (vs message.created_at
    #                 which is when the message row was inserted)
    #
    # No reads/writes yet — commit 2 (dual-write) wires the writes,
    # commit 3 (migration) populates from existing parts_json, commit
    # 5 switches the read path. Until commit 5, this table is empty
    # and the old parts_json column is the source of truth.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS message_parts (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            type TEXT NOT NULL,
            data_json TEXT NOT NULL,
            seq INTEGER NOT NULL DEFAULT 0,
            time_created REAL NOT NULL,
            FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_message_parts_message_seq "
        "ON message_parts(message_id, seq)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_message_parts_session_seq "
        "ON message_parts(session_id, seq)"
    )

    # Compaction message type (opencode-aligned, fixes "spontaneous summary" bug)
    # Use nullable column first, then UPDATE existing rows, to avoid NOT NULL failure
    _add_column(conn, "messages", "message_type", "TEXT")
    conn.execute(
        "UPDATE messages SET message_type = 'assistant' WHERE message_type IS NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_session_type_created "
        "ON messages(session_id, message_type, created_at)"
    )

    # ── One-time destructive/expensive migrations (versioned) ────────
    # Run once per database via PRAGMA user_version, not on every
    # connection open. Previously the DELETE below executed on every
    # _get_db() call (O(n) per write + any stray role='tool' rows — e.g.
    # written by the B4 transition — were destroyed at runtime). The
    # migration script (scripts/migrate_role_tool_to_assistant.py) must
    # have been run BEFORE this code on existing DBs, otherwise
    # result-bearing tool results are lost (same caveat, now only once).
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    _run_schema_migrations(conn, version)

    # Attempts table — tracks each AgentLoop execution (借鉴 vibe_trading)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS attempts (
            attempt_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            parent_attempt_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            prompt TEXT,
            run_dir TEXT,
            summary TEXT,
            react_trace_json TEXT,
            metrics_json TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            error TEXT,
            message_id TEXT,
            persona TEXT,
            mode TEXT NOT NULL DEFAULT 'build',
            model_override TEXT,
            thinking TEXT NOT NULL DEFAULT 'auto',
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_attempts_session "
        "ON attempts(session_id, created_at)"
    )

    # event_log (Level 3, Phase 3 B1 commit 1) — append-only event store.
    #
    # Every AgentLoop event (message_received, text.started, text_delta,
    # text.ended, tool.call, tool.result, llm_usage, agent_done, ...) is
    # persisted here as an immutable row. The projector reads from this
    # log to update messages + message_parts; the EventBus reads from
    # here to replay events for SSE reconnect.
    #
    # Schema (opencode-aligned, canonical in core/storage/event_schema.py):
    #   id               — stable per-event UUID
    #   aggregate_id     — session_id (the aggregate root)
    #   seq              — per-aggregate monotonic integer (UNIQUE)
    #   type             — event type string (dot-namespaced: "text.started")
    #   data_json        — event payload as JSON (preserves opencode shape)
    #   time_created     — wall-clock timestamp (server time.time())
    #   parent_event_id  — P0-1: trace tree (nullable)
    #   branch_id        — P0-1: fork branch (default 'main')
    #
    # The (aggregate_id, seq) UNIQUE INDEX ensures events are append-only
    # and provides efficient replay (WHERE aggregate_id = ? ORDER BY seq).
    # The (type, time_created) index supports time-range queries by event
    # type (e.g. "all tool_result events in the last hour").
    from agentnodes.foundation.storage.event_schema import ensure_event_log_schema

    ensure_event_log_schema(conn)

    # FTS5 virtual table (graceful fallback if FTS5 not compiled in).
    _ensure_fts_table(conn)

    # Restore the caller's FK setting (see comment at function head).
    conn.execute(f"PRAGMA foreign_keys={int(_fk_before)}")


def _run_schema_migrations(
    conn: sqlite3.Connection, version: int
) -> None:
    """Run one-time versioned schema migrations (idempotent via PRAGMA)."""
    if version < 2:
        # Backfill text-part ids while parts_json still exists
        _migrate_text_part_ids(conn)
        conn.execute("DELETE FROM messages WHERE role = 'tool'")
        if _has_column(conn, "messages", "tool_call_id"):
            _drop_column(conn, "messages", "tool_call_id")
        if _has_column(conn, "messages", "parts_json"):
            _drop_column(conn, "messages", "parts_json")
        conn.execute("PRAGMA user_version = 2")
    if version < 3:
        # One-time compaction-type backfill (full-table LIKE scan)
        _migrate_message_types(conn)
        conn.execute("PRAGMA user_version = 3")
    if version < 4:
        # Chat persona column (Composer agent selector). Nullable so old
        # rows start as the default chat persona.
        _add_column(conn, "attempts", "persona", "TEXT")
        conn.execute("PRAGMA user_version = 4")
    if version < 5:
        # Plan/Build mode + model override + thinking params (A2 feature).
        _add_column(conn, "attempts", "mode", "TEXT NOT NULL DEFAULT 'build'")
        _add_column(conn, "attempts", "model_override", "TEXT")
        _add_column(conn, "attempts", "thinking", "TEXT NOT NULL DEFAULT 'auto'")
        conn.execute("PRAGMA user_version = 5")
    if version < 6:
        # P0-1 A4: event_log UNIQUE upgraded to (aggregate_id, branch_id,
        # seq) so multiple fork branches can share the same seq space.
        # Fresh DBs already have the new constraint via the canonical DDL;
        # pre-A4 tables are rebuilt in place (idempotent: skips itself when
        # the new UNIQUE is already present). parent_event_id and
        # branch_id columns are backfilled inside ensure_event_log_schema,
        # which runs unconditionally in _ensure_schema above.
        from agentnodes.foundation.storage.event_schema import migrate_event_log_unique
        migrate_event_log_unique(conn)
        conn.execute("PRAGMA user_version = 6")


def _ensure_fts_table(conn: sqlite3.Connection) -> None:
    """Create/repair the messages_fts full-text search table + triggers.

    Uses trigram tokenizer for substring matching across Chinese/English
    boundaries. Auto-heals a wrong-tokenizer FTS table. Gracefully
    disables search if FTS5 is unavailable.
    """
    try:
        _ensure_fts_table_impl(conn)
    except sqlite3.OperationalError as exc:
        logger.warning("FTS5 unavailable, search endpoint will be disabled: %s", exc)


def _ensure_fts_table_impl(conn: sqlite3.Connection) -> None:
    """Create/repair the FTS table assuming FTS5 is available."""
    needs_fts_rebuild = False
    existing_fts = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='messages_fts'"
    ).fetchone()
    if existing_fts and existing_fts[0]:
        sql_text = existing_fts[0].lower()
        if "tokenize='trigram'" not in sql_text and 'tokenize="trigram"' not in sql_text:
            needs_fts_rebuild = True
            logger.warning(
                "messages_fts exists with wrong tokenizer; "
                "auto-rebuilding with trigram tokenizer..."
            )

    if needs_fts_rebuild:
        # Drop the triggers first so we can rebuild cleanly
        for trig in ("messages_ai", "messages_ad", "messages_au"):
            try:
                conn.execute(f"DROP TRIGGER IF EXISTS {trig}")
            except sqlite3.OperationalError as exc:
                logger.warning("drop trigger %s: %s", trig, exc)
        try:
            conn.execute("DROP TABLE IF EXISTS messages_fts")
        except sqlite3.OperationalError as exc:
            logger.error(
                "drop messages_fts failed (%s); skipping FTS rebuild", exc
            )
            needs_fts_rebuild = False

    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
            content,
            role,
            content='messages',
            content_rowid='rowid',
            tokenize='trigram'
        )
    """)
    # Triggers to keep FTS in sync with messages
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, content, role)
            VALUES (new.rowid, new.content, new.role);
        END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, content, role)
            VALUES ('delete', old.rowid, old.content, old.role);
        END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, content, role)
            VALUES ('delete', old.rowid, old.content, old.role);
            INSERT INTO messages_fts(rowid, content, role)
            VALUES (new.rowid, new.content, new.role);
        END
    """)

    # If we rebuilt the FTS table, repopulate it from messages
    if needs_fts_rebuild:
        try:
            conn.execute(
                "INSERT INTO messages_fts(rowid, content, role) "
                "SELECT rowid, content, role FROM messages"
            )
            logger.info(
                "messages_fts rebuilt with trigram tokenizer; "
                "backfilled from messages table"
            )
        except sqlite3.OperationalError as exc:
            logger.error("FTS backfill failed (continuing): %s", exc)


def _add_column(conn: sqlite3.Connection, table: str, column: str, spec: str) -> None:
    """Add a column if it doesn't exist (SQLite has no IF NOT EXISTS for columns)."""
    _validate_identifier(table, "table")
    _validate_identifier(column, "column")
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")
    except sqlite3.OperationalError:
        # Column already exists — expected on second run
        pass


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """True if the given table has the given column."""
    _validate_identifier(table, "table")
    _validate_identifier(column, "column")
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def _validate_identifier(name: str, kind: str) -> None:
    """SEC-2: validate table/column names to prevent SQL injection."""
    import re as _re
    if not _re.match(r"^[a-zA-Z0-9_]+$", name):
        raise ValueError(f"Invalid {kind} name: {name!r} (only alphanumeric + underscore allowed)")


def _drop_column(conn: sqlite3.Connection, table: str, column: str) -> None:
    """Drop a column from a table.

    SQLite has no DROP COLUMN directly until 3.35.0. For broad
    compatibility we use the 12-step "rename-copy-drop" recipe:
    1. Create new_<table> with desired schema (no dropped column)
    2. Copy data from old → new
    3. Drop old table
    4. Rename new → old
    5. Recreate indexes, triggers, foreign keys

    This is heavy but correct on any SQLite version.
    """
    _validate_identifier(table, "table")
    _validate_identifier(column, "column")
    if not _has_column(conn, table, column):
        return  # already dropped

    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    keep_cols = [r[1] for r in rows if r[1] != column]
    keep_cols_csv = ",".join(keep_cols)

    new_table = f"_new_{table}"
    # Build CREATE TABLE statement from existing schema
    schema_rows = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchall()
    if not schema_rows:
        return
    original_sql = schema_rows[0][0]

    # Extract the actual CREATE TABLE line (may be quoted or unquoted).
    create_match = re.match(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\"?[\w]+\"?)",
        original_sql,
        re.IGNORECASE,
    )
    if not create_match:
        return
    table_ref = create_match.group(1)
    quoted = table_ref.startswith('"') and table_ref.endswith('"')

    # Strip the column from the original CREATE TABLE
    col_pattern = re.compile(
        rf"\b{re.escape(column)}\b\s+[^,)\n]+,?\s*",
        re.IGNORECASE | re.MULTILINE,
    )
    new_sql = col_pattern.sub("", original_sql, count=1)
    # If we left a trailing comma, remove it
    new_sql = re.sub(r",\s*\)", ")", new_sql)
    # Build the new CREATE TABLE with the new table name, preserving quoting
    if quoted:
        new_table_ref = f'"{new_table}"'
    else:
        new_table_ref = new_table
    new_sql = re.sub(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?[\w]+\"?",
        f"CREATE TABLE {new_table_ref}",
        new_sql,
        count=1,
        flags=re.IGNORECASE,
    )

    conn.execute(f"DROP TABLE IF EXISTS {new_table}")
    conn.execute(new_sql)
    conn.execute(
        f"INSERT INTO {new_table} ({keep_cols_csv}) SELECT {keep_cols_csv} FROM {table}"
    )
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {new_table} RENAME TO {table}")

    # Recreate indexes that referenced the dropped column
    idx_rows = conn.execute(
        f"SELECT name, sql FROM sqlite_master "
        f"WHERE type='index' AND tbl_name='{table}' AND sql LIKE '%{column}%'"
    ).fetchall()
    for name, sql in idx_rows:
        try:
            conn.execute(f"DROP INDEX IF EXISTS {name}")
        except sqlite3.OperationalError:
            pass

    # Note: triggers and FKs that reference the dropped column will
    # be lost in the rebuild. For our case (parts_json, tool_call_id)
    # neither is referenced by any trigger, so we're safe.


def _migrate_text_part_ids(conn: sqlite3.Connection) -> None:
    """Assign deterministic ids to text parts missing one (text-part-routing).

    After the opencode-style 3-step text protocol (PR1), every text part
    in `parts_json` must carry an `id` field so the frontend can route
    streaming deltas to the correct segment via findLast by id.

    This migration scans all messages and assigns ``f"legacy-{msg_id}-{idx}"``
    to any text part without an id. Idempotent: parts that already have an
    id are left untouched. Failures are logged and skipped so a single
    corrupt row doesn't abort the whole migration.

    Level 2 / Phase 2 commit 6: parts_json column was dropped. This
    migration is a no-op (no rows to update) on post-migration DBs. We
    guard the SELECT with a column-existence check to avoid OperationalError.
    """
    if not _has_column(conn, "messages", "parts_json"):
        return  # post-migration DB; nothing to do
    rows = conn.execute(
        "SELECT id, parts_json FROM messages WHERE parts_json IS NOT NULL"
    ).fetchall()
    updated = 0
    for row in rows:
        msg_id = row["id"]
        try:
            parts = json.loads(row["parts_json"])
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("migration: msg %s parse failed: %s", msg_id, exc)
            continue
        if not isinstance(parts, list):
            continue
        changed = False
        for i, p in enumerate(parts):
            if (
                isinstance(p, dict)
                and p.get("type") == "text"
                and not p.get("id")
            ):
                p["id"] = f"legacy-{msg_id}-{i}"
                changed = True
        if changed:
            try:
                conn.execute(
                    "UPDATE messages SET parts_json = ? WHERE id = ?",
                    (json.dumps(parts, ensure_ascii=False), msg_id),
                )
                updated += 1
            except sqlite3.Error as exc:
                logger.warning("migration: msg %s update failed: %s", msg_id, exc)
    if updated:
        logger.info(
            "text-part-routing migration: assigned ids to %d legacy text parts",
            updated,
        )


def _migrate_message_types(conn: sqlite3.Connection) -> None:
    """Mark historical compaction messages.

    Part of the opencode-aligned compaction fix. Old assistant messages
    with [context summary] prefix, ## Anchored Summary structure, or
    LLM-generated summary format (## Objective...) are L4-compaction
    artifacts, NOT regular assistant turns. Without the
    message_type='compaction' flag, the LLM treats them as previous
    turns and continues the summary task on the next user message —
    producing "spontaneous summaries".

    Idempotent: only updates rows where message_type='assistant' (the
    default), so re-running the migration is a no-op.
    """
    updated = conn.execute("""
        UPDATE messages
        SET message_type = 'compaction'
        WHERE message_type = 'assistant'
          AND (
              content LIKE '[context summary]%'
              OR content LIKE '## Anchored Summary%'
              OR content LIKE '## Objective%'
              OR content LIKE '%## Important Details%'
          )
    """).rowcount
    if updated:
        logger.info(
            "compaction migration: marked %d historical messages as compaction",
            updated,
        )


_db_thread_local = threading.local()


def _get_db() -> sqlite3.Connection:
    """Return the per-thread SQLite connection. Ensures schema is up to date.

    - One connection per worker thread (FastAPI serves requests on a
      thread pool): endpoints that used to open a fresh connection per
      request and rely on GC to close it now reuse a bounded set of
      connections (F1-3). Connections live for the thread's lifetime
      and are closed by GC at process exit; WAL mode keeps readers
      non-blocking.
    - The cache is keyed by db path (thread-local dict), so switching
      workspace paths (tests, multiple workspaces) opens a fresh
      connection instead of reusing a stale one.
    - WAL journal + busy_timeout: concurrent readers/writers no longer
      hit immediate ``database is locked`` (previously concurrent
      INSERT+UPDATE triggers could fail and messages were silently
      lost because persist_message swallows exceptions).
    - One-time schema migrations run inside _ensure_schema (guarded by
      PRAGMA user_version), not per connection.
    """
    db_path = str(_get_db_path())
    cache = getattr(_db_thread_local, "conns", None)
    if cache is None:
        cache = {}
        _db_thread_local.conns = cache
    conn = cache.get(db_path)
    if conn is None:
        conn = sqlite3.connect(db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        _ensure_schema(conn)
        conn.commit()
        cache[db_path] = conn
    return conn


def _fetch_session_owned(conn: sqlite3.Connection, session_id: str, user_id: str) -> sqlite3.Row:
    """Fetch a session row, enforcing ownership (IDOR protection).

    Returns the row, or raises 404 (not found) / 403 (other user's).
    """
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return row


def _row_to_session(row: sqlite3.Row) -> dict:
    """Convert a sessions row to API JSON shape."""
    tags_raw = row["tags_json"] or "[]"
    try:
        tags = json.loads(tags_raw)
    except (json.JSONDecodeError, TypeError):
        tags = []
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "starred": bool(row["starred"]),
        "tags": tags,
        "message_count": row["message_count"],
        "archived": bool(row["archived"]),
    }


def _row_to_message(
    row: sqlite3.Row,
    parts: list[Any] | None = None,
) -> dict:
    """Convert a messages row to API JSON shape.

    Args:
        row: sqlite3.Row from a `SELECT * FROM messages ...` query.
        parts: Optional pre-fetched parts (Level 2, Phase 2). If None,
            falls back to `parts_json` column on the row. Callers
            should pass parts explicitly to avoid the N+1 problem
            and to support the new message_parts table (commit 5).

    Reads parts from:
   1. The `parts` parameter (preferred; batch-fetched by caller)
    2. The legacy `parts_json` column (fallback for pre-migration
       rows and test fixtures)
    """
    if parts is None:
        # Fallback: legacy parts_json column (Level 2 pre-migration)
        parts = []
        if "parts_json" in row.keys() and row["parts_json"]:
            try:
                parsed = json.loads(row["parts_json"])
                parts = parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, TypeError):
                parts = []

    metadata = None
    if row["metadata_json"]:
        try:
            metadata = json.loads(row["metadata_json"])
        except (json.JSONDecodeError, TypeError):
            metadata = None

    # Extract agent_id from metadata (set by projector for study agents)
    agent_id = None
    if metadata and "agent_id" in metadata:
        agent_id = metadata.pop("agent_id")

    # For user messages without explicit parts, build a single text part
    # from content so the frontend doesn't need to handle null parts.
    # Same for error messages (friendly text in content → text part).
    if not parts and row["content"]:
        if row["role"] == "user" or row["message_type"] == "error":
            parts = [{"type": "text", "text": row["content"]}]
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "role": row["role"],
        "content": row["content"],
        "parts": parts,
        "tool_call_id": row["tool_call_id"] if "tool_call_id" in row.keys() else None,
        "agent_id": agent_id,
        "created_at": row["created_at"],
        "seq": row["seq"] if "seq" in row.keys() else 0,
        "metadata": metadata,
        "message_type": row["message_type"] if "message_type" in row.keys() else "assistant",
    }


def ensure_session_row(db_path: Path, session_id: str, title: str) -> None:
    """确保 chat-session 行存在（不存在则创建）。

    **为什么在这里**：它操作的就是本模块管理的 ``sessions`` 表——
    迁自 ``apps/study/engine_common.py::ensure_study_session``
    （2026-11-05 批次 3）。原名带 ``study`` 是历史包袱，实现里零 study 语义。

    **用途**：SSE 连接时若 session 行不存在（404）且 id 以 ``study_`` 开头，
    自动重建该行，好让 SSE 能继续（会话行被删或从未创建的场景）。

    **所有权**：session API 有 IDOR 校验（``_fetch_session_owned``），
    所以新行必须归真实用户——从现有 session 里取出现最多的非 system 用户。
    这是「谁在跑这个 study」的合理推断；推断不出来才退回 ``system``。

    失败只记 warning 不抛：重建会话行是尽力而为，不该让 SSE 连接失败。
    """
    import time

    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT user_id, COUNT(*) AS c FROM sessions "
            "WHERE user_id IS NOT NULL AND user_id != 'system' "
            "GROUP BY user_id ORDER BY c DESC LIMIT 1"
        ).fetchone()
        owner = row["user_id"] if row else "system"
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, title, user_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, title, owner, time.time(), time.time()),
        )
        conn.commit()
    except Exception:
        logger.warning("ensure_session_row failed for %s", session_id, exc_info=True)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ── Schema 端口注册（2026-11-05 依赖倒置）────────────────────────────
#
# kernel 的 ``SQLiteStore._init_schema`` / ``SQLiteBackend._init_schema``
# 需要「建表」，但内核不能 import 接口层（L3→L1 上行违规）。
# 因此 schema 归 L4 的端口声明（foundation/storage/schema_port），
# **真实 DDL 由本模块在 import 时注册**——这是外层向内层登记，
# 依赖方向 L1→L4，合法。
#
# 生产路径必然 import 本模块（app.py 装配 router 时），所以注册总会发生；
# 纯内核场景（单元测试、嵌入式）走 schema_port 的最小退化 DDL。
try:
    from agentnodes.foundation.storage.schema_port import (
        register_schema_initializer,
    )

    register_schema_initializer(_ensure_schema)
except Exception:  # noqa: BLE001 —— 注册失败不应阻断模块可用性
    logger.debug("schema initializer registration skipped", exc_info=True)
