# -*- coding: utf-8 -*-
"""3a.3a：send_turn 深模块的直测（穿过唯一外部 seam）。

验收（决策 5/6/7）：历史预检失败零 Attempt 事件、成功路径、错误类型映射、
单例观察器幂等——全部用 fake adapter 直测，不戳内部同步原语。
"""
from __future__ import annotations

import pytest

from agentnodes.apps.chat.send_turn import (
    HistoryUnavailable,
    QueueFull,
    SendTurnDeps,
    SendTurnInput,
    SendTurnResult,
    send_turn,
)
from agentnodes.kernel.orchestration.contracts import QueueMode, RejectReason


class FakeStore:
    def __init__(self, messages=None, fail_history=False):
        self.messages = messages or []
        self.fail_history = fail_history

    def get_messages(self, session_id, limit=100):
        if self.fail_history:
            raise RuntimeError("db down")
        return self.messages


class FakeService:
    def __init__(self, messages=None, fail_history=False):
        self.store = FakeStore(messages, fail_history)

    def _convert_messages_to_history(self, stored, keep_all_compactions=False):
        return [{"role": m["role"], "content": m["content"]} for m in stored]


class FakeReceipt:
    def __init__(self, accepted=True, reason=None, session_id="s1", current_size=0):
        self.accepted = accepted
        self.reason = reason
        self.session_id = session_id
        self.current_size = current_size


class FakeMailbox:
    def peek_depth(self):
        return 2


class FakeEntry:
    def __init__(self, receipt):
        self._receipt = receipt
        self.submitted = []

    def submit(self, request, max_pending=None):
        self.submitted.append((request, max_pending))
        return self._receipt


class FakeGateway:
    def __init__(self, receipt=None):
        self.entry = FakeEntry(receipt or FakeReceipt())
        self._sinks = []
        self._mailbox = FakeMailbox()

    def add_sink(self, sink):
        self._sinks.append(sink)
        return lambda: None

    def consumer_running(self):
        return False

    def entry_mailbox_for(self, key):
        return self._mailbox


def _deps(gw, service, emitted, pushed):
    return SendTurnDeps(
        gateway=gw,
        service=service,
        emit=lambda sid, kind, data: emitted.append((sid, kind, data)),
        publish_sse=lambda kind, data, sid: pushed.append((sid, kind, data)),
        build_llm_config=lambda: None,
        wait_timeout=lambda: 1.0,
    )


@pytest.mark.asyncio
async def test_history_failure_raises_before_any_gateway_submit():
    """决策 5：历史预检失败 → HistoryUnavailable，Gateway 零 submit、
    零 emit（尤其无 attempt.created 半提交事件）。"""
    gw = FakeGateway()
    emitted, pushed = [], []
    deps = _deps(gw, FakeService(fail_history=True), emitted, pushed)
    inp = SendTurnInput(session_id="s1", content="hello", receipt_only=True)

    with pytest.raises(HistoryUnavailable) as exc:
        await send_turn(inp, deps)

    assert exc.value.detail["error"] == "history_unavailable"
    assert gw.entry.submitted == []
    assert emitted == [] and pushed == []
    assert gw._sinks == []


@pytest.mark.asyncio
async def test_receipt_only_result_fields():
    """receipt 路径：SendTurnResult 字段与现网响应逐字对齐。"""
    gw = FakeGateway()
    emitted, pushed = [], []
    deps = _deps(gw, FakeService(), emitted, pushed)
    inp = SendTurnInput(
        session_id="s1", content="hello", receipt_only=True,
        user_message_id="u1", assistant_message_id="a1", attempt_id="at1",
        queue_mode=QueueMode.FOLLOWUP,
    )
    result = await send_turn(inp, deps)
    assert isinstance(result, SendTurnResult)
    assert result.accepted is True
    assert result.as_dict() == {
        "accepted": True, "message_id": "u1", "user_message_id": "u1",
        "assistant_message_id": "a1", "attempt_id": "at1", "session_id": "s1",
    }
    assert len(gw.entry.submitted) == 1


@pytest.mark.asyncio
async def test_queue_full_error_type():
    """queue-full → 应用层 QueueFull（detail 与现网 HTTP body 一致）。"""
    gw = FakeGateway(FakeReceipt(accepted=False, reason=RejectReason.QUEUE_FULL, current_size=10))
    emitted, pushed = [], []
    deps = _deps(gw, FakeService(), emitted, pushed)
    inp = SendTurnInput(session_id="s1", content="hello", receipt_only=True, max_pending=10)

    with pytest.raises(QueueFull) as exc:
        await send_turn(inp, deps)

    assert exc.value.detail == {"error": "queue_full", "limit": 10, "current_size": 10}


@pytest.mark.asyncio
async def test_answer_persister_installed_once():
    """决策 7 资源不变量：一个 Gateway 仅安装一个答案持久化观察器。"""
    gw = FakeGateway()
    emitted, pushed = [], []
    deps = _deps(gw, FakeService(), emitted, pushed)
    inp = SendTurnInput(session_id="s1", content="hello", receipt_only=True)

    await send_turn(inp, deps)
    await send_turn(inp, deps)

    # 一个 persister + 每次 receipt 一个桥（桥在收尾事件自注销，fake 无事件
    # 故累积）——只断言 persister 幂等：所有 sink 中只一个处理 run.answer
    answer_sinks = [s for s in gw._sinks
                    if getattr(s, "__name__", "") == "_persist_answer"]
    assert len(answer_sinks) == 1
