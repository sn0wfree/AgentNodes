"""P3-C: Prompt auto-discovery tests."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.kernel.agent.prompt_builder import (
    ChatPromptBuilder,
    PromptBuilderFactory,
    StaticFilePromptBuilder,
    _NullBuilder,
)


class TestPromptAutoDiscovery:
    def test_known_role_returns_builder(self):
        """Bundled role returns its builder（含硬编码与动态发现两条路径）。

        2026-11-05 批次 7：researcher 已归档，改用 chat（硬编码）与
        orchestrator（动态发现）各测一次。
        """
        assert isinstance(PromptBuilderFactory.get("chat"), ChatPromptBuilder)
        assert isinstance(
            PromptBuilderFactory.get("orchestrator"), StaticFilePromptBuilder
        )

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
        for role in ["chat"]:
            assert role not in discovered
        # 2026-11-05 批次 7：researcher / strategist 的提示词已归档，
        # 不再硬编码，因此不再作为「不可覆盖」的样本。

    def test_list_roles_includes_hardcoded(self):
        # 2026-11-05 批次 7：研究角色归档后，硬编码只剩 chat；
        # 其余角色由 _discover_prompts() 从 templates/.prompts/*.md 扫描得到。
        roles = PromptBuilderFactory.list_roles()
        assert "chat" in roles
        for role in ("orchestrator", "planner", "evaluator"):
            assert role in roles

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
