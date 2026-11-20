# -*- coding: utf-8 -*-
"""B2.3 子批 3a：隔离上下文 codec 契约测试（全平台无 skipif）。

codec 是纯函数，不触平台路径——POSIX CI（ubuntu unit job）也真跑，
为未来 POSIX 验证积累无平台依赖的覆盖（方案 §3 / §6.3）。

覆盖：9 字段往返（含 Path/tuple 类型强转还原）、ctx_v 三类显式报错、
白名单外键两侧拒、回调对象构造级不可能泄漏、相对路径/.. 拒。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agentnodes.kernel.agent.tools._isolation_context import (
    CTX_VERSION,
    WIRE_FIELDS,
    IsolationContextError,
    IsolationCtxWire,
    from_wire,
    to_wire,
)
from agentnodes.kernel.agent.tools.core import ToolContext


def _full_ctx(**overrides) -> ToolContext:
    """9 个纯数据字段全填 + 全部回调/进程内对象字段也填（泄漏测试用）。"""
    base = dict(
        workspace=Path("D:/ws/proj"),
        session_id="sess-1",
        strategy_dir=Path("D:/ws/strategy"),
        runs_dir=Path("D:/ws/runs"),
        results_tsv=Path("D:/ws/results.tsv"),
        write_roots=("D:/ws/out", "D:/ws/tmp"),
        read_roots=("D:/ws/in",),
        message_id="msg-1",
        tool_call_id="tc-1",
        # 以下均**不得**跨界：
        emit_progress=lambda x: None,
        emit_event=lambda k, d: None,
        permission_evaluator=object(),
        permission_gateway=object(),
        data_store=object(),
        sandbox=object(),
        before_execute=lambda: None,
    )
    base.update(overrides)
    return ToolContext(**base)


class TestRoundTrip:
    def test_wire_key_set_is_whitelist_plus_version(self):
        """wire 键集合 == 白名单 + ctx_v——对象字段构造级不可能泄漏。"""
        wire = to_wire(_full_ctx())
        assert set(wire) == WIRE_FIELDS | {"ctx_v"}

    def test_all_nine_fields_round_trip(self):
        ctx = _full_ctx()
        wire = to_wire(ctx)
        assert wire["ctx_v"] == CTX_VERSION
        # 类型强转：Path → str、tuple → list
        assert wire["workspace"] == str(ctx.workspace)
        assert wire["write_roots"] == list(ctx.write_roots)
        assert wire["runs_dir"] == str(ctx.runs_dir)

        decoded = from_wire(wire)
        # 还原：str → Path、list → tuple；9 字段逐一相等
        for name in WIRE_FIELDS:
            assert getattr(decoded, name) == getattr(ctx, name), name
        assert isinstance(decoded.workspace, Path)
        assert isinstance(decoded.write_roots, tuple)

    def test_callback_fields_never_cross(self):
        """回调全填的 ToolContext 编码后无对象键；解码侧回调恒 None。"""
        wire = to_wire(_full_ctx())
        for forbidden in ("emit_progress", "emit_event", "permission_evaluator",
                          "permission_gateway", "data_store", "sandbox",
                          "before_execute"):
            assert forbidden not in wire
        decoded = from_wire(wire)
        assert decoded.emit_progress is None
        assert decoded.emit_event is None
        assert decoded.permission_gateway is None
        assert decoded.data_store is None
        assert decoded.sandbox is None
        assert decoded.before_execute is None

    def test_none_fields_stay_none(self):
        wire = to_wire(ToolContext())
        assert wire["ctx_v"] == CTX_VERSION
        assert all(wire[f] is None for f in WIRE_FIELDS)
        decoded = from_wire(wire)
        assert all(getattr(decoded, f) is None for f in WIRE_FIELDS)


class TestVersionErrors:
    @pytest.mark.parametrize("bad", [
        {},                      # 缺失
        {"ctx_v": "1"},          # 非整型
        {"ctx_v": 0},            # 无效
        {"ctx_v": -1},           # 无效
        {"ctx_v": CTX_VERSION + 1},  # 不兼容
        {"ctx_v": True},         # bool 不算 int
    ])
    def test_invalid_version_rejected(self, bad):
        with pytest.raises(IsolationContextError, match="ctx_v"):
            from_wire({"workspace": "D:/ws", **bad})

    def test_wire_schema_version_matches_codec(self):
        assert IsolationCtxWire(ctx_v=CTX_VERSION).ctx_v == CTX_VERSION


class TestRejectExtraKeys:
    def test_unknown_key_rejected(self):
        bad = to_wire(_full_ctx())
        bad["permission_gateway"] = "leak"
        with pytest.raises(IsolationContextError, match="unexpected wire ctx keys"):
            from_wire(bad)

    def test_object_value_for_data_field_rejected(self):
        bad = to_wire(_full_ctx())
        bad["workspace"] = object()
        with pytest.raises(IsolationContextError, match="workspace must be str"):
            from_wire(bad)

    def test_extra_data_field_rejected(self):
        """未来新增字段若两端不同步，decode 侧显式拒（漂移可见）。"""
        bad = to_wire(_full_ctx())
        bad["new_future_field"] = "x"
        with pytest.raises(IsolationContextError, match="new_future_field"):
            from_wire(bad)


class TestPathValidation:
    @pytest.mark.parametrize("bad", ["relative/path", "./relative", ""])
    def test_relative_path_rejected(self, bad):
        wire = to_wire(_full_ctx())
        wire["workspace"] = bad
        with pytest.raises(IsolationContextError, match="absolute path"):
            from_wire(wire)

    @pytest.mark.parametrize("path_field", [
        "workspace", "strategy_dir", "runs_dir", "results_tsv",
    ])
    def test_dotdot_segment_rejected(self, path_field):
        wire = to_wire(_full_ctx())
        wire[path_field] = "D:/ws/a/../b"
        with pytest.raises(IsolationContextError, match="'\\.\\.'"):
            from_wire(wire)

    def test_read_roots_type_rejected(self):
        wire = to_wire(_full_ctx())
        wire["read_roots"] = [1, 2]
        with pytest.raises(IsolationContextError, match="read_roots must be a list"):
            from_wire(wire)


class TestInputTypes:
    def test_to_wire_rejects_non_toolcontext(self):
        with pytest.raises(IsolationContextError, match="expects ToolContext"):
            to_wire({"workspace": "D:/ws"})

    def test_from_wire_rejects_non_dict(self):
        with pytest.raises(IsolationContextError, match="must be dict"):
            from_wire("not-a-dict")  # type: ignore[arg-type]
