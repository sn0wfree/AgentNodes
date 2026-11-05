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
from agentnodes.foundation.storage.db_backend import (
    AUTO_REPAIR_TIMEOUT_S,
    WAL_BUSY_TIMEOUT_MS,
    SQLiteBackend,
    threading_lock,
)

logger = logging.getLogger(__name__)


# ── Types ────────────────────────────────────────────────────────────


Message = dict[str, Any]


# ── Constants ────────────────────────────────────────────────────────


# 统一会话库文件名 / 路径解析已下沉到 foundation/storage/session_paths.py
# （2026-11-05 拆分）。这里 re-export，旧调用点
# `from memory_manager import resolve_session_db_path` 继续可用。
from agentnodes.foundation.storage.session_paths import (  # noqa: F401
    DEFAULT_DB_PATH,
    SESSION_DB_FILENAME,
    resolve_db_path,
    resolve_session_db_path,
)



# ── SQLiteStore (source of truth) ───────────────────────────────────


class SQLiteStore(SQLiteBackend):
    """会话存储：SQLite 后端 + 会话 CRUD。

    **2026-11-05 拆分**：连接管理 / 健康检查 / 自愈等零会话语义的基建
    已下沉到 ``foundation/storage/db_backend.SQLiteBackend``（L4）。本类
    只剩会话域真正需要的 CRUD（``ensure_session_row`` / ``list_messages`` /
    ``delete_session`` / ``list_recent_sessions``）。

    判据：库里建的是 ``sessions`` / ``messages`` **会话表**，所以「这一层
    认识 session」是合理的；但「怎么拿一条 WAL 连接」与 session 无关，
    不该混在这里。
    """

    # ── CRUD operations (caller holds self._lock) ─────────────────

    def ensure_session_row(self, session_id: str) -> None:
        """Make sure the FK anchor row exists (B4 Step 1).

        The projector inserts into ``messages`` with ``foreign_keys=ON``, so a
        missing ``sessions`` row makes every flush fail with "FOREIGN KEY
        constraint failed" and the message silently never materialises.
        Event-emitting callers must create the anchor first; this is the same
        statement ``insert_message`` has always run inline.
        """
        conn = self._ensure_conn()
        ts = time.time()
        conn.execute(
            """INSERT INTO sessions (id, user_id, created_at, updated_at)
            VALUES (?, 'anonymous', ?, ?)
            ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at""",
            (session_id, ts, ts),
        )
        conn.commit()

    # B4 Step 4: ``insert_message`` and ``compact_messages`` were deleted.
    # Both wrote ``INSERT INTO messages`` directly, which made this class a
    # second writer beside the projector (the write-whitelist gate flagged
    # it). append() emits events now, and compaction goes through
    # SessionService.compact_history -> compact.ended -> projector. Neither
    # method had a production caller left.

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

    # B4 Step 4: compact_messages deleted — see the note above
    # list_messages. It wrote INSERT INTO messages, making this a second
    # writer; compaction now flows through the projector.

    # close() 继承自 SQLiteBackend（foundation/storage/db_backend.py）


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

    # B4 Step 4: compact_messages deleted alongside the SQLiteStore twin.

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
        projection_sink: Any = None,
    ):
        self._db_path = resolve_db_path(db_path)
        # 2026-11-05：投影器由组合根注入（与 EventStore 同一契约）。
        # 不注入时 ``append`` 只写 event_log、``get`` 读到空——这正是
        # 「组合根被绕过」的典型症状：每个自建 EventStore 的组件都
        # 重新决定一次「要不要投影」，且各自 new 一个带状态的 Projector。
        self._projection_sink = projection_sink
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
        # B4 D1: lazily created EventStore used only to record the /clear
        # context boundary. Held so close() can release its connection.
        self._event_store: Any | None = None

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
        """Reset the LLM's context window without destroying history (B4 D1).

        The documented semantics of /clear are "clear the context, keep the
        history" (slash_commands.py:821-829), but this used to DELETE the
        rows -- so the history really was gone, and (before the D7 hotfix)
        the sessions row too, which broke every later write.

        Now it emits ``compact.ended``. ``_on_compact`` with no
        ``compacted_until_seq`` records 0, which the history filter reads as
        "everything before this point is out of context" -- history stays
        queryable, the model stops seeing it. No new event type: the marker
        is exactly what manual compaction already produces.
        """
        self._cache.invalidate(session_id)
        await self._locks.clear(session_id)
        self._emergency_buffer.pop(session_id, None)
        try:
            self._emit_clear_marker(session_id)
        except Exception as exc:  # noqa: BLE001 — clear must not fail the caller
            logger.warning("clear() marker emit failed for %s: %s", session_id, exc)

    def _emit_event(self, session_id: str, event_type: str, payload: dict) -> None:
        """Emit through a lazily built EventStore (B4 Step 1).

        ``flush_to_messages=True`` is essential: without it the event lands in
        event_log but the projector never materialises the messages row, so
        ``append`` would appear to succeed while ``get`` returned nothing.

        **2026-11-05**：投影器随构造注入 —— 否则这里自建的 EventStore
        没有 sink，``flush_to_messages=True`` 是空转（B4 陷阱重现）。
        注入为空时仍构造 EventStore 但明确日志告知「不投影」，不再
        假装成功。

        Lazy import: ``event_store`` imports this module at module level.
        The instance is kept because it owns a SQLite connection.
        """
        from .event_store import EventStore

        store = getattr(self, "_event_store", None)
        if store is None:
            sink = getattr(self, "_projection_sink", None)
            if sink is None:
                logger.warning(
                    "UnifiedMemoryManager has no projection_sink: events will "
                    "land in event_log but messages table stays empty "
                    "(append-then-get will return nothing). "
                    "Pass projection_sink=<Projector> from the composition root."
                )
            store = EventStore(
                self._db_path,
                flush_to_messages=True,
                projection_sink=sink,
            )
            self._event_store = store
        store.emit(session_id, event_type, payload)

    def _emit_clear_marker(self, session_id: str) -> None:
        """Emit the compaction boundary that /clear represents."""
        self._emit_event(
            session_id,
            "compact.ended",
            {"summary": "[context cleared]", "reason": "manual"},
        )

    async def compact(self, session_id: str, strategy: Any) -> bool:
        """Two-phase commit: SQLite write first, cache invalidate only if Phase 1 succeeds."""
    # B4 Step 1: ``compact()`` was deleted. It had zero production callers --
    # /compact is implemented by SessionService.compact_history, which emits
    # ``compact.ended`` and lets the projector write the marker. Keeping a
    # direct-SQL twin would have been a second writer after eventisation.

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
        """Persist one message by emitting an event (B4 Step 1).

        Before this, MemoryManager INSERTed into ``messages`` directly, which
        made it a second writer alongside the projector and left the two
        disagreeing on ``seq`` (mm: ``len(existing)``, projector: ``len+1``).
        Now it emits the boundary event and lets ``ProjectionStore`` be the
        only writer -- which is what the architecture's single-writer rule
        asks for, and what the write-whitelist gate enforces.

        The message id is minted here because the projector requires it in the
        payload (``_on_message_received`` reads ``data["message_id"]``); the
        return value contract (an id string) is unchanged.

        Cache: invalidated, not refreshed. The projector may run in another
        process, so the local cache cannot be trusted after the emit -- the
        next ``get()`` re-reads from SQLite.
        """
        msg_id = str(uuid.uuid4())
        if role == "user":
            event_type = "message_received"
            payload: dict[str, Any] = {
                "message_id": msg_id,
                "user_message_id": msg_id,
                "role": "user",
                "content": content,
            }
        else:
            event_type = "assistant_message"
            payload = {
                "message_id": msg_id,
                "content": content,
                "message_type": message_type,
            }
        if metadata:
            # D7 made the projector merge this; without it the public
            # `append(metadata=...)` signature would silently drop it again.
            payload["metadata"] = metadata

        # The projector's insert needs the sessions anchor row or it fails FK.
        self._ensure_session_anchor(session_id)
        self._emit_event(session_id, event_type, payload)
        self._cache.invalidate(session_id)
        return msg_id

    def _ensure_session_anchor(self, session_id: str) -> None:
        """Create the sessions row through whichever backend is active."""
        ensure = getattr(self._backend, "ensure_session_row", None)
        if callable(ensure):
            try:
                ensure(session_id)
                return
            except Exception as exc:  # noqa: BLE001 — log and keep going
                logger.warning(
                    "ensure_session_row failed for %s: %s", session_id, exc,
                )
                return
        # In-memory / duck-typed backends have no FK constraint to satisfy.

    @property
    def is_degraded(self) -> bool:
        return getattr(self, "_degraded", False)

    def close(self) -> None:
        # B4 D1: release the lazily created clear-marker EventStore first.
        store = getattr(self, "_event_store", None)
        if store is not None:
            try:
                # EventStore owns a SQLiteStore; close that directly (the
                # store itself has no close(), only its backend does).
                backend = getattr(store, "backend", None)
                if backend is not None:
                    backend.close()
            except Exception:  # noqa: BLE001 — close must not raise
                pass
            self._event_store = None
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
