"""批 B：_process_session_queue 的五分支。

**消费者是唯一变量**：``_run_attempt`` 用替身（可控成功/抛错/消失），
其余全真——真 queue、真 store、真 emit 序列。

覆盖方案 §3 批 B：
- vanilla（依次执行 → 队列退出，processing 清理）
- attempt 在 DB 中消失 → warning + continue（不卡队列）
- _run_attempt 抛异常 → 吞噬 + 继续下一条
- cancel 在飞 attempt → queue_paused 事件 → resume 后继续
- exit-vs-enqueue handoff（finally 里重启消费者）

**两条硬约束（踩过）**：
1. ``Attempt.attempt_id`` 是自动生成的 uuid —— 队列项的 id 必须与 DB 行一致，
   否则消费者走「vanished」分支。
2. ``SessionStore`` **不建表** —— schema 由 db 层 ensure（``_get_db_at``），
   且 store 与 db 层必须指向**同一个文件**。
"""
from __future__ import annotations

import asyncio

import pytest

from agentnodes.kernel.session.models import Attempt, AttemptId, AttemptStatus
from agentnodes.kernel.session.service import SessionService
from agentnodes.kernel.session.store import SessionStore


class EventCollector:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def emit(self, session_id: str, kind: str, data: dict) -> None:
        self.events.append((session_id, kind, data))

    def kinds(self) -> list[str]:
        return [k for _, k, _ in self.events]

    def of(self, kind: str) -> list[dict]:
        return [d for _, k, d in self.events if k == kind]


@pytest.fixture
def svc(tmp_path, monkeypatch):
    """真 store + 事件收集器；``_run_attempt`` 由各用例替换。

    同一文件 + schema 由 db 层建：store 用构造参数建库、db 层用 env 解析路径，
    两者必须同源；且 ``SessionStore`` 不建表（假设 schema 已由 db 层 ensure）。
    """
    monkeypatch.setenv("AGENTNODES_SESSION_DB", str(tmp_path / "sessions.db"))
    from agentnodes.kernel.session.db import _get_db_at

    _get_db_at(str(tmp_path / "sessions.db"))  # db 层建 schema
    store = SessionStore(str(tmp_path / "sessions.db"))
    bus = EventCollector()
    service = SessionService(store=store, event_bus=bus)
    service.events = bus  # type: ignore[attr-defined]
    yield service
    for task in list(service._active_loops.values()):
        task.cancel()
    for task in list(service._queue_consumers.values()):
        task.cancel()


def _make_attempt(store, session_id: str) -> Attempt:
    """建一条 attempt 行并返回它（attempt_id 是自动生成的 uuid）。"""
    attempt = Attempt(
        session_id=session_id,
        prompt="hello",
        message_id="m-1",
        status=AttemptStatus.PENDING,
        created_at="2026-11-17T00:00:00",
    )
    store.create_attempt(attempt)
    return attempt


class TestVanillaConsumer:
    async def test_processes_items_in_order_and_exits(self, svc):
        """两条入队 → 依次执行 → 队列退出 + processing 清理。"""
        calls: list[str] = []

        async def fake_run_attempt(*, session_id, attempt, **kw):
            calls.append(attempt.attempt_id)
            return {}

        svc._run_attempt = fake_run_attempt  # type: ignore[method-assign]

        a1 = _make_attempt(svc.store, "s")
        a2 = _make_attempt(svc.store, "s")
        svc._session_queues["s"] = asyncio.Queue()
        svc._session_queues["s"].put_nowait({"attempt_id": a1.attempt_id})
        svc._session_queues["s"].put_nowait({"attempt_id": a2.attempt_id})

        svc._processing_sessions.add("s")
        consumer = asyncio.create_task(svc._process_session_queue("s"))
        await asyncio.wait_for(consumer, timeout=3.0)

        assert calls == [a1.attempt_id, a2.attempt_id], "FIFO 顺序"
        assert "s" not in svc._processing_sessions, "退出后清理 processing 标记"

    async def test_attempt_vanished_continues(self, svc):
        """attempt 在 DB 中消失 → warning + continue，不卡队列（L503-511）。"""
        calls: list[str] = []

        async def fake_run_attempt(*, session_id, attempt, **kw):
            calls.append(attempt.attempt_id)
            return {}

        svc._run_attempt = fake_run_attempt  # type: ignore[method-assign]
        svc._session_queues["s"] = asyncio.Queue()
        svc._session_queues["s"].put_nowait({"attempt_id": "gone"})   # DB 里没有

        svc._processing_sessions.add("s")
        consumer = asyncio.create_task(svc._process_session_queue("s"))
        await asyncio.wait_for(consumer, timeout=3.0)
        assert calls == [], "消失的 attempt 不应被执行"
        assert "s" not in svc._processing_sessions


