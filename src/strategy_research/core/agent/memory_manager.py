"""Phase 7+8 — MemoryManager: SQLite 单一事实源 + cache 加速 + auto-repair.

Unifies 3 parallel history sources:
- ``api/routers/chat.py:_session_histories`` (legacy in-memory dict, removed)
- ``cli/tui/session.py:self.ctx.history`` (TUI in-memory list)
- ``api/session/service.py`` SQLite messages table (current production path)

Architecture:
- ``SQLiteStore`` (source of truth) — WAL mode + auto_repair on corruption
- ``SessionCache`` (LRU accelerator) — bounded by compaction-linked max_entries
- ``UnifiedMemoryManager`` (Protocol impl) — write-through + emergency fallback

Health:
- ``is_degraded`` reflects backend status
- ``emergency_buffer`` last-resort when both SQLite and cache fail
- ``health_report()`` exposes active layer + failure counts for telemetry
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import statistics
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .cache import (
    CacheConfig,
    ConfigValidator,
    SessionCache,
    SessionLockMap,
)

logger = logging.getLogger(__name__)


# ── Types ────────────────────────────────────────────────────────────


Message = dict[str, Any]


# ── Constants ────────────────────────────────────────────────────────


# Unified session DB filename (dot-prefixed → hidden in file managers).
# Lives in the workspace dir so EventStore, web_session, and SessionStore
# all read/write the SAME file. Resolution order (see
# ``resolve_session_db_path``):
#   1. SR_SESSIONS_DB env (explicit override)
#   2. <SR_WORKSPACE_PATH>/.quantnodes_strategy_research_session.db
#   3. <cwd>/.quantnodes_strategy_research_session.db
#   4. ~/.quantnodes/.quantnodes_strategy_research_session.db (fallback)
SESSION_DB_FILENAME = ".quantnodes_strategy_research_session.db"
WAL_BUSY_TIMEOUT_MS = 5_000
AUTO_REPAIR_TIMEOUT_S = 30

# Backward-compat alias (legacy code may still reference DEFAULT_DB_PATH).
# Kept so existing imports don't break, but resolve_db_path now routes
# through resolve_session_db_path which uses the new filename.
DEFAULT_DB_PATH = Path.home() / ".quantnodes" / SESSION_DB_FILENAME


def resolve_session_db_path() -> Path:
    """Resolve the unified session DB path.

    Priority:
      1. ``SR_SESSIONS_DB`` env var (explicit absolute path override)
      2. ``SR_WORKSPACE_PATH`` env var / ``<filename>``
      3. current working dir / ``<filename>``
      4. ``~/.quantnodes`` / ``<filename>`` (last-resort fallback)

    The parent directory is created if missing. Returns an absolute Path.

    This is the SINGLE source of truth for the session DB location —
    both ``EventStore.resolve_db_path`` and
    ``web_session._get_db_path`` route through here so they can never
    diverge (the historical ``sessions.db`` vs
    ``quantnodes_strategy_research_user.db`` split is gone).
    """
    # 1. Explicit override
    env_explicit = os.environ.get("SR_SESSIONS_DB")
    if env_explicit:
        p = Path(env_explicit)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    # 2/3. Workspace-relative (SR_WORKSPACE_PATH or cwd)
    workspace_env = os.environ.get("SR_WORKSPACE_PATH")
    workspace = Path(workspace_env) if workspace_env else Path.cwd()
    p = (workspace / SESSION_DB_FILENAME).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def resolve_db_path(override: Path | None = None) -> Path:
    """Resolve the session DB path (used by EventStore / MemoryManager).

    Priority: explicit ``override`` arg > ``resolve_session_db_path()``
    (which itself honors ``SR_SESSIONS_DB`` / ``SR_WORKSPACE_PATH`` /
    cwd / ``~/.quantnodes`` fallback).
    """
    if override is not None:
        return Path(override)
    return resolve_session_db_path()


# ── SQLiteStore (source of truth) ───────────────────────────────────


class SQLiteStore:
    """SQLite backend with WAL mode, integrity check, and auto-repair."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading_lock()
        self._healthy = False

    def _ensure_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(self._db_path),
                timeout=WAL_BUSY_TIMEOUT_MS / 1000,
                check_same_thread=False,
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            # Note: foreign_keys intentionally NOT enabled here — the
            # EventStore writes event_log rows for sessions that may not
            # have a `sessions` row yet; FK enforcement is owned by the
            # web_session connection where the unified schema is managed.
            self._init_schema(self._conn)
        return self._conn

    @staticmethod
    def _init_schema(conn: sqlite3.Connection) -> None:
        """Delegate to the single DDL owner (schema-dual-master hotfix).

        This class used to carry its own ``sessions``/``messages`` DDL, so
        whichever of SQLiteStore / ``web_session_db._ensure_schema`` created
        the database first silently decided the schema. A store created here
        lacked ``message_parts``/``attempts``/FTS, and a later projector
        flush then died with ``no such table: message_parts``.

        ``web_session_db._ensure_schema`` is the one authoritative DDL (full
        table set + versioned migrations). Lazy import: ``event_store`` is
        imported at module level here, and it lazily imports the projector
        from ``api.session`` (event_store.py) -- same precedent, so this
        adds no new import direction.
        """
        from ...api.session.web_session_db import _ensure_schema

        _ensure_schema(conn)

    def health_check(self) -> bool:
        """Verify DB is not corrupted. Cheap PRAGMA check."""
        try:
            if not self._db_path.exists():
                self._db_path.parent.mkdir(parents=True, exist_ok=True)
                self._db_path.touch()
            conn = sqlite3.connect(str(self._db_path), timeout=5.0)
            result = conn.execute("PRAGMA integrity_check").fetchone()
            conn.close()
            self._healthy = bool(result and result[0] == "ok")
            return self._healthy
        except Exception as exc:
            logger.error("SQLite health_check failed: %s", exc)
            self._healthy = False
            return False

    def auto_repair(self) -> bool:
        """Recover corrupted DB via sqlite3 .dump + re-import."""
        try:
            backup = self._db_path.with_suffix(
                f".corrupt.{int(time.time())}.db"
            )
            shutil.copy2(self._db_path, backup)

            dump = subprocess.run(
                ["sqlite3", str(self._db_path), ".dump"],
                capture_output=True,
                text=True,
                timeout=AUTO_REPAIR_TIMEOUT_S,
            )
            if dump.returncode != 0:
                logger.error("sqlite3 dump failed: %s", dump.stderr)
                return False

            fresh = self._db_path.with_suffix(".repaired.db")
            import_proc = subprocess.run(
                ["sqlite3", str(fresh)],
                input=dump.stdout,
                capture_output=True,
                text=True,
                timeout=AUTO_REPAIR_TIMEOUT_S,
            )
            if import_proc.returncode != 0:
                return False

            shutil.move(str(fresh), str(self._db_path))
            logger.warning("SQLite auto-repair succeeded; backup at %s", backup)
            self._healthy = True
            self._conn = None  # force reconnect
            return True
        except Exception as exc:
            logger.exception("SQLite auto-repair failed: %s", exc)
            return False

    # ── CRUD operations (caller holds self._lock) ─────────────────

    def insert_message(
        self, session_id: str, role: str, content: str,
        metadata: dict | None = None,
        message_type: str = "assistant",
        seq: int = 0,
    ) -> str:
        conn = self._ensure_conn()
        msg_id = str(uuid.uuid4())
        ts = time.time()
        meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        try:
            # Parent row first — foreign_keys=ON enforces the FK.
            conn.execute(
                """INSERT INTO sessions (id, user_id, created_at, updated_at, message_count)
                VALUES (?, 'anonymous', ?, ?, 1)
                ON CONFLICT(id) DO UPDATE SET
                    message_count = message_count + 1,
                    updated_at = excluded.updated_at""",
                (session_id, ts, ts),
            )
            conn.execute(
                """INSERT INTO messages
                (id, session_id, role, content, created_at, metadata_json,
                 message_type, seq)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (msg_id, session_id, role, content, ts, meta_json, message_type, seq),
            )
            conn.commit()
            return msg_id
        except sqlite3.OperationalError as exc:
            logger.error("SQLite insert_message failed: %s", exc)
            raise

    def list_messages(self, session_id: str) -> list[Message]:
        conn = self._ensure_conn()
        rows = conn.execute(
            """SELECT id, session_id, role, content, created_at,
                      metadata_json, message_type, seq
               FROM messages WHERE session_id = ?
               ORDER BY created_at ASC, seq ASC""",
            (session_id,),
        ).fetchall()
        result: list[Message] = []
        for r in rows:
            msg: Message = {
                "id": r[0],
                "session_id": r[1],
                "role": r[2],
                "content": r[3],
                "created_at": r[4],
                "message_type": r[6],
                "seq": r[7],
            }
            if r[5]:
                try:
                    msg["metadata"] = json.loads(r[5])
                except Exception:
                    pass
            result.append(msg)
        return result

    def delete_session(self, session_id: str) -> None:
        """Clear one session's messages, keeping the session row itself (D7 hotfix).

        Deleting the ``sessions`` row used to cascade-break every subsequent
        projector flush: ``messages.session_id`` and ``event_log.aggregate_id``
        both reference it, so after ``/clear`` the confirmation message (and
        everything else) failed with FOREIGN KEY constraint errors until some
        other write path recreated the row. A session row with zero messages
        is harmless; a missing one is not.
        """
        conn = self._ensure_conn()
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.commit()

    def list_recent_sessions(self, limit: int = 10) -> list[str]:
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT id FROM sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [r[0] for r in rows]

    def compact_messages(
        self, session_id: str, summary: str, keep_recent: int = 4,
    ) -> bool:
        """Replace all but last N messages with a single summary entry.

        Used by compaction strategy. Returns True on success.
        """
        conn = self._ensure_conn()
        try:
            # Get all messages, keep last N
            rows = conn.execute(
                """SELECT id, created_at, seq FROM messages
                   WHERE session_id = ?
                   ORDER BY created_at ASC, seq ASC""",
                (session_id,),
            ).fetchall()
            if len(rows) <= keep_recent + 1:
                return True  # not enough to compact

            to_delete = [r[0] for r in rows[:-keep_recent]]
            cutoff_ts = rows[-keep_recent][1] if keep_recent > 0 else time.time()
            summary_id = str(uuid.uuid4())

            # Delete old messages
            placeholders = ",".join("?" * len(to_delete))
            conn.execute(
                f"DELETE FROM messages WHERE id IN ({placeholders})",
                to_delete,
            )
            # Insert summary at cutoff time
            conn.execute(
                """INSERT INTO messages
                (id, session_id, role, content, created_at, message_type)
                VALUES (?, ?, 'system', ?, ?, 'compaction')""",
                (summary_id, session_id, summary, cutoff_ts - 0.001),
            )
            conn.commit()
            return True
        except sqlite3.OperationalError as exc:
            logger.error("SQLite compact_messages failed: %s", exc)
            return False

    def close(self) -> None:
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None


def threading_lock():
    """Stand-in for asyncio.Lock (sync context for SQLiteStore)."""
    import threading
    return threading.RLock()


# ── InMemoryStore (degraded backend) ───────────────────────────────


class InMemoryStore:
    """In-memory backend used when SQLite is unavailable."""

    def __init__(self):
        self._data: dict[str, list[Message]] = {}
        self._lock = threading.RLock()

    def health_check(self) -> bool:
        return True

    def auto_repair(self) -> bool:
        return True  # nothing to repair

    def insert_message(
        self, session_id, role, content, metadata=None,
        message_type="assistant", seq=0,
    ) -> str:
        with self._lock:
            msg: Message = {
                "id": str(uuid.uuid4()),
                "session_id": session_id,
                "role": role,
                "content": content,
                "created_at": time.time(),
                "message_type": message_type,
                "seq": seq,
                **({"metadata": metadata} if metadata else {}),
            }
            self._data.setdefault(session_id, []).append(msg)
            return msg["id"]

    def list_messages(self, session_id) -> list[Message]:
        with self._lock:
            return list(self._data.get(session_id, []))

    def delete_session(self, session_id) -> None:
        """Clear messages only — mirrors SQLiteStore.delete_session (D7 hotfix)."""
        with self._lock:
            self._data[session_id] = []

    def list_recent_sessions(self, limit=10) -> list[str]:
        with self._lock:
            return list(self._data.keys())[:limit]

    def compact_messages(self, session_id, summary, keep_recent=4) -> bool:
        with self._lock:
            msgs = self._data.get(session_id, [])
            if len(msgs) <= keep_recent + 1:
                return True
            kept = msgs[-keep_recent:]
            summary_msg: Message = {
                "id": str(uuid.uuid4()),
                "session_id": session_id,
                "role": "system",
                "content": summary,
                "created_at": time.time(),
                "message_type": "compaction",
                "seq": 0,
            }
            self._data[session_id] = [summary_msg] + kept
            return True

    def close(self) -> None:
        pass


# ── DynamicAvgTokenEstimator (history-based) ───────────────────────


class DynamicAvgTokenEstimator:
    """Estimate avg_tokens_per_message from history.

    Algorithm: (p86 + 1σ) × safety_factor
    Cold start (< min_samples): fall back to config.avg_tokens_per_message
    Re-estimates every config.re_resolve_interval_seconds
    """

    def __init__(
        self,
        memory_manager: "UnifiedMemoryManager",
        config: CacheConfig,
    ):
        self._mm = memory_manager
        self._config = config
        self._cached_value: int | None = None
        self._cached_at: float = 0.0

    def estimate(self) -> int:
        now = time.time()
        if (
            self._cached_value is not None
            and now - self._cached_at < self._config.re_resolve_interval_seconds
        ):
            return self._cached_value

        samples = self._sample_recent_message_tokens()
        if len(samples) < self._config.avg_tokens_min_samples:
            return self._config.avg_tokens_per_message

        from .cache import compute_p86_plus_sigma
        try:
            p86_plus_sigma = compute_p86_plus_sigma(samples)
            estimated = int(
                p86_plus_sigma * self._config.avg_tokens_safety_factor
            )
        except Exception:
            mean = statistics.mean(samples)
            estimated = int(mean * self._config.avg_tokens_safety_factor)

        # Sanity clamp
        if estimated < 10:
            estimated = 10
        if estimated > 10_000:
            estimated = 10_000

        self._cached_value = estimated
        self._cached_at = now
        return estimated

    def invalidate(self) -> None:
        self._cached_value = None
        self._cached_at = 0.0

    def _sample_recent_message_tokens(self) -> list[int]:
        sessions = self._mm._backend.list_recent_sessions(limit=10)
        tokens: list[int] = []
        chars_per_token = max(self._config.chars_per_token, 0.1)
        for sid in sessions:
            history = self._mm._backend.list_messages(sid)
            for msg in history[-10:]:
                content = msg.get("content", "")
                tokens.append(max(1, len(content) // int(chars_per_token)))
                if len(tokens) >= self._config.avg_tokens_estimation_window:
                    return tokens
        return tokens


# ── MemoryManager Protocol ──────────────────────────────────────────


class MemoryManager(Protocol):
    """Phase 8 — unified session history (SQLite 事实源 + 内存 cache)."""

    def append(
        self, session_id: str, role: str, content: str,
        metadata: dict | None = None,
        message_type: str = "assistant",
    ) -> str: ...

    def get(
        self, session_id: str, *, use_cache: bool = True,
    ) -> list[Message]: ...

    def clear(self, session_id: str) -> None: ...

    def compact(self, session_id: str, strategy: Any) -> bool: ...

    def list_recent_sessions(self, limit: int = 10) -> list[str]: ...

    def health_report(self) -> "HealthReport": ...


# ── HealthReport ─────────────────────────────────────────────────────


@dataclass
class HealthReport:
    """System health snapshot."""

    mm_degraded: bool = False
    mm_backend: str = "sqlite"
    cache_active_layer: str = "unknown"
    cache_failure_counts: dict[str, int] = field(default_factory=dict)
    cache_max_entries: int = 1000
    cache_session_count: int = 0
    sqlite_healthy: bool = True
    sqlite_last_check_at: float = 0.0
    sqlite_repaired: bool = False
    emergency_buffer_active: bool = False
    emergency_buffer_session_count: int = 0
    last_compaction_at: float = 0.0
    last_compaction_failures: int = 0


# ── UnifiedMemoryManager ────────────────────────────────────────────


class UnifiedMemoryManager:
    """SQLite = source of truth, 内存 = cache, emergency buffer = last-resort."""

    def __init__(
        self,
        db_path: Path | None = None,
        cache_config: CacheConfig | None = None,
        compact_config: Any = None,
    ):
        self._db_path = resolve_db_path(db_path)
        cache_config = cache_config or CacheConfig.from_env()
        cache_config = ConfigValidator.validate(cache_config)
        cache_config.compact_config = compact_config

        # 1. SQLite + auto-repair fallback
        backend: SQLiteStore | InMemoryStore
        sqlite_store = SQLiteStore(self._db_path)
        if not sqlite_store.health_check():
            logger.warning(
                "SQLite health check failed at %s; attempting auto-repair",
                self._db_path,
            )
            if sqlite_store.auto_repair():
                logger.warning("SQLite auto-repair succeeded")
                sqlite_store._repaired = True  # type: ignore[attr-defined]
            else:
                logger.error(
                    "SQLite auto-repair failed; falling back to in-memory mode"
                )
                backend = InMemoryStore()
                self._degraded = True
                self._backend = backend
                self._cache_config = cache_config
                self._cache = SessionCache(cache_config)
                self._locks = SessionLockMap()
                self._emergency_buffer: dict[str, list[Message]] = {}
                self._estimator = DynamicAvgTokenEstimator(self, cache_config)
                self._last_compaction_at = 0.0
                self._last_compaction_failures = 0
                return
        backend = sqlite_store
        self._degraded = False
        self._backend = backend
        self._sqlite_store = sqlite_store
        self._cache_config = cache_config
        self._cache = SessionCache(cache_config)
        self._locks = SessionLockMap()
        self._emergency_buffer: dict[str, list[Message]] = {}
        self._estimator = DynamicAvgTokenEstimator(self, cache_config)
        self._last_compaction_at = 0.0
        self._last_compaction_failures = 0

    # ── Public API ────────────────────────────────────────────────

    async def append(
        self, session_id: str, role: str, content: str,
        metadata: dict | None = None,
        message_type: str = "assistant",
    ) -> str:
        try:
            return await self._primary_append(
                session_id, role, content, metadata, message_type,
            )
        except Exception as exc:
            logger.exception(
                "append() failed for session %s; using emergency buffer: %s",
                session_id, exc,
            )
            self._emergency_buffer.setdefault(session_id, []).append({
                "id": f"emergency_{uuid.uuid4().hex[:8]}",
                "session_id": session_id,
                "role": role,
                "content": content,
                "created_at": time.time(),
                "message_type": message_type,
                **({"metadata": metadata} if metadata else {}),
            })
            return f"emergency_{uuid.uuid4().hex[:8]}"

    async def get(
        self, session_id: str, *, use_cache: bool = True,
    ) -> list[Message]:
        if use_cache:
            cached = self._cache.get(session_id)
            if cached is not None:
                return list(cached)
        try:
            msgs = self._backend.list_messages(session_id)
        except Exception as exc:
            logger.exception("get() failed; returning emergency buffer: %s", exc)
            return list(self._emergency_buffer.get(session_id, []))
        self._cache.set(session_id, msgs)
        return msgs

    async def clear(self, session_id: str) -> None:
        try:
            self._backend.delete_session(session_id)
        except Exception:
            pass
        self._cache.invalidate(session_id)
        await self._locks.clear(session_id)
        self._emergency_buffer.pop(session_id, None)

    async def compact(self, session_id: str, strategy: Any) -> bool:
        """Two-phase commit: SQLite write first, cache invalidate only if Phase 1 succeeds."""
        async with await self._locks.get(session_id):
            # Phase 1: SQLite compact
            try:
                summary = getattr(strategy, "summary", None) or "compaction summary"
                keep_recent = getattr(strategy, "keep_recent", 4)
                ok = self._backend.compact_messages(
                    session_id, summary, keep_recent,
                )
            except Exception as exc:
                self._last_compaction_failures += 1
                logger.error(
                    "compact Phase 1 failed for session %s: %s",
                    session_id, exc,
                )
                return False
            if not ok:
                self._last_compaction_failures += 1
                return False

            # Phase 2: invalidate cache (best-effort)
            try:
                self._cache.invalidate(session_id)
                self._estimator.invalidate()
                self._last_compaction_at = time.time()
            except Exception as exc:
                logger.warning(
                    "compact Phase 2 (cache invalidate) failed: %s; "
                    "SQLite already updated, next get() will cold-load",
                    exc,
                )
            return True

    def list_recent_sessions(self, limit: int = 10) -> list[str]:
        try:
            return self._backend.list_recent_sessions(limit)
        except Exception:
            return list(self._emergency_buffer.keys())[:limit]

    def health_report(self) -> HealthReport:
        report = HealthReport(
            mm_degraded=self.is_degraded,
            mm_backend=type(self._backend).__name__,
            cache_active_layer=self._estimator._cached_value is not None
                and "p86_plus_sigma" or "unknown",
            cache_failure_counts={},
            cache_max_entries=self._cache.current_max_entries,
            cache_session_count=self._cache.session_count,
            emergency_buffer_active=bool(self._emergency_buffer),
            emergency_buffer_session_count=len(self._emergency_buffer),
            last_compaction_at=self._last_compaction_at,
            last_compaction_failures=self._last_compaction_failures,
        )
        if hasattr(self, "_sqlite_store"):
            report.sqlite_healthy = self._sqlite_store.health_check()
            report.sqlite_last_check_at = time.time()
            report.sqlite_repaired = getattr(
                self._sqlite_store, "_repaired", False,
            )
        return report

    # ── Private helpers ───────────────────────────────────────────

    async def _primary_append(
        self, session_id, role, content, metadata, message_type,
    ) -> str:
        # Per-session lock (sync lock; SQLiteStore uses RLock)
        seq = 0  # simple monotonic per-session
        # Use sync acquire/release pattern for SQLiteStore
        if hasattr(self._backend, "_lock"):
            with self._backend._lock:  # type: ignore[attr-defined]
                # Calculate next seq
                existing = self._backend.list_messages(session_id)
                seq = len(existing)
                msg_id = self._backend.insert_message(
                    session_id, role, content, metadata, message_type, seq,
                )
        else:
            msg_id = self._backend.insert_message(
                session_id, role, content, metadata, message_type, seq,
            )

        # Cache update (WRITE_THROUGH: cache reflects SQLite state)
        # Important: re-read from backend (NOT cache) so cross-process writes
        # are reflected. The cache is per-process; SQLite is the shared truth.
        if self._cache_config.write_policy == WritePolicyT.WRITE_THROUGH:
            try:
                fresh = self._backend.list_messages(session_id)
                self._cache.set(session_id, fresh)
            except Exception:
                # Fallback: append to existing cache if backend read fails
                existing = self._cache.get(session_id) or []
                existing.append({
                    "id": msg_id,
                    "session_id": session_id,
                    "role": role,
                    "content": content,
                    "created_at": time.time(),
                    "message_type": message_type,
                    **({"metadata": metadata} if metadata else {}),
                })
                self._cache.set(session_id, existing)
        return msg_id

    @property
    def is_degraded(self) -> bool:
        return getattr(self, "_degraded", False)

    def close(self) -> None:
        if hasattr(self, "_sqlite_store"):
            self._sqlite_store.close()


class WritePolicyT:
    """Namespace for write policy values (avoid enum import cycle)."""
    WRITE_THROUGH = "write_through"


# ── Factory ─────────────────────────────────────────────────────────


_default_instance: UnifiedMemoryManager | None = None


class MemoryManagerFactory:
    """Process-singleton factory for MemoryManager."""

    @classmethod
    def create(
        cls,
        db_path: Path | None = None,
        cache_config: CacheConfig | None = None,
        compact_config: Any = None,
    ) -> UnifiedMemoryManager:
        global _default_instance
        if (
            _default_instance is None
            or (db_path is not None and _default_instance._db_path != resolve_db_path(db_path))
        ):
            _default_instance = UnifiedMemoryManager(
                db_path=db_path,
                cache_config=cache_config,
                compact_config=compact_config,
            )
        return _default_instance

    @classmethod
    def reset(cls) -> None:
        global _default_instance
        if _default_instance is not None:
            _default_instance.close()
        _default_instance = None


def get_default_memory_manager() -> UnifiedMemoryManager:
    """Module-level accessor used by chat.py emergency fallback."""
    return MemoryManagerFactory.create()


__all__ = [
    "DEFAULT_DB_PATH",
    "DynamicAvgTokenEstimator",
    "HealthReport",
    "InMemoryStore",
    "MemoryManager",
    "MemoryManagerFactory",
    "SQLiteStore",
    "UnifiedMemoryManager",
    "get_default_memory_manager",
    "resolve_db_path",
    "resolve_session_db_path",
    "SESSION_DB_FILENAME",
]
