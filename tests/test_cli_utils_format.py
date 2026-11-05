"""Tests for ``cli.utils.format`` — pure formatters."""

from __future__ import annotations

from agentnodes.interfaces.cli.utils.format import (
    abbreviate_num,
    format_duration,
    format_tokens,
)

# ─── format_duration (ms) ───────────────────────────────────────────────


class TestFormatDurationMs:
    def test_zero_ms(self):
        assert format_duration(0) == "0ms"

    def test_small_ms(self):
        assert format_duration(230) == "230ms"

    def test_999_ms(self):
        assert format_duration(999) == "999ms"

    def test_under_one_second(self):
        # < 1000ms ⇒ ms form
        assert format_duration(999) == "999ms"

    def test_one_second_plus(self):
        assert format_duration(1500) == "1.5s"

    def test_long_seconds(self):
        assert format_duration(45_000) == "45.0s"

    def test_minutes_and_seconds(self):
        # 4m 12s = 252000ms
        assert format_duration(252_000) == "4m 12s"

    def test_hours(self):
        # 1h 02m = 3_720_000ms
        assert format_duration(3_720_000) == "1h 02m"

    def test_none_returns_dash(self):
        assert format_duration(None) == "—"

    def test_negative_returns_dash(self):
        assert format_duration(-100) == "—"

    def test_invalid_returns_dash(self):
        assert format_duration("not a number") == "—"

    def test_nan_returns_dash(self):
        assert format_duration(float("nan")) == "—"


# ─── format_duration (seconds) ───────────────────────────────────────────


class TestFormatDurationSeconds:
    def test_zero(self):
        assert format_duration(0, unit="s") == "0.0s"

    def test_under_one_minute(self):
        assert format_duration(45, unit="s") == "45.0s"

    def test_one_minute(self):
        assert format_duration(60, unit="s") == "1m 00s"

    def test_padded_minutes(self):
        # 4m 12s
        assert format_duration(252, unit="s") == "4m 12s"

    def test_hours(self):
        assert format_duration(3720, unit="s") == "1h 02m"


# ─── format_tokens ───────────────────────────────────────────────────────


class TestFormatTokens:
    def test_zero(self):
        assert format_tokens(0) == "0 tokens"

    def test_small(self):
        assert format_tokens(452) == "452 tokens"

    def test_thousands(self):
        assert format_tokens(1500) == "1.5k tokens"

    def test_k_no_decimal(self):
        assert format_tokens(2000) == "2k tokens"

    def test_millions(self):
        assert format_tokens(3_500_000) == "3.5M tokens"

    def test_billions(self):
        assert format_tokens(4_000_000_000) == "4B tokens"

    def test_none_returns_dash(self):
        assert format_tokens(None) == "—"

    def test_negative_returns_dash(self):
        assert format_tokens(-5) == "—"

    def test_invalid_returns_dash(self):
        assert format_tokens("garbage") == "—"

    def test_boundary_at_1000(self):
        assert format_tokens(999) == "999 tokens"
        assert format_tokens(1000) == "1k tokens"

    def test_boundary_at_1m(self):
        assert format_tokens(999_999) == "1000k tokens"  # quirk: 999.999 → round1 ⇒ 1000
        assert format_tokens(1_000_000) == "1M tokens"


# ─── abbreviate_num ──────────────────────────────────────────────────────


class TestAbbreviateNum:
    def test_zero(self):
        assert abbreviate_num(0) == "0"
        assert abbreviate_num(0, currency="$") == "$0"

    def test_small_int(self):
        assert abbreviate_num(452) == "452"
        assert abbreviate_num(452, currency="$") == "$452"

    def test_negative_small_int(self):
        assert abbreviate_num(-42) == "-42"

    def test_thousands(self):
        assert abbreviate_num(12_400) == "12.4k"
        assert abbreviate_num(12_400, currency="$") == "$12.4k"

    def test_thousands_exact(self):
        # No trailing .0
        assert abbreviate_num(10_000) == "10k"

    def test_millions(self):
        assert abbreviate_num(3_200_000) == "3.2M"

    def test_billions(self):
        assert abbreviate_num(1_500_000_000) == "1.5B"

    def test_fractional_below_one(self):
        # 3 decimals
        assert abbreviate_num(0.003) == "0.003"
        assert abbreviate_num(0.003, currency="$") == "$0.003"

    def test_fractional_above_one(self):
        # ≥1 integer part, |n|<1000 ⇒ rendered as-is
        assert abbreviate_num(452.7) == "453"  # rounds up to int presentation

    def test_none_returns_dash(self):
        assert abbreviate_num(None) == "—"

    def test_invalid_returns_dash(self):
        assert abbreviate_num("oops") == "—"

    def test_negative_thousands(self):
        assert abbreviate_num(-5_000) == "-5k"

    def test_currency_no_value(self):
        # When currency set but value is 0
        assert abbreviate_num(0, currency="€") == "€0"
