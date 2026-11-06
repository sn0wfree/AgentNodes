"""Tests for core.goal.policy — normalize + live-execution rejection.

The policy module is the gatekeeper for LIVE_TRADING_OR_EXECUTION goal text.
Both English and Chinese execution phrases must be rejected (research-only).
"""

from __future__ import annotations

import pytest

from agentnodes.apps.goal.policy import (
    normalize_required_text,
    reject_live_execution_objective,
)

# ─── normalize_required_text ─────────────────────────────────────────────


class TestNormalizeRequiredText:
    def test_strips_whitespace(self):
        assert normalize_required_text("  hello  ", "x") == "hello"

    def test_passes_clean_value(self):
        assert normalize_required_text("hello", "field") == "hello"

    def test_raises_on_empty(self):
        with pytest.raises(ValueError, match="field cannot be empty"):
            normalize_required_text("", "field")

    def test_raises_on_whitespace_only(self):
        with pytest.raises(ValueError, match="field cannot be empty"):
            normalize_required_text("   \t\n", "field")

    def test_field_name_in_error(self):
        with pytest.raises(ValueError, match="objective cannot be empty"):
            normalize_required_text("", "objective")


# ─── reject_live_execution_objective ──────────────────────────────────────


class TestRejectLiveExecutionEnglish:
    """English execution language must be rejected (place/submit/execute/send order)."""

    @pytest.mark.parametrize(
        "text",
        [
            "place an order for AAPL",
            "submit a market order",
            "execute this trade immediately",
            "send order to broker",
            "PLACE ORDER NOW",
            "Submit Order Right Now",
        ],
    )
    def test_rejects_place_submit_execute_send(self, text):
        with pytest.raises(ValueError, match="live trading or execution goals are not supported"):
            reject_live_execution_objective(text)

    @pytest.mark.parametrize(
        "text",
        [
            "buy AAPL now",
            "sell TSLA immediately",
            "long BTC market order",
            "short ETH limit order",
            "buy 100 shares now",
            "sell 1 contract immediately",
        ],
    )
    def test_rejects_buy_sell_short_long(self, text):
        with pytest.raises(ValueError, match="live trading or execution goals are not supported"):
            reject_live_execution_objective(text)


class TestRejectLiveExecutionChinese:
    """Chinese execution language must be rejected."""

    @pytest.mark.parametrize(
        "text",
        [
            "立即下单",
            "马上买",
            "现在买",
            "立即卖",
            "马上卖",
            "现在卖",
            "市价单",
            "限价单",
        ],
    )
    def test_rejects_chinese_execution(self, text):
        with pytest.raises(ValueError, match="live trading or execution goals are not supported"):
            reject_live_execution_objective(text)


class TestAcceptResearchGoals:
    """Pure research goals must pass."""

    @pytest.mark.parametrize(
        "text",
        [
            "研究 A 股动量因子",
            "Investigate momentum in large caps",
            "Build a long-only equity model",
            "Test a pairs trading strategy in simulation",
            "Analyze correlations between AAPL and MSFT",
            "Compare factor portfolios",
            "用 tushare 数据验证 alpha",
            "把策略放到模拟环境跑",
        ],
    )
    def test_accepts_research(self, text):
        """Research-only text is allowed (no execution patterns)."""
        reject_live_execution_objective(text)  # must not raise

    def test_strips_before_check(self):
        """Leading/trailing whitespace is stripped before pattern match."""
        reject_live_execution_objective("  研究动量因子  ")  # must not raise


class TestRejectEdgeCases:
    """Mixed text and edge cases."""

    def test_partial_match_still_rejected(self):
        """Even an execution phrase embedded in research text triggers rejection.

        This is intentional — execution language is unsafe regardless of context.
        """
        with pytest.raises(ValueError):
            reject_live_execution_objective("I want to research and buy AAPL now")

    def test_similar_word_not_rejected(self):
        """Word boundary means 'orderly' (not 'order') should NOT trigger."""
        # "orderly" does not match \border\b because of the ly suffix
        reject_live_execution_objective("Build an orderly factor pipeline")

    def test_english_word_inside_chinese(self):
        with pytest.raises(ValueError):
            reject_live_execution_objective("现在买 AAPL")
