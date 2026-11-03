"""HangingEventsStore — 卡死防护事件持久化。

每个事件（LLM 墙钟超时、回测日志停滞、agent no_progress、
熔断器 OPEN、watchdog 强制中断）写入 ``hanging_events`` 表
（与 ``studies`` 同文件，goals.db）。只读查询用于：
- ``/api/study/_internal/dump`` 的 24h 事件计数
- ``/api/admin/hangs/report`` 的聚合报告

写入点是各防护层主动调用 ``record()``；进程重启后数据保留。
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from ..storage.sqlite import resolve_db_path

_DB_PATH_ENV = "QUANTNODES_RESEARCH_GOAL_DB_PATH"

# 与 scheduler watchdog / agent 熔断器共用的事件种类
EVENT_WALLCLOCK = "wallclock_timeout"
EVENT_LOG_STALL = "log_stall"
EVENT_NO_PROGRESS = "no_progress"
EVENT_CIRCUIT_OPEN = "circuit_breaker_open"
EVENT_WATCHDOG = "watchdog_interrupt"
EVENT_CHAT_STALL = "chat_attempt_stall"  # C1: chat attempt timed out

_ALL_EVENT_TYPES = frozenset({
    EVENT_WALLCLOCK,
    EVENT_LOG_STALL,
    EVENT_NO_PROGRESS,
    EVENT_CIRCUIT_OPEN,
    EVENT_WATCHDOG,
    EVENT_CHAT_STALL,
})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS hanging_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT NOT NULL,
    study_id    TEXT,
    session_id  TEXT,
    detail      TEXT,
    created_at  REAL NOT NULL,
    trace_id    TEXT,
    round_num   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_hanging_events_type_time
    ON hanging_events(event_type, created_at);
CREATE INDEX IF NOT EXISTS idx_hanging_events_session
    ON hanging_events(session_id, created_at);
"""

# 旧库升级：trace_id / round_num 列（2026-11-02 P0.5，幂等 ALTER）。
_MIGRATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("trace_id", "TEXT"),
    ("round_num", "INTEGER"),
)


