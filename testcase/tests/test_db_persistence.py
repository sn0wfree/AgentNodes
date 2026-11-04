"""Regression tests for apps/db.py data-persistence helpers.

Covers the fixed `save_weight_history` / `save_nav_history` NameError
(the DataFrame result was discarded before referencing ``FROM df``),
plus the general INSERT OR REPLACE semantics against a temp workspace.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from strategy_research.apps import db


@pytest.fixture
def ws(tmp_path) -> Path:
    assert db.init_db(tmp_path)
    return tmp_path


def test_save_weight_history_persists_rows(ws: Path) -> None:
    """Regression: previously raised NameError (df discarded), so the
    weight history was never written — backtest data silently lost."""
    history = [
        (pd.Timestamp("2026-01-01"), {"AAPL": 0.5, "MSFT": 0.5}),
        (pd.Timestamp("2026-01-02"), {"AAPL": 1.0}),
    ]
    assert db.save_weight_history(ws, "strat_a", "run_001", history) is True

    conn = db.get_connection(ws)
    try:
        rows = conn.execute(
            "SELECT strategy_name, run, date, asset_code, weight "
            "FROM weight_history ORDER BY date, asset_code"
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 3
    assert rows[0][0] == "strat_a" and rows[0][1] == "run_001"
    assert rows[0][3] == "AAPL" and rows[0][4] == 0.5
    assert rows[1][3] == "MSFT" and rows[1][4] == 0.5
    assert rows[2][3] == "AAPL" and rows[2][4] == 1.0


def test_save_weight_history_empty_returns_true(ws: Path) -> None:
    assert db.save_weight_history(ws, "s", "r", []) is True


def test_save_nav_history_persists_rows(ws: Path) -> None:
    """Regression: same discarded-DataFrame NameError as weight history."""
    nav = pd.Series(
        [1.0, 1.1, 1.2],
        index=[pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-02"),
               pd.Timestamp("2026-01-03")],
    )
    assert db.save_nav_history(ws, "strat_b", "run_002", nav) is True

    conn = db.get_connection(ws)
    try:
        rows = conn.execute(
            "SELECT strategy_name, run, nav FROM nav_history "
            "ORDER BY date"
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 3
    assert rows[0] == ("strat_b", "run_002", 1.0)
    assert rows[2] == ("strat_b", "run_002", 1.2)


def test_save_nav_history_idempotent(ws: Path) -> None:
    """INSERT OR REPLACE keeps latest NAV per (strategy, run, date)."""
    nav = pd.Series(
        [1.0], index=[pd.Timestamp("2026-01-01")],
    )
    assert db.save_nav_history(ws, "s", "r", nav) is True
    assert db.save_nav_history(ws, "s", "r", nav) is True

    conn = db.get_connection(ws)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM nav_history WHERE strategy_name='s'"
        ).fetchone()[0]
    finally:
        conn.close()

    assert count == 1
