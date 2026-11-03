"""因子验证工具。

实现 IC/IR 验证、6 维评分、Mutual IC 去重 (时序口径)、IC 衰减检查。

**单源评分铁律**（docs/phase1-research-discipline.md §2）：

    因子质检评分（IC/IR/stability/…/overall）的唯一真源是本模块。
    任何模块不得内联复制评分公式；需要分数 → 调用
    ``compute_overall_score`` 及其 6 维打分函数。升级评分公式
    （如 quantskills B1 primary_score v2）时只改 ``compute_overall_score``
    单点——历史评分因此可比。

**排除**：``config_runner._compute_scores`` 是组合权重构造（横截面
混合），语义不同，不适用本铁律。``templates/prepare.py::
_compute_factor_scores`` 是模板内历史公式副本（豁免待迁移）。
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional, cast

import numpy as np
import pandas as pd

# ============================================================
# 验证阈值
# ============================================================

# IC/IR 阈值
IC_THRESHOLD = 0.03
IR_THRESHOLD = 0.5

# 6 维评分权重
SCORE_WEIGHTS = {
    "stability": 0.25,
    "diversification": 0.20,
    "turnover": 0.15,
    "monotonicity": 0.20,
    "coverage": 0.10,
    "rank_ic": 0.10,
}

# IC 衰减阈值
IC_DECAY_THRESHOLD = 0.5  # 20d IC / 1d IC >= 0.5

# Mutual IC 去重阈值
MUTUAL_IC_THRESHOLD = 0.7


# ============================================================
# IC/IR 计算
# ============================================================

def compute_ic(
    factor_values: pd.Series,
    forward_returns: pd.Series,
    method: str = "pearson",
) -> dict:
    """计算 IC 和 IR。

    Args:
        factor_values: 因子值 (index=date 或 MultiIndex)
        forward_returns: 未来收益率
        method: "pearson" 或 "spearman"

    Returns:
        dict: {
            "ic_mean": float,
            "ic_std": float,
            "ir": float,
            "ic_series": pd.Series,
        }
    """
    # 对齐数据
    df = pd.DataFrame({
        "factor": factor_values,
        "return": forward_returns,
    }).dropna()

    if len(df) < 10:
        return {"ic_mean": 0.0, "ic_std": 0.0, "ir": 0.0, "ic_series": pd.Series()}

    # 如果是 MultiIndex (date, asset)，按 date 分组计算 IC
    corr_method = cast(Any, method)
    if isinstance(df.index, pd.MultiIndex):
        ic_series = df.groupby(level=0).apply(
            lambda g: cast(pd.Series, g["factor"]).corr(
                cast(pd.Series, g["return"]), method=corr_method
            )
            if len(g) >= 10 else np.nan
        )
    else:
        # 单日截面
        ic_series = pd.Series([
            cast(pd.Series, df["factor"]).corr(
                cast(pd.Series, df["return"]), method=corr_method
            )
        ])

    ic_series = ic_series.dropna()
    if ic_series.empty:
        return {"ic_mean": 0.0, "ic_std": 0.0, "ir": 0.0, "ic_series": pd.Series()}

    ic_mean = float(ic_series.mean())
    ic_std = float(ic_series.std()) if len(ic_series) > 1 else 0.0
    ir = ic_mean / ic_std if ic_std > 0 else 0.0

    return {
        "ic_mean": ic_mean,
        "ic_std": ic_std,
        "ir": ir,
        "ic_series": ic_series,
    }


def compute_ic_decay(
    factor_values: pd.Series,
    prices: pd.DataFrame,
    periods: list[int] = [1, 5, 20],
) -> dict:
    """计算 IC 衰减。

    Args:
        factor_values: 因子值
        prices: 价格数据 (index=date, columns=assets)
        periods: 衰减周期列表

    Returns:
        dict: {f"ic_decay_{p}d": float for p in periods}
    """
    result = {}
    for period in periods:
        # 计算 period 期 forward returns
        forward_returns = prices.pct_change(period, fill_method=None).shift(-period)
        if isinstance(forward_returns, pd.DataFrame):
            forward_returns = forward_returns.mean(axis=1)

        ic_info = compute_ic(factor_values, forward_returns)
        result[f"ic_decay_{period}d"] = ic_info["ic_mean"]

    return result


# ============================================================
# 6 维评分
# ============================================================

def score_stability(ic_series: pd.Series) -> float:
    """稳定性评分: IC 序列的稳定性。

    使用 IC 正比例 和 IC 标准差 综合评分。
    """
    if ic_series.empty or len(ic_series) < 5:
        return 0.0

    # IC 正比例
    positive_ratio = (ic_series > 0).mean()

    # IC 标准差 (越小越稳定)
    ic_std = ic_series.std()
    stability = 1.0 / (1.0 + ic_std * 10)  # 归一化

    return float(positive_ratio * 0.6 + stability * 0.4)


def score_diversification(factor_values: pd.Series) -> float:
    """分散化评分: 因子在不同资产上的分散程度。

    使用截面标准差的均值。
    """
    if isinstance(factor_values.index, pd.MultiIndex):
        # MultiIndex: 按 date 分组计算截面标准差
        cross_std = factor_values.groupby(level=0).std()
        if cross_std.empty:
            return 0.0
        # 归一化到 0-1
        return float(min(1.0, cross_std.mean() / 2.0))
    else:
        # 单日截面
        std = factor_values.std()
        return float(min(1.0, std / 2.0))


def score_turnover(factor_values: pd.Series) -> float:
    """换手率评分: 因子值的变化频率。

    使用相邻日期因子值的 Rank 相关性。
    """
    if isinstance(factor_values.index, pd.MultiIndex):
        # MultiIndex: 按 date 分组计算 Rank 相关
        dates = factor_values.index.get_level_values(0).unique().sort_values()
        if len(dates) < 2:
            return 0.0

        correlations = []
        for i in range(1, min(len(dates), 10)):
            prev_date = dates[i - 1]
            curr_date = dates[i]

            prev_vals = factor_values.loc[prev_date] if prev_date in factor_values.index else pd.Series()
            curr_vals = factor_values.loc[curr_date] if curr_date in factor_values.index else pd.Series()

            if len(prev_vals) > 5 and len(curr_vals) > 5:
                # 对齐
                common = prev_vals.index.intersection(curr_vals.index)
                if len(common) > 5:
                    corr = prev_vals[common].rank().corr(curr_vals[common].rank())
                    if not np.isnan(corr):
                        correlations.append(corr)

        if not correlations:
            return 0.0

        # 高相关性 = 低换手 = 高分
        avg_corr = np.mean(correlations)
        return float(max(0.0, min(1.0, avg_corr)))
    else:
        return 0.5  # 单日无法计算


def score_monotonicity(
    factor_values: pd.Series,
    forward_returns: pd.Series,
    n_quantiles: int = 5,
) -> float:
    """单调性评分: 因子分组收益的单调性。

    将因子值分为 n_quantiles 组，检查收益是否单调。
    """
    df = pd.DataFrame({
        "factor": factor_values,
        "return": forward_returns,
    }).dropna()

    if len(df) < n_quantiles * 10:
        return 0.0

    # 分组
    try:
        df["quantile"] = pd.qcut(df["factor"], n_quantiles, labels=False, duplicates="drop")
    except ValueError:
        return 0.0

    # 计算每组平均收益
    group_returns = df.groupby("quantile")["return"].mean()

    if len(group_returns) < 2:
        return 0.0

    # 计算单调性 (Spearman rank correlation)
    ranks = np.arange(len(group_returns))
    group_ranks = group_returns.rank().values

    if len(ranks) < 2:
        return 0.0

    monotonicity = np.corrcoef(ranks, group_ranks)[0, 1]
    if np.isnan(monotonicity):
        return 0.0

    return float(abs(monotonicity))


def score_coverage(factor_values: pd.Series) -> float:
    """覆盖率评分: 因子值的非空比例。"""
    if factor_values.empty:
        return 0.0

    coverage = factor_values.notna().mean()
    return float(coverage)


def score_rank_ic(ic_mean: float) -> float:
    """Rank IC 评分: 基于 IC 绝对值的评分。"""
    # IC 绝对值越大越好，但有上限
    abs_ic = abs(ic_mean)
    if abs_ic >= 0.1:
        return 1.0
    elif abs_ic >= 0.05:
        return 0.8
    elif abs_ic >= 0.03:
        return 0.6
    elif abs_ic >= 0.01:
        return 0.4
    else:
        return 0.2


def compute_6d_scores(
    ic_series: pd.Series,
    factor_values: pd.Series,
    forward_returns: pd.Series,
    ic_mean: float = 0.0,
) -> dict:
    """计算 6 维评分。

    Returns:
        dict: {
            "stability": float,
            "diversification": float,
            "turnover": float,
            "monotonicity": float,
            "coverage": float,
            "rank_ic": float,
        }
    """
    return {
        "stability": score_stability(ic_series),
        "diversification": score_diversification(factor_values),
        "turnover": score_turnover(factor_values),
        "monotonicity": score_monotonicity(factor_values, forward_returns),
        "coverage": score_coverage(factor_values),
        "rank_ic": score_rank_ic(ic_mean),
    }


def compute_overall_score(scores: dict) -> float:
    """计算综合评分。"""
    total = 0.0
    for key, weight in SCORE_WEIGHTS.items():
        total += scores.get(key, 0.0) * weight
    return float(total)


# ============================================================
# Mutual IC 去重
# ============================================================

def compute_mutual_ic(
    factor_values_1: pd.Series,
    factor_values_2: pd.Series,
    min_dates: int = 5,
    min_assets: int = 5,
) -> float:
    """计算两个因子的 Mutual IC (时序口径)。

    MultiIndex (date, asset) 面板: 逐日计算截面 Spearman 秩相关, 取有效日期
    均值——单一截面可能恰好同向, 时序均值才是冗余判据 (QuantaAlpha 式因子值
    相关性)。验证期准入去重应传入验证期窗口的因子值。

    非面板序列 (date 单索引): 对齐后整段 Spearman 秩相关。

    有效截面不足 ( < min_dates 个日期或截面资产 < min_assets) 时返回 0.0:
    视为"无法判定冗余", 调用方 fail-open 保留双方。
    """
    df = pd.DataFrame({
        "factor1": factor_values_1,
        "factor2": factor_values_2,
    }).dropna()

    if len(df) < 10:
        return 0.0

    if isinstance(df.index, pd.MultiIndex):
        corrs = (
            df.groupby(level=0)
            .apply(
                lambda g: g["factor1"].rank().corr(g["factor2"].rank())
                if len(g) >= min_assets
                else np.nan
            )
            .dropna()
        )
        if len(corrs) < min_dates:
            return 0.0
        return float(corrs.mean())

    corr = df["factor1"].rank().corr(df["factor2"].rank())
    return float(corr) if not np.isnan(corr) else 0.0


def deduplicate_factors(
    factors: list[dict],
    threshold: float = MUTUAL_IC_THRESHOLD,
) -> list[dict]:
    """Mutual IC 去重 (时序口径)。

    Args:
        factors: [{"factor_name": str, "factor_values": pd.Series, ...}]
            factor_values 应为验证期窗口的因子值 (date, asset) 面板
        threshold: 相关系数阈值

    Returns:
        list: 去重后的因子列表
    """
    if len(factors) <= 1:
        return factors

    # 按 overall_score 排序 (保留高分因子)
    factors = sorted(factors, key=lambda x: x.get("overall_score", 0), reverse=True)

    selected = [factors[0]]
    for factor in factors[1:]:
        is_redundant = False
        for selected_factor in selected:
            corr = compute_mutual_ic(
                factor.get("factor_values", pd.Series()),
                selected_factor.get("factor_values", pd.Series()),
            )
            if abs(corr) >= threshold:
                is_redundant = True
                break

        if not is_redundant:
            selected.append(factor)

    return selected


# ============================================================
# 主验证函数
# ============================================================

def validate_factor(
    factor_code: str,
    prices: pd.DataFrame,
    forward_returns: Optional[pd.Series] = None,
    factor_values: Optional[pd.Series] = None,
    strategy_name: str = "",
    source: str = "",
) -> dict:
    """验证单个因子。

    Args:
        factor_code: 因子表达式
        prices: 价格数据 (index=date, columns=assets)
        forward_returns: 未来收益率 (可选，自动计算)
        factor_values: 因子值 (可选，自动计算)
        strategy_name: 策略名称
        source: 因子来源

    Returns:
        dict: {
            "passed": bool,
            "ic_mean": float,
            "ic_std": float,
            "ir": float,
            "rank_ic_mean": float,
            "scores": dict,
            "overall_score": float,
            "fail_reasons": list[str],
        }
    """
    from strategy_research.apps.compute_factor import compute_factor

    # 计算因子值
    if factor_values is None:
        factor_values = compute_factor(factor_code, prices)

    # 计算 forward returns
    if forward_returns is None:
        forward_returns = prices.pct_change(fill_method=None).shift(-1).mean(axis=1)

    # 对齐
    df = pd.DataFrame({
        "factor": factor_values,
        "return": forward_returns,
    }).dropna()

    if len(df) < 20:
        return {
            "passed": False,
            "ic_mean": 0.0,
            "ic_std": 0.0,
            "ir": 0.0,
            "rank_ic_mean": 0.0,
            "scores": {},
            "overall_score": 0.0,
            "fail_reasons": ["数据不足 (< 20)"],
        }

    # 计算 IC/IR
    ic_info = compute_ic(factor_values, forward_returns)
    ic_mean = ic_info["ic_mean"]
    ic_std = ic_info["ic_std"]
    ir = ic_info["ir"]
    ic_series = ic_info["ic_series"]

    # 计算 Rank IC
    rank_ic_info = compute_ic(factor_values, forward_returns, method="spearman")
    rank_ic_mean = rank_ic_info["ic_mean"]

    # 计算 IC 衰减
    ic_decay = compute_ic_decay(factor_values, prices)

    # 计算 6 维评分
    scores = compute_6d_scores(ic_series, factor_values, forward_returns, ic_mean)

    # 计算综合评分
    overall_score = compute_overall_score(scores)

    # 验证阈值
    fail_reasons = []
    if abs(ic_mean) < IC_THRESHOLD:
        fail_reasons.append(f"IC < {IC_THRESHOLD} (当前: {ic_mean:.4f})")
    if abs(ir) < IR_THRESHOLD:
        fail_reasons.append(f"IR < {IR_THRESHOLD} (当前: {ir:.4f})")

    # IC 衰减检查
    ic_1d = ic_decay.get("ic_decay_1d", 0.0)
    ic_20d = ic_decay.get("ic_decay_20d", 0.0)
    if abs(ic_1d) > 0.01 and abs(ic_20d) / abs(ic_1d) < IC_DECAY_THRESHOLD:
        fail_reasons.append(
            f"IC 衰减过快 (1d: {ic_1d:.4f}, 20d: {ic_20d:.4f})"
        )

    is_valid = len(fail_reasons) == 0

    return {
        "passed": is_valid,
        "ic_mean": ic_mean,
        "ic_std": ic_std,
        "ir": ir,
        "rank_ic_mean": rank_ic_mean,
        "ic_decay": ic_decay,
        "scores": scores,
        "overall_score": overall_score,
        "fail_reasons": fail_reasons,
    }


def validate_factors_batch(
    factors: list[dict],
    prices: pd.DataFrame,
    strategy_name: str = "",
    deduplicate: bool = True,
) -> dict:
    """批量验证因子。

    Args:
        factors: [{"factor_name": str, "factor_code": str, ...}]
        prices: 价格数据
        strategy_name: 策略名称
        deduplicate: 是否进行 Mutual IC 去重

    Returns:
        dict: {
            "candidates": list[dict],
            "rejected": list[dict],
            "total": int,
            "passed": int,
        }
    """
    candidates = []
    rejected = []

    for factor in factors:
        factor_name = factor.get("factor_name", "unknown")
        factor_code = factor.get("factor_code", "")

        result = validate_factor(
            factor_code=factor_code,
            prices=prices,
            strategy_name=strategy_name,
            source=factor.get("source", ""),
        )
        result["factor_name"] = factor_name
        result["factor_code"] = factor_code

        if result["passed"]:
            candidates.append(result)
        else:
            rejected.append(result)

    # Mutual IC 去重
    if deduplicate and len(candidates) > 1:
        candidates = deduplicate_factors(candidates)

    return {
        "candidates": candidates,
        "rejected": rejected,
        "total": len(factors),
        "passed": len(candidates),
    }


def compute_data_fingerprint(prices: pd.DataFrame) -> str:
    """计算数据指纹。"""
    # 使用列名、行数、数据范围作为指纹
    content = f"{list(prices.columns)}_{len(prices)}_{prices.index.min()}_{prices.index.max()}"
    return hashlib.md5(content.encode()).hexdigest()[:16]
