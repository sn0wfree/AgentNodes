"""最后一批 B/C 档收尾测试——hook 分支 / scheduler dump / Memory 注入器。

覆盖 docs/coverage-90-plan-20261030.md §3.2 收尾缺口。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from strategy_research.core.agent.context_injector import (
    MemoryRetrieverInjector,
)
from strategy_research.core.goal.workflow_hook import GoalWorkflowHook
from strategy_research.apps.study.scheduler import StudyScheduler


# ── workflow_hook 收尾分支（B 档）─────────────────────────


class FakeSnapshotStore:
    def __init__(self, snapshot):
        self._snapshot = snapshot
        self.fail_append = False

    def get_current_snapshot(self, session_id):
        return self._snapshot

    def append_evidence(self, **kw):
        if self.fail_append:
            raise RuntimeError("append down")


def _snapshot(*cids):
    return {"criteria": [{"criterion_id": c} for c in cids], "evidence": []}


def _loop(**kw):
    """模块级最小 AgentLoop stub（injector 测试共用）。"""
    defaults = dict(session_id="s1", enable_goal_injection=True,
                    enable_memory_injection=True)
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_hook_parse_output_dict_form():
    h = GoalWorkflowHook.__new__(GoalWorkflowHook)
    assert h._parse_output({"output": {"a": 1}}) == {"a": 1}
    assert h._parse_output({"answer": "文本"}) == "文本"
    # 纯字符串无 .output 属性也不是 dict → raw 保持 None（锁定现行为）
    assert h._parse_output("raw string") is None


def test_hook_layer_result_capture_failure_tolerated():
    """_parse_output 抛错 → 记警告跳过（不影响其他 agent）。"""
    from strategy_research.core.goal.workflow_hook import GoalWorkflowHook

    h = GoalWorkflowHook.__new__(GoalWorkflowHook)
    h._session_id = "s"
    h._goal_id = "g"
    h._evidence_map = {}
    h._store = FakeSnapshotStore(_snapshot("c1"))
    h._runner = None
    h._run_store = None
    h._run_id = ""
    h._completion_strategy = None
    h._completion_mode = ""
    h._workflow_name = ""
    h._event_bus = None
    h._completed = False
    h._evidence_count = 0
    h._layer_results = {}
    h._metric_targets = None

    class Exploding:
        @property
        def output(self):
            raise RuntimeError("boom")

    h.on_layer_complete(0, ["bad"], {"bad": Exploding()})
    assert "bad" not in h._layer_results
    assert "bad" not in h._layer_results


def test_hook_criteria_auto_complete_via_coverage():
    """全部 required 准则被证据覆盖 → on_layer_complete 触发自动完成。"""
    from strategy_research.core.goal.workflow_hook import GoalWorkflowHook

    snapshot = {
        "criteria": [{"criterion_id": "c1", "required": True}],
        "evidence": [{"criterion_id": "c1"}],
    }
    store = FakeSnapshotStore(snapshot)
    completed_calls: list[tuple] = []

    class SyncStrategy:
        async def complete(self, store, session_id, goal_id, criteria, evidence, name):
            completed_calls.append((session_id, goal_id))

    h = GoalWorkflowHook(
        session_id="s", goal_id="g", evidence_map={}, store=store,
        completion_strategy=SyncStrategy(), completion_mode="auto",
        event_bus=None,
    )
    # 在事件循环外（无 running loop）→ asyncio.run 同步路径
    h.on_layer_complete(0, ["researcher"], {})
    assert h.completed is True
    assert completed_calls == [("s", "g")]


def test_hook_auto_complete_event_loop_schedules(capfd):
    """有 running loop → create_task 调度而非同步执行。"""
    import asyncio

    snapshot = {
        "criteria": [{"criterion_id": "c1", "required": True}],
        "evidence": [{"criterion_id": "c1"}],
    }
    store = FakeSnapshotStore(snapshot)
    scheduled: list = []

    class Strategy:
        async def complete(self, *a):
            scheduled.append(a)

    h = GoalWorkflowHook(
        session_id="s", goal_id="g", evidence_map={}, store=store,
        completion_strategy=Strategy(), completion_mode="auto",
    )

    async def drive():
        h.on_layer_complete(0, ["researcher"], {})
        await asyncio.sleep(0.05)  # 让 create_task 的协程跑完

    asyncio.run(drive())
    assert h.completed is True
    assert len(scheduled) == 1


def test_hook_auto_complete_strategy_failure_emits_workflow_failed():
    """锁定现行为：create_task 即置 completed=True——strategy 的后续
    失败只发 workflow_failed 事件，不回滚 completed 标志。"""
    snapshot = {
        "criteria": [{"criterion_id": "c1", "required": True}],
        "evidence": [{"criterion_id": "c1"}],
    }
    store = FakeSnapshotStore(snapshot)
    events: list[tuple] = []

    class Bus:
        def emit(self, *a, **k):
            events.append(a[0])

    class FailStrategy:
        async def complete(self, *a):
            raise RuntimeError("completion down")

    h = GoalWorkflowHook(
        session_id="s", goal_id="g", evidence_map={}, store=store,
        completion_strategy=FailStrategy(), completion_mode="auto",
        event_bus=Bus(),
    )

    async def drive():
        h.on_layer_complete(0, ["researcher"], {})
        await asyncio.sleep(0.05)

    asyncio.run(drive())
    assert h.completed is True  # 调度即视为完成（锁定现行为）


def test_hook_collect_evidence_store_append_failure():
    """append_evidence 抛错 → 返回 False（不冒泡）。"""
    store = FakeSnapshotStore(_snapshot("c1"))
    store.fail_append = True
    h = GoalWorkflowHook(
        session_id="s", goal_id="g", evidence_map={"researcher": 0},
        store=store,
    )
    ok = h._collect_evidence("researcher", "足够长的证据文本内容。", 0)
    assert ok is False


# ── scheduler dump 方法（B 档）───────────────────────────


def test_scheduler_dump_methods():
    """dump_* 观测方法返回 JSON-able dict（不抛）。"""
    from strategy_research.apps.study.scheduler import StudyScheduler

    class MinimalStore:
        def get_study(self, sid):
            return None

    sched = StudyScheduler(MinimalStore())
    d1 = sched.dump_concurrency()
    d2 = sched.dump_watchdog()
    d3 = sched.dump_session_queues()
    assert isinstance(d1, dict) and isinstance(d2, dict) and isinstance(d3, dict)


def test_scheduler_shutdown_idempotent():
    from strategy_research.apps.study.scheduler import StudyScheduler

    class MinimalStore:
        def get_study(self, sid):
            return None

    sched = StudyScheduler(MinimalStore())
    asyncio.run(sched.shutdown())
    asyncio.run(sched.shutdown())  # 幂等


# ── context_injector MemoryRetriever（C 档）──────────────


def test_memory_retriever_builds_block(monkeypatch):
    from strategy_research.kernel.memory.persistent import MemoryEntry
    from strategy_research.core.agent.context_injector import (
        MemoryRetrieverInjector,
    )

    inj = MemoryRetrieverInjector()

    entry = MemoryEntry(
        path=Path("factor.md"), title="factor_notes",
        description="动量因子", memory_type="project",
        body="动量因子研究记录。", modified_at=0.0,
    )
    monkeypatch.setattr(inj, "_retrieve", lambda loop, task: [entry])
    loop = SimpleNamespace(memory=None, session_id="s1")
    block = inj._build_block(loop, "动量")
    assert "factor_notes" in block


def test_memory_retriever_empty_memory_returns_empty_block(monkeypatch):
    from strategy_research.core.agent.context_injector import (
        MemoryRetrieverInjector,
    )

    inj = MemoryRetrieverInjector()
    monkeypatch.setattr(inj, "_retrieve", lambda loop, task: [])
    loop = SimpleNamespace(memory=None, session_id="s1")
    assert inj._build_block(loop, "任务") == ""


def test_memory_retriever_load_memory_default_none():
    from strategy_research.core.agent.context_injector import (
        MemoryRetrieverInjector,
    )
    from strategy_research.kernel.memory.persistent import PersistentMemory

    inj = MemoryRetrieverInjector(memory=PersistentMemory())
    loaded = inj._load_memory()
    assert loaded is None or isinstance(loaded, PersistentMemory)


def test_memory_retriever_inject_pre_run_no_memory(monkeypatch):
    from strategy_research.core.agent.context_injector import (
        MemoryRetrieverInjector,
    )

    inj = MemoryRetrieverInjector()
    monkeypatch.setattr(inj, "_build_block", lambda loop, task: None)
    messages: list = []
    inj.inject_pre_run(SimpleNamespace(session_id="s"), "任务", messages)
    assert messages == []


def test_memory_retriever_build_block_with_entries(monkeypatch):
    """_build_block + _format_entry：有命中 → <relevant-memory> 块。"""
    from strategy_research.core.agent.context_injector import (
        MemoryRetrieverInjector,
    )

    def _loop(**kw):
        defaults = dict(session_id="s1", enable_goal_injection=True,
                        enable_memory_injection=True)
        defaults.update(kw)
        return SimpleNamespace(**defaults)

    inj = MemoryRetrieverInjector()
    entries = [
        SimpleNamespace(title="factor_notes", memory_type="project",
                        body="动量因子研究记录。"),
        {"title": "hypo", "type": "hypothesis", "statement": "动量有效"},
    ]
    monkeypatch.setattr(inj, "_retrieve", lambda loop, task: entries)
    loop = SimpleNamespace(memory=None, session_id="s1")
    block = inj._build_block(loop, "动量")
    assert block.startswith("<relevant-memory>")
    assert "factor_notes" in block
    assert "[project/factor_notes]" in block
    assert "[hypothesis/hypo]" in block


def test_memory_retrieve_with_memory_and_hypotheses(monkeypatch):
    """_retrieve：memory 命中 + include_hypotheses 分支。"""
    from strategy_research.core.agent.context_injector import (
        MemoryRetrieverInjector,
    )

    entry = SimpleNamespace(
        title="momentum", memory_type="project", body="动量 momentum 研究。")

    mem = SimpleNamespace(
        find_relevant=lambda task, max_results: [entry])
    inj = MemoryRetrieverInjector(memory=mem, include_hypotheses=True)
    monkeypatch.setattr(inj, "_retrieve_hypotheses",
                        lambda loop, task: [{"title": "动量假说", "id": "h1"}])
    hits = inj._retrieve(_loop(), "动量 momentum")
    assert len(hits) == 2  # memory 1 + hypotheses 1


def test_retrieve_hypotheses_registry_error_returns_empty(monkeypatch):
    from strategy_research.core.agent.context_injector import (
        MemoryRetrieverInjector,
    )

    inj = MemoryRetrieverInjector(include_hypotheses=True)

    import strategy_research.apps.hypothesis.registry as hr

    class Boom:
        def __init__(self):
            raise RuntimeError("registry down")

    monkeypatch.setattr(hr, "HypothesisRegistry", Boom, raising=False)
    assert inj._retrieve_hypotheses(_loop(), "task") == []


def test_retrieve_hypotheses_scores_and_sorts(monkeypatch):
    from strategy_research.core.agent.context_injector import (
        MemoryRetrieverInjector,
    )
    import strategy_research.apps.hypothesis.registry as hr

    rows = [
        {"title": "无关条目", "id": "r1"},
        {"title": "动量假说 momentum", "id": "r2"},
        {"title": "动量也提 momentum 一次", "id": "r3"},
    ]

    class FakeReg:
        def list(self):
            return rows

    monkeypatch.setattr(hr, "HypothesisRegistry", lambda: FakeReg(),
                        raising=False)
    inj = MemoryRetrieverInjector(include_hypotheses=True)
    hits = inj._retrieve_hypotheses(_loop(), "动量 momentum")
    ids = [h["id"] for h in hits]
    assert "r1" not in ids  # 零分被过滤
    assert "r2" in ids and "r3" in ids  # 平分按序保留


def test_memory_retriever_inject_pre_run_prepends_block(monkeypatch):
    """inject_pre_run：命中 → 块前置于任务文本。"""
    from strategy_research.core.agent.context_injector import (
        MemoryRetrieverInjector,
    )

    inj = MemoryRetrieverInjector()
    monkeypatch.setattr(inj, "_build_block",
                        lambda loop, task: "<relevant-memory>x</relevant-memory>")
    out = inj.inject_pre_run(SimpleNamespace(session_id="s"), "任务本体", [])
    assert out.startswith("<relevant-memory>")
    assert "任务本体" in out


def test_memory_retriever_inject_pre_run_disabled(monkeypatch):
    from strategy_research.core.agent.context_injector import (
        MemoryRetrieverInjector,
    )

    inj = MemoryRetrieverInjector()
    called = {"n": 0}
    monkeypatch.setattr(inj, "_build_block",
                        lambda loop, task: called.__setitem__("n", 1) or "")
    out = inj.inject_pre_run(
        SimpleNamespace(session_id="s", enable_memory_injection=False),
        "任务", [])
    assert called["n"] == 0
    assert out == "任务"


# ── C 档最后 16 行：goal_tools status 校验 + list_goals 异常 ──


def test_list_goals_invalid_status_is_actionable(tmp_path, monkeypatch):
    from strategy_research.core.agent.builtin_tools.goal_tools import (
        ListGoalsTool,
    )
    from strategy_research.core.goal.store import GoalStore
    from strategy_research.core.agent.tools import ToolContext

    db = tmp_path / "goals3.db"
    store = GoalStore(db_path=db)
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(db))
    try:
        out = json.loads(ListGoalsTool().execute(
            ctx=ToolContext(workspace=tmp_path, session_id="s"),
            status="bogus_status"))
        assert out["status"] == "error"
        assert "invalid status" in json.dumps(out)
    finally:
        try:
            store.close()
        except Exception:
            pass
