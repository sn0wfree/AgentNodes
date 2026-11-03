"""data_readiness 检查核心 + run_backtest 门禁集成测试。

docs/run-backtest-data-gate.md — C1~C7 检查项。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from strategy_research.apps.data_readiness import check_data_readiness


def _seed_db(workspace: Path, strategy: str = "s1", codes=None, dates=None):
    """Seeds DuckDB with N-day OHLCV per code + one ghost (1-row) asset."""
    import numpy as np

    from strategy_research.apps.db import save_ohlcv_to_db

    codes = codes or ["000001.SZ", "600519.SH"]
    dates = dates or pd.bdate_range("2023-01-02", periods=270)
    rng = np.random.default_rng(5)
    data_map: dict[str, pd.DataFrame] = {}
    for code in codes:
        close = 100 * np.cumprod(1 + rng.normal(0.0004, 0.02, len(dates)))
        data_map[code] = pd.DataFrame(
            {"open": close * 0.99, "high": close * 1.02, "low": close * 0.98,
             "close": close, "volume": 1_000_000},
            index=pd.DatetimeIndex(dates, name="trade_date"),
        )
    ghost = pd.DataFrame(
        {"open": [float("nan")], "high": [float("nan")], "low": [float("nan")],
         "close": [float("nan")], "volume": [0.0]},
        index=pd.DatetimeIndex([dates[0]], name="trade_date"),
    )
    data_map["999999.XX"] = ghost
    save_ohlcv_to_db(workspace, data_map, strategy)


def _config(text: str) -> dict:
    import yaml

    return yaml.safe_load(text)


GOOD_CONFIG = """strategy:
  name: s1
  type: rotation
data:
  source: duckdb
  codes: ['000001.SZ', '600519.SH']
  start_date: '2023-01-02'
  end_date: '2023-12-31'
rebalance:
  freq: M
  min_history: 60
factors:
  - name: momentum_20d
    code: 'ts_return(close, 20)'
    weight: 1.0
