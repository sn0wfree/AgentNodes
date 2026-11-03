"""因子工具: compute_factor / factor_analysis / pattern_recognition 与因子研究套件。"""

from __future__ import annotations

import logging
from typing import cast

from ...compute_factor import FactorComputeError, compute_factor
from ..tools import (
    BaseTool,
    ToolContext,
)
from .utils import err_actionable, tool_ok

logger = logging.getLogger(__name__)




def _build_factor_panel(
    df, assets: list, factor_code: str, min_bars: int = 20
) -> dict:
    """Compute the factor per asset into a date-indexed panel dict.

    Assets with insufficient bars or failed factor computation are
    skipped. Duplicated index rows are deduplicated (keep first).
    """
    from strategy_research.apps.tools.data_transforms import long_to_single_asset_wide

    factor_panel = {}
    for asset_code in assets:
        adf = long_to_single_asset_wide(df, asset=asset_code, value_cols="ohlcv")
        if len(adf) < min_bars:
            continue
        try:
            fv = compute_factor(factor_code, adf)
            if hasattr(fv, 'index') and fv.index.duplicated().any():
                fv = fv[~fv.index.duplicated(keep='first')]
            factor_panel[asset_code] = fv
        except Exception:
            continue
    return factor_panel


def _build_ret_panel(df, assets, forward_days: int) -> dict:
    """Build the forward-return panel (close pct_change shifted -forward_days)."""
    from strategy_research.apps.tools.data_transforms import long_to_single_asset_wide

    ret_panel = {}
    for asset_code in assets:
        adf = long_to_single_asset_wide(df, asset=asset_code, value_cols="close")
        ret_panel[asset_code] = adf["close"].pct_change(forward_days).shift(-forward_days)
    return ret_panel


def _compute_spearman_ic(f_df, r_df, common_dates) -> list:
    """Compute daily cross-sectional Spearman IC values (list of floats)."""
    import pandas as pd

    ic_list = []
    for dt in common_dates:
        fv = f_df.loc[dt].dropna()
        rv = r_df.loc[dt].dropna()
        common = fv.index.intersection(rv.index)
        if len(common) < 3:
            continue
        ic = fv[common].corr(rv[common], method="spearman")
        if pd.notna(ic):
            ic_list.append(ic)
    return ic_list


def _compute_quintile_returns(factor_df, ret_df, common_dates, n_groups: int) -> dict:
    """Assign quintile groups per date and compute each group's return series."""
    import pandas as pd

    group_returns = {g: [] for g in range(n_groups)}
    for dt in common_dates:
        fv = factor_df.loc[dt].dropna()
        rv = ret_df.loc[dt].dropna()
        common = fv.index.intersection(rv.index)
        if len(common) < n_groups * 2:
            continue
        fv_sorted = fv[common].sort_values()
        n_per = len(fv_sorted) // n_groups
        for g in range(n_groups):
            start_idx = g * n_per
            end_idx = start_idx + n_per if g < n_groups - 1 else len(fv_sorted)
            group_assets = fv_sorted.index[start_idx:end_idx]
            g_ret = rv[group_assets].mean()
            if pd.notna(g_ret):
                group_returns[g].append(float(g_ret))
    return group_returns


def _compute_daily_ic_series(factor_df, ret_df, common_dates):
    """Compute daily cross-sectional Pearson/Spearman IC.

    Returns ``(pearson_list, spearman_list, valid_dates)``.
    """
    import pandas as pd

    ic_pearson_list = []
    ic_spearman_list = []
    valid_dates = []
    for dt in common_dates:
        fv = factor_df.loc[dt].dropna()
        rv = ret_df.loc[dt].dropna()
        common = fv.index.intersection(rv.index)
        if len(common) < 3:
            continue
        f_vals = fv[common]
        r_vals = rv[common]
        pearson_ic = f_vals.corr(r_vals)
        spearman_ic = f_vals.corr(r_vals, method="spearman")
        if pd.notna(pearson_ic):
            ic_pearson_list.append(pearson_ic)
            ic_spearman_list.append(spearman_ic)
            valid_dates.append(dt)
    return ic_pearson_list, ic_spearman_list, valid_dates


def _load_ohlcv(ctx: ToolContext, tool: str, start_date: str | None = None, end_date: str | None = None):
    """Open the workspace DuckDB and load ohlcv rows in the optional date window.

    Returns ``(ok, conn, prices_df, err_json)``; on failure ``conn``/``prices_df`` are ``None``.
    """
    if ctx.workspace is None:
        return False, None, None, err_actionable(
            "missing workspace context", fix="AgentLoop 注入 workspace; 直接调用时传 ctx", tool=tool
        )
    try:
        from ...db import get_connection

        conn = get_connection(ctx.workspace)
    except Exception as exc:
        return False, None, None, err_actionable(f"db open failed: {exc}", tool=tool)
    if conn is None:
        return False, None, None, err_actionable("workspace has no DuckDB", tool=tool)
    try:
        query = "SELECT date, asset, open, high, low, close, volume FROM ohlcv"
        clauses = []
        if start_date:
            clauses.append(f"date >= '{start_date}'")
        if end_date:
            clauses.append(f"date <= '{end_date}'")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY date, asset"
        prices_df = conn.execute(query).fetch_df()
    except Exception as exc:
        return False, None, None, err_actionable(f"ohlcv query failed: {exc}", tool=tool)
    if prices_df.empty:
        return False, None, None, err_actionable("ohlcv table is empty", tool=tool)
    return True, conn, prices_df, None


