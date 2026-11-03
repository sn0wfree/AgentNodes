"""P3-B extended: Tool plugin discovery edge cases tests."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.agent.builtin_tools import _discover_tool_plugins
from strategy_research.kernel.agent.tools import ToolRegistry, BaseTool


class _TestTool(BaseTool):
    def __init__(self, name: str):
        self.name = name
        self.description = f"Test {name}"
        self.parameters = {}
        self.brief = ""
        self.effects = set()
        self.repeatable = False
        self.strict = False
        self.category = ""

    def execute(self, **kwargs):
        return '{"status": "ok"}'


class TestToolPluginDiscoveryExtended:
    def test_multiple_plugins_discovered(self):
        """Multiple entry points are all loaded."""
        r = ToolRegistry()

        def _register_a(reg):
            reg.register(_TestTool("plugin_a"))

        def _register_b(reg):
            reg.register(_TestTool("plugin_b"))

        ep_a = MagicMock()
        ep_a.name = "plugin_a"
        ep_a.load.return_value = _register_a

        ep_b = MagicMock()
        ep_b.name = "plugin_b"
        ep_b.load.return_value = _register_b

        mock_eps = MagicMock()
        mock_eps.select.return_value = [ep_a, ep_b]

        with patch("importlib.metadata.entry_points", return_value=mock_eps):
            _discover_tool_plugins(r)

        assert r.get("plugin_a") is not None
        assert r.get("plugin_b") is not None
        assert len(r) == 2

    def test_plugin_failure_doesnt_block_others(self):
        """One failing plugin doesn't prevent others from loading."""
        r = ToolRegistry()

        def _register_ok(reg):
            reg.register(_TestTool("ok_tool"))

        ep_ok = MagicMock()
        ep_ok.name = "ok_plugin"
        ep_ok.load.return_value = _register_ok

        ep_bad = MagicMock()
        ep_bad.name = "bad_plugin"
        ep_bad.load.side_effect = RuntimeError("import error")

        mock_eps = MagicMock()
        mock_eps.select.return_value = [ep_bad, ep_ok]

        with patch("importlib.metadata.entry_points", return_value=mock_eps):
            _discover_tool_plugins(r)

        assert r.get("ok_tool") is not None
        assert len(r) == 1

    def test_plugin_returns_none(self):
        """Plugin that returns None is caught by exception handler."""
        r = ToolRegistry()
        ep = MagicMock()
        ep.name = "none_plugin"
        ep.load.return_value = None  # returns None instead of callable

        mock_eps = MagicMock()
        mock_eps.select.return_value = [ep]

        # Calling None() raises TypeError, caught by the try/except
        with patch("importlib.metadata.entry_points", return_value=mock_eps):
            _discover_tool_plugins(r)
        # Registry unchanged — error was logged, not raised
        assert len(r) == 0

    def test_python39_dict_fallback(self):
        """entry_points returns dict (Python 3.9 style)."""
        r = ToolRegistry()

        def _register(reg):
            reg.register(_TestTool("dict_tool"))

        ep = MagicMock()
        ep.name = "dict_ep"
        ep.load.return_value = _register

        # Dict-style entry_points
        with patch("importlib.metadata.entry_points", return_value={"strategy_research.tools": [ep]}):
            _discover_tool_plugins(r)

        assert r.get("dict_tool") is not None

    def test_no_matching_group_in_dict(self):
        """Dict entry_points with no matching group."""
        r = ToolRegistry()
        with patch("importlib.metadata.entry_points", return_value={"other.group": []}):
            _discover_tool_plugins(r)
        assert len(r) == 0
