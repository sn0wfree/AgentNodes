"""SessionService 批 A：队列与生命周期状态机（覆盖方案 A 带 ~45 行）。

**夹具原则（方案 §2）**：
- L0 真件组合 —— ``AGENTNODES_SESSION_DB`` env + **真 SessionStore**（tmp SQLite，
  不 mock 存储：SQL 语义真实）+ EventCollector（事件序列断言）
- 队列消费者用 ``AGENTNODES_TEST_CHAT=1`` 官方短路（方案 §1 发现的钩子），
  让批 A 的唯一变量是**队列/生命周期状态机**本身

对应方案：docs/design/service-coverage-plan-20261117.md §3 批 A
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from agentnodes.kernel.session.models import Attempt, AttemptId, AttemptStatus
from agentnodes.kernel.session.service import _QUEUE_LIMIT, SessionService
from agentnodes.kernel.session.store import SessionStore


class EventCollector:
    """记录 ``event_bus.emit(session_id, kind, data)`` 序列。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def emit(self, session_id: str, kind: str, data: dict) -> None:
        self.events.append((session_id, kind, data))

    def kinds(self) -> list[str]:
        return [k for _, k, _ in self.events]

    def of(self, kind: str) -> list[tuple[str, str, dict]]:
        return [e for e in self.events if e[1] == kind]

    def first(self, kind: str) -> dict:
        found = self.of(kind)
        assert found, f"未发射 {kind}；实际序列 {self.kinds()}"
        return found[0][2]


@pytest.fixture
def svc(tmp_path, monkeypatch):
    """真件 SessionService：真 store + 事件收集器 + tmp 统一会话库。

    **消费者被隔离**（批 A 只测队列/生命周期状态机；消费者本身是批 B 的主题）：
    ``_process_session_queue`` 替身长挂——既让 ``_processing_sessions`` 保持
    真实语义（消费者真跑会立刻退出并 discard），又不真跑 attempt 链路。
    """
    monkeypatch.setenv("AGENTNODES_SESSION_DB", str(tmp_path / "sessions.db"))
    store = SessionStore(str(tmp_path / "sessions.db"))
    bus = EventCollector()
    service = SessionService(store=store, event_bus=bus)
    service.events = bus  # type: ignore[attr-defined]  # 便于断言

    async def _idle_consumer(session_id: str) -> None:
        # 长挂：保持 processing 状态；teardown 里被取消
        await asyncio.sleep(3600)

    monkeypatch.setattr(service, "_process_session_queue", _idle_consumer)

    yield service
    # 清理在飞任务，避免 pytest-asyncio 收尾告警
    for task in list(service._active_loops.values()):
        task.cancel()
    for task in list(service._queue_consumers.values()):
        task.cancel()
    # SessionStore 每次操作自开自关（无 close()，K4 连接生命周期）


def _seed_session(svc, session_id: str = "sess-a", title: str = "新会话") -> None:
    svc.create_session(session_id, title=title, user_id="tester")


class TestSendMessageLifecycle:
    async def test_first_message_auto_titles_and_emits_meta_update(self, svc):
        """首条用户消息 → 自动标题 + session_meta_updated 事件（L183-199）。"""
        _seed_session(svc, "sess-title")
        res = await svc.send_message("sess-title", "帮我回测动量因子")
        assert res["status"] in ("processing", "queued")
        meta = svc.events.first("session_meta_updated")
        assert meta["session_id"] == "sess-title"
        assert meta["title"] == "帮我回测动量因子"

    async def test_message_received_carries_queue_metadata(self, svc):
        _seed_session(svc)
        await svc.send_message("sess-a", "hi")
        msg = svc.events.first("message_received")
        assert msg["role"] == "user" and msg["content"] == "hi"
        assert msg["queue_position"] == 1
        assert msg["status"] == "processing"
        assert msg["attempt_id"]

    async def test_queue_state_event_reports_length(self, svc):
        """queue_state 在入队后发射，queue_length 是真实队列长度（L274-281）。"""
        _seed_session(svc)
        await svc.send_message("sess-a", "hi")
        state = svc.events.first("queue_state")
        assert state["session_id"] == "sess-a"
        assert state["queue_length"] >= 1

    async def test_queue_full_returns_error_dict(self, svc):
        """队列满 → queue_full 错误 dict（L204-210），不再入队。"""
        _seed_session(svc)
        # 直接预填队列到上限（避免真跑 10 次）
        svc._session_queues["sess-a"] = asyncio.Queue()
        for _ in range(_QUEUE_LIMIT):
            await svc._session_queues["sess-a"].put({"attempt_id": "x"})
        res = await svc.send_message("sess-a", "overflow")
        assert res["error"] == "queue_full"
        assert res["limit"] == _QUEUE_LIMIT
        assert res["current_size"] == _QUEUE_LIMIT

    async def test_second_message_is_queued_not_processing(self, svc):
        """同会话第二条 → status=queued（队列 FIFO）。"""
        _seed_session(svc)
        first = await svc.send_message("sess-a", "first")
        second = await svc.send_message("sess-a", "second")
        assert first["queue_position"] == 1
        assert second["queue_position"] == 2
        assert second["status"] == "queued"