def _resolve_asset_universe(prices_df, universe: str):
    """Resolve the asset list from the universe spec ('all' or comma list)."""
    all_assets = sorted(prices_df["asset"].unique())
    if universe != "all":
        return [a.strip() for a in universe.split(",")]
    return all_assets


def _load_factor_panel_data(
    ctx: ToolContext,
    tool: str,
    factor_code: str,
    universe: str = "all",
    start_date: str | None = None,
    end_date: str | None = None,
    min_assets: int = 3,
):
    """Load ohlcv, filter universe and compute the factor panel.

    Returns ``(factor_panel, df)`` on success, or ``(None, err_json)`` on failure.
    """
    if not isinstance(factor_code, str) or not factor_code:
        return None, err_actionable("missing or invalid 'factor_code'", tool=tool)
    ok, _conn, prices_df, err = _load_ohlcv(ctx, tool, start_date, end_date)
    if not ok or prices_df is None:
        return None, err or err_actionable("ohlcv load failed", tool=tool)
    assets = _resolve_asset_universe(prices_df, universe)
    df = prices_df[prices_df["asset"].isin(assets)].copy()
    factor_panel = _build_factor_panel(df, assets, factor_code)
    if len(factor_panel) < min_assets:
        return None, err_actionable(
            f"factor computation succeeded on < {min_assets} assets ({len(factor_panel)})", tool=tool
        )
    return factor_panel, df


# ── 4. ComputeFactorTool ────────────────────────────────────────────


class ComputeFactorTool(BaseTool):
    """在工作区价格数据上计算因子表达式（单资产，返回采样）。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 迁移 v2 (显式签名 + ToolContext)
    #
    # ## 用途
    # 在单资产宽表 (close/open/high/low/volume) 上计算因子表达式
    # (如 'ts_mean(close, 20) / ts_mean(close, 60) - 1'), 返回结果采样。
    #
    # ## 参数
    # - factor_code: 因子表达式 (必填)
    # - asset: 资产代码 (默认第一个可用资产)
    # - factor_name: 因子名 (可选, 用于展示)
    # - n_samples: 采样数 (默认 5)
    #
    # ## 示例
    # {"factor_code": "ts_return(close, 20)"}
    #
    # ## 边界
    # 只读工具; 读取 workspace DuckDB 的 ohlcv 视图 (price_data);
    # 数据为空会给 workflow 提示。
    #
    # ## 错误处理范式
    # - 缺 factor_code → error + expected 示例
    # - 无 DB/空表 → error + fix: get_market_data → compute_factor
    # - asset 不存在 → error + expected 可用资产列表
    # - 表达式错误 → error + available_columns 与示例表达式
    # - 均可安全重试
    #
    # ## 相关工具
    # get_market_data: 数据前置; factor_analysis/factor_quintile_returns 等: 后续分析
    # ─────────────────────────────────────────────
    """

    name = "compute_factor"
    description = (
        "在单资产价格数据上计算因子表达式 (如 'ts_mean(close, 20) / ts_mean(close, 60) - 1'), "
        "返回结果采样; 数据来自 workspace DuckDB。"
    )
    repeatable = True
    category = "因子"

    def execute(
        self,
        ctx: ToolContext,
        factor_code: str,
        asset: str | None = None,
        factor_name: str | None = None,
        n_samples: int = 5,
    ) -> str:
        if ctx.workspace is None:
            return err_actionable(
                "missing workspace context",
                fix="AgentLoop 注入 workspace; 直接调用时传 ctx",
                tool="compute_factor",
            )
        workspace = ctx.workspace

        if not isinstance(factor_code, str) or not factor_code:
            return err_actionable(
                "missing or invalid 'factor_code'",
                received=factor_code,
                expected="non-empty factor expression, e.g. 'ts_mean(close, 20) / ts_mean(close, 60) - 1'",
                fix="pass a valid expression; see templates/.skills/factor-research.md for operators",
                tool="compute_factor",
            )
        factor_name = factor_name or ""

        # Load price data from workspace DuckDB
        try:
            from ...db import get_connection
            conn = get_connection(workspace)
        except Exception as exc:                    # noqa: BLE001
            return err_actionable(
                f"db open failed: {exc}",
                fix="ensure workspace has data.duckdb; run quantnodes-research init or import_data first",
                tool="compute_factor",
            )
        if conn is None:
            return err_actionable(
                "workspace has no DuckDB",
                fix="call import_data first to populate the ohlcv table",
                tool="compute_factor",
            )

        try:
            prices_df = conn.execute(
                "SELECT date, asset, open, high, low, close, volume "
                "FROM ohlcv ORDER BY date, asset"
            ).fetch_df()
        except Exception as exc:                    # noqa: BLE001
            return err_actionable(
                f"ohlcv query failed: {exc}",
                fix="call import_data to create the ohlcv table; see workflow: get_market_data → import_data → compute_factor",
                tool="compute_factor",
            )

        if prices_df.empty:
            return err_actionable(
                "ohlcv table is empty",
                fix=(
                    "1) get_market_data(codes=['600519.SH'], "
                    "start_date='2023-01-01', end_date='2023-12-31', "
                    "strategy_name='default') fetches and persists OHLCV to "
                    "DuckDB in one step; 2) compute_factor(factor_code=...) again"
                ),
                tool="compute_factor",
            )

        # Pick asset (default: first)
        available_assets = sorted(prices_df["asset"].unique())
        if not available_assets:
            return err_actionable(
                "no assets in ohlcv table",
                fix="import data for at least one asset",
                tool="compute_factor",
            )
        if asset is None:
            asset = available_assets[0]
        elif asset not in available_assets:
            return err_actionable(
                f"asset '{asset}' not found",
                received=asset,
                expected=f"one of {available_assets[:10]}",
                fix="omit `asset` to use the first available, or pass a valid asset code",
                tool="compute_factor",
            )

        # Build single-asset wide DataFrame (date index, ohlcv columns)
        from strategy_research.apps.tools.data_transforms import long_to_single_asset_wide

        asset_df = long_to_single_asset_wide(prices_df, asset=asset, value_cols="ohlcv")

        try:
            series = compute_factor(factor_code, asset_df, factor_name=factor_name)
        except FactorComputeError as exc:
            return err_actionable(
                str(exc),
                received=factor_code,
                fix=(
                    f"Use only available columns: {exc.available_columns}. "
                    f"Sample valid expressions: ts_return(close, 20), ts_std(close, 20), "
                    f"ts_mean(close, 60)"
                ),
                tool="compute_factor",
            )

        # Sample the result
        non_null = series.dropna()
        if len(non_null) == 0:
            return err_actionable(
                "factor produced no non-null values",
                received={"factor_code": factor_code, "asset": asset},
                fix="factor may need more data or different parameters",
                tool="compute_factor",
                extra={"factor_name": factor_name, "asset": asset},
            )
        sample = non_null.head(n_samples).to_dict()
        sample = {str(k): (None if v != v else float(v)) for k, v in sample.items()}

        return tool_ok({
            "factor_name": factor_name or "(unnamed)",
            "factor_code": factor_code,
            "asset": asset,
            "n_total": int(len(series)),
            "n_non_null": int(len(non_null)),
            "sample": sample,
            "first_date": str(series.index.min()) if len(series) else None,
            "last_date": str(series.index.max()) if len(series) else None,
        })


