"""YAML 配置驱动回测。

从 YAML 文件加载配置，创建策略和引擎，运行回测。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd
import yaml

logger = logging.getLogger(__name__)

from strategy_research.apps.db import load_price_data
from strategy_research.core.utils.backtest_config import (
    BacktestConfig,
    CostConfig,
    StopLossConfig,
    TrendFilterConfig,
    VolTargetingConfig,
)
from strategy_research.core.utils.strategy_engine import BacktestResult, StrategyEngine

# ============================================================
# 1. YAML 加载
# ============================================================

def load_yaml_config(path: str | Path) -> dict:
    """加载 YAML 配置文件."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ============================================================
# 2. 配置创建
# ============================================================

def create_cost_config(cfg: dict) -> CostConfig:
    """从 YAML 创建 CostConfig."""
    cost_cfg = cfg.get("cost", {})
    return CostConfig(
        enabled=cost_cfg.get("enabled", False),
        commission_bp=cost_cfg.get("commission_bp", 5),
        slippage_bp=cost_cfg.get("slippage_bp", 10),
        impact_factor=cost_cfg.get("impact_factor", 0.1),
        flat_cost_bps=cost_cfg.get("flat_cost_bps"),
    )


def create_engine(cfg: dict) -> StrategyEngine:
    """从 YAML 配置创建 StrategyEngine."""
    risk_cfg = cfg.get("risk", {})
    vt_cfg = risk_cfg.get("vol_targeting", {})
    tf_cfg = risk_cfg.get("trend_filter", {})
    sl_cfg = risk_cfg.get("stop_loss", {})

    vt = None
    if vt_cfg.get("enabled"):
        vt = VolTargetingConfig(
            enabled=True,
            target_vol=vt_cfg.get("target_vol", 0.15),
            lookback=vt_cfg.get("lookback", 60),
            min_scale=vt_cfg.get("min_scale", 0.3),
            max_scale=vt_cfg.get("max_scale", 2.0),
        )

    tf = None
    if tf_cfg.get("enabled"):
        tf = TrendFilterConfig(
            enabled=True,
            ma_window=tf_cfg.get("ma_window", 200),
            bear_exposure=tf_cfg.get("bear_exposure", 0.5),
        )

    sl = None
    if sl_cfg.get("enabled"):
        sl = StopLossConfig(
            enabled=True,
            threshold=sl_cfg.get("threshold", -0.10),
            cooldown_weeks=sl_cfg.get("cooldown_weeks", 5),
        )

    # backtest.risk_free_rate: 年化无风险利率,进 sharpe/sortino 超额分子
    bt_cfg = cfg.get("backtest", {})
    return StrategyEngine(
        vol_targeting=vt, trend_filter=tf, stop_loss=sl,
        risk_free_rate=float(bt_cfg.get("risk_free_rate", 0.0) or 0.0),
    )


def create_backtest_config(cfg: dict) -> BacktestConfig:
    """从 YAML 创建 BacktestConfig."""
    rebal_cfg = cfg.get("rebalance", {})
    return BacktestConfig(
        rebal_freq=rebal_cfg.get("freq", "M"),
        min_history=rebal_cfg.get("min_history", 252),
        top_n=cfg.get("top_n", 10),
        max_weight=cfg.get("max_weight", 0.25),
        weight_method=cfg.get("weight_method", "inverse_vol"),
        cost=create_cost_config(cfg),
    )


# ============================================================
# 3. 数据加载
# ============================================================

def _is_data_fresh(workspace_path: Path, strategy_name: str, end_date: str) -> bool:
    """Check if DuckDB data is fresh enough (ends within 7 days of end_date)."""
    from datetime import datetime
    try:
        from strategy_research.apps.db import get_connection
        conn = get_connection(workspace_path, read_only=True)
        if conn is None:
            return False
        result = conn.execute(
            "SELECT MAX(date) FROM price_data WHERE strategy_name = ?",
            [strategy_name],
        ).fetchone()
        conn.close()
        if result and result[0]:
            max_date = result[0]
            if isinstance(max_date, str):
                max_date = datetime.strptime(max_date, "%Y-%m-%d").date()
            target = datetime.strptime(end_date, "%Y-%m-%d").date()
            return (target - max_date).days <= 7
    except Exception:
        pass
    return False


