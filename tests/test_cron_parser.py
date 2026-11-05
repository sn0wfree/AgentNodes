"""Tests for cron_parser.py — 5-field cron expression parser."""

from __future__ import annotations

import sys
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.kernel.scheduled_research.cron_parser import (
    next_cron_trigger,
    parse_cron,
    validate_cron,
)


class TestParseCron(unittest.TestCase):

    def test_every_minute(self) -> None:
        fields = parse_cron("* * * * *")
        self.assertEqual(fields.minutes, set(range(60)))
        self.assertEqual(fields.hours, set(range(24)))
        self.assertEqual(fields.days_of_month, set(range(1, 32)))
        self.assertEqual(fields.months, set(range(1, 13)))
        self.assertEqual(fields.days_of_week, set(range(7)))

    def test_specific_time(self) -> None:
        fields = parse_cron("0 2 * * *")
        self.assertEqual(fields.minutes, {0})
        self.assertEqual(fields.hours, {2})

    def test_step_every_15_minutes(self) -> None:
        fields = parse_cron("*/15 * * * *")
        self.assertEqual(fields.minutes, {0, 15, 30, 45})

    def test_range_hours(self) -> None:
        fields = parse_cron("0 9-17 * * *")
        self.assertEqual(fields.hours, set(range(9, 18)))

    def test_multiple_values(self) -> None:
        fields = parse_cron("0,30 * * * *")
        self.assertEqual(fields.minutes, {0, 30})

    def test_weekday_only(self) -> None:
        fields = parse_cron("0 9 * * 1-5")
        self.assertEqual(fields.days_of_week, {1, 2, 3, 4, 5})

    def test_invalid_field_count(self) -> None:
        with self.assertRaises(ValueError):
            parse_cron("* * * *")

    def test_out_of_bounds_value(self) -> None:
        with self.assertRaises(ValueError):
            parse_cron("60 * * * *")

    def test_out_of_bounds_range(self) -> None:
        with self.assertRaises(ValueError):
            parse_cron("* 24 * * *")

    def test_zero_step_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_cron("*/0 * * * *")


class TestValidateCron(unittest.TestCase):

    def test_valid(self) -> None:
        self.assertTrue(validate_cron("0 2 * * *"))

    def test_invalid(self) -> None:
        self.assertFalse(validate_cron("invalid"))

    def test_wrong_field_count(self) -> None:
        self.assertFalse(validate_cron("* * * *"))


class TestNextCronTrigger(unittest.TestCase):

    def test_next_trigger_every_minute(self) -> None:
        now = time.time()
        next_ts = next_cron_trigger("* * * * *", after=now)
        self.assertGreater(next_ts, now)
        self.assertLess(next_ts, now + 120)

    def test_next_trigger_daily(self) -> None:
        now = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        ts = now.timestamp()
        next_ts = next_cron_trigger("0 2 * * *", after=ts)
        expected = now.replace(hour=2)
        if expected <= now:
            expected += timedelta(days=1)
        self.assertEqual(next_ts, expected.timestamp())

    def test_weekday_only_skips_weekend(self) -> None:
        now = datetime.now()
        # Find a Saturday (5)
        days_ahead = (5 - now.weekday()) % 7
        saturday = now + timedelta(days=days_ahead)
        saturday = saturday.replace(hour=10, minute=0, second=0, microsecond=0)
        next_ts = next_cron_trigger("0 10 * * 1-5", after=saturday.timestamp())
        next_dt = datetime.fromtimestamp(next_ts)
        # Should skip to Monday (0)
        self.assertIn(next_dt.weekday(), {0, 1, 2, 3, 4})

    def test_no_valid_trigger_raises(self) -> None:
        now = datetime.now()
        ts = now.timestamp()
        with self.assertRaises(ValueError):
            next_cron_trigger("0 0 30 2 *", after=ts)


if __name__ == "__main__":
    unittest.main()
