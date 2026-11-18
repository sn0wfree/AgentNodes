"""GoalWorkflow checkpoint-resume 集成测试（覆盖 workflow.py 573-706 块）。

流程：构造 runner → 种子 hook layer_results → checkpoint() 落盘 →
resume_from_checkpoint 恢复状态 → resume_and_continue 以 pre_completed
继续执行（单层 DAG + start_layer=1 → 无剩余层 → 直接完成）。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentnodes.apps.goal.workflow import (
    CompletionConfig,
    GoalAgentConfig,
    GoalWorkflowConfig,
    GoalWorkflowRunner,
)
from agentnodes.apps.goal.workflow_hook import GoalWorkflowHook


CFG = GoalWorkflowConfig(
    name="resume_flow",
    description="checkpoint-resume 集成测试",
    agents=[
        GoalAgentConfig(id="researcher", prompt_file=".prompts/researcher.md",
                        evidence_criterion=0),
    ],
    dag={"researcher": []},
    completion=CompletionConfig(mode="auto", monitor_interval_seconds=None),
)


class FakeStore:
    """resume 路径需要的最小 GoalStore 面。"""

    def __init__(self):
        self.goals = {}

    def get_goal(self, goal_id):
        return self.goals.get(goal_id)

    def get_current_snapshot(self, session_id):
        return None


@pytest.fixture()
def cp_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("STRATEGY_RESEARCH_CHECKPOINT_BASE_DIR",
                       str(tmp_path / "cps"))
    return tmp_path / "cps"


def _make_runner(store: FakeStore, ws: Path, session_id="sess-cp") -> GoalWorkflowRunner:
    runner = GoalWorkflowRunner(
        CFG, session_id, store=store, workspace=ws,
    )
    runner.set_goal_id("g1")
    return runner


def _seed_hook(runner: GoalWorkflowRunner, output: str) -> None:
    """模拟「第 0 层已完成」：挂上带 layer_results 的 hook。"""
    runner._hook = GoalWorkflowHook(
        session_id=runner._session_id,
        goal_id=runner._goal_id,
        evidence_map={"researcher": 0},
        store=runner._store,
        runner=runner,
        workflow_name=CFG.name,
        event_bus=runner._event_bus,
    )
    runner._hook._layer_results = {
        "researcher": {"output": output, "status": "success"},
    }
    runner._state.status = "paused"
    runner._state.current_layer = 1


# ── checkpoint() ─────────────────────────────────────────


def test_checkpoint_saves_layer_results(cp_env: Path, tmp_path: Path):
    store = FakeStore()
    runner = _make_runner(store, tmp_path)
    _seed_hook(runner, output="第一层产出")

    path = runner.checkpoint()
    assert path is not None and path.exists()
    assert (path / "layer_results.json").exists()
    assert (path / "state.json").exists()

    import json

    lr = json.loads((path / "layer_results.json").read_text(encoding="utf-8"))
    assert lr["researcher"]["output"] == "第一层产出"


def test_checkpoint_without_hook_returns_path_with_summary(cp_env, tmp_path):
    store = FakeStore()
    runner = _make_runner(store, tmp_path)
    path = runner.checkpoint()
    assert path is not None  # 空 layer_results 也保存（fallback summary）


# ── resume_from_checkpoint ───────────────────────────────


def test_resume_from_checkpoint_restores_state(cp_env, tmp_path):
    store = FakeStore()
    runner = _make_runner(store, tmp_path)
    _seed_hook(runner, output="第一层产出")
    runner.checkpoint()

    restored = GoalWorkflowRunner.resume_from_checkpoint(
        session_id="sess-cp", goal_id="g1", config=CFG,
        store=store, workspace=tmp_path,
    )
    assert restored is not None
    assert restored._goal_id == "g1"
    assert restored._state.current_layer == 1
    assert restored._state.status == "paused"
    assert restored._hook is not None
    assert "researcher" in restored._hook._layer_results


def test_resume_from_checkpoint_missing_returns_none(cp_env, tmp_path):
    restored = GoalWorkflowRunner.resume_from_checkpoint(
        session_id="none", goal_id="none", config=CFG,
        store=FakeStore(), workspace=tmp_path,
    )
    assert restored is None


# ── resume_and_continue（573-706 主块）───────────────────


def test_resume_and_continue_completes_from_pre_completed(cp_env, tmp_path):
    """完整恢复流：checkpoint → resume_and_continue → pre_completed
    直接判定成功（单层 DAG + start_layer=1 → 无剩余层执行）。"""
    store = FakeStore()
    store.goals["g1"] = SimpleNamespace(objective="研究动量因子")
    runner = _make_runner(store, tmp_path)
    _seed_hook(runner, output="第一层产出")
    runner.checkpoint()

    restored = GoalWorkflowRunner.resume_from_checkpoint(
        session_id="sess-cp", goal_id="g1", config=CFG,
        store=store, workspace=tmp_path,
    )
    assert restored is not None
    goal_id = asyncio.run(restored.resume_and_continue())
    assert goal_id == "g1"
    # pre_completed 的成功 → completed
    assert restored._state.status == "completed"
    assert restored._state.error_message == ""


def test_resume_and_continue_raises_without_checkpoint(cp_env, tmp_path):
    store = FakeStore()
    runner = _make_runner(store, tmp_path)
    with pytest.raises(FileNotFoundError):
        asyncio.run(runner.resume_and_continue())


def test_resume_and_continue_error_on_runtime_failure(cp_env, tmp_path, monkeypatch):
    """runtime 抛错 → state=error + workflow_failed 事件。"""
    store = FakeStore()
    store.goals["g1"] = SimpleNamespace(objective="obj")
    runner = _make_runner(store, tmp_path)
    _seed_hook(runner, output="第一层产出")
    runner.checkpoint()

    restored = GoalWorkflowRunner.resume_from_checkpoint(
        session_id="sess-cp", goal_id="g1", config=CFG,
        store=store, workspace=tmp_path,
    )
    # 放大器：让 to_thread 内的 execute 抛错——替换 SwarmRuntime 构造
    import agentnodes.kernel.swarm.runtime as rt_mod

    class Boom:
        def execute(self, *a, **k):
            raise RuntimeError("runtime exploded")

    # *a/**k：SwarmRuntime 现在接受 on_event=（agent 事件接线），桩要兼容。
    monkeypatch.setattr(rt_mod, "SwarmRuntime", lambda *a, **k: Boom())
    events: list[str] = []
    restored._event_bus.emit = lambda *a, **k: events.append(a[0])

    goal_id = asyncio.run(restored.resume_and_continue())
    assert goal_id == "g1"
    assert restored._state.status == "error"
    assert "runtime exploded" in restored._state.error_message
    assert "workflow_failed" in events


# ── 批1 #2（三前端一核心）：resume 与 chat 的会话执行互斥 ────────────


def test_resume_from_checkpoint_passes_lease(cp_env: Path, tmp_path: Path):
    """工厂透传 execution_lease；缺省 None 保持旧行为（直构测试路径）。"""
    from agentnodes.kernel.gateway.session_lease import SessionExecutionLease

    store = FakeStore()
    runner = _make_runner(store, tmp_path)
    _seed_hook(runner, output="第一层产出")
    runner.checkpoint()

    lease = SessionExecutionLease()
    restored = GoalWorkflowRunner.resume_from_checkpoint(
        session_id="sess-cp", goal_id="g1", config=CFG,
        store=store, workspace=tmp_path, execution_lease=lease,
    )
    assert restored is not None
    assert restored._execution_lease is lease

    restored_default = GoalWorkflowRunner.resume_from_checkpoint(
        session_id="sess-cp", goal_id="g1", config=CFG,
        store=store, workspace=tmp_path,
    )
    assert restored_default is not None
    assert restored_default._execution_lease is None


def test_resume_and_continue_waits_for_lease_then_releases(
    cp_env: Path, tmp_path: Path, monkeypatch,
):
    """resume 与 chat 互斥全链：chat 持锁时自旋等待 → 释放后获取 →
    执行期间持有 goal:<goal_id> → 成功结束（finally）后释放。

    此前 resume_and_continue 全程零 lease 触点，checkpoint 恢复的
    DAG 可与 Gateway chat 并发写同一 session（裁决 2 缺口，批1 #2）。
    """
    import threading

    import agentnodes.kernel.swarm.runtime as rt_mod

    from agentnodes.kernel.gateway.session_lease import SessionExecutionLease

    store = FakeStore()
    store.goals["g1"] = SimpleNamespace(objective="obj")
    runner = _make_runner(store, tmp_path)
    _seed_hook(runner, output="第一层产出")
    runner.checkpoint()

    lease = SessionExecutionLease()
    restored = GoalWorkflowRunner.resume_from_checkpoint(
        session_id="sess-cp", goal_id="g1", config=CFG,
        store=store, workspace=tmp_path, execution_lease=lease,
    )
    assert restored is not None

    seen: dict = {}
    started = threading.Event()
    release_exec = threading.Event()

    class SlowRuntime:
        def __init__(self, *a, **k):
            pass

        def execute(self, *a, **k):
            seen["holder_during_execute"] = lease.holder("sess-cp")
            started.set()
            release_exec.wait(timeout=5)
            return SimpleNamespace(success=True)

    # *a/**k：SwarmRuntime 接受 on_event=（agent 事件接线），桩要兼容。
    monkeypatch.setattr(rt_mod, "SwarmRuntime", lambda *a, **k: SlowRuntime())

    async def main() -> str:
        chat_token = lease.acquire("sess-cp", "chat:run-1")
        assert chat_token is not None  # 预占：模拟 chat 执行中

        task = asyncio.create_task(restored.resume_and_continue())
        # 让 resume 进入自旋（0.25s 周期），租约仍归 chat
        await asyncio.sleep(0.35)
        assert not task.done()
        assert lease.holder("sess-cp") == "chat:run-1"

        # chat 释放 → resume 获取并执行（execute 内观测到 goal 持有）。
        # 注意：started.wait() 是阻塞调用，不能放在事件循环线程里——
        # 会把整个 loop 卡死、resume 任务永远跑不到 execute。轮询代替。
        lease.release("sess-cp", chat_token)
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.05)
        assert started.is_set()
        assert lease.holder("sess-cp") == "goal:g1"
        release_exec.set()
        return await asyncio.wait_for(task, timeout=5)

    goal_id = asyncio.run(main())
    assert goal_id == "g1"
    assert seen["holder_during_execute"] == "goal:g1"
    # 成功路径 finally 释放
    assert lease.holding("sess-cp") is False
    assert lease.acquire("sess-cp", "chat:run-2") is not None


def test_resume_and_continue_releases_lease_on_runtime_failure(
    cp_env: Path, tmp_path: Path, monkeypatch,
):
    """异常路径也释放（finally）：execute 抛错 → 租约归还。"""
    import agentnodes.kernel.swarm.runtime as rt_mod

    from agentnodes.kernel.gateway.session_lease import SessionExecutionLease

    store = FakeStore()
    store.goals["g1"] = SimpleNamespace(objective="obj")
    runner = _make_runner(store, tmp_path)
    _seed_hook(runner, output="第一层产出")
    runner.checkpoint()

    lease = SessionExecutionLease()
    restored = GoalWorkflowRunner.resume_from_checkpoint(
        session_id="sess-cp", goal_id="g1", config=CFG,
        store=store, workspace=tmp_path, execution_lease=lease,
    )
    assert restored is not None

    class Boom:
        def __init__(self, *a, **k):
            pass

        def execute(self, *a, **k):
            raise RuntimeError("runtime exploded")

    monkeypatch.setattr(rt_mod, "SwarmRuntime", lambda *a, **k: Boom())
    asyncio.run(restored.resume_and_continue())
    assert lease.holding("sess-cp") is False
