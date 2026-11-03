"""Tests for ContextBuilder + token estimation."""

from __future__ import annotations

from pathlib import Path

import pytest

from strategy_research.core.agent.builtin_tools import build_default_registry
from strategy_research.core.agent.context import (
    ContextBuilder,
    estimate_tokens,
)
from strategy_research.core.agent.tools import ToolRegistry
from strategy_research.core.llm import LLMConfig
from strategy_research.kernel.memory.persistent import PersistentMemory

# ── Token estimation ─────────────────────────────────────────────────


class TestTokenEstimation:
    def test_empty_messages(self):
        assert estimate_tokens([]) == 1  # min 1

    def test_single_short_message(self):
        msgs = [{"role": "user", "content": "hi"}]
        n = estimate_tokens(msgs)
        assert n >= 1
        assert n < 10

    def test_long_message(self):
        msgs = [{"role": "user", "content": "x" * 4000}]
        n = estimate_tokens(msgs)
        # 4000 chars / 3 chars/token ≈ 1333
        assert 1000 < n < 1500

    def test_multiple_messages(self):
        msgs = [
            {"role": "system", "content": "x" * 1000},
            {"role": "user", "content": "x" * 500},
            {"role": "assistant", "content": "x" * 500},
        ]
        n = estimate_tokens(msgs)
        assert 500 < n < 800

    def test_tool_call_in_message(self):
        msgs = [{"role": "assistant", "content": None,
                 "tool_calls": [{"function": {"arguments": '{"a":1}'}}]}]
        n = estimate_tokens(msgs)
        assert n >= 1

    def test_tool_message_role_overhead(self):
        msgs = [{"role": "tool", "content": "x"}]
        n = estimate_tokens(msgs)
        assert n >= 33  # role overhead adds ~100 chars

    def test_none_content(self):
        msgs = [{"role": "assistant", "content": None}]
        assert estimate_tokens(msgs) >= 1


# ── ContextBuilder basics ────────────────────────────────────────────


class TestContextBuilder:
    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        (tmp_path / "strategies").mkdir()
        return tmp_path

    @pytest.fixture
    def registry(self) -> ToolRegistry:
        return build_default_registry()

    def test_system_prompt_contains_workspace(self, registry, workspace):
        b = ContextBuilder(LLMConfig(), registry, workspace=workspace)
        sp = b.build_system_prompt()
        assert str(workspace) in sp

    def test_system_prompt_contains_tools(self, registry, workspace):
        b = ContextBuilder(LLMConfig(), registry, workspace=workspace)
        sp = b.build_system_prompt()
        assert "read" in sp
        assert "write" in sp
        assert "run_backtest" in sp
        assert "compute_factor" in sp
        assert "git_diff" in sp
        assert "list_history" in sp

    def test_system_prompt_has_role(self, registry, workspace):
        b = ContextBuilder(LLMConfig(), registry, workspace=workspace)
        sp = b.build_system_prompt()
        assert "策略研究助手" in sp or "agent" in sp.lower()

    def test_system_prompt_cached(self, registry, workspace):
        b = ContextBuilder(LLMConfig(), registry, workspace=workspace)
        sp1 = b.build_system_prompt()
        sp2 = b.build_system_prompt()
        assert sp1 is sp2  # exact same object

    def test_system_prompt_without_workspace(self, registry):
        b = ContextBuilder(LLMConfig(), registry)
        sp = b.build_system_prompt()
        assert "(unset)" in sp

    def test_empty_tool_registry(self, workspace):
        empty_reg = ToolRegistry()
        b = ContextBuilder(LLMConfig(), empty_reg, workspace=workspace)
        sp = b.build_system_prompt()
        assert "no tools available" in sp


# ── Initial messages + auto-recall ───────────────────────────────────