# ── 7. FactorAnalysisTool ──────────────────────────────────────────


class FactorAnalysisTool(BaseTool):
    """分析因子 IC/IR 统计（单资产）。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 迁移 v2 (显式签名 + ToolContext)
    #
    # ## 用途
    # 对因子表达式做 IC/IR 分析: 计算 IC mean、spearman IC、观测数。
    # 需要 workspace DuckDB 有价格数据。
    #
    # ## 参数
    # - factor_code: 因子表达式 (必填)
    # - asset: 资产代码 (默认第一个可用)
    # - forward_days: 前向收益天数 (默认 5)
    #
    # ## 示例
    # {"factor_code": "ts_return(close, 20)"}
    #
    # ## 边界
    # 只读工具; 观测数 < 10 时返回 insufficient data 错误。
    #
    # ## 错误处理范式
    # - 无 DB/空表 → error + workflow 提示
    # - asset 不存在 → error + expected 可用资产
    # - 数据不足 → error + 需要 >= 10 行
    # - 均可安全重试
    #
    # ## 相关工具
    # compute_factor: 单因子计算; factor_quintile_returns 等: 深入分析
    # ─────────────────────────────────────────────
    """

    name = "factor_analysis"
    description = (
        "对因子表达式做 IC/IR 分析 (IC mean / spearman IC / 观测数)。"
    )
    repeatable = True
    category = "因子"

    def execute(
        self,
        ctx: ToolContext,
        factor_code: str,
        asset: str | None = None,
        forward_days: int = 5,
    ) -> str:
        if ctx.workspace is None:
            return err_actionable(
                "missing workspace context",
                fix="AgentLoop 注入 workspace; 直接调用时传 ctx",
                tool="factor_analysis",
            )
        workspace = ctx.workspace

        if not isinstance(factor_code, str) or not factor_code:
            return err_actionable("missing or invalid 'factor_code'", tool="factor_analysis")
        forward_days = int(forward_days)

        try:
            from ...db import get_connection
            conn = get_connection(workspace)
        except Exception as exc:  # noqa: BLE001
            return err_actionable(f"db open failed: {exc}", tool="factor_analysis")

        if conn is None:
            return err_actionable("workspace has no DuckDB", tool="factor_analysis")

        try:
            prices_df = conn.execute(
                "SELECT date, asset, close FROM ohlcv ORDER BY date, asset"
            ).fetch_df()
        except Exception as exc:  # noqa: BLE001
            return err_actionable(f"ohlcv query failed: {exc}", tool="factor_analysis")

        if prices_df.empty:
            return err_actionable("ohlcv table is empty", tool="factor_analysis")

        available_assets = sorted(prices_df["asset"].unique())
        if asset is None:
            asset = available_assets[0]
        elif asset not in available_assets:
            return err_actionable(f"asset '{asset}' not found", tool="factor_analysis")

        asset_df = prices_df[prices_df["asset"] == asset].copy()
        asset_df = asset_df.drop_duplicates(subset=["date"], keep="last")
        asset_df = asset_df.set_index("date")[["close"]]
        asset_df = asset_df.sort_index()

        try:
            factor_series = compute_factor(factor_code, asset_df)
        except FactorComputeError as exc:
            return err_actionable(
                str(exc),
                tool="factor_analysis",
            )
        except Exception as exc:  # noqa: BLE001
            return err_actionable(f"compute failed: {exc}", tool="factor_analysis")

        # Compute forward returns
        asset_df["fwd_ret"] = asset_df["close"].pct_change(forward_days).shift(-forward_days)

        # Align and compute IC
        import pandas as pd
        aligned = pd.concat([factor_series, asset_df["fwd_ret"]], axis=1).dropna()
        if len(aligned) < 10:
            return err_actionable("insufficient data for IC analysis (need >= 10 rows)", tool="factor_analysis")

        ic = aligned.iloc[:, 0].corr(aligned["fwd_ret"])
        ic_mean = float(aligned.iloc[:, 0].corr(aligned["fwd_ret"], method="spearman")) if len(aligned) > 5 else 0.0

        return tool_ok({
            "factor_code": factor_code,
            "asset": asset,
            "forward_days": forward_days,
            "ic_mean": round(ic, 4) if pd.notna(ic) else None,
            "spearman_ic": round(ic_mean, 4),
            "n_observations": len(aligned),
        })


