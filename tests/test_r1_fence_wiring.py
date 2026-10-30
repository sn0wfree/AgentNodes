"""R1 fence 挂根测试 —— INV-4/INV-5 落进控制器的行为契约。

设计：docs/stage-r1-fence-wiring-design-20261030.md
  A. 正常轮：settle 前 fence 已 release
  B. 中途超越：ok=False + run.fenced + settled{fenced:True}；栅栏归超越者
  C. 无 fence：行为不变（等价门）
  D. Gateway 注入同一性（唯一实例，无双栅栏）
  E. 连续两 turn：release 不误伤后继者
  F. 引擎崩 + fence：失败路径释放，下一 turn 可跑
  G. INV-4 进注册表：verify_all 全绿
"""
from __future__ import annotations

from dataclasses import dataclass, field

from strategy_research.core.gateway import (
    Gateway,
    SessionController,
    SessionFence,
    invariants,
    verify_all,
)
from strategy_research.core.orchestration import (
    EntryPoint,
    SourceKind,
    WorkKind,
    WorkRequest,
)


# ── 假件（与 test_session_controller 同型）────────────────


@dataclass
class Resp:
    content: str = "回答"
    usage: dict = field(default_factory=lambda: {"total_tokens": 5})


class FakeClient:
    def __init__(self, answer: str = "回答", fail_times: int = 0) -> None:
        self.answer = answer
        self.fail_times = fail_times

    def chat(self, messages, **kwargs):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("client exploded")
        return Resp(content=self.answer)


class Probe:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def __call__(self, kind, data):
        self.events.append((kind, data))

    def of(self, kind):
        return [d for k, d in self.events if k == kind]


def make_controller(*, fence=None, engine=None, client=None):
    probe = Probe()
    ep = EntryPoint(sink=probe)
    kwargs: dict = dict(client=client or FakeClient(), sink=probe, fence=fence)
    if engine is not None:
        kwargs["engine_factory"] = lambda: engine
    return SessionController(ep, **kwargs), ep, probe


def chat(text: str = "q", session_key: str = "chat:s") -> WorkRequest:
    return WorkRequest(
        kind=WorkKind.CHAT,
        source=SourceKind.CLI,
        session_key=session_key,
        payload={"text": text},
    )


# ── A. 正常轮 ────────────────────────────────────────────


def test_normal_turn_releases_fence():
    fence = SessionFence()
    controller, ep, probe = make_controller(fence=fence)
    ep.submit(chat())
    runs = controller.run_pending()
    assert len(runs) == 1 and runs[0].ok
    assert fence.active_run("s") is None
    assert probe.of("run.fenced") == []


# ── B. 中途超越 ──────────────────────────────────────────


def test_superseded_run_is_fenced():
    fence = SessionFence()

    class SupersedingEngine:
        def run(self, state):
            fence.claim(
                str(state.get("fence_domain") or ""),
                f"{state.get('run_id')}:next",
            )
            return {"answer": "stale"}

    controller, ep, probe = make_controller(
        fence=fence, engine=SupersedingEngine(),
    )
    ep.submit(chat())
    runs = controller.run_pending()
    assert len(runs) == 1
    assert runs[0].ok is False
    assert runs[0].error is not None and runs[0].error.startswith("fenced:")

    fenced = probe.of("run.fenced")
    assert len(fenced) == 1 and fenced[0]["run_id"] == runs[0].run_id
    assert fenced[0]["reason"]

    settled = probe.of("run.settled")
    assert settled[-1]["ok"] is False
    assert settled[-1]["fenced"] is True

    # 栅栏归超越者持有：active 指向后继 run，先到者未误释放
    assert fence.active_run("s") == f"{runs[0].run_id}:next"


# ── C. 无 fence 等价 ─────────────────────────────────────


def test_no_fence_path_unchanged():
    controller, ep, probe = make_controller()
    ep.submit(chat())
    runs = controller.run_pending()
    assert runs[0].ok
    assert probe.of("run.fenced") == []
    settled = probe.of("run.settled")[-1]
    assert "fenced" not in settled


# ── D. Gateway 注入同一性 ────────────────────────────────


def test_gateway_wires_its_own_fence():
    gw = Gateway(client=FakeClient())
    assert gw.controller._fence is gw.fence


# ── E. 连续两 turn ───────────────────────────────────────


def test_two_consecutive_turns_both_pass():
    fence = SessionFence()
    controller, ep, _ = make_controller(fence=fence)
    ep.submit(chat("q1"))
    ep.submit(chat("q2"))
    runs = controller.run_pending()
    assert [r.ok for r in runs] == [True, True]
    assert fence.active_run("s") is None


# ── F. 引擎崩 + fence ────────────────────────────────────


def test_engine_crash_releases_fence():
    fence = SessionFence()
    controller, ep, probe = make_controller(
        fence=fence, client=FakeClient(fail_times=1),
    )
    ep.submit(chat("boom"))
    runs = controller.run_pending()
    assert runs[0].ok is False
    assert probe.of("run.failed")
    assert fence.active_run("s") is None

    ep.submit(chat("ok"))
    reruns = controller.run_pending()
    assert reruns[0].ok


# ── G. INV-4 注册表 ──────────────────────────────────────


def test_inv4_registered_and_all_green():
    ids = [inv.id for inv in invariants()]
    assert "INV-4" in ids
    ok, violations = verify_all(raise_on_violation=False)
    assert ok, violations
