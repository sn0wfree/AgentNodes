"""RSI 驱动 + 预算闸 + 证据增量门（组装根，L1）。

**这个模块存在的理由**：``build_rsi_loop()`` 在本批之前**生产零调用**——
零件齐全（holdout / EvolutionAgent / ValidationGate / golden replay），
但没有任何东西真的去驱动它。本模块就是那个驱动：一个薄薄的一层，把
"什么时候值得跑一轮 RSI"的判断题从 kernel 里拿出来，放到已知配置与
宿主的 L1。

**它做的三件事**（全部在真正跑 cycle 之前）：

1. **halt 护栏** — 全局 kill switch 一旦置位，立刻停（评审「D4 护栏失效」）。
2. **预算闸** — 每 session ≤ 1 cycle、每日 ≤ N（缺省 3）。RSI 会真跑
   LLM golden eval（每轮 ~10-30 次调用），没有预算闸的自我进化等于
   烧钱。阈值是模块常量 + env 覆盖（框架 §0.5「门槛可调」）。
3. **证据增量门** — 自上次 cycle 之后没有新的 ``assignment.report`` 就
   ``skip(no_new_evidence)``。同样的证据重复验证只会得到同样的结论；
   没有增量时跑 RSI 是纯浪费，而且会污染"验证历史"。

**状态落盘**：``~/.agentnodes/rsi_state.json``（``AGENTNODES_RSI_STATE_PATH``
可覆盖）。目录解析复用 ``foundation.storage.sqlite.ensure_data_dir``——
不硬编码 home，也不各自实现一套迁移逻辑。

**为什么在 L1 而不是 kernel**：它需要 Gateway（含 ticket_sink 的签名口径）、
RSI 装配、halt、数据目录——这四样只有组装根能同时看到。放 kernel 会逼
kernel 反向依赖 interfaces（L3→L1 上行违规）。
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

# ── 预算常量（可参数化；环境变量覆盖）────────────────────────────────

#: 单个 session 最多跑几轮 cycle。1 = 一个会话只进化一次（避免同一批
#: 证据被反复"验证"成噪声）。env: ``AGENTNODES_RSI_PER_SESSION_LIMIT``。
PER_SESSION_CYCLE_LIMIT = 1

#: 每日全局 cycle 上限。缺省 3：按"每轮 ~10-30 次 LLM 调用"估算，
#: 3 轮 ≈ 30-90 次调用/天，是可控的探索预算。
#: env: ``AGENTNODES_RSI_DAILY_LIMIT``。
DAILY_CYCLE_LIMIT = 3

#: 状态文件名（落在 ``ensure_data_dir()`` 下）。
STATE_FILENAME = "rsi_state.json"

#: 状态文件路径覆盖（测试 / 多实例隔离）。
_STATE_PATH_ENV = "AGENTNODES_RSI_STATE_PATH"

#: 记住多少个最近跑过的 session id（有界，避免状态文件无限增长）。
MAX_TRACKED_SESSIONS = 200


# ── 结果类型 ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CycleOutcome:
    """一轮驱动的结果（供 CLI 打印 / 测试断言）。

    ``ran`` 与 ``ok`` 是两件事：被门挡下时 ``ran=False``（没跑），跑完
    但验证未通过时 ``ran=True, ok=False``。混在一起会让"跳过了"与
    "验证失败"在统计上不可区分。
    """

    ran: bool
    ok: bool
    reason: str
    detail: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ran": self.ran,
            "ok": self.ok,
            "reason": self.reason,
            **self.detail,
        }


# ── 状态 ────────────────────────────────────────────────────────────


def state_path() -> Path:
    """状态文件路径（env 覆盖优先，否则数据目录）。"""
    override = os.environ.get(_STATE_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    try:
        from agentnodes.foundation.storage.sqlite import ensure_data_dir

        return ensure_data_dir() / STATE_FILENAME
    except Exception:  # noqa: BLE001 —— 数据目录不可解析时退回 home
        return Path.home() / ".agentnodes" / STATE_FILENAME


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _today(now: datetime | None = None) -> str:
    return (now or _utc_now()).date().isoformat()


def load_state() -> dict[str, Any]:
    """读状态；不存在/损坏返回空骨架（损坏不该让 RSI 永久停摆）。"""
    path = state_path()
    if not path.exists():
        return _empty_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("rsi state unreadable (%s): %s", path, exc)
        return _empty_state()
    if not isinstance(raw, dict):
        return _empty_state()
    state = _empty_state()
    state.update(raw)
    return state


def save_state(state: dict[str, Any]) -> None:
    """落盘（原子写）。失败只记日志：预算状态丢失比阻断 RSI 更可接受。"""
    path = state_path()
    try:
        from agentnodes.foundation.utils.io_utils import atomic_write_json

        atomic_write_json(path, state)
    except Exception as exc:  # noqa: BLE001
        logger.warning("rsi state persist failed (%s): %s", path, exc)


def _empty_state() -> dict[str, Any]:
    return {
        "last_cycle_at": "",
        "last_evidence_count": 0,
        "daily": {"date": "", "count": 0},
        "sessions": [],
        "cycles_total": 0,
        "last_signature": "",
        "skip_counts": {},
    }


# ── 预算闸 ──────────────────────────────────────────────────────────


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning("invalid %s=%r, using default %d", name, raw, default)
        return default


def per_session_limit() -> int:
    return _int_env("AGENTNODES_RSI_PER_SESSION_LIMIT", PER_SESSION_CYCLE_LIMIT)


def daily_limit() -> int:
    return _int_env("AGENTNODES_RSI_DAILY_LIMIT", DAILY_CYCLE_LIMIT)


def check_budget(
    state: dict[str, Any], *, session_id: str, now: datetime | None = None
) -> str | None:
    """返回 ``None`` = 允许，否则返回拒绝原因（``session_budget`` / ``daily_budget``）。"""
    if session_id and session_id in (state.get("sessions") or []):
        return "session_budget"

    daily = state.get("daily") or {}
    today = _today(now)
    if daily.get("date") == today and int(daily.get("count", 0)) >= daily_limit():
        return "daily_budget"
    return None


def record_cycle(
    state: dict[str, Any],
    *,
    session_id: str,
    evidence_count: int,
    signature: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """记账：更新每日计数、session 名单、证据游标。返回新 state。"""
    moment = now or _utc_now()
    today = moment.date().isoformat()
    daily = state.get("daily") or {}
    if daily.get("date") != today:
        daily = {"date": today, "count": 0}
    daily["count"] = int(daily.get("count", 0)) + 1
    state["daily"] = daily

    sessions = list(state.get("sessions") or [])
    if session_id and session_id not in sessions:
        sessions.append(session_id)
    state["sessions"] = sessions[-MAX_TRACKED_SESSIONS:]

    state["last_cycle_at"] = moment.isoformat().replace("+00:00", "Z")
    state["last_evidence_count"] = int(evidence_count)
    state["last_signature"] = signature
    state["cycles_total"] = int(state.get("cycles_total", 0)) + 1
    return state


# ── 证据读取 ────────────────────────────────────────────────────────


def collect_reports(gw: Any, *, limit: int = 5000) -> list[dict[str, Any]]:
    """读全部 ``assignment.report``（跨 session，只读）。

    :param limit: 上限。历史事件可能很多，而"有没有增量"只需要知道总量；
      超出上限时返回最后 ``limit`` 条（时间序由 store 决定）。
    """
    store = getattr(gw, "store", None)
    if store is None:
        return []
    try:
        from agentnodes.kernel.evolution.holdout import EventStoreReportSource

        source = EventStoreReportSource(store)
        rows = list(source.iter_assignment_reports())
    except Exception as exc:  # noqa: BLE001 —— 读不出证据就按"无证据"处理
        logger.warning("rsi evidence read failed: %s", exc)
        return []
    return rows[-limit:] if limit and len(rows) > limit else rows


def select_signature(
    reports: Iterable[dict[str, Any]],
) -> tuple[str, int]:
    """选出证据最多的 ``task_signature``（确定性）。

    平票按签名字典序——**必须确定**：同一份证据两次驱动要选同一个任务，
    否则两次 cycle 验证的不是同一件事（附录 C 的确定性要求）。

    返回 ``(signature, count)``；无签名时返回 ``("", 0)``。
    """
    counts: dict[str, int] = {}
    for raw in reports:
        sig = str(raw.get("task_signature") or "")
        if sig:
            counts[sig] = counts.get(sig, 0) + 1
    if not counts:
        return "", 0
    # 先比次数（降序），再比签名（升序）→ 完全确定
    best = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    return best[0], best[1]


def resolve_objective(
    signature: str,
    *,
    role: str,
    tool_set: Iterable[str],
) -> str | None:
    """从签名反查一个"能重算出同一签名"的 objective。

    签名的口径是 ``role + toolset + 意图桶``（见 ``holdout.compute_task_signature``），
    桶是封闭集合 → 有限枚举即可精确反查。反查不到（签名属于别的 role/
    toolset 空间，或来自旧口径）返回 None：此时**不该猜**，因为猜出来的
    objective 会让 ``EvolutionAgent`` 重算出不同签名，抽到的 holdout 与
    提案对象就不是同一任务。
    """
    try:
        from agentnodes.kernel.evolution.holdout import (
            intent_for_signature,
            objective_for_intent,
        )
    except Exception:  # noqa: BLE001
        return None
    intent = intent_for_signature(signature, role=role, tool_set=tool_set)
    if intent is None:
        return None
    return objective_for_intent(intent)


# ── 驱动 ────────────────────────────────────────────────────────────


class _GwProxy:
    """给 ``build_rsi_loop`` 的 Gateway 视图，唯一目的是替换 ``_combined_sink``。

    ``build_rsi_loop`` 用 ``gw._combined_sink`` 作为 RSI 事件的发射器，而
    Gateway 的默认 sink 会把"没有 session_id 的事件"全落 ``default`` 分区
    （评审 P2i 的老问题）。这里包一层注入 ``session_id``，让 RSI 的全部
    事件落到本轮的 RSI session 分区；其余属性（genome / capacity / store）
    原样透传。
    """

    def __init__(self, gw: Any, emit: Callable[[str, dict], None]) -> None:
        object.__setattr__(self, "_gw", gw)
        object.__setattr__(self, "_combined_sink", emit)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_gw"), name)


def _session_emitter(
    gw: Any, session_id: str
) -> Callable[[str, dict], None]:
    """构造带 session_id 的事件发射器（缺省走 Gateway 的 combined sink）。"""
    base = getattr(gw, "_combined_sink", None)
    if not callable(base):
        return lambda kind, data: None

    def emit(kind: str, data: dict[str, Any]) -> None:
        payload = dict(data or {})
        payload.setdefault("session_id", session_id)
        base(kind, payload)

    return emit


def _resolve_gateway(gateway: Any | None) -> tuple[Any | None, str | None]:
    """返回 ``(gateway, error)``。error 非 None 表示取不到 Gateway。"""
    if gateway is not None:
        return gateway, None
    try:
        from agentnodes.kernel.gateway.singleton import get_gateway

        return get_gateway(), None
    except Exception as exc:  # noqa: BLE001
        logger.warning("rsi gateway unavailable: %s", exc)
        return None, str(exc)


def _gate_halt() -> tuple[bool, str]:
    """门 1：halt。返回 ``(blocked, reason)``。"""
    try:
        from agentnodes.interfaces.cli.halt import HALT_REASON, is_halted
    except Exception:  # noqa: BLE001 —— halt 模块不可用不影响驱动
        logger.debug("halt check unavailable", exc_info=True)
        return False, ""
    if is_halted():
        return True, HALT_REASON
    return False, ""


def _select_evidence(
    gw: Any, state: dict[str, Any]
) -> tuple[str | None, int, int, str]:
    """门 3：证据增量 + 签名选择。

    返回 ``(signature, sig_count, evidence_count, blocked_reason)``。
    ``signature is None`` 表示被挡下，此时 ``blocked_reason`` 是原因，
    ``evidence_count`` 仍是真实观测值（诊断要用）。
    """
    reports = collect_reports(gw)
    evidence_count = len(reports)
    last_count = int(state.get("last_evidence_count", 0) or 0)
    if evidence_count <= last_count:
        return None, 0, evidence_count, "no_new_evidence"
    signature, sig_count = select_signature(reports)
    if not signature:
        # 有 report 但都没签名 → 旧口径事件（迁移前）或 store 没注入 role/
        # toolset。这不是"没证据"，是"证据不可用于 RSI"，必须可见。
        return None, 0, evidence_count, "no_signed_evidence"
    return signature, sig_count, evidence_count, ""


def _signature_scope(gw: Any) -> tuple[str, tuple[str, ...]]:
    """从 TicketStore 读签名口径（它是签名的唯一计算点）。"""
    sink = getattr(gw, "ticket_sink", None)
    role = str(getattr(sink, "signature_role", "") or "")
    tool_set = tuple(getattr(sink, "signature_tool_set", ()) or ())
    return role, tool_set


def _assemble_loop(gw: Any, emit: Callable[[str, dict], None], seed: int) -> Any | None:
    """装配 RSI 闭环；失败返回 None（由调用方发 ``wiring_skipped``）。"""
    try:
        from agentnodes.interfaces.rsi_wiring import build_rsi_loop

        return build_rsi_loop(_GwProxy(gw, emit), seed=seed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("rsi loop assembly failed: %s", exc)
        return None


def _run_loop(
    loop: Any,
    *,
    signature: str,
    objective: str,
    role: str,
    tool_set: tuple[str, ...],
    sid: str,
    sig_count: int,
    evidence_count: int,
) -> CycleOutcome:
    """跑 holdout→提案→验证，把结果包成 ``CycleOutcome``。"""
    try:
        result = loop.run_once(
            signature,
            objective=objective,
            role=role,
            tool_set=frozenset(tool_set),
        )
        return CycleOutcome(
            ran=True,
            ok=bool(result.get("ok")),
            reason=str(result.get("reason") or "unknown"),
            detail={
                "session_id": sid,
                "task_signature": signature,
                "signature_samples": sig_count,
                "evidence_count": evidence_count,
                **{k: v for k, v in result.items() if k not in ("ok", "reason")},
            },
        )
    except Exception as exc:  # noqa: BLE001 —— run_once 声称永不抛，双保险
        logger.warning("rsi cycle crashed: %s", exc)
        return CycleOutcome(
            ran=True, ok=False, reason="cycle_crashed",
            detail={"error": str(exc), "session_id": sid},
        )


def _build_monitor(
    emit: Callable[[str, dict], None], state: dict[str, Any]
) -> Any | None:
    """建 SkipMonitor 并从持久状态恢复窗口轨迹（失败返回 None）。

    **必须在第一道门之前建好**：被门挡下的跳过恰恰是最需要被观察的。
    窗口轨迹从持久状态恢复，所以 24h 的"连续超阈"跨进程成立——CLI 每次
    调用都是新进程，不恢复就等于每次清零。
    """
    try:
        from agentnodes.kernel.evolution.skip_monitor import SkipMonitor

        monitor = SkipMonitor(emit=emit)
        monitor.load_snapshot(state.get("skip_monitor"))
        return monitor
    except Exception:  # noqa: BLE001 —— 监控不可用不阻断驱动
        logger.debug("skip monitor unavailable", exc_info=True)
        return None


def _flush_monitor(monitor: Any | None, state: dict[str, Any]) -> None:
    """把 monitor 的快照写进 state（snapshot + 累计计数并集）。"""
    if monitor is None:
        return
    try:
        snap = monitor.snapshot()
        state["skip_monitor"] = snap
        totals = {
            s["reason"]: int(s.get("total", 0))
            for s in snap.get("stats", []) if s.get("reason")
        }
        if totals:
            merged = dict(state.get("skip_counts") or {})
            for reason, n in totals.items():
                merged[reason] = max(int(merged.get(reason, 0)), n)
            state["skip_counts"] = merged
    except Exception:  # noqa: BLE001
        pass


def _make_skipper(
    *,
    emit: Callable[[str, dict], None],
    state: dict[str, Any],
    monitor: Any | None,
) -> Callable[..., CycleOutcome]:
    """构造 ``skip(reason, **detail)``：发事件 → 记监控 → 落盘 → 返回。"""

    def skip(reason: str, **detail: Any) -> CycleOutcome:
        emit("rsi.cycle_skipped", {"reason": reason, **detail})
        if monitor is not None:
            monitor.observe({"reason": reason})
            _flush_monitor(monitor, state)
        else:
            # monitor 不可用时的降级计数：至少让"跳过多少次"可见。
            # **不要两边都加**——那会把每类跳过记成两倍。
            counts = dict(state.get("skip_counts") or {})
            counts[reason] = int(counts.get(reason, 0)) + 1
            state["skip_counts"] = counts
        save_state(state)
        logger.info("rsi cycle skipped: %s %s", reason, detail)
        return CycleOutcome(ran=False, ok=False, reason=reason, detail=detail)

    return skip


def run_once(
    *,
    seed: int = 0,
    session_id: str | None = None,
    gateway: Any = None,
    force: bool = False,
    now: datetime | None = None,
) -> CycleOutcome:
    """跑一轮 RSI（过三道门之后）。

    :param seed: holdout / golden eval 的抽样种子（附录 C：必须显式给）
    :param session_id: 预算口径的会话 id。None = 本次调用视为一个新会话
        （``rsi-<8hex>``）——CLI 每次调用都是一次独立的进化机会；显式传入
        同一 id 时受"每 session ≤ 1 cycle"约束（供 chat 侧复用）。
    :param gateway: 已接线的 Gateway；None = ``get_gateway()``
    :param force: 跳过预算闸（**不**跳过 halt 与证据门）。仅供运维手动
        重跑；不建议在自动化里使用。
    :param now: 时间源（测试注入）

    **永不抛出**：RSI 是增强通道，任何失败都落成返回值 + 事件。
    """
    sid = session_id or f"rsi-{uuid.uuid4().hex[:8]}"
    state = load_state()

    gw, gw_error = _resolve_gateway(gateway)
    if gw is None:
        return CycleOutcome(
            ran=False, ok=False, reason="gateway_unavailable",
            detail={"error": gw_error or ""},
        )

    emit = _session_emitter(gw, sid)
    monitor = _build_monitor(emit, state)
    skip = _make_skipper(emit=emit, state=state, monitor=monitor)

    # ── 门 1：halt（不可 force 绕过）──────────────────────────────
    halted, halt_reason = _gate_halt()
    if halted:
        return skip("halted", halt_reason=halt_reason)

    # ── 门 2：预算 ────────────────────────────────────────────────
    if not force:
        blocked = check_budget(state, session_id=sid, now=now)
        if blocked is not None:
            return skip(
                blocked,
                session_id=sid,
                daily_limit=daily_limit(),
                per_session_limit=per_session_limit(),
            )

    # ── 门 3：证据增量 + 签名 ─────────────────────────────────────
    signature, sig_count, evidence_count, blocked = _select_evidence(gw, state)
    if signature is None:
        detail: dict[str, Any] = {"evidence_count": evidence_count}
        if blocked == "no_new_evidence":
            detail["last_evidence_count"] = int(
                state.get("last_evidence_count", 0) or 0
            )
        return skip(blocked, **detail)

    role, tool_set = _signature_scope(gw)
    objective = resolve_objective(signature, role=role, tool_set=tool_set)
    if objective is None:
        # 签名来自别的 role/toolset 空间（或旧口径）：不能猜 objective，
        # 否则提案重算出的签名与抽样签名不一致。
        return skip(
            "signature_unmapped",
            task_signature=signature,
            role=role,
            tool_set=list(tool_set),
        )

    # ── 装配 + 跑 ────────────────────────────────────────────────
    loop = _assemble_loop(gw, emit, seed)
    if loop is None:
        return skip("wiring_skipped", task_signature=signature)

    outcome = _run_loop(
        loop,
        signature=signature,
        objective=objective,
        role=role,
        tool_set=tool_set,
        sid=sid,
        sig_count=sig_count,
        evidence_count=evidence_count,
    )

    # 记账（只有真的跑了 cycle 才消耗预算）
    record_cycle(
        state,
        session_id=sid,
        evidence_count=evidence_count,
        signature=signature,
        now=now,
    )
    _flush_monitor(monitor, state)
    save_state(state)
    return outcome


__all__ = [
    "DAILY_CYCLE_LIMIT",
    "MAX_TRACKED_SESSIONS",
    "PER_SESSION_CYCLE_LIMIT",
    "CycleOutcome",
    "check_budget",
    "collect_reports",
    "daily_limit",
    "load_state",
    "per_session_limit",
    "record_cycle",
    "resolve_objective",
    "run_once",
    "save_state",
    "select_signature",
    "state_path",
]
