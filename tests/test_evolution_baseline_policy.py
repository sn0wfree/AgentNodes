# -*- coding: utf-8 -*-
"""WP-8 批 3b.1：``BaselinePolicy`` 直测（自 rsi_wiring 迁出后的新路径锚）。

镜像 tests/test_golden_eval.py::TestBaselineRollingMedian 的 6 用例打
``apps.evolution.baseline_policy.BaselinePolicy`` 新路径；旧测试的
``rw._baseline_metric`` 锚点保持不动（薄委托等价，双锚防漂移）。
run_golden_eval 的模块属性 patch 依赖 BaselinePolicy 内的函数内懒
import——本文件同时是「懒 import 约束不被重构掉」的守卫。
"""
from __future__ import annotations

import pytest

from agentnodes.apps.evolution.baseline_policy import BaselinePolicy
from agentnodes.kernel.evolution.validation_gate import ReplayOutcome


class TestBaselineRollingMedian:
    def _gw(self, version: str = "gv-0"):
        class _Genome:
            def current_version_id(self) -> str:
                return version

        class _Gw:
            genome = _Genome()

        return _Gw()

    def _policy(self, monkeypatch, values):
        """把 run_golden_eval 换成按序返回 fixed result。

        ``values`` 依次消费；用完后重复最后一个（便于断言稳态）。
        """
        import agentnodes.kernel.evolution.golden_eval as ge

        seq = list(values)
        calls = {"n": 0}

        def fake_run(tasks, *, seed, size=ge.DEFAULT_SAMPLE_SIZE):
            idx = min(calls["n"], len(seq) - 1)
            calls["n"] += 1
            value = seq[idx]
            if value is None:
                return ge.GoldenEvalResult(
                    metric=0.0, tasks_run=0, skipped=True,
                    skip_reason="insufficient valid samples", tasks_selected=10,
                )
            return ge.GoldenEvalResult(
                metric=value, tasks_run=10, tasks_selected=10,
                cost=0.8, cost_samples=5, tokens=1200.0, tokens_samples=5,
            )

        monkeypatch.setattr(ge, "run_golden_eval", fake_run)
        policy = BaselinePolicy(self._gw(), tasks=[object()], seed=0)
        return policy, calls

    def test_median_smooths_single_low_round(self, monkeypatch):
        """单轮低分不得把基线拖低（均值会被拖，中位数不会）。"""
        policy, _ = self._policy(monkeypatch, [1.0, 1.0, 0.2])
        observed = [policy().metric for _ in range(3)]
        assert observed[:2] == [1.0, 1.0]
        assert observed[2] == 1.0, "三轮 [1.0, 1.0, 0.2] 的中位数必须是 1.0"

    def test_baseline_tracks_multi_round_improvement(self, monkeypatch):
        """连续改进后基线跟着上移（旧实现的假阳性根因）。"""
        policy, _ = self._policy(monkeypatch, [0.60, 0.70, 0.80, 0.90, 0.95])
        values = [policy().metric for _ in range(5)]
        assert values[0] == 0.60
        assert values[-1] == 0.80, "5 轮窗口的中位数应是中间观测，而非第一轮"
        assert values[-1] > values[0]

    def test_skipped_round_does_not_pollute_window(self, monkeypatch):
        """skipped 轮次不进窗口：基线不得因无效轮次被覆盖。"""
        policy, _ = self._policy(monkeypatch, [1.0, None, None])
        assert policy().metric == 1.0
        assert policy().metric == 1.0, "skipped 后仍应返回窗口内有效值，而非 None"

    def test_no_valid_round_yet_returns_none(self, monkeypatch):
        policy, _ = self._policy(monkeypatch, [None])
        assert policy() is None, "从无有效样本 → None（Gate 会 escalate）"

    def test_window_carries_cost_and_tokens(self, monkeypatch):
        """基线必须带 cost/tokens，否则成本闸在基线侧恒无样本 → 永跳过。"""
        policy, _ = self._policy(monkeypatch, [0.9])
        out = policy()
        assert isinstance(out, ReplayOutcome)
        assert out.cost == pytest.approx(0.8)
        assert out.cost_samples == 5
        assert out.tokens == pytest.approx(1200.0)
        assert out.tokens_samples == 5

    def test_empty_tasks_returns_none(self):
        assert BaselinePolicy(self._gw(), tasks=[], seed=0)() is None

    def test_window_bounded_at_five(self, monkeypatch):
        """窗口有界：超过 WINDOW_SIZE 的旧观察被挤出。

        7 轮 [0.1..0.5, 0.9, 0.9] 的逐轮中位数序列——第 6 轮起 0.1/0.2
        依次被挤出，证明窗口只保留最近 5 个观察。
        """
        policy, _ = self._policy(
            monkeypatch, [0.1, 0.2, 0.3, 0.4, 0.5, 0.9, 0.9],
        )
        metrics = [policy().metric for _ in range(7)]
        assert metrics == pytest.approx([0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5])

    def test_delegation_from_rsi_wiring_equivalent(self, monkeypatch):
        """薄委托等价：rsi_wiring._baseline_metric 返回 BaselinePolicy 实例。"""
        import agentnodes.interfaces.rsi_wiring as rw
        from agentnodes.apps.evolution.baseline_policy import BaselinePolicy as BP

        import agentnodes.kernel.evolution.golden_eval as ge

        def fake_run(tasks, *, seed, size=ge.DEFAULT_SAMPLE_SIZE):
            return ge.GoldenEvalResult(
                metric=1.0, tasks_run=10, tasks_selected=10,
                cost=0.8, cost_samples=5, tokens=1200.0, tokens_samples=5,
            )

        monkeypatch.setattr(ge, "run_golden_eval", fake_run)
        baseline = rw._baseline_metric(self._gw(), tasks=[object()], seed=0)
        assert isinstance(baseline, BP)
        assert baseline().metric == 1.0
