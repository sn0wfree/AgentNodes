"""Canonical ``event_log`` schema (P0-1 A1+A4).

Single source of truth for the ``event_log`` DDL, previously duplicated in
three places with a creeping divergence:

- ``api/routers/web_session.py`` — canonical: FK, ``UNIQUE (aggregate_id, seq)``,
  ``data_json NOT NULL``, both indexes.
- ``core/agent/event_store.py`` — simplified: missing FK / UNIQUE / NOT NULL /
  the ``(type, time_created)`` index.
- ``api/session/backfill_event_log.py`` — middle: UNIQUE but no FK / second index.

Every writer / creator of ``event_log`` routes through
``ensure_event_log_schema()`` so the historical "whoever creates first wins"
schema divergence (``sessions`` / ``messages`` / ``event_log``) can never recur.

P0-1 additions:

- A3: ``parent_event_id`` (trace tree) and ``branch_id`` (fork support).
- A4: ``UNIQUE (aggregate_id, seq)`` → ``UNIQUE (aggregate_id, branch_id, seq)``
  so multiple fork branches can share the same ``(aggregate_id, seq)`` space.

Fresh DBs get the new constraint in the canonical DDL. Pre-existing DBs are
upgraded via the explicit ``migrate_event_log_unique()`` rebuild — which is
idempotent and skips itself when the new UNIQUE is already present.

Callers should chain ``ensure_event_log_schema(conn)`` (always) with
``migrate_event_log_unique(conn)`` (always; no-op when fresh) on every DB
open. ``web_session._run_schema_migrations`` and the EventStore's
``_init_event_log_schema`` both do so.
"""

from __future__ import annotations

import sqlite3

__all__ = [
    "EVENT_LOG_DDL",
    "EVENT_LOG_INDEXES",
    "ENVELOPE_COLUMNS",
    "ensure_event_log_schema",
    "migrate_event_log_unique",
]

# The canonical event_log DDL. FK to sessions(id) is declared here for both
# creation orders; enforcement is per-connection (EventStore's SQLiteStore
# intentionally leaves foreign_keys OFF — see memory_manager._ensure_conn —
# so it can write events for sessions without a sessions row yet, while the
# web_session connection owns FK enforcement).
#
# P0-1 A4: UNIQUE upgraded to (aggregate_id, branch_id, seq) so each fork
# branch owns its own seq space. Existing rows are all branch_id='main'
# (schema DEFAULT), so the data invariant "main-branch seq is unique" is
# preserved by the new constraint.
EVENT_LOG_DDL = """
CREATE TABLE IF NOT EXISTS event_log (
    id TEXT PRIMARY KEY,
    aggregate_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    data_json TEXT NOT NULL,
    time_created REAL NOT NULL,
    parent_event_id TEXT,
    branch_id TEXT NOT NULL DEFAULT 'main',
    session_key TEXT,
    run_id TEXT,
    schema_v TEXT NOT NULL DEFAULT '1',
    FOREIGN KEY (aggregate_id) REFERENCES sessions(id) ON DELETE CASCADE,
    UNIQUE (aggregate_id, branch_id, seq)
)
"""

EVENT_LOG_INDEXES = (
    (
        "idx_event_log_aggregate_seq",
        "CREATE INDEX IF NOT EXISTS idx_event_log_aggregate_seq "
        "ON event_log(aggregate_id, seq)",
    ),
    (
        "idx_event_log_type_time",
        "CREATE INDEX IF NOT EXISTS idx_event_log_type_time "
        "ON event_log(type, time_created)",
    ),
    (
        "idx_event_log_run_id",
        "CREATE INDEX IF NOT EXISTS idx_event_log_run_id "
        "ON event_log(run_id)",
    ),
)

# P-1 信封列（S1，docs/s1-event-protocol-unification-design-20261027.md §2）。
# canonical 表 = 信封超集：type 即 kind、time_created 即 ts，另补三列。
_ENVELOPE_COLUMNS = (
    ("session_key", "session_key TEXT"),
    ("run_id", "run_id TEXT"),
    ("schema_v", "schema_v TEXT NOT NULL DEFAULT '1'"),
)

# Public alias: the P-1 envelope column names added on top of the P0-1 table.
ENVELOPE_COLUMNS = tuple(c[0] for c in _ENVELOPE_COLUMNS)


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    try:
        cur = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        return cur.fetchone() is not None
    except sqlite3.Error:
        return False


def _add_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    ddl: str,
) -> None:
    """Idempotently add a column to an existing table."""
    if column not in _table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _existing_unique_sets(conn: sqlite3.Connection, table: str) -> list[set[str]]:
    """Return the column-sets of every UNIQUE constraint on ``table``."""
    rows = conn.execute(f"PRAGMA index_list({table})").fetchall()
    out: list[set[str]] = []
    for r in rows:
        idx_name = r[1]
        # index_list: seq, name, unique, origin, partial
        is_unique = r[2]
        origin = r[3] if len(r) > 3 else None
        # Skip indexes that back a UNIQUE constraint vs an INDEX.
        # sqlite_autoindex_* are UNIQUE; non-unique indexes are skipped here.
        if not is_unique and origin != "u":
            continue
        info = conn.execute(f"PRAGMA index_info({idx_name})").fetchall()
        cols = {row[2] for row in info}
        if cols:
            out.append(cols)
    return out