def load_data(cfg: dict, workspace_path: Path) -> pd.DataFrame:
    """Load price data with DuckDB caching and online fallback.

    Source modes:
    - "duckdb": load from local DB only
    - "auto"|"tencent"|"akshare"|etc: fetch online, save to DuckDB, return
    - "auto+duckdb": DuckDB cache + online refresh (recommended)
      1. DuckDB has fresh data → use cache
      2. DuckDB empty or stale → fetch online → save to DuckDB → return

    The returned panel is filtered to ``data.codes`` when declared:
    orphan assets left over in DuckDB from earlier fetches (codes that
    are no longer in the strategy config) must never enter the panel —
    they carry short/NaN histories that poison factor scores and
    weights (see docs/run-backtest-data-gate.md).
    """
    strategy_name = cfg.get("strategy", {}).get("name", "default")
    data_cfg = cfg.get("data", {})
    source = data_cfg.get("source", "duckdb")
    codes = data_cfg.get("codes", [])
    start_date = data_cfg.get("start_date", "2020-01-01")
    end_date = data_cfg.get("end_date", "2025-12-31")

    def _finalize(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty or not codes:
            return df
        wanted = [c for c in codes if c in df.columns]
        if not wanted:
            return pd.DataFrame(index=df.index)
        if len(wanted) < len(df.columns):
            logger.info(
                "filtering panel to %d/%d configured codes "
                "(orphans dropped)", len(wanted), len(df.columns)
            )
        return cast(pd.DataFrame, df[wanted])

    # --- Cache mode: auto+duckdb ---
    if source == "auto+duckdb":
        # 1. Check if DuckDB cache is fresh
        if codes and _is_data_fresh(workspace_path, strategy_name, end_date):
            df = load_price_data(workspace_path, strategy_name, start_date, end_date)
            if not df.empty:
                logger.info("Using DuckDB cache for %s", strategy_name)
                return _finalize(df)
        # 2. Cache miss — fetch online
        if codes:
            try:
                from strategy_research.apps.data_source.registry import resolve_loader
                from strategy_research.apps.data_source.utils import detect_market
                from strategy_research.apps.db import save_ohlcv_to_db

                market = detect_market(codes[0]) if codes else "a_share"
                loader = resolve_loader(market)  # auto-select best source
                logger.info("Cache miss: fetching from %s for %d codes", loader.name, len(codes))
                data_map = loader.fetch(codes, start_date, end_date)
                n_rows = save_ohlcv_to_db(workspace_path, data_map, strategy_name)
                logger.info("Saved %d rows to DuckDB cache", n_rows)
                return _finalize(load_price_data(workspace_path, strategy_name, start_date, end_date))
            except Exception as exc:
                logger.warning("Online fetch failed: %s", exc)
        return pd.DataFrame()

    # --- DuckDB only ---
    if source == "duckdb":
        df = load_price_data(workspace_path, strategy_name, start_date, end_date)
        if not df.empty:
            return _finalize(df)
        if not codes:
            return df

    # --- Online fetch (auto/tencent/akshare/etc) ---
    if codes:
        try:
            from strategy_research.apps.data_source.registry import resolve_loader
            from strategy_research.apps.data_source.utils import detect_market
            from strategy_research.apps.db import save_ohlcv_to_db

            market = detect_market(codes[0]) if codes else "a_share"
            loader = resolve_loader(market)

            logger.info("Fetching data from %s for %d codes", loader.name, len(codes))
            data_map = loader.fetch(codes, start_date, end_date)

            n_rows = save_ohlcv_to_db(workspace_path, data_map, strategy_name)
            logger.info("Saved %d rows to DuckDB", n_rows)

            return _finalize(load_price_data(workspace_path, strategy_name, start_date, end_date))
        except Exception as exc:
            logger.warning("Online fetch failed: %s", exc)

    return pd.DataFrame()


# ============================================================
# 4. 策略创建
# ============================================================

def create_strategy(cfg: dict, workspace_path=None):
    """从 YAML 配置创建策略实例."""
    factors = cfg.get("factors", [])
    params = cfg.get("strategy_params", {})

    # 合并顶层参数到 params
    for key in ["top_n", "max_weight", "weight_method", "vol_lookback"]:
        if key in cfg and key not in params:
            params[key] = cfg[key]

    strategy_name = params.get("name") or cfg.get("strategy", {}).get("name", "default")
    return FactorStrategy(
        factors, params,
        workspace_path=workspace_path,
        strategy_name=strategy_name,
        codes=cfg.get("data", {}).get("codes") or None,
    )


class FactorStrategy:
    """因子策略: 基于因子表达式计算权重.

    支持 3 种因子类型（按 YAML 配置自动识别）：
    1. **code**: 表达式因子（如 `ts_return(close, 20)`）
    2. **alpha_id**: 单个 Alpha Zoo 因子（如 `gtja191_001`）
    3. **alpha_ids**: 多个 Alpha Zoo 因子组合 + combination 方法

    Expression factors need per-asset wide (T, 5) OHLCV panels to satisfy
    the factor DSL.  When ``workspace_path`` and ``strategy_name`` are
    set, ``compute_weights`` loads long-format OHLCV from DuckDB, splits
    it per asset, runs the DSL on each, and stitches the result back
    into a wide (T, N) factor panel.
    """

    def __init__(
        self,
        factors: list[dict],
        params: dict,
        workspace_path: str | Path | None = None,
        strategy_name: str | None = None,
        codes: list[str] | None = None,
    ):
        self.factors = factors
        self.params = params
        self.workspace_path = (
            Path(workspace_path) if workspace_path is not None else None
        )
        self.strategy_name = strategy_name
        self.codes = codes
        # 运行期因子失败收集：{factor, asset, error, occurrences}。
        # 完整列表由 run_backtest_from_yaml 写 factor_failures.json，
        # 聚合摘要进 metrics.json 与工具返回（docs/run-backtest-data-gate.md）。
        self.factor_failures: list[dict] = []

    def _record_factor_failure(self, factor: str, asset: str, error: str | BaseException) -> None:
        for rec in self.factor_failures:
            if rec["factor"] == factor and rec["asset"] == asset:
                rec["occurrences"] += 1
                return
        self.factor_failures.append({
            "factor": factor,
            "asset": asset,
            "error": str(error)[:300],
            "occurrences": 1,
        })

    def _load_long_ohlcv(
        self, up_to_date: pd.Timestamp
    ) -> pd.DataFrame:
        """Load long-format OHLCV from DuckDB up to and including ``up_to_date``.

        Raises:
            RuntimeError: when ``workspace_path`` / ``strategy_name`` is
                not set, or DuckDB is missing / unreadable.
        """
        if self.workspace_path is None or self.strategy_name is None:
            raise RuntimeError(
                "FactorStrategy needs workspace_path and strategy_name to load OHLCV. "
                "Pass them to create_strategy(cfg, workspace_path=...)."
            )
        from strategy_research.apps.db import get_connection

        conn = get_connection(self.workspace_path, read_only=True)
        if conn is None:
            raise RuntimeError(
                f"Cannot open DuckDB at {self.workspace_path}; cannot load OHLCV"
            )
        try:
            query = """
                SELECT date, asset_code, open, high, low, close, volume
                FROM price_data
                WHERE strategy_name = ? AND date <= ?
            """
            params: list = [self.strategy_name, pd.Timestamp(up_to_date).date()]
            # 只加载 config.codes 内的资产 —— 幽灵资产（历史残留、短/NaN 历史）
            # 会毒化因子分数与权重（docs/run-backtest-data-gate.md）。
            if self.codes:
                placeholders = ", ".join(["?" for _ in self.codes])
                query += f" AND asset_code IN ({placeholders})"
                params.extend(self.codes)
            query += " ORDER BY date, asset_code"
            df = conn.execute(query, params).fetchdf()
        finally:
            conn.close()
        return df

    def _has_expression_factors(self) -> bool:
        return any(f.get("code") for f in self.factors)

    def compute_weights(
        self,
        date: pd.Timestamp,
        price_panel: pd.DataFrame,
        nav_history: pd.Series,
    ) -> dict[str, float]:
        """计算权重."""
        # 1. 计算因子值
        factor_values = self._compute_factor_values(date, price_panel)

        # 2. 计算综合分数 — 取每个因子在当前 date 的横截面值
        scores = self._compute_scores(date, price_panel, factor_values)

        # 3. 选择 top_n
        # 防御：因子失败/无数据的资产 score 为 0/NaN，绝不能当作"最优"被选中
        # （0 > 真实资产的负分 → 幽灵资产混入权重 → NaN 毒化）。无效分数先剔除。
        scores = scores.replace([np.inf, -np.inf], np.nan)
        eligible = scores.dropna()
        if eligible.empty:
            # 所有因子都无有效分数（数据不足/全部失败）——返回空权重，
            # 引擎保持净值不动，绝不产出 NaN。
            return {}
        top_n = self.params.get("top_n", 10)
        selected = eligible.nlargest(top_n).index.tolist()

        # 4. 计算权重
        return self._compute_weights_for_selected(date, price_panel, selected)

    def _compute_factor_values(
        self, date: pd.Timestamp, price_panel: pd.DataFrame
    ) -> dict[str, Any]:
        """Compute per-factor values (expression / single alpha / combined)."""
        from strategy_research.apps.alpha_zoo_adapter import AlphaZooAdapter
        from strategy_research.apps.tools.data_transforms import (
            is_wide_close_format,
            long_to_wide_ohlcv_per_asset,
        )

        factor_values: dict[str, Any] = {}
        alpha_zoo = None  # lazy init

        # Expression factors need real per-asset OHLCV.  Load once from
        # DuckDB, split per asset, then run the DSL on each panel.
        ohlcv_panels: dict[str, pd.DataFrame] | None = None
        if self._has_expression_factors() and self.workspace_path is not None:
            long_ohlcv = self._load_long_ohlcv(date)
            if long_ohlcv.empty:
                # fail-fast: expression factors with no data is a bug
                if is_wide_close_format(price_panel):
                    raise RuntimeError(
                        "FactorStrategy.compute_weights received multi-asset "
                        "wide(T, N) close-only panel AND DuckDB has no OHLCV. "
                        "Either provide long-format OHLCV in DuckDB "
                        f"({self.workspace_path}/{self.strategy_name}) "
                        "or use alpha_id / alpha_ids factors instead of "
                        "expression 'code' factors."
                    )
                raise RuntimeError(
                    f"No OHLCV data in DuckDB for strategy "
                    f"{self.strategy_name!r} up to {date}; cannot evaluate "
                    "expression factors."
                )
            ohlcv_panels = long_to_wide_ohlcv_per_asset(
                long_ohlcv, asset_col="asset_code"
            )

        for factor in self.factors:
            name = factor.get("name", "unknown")

            # 方式 1: 表达式因子
            code = factor.get("code", "")
            if code:
                self._compute_expression_factor(
                    name, code, date, price_panel, ohlcv_panels, factor_values
                )
                continue

            # 方式 2: 单个 Alpha Zoo 因子
            alpha_id = factor.get("alpha_id", "")
            if alpha_id:
                if alpha_zoo is None:
                    alpha_zoo = AlphaZooAdapter()
                try:
                    wide = alpha_zoo.compute_as_wide(alpha_id, price_panel.loc[:date])
                    factor_values[name] = wide
                except Exception as e:
                    logger.warning(f"⚠️  Alpha Zoo {alpha_id} 计算失败: {e}")
                    self._record_factor_failure(name, alpha_id, e)
                continue

            # 方式 3: 多个 Alpha Zoo 因子组合
            alpha_ids = factor.get("alpha_ids", [])
            if alpha_ids:
                if alpha_zoo is None:
                    alpha_zoo = AlphaZooAdapter()
                combination = factor.get("combination", "equal")
                try:
                    df_batch = alpha_zoo.compute_batch(alpha_ids, price_panel.loc[:date])
                    if df_batch.empty:
                        continue
                    if combination in ("equal", "ic_weighted"):
                        combined = df_batch.mean(axis=1)
                    else:
                        combined = df_batch.mean(axis=1)
                    combined_wide = combined.unstack()
                    factor_values[name] = combined_wide
                except Exception as e:
                    logger.warning(f"⚠️  Alpha Zoo 组合 {alpha_ids} 计算失败: {e}")
                    self._record_factor_failure(name, ",".join(alpha_ids)[:80], e)

        return factor_values

    def _compute_expression_factor(
        self,
        name: str,
        code: str,
        date: pd.Timestamp,
        price_panel: pd.DataFrame,
        ohlcv_panels: dict[str, pd.DataFrame] | None,
        factor_values: dict[str, Any],
    ) -> None:
        """Evaluate an expression factor per-asset into a wide DataFrame."""
        from strategy_research.apps.compute_factor import FactorComputeError, compute_factor

        if ohlcv_panels is None:
            logger.warning(
                f"⚠️  因子 {name} 跳过: expression factor requires "
                "workspace_path and strategy_name; provide them via "
                "create_strategy(cfg, workspace_path=...)"
            )
            return
        # wide(T, N) per-asset factor result, indexed by date
        wide = pd.DataFrame(
            index=price_panel.index,
            columns=price_panel.columns,
            dtype=float,
        )
        for asset in price_panel.columns:
            asset_df = ohlcv_panels.get(asset)
            if asset_df is None:
                continue
            try:
                s = compute_factor(code, asset_df.loc[:date])
                wide[asset] = s.reindex(price_panel.index)
            except FactorComputeError as e:
                logger.warning(f"⚠️  因子 {name} 在 {asset} 失败: {e}")
                self._record_factor_failure(name, asset, e)
            except Exception as e:
                logger.warning(f"⚠️  因子 {name} 在 {asset} 异常: {e}")
                self._record_factor_failure(name, asset, e)
        factor_values[name] = wide

    def _compute_scores(
        self,
        date: pd.Timestamp,
        price_panel: pd.DataFrame,
        factor_values: dict[str, Any],
    ) -> pd.Series:
        """Blend per-factor cross-sections into a combined score series."""
        scores = pd.Series(0.0, index=price_panel.columns)
        for factor in self.factors:
            name = factor.get("name", "unknown")
            weight = factor.get("weight", 1.0)
            if name not in factor_values:
                continue
            fv = factor_values[name]
            # 取当前 date 的横截面（per-asset）
            if isinstance(fv, pd.DataFrame):
                # wide DataFrame (T,N) → 取当前 date 一行
                if date in fv.index:
                    current = fv.loc[date]
                else:
                    current = fv.iloc[-1] if len(fv) > 0 else pd.Series(0.0, index=price_panel.columns)
            else:
                # Series（单资产情况）
                current = fv
            # 对齐到 price_panel 的列
            aligned = current.reindex(price_panel.columns, fill_value=0)
            scores = scores.add(aligned * weight, fill_value=0)
        return scores

    def _compute_weights_for_selected(
        self,
        date: pd.Timestamp,
        price_panel: pd.DataFrame,
        selected: list[str],
    ) -> dict[str, float]:
        """Compute weights for the selected assets."""
        weight_method = self.params.get("weight_method", "inverse_vol")
        if weight_method == "inverse_vol":
            lookback = self.params.get("vol_lookback", 60)
            vols = price_panel[selected].pct_change(fill_method=None).iloc[-lookback:].std()
            valid_vols = vols.dropna()
            if valid_vols.empty:
                # 波动率全部无效（历史不足/停牌）——退化为等权
                return {c: 1.0 / len(selected) for c in selected}
            inv_vol = 1.0 / valid_vols.clip(lower=0.01)
            return (inv_vol / inv_vol.sum()).to_dict()
        # 等权
        return {c: 1.0 / len(selected) for c in selected}

    def on_risk_check(
        self,
        weights: dict[str, float],
        nav_history: pd.Series,
        date: pd.Timestamp,
    ) -> dict[str, float]:
        """自定义风控."""
        from strategy_research.core.utils.backtest_utils import apply_max_weight, normalize_weights

        max_weight = self.params.get("max_weight", 0.25)
        weights = apply_max_weight(weights, max_weight)
        return normalize_weights(weights)


# ============================================================
# 5. 一键回测
# ============================================================

def run_from_yaml(yaml_path: str | Path, workspace_path: Path) -> BacktestResult:
    """YAML → Strategy → Engine → BacktestResult.

    Args:
        yaml_path: YAML 配置文件路径
        workspace_path: 工作区路径 (用于 DuckDB)

    Returns:
        BacktestResult (nav_daily, weights_history, metrics)
    """
    cfg = load_yaml_config(yaml_path)
    strategy = create_strategy(cfg, workspace_path=workspace_path)
    engine = create_engine(cfg)
    data = load_data(cfg, workspace_path)

    if data.empty:
        raise ValueError("数据为空，请先导入价格数据")

    # 归一化价格. 不同资产的起始交易日可能不同（列内前导 NaN），
    # 不能用 data.iloc[0] 做除数（整列会被 NaN 污染）：
    # 每列除以各自的首个有效值（bfill 把前导 NaN 填为首个有效值）。
    data_norm = data / data.bfill().iloc[0]

    # 参数
    rebal_cfg = cfg.get("rebalance", {})
    rebal_freq = rebal_cfg.get("freq", "M")
    min_history = rebal_cfg.get("min_history", 252)
    cost = create_cost_config(cfg)

    result = engine.run(
        price_panel=data_norm,
        strategy=strategy,
        rebal_freq=rebal_freq,
        min_history=min_history,
        cost=cost,
    )
    # 运行期因子失败收集 → BacktestResult（由 backtest.py 落盘/返回）
    result.factor_failures = list(getattr(strategy, "factor_failures", None) or [])
    return result
