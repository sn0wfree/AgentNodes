# -*- coding: utf-8 -*-
"""SQLite 后端基类（L4）—— 连接管理 + 健康检查 + 自愈。

**为什么拆出来**：原先 ``SQLiteStore``（kernel/agent/memory_manager.py, 190 行）
把两类职责糅在一起：

- **零会话语义的基建**：连接生命周期（WAL / busy_timeout）、
  ``PRAGMA integrity_check``、``.dump`` 自愈、句柄释放
- **会话 CRUD**：``ensure_session_row`` / ``list_messages`` /
  ``delete_session`` / ``list_recent_sessions``

前者对所有 SQLite 消费者都一样（事件库、genome 库、任何 store），后者
只有会话域才需要。放在同一类里导致 L3 的会话代码和 L4 的存储原语耦合，
也让「foundation 零业务语义」这条红线无法成立。

本模块只放前者。会话 CRUD 留在 ``kernel.agent.memory_manager.SQLiteStore``
（继承本类的 ``SQLiteBackend``）。

**依赖方向**：``memory_manager``(L3) → 本模块(L4)，合法下行。
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

#: WAL 写锁等待上限（毫秒）。
WAL_BUSY_TIMEOUT_MS = 5_000

#: 自愈（.dump + 重新导入）的单步超时。
AUTO_REPAIR_TIMEOUT_S = 30


def threading_lock() -> threading.RLock:
    """每-store 的连接锁（可重入：同一线程的嵌套 with 不死锁）。"""
    return threading.RLock()


class SQLiteBackend:
    """SQLite 连接管理 + 健康检查 + 自愈。

    子类负责 schema 与 CRUD；本类只管「怎么安全地拿到一个连接」。

    **schema 初始化走端口**：``_init_schema`` 调用
    ``foundation.storage.schema_port.ensure_schema``——由上层
    （L1 的 ``web_session_db``）在 import 时注册真实 DDL，本层不认识
    具体表结构。这避免了 L3/L4 反向依赖接口层。
    """

    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading_lock()
        self._healthy = False

    # ── 连接 ──────────────────────────────────────────────────────

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
            # 刻意不在此处开 foreign_keys：EventStore 会为「sessions 行尚不存在」
            # 的会话写 event_log，FK 强制的归属权在 web_session 那条连接上。
            self._init_schema(self._conn)
        return self._conn

    @staticmethod
    def _init_schema(conn: sqlite3.Connection) -> None:
        """委托给注册的 schema 初始化器（见 schema_port 模块）。"""
        from agentnodes.foundation.storage.schema_port import ensure_schema

        ensure_schema(conn)

    def close(self) -> None:
        """释放连接（幂等；Windows 上未关闭的句柄会让临时目录无法删除）。"""
        if self._conn:
            try:
                self._conn.close()
            except Exception as exc:  # noqa: BLE001 — 释放尽力而为
                logger.debug("SQLite close failed: %s", exc)
            finally:
                self._conn = None

    # ── 健康检查 / 自愈 ───────────────────────────────────────────

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
        except Exception as exc:  # noqa: BLE001
            logger.error("SQLite health_check failed: %s", exc)
            self._healthy = False
            return False

    def auto_repair(self) -> bool:
        """Recover corrupted DB via sqlite3 .dump + re-import."""
        try:
            backup = self._db_path.with_suffix(f".corrupt.{int(time.time())}.db")
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
        except Exception as exc:  # noqa: BLE001
            logger.exception("SQLite auto-repair failed: %s", exc)
            return False

    @property
    def db_path(self) -> Path:
        return self._db_path


__all__ = [
    "AUTO_REPAIR_TIMEOUT_S",
    "SQLiteBackend",
    "WAL_BUSY_TIMEOUT_MS",
    "threading_lock",
]
