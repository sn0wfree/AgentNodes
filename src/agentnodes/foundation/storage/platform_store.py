"""PlatformStore —— 数据面唯一写入者（不变量 7）。

依据 docs/archive/2026-10/stage-3b-plan-20261026.md + platform-data-plane-discussion §9/§10。

存储布局（每 session 一自包含库 + 全局小库）：
    <data_dir>/sessions/<session_id>.sqlite   ← event_log + blobs
    <data_dir>/platform.sqlite               ← session_registry + artifact_registry
                                                + schema_migrations

写入者唯一：所有写操作经由 PlatformStore；其他模块不持有 sqlite 连接。
emit = 同一事务内写 event_log（不变量 7 的物理实现）。
事件永不删除（用户拍板）。月度归档是纯导出，主库不动。
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .event_schema import ensure_event_log_schema

logger = logging.getLogger(__name__)

# ── DDL ───────────────────────────────────────────────────
# event_log 使用 canonical schema（core/storage/event_schema.py，单一事实源）。
# S1 修正：PlatformStore 不再手写自己的 event_log DDL——那曾是文档警告的
# 「第四处 DDL 重复」。

_BLOBS_DDL = """
CREATE TABLE IF NOT EXISTS blobs (
    hash TEXT PRIMARY KEY,
    content BLOB NOT NULL,
    size INTEGER NOT NULL,
    created_at REAL NOT NULL
)
"""

_SESSION_REGISTRY_DDL = """
CREATE TABLE IF NOT EXISTS session_registry (
    session_id TEXT PRIMARY KEY,
    session_key TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    last_active REAL NOT NULL
)
"""

_ARTIFACT_REGISTRY_DDL = """
CREATE TABLE IF NOT EXISTS artifact_registry (
    artifact_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    uri TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    bytes INTEGER NOT NULL DEFAULT 0,
    schema_v TEXT NOT NULL DEFAULT '1',
    status TEXT NOT NULL DEFAULT 'registered',
    created_at REAL NOT NULL,
    created_by_run_id TEXT,
    parent_id TEXT,
    study_id TEXT,
    round_id TEXT
)
"""

_SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    domain TEXT NOT NULL,
    version INTEGER NOT NULL,
    applied_at REAL NOT NULL,
    PRIMARY KEY (domain, version)
)
"""


@dataclass(frozen=True)
class Event:
    seq: int
    ts: float
    kind: str
    data: dict[str, Any]
    run_id: str | None = None
    schema_v: str = "1"


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    kind: str
    uri: str
    sha256: str
    bytes: int
    schema_v: str = "1"
    status: str = "registered"
    created_at: float = 0.0
    created_by_run_id: str | None = None
    parent_id: str | None = None
    study_id: str | None = None
    round_id: str | None = None


