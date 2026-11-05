"""templates/prepare.py evaluate() — 调仓时点回归测试。

钉住"收盘调仓"约定:用第 i 日数据(因子分数基于 close[i])选出的权重
不得赚取第 i 日收益(close[i]/close[i-1]-1),否则构成 1 根 K 线前视
偏差。调仓日损益归旧权重,新权重自下一根 K 线生效。

构造:常数因子(恒选 AAA)+ 单日 +50% 跳价。
- 跳价落在调仓日(i=20):修复后不得进 NAV(旧代码会赚到 → 用例失败)。
- 跳价落在调仓日次日(i=21):必须进 NAV(防止修成"永不建仓")。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"

REBALANCE_FREQ = 20


def _load_prepare():
    spec = importlib.util.spec_from_file_location(
        "prepare_no_lookahead_fixture", TEMPLATES / "prepare.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _step_prices(cross_day: int, n_days: int = 80) -> pd.DataFrame:
    """AAA 价格阶梯:cross_day 起 100 → 150 且不再回落。

    于是 cross_day 当日收益 = +50%,cross_day+1 起收益为 0。
    (不能用单日尖峰:往返的下跌腿会落在次日,污染判别。)
    """
    idx = pd.bdate_range("2024-01-01", periods=n_days)
    df = pd.DataFrame({"AAA": 100.0, "BBB": 100.0}, index=idx)
    df.loc[df.index[cross_day]:, "AAA"] = 150.0
    return df


def _evaluate(prices: pd.DataFrame) -> dict:
    """evaluate() with a stubbed factor that always ranks AAA first."""
    mod = _load_prepare()
    factor = pd.DataFrame({"AAA": 1.0, "BBB": 0.0}, index=prices.index)
    mod.compute_factors = lambda _prices, _exprs: {"const": factor}

    params = {"top_n": 1, "max_weight": 1.0, "rebalance_freq": REBALANCE_FREQ}
    exprs = [{"factor_name": "const", "factor_code": "1", "weight": 1.0}]
    return mod.evaluate(params, exprs, "equal", {"prices": prices})


def test_spike_on_rebalance_day_is_not_earned() -> None:
    # 阶梯落在第 20 日(首个调仓日):当日 +50% 归旧权重(空仓)。
    prices = _step_prices(REBALANCE_FREQ)

    metrics = _evaluate(prices)

    # 旧代码:新权重当日生效,+50% 进入 NAV → 本断言失败(前视回归)。
    assert metrics["ann_return"] == 0.0, (
        "调仓日收益被计入新权重:1 根 K 线前视偏差回归"
    )
    assert metrics["calmar"] == 0.0
    # trades = 实际变动持仓数。调仓时点修正后，首次建仓与后续调仓
    # （i=20/40/60）各自产生真实的持仓变动记录 → ≥1。
    # 关键是「建过仓」而不是「恰好 1 笔」。
    assert metrics["trades"] >= 1


def test_spike_after_rebalance_day_is_earned() -> None:
    # 阶梯落在第 21 日(调仓次日):新权重已生效,+50% 必须计收。
    prices = _step_prices(REBALANCE_FREQ + 1)

    metrics = _evaluate(prices)

    # 防止过度修正:调仓次日起新权重必须正常计收。
    assert metrics["ann_return"] > 1.0
    assert metrics["sharpe"] > 0.0  # 净值单边上涨 → 回撤为 0,calmar 恒 0,用 sharpe
