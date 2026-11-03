"""R3 同 session 串行域测试——跨 kind（chat × study round）互斥。

设计：docs/stage-r3-session-domain-design-20261030.md
  A. fence 域：chat:{X} 与 automation:{X} 归一为物理 id X
  B. chat 侧：session 被占（study 占栏）→ run_pending 跳过，释放后消费
  C. study 侧：_run_one_study 执行期间占栏、结束释放（打桩 _run_one_study_locked）
  D. study 侧等待：fence 被占时先等后占（同 session 两 study 串行）
  E. 交替全序：chat → study → chat 串行
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from strategy_research.core.gateway import SessionController, SessionFence
from strategy_research.core.gateway.session_controller import _fence_domain
from strategy_research.kernel.orchestration import (
    EntryPoint,
    QueueMode,
    SourceKind,
    WorkKind,
    WorkRequest,
)


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


def chat_req(text="q", session_key="chat:s") -> WorkRequest:
    return WorkRequest(
        kind=WorkKind.CHAT, source=SourceKind.CLI,
        session_key=session_key, payload={"text": text},
    )


def study_req(study_id="st-1", session_key="automation:s") -> WorkRequest:
    return WorkRequest(
        kind=WorkKind.AUTOMATION, source=SourceKind.SCHEDULER,
        session_key=session_key,
        payload={"study_id": study_id, "session_id": "s"},
    )


# ── A. fence 域归一 ──────────────────────────────────────


def test_fence_domain_strips_kind_prefix():
    assert _fence_domain("chat:sess-1") == "sess-1"
    assert _fence_domain("automation:sess-1") == "sess-1"
    assert _fence_domain("mcp:x") == "x"
    assert _fence_domain("bare") == "bare"


# ── B. chat 侧跳过 ───────────────────────────────────────


def test_chat_consumer_skips_occupied_session():
    fence = SessionFence()
    probe = Probe()
    ep = EntryPoint(sink=probe)
    controller = SessionController(
        ep, client=FakeClient(), sink=probe, fence=fence,
    )
    ep.submit(chat_req())
    study_token = fence.claim("s", "study:st-1:r0")  # 模拟 study round 占栏

    assert controller.run_pending() == []  # 跳过，工单留队
    assert ep.mailbox_for("chat:s").peek_depth() == 1

    fence.release(study_token)  # 释放
    runs = controller.run_pending()
    assert len(runs) == 1 and runs[0].ok
    assert ep.mailbox_for("chat:s").peek_depth() == 0


# ── C/D. study 侧占栏与等待 ──────────────────────────────


class _FakeStudy:
    def __init__(self, session_id: str) -> None:
        self.study_id = "st-1"
        self.session_id = session_id
        self.owner_session_id = session_id
        self.execution_status = "queued"


class _FakeStore:
    def __init__(self, study: _FakeStudy) -> None:
        self.study = study

    def get_study(self, study_id):
        return self.study

    def update_execution_status(self, *a, **k):
        pass


def _make_scheduler(store, fence):
    """构造带假 gateway 的 StudyScheduler（不碰真单例——打桩取栅栏路径）。"""
    from strategy_research.core.study.scheduler import StudyScheduler

    sched = StudyScheduler(store)
    sched._per_user_limit = 0

    class _FakeGW:
        pass

    gw = _FakeGW()
    gw.fence = fence
    import strategy_research.core.study.scheduler as sched_mod

    original = sched_mod._get_gateway_singleton
    sched_mod._get_gateway_singleton = lambda: gw
    return sched, sched_mod, original


def test_study_execution_claims_and_releases_fence():
    fence = SessionFence()
    store = _FakeStore(_FakeStudy("s"))
    sched, sched_mod, original = _make_scheduler(store, fence)
    try:
        during: list[str | None] = []

        async def fake_locked(study_id):
            during.append(fence.active_run("s"))

        sched._run_one_study_locked = fake_locked  # type: ignore[method-assign]
        asyncio.run(sched._run_one_study("st-1"))
        assert during == ["study:st-1:r0"]  # 执行期间占栏
        assert fence.active_run("s") is None  # 结束释放
    finally:
        sched_mod._get_gateway_singleton = original


def test_study_waits_while_fence_busy_then_serializes():
    fence = SessionFence()
    store = _FakeStore(_FakeStudy("s"))
    sched, sched_mod, original = _make_scheduler(store, fence)
    try:
        order: list[str] = []
        token = fence.claim("s", "chat:turn-1")  # 先模拟 chat turn 占栏

        async def fake_locked(study_id):
            order.append(f"study:{fence.active_run('s')}")

        sched._run_one_study_locked = fake_locked  # type: ignore[method-assign]

        async def release_soon():
            await asyncio.sleep(0.6)
            order.append("chat-released")
            fence.release(token)

        async def run():
            await asyncio.gather(
                sched._run_one_study("st-1"), release_soon(),
            )

        asyncio.run(run())
        # study 等到 chat 释放后才执行
        assert order[0] == "chat-released"
        assert order[1] == "study:study:st-1:r0"
    finally:
        sched_mod._get_gateway_singleton = original


# ── E. 交替全序 ──────────────────────────────────────────


def test_chat_and_study_alternate_serially():
    """同一物理 session：study 占栏 → chat 等待；chat 执行 → 全序不重叠。"""
    fence = SessionFence()
    probe = Probe()
    ep = EntryPoint(sink=probe, is_session_active=fence.active_run)
    controller = SessionController(
        ep, client=FakeClient(), sink=probe, fence=fence,
    )
    # study 先占栏（模拟在跑的 round），chat 与 automation 工单都已提交
    fence.claim("s", "study:st-1:r0")
    ep.submit(chat_req("q1"))
    ep.submit(study_req())

    # chat 消费者：被占，跳过
    assert controller.run_pending({WorkKind.CHAT}) == []

    # study 结束释放 → chat 恢复
    fence.release(fence.claim("s", "study:st-1:r0"))
    runs = controller.run_pending({WorkKind.CHAT})
    assert len(runs) == 1 and runs[0].ok
