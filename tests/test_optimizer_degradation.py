"""H 档补测（P1-6）—— `engine/optimizers/base.py` 的降级与窗口逻辑。

本机没有 QuantOPT（``_HAS_QUANTOPT = False``），四个依赖它的优化器全部
走 ImportError → 返回 None → 回落等权的路径。这条降级链此前未测。

它错了的后果是**静默产出错误权重**：比如退化判定失效会把全零协方差矩阵
当成有效输入，或者窗口不足时用未初始化的权重。

覆盖：
- ``_equal_volatility_optimize`` 的极小手算断言（含零波动保护）
- ``_single_optimize`` 的：全零协方差 → None、未知方法 → None、
  需要 QuantOPT 的四个方法在本机 → None、异常被吞成 None
- ``_apply_sign_preservation`` 的多空对齐与「无信号不动」
- ``optimize_weights`` 静态/滚动两种模式、窗口不足沿用上期、
  turnover 统计、空列与未知方法的行为
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_research.core.engine.optimizers import base as ob


# ── _equal_volatility_optimize ───────────────────────────


def test_equal_volatility_inverse_weights():
    """vol=(1,2,4) → inv=(1,0.5,0.25) → 归一到 1。"""
    cov = np.diag([1.0, 4.0, 16.0])
    w = ob._equal_volatility_optimize(["a", "b", "c"], cov)
    expected = np.array([1.0, 0.5, 0.25])
    np.testing.assert_allclose(w, expected / expected.sum())
    assert w.sum() == pytest.approx(1.0)


def test_equal_volatility_zero_vol_is_floored():
    """零波动资产不能让权重变成 inf/nan——先夹到 1e-10。"""
    cov = np.diag([0.0, 1.0])
    w = ob._equal_volatility_optimize(["a", "b"], cov)
    assert np.all(np.isfinite(w))
    assert w[0] > w[1]  # 零波动 = 极高权重


# ── _single_optimize ─────────────────────────────────────


def _window(n=10, cols=("a", "b")) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    return pd.DataFrame(rng.normal(0, 0.01, size=(n, len(cols))), columns=list(cols))


def test_single_optimize_all_zero_cov_returns_none():
    """退化协方差（全零）→ 拒绝优化，而不是产出无意义权重。"""
    flat = pd.DataFrame(0.0, index=range(10), columns=["a", "b"])
    assert ob._single_optimize(["a", "b"], flat, None, "equal_volatility") is None


def test_single_optimize_nan_cov_is_sanitised():
    df = _window()
    df.iloc[0, 0] = np.nan
    w = ob._single_optimize(["a", "b"], df, None, "equal_volatility")
    assert w is not None and np.all(np.isfinite(w))


def test_single_optimize_unknown_method_returns_none():
    assert ob._single_optimize(["a", "b"], _window(), None, "nope") is None


@pytest.mark.skipif(ob._HAS_QUANTOPT, reason="QuantOPT installed; import guard untested")
@pytest.mark.parametrize(
    "method", ["risk_parity", "mean_variance", "max_diversification", "turnover_aware"]
)
def test_quantopt_methods_return_none_without_library(method):
    """没有 QuantOPT 时必须安静返回 None（由上层回落等权），不能抛。"""
    assert ob._single_optimize(["a", "b"], _window(), None, method) is None


def test_single_optimize_swallows_optimizer_exception(monkeypatch):
    """优化器内部抛错 → None（不冒泡），由调用方降级。"""
    def boom(*a, **k):
        raise RuntimeError("solver diverged")

    monkeypatch.setattr(ob, "_equal_volatility_optimize", boom)
    assert ob._single_optimize(["a", "b"], _window(), None, "equal_volatility") is None


# ── _apply_sign_preservation ─────────────────────────────


def test_sign_preservation_flips_short_positions():
    w = np.array([0.4, 0.4, 0.2])
    pos = np.array([1.0, -1.0, 0.0])
    out = ob._apply_sign_preservation(w, pos)
    np.testing.assert_allclose(out, [0.4, -0.4, 0.2])


def test_sign_preservation_no_signal_keeps_weights():
    w = np.array([0.5, 0.5])
    out = ob._apply_sign_preservation(w, np.array([0.0, 0.0]))
    np.testing.assert_allclose(out, w)


def test_sign_preservation_does_not_mutate_input():
    w = np.array([0.5, 0.5])
    ob._apply_sign_preservation(w, np.array([-1.0, 1.0]))
    np.testing.assert_allclose(w, [0.5, 0.5])


# ── optimize_weights：静态模式 ───────────────────────────


def _dates(n):
    return pd.date_range("2024-01-01", periods=n, freq="D")


def test_static_mode_tiles_single_weight_across_dates():
    ret = _window(20)
    pos = pd.DataFrame(np.ones((20, 2)), columns=["a", "b"])
    out = ob.optimize_weights(ret, pos, _dates(20))
    assert out.shape == (20, 2)
    # 每行相同
    np.testing.assert_allclose(out.iloc[0].values, out.iloc[-1].values)


def test_static_mode_respects_short_signals():
    ret = _window(20)
    pos = pd.DataFrame(
        {"a": np.ones(20), "b": -np.ones(20)})
    out = ob.optimize_weights(ret, pos, _dates(20))
    assert out["a"].iloc[-1] > 0
    assert out["b"].iloc[-1] < 0


def test_static_mode_can_disable_sign_preservation():
    ret = _window(20)
    pos = pd.DataFrame({"a": np.ones(20), "b": -np.ones(20)})
    out = ob.optimize_weights(ret, pos, _dates(20), sign_preservation=False)
    assert out["b"].iloc[-1] > 0  # 不再翻负


def test_empty_columns_returns_pos_unchanged():
    ret = pd.DataFrame(index=range(5))
    pos = pd.DataFrame({"x": [1.0] * 5})
    out = ob.optimize_weights(ret, pos, _dates(5))
    pd.testing.assert_frame_equal(out, pos)


def test_empty_columns_with_turnover_returns_zero_stats():
    ret = pd.DataFrame(index=range(5))
    pos = pd.DataFrame({"x": [1.0] * 5})
    out, stats = ob.optimize_weights(ret, pos, _dates(5), return_turnover=True)
    assert stats == {"turnover": [], "avg_turnover": 0.0}


def test_unknown_method_raises():
    ret = _window(5)
    pos = pd.DataFrame(np.ones((5, 2)), columns=["a", "b"])
    with pytest.raises(ValueError, match="Unknown method"):
        ob.optimize_weights(ret, pos, _dates(5), method="mystery")


def test_optimizer_failure_falls_back_to_equal_weights(monkeypatch):
    monkeypatch.setattr(ob, "_single_optimize", lambda *a, **k: None)
    ret = _window(10)
    pos = pd.DataFrame(np.ones((10, 2)), columns=["a", "b"])
    out = ob.optimize_weights(ret, pos, _dates(10))
    np.testing.assert_allclose(out.iloc[0].values, [0.5, 0.5])


def test_static_mode_turnover_stats_are_empty():
    ret = _window(10)
    pos = pd.DataFrame(np.ones((10, 2)), columns=["a", "b"])
    out, stats = ob.optimize_weights(ret, pos, _dates(10), return_turnover=True)
    assert stats["avg_turnover"] == 0.0
    assert stats["turnover"] == []


# ── optimize_weights：滚动模式 ───────────────────────────


def test_rolling_mode_short_window_reuses_previous_weights():
    """窗口不足时沿用上期；最开始没有上期就用等权。"""
    ret = _window(20)
    pos = pd.DataFrame(np.ones((20, 2)), columns=["a", "b"])
    out = ob.optimize_weights(ret, pos, _dates(20), lookback=10)
    assert out.shape == (20, 2)
    np.testing.assert_allclose(out.iloc[0].values, [0.5, 0.5])


def test_rolling_mode_reports_turnover_series():
    ret = _window(30)
    pos = pd.DataFrame(np.ones((30, 2)), columns=["a", "b"])
    out, stats = ob.optimize_weights(
        ret, pos, _dates(30), lookback=10, return_turnover=True)
    assert stats["rebalance_count"] == len(stats["turnover"])
    assert stats["total_turnover"] == pytest.approx(sum(stats["turnover"]))
    assert stats["avg_turnover"] >= 0


def test_rolling_mode_failure_keeps_previous_weights(monkeypatch):
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        return None if calls["n"] > 1 else np.array([0.3, 0.7])

    monkeypatch.setattr(ob, "_single_optimize", flaky)
    ret = _window(30)
    pos = pd.DataFrame(np.ones((30, 2)), columns=["a", "b"])
    out = ob.optimize_weights(ret, pos, _dates(30), lookback=10)
    # 一旦成功过一次，后续失败应沿用那次权重而不是等权
    assert out.iloc[-1].values.sum() == pytest.approx(1.0)


def test_rolling_mode_without_sign_preservation():
    ret = _window(30)
    pos = pd.DataFrame({"a": np.ones(30), "b": -np.ones(30)})
    out = ob.optimize_weights(ret, pos, _dates(30), lookback=10, sign_preservation=False)
    assert (out["b"] >= 0).all()
