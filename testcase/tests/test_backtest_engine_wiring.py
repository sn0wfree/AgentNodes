"""W10-3: ``config_runner.create_engine`` 必须与旧的直接 ``StrategyEngine`` 等价。

背景：``apps/backtest_engine`` 是 P0-3 的产物（``BacktestEngine`` Protocol +
provider factory），但生产路径 ``config_runner.run_from_yaml`` 一直直接
``StrategyEngine(...)``，让整个包成了无引用的孤岛。接通 factory 的风险在于
**风险参数可能在适配层被丢掉**——那会改变回测数值，而多数测试只比对结构，
不会发现。所以这里先钉住「参数等价」，再允许接线。

断言的是**引擎属性**，不是对象身份：适配器包的引擎与直接构造的引擎在
vol_targeting / trend_filter / stop_loss / risk_free_rate 上必须一致。
"""

from __future__ import annotations

import pytest

from strategy_research.apps.backtest_engine import (
    BacktestEngine,
    get_engine,
)
from strategy_research.apps.backtest_engine import (
    create_engine as create_protocol_engine,
)
from strategy_research.apps.backtest_engine.strategy_engine_adapter import (
    StrategyEngineAdapter,
)
from strategy_research.apps.config_runner import create_engine as create_cfg_engine
from strategy_research.foundation.utils.backtest_config import (
    StopLossConfig,
    TrendFilterConfig,
    VolTargetingConfig,
)
from strategy_research.foundation.utils.strategy_engine import StrategyEngine

RISK_KEYS = ("vol_targeting", "trend_filter", "stop_loss", "risk_free_rate")


def _full_cfg() -> dict:
    """A config that enables every risk control (the worst case for dropping)."""
    return {
        "risk": {
            "vol_targeting": {
                "enabled": True,
                "target_vol": 0.12,
                "lookback": 40,
                "min_scale": 0.4,
                "max_scale": 1.8,
            },
            "trend_filter": {"enabled": True, "ma_window": 150, "bear_exposure": 0.3},
            "stop_loss": {"enabled": True, "threshold": -0.08, "cooldown_weeks": 3},
        },
        "backtest": {"risk_free_rate": 0.025},
    }


def _unwrap(adapter) -> StrategyEngine:
    assert isinstance(adapter, StrategyEngineAdapter)
    return adapter.engine


def test_adapter_forwards_all_risk_constructor_params() -> None:
    """适配器必须把 4 个构造参数原样交给 StrategyEngine。"""
    vt = VolTargetingConfig(enabled=True, target_vol=0.2, lookback=30)
    tf = TrendFilterConfig(enabled=True, ma_window=120, bear_exposure=0.4)
    sl = StopLossConfig(enabled=True, threshold=-0.12, cooldown_weeks=2)

    adapter = create_protocol_engine(
        "strategy",
        vol_targeting=vt,
        trend_filter=tf,
        stop_loss=sl,
        risk_free_rate=0.03,
    )
    engine = _unwrap(adapter)

    assert engine.vol_targeting is vt
    assert engine.trend_filter is tf
    assert engine.stop_loss is sl
    assert engine.risk_free_rate == pytest.approx(0.03)


def test_adapter_defaults_match_plain_engine() -> None:
    """不传参数时，适配器的引擎应与 ``StrategyEngine()`` 等价。"""
    adapter_engine = _unwrap(create_protocol_engine("strategy"))
    plain = StrategyEngine()
    for key in RISK_KEYS:
        assert getattr(adapter_engine, key) == getattr(plain, key), key


def test_config_runner_engine_matches_direct_construction() -> None:
    """``config_runner.create_engine(cfg)`` ≡ 旧实现的直接构造。

    这是本项的核心等价断言：接通 factory 前后，引擎的风险属性必须一致。
    """
    cfg = _full_cfg()

    # 旧实现（W10-3 之前的代码路径，照抄一份作为 oracle）
    risk = cfg["risk"]
    vt_cfg, tf_cfg, sl_cfg = (
        risk["vol_targeting"], risk["trend_filter"], risk["stop_loss"],
    )
    expected = StrategyEngine(
        vol_targeting=VolTargetingConfig(
            enabled=True,
            target_vol=vt_cfg["target_vol"],
            lookback=vt_cfg["lookback"],
            min_scale=vt_cfg["min_scale"],
            max_scale=vt_cfg["max_scale"],
        ),
        trend_filter=TrendFilterConfig(
            enabled=True,
            ma_window=tf_cfg["ma_window"],
            bear_exposure=tf_cfg["bear_exposure"],
        ),
        stop_loss=StopLossConfig(
            enabled=True,
            threshold=sl_cfg["threshold"],
            cooldown_weeks=sl_cfg["cooldown_weeks"],
        ),
        risk_free_rate=cfg["backtest"]["risk_free_rate"],
    )

    actual = _unwrap(create_cfg_engine(cfg))

    assert actual.risk_free_rate == pytest.approx(expected.risk_free_rate)
    for key in ("vol_targeting", "trend_filter", "stop_loss"):
        got = getattr(actual, key)
        want = getattr(expected, key)
        assert (got is None) == (want is None), f"{key}: presence differs"
        if want is not None:
            # dataclass 逐字段相等
            assert got == want, f"{key}: {got!r} != {want!r}"


def test_config_runner_engine_is_protocol_compliant() -> None:
    """返回值必须满足 ``BacktestEngine`` Protocol（runtime_checkable）。"""
    engine = create_cfg_engine({"risk": {}, "backtest": {}})
    assert isinstance(engine, BacktestEngine)
    assert hasattr(engine, "run")


def test_create_engine_does_not_cache_per_run_config() -> None:
    """``create_engine`` 必须每次新建——缓存会把首个配置套用到后续每次回测。"""
    a = _unwrap(create_protocol_engine("strategy", risk_free_rate=0.01))
    b = _unwrap(create_protocol_engine("strategy", risk_free_rate=0.09))
    assert a.risk_free_rate == pytest.approx(0.01)
    assert b.risk_free_rate == pytest.approx(0.09)
    assert a is not b


def test_get_engine_is_still_a_cached_singleton() -> None:
    """``get_engine`` 保持原语义（缓存），供不需要 per-run 配置的调用方使用。"""
    assert get_engine("strategy") is get_engine("strategy")


def test_unknown_provider_is_loud() -> None:
    """未知 provider 必须抛 KeyError，不得静默降级。"""
    with pytest.raises(KeyError):
        create_protocol_engine("nope")


def test_unknown_init_kwarg_is_loud() -> None:
    """适配器不接受的构造参数必须抛 TypeError，不得静默丢弃。"""
    with pytest.raises(TypeError):
        create_protocol_engine("strategy", not_a_real_param=1)
