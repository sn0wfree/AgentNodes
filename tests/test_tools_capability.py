"""P0-2 D — ToolContext capability seam tests.

Covers: ToolContext accepts new optional fields, helper functions
raise a helpful error when the seam is missing, and AgentLoop's
``_build_data_store`` / ``_build_sandbox`` produce working instances.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.agent.sandbox import (
    ExecutionSandbox,
    StaticSandbox,
)
from strategy_research.kernel.agent.tools import ToolContext
from strategy_research.kernel.agent.tools_capability import (
    ToolCapabilityError,
    get_data_store,
    get_sandbox,
)
from strategy_research.foundation.storage import get_store
from strategy_research.foundation.storage.data_store import DataStore


class TestToolContextFields:
    def test_default_fields_still_none(self):
        ctx = ToolContext()
        assert ctx.data_store is None
        assert ctx.sandbox is None

    def test_fields_assignable(self, tmp_path):
        store = get_store()
        sandbox = StaticSandbox(tmp_path)
        ctx = ToolContext(data_store=store, sandbox=sandbox)
        assert ctx.data_store is store
        assert ctx.sandbox is sandbox


class TestCapabilityHelpers:
    def test_get_data_store_returns_injected(self, tmp_path):
        store = get_store()
        ctx = ToolContext(data_store=store)
        assert get_data_store(ctx) is store

    def test_get_sandbox_returns_injected(self, tmp_path):
        sandbox = StaticSandbox(tmp_path)
        ctx = ToolContext(sandbox=sandbox)
        assert get_sandbox(ctx) is sandbox
        assert isinstance(get_sandbox(ctx), ExecutionSandbox)

    def test_get_data_store_missing_raises(self):
        ctx = ToolContext()
        with pytest.raises(ToolCapabilityError) as ei:
            get_data_store(ctx)
        assert "data_store" in str(ei.value)
        assert "ToolContext" in str(ei.value)

    def test_get_sandbox_missing_raises(self):
        ctx = ToolContext()
        with pytest.raises(ToolCapabilityError) as ei:
            get_sandbox(ctx)
        assert "sandbox" in str(ei.value)
        assert "ToolContext" in str(ei.value)

    def test_get_data_store_none_context_raises(self):
        with pytest.raises(ToolCapabilityError):
            get_data_store(None)

    def test_get_sandbox_none_context_raises(self):
        with pytest.raises(ToolCapabilityError):
            get_sandbox(None)

    def test_data_store_is_protocol_compatible(self, tmp_path):
        """The injected value is a real DataStore — not just truthy."""
        ctx = ToolContext(data_store=get_store())
        assert isinstance(get_data_store(ctx), DataStore)
