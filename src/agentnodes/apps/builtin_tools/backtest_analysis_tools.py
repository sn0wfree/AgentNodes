"""回测分析工具: strategy_compare / drawdown_analysis / benchmark_comparison。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

from agentnodes.kernel.agent.tools import (
    BaseTool,
    ToolContext,
)
from agentnodes.kernel.agent.builtin_tools.utils import err_actionable, tool_ok

logger = logging.getLogger(__name__)




def _load_latest_run(ctx: ToolContext, tool: str, strategy_name: str):
    """Locate the latest run dir for a strategy.

    Returns ``latest_run`` (Path) on success, or an ``err_json`` string on failure.
    """
    if ctx.workspace is None:
        return err_actionable(
            "missing workspace context", fix="AgentLoop 注入 workspace; 直接调用时传 ctx", tool=tool
        )
    workspace = ctx.workspace
    if not strategy_name:
        return err_actionable("missing 'strategy_name'", tool=tool)
    runs_dir = ctx.runs_dir if ctx.runs_dir is not None else workspace / "strategies" / strategy_name / "runs"
    if not runs_dir.exists():
        return err_actionable(f"runs directory not found: {runs_dir}", tool=tool)
    run_dirs = sorted([d for d in runs_dir.iterdir() if d.is_dir()])
    if not run_dirs:
        return err_actionable("no runs found", tool=tool)
    return run_dirs[-1]


def _read_equity_curve(run_dir: Path, use_log_fallback: bool = False):
    """Read the equity series from a run dir's common csv formats.

    Optionally falls back to parsing ``equity=`` values from run.log.
    Returns a float ndarray, or ``None`` if no usable series was found.
    """
    import numpy as np
    import pandas as pd

    equity = None
    for fname in ["equity.csv", "equity_curve.csv", "portfolio.csv", "nav.csv"]:
        fpath = run_dir / fname
        if fpath.exists():
            try:
                eq_df = pd.read_csv(fpath)
                for col in ["equity", "nav", "portfolio_value", "value", "close"]:
                    if col in eq_df.columns:
                        equity = eq_df[col].values
                        break
                if equity is not None:
                    break
            except Exception:
                continue

    if equity is None and use_log_fallback:
        log_path = run_dir / "run.log"
        if log_path.exists():
            try:
                log_text = log_path.read_text(encoding="utf-8")
                import re

                eq_matches = re.findall(r"equity[=:]\s*([\d.]+)", log_text)
                if eq_matches:
                    equity = np.array([float(v) for v in eq_matches])
            except Exception:
                pass

    if equity is None:
        return None
    return np.array(equity, dtype=float)


def _load_benchmark_ohlcv(
    ctx: ToolContext, tool: str, benchmark_code: str, start_date: str | None = None, end_date: str | None = None
):
    """Load the benchmark asset's close series from the workspace DuckDB.

    Returns ``(ok, bench_df, err_json)``; on failure ``bench_df`` is ``None``.
    """
    if ctx.workspace is None:
        return False, None, err_actionable(
            "missing workspace context", fix="AgentLoop 注入 workspace; 直接调用时传 ctx", tool=tool
        )
    try:
        from agentnodes.kernel.agent.capability import resolve

        get_connection = resolve("db.get_connection")

        conn = get_connection(ctx.workspace)
    except Exception as exc:
        return False, None, err_actionable(f"db open failed: {exc}", tool=tool)
    if conn is None:
        return False, None, err_actionable("workspace has no DuckDB", tool=tool)
    try:
        query = f"SELECT date, close FROM ohlcv WHERE asset = '{benchmark_code}'"
        if start_date:
            query += f" AND date >= '{start_date}'"
        if end_date:
            query += f" AND date <= '{end_date}'"
        query += " ORDER BY date"
        bench_df = conn.execute(query).fetch_df()
    except Exception as exc:
        return False, None, err_actionable(f"benchmark query failed: {exc}", tool=tool)
    if bench_df.empty:
        return False, None, err_actionable(f"no data found for benchmark '{benchmark_code}'", tool=tool)
    return True, bench_df, None


def _load_latest_run_equity(
    ctx: ToolContext, tool: str, strategy_name: str, min_points: int = 10, use_log_fallback: bool = True
):
    """Locate the latest run and read its equity series.

    Returns ``(latest_run, equity)`` on success, or ``(None, err_json)`` on failure.
    """
    latest_run = _load_latest_run(ctx, tool, strategy_name)
    if isinstance(latest_run, str):
        return None, latest_run
    equity = _read_equity_curve(latest_run, use_log_fallback=use_log_fallback)
    if equity is None or len(equity) < min_points:
        return None, err_actionable("could not find equity curve data in the latest run", tool=tool)
    return latest_run, equity


# ── 16. StrategyCompare ────────────────────────────────────────────────


class StrategyCompare(BaseTool):
    """多策略指标横向对比。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 补全说明书 (v2 范式 8 节模板)
    #
    # ## 用途
    # 读取多个策略 runs/results.tsv 的最新一行, 按指定指标列横向对比,
    # 用于回测结果选优。缺失结果文件的策略带 error 字段, 不整体失败。
    #
    # ## 参数
    # - strategy_names: 逗号分隔的策略名列表 (必填)
    # - metrics: 逗号分隔的指标列 (默认
    #   sharpe,ann_return,max_dd,calmar,turnover,win_rate)
    #
    # ## 示例
    # {"strategy_names": "mom_20d,mom_60d", "metrics": "sharpe,ann_return,max_dd"}
    #
    # ## 边界
    # 只读工具; 需要 workspace; 各策略须已跑过回测 (results.tsv 存在);
    # 指标列不存在时该列为 null; 数值转浮点失败时保留原值。
    #
    # ## 错误处理范式
    # - strategy_names 缺失 → error
    # - 单策略 results.tsv 缺失/读取失败/无记录 → 该策略行带 error
    #   (非整体失败)
    # - 幂等: 只读不写
    #
    # ## 相关工具
    # 前置: run_backtest; 后续: drawdown_analysis / benchmark_comparison
    # ─────────────────────────────────────────────────────────────
    """

    name = "strategy_compare"
    description = "对比多个策略的回测指标 (读各策略 runs/results.tsv), 指标列可指定。"
    repeatable = True
    category = "回测"

    def execute(
        self,
        ctx: ToolContext,
        strategy_names: str,
        metrics: str = "sharpe,ann_return,max_dd,calmar,turnover,win_rate",
    ) -> str:
        if ctx.workspace is None:
            return err_actionable("missing workspace context", fix="AgentLoop 注入 workspace; 直接调用时传 ctx", tool="strategy_compare")
        workspace = ctx.workspace
        strategy_names_str = strategy_names
        if not strategy_names_str:
            return err_actionable("missing 'strategy_names'", tool="strategy_compare")
        strategy_names_list = [s.strip() for s in strategy_names_str.split(",")]
        metrics_str = metrics
        metrics_keys = [m.strip() for m in metrics_str.split(",")]

        results = []
        for name in strategy_names_list:
            results_path = workspace / "strategies" / name / "runs" / "results.tsv"
            if not results_path.exists():
                results.append({"strategy": name, "error": f"results.tsv not found at {results_path}"})
                continue

            try:
                import csv
                with open(results_path, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f, delimiter="\t")
                    rows = list(reader)
            except Exception as exc:
                results.append({"strategy": name, "error": f"read failed: {exc}"})
                continue

            if not rows:
                results.append({"strategy": name, "error": "no runs found"})
                continue

            latest = rows[-1]
            row = {"strategy": name}
            for key in metrics_keys:
                val = latest.get(key)
                if val is not None:
                    try:
                        row[key] = round(float(val), 4)
                    except (ValueError, TypeError):
                        row[key] = val
                else:
                    row[key] = None
            row["run_name"] = latest.get("run_name", "")
            results.append(row)

        return tool_ok({
            "strategies": strategy_names,
            "metrics": metrics_keys,
            "comparison": results,
        })


# ── 17. DrawdownAnalysis ──────────────────────────────────────────────


class DrawdownAnalysis(BaseTool):
    """策略回撤深度分析（最大回撤/回撤期列表）。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 补全说明书 (v2 范式 8 节模板)
    #
    # ## 用途
    # 从最新 run 的权益曲线计算回撤序列: 最大回撤、当前回撤、回撤期
    # 数量与按深度排序的 Top N 回撤区间 (含开始/谷底/恢复索引与时长)。
    # 依据回撤深度与恢复时长判断风控参数是否需要调整。
    #
    # ## 参数
    # - strategy_name: 策略名 (必填)
    # - top_n: 返回的回撤区间数量 (默认 5)
    #
    # ## 示例
    # {"strategy_name": "mom_20d", "top_n": 10}
    #
    # ## 边界
    # 只读工具; 需要 workspace; 最新 run 须含权益曲线
    # (equity.csv/equity_curve.csv/portfolio.csv/nav.csv 之一, 或
    # run.log 含 equity= 数值); 权益点 < 10 报错; 仍在回撤中的区间
    # recovery_idx 为 null。
    #
    # ## 错误处理范式
    # - runs 目录不存在/无 run → error
    # - 找不到权益曲线或点 < 10 → error, 检查 run 输出
    # - 幂等: 只读不写
    #
    # ## 相关工具
    # 前置: run_backtest; 后续: benchmark_comparison / strategy_compare
    # ─────────────────────────────────────────────────────────────
    """

    name = "drawdown_analysis"
    description = "分析策略回撤期: 从最近 run 的权益曲线计算最大回撤与 Top N 回撤区间。"
    repeatable = True
    category = "回测"

    def execute(
        self,
        ctx: ToolContext,
        strategy_name: str,
        top_n: int = 5,
    ) -> str:
        import numpy as np

        top_n = int(top_n)
        latest_run, equity = _load_latest_run_equity(ctx, "drawdown_analysis", strategy_name)
        if latest_run is None:
            return cast(str, equity)

        # Compute drawdown series
        peak = np.maximum.accumulate(equity)
        drawdown = (equity - peak) / peak

        # Find drawdown periods
        in_dd = drawdown < 0
        periods = []
        start = None
        for i in range(len(in_dd)):
            if in_dd[i] and start is None:
                start = i
            elif not in_dd[i] and start is not None:
                # Drawdown ended at i-1, recovered at i
                depth = float(np.min(drawdown[start:i]))
                trough_idx = start + int(np.argmin(drawdown[start:i]))
                periods.append({
                    "start_idx": int(start),
                    "trough_idx": int(trough_idx),
                    "recovery_idx": int(i),
                    "depth": round(depth, 4),
                    "duration": int(i - start),
                    "recovery_duration": int(i - trough_idx),
                })
                start = None

        # If still in drawdown at end
        if start is not None:
            depth = float(np.min(drawdown[start:]))
            trough_idx = start + int(np.argmin(drawdown[start:]))
            periods.append({
                "start_idx": int(start),
                "trough_idx": int(trough_idx),
                "recovery_idx": None,
                "depth": round(depth, 4),
                "duration": int(len(equity) - start),
                "recovery_duration": None,
                "note": "still in drawdown",
            })

        # Sort by depth and take top N
        periods.sort(key=lambda p: p["depth"])
        top_periods = periods[:top_n]

        max_dd = round(float(np.min(drawdown)), 4)
        current_dd = round(float(drawdown[-1]), 4)

        return tool_ok({
            "strategy": strategy_name,
            "run": latest_run.name,
            "equity_length": len(equity),
            "max_drawdown": max_dd,
            "current_drawdown": current_dd,
            "n_drawdown_periods": len(periods),
            "top_drawdowns": top_periods,
        })


# ── 18. BenchmarkComparison ────────────────────────────────────────────


class BenchmarkComparison(BaseTool):
    """策略 vs 基准表现对比（alpha/beta/IR）。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 补全说明书 (v2 范式 8 节模板)
    #
    # ## 用途
    # 对比策略最新 run 的权益曲线与基准 (DuckDB ohlcv 中的指数/标的)
    # 的日收益: 年化 alpha、beta、跟踪误差、信息比率、最大相对回撤与
    # 双方年化收益。用于判断策略是否相对基准有超额。
    #
    # ## 参数
    # - strategy_name: 策略名 (必填)
    # - benchmark_code: 基准代码 (必填, 如 000300.SH, 须已在 ohlcv)
    # - start_date/end_date: 基准数据时间窗 (可选, ISO 日期)
    #
    # ## 示例
    # {"strategy_name": "mom_20d", "benchmark_code": "000300.SH"}
    #
    # ## 边界
    # 只读工具; 需要 workspace; 策略须有最新权益曲线 (≥10 点);
    # 基准代码须已入库; 两者按尾部对齐取较短长度; 基准查询用字符串
    # 拼接 asset 值 — 仅传已知代码。
    #
    # ## 错误处理范式
    # - 策略/基准缺参 → error + expected
    # - 基准未入库/无数据 → error, 先 get_market_data(benchmark_code)
    # - 权益曲线缺失 → error
    # - beta 分母为零时 beta/alpha 为 null (非失败)
    # - 幂等: 只读不写
    #
    # ## 相关工具
    # 前置: run_backtest + get_market_data; 同类: drawdown_analysis
    # ─────────────────────────────────────────────────────────────
    """

    name = "benchmark_comparison"
    description = "对比策略与基准: alpha/beta/tracking error/information ratio/相对回撤。"
    repeatable = True
    category = "回测"

    def execute(
        self,
        ctx: ToolContext,
        strategy_name: str,
        benchmark_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> str:
        import numpy as np

        if not benchmark_code:
            return err_actionable("missing 'benchmark_code'", tool="benchmark_comparison")

        latest_run, equity = _load_latest_run_equity(
            ctx, "benchmark_comparison", strategy_name, use_log_fallback=False
        )
        if latest_run is None:
            return cast(str, equity)
        strategy_equity = np.asarray(equity, dtype=float)

        # Get benchmark prices from DuckDB
        ok, bench_df, err = _load_benchmark_ohlcv(ctx, "benchmark_comparison", benchmark_code, start_date, end_date)
        if not ok or bench_df is None:
            return err or err_actionable("benchmark load failed", tool="benchmark_comparison")
        bench_equity = bench_df["close"].values.astype(float)

        # Align lengths
        min_len = min(len(strategy_equity), len(bench_equity))
        strat_ret = np.diff(strategy_equity[-min_len:]) / strategy_equity[-min_len:-1]
        bench_ret = np.diff(bench_equity[-min_len:]) / bench_equity[-min_len:-1]

        # Compute metrics
        excess_ret = strat_ret - bench_ret
        bench_var = float(np.var(bench_ret))
        beta = (float(np.cov(strat_ret, bench_ret, ddof=0)[0, 1] / bench_var)
                if bench_var > 0 else None)
        alpha_ann = float((np.mean(strat_ret) - beta * np.mean(bench_ret)) * 252) if beta is not None else None
        tracking_error = float(np.std(excess_ret) * np.sqrt(252))
        info_ratio = float(np.mean(excess_ret) * 252 / tracking_error) if tracking_error > 0 else None

        # Relative drawdown
        cum_excess = np.cumprod(1 + excess_ret)
        rel_peak = np.maximum.accumulate(cum_excess)
        rel_dd = (cum_excess - rel_peak) / rel_peak
        max_rel_dd = float(np.min(rel_dd))

        return tool_ok({
            "strategy": strategy_name,
            "benchmark": benchmark_code,
            "n_periods": min_len,
            "alpha_annualized": round(alpha_ann, 4) if alpha_ann is not None else None,
            "beta": round(beta, 4) if beta is not None else None,
            "tracking_error": round(tracking_error, 4),
            "information_ratio": round(info_ratio, 4) if info_ratio is not None else None,
            "max_relative_drawdown": round(max_rel_dd, 4),
            "strategy_annual_return": round(float(np.mean(strat_ret) * 252), 4),
            "benchmark_annual_return": round(float(np.mean(bench_ret) * 252), 4),
            "run": latest_run.name,
        })
