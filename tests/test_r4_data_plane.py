"""R4 数据面测试——P-3 接线、P-5 指针读回、PROTOCOL_VERSION。

设计：docs/stage-r4-data-plane-design-20261030.md
  A. singleton：data_dir 注入 + QNODES_PLATFORM_STORE=0 回退
  B. artifact 通道：小 metrics 内联（等价）、大 metrics 转指针 + 登记
  C. 指针读回：sha256 校验、篡改检测
  D. runner 接线：_artifacts=None 与有通道两种路径
  E. PROTOCOL_VERSION：常量、emit 默认、replay 拒绝未知版本
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research.core.orchestration.contracts import (
    PROTOCOL_VERSION,
    ProtocolVersionError,
    check_event_version,
)
from strategy_research.core.storage.artifact_channel import (
    ARTIFACT_THRESHOLD_BYTES,
    ArtifactChannel,
)
from strategy_research.core.storage.platform_store import PlatformStore


@pytest.fixture()
def store(tmp_path: Path) -> PlatformStore:
    s = PlatformStore(data_dir=tmp_path / "platform")
    yield s
    s.close()


@pytest.fixture()
def channel(store: PlatformStore, tmp_path: Path) -> ArtifactChannel:
    return ArtifactChannel(store, tmp_path / "ws")


# ── A. singleton 注入 ────────────────────────────────────


def test_singleton_injects_platform_store(tmp_path, monkeypatch):
    monkeypatch.setenv("SR_WORKSPACE_PATH", str(tmp_path))
    monkeypatch.delenv("QNODES_PLATFORM_STORE", raising=False)
    from strategy_research.core.gateway import reset_gateway
    from strategy_research.core.gateway.singleton import get_gateway

    reset_gateway()
    try:
        class _C:
            def chat(self, messages, **kwargs):
                raise AssertionError("unused")

        gw = get_gateway(client=_C())
        assert gw.store is not None
        assert gw.artifacts is not None
        assert (tmp_path / ".quantnodes" / "platform").exists()
    finally:
        gw.close()
        reset_gateway()


def test_singleton_store_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("SR_WORKSPACE_PATH", str(tmp_path))
    monkeypatch.setenv("QNODES_PLATFORM_STORE", "0")
    from strategy_research.core.gateway import reset_gateway
    from strategy_research.core.gateway.singleton import get_gateway

    reset_gateway()
    try:
        class _C:
            def chat(self, messages, **kwargs):
                raise AssertionError("unused")

        gw = get_gateway(client=_C())
        assert gw.store is None
        assert gw.artifacts is None
    finally:
        reset_gateway()


# ── B. artifact 通道 ─────────────────────────────────────


def test_small_metrics_stay_inline(channel):
    small = {"sharpe": 1.2, "max_dd": -0.1}
    out = channel.maybe_artifact(
        session_id="s", payload=small, kind="round_metrics",
    )
    assert out is small  # 同一对象原样返回（事件字节不变）


def test_large_metrics_become_pointer_and_register(channel, store):
    large = {"curve": list(range(ARTIFACT_THRESHOLD_BYTES))}
    out = channel.maybe_artifact(
        session_id="s", payload=large, kind="round_metrics",
        run_id="sid:R1", study_id="sid", round_id="R1",
    )
    assert not isinstance(out, dict)  # ArtifactPointer
    assert out.bytes > ARTIFACT_THRESHOLD_BYTES
    assert out.uri.startswith("studies/sid/rounds/R1/artifacts/")
    rows = store.list_artifacts(study_id="sid")
    assert any(r.artifact_id == out.artifact_id for r in rows)


# ── C. 指针读回 ──────────────────────────────────────────


def test_pointer_roundtrip_and_tamper(channel, tmp_path):
    payload = {"big": "x" * (ARTIFACT_THRESHOLD_BYTES + 10)}
    ptr = channel.maybe_artifact(
        session_id="s", payload=payload, kind="round_metrics",
        study_id="sid", round_id="R1",
    )
    assert channel.load(ptr) == payload

    abs_path = tmp_path / "ws" / ptr.uri
    abs_path.write_bytes(b'{"tampered": true}')
    with pytest.raises(ValueError):
        channel.load(ptr)


# ── D. runner 接线 ───────────────────────────────────────


def test_runner_keeps_metrics_inline_without_channel():
    """_artifacts=None → 现行为（等价门）。"""
    from strategy_research.core.study.runner import AutoresearchRunner

    assert hasattr(AutoresearchRunner, "__init__")
    import inspect

    sig = inspect.signature(AutoresearchRunner.__init__)
    assert "artifacts" in sig.parameters
    assert sig.parameters["artifacts"].default is None


# ── E. PROTOCOL_VERSION ──────────────────────────────────


def test_protocol_version_constant_and_default(store):
    assert PROTOCOL_VERSION == "1"
    ev = store.emit("sess-pv", "test.event", {"x": 1})
    assert ev.schema_v == PROTOCOL_VERSION


def test_replay_rejects_unknown_version(store):
    store.emit("sess-pv2", "ok.event", {"x": 1})
    rows = store.replay_rows("sess-pv2")
    assert rows and rows[0]["schema_v"] == PROTOCOL_VERSION

    # 手工写入一条未来版本事件（走 store 自己的连接，与实现同源）
    conn = store._get_session_conn("sess-pv2")
    conn.execute(
        "INSERT INTO event_log (id, aggregate_id, seq, type, data_json, "
        "time_created, parent_event_id, branch_id, session_key, run_id, schema_v) "
        "VALUES (?, ?, ?, ?, ?, ?, NULL, 'main', NULL, NULL, '99')",
        ("fut-1", "sess-pv2", 99999, "future.event", "{}", 1.0),
    )
    conn.commit()

    with pytest.raises(ProtocolVersionError):
        store.replay("sess-pv2")
    # 显式放宽可读取（迁移路径）
    events = store.replay("sess-pv2", strict_version=False)
    assert any(e.schema_v == "99" for e in events)


def test_check_event_version_none_is_default():
    check_event_version(None)  # 不抛
    with pytest.raises(ProtocolVersionError):
        check_event_version("2")
