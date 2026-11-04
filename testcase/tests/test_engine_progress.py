"""Tests for engine backtest progress lines (progress_every) — the
stdout progress output that drives run.log liveness in the backgrounded
mode."""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.apps.engine.base import BaseEngine


def _make_engine() -> BaseEngine:
    from strategy_research.apps.engine.base import BaseEngine as _BE

    class _Simple(_BE):
        def can_execute(self, symbol, direction, bar):
            return True

        def round_size(self, raw_size, price):
            return max(round(raw_size, 2), 0.0)

        def calc_commission(self, size, price, direction, is_open):
            return 0.0

        def apply_slippage(self, price, direction):
            return price

    engine = _Simple({})
    # stub the heavy bits: alignment returns canned structures
    import pandas as pd

    engine._align = MagicMock(
        return_value=(
            pd.DatetimeIndex(pd.date_range("2024-01-01", periods=150, freq="D")),
            MagicMock(),
            pd.DataFrame(columns=["AAA", "BBB"]),
            MagicMock(),
        )
    )
    engine._calc_equity = MagicMock(return_value=1_000_000.0)
    engine._rebalance = MagicMock()
    engine.on_bar = MagicMock()
    return engine


class TestEngineProgress(unittest.TestCase):

    def test_progress_every_prints_phase_and_bar_lines(self) -> None:
        engine = _make_engine()

        data_map = {c: MagicMock() for c in ("AAA", "BBB")}
        signal_map = {c: MagicMock() for c in ("AAA", "BBB")}
        buf = io.StringIO()
        with redirect_stdout(buf):
            engine.run_backtest(
                data_map, signal_map, ["AAA", "BBB"],
                progress_every=10,
            )
        out = buf.getvalue()
        self.assertIn("[backtest] align done: 150 bars, 2 codes", out)
        self.assertIn("[backtest] bar 1/150", out)
        self.assertIn("[backtest] metrics done", out)

    def test_progress_none_is_silent(self) -> None:
        engine = _make_engine()

        data_map = {c: MagicMock() for c in ("AAA",)}
        signal_map = {c: MagicMock() for c in ("AAA",)}
        buf = io.StringIO()
        with redirect_stdout(buf):
            engine.run_backtest(data_map, signal_map, ["AAA"])
        self.assertEqual(buf.getvalue(), "")

    def test_auto_frequency_caps_at_100_lines(self) -> None:
        """progress_every=0 → 自动频率（≤100 行），即使 bar 数很大."""
        engine = _make_engine()
        import pandas as pd

        engine._align = MagicMock(
            return_value=(
                pd.DatetimeIndex(
                    pd.date_range("2020-01-01", periods=10_000, freq="D")
                ),
                MagicMock(), pd.DataFrame(columns=["AAA"]), MagicMock(),
            )
        )
        data_map = {c: MagicMock() for c in ("AAA",)}
        signal_map = {c: MagicMock() for c in ("AAA",)}
        buf = io.StringIO()
        with redirect_stdout(buf):
            engine.run_backtest(data_map, signal_map, ["AAA"], progress_every=0)
        lines = [l for l in buf.getvalue().splitlines() if l.startswith("[backtest] bar ")]
        self.assertLessEqual(len(lines), 100)
        self.assertEqual(lines[0], "[backtest] bar 1/10000")


if __name__ == "__main__":
    unittest.main()
