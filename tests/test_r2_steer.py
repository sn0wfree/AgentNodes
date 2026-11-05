"""R2.2 steer 真注入测试——路由、安全点注入、滞留晋升、溢出。

设计：docs/archive/2026-10/stage-r2-consumer-steer-aging-design-20261030.md §3
  A. 无活跃 run：steer 直接成正常 turn（steer.became_turn）
  B. 有活跃 run：入 steer 队（steer.queued），不进主队
  C. 安全点注入：react 节点 drain 并追加为 user 消息（steer.applied）
  D. 滞留晋升：turn 收尾未被消费的 steer 转主队（不丢消息）
  E. 溢出：steer_cap 满后最旧转主队（steer.overflow_to_queue）
  F. 异常路径：引擎崩时滞留 steer 同样晋升
"""
from __future__ import annotations

from dataclasses import dataclass, field

from strategy_research.kernel.gateway import SessionController, SessionFence
from strategy_research.kernel.orchestration import (
    EntryPoint,
    Mailbox,
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


def make_req(text="插话", *, mode=QueueMode.STEER, session_key="chat:s") -> WorkRequest:
    return WorkRequest(
        kind=WorkKind.CHAT, source=SourceKind.CLI,
        session_key=session_key, payload={"text": text},
        queue_mode=mode,
    )


# ── A. 无活跃 run → became_turn ──────────────────────────


def test_steer_without_active_run_becomes_turn():
    probe = Probe()
    ep = EntryPoint(sink=probe)  # 无 is_session_active → 视为无活跃
    receipt = ep.submit(make_req())
    assert receipt.accepted
    assert len(probe.of("steer.became_turn")) == 1
    assert probe.of("steer.queued") == []
    mb = ep.mailbox_for("chat:s")
    assert mb is not None and mb.peek_depth() == 1


# ── B. 有活跃 run → steer 队 ─────────────────────────────


def test_steer_with_active_run_goes_to_steer_queue():
    fence = SessionFence()
    probe = Probe()
    ep = EntryPoint(sink=probe, is_session_active=fence.active_run)
    ep.submit(make_req(text="首轮"))  # 未认领 → became_turn
    assert len(probe.of("steer.became_turn")) == 1

    sid = ep.session_id_for("chat:s")
    fence.claim("s", f"{sid}#1:r0")  # 模拟活跃 run（fence 域=物理 id "s"）
    receipt = ep.submit(make_req(text="插话"))
    assert receipt.accepted
    assert len(probe.of("steer.queued")) == 1
    mb = ep.mailbox_for("chat:s")
    assert mb.peek_depth() == 1  # 主队只有首轮，steer 不占主队
    assert mb.drain_steer() == ["插话"]


# ── C. 安全点注入 ────────────────────────────────────────


def test_steer_injected_at_safety_point():
    fence = SessionFence()
    probe = Probe()
    ep = EntryPoint(sink=probe, is_session_active=fence.active_run)

    drained_seen: list[list[str]] = []

    class Engine:
        def run(self, state):
            inbox = state.get("steer_inbox")
            assert inbox is not None
            drained_seen.append(inbox.drain())
            return {"answer": "done"}

    controller = SessionController(
        ep, client=FakeClient(), sink=probe,
        fence=fence, engine_factory=lambda: Engine(),
    )
    ep.submit(make_req(text="首轮", mode=QueueMode.FOLLOWUP))
    token = fence.claim("s", "s#1:r0")  # 提交期模拟活跃 run（域=物理 id，R3）
    ep.submit(make_req(text="看一下回测"))
    ep.submit(make_req(text="再加个止损"))
    fence.release(token)  # 释放后控制器才消费（R3：占用即跳过）

    runs = controller.run_pending()
    assert runs[0].ok
    assert drained_seen[0] == ["看一下回测", "再加个止损"]  # 合批 drain
    assert len(probe.of("steer.applied")) == 1
    assert probe.of("steer.applied")[0]["count"] == 2


# ── D. 滞留晋升 ──────────────────────────────────────────


def test_leftover_steer_promoted_after_turn():
    probe = Probe()
    ep = EntryPoint(sink=probe, is_session_active=lambda sid: True)
    seen_texts: list[str] = []

    class Engine:
        def run(self, state):
            texts = [
                m.get("content") for m in state.get("messages") or []
                if m.get("role") == "user"
            ]
            seen_texts.append(str(texts[-1] if texts else ""))
            return {"answer": "done"}  # 不 drain → steer 滞留

    fence = SessionFence()
    controller = SessionController(
        ep, client=FakeClient(), sink=probe,
        fence=fence, engine_factory=lambda: Engine(),
    )
    ep.submit(make_req(text="活", mode=QueueMode.FOLLOWUP))
    ep.submit(make_req(text="来晚了"))
    runs = controller.run_pending()
    # 收尾晋升：滞留 steer 成为主队工单，并在同一轮 run_pending 里被执行
    assert len(runs) == 2 and all(r.ok for r in runs)
    assert seen_texts == ["活", "来晚了"]
    assert len(probe.of("steer.became_turn")) >= 1
    assert ep.mailbox_for("chat:s").peek_depth() == 0  # 全部消费完


# ── E. 溢出 ──────────────────────────────────────────────


def test_steer_cap_overflow_promotes_oldest():
    probe = Probe()
    mb = Mailbox("sess-x", steer_cap=2, sink=probe)
    for text in ("a", "b", "c"):
        mb.submit(make_req(text=text), resolve_session=lambda k: "s",
                  is_session_active=True)
    assert len(probe.of("steer.overflow_to_queue")) == 1
    assert mb.peek_depth() == 1  # 最旧的 "a" 被转主队
    assert mb.drain_steer() == ["b", "c"]


# ── F. 异常路径晋升 ──────────────────────────────────────


def test_crash_promotes_leftover_steer():
    class Boom:
        def chat(self, messages, **kwargs):
            raise RuntimeError("client exploded")

    probe = Probe()
    ep = EntryPoint(sink=probe, is_session_active=lambda sid: True)
    fence = SessionFence()
    controller = SessionController(
        ep, client=Boom(), sink=probe,
        fence=fence,  # 默认 llm 节点：不 drain steer_inbox
    )
    ep.submit(make_req(text="崩", mode=QueueMode.FOLLOWUP))
    ep.submit(make_req(text="滞留"))
    runs = controller.run_pending()
    assert runs[0].ok is False
    # 滞留 steer 晋升后被同一轮 run_pending 消费（client 仍炸 → 两个失败）
    assert len(runs) == 2 and all(r.ok is False for r in runs)
    assert len(probe.of("run.failed")) == 2