"""


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    _seed_db(ws)
    return ws


def _status(report, cid: str) -> str:
    for c in report.checks:
        if c.id == cid:
            return c.status
    raise AssertionError(f"check {cid} missing: {[c.id for c in report.checks]}")


def test_all_good_passes(workspace: Path):
    cfg = _config(GOOD_CONFIG)
    report = check_data_readiness(workspace, "s1", cfg=cfg)
    assert report.ok is True
    assert _status(report, "C1") == "ok"
    assert _status(report, "C3") == "ok"
    assert _status(report, "C4") == "ok"
    assert _status(report, "C5") == "ok"
    assert _status(report, "C6") == "ok"


def test_c1_missing_asset_fails(workspace: Path):
    cfg = _config(GOOD_CONFIG.replace("codes: ['000001.SZ', '600519.SH']",
                                      "codes: ['000001.SZ', '300015.SZ']"))
    report = check_data_readiness(workspace, "s1", cfg=cfg)
    assert report.ok is False
    assert _status(report, "C1") == "fail"
    c1 = next(c for c in report.checks if c.id == "C1")
    assert "300015.SZ" in c1.detail
    assert "get_market_data" in c1.fix_hint


def test_c2_orphan_warn_does_not_block(workspace: Path):
    cfg = _config(GOOD_CONFIG)
    report = check_data_readiness(workspace, "s1", cfg=cfg)
    assert _status(report, "C2") == "warn"
    assert report.ok is True  # orphan is warn-only


def test_c3_window_short_back_fails(workspace: Path):
    cfg = _config(GOOD_CONFIG.replace("end_date: '2023-12-31'",
                                      "end_date: '2024-06-30'"))
    report = check_data_readiness(workspace, "s1", cfg=cfg)
    assert _status(report, "C3") == "fail"
    assert report.ok is False


def test_c4_tiny_asset_fails(workspace: Path):
    cfg = _config(GOOD_CONFIG.replace("codes: ['000001.SZ', '600519.SH']",
                                      "codes: ['000001.SZ', '999999.XX']"))
    report = check_data_readiness(workspace, "s1", cfg=cfg)
    assert _status(report, "C4") == "fail"


def test_c5_quality_fails_on_all_nan_asset(workspace: Path):
    cfg = _config(GOOD_CONFIG.replace("codes: ['000001.SZ', '600519.SH']",
                                      "codes: ['000001.SZ', '999999.XX']"))
    report = check_data_readiness(workspace, "s1", cfg=cfg)
    assert _status(report, "C5") == "fail"


def test_c6_bad_factor_syntax_fails(workspace: Path):
    cfg = _config(GOOD_CONFIG.replace("code: 'ts_return(close, 20)'",
                                      "code: 'ts_return(close, 20'"))
    report = check_data_readiness(workspace, "s1", cfg=cfg)
    assert _status(report, "C6") == "fail"
    assert report.ok is False


def test_no_codes_skips_coverage_checks(workspace: Path):
    cfg = _config(GOOD_CONFIG.replace("  codes: ['000001.SZ', '600519.SH']\n", ""))
    report = check_data_readiness(workspace, "s1", cfg=cfg)
    assert _status(report, "C1") == "warn"  # skipped + hint
    assert report.ok is True


def test_explicit_codes_mode(workspace: Path):
    """source='explicit': cfg=None + codes/dates 直传。"""
    report = check_data_readiness(
        workspace, "s1", cfg=None,
        codes=["000001.SZ", "600519.SH"],
        start_date="2023-01-02", end_date="2023-12-31",
    )
    assert report.ok is True
    assert _status(report, "C1") == "ok"


def test_c7_included_when_requested(workspace: Path):
    cfg = _config(GOOD_CONFIG)
    report = check_data_readiness(workspace, "s1", cfg=cfg, include_cleaning=True)
    assert any(c.id == "C7" for c in report.checks)


# ── 门禁集成: run_backtest 在数据未就绪时拦截、不建 run ─────────────


def test_run_backtest_gate_blocks_and_does_not_create_run(workspace: Path):
    from strategy_research.kernel.agent.builtin_tools import RunBacktestTool
    from strategy_research.kernel.agent.tools import ToolContext

    sdir = workspace / "strategies" / "s1"
    sdir.mkdir(parents=True)
    # codes 缺 300015.SZ → C1 fail → 门禁拦截
    cfg_text = GOOD_CONFIG.replace("codes: ['000001.SZ', '600519.SH']",
                                   "codes: ['000001.SZ', '300015.SZ']")
    (sdir / "config.yaml").write_text(cfg_text)

    out = RunBacktestTool().execute(
        ctx=ToolContext(workspace=workspace), strategy_name="s1",
    )
    import json

    payload = json.loads(out)
    assert payload["status"] == "error"
    assert payload.get("step") == "data_gate"
    assert "readiness" in payload
    assert (sdir / "runs").exists() is False, "gate must not create run dir"


def test_run_backtest_gate_ok_runs(workspace: Path):
    from strategy_research.kernel.agent.builtin_tools import RunBacktestTool
    from strategy_research.kernel.agent.tools import ToolContext

    sdir = workspace / "strategies" / "s1"
    sdir.mkdir(parents=True)
    (sdir / "config.yaml").write_text(GOOD_CONFIG)

    out = RunBacktestTool().execute(
        ctx=ToolContext(workspace=workspace), strategy_name="s1",
    )
    import json

    payload = json.loads(out)
    assert payload["status"] == "ok"
    assert payload["run_status"] == "success"
    assert "readiness" in payload
    assert (sdir / "runs" / payload["run"] / "metrics.json").exists()


def test_check_data_tool_returns_report(workspace: Path):
    from strategy_research.kernel.agent.builtin_tools.data_tools import CheckDataTool
    from strategy_research.kernel.agent.tools import ToolContext

    sdir = workspace / "strategies" / "s1"
    sdir.mkdir(parents=True)
    (sdir / "config.yaml").write_text(GOOD_CONFIG)

    out = CheckDataTool().execute(
        ctx=ToolContext(workspace=workspace), strategy_name="s1",
    )
    import json

    payload = json.loads(out)
    assert payload["status"] == "ok"
    assert payload["readiness"]["ok"] is True
    assert "checks" in payload["readiness"]


def test_check_data_tool_explicit_mode(workspace: Path):
    from strategy_research.kernel.agent.builtin_tools.data_tools import CheckDataTool
    from strategy_research.kernel.agent.tools import ToolContext

    out = CheckDataTool().execute(
        ctx=ToolContext(workspace=workspace), strategy_name="s1",
        source="explicit", codes=["000001.SZ", "600519.SH"],
        start_date="2023-01-02", end_date="2023-12-31",
    )
    import json

    payload = json.loads(out)
    assert payload["status"] == "ok"
    assert payload["readiness"]["ok"] is True


def test_check_data_tool_missing_strategy(workspace: Path):
    """缺必填参数由框架拦截 (TypeError → loop 重试/兜底)。"""
    from strategy_research.kernel.agent.builtin_tools.data_tools import CheckDataTool
    from strategy_research.kernel.agent.tools import ToolContext

    with pytest.raises(TypeError):
        CheckDataTool().execute(ctx=ToolContext(workspace=workspace))