# ── 8. PatternRecognitionTool ──────────────────────────────────────


class PatternRecognitionTool(BaseTool):
    """识别价格形态（头肩/双顶底/趋势线/支撑阻力）。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 补全说明书 (v2 范式 8 节模板)
    #
    # ## 用途
    # 从 DuckDB ohlcv 读取最近 N 根 K 线, 用简化启发式检测价格形态:
    # 均线趋势 (MA5 vs MA20)、近阻力/近支撑 (接近近期高低点 2% 内)、
    # 波动率挤压 (近 5 日标准差 < 近 20 日的 60%)。非严格形态识别,
    # 输出带置信度, 作为研究输入而非交易信号。
    #
    # ## 参数
    # - asset: 限定单个资产代码 (可选; 缺省分析全部资产)
    # - lookback: 分析的 K 线数量 (默认 60)
    #
    # ## 示例
    # {"asset": "600519.SH", "lookback": 120}
    #
    # ## 边界
    # 只读工具; 需要 workspace 含 DuckDB 且 ohlcv 非空; 数据量 < 10 根
    # 报 insufficient data。
    #
    # ## 错误处理范式
    # - 缺 workspace / 库不可用 / ohlcv 为空 → error, 先入库
    # - 数据不足 (< 10 根) → error, 需 get_market_data(persist=True)
    # - 幂等: 只读不写
    #
    # ## 相关工具
    # 前置: get_market_data / import_data; 同类: compute_factor
    # ─────────────────────────────────────────────────────────────
    """

    name = "pattern_recognition"
    description = "识别常见图表形态 (头肩顶底/双顶底/趋势线/支撑阻力); 需要 DuckDB 价格数据。"
    repeatable = True
    category = "分析"

    def execute(
        self,
        ctx: ToolContext,
        asset: str | None = None,
        lookback: int = 60,
    ) -> str:
        if ctx.workspace is None:
            return err_actionable("missing workspace context", fix="AgentLoop 注入 workspace; 直接调用时传 ctx", tool="pattern_recognition")
        workspace = ctx.workspace
        lookback = int(lookback)

        try:
            from ...db import get_connection
            conn = get_connection(workspace)
        except Exception as exc:  # noqa: BLE001
            return err_actionable(f"db open failed: {exc}", tool="pattern_recognition")

        if conn is None:
            return err_actionable("workspace has no DuckDB", tool="pattern_recognition")

        try:
            prices_df = conn.execute(
                "SELECT date, asset, open, high, low, close, volume FROM ohlcv ORDER BY date"
            ).fetch_df()
        except Exception as exc:  # noqa: BLE001
            return err_actionable(f"ohlcv query failed: {exc}", tool="pattern_recognition")

        if prices_df.empty:
            return err_actionable("ohlcv table is empty", tool="pattern_recognition")

        if asset:
            prices_df = prices_df[prices_df["asset"] == asset]

        prices_df = prices_df.tail(lookback)
        if len(prices_df) < 10:
            return err_actionable("insufficient data", tool="pattern_recognition")

        closes = prices_df["close"].values
        highs = prices_df["high"].values
        lows = prices_df["low"].values

        patterns = []

        # Simple trend detection
        if len(closes) >= 20:
            ma20 = closes[-20:].mean()
            ma5 = closes[-5:].mean() if len(closes) >= 5 else ma20
            if ma5 > ma20:
                patterns.append({"pattern": "uptrend", "confidence": 0.6})
            elif ma5 < ma20:
                patterns.append({"pattern": "downtrend", "confidence": 0.6})

        # Support/Resistance
        recent_high = float(highs.max())
        recent_low = float(lows.min())
        current = float(closes[-1])
        range_pct = (recent_high - recent_low) / recent_high * 100 if recent_high > 0 else 0

        if current >= recent_high * 0.98:
            patterns.append({"pattern": "near_resistance", "level": round(recent_high, 2), "confidence": 0.5})
        if current <= recent_low * 1.02:
            patterns.append({"pattern": "near_support", "level": round(recent_low, 2), "confidence": 0.5})

        # Volatility squeeze
        if len(closes) >= 20:
            std20 = float(closes[-20:].std())
            std5 = float(closes[-5:].std()) if len(closes) >= 5 else std20
            if std5 < std20 * 0.6:
                patterns.append({"pattern": "volatility_squeeze", "confidence": 0.5})

        return tool_ok({
            "asset": asset or "(all)",
            "lookback": lookback,
            "current_price": round(current, 2),
            "range_pct": round(range_pct, 2),
            "patterns": patterns,
        })


