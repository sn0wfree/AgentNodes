"""B1 interrupt 语义测试——CancelScope、节点边界取消、controller 收尾。

设计：docs/archive/2026-10/stage-b1-interrupt-design-20261030.md
  A. CancelScope 基本语义（单向、幂等、reason 首次生效）
  B. 引擎节点边界响应取消（进入前 / 结束后两个检查点）
  C. 控制器：取消不发 run.answer，发 run.cancelled；fence 释放
  D. interrupt 工单在有活跃 run 时触发取消
  E. 取消后下一 turn 正常接上
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

import pytest

from strategy_research.kernel.gateway import Gateway, SessionController, SessionFence
from strategy_research.kernel.orchestration import (
    EntryPoint,
    QueueMode,
    SourceKind,
    WorkKind,
    WorkRequest,
)
from strategy_research.kernel.runtimes.cancel_scope import CancelScope, RunCancelled
from strategy_research.kernel.runtimes.graph_engine import INTERRUPT_KEY, GraphEngine


# ── A. CancelScope ───────────────────────────────────────


def test_cancel_scope_basic():
    s = CancelScope()
    assert not s.cancelled and s.reason is None
    s.cancel(reason="interrupt")
    assert s.cancelled and s.reason == "interrupt"
    with pytest.raises(RunCancelled) as ei:
        s.raise_if_cancelled()
    assert ei.value.reason == "interrupt"


def test_cancel_scope_is_one_way_and_idempotent():
    s = CancelScope()
    s.cancel(reason="first")
    s.cancel(reason="second")  # 不覆盖
    assert s.reason == "first"
    assert s.cancelled


# ── B. 引擎节点边界 ──────────────────────────────────────


def test_engine_stops_at_node_boundary_after_cancel():
    scope = CancelScope()
    ran: list[str] = []
    engine = GraphEngine()

    def n1(state):
        ran.append("n1")
        scope.cancel(reason="user")  # 节点内触发
        return state

    def n2(state):
        ran.append("n2")  # 不应执行
        return state

    engine.add_node("n1", n1).add_node("n2", n2)
    engine.set_entry("n1")
    engine.add_edge("n1", "n2")

    out = engine.run({"cancel_scope": scope})
    assert ran == ["n1"]
    assert INTERRUPT_KEY in out


def test_engine_cancelled_before_start_runs_nothing():
    scope = CancelScope()
    scope.cancel()
    ran: list[str] = []
    engine = GraphEngine()
    engine.add_node("n1", lambda s: (ran.append("n1"), s)[1])
    engine.set_entry("n1")

    out = engine.run({"cancel_scope": scope})
    assert ran == []
    assert INTERRUPT_KEY in out


def test_engine_node_raising_runcancelled_is_absorbed():
    engine = GraphEngine()

    def n1(state):
        raise RunCancelled("tool")

    engine.add_node("n1", n1)
    engine.set_entry("n1")
    out = engine.run({})
    assert INTERRUPT_KEY in out
    assert "tool" in str(out[INTERRUPT_KEY])


# ── C. 控制器收尾语义 ────────────────────────────────────


@dataclass
class Resp:
    content: str = "回答"
    usage: dict = field(default_factory=lambda: {"total_tokens": 5})


class FakeClient:
    def chat(self, messages, **kwargs):
        return Resp()


class Probe:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def __call__(self, kind, data):
        self.events.append((kind, data))

    def of(self, kind):
        return [d for k, d in self.events if k == kind]


def chat(text="q", key="chat:s") -> WorkRequest:
    return WorkRequest(
        kind=WorkKind.CHAT, source=SourceKind.CLI,
        session_key=key, payload={"text": text},
    )


def test_cancelled_run_emits_no_answer_and_releases_fence():
    fence = SessionFence()
    registry: dict = {}
    probe = Probe()
    ep = EntryPoint(sink=probe)

    class CancellingEngine:
        def run(self, state):
            scope = state["cancel_scope"]
            scope.cancel(reason="interrupt")
            return {"answer": "不应出现", "interrupt": {"reason": "cancelled"}}

    controller = SessionController(
        ep, client=FakeClient(), sink=probe, fence=fence,
        cancel_registry=registry, engine_factory=lambda: CancellingEngine(),
    )
    ep.submit(chat())
    runs = controller.run_pending()

    assert len(runs) == 1
    assert runs[0].ok is False
    assert "cancelled" in (runs[0].error or "")
    # 无产物
    assert probe.of("run.answer") == []
    # 明确事件
    assert len(probe.of("run.cancelled")) == 1
    settled = probe.of("run.settled")[-1]
    assert settled["ok"] is False and settled["cancelled"] is True
    # 栅栏已释放，registry 已清
    assert fence.active_run("s") is None
    assert registry == {}


# ── D/E. interrupt 触发取消 + 下一 turn 接上 ─────────────


def test_interrupt_request_cancels_active_run():
    """有活跃 run 时提交 INTERRUPT → 取消该 run。"""
    gw = Gateway(client=FakeClient())
    scope = CancelScope()
    gw._cancel_scopes["x"] = scope  # 域=物理 session id（R3 约定）

    req = WorkRequest(
        kind=WorkKind.CHAT, source=SourceKind.SCHEDULER,
        session_key="chat:x", payload={"text": "打断"},
        queue_mode=QueueMode.INTERRUPT,
    )
    # fence 里先放一个活跃 run（is_session_active 依据）
    gw.fence.claim("x", "run-1")
    gw.entry.submit(req)
    assert scope.cancelled is True


def test_cancel_session_api():
    gw = Gateway(client=FakeClient())
    assert gw.cancel_session("nobody") is False  # 无活跃 run
    scope = CancelScope()
    gw._cancel_scopes["sess-1"] = scope
    assert gw.cancel_session("sess-1", reason="user") is True
    assert scope.cancelled and scope.reason == "user"
    assert gw.cancel_session("sess-1") is True  # 幂等


def test_next_turn_runs_after_cancellation():
    """取消后同 session 的下一个 turn 正常执行（栅栏已释放）。"""
    fence = SessionFence()
    probe = Probe()
    ep = EntryPoint(sink=probe)
    calls = {"n": 0}

    class CancelOnce:
        def run(self, state):
            calls["n"] += 1
            if calls["n"] == 1:
                state["cancel_scope"].cancel(reason="interrupt")
                return {"interrupt": {"reason": "cancelled"}}
            return {"answer": "第二轮成功"}

    controller = SessionController(
        ep, client=FakeClient(), sink=probe, fence=fence,
        engine_factory=lambda: CancelOnce(),
    )
    ep.submit(chat("q1"))
    ep.submit(chat("q2"))
    runs = controller.run_pending()

    assert [r.ok for r in runs] == [False, True]
    assert runs[1].answer == "第二轮成功"
    assert fence.active_run("s") is None


def test_thread_safe_cancel():
    """取消信号跨线程可见（消费者线程 vs 请求线程）。"""
    scope = CancelScope()
    seen: list[bool] = []

    def worker():
        while not scope.cancelled:
            pass
        seen.append(True)

    t = threading.Thread(target=worker)
    t.start()
    scope.cancel(reason="cross-thread")
    t.join(2.0)
    assert seen == [True]
