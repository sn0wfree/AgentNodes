"""Tests for Stage 5 - ThinkingSpinner verb pool + StatusHeader iter count."""
from __future__ import annotations

from unittest import mock

from strategy_research.interfaces.cli.tui.widgets.status_header import StatusHeader
from strategy_research.interfaces.cli.tui.widgets.thinking_spinner import _VERB_POOL, ThinkingSpinner


class TestThinkingSpinnerVerbPool:
    def test_start_default_verb_is_first_in_pool(self):
        spinner = ThinkingSpinner()
        with mock.patch.object(spinner, "set_interval", return_value=None), \
             mock.patch.object(spinner, "update"):
            spinner.start()
        assert spinner._verb == _VERB_POOL[0]

    def test_start_with_explicit_verb(self):
        spinner = ThinkingSpinner()
        with mock.patch.object(spinner, "set_interval", return_value=None), \
             mock.patch.object(spinner, "update"):
            spinner.start(verb="custom")
        assert spinner._verb == "custom"

    def test_rotate_verb_cycles_through_pool(self):
        spinner = ThinkingSpinner()
        spinner._verb_idx = 0
        spinner._rotate_verb()
        assert spinner._verb == _VERB_POOL[1]
        assert spinner._verb_idx == 1
        spinner._verb_idx = len(_VERB_POOL) - 1
        spinner._rotate_verb()
        assert spinner._verb_idx == 0
        assert spinner._verb == _VERB_POOL[0]

    def test_stop_cancels_verb_timer(self):
        spinner = ThinkingSpinner()
        verb_timer = mock.MagicMock()
        spinner._verb_timer = verb_timer
        spinner._timer = mock.MagicMock()
        spinner.stop()
        verb_timer.cancel.assert_called_once()
        assert spinner._verb_timer is None


class TestStatusHeaderIterCount:
    def test_update_status_accepts_iter_count(self):
        header = StatusHeader()
        with mock.patch.object(header, "update"):
            header.update_status(iter_count=3, iter_max=10)
        assert header._iter_count == 3
        assert header._iter_max == 10

    def test_refresh_shows_iter_when_max_set(self):
        header = StatusHeader()
        header._iter_count = 2
        header._iter_max = 5
        with mock.patch.object(header, "update") as m:
            header._refresh()
        content = m.call_args.args[0]
        assert "iter 2/5" in content

    def test_refresh_hides_iter_when_max_zero(self):
        header = StatusHeader()
        header._iter_max = 0
        with mock.patch.object(header, "update") as m:
            header._refresh()
        content = m.call_args.args[0]
        assert "iter" not in content
