# -*- coding: utf-8 -*-
"""逐出口的 Gateway 绝对值断言（裁决 3 兼容出口审查）。

原则（维护者 2026-11-18 指示）：**不依赖旧实现**——每个断言只读
Gateway / apps 的**当前**行为，给出绝对值（而不是"与旧路径相同"）。
旧路径本身有缺陷的出口不在此照搬，其差异在审查文档中单列并裁决。

覆盖出口 → Gateway 对应物：
  send_message          → Gateway api_chat_request + mailbox + run
  cancel                → Gateway.cancel_session（暂停 mailbox + 取消 run）
  cancel_session        → Gateway.cancel_session
  resume_queue          → Gateway.resume_session
  wait_for_attempt      → Gateway.wait_turn
  list_active_attempts  → apps/chat.attempts_query（存储快照 × Gateway 实时状态）
  get_available_actions → Gateway.chat_session_state（+ lease_holder）
  is_session_processing → Gateway.execution_lease.holder
"""
from __future__ import annotations

import threading
import time

from agentnodes.kernel.gateway.gateway import Gateway


class FakeClient:
    """最小 LLM 客户端替身：每次调用返回固定 answer。"""

    def __init__(self, answer: str = "ok", delay: float = 0.0) -> None:
        self.answer = answer
        self.delay = delay
        self.calls: list[dict] = []

    def chat(self, messages, **kwargs):  # noqa: ANN003 — duck-typed
        self.calls.append({"messages": messages, **kwargs})
        if self.delay:
            time.sleep(self.delay)
        return _FakeResponse(self.answer)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content
        self.reasoning_content = ""
        self.tool_calls: list = []
        self.finish_reason = "stop"
        self.usage = {}


def _make_gateway(client=None, **kw) -> tuple[Gateway, FakeClient]:
    """返回 (gateway, client)——Gateway 不保存 client 引用，测试自持。"""
    cli = client or FakeClient()
    return Gateway(client=cli, **kw), cli


def _submit(gw: Gateway, session_id: str = "s1", text: str = "hi", **kw):
    from agentnodes.kernel.gateway.entry_adapters import api_chat_request

    return gw.entry.submit(api_chat_request(session_id, text, **kw))


# ── 出口 1：send_message → Gateway 提交 + 执行 ───────────────────────


class TestGatewaySendParity:
    def test_submit_then_run_executes_and_returns_result(self):
        """提交后驱动一轮：产出 answer，run 结果可用。"""
        gw, _ = _make_gateway()
        _submit(gw)
        results = gw.run_once()
        assert len(results) == 1
        assert results[0].answer == "ok"

    def test_submit_returns_accepted_receipt(self):
        """提交返回两态回执（accepted + 物理 session_id）。"""
        gw, _ = _make_gateway()
        receipt = _submit(gw)
        assert receipt.accepted is True
        assert receipt.reason is None
        assert receipt.session_id

    def test_empty_queue_run_returns_nothing(self):
        """空队列 → 无结果（旧路径同语义：没有待办就不产出）。"""
        gw, _ = _make_gateway()
        assert gw.run_once() == []


# ── 出口 2/3：cancel / cancel_session → Gateway.cancel_session ─────────


class TestGatewayCancelParity:
    def test_cancel_no_active_run_returns_false(self):
        gw, _ = _make_gateway()
        assert gw.cancel_session("nobody") is False

    def test_cancel_active_run_returns_true_and_pauses(self):
        """取消活跃 run → True + mailbox 暂停（不消费后续 turn）。

        注意：mailbox 由提交惰性创建（``mailbox_for`` 不建新 mailbox），
        因此须先提交工单再 claim fence，否则无 mailbox 可暂停。
        """
        gw, _ = _make_gateway()
        _submit(gw)  # 建 mailbox
        gw.fence.claim("s1", "run-1")
        assert gw.cancel_session("s1") is True
        assert gw.chat_session_state("s1")["paused"] is True

    def test_cancel_is_idempotent(self):
        gw, _ = _make_gateway()
        _submit(gw)
        gw.fence.claim("s1", "run-1")
        assert gw.cancel_session("s1") is True
        assert gw.cancel_session("s1") is True