# ── 12. FactorCrossSectionalAnalysis ──────────────────────────────────


class FactorCrossSectionalAnalysis(BaseTool):
    """截面 IC 分析（全资产池，Pearson/Spearman）。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 补全说明书 (v2 范式 8 节模板)
    #
    # ## 用途
    # 对资产池计算因子表达式的逐日截面 IC (Pearson + Spearman), 汇总
    # IC 均值/标准差/IR/IC>0 比例, 并附前 5 个样本日期。验证因子在
    # 横截面上是否有区分度。单资产验证用 factor_analysis。
    #
    # ## 参数
    # - factor_code: 因子表达式 (必填, 语法见 .skills/factor-research.md)
    # - universe: 逗号分隔代码或 all (默认 all)
    # - start_date/end_date: 数据时间窗 (可选, ISO 日期)
    # - forward_days: 前向收益窗口天数 (默认 5)
    #
    # ## 示例
    # {"factor_code": "ts_mean(close,20)/ts_mean(close,60)-1",
    #  "universe": "600519.SH,000858.SZ,000001.SZ"}
    #
    # ## 边界
    # 只读工具; 需要 DuckDB ohlcv 数据; 需 ≥3 资产且 ≥3 个因子计算
    # 成功; 有效 IC 观测 ≥5; 样本 < 20 根 K 线的资产被跳过。
    #
    # ## 错误处理范式
    # - universe 含不存在代码 → error + 缺失列表
    # - 资产数/因子成功数 < 3 → error, 需先入库更多资产
    # - IC 观测 < 5 → error "too few valid IC observations"
    # - ohlcv 为空/库不可用 → error, 先 get_market_data(persist=True)
    # - 幂等: 只读不写
    #
    # ## 相关工具
    # 前置: get_market_data; 后续: factor_quintile_returns / factor_ic_decay;
    # 同类: factor_analysis (单资产)
    # ─────────────────────────────────────────────────────────────
    """

    name = "factor_cross_sectional_analysis"
    description = "对资产池计算因子表达式的截面 IC (Pearson/Spearman): IC mean/std/IR/IC>0 比例, 含日度 IC 序列。"
    repeatable = True
    category = "因子"

    def execute(
        self,
        ctx: ToolContext,
        factor_code: str,
        universe: str = "all",
        start_date: str | None = None,
        end_date: str | None = None,
        forward_days: int = 5,
    ) -> str:
        import numpy as np

        if ctx.workspace is None:
            return err_actionable("missing workspace context", fix="AgentLoop 注入 workspace; 直接调用时传 ctx", tool="factor_cross_sectional_analysis")
        workspace = ctx.workspace

        if not isinstance(factor_code, str) or not factor_code:
            return err_actionable("missing or invalid 'factor_code'", tool="factor_cross_sectional_analysis")
        universe_str = universe
        forward_days = int(forward_days)

        try:
            from ...db import get_connection
            conn = get_connection(workspace)
        except Exception as exc:
            return err_actionable(f"db open failed: {exc}", tool="factor_cross_sectional_analysis")
        if conn is None:
            return err_actionable("workspace has no DuckDB", tool="factor_cross_sectional_analysis")

        try:
            query = "SELECT date, asset, open, high, low, close, volume FROM ohlcv"
            clauses = []
            if start_date:
                clauses.append(f"date >= '{start_date}'")
            if end_date:
                clauses.append(f"date <= '{end_date}'")
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY date, asset"
            prices_df = conn.execute(query).fetch_df()
        except Exception as exc:
            return err_actionable(f"ohlcv query failed: {exc}", tool="factor_cross_sectional_analysis")

        if prices_df.empty:
            return err_actionable("ohlcv table is empty", tool="factor_cross_sectional_analysis")

        # Filter universe
        all_assets = sorted(prices_df["asset"].unique())
        if universe_str != "all":
            assets = [a.strip() for a in universe_str.split(",")]
            missing = [a for a in assets if a not in all_assets]
            if missing:
                return err_actionable(f"assets not found: {missing[:5]}", tool="factor_cross_sectional_analysis")
        else:
            assets = all_assets

        if len(assets) < 3:
            return err_actionable(f"need >= 3 assets for cross-sectional IC, got {len(assets)}", tool="factor_cross_sectional_analysis")

        df = prices_df[prices_df["asset"].isin(assets)].copy()

        # Compute factor per asset and build date×asset panel
        import pandas as pd

        factor_panel = _build_factor_panel(df, assets, factor_code)

        if len(factor_panel) < 3:
            return err_actionable(f"factor computation succeeded on < 3 assets ({len(factor_panel)})", tool="factor_cross_sectional_analysis")

        # Build forward return panel
        ret_panel = _build_ret_panel(df, factor_panel, forward_days)

        # Compute daily cross-sectional IC
        factor_df = pd.DataFrame(factor_panel)
        ret_df = pd.DataFrame(ret_panel)
        common_dates = factor_df.index.intersection(ret_df.index)
        factor_df = factor_df.loc[common_dates]
        ret_df = ret_df.loc[common_dates]

        ic_pearson_list, ic_spearman_list, valid_dates = _compute_daily_ic_series(
            factor_df, ret_df, common_dates
        )

        if len(ic_pearson_list) < 5:
            return err_actionable(f"too few valid IC observations ({len(ic_pearson_list)})", tool="factor_cross_sectional_analysis")

        ic_arr = np.array(ic_pearson_list)
        spear_arr = np.array(ic_spearman_list)

        return tool_ok({
            "factor_code": factor_code,
            "n_assets": len(factor_panel),
            "n_dates": len(ic_pearson_list),
            "forward_days": forward_days,
            "ic_pearson_mean": round(float(np.mean(ic_arr)), 4),
            "ic_pearson_std": round(float(np.std(ic_arr)), 4),
            "ir": round(float(np.mean(ic_arr) / np.std(ic_arr)), 4) if np.std(ic_arr) > 0 else None,
            "ic_pearson_gt0_ratio": round(float(np.mean(ic_arr > 0)), 4),
            "ic_spearman_mean": round(float(np.mean(spear_arr)), 4),
            "ic_spearman_std": round(float(np.std(spear_arr)), 4),
            "sample_dates": [str(d) for d in valid_dates[:5]],
        })


