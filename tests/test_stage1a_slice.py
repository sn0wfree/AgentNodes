"""阶段 1a 端到端切片测试 —— 一条真实请求走完新架构八站。

链路：EntryPoint.submit → mailbox → SessionController 准入（turn/run 签发）
→ GraphEngine（llm 节点，Fake client）→ 事件流 → 回答。

验收（replan §阶段 1a）：事件序列完整可断言；turn/run 身份符合 P-4；
失败路径不拖垮控制器；多会话隔离。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from strategy_research.kernel.gateway import SessionController
from strategy_research.kernel.orchestration import (
    EntryPoint,
    SourceKind,
    WorkKind,
    WorkRequest,
)
from strategy_research.kernel.orchestration.entry_point import EntryPoint


@dataclass
class FakeResponse:
    content: str = ""
    usage: dict = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.usage is None:
            self.usage = {"total_tokens": 10}


class FakeLLM:
    """可编程假客户端：按序返回预设回答；前 fail_first 次调用抛异常（模拟瞬时故障）。"""

    def __init__(self, answers: list[str] | None = None, fail_first: int = 0) -> None:
        self.answers = list(answers or ["好的，明白了。"])
        self.calls: list[list[dict]] = []
        self.fail_first = fail_first
        self._call_count = 0

    def chat(self, messages: list[dict], **kwargs: Any) -> FakeResponse:
        self._call_count += 1
        if self._call_count <= self.fail_first:
            raise RuntimeError("API down")
        self.calls.append(list(messages))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return FakeResponse(content=answer)


class Probe:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def __call__(self, kind: str, data: dict) -> None:
        self.events.append((kind, data))

    def kinds(self) -> list[str]:
        return [k for k, _ in self.events]


def build_slice(answers: list[str] | None = None):
    """装配完整切片：EntryPoint + 控制器 + 共享事件探针。"""
    probe = Probe()
    llm = FakeLLM(answers)
    ep = EntryPoint(sink=probe)
    controller = SessionController(ep, client=llm, sink=probe)
    return ep, controller, llm, probe


def chat_request(text: str, session_key: str = "chat:main") -> WorkRequest:
    return WorkRequest(
        kind=WorkKind.CHAT,
        source=SourceKind.API_CHAT,
        session_key=session_key,
        payload={"text": text},
    )


# ── 主链路 ───────────────────────────────────────────────


def test_full_slice_single_turn():
    ep, controller, llm, probe = build_slice(answers=["动量因子是价格对过去收益的依赖。"])

    receipt = ep.submit(chat_request("什么是动量因子？"))
    assert receipt.accepted

    results = controller.run_pending()

    assert len(results) == 1
    run = results[0]
    assert run.ok
    assert "动量因子" in run.answer

    # LLM 收到用户消息
    assert len(llm.calls) == 1
    assert llm.calls[0][-1] == {"role": "user", "content": "什么是动量因子？"}

    # 事件流八站齐全：api_chat 默认 steer → 无活跃 run 直接成 turn（R2.2）
    kinds = probe.kinds()
    assert kinds[0] == "steer.became_turn"
    assert kinds[1] == "work.enqueued"
    assert "turn.admitted" in kinds
    assert "run.started" in kinds
    assert "node.started" in kinds and "node.completed" in kinds
    assert kinds[-1] == "run.settled"
    assert kinds.index("turn.admitted") < kinds.index("run.started") < kinds.index("node.started")


# ── P-4 身份格式 ─────────────────────────────────────────


def test_identity_hierarchy_per_p4():
    ep, controller, _, probe = build_slice()

    receipt = ep.submit(chat_request("hi"))
    session_id = receipt.session_id
    [run] = controller.run_pending()

    assert run.turn_id == f"{session_id}#1"
    assert run.run_id == f"{session_id}#1:r0"

    ep.submit(chat_request("again"))
    [run2] = controller.run_pending()
    assert run2.turn_id == f"{session_id}#2"          # turn 单调
    assert run2.run_id == f"{session_id}#2:r0"        # 每 turn 新 run


def test_turn_counters_isolated_across_sessions():
    ep, controller, _, _ = build_slice()
    ep.submit(chat_request("a", session_key="chat:a"))
    ep.submit(chat_request("b", session_key="chat:b"))
    results = controller.run_pending()
    ids = {r.turn_id.split("#")[0] for r in results}
    assert len(ids) == 2  # 两个不同 session_id
    assert all(r.turn_id.endswith("#1") for r in results)  # 各自从 1 计数


# ── 失败路径 ─────────────────────────────────────────────


def test_llm_failure_does_not_break_controller():
    ep, controller, llm, probe = build_slice()
    llm.fail_first = 1  # 第一次调用失败（瞬时故障），之后恢复

    ep.submit(chat_request("boom"))
    ep.submit(chat_request("next"))  # 崩溃后队列继续
    results = controller.run_pending()

    assert results[0].ok is False
    assert "API down" in (results[0].error or "")
    assert results[1].ok is True  # 第二个 turn 不受影响
    settled = [d for k, d in probe.events if k == "run.settled"]
    assert [s["ok"] for s in settled] == [False, True]
    failed = [d for k, d in probe.events if k == "run.failed"]
    assert len(failed) == 1


# ── 多 turn 排队（submit 两次再统一驱动） ──────────────────


def test_two_turns_queue_then_drain_in_order():
    ep, controller, llm, _ = build_slice(answers=["first", "second"])
    ep.submit(chat_request("q1"))
    ep.submit(chat_request("q2"))
    results = controller.run_pending()
    assert [r.answer for r in results] == ["first", "second"]  # FIFO
    assert len(llm.calls) == 2
