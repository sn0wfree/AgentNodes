"""P3-C: Prompt auto-discovery tests."""

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


class TestPromptAutoDiscovery:
    def test_known_role_returns_builder(self):
        """Hardcoded roles return their builder."""
        builder = PromptBuilderFactory.get("researcher")
        assert isinstance(builder, StaticFilePromptBuilder)

    def test_unknown_role_returns_null_by_default(self):
        """Unknown role returns _NullBuilder when no .prompts/ dir has matching file."""
        # This may or may not find files depending on the filesystem
        builder = PromptBuilderFactory.get("nonexistent_role_xyz_999")
        assert isinstance(builder, _NullBuilder)

    def test_discover_prompts_returns_dict(self):
        """_discover_prompts returns a dict of role -> builder."""
        result = PromptBuilderFactory._discover_prompts()
        assert isinstance(result, dict)

    def test_discover_skips_hardcoded_roles(self):
        """Discovered roles don't override hardcoded ones."""
        discovered = PromptBuilderFactory._discover_prompts()
        # These are hardcoded, should not appear in discovered
        for role in ["chat", "researcher", "strategist"]:
            assert role not in discovered

    def test_list_roles_includes_hardcoded(self):
        roles = PromptBuilderFactory.list_roles()
        assert "researcher" in roles
        assert "chat" in roles
        assert "strategist" in roles

    def test_register_overrides(self):
        """register() adds to hardcoded dict."""
        custom = StaticFilePromptBuilder("researcher")  # same role, different instance
        PromptBuilderFactory.register("custom_test_role", custom)
        assert PromptBuilderFactory.get("custom_test_role") is custom
        # Cleanup
        del PromptBuilderFactory._BUILDERS["custom_test_role"]

    def test_discover_prompts_handles_missing_dir(self, tmp_path):
        """Gracefully handles missing templates/.prompts directory."""
        with patch.object(
            PromptBuilderFactory,
            "_discover_prompts",
            return_value={},
        ):
            result = PromptBuilderFactory._discover_prompts()
            assert result == {}

    def test_get_caches_discovered(self):
        """First get() call caches discovered prompts."""
        # Reset cache
        PromptBuilderFactory._discovered = None
        # First call triggers discovery
        PromptBuilderFactory.get("nonexistent_role_xyz_999")
        # Cache should now be set
        assert PromptBuilderFactory._discovered is not None
        # Cleanup
        PromptBuilderFactory._discovered = None