class TestProcessingState:
    def test_mark_processing_toggles_membership(self, svc):
        assert svc.is_session_processing("s1") is False
        svc.mark_session_processing("s1", processing=True)
        assert svc.is_session_processing("s1") is True
        svc.mark_session_processing("s1", processing=False)
        assert svc.is_session_processing("s1") is False

    def test_mark_processing_false_on_unknown_is_noop(self, svc):
        svc.mark_session_processing("never", processing=False)
        assert svc.is_session_processing("never") is False


class TestAvailableActions:
    def test_idle_offers_send(self, svc):
        actions = svc.get_available_actions("s1")
        assert [a["name"] for a in actions] == ["send"]
        assert actions[0]["destructive"] == "false"

    def test_processing_offers_cancel(self, svc):
        svc.mark_session_processing("s1", processing=True)
        actions = svc.get_available_actions("s1")
        assert [a["name"] for a in actions] == ["cancel"]

    def test_paused_offers_resume(self, svc):
        svc._paused_sessions["s1"] = asyncio.Event()
        actions = svc.get_available_actions("s1")
        assert [a["name"] for a in actions] == ["resume"]

    def test_processing_and_paused_offers_both(self, svc):
        svc.mark_session_processing("s1", processing=True)
        svc._paused_sessions["s1"] = asyncio.Event()
        actions = svc.get_available_actions("s1")
        assert [a["name"] for a in actions] == ["cancel", "resume"]


class TestPauseResume:
    def test_resume_unknown_session_returns_false(self, svc):
        assert svc.resume_queue("never-paused") is False

    def test_resume_sets_the_pause_event(self, svc):
        evt = asyncio.Event()
        svc._paused_sessions["s1"] = evt
        assert svc.resume_queue("s1") is True
        assert evt.is_set()


class TestCancel:
    def test_cancel_unknown_attempt_returns_false(self, svc):
        assert svc.cancel(AttemptId("nope")) is False

    async def test_cancel_active_attempt_cancels_task(self, svc):
        """在飞 attempt → task.cancel() + True（L307-311）。"""
        started = asyncio.Event()

        async def forever():
            started.set()
            await asyncio.sleep(3600)

        task = asyncio.create_task(forever())
        await started.wait()
        svc._active_loops[AttemptId("a1")] = task

        assert svc.cancel(AttemptId("a1")) is True
        await asyncio.sleep(0)
        assert task.cancelled() or task.cancelling()

    async def test_cancel_session_cancels_consumer(self, svc):
        """cancel_session → 取消消费者任务 + True（L331-335）。"""
        _seed_session(svc)
        await svc.send_message("sess-a", "hi")
        consumer = svc._queue_consumers.get("sess-a")
        assert consumer is not None
        assert svc.cancel_session("sess-a") is True
        await asyncio.sleep(0)

    async def test_cancel_session_without_consumer_returns_false(self, svc):
        assert svc.cancel_session("never-used") is False


class TestWaitForAttempt:
    async def test_fast_path_returns_completed_result(self, svc):
        """结果已就绪 → 立即返回，不等事件（L466-468）。"""
        svc._attempt_results["a-done"] = {"status": "completed", "summary": "ok", "error": ""}
        res = await svc.wait_for_attempt("sess-a", "a-done", timeout=1.0)
        assert res == {"status": "completed", "summary": "ok", "error": ""}

    async def test_timeout_returns_none(self, svc):
        """超时 → None（L475-476）。"""
        res = await svc.wait_for_attempt("sess-a", "a-slow", timeout=0.05)
        assert res is None
        assert "a-slow" not in svc._attempt_events, "超时后事件注册应清理"

    async def test_signal_wakes_waiter(self, svc):
        """_signal_attempt_done → 唤醒等待者并返回结果（L867-872 + L479）。"""
        waiter = asyncio.create_task(svc.wait_for_attempt("sess-a", "a-sig", timeout=5.0))
        await asyncio.sleep(0.01)
        svc._signal_attempt_done("a-sig", {"status": "completed", "summary": "s", "error": ""})
        res = await waiter
        assert res["status"] == "completed"
