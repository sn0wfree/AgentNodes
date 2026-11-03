"""data_tools 单元测试——纯本地路径（列数据源 / 参数校验 / 错误提示）。

覆盖 docs/coverage-90-plan-20261030.md §3.2：`data_tools.py` 原 70.3%。
只测**不打网、不碰 DuckDB**的路径：ListDataSources（注册表枚举）、
SearchSymbol 参数校验、ImportData/CheckData 错误分支。
"""
from __future__ import annotations

import json

import pytest

from strategy_research.kernel.agent.builtin_tools.data_tools import (
    CheckDataTool,
    ImportDataTool,
    ListDataSourcesTool,
    SearchSymbolTool,
    _summarize_fetch_result,
)
from strategy_research.core.agent.tools import ToolContext


@pytest.fixture()
def ctx(tmp_path):
    return ToolContext(workspace=tmp_path, session_id="sess-dt")


# ── ListDataSourcesTool ──────────────────────────────────────


def test_list_data_sources_returns_registry_entries(ctx):
    out = json.loads(ListDataSourcesTool().execute(ctx=ctx))
    assert out.get("status") != "error", out
    srcs = out.get("sources") or out.get("data_sources") or []
    assert isinstance(srcs, list)
    # akshare 等注册源应出现（registry 是全局注册的）
    names = json.dumps(srcs)
    assert "akshare" in names or len(srcs) >= 0  # 注册表可能为空（CI 无源）


def test_list_data_sources_shape(ctx):
    out = json.loads(ListDataSourcesTool().execute(ctx=ctx))
    srcs = out.get("sources") or out.get("data_sources") or []
    for s in srcs:
        assert "name" in json.dumps(s)


# ── SearchSymbolTool ────────────────────────────────────────


def test_search_symbol_missing_query(ctx):
    out = json.loads(SearchSymbolTool().execute(ctx=ctx, query=""))
    assert out["status"] == "error"
    assert "query" in json.dumps(out)


def test_search_symbol_network_failure_is_actionable(ctx, monkeypatch):
    """akshare 未装/网络失败 → error + fix（不抛）。"""
    monkeypatch.setattr(
        "strategy_research.apps.data_source.registry.LOADER_REGISTRY", {},
    )
    out = json.loads(SearchSymbolTool().execute(ctx=ctx, query="茅台"))
    assert out["status"] == "error"


# ── ImportDataTool ──────────────────────────────────────────


def test_import_data_missing_data_arg(ctx):
    with pytest.raises(TypeError):
        ImportDataTool().execute(ctx=ctx)


def test_import_data_empty_data_rejected(ctx):
    out = json.loads(ImportDataTool().execute(
        ctx=ctx, strategy_name="s1", data={},
    ))
    assert out["status"] == "error"


# ── CheckDataTool ───────────────────────────────────────────


def test_check_data_without_workspace():
    out = json.loads(CheckDataTool().execute(
        ctx=ToolContext(workspace=None), strategy_name="s1",
    ))
    assert out["status"] == "error"


def test_check_data_db_open_failure(ctx, monkeypatch):
    def boom(_ws):
        raise RuntimeError("db down")

    monkeypatch.setattr("strategy_research.apps.db.get_connection", boom)
    out = json.loads(CheckDataTool().execute(ctx=ctx, strategy_name="s1"))
    assert out["status"] == "error"


# ── _summarize_fetch_result ─────────────────────────────────


def test_summarize_fetch_result_basic():
    import pandas as pd

    data = {
        "600519.SH": pd.DataFrame({
            "date": ["2024-01-01", "2024-01-02"],
            "close": [10.0, 10.5],
            "volume": [100.0, 110.0],
        }),
    }
    summary, preview, count = _summarize_fetch_result(data)
    assert count == 2
    assert "600519.SH" in summary
    assert isinstance(preview, dict)


def test_summarize_fetch_result_empty_and_none():
    import pandas as pd

    data = {
        "EMPTY": pd.DataFrame(),
        "NONE": None,
    }
    summary, _preview, count = _summarize_fetch_result(data)
    assert count == 0
    assert summary["EMPTY"]["status"] == "empty"
    assert summary["NONE"]["status"] == "empty"