class PlatformStore:
    """数据面唯一写入者（不变量 7）。

    持有全局库连接 + 按需缓存 per-session 库连接（LRU 简化为 dict，A1 改 LRU）。
    emit() 是唯一事件写入入口。store_blob() 是唯一大内容写入入口。
    """

    def __init__(self, data_dir: str | Path | None = None, *, db_path: str | Path | None = None) -> None:
        """两种模式（S1）：

        - ``data_dir``：P-2 目标形态，per-session 自包含文件
          （``<data_dir>/sessions/<session_id>.sqlite``）+ 全局 platform.sqlite。
        - ``db_path``：兼容单库模式——所有 session 共用一个既有文件
          （S1 指向现有 ``.quantnodes_agentnodes_session.db``，S2 切换）。
          全局库功能（session/artifact 登记）在此模式不可用。
        """
        if (data_dir is None) == (db_path is None):
            raise ValueError("PlatformStore: data_dir 与 db_path 必须二选一")
        self._db_path_mode = db_path is not None
        if self._db_path_mode:
            self._single_path = Path(db_path)
            self._single_path.parent.mkdir(parents=True, exist_ok=True)
            self._data_dir = None
            self._sessions_dir = None
            self._global_path = None
        else:
            self._data_dir = Path(data_dir)
            self._sessions_dir = self._data_dir / "sessions"
            self._sessions_dir.mkdir(parents=True, exist_ok=True)
            self._global_path = self._data_dir / "platform.sqlite"
            self._single_path = None
        self._global_conn: sqlite3.Connection | None = None
        self._session_conns: dict[str, sqlite3.Connection] = {}
        # F2：fanout 分支（跨线程）事件回流前的物理护栏——sqlite 连接
        # 不允许并发使用，全部公开方法串行化短事务（不变量 7 的物理形态）。
        self._lock = threading.Lock()
        self._init_global = False
        if not self._db_path_mode:
            self._ensure_global_tables()

    # ── 连接管理 ─────────────────────────────────────────

    def _get_global_conn(self) -> sqlite3.Connection:
        if self._db_path_mode:
            raise NotImplementedError(
                "PlatformStore(db_path=...) 兼容单库模式不含全局库"
                "（session/artifact 登记属 per-session 形态，S2 切换）"
            )
        if self._global_conn is None:
            conn = sqlite3.connect(str(self._global_path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._global_conn = conn
            self._ensure_global_tables()
        return self._global_conn

    def _get_session_conn(self, session_id: str) -> sqlite3.Connection:
        if self._db_path_mode:
            # 兼容单库：全部 session 共用一个文件连接。
            if "single" not in self._session_conns:
                conn = sqlite3.connect(str(self._single_path), check_same_thread=False)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")
                ensure_event_log_schema(conn)
                self._session_conns["single"] = conn
            return self._session_conns["single"]
        if session_id not in self._session_conns:
            path = self._sessions_dir / f"{session_id}.sqlite"
            conn = sqlite3.connect(str(path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
            ensure_event_log_schema(conn)
            self._ensure_session_tables(conn)
            self._session_conns[session_id] = conn
        return self._session_conns[session_id]

    def _ensure_global_tables(self) -> None:
        conn = self._get_global_conn()
        for ddl in (_SCHEMA_MIGRATIONS_DDL, _SESSION_REGISTRY_DDL, _ARTIFACT_REGISTRY_DDL):
            conn.execute(ddl)
        conn.commit()

    def _ensure_session_tables(self, conn: sqlite3.Connection) -> None:
        # event_log 由 ensure_event_log_schema 统一建（见 _get_session_conn）。
        conn.execute(_BLOBS_DDL)
        conn.commit()

    # ── P-1 事件流（唯一写入入口） ───────────────────────

    def emit(
        self,
        session_id: str,
        kind: str,
        data: dict[str, Any],
        *,
        run_id: str | None = None,
        schema_v: str | None = None,
        session_key: str | None = None,
        parent_event_id: str | None = None,
        branch_id: str = "main",
    ) -> Event:
        """追加事件到 canonical event_log（P-1 信封，S1）。

        seq 由 ``append_event_row`` 按 (aggregate_id, branch_id) 原子分配。
        schema_v 默认取 PROTOCOL_VERSION（R4：单一来源）。
        """
        if schema_v is None:
            from agentnodes.foundation.protocol import (
                PROTOCOL_VERSION,
            )

            schema_v = PROTOCOL_VERSION
        row = {
            "id": str(uuid.uuid4()),
            "aggregate_id": session_id,
            "type": kind,
            "data_json": json.dumps(data, ensure_ascii=False, default=str),
            "time_created": time.time(),
            "parent_event_id": parent_event_id,
            "branch_id": branch_id,
            "session_key": session_key,
            "run_id": run_id,
            "schema_v": schema_v,
        }
        with self._lock:
            conn = self._get_session_conn(session_id)
            self._append_event_row_locked(conn, row)
        return Event(
            seq=row["seq"],
            ts=row["time_created"],
            kind=kind,
            data=data,
            run_id=run_id,
            schema_v=schema_v,
        )

    def append_event_row(self, row: dict[str, Any]) -> int:
        """行级插入（EventStore 门面用，S1）。

        ``row`` 键 = canonical 列：id/aggregate_id/type/data_json/
        time_created/parent_event_id/branch_id/session_key/run_id/schema_v。
        ``seq`` 在此原子分配（MAX(seq)+1 per aggregate+branch，与写同锁），
        写回 row["seq"] 并返回。数据文件按 ``aggregate_id`` 选择。
        """
        aggregate = row["aggregate_id"]
        with self._lock:
            conn = self._get_session_conn(aggregate)
            self._append_event_row_locked(conn, row)
        return row["seq"]

    def _append_event_row_locked(self, conn: sqlite3.Connection, row: dict[str, Any]) -> None:
        """seq 分配 + INSERT（调用方必须已持锁）。"""
        cur = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM event_log "
            "WHERE aggregate_id = ? AND branch_id = ?",
            (row["aggregate_id"], row.get("branch_id") or "main"),
        )
        row["seq"] = (cur.fetchone()[0] or 0) + 1
        conn.execute(
            "INSERT INTO event_log (id, aggregate_id, seq, type, data_json, "
            "time_created, parent_event_id, branch_id, session_key, run_id, schema_v) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["id"], row["aggregate_id"], row["seq"], row["type"],
                row["data_json"], row["time_created"],
                row.get("parent_event_id"), row.get("branch_id") or "main",
                row.get("session_key"), row.get("run_id"),
                row.get("schema_v") or "1",
            ),
        )
        conn.commit()

    def copy_prefix(
        self, src_aggregate: str, at_seq: int, new_aggregate: str
    ) -> int:
        """复制 [0, at_seq] 事件到新 aggregate（EventStore.fork 用，S1）。

        新 seq 空间 1..N（源 seq 不保留），行 id 重新生成。锁内完成，
        并发 emit 不会撕裂快照。返回复制条数。
        """
        with self._lock:
            conn = self._get_session_conn(new_aggregate)
            cur = conn.execute(
                """
                INSERT INTO event_log
                    (id, aggregate_id, seq, type, data_json,
                     time_created, parent_event_id, branch_id,
                     session_key, run_id, schema_v)
                SELECT
                    lower(hex(randomblob(16))),
                    ?,
                    ROW_NUMBER() OVER (ORDER BY seq ASC),
                    type, data_json, time_created,
                    parent_event_id, branch_id, session_key, run_id, schema_v
                FROM event_log
                WHERE aggregate_id = ? AND seq <= ?
                """,
                (new_aggregate, src_aggregate, at_seq),
            )
            conn.commit()
            return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    def count_events(self, aggregate_id: str | None = None) -> int:
        """事件计数（可选按 aggregate；EventStore.count 用，S1）。

        per-session 模式下无参全局计数语义不成立（事件分散在每 session
        文件）——生产代码零调用，直接拒绝而非给出错误的 0。
        """
        if aggregate_id is None and not self._db_path_mode:
            raise NotImplementedError(
                "count_events(None) 需要 db_path 兼容单库模式"
                "（per-session 模式事件分散在每 session 文件）"
            )
        with self._lock:
            conn = self._get_session_conn(aggregate_id or "single-count")
            if aggregate_id is None:
                row = conn.execute("SELECT COUNT(*) FROM event_log").fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM event_log WHERE aggregate_id = ?",
                    (aggregate_id,),
                ).fetchone()
        return row[0] if row else 0

    def replay_rows(
        self,
        aggregate_id: str,
        from_seq: int = 0,
        *,
        types: list[str] | tuple[str, ...] | None = None,
        branch_id: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """按 aggregate 重放事件行（EventStore 门面用，S1）。

        WHERE aggregate_id = ? AND seq > ? [AND type IN …] [AND branch_id = ?]
        ORDER BY seq ASC [LIMIT ?]——与 EventStore 旧 _replay 语义逐项一致。
        """
        with self._lock:
            conn = self._get_session_conn(aggregate_id)
            clauses = ["aggregate_id = ?", "seq > ?"]
            params: list[Any] = [aggregate_id, from_seq]
            if types:
                placeholders = ",".join("?" for _ in types)
                clauses.append(f"type IN ({placeholders})")
                params.extend(types)
            if branch_id is not None:
                clauses.append("branch_id = ?")
                params.append(branch_id)
            sql = (
                "SELECT id, aggregate_id, seq, type, data_json, time_created, "
                "parent_event_id, branch_id, session_key, run_id, schema_v "
                f"FROM event_log WHERE {' AND '.join(clauses)} ORDER BY seq ASC"
            )
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
            cur = conn.execute(sql, params)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        return rows

    def replay(
        self,
        session_id: str,
        *,
        from_seq: int = 0,
        kinds: list[str] | None = None,
        limit: int = 1000,
        strict_version: bool = True,
    ) -> list[Event]:
        """按 session 从指定 seq 起重放事件（fold 用；兼容旧 API）。

        R4：``strict_version=True`` 时逐行校验 schema_v——不匹配即抛
        ProtocolVersionError（不猜跨版本语义）。需要读取历史旧版本做
        迁移时显式传 False。
        """
        from agentnodes.foundation.protocol import (
            check_event_version,
        )

        rows = self.replay_rows(
            session_id, from_seq=from_seq, types=kinds, limit=limit,
        )
        if strict_version:
            for r in rows:
                check_event_version(r["schema_v"])
        return [
            Event(
                seq=r["seq"], ts=r["time_created"], kind=r["type"],
                data=json.loads(r["data_json"]), run_id=r["run_id"],
                schema_v=r["schema_v"] or "1",
            )
            for r in rows
        ]

    def latest_seq(self, session_id: str) -> int:
        with self._lock:
            conn = self._get_session_conn(session_id)
            row = conn.execute(
                "SELECT MAX(seq) FROM event_log WHERE aggregate_id = ?",
                (session_id,),
            ).fetchone()
        return row[0] if row and row[0] is not None else 0

    # ── blob 去重 ────────────────────────────────────────

    def store_blob(self, session_id: str, content: bytes) -> str:
        """内容寻址去重：同哈希不重写，返回哈希。"""
        h = hashlib.sha256(content).hexdigest()
        with self._lock:
            conn = self._get_session_conn(session_id)
            conn.execute(
                "INSERT OR IGNORE INTO blobs (hash, content, size, created_at) "
                "VALUES (?, ?, ?, ?)",
                (h, content, len(content), time.time()),
            )
            conn.commit()
        return h

    def read_blob(self, session_id: str, hash_: str) -> bytes:
        with self._lock:
            conn = self._get_session_conn(session_id)
            row = conn.execute(
                "SELECT content FROM blobs WHERE hash = ?", (hash_,)
            ).fetchone()
        if row is None:
            raise KeyError(f"blob {hash_[:12]} not found")
        return row[0]

    # ── 会话注册（全局库） ───────────────────────────────

    def register_session(self, session_key: str) -> str:
        """惰性建会话：返回 session_id（已存在则复用并刷新 last_active）。"""
        with self._lock:
            conn = self._get_global_conn()
            row = conn.execute(
                "SELECT session_id FROM session_registry WHERE session_key = ?",
                (session_key,),
            ).fetchone()
            now = time.time()
            if row:
                session_id = row[0]
                conn.execute(
                    "UPDATE session_registry SET last_active = ? WHERE session_id = ?",
                    (now, session_id),
                )
            else:
                session_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO session_registry (session_id, session_key, created_at, last_active) "
                    "VALUES (?, ?, ?, ?)",
                    (session_id, session_key, now, now),
                )
            conn.commit()
            return session_id

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._get_global_conn()
            rows = conn.execute(
                "SELECT session_id, session_key, created_at, last_active "
                "FROM session_registry ORDER BY last_active DESC"
            ).fetchall()
        return [
            {"session_id": r[0], "session_key": r[1],
             "created_at": r[2], "last_active": r[3]}
            for r in rows
        ]

    def iter_session_ids(self) -> list[str]:
        """枚举有 event_log 的全部 session id（B2 fold 用）。

        不依赖 session_registry（惰性登记，可能为空）：
        per-session 模式扫 sessions/ 目录文件名；单库模式查 distinct。
        """
        ids: list[str] = []
        if self._db_path_mode:
            with self._lock:
                conn = self._get_session_conn("single")
                rows = conn.execute(
                    "SELECT DISTINCT aggregate_id FROM event_log"
                ).fetchall()
            ids = [str(r[0]) for r in rows if r[0]]
        else:
            if self._sessions_dir.exists():
                ids = sorted(p.stem for p in self._sessions_dir.glob("*.sqlite"))
        return ids

    # ── 工件登记（P-3） ──────────────────────────────────

    def register_artifact(self, artifact: Artifact) -> None:
        with self._lock:
            conn = self._get_global_conn()
            conn.execute(
                "INSERT OR REPLACE INTO artifact_registry "
                "(artifact_id, kind, uri, sha256, bytes, schema_v, status, "
                " created_at, created_by_run_id, parent_id, study_id, round_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    artifact.artifact_id, artifact.kind, artifact.uri,
                    artifact.sha256, artifact.bytes, artifact.schema_v,
                    artifact.status, artifact.created_at or time.time(),
                    artifact.created_by_run_id, artifact.parent_id,
                    artifact.study_id, artifact.round_id,
                ),
            )
            conn.commit()

    def list_artifacts(self, study_id: str | None = None) -> list[Artifact]:
        with self._lock:
            conn = self._get_global_conn()
            if study_id:
                rows = conn.execute(
                    "SELECT * FROM artifact_registry WHERE study_id = ? ORDER BY created_at",
                    (study_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM artifact_registry ORDER BY created_at"
                ).fetchall()
        return [
            Artifact(
                artifact_id=r[0], kind=r[1], uri=r[2], sha256=r[3],
                bytes=r[4], schema_v=r[5], status=r[6], created_at=r[7],
                created_by_run_id=r[8], parent_id=r[9],
                study_id=r[10], round_id=r[11],
            )
            for r in rows
        ]

    # ── 健康 ─────────────────────────────────────────────

    def quick_check(self) -> bool:
        with self._lock:
            conn = self._get_global_conn()
            result = conn.execute("PRAGMA quick_check").fetchone()
        return result[0] == "ok" if result else False

    def close(self) -> None:
        with self._lock:
            for conn in self._session_conns.values():
                conn.close()
            self._session_conns.clear()
            if self._global_conn:
                self._global_conn.close()
                self._global_conn = None
