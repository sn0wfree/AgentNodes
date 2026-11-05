"""P2.1 回归 —— 回放必须校验协议版本，且写入必须显式携带 schema_v。

缺陷背景（2026-11-02 复核发现）：交付总结声称「PROTOCOL_VERSION 是事件
信封 schema_v 的唯一来源；replay 遇不匹配版本抛 ProtocolVersionError」。
实际上：

1. ``EventStore._replay`` —— 真正的回放入口 —— 直接调
   ``PlatformStore.replay_rows`` 然后 ``EventV2.from_row``，**完全没有**
   版本校验。只有 ``PlatformStore.replay()`` 那条兼容 API 校验。
2. ``EventV2.to_row()`` 不含 ``schema_v``，写入时靠 event_log 的 DDL
   DEFAULT ``'1'``。也就是说改 ``PROTOCOL_VERSION`` 并不会改变写入内容。

后果：跨版本读入不兼容事件不会报错，只是语义被静默误读——这是数据损坏，
不是降级。本文件锁住修复后的两条不变量。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agentnodes.kernel.agent.event_store import EventStore
from agentnodes.kernel.events.event_v2 import EventV2
from agentnodes.kernel.orchestration.contracts import (
    PROTOCOL_VERSION,
    ProtocolVersionError,
)


@pytest.fixture()
def store(tmp_path: Path):
    return EventStore(db_path=tmp_path / "events.db")


# ── 写入侧：schema_v 显式携带 ─────────────────────────────


def test_to_row_carries_current_protocol_version():
    ev = EventV2.create(aggregate_id="a", seq=1, type="t", data={})
    row = ev.to_row()
    assert row["schema_v"] == PROTOCOL_VERSION


def test_persisted_row_has_explicit_schema_v(store, tmp_path):
    """落库后读回 schema_v 必须是代码常量，而不是 DDL 默认值。"""
    store.emit("s1", "assistant_message", {"t": "x"})
    con = sqlite3.connect(tmp_path / "events.db")
    try:
        got = con.execute("SELECT schema_v FROM event_log").fetchone()[0]
    finally:
        con.close()
    assert got == PROTOCOL_VERSION


# ── 读取侧：EventStore._replay 校验 ───────────────────────


def test_replay_accepts_matching_version(store):
    store.emit("s1", "assistant_message", {"t": "ok"})
    assert len(store.replay("s1")) == 1


def test_replay_rejects_unknown_version(store, tmp_path):
    """手工写入不兼容版本，回放必须抛——不能静默读入。"""
    store.emit("s1", "assistant_message", {"t": "x"})
    con = sqlite3.connect(tmp_path / "events.db")
    try:
        con.execute("UPDATE event_log SET schema_v = '999'")
        con.commit()
    finally:
        con.close()

    with pytest.raises(ProtocolVersionError, match="999"):
        store.replay("s1")


def test_schema_v_column_is_not_null(store, tmp_path):
    """schema_v 有 NOT NULL 约束——空值无法落库，故不存在「None 版本」行。

    契约里 ``check_event_version(None)`` 视为 "1"，那是为**升级前**的旧库
    预留的宽松口；当前 schema 下写不进去，所以不需要为 None 造用例。
    """
    store.emit("s1", "assistant_message", {})
    con = sqlite3.connect(tmp_path / "events.db")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("UPDATE event_log SET schema_v = NULL")
        con.rollback()
    finally:
        con.close()


def test_replay_rejects_when_any_row_is_incompatible(store, tmp_path):
    """一批事件里只要有一条版本不对，整批拒绝（不做部分回放）。"""
    for i in range(3):
        store.emit("s1", "text_delta", {"i": i})
    con = sqlite3.connect(tmp_path / "events.db")
    try:
        con.execute("UPDATE event_log SET schema_v = '999' WHERE seq = 2")
        con.commit()
    finally:
        con.close()

    with pytest.raises(ProtocolVersionError):
        store.replay("s1")


def test_partial_replay_with_filters_still_checks_version(store, tmp_path):
    """带 types 过滤也不能绕过校验——只回放合规的那几条同样是损坏。"""
    store.emit("s1", "text_delta", {})
    store.emit("s1", "iter_start", {})
    con = sqlite3.connect(tmp_path / "events.db")
    try:
        con.execute("UPDATE event_log SET schema_v = '999' WHERE type = 'iter_start'")
        con.commit()
    finally:
        con.close()

    with pytest.raises(ProtocolVersionError):
        store.replay("s1", types=["iter_start"])
    # 不含坏行时正常
    assert len(store.replay("s1", types=["text_delta"])) == 1


# ── 内存后端等价 ──────────────────────────────────────────


def test_in_memory_replay_unaffected(store):
    """InMemory 路径不经过 rows，不该因新校验而失败。"""
    store.emit("s1", "text_delta", {"i": 1})
    assert [e.seq for e in store.replay("s1")] == [1]
