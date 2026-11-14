"""无前视调仓回测模拟与组合指标（领域中立算法）。

从 ``templates/prepare.py``（2026-11-05 无前视修复版）提取的评估算法闭包，
随 C 方案成为 kernel 正式代码（方案见
``docs/design/prepare-algo-kernel-extract-20261114.md``）。模板副本已随 W1
``9140f6c3`` 从 git 移除，本地未跟踪副本是含修复的唯一版本——本模块使其
不再依附于一个 gitignore 的漂移文件。

**时序约定（被测性质，``tests/test_backtest_no_lookahead.py`` 钉住）**：
用第 i 日数据（因子分数基于 close[i]）选出的权重不得赚取第 i 日收益，
否则构成 1 根 K 线前视偏差；调仓日损益归旧权重，新权重自下一根 K 线生效。

提取边界：
  - 只提算法闭包（evaluate + 打分/模拟/指标）；数据加载、apps 因子路径、
    metrics_raw.json 落盘（死契约零读者）不提取。
  - 因子来源注入化：``factor_frames=None`` 时退回 ``compute_simple_factors``。
  - kernel 无 I/O：因子失败信息不写文件，放进返回 dict。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = ["evaluate", "compute_simple_factors"]


def compute_simple_factors(
    prices: pd.DataFrame, factor_exprs: list[dict]
) -> dict[str, pd.DataFrame]:
    """简单因子计算（纯 pandas，不依赖任何外部因子服务）。

    支持 ``ts_return(close, n)`` / ``ts_std(close, n)`` 简单表达式；
    其余表达式一律退回 20 日动量。
    """
    factors = {}
    for expr in factor_exprs:
        name = expr.get("factor_name", "unknown")
        code = expr.get("factor_code", "")

        # 解析简单表达式
        if "ts_return" in code:
            # 提取窗口参数
            try:
                window = int(code.split(",")[-1].strip().rstrip(")"))
                factors[name] = prices.pct_change(window, fill_method=None)
            except (ValueError, IndexError):
                factors[name] = prices.pct_change(20, fill_method=None)
        elif "ts_std" in code:
            try:
                window = int(code.split(",")[-1].strip().rstrip(")"))
                factors[name] = prices.pct_change(fill_method=None).rolling(window).std()
            except (ValueError, IndexError):
                factors[name] = prices.pct_change(fill_method=None).rolling(20).std()
        else:
            # 默认: 20 日动量
            factors[name] = prices.pct_change(20, fill_method=None)

    return factors


def evaluate(
    params: dict,
    factor_exprs: list[dict],
    factor_weight_method: str,
    data: dict,
    *,
    factor_frames: dict[str, pd.DataFrame] | None = None,
) -> dict:
    """评估策略表现（无前视调仓模拟）。

    Args:
        params: 策略参数
            - "top_n": 持仓数量（默认 10）
            - "max_weight": 单资产最大权重（默认 0.25）
            - "rebalance_freq": 调仓频率（交易日，默认 20）
        factor_exprs: 因子表达式列表
            [{"factor_name": "mom_20d", "factor_code": "ts_return(close, 20)", ...}]
        factor_weight_method: 因子权重方式，"equal" 或 "inv_vol"
        data: 数据 dict，必须含 "prices"（DataFrame，index=date, columns=assets）
        factor_frames: 因子面板注入（{factor_name: DataFrame}）。None 时用
            ``compute_simple_factors`` 现算。面板 dict 可携带 ``factor_failures``
            元数据键（进返回 dict，不进因子迭代）。

    Returns:
        dict: 评估指标（calmar/sharpe/max_dd/ann_return/ann_vol/sortino/
        turnover/trades；数据不足时全 0）。
    """
    prices = data.get("prices", pd.DataFrame())
    if prices.empty or len(prices) < 60:
        return _empty_metrics()

    # 参数
    top_n = params.get("top_n", 10)
    max_weight = params.get("max_weight", 0.25)
    rebalance_freq = params.get("rebalance_freq", 20)

    # 因子：注入优先，否则现算（模板版此处的 apps.compute_factor 路径已死）
    if factor_frames is not None:
        factors = dict(factor_frames)  # 拷贝后 pop，不污染调用方输入
    else:
        factors = compute_simple_factors(prices, factor_exprs)
    if not factors:
        return _empty_metrics()

    # factor_failures 是元数据 key，不进因子面板迭代；kernel 无 I/O，
    # 改放返回 dict（模板版此处写 factor_failures.json 文件）
    factor_failures = factors.pop("factor_failures", None)

    # 计算综合分数
    scores = _compute_factor_scores(prices, factors, factor_exprs)

    # 计算日收益
    returns = prices.pct_change(fill_method=None)

    # 模拟回测
    nav, weight_changes = _simulate_backtest(
        prices, returns, scores, top_n, max_weight, rebalance_freq, factor_weight_method
    )

    # 构造 NAV 序列
    nav_series = pd.Series(
        nav, index=prices.index[rebalance_freq - 1:rebalance_freq - 1 + len(nav)]
    )

    # 计算指标
    ann_return = _ann_return(nav_series)
    max_dd = _max_drawdown(nav_series)
    sharpe = _sharpe(nav_series)
    ann_vol = _ann_vol(nav_series)
    sortino = _sortino(nav_series)
    turnover = np.mean(weight_changes) * 252 if weight_changes else 0.0

    # Calmar
    calmar = ann_return / abs(max_dd) if max_dd < 0 else 0.0

    metrics = {
        "calmar": calmar,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "ann_return": ann_return,
        "ann_vol": ann_vol,
        "sortino": sortino,
        "turnover": turnover,
        "trades": len(weight_changes),
    }
    if factor_failures:
        metrics["factor_failures"] = factor_failures
    return metrics


def _compute_factor_scores(
    prices: pd.DataFrame,
    factors: dict,
    factor_exprs: list[dict],
) -> pd.DataFrame:
    """Blend per-factor values into a combined score DataFrame.

    NOTE (single-source scoring iron rule, docs/phase1-research-discipline.md):
    this is a PORTFOLIO BLEND (weights applied to factor values), not
    factor quality scoring — factor quality must come from
    ``core.factor_validate.compute_overall_score``. If this blend
    formula ever gains a quality component, delegate instead of copying.
    """
    scores = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    for name, factor_df in factors.items():
        weight = 1.0
        for expr in factor_exprs:
            if expr.get("factor_name") == name:
                weight = expr.get("weight", 1.0)
                break
        aligned = factor_df.reindex(prices.index).reindex(columns=prices.columns)
        scores = scores.add(aligned * weight, fill_value=0)
    return scores


def _simulate_backtest(
    prices: pd.DataFrame,
    returns: pd.DataFrame,
    scores: pd.DataFrame,
    top_n: int,
    max_weight: float,
    rebalance_freq: int,
    factor_weight_method: str,
) -> tuple[list[float], list[float]]:
    """Simulate periodic rebalancing; returns (nav, weight_changes).

    **时序约定（无前视）**：第 i 日的收益用**当日开盘前已持有的权重**计算，
    调仓发生在收益结算**之后**——即 close[i] 决定的权重最早在 i+1 日生效。

    2026-11-05 修复：原实现把「调仓」放在循环体开头，随即用新权重乘
    ``returns.iloc[i]``，等于用 close[i] 的信息赚取 close[i] 当天的收益，
    构成 1 根 K 线前视偏差（tests/test_backtest_no_lookahead.py 钉住）。
    """
    nav = [1.0]
    prev_weights = {}
    weight_changes = []

    for i in range(rebalance_freq, len(prices)):
        # ① 先结算当日收益：用**上一次调仓决定、此刻仍持有**的权重。
        daily_ret = 0.0
        for asset, w in prev_weights.items():
            if asset in returns.columns:
                r = returns.iloc[i][asset]
                if pd.notna(r):
                    daily_ret += w * r

        # ② 再执行调仓（新权重自下一根 K 线起才计收）。
        if (i - rebalance_freq) % rebalance_freq == 0:
            day_scores = scores.iloc[i].dropna()
            if len(day_scores) < top_n:
                top_assets = day_scores.nlargest(min(top_n, len(day_scores))).index.tolist()
            else:
                top_assets = day_scores.nlargest(top_n).index.tolist()

            # 计算权重
            if factor_weight_method == "inv_vol" and len(top_assets) > 0:
                lookback = min(60, i)
                vols = returns.iloc[i - lookback:i][top_assets].std()
                vols = vols.clip(lower=0.01)
                inv_vol = 1.0 / vols
                new_weights = (inv_vol / inv_vol.sum()).to_dict()
            else:
                new_weights = {a: 1.0 / len(top_assets) for a in top_assets}

            # 最大权重约束
            for a in new_weights:
                if new_weights[a] > max_weight:
                    new_weights[a] = max_weight
            total = sum(new_weights.values())
            if total > 0:
                new_weights = {a: w / total for a, w in new_weights.items()}

            # 记录换手
            all_assets = set(list(prev_weights.keys()) + list(new_weights.keys()))
            change = sum(abs(new_weights.get(a, 0) - prev_weights.get(a, 0)) for a in all_assets) / 2
            weight_changes.append(change)

            prev_weights = new_weights

        nav.append(nav[-1] * (1 + daily_ret))

    return nav, weight_changes


def _empty_metrics() -> dict:
    """返回空指标（数据不足时的全 0 键集）。"""
    return {
        "calmar": 0.0,
        "sharpe": 0.0,
        "max_dd": 0.0,
        "ann_return": 0.0,
        "ann_vol": 0.0,
        "sortino": 0.0,
        "turnover": 0.0,
        "trades": 0,
    }


def _ann_return(nav: pd.Series, freq: int = 252) -> float:
    """计算年化收益率。"""
    if nav.empty or len(nav) < 2:
        return 0.0
    rets = nav.pct_change(fill_method=None).dropna()
    if rets.empty:
        return 0.0
    n_years = len(rets) / freq
    total_ret = nav.iloc[-1] / nav.iloc[0] - 1
    return float((1 + total_ret) ** (1 / max(n_years, 1e-9)) - 1)


def _max_drawdown(nav: pd.Series) -> float:
    """计算最大回撤 (负数)。"""
    if nav.empty or len(nav) < 2:
        return 0.0
    cummax = nav.cummax()
    dd = (nav / cummax - 1)
    return float(dd.min())


def _sharpe(nav: pd.Series, freq: int = 252) -> float:
    """计算 Sharpe 比率。"""
    vol = _ann_vol(nav, freq)
    if vol == 0:
        return 0.0
    return _ann_return(nav, freq) / vol


def _ann_vol(nav: pd.Series, freq: int = 252) -> float:
    """计算年化波动率。"""
    if nav.empty or len(nav) < 2:
        return 0.0
    rets = nav.pct_change(fill_method=None).dropna()
    return float(rets.std() * np.sqrt(freq)) if not rets.empty else 0.0


def _sortino(nav: pd.Series, freq: int = 252) -> float:
    """计算 Sortino 比率。"""
    if nav.empty:
        return 0.0
    rets = nav.pct_change(fill_method=None).dropna()
    if rets.empty:
        return 0.0
    downside = rets[rets < 0]
    dd = float(downside.std() * np.sqrt(freq)) if not downside.empty else 0.0
    if dd == 0:
        return 0.0
    return _ann_return(nav, freq) / dd