# ── 出口 4：resume_queue → Gateway.resume_session ────────────────────


class TestGatewayResumeParity:
    def test_resume_unpaused_returns_false(self):
        gw, _ = _make_gateway()
        assert gw.resume_session("s1") is False

    def test_resume_after_cancel_resumes_mailbox(self):
        """取消→暂停→恢复：mailbox 不再暂停，可继续消费。"""
        gw, _ = _make_gateway()
        _submit(gw)
        gw.fence.claim("s1", "run-1")
        gw.cancel_session("s1")
        assert gw.chat_session_state("s1")["paused"] is True
        assert gw.resume_session("s1") is True
        assert gw.chat_session_state("s1")["paused"] is False


# ── 出口 5：wait_for_attempt → Gateway.wait_turn ─────────────────────


class TestGatewayWaitParity:
    def test_wait_turn_returns_result_after_completion(self):
        """提交→驱动→wait_turn 拿到本轮结果（代际语义，不返回上一轮）。"""
        gw, _ = _make_gateway()
        receipt = _submit(gw)
        waiting = threading.Event()
        results = []

        class ObservedEvent(threading.Event):
            def wait(self, timeout=None):
                waiting.set()
                return super().wait(timeout)

        gw._turn_done[receipt.session_id] = ObservedEvent()
        waiter = threading.Thread(
            target=lambda: results.append(gw.wait_turn(receipt.session_id, timeout=5))
        )
        waiter.start()
        try:
            assert waiting.wait(2), "wait_turn did not start waiting"
            gw.start_consumer(poll_interval=0.05)
        finally:
            waiter.join(timeout=6)
            gw.stop_consumer()
        assert not waiter.is_alive()
        assert len(results) == 1 and results[0] is not None
        assert results[0].answer == "ok"

    def test_wait_turn_timeout_returns_none(self):
        """无 turn 完成 → 超时返回 None（旧路径同语义）。"""
        gw, _ = _make_gateway()
        assert gw.wait_turn("s1", timeout=0.2) is None


# ── 出口 6：list_active_attempts → apps/chat.attempts_query ──────────


class TestGatewayAttemptsParity:
    def test_attempts_query_combines_store_and_gateway_state(self):
        """查询用例：存储快照 × Gateway 实时状态（僵尸行丢弃）。"""
        from agentnodes.apps.chat.attempts_query import query_live_attempts

        class _Store:
            def __init__(self, rows):
                self._rows = rows

            def list_attempts_by_status(self, session_id, statuses):
                return list(self._rows)

        class _Attempt:
            def __init__(self, aid, status):
                self.attempt_id = aid
                self.status = status
                self.message_id = "m1"
                self.prompt = "p"
                self.created_at = "2026-11-18T00:00:00+00:00"
                self.error = None

        from agentnodes.kernel.session.models import AttemptStatus

        store = _Store([_Attempt("a1", AttemptStatus.RUNNING)])
        gw, _ = _make_gateway()
        assert query_live_attempts("s1", store=store, gateway=gw) == []  # 无实时状态→丢弃

    def test_attempts_query_excludes_leased_but_untracked_attempt(self):
        from agentnodes.apps.chat.attempts_query import query_live_attempts
        from agentnodes.kernel.session.models import AttemptStatus

        class _Store:
            def list_attempts_by_status(self, session_id, statuses):
                class _A:
                    attempt_id = "a1"
                    status = AttemptStatus.RUNNING
                    message_id = "m1"
                    prompt = "p"
                    created_at = "t"
                    error = None
                return [_A()]

        gw, _ = _make_gateway()
        gw.execution_lease.acquire("s1", "chat:run-1")
        out = query_live_attempts("s1", store=_Store(), gateway=gw)
        # 无 attempt_state 的 gateway → 该 attempt 非活跃 → 空
        assert out == []

    def test_attempts_query_returns_real_queued_and_running(self):
        from agentnodes.apps.chat.attempts_query import query_live_attempts
        from agentnodes.kernel.session.models import AttemptStatus

        class Store:
            def list_attempts_by_status(self, session_id, statuses):
                class Attempt:
                    attempt_id = "a1"
                    status = AttemptStatus.RUNNING
                    message_id = "m1"
                    prompt = "p"
                    created_at = "t"
                    error = None
                return [Attempt()]

        started = threading.Event()
        release = threading.Event()

        class Engine:
            def run(self, state):
                started.set()
                if not release.wait(5):
                    raise TimeoutError("probe release was not signalled")
                return {"answer": "ok"}

        gw, _ = _make_gateway()
        gw.controller._engine_factory = Engine
        receipt = _submit(gw, attempt_id="a1")
        store = Store()
        queued = query_live_attempts(receipt.session_id, store=store, gateway=gw)
        assert len(queued) == 1 and queued[0]["status"] == "queued"

        worker = threading.Thread(target=gw.run_once)
        worker.start()
        try:
            assert started.wait(2), "engine did not start"
            running = query_live_attempts(receipt.session_id, store=store, gateway=gw)
            assert len(running) == 1
            assert running[0]["attempt_id"] == "a1"
            assert running[0]["status"] == "running"
            by_business_id = query_live_attempts("s1", store=store, gateway=gw)
            assert by_business_id == running
            assert query_live_attempts("another", store=store, gateway=gw) == []
        finally:
            release.set()
            worker.join(timeout=6)
        assert not worker.is_alive()


