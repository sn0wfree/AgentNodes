"""goal.settled → RSI cycle 消费器（D4 阶段 2，框架 v1.1 §3.1）。

覆盖三类不变量：
1. **质量防线**——判据强度门 + writer 鉴别，恒真/自证/未完成都不触发；
2. **触发路径**——正常完成触发一轮 cycle，带 session_id；
3. **永不抛出**——消费者住在事件泵里，任何失败都必须落成事件。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.interfaces.goal_settled_consumer import (
    criteria_are_strong,
    make_goal_settled_consumer,
    payload_is_trivial,
    should_trigger,
)


# ── 判据强度 ─────────────────────────────────────────────────────────


class TestCriteriaStrength:
    def test_both_positive_is_strong(self):
        assert criteria_are_strong(3, 2) is True

    @pytest.mark.parametrize("total,met", [(0, 0), (0, 5), (3, 0), (-1, 1)])
    def test_zero_or_negative_side_is_weak(self, total: int, met: int):
        assert criteria_are_strong(total, met) is False

    @pytest.mark.parametrize("text", ["true", "1", "yes", "TRUE", " 1 ", "always"])
    def test_trivial_criteria_text(self, text: str):
        """评审 A1：归档求值器实测 evaluate("true")==True。"""
        assert payload_is_trivial({"criteria_text": text}) is True

    @pytest.mark.parametrize("text", ["", "ic > 0.05", "sharpe >= 1.2"])
    def test_meaningful_criteria_text(self, text: str):
        assert payload_is_trivial({"criteria_text": text}) is False


# ── should_trigger 判定表 ────────────────────────────────────────────


def _payload(**over: object) -> dict:
    base = {
        "goal_id": "g-1",
        "session_id": "s-1",
        "outcome": "completed",
        "writer": "goal.store",
        "criteria_total": 3,
        "criteria_met": 3,
    }
    base.update(over)
    return base


class TestShouldTrigger:
    def test_happy_path(self):
        ok, reason = should_trigger(_payload())
        assert ok is True
        assert reason == ""

    @pytest.mark.parametrize("outcome", ["abandoned", "failed", ""])
    def test_non_completed_does_not_trigger(self, outcome: str):
        ok, reason = should_trigger(_payload(outcome=outcome))
        assert ok is False
        assert reason == "not_completed"

    @pytest.mark.parametrize("writer", ["agent_tool", "", "unknown", None])
    def test_untrusted_writer_does_not_trigger(self, writer):
        """评审 C7：写入方鉴别是阶段 2 的前置，不信任 agent 自证。"""
        ok, reason = should_trigger(_payload(writer=writer))
        assert ok is False
        assert reason == "untrusted_writer"

    @pytest.mark.parametrize("writer", ["goal.store", "goal.workflow", "session_controller"])
    def test_trusted_writers(self, writer: str):
        ok, _ = should_trigger(_payload(writer=writer))
        assert ok is True

    def test_zero_criteria_is_weak(self):
        ok, reason = should_trigger(_payload(criteria_total=0, criteria_met=0))
        assert ok is False
        assert reason == "weak_criteria"

    def test_unmet_criteria_is_weak(self):
        ok, reason = should_trigger(_payload(criteria_total=3, criteria_met=0))
        assert ok is False
        assert reason == "weak_criteria"

    def test_trivial_criteria_does_not_trigger(self):
        ok, reason = should_trigger(_payload(criteria_text="true"))
        assert ok is False
        assert reason == "trivial_criteria"

    @pytest.mark.parametrize("bad", ["x", None, {}, 2.5])
    def test_malformed_counts(self, bad):
        ok, reason = should_trigger(_payload(criteria_total=bad))
        assert ok is False
        assert reason == "malformed_criteria_counts"


# ── consumer 行为 ────────────────────────────────────────────────────


class _Outcome:
    ran = True
    ok = True
    reason = "approved"


class TestConsumer:
    def _collect(self, **kwargs):
        calls: list[str | None] = []
        events: list[tuple[str, dict]] = []
        consume = make_goal_settled_consumer(
            run_cycle=kwargs.pop("run_cycle", lambda session_id=None: _Outcome()),
            emit=lambda k, d: events.append((k, d)),
        )
        return consume, calls, events

    def test_triggered_runs_cycle_with_session(self):
        calls: list[str | None] = []

        def run_cycle(session_id=None):
            calls.append(session_id)
            return _Outcome()

        events: list[tuple[str, dict]] = []
        consume = make_goal_settled_consumer(
            run_cycle=run_cycle, emit=lambda k, d: events.append((k, d))
        )
        consume("goal.settled", _payload())

        assert calls == ["s-1"], "cycle 应以 payload 的 session_id 为预算口径"
        assert [k for k, _ in events] == [
            "rsi.goal_settled_triggered",
            "rsi.goal_settled_cycle_done",
        ]

    def test_ignored_emits_reason(self):
        calls: list[str | None] = []
        events: list[tuple[str, dict]] = []
        consume = make_goal_settled_consumer(
            run_cycle=lambda session_id=None: calls.append(session_id) or _Outcome(),
            emit=lambda k, d: events.append((k, d)),
        )
        consume("goal.settled", _payload(outcome="abandoned"))

        assert calls == [], "未完成不应触发 cycle"
        assert len(events) == 1
        assert events[0][0] == "rsi.goal_settled_ignored"
        assert events[0][1]["reason"] == "not_completed"

    def test_other_kinds_ignored(self):
        calls: list[str | None] = []
        events: list[tuple[str, dict]] = []
        consume = make_goal_settled_consumer(
            run_cycle=lambda session_id=None: calls.append(session_id) or _Outcome(),
            emit=lambda k, d: events.append((k, d)),
        )
        consume("goal_updated", _payload())

        assert calls == []
        assert events == []

    def test_cycle_exception_is_swallowed(self):
        """消费者住在事件泵里——异常必须落成事件而非外泄。"""
        events: list[tuple[str, dict]] = []

        def boom(**kwargs):
            raise RuntimeError("cycle exploded")

        consume = make_goal_settled_consumer(
            run_cycle=boom, emit=lambda k, d: events.append((k, d))
        )
        consume("goal.settled", _payload())  # 不抛

        assert [k for k, _ in events] == [
            "rsi.goal_settled_triggered",
            "rsi.goal_settled_failed",
        ]

    def test_missing_session_id_passes_none(self):
        calls: list[str | None] = []
        consume = make_goal_settled_consumer(
            run_cycle=lambda session_id=None: calls.append(session_id) or _Outcome(),
            emit=lambda k, d: None,
        )
        consume("goal.settled", _payload(session_id=None))
        assert calls == [None]
