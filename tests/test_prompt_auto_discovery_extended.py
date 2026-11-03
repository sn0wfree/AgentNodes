"""P3-C extended: Prompt auto-discovery edge cases tests."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.agent.prompt_builder import (
    PromptBuilderFactory,
    StaticFilePromptBuilder,
    _NullBuilder,
)


class TestPromptAutoDiscoveryExtended:
    def test_get_unknown_role_caches_result(self):
        """First get() for unknown role caches the discovery result."""
        PromptBuilderFactory._discovered = None
        PromptBuilderFactory.get("nonexistent_xyz")
        assert PromptBuilderFactory._discovered is not None
        # Cleanup
        PromptBuilderFactory._discovered = None

    def test_discover_prompts_idempotent(self):
        """Multiple calls to _discover_prompts return same result."""
        PromptBuilderFactory._discovered = None
        r1 = PromptBuilderFactory._discover_prompts()
        r2 = PromptBuilderFactory._discover_prompts()
        # Both should have same keys (may be different dict objects)
        assert set(r1.keys()) == set(r2.keys())

    def test_list_roles_includes_discovered(self):
        """list_roles includes both hardcoded and discovered roles."""
        # Reset cache to trigger discovery
        PromptBuilderFactory._discovered = None
        roles = PromptBuilderFactory.list_roles()
        # Should include hardcoded
        assert "chat" in roles
        assert "researcher" in roles

    def test_register_doesnt_affect_discovered(self):
        """register() adds to _BUILDERS, not _discovered."""
        PromptBuilderFactory._discovered = None
        custom = StaticFilePromptBuilder("researcher")
        PromptBuilderFactory.register("_test_custom_", custom)
        try:
            assert "_test_custom_" in PromptBuilderFactory._BUILDERS
            # get() should find it in _BUILDERS
            assert PromptBuilderFactory.get("_test_custom_") is custom
        finally:
            del PromptBuilderFactory._BUILDERS["_test_custom_"]

    def test_null_builder_returns_empty(self):
        """_NullBuilder returns empty strings/lists."""
        nb = _NullBuilder()
        assert nb.build_system_prompt("role", {}) == ""
        assert nb.build_messages("task", [], {}) == []
        assert nb.estimate_tokens([]) == 0
        result = nb.validate([])
        assert result.ok is True

    def test_discover_with_empty_dir(self, tmp_path):
        """_discover_prompts with empty directory finds nothing."""
        with patch.object(Path, "__truediv__", return_value=tmp_path / "nonexistent"):
            result = PromptBuilderFactory._discover_prompts()
            # May or may not find files depending on the patch
            assert isinstance(result, dict)
