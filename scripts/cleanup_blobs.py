"""P0-1 C3 — manual sidecar blob cleanup (TTL = 365 days).

Lists blobs whose ``last_access`` in ``blob_refs`` is older than
``SR_BLOB_TTL_DAYS`` (default 365 — financial compliance), writes an
audit row, then unlinks the blob file + DELETE the metadata row.

Default is ``--dry-run`` (report only). Pass ``--apply`` to actually
delete. Pass ``--ttl-days N`` to override.

Run from the project root:

    python -m scripts.cleanup_blobs --dry-run
    python -m scripts.cleanup_blobs --apply --ttl-days 90
    python -m scripts.cleanup_blobs --apply --db-path /path/to/events.db
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path

logger = logging.getLogger("cleanup_blobs")


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
        "--apply", action="store_true",
        help="Actually delete blobs (default: dry-run report only)",
    )
    parser.add_argument(
        "--ttl-days", type=int, default=None,
        help="Override TTL days (default: SR_BLOB_TTL_DAYS or 365)",
    )
    parser.add_argument(
        "--limit", type=int, default=1000,
        help="Max blobs to process (default: 1000)",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    ttl_days = args.ttl_days
    if ttl_days is None:
        import os
        env_val = os.environ.get("SR_BLOB_TTL_DAYS", "365")
        try:
            ttl_days = int(env_val)
        except ValueError:
            logger.warning(
                "SR_BLOB_TTL_DAYS=%r is not an int; falling back to 365",
                env_val,
            )
            ttl_days = 365

    db_path = _resolve_db_path(args.db_path)
    if not db_path.exists():
        logger.error("DB not found: %s", db_path)
        return 1

    conn = sqlite3.connect(str(db_path))
    try:
        from agentnodes.core.storage.blob_schema import (
            ensure_blob_refs_schema,
            list_stale_blobs,
        )

        ensure_blob_refs_schema(conn)
        candidates = list_stale_blobs(
            conn, ttl_days=ttl_days, limit=args.limit,
        )
    finally:
        conn.close()

    if not candidates:
        logger.info(
            "No stale blobs found (TTL=%d days, db=%s)",
            ttl_days, db_path,
        )
        return 0

    logger.info("Found %d stale blob candidates (TTL=%d days):", len(candidates), ttl_days)
    for c in candidates[:20]:
        logger.info("  %s (ref_count=%d, last_access=%.0f)",
                    c["blob_path"], c["ref_count"], c["last_access"])

    if not args.apply:
        logger.info(
            "Dry-run only — re-run with --apply to actually delete."
        )
        return 0

    # Apply: delete files + metadata + audit log.
    blob_dir = db_path.parent / "trace-blobs"
    deleted = 0
    failed = 0
    audit_log_path = db_path.parent / "blob-cleanup-audit.log"
    with audit_log_path.open("a", encoding="utf-8") as audit:
        audit.write(f"# cleanup run {time.time():.0f} ttl_days={ttl_days}\n")
        for c in candidates:
            path = blob_dir / Path(c["blob_path"]).name
            try:
                if path.exists():
                    path.unlink()
                conn = sqlite3.connect(str(db_path))
                try:
                    conn.execute(
                        "DELETE FROM blob_refs WHERE blob_path = ?",
                        (c["blob_path"],),
                    )
                    conn.commit()
                finally:
                    conn.close()
                audit.write(
                    f"{time.time():.0f}\tDELETE\t{c['blob_path']}\t"
                    f"ref_count={c['ref_count']}\n"
                )
                deleted += 1
            except OSError as exc:
                logger.warning(
                    "Failed to unlink %s: %s", c["blob_path"], exc,
                )
                failed += 1

    logger.info("Deleted %d blobs (%d failed). Audit: %s",
                deleted, failed, audit_log_path)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
