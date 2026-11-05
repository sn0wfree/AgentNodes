"""fencing（A3）单元测试 —— 认领/超越/校验/释放/隔离/线程安全。"""
from __future__ import annotations

import threading

import pytest

from agentnodes.kernel.gateway.fencing import FenceToken, SessionFence

SID = "sess-1"
SID2 = "sess-2"


def test_claim_then_validate_ok():
    fence = SessionFence()
    token = fence.claim(SID, "t1:r0")
    assert fence.validate(token).ok
    assert fence.active_run(SID) == "t1:r0"


def test_new_claim_supersedes_old():
    fence = SessionFence()
    old = fence.claim(SID, "t1:r0")
    new = fence.claim(SID, "t2:r0")

    assert not fence.validate(old).ok  # 旧 run 提交被拦
    assert "superseded" in (fence.validate(old).reason or "")
    assert fence.validate(new).ok
    assert fence.active_run(SID) == "t2:r0"


def test_release_clears_and_reclaim_works():
    fence = SessionFence()
    token = fence.claim(SID, "t1:r0")
    fence.release(token)

    assert fence.active_run(SID) is None
    assert not fence.validate(token).ok  # 释放后提交被拦
    assert "no active run" in (fence.validate(token).reason or "")

    fresh = fence.claim(SID, "t3:r0")
    assert fence.validate(fresh).ok


def test_release_does_not_evict_successor():
    """旧 run 迟到的 release 不得误删后继者的认领。"""
    fence = SessionFence()
    old = fence.claim(SID, "t1:r0")
    fence.claim(SID, "t2:r0")  # 后继者
    fence.release(old)

    assert fence.active_run(SID) == "t2:r0"


def test_sessions_are_isolated():
    fence = SessionFence()
    t1 = fence.claim(SID, "run-a")
    t2 = fence.claim(SID2, "run-b")

    assert fence.validate(t1).ok
    assert fence.validate(t2).ok
    fence.claim(SID, "run-a2")  # 只超越 SID
    assert not fence.validate(t1).ok
    assert fence.validate(t2).ok  # SID2 不受影响


def test_claim_seq_monotonic():
    fence = SessionFence()
    tokens = [fence.claim(SID, f"run-{i}") for i in range(5)]
    seqs = [t.claim_seq for t in tokens]
    assert seqs == sorted(seqs) and len(set(seqs)) == 5


def test_token_is_frozen():
    fence = SessionFence()
    token = fence.claim(SID, "run")
    with pytest.raises(Exception):
        token.run_id = "hacked"  # type: ignore[misc]


def test_threaded_claims_never_lose_fence():
    """并发认领：任一时刻每个 session 只有一个 active，且最后认领者胜出。"""
    fence = SessionFence()
    results: list[FenceToken] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        token = fence.claim(SID, f"run-{i}")
        with lock:
            results.append(token)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert fence.active_run(SID) is not None
    # 只有最后一个认领者的 token 有效（其余全部被超越）
    last = max(results, key=lambda t: t.claim_seq)
    assert fence.validate(last).ok
    assert sum(fence.validate(t).ok for t in results) == 1
