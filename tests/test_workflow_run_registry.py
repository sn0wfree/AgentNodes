# -*- coding: utf-8 -*-
"""WP-7（M13 裁决 §6 #6）：WorkflowRunRegistry 终态保留语义锁定。

裁决：已知终态记录保留（无自动 prune），保护 known-terminal
approve=409 / unknown approve=404 的语义差（prune-on-access 实验
已回退，见 m13-workflow-registry-review-20261119.md §5）。prune()
当前零接线（死代码），其退役走独立删除审批（R-020），本文件不测
死方法。API 层正断言锁见 test_workflow_approval_api.py。
"""
from __future__ import annotations

from types import SimpleNamespace

from agentnodes.kernel.workflow.executor import WorkflowRunRegistry


def _runner(run_id: str, status: str) -> SimpleNamespace:
    return SimpleNamespace(
        run_id=run_id, status=status,
        status_snapshot=lambda: {"run_id": run_id, "status": status},
    )


def test_terminal_runner_retained_after_get():
    """终态 runner 经任意多次 get 仍保留（无 get-驱逐）。"""
    registry = WorkflowRunRegistry()
    stub = _runner("wf_t1", "failed")
    registry.put(stub)
    assert registry.get("wf_t1") is stub
    assert registry.get("wf_t1") is stub
    assert registry.get("wf_ghost") is None


def test_pop_is_the_only_removal_path():
    """显式 pop（DELETE 端点路径）才是唯一移除路径。"""
    registry = WorkflowRunRegistry()
    stub = _runner("wf_t2", "completed")
    registry.put(stub)
    assert registry.pop("wf_t2") is stub
    assert registry.get("wf_t2") is None
    assert registry.pop("wf_t2") is None  # 幂等


def test_active_lists_terminal_runs():
    """active() 快照含终态 run（保留语义，区别于 goal cache 的访问清理）。"""
    registry = WorkflowRunRegistry()
    registry.put(_runner("wf_a", "awaiting"))
    registry.put(_runner("wf_b", "failed"))
    registry.put(_runner("wf_c", "completed"))
    statuses = {s["run_id"]: s["status"] for s in registry.active()}
    assert statuses == {"wf_a": "awaiting", "wf_b": "failed", "wf_c": "completed"}
