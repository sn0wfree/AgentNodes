# -*- coding: utf-8 -*-
"""4c B2.2 子批 3d：隔离执行平台守卫测试（全平台可跑，无 skipif）。

「平台守卫」类断言不依赖 Windows：POSIX CI（ubuntu unit job）也必须
真跑——未验证平台必须显式拒绝而非静默通过。Windows 行为类（真 worker
/ Job Object）留在 test_isolation_channel.py / test_isolation_pilot.py
的 win32 gate 内；per-test gate 样板见 test_isolation_adapter.py。

平台矩阵记录：docs/design/isolated-execution-b22-design-20261119.md §11。
"""
from __future__ import annotations

import sys

import pytest

from agentnodes.kernel.agent.tools.isolation import (
    ACTUAL_STATES,
    ApprovalProxy,
    IsolationChannel,
    UnsupportedPlatform,
    _EOF,
    _PosixAdapter,
    _normalize_ctx_wire,
    build_worker_adapter,
    isolate_tool_call,
)
from agentnodes.kernel.agent.tools._isolation_context import IsolationContextError


def test_approval_proxy_defaults_to_deny():
    """未知裁决 → deny（fail-closed，纯进程内）。"""
    proxy = ApprovalProxy(lambda req: "maybe")
    assert proxy.decide({"id": "x"}) == "deny"


def test_actual_states_match_4a_vocabulary():
    """actual_state 词表与 4a 的 timeout_tool_outcomes[].state 严格一致。"""
    assert ACTUAL_STATES == {"stopped", "still_running", "returned", "unknown"}


def test_unsupported_platform_is_explicit():
    """显式传入 POSIX adapter → isolate_tool_call 拒绝执行。

    未验证平台必须显式拒绝（UnsupportedPlatform），不静默降级到
    进程内执行。全平台跑：Windows 上也应成立（adapter 是显式指定的）。
    """
    adapter = _PosixAdapter()
    assert adapter.supported() is False
    with pytest.raises(UnsupportedPlatform):
        isolate_tool_call("probe_context", {}, adapter=adapter)


def test_build_worker_adapter_matches_platform():
    """平台承诺：Windows → 已验证 adapter；其余 → 禁启用 adapter。

    POSIX 生产启用前必须完成平台测试矩阵（WP-3 前置裁决），在此之前
    supported() == False 是契约，不是缺陷。
    """
    adapter = build_worker_adapter()
    if sys.platform == "win32":
        assert adapter.supported() is True
    else:
        assert adapter.supported() is False


def test_read_timeout_is_not_eof():
    """回归：读超时（worker 只是慢）不得被当成 EOF/退出。

    根因：早期实现用 None 同时表示「超时」与「EOF」，worker 首次 import
    慢于一次读超时就被误判为已退出 → 实际状态错报 unknown。伪 handle
    驱动，不依赖真 worker（POSIX 可跑）。
    """
    handle = {"proc": type("P", (), {
        "stdout": iter(()), "stderr": None, "pid": 1,
    })()}
    channel = IsolationChannel(handle)
    assert channel.read(0.05) is None, "空队列读超时 → None（非 EOF）"
    assert channel._eof is False, "读超时不置 EOF 标志"

    # 显式 EOF 哨兵 → 置 EOF 标志
    channel._queue.put(_EOF)
    assert channel.read(0.05) is None
    assert channel._eof is True, "真 EOF 才置标志"


# ── B2.3 3b：发送侧 ctx 统一编码（全平台，无真 worker）─────────────


def test_bare_ctx_dict_rejected():
    """裸 dict（无 ctx_v）必须显式拒——调用方须经 codec 构造（决策 3）。"""
    with pytest.raises(IsolationContextError, match="bare ctx dict"):
        _normalize_ctx_wire({"workspace": "D:/probe"})


def test_none_ctx_becomes_versioned_wire():
    ctx = _normalize_ctx_wire(None)
    assert ctx["ctx_v"] == 1
    assert set(ctx) == {"ctx_v", "workspace", "session_id", "strategy_dir",
                        "runs_dir", "results_tsv", "write_roots", "read_roots",
                        "message_id", "tool_call_id"}


def test_versioned_dict_passes_through():
    wire = {"ctx_v": 1, "workspace": "D:/probe"}
    assert _normalize_ctx_wire(wire) is wire  # 不拷贝，原样透传（worker 侧校验）


def test_toolcontext_encoded():
    from agentnodes.kernel.agent.tools.core import ToolContext

    wire = _normalize_ctx_wire(ToolContext(session_id="s1"))
    assert wire["ctx_v"] == 1
    assert wire["session_id"] == "s1"
    assert wire["workspace"] is None
