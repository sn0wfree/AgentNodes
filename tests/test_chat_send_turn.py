# -*- coding: utf-8 -*-
"""3a.3a：send_turn 深模块的直测（穿过唯一外部 seam）。

验收（决策 5/6/7）：历史预检失败零 Attempt 事件、成功路径、错误类型映射、
单例观察器幂等——全部用 fake adapter 直测，不戳内部同步原语。
"""
from __future__ import annotations

import pytest

from agentnodes.apps.chat.send_turn import (
    HistoryDeps,
    HistoryUnavailable,
    QueueFull,
    SendTurnDeps,
    SendTurnInput,
    SendTurnResult,
    precheck_history,
    send_turn,
)
from agentnodes.kernel.orchestration.contracts import QueueMode, RejectReason


class FakeHistory:
    """C2：load_history callable fake——不再复制 SessionService 内部形状。"""

    def __init__(self, messages=None, fail_history=False):
        self.messages = messages or []
        self.fail_history = fail_history
        self.calls = {"n": 0}

    def __call__(self, session_id, keep_all_compactions=False):
        self.calls["n"] += 1
        if self.fail_history:
            raise RuntimeError("db down")
        return [{"role": m["role"], "content": m["content"]} for m in self.messages]


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
        self._sink_keys = set()
        self._mailbox = FakeMailbox()

    def add_sink(self, sink):
        self._sinks.append(sink)
        return lambda: None

    def ensure_sink(self, key, factory):
        """C5：与 kernel Gateway 同契约的幂等注册（fake 侧镜像）。"""
        if key in self._sink_keys:
            return False
        self._sink_keys.add(key)
        self._sinks.append(factory())
        return True

    def consumer_running(self):
        return False

    def entry_mailbox_for(self, key):
        return self._mailbox


def _deps(gw, history, emitted, pushed):
    return SendTurnDeps(
        gateway=gw,
        load_history=history,
        update_attempt_status=lambda *a, **k: None,
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
    deps = _deps(gw, FakeHistory(fail_history=True), emitted, pushed)
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
    deps = _deps(gw, FakeHistory(), emitted, pushed)
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
    deps = _deps(gw, FakeHistory(), emitted, pushed)
    inp = SendTurnInput(session_id="s1", content="hello", receipt_only=True, max_pending=10)

    with pytest.raises(QueueFull) as exc:
        await send_turn(inp, deps)

    assert exc.value.detail == {"error": "queue_full", "limit": 10, "current_size": 10}


@pytest.mark.asyncio
async def test_answer_persister_installed_once():
    """决策 7 资源不变量：一个 Gateway 仅安装一个答案持久化观察器。"""
    gw = FakeGateway()
    emitted, pushed = [], []
    deps = _deps(gw, FakeHistory(), emitted, pushed)
    inp = SendTurnInput(session_id="s1", content="hello", receipt_only=True)

    await send_turn(inp, deps)
    await send_turn(inp, deps)

    # 一个 persister + 每次 receipt 一个桥（桥在收尾事件自注销，fake 无事件
    # 故累积）——只断言 persister 幂等：所有 sink 中只一个处理 run.answer
    answer_sinks = [s for s in gw._sinks
                    if getattr(s, "__name__", "") == "_persist_answer"]
    assert len(answer_sinks) == 1


def test_ensure_sink_idempotent():
    """C5：ensure_sink 同 key 恰装一次（幂等键归 Gateway 所有）。"""
    from agentnodes.kernel.gateway.gateway import Gateway

    gw = Gateway(client=object(), engine_factory=lambda: None)
    installed = []

    def factory():
        sink = lambda kind, data: installed.append((kind, data))
        return sink

    assert gw.ensure_sink("k", factory) is True
    assert gw.ensure_sink("k", factory) is False  # 二次注册不重复安装
    assert len(installed) == 0  # 未广播前不触发
    gw._combined_sink("run.answer", {"run_id": "r1"})
    assert len(installed) == 1, "同 key 恰一个 sink 实例收到事件"
    # 不同 key 各自注册
    assert gw.ensure_sink("other", factory) is True


@pytest.mark.asyncio
async def test_prevalidated_history_skips_second_read():
    """重复 A 修复：调用方已预检并传入 history 时，send_turn 不再二次
    读库（预检只读幂等，但两次读成本翻倍）。"""
    gw = FakeGateway()
    emitted, pushed = [], []
    history = FakeHistory()
    deps = _deps(gw, history, emitted, pushed)

    # 入口侧预检一次（send_async 的做法）——经 HistoryDeps 窄接口
    pre = precheck_history(
        SendTurnInput(session_id="s1", content="hello"),
        HistoryDeps(load_history=history, build_llm_config=lambda: None),
    )
    assert history.calls["n"] == 1

    # 传入预检结果 → send_turn 跳过二次读取
    result = await send_turn(
        SendTurnInput(
            session_id="s1", content="hello", receipt_only=True,
            prevalidated_history=pre,
        ),
        deps,
    )
    assert result.accepted is True
    assert history.calls["n"] == 1, "send_turn 不应二次读历史"

    # 未提供时仍自包含预检（兼容旧调用）
    await send_turn(
        SendTurnInput(session_id="s1", content="hello", receipt_only=True),
        deps,
    )
    assert history.calls["n"] == 2