# ── 13. FactorQuintileReturns ──────────────────────────────────────────


class FactorQuintileReturns(BaseTool):
    """因子分层组合收益分析（quintile 分组）。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 补全说明书 (v2 范式 8 节模板)
    #
    # ## 用途
    # 把资产池按因子值逐日分为 N 组 (默认 5 组), 计算各组的平均前向
    # 收益 (holding_period 天) 与多空价差 (Qn - Q1), 检验因子分组
    # 单调性。
    #
    # ## 参数
    # - factor_code: 因子表达式 (必填)
    # - universe: 逗号分隔代码或 all (默认 all)
    # - start_date/end_date: 数据时间窗 (可选)
    # - n_groups: 分组数 (默认 5)
    # - holding_period: 前向收益持有天数 (默认 5)
    #
    # ## 示例
    # {"factor_code": "ts_rank(close,20)", "n_groups": 5, "holding_period": 5}
    #
    # ## 边界
    # 只读工具; 需要 DuckDB ohlcv; 资产数须 ≥ n_groups*2; 样本 < 20 根
    # 或因子计算失败的资产被跳过; 某日有效资产不足则跳过该日。
    #
    # ## 错误处理范式
    # - 资产不足 n_groups*2 → error + 所需/实有数量
    # - ohlcv 为空 → error, 先入库
    # - 某组无观测 → 该组 mean_return 为 null (非整体失败)
    # - 幂等: 只读不写
    #
    # ## 相关工具
    # 前置: get_market_data; 后续: factor_ic_decay / factor_turnover;
    # 同类: factor_cross_sectional_analysis
    # ─────────────────────────────────────────────────────────────
    """

    name = "factor_quintile_returns"
    description = "把资产池按因子值分 N 组, 计算各组的平均前向收益与多空价差。"
    repeatable = True
    category = "因子"

    def execute(
        self,
        ctx: ToolContext,
        factor_code: str,
        universe: str = "all",
        start_date: str | None = None,
        end_date: str | None = None,
        n_groups: int = 5,
        holding_period: int = 5,
    ) -> str:
        import numpy as np

        if ctx.workspace is None:
            return err_actionable("missing workspace context", fix="AgentLoop 注入 workspace; 直接调用时传 ctx", tool="factor_quintile_returns")
        workspace = ctx.workspace

        if not isinstance(factor_code, str) or not factor_code:
            return err_actionable("missing or invalid 'factor_code'", tool="factor_quintile_returns")
        universe_str = universe
        n_groups = int(n_groups)
        holding_period = int(holding_period)

        try:
            from ...db import get_connection
            conn = get_connection(workspace)
        except Exception as exc:
            return err_actionable(f"db open failed: {exc}", tool="factor_quintile_returns")
        if conn is None:
            return err_actionable("workspace has no DuckDB", tool="factor_quintile_returns")

        try:
            query = "SELECT date, asset, open, high, low, close, volume FROM ohlcv"
            clauses = []
            if start_date:
                clauses.append(f"date >= '{start_date}'")
            if end_date:
                clauses.append(f"date <= '{end_date}'")
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY date, asset"
            prices_df = conn.execute(query).fetch_df()
        except Exception as exc:
            return err_actionable(f"ohlcv query failed: {exc}", tool="factor_quintile_returns")

        if prices_df.empty:
            return err_actionable("ohlcv table is empty", tool="factor_quintile_returns")

        all_assets = sorted(prices_df["asset"].unique())
        if universe_str != "all":
            assets = [a.strip() for a in universe_str.split(",")]
        else:
            assets = all_assets

        if len(assets) < n_groups * 2:
            return err_actionable(f"need >= {n_groups * 2} assets for {n_groups}-group analysis, got {len(assets)}", tool="factor_quintile_returns")

        import pandas as pd
        df = prices_df[prices_df["asset"].isin(assets)].copy()

        # Compute factor per asset
        factor_panel = _build_factor_panel(df, assets, factor_code)

        # Forward return panel
        ret_panel = _build_ret_panel(df, factor_panel, holding_period)

        factor_df = pd.DataFrame(factor_panel)
        ret_df = pd.DataFrame(ret_panel)
        common_dates = factor_df.index.intersection(ret_df.index)
        factor_df = factor_df.loc[common_dates]
        ret_df = ret_df.loc[common_dates]

        # Assign quintile groups per date and compute group returns
        group_returns = _compute_quintile_returns(
            factor_df, ret_df, common_dates, n_groups
        )

        result = {}
        for g in range(n_groups):
            rets = group_returns[g]
            if rets:
                result[f"Q{g+1}_mean_return"] = round(float(np.mean(rets)), 6)
                result[f"Q{g+1}_n_periods"] = len(rets)
            else:
                result[f"Q{g+1}_mean_return"] = None
                result[f"Q{g+1}_n_periods"] = 0

        q1 = result.get("Q1_mean_return")
        qn = result.get(f"Q{n_groups}_mean_return")
        if q1 is not None and qn is not None:
            result["long_short_spread"] = round(qn - q1, 6)

        return tool_ok({
            "factor_code": factor_code,
            "n_groups": n_groups,
            "holding_period": holding_period,
            "n_assets_used": len(factor_panel),
            **result,
        })


