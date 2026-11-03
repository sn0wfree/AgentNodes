"""Phase 7 — EventStore: SQLite event_log 单一事实源 + cache + SSE push.

Replaces:
- ``api/session/events.py:EventBus`` (legacy in-memory buffer)
- ``api/session/event_bus_v2.py:EventBusV2`` (dual-write event_log + legacy)

Architecture:
- SQLite event_log is the single source of truth (matches memory_manager.py)
- In-memory cache for hot sessions (LRU, same SessionCache as memory)
- Optional SSE push callback (replaces legacy asyncio.Queue subscribers)

Auto-repair: same SQLite health_check + auto_repair as MemoryManager.
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from strategy_research.kernel.events.event_v2 import EventV2
from strategy_research.foundation.protocol import ProtocolVersionError
from .cache import CacheConfig, SessionCache, SessionLockMap
from .memory_manager import InMemoryStore, SQLiteStore, resolve_db_path

logger = logging.getLogger(__name__)


# ── EventStore ──────────────────────────────────────────────────────


class EventStore:
    """SQLite event_log 单一事实源 + cache + SSE push.

    Drop-in replacement for ``EventBus`` + ``EventBusV2``. The same SQLite
    DB (``~/.quantnodes/sessions.db``) holds both ``messages`` and
    ``event_log`` tables — single physical file, two logical tables.

    When ``flush_to_messages=True``, every emit() also calls
    Projector.flush() to update the ``messages`` + ``message_parts``
    tables (materialized views). This is the safe default for the
    web/API path — the projector flush is best-effort: if it fails,
    the event_log still has all events (source of truth).
    """

    def __init__(
        self,
        db_path: Path | None = None,
        cache_config: CacheConfig | None = None,
        sse_pusher: Callable[[str, EventV2], None] | None = None,
        flush_to_messages: bool = False,
    ):
        self._db_path = resolve_db_path(db_path)
        self._flush_to_messages = flush_to_messages
        # Lazy projector (avoids circular import at startup)
        self._projector = None
        cache_config = cache_config or CacheConfig.from_env()

        # 1. SQLite backend with auto-repair fallback
        sqlite_store = SQLiteStore(self._db_path)
        if not sqlite_store.health_check():
            logger.warning(
                "SQLite health check failed at %s; attempting auto-repair",
                self._db_path,
            )
            if not sqlite_store.auto_repair():
                logger.error(
                    "SQLite auto-repair failed; falling back to in-memory mode"
                )
                self._backend: SQLiteStore | InMemoryStore = InMemoryStore()
                self._degraded = True
            else:
                self._backend = sqlite_store
                self._degraded = False
        else:
            self._backend = sqlite_store
            self._degraded = False
        self._sqlite_store = sqlite_store if not self._degraded else None

        # 2. Cache for hot sessions
        self._cache_config = cache_config
        self._cache = SessionCache(cache_config)
        self._locks = SessionLockMap()

        # 3. SSE push callback (replaces asyncio.Queue subscribers)
        self._sse_pusher = sse_pusher

        # 4. Live subscribers (for subscribe() async iterator)
        self._live_queues: dict[str, list[asyncio.Queue]] = {}
        self._live_lock = threading.RLock()

        # P0-1 B3: when the backend is the InMemoryStore (SQLite
        # auto-repair failed), event_log has no real table. We keep a
        # per-process dict of ``[EventV2]`` here so ``_replay`` can
        # still serve the same from_seq / types / branch_id / limit
        # contract. Production runs use SQLite so this stays empty;
        # degraded-mode runs at least have a working read path.
        self._in_memory_events: dict[str, list[EventV2]] = {}

        # 5. Initialize schema (idempotent)
        self._init_event_log_schema()

        # S1 门面化：event_log 的存储 SQL 收口到 PlatformStore（唯一写入者，
        # 不变量 7）。EventStore 保留全部编排（trace/cache/SSE/live/fanout/
        # projector flush/降级模式），只把 _persist/_replay/_next_seq 的
        # SQLite 访问转调行级 API。兼容单库模式指向既有 .session.db（S2 拆分）。
        self._ps = None
        if not isinstance(self._backend, InMemoryStore):
            from ..storage.platform_store import PlatformStore

            self._ps = PlatformStore(db_path=self._db_path)

    def _init_event_log_schema(self) -> None:
        if isinstance(self._backend, InMemoryStore):
            # In-memory store doesn't persist; event_log lives only in cache
            return
        conn = self._backend._ensure_conn()  # type: ignore[attr-defined]
        from ..storage.event_schema import (
            ensure_event_log_schema,
            migrate_event_log_unique,
        )

        # P0-1 A1+A3+A4: create / reconcile the table, then upgrade the
        # UNIQUE constraint for fork-aware seq spaces. Both are idempotent;
        # fresh DBs go straight to the new schema.
        ensure_event_log_schema(conn)
        migrate_event_log_unique(conn)

    @property
    def is_degraded(self) -> bool:
        return self._degraded

    @property
    def backend(self):
        return self._backend

    # ── Public API ────────────────────────────────────────────────

    # ── emit helpers (P2-2 extraction, docs/p2-c901-final-design.md) ──

    def _inject_trace_context(self, data: dict[str, Any]) -> dict[str, Any]:
        """C3: merge trace_id/session_id/study_id/round_num from ContextVars.

        Keys already present in ``data`` win. Best-effort: any error
        leaves the data untouched.
        """
        merged = dict(data)
        try:
            from strategy_research.foundation.observability.trace import (
                _round_num,
                _study_id,
                _trace_id,
            )
            from strategy_research.foundation.observability.trace import (
                _session_id as _ctx_session,
            )
            if "trace_id" not in merged:
                tid = _trace_id.get()
                if tid:
                    merged["trace_id"] = tid
            if "session_id" not in merged:
                sid = _ctx_session.get()
                if sid:
                    merged["session_id"] = sid
            if "study_id" not in merged:
                stid = _study_id.get()
                if stid:
                    merged["study_id"] = stid
            if "round_num" not in merged:
                rn = _round_num.get()
                if rn is not None:
                    merged["round_num"] = rn
        except Exception:  # noqa: BLE001 — best-effort
            pass
        return merged

    def _create_and_persist(
        self,
        session_id: str,
        event_type: str,
        data: dict[str, Any],
        parent_event_id: str | None,
        branch_id: str,
    ) -> EventV2:
        """Assign the next seq, build the EventV2, persist under the backend lock.

        Persist failures are logged and swallowed — live subscribers
        must still receive the event.
        """
        with self._backend._lock if hasattr(self._backend, "_lock") else _noop_cm():  # type: ignore[attr-defined]
            seq = self._next_seq(session_id)
            event = EventV2.create(
                aggregate_id=session_id,
                seq=seq,
                type=event_type,
                data=data,
                parent_event_id=parent_event_id,
                branch_id=branch_id,
            )
            try:
                self._persist(event)
            except Exception as exc:
                logger.exception("event_log persist failed: %s", exc)
                # Continue — at least push to live subscribers
                pass
        return event

    def _flush_projector(self, session_id: str, event_type: str) -> None:
        """Best-effort Projector.flush() when flush_to_messages is on."""
        if not (self._flush_to_messages and self._should_flush(event_type)):
            return
        try:
            proj = self._get_projector()
            if proj is not None:
                state, touched = proj.project_incremental(
                    session_id, collect_touched=True,
                )
                proj.flush(state, touched=touched)
        except Exception as exc:
            logger.warning(
                "Projector flush failed for session %s: %s",
                session_id, exc,
            )

    def emit(
        self,
        session_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
        *,
        parent_event_id: str | None = None,
        branch_id: str = "main",
    ) -> EventV2:
        """Emit event: SQLite → cache → SSE push + live subscribers.

        Same signature as legacy EventBus.emit(). Auto-assigns monotonic
        seq per session. When ``flush_to_messages=True``, also calls
        Projector.flush() to update messages + message_parts tables
        (best-effort — failures are logged and ignored).

        C3: automatically injects ``trace_id`` / ``session_id`` /
        ``study_id`` / ``round_num`` from the current trace ContextVar
        into every event's data dict (if not already present), so SSE
        clients can correlate events without explicit binding at each
        emit site.
        """
        merged = self._inject_trace_context(dict(data or {}))
        event = self._create_and_persist(
            session_id, event_type, merged, parent_event_id, branch_id,
        )

        # Cache update
        self._cache.append(session_id, event)

        # SSE push callback
        if self._sse_pusher:
            try:
                self._sse_pusher(session_id, event)
            except Exception as exc:
                logger.warning("SSE push failed: %s", exc)

        # Live subscribers
        self._broadcast_live(session_id, event)

        # Fan out to the parent study channel (round sessions only):
        # SSE + live mirroring, no event_log/cache/projector writes.
        self._fan_out_parent(session_id, event)

        self._flush_projector(session_id, event_type)

        return event

    def publish(self, event: EventV2) -> None:
        """Lower-level publish: takes a pre-built EventV2.

        C3: injects trace context into event data if not already present.
        """
        if not event.id:
            event.id = str(uuid.uuid4())
        if not event.time_created:
            event.time_created = time.time()
        # C3: inject trace context
        try:
            from strategy_research.foundation.observability.trace import (
                _round_num,
                _study_id,
                _trace_id,
            )
            from strategy_research.foundation.observability.trace import (
                _session_id as _ctx_session,
            )
            if "trace_id" not in event.data:
                tid = _trace_id.get()
                if tid:
                    event.data["trace_id"] = tid
            if "session_id" not in event.data:
                sid = _ctx_session.get()
                if sid:
                    event.data["session_id"] = sid
            if "study_id" not in event.data:
                stid = _study_id.get()
                if stid:
                    event.data["study_id"] = stid
            if "round_num" not in event.data:
                rn = _round_num.get()
                if rn is not None:
                    event.data["round_num"] = rn
        except Exception:  # noqa: BLE001 — best-effort
            pass
        with self._backend._lock if hasattr(self._backend, "_lock") else _noop_cm():  # type: ignore[attr-defined]
            try:
                self._persist(event)
            except Exception:
                logger.exception("event_log persist failed (publish)")
        self._cache.append(event.aggregate_id, event)
        if self._sse_pusher:
            try:
                self._sse_pusher(event.aggregate_id, event)
            except Exception:
                pass
        self._broadcast_live(event.aggregate_id, event)
        self._fan_out_parent(event.aggregate_id, event)

    async def subscribe(
        self,
        session_id: str,
        *,
        keepalive_after: float | None = None,
    ) -> AsyncIterator[EventV2 | None]:
        """Async iterator: yield cache replay + live events as they arrive.

        Combines replay (catch up on missed events) + live (future events).
        Use ``break`` or ``CancelledError`` to stop.

        ``keepalive_after``（P0 修复之二）：空闲超过该秒数时 yield ``None``
        ——SSE 端点把它翻译成 comment 行心跳。**必须在 subscribe 内部做
        wait_for**：在端点层对 ``__anext__()`` 套 wait_for 会把
        CancelledError 注入生成器帧，直接杀死整条流（2026-10-31 教训）。
        取消 `queue.get()` 本身是安全的（无副作用，队列不损）。
        """
        # 1. Replay cache first
        cached = self._cache.get(session_id)
        if cached is not None:
            for ev in cached:
                yield ev

        # 2. Then replay any non-cached SQLite tail (cross-process writes)
        try:
            tail = self._replay(session_id)
            last_cache_seq = cached[-1].seq if cached else 0
            for ev in tail:
                if ev.seq > last_cache_seq:
                    yield ev
        except Exception:
            # Degraded: continue with live events only, but never
            # silently — a corrupted DB read must be distinguishable
            # from a genuinely empty tail.
            logger.exception("subscribe: tail replay failed for %s", session_id)

        # 3. Subscribe to live events
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._live_lock:
            self._live_queues.setdefault(session_id, []).append(queue)
        try:
            while True:
                if keepalive_after is not None:
                    try:
                        event = await asyncio.wait_for(
                            queue.get(), timeout=keepalive_after,
                        )
                    except asyncio.TimeoutError:
                        yield None  # keepalive tick（端点翻译成心跳帧）
                        continue
                else:
                    event = await queue.get()
                yield event
        except asyncio.CancelledError:
            pass
        finally:
            with self._live_lock:
                queues = self._live_queues.get(session_id, [])
                if queue in queues:
                    queues.remove(queue)

    def replay(
        self,
        session_id: str,
        from_seq: int = 0,
        *,
        types: list[str] | tuple[str, ...] | None = None,
        branch_id: str | None = None,
        limit: int | None = None,
    ) -> list[EventV2]:
        """Return events for session with seq > from_seq.

        P0-1 B1 — optional filters pushed down to SQLite WHERE clauses:
        - ``types``: ``type IN (...)`` — primary win for Trajectory View
          (which only consumes ~5% of total events).
        - ``branch_id``: ``branch_id = ?`` — A4 fork-aware scoping.
        - ``limit``: row cap (SQLite returns at most this many events).
        """
        return self._replay(
            session_id,
            from_seq=from_seq,
            types=types,
            branch_id=branch_id,
            limit=limit,
        )

    def last_seq(self, session_id: str) -> int:
        """Return the highest seq for session (or 0)."""
        return self._next_seq(session_id) - 1

    def fork(
        self,
        session_id: str,
        at_seq: int,
        *,
        new_session_id: str | None = None,
    ) -> tuple[str, int]:
        """Copy events ``[0, at_seq]`` of ``session_id`` into a new session.

        P0-1 C1. The new session starts its own seq space at 1 (the source
        session's seq numbers are not preserved). Branch is always
        ``"main"`` — multi-branch forks land in Phase D. Returns
        ``(new_session_id, copied_event_count)``.

        Raises ``ValueError`` for invalid ``at_seq`` or pre-existing
        ``new_session_id``. Operates under the backend lock so concurrent
        emits on the source can't tear the snapshot mid-copy.
        """
        if at_seq <= 0:
            raise ValueError(
                f"at_seq must be positive, got {at_seq}"
            )

        if isinstance(self._backend, InMemoryStore):
            raise RuntimeError(
                "fork() requires a SQLite backend; InMemoryStore is "
                "degraded mode and cannot host forkable sessions."
            )

        max_seq = self.last_seq(session_id)
        if at_seq > max_seq:
            raise ValueError(
                f"at_seq={at_seq} exceeds last_seq={max_seq} for "
                f"session {session_id!r}"
            )

        new_sid = new_session_id or f"{session_id}-fork-{uuid.uuid4().hex[:8]}"
        if self.last_seq(new_sid) > 0:
            raise ValueError(
                f"new_session_id {new_sid!r} already has events"
            )

        # S1 门面化：复制 SQL 收口到 PlatformStore.copy_prefix（新 seq 空间
        # 1..N、行 id 重新生成、锁内快照——语义与原实现逐项一致）。
        copied = self._ps.copy_prefix(session_id, at_seq, new_sid)
        return new_sid, int(copied)

    def count(self, session_id: str | None = None) -> int:
        """Total events (optionally filtered by session)."""
        try:
            if not isinstance(self._backend, InMemoryStore) and self._ps is not None:
                return self._ps.count_events(session_id)
            else:
                # InMemoryStore
                data = getattr(self._backend, "_data", {})
                if session_id is None:
                    return sum(len(v) for v in data.values())
                return len(data.get(session_id, []))
        except Exception:
            logger.exception("count: read failed for %s", session_id)
            return 0

    # ── Private helpers ───────────────────────────────────────────

    def _persist(self, event: EventV2) -> None:
        if isinstance(self._backend, InMemoryStore):
            # P0-1 B3: degraded-mode fallback keeps EventV2 in a
            # per-process dict so _replay() can serve the same contract
            # (from_seq / types / branch_id / limit) without SQLite.
            self._in_memory_events.setdefault(
                event.aggregate_id, []
            ).append(event)
            return
        # S1 门面化：行组装 + seq 原子分配都在 PlatformStore（唯一写入者）。
        # 以库分配的 seq 为准回写 EventV2（与 _create_and_persist 预分配值
        # 可能不同——两锁窗口内的并发插入以库为准）。
        row = event.to_row()
        row["branch_id"] = event.branch_id
        assigned = self._ps.append_event_row(row)
        event.seq = assigned

    def _replay(
        self,
        session_id: str,
        from_seq: int = 0,
        *,
        types: list[str] | tuple[str, ...] | None = None,
        branch_id: str | None = None,
        limit: int | None = None,
    ) -> list[EventV2]:
        try:
            if not isinstance(self._backend, InMemoryStore) and self._ps is not None:
                # S1 门面化：WHERE/ORDER 语义在 PlatformStore.replay_rows
                # （与旧 SQL 逐项一致），这里只做行 → EventV2 映射。
                # 以当前 _backend 为准（测试可能在构造后替换为 InMemoryStore）。
                rows = self._ps.replay_rows(
                    session_id,
                    from_seq=from_seq,
                    types=types,
                    branch_id=branch_id,
                    limit=limit,
                )
                # R4 补漏：回放必须校验协议版本。此前这条路径直接跳过
                # check_event_version，于是「PROTOCOL_VERSION 是唯一来源、
                # 回放遇不匹配即拒」只对 PlatformStore.replay() 成立，
                # 而 EventStore（真正的回放入口）可以静默读入不兼容版本。
                from strategy_research.foundation.protocol import (
                    check_event_version,
                )

                for row in rows:
                    check_event_version(row.get("schema_v"))
                return [EventV2.from_row(r) for r in rows]
            else:
                # InMemoryStore — apply the same filters in Python so the
                # contract is consistent across backends. ``_persist``
                # mirrors every EventV2 into ``_in_memory_events`` so
                # _replay has the same shape as the SQLite path
                # (events carry .seq / .type / .branch_id).
                events = [
                    ev for ev in self._in_memory_events.get(session_id, [])
                    if ev.seq > from_seq
                ]
                if types:
                    type_set = set(types)
                    events = [ev for ev in events if ev.type in type_set]
                if branch_id is not None:
                    events = [ev for ev in events if ev.branch_id == branch_id]
                events.sort(key=lambda ev: ev.seq)
                if limit is not None:
                    events = events[:limit]
                return events
        except ProtocolVersionError:
            # 版本不匹配**不能**降级成「没有历史」。吞掉它等于把「数据损坏」
            # 伪装成「这台机器上还没有事件」，调用方会静默按空历史继续跑，
            # 用不兼容的语义重建状态。必须穿透到调用方（P2.1）。
            raise
        except Exception:
            # 其他读取失败（DB 打不开、行损坏）仍按既有契约降级为 []：
            # callers treat [] as "no history", which is indistinguishable
            # from a corrupt/unreadable DB unless we log. Keep the contract
            # ([]) but make the failure visible.
            logger.exception("_replay: read failed for %s", session_id)
            return []

    def _next_seq(self, session_id: str) -> int:
        try:
            if not isinstance(self._backend, InMemoryStore) and self._ps is not None:
                # S1 门面化：预览语义（下一 seq）。真实分配在
                # append_event_row 内原子完成——并发窗口内可能偏大，
                # 以 persist 回写为准。
                return self._ps.latest_seq(session_id) + 1
            else:
                # InMemoryStore — count from the EventV2 mirror that
                # ``_persist`` writes, not the backend's Message dict
                # (which holds projected messages, not raw events).
                events = self._in_memory_events.get(session_id, [])
                return max((ev.seq for ev in events), default=0) + 1
        except Exception:
            return 1

    def _broadcast_live(self, session_id: str, event: EventV2) -> None:
        with self._live_lock:
            queues = list(self._live_queues.get(session_id, []))
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Live queue full for session %s", session_id)

    def _fan_out_parent(self, session_id: str, event: EventV2) -> None:
        """Mirror a round-session event to its parent study channel.

        Only SSE + live subscribers — the event_log row and projector
        state remain bound to ``study:{id}:round:{n}`` so per-round
        replay stays coherent. Best-effort: fan-out failures never
        break the primary emit path.
        """
        parent = parent_study_channel(session_id)
        if parent is None:
            return
        if self._sse_pusher is not None:
            try:
                self._sse_pusher(parent, event)
            except Exception as exc:  # noqa: BLE001
                logger.warning("SSE parent fan-out failed (%s): %s", parent, exc)
        self._broadcast_live(parent, event)

    def _get_projector(self):
        """Lazy-import Projector to avoid circular imports at startup."""
        if self._projector is None and self._flush_to_messages:
            try:
                from strategy_research.api.session.projector import Projector
                self._projector = Projector(self._db_path)
            except Exception as exc:
                logger.warning("Failed to create Projector: %s", exc)
                return None
        return self._projector

    def _should_flush(self, event_type: str) -> bool:
        """Determine if an event should trigger a projector flush."""
        boundary_types = {
            "message_received",
            "assistant_message",
            "compact",
            "compact.ended",
            # Each LLM iteration boundary persists in-flight responses
            # so a refresh during a long agent run still shows the
            # completed iterations (not just finalized messages).
            "iter_start",
        }
        return event_type in boundary_types

    def close(self) -> None:
        """Release the SQLite backend handle (W1-8).

        EvenStore 持有 ``SQLiteStore`` 的一条长连接；Windows 上未关闭的句柄
        会让临时目录无法删除（WinError 32），也会在进程内跨用例污染。
        best-effort：重复调用安全，degraded（InMemoryStore）后端同样支持。
        """
        backend = getattr(self, "_backend", None)
        close = getattr(backend, "close", None)
        if close is not None:
            try:
                close()
            except Exception as exc:  # noqa: BLE001 — 释放尽力而为
                logger.debug("EventStore.close failed: %s", exc)
        sqlite_store = getattr(self, "_sqlite_store", None)
        if sqlite_store is not None and sqlite_store is not backend:
            try:
                sqlite_store.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("EventStore.close (sqlite_store) failed: %s", exc)

    def health_report(self) -> dict[str, Any]:
        # P0-1 B4: surface the SessionCache hit rate so operators can
        # tell whether the LRU cap is sized for the working set.
        stats = self._cache.stats
        return {
            "event_store": {
                "degraded": self.is_degraded,
                "backend": type(self._backend).__name__,
                "live_subscribers": sum(
                    len(q) for q in self._live_queues.values()
                ),
                "cache_session_count": self._cache.session_count,
                "cache_hits": stats.hits,
                "cache_misses": stats.misses,
                "cache_hit_rate": stats.hit_rate,
                "cache_evictions": stats.evictions,
                "cache_invalidations": stats.invalidations,
            },
        }


class _noop_cm:
    """No-op context manager for backends without _lock attr."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


# Channel naming: round sessions are ``study:{study_id}:round:{n}``.
# The StudyDetailPage subscribes to the bare study channel via
# /api/chat/events — round-scoped agent events must be fanned out to
# that parent channel (SSE + live subscribers only; event_log stays
# scoped to the round session so replay/projector semantics are kept).
_ROUND_CHANNEL_RE = re.compile(r"^study:(?P<sid>[^:]+):round:\d+$")


def parent_study_channel(session_id: str) -> str | None:
    """Return the parent study channel for a round session id, else None."""
    m = _ROUND_CHANNEL_RE.match(session_id)
    return f"study:{m.group('sid')}" if m else None


class EventStoreFactory:
    """DEPRECATED: use EventStore directly. Kept for backward compat in tests only."""
    pass


__all__ = [
    "EventStore",
    "EventV2",
    "parent_study_channel",
]
