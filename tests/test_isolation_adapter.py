# -*- coding: utf-8 -*-
"""4c B2.2 子批 3a：平台 adapter seam 行为测试（不接生产、不接 IPC）。

E1 启动/关停、E4 树终止、E5 确认退出序列在 adapter 层的等价测试；
E2 上下文 / E3 审批属 3b IPC 层。POSIX 禁启用保护与平台无关测试全平台可跑。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agentnodes.kernel.agent.tools.isolation import (
    UnsupportedPlatform,
    WorkerAdapter,
    _PosixAdapter,
    build_worker_adapter,
)

ROOT = Path(__file__).resolve().parents[2]
WORKER_SCRIPT = ROOT / "scripts/prototypes/sync_isolation_probe.py"


def test_build_adapter_returns_protocol():
    adapter = build_worker_adapter()
    assert isinstance(adapter, WorkerAdapter)


def test_posix_adapter_disabled():
    adapter = _PosixAdapter()
    assert adapter.supported() is False
    with pytest.raises(UnsupportedPlatform):
        adapter.spawn("anything")
    with pytest.raises(UnsupportedPlatform):
        adapter.kill_and_confirm(None, [], 1.0)
    assert adapter.close(None) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only：原型依赖 kernel32")
def test_e1_spawn_and_clean_shutdown():
    adapter = build_worker_adapter()
    assert adapter.supported() is True
    handle = adapter.spawn(str(WORKER_SCRIPT))
    try:
        assert handle["proc"].poll() is None
    finally:
        adapter.close(handle)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only：原型依赖 kernel32")
def test_e5_kill_confirms_before_release():
    adapter = build_worker_adapter()
    handle = adapter.spawn(str(WORKER_SCRIPT))
    proc = handle["proc"]
    try:
        confirmed = adapter.kill_and_confirm(handle, [proc.pid], 5.0)
        assert all(v in ("exited", "already-gone") for v in confirmed.values())
        assert proc.poll() is not None, "确认退出后进程应已结束"
    finally:
        adapter.close(handle)