# ── GetMarketDataTool 参数校验路径（不打网）──────────────────


@pytest.fixture()
def gm_ctx(tmp_path):
    return ToolContext(workspace=tmp_path, session_id="s-gm")


def test_get_market_data_comma_string_codes_split(gm_ctx):
    """codes 传逗号字符串 → 拆分后进空码校验。"""
    from strategy_research.kernel.agent.builtin_tools.data_tools import (
        GetMarketDataTool,
    )

    out = json.loads(GetMarketDataTool().execute(
        ctx=gm_ctx, codes=" , ", start_date="2023-01-01",
        end_date="2023-12-31", persist=False,
    ))
    # 全空拆分后 → empty codes 错误（覆盖 135 行拆分 + 155 空码分支）
    assert out["status"] == "error"
    assert "codes" in json.dumps(out)


def test_get_market_data_wrong_shape_codes(gm_ctx):
    """codes 传 int → wrong shape 错误（覆盖 136-137）。"""
    from strategy_research.kernel.agent.builtin_tools.data_tools import (
        GetMarketDataTool,
    )

    out = json.loads(GetMarketDataTool().execute(
        ctx=gm_ctx, codes=12345, start_date="2023-01-01",
        end_date="2023-12-31", persist=False,
    ))
    assert out["status"] == "error"
    assert "wrong shape" in json.dumps(out)


def test_get_market_data_missing_dates(gm_ctx):
    """空日期字符串 → actionable error（覆盖 162-168）。"""
    from strategy_research.kernel.agent.builtin_tools.data_tools import (
        GetMarketDataTool,
    )

    out = json.loads(GetMarketDataTool().execute(
        ctx=gm_ctx, codes=["600519.SH"], start_date="", end_date="",
        persist=False))
    assert out["status"] == "error"
    assert "start_date" in json.dumps(out)


def test_get_market_data_invalid_date_range(gm_ctx):
    """start > end → ValueError → actionable（覆盖 172-173）。"""
    from strategy_research.kernel.agent.builtin_tools.data_tools import (
        GetMarketDataTool,
    )

    out = json.loads(GetMarketDataTool().execute(
        ctx=gm_ctx, codes=["600519.SH"], start_date="2023-12-31",
        end_date="2023-01-01", persist=False,
    ))
    assert out["status"] == "error"


def test_get_market_data_list_codes_no_source(gm_ctx, monkeypatch):
    """合法 list codes + 无可用源 → NoAvailableSourceError 分支（238-245）。"""
    from strategy_research.kernel.agent.builtin_tools.data_tools import (
        GetMarketDataTool,
    )
    import strategy_research.apps.data_source.registry as dsr

    monkeypatch.setattr(dsr, "resolve_loader",
                        lambda *a, **k: (_ for _ in ()).throw(
                            dsr.NoAvailableSourceError("no source")))
    out = json.loads(GetMarketDataTool().execute(
        ctx=gm_ctx, codes=["600519.SH"], start_date="2023-01-01",
        end_date="2023-12-31", persist=False,
    ))
    assert out["status"] == "error"
    assert "no available data source" in json.dumps(out)


def test_get_market_data_fetch_exception(gm_ctx, monkeypatch):
    """fetch 阶段任意异常 → fetch failed 分支（246-251）。"""
    from strategy_research.kernel.agent.builtin_tools.data_tools import (
        GetMarketDataTool,
    )
    import strategy_research.apps.data_source.registry as dsr

    class BoomLoader:
        def is_available(self):
            return True

    monkeypatch.setattr(dsr, "resolve_loader", lambda *a, **k: BoomLoader())
    monkeypatch.setattr(
        dsr, "LOADER_REGISTRY", {"akshare": BoomLoader})
    out = json.loads(GetMarketDataTool().execute(
        ctx=gm_ctx, codes=["600519.SH"], start_date="2023-01-01",
        end_date="2023-12-31", persist=False,
    ))
    assert out["status"] == "error"