def ensure_event_log_schema(conn: sqlite3.Connection) -> None:
    """Create / reconcile the ``event_log`` table and its indexes.

    Idempotent. Safe on fresh DBs (full canonical DDL), pre-existing DBs
    (backfills ``parent_event_id`` / ``branch_id`` via ALTER TABLE), and
    degraded in-memory backends (no-op if the connection has no table).

    Does NOT set ``PRAGMA foreign_keys`` — that is the caller's concern
    (web_session enables it; EventStore intentionally does not).
    """
    conn.execute(EVENT_LOG_DDL)

    # Backfill P0-1 columns for pre-existing event_log tables.
    _add_column(conn, "event_log", "parent_event_id", "parent_event_id TEXT")
    _add_column(
        conn,
        "event_log",
        "branch_id",
        "branch_id TEXT NOT NULL DEFAULT 'main'",
    )
    # Backfill P-1 envelope columns (S1). Idempotent ALTERs; existing rows
    # get schema_v='1' via the DEFAULT, session_key/run_id stay NULL.
    for _col, ddl in _ENVELOPE_COLUMNS:
        _add_column(conn, "event_log", _col, ddl)

    for _name, ddl in EVENT_LOG_INDEXES:
        conn.execute(ddl)
    conn.commit()


def migrate_event_log_unique(conn: sqlite3.Connection) -> bool:
    """Upgrade ``UNIQUE (aggregate_id, seq)`` → ``UNIQUE (aggregate_id, branch_id, seq)``.

    P0-1 A4. Idempotent: returns ``False`` when the table doesn't exist, or
    when the new UNIQUE is already present. Returns ``True`` when the table
    was rebuilt.

    The rebuild copies every row into ``event_log_new`` under the new
    schema, swaps the tables, then recreates the two secondary indexes.
    Wrapped in an explicit ``BEGIN IMMEDIATE`` transaction so concurrent
    writes from other connections can't observe a partial state.
    """
    if not _table_exists(conn, "event_log"):
        return False

    uniques = _existing_unique_sets(conn, "event_log")
    new_unique = {"aggregate_id", "branch_id", "seq"}
    if new_unique in uniques:
        return False  # already migrated

    columns = _table_columns(conn, "event_log")
    # Pre-flight: every column we need to copy into event_log_new must exist.
    required = {"id", "aggregate_id", "seq", "type", "data_json", "time_created"}
    missing = required - columns
    if missing:
        # The fresh DDL plus ensure_event_log_schema's column backfills must
        # run first. Bail loudly so the caller can re-order.
        raise RuntimeError(
            f"event_log is missing columns required for A4 migration: "
            f"{sorted(missing)}. Run ensure_event_log_schema() first."
        )

    # Copy every column the source table actually has (P0-1: 8 columns;
    # S1 adds the P-1 envelope columns). Destination DDL below carries the
    # full current set, so copying a subset is always safe.
    base_cols = [
        "id", "aggregate_id", "seq", "type", "data_json",
        "time_created", "parent_event_id", "branch_id",
    ]
    envelope_present = [c for c, _ in _ENVELOPE_COLUMNS if c in columns]
    select_cols = ", ".join(base_cols + envelope_present)

    # Close any pending implicit transaction so BEGIN IMMEDIATE doesn't
    # collide with a DDL/DML the caller has staged. The rebuild is
    # self-contained — we don't need to preserve the caller's pending
    # work, just our own DDL/DML that follows.
    conn.commit()

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            CREATE TABLE event_log_new (
                id TEXT PRIMARY KEY,
                aggregate_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                type TEXT NOT NULL,
                data_json TEXT NOT NULL,
                time_created REAL NOT NULL,
                parent_event_id TEXT,
                branch_id TEXT NOT NULL DEFAULT 'main',
                session_key TEXT,
                run_id TEXT,
                schema_v TEXT NOT NULL DEFAULT '1',
                FOREIGN KEY (aggregate_id) REFERENCES sessions(id)
                    ON DELETE CASCADE,
                UNIQUE (aggregate_id, branch_id, seq)
            )
            """
        )
        # ``parent_event_id`` and ``branch_id`` may be NULL in pre-A4 rows
        # (the column-add backfill in ensure_event_log_schema defaults
        # branch_id at the SQLite layer, but parent_event_id stays NULL
        # for legacy rows). The SELECT picks up NULL as-is; the destination
        # column is NULL-tolerant.
        conn.execute(
            f"INSERT INTO event_log_new ({select_cols}) "
            f"SELECT {select_cols} FROM event_log"
        )
        conn.execute("DROP TABLE event_log")
        conn.execute("ALTER TABLE event_log_new RENAME TO event_log")

        # Recreate the secondary indexes (the rename drops them).
        for _name, ddl in EVENT_LOG_INDEXES:
            conn.execute(ddl)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
