"""S1 验收测试 —— 事件协议统一（docs/s1-event-protocol-unification-design-20261027.md §6）。

1. 往返相等：EventV2 → emit → replay 逐字段相等（含信封新列）；
2. 存量迁移：旧 8 列 schema 库 → ensure 补列 → 既有行可 replay、seq 连续；
3. 门禁：EventStore 不再直接执行 event_log SQL（存储收口 PlatformStore）。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from strategy_research.core.agent.event_store import EventStore
from strategy_research.foundation.storage.event_schema import (
    ENVELOPE_COLUMNS,
)


@pytest.fixture()
def store(tmp_path: Path):
    return EventStore(db_path=tmp_path / "events.db")


# ── 往返相等 ─────────────────────────────────────────────


def test_round_trip_full_envelope(store):
    emitted = store.emit(
        "sess-1", "assistant_message",
        {"text": "你好", "n": 1},
        parent_event_id="evt-parent",
        branch_id="exp1",
    )
    replayed = store.replay("sess-1", branch_id="exp1")
    assert len(replayed) == 1
    a, b = emitted, replayed[0]
    assert (a.id, a.aggregate_id, a.seq, a.type) == (b.id, b.aggregate_id, b.seq, b.type)
    assert a.data == b.data
    assert a.time_created == pytest.approx(b.time_created)
    assert a.parent_event_id == b.parent_event_id == "evt-parent"
    assert a.branch_id == b.branch_id == "exp1"


def test_round_trip_filters_and_paging(store):
    for i in range(5):
        store.emit("s", "text_delta", {"i": i})
    store.emit("s", "iter_start", {})

    by_type = store.replay("s", types=["iter_start"])
    assert [e.type for e in by_type] == ["iter_start"]
    assert by_type[0].seq == 6

    paged = store.replay("s", from_seq=3, limit=2)
    assert [e.seq for e in paged] == [4, 5]

    all_rows = store.replay("s")
    assert [e.seq for e in all_rows] == [1, 2, 3, 4, 5, 6]


def test_seq_per_branch_independent(store):
    a1 = store.emit("s", "loop_start", branch_id="main")
    b1 = store.emit("s", "loop_start", branch_id="exp1")
    b2 = store.emit("s", "text_delta", branch_id="exp1")
    a2 = store.emit("s", "text_delta", branch_id="main")
    assert (a1.seq, a2.seq) == (1, 2)
    assert (b1.seq, b2.seq) == (1, 2)


def test_envelope_columns_in_store():
    """信封三列已在 canonical DDL 里（P-1 对齐）。"""
    assert set(ENVELOPE_COLUMNS) == {"session_key", "run_id", "schema_v"}


def test_platform_store_emit_writes_envelope(tmp_path: Path):
    """PlatformStore.emit 落 canonical 列；run_id/session_key 可查。"""
    from strategy_research.foundation.storage.platform_store import PlatformStore

    ps = PlatformStore(data_dir=tmp_path)
    ps.emit(
        "sess", "tool_started", {"tool": "calc"},
        run_id="t1:r0", session_key="chat:sess",
    )
    rows = ps.replay_rows("sess")
    assert rows[0]["run_id"] == "t1:r0"
    assert rows[0]["session_key"] == "chat:sess"
    assert rows[0]["schema_v"] == "1"
    assert rows[0]["aggregate_id"] == "sess"


def test_legacy_db_migration_and_replay(tmp_path: Path):
    """存量迁移：旧 8 列表 + 既有行 → ensure 补列 → 可 replay、seq 连续。"""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    # 旧 P0-1 canonical（8 列，无信封三列）——手工建（不走 ensure）
    conn.execute("""
        CREATE TABLE event_log (
            id TEXT PRIMARY KEY,
            aggregate_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            type TEXT NOT NULL,
            data_json TEXT NOT NULL,
            time_created REAL NOT NULL,
            parent_event_id TEXT,
            branch_id TEXT NOT NULL DEFAULT 'main',
            UNIQUE (aggregate_id, branch_id, seq)
        )
    """)
    for i in range(1, 4):
        conn.execute(
            "INSERT INTO event_log (id, aggregate_id, seq, type, data_json, "
            "time_created, parent_event_id, branch_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (f"e{i}", "old-sess", i, "text_delta", '{"i": %d}' % i, 1000.0 + i,
             None, "main"),
        )
    conn.commit()
    conn.close()

    # 打开（EventStore 构造触发 PlatformStore 兼容模式 → ensure 补列）
    store = EventStore(db_path=db)
    # 旧行可 replay
    old = store.replay("old-sess")
    assert [e.seq for e in old] == [1, 2, 3]
    assert old[0].data == {"i": 1}
    # 新事件追加，seq 连续
    new = store.emit("old-sess", "iter_start", {"fresh": True})
    assert new.seq == 4
    assert len(store.replay("old-sess")) == 4

    # 库内列存在
    conn = sqlite3.connect(str(db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(event_log)")}
    conn.close()
    assert {"session_key", "run_id", "schema_v"} <= cols


def test_compat_single_file_mode(tmp_path: Path):
    """PlatformStore db_path 兼容模式：跨 aggregate 同文件、全局库不可用。"""
    from strategy_research.foundation.storage.platform_store import PlatformStore

    ps = PlatformStore(db_path=tmp_path / "single.db")
    ps.emit("a", "e", {"x": 1})
    ps.emit("b", "e", {"x": 2})
    assert ps.latest_seq("a") == 1
    assert ps.latest_seq("b") == 1
    assert len(ps.replay_rows("a")) == 1

    with pytest.raises(NotImplementedError, match="兼容单库模式"):
        ps.register_session("k")


def test_facade_no_direct_event_log_sql():
    """门禁：EventStore 不再直接执行 event_log SQL（存储收口 PlatformStore）。"""
    from pathlib import Path as _P

    src = (_P(__file__).resolve().parents[1] / "src" / "strategy_research"
           / "core" / "agent" / "event_store.py").read_text(encoding="utf-8")
    assert "INSERT INTO event_log" not in src
    assert "FROM event_log" not in src
