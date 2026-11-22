# -*- coding: utf-8 -*-
"""C8：两-sided 判定单点 `_two_sided_verdict` 的边界契约测试。

既有门测试的探针都在阈值内部（threshold/2）或外部（threshold*2）——
**exact ±threshold 等值点今天无覆盖**。本文件钉住严格不等式：
等于阈值 → 不触发该分支（reject 需严格大于、escalate 需严格小于）。
"""
from __future__ import annotations

import pytest

from agentnodes.kernel.evolution.validation_gate import _two_sided_verdict


class TestTwoSidedVerdict:
    def test_exact_threshold_is_pass_not_reject(self):
        """degradation == +threshold → 不触发 reject（严格 >）。"""
        assert _two_sided_verdict(0.20, 0.20) == "pass"

    def test_exact_negative_threshold_is_pass_not_escalate(self):
        """degradation == -threshold → 不触发 escalate（严格 <）。"""
        assert _two_sided_verdict(-0.20, 0.20) == "pass"

    def test_beyond_threshold_rejects(self):
        assert _two_sided_verdict(0.21, 0.20) == "reject"

    def test_beyond_negative_threshold_escalates(self):
        assert _two_sided_verdict(-0.21, 0.20) == "escalate"

    def test_within_threshold_passes(self):
        assert _two_sided_verdict(0.0, 0.20) == "pass"
        assert _two_sided_verdict(0.10, 0.20) == "pass"

    def test_zero_threshold_one_sided_extremes(self):
        """threshold=0 的退化值域（同号同量级）：>0 拒、<0 升级、0 过。"""
        assert _two_sided_verdict(0.001, 0.0) == "reject"
        assert _two_sided_verdict(-0.001, 0.0) == "escalate"
        assert _two_sided_verdict(0.0, 0.0) == "pass"

    @pytest.mark.parametrize("threshold", [0.20, 0.30, 0.15])
    def test_three_gate_thresholds_share_one_verdict_shape(self, threshold):
        """held-in / cost / held-out 三个阈值面共用同一判定形状（C8 单点）。"""
        assert _two_sided_verdict(threshold + 1e-9, threshold) == "reject"
        assert _two_sided_verdict(-(threshold + 1e-9), threshold) == "escalate"
        assert _two_sided_verdict(threshold, threshold) == "pass"
