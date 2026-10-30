"""R6 Director 测试——确定性停因映射、红线（不读预算/不开 run）、事件接线。

设计：docs/stage-r6-research-domain-assessment-20261030.md §2
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from strategy_research.core.study.director import (
    ACTION_ESCALATE,
    ACTION_RESCOPE,
    ACTION_STOP,
    DirectiveProposal,
    propose_next,
)


def study(sid: str = "st-1") -> SimpleNamespace:
    return SimpleNamespace(study_id=sid)


# ── A. 停因映射 ──────────────────────────────────────────


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("targets_met", ACTION_STOP),
        ("cancelled", ACTION_STOP),
        ("paused", ACTION_STOP),
        ("stagnation", ACTION_RESCOPE),
        ("stagnation_discard_streak", ACTION_RESCOPE),
        ("novelty_rejected", ACTION_RESCOPE),
        ("repeated_deviation", ACTION_RESCOPE),
        ("review_failed", ACTION_RESCOPE),
        ("early_stopped", ACTION_RESCOPE),
        ("budget_exceeded", ACTION_ESCALATE),
        ("max_rounds", ACTION_ESCALATE),
        ("needs_refresh", ACTION_ESCALATE),
        ("error", ACTION_ESCALATE),
    ],
)
def test_reason_mapping(reason, expected):
    p = propose_next(study(), reason)
    assert isinstance(p, DirectiveProposal)
    assert p.action == expected
    assert p.reason == reason
    assert p.study_id == "st-1"
    assert p.detail


def test_unknown_reason_escalates_without_guessing():
    p = propose_next(study(), "some-new-reason")
    assert p.action == ACTION_ESCALATE
    assert "未知停因" in p.detail


# ── B. 红线：不读预算 ────────────────────────────────────


def test_proposal_carries_no_budget_fields():
    """红线：不携带/不读取任何预算数值——只有停因字符串原样回填。"""
    p = propose_next(study(), "budget_exceeded", metrics={"sharpe": 1.0})
    d = p.to_dict()
    assert set(d) == {"action", "reason", "study_id", "detail", "payload"}
    # reason 原样回填（含 "budget_exceeded" 是正确行为）；payload 里不得有数值
    assert d["payload"] == {"metrics_present": True}
    assert all(
        not isinstance(v, (int, float)) or isinstance(v, bool)
        for v in d["payload"].values()
    )


def test_propose_next_does_not_query_study_attributes(monkeypatch):
    """只读 study_id——碰其他字段（尤其预算）就失败，锁定红线。"""

    class OnlyId:
        study_id = "st-x"

        def __getattr__(self, name):  # noqa: D105
            raise AssertionError(f"propose_next 不应读取 study.{name}")

    p = propose_next(OnlyId(), "targets_met")
    assert p.study_id == "st-x"


# ── C. 红线：不自行开 run ────────────────────────────────


def test_proposal_is_pure_no_side_effects():
    """纯函数：同一输入两次调用结果相同，且不产生任何外部状态。"""
    s = study()
    a = propose_next(s, "stagnation")
    b = propose_next(s, "stagnation")
    assert a.to_dict() == b.to_dict()
    assert a is not b  # 每次新对象（不可变 dataclass）


# ── D. 事件接线（runner 收尾）────────────────────────────


def test_runner_emits_directive_event_after_lifecycle():
    """run() 收尾必须发 study.directive；且不发任何「自动开 run」信号。"""
    from strategy_research.core.study.runner import AutoresearchRunner

    events: list[tuple[str, dict]] = []

    class FakeStore:
        def get_study(self, sid):
            return SimpleNamespace(
                study_id=sid, session_id="sess-1",
                last_metrics={"sharpe": 1.1},
            )

    runner = AutoresearchRunner.__new__(AutoresearchRunner)
    runner.study = SimpleNamespace(study_id="st-1", session_id="sess-1")
    runner.study_store = FakeStore()
    runner.emitter = SimpleNamespace(
        emit=lambda sid, kind, data: events.append((kind, data)),
    )
    runner._emit_directive("st-1", "sess-1", "stagnation")

    kinds = [k for k, _ in events]
    assert kinds == ["study.directive"]
    payload = events[0][1]
    assert payload["action"] == ACTION_RESCOPE
    assert payload["reason"] == "stagnation"
    # 红线：事件里不含任何「已提交/已排队」语义
    assert "submit" not in str(payload).lower()
    assert "queued" not in str(payload).lower()