# ── 14. FactorICDecay ──────────────────────────────────────────────────


class FactorICDecay(BaseTool):
    """因子 IC 衰减曲线（多前向周期）。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 补全说明书 (v2 范式 8 节模板)
    #
    # ## 用途
    # 计算因子在多个前向收益周期 (默认 1,5,10,20,60 天) 的逐日截面
    # Spearman IC 均值/标准差/IR, 观察预测力随周期的衰减速度,
    # 用于选择因子最佳持有周期。
    #
    # ## 参数
    # - factor_code: 因子表达式 (必填)
    # - universe: 逗号分隔代码或 all (默认 all)
    # - start_date/end_date: 数据时间窗 (可选)
    # - horizons: 逗号分隔的前向周期列表 (默认 1,5,10,20,60)
    #
    # ## 示例
    # {"factor_code": "ts_mean(close,20)/ts_mean(close,60)-1",
    #  "horizons": "5,10,20"}
    #
    # ## 边界
    # 只读工具; 需要 DuckDB ohlcv; 因子计算成功资产须 ≥3; 单日截面
    # 有效资产 < 3 则跳过该日。
    #
    # ## 错误处理范式
    # - 因子成功资产 < 3 → error
    # - 某 horizon 无有效观测 → 该周期 ic_mean 等为 null (非整体失败)
    # - ohlcv 为空 → error, 先入库
    # - 幂等: 只读不写
    #
    # ## 相关工具
    # 前置: get_market_data; 后续: 按最佳 horizon 构建策略;
    # 同类: factor_cross_sectional_analysis / factor_turnover
    # ─────────────────────────────────────────────────────────────
    """

    name = "factor_ic_decay"
    description = "计算因子在多个前向收益周期 (如 1,5,10,20,60 天) 的截面 IC, 衡量预测力衰减速度。"
    repeatable = True
    category = "因子"

    def execute(
        self,
        ctx: ToolContext,
        factor_code: str,
        universe: str = "all",
        start_date: str | None = None,
        end_date: str | None = None,
        horizons: str = "1,5,10,20,60",
    ) -> str:
        import numpy as np

        if ctx.workspace is None:
            return err_actionable("missing workspace context", fix="AgentLoop 注入 workspace; 直接调用时传 ctx", tool="factor_ic_decay")
        workspace = ctx.workspace

        if not isinstance(factor_code, str) or not factor_code:
            return err_actionable("missing or invalid 'factor_code'", tool="factor_ic_decay")
        universe_str = universe
        horizons_str = horizons
        horizons_list = [int(h.strip()) for h in horizons_str.split(",")]

        try:
            from ...db import get_connection
            conn = get_connection(workspace)
        except Exception as exc:
            return err_actionable(f"db open failed: {exc}", tool="factor_ic_decay")
        if conn is None:
            return err_actionable("workspace has no DuckDB", tool="factor_ic_decay")

        try:
            query = "SELECT date, asset, open, high, low, close, volume FROM ohlcv"
            clauses = []
            if start_date:
                clauses.append(f"date >= '{start_date}'")
            if end_date:
                clauses.append(f"date <= '{end_date}'")
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY date, asset"
            prices_df = conn.execute(query).fetch_df()
        except Exception as exc:
            return err_actionable(f"ohlcv query failed: {exc}", tool="factor_ic_decay")

        if prices_df.empty:
            return err_actionable("ohlcv table is empty", tool="factor_ic_decay")

        all_assets = sorted(prices_df["asset"].unique())
        if universe_str != "all":
            assets = [a.strip() for a in universe_str.split(",")]
        else:
            assets = all_assets

        import pandas as pd
        df = prices_df[prices_df["asset"].isin(assets)].copy()

        # Compute factor per asset
        factor_panel = _build_factor_panel(df, assets, factor_code)

        if len(factor_panel) < 3:
            return err_actionable(f"factor computation succeeded on < 3 assets ({len(factor_panel)})", tool="factor_ic_decay")

        factor_df = pd.DataFrame(factor_panel)

        # Compute IC at each horizon
        results = []
        for h in horizons_list:
            ret_panel = _build_ret_panel(df, factor_panel, h)

            ret_df = pd.DataFrame(ret_panel)
            common_dates = factor_df.index.intersection(ret_df.index)
            f_df = factor_df.loc[common_dates]
            r_df = ret_df.loc[common_dates]

            ic_list = _compute_spearman_ic(f_df, r_df, common_dates)

            if ic_list:
                arr = np.array(ic_list)
                results.append({
                    "horizon": h,
                    "ic_mean": round(float(np.mean(arr)), 4),
                    "ic_std": round(float(np.std(arr)), 4),
                    "ir": round(float(np.mean(arr) / np.std(arr)), 4) if np.std(arr) > 0 else None,
                    "n_periods": len(ic_list),
                })
            else:
                results.append({"horizon": h, "ic_mean": None, "ic_std": None, "ir": None, "n_periods": 0})

        return tool_ok({
            "factor_code": factor_code,
            "n_assets": len(factor_panel),
            "ic_decay": results,
        })


