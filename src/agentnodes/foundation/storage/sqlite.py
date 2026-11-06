"""Shared SQLite store infrastructure (P2: storage-layer unification).

Single home for the connection/transaction/JSON/id boilerplate that was
copy-pasted across ``GoalStore``, ``StudyStore``, ``HypothesisStore`` and
friends:

- ``now_iso`` / ``new_id`` / ``json_dumps`` / ``json_loads`` — column helpers
- ``resolve_db_path`` — env-override or ``~/.agentnodes/<db>``
- ``connect`` — PRAGMA-tuned connection (WAL, busy_timeout, FK, NORMAL)
- ``synchronized`` — per-store RLock decorator for shared connections
- ``write_transaction`` — ``BEGIN IMMEDIATE`` write transaction
- ``table_columns`` / ``ensure_column`` / ``user_version`` / ``set_user_version``
  — lightweight migration primitives
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

F = TypeVar("F", bound=Callable)

DEFAULT_DATA_DIR = Path.home() / ".agentnodes"

#: 重命名前的数据目录（2026-11-05 批次 9 之前叫 ``~/.quantnodes``）。
#: 首次访问时由 :func:`ensure_data_dir` 做一次性迁移。
LEGACY_DATA_DIR = Path.home() / ".quantnodes"

#: 迁移标记文件——存在即表示已迁移过（避免重复拷贝/覆盖用户新数据）。
_MIGRATED_MARKER = ".migrated-from-quantnodes"

#: 进程内锁：pytest -n 4 的 worker 虽各自独立进程，但同进程内的多线程
#: （asyncio worker 线程）仍会并发进入 ensure_data_dir。
import threading as _threading

_ENSURE_LOCK = _threading.Lock()


def ensure_data_dir(base: Path | None = None) -> Path:
    """Return the data dir, migrating ``~/.quantnodes`` on first use.

    迁移规则（保守，不丢数据）：

    1. 目标是 ``~/.agentnodes``（可由 ``AN_DATA_DIR`` 覆盖）。
    2. 若目标已有标记文件 → 直接返回（已迁移过）。
    3. 若目标不存在且旧目录存在 → 整树复制（``copytree``），**旧目录保留**
       作为备份（不删，让用户自己决定）。
    4. 两者都不存在 → 创建目标目录。

    **并发安全**（2026-11-05）：``pytest -n 4`` 下多个 worker 会同时进入本
    函数，``copytree`` 与 ``mkdir`` 都可能有竞态。用进程内锁 + 幂等
    ``exist_ok`` + 迁移标记实现「先到者建、后到者等」；跨进程竞态由
    ``mkdir(exist_ok=True)`` 与标记文件的存在性判断兜底。

    失败不抛错：数据目录不可写时退化为「不迁移、返回目标路径」，
    由具体 store 在写入时报错，而不是让 import 期崩掉。
    """
    target = base or Path(
        os.environ.get("AN_DATA_DIR", "").strip() or DEFAULT_DATA_DIR
    )
    target = Path(target).expanduser()

    with _ENSURE_LOCK:
        try:
            if (target / _MIGRATED_MARKER).exists():
                return target
            if target.exists():
                # 目录已存在（可能是别的 worker 刚建的）——不再复制，
                # 只补标记，避免覆盖正在写入的数据。
                _write_marker(target)
                return target
            if LEGACY_DATA_DIR.exists():
                import shutil

                shutil.copytree(LEGACY_DATA_DIR, target, dirs_exist_ok=True)
                _write_marker(target)
                import logging

                logging.getLogger(__name__).info(
                    "data dir migrated: %s -> %s (legacy kept as backup)",
                    LEGACY_DATA_DIR, target,
                )
            target.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # 不可写就交给调用方报错
    return target


def _write_marker(target: Path) -> None:
    """写迁移标记（幂等；并发下写失败不致命）。"""
    marker = target / _MIGRATED_MARKER
    if marker.exists():
        return
    try:
        marker.write_text(
            "data dir ready (2026-11-05 batch 9 rename)\n"
            "if migrated from ~/.quantnodes, the old dir is kept as backup\n",
            encoding="utf-8",
        )
    except OSError:
        pass


# ── Column / value helpers ────────────────────────────────────────


def now_iso() -> str:
    """UTC ISO-8601 timestamp for ledger columns."""
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    """Random 12-hex id with a domain prefix (e.g. ``goal_ab12cd34ef56``)."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def json_dumps(value: object) -> str:
    """Canonical JSON encoding for ledger JSON columns."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads(value: str | None, default: object) -> object:
    """Decode a ledger JSON column; missing/empty → ``default``."""
    if not value:
        return default
    return json.loads(value)


# ── Path / connection ─────────────────────────────────────────────


#: A5 (评审 F10) — 目标台账的唯一环境变量名。
#:
#: 历史上有三个名字并存（``AN_GOAL_DB_PATH`` 出现在 L1 的 app/server，
#: ``QUANTNODES_RESEARCH_GOAL_DB_PATH`` 出现在 apps/goal，旧测试用
#: ``STRATEGY_RESEARCH_GOAL_DB``），导致 ``--goal-db`` 只影响 API 路由、
#: 不影响 agent 工具 / ticket / 调度器 —— 同一个进程里两个 goals.db。
#: 现在统一为 ``AGENTNODES_GOAL_DB_PATH``（与 kernel/foundation 多数派一致），
#: 旧名保留为**只读兜底**，避免已部署环境的配置静默失效。
GOAL_DB_ENV = "AGENTNODES_GOAL_DB_PATH"

#: 目标台账的旧环境变量名（按优先级，新名永远先查）。
GOAL_DB_LEGACY_ENVS = (
    "AN_GOAL_DB_PATH",
    "QUANTNODES_RESEARCH_GOAL_DB_PATH",
    "STRATEGY_RESEARCH_GOAL_DB",
)


def goal_db_env_value() -> str:
    """Return the raw goal-DB env override (canonical name first).

    Empty string when nothing is configured. Exposed so L1 entrypoints
    (:mod:`agentnodes.interfaces.api.app`, CLI ``--goal-db``) read the
    same name/priority as :func:`resolve_db_path` — no second fact source.
    """
    for name in (GOAL_DB_ENV, *GOAL_DB_LEGACY_ENVS):
        raw = os.environ.get(name, "").strip()
        if raw:
            return raw
    return ""


def resolve_db_path(db_name: str, env_var: str) -> Path:
    """Resolve a store's DB path: env override, else ``~/.agentnodes/<db>``.

    For the goal ledger (``goals.db``) the canonical
    ``AGENTNODES_GOAL_DB_PATH`` is checked first, then the historical
    aliases in :data:`GOAL_DB_LEGACY_ENVS`, then the ``env_var`` the caller
    passed (A5 统一：新名优先、旧名兜底). Every other store keeps the
    caller-supplied single env var.
    """
    candidates: list[str] = []
    if db_name == "goals.db":
        candidates = [GOAL_DB_ENV, *GOAL_DB_LEGACY_ENVS]
        if env_var not in candidates:
            candidates.append(env_var)
    else:
        candidates = [env_var]

    for name in candidates:
        raw_path = os.environ.get(name, "").strip()
        if raw_path:
            return Path(raw_path).expanduser()
    return ensure_data_dir() / db_name


def connect(path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with the standard PRAGMA tuning.

    - ``check_same_thread=False`` so the shared connection can be used
      from the asyncio event loop / worker threads (callers serialize
      access with ``synchronized``).
    - WAL + ``synchronous=NORMAL`` for read/write concurrency.
    - ``foreign_keys=ON`` + ``busy_timeout`` for cross-connection safety.
    """
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# ── Threading / transactions ──────────────────────────────────────


def synchronized(method: F) -> F:
    """Serialize access to a shared SQLite connection (expects ``self._lock``)."""

    @wraps(method)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:  # type: ignore[misc]
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


@contextmanager
def write_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """``BEGIN IMMEDIATE`` write transaction with commit/rollback.

    ``BEGIN IMMEDIATE`` takes the write lock up front, which avoids
    ``database is locked`` races when multiple processes/connections
    share the same SQLite file.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ── Migration primitives ──────────────────────────────────────────


def user_version(conn: sqlite3.Connection) -> int:
    """Read ``PRAGMA user_version`` (0 when unversioned)."""
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def set_user_version(conn: sqlite3.Connection, version: int) -> None:
    """Write ``PRAGMA user_version``."""
    conn.execute(f"PRAGMA user_version = {version}")


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Column names of a table (empty set when the table does not exist)."""
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.OperationalError:
        return set()
    return {str(row["name"]) for row in rows}


def ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> bool:
    """Add a column to a table if missing. Returns True when added."""
    if column in table_columns(conn, table):
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    return True
