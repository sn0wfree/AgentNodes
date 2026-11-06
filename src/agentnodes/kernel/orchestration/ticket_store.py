"""TicketStore — Ticket/Assignment 的持久化 + 编排（L3 内核机制）。

**为什么存在**：``Ticket``（任务身份）与 ``Assignment``（执行绑定）是通用
平台概念，不是研究业务（逐字段论证见 ``ticket.py`` 模块 docstring）。
把它们连同存储、状态机编排一起收进 kernel，``SessionController`` 就能
直接持有，不必经 ``CapabilityRegistry.resolve("study.ticket_sink")`` 绕一圈。

**与批次 3 前的关系**：本模块由两处合并而来（2026-11-05）：

| 原位置 | 搬来的内容 |
|---|---|
| ``apps/study/store.py`` | 2 张表 DDL + 5 个索引 + 7 个 CRUD 方法 + 2 个 row→对象转换 |
| ``apps/study/ticket_sink.py`` | ``admit`` / ``settle`` / ``recover`` / ``recover_and_mark`` |

合并理由：两者操作同一份状态机，且都**零业务语义**。拆开只会让
「谁能改 Assignment」有两个入口。

**数据库**：与 ``StudyStore`` 同库（``goals.db``，``AGENTNODES_GOAL_DB_PATH``
可覆盖）。库名里的 ``RESEARCH`` 是历史包袱，改它会破坏既有用户数据——
留作包名重命名批次一起处理。

**事件契约（H6）**：每次 Assignment 状态变更 → ``assignment.report``；
Ticket 准入 → ``ticket.upserted``。``emit`` 为 None 时只持久化不发射
（测试路径）。
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterable

from agentnodes.foundation.storage.sqlite import (
    connect,
    ensure_column,
    json_dumps,
    json_loads,
    resolve_db_path,
    write_transaction,
)

from .ticket import (
    Assignment,
    AssignmentStatus,
    Ticket,
    TicketStatus,
)

logger = logging.getLogger(__name__)

_DB_PATH_ENV = "AGENTNODES_GOAL_DB_PATH"

#: ``settle()`` 的 status 字符串 → AssignmentStatus。
#: 只接受六个会 emit report 的态（施工方案 §2.7）；``pending`` 不在其中。
_SETTLE_STATUS: dict[str, AssignmentStatus] = {
    "running": AssignmentStatus.RUNNING,
    "waiting": AssignmentStatus.WAITING,
    "ready": AssignmentStatus.READY,
    "blocked": AssignmentStatus.BLOCKED,
    "failed": AssignmentStatus.FAILED,
    "dropped": AssignmentStatus.DROPPED,
}


def default_db_path() -> Path:
    """Ticket/Assignment 库路径（与 StudyStore 同库）。

    解析顺序：``AGENTNODES_GOAL_DB_PATH`` → ``~/.agentnodes/goals.db``。
    """
    return resolve_db_path("goals.db", _DB_PATH_ENV)


#: 任务签名口径的 env 覆盖。**为什么放在 kernel 而不是组装根**：
#: ``TicketStore`` 是签名的唯一计算点，口径必须与它同源；把这些名字写在
#: L1 会创造"两处都知道签名怎么算"的第二事实源。
_SIG_ROLE_ENV = "AGENTNODES_RSI_ROLE"
_SIG_TOOLSET_ENV = "AGENTNODES_RSI_TOOLSET"

#: 解析不到任何工具集时的最小口径。**刻意很窄**：一个确定性的窄空间好过
#: 一个随运行时漂移的宽空间——后者会让"同一签名"逐渐收集到语义不同的
#: 轨迹，holdout 从"同任务历史"退化成"随便一批历史"。
_FALLBACK_ROLE = "chat"
_FALLBACK_TOOLSET: tuple[str, ...] = ("read",)


#: 解析缓存：``(role_env, toolset_env) -> scope``。
#:
#: ``build_default_registry()`` 首次调用约 200ms（要 import 全量工具模块）。
#: 装配根在同一个进程里可能多次构造 TicketStore（测试尤其如此），重复付
#: 这笔钱还带来一个隐蔽风险：两次构造拿到**不同的**工具集 → 同一份历史
#: 被算成两种签名。按 env 键缓存，既省时间又保证同进程内口径一致。
_SCOPE_CACHE: dict[tuple[str, str], tuple[str, tuple[str, ...]]] = {}


def resolve_signature_scope() -> tuple[str, tuple[str, ...]]:
    """推出生效的 ``(role, tool_set)`` 签名口径（确定性、有缓存）。

    解析顺序：
    1. ``AGENTNODES_RSI_ROLE`` / ``AGENTNODES_RSI_TOOLSET``（逗号分隔）
       —— 运维可钉住，测试可隔离。
    2. 默认工具链的实际注册 kind（``build_default_registry``）—— 生产口径
       与真实可用的工具一致。
    3. ``("chat", ("read",))`` —— 工具链不可用时的最小可用口径。

    任一步的异常都被吞掉并继续下沉：签名是"证据归类"的辅助字段，不该
    因为工具链装配失败就把 turn 打挂。
    """
    role = os.environ.get(_SIG_ROLE_ENV, "").strip()
    raw_tools = os.environ.get(_SIG_TOOLSET_ENV, "").strip()
    cache_key = (role, raw_tools)
    cached = _SCOPE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    if raw_tools:
        resolved = (role or _FALLBACK_ROLE, _tool_names_from(raw_tools))
        _SCOPE_CACHE[cache_key] = resolved
        return resolved

    try:
        from agentnodes.kernel.agent.builtin_tools import build_default_registry

        registry = build_default_registry()
        names = _registry_kinds(registry)
        if names:
            resolved = (
                role or _FALLBACK_ROLE,
                tuple(sorted({n.split(".")[-1] for n in names if n})),
            )
            _SCOPE_CACHE[cache_key] = resolved
            return resolved
    except Exception:  # noqa: BLE001 —— 工具链不可用，退回最小口径
        logger.debug("signature tool_set resolution failed", exc_info=True)

    resolved = (role or _FALLBACK_ROLE, _FALLBACK_TOOLSET)
    _SCOPE_CACHE[cache_key] = resolved
    return resolved


def _tool_names_from(raw: str) -> tuple[str, ...]:
    """``"read, write"`` → ``("read", "write")``（去空、排序、小写）。"""
    return tuple(sorted({t.strip().lower() for t in raw.split(",") if t.strip()}))


def _registry_kinds(registry: Any) -> list[str]:
    """尽力读出工具名列表（不同实现暴露不同名字）。

    已知形态：
    - ``CapabilityRegistry.list_kinds()`` / ``.kinds``（阶段二注册表）
    - ``ToolRegistry.tool_names()``（``build_default_registry`` 的真实返回）
    """
    for attr in ("list_kinds", "kinds", "registered_kinds", "tool_names"):
        candidate = getattr(registry, attr, None)
        if candidate is None:
            continue
        try:
            value = candidate() if callable(candidate) else candidate
            names = [str(v) for v in (value or ())]
            if names:
                return names
        except Exception:  # noqa: BLE001 —— 换下一个候选
            continue
    return []


class TicketStore:
    """Ticket/Assignment 的 SQLite 持久化 + 状态机编排。

    :param db_path: 库路径；None = 走 :func:`default_db_path`
    :param emit: ``(event_type, data) -> None`` 事件发射器。None = 只持久化。
    """

    #: 非终态 status（崩溃后需要恢复的）。failed/dropped/ended 是终态。
    NON_TERMINAL: frozenset[str] = frozenset(
        {"pending", "running", "waiting", "ready", "blocked"}
    )

    def __init__(
        self,
        db_path: Path | None = None,
        *,
        emit: Callable[[str, dict[str, Any]], None] | None = None,
        role: str | None = None,
        tool_set: Iterable[str] | None = None,
    ) -> None:
        """构造 store。

        :param role: 任务签名用的 role。None = 按 :func:`resolve_signature_scope`
            解析（env → 缺省）。默认 ``""`` 是"未分类"的**合法**签名空间
            （``""`` 与 ``"chat"`` 是两个不同空间，不会互相污染），但生产
            路径不该用它——所以 None（未指定）走解析，显式 ``""`` 才固定
            为空空间。
        :param tool_set: 任务签名用的工具集（排序后才进哈希）。None 同
            ``role``：走解析。显式空元组 = 固定为空集。

        **为什么要显式区分 None 与空值**：``TicketStore()`` 的两个参数都
        缺省时若都取空，生产签名的空间就退化成 ``("", ())``——所有任务
        挤进同一个桶，holdout 抽到的"历史"与当前任务无关（验证失去意义）。
        None 让"没指定"能落到一个确定的、与工具链一致的缺省口径。
        """
        if role is None or tool_set is None:
            default_role, default_tools = resolve_signature_scope()
            role = default_role if role is None else role
            tool_set = default_tools if tool_set is None else tool_set

        self.db_path = Path(db_path) if db_path is not None else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = connect(self.db_path)
        self._closed = False
        self._lock = threading.RLock()
        self._emit = emit
        self._role = role
        self._tool_set: tuple[str, ...] = tuple(sorted(str(t) for t in tool_set))
        self._init_db()

    # ── 生命周期 ────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the underlying SQLite connection (idempotent)."""
        with self._lock:
            if not self._closed:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                self._closed = True

    def __enter__(self) -> "TicketStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def bind_emit(
        self, emit: Callable[[str, dict[str, Any]], None] | None
    ) -> None:
        """装配根在 Gateway 构造完成后回填事件发射器。

        为什么不能构造时传：sink 必须落到 ``Gateway._combined_sink``，
        而后者只有实例存在后才可调用（Worker Slot 订阅也在那里）。
        装配根先建 sink、再建 Gateway、最后回填——避免用一个
        捕获循环引用的 lambda 掩盖依赖顺序。
        """
        self._emit = emit

    # ── schema ─────────────────────────────────────────────────────

    def _init_db(self) -> None:
        """Create ``tickets`` / ``assignments`` tables + indexes if absent."""
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tickets (
                    ticket_id               TEXT PRIMARY KEY,
                    objective               TEXT NOT NULL,
                    origin_session_key      TEXT NOT NULL DEFAULT '',
                    authorization_scope     TEXT,
                    priority                INTEGER NOT NULL DEFAULT 0,
                    status                  TEXT NOT NULL DEFAULT 'pending',
                    superseded_by           TEXT,
                    created_at              TEXT NOT NULL,
                    updated_at              TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tickets_status "
                "ON tickets(status)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tickets_session "
                "ON tickets(origin_session_key)"
            )
            # 同一 Ticket 可有多次 Assignment（重试）；run_id 唯一约束由
            # 调用方（准入路径）保证，这里只建查询索引。
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS assignments (
                    assignment_id       TEXT PRIMARY KEY,
                    ticket_id           TEXT NOT NULL,
                    run_id              TEXT NOT NULL,
                    worker_session_id   TEXT NOT NULL DEFAULT '',
                    status              TEXT NOT NULL DEFAULT 'pending',
                    fence_token         TEXT NOT NULL DEFAULT '',
                    report_payload      TEXT,
                    started_at          TEXT NOT NULL,
                    ended_at            TEXT,
                    task_signature      TEXT NOT NULL DEFAULT ''
                )
                """
            )
            # 增量迁移（W4 批次 2）：老库的 assignments 表没有
            # task_signature 列。``CREATE TABLE IF NOT EXISTS`` 不会补列，
            # 故显式 ALTER。缺了它 settle 时写不进去 → holdout 恒空。
            ensure_column(
                self._conn, "assignments", "task_signature",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_assignments_ticket "
                "ON assignments(ticket_id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_assignments_run "
                "ON assignments(run_id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_assignments_status "
                "ON assignments(status)"
            )
            self._conn.commit()

    # ── Ticket CRUD ────────────────────────────────────────────────

    def upsert_ticket(self, ticket: Ticket) -> Ticket:
        """Insert or update a Ticket by ``ticket_id``.

        幂等（施工方案 §2.4）：同一 ``ticket_id`` 多次调用只保留一行。
        ``created_at`` 保留首次值，``updated_at`` 每次刷新。
        """
        with self._lock, write_transaction(self._conn) as conn:
            conn.execute(
                """
                INSERT INTO tickets (
                    ticket_id, objective, origin_session_key, authorization_scope,
                    priority, status, superseded_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticket_id) DO UPDATE SET
                    objective           = excluded.objective,
                    origin_session_key  = excluded.origin_session_key,
                    authorization_scope = excluded.authorization_scope,
                    priority            = excluded.priority,
                    status              = excluded.status,
                    superseded_by       = excluded.superseded_by,
                    updated_at          = excluded.updated_at
                """,
                (
                    ticket.ticket_id,
                    ticket.objective,
                    ticket.origin_session_key,
                    json_dumps(ticket.authorization_scope),
                    ticket.priority,
                    ticket.status.value,
                    ticket.superseded_by,
                    ticket.created_at,
                    ticket.updated_at,
                ),
            )
        return ticket

    def get_ticket(self, ticket_id: str) -> Ticket | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)
            ).fetchone()
        return self._ticket_from_row(row) if row else None

    def list_tickets(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[Ticket]:
        sql = "SELECT * FROM tickets"
        args: list[Any] = []
        if status is not None:
            sql += " WHERE status = ?"
            args.append(status)
        sql += " ORDER BY priority DESC, created_at ASC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, tuple(args)).fetchall()
        return [self._ticket_from_row(r) for r in rows]

    # ── Assignment CRUD ────────────────────────────────────────────

    def create_assignment(self, assignment: Assignment) -> Assignment:
        """Insert a new Assignment row (每次准入都是新行，对应重试)."""
        with self._lock, write_transaction(self._conn) as conn:
            conn.execute(
                """
                INSERT INTO assignments (
                    assignment_id, ticket_id, run_id, worker_session_id,
                    status, fence_token, report_payload, started_at, ended_at,
                    task_signature
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assignment.assignment_id,
                    assignment.ticket_id,
                    assignment.run_id,
                    assignment.worker_session_id,
                    assignment.status.value,
                    assignment.fence_token,
                    json_dumps(assignment.report_payload),
                    assignment.started_at,
                    assignment.ended_at,
                    assignment.task_signature,
                ),
            )
        return assignment

    def update_assignment(self, assignment: Assignment) -> Assignment:
        """Persist a status/report change for an existing Assignment."""
        with self._lock, write_transaction(self._conn) as conn:
            conn.execute(
                """
                UPDATE assignments SET
                    status = ?, report_payload = ?, ended_at = ?,
                    task_signature = ?
                WHERE assignment_id = ?
                """,
                (
                    assignment.status.value,
                    json_dumps(assignment.report_payload),
                    assignment.ended_at,
                    assignment.task_signature,
                    assignment.assignment_id,
                ),
            )
        return assignment

    def get_assignment(self, assignment_id: str) -> Assignment | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM assignments WHERE assignment_id = ?",
                (assignment_id,),
            ).fetchone()
        return self._assignment_from_row(row) if row else None

    def list_assignments(
        self, ticket_id: str | None = None, *, limit: int = 100
    ) -> list[Assignment]:
        sql = "SELECT * FROM assignments"
        args: list[Any] = []
        if ticket_id is not None:
            sql += " WHERE ticket_id = ?"
            args.append(ticket_id)
        sql += " ORDER BY started_at ASC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, tuple(args)).fetchall()
        return [self._assignment_from_row(r) for r in rows]

    # ── 任务签名 ────────────────────────────────────────────────────

    @property
    def signature_role(self) -> str:
        """签名口径里的 role（只读；RSI 驱动据此算同一签名空间）。"""
        return self._role

    @property
    def signature_tool_set(self) -> tuple[str, ...]:
        """签名口径里的 tool_set（已排序、只读）。"""
        return self._tool_set

    def _signature_for(self, objective: str) -> str:
        """按本 store 的 ``(role, tool_set)`` 计算任务签名（确定性、无 LLM）。

        **为什么由 store 算而不是事件发起方填**：签名是 holdout 的匹配键，
        决定了"哪些历史轨迹能进验证集"。若发起方可以自填，验证集就能被
        挑选（H5 防奖励黑客的最后一环）。store 是唯一的准入/收尾入口，
        所以它是唯一合法的计算点。

        计算失败（kernel.evolution 不可用）返回空串并记 debug——签名缺失
        只会让 holdout 抽不到样本，不会阻断 turn。
        """
        try:
            from agentnodes.kernel.evolution.holdout import compute_task_signature

            return compute_task_signature(objective, self._role, self._tool_set)
        except Exception:  # noqa: BLE001 —— 签名算不出不阻断准入/收尾
            logger.debug("task signature computation failed", exc_info=True)
            return ""

    # ── 编排：准入 ──────────────────────────────────────────────────

    def admit(
        self,
        *,
        session_key: str,
        session_id: str,
        run_id: str,
        turn_id: str,
        objective: str,
        fence_token: str = "",
    ) -> Assignment:
        """准入一个 turn：upsert Ticket（幂等）+ create Assignment(pending)。

        返回新建的 Assignment，供控制器在收尾时 ``settle()``。
        任何异常都向上抛，由控制器降级为 ``ticket.admit_failed`` 事件。

        W4 批次 2：这里把 ``task_signature`` 算好并**随行持久化**。此前
        report 不带签名 → ``holdout.sample()`` 的签名匹配恒空 → RSI 闭环
        永远走 ``empty_holdout``。
        """
        existing = self._find_ticket_for(session_key)
        if existing is None:
            ticket = Ticket.new(
                objective=objective,
                origin_session_key=session_key,
            )
            ticket = ticket.transition_to(TicketStatus.ACTIVE)
        else:
            ticket = existing
            if ticket.status is TicketStatus.PENDING:
                ticket = ticket.transition_to(TicketStatus.ACTIVE)
        self.upsert_ticket(ticket)

        assignment = Assignment.new(
            ticket.ticket_id,
            run_id,
            worker_session_id=session_id,
            fence_token=fence_token,
        )
        # 用**持久化的** ticket.objective 而非调用方入参：复用既有 Ticket
        # 时两者可能不同（同一 session 的第二轮），而签名必须与 Ticket
        # 身份一致，否则同一任务会产生多个签名空间。
        assignment = replace(
            assignment,
            task_signature=self._signature_for(ticket.objective),
        )
        self.create_assignment(assignment)

        self._emit_event("ticket.upserted", {
            "ticket_id": ticket.ticket_id,
            "objective": ticket.objective,
            "status": ticket.status.value,
            "session_id": session_id,
            "task_signature": assignment.task_signature,
        })
        return assignment

    # ── 编排：收尾 ──────────────────────────────────────────────────

    def settle(
        self,
        *,
        assignment: Assignment,
        status: str,
        reason: str | None = None,
    ) -> Assignment:
        """把 Assignment 推到 ``status`` 终态并 emit ``assignment.report``。

        ``status`` 只接受六个 report 态（施工方案 §2.7 口径）；非法值抛
        ``ValueError``（控制器已 catch，不会打断 turn）。

        W4 批次 2：签名在这里**再兜一次**——历史行（迁移前）或调用方直构
        的 Assignment 可能没有签名；此时从 Ticket.objective 重算，保证
        ``assignment.report`` 一定带签名（否则该条轨迹对 holdout 不可见）。
        """
        target = _SETTLE_STATUS.get(status)
        if target is None:
            raise ValueError(
                f"settle() status must be one of {sorted(_SETTLE_STATUS)}, got {status!r}"
            )

        payload: dict[str, Any] = {}
        if reason:
            payload["reason"] = reason

        moved = assignment
        # 路径可能是 pending→X（引擎尚未启动）或 running→X；也允许
        # running→ended 后再 settle（上游已收尾）。逐跳转移以复用状态机校验。
        if moved.status is AssignmentStatus.PENDING and target is not (
            AssignmentStatus.RUNNING
        ):
            moved = moved.transition_to(target, payload=payload)
        elif moved.status is AssignmentStatus.RUNNING:
            moved = moved.transition_to(target, payload=payload)
        elif moved.status is not target:
            moved = moved.transition_to(target, payload=payload)

        if not moved.task_signature:
            objective = ""
            try:
                ticket = self.get_ticket(moved.ticket_id)
                objective = ticket.objective if ticket is not None else ""
            except Exception:  # noqa: BLE001 —— 查 ticket 失败不阻断收尾
                logger.debug("ticket lookup for signature failed", exc_info=True)
            moved = replace(moved, task_signature=self._signature_for(objective))

        self.update_assignment(moved)

        if moved.should_emit_report:
            self._emit_event("assignment.report", moved.report_event_data())
        return moved

    # ── 编排：崩溃恢复 ──────────────────────────────────────────────

    def recover(self) -> list[Assignment]:
        """返回所有非终态 Assignment（进程重启后仍待办的那些）。

        纯读：不改任何行、不发事件。调用方（Gateway.recover）决定如何
        纳入记账。emit 也留给定调用方——恢复事件的 reason 取决于
        "为什么重启"，那属于运维上下文，不是本模块能知道的。

        上限 5000 而非更小：崩溃后积压量没有先验上界，取一个远大于
        正常待办量的值，避免把真正的待办项截掉一半（截断的恢复比
        不恢复更危险——它让审计视图看起来"干净"）。
        """
        return [
            a for a in self.list_assignments(limit=5000)
            if a.status.value in self.NON_TERMINAL
        ]

    def recover_and_mark(self, reason: str = "restart") -> list[Assignment]:
        """恢复非终态 Assignment，把它们标成 ``blocked`` 并 emit report。

        **只恢复状态，不恢复执行**（施工方案定稿：auto_resume=false）。
        标注 ``blocked`` 而不是 ``failed``：这些任务没失败，只是进程重启
        打断了它们，需要重新准入——用 failed 会污染失败率统计。
        """
        recovered: list[Assignment] = []
        for a in self.recover():
            try:
                moved = a.transition_to(
                    AssignmentStatus.BLOCKED,
                    payload={"reason": reason, "current_step": "restart"},
                )
            except Exception:  # noqa: BLE001 —— 转移不合法就跳过该条
                logger.debug("cannot mark %s as blocked", a.assignment_id, exc_info=True)
                continue
            self.update_assignment(moved)
            if moved.should_emit_report:
                self._emit_event("assignment.report", moved.report_event_data())
            recovered.append(moved)
        return recovered

    # ── helpers ────────────────────────────────────────────────────

    def _emit_event(self, event_type: str, data: dict[str, Any]) -> None:
        """发事件；发射器缺失或抛错都不影响持久化（H6 尽力而为）。"""
        if self._emit is None:
            return
        try:
            self._emit(event_type, data)
        except Exception:  # noqa: BLE001 —— 事件发射失败不得回滚已落库的状态
            logger.warning("emit %s failed", event_type, exc_info=True)

    def _find_ticket_for(self, session_key: str) -> Ticket | None:
        """Return the active/pending Ticket for a session_key, if any.

        同一 session 的后续 turn 复用同一个 Ticket（任务身份不变，
        执行绑定每次新建）—— 施工方案 §2.4。
        """
        for t in self.list_tickets(limit=500):
            if t.origin_session_key != session_key:
                continue
            if t.status in (TicketStatus.ACTIVE, TicketStatus.PENDING):
                return t
        return None

    @staticmethod
    def _ticket_from_row(row: sqlite3.Row) -> Ticket:
        return Ticket(
            ticket_id=row["ticket_id"],
            objective=row["objective"],
            origin_session_key=row["origin_session_key"],
            authorization_scope=json_loads(row["authorization_scope"], {}) or {},
            priority=int(row["priority"]),
            status=TicketStatus(row["status"]),
            superseded_by=row["superseded_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _assignment_from_row(row: sqlite3.Row) -> Assignment:
        return Assignment(
            assignment_id=row["assignment_id"],
            ticket_id=row["ticket_id"],
            run_id=row["run_id"],
            worker_session_id=row["worker_session_id"],
            status=AssignmentStatus(row["status"]),
            fence_token=row["fence_token"],
            report_payload=json_loads(row["report_payload"], {}) or {},
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            # 老库（迁移前）的行没有该列值 → 空串，holdout 自然跳过。
            task_signature=(
                row["task_signature"]
                if "task_signature" in row.keys() else ""
            ) or "",
        )


__all__ = ["TicketStore", "default_db_path", "resolve_signature_scope"]
