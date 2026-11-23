# -*- coding: utf-8 -*-
"""SessionExecutionLease 单元测试（裁决 2 · B1：原语 + 单测，零接线）。

设计：docs/design/goal-gateway-session-lease-design-20261118.md §2-§5。

覆盖 B1 验证清单：
- 原子竞争（asyncio 并发 acquire 只成一个；跨线程亦然）
- ownership token 校验（错误 token 不误放、双重释放）
- 释放后再获取、不顶替语义、holder 只读观测、session 隔离
- 原子性契约：acquire 必须纯同步（源码无 async/await 痕迹）
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
import threading

import pytest

from agentnodes.kernel.gateway.session_lease import (
    LeaseRecord,
    SessionExecutionLease,
)

# ── 基本生命周期 ────────────────────────────────────────────────────


class TestBasicLifecycle:
    def test_acquire_returns_token(self):
        lease = SessionExecutionLease()
        token = lease.acquire("s1", "goal:g1")
        assert isinstance(token, str) and token
        assert lease.holding("s1") is True

    def test_release_with_correct_token(self):
        lease = SessionExecutionLease()
        token = lease.acquire("s1", "goal:g1")
        assert lease.release("s1", token) is True
        assert lease.holding("s1") is False

    def test_reacquire_after_release_gets_new_token(self):
        lease = SessionExecutionLease()
        t1 = lease.acquire("s1", "goal:g1")
        lease.release("s1", t1)
        t2 = lease.acquire("s1", "chat:r1")
        assert t2 is not None and t2 != t1
        assert lease.holder("s1") == "chat:r1"

    def test_owner_recorded_in_holder(self):
        lease = SessionExecutionLease()
        lease.acquire("s1", "goal:goal-42")
        assert lease.holder("s1") == "goal:goal-42"


# ── 不顶替 / 拒绝非持有释放 ─────────────────────────────────────────


class TestNoPreemption:
    def test_acquire_on_held_session_returns_none(self):
        lease = SessionExecutionLease()
        assert lease.acquire("s1", "goal:g1") is not None
        assert lease.acquire("s1", "chat:r1") is None

    def test_failed_acquire_does_not_replace_holder(self):
        lease = SessionExecutionLease()
        lease.acquire("s1", "goal:g1")
        lease.acquire("s1", "chat:r1")
        assert lease.holder("s1") == "goal:g1"


class TestTokenValidation:
    def test_stale_token_cannot_release_a_reacquired_session(self):
        lease = SessionExecutionLease()
        old_token = lease.acquire("s1", "goal:g1")
        assert old_token is not None
        assert lease.release("s1", old_token) is True

        new_token = lease.acquire("s1", "chat:r1")
        assert new_token is not None
        assert lease.release("s1", old_token) is False
        assert lease.holder("s1") == "chat:r1"
        assert lease.release("s1", new_token) is True

    def test_release_with_wrong_token_fails_and_keeps_lease(self):
        lease = SessionExecutionLease()
        lease.acquire("s1", "goal:g1")
        assert lease.release("s1", "not-the-token") is False
        assert lease.holding("s1") is True

    def test_double_release_fails(self):
        lease = SessionExecutionLease()
        token = lease.acquire("s1", "goal:g1")
        assert lease.release("s1", token) is True
        assert lease.release("s1", token) is False

    def test_release_on_empty_session_fails(self):
        lease = SessionExecutionLease()
        assert lease.release("s1", "any") is False


# ── session 隔离 ─────────────────────────────────────────────────────


class TestSessionIsolation:
    def test_independent_sessions_acquire_independently(self):
        lease = SessionExecutionLease()
        assert lease.acquire("s1", "goal:g1") is not None
        assert lease.acquire("s2", "chat:r1") is not None
        assert lease.holder("s1") == "goal:g1"
        assert lease.holder("s2") == "chat:r1"

    def test_release_one_does_not_touch_other(self):
        lease = SessionExecutionLease()
        t1 = lease.acquire("s1", "goal:g1")
        t2 = lease.acquire("s2", "chat:r1")
        lease.release("s1", t1)
        assert lease.holding("s1") is False
        assert lease.holding("s2") is True
        assert lease.release("s2", t2) is True


# ── holder 只读观测 ─────────────────────────────────────────────────


class TestHolderObservation:
    def test_holder_none_when_free(self):
        lease = SessionExecutionLease()
        assert lease.holder("nobody") is None

    def test_holder_returns_owner_not_token(self):
        lease = SessionExecutionLease()
        token = lease.acquire("s1", "chat:run-9")
        holder = lease.holder("s1")
        assert holder == "chat:run-9"
        assert holder != token  # 观测面不得泄漏 token（token 只给持有者）

    def test_lease_record_shape(self):
        lease = SessionExecutionLease()
        token = lease.acquire("s1", "goal:g1")
        record = lease._leases["s1"]
        assert isinstance(record, LeaseRecord)
        assert (record.session_id, record.owner, record.token) == ("s1", "goal:g1", token)


# ── 原子竞争（B1 验证清单核心）─────────────────────────────────────


class TestAtomicContention:
    @pytest.mark.asyncio
    async def test_concurrent_asyncio_acquires_exactly_one_wins(self):
        """N 个 asyncio 任务同 tick 并发 acquire，恰好一个成功。"""
        lease = SessionExecutionLease()
        owners = [f"goal:g{i}" for i in range(20)]

        async def try_acquire(owner: str) -> str | None:
            # create_task 后所有任务在第一个 await 边界处汇合，
            # 每个"醒来的瞬间"都尝试获取——竞争窗口内只允许一个成功。
            await asyncio.sleep(0)
            return lease.acquire("s1", owner)

        tokens = await asyncio.gather(*(try_acquire(o) for o in owners))
        winners = [t for t in tokens if t is not None]
        assert len(winners) == 1, f"恰好一个赢家，实际 {len(winners)}"
        assert lease.holding("s1") is True

    @pytest.mark.asyncio
    async def test_release_then_pending_waiters_one_succeeds(self):
        """释放瞬间多个等待者重试，仍恰好一个获得。"""
        lease = SessionExecutionLease()
        t1 = lease.acquire("s1", "goal:g1")

        async def waiter(i: int) -> str | None:
            for _ in range(50):
                token = lease.acquire("s1", f"chat:r{i}")
                if token is not None:
                    return token
                await asyncio.sleep(0.001)
            return None

        tasks = [asyncio.create_task(waiter(i)) for i in range(5)]
        await asyncio.sleep(0.01)
        lease.release("s1", t1)
        results = await asyncio.gather(*tasks)
        winners = [r for r in results if r is not None]
        assert len(winners) == 1
        assert lease.holding("s1") is True

    def test_cross_thread_contention_single_winner(self):
        """跨线程并发（threading.Lock 保底路径）也只允许一个赢家。"""
        lease = SessionExecutionLease()
        results: list[str | None] = []
        barrier = threading.Barrier(8)

        def worker(i: int) -> None:
            barrier.wait()
            results.append(lease.acquire("s1", f"goal:g{i}"))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len([r for r in results if r is not None]) == 1

    @pytest.mark.asyncio
    async def test_no_lost_update_between_acquire_and_release(self):
        """交错序列（A 获取 → B 失败 → A 释放 → B 成功）状态一致。"""
        lease = SessionExecutionLease()
        ta = lease.acquire("s1", "goal:g1")
        assert ta is not None
        await asyncio.sleep(0)
        assert lease.acquire("s1", "chat:r1") is None
        assert lease.release("s1", ta) is True
        tb = lease.acquire("s1", "chat:r1")
        assert tb is not None
        assert lease.holder("s1") == "chat:r1"


# ── 原子性契约钉住（防未来回归）───────────────────────────────────


class TestAtomicityContract:
    def test_acquire_is_synchronous(self):
        """acquire 必须是纯同步函数——引入 async/await 会打开竞态窗口。

        设计文档 §2.1：asyncio 单线程内 check-then-set 的原子性依赖
        「方法内无让出点」。用 AST 断言（子串扫描会被注释里的说明文字
        误伤）：无 async 函数定义、无任何 await/async-for/async-with 节点。
        """
        assert not inspect.iscoroutinefunction(SessionExecutionLease.acquire)
        tree = ast.parse(textwrap.dedent(
            inspect.getsource(SessionExecutionLease.acquire)
        ))
        forbidden = (ast.AsyncFunctionDef, ast.Await, ast.AsyncFor, ast.AsyncWith)
        for node in ast.walk(tree):
            assert not isinstance(node, forbidden), (
                f"acquire 出现 {type(node).__name__}（破坏原子性契约）"
            )

    def test_release_is_synchronous(self):
        assert not inspect.iscoroutinefunction(SessionExecutionLease.release)
        tree = ast.parse(textwrap.dedent(
            inspect.getsource(SessionExecutionLease.release)
        ))
        forbidden = (ast.AsyncFunctionDef, ast.Await, ast.AsyncFor, ast.AsyncWith)
        for node in ast.walk(tree):
            assert not isinstance(node, forbidden), (
                f"release 出现 {type(node).__name__}（破坏原子性契约）"
            )
