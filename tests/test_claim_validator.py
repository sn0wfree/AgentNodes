"""Unit tests for the claim validator (truthfulness L2).

See docs/archive/undated/claim-validation-badge-design.md §7.1.

**批次 4d**：指标关键词表默认空（kernel 不再内置量化指标名），
本文件测试的是**量化词表生效时**的行为，因此模块级注册。
"""
from __future__ import annotations

import pytest

from agentnodes.kernel.agent.validators import validate_claims


@pytest.fixture(autouse=True)
def _register_quant_keywords():
    """本模块测试量化指标识别，需先注册量化词表（与生产装配路径一致）。"""
    from agentnodes.apps.agent_plugins.quant_display import (
        QUANT_CLAIM_KEYWORDS,
    )
    from agentnodes.kernel.agent.validators.claim_validator import (
        clear_registered_keywords,
        register_metric_keywords,
    )

    clear_registered_keywords()
    register_metric_keywords(QUANT_CLAIM_KEYWORDS)
    yield
    clear_registered_keywords()


class TestKernelDefaults:
    """kernel 默认无业务词表——这是「删掉 apps/ 也能跑」的直接体现。"""

    def test_no_keywords_by_default(self):
        from agentnodes.kernel.agent.validators.claim_validator import (
            active_metric_keywords,
            clear_registered_keywords,
        )

        clear_registered_keywords()
        assert active_metric_keywords() == []

    def test_no_claims_without_keywords(self):
        """不注册词表时，任何数字都不被当成 metric claim。"""
        from agentnodes.kernel.agent.validators.claim_validator import (
            clear_registered_keywords,
        )

        clear_registered_keywords()
        result = validate_claims(
            "sharpe 是 1.5", tool_result_texts=[]
        )
        assert result.total_claims == 0


class TestFlagsUnverified:
    def test_flags_unverified_sharpe(self):
        """答案里的 sharpe 在 tool 结果中找不到 → unverified."""
        result = validate_claims(
            "V1 的 sharpe 是 1.5，年化 18%。",
            tool_result_texts=["已加载数据 5000 行"],
        )
        assert not result.ok
        assert result.total_claims == 2
        assert "sharpe=1.5" in result.unverified
        assert result.confidence == 0.0

    def test_passes_metric_in_tool_result(self):
        """答案里的数字能在 tool 结果中找到 → verified."""
        result = validate_claims(
            "回测完成，sharpe 为 1.42",
            tool_result_texts=['{"sharpe": 1.42, "annual_return": 0.18}'],
        )
        assert result.ok
        assert "sharpe=1.42" in result.verified
        assert result.unverified == []


class TestIgnoresNonClaims:
    def test_ignores_dates_and_counts(self):
        """日期/计数不是指标 claim → total_claims=0."""
        result = validate_claims(
            "策略创建于 2020-01-01，包含 3 个因子，run_id=20250608。",
            tool_result_texts=[],
        )
        assert result.total_claims == 0
        assert result.ok
        assert result.confidence == 1.0

    def test_empty_claims_ok(self):
        result = validate_claims("我已经创建了策略文件。", [])
        assert result.ok
        assert result.total_claims == 0


class TestTolerance:
    def test_tolerance_comparison(self):
        """tool 结果 1.420，答案 1.42 → 容差内 verified."""
        result = validate_claims(
            "sharpe 1.42",
            tool_result_texts=['{"sharpe": 1.420}'],
        )
        assert result.ok
        assert "sharpe=1.42" in result.verified

    def test_out_of_tolerance_flagged(self):
        """tool 结果 1.5，答案 2.0 → unverified."""
        result = validate_claims(
            "sharpe 2.0",
            tool_result_texts=['{"sharpe": 1.5}'],
        )
        assert not result.ok
        assert "sharpe=2.0" in result.unverified


class TestChineseMetrics:
    def test_chinese_metrics_flagged(self):
        result = validate_claims(
            "该策略年化收益 15%，最大回撤 -8%。",
            tool_result_texts=[],
        )
        assert not result.ok
        assert any("年化" in c and "15%" in c for c in result.unverified)
        assert any("回撤" in c for c in result.unverified)

    def test_chinese_metric_verified_from_tool(self):
        result = validate_claims(
            "年化收益 15%",
            tool_result_texts=["annual_return=0.15"],
        )
        assert result.ok
        assert any("年化" in v for v in result.verified)


class TestMultipleToolResults:
    def test_history_tool_results_count(self):
        """跨轮 tool 结果都参与验证（历史 + 本轮）。"""
        result = validate_claims(
            "V1 sharpe 1.2，V2 sharpe 1.8",
            tool_result_texts=[
                '{"sharpe": 1.2}',  # 历史轮
                '{"sharpe": 1.8}',  # 本轮
            ],
        )
        assert result.ok
        assert len(result.verified) == 2

    def test_partial_verification_confidence(self):
        """部分验证 → confidence 在 0~1 之间."""
        result = validate_claims(
            "sharpe 1.2，年化 25%",
            tool_result_texts=['{"sharpe": 1.2}'],
        )
        assert not result.ok
        assert result.total_claims == 2
        assert result.confidence == 0.5
