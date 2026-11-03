"""R-A 在 goal 域的副作用：重跑 agent 会导致证据重复计数。

背景：R-A 让 ``branches[].action=retry`` 真正重跑目标 agent。重跑会再次
触发 ``on_agent_complete``，而 ``GoalWorkflowHook.on_agent_complete`` 里
``self._evidence_count += 1`` 是无条件递增的——同一次逻辑产出被计两遍，
可能让 goal 的「证据数量」提前达标。

当前生产影响为 0（全库 4 个 workflow 模板 + 6 个 goal preset 零 branches
配置），但这是机制层面的真实缺陷：一旦有人配置 retry，证据账就错了。

修复方向：证据按 agent_id 幂等（同一 agent 的重跑覆盖而非累加）。
本文件先锁住现状以便修复，再用测试证明修复生效。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from strategy_research.apps.goal.workflow_hook import GoalWorkflowHook
from strategy_research.kernel.swarm.runtime import AgentResult, AgentStatus


class FakeStore:
    """只提供 hook 需要的最小面：证据追加 + 快照。"""

    def __init__(self):
        self.evidence = []

    def get_current_snapshot(self, session_id):
        return {
            "criteria": [{"criterion_id": "c1", "description": "d", "met": False}],
            "evidence": list(self.evidence),
        }

    def append_evidence(self, *, session_id, goal_id, expected_goal_id, evidence):
        self.evidence.append(evidence)
        return True


def _hook(store):
    hook = GoalWorkflowHook(
        session_id="s1", goal_id="g1",
        evidence_map={"worker": 0},          # worker 覆盖 criterion 0
        store=store, runner=None,
        workflow_name="wf",
    )
    return hook


def _result(output: str = "一段足够长的研究结论文本用于触发证据收集") -> AgentResult:
    return AgentResult(
        agent_id="worker", status=AgentStatus.SUCCESS,
        output=output, summary=output[:20],
    )


def test_evidence_counts_once_per_agent_completion():
    """基线：单次完成 → 证据计 1。"""
    store = FakeStore()
    hook = _hook(store)
    hook.on_agent_complete("worker", _result(), {})
    assert hook.evidence_count == 1


def test_retry_does_not_double_count_evidence():
    """R-A 场景：同一 agent 因 retry 完成两次 → 证据仍应只计 1。

    当前实现会累加（2），这是待修的真实缺陷；此测试即为其守护。
    """
    store = FakeStore()
    hook = _hook(store)
    hook.on_agent_complete("worker", _result("第一次产出的研究结论文本内容"), {})
    hook.on_agent_complete("worker", _result("第二次产出的研究结论文本内容"), {})
    assert hook.evidence_count == 1, (
        f"重跑导致证据重复计数：{hook.evidence_count}"
    )
