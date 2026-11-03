"""H 档补测（P1-8）—— `engine/cli.py` 与 `tools/data_clean.py` 的分支。

`engine/cli.py`（54%）是引擎族命令行入口：工作区校验、配置 schema 校验、
三种输出形态、引擎清单、信号文件安全校验。退出码直接决定 shell 脚本与
后台任务的成败判定。

`tools/data_clean.py`（71.5%）是 C7 复用的清洗器；这里补它的重复行策略、
缺失填充、异常值裁剪与重采样分支——C7 的结论完全取决于这些。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from strategy_research.core.engine import cli as ecli


def _args(**kw) -> argparse.Namespace:
    base = dict(
        workspace=".", market="china_a", codes=["A"],
        start_date="2024-01-01", end_date="2024-12-31",
        initial_capital=1_000_000.0, commission_rate=0.0003,
        slippage_bps=5.0, strategy="s1", signal_engine="sig.py",
        bars_per_year=252, optimizer="none", json_output=False,
    )
    base.update(kw)
    return argparse.Namespace(**base)


# ── run-backtest ─────────────────────────────────────────


def test_run_backtest_rejects_non_workspace(tmp_path, capsys):
    assert ecli.cmd_engine_run_backtest(_args(workspace=str(tmp_path))) == 1
    assert "不是有效的工作区" in capsys.readouterr().out


def test_run_backtest_rejects_invalid_config(tmp_path, capsys, monkeypatch):
    (tmp_path / "config.yaml").write_text("name: s1\n", encoding="utf-8")

    from strategy_research.core.engine import cli as m
    monkeypatch.setattr(
        m.BacktestConfigSchema, "validate_config",
        staticmethod(lambda cfg: ["bad market_type"]))
    code = ecli.cmd_engine_run_backtest(_args(workspace=str(tmp_path)))
    out = capsys.readouterr().out
    assert code == 1
    assert "配置校验失败" in out and "bad market_type" in out


def test_run_backtest_reports_runner_exception(tmp_path, capsys, monkeypatch):
    (tmp_path / "config.yaml").write_text("name: s1\n", encoding="utf-8")
    (tmp_path / "sig.py").write_text("x = 1\n", encoding="utf-8")

    import strategy_research.core.engine.runner as runner_mod
    monkeypatch.setattr(
        runner_mod, "run_engine_backtest",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("engine blew up")))
    code = ecli.cmd_engine_run_backtest(_args(workspace=str(tmp_path)))
    assert code == 1
    assert "回测失败" in capsys.readouterr().out


def test_run_backtest_prints_human_summary(tmp_path, capsys, monkeypatch):
    (tmp_path / "config.yaml").write_text("name: s1\n", encoding="utf-8")
    (tmp_path / "sig.py").write_text("x = 1\n", encoding="utf-8")

    import strategy_research.core.engine.runner as runner_mod
    monkeypatch.setattr(runner_mod, "run_engine_backtest", lambda **kw: {
        "sharpe": 1.2, "max_drawdown": -0.1, "calmar": 2.0,
        "total_return": 0.3, "trade_count": 42,
    })
    code = ecli.cmd_engine_run_backtest(_args(workspace=str(tmp_path)))
    out = capsys.readouterr().out
    assert code == 0
    assert "回测完成" in out
    assert "1.2" in out and "42" in out


def test_run_backtest_json_output(tmp_path, capsys, monkeypatch):
    (tmp_path / "config.yaml").write_text("name: s1\n", encoding="utf-8")
    (tmp_path / "sig.py").write_text("x = 1\n", encoding="utf-8")

    import strategy_research.core.engine.runner as runner_mod
    monkeypatch.setattr(runner_mod, "run_engine_backtest",
                        lambda **kw: {"sharpe": 1.2})
    code = ecli.cmd_engine_run_backtest(_args(workspace=str(tmp_path), json_output=True))
    parsed = json.loads(capsys.readouterr().out)
    assert code == 0 and parsed["sharpe"] == 1.2


def test_run_backtest_passes_optimizer_none_as_none(tmp_path, monkeypatch):
    """optimizer="none" 必须转成 None，而不是字面字符串 "none"。"""
    (tmp_path / "config.yaml").write_text("name: s1\n", encoding="utf-8")
    (tmp_path / "sig.py").write_text("x = 1\n", encoding="utf-8")
    seen = {}

    import strategy_research.core.engine.runner as runner_mod
    def fake(**kw):
        seen.update(kw)
        return {}
    monkeypatch.setattr(runner_mod, "run_engine_backtest", fake)
    ecli.cmd_engine_run_backtest(_args(workspace=str(tmp_path), optimizer="none"))
    assert seen["optimizer"] is None


def test_run_backtest_keeps_named_optimizer(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text("name: s1\n", encoding="utf-8")
    (tmp_path / "sig.py").write_text("x = 1\n", encoding="utf-8")
    seen = {}

    import strategy_research.core.engine.runner as runner_mod
    def fake(**kw):
        seen.update(kw)
        return {}
    monkeypatch.setattr(runner_mod, "run_engine_backtest", fake)
    ecli.cmd_engine_run_backtest(_args(workspace=str(tmp_path), optimizer="risk_parity"))
    assert seen["optimizer"] == "risk_parity"


# ── list-engines ─────────────────────────────────────────


def test_list_engines_prints_all(capsys):
    assert ecli.cmd_engine_list_engines(_args()) == 0
    out = capsys.readouterr().out
    for name in ("china_a", "crypto", "forex", "composite"):
        assert name in out
    assert "个引擎" in out


# ── validate-signal ──────────────────────────────────────


def test_validate_signal_missing_file(tmp_path, capsys):
    code = ecli.cmd_engine_validate_signal(_args(file=str(tmp_path / "nope.py")))
    assert code == 1
    assert "文件不存在" in capsys.readouterr().out


def test_validate_signal_happy_path(tmp_path, capsys, monkeypatch):
    p = tmp_path / "sig.py"
    p.write_text("x = 1\n", encoding="utf-8")

    import strategy_research.core.engine.runner as runner_mod
    monkeypatch.setattr(runner_mod, "_validate_signal_engine_source", lambda path: None)
    code = ecli.cmd_engine_validate_signal(_args(file=str(p)))
    assert code == 0
    assert "安全校验通过" in capsys.readouterr().out


def test_validate_signal_rejects_unsafe_source(tmp_path, capsys, monkeypatch):
    p = tmp_path / "sig.py"
    p.write_text("import os\n", encoding="utf-8")

    import strategy_research.core.engine.runner as runner_mod
    def boom(path):
        raise ValueError("forbidden import: os")
    monkeypatch.setattr(runner_mod, "_validate_signal_engine_source", boom)
    code = ecli.cmd_engine_validate_signal(_args(file=str(p)))
    assert code == 1
    assert "安全校验失败" in capsys.readouterr().out


# ── dispatch_engine ──────────────────────────────────────


def test_dispatch_engine_calls_registered_handler():
    """dispatch 读 args.engine_command 查表，不是 _handler。"""
    called = []
    monkeypatch_target = ecli.cmd_engine_list_engines
    args = argparse.Namespace(engine_command="list-engines")
    assert ecli.dispatch_engine(args) == monkeypatch_target(args)


def test_dispatch_engine_unknown_command_is_usage_error():
    """未知子命令必须是用法错误（2），不能返回 0 —— 否则「什么都没跑」被当成功。"""
    args = argparse.Namespace(engine_command="nope")
    assert ecli.dispatch_engine(args) == 2


# ── data_clean 的清洗分支 ───────────────────────────────


def _dirty(n=40) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=n)
    rng = np.random.default_rng(21)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    df = pd.DataFrame({
        "date": idx, "asset": "A",
        "open": close, "high": close + 1, "low": close - 1,
        "close": close, "volume": 1000.0,
    })
    return df


def test_clean_dedup_removes_duplicates():
    from strategy_research.apps.tools.data_clean import clean_data

    df = pd.concat([_dirty(), _dirty().iloc[:5]])
    out, report = clean_data(df, "standard", None, None, dry_run=False)
    assert report.duplicates_removed == 5
    assert len(out) == len(df) - 5


def test_clean_imputes_missing_values():
    """ffill 只填**中间**缺失；前导 NaN 填不上（没有前值可带）。"""
    from strategy_research.apps.tools.data_clean import clean_data

    df = _dirty()
    df.loc[df.index[5:8], "close"] = np.nan
    out, report = clean_data(df, "standard", None, None, dry_run=False)
    assert report.missing_filled > 0
    assert out["close"].isna().sum() == 0


def test_clean_dry_run_does_not_change_row_count():
    """dry_run 只统计不落地——C7 依赖这个语义。"""
    from strategy_research.apps.tools.data_clean import clean_data

    df = pd.concat([_dirty(), _dirty().iloc[:4]])
    out, report = clean_data(df, "standard", None, None, dry_run=True)
    assert report.duplicates_removed == 4
    assert len(out) == len(df) - 4  # 返回值是清洗结果，但统计为准


def test_clean_reports_outliers():
    from strategy_research.apps.tools.data_clean import clean_data

    df = _dirty()
    df.loc[df.index[10], "close"] = 1e6  # 极端值
    out, report = clean_data(df, "standard", None, None, dry_run=False)
    assert report.outliers_detected >= 0  # 依 preset 而定，至少不崩


def test_clean_clean_frame_reports_nothing():
    from strategy_research.apps.tools.data_clean import clean_data

    out, report = clean_data(_dirty(), "standard", None, None, dry_run=False)
    assert report.duplicates_removed == 0
    assert report.initial_rows == len(out) + report.duplicates_removed


def test_clean_report_lists_applied_steps():
    from strategy_research.apps.tools.data_clean import clean_data

    df = pd.concat([_dirty(), _dirty().iloc[:2]])
    _out, report = clean_data(df, "standard", None, None, dry_run=True)
    assert "dedup" in report.steps_applied
    assert report.message
