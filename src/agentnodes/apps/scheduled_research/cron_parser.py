"""Cron parser — 5-field cron expression parser.

Supports: min(0-59) hour(0-23) dom(1-31) month(1-12) dow(0-6)
Operators: * (any), */n (every n), bare numbers
DOM/Month/DOW are AND semantics.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import NamedTuple


class CronFields(NamedTuple):
    """Parsed cron fields."""
    minutes: set[int]
    hours: set[int]
    days_of_month: set[int]
    months: set[int]
    days_of_week: set[int]


def parse_cron(expr: str) -> CronFields:
    """Parse a 5-field cron expression.

    Args:
        expr: Cron string like "0 2 * * *" or "*/15 9-17 * * 1-5"

    Returns:
        CronFields with sets of valid values for each field.

    Raises:
        ValueError: If expression is invalid.
    """
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Cron expression must have 5 fields, got {len(parts)}: {expr}")

    return CronFields(
        minutes=_parse_field(parts[0], 0, 59),
        hours=_parse_field(parts[1], 0, 23),
        days_of_month=_parse_field(parts[2], 1, 31),
        months=_parse_field(parts[3], 1, 12),
        days_of_week=_parse_field(parts[4], 0, 6),
    )


def _parse_field(field_str: str, min_val: int, max_val: int) -> set[int]:
    """Parse a single cron field into a set of valid values."""
    values: set[int] = set()

    for part in field_str.split(","):
        part = part.strip()
        if part == "*":
            values.update(range(min_val, max_val + 1))
        elif "*/ " in part or part.startswith("*/"):
            # */n format
            step_str = part.split("/")[1]
            step = int(step_str)
            if step <= 0:
                raise ValueError(f"Step must be positive: {part}")
            values.update(range(min_val, max_val + 1, step))
        elif "-" in part:
            # Range: a-b
            start_str, end_str = part.split("-", 1)
            start = int(start_str)
            end = int(end_str)
            if start < min_val or end > max_val or start > end:
                raise ValueError(f"Range {part} out of bounds [{min_val}-{max_val}]")
            values.update(range(start, end + 1))
        else:
            # Single number
            val = int(part)
            if val < min_val or val > max_val:
                raise ValueError(f"Value {val} out of bounds [{min_val}-{max_val}]")
            values.add(val)

    return values


def next_cron_trigger(expr: str, after: float | None = None) -> float:
    """Calculate the next trigger time for a cron expression.

    Args:
        expr: 5-field cron expression.
        after: Timestamp to calculate after (default: now).

    Returns:
        Epoch timestamp of next trigger.
    """
    fields = parse_cron(expr)
    t = datetime.fromtimestamp(after if after is not None else time.time())
    # Start from next minute
    t = t.replace(second=0, microsecond=0) + timedelta(minutes=1)

    # Search forward (max 366 days to handle yearly patterns)
    for _ in range(366 * 24 * 60):
        if (
            t.minute in fields.minutes
            and t.hour in fields.hours
            and t.day in fields.days_of_month
            and t.month in fields.months
            and t.weekday() in fields.days_of_week  # Monday=0, Sunday=6
        ):
            return t.timestamp()
        t += timedelta(minutes=1)

    raise ValueError(f"No valid trigger found within 366 days for cron: {expr}")


def validate_cron(expr: str) -> bool:
    """Validate a cron expression without computing next trigger."""
    try:
        parse_cron(expr)
        return True
    except (ValueError, IndexError):
        return False
