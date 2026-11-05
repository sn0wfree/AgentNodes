"""H 档补测 —— `core/mcp/server.py` 的底层 helper 与包装器。

高层工具（goal/memory/backtest/data）已有三份测试文件；缺的是
``get_market_data`` 管线下的纯函数（序列化/截断/loader 解析）与
``_wrap_base_tool``（把 BaseTool 包成 MCP 工具的参数映射与 workspace 路由）。
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from strategy_research.interfaces.mcp import server as mcp_server
from strategy_research.interfaces.mcp.server import (
    MCPTool,
    MCPServer,
    _df_to_records,
    _package_fetch_result,
    _resolve_market_loader,
    _serialize_value,
    _wrap_base_tool,
)


# ── _serialize_value ────────────────────────────────────────


def test_serialize_datetime_to_iso():
    assert _serialize_value(datetime(2024, 1, 2, 3, 4)) == "2024-01-02T03:04:00"


def test_serialize_numpy_scalar_to_python():
    out = _serialize_value(np.int64(5))
    assert out == 5 and isinstance(out, int)
    out = _serialize_value(np.float64(1.5))
    assert out == 1.5 and isinstance(out, float)


def test_serialize_plain_value_passthrough():
    assert _serialize_value("x") == "x"
    assert _serialize_value(7) == 7
    assert _serialize_value(None) is None


# ── _df_to_records ──────────────────────────────────────────


def test_df_to_records_json_safe():
    df = pd.DataFrame({
        "date": [pd.Timestamp("2024-01-01")],
        "close": [np.float64(10.5)],
    })
    rec = _df_to_records(df.reset_index())
    assert rec[0]["date"] == "2024-01-01T00:00:00"
    assert rec[0]["close"] == 10.5


def test_df_to_records_empty_df_returns_empty():
    assert _df_to_records(pd.DataFrame({"a": []})) == []


# ── _package_fetch_result ───────────────────────────────────


def _frame(n=10, base=10.0) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame({"close": np.linspace(base, base + n, n)}, index=idx)


def test_package_empty_dataframe_becomes_empty_list():
    data, total, truncated = _package_fetch_result({"A": pd.DataFrame()}, 5)
    assert data["A"] == []
    assert total == 0
    assert truncated is False


def test_package_none_entry_becomes_empty_list():
    data, _, _ = _package_fetch_result({"A": None}, 5)
    assert data["A"] == []


def test_package_truncates_to_tail_and_flags():
    data, total, truncated = _package_fetch_result({"A": _frame(10)}, 5)
    assert len(data["A"]) == 5
    assert total == 5
    assert truncated is True
    # tail 语义：保留的是最后 5 行
    assert data["A"][-1]["close"] == pytest.approx(20.0)


def test_package_no_truncation_under_limit():
    data, total, truncated = _package_fetch_result({"A": _frame(4)}, 5)
    assert total == 4
    assert truncated is False


def test_package_multiple_codes_sum_rows():
    data, total, truncated = _package_fetch_result(
        {"A": _frame(4), "B": _frame(6)}, 10)
    assert total == 10
    assert set(data) == {"A", "B"}


# ── _resolve_market_loader ──────────────────────────────────


def test_resolve_explicit_source_unavailable_returns_none(monkeypatch):
    from strategy_research.apps.data_source import registry as reg

    class Unavailable:
        def is_available(self):
            return False

    monkeypatch.setitem(reg.LOADER_REGISTRY, "ghost_src", Unavailable)
    loader, effective = _resolve_market_loader("ghost_src", ["000001.SZ"])
    assert loader is None
    assert effective == ""


def test_resolve_explicit_source_available(monkeypatch):
    from strategy_research.apps.data_source import registry as reg

    class Available:
        name = "ghost_src"

        def is_available(self):
            return True

    monkeypatch.setitem(reg.LOADER_REGISTRY, "ghost_src", Available)
    loader, effective = _resolve_market_loader("ghost_src", ["000001.SZ"])
    assert loader is not None
    assert effective == "ghost_src"


def test_resolve_auto_detection_falls_back_to_market(monkeypatch):
    from strategy_research.apps.data_source import registry as reg
    from strategy_research.apps.data_source import utils as dsu

    class AutoLoader:
        name = "auto_loader"

    monkeypatch.setattr(reg, "resolve_loader", lambda market: AutoLoader())
    monkeypatch.setattr(dsu, "detect_market", lambda code: "a_share")
    # 未注册的显式 source → 走 auto 分支
    loader, effective = _resolve_market_loader("no_such_source", ["000001.SZ"])
    assert isinstance(loader, AutoLoader)
    assert effective == "auto_loader"


# ── _handle_get_market_data 校验分支 ────────────────────────


def _call_handle(**overrides):
    kwargs = dict(codes=["A"], start_date="2024-01-01", end_date="2024-01-31",
                  interval="1d", source=None, max_rows=10, force_refresh=False)
    kwargs.update(overrides)
    return mcp_server._handle_get_market_data(**kwargs)


def test_handle_requires_codes():
    out = _call_handle(codes=None)
    assert "codes is required" in out


def test_handle_requires_dates():
    assert "start_date and end_date are required" in _call_handle(start_date="")
    assert "start_date and end_date are required" in _call_handle(end_date="")


def test_handle_rejects_inverted_range():
    import json

    out = json.loads(_call_handle(start_date="2024-02-01", end_date="2024-01-01"))
    assert out["status"] == "error"


def test_handle_unknown_source_falls_back_to_auto(monkeypatch):
    """未注册的显式 source 走 auto 检测分支（mock 掉真网络）。"""

    class FakeLoader:
        name = "fake_auto"

        def fetch(self, codes, start, end, interval="1d", force_refresh=False):
            import pandas as pd

            return {c: pd.DataFrame({"close": [1.0, 2.0]}) for c in codes}

    import strategy_research.apps.data_source.registry as reg
    monkeypatch.setattr(reg, "resolve_loader", lambda market: FakeLoader())
    monkeypatch.setattr(
        "strategy_research.apps.data_source.utils.detect_market",
        lambda code: "a_share")

    import json as _json

    out = _json.loads(_call_handle(source="definitely_not_a_source_xyz"))
    assert out["status"] == "ok"
    assert out["meta"]["source"] == "fake_auto"


# ── _wrap_base_tool ─────────────────────────────────────────


class _MiniTool:
    """schema 来自 _schema_from_signature（函数签名），所以要带真签名。"""

    name = "mini"
    description = "mini tool"

    def execute(self, path: str, limit: int = 10) -> str:
        return f"ok"

    to_openai_schema = None  # _wrap_base_tool 不用它

    def invoke(self, args):
        ctx = args.get("ctx")
        return f"path={args.get('path')} limit={args.get('limit')} workspace={ctx.workspace}"


def test_wrap_schema_adds_workspace_param():
    wrapped = _wrap_base_tool(_MiniTool(), include_workspace=True)
    schema = wrapped.to_schema()
    assert "workspace" in schema["inputSchema"]["properties"]
    assert "workspace" in schema["inputSchema"]["required"]


def test_wrap_param_map_renames():
    wrapped = _wrap_base_tool(
        _MiniTool(), param_map={"mcp_path": "path"}, include_workspace=False)
    assert "mcp_path" in wrapped.to_schema()["inputSchema"]["properties"]
    # MCP 名 → agent 名在 handler 里翻译
    out = wrapped.handler(mcp_path="a.txt")
    assert "path=a.txt" in out


def test_wrap_schema_strict_is_off():
    """MCP schema 不得用 strict 模式（会把所有参数塞进 required）。"""
    wrapped = _wrap_base_tool(_MiniTool())
    schema = wrapped.to_schema()["inputSchema"]
    assert "limit" in schema["properties"]
    assert "limit" not in schema["required"]


def test_wrap_handler_routes_workspace_into_ctx(tmp_path):
    wrapped = _wrap_base_tool(_MiniTool(), include_workspace=True)
    out = wrapped.handler(path="a.txt", workspace=str(tmp_path))
    assert str(tmp_path) in out


def test_wrap_handler_catches_exception_as_tool_error():
    class Boom(_MiniTool):
        def invoke(self, args):
            raise RuntimeError("kaboom")

    wrapped = _wrap_base_tool(Boom())
    out = wrapped.handler(path="x")
    assert "kaboom" in out


# ── MCPServer 注册面 ────────────────────────────────────────


def test_server_call_unknown_tool_error():
    server = MCPServer()
    assert "not found" in server.call_tool("nope", {})["error"]


def test_server_call_tool_wraps_handler_exception():
    server = MCPServer()
    server.register(MCPTool(name="boom", description="",
                            parameters={"type": "object", "properties": {}},
                            handler=lambda **kw: (_ for _ in ()).throw(
                                ValueError("handler died"))))
    out = server.call_tool("boom", {})
    assert "ValueError" in out["error"]


def test_server_register_default_tools_all_listed():
    server = MCPServer()
    server.register_default_tools()
    names = {t["name"] for t in server.list_tools()}
    # 每个类别至少一个代表
    # start_research_goal 已随 apps/goal 移除（2026-11-04 批次 2）
    for expected in ("list_skills", "run_backtest", "list_swarm_presets"):
        assert expected in names, expected
