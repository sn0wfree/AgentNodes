"""PlatformStore 并发写测试（F2 写线程锁）。

fanout 分支（跨线程）事件回流的前置护栏：全部公开方法串行化后，
多线程并发 emit/blob 不丢事件、不抛异常。
"""
from __future__ import annotations

import hashlib
import threading

from strategy_research.foundation.storage.platform_store import Artifact, PlatformStore


def test_concurrent_emit_all_persisted(tmp_path):
    store = PlatformStore(data_dir=tmp_path)
    session_id = store.register_session("chat:t1")
    errors: list[Exception] = []

    def worker(tid: int) -> None:
        try:
            for i in range(5):
                store.emit(session_id, "branch.settled", {"tid": tid, "i": i})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert store.latest_seq(session_id) == 40
    events = store.replay(session_id, limit=100)
    assert len(events) == 40
    assert sorted({e.data["tid"] for e in events}) == list(range(8))
    store.close()


def test_concurrent_emit_and_blob_mixed(tmp_path):
    store = PlatformStore(data_dir=tmp_path)
    session_id = store.register_session("chat:t2")
    errors: list[Exception] = []
    hashes: list[str] = []
    hash_lock = threading.Lock()

    def worker(tid: int) -> None:
        try:
            for i in range(4):
                store.emit(session_id, "branch.settled", {"tid": tid, "i": i})
                h = store.store_blob(session_id, f"blob-{tid}-{i}".encode())
                with hash_lock:
                    hashes.append(h)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert store.latest_seq(session_id) == 16
    # 全部 blob 可读且内容正确（内容寻址去重后仍逐条可验证）
    assert len(set(hashes)) == 16
    for tid in range(4):
        for i in range(4):
            h = __import__("hashlib").sha256(f"blob-{tid}-{i}".encode()).hexdigest()
            assert store.read_blob(session_id, h) == f"blob-{tid}-{i}".encode()
    store.close()


def test_cross_thread_emit_without_lock_crash_regression(tmp_path):
    """回归锁意图：单线程路径行为不变（emit 返回 seq 单调）。"""
    store = PlatformStore(data_dir=tmp_path)
    session_id = store.register_session("chat:t3")
    seqs = [store.emit(session_id, "e", {"n": i}).seq for i in range(3)]
    assert seqs == [1, 2, 3]
    store.close()


# ── 补充：并发登记与读写交错 ─────────────────────────────


def test_concurrent_register_session_same_key_dedups(tmp_path):
    """8 线程同 key 并发注册：全部拿到同一个 session_id，登记表只有一行。"""
    store = PlatformStore(data_dir=tmp_path)
    results: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        sid = store.register_session("chat:same")
        with lock:
            results.append(sid)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(set(results)) == 1
    assert len(store.list_sessions()) == 1
    store.close()


def test_concurrent_register_artifact_all_persisted(tmp_path):
    store = PlatformStore(data_dir=tmp_path)

    def worker(tid: int) -> None:
        for i in range(3):
            content = f"artifact-{tid}-{i}".encode()
            store.register_artifact(
                Artifact(
                    artifact_id=f"a-{tid}-{i}",
                    kind="backtest_result",
                    uri=f"studies/s/rounds/R-1/artifacts/a-{tid}-{i}.json",
                    sha256=hashlib.sha256(content).hexdigest(),
                    bytes=len(content),
                )
            )

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    artifacts = store.list_artifacts()
    assert len(artifacts) == 12
    assert all(a.kind == "backtest_result" for a in artifacts)
    store.close()


def test_replay_during_concurrent_emit_never_crashes(tmp_path):
    """读（replay/latest_seq）与写（emit）跨线程交错：不抛异常、终态完整。"""
    store = PlatformStore(data_dir=tmp_path)
    session_id = store.register_session("chat:t4")
    errors: list[Exception] = []
    stop = threading.Event()

    def writer() -> None:
        try:
            for i in range(30):
                store.emit(session_id, "e", {"i": i})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def reader() -> None:
        try:
            while not stop.is_set():
                store.replay(session_id, limit=100)
                store.latest_seq(session_id)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    w = threading.Thread(target=writer)
    r = threading.Thread(target=reader)
    w.start()
    r.start()
    w.join()
    stop.set()
    r.join()

    assert errors == []
    assert store.latest_seq(session_id) == 30
    store.close()