class TestInitialMessages:
    @pytest.fixture
    def setup(self, tmp_path: Path):
        ws = tmp_path
        (ws / "strategies").mkdir()
        # Pre-populate memory via writer, then create reader (loads snapshot)
        mem_writer = PersistentMemory(memory_dir=ws / "memory")
        mem_writer.add("feedback_momentum", "momentum_20_60 在中小盘失效",
                description="avoid high top_n in small caps")
        mem_writer.add("feedback_vol", "volatility factor helps in sideways",
                description="use std(returns, 20) as cross-section filter")
        mem_writer.add("general_note", "always check max_dd before keep",
                description="max_dd > 50% means strategy too risky")
        mem = PersistentMemory(memory_dir=ws / "memory")
        registry = build_default_registry()
        builder = ContextBuilder(
            LLMConfig(), registry, memory=mem, workspace=ws,
        )
        return builder, mem, registry

    def test_two_messages(self, setup):
        builder, _, _ = setup
        msgs = builder.build_initial_messages("improve momentum")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"

    def test_user_content_contains_task(self, setup):
        builder, _, _ = setup
        msgs = builder.build_initial_messages("improve momentum")
        assert "improve momentum" in msgs[1]["content"]

    def test_user_content_includes_recalled(self, setup):
        builder, _, _ = setup
        msgs = builder.build_initial_messages("improve momentum for small caps")
        content = msgs[1]["content"]
        # Should recall the small-caps memory
        assert "<recalled-memories>" in content
        assert "small caps" in content.lower() or "中小盘" in content

    def test_no_recall_when_no_match(self, setup):
        builder, _, _ = setup
        builder.build_initial_messages('completely unrelated task xyz123')
        # May or may not recall anything depending on token overlap
        # The test just ensures no crash

    def test_no_recall_when_no_memory(self, tmp_path):
        (tmp_path / "strategies").mkdir()
        builder = ContextBuilder(
            LLMConfig(),
            build_default_registry(),
            memory=None,
            workspace=tmp_path,
        )
        msgs = builder.build_initial_messages("anything")
        content = msgs[1]["content"]
        # No <recalled-memories> tag when no memory
        assert "<recalled-memories>" not in content

    def test_no_memory_object(self, tmp_path):
        (tmp_path / "strategies").mkdir()
        builder = ContextBuilder(
            LLMConfig(),
            build_default_registry(),
            memory=None,
            workspace=tmp_path,
        )
        msgs = builder.build_initial_messages("task")
        assert len(msgs) == 2


# ── Memory snapshot ──────────────────────────────────────────────────


class TestMemorySnapshot:
    def test_snapshot_frozen_at_init(self, tmp_path):
        (tmp_path / "strategies").mkdir()
        mem = PersistentMemory(memory_dir=tmp_path / "memory")
        builder = ContextBuilder(LLMConfig(), build_default_registry(),
                                memory=mem, workspace=tmp_path)
        sp1 = builder.build_system_prompt()
        # Add new memory AFTER builder construction
        mem.add("new_entry", "added later", description="late")
        sp2 = builder.build_system_prompt()
        # Snapshot should be FROZEN — no change
        assert sp1 == sp2

    def test_empty_memory_section(self, tmp_path):
        (tmp_path / "strategies").mkdir()
        # No memory dir exists at all
        builder = ContextBuilder(LLMConfig(), build_default_registry(),
                                memory=None, workspace=tmp_path)
        sp = builder.build_system_prompt()
        assert "(empty)" in sp or "memory" in sp.lower()

    def test_with_memory(self, tmp_path):
        (tmp_path / "strategies").mkdir()
        # Pre-populate via one PersistentMemory, then create a new instance
        # (which loads snapshot from disk)
        mem_writer = PersistentMemory(memory_dir=tmp_path / "memory")
        mem_writer.add("test_entry", "test body", description="desc")
        # Now create a fresh instance — it will load MEMORY.md into snapshot
        mem_reader = PersistentMemory(memory_dir=tmp_path / "memory")
        builder = ContextBuilder(LLMConfig(), build_default_registry(),
                                memory=mem_reader, workspace=tmp_path)
        sp = builder.build_system_prompt()
        assert "test_entry" in sp


# ── Estimate_tokens with realistic messages ──────────────────────────


class TestRealisticEstimation:
    def test_full_initial_messages(self, tmp_path):
        (tmp_path / "strategies").mkdir()
        b = ContextBuilder(
            LLMConfig(),
            build_default_registry(),
            memory=None,
            workspace=tmp_path,
        )
        msgs = b.build_initial_messages("a moderately long task description " * 10)
        n = b.estimate_tokens(msgs)
        # System prompt ~700-1200 tokens (varies with tool count); user msg ~50; total ~750-1250
        assert 500 < n < 2000

    def test_estimation_grows_with_messages(self, tmp_path):
        (tmp_path / "strategies").mkdir()
        b = ContextBuilder(LLMConfig(), build_default_registry(),
                          memory=None, workspace=tmp_path)
        msgs1 = b.build_initial_messages("x")
        n1 = b.estimate_tokens(msgs1)
        # Append more user messages
        msgs2 = msgs1 + [{"role": "user", "content": "y" * 1000}]
        n2 = b.estimate_tokens(msgs2)
        assert n2 > n1