class HangingEventsStore:
    """SQLite store for hanging-protection events.

    Mirrors ``StudyStore``'s connection ownership: one connection per
    instance, usable as a context manager. Safe for concurrent access
    via an internal RLock (writes are short).
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = (
            Path(db_path) if db_path is not None
            else resolve_db_path("goals.db", _DB_PATH_ENV)
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._closed = False
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate_columns()
            self._conn.commit()

    def _migrate_columns(self) -> None:
        """Idempotent column migration for pre-trace databases."""
        existing = {
            r["name"] for r in
            self._conn.execute("PRAGMA table_info(hanging_events)").fetchall()
        }
        for col, col_type in _MIGRATION_COLUMNS:
            if col not in existing:
                self._conn.execute(
                    f"ALTER TABLE hanging_events ADD COLUMN {col} {col_type}"
                )

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                # 置 None：让「已关闭」可被调用方观测（也防后续误用拿到
                # 已关闭的连接）。_get_conn 在 _conn 为 None 时会重开。
                self._conn = None
                self._closed = True

    def __enter__(self) -> "HangingEventsStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ── writes ──────────────────────────────────────────────────────

    def record(
        self,
        event_type: str,
        *,
        study_id: str | None = None,
        session_id: str | None = None,
        detail: str | None = None,
        trace_id: str | None = None,
        round_num: int | None = None,
    ) -> None:
        """Append one hanging-protection event. Never raises on write
        failure (observability must not break the main path).

        ``trace_id`` / ``round_num`` default to the current ContextVar
        trace context (same join key as logs / event_log), so callers
        inside a ``bind_trace`` scope get the columns for free.
        """
        if event_type not in _ALL_EVENT_TYPES:
            return
        if self._closed:
            return  # closed — best-effort, observability must not raise
        if trace_id is None or round_num is None:
            try:
                from .trace import get_trace_context

                ctx = get_trace_context()
            except Exception:  # noqa: BLE001 — trace 不可用不阻断
                ctx = {}
            if trace_id is None:
                trace_id = ctx.get("trace_id")
            if round_num is None:
                round_num = ctx.get("round_num")
        try:
            with self._lock:
                with self._conn:
                    self._conn.execute(
                        "INSERT INTO hanging_events "
                        "(event_type, study_id, session_id, detail, created_at, "
                        "trace_id, round_num) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (event_type, study_id, session_id, detail, time.time(),
                         trace_id, round_num),
                    )
        except sqlite3.Error:
            pass  # best-effort

    # ── queries ─────────────────────────────────────────────────────

    def count_since(
        self,
        *,
        session_id: str | None = None,
        study_id: str | None = None,
        hours: float = 24,
    ) -> dict[str, int]:
        """Count events of each type in the last ``hours`` (default 24h)."""
        since = time.time() - hours * 3600
        params: list[Any] = [since]
        sql = "SELECT event_type, COUNT(*) AS n FROM hanging_events " \
              "WHERE created_at >= ?"
        if session_id:
            sql += " AND session_id = ?"
            params.append(session_id)
        if study_id:
            sql += " AND study_id = ?"
            params.append(study_id)
        sql += " GROUP BY event_type"
        out = {t: 0 for t in _ALL_EVENT_TYPES}
        try:
            with self._lock:
                rows = self._conn.execute(sql, params).fetchall()
        except sqlite3.Error:
            return out
        for r in rows:
            out[r["event_type"]] = r["n"]
        return out

    def report(
        self,
        *,
        hours: float = 24,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Aggregate report for the ops runbook.

        Returns:
            {
              "window_hours": 24,
              "total_events": N,
              "by_type": {type: n, ...},
              "by_study": [{"study_id": ..., "count": n}, ...],
              "recent": [ {event_type, study_id, session_id, detail,
                           created_at, created_at_iso}, ... ],
            }
        """
        since = time.time() - hours * 3600
        out: dict[str, Any] = {
            "window_hours": hours,
            "total_events": 0,
            "by_type": {t: 0 for t in _ALL_EVENT_TYPES},
            "by_study": [],
            "recent": [],
        }
        try:
            with self._lock:
                total = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM hanging_events WHERE created_at >= ?",
                    (since,),
                ).fetchone()["n"]
                by_type = self._conn.execute(
                    "SELECT event_type, COUNT(*) AS n FROM hanging_events "
                    "WHERE created_at >= ? GROUP BY event_type",
                    (since,),
                ).fetchall()
                by_study = self._conn.execute(
                    "SELECT COALESCE(study_id, '<none>') AS study_id, "
                    "COUNT(*) AS n FROM hanging_events WHERE created_at >= ? "
                    "GROUP BY study_id ORDER BY n DESC LIMIT ?",
                    (since, limit),
                ).fetchall()
                recent = self._conn.execute(
                    "SELECT event_type, study_id, session_id, detail, created_at, "
                    "trace_id, round_num "
                    "FROM hanging_events WHERE created_at >= ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (since, limit),
                ).fetchall()
        except sqlite3.Error:
            return out

        out["total_events"] = total
        for r in by_type:
            out["by_type"][r["event_type"]] = r["n"]
        out["by_study"] = [
            {"study_id": r["study_id"], "count": r["n"]} for r in by_study
        ]
        import datetime as _dt

        def _iso(ts: float) -> str:
            return _dt.datetime.fromtimestamp(
                ts, tz=_dt.timezone.utc,
            ).isoformat()

        out["recent"] = [
            {
                "event_type": r["event_type"],
                "study_id": r["study_id"],
                "session_id": r["session_id"],
                "detail": r["detail"],
                "created_at": r["created_at"],
                "created_at_iso": _iso(r["created_at"]),
                "trace_id": r["trace_id"],
                "round_num": r["round_num"],
            }
            for r in recent
        ]
        return out

    def list_recent(
        self,
        *,
        study_id: str | None = None,
        hours: float = 24,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Recent events (newest first), optionally filtered by study.

        Returns rows as dicts with iso timestamps — the per-study view
        the UI draws its badge / panel from.
        """
        since = time.time() - hours * 3600
        params: list[Any] = [since]
        sql = (
            "SELECT event_type, study_id, session_id, detail, created_at, "
            "trace_id, round_num "
            "FROM hanging_events WHERE created_at >= ?"
        )
        if study_id:
            sql += " AND study_id = ?"
            params.append(study_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        import datetime as _dt

        def _iso(ts: float) -> str:
            return _dt.datetime.fromtimestamp(
                ts, tz=_dt.timezone.utc,
            ).isoformat()

        try:
            with self._lock:
                rows = self._conn.execute(sql, params).fetchall()
        except sqlite3.Error:
            return []
        return [
            {
                "event_type": r["event_type"],
                "study_id": r["study_id"],
                "session_id": r["session_id"],
                "detail": r["detail"],
                "created_at": r["created_at"],
                "created_at_iso": _iso(r["created_at"]),
                "trace_id": r["trace_id"],
                "round_num": r["round_num"],
            }
            for r in rows
        ]

    def clear(self, *, hours: float | None = None) -> int:
        """Delete events (all or older than ``hours``). Returns rows removed."""
        if self._closed:
            return 0  # closed — best-effort
        try:
            with self._lock:
                with self._conn:
                    if hours is None:
                        cur = self._conn.execute("DELETE FROM hanging_events")
                    else:
                        since = time.time() - hours * 3600
                        cur = self._conn.execute(
                            "DELETE FROM hanging_events WHERE created_at < ?",
                            (since,),
                        )
                    return cur.rowcount
        except sqlite3.Error:
            return 0


# ── module-level convenience (best-effort, no-throw) ────────────────


def record_event(
    event_type: str,
    *,
    study_id: str | None = None,
    session_id: str | None = None,
    detail: str | None = None,
    trace_id: str | None = None,
    round_num: int | None = None,
) -> None:
    """Write an event, swallowing any failure (observability is best-effort)."""
    try:
        with HangingEventsStore() as s:
            s.record(
                event_type,
                study_id=study_id,
                session_id=session_id,
                detail=detail,
                trace_id=trace_id,
                round_num=round_num,
            )
    except Exception:
        pass
