# -*- coding: utf-8 -*-
"""goal.settled → RSI cycle 的驱动接线（D4 阶段 2，框架 v1.1 §3.1）。

**为什么需要这个模块**：GEP-RSI 的第 0 步「谁拉起一轮 cycle」在阶段 1 只有
CLI + cron。阶段 2 要加两个高信号触发点：

- **``goal.settled``**（本模块）——信号密度最高：一个刚结束的 goal 就是一份
  完整证据包（判据 + 证据 + 结果），正好直喂第 1 步表型诊断。
- ``run.settled``（后续批次）——需要去重门（冷却窗口）。

**为什么在 L1 而不在 kernel**：本模块要同时看到事件流（L3）与
``rsi_cli.run_once``（L1 组装根）。放进 kernel 会逼 kernel 反向依赖 L1。
R1 已恢复 ``apps/goal`` 的 ``goal.settled`` 事件（``event_v2.EventType``），
消费者住组装根是唯一合法位置。

**质量防线（评审「阶段 2 前置」）**：
1. **判据强度门**——恒真判据（``"true"`` / ``"1"`` / ``x or true``）不触发
   cycle。评审实测归档求值器 ``evaluate("true")==True``；判据被写松就成了
   「自己写考勤」。
2. **写入方鉴别**——payload 的 ``writer`` 字段区分「业务侧正常完成」与
   「工具侧自证完成」。后者不走 RSI 触发（避免 agent 自己刷 cycle 预算）。
3. **每 session 限频**——复用 ``rsi_cli`` 的预算闸（session ≤ 1 / 日 ≤ N）。

**不变式**：本模块**永不抛出**。goal.settled 是通知，不是命令；驱动失败
必须记事件而不是打断 settle 流程。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: 恒真判据的形态（评审 A1 实测：``evaluate("true")==True``）。
#: 命中任一 → 判据强度不足 → 不触发 cycle。
_TRIVIAL_CRITERIA = frozenset({"true", "1", "yes", "always", "t"})

#: 允许触发 RSI 的 writer 取值。空/未知 → 不触发（保守）。
_TRUSTED_WRITERS = frozenset({"goal.store", "goal.workflow", "session_controller"})


def _as_count(value: Any) -> int | None:
    """把 payload 里的计数规范化为 int；无法解析返回 None（不默认成 0）。

    **为什么不能 `int(v or 0)`**：那会把 ``None`` 悄悄当成 0，使「字段缺失 /
    类型错误」与「真的是 0 条判据」混为一谈——前者是畸形 payload（应拒），
    后者是弱信号（也应拒，但 reason 不同）。两者分开才能排障。
    布尔要单独挡掉（``True`` 是 int 子类，但语义上不是计数）。
    """
    if isinstance(value, bool):
        return None
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text, 10)
        except ValueError:
            return None
    return None  # float / dict / list 等一律视为畸形


def criteria_are_strong(criteria_total: int, criteria_met: int) -> bool:
    """判据强度门的最小实现：至少有一条判据且确有覆盖。

    更完整的强度检查（拒恒真表达式）在判据创建侧（``apps/goal`` 的
    policy / validator）。本函数只做「数量合理性」——0 判据的 goal 完成
    不算有效信号（那是「没有目标的目标」）。
    """
    return criteria_total > 0 and criteria_met > 0


def payload_is_trivial(payload: dict[str, Any]) -> bool:
    """payload 里若带判据文本，检查是否恒真。"""
    text = str(payload.get("criteria_text") or "").strip().lower()
    return bool(text) and text in _TRIVIAL_CRITERIA


def should_trigger(payload: dict[str, Any]) -> tuple[bool, str]:
    """判断一条 ``goal.settled`` 是否应触发 RSI cycle。

    → ``(trigger: bool, reason: str)``。reason 用于事件与日志，空串表示触发。
    """
    if str(payload.get("outcome") or "") != "completed":
        # 只有「判据达成」才是正反馈信号；abandoned/failed 不触发
        # （失败是表型诊断的原料，但不是「该进化了」的信号）。
        return False, "not_completed"

    if str(payload.get("writer") or "") not in _TRUSTED_WRITERS:
        return False, "untrusted_writer"

    try:
        total = _as_count(payload.get("criteria_total"))
        met = _as_count(payload.get("criteria_met"))
    except (TypeError, ValueError):
        return False, "malformed_criteria_counts"
    if total is None or met is None:
        return False, "malformed_criteria_counts"

    if not criteria_are_strong(total, met):
        return False, "weak_criteria"

    if payload_is_trivial(payload):
        return False, "trivial_criteria"

    return True, ""


def make_goal_settled_consumer(
    *,
    run_cycle: Callable[..., Any] | None = None,
    emit: Callable[[str, dict], None] | None = None,
) -> Callable[[str, dict[str, Any]], None]:
    """构造 ``(kind, payload) -> None`` 的事件消费者。

    :param run_cycle: 注入的 cycle 执行器（缺省延迟导入 ``rsi_cli.run_once``）。
        测试注入替身即可断言「触发/不触发」。
    :param emit: 事件出口（缺省写日志）。

    **永不抛出**——消费者在事件泵里跑，任何异常都会污染事件流。
    """
    _emit = emit or (lambda kind, data: None)

    def _run(session_id: str | None = None) -> Any:
        if run_cycle is not None:
            return run_cycle(session_id=session_id)
        from agentnodes.interfaces.rsi_cli import run_once

        return run_once(session_id=session_id)

    def consume(kind: str, payload: dict[str, Any]) -> None:
        if kind != "goal.settled":
            return
        try:
            ok, reason = should_trigger(payload)
            if not ok:
                _emit(
                    "rsi.goal_settled_ignored",
                    {"goal_id": payload.get("goal_id", ""), "reason": reason},
                )
                logger.debug(
                    "goal.settled ignored for RSI: %s (goal_id=%s)",
                    reason, payload.get("goal_id", ""),
                )
                return

            sid = str(payload.get("session_id") or "") or None
            _emit(
                "rsi.goal_settled_triggered",
                {
                    "goal_id": payload.get("goal_id", ""),
                    "session_id": sid or "",
                    "criteria_total": payload.get("criteria_total"),
                    "criteria_met": payload.get("criteria_met"),
                },
            )
            outcome = _run(session_id=sid)
            _emit(
                "rsi.goal_settled_cycle_done",
                {
                    "goal_id": payload.get("goal_id", ""),
                    "ran": bool(getattr(outcome, "ran", False)),
                    "ok": bool(getattr(outcome, "ok", False)),
                    "reason": str(getattr(outcome, "reason", "")),
                },
            )
        except Exception as exc:  # noqa: BLE001 —— 消费者永不抛出
            logger.warning("goal.settled consumer failed: %s", exc)
            _emit(
                "rsi.goal_settled_failed",
                {"goal_id": payload.get("goal_id", ""), "error": str(exc)},
            )

    return consume


__all__ = [
    "criteria_are_strong",
    "make_goal_settled_consumer",
    "payload_is_trivial",
    "should_trigger",
]