# ── Integration with real memory + tools ─────────────────────────────


class TestIntegration:
    def test_end_to_end_message_building(self, tmp_path):
        (tmp_path / "strategies").mkdir()
        # Pre-populate then create new instance for snapshot loading
        mem_writer = PersistentMemory(memory_dir=tmp_path / "memory")
        mem_writer.add("first_note", "body 1", description="alpha observation")
        mem_writer.add("second_note", "body 2", description="beta observation")
        mem = PersistentMemory(memory_dir=tmp_path / "memory")

        builder = ContextBuilder(
            LLMConfig(),
            build_default_registry(),
            memory=mem,
            workspace=tmp_path,
        )
        msgs = builder.build_initial_messages("check alpha observation")
        sys_msg = msgs[0]["content"]
        user_msg = msgs[1]["content"]

        # System has workspace + tools + memory snapshot
        assert str(tmp_path) in sys_msg
        assert "alpha" in sys_msg or "first_note" in sys_msg
        # User has task + recalled memories
        assert "check alpha" in user_msg
        assert "<recalled-memories>" in user_msg

    def test_tool_definitions_in_system(self, tmp_path):
        (tmp_path / "strategies").mkdir()
        builder = ContextBuilder(
            LLMConfig(), build_default_registry(), memory=None, workspace=tmp_path,
        )
        sp = builder.build_system_prompt()
        # Check that all 6 tools' names appear with descriptions
        for tool_name in ["read", "write", "run_backtest",
                          "compute_factor", "git_diff", "list_history"]:
            assert tool_name in sp, f"tool {tool_name} missing from system prompt"

    def test_token_estimation_with_tool_calls(self):
        """Messages with tool_calls should be counted properly."""
        msgs = [
            {"role": "user", "content": "do task"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"function": {"name": "f", "arguments": '{"x": 1, "y": 2}'}}]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"result": 42}'},
        ]
        n = estimate_tokens(msgs)
        assert n > 30  # All contributions counted


# ── Custom system prompt ─────────────────────────────────────────────


class TestCustomSystemPrompt:
    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        (tmp_path / "strategies").mkdir()
        return tmp_path

    @pytest.fixture
    def registry(self) -> ToolRegistry:
        return build_default_registry()

    def test_custom_prompt_used(self, registry, workspace):
        """当传入 system_prompt 时，使用自定义 prompt"""
        custom = "你是数据质量检查员。\n\n可用工具:\n{tool_list}"
        b = ContextBuilder(LLMConfig(), registry, workspace=workspace,
                          system_prompt=custom)
        sp = b.build_system_prompt()
        assert "数据质量检查员" in sp
        assert "read" in sp  # {tool_list} 被替换

    def test_custom_prompt_with_workspace_placeholder(self, registry, workspace):
        """自定义 prompt 中的 {workspace} 被替换"""
        custom = "工作区: {workspace}"
        b = ContextBuilder(LLMConfig(), registry, workspace=workspace,
                          system_prompt=custom)
        sp = b.build_system_prompt()
        assert str(workspace) in sp

    def test_user_message_prefix(self, registry, workspace):
        """user_message_prefix 被拼在 task 前面"""
        prefix = "## 当前状态\ncalmar=0.42"
        b = ContextBuilder(LLMConfig(), registry, workspace=workspace,
                          user_message_prefix=prefix)
        msgs = b.build_initial_messages("improve strategy")
        content = msgs[1]["content"]
        assert "calmar=0.42" in content
        assert "improve strategy" in content

    def test_custom_prompt_caches(self, registry, workspace):
        """自定义 prompt 也缓存"""
        custom = "你是分析师。{tool_list}"
        b = ContextBuilder(LLMConfig(), registry, workspace=workspace,
                          system_prompt=custom)
        sp1 = b.build_system_prompt()
        sp2 = b.build_system_prompt()
        assert sp1 is sp2
