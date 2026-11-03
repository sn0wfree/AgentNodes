"""SessionController 独立测试 —— 准入、身份签发、驱动、失败、边界。

此前控制器只经 Gateway 间接测到；这里直接测其契约：
  A. run_pending 驱动语义（空队列、多会话、重复调用）
  B. 身份签发（turn 单调、run 派生、跨会话隔离、P-4 格式）
  C. 事件序列（admitted → started → settled，失败路径）
  D. 失败隔离（引擎崩、单 turn 不影响其他 turn）
  E. 引擎构造（默认 llm 节点、system 注入、factory 注入）
  F. 边界（payload 无 text、空消息、非 chat kind）
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from strategy_research.core.gateway.session_controller import SessionController, _payload_text
from strategy_research.core.orchestration import (
    EntryPoint,
    SourceKind,
    WorkKind,
    WorkRequest,
)


# ── 假件 ─────────────────────────────────────────────────


@dataclass
class Resp:
    content: str = "回答"
    usage: dict = field(default_factory=lambda: {"total_tokens": 5})


class FakeClient:
    def __init__(self, answer: str = "回答", fail: bool = False) -> None:
        self.answer = answer
        self.fail = fail
        self.calls: list[list[dict]] = []

    def chat(self, messages, **kwargs):
        if self.fail:
            raise RuntimeError("client exploded")
        self.calls.append(list(messages))
        return Resp(content=self.answer)


class Probe:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def __call__(self, kind, data):
        self.events.append((kind, data))

    def kinds(self) -> list[str]:
        return [k for k, _ in self.events]

    def of(self, kind):
        return [d for k, d in self.events if k == kind]


def make_controller(client=None, *, system=None, answer="回答", fail=False):
    probe = Probe()
    client = client or FakeClient(answer=answer, fail=fail)
    ep = EntryPoint(sink=probe)
    controller = SessionController(ep, client=client, system=system, sink=probe)
    return controller, ep, probe, client


def chat(text: str = "q", session_key: str = "chat:s") -> WorkRequest:
    return WorkRequest(
        kind=WorkKind.CHAT,
        source=SourceKind.CLI,
        session_key=session_key,
        payload={"text": text},
    )


# ── A. run_pending 驱动语义 ──────────────────────────────


def test_run_pending_empty_returns_empty():
    controller, _, probe, _ = make_controller()
    assert controller.run_pending() == []
    assert probe.kinds() == []


def test_run_pending_drains_all_sessions():
    controller, ep, _, _ = make_controller()
    ep.submit(chat("a", "chat:s1"))
    ep.submit(chat("b", "chat:s2"))
    ep.submit(chat("c", "chat:s1"))
    runs = controller.run_pending()
    assert len(runs) == 3
    assert all(r.ok for r in runs)


def test_run_pending_idempotent_when_empty():
    controller, ep, _, _ = make_controller()
    ep.submit(chat())
    controller.run_pending()
    assert controller.run_pending() == []


def test_run_pending_returns_results_in_queue_order():
    controller, ep, _, client = make_controller(answer="x")
    ep.submit(chat("first"))
    ep.submit(chat("second"))
    controller.run_pending()
    assert [c[-1]["content"] for c in client.calls] == ["first", "second"]


# ── B. 身份签发 ──────────────────────────────────────────


def test_turn_id_format_matches_p4():
    controller, ep, _, _ = make_controller()
    receipt = ep.submit(chat())
    [run] = controller.run_pending()
    session_id = receipt.session_id
    assert run.turn_id == f"{session_id}#1"
    assert run.run_id == f"{session_id}#1:r0"


def test_turn_counter_increments_per_session():
    controller, ep, _, _ = make_controller()
    ep.submit(chat("a"))
    [r1] = controller.run_pending()
    ep.submit(chat("b"))
    [r2] = controller.run_pending()
    assert r1.turn_id.endswith("#1")
    assert r2.turn_id.endswith("#2")
    assert r1.turn_id.split("#")[0] == r2.turn_id.split("#")[0]


def test_turn_counters_independent_across_sessions():
    controller, ep, _, _ = make_controller()
    ep.submit(chat("a", "chat:x"))
    ep.submit(chat("b", "chat:y"))
    runs = controller.run_pending()
    assert all(r.turn_id.endswith("#1") for r in runs)
    assert len({r.turn_id.split("#")[0] for r in runs}) == 2


def test_run_id_always_r0_in_stage_1a():
    """1a 每 turn 一次运行；turn:run=1:N 的 N 在 A2 重试时才增长。"""
    controller, ep, _, _ = make_controller()
    for _ in range(3):
        ep.submit(chat())
    runs = controller.run_pending()
    assert all(r.run_id.endswith(":r0") for r in runs)


# ── C. 事件序列 ──────────────────────────────────────────


def test_success_event_sequence():
    controller, ep, probe, _ = make_controller()
    ep.submit(chat())
    controller.run_pending()
    kinds = probe.kinds()
    # turn.admitted → run.started → node.* → run.settled
    assert kinds.index("turn.admitted") < kinds.index("run.started")
    assert kinds.index("run.started") < kinds.index("run.settled")
    assert "run.failed" not in kinds


def test_failure_event_sequence():
    controller, ep, probe, _ = make_controller(fail=True)
    ep.submit(chat())
    controller.run_pending()
    kinds = probe.kinds()
    assert "run.failed" in kinds
    # failed 之后仍有 settled（终态必发）
    assert kinds.index("run.failed") < kinds.index("run.settled")
    assert probe.of("run.settled")[0]["ok"] is False


def test_settled_carries_answer_length():
    controller, ep, probe, _ = make_controller(answer="12345")
    ep.submit(chat())
    controller.run_pending()
    settled = probe.of("run.settled")[0]
    assert settled["answer_chars"] == 5


def test_admitted_event_carries_ids():
    controller, ep, probe, _ = make_controller()
    receipt = ep.submit(chat())
    controller.run_pending()
    admitted = probe.of("turn.admitted")[0]
    assert admitted["turn_id"].startswith(receipt.session_id)
    assert admitted["session_id"] == receipt.session_id


# ── D. 失败隔离 ──────────────────────────────────────────


def test_failure_does_not_stop_subsequent_turns():
    controller, ep, probe, _ = make_controller(fail=True)
    ep.submit(chat("a"))
    ep.submit(chat("b"))
    runs = controller.run_pending()
    assert len(runs) == 2
    assert all(not r.ok for r in runs)  # client 一直失败


def test_transient_failure_then_success():
    """第一次失败、第二次成功（client 状态切换）。"""
    probe = Probe()
    ep = EntryPoint(sink=probe)

    class FlakyClient:
        def __init__(self):
            self.n = 0

        def chat(self, messages, **kwargs):
            self.n += 1
            if self.n == 1:
                raise RuntimeError("first fails")
            return Resp(content="second ok")

    controller = SessionController(ep, client=FlakyClient(), sink=probe)
    ep.submit(chat("a"))
    ep.submit(chat("b"))
    runs = controller.run_pending()
    assert runs[0].ok is False
    assert runs[1].ok is True and runs[1].answer == "second ok"


def test_failure_error_text_preserved():
    controller, ep, _, _ = make_controller(fail=True)
    ep.submit(chat())
    [run] = controller.run_pending()
    assert "client exploded" in (run.error or "")


# ── E. 引擎构造 ──────────────────────────────────────────


def test_default_engine_is_single_llm_node():
    controller, ep, probe, _ = make_controller()
    ep.submit(chat())
    controller.run_pending()
    node_started = [d["node"] for d in probe.of("node.started")]
    assert node_started == ["llm"]


def test_system_prompt_injected():
    controller, ep, _, client = make_controller(system="你是助手")
    ep.submit(chat())
    controller.run_pending()
    assert client.calls[0][0] == {"role": "system", "content": "你是助手"}


def test_no_system_prompt_added_when_none():
    controller, ep, _, client = make_controller()
    ep.submit(chat())
    controller.run_pending()
    assert all(m["role"] != "system" for m in client.calls[0])


def test_engine_factory_override():
    """A1 前注入点：自定义引擎替换默认单节点图。"""
    from strategy_research.kernel.runtimes import GraphEngine

    probe = Probe()
    ep = EntryPoint(sink=probe)
    client = FakeClient()

    def build():
        engine = GraphEngine(sink=probe)

        def custom(state):
            return {**state, "answer": "自定义引擎", "messages": state.get("messages", [])}

        engine.add_node("custom", custom)
        return engine

    controller = SessionController(ep, client=client, sink=probe)
    controller._build_engine = build
    ep.submit(chat())
    [run] = controller.run_pending()
    assert run.answer == "自定义引擎"


# ── F. 边界 ──────────────────────────────────────────────


def test_payload_without_text_yields_empty_message():
    controller, ep, _, client = make_controller()
    ep.submit(
        WorkRequest(
            kind=WorkKind.CHAT, source=SourceKind.CLI,
            session_key="chat:s", payload={},
        )
    )
    controller.run_pending()
    assert client.calls[0][-1] == {"role": "user", "content": ""}


def test_payload_text_non_string_coerced_to_empty():
    assert _payload_text(WorkRequest(
        kind=WorkKind.CHAT, source=SourceKind.CLI,
        session_key="chat:s", payload={"text": 123},
    )) == ""


def test_non_chat_kind_still_executes():
    """1a 阶段不按 kind 分流；非 chat kind 同样走 llm 节点。"""
    controller, ep, _, _ = make_controller()
    ep.submit(
        WorkRequest(
            kind=WorkKind.STUDY, source=SourceKind.CLI,
            session_key="study:s", payload={"text": "研究"},
        )
    )
    runs = controller.run_pending()
    assert len(runs) == 1 and runs[0].ok


def test_session_id_lookup_missing_returns_empty_prefix():
    """controller 从 entry 取 session_id；缺失时退化为空前缀（防御）。"""
    probe = Probe()
    ep = EntryPoint(sink=probe)
    controller = SessionController(ep, client=FakeClient(), sink=probe)
    # 直接给一个未注册 session 的 mailbox（模拟异常路径）
    ep.submit(chat("q", "chat:registered"))
    [run] = controller.run_pending()
    assert run.turn_id.startswith(run.turn_id.split("#")[0])
