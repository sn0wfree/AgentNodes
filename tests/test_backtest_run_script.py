"""H 档补测（P1-3）—— `core/backtest.py` 的 ``run_backtest_from_yaml``。

这段（557–675）是 YAML 回测入口的完整编排：跑 YAML → 建 run 目录 →
落 metrics → 写 factor_failures → 存 DuckDB → 导出 equity_curve →
写 run_card。此前只有「配置文件不存在」与异常兜底被测到，**成功路径
整条空白**。

这些落盘文件是 agent 后续 read_file 的唯一数据源——少写一个，agent
就只能凭空猜。测试把 ``config_runner.run_from_yaml`` 换成假 Result，
逐个验证落盘契约。

注意：模块里有两个入口——
- ``run_backtest_script``：跑 strategy.py（脚本模式，需 strategy.py 存在）
- ``run_backtest_from_yaml``：跑 config.yaml（本文件测的这个）
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from strategy_research.core import backtest as bt


class FakeResult:
    def __init__(self, metrics=None, factor_failures=None, warnings=None,
                 nav=None):
        self.metrics = metrics if metrics is not None else {"calmar": 1.1, "sharpe": 0.9}
        self.factor_failures = factor_failures or []
        self.warnings = warnings or []
        self.weights_history = {}
        self.nav_daily = nav if nav is not None else pd.Series(
            [1.0, 1.01], index=pd.to_datetime(["2026-01-02", "2026-01-05"]))


@pytest.fixture
def ws(tmp_path) -> Path:
    d = tmp_path / "ws"
    (d / "strategies" / "s1").mkdir(parents=True)
    (d / "strategies" / "s1" / "config.yaml").write_text("name: s1\n", encoding="utf-8")
    return d


def _run(ws: Path, monkeypatch, result=None, **kw):
    from strategy_research.apps import config_runner

    fake = result or FakeResult()
    monkeypatch.setattr(config_runner, "run_from_yaml", lambda *a, **k: fake)
    # 隔离外部副作用：DuckDB / git / run_card
    monkeypatch.setattr(bt, "save_backtest_result", lambda **k: None)
    monkeypatch.setattr(bt, "save_weight_history", lambda *a, **k: None)
    monkeypatch.setattr(bt, "save_nav_history", lambda *a, **k: None)
    monkeypatch.setattr(bt, "git_get_hash", lambda *a, **k: "deadbeef")
    monkeypatch.setattr(bt, "write_run_card", lambda *a, **k: None)
    return bt.run_backtest_from_yaml(workspace_path=ws, strategy_name="s1", **kw)


# ── 前置校验 ─────────────────────────────────────────────


def test_missing_config_returns_error(tmp_path):
    ws = tmp_path / "empty"
    (ws / "strategies" / "none").mkdir(parents=True)
    out = bt.run_backtest_from_yaml(workspace_path=ws, strategy_name="none")
    assert out["success"] is False
    assert "配置文件不存在" in out["error"]


def test_custom_strategy_dir_is_honoured(tmp_path, monkeypatch):
    """study 场景传入外部 strategy_dir——config.yaml 从那里读。"""
    other = tmp_path / "study" / "sX"
    other.mkdir(parents=True)
    (other / "config.yaml").write_text("name: sX\n", encoding="utf-8")
    out = _run(tmp_path, monkeypatch,
               strategy_dir=other, runs_dir=tmp_path / "rounds")
    assert out["success"] is True
    assert (tmp_path / "rounds" / out["run"]).is_dir()


# ── 成功路径 ─────────────────────────────────────────────


def test_success_writes_metrics_and_returns(ws, monkeypatch):
    out = _run(ws, monkeypatch, action="tweak_factor", description="why")
    assert out["success"] is True
    assert out["run"].startswith("run_")
    run_dir = ws / "strategies" / "s1" / "runs" / out["run"]
    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["action"] == "tweak_factor"
    assert metrics["description"] == "why"
    assert metrics["status"] == "success"
    assert metrics["commit"] == "deadbeef"


def test_success_exports_equity_curve(ws, monkeypatch):
    out = _run(ws, monkeypatch)
    run_dir = ws / "strategies" / "s1" / "runs" / out["run"]
    curve = run_dir / "equity_curve.csv"
    assert curve.exists()
    text = curve.read_text(encoding="utf-8")
    assert "date,nav" in text
    assert "2026-01-02" in text


def test_empty_nav_writes_no_curve_and_no_warning(ws, monkeypatch):
    out = _run(ws, monkeypatch, result=FakeResult(nav=pd.Series([], dtype=float)))
    run_dir = ws / "strategies" / "s1" / "runs" / out["run"]
    assert not (run_dir / "equity_curve.csv").exists()
    assert out["metrics"]["warnings"] == []


def test_nav_export_failure_becomes_warning(ws, monkeypatch):
    """导出失败不能让整轮回测失败——降级成 warning 并重写 metrics。"""
    bad_nav = pd.Series([1.0], index=pd.Index(["not-a-date"]))
    monkeypatch.setattr(
        pd, "to_datetime",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("bad")),
    )
    out = _run(ws, monkeypatch, result=FakeResult(nav=bad_nav))
    assert out["success"] is True
    assert any("equity_curve.csv 导出失败" in w for w in out["metrics"]["warnings"])


def test_factor_failures_are_persisted_and_summarised(ws, monkeypatch):
    failures = [
        {"factor": "mom", "error": "all NaN"},
        {"factor": "mom", "error": "all NaN"},
        {"factor": "vol", "error": "zero variance"},
    ]
    out = _run(ws, monkeypatch, result=FakeResult(factor_failures=failures))
    run_dir = ws / "strategies" / "s1" / "runs" / out["run"]

    raw = json.loads((run_dir / "factor_failures.json").read_text(encoding="utf-8"))
    assert len(raw) == 3

    summary = out["metrics"]["factor_failures_summary"]
    by_factor = {s["factor"]: s for s in summary}
    assert by_factor["mom"]["failed_assets"] == 2
    assert by_factor["vol"]["failed_assets"] == 1
    assert by_factor["mom"]["first_error"] == "all NaN"


def test_no_factor_failures_omits_file_and_summary(ws, monkeypatch):
    out = _run(ws, monkeypatch)
    run_dir = ws / "strategies" / "s1" / "runs" / out["run"]
    assert not (run_dir / "factor_failures.json").exists()
    assert "factor_failures_summary" not in out["metrics"]
    assert out["factor_failures"] == []


def test_warnings_from_result_are_propagated(ws, monkeypatch):
    out = _run(ws, monkeypatch, result=FakeResult(warnings=["cache miss"]))
    assert out["metrics"]["warnings"] == ["cache miss"]


def test_runner_exception_is_captured_not_raised(ws, monkeypatch):
    from strategy_research.apps import config_runner
    monkeypatch.setattr(
        config_runner, "run_from_yaml",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    out = bt.run_backtest_from_yaml(workspace_path=ws, strategy_name="s1")
    assert out["success"] is False
    assert "boom" in out["error"]


# ── _factor_failures_summary ─────────────────────────────


def test_summary_caps_at_five_and_reports_remainder():
    failures = [{"factor": f"f{i}", "error": "e"} for i in range(8)]
    summary = bt._factor_failures_summary(failures, max_items=5)
    assert len(summary) == 6  # 5 条 + 1 条汇总
    assert summary[-1]["factor"] == "..."
    assert summary[-1]["failed_assets"] == 3
    assert "3 个因子" in summary[-1]["first_error"]


def test_summary_truncates_long_error():
    failures = [{"factor": "f", "error": "x" * 500}]
    assert len(bt._factor_failures_summary(failures)[0]["first_error"]) == 200


def test_summary_handles_missing_factor_name():
    summary = bt._factor_failures_summary([{"error": "e"}])
    assert summary[0]["factor"] == "unknown"


def test_summary_of_empty_list_is_empty():
    assert bt._factor_failures_summary([]) == []


# ── evaluate_experiment ──────────────────────────────────
# 注意：get_connection 是在函数内部 import 的，要 patch db 模块而非 bt。


def test_evaluate_missing_run_dir_returns_false(tmp_path):
    assert bt.evaluate_experiment(tmp_path, "s1", "run_0001") is False


def test_evaluate_missing_metrics_returns_false(tmp_path):
    (tmp_path / "strategies" / "s1" / "runs" / "run_0001").mkdir(parents=True)
    assert bt.evaluate_experiment(tmp_path, "s1", "run_0001") is False


def test_evaluate_updates_metrics_status(tmp_path, monkeypatch):
    run_dir = tmp_path / "strategies" / "s1" / "runs" / "run_0001"
    run_dir.mkdir(parents=True)
    (run_dir / "metrics.json").write_text(
        json.dumps({"calmar": 1.0, "status": "pending"}), encoding="utf-8")
    monkeypatch.setattr(bt, "update_results_tsv", lambda *a, **k: None)

    from strategy_research.core import db as dbmod
    monkeypatch.setattr(dbmod, "get_connection", lambda *a, **k: None)

    assert bt.evaluate_experiment(tmp_path, "s1", "run_0001", status="discard") is True
    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["status"] == "discard"
