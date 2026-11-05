"""artifact 通道测试 —— 阈值分流 / 落盘 / 登记 / 读回校验 / 目录布局。"""
from __future__ import annotations

import json

import pytest

from agentnodes.foundation.storage.artifact_channel import (
    ARTIFACT_THRESHOLD_BYTES,
    ArtifactChannel,
    ArtifactPointer,
)
from agentnodes.foundation.storage.platform_store import PlatformStore


@pytest.fixture
def channel(tmp_path):
    store = PlatformStore(data_dir=tmp_path / "data")
    return ArtifactChannel(store, workspace=tmp_path / "workspace"), store, tmp_path


# ── 阈值分流 ────────────────────────────────────────────


def test_small_payload_stays_inline(channel):
    ch, _, _ = channel
    payload = {"metrics": {"sharpe": 1.2}}
    result = ch.maybe_artifact(
        session_id="s1", payload=payload, kind="backtest_result",
    )
    assert result is payload  # 原样返回（进事件）


def test_large_payload_becomes_pointer(channel):
    ch, _, _ = channel
    big = {"rows": ["x" * 100 for _ in range(ARTIFACT_THRESHOLD_BYTES // 50)]}
    result = ch.maybe_artifact(
        session_id="s1", payload=big, kind="backtest_result", run_id="t1:r0",
    )
    assert isinstance(result, ArtifactPointer)
    assert result.bytes > ARTIFACT_THRESHOLD_BYTES
    assert result.kind == "backtest_result"


def test_threshold_boundary(channel):
    ch, _, _ = channel
    # 刚好等于阈值 → inline
    exact = b"x" * ARTIFACT_THRESHOLD_BYTES
    result = ch.maybe_artifact(session_id="s1", payload=exact, kind="blob")
    assert result is exact  # inline（<=）

    # 超一字节 → pointer
    over = b"x" * (ARTIFACT_THRESHOLD_BYTES + 1)
    result2 = ch.maybe_artifact(session_id="s1", payload=over, kind="blob")
    assert isinstance(result2, ArtifactPointer)


# ── 落盘 + 登记 ─────────────────────────────────────────


def test_large_payload_written_to_disk(channel):
    ch, store, tmp_path = channel
    big = {"data": "y" * (ARTIFACT_THRESHOLD_BYTES + 100)}
    pointer = ch.maybe_artifact(
        session_id="s1", payload=big, kind="backtest_result",
        run_id="t1:r0", study_id="study-1", round_id="R-003",
    )
    assert isinstance(pointer, ArtifactPointer)
    # 文件真的存在
    path = tmp_path / "workspace" / pointer.uri
    assert path.exists()
    # D-4 布局
    assert pointer.uri.startswith("studies/study-1/rounds/R-003/artifacts/")


def test_artifact_registered_in_platform_store(channel):
    ch, store, _ = channel
    big = {"data": "z" * (ARTIFACT_THRESHOLD_BYTES + 100)}
    pointer = ch.maybe_artifact(
        session_id="s1", payload=big, kind="backtest_result",
        run_id="t1:r0", study_id="study-1",
    )
    artifacts = store.list_artifacts(study_id="study-1")
    assert len(artifacts) == 1
    assert artifacts[0].artifact_id == pointer.artifact_id
    assert artifacts[0].status == "registered"
    assert artifacts[0].created_by_run_id == "t1:r0"


def test_blob_also_stored_for_dedup(channel):
    ch, store, _ = channel
    big = {"data": "w" * (ARTIFACT_THRESHOLD_BYTES + 100)}
    pointer = ch.maybe_artifact(session_id="s1", payload=big, kind="x")
    # blob 里也有一份（去重 + 快速读取）
    content = store.read_blob("s1", pointer.sha256)
    assert len(content) == pointer.bytes


# ── 读回 ────────────────────────────────────────────────


def test_roundtrip_dict(channel):
    ch, _, _ = channel
    big = {"metrics": {"sharpe": 1.5, "rows": ["x" * 100] * 1000}}
    pointer = ch.maybe_artifact(session_id="s1", payload=big, kind="backtest_result")
    restored = ch.load(pointer)
    assert restored == big


def test_roundtrip_bytes(channel):
    ch, _, _ = channel
    big = b"\x00\x01" * (ARTIFACT_THRESHOLD_BYTES)
    pointer = ch.maybe_artifact(session_id="s1", payload=big, kind="binary")
    assert ch.load(pointer) == big


def test_roundtrip_from_dict_pointer(channel):
    ch, _, _ = channel
    big = {"k": "v" * (ARTIFACT_THRESHOLD_BYTES + 10)}
    pointer = ch.maybe_artifact(session_id="s1", payload=big, kind="x")
    restored = ch.load(pointer.to_dict())  # 事件里的 dict 形式
    assert restored == big


def test_tamper_detection(channel):
    ch, _, tmp_path = channel
    big = {"k": "v" * (ARTIFACT_THRESHOLD_BYTES + 10)}
    pointer = ch.maybe_artifact(session_id="s1", payload=big, kind="x")
    # 篡改文件
    path = tmp_path / "workspace" / pointer.uri
    path.write_bytes(b'{"tampered": true}')
    with pytest.raises(ValueError, match="校验失败"):
        ch.load(pointer)


def test_missing_file_raises(channel):
    ch, _, _ = channel
    pointer = ArtifactPointer(
        artifact_id="a1", kind="x", uri="files/nonexistent.json",
        sha256="0" * 64, bytes=10,
    )
    with pytest.raises(FileNotFoundError):
        ch.load(pointer)


# ── 目录布局 ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("study_id", "round_id", "expected_prefix"),
    [
        ("study-1", "R-003", "studies/study-1/rounds/R-003/artifacts/"),
        ("study-1", None, "studies/study-1/artifacts/"),
        (None, None, "files/"),
    ],
)
def test_uri_layout_by_context(channel, study_id, round_id, expected_prefix):
    ch, _, _ = channel
    big = {"k": "v" * (ARTIFACT_THRESHOLD_BYTES + 10)}
    pointer = ch.maybe_artifact(
        session_id="s1", payload=big, kind="x",
        study_id=study_id, round_id=round_id,
    )
    assert pointer.uri.startswith(expected_prefix)


def test_artifact_id_derived_from_run(channel):
    ch, _, _ = channel
    big = {"k": "v" * (ARTIFACT_THRESHOLD_BYTES + 10)}
    pointer = ch.maybe_artifact(session_id="s1", payload=big, kind="x", run_id="t1:r0")
    assert pointer.artifact_id.startswith("t1:r0:a")
