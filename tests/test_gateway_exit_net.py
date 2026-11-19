# -*- coding: utf-8 -*-
"""R-016 出口网补齐（路线步骤 5）：旧队列出口在新 Gateway 路径的对应面。

逐项钉住兼容审查 §9 的三个未闭合项：
① wait/attempt reload（wait_turn 代际语义，不等待指定 attempt）；
② 旧 attempt 消失 → 新路径 execute 侧不依赖 store 行（旧路径曾永久阻塞）；
③ 队列交接（lease 释放后常驻消费者自动继续）+ 配置/构造失败收尾。

全部用真实 Gateway + FakeClient（与 test_gateway_behavior 同款桩），不 mock
控制面。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from agentnodes.kernel.gateway import api_chat_request
from agentnodes.kernel.gateway.gateway import Gateway


@dataclass
class _Resp:
    content: str = "ok"
    usage: dict = field(default_factory=lambda: {"total_tokens": 10})


class _Client:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(list(messages))
        if self.fail:
            raise RuntimeError("engine construction/execution failed")
        return _Resp()


def _gw(fail=False, sink=None):
    return Gateway(client=_Client(fail=fail), sink=sink)


class TestStep5ExitNet:
    def test_wait_turn_is_session_generational_not_attempt_scoped(self):
        """① wait_turn 按会话代际等待——已完成一轮后新 waiter 超时返回
        None（不复用上一轮结果），也不是指定 attempt 的等待接口。"""
        gw = _gw()
        receipt = gw.entry.submit(api_chat_request("s1", "first"))
        [run] = gw.run_once()
        assert run.ok

        assert gw.wait_turn("chat:s1", timeout=0.05) is None
        assert gw.attempt_state(receipt.session_id, "attempt-any") is None

    def test_engine_failure_settles_failed_and_no_answer(self):
        """配置/构造失败出口：run.failed + run.settled(ok=False) + 无产物。"""
        events = []
        gw = _gw(fail=True, sink=lambda kind, data: events.append((kind, data)))
        gw.entry.submit(api_chat_request("s1", "boom"))
        [run] = gw.run_once()

        assert not run.ok
        kinds = [k for k, _ in events]
        assert "run.failed" in kinds
        assert ("run.settled", False) in [
            (k, d.get("ok", True)) for k, d in events if k == "run.settled"
        ]
        assert "run.answer" not in kinds  # 无产物

    def test_execute_does_not_depend_on_store_attempt_row(self):
        """② 旧 attempt 消失对应面：Gateway 执行路径不查 store 行——
        带任意 attempt_id 的请求仍能完成（旧路径有消费者永久阻塞 bug）。"""
        gw = _gw()
        receipt = gw.entry.submit(api_chat_request(
            "s2", "still-runs", attempt_id="vanished-attempt",
        ))
        assert receipt.accepted
        [run] = gw.run_once()
        assert run.ok
        assert "ok" in run.answer

    def test_lease_handoff_resumes_consumer(self):
        """③ 队列交接：lease 释放后常驻消费者自动继续（不手动 run_once）。"""
        gw = _gw()
        token = gw.execution_lease.acquire("s3", "goal:g1")
        assert token is not None
        gw.entry.submit(api_chat_request("s3", "queued"))
        gw.start_consumer(poll_interval=0.01)
        try:
            time.sleep(0.2)
            assert gw.attempt_state("s3", "any") is None
            # lease 持有期间不得执行（用队列深度观测，不碰内部计数器）
            assert gw.entry.mailbox_for("chat:s3").peek_depth() >= 1
            gw.execution_lease.release("s3", token)
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and gw.entry.mailbox_for("chat:s3").peek_depth():
                time.sleep(0.05)
            assert gw.entry.mailbox_for("chat:s3").peek_depth() == 0
        finally:
            gw.stop_consumer()