# ── 15. FactorTurnover ─────────────────────────────────────────────────


class FactorTurnover(BaseTool):
    """因子排名换手率分析（排名稳定性）。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 补全说明书 (v2 范式 8 节模板)
    #
    # ## 用途
    # 按 rebalance_freq 天间隔采样因子值, 计算相邻采样日资产排名的
    # Spearman 相关, 换手率 = 1 - 秩相关; 输出平均/中位换手与排名
    # 稳定度 (1 - 平均换手)。低换手因子排名稳定, 更适合实盘。
    #
    # ## 参数
    # - factor_code: 因子表达式 (必填)
    # - universe: 逗号分隔代码或 all (默认 all)
    # - start_date/end_date: 数据时间窗 (可选)
    # - rebalance_freq: 采样间隔天数 (默认 5)
    #
    # ## 示例
    # {"factor_code": "ts_mean(close,20)/ts_mean(close,60)-1",
    #  "rebalance_freq": 10}
    #
    # ## 边界
    # 只读工具; 需要 DuckDB ohlcv; 因子成功资产须 ≥3; 采样期 < 2 报错;
    # 相邻采样日公共资产 < 3 的间隔被跳过。
    #
    # ## 错误处理范式
    # - 采样期 < 2 → error "not enough rebalancing periods"
    # - 无有效换手观测 → error "no valid turnover observations"
    # - 因子成功资产 < 3 → error
    # - 幂等: 只读不写
    #
    # ## 相关工具
    # 前置: get_market_data; 同类: factor_ic_decay / factor_quintile_returns
    # ─────────────────────────────────────────────────────────────
    """

    name = "factor_turnover"
    description = "衡量因子排名随时间的变化: 相邻调仓期的平均秩相关; 低换手 = 因子稳定。"
    repeatable = True
    category = "因子"

    def execute(
        self,
        ctx: ToolContext,
        factor_code: str,
        universe: str = "all",
        start_date: str | None = None,
        end_date: str | None = None,
        rebalance_freq: int = 5,
    ) -> str:
        import numpy as np
        import pandas as pd

        rebalance_freq = int(rebalance_freq)
        loaded = _load_factor_panel_data(
            ctx, "factor_turnover", factor_code, universe, start_date, end_date
        )
        if loaded[0] is None:
            return cast(str, loaded[1])
        factor_panel, _df = loaded

        factor_df = pd.DataFrame(factor_panel)

        # Sample dates at rebalance frequency
        dates = sorted(factor_df.index)
        sampled_dates = dates[::rebalance_freq]
        if len(sampled_dates) < 2:
            return err_actionable("not enough rebalancing periods", tool="factor_turnover")

        # Compute rank correlation between consecutive periods
        turnover_list = []
        for i in range(1, len(sampled_dates)):
            prev_ranks = factor_df.loc[sampled_dates[i - 1]].dropna().rank()
            curr_ranks = factor_df.loc[sampled_dates[i]].dropna().rank()
            common = prev_ranks.index.intersection(curr_ranks.index)
            if len(common) < 3:
                continue
            rank_corr = prev_ranks[common].corr(curr_ranks[common], method="spearman")
            if pd.notna(rank_corr):
                turnover_list.append(1.0 - float(rank_corr))

        if not turnover_list:
            return err_actionable("no valid turnover observations", tool="factor_turnover")

        arr = np.array(turnover_list)
        return tool_ok({
            "factor_code": factor_code,
            "n_assets": len(factor_panel),
            "n_periods": len(turnover_list),
            "rebalance_freq_days": rebalance_freq,
            "avg_turnover": round(float(np.mean(arr)), 4),
            "median_turnover": round(float(np.median(arr)), 4),
            "std_turnover": round(float(np.std(arr)), 4),
            "avg_rank_stability": round(1.0 - float(np.mean(arr)), 4),
        })
