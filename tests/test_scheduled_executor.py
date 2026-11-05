"""Tests for ScheduledResearchExecutor — asyncio mode + study dispatch."""

from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.kernel.scheduled_research.executor import ScheduledResearchExecutor
from agentnodes.kernel.scheduled_research.models import JobStatus, ScheduledResearchJob
from agentnodes.kernel.scheduled_research.store import ScheduledResearchStore


class ExecutorTestCase(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        self.tmpdir = TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "goals.db"
        self.store = ScheduledResearchStore(path=self.db_path)

    def tearDown(self) -> None:
        self.store.close()
        self.tmpdir.cleanup()

    async def test_tick_dispatches_due_job(self) -> None:
        dispatched: list[str] = []

        async def fn(job):
            dispatched.append(job.id)
            return f"study_of_{job.id}"

        executor = ScheduledResearchExecutor(
            self.store, tick_interval=60.0, dispatch_fn=fn,
        )
        job = ScheduledResearchJob(
            id="due_1", workspace="/ws", strategy_name="s1",
            next_run_at=time.time() - 1,  # already due
        )
        self.store.add(job)

        await executor._tick()

        self.assertEqual(dispatched, ["due_1"])
        found = self.store.get("due_1")
        self.assertEqual(found.status, JobStatus.COMPLETED)
        self.assertEqual(found.last_run_id, "study_of_due_1")
        self.assertIsNotNone(found.last_run_at)
        executor.stop()

    async def test_tick_skips_not_due(self) -> None:
        dispatched: list[str] = []

        async def fn(job):
            dispatched.append(job.id)

        executor = ScheduledResearchExecutor(self.store, dispatch_fn=fn)
        job = ScheduledResearchJob(
            id="future_1", workspace="/ws", strategy_name="s1",
            next_run_at=time.time() + 3600,
        )
        self.store.add(job)

        await executor._tick()

        self.assertEqual(dispatched, [])
        found = self.store.get("future_1")
        self.assertEqual(found.status, JobStatus.PENDING)

    async def test_tick_skips_cancelled(self) -> None:
        dispatched: list[str] = []

        async def fn(job):
            dispatched.append(job.id)

        executor = ScheduledResearchExecutor(self.store, dispatch_fn=fn)
        job = ScheduledResearchJob(
            id="cancelled_1", workspace="/ws", strategy_name="s1",
            next_run_at=time.time() - 1, status=JobStatus.CANCELLED,
        )
        self.store.add(job)

        await executor._tick()

        self.assertEqual(dispatched, [])

    async def test_dispatch_failure_records_error(self) -> None:
        async def fn(job):
            raise RuntimeError("boom")

        executor = ScheduledResearchExecutor(self.store, dispatch_fn=fn)
        job = ScheduledResearchJob(
            id="fail_1", workspace="/ws", strategy_name="s1",
            next_run_at=time.time() - 1,
        )
        self.store.add(job)

        await executor._tick()

        found = self.store.get("fail_1")
        self.assertEqual(found.status, JobStatus.FAILED)
        self.assertIn("boom", found.config.get("last_error", ""))

    async def test_recurring_interval_reschedules(self) -> None:
        executor = ScheduledResearchExecutor(
            self.store, dispatch_fn=lambda job: None,
        )
        job = ScheduledResearchJob(
            id="int_1", workspace="/ws", strategy_name="s1",
            interval_ms=60_000, next_run_at=time.time() - 1,
        )
        self.store.add(job)

        await executor._tick()

        found = self.store.get("int_1")
        self.assertEqual(found.status, JobStatus.PENDING)
        self.assertGreater(found.next_run_at, time.time())

    async def test_recurring_cron_reschedules(self) -> None:
        executor = ScheduledResearchExecutor(
            self.store, dispatch_fn=lambda job: None,
        )
        job = ScheduledResearchJob(
            id="cron_1", workspace="/ws", strategy_name="s1",
            cron="0 * * * *", next_run_at=time.time() - 1,
        )
        self.store.add(job)

        await executor._tick()

        found = self.store.get("cron_1")
        self.assertEqual(found.status, JobStatus.PENDING)
        self.assertGreater(found.next_run_at, time.time())

    async def test_sync_dispatch_fn_supported(self) -> None:
        calls: list[str] = []
        executor = ScheduledResearchExecutor(
            self.store,
            dispatch_fn=lambda job: calls.append(job.id) or "sync_ok",
        )
        job = ScheduledResearchJob(
            id="sync_1", workspace="/ws", strategy_name="s1",
            next_run_at=time.time() - 1,
        )
        self.store.add(job)

        await executor._tick()

        self.assertEqual(calls, ["sync_1"])
        found = self.store.get("sync_1")
        self.assertEqual(found.status, JobStatus.COMPLETED)
        self.assertEqual(found.last_run_id, "sync_ok")

    async def test_run_once_async(self) -> None:
        executor = ScheduledResearchExecutor(
            self.store, dispatch_fn=lambda job: "ran",
        )
        job = ScheduledResearchJob(
            id="run_1", workspace="/ws", strategy_name="s1",
            next_run_at=time.time() + 3600,
        )
        self.store.add(job)

        ok = await executor.run_once_async("run_1")
        self.assertTrue(ok)
        found = self.store.get("run_1")
        self.assertEqual(found.status, JobStatus.COMPLETED)

    async def test_run_once_async_not_found(self) -> None:
        executor = ScheduledResearchExecutor(self.store)
        self.assertFalse(await executor.run_once_async("ghost"))

    async def test_recover_stale_running_on_start(self) -> None:
        """_run_loop recovers stale RUNNING jobs before ticking."""
        job = ScheduledResearchJob(
            id="stale_1", workspace="/ws", strategy_name="s1",
            status=JobStatus.RUNNING, next_run_at=time.time() - 1,
        )
        self.store.add(job)
        dispatched: list[str] = []

        async def fn(job):
            dispatched.append(job.id)

        executor = ScheduledResearchExecutor(
            self.store, tick_interval=0.05, dispatch_fn=fn,
        )
        loop = asyncio.get_running_loop()
        executor.start(loop=loop)
        try:
            # 2 ticks: recovery happens inside _run_loop before first tick
            await asyncio.sleep(0.15)
        finally:
            executor.stop()
            await asyncio.sleep(0)

        self.assertGreaterEqual(len(dispatched), 1)
        found = self.store.get("stale_1")
        self.assertEqual(found.status, JobStatus.COMPLETED)

    async def test_default_dispatch_study_bridge(self) -> None:
        """``target='study'`` 已移除：必须**显式失败**，不得静默 no-op。

        2026-11-05 批次 3：apps/study 归档后，这条默认派发路径没有目标了。
        契约变更（docs/design/batch3-study-removal-design-20261105.md §5.1）：
        抛 RuntimeError + 调度记录留 FAILED + 可读原因 —— 用户能区分
        「没配好」与「跑完了」。静默成功会让运维以为任务真的执行过。
        """
        executor = ScheduledResearchExecutor(self.store)
        job = ScheduledResearchJob(
            id="bridge_1", workspace="/ws", strategy_name="s1",
            prompt="研究动量因子", next_run_at=time.time() - 1,
        )
        self.store.add(job)

        await executor._tick()

        found = self.store.get("bridge_1")
        self.assertEqual(found.status, JobStatus.FAILED)
        self.assertIn("apps/study", found.config.get("last_error", ""))
        self.assertIn("batch 3", found.config.get("last_error", ""))

    async def test_legacy_autoresearch_target(self) -> None:
        """'autoresearch' target falls back to the subprocess path."""
        executor = ScheduledResearchExecutor(self.store)
        job = ScheduledResearchJob(
            id="legacy_1", workspace="/ws", strategy_name="s1",
            target="autoresearch", next_run_at=time.time() - 1,
        )
        self.store.add(job)

        with patch.object(
            executor, "_default_dispatch_subprocess",
            return_value=None,
        ) as mock_sub:
            await executor._tick()

        mock_sub.assert_called_once()
        self.assertEqual(mock_sub.call_args.args[0].id, "legacy_1")
        found = self.store.get("legacy_1")
        self.assertEqual(found.status, JobStatus.COMPLETED)


class ExecutorThreadModeTest(unittest.TestCase):
    """CLI (thread) mode — start() without a loop."""

    def setUp(self) -> None:
        self.tmpdir = TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "goals.db"
        self.store = ScheduledResearchStore(path=self.db_path)

    def tearDown(self) -> None:
        self.store.close()
        self.tmpdir.cleanup()

    def test_start_stop_thread_mode(self) -> None:
        executor = ScheduledResearchExecutor(
            self.store, tick_interval=0.05,
            dispatch_fn=lambda job: None,
        )
        executor.start()
        try:
            time.sleep(0.15)
        finally:
            executor.stop()
        self.assertFalse(executor._running)

    def test_run_once_blocking(self) -> None:
        executor = ScheduledResearchExecutor(
            self.store, dispatch_fn=lambda job: "ran_once",
        )
        job = ScheduledResearchJob(
            id="run_1", workspace="/ws", strategy_name="s1",
            next_run_at=time.time() + 3600,
        )
        self.store.add(job)

        ok = executor.run_once("run_1")
        self.assertTrue(ok)
        found = self.store.get("run_1")
        self.assertEqual(found.status, JobStatus.COMPLETED)
        self.assertEqual(found.last_run_id, "ran_once")


if __name__ == "__main__":
    unittest.main()