class TestErrorHandling:
    async def test_attempt_exception_is_swallowed_and_queue_continues(self, svc):
        """_run_attempt 抛异常 → 吞噬 + 消费者仍正常退出（L548-553）。"""
        calls: list[str] = []
        boom_id: list[str] = []

        async def fake_run_attempt(*, session_id, attempt, **kw):
            calls.append(attempt.attempt_id)
            if attempt.attempt_id == boom_id[0]:
                raise RuntimeError("attempt blew up")
            return {}

        svc._run_attempt = fake_run_attempt  # type: ignore[method-assign]
        boom = _make_attempt(svc.store, "s")
        boom_id.append(boom.attempt_id)
        svc._session_queues["s"] = asyncio.Queue()
        svc._session_queues["s"].put_nowait({"attempt_id": boom.attempt_id})

        svc._processing_sessions.add("s")
        consumer = asyncio.create_task(svc._process_session_queue("s"))
        await asyncio.wait_for(consumer, timeout=3.0)
        assert calls == [boom.attempt_id], "异常 attempt 被处理过（不是跳过）"
        assert "s" not in svc._processing_sessions, "异常后消费者仍正常退出"


class TestCancelPausesQueue:
    async def test_cancel_emits_queue_paused_and_waits_for_resume(self, svc):
        """cancel 在飞 → queue_paused 事件 → 等 resume（L530-547）。"""
        started = asyncio.Event()
        release = asyncio.Event()

        async def fake_run_attempt(*, session_id, attempt, **kw):
            started.set()
            await release.wait()
            return {}

        svc._run_attempt = fake_run_attempt  # type: ignore[method-assign]
        a1 = _make_attempt(svc.store, "s")
        svc._session_queues["s"] = asyncio.Queue()
        svc._session_queues["s"].put_nowait({"attempt_id": a1.attempt_id})

        svc._processing_sessions.add("s")
        consumer = asyncio.create_task(svc._process_session_queue("s"))
        await asyncio.wait_for(started.wait(), timeout=3.0)

        # 在飞 attempt 的 task 已被消费者登记 → cancel 它
        assert svc.cancel(AttemptId(a1.attempt_id)) is True

        # 消费者捕获 CancelledError 后才创建 pause event —— 等它出现
        for _ in range(100):
            if "s" in svc._paused_sessions:
                break
            await asyncio.sleep(0.01)
        paused = svc.events.of("queue_paused")
        assert paused, f"应发射 queue_paused；实际 {svc.events.kinds()}"
        assert paused[0]["next_attempt_id"] == a1.attempt_id
        assert "s" in svc._paused_sessions, "队列进入暂停态"

        # resume → 消费者继续（它等的是 pause_evt）
        assert svc.resume_queue("s") is True
        release.set()
        # 消费者 resumed 后回到 ``queue.get()`` 等待下一条 —— 它是**长驻**的
        # （生产路径靠 send_message 在 processing 标记缺失时重启消费者），
        # 队列空时不会自行退出。所以这里等它回到等待态，而不是等它完成。
        for _ in range(100):
            if not svc._active_loops:
                break
            await asyncio.sleep(0.01)
        # 收尾：取消消费者，避免任务泄漏
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)


class TestExitVsEnqueueHandoff:
    async def test_item_enqueued_during_exit_restarts_consumer(self, svc):
        """消费者退出瞬间有新条目 → finally 里重启消费者（L570-576）。

        队列的经典竞态：消费者看到 queue 空决定退出，但生产者已在
        「取队列 → 放入」之间。正确行为是重启消费者而不是丢弃该条目。
        """
        calls: list[str] = []
        first_done = asyncio.Event()
        a1 = _make_attempt(svc.store, "s")
        a2 = _make_attempt(svc.store, "s")

        async def fake_run_attempt(*, session_id, attempt, **kw):
            calls.append(attempt.attempt_id)
            if attempt.attempt_id == a1.attempt_id:
                # 第一次执行时塞入第二条（模拟退出前的入队竞态）
                svc._session_queues["s"].put_nowait({"attempt_id": a2.attempt_id})
                first_done.set()
            return {}

        svc._run_attempt = fake_run_attempt  # type: ignore[method-assign]
        svc._session_queues["s"] = asyncio.Queue()
        svc._session_queues["s"].put_nowait({"attempt_id": a1.attempt_id})

        svc._processing_sessions.add("s")
        consumer = asyncio.create_task(svc._process_session_queue("s"))
        await asyncio.wait_for(first_done.wait(), timeout=3.0)
        # 给 handoff 分支一点时间重启消费者
        await asyncio.sleep(0.2)
        # 消费者正常退出时会把队列从字典里 pop（L581）——用 .get() 取，
        # 避免断言撞上「队列已被回收」。
        q = svc._session_queues.get("s")
        if q is not None:
            await asyncio.wait_for(q.join(), timeout=3.0)
        await asyncio.sleep(0.05)

        assert a2.attempt_id in calls, f"竞态条目不得被丢弃；实际执行 {calls}"
