"""回测分析三件套单元测试: strategy_compare / drawdown_analysis / benchmark_comparison。

此前仅有注册白名单断言, 无功能测试。手算可验证数据:
- strategy_compare: results.tsv 行/列选择语义
- drawdown_analysis: 已知权益曲线 → 精确回撤区间
- benchmark_comparison: 已知策略/基准序列 → alpha/beta/IR 数学断言
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research.kernel.agent.builtin_tools import (
    BenchmarkComparison,
    DrawdownAnalysis,
    StrategyCompare,
)
from strategy_research.kernel.agent.tools import ToolContext

# 已知权益曲线 (12 点): 一次完整回撤 + 一次浅回撤
EQUITY = [1.0, 1.1, 1.2, 1.05, 0.9, 1.0, 1.2, 1.3, 1.25, 1.35, 1.4, 1.5]
DATES = [f"2024-01-{i + 1:02d}" for i in range(len(EQUITY))]


def parse(result: str) -> dict:
    return json.loads(result)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "strategies").mkdir(parents=True)
    return ws


def write_run(ws: Path, strategy: str, run: str, equity: list | None = None,
              name: str = "equity.csv") -> Path:
    run_dir = ws / "strategies" / strategy / "runs" / run
    run_dir.mkdir(parents=True)
    if equity is not None:
        from datetime import date, timedelta

        d = date(2024, 1, 1)
        lines = ["date,equity"]
        for v in equity:
            lines.append(f"{d.isoformat()},{v}")
            d += timedelta(days=1)
        (run_dir / name).write_text("\n".join(lines) + "\n")
    return run_dir


def write_results_tsv(ws: Path, strategy: str, rows: list[dict]) -> Path:
    path = ws / "strategies" / strategy / "runs" / "results.tsv"
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = list(rows[0].keys())
    lines = ["\t".join(cols)]
    lines += ["\t".join(str(r[c]) for c in cols) for r in rows]
    path.write_text("\n".join(lines) + "\n")
    return path


# ── StrategyCompare ──────────────────────────────────────────────────


class TestStrategyCompare:
    def test_compare_latest_rows(self, workspace):
        write_results_tsv(workspace, "mom_20d", [
            {"run_name": "run_0001", "sharpe": "0.5", "ann_return": "0.1234567", "max_dd": "-0.1"},
            {"run_name": "run_0002", "sharpe": "0.8", "ann_return": "0.25", "max_dd": "-0.05"},
        ])
        write_results_tsv(workspace, "mom_60d", [
            {"run_name": "run_0001", "sharpe": "1.2", "ann_return": "0.3", "max_dd": "-0.2"},
        ])
        result = parse(StrategyCompare().execute(
            ctx=ToolContext(workspace=workspace),
            strategy_names="mom_20d,mom_60d",
            metrics="sharpe,ann_return,max_dd",
        ))
        assert result["status"] == "ok"
        comp = {r["strategy"]: r for r in result["comparison"]}
        # 取最新一行 (run_0002) 且 round 4 位
        assert comp["mom_20d"]["run_name"] == "run_0002"
        assert comp["mom_20d"]["sharpe"] == 0.8
        assert comp["mom_20d"]["ann_return"] == 0.25
        assert comp["mom_60d"]["sharpe"] == 1.2

    def test_missing_strategy_gets_error_field(self, workspace):
        write_results_tsv(workspace, "exists", [{"run_name": "run_0001", "sharpe": "0.5"}])
        result = parse(StrategyCompare().execute(
            ctx=ToolContext(workspace=workspace),
            strategy_names="exists,missing",
            metrics="sharpe",
        ))
        assert result["status"] == "ok"
        by_name = {r["strategy"]: r for r in result["comparison"]}
        assert by_name["exists"]["sharpe"] == 0.5
        assert "error" in by_name["missing"]

    def test_empty_results_file(self, workspace):
        path = workspace / "strategies" / "s1" / "runs" / "results.tsv"
        path.parent.mkdir(parents=True)
        path.write_text("run_name\tsharpe\n")
        result = parse(StrategyCompare().execute(
            ctx=ToolContext(workspace=workspace),
            strategy_names="s1", metrics="sharpe",
        ))
        assert result["status"] == "ok"
        assert "no runs found" in result["comparison"][0]["error"]

    def test_missing_metric_column_null_and_parse_failure_preserved(self, workspace):
        write_results_tsv(workspace, "s1", [
            {"run_name": "run_0001", "sharpe": "0.5", "win_rate": "abc"},
        ])
        result = parse(StrategyCompare().execute(
            ctx=ToolContext(workspace=workspace),
            strategy_names="s1", metrics="sharpe,calmar,win_rate",
        ))
        row = result["comparison"][0]
        assert row["sharpe"] == 0.5
        assert row["calmar"] is None
        assert row["win_rate"] == "abc"  # 数值转浮点失败保留原值

    def test_missing_strategy_names(self, workspace):
        result = parse(StrategyCompare().execute(
            ctx=ToolContext(workspace=workspace), strategy_names="",
        ))
        assert result["status"] == "error"
        assert "strategy_names" in result["error"]

    def test_missing_workspace(self):
        result = parse(StrategyCompare().execute(
            ctx=ToolContext(), strategy_names="s1",
        ))
        assert result["status"] == "error"

    def test_strategy_names_split_whitespace(self, workspace):
        write_results_tsv(workspace, "a", [{"run_name": "r", "sharpe": "1"}])
        write_results_tsv(workspace, "b", [{"run_name": "r", "sharpe": "2"}])
        result = parse(StrategyCompare().execute(
            ctx=ToolContext(workspace=workspace),
            strategy_names=" a , b ",
            metrics="sharpe",
        ))
        assert [r["strategy"] for r in result["comparison"]] == ["a", "b"]


# ── DrawdownAnalysis ─────────────────────────────────────────────────


class TestDrawdownAnalysis:
    def test_known_equity_curve_exact_drawdowns(self, workspace):
        write_run(workspace, "mom_20d", "run_0001", EQUITY)
        result = parse(DrawdownAnalysis().execute(
            ctx=ToolContext(workspace=workspace),
            strategy_name="mom_20d",
        ))
        assert result["status"] == "ok"
        assert result["run"] == "run_0001"
        assert result["equity_length"] == len(EQUITY)
        assert result["max_drawdown"] == -0.25
        assert result["current_drawdown"] == 0.0
        assert result["n_drawdown_periods"] == 2
        top = result["top_drawdowns"]
        assert len(top) == 2
        # 主回撤: 峰值 1.2 → 谷底 0.9 (idx 3→4), 恢复 idx 6
        assert top[0]["start_idx"] == 3
        assert top[0]["trough_idx"] == 4
        assert top[0]["recovery_idx"] == 6
        assert top[0]["depth"] == -0.25
        assert top[0]["duration"] == 3
        # 浅回撤: idx 8
        assert top[1]["depth"] == -0.0385

    def test_top_n_limits_periods(self, workspace):
        write_run(workspace, "s1", "run_0001", EQUITY)
        result = parse(DrawdownAnalysis().execute(
            ctx=ToolContext(workspace=workspace),
            strategy_name="s1", top_n=1,
        ))
        assert len(result["top_drawdowns"]) == 1
        assert result["top_drawdowns"][0]["depth"] == -0.25

    def test_ongoing_drawdown_no_recovery(self, workspace):
        equity = [1.0, 1.1, 1.2, 0.9, 0.8] * 3  # 末尾仍在回撤
        write_run(workspace, "s1", "run_0001", equity)
        result = parse(DrawdownAnalysis().execute(
            ctx=ToolContext(workspace=workspace),
            strategy_name="s1",
        ))
        ongoing = [p for p in result["top_drawdowns"] if p["recovery_idx"] is None]
        assert ongoing, "应存在未恢复的回撤区间"
        assert ongoing[0]["note"] == "still in drawdown"
        assert result["current_drawdown"] < 0

    def test_nav_column_supported(self, workspace):
        run_dir = write_run(workspace, "s1", "run_0001")
        lines = ["date,nav"]
        lines += [f"{DATES[i]},{v}" for i, v in enumerate(EQUITY)]
        (run_dir / "nav.csv").write_text("\n".join(lines) + "\n")
        result = parse(DrawdownAnalysis().execute(
            ctx=ToolContext(workspace=workspace),
            strategy_name="s1",
        ))
        assert result["status"] == "ok"
        assert result["max_drawdown"] == -0.25

    def test_run_log_equity_regex(self, workspace):
        run_dir = workspace / "strategies" / "s1" / "runs" / "run_0001"
        run_dir.mkdir(parents=True)
        (run_dir / "run.log").write_text(
            "\n".join(f"equity={v}" for v in EQUITY) + "\n"
        )
        result = parse(DrawdownAnalysis().execute(
            ctx=ToolContext(workspace=workspace),
            strategy_name="s1",
        ))
        assert result["status"] == "ok"
        assert result["equity_length"] == len(EQUITY)

    def test_no_runs_dir(self, workspace):
        result = parse(DrawdownAnalysis().execute(
            ctx=ToolContext(workspace=workspace),
            strategy_name="ghost",
        ))
        assert result["status"] == "error"

    def test_empty_runs_dir(self, workspace):
        (workspace / "strategies" / "s1" / "runs").mkdir(parents=True)
        result = parse(DrawdownAnalysis().execute(
            ctx=ToolContext(workspace=workspace),
            strategy_name="s1",
        ))
        assert result["status"] == "error"
        assert "no runs found" in result["error"]

    def test_no_equity_curve(self, workspace):
        run_dir = workspace / "strategies" / "s1" / "runs" / "run_0001"
        run_dir.mkdir(parents=True)
        (run_dir / "metrics.json").write_text("{}")
        result = parse(DrawdownAnalysis().execute(
            ctx=ToolContext(workspace=workspace),
            strategy_name="s1",
        ))
        assert result["status"] == "error"

    def test_too_few_equity_points(self, workspace):
        write_run(workspace, "s1", "run_0001", [1.0, 1.1, 1.0, 1.2, 1.1])
        result = parse(DrawdownAnalysis().execute(
            ctx=ToolContext(workspace=workspace),
            strategy_name="s1",
        ))
        assert result["status"] == "error"

    def test_latest_run_selected(self, workspace):
        write_run(workspace, "s1", "run_0001", [1.0] * 12)
        write_run(workspace, "s1", "run_0002", EQUITY)
        result = parse(DrawdownAnalysis().execute(
            ctx=ToolContext(workspace=workspace),
            strategy_name="s1",
        ))
        assert result["run"] == "run_0002"

    def test_missing_strategy_name(self, workspace):
        result = parse(DrawdownAnalysis().execute(
            ctx=ToolContext(workspace=workspace), strategy_name="",
        ))
        assert result["status"] == "error"


# ── BenchmarkComparison ──────────────────────────────────────────────


def seed_benchmark(ws: Path, code: str, closes: list[float]) -> None:
    from strategy_research.apps.db import get_connection, init_db

    init_db(ws)
    con = get_connection(ws)
    from datetime import date, timedelta

    rows = []
    d = date(2024, 1, 1)
    for c in closes:
        rows.append(("default", code, d, c, c, c, c, 100.0))
        d += timedelta(days=1)
    con.executemany("INSERT INTO price_data VALUES (?,?,?,?,?,?,?,?)", rows)
    con.close()


class TestBenchmarkComparison:
    def test_alpha_beta_ir_metrics(self, workspace):
        # 策略: 1→2 线性 (12 点); 基准: 恒值 1.0 → var=0 → beta/alpha null
        write_run(workspace, "mom_20d", "run_0001", EQUITY)
        seed_benchmark(workspace, "000300.SH", [1.0] * 12)
        result = parse(BenchmarkComparison().execute(
            ctx=ToolContext(workspace=workspace),
            strategy_name="mom_20d",
            benchmark_code="000300.SH",
        ))
        assert result["status"] == "ok"
        assert result["beta"] is None
        assert result["alpha_annualized"] is None
        assert result["n_periods"] == 12
        assert result["strategy_annual_return"] > 0
        assert result["benchmark_annual_return"] == 0.0
        assert result["tracking_error"] > 0
        assert result["information_ratio"] is not None
        assert result["max_relative_drawdown"] < 0

    def test_flat_benchmark_flat_strategy_zero_tracking(self, workspace):
        flat = [1.0] * 12
        write_run(workspace, "s1", "run_0001", flat)
        seed_benchmark(workspace, "000300.SH", flat)
        result = parse(BenchmarkComparison().execute(
            ctx=ToolContext(workspace=workspace),
            strategy_name="s1",
            benchmark_code="000300.SH",
        ))
        assert result["status"] == "ok"
        assert result["tracking_error"] == 0.0
        assert result["information_ratio"] is None
        assert result["beta"] is None  # var(bench)=0 → beta 未定义

    def test_benchmark_not_in_db(self, workspace):
        write_run(workspace, "s1", "run_0001", EQUITY)
        result = parse(BenchmarkComparison().execute(
            ctx=ToolContext(workspace=workspace),
            strategy_name="s1",
            benchmark_code="999999.SH",
        ))
        assert result["status"] == "error"
        assert "999999.SH" in result["error"]

    def test_missing_benchmark_code(self, workspace):
        write_run(workspace, "s1", "run_0001", EQUITY)
        result = parse(BenchmarkComparison().execute(
            ctx=ToolContext(workspace=workspace),
            strategy_name="s1", benchmark_code="",
        ))
        assert result["status"] == "error"
        assert "benchmark_code" in result["error"]

    def test_missing_strategy_name(self, workspace):
        result = parse(BenchmarkComparison().execute(
            ctx=ToolContext(workspace=workspace),
            strategy_name="", benchmark_code="000300.SH",
        ))
        assert result["status"] == "error"

    def test_no_equity_curve(self, workspace):
        run_dir = workspace / "strategies" / "s1" / "runs" / "run_0001"
        run_dir.mkdir(parents=True)
        seed_benchmark(workspace, "000300.SH", [1.0] * 12)
        result = parse(BenchmarkComparison().execute(
            ctx=ToolContext(workspace=workspace),
            strategy_name="s1", benchmark_code="000300.SH",
        ))
        assert result["status"] == "error"

    def test_flat_strategy_zero_beta(self, workspace):
        write_run(workspace, "s1", "run_0001", [1.0] * 12)
        seed_benchmark(workspace, "000300.SH", [float(i) for i in range(1, 13)])
        result = parse(BenchmarkComparison().execute(
            ctx=ToolContext(workspace=workspace),
            strategy_name="s1", benchmark_code="000300.SH",
        ))
        assert result["status"] == "ok"
        assert result["beta"] == 0.0
        assert result["alpha_annualized"] is not None

    def test_beta_with_correlated_series(self, workspace):
        # 策略价格 = 基准 × 2 → 收益序列相同 → beta ≈ 1.0 (归一化后)
        bench = [100.0 + i for i in range(20)]
        strat = [2 * b for b in bench]
        write_run(workspace, "s1", "run_0001", strat)
        seed_benchmark(workspace, "000300.SH", bench)
        result = parse(BenchmarkComparison().execute(
            ctx=ToolContext(workspace=workspace),
            strategy_name="s1", benchmark_code="000300.SH",
        ))
        assert result["status"] == "ok"
        assert abs(result["beta"] - 1.0) < 0.01
        assert result["alpha_annualized"] == 0.0  # 同涨跌 → 零超额