# ── 出口 7：get_available_actions → Gateway.chat_session_state ────────


class TestGatewayActionsParity:
    def test_idle_state_allows_send(self):
        gw, _ = _make_gateway()
        state = gw.chat_session_state("s1")
        assert state["active"] is False and state["pending"] is False
        assert state["paused"] is False
        assert state["lease_holder"] is None

    def test_pending_state_flagged(self):
        gw, _ = _make_gateway()
        _submit(gw)
        assert gw.chat_session_state("s1")["pending"] is True

    def test_lease_holder_exposed_for_goal_occupancy(self):
        """goal 持有 lease → state 暴露持有者（UI 据此抑制 send）。"""
        gw, _ = _make_gateway()
        gw.execution_lease.acquire("s1", "goal:g1")
        assert gw.chat_session_state("s1")["lease_holder"] == "goal:g1"


# ── 出口 8：is_session_processing → Gateway.execution_lease.holder ───


class TestGatewayLeaseParity:
    def test_holder_none_when_free(self):
        gw, _ = _make_gateway()
        assert gw.execution_lease.holder("s1") is None

    def test_holder_reports_goal_owner(self):
        gw, _ = _make_gateway()
        token = gw.execution_lease.acquire("s1", "goal:g1")
        assert gw.execution_lease.holder("s1") == "goal:g1"
        gw.execution_lease.release("s1", token)
        assert gw.execution_lease.holder("s1") is None


# ── 缺口②：lease 释放后**常驻消费者**自动继续（不靠手动 run_once）────


class TestResidentConsumerContinues:
    """缺口②：之前 B2 测试靠手动 run_pending 消费，未证明常驻消费者自动继续。

    本测试启动**真实常驻消费者线程**（Gateway.start_consumer），验证：
    goal 持有 lease 期间工单不执行；lease 释放后消费者**自动**继续消费。
    """
    def test_resident_consumer_executes_after_lease_release(self):
        gw, client = _make_gateway(client=FakeClient(delay=0.01))
        gw.start_consumer(poll_interval=0.05)

        token = gw.execution_lease.acquire("s1", "goal:g1")
        _submit(gw)

        # goal 持有期间：不应执行
        time.sleep(0.3)
        assert client.calls == [], "goal 持有 lease 期间不得执行"

        # 释放 lease → 消费者应自动继续（不手动调 run_once）
        gw.execution_lease.release("s1", token)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not client.calls:
            time.sleep(0.05)

        assert client.calls, "lease 释放后常驻消费者必须自动继续消费"
        gw.stop_consumer()
