"""P3-B: Tool plugin discovery tests."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.agent.builtin_tools import _discover_tool_plugins
from strategy_research.kernel.agent.tools import ToolRegistry


class TestToolPluginDiscovery:
    def test_discover_with_no_plugins(self):
        """No entry_points registered — registry unchanged."""
        r = ToolRegistry()
        initial_count = len(r)
        # Mock the importlib.metadata.entry_points call inside the function
        with patch("importlib.metadata.entry_points", return_value={}):
            _discover_tool_plugins(r)
        assert len(r) == initial_count

    def test_discover_with_empty_selectable(self):
        """SelectableGroups with no matching group."""
        r = ToolRegistry()
        mock_eps = MagicMock()
        mock_eps.select.return_value = []
        with patch("importlib.metadata.entry_points", return_value=mock_eps):
            _discover_tool_plugins(r)
        assert len(r) == 0

    def test_discover_calls_register_fn(self):
        """Entry point's load() is called with the registry."""
        r = ToolRegistry()

        def _register_plugin(reg):
            from strategy_research.kernel.agent.tools import BaseTool

            class _PluginTool(BaseTool):
                name = "plugin_tool"
                description = "Plugin tool"
                parameters = {}

                def execute(self, **kwargs):
                    return '{"status": "ok"}'

            reg.register(_PluginTool())

        mock_ep = MagicMock()
        mock_ep.name = "test_plugin"
        mock_ep.load.return_value = _register_plugin

        mock_eps = MagicMock()
        mock_eps.select.return_value = [mock_ep]

        with patch("importlib.metadata.entry_points", return_value=mock_eps):
            _discover_tool_plugins(r)

        assert r.get("plugin_tool") is not None

    def test_discover_logs_failure(self):
        """Failed plugin is logged, not raised."""
        r = ToolRegistry()
        mock_ep = MagicMock()
        mock_ep.name = "broken_plugin"
        mock_ep.load.side_effect = RuntimeError("import failed")

        mock_eps = MagicMock()
        mock_eps.select.return_value = [mock_ep]

        # Should not raise
        with patch("importlib.metadata.entry_points", return_value=mock_eps):
            _discover_tool_plugins(r)
        # Registry still empty
        assert len(r) == 0

    def test_discover_dict_eps(self):
        """Works with dict-style entry_points (Python 3.10 fallback)."""
        r = ToolRegistry()

        def _register_plugin(reg):
            from strategy_research.kernel.agent.tools import BaseTool

            class _DictTool(BaseTool):
                name = "dict_tool"
                description = "Dict tool"
                parameters = {}

                def execute(self, **kwargs):
                    return '{"status": "ok"}'

            reg.register(_DictTool())

        mock_ep = MagicMock()
        mock_ep.name = "dict_plugin"
        mock_ep.load.return_value = _register_plugin

        with patch("importlib.metadata.entry_points", return_value={"strategy_research.tools": [mock_ep]}):
            _discover_tool_plugins(r)

        assert r.get("dict_tool") is not None
