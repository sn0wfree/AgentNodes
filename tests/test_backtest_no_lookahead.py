"""apps.algo.backtest.evaluate() — 调仓时点回归测试。

（自 tests/test_prepare_no_lookahead.py 迁移，2026-11-14：算法随 C 方案
提取为独立模块（docs/design/prepare-algo-kernel-extract-20261114.md），
importlib 文件加载 → 正常 import，compute_factors 模块桩 → factor_frames
参数注入。两条时序断言自模板时代起一字未动。
2026-11-16：该模块自 ``kernel/algo`` 迁到 ``apps/algo``——它含业务名词，
住 kernel 会破「kernel 零业务名词」门禁；import 随之改。）

钉住"收盘调仓"约定:用第 i 日数据(因子分数基于 close[i])选出的权重
不得赚取第 i 日收益(close[i]/close[i-1]-1),否则构成 1 根 K 线前视
偏差。调仓日损益归旧权重,新权重自下一根 K 线生效。

构造:常数因子(恒选 AAA)+ 单日 +50% 跳价。
- 跳价落在调仓日(i=20):修复后不得进 NAV(旧代码会赚到 → 用例失败)。
- 跳价落在调仓日次日(i=21):必须进 NAV(防止修成"永不建仓")。
"""
from __future__ import annotations

import pandas as pd

from agentnodes.apps.algo import evaluate

REBALANCE_FREQ = 20


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
    """evaluate() with an injected constant factor that always ranks AAA first."""
    factor = pd.DataFrame({"AAA": 1.0, "BBB": 0.0}, index=prices.index)

    params = {"top_n": 1, "max_weight": 1.0, "rebalance_freq": REBALANCE_FREQ}
    exprs = [{"factor_name": "const", "factor_code": "1", "weight": 1.0}]
    return evaluate(params, exprs, "equal", {"prices": prices},
                    factor_frames={"const": factor})


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
