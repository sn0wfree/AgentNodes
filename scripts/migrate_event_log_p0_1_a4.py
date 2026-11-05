"""P0-1 A4: in-place upgrade of event_log UNIQUE for fork-aware seq spaces.

Upgrades ``UNIQUE (aggregate_id, seq)`` → ``UNIQUE (aggregate_id, branch_id, seq)``
by rebuilding event_log. Idempotent: re-running on a fresh DB is a no-op.

Run from the project root:

    python -m scripts.migrate_event_log_p0_1_a4
    python -m scripts.migrate_event_log_p0_1_a4 --db-path /path/to/events.db
    python -m scripts.migrate_event_log_p0_1_a4 --dry-run

The DB path is resolved the same way ``web_session`` and ``EventStore``
resolve it (``AN_SESSIONS_DB`` > ``AN_WORKSPACE_PATH`` > cwd > ``~/.quantnodes``).
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

logger = logging.getLogger("migrate_event_log_p0_1_a4")


def _resolve_db_path(override: str | None) -> Path:
    if override:
        return Path(override).expanduser()
    from agentnodes.core.agent.memory_manager import resolve_db_path
    return resolve_db_path()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--db-path", default=None,
        help="Override the SQLite path (default: project resolver)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report whether a migration is needed without running it.",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Verbose logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    db_path = _resolve_db_path(args.db_path)
    if not db_path.exists():
        logger.error("DB not found: %s", db_path)
        return 1

    logger.info("Opening DB at %s", db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        from agentnodes.core.storage.event_schema import (
            ensure_event_log_schema,
            migrate_event_log_unique,
        )

        # Column backfill (idempotent) — required so the rebuild sees
        # parent_event_id / branch_id in the source table.
        ensure_event_log_schema(conn)

        if args.dry_run:
            from agentnodes.core.storage.event_schema import (
                _existing_unique_sets,
                _table_exists,
            )
            if not _table_exists(conn, "event_log"):
                logger.info("event_log does not exist; nothing to do")
                return 0
            uniques = _existing_unique_sets(conn, "event_log")
            new = {"aggregate_id", "branch_id", "seq"}
            needed = new not in uniques
            logger.info(
                "event_log uniques = %s; new UNIQUE present = %s; migration needed = %s",
                uniques, new in uniques, needed,
            )
            return 0 if not needed else 0  # dry-run never fails

        migrated = migrate_event_log_unique(conn)
        if migrated:
            logger.info("event_log UNIQUE migrated to (aggregate_id, branch_id, seq)")
        else:
            logger.info("event_log already at A4 schema; no-op")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
