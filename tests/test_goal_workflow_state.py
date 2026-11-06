"""GoalWorkflow 状态与证据收集器单元测试。

覆盖 docs/coverage-90-plan-20261030.md §3.2：`goal/workflow.py` 原 66.2%。
这里测纯状态对象（GoalWorkflowState）与证据收集器（GoalEvidenceCollector，
store 全替身）——它们是 goal 执行的记账核心。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentnodes.apps.goal.workflow import (
    GoalEvidenceCollector,
    GoalWorkflowState,
)


# ── GoalWorkflowState ───────────────────────────────────────


def test_state_defaults():
    s = GoalWorkflowState()
    assert s.status == "idle"
    assert s.current_layer == 0
    assert s.paused is False
    assert s.cancelled is False
    assert s.agent_statuses == {}
    assert s.evidence_count == 0


def test_set_agent_status_records_error():
    s = GoalWorkflowState()
    s.set_agent_status("a", "running")
    assert s.agent_statuses == {"a": "running"}
    assert s.agent_errors == {}

    s.set_agent_status("a", "failed", error="boom")
    assert s.agent_statuses["a"] == "failed"
    assert s.agent_errors == {"a": "boom"}


def test_set_agent_status_error_not_overwritten_by_success():
    s = GoalWorkflowState()
    s.set_agent_status("a", "failed", error="boom")
    s.set_agent_status("a", "success")  # 无 error 参数 → 不清历史错误
    assert s.agent_statuses["a"] == "success"
    assert s.agent_errors == {"a": "boom"}


def test_get_summary_is_serializable_and_copies():
    s = GoalWorkflowState()
    s.status = "running"
    s.current_layer = 2
    s.evidence_count = 5
    s.error_message = "x"
    s.set_agent_status("a", "done")

    summary = s.get_summary()
    assert summary == {
        "status": "running",
        "current_layer": 2,
        "paused": False,
        "agent_statuses": {"a": "done"},
        "evidence_count": 5,
        "error_message": "x",
    }
    # 深拷贝语义：改 summary 不影响原状态
    summary["agent_statuses"]["a"] = "mutated"
    assert s.agent_statuses["a"] == "done"


# ── GoalEvidenceCollector ───────────────────────────────────


class FakeStore:
    def __init__(self, snapshot=None):
        self._snapshot = snapshot
        self.appended: list[dict] = []

    def get_current_snapshot(self, session_id):
        return self._snapshot

    def append_evidence(self, **kwargs):
        self.appended.append(kwargs)


def _snapshot_with_criteria(*criterion_ids):
    return {"criteria": [{"criterion_id": cid} for cid in criterion_ids]}


def test_collect_appends_evidence():
    store = FakeStore(_snapshot_with_criteria("c1", "c2"))
    col = GoalEvidenceCollector(store, "sess-1", "goal-1")
    n = col.collect("researcher", {"answer": "这是一段足够长的分析文本。"}, 0)
    assert n == 1
    assert len(store.appended) == 1
    ev = store.appended[0]
    assert ev["session_id"] == "sess-1"
    assert ev["goal_id"] == "goal-1"
    assert ev["evidence"].criterion_id == "c1"
    assert ev["evidence"].source_type == "researcher"


def test_collect_rejects_short_answer():
    store = FakeStore(_snapshot_with_criteria("c1"))
    col = GoalEvidenceCollector(store, "s", "g")
    assert col.collect("a", {"answer": "短"}, 0) == 0
    assert col.collect("a", {"answer": ""}, 0) == 0
    assert store.appended == []


def test_collect_rejects_missing_answer_key():
    store = FakeStore(_snapshot_with_criteria("c1"))
    col = GoalEvidenceCollector(store, "s", "g")
    assert col.collect("a", {}, 0) == 0


def test_collect_rejects_when_no_snapshot():
    store = FakeStore(None)
    col = GoalEvidenceCollector(store, "s", "g")
    assert col.collect("a", {"answer": "足够长的回答内容。"}, 0) == 0


def test_collect_rejects_out_of_range_criterion():
    store = FakeStore(_snapshot_with_criteria("c1"))
    col = GoalEvidenceCollector(store, "s", "g")
    assert col.collect("a", {"answer": "足够长的回答内容。"}, 5) == 0
    assert col.collect("a", {"answer": "足够长的回答内容。"}, -1) == 0


def test_collect_rejects_criterion_without_id():
    store = FakeStore({"criteria": [{"other": "field"}]})
    col = GoalEvidenceCollector(store, "s", "g")
    assert col.collect("a", {"answer": "足够长的回答内容。"}, 0) == 0


def test_collect_truncates_long_answer():
    store = FakeStore(_snapshot_with_criteria("c1"))
    col = GoalEvidenceCollector(store, "s", "g")
    col.collect("a", {"answer": "x" * 5000}, 0)
    ev = store.appended[0]
    assert len(ev["evidence"].text) == 2000


def test_collect_swallows_store_error():
    class BoomStore(FakeStore):
        def append_evidence(self, **kw):
            raise RuntimeError("db down")

    col = GoalEvidenceCollector(BoomStore(_snapshot_with_criteria("c1")), "s", "g")
    assert col.collect("a", {"answer": "足够长的回答内容。"}, 0) == 0
