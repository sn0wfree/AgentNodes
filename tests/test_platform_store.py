"""PlatformStore 单元测试。"""
from __future__ import annotations

import pytest

from strategy_research.foundation.storage.platform_store import Artifact, PlatformStore


@pytest.fixture
def pstore(tmp_path):
    return PlatformStore(data_dir=tmp_path)


def test_emit_and_replay(pstore):
    pstore.emit("s1", "test.event", {"key": "value"}, run_id="t1:r0")
    events = pstore.replay("s1")
    assert len(events) == 1
    assert events[0].kind == "test.event"
    assert events[0].data == {"key": "value"}
    assert events[0].run_id == "t1:r0"


def test_emit_sequential_seq(pstore):
    for i in range(5):
        pstore.emit("s1", "e", {"n": i})
    events = pstore.replay("s1")
    assert [e.seq for e in events] == [1, 2, 3, 4, 5]


def test_replay_from_seq(pstore):
    for i in range(5):
        pstore.emit("s1", "e", {"n": i})
    events = pstore.replay("s1", from_seq=3)
    assert [e.seq for e in events] == [4, 5]


def test_replay_filter_by_kinds(pstore):
    pstore.emit("s1", "a.first", {})
    pstore.emit("s1", "b.second", {})
    pstore.emit("s1", "a.third", {})
    events = pstore.replay("s1", kinds=["a.first", "a.third"])
    assert len(events) == 2
    assert all(e.kind.startswith("a.") for e in events)


def test_events_between_sessions_isolated(pstore):
    pstore.emit("s1", "event.in.s1", {})
    pstore.emit("s2", "event.in.s2", {})
    s1 = pstore.replay("s1")
    s2 = pstore.replay("s2")
    assert len(s1) == 1 and s1[0].kind == "event.in.s1"
    assert len(s2) == 1 and s2[0].kind == "event.in.s2"


def test_latest_seq_empty_is_zero(pstore):
    assert pstore.latest_seq("s1") == 0


def test_latest_seq_after_emit(pstore):
    pstore.emit("s1", "e", {})
    assert pstore.latest_seq("s1") >= 1


def test_store_blob_dedup(pstore):
    content = b"hello world"
    h1 = pstore.store_blob("s1", content)
    h2 = pstore.store_blob("s1", content)
    assert h1 == h2
    conn = pstore._get_session_conn("s1")
    count = conn.execute("SELECT COUNT(*) FROM blobs").fetchone()[0]
    assert count == 1


def test_store_read_blob_roundtrip(pstore):
    content = b"\x00\x01\x02binary"
    h = pstore.store_blob("s1", content)
    assert pstore.read_blob("s1", h) == content


def test_read_blob_missing_raises(pstore):
    with pytest.raises(KeyError):
        pstore.read_blob("s1", "nonexistent")


def test_blob_isolation_across_sessions(pstore):
    content = b"data"
    h = pstore.store_blob("s1", content)
    with pytest.raises(KeyError):
        pstore.read_blob("s2", h)


def test_register_session_creates_and_reuses(pstore):
    sid1 = pstore.register_session("chat:main")
    sid2 = pstore.register_session("chat:main")
    assert sid1 == sid2


def test_register_session_different_keys_unique(pstore):
    sid1 = pstore.register_session("chat:a")
    sid2 = pstore.register_session("chat:b")
    assert sid1 != sid2


def test_list_sessions(pstore):
    pstore.register_session("chat:a")
    pstore.register_session("chat:b")
    sessions = pstore.list_sessions()
    assert len(sessions) == 2
    assert {s["session_key"] for s in sessions} == {"chat:a", "chat:b"}


def test_register_and_list_artifacts(pstore):
    a = Artifact(
        artifact_id="a1", kind="backtest_result",
        uri="studies/s1/rounds/R-001/results.parquet",
        sha256="abc123", bytes=1024,
        created_by_run_id="run-1", study_id="study-1", round_id="R-001",
    )
    pstore.register_artifact(a)
    arts = pstore.list_artifacts(study_id="study-1")
    assert len(arts) == 1
    assert arts[0].artifact_id == "a1"
    assert arts[0].kind == "backtest_result"


def test_list_artifacts_empty(pstore):
    assert pstore.list_artifacts() == []


def test_quick_check_ok(pstore):
    pstore.emit("s1", "e", {})
    assert pstore.quick_check() is True
