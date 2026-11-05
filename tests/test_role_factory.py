"""Phase C-1 — agent role factory + AgentLoop 真接测试。

覆盖:
- _load_role_system_prompt: 9 个 role 都加载成功
- _get_tool_whitelist: role → tool 映射正确
- should_use_real_llm: env var / api key 决策
- build_agent_loop: 构造 AgentLoop 成功 (mock LLM)
- run_agent_via_llm: 异常路径返回 JSON error
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


class TestRolePromptLoading:
    """9 个 role 的 prompt 都能从 templates/.prompts/ 加载 (via PromptBuilderFactory)."""

    def test_all_10_roles_have_prompt_files(self):
        from agentnodes.kernel.agent.prompt_builder import PromptBuilderFactory
        roles = ["researcher", "data_quality", "factor_analyst", "strategist",
                 "portfolio_construction", "risk_controller", "attribution_analyst",
                 "anti_overfit_analyst", "backtest_diagnostics", "critic"]
        for role in roles:
            prompt = PromptBuilderFactory.get(role).build_system_prompt(role, {})
            assert len(prompt) > 50, f"{role} prompt is too short or missing"
            # After common layer prepended, role content follows; check
            # for header anywhere in the prompt (not just at start).
            assert "# Role:" in prompt, f"{role} prompt missing # Role: header"

    def test_unknown_role_returns_empty(self):
        from agentnodes.kernel.agent.prompt_builder import PromptBuilderFactory
        assert PromptBuilderFactory.get("nonexistent_role_xyz").build_system_prompt(
            "nonexistent_role_xyz", {}
        ) == ""

    def test_prompt_files_match_templates(self):
        """9 个 prompt 文件名与 templates/.prompts/ 对应."""
        from agentnodes import _TEMPLATES_DIR
        from agentnodes.kernel.agent.role_factory import _ROLE_PROMPT_FILES
        prompts_dir = _TEMPLATES_DIR / ".prompts"
        for role, filename in _ROLE_PROMPT_FILES.items():
            assert (prompts_dir / filename).exists(), f"{filename} missing in {prompts_dir}"


class TestToolWhitelist:
    """role → tool whitelist 映射."""

    def test_strategist_has_write_tools(self):
        """strategist 必须能 write_file + run_backtest."""
        from agentnodes.kernel.agent.role_factory import _get_tool_whitelist
        wl = _get_tool_whitelist("strategist")
        assert "write" in wl
        assert "run_backtest" in wl
        assert "read" in wl

    def test_data_quality_minimal(self):
        """data_quality 是只读 agent, 包含 read_file + web tools."""
        from agentnodes.kernel.agent.role_factory import _get_tool_whitelist
        wl = _get_tool_whitelist("data_quality")
        assert "read" in wl
        assert "websearch" in wl
        assert "webfetch" in wl

    def test_critic_is_readonly(self):
        """critic 不能 write_file."""
        from agentnodes.kernel.agent.role_factory import _get_tool_whitelist
        wl = _get_tool_whitelist("critic")
        assert "write" not in wl
        assert "read" in wl

    def test_unknown_role_minimal_default(self):
        """未知 role 退到 minimal 最小集 (fail-safe)."""
        from agentnodes.kernel.agent.role_factory import _get_tool_whitelist
        from agentnodes.kernel.agent.toolsets import TOOLSETS
        assert _get_tool_whitelist("foo_bar_baz") == TOOLSETS["minimal"]


class TestShouldUseRealLlm:
    """should_use_real_llm() 决策."""

    def test_no_env_var_no_api_key_returns_false(self, monkeypatch):
        """没设 AUTORESEARCH_BEHAVIOR + 无 api key → 走 stub."""
        monkeypatch.delenv("AUTORESEARCH_BEHAVIOR", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("AGENTNODES_LLM_PROFILE", raising=False)
        # conftest._isolate_llm_bridge already points the bridge at a
        # disabled fixture, so LLMConfig.load() returns no api_key.
        from agentnodes.kernel.agent.role_factory import should_use_real_llm
        assert should_use_real_llm() is False

    def test_env_var_set_returns_false(self, monkeypatch):
        """AUTORESEARCH_BEHAVIOR=stub → 强制走 stub, 即便有 api key."""
        monkeypatch.setenv("AUTORESEARCH_BEHAVIOR", "static")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        from agentnodes.kernel.agent.role_factory import should_use_real_llm
        assert should_use_real_llm() is False

    def test_placeholder_api_key_returns_false(self, monkeypatch):
        """api_key 是占位符 → 走 stub."""
        monkeypatch.delenv("AUTORESEARCH_BEHAVIOR", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "your-api-key-here")
        # conftest._isolate_llm_bridge disables the bridge, so the
        # placeholder OPENAI_API_KEY is the only credential source.
        from agentnodes.kernel.agent.role_factory import should_use_real_llm
        assert should_use_real_llm() is False


class TestBuildAgentLoop:
    """build_agent_loop() 构造 AgentLoop 实例."""

    def test_returns_agent_loop_with_prompt(self, tmp_path):
        from agentnodes.kernel.agent.loop import AgentLoop
        from agentnodes.kernel.agent.role_factory import build_agent_loop

        loop = build_agent_loop(
            role="researcher",
            workspace_path=tmp_path,
            strategy_name="test_strat",
        )
        assert loop is not None
        assert isinstance(loop, AgentLoop)
        # system_prompt 应加载了 researcher.md
        assert loop.context_builder is not None
        # tool whitelist 应过滤到只读工具
        assert "compute_factor" not in [t.name for t in loop.registry._tools.values()]
        # 但应保留 read_file
        tool_names = [t.name for t in loop.registry._tools.values()]
        assert "read" in tool_names

    def test_strategist_has_write_tools(self, tmp_path):
        from agentnodes.kernel.agent.role_factory import build_agent_loop

        loop = build_agent_loop(
            role="strategist",
            workspace_path=tmp_path,
            strategy_name="test_strat",
        )
        assert loop is not None
        tool_names = {t.name for t in loop.registry._tools.values()}
        assert "write" in tool_names
        # 批次 6：业务工具归位 L2 后经 CapabilityRegistry 重新注册，
        # run_backtest 又出现在 registry 里（其底层 backtest.* kind 仍缺失，
        # 调用时走降级路径）。这里只断言「写出能力可用」，不断言具体工具集。

    def test_unknown_role_returns_none(self, tmp_path):
        from agentnodes.kernel.agent.role_factory import build_agent_loop

        loop = build_agent_loop(
            role="nonexistent_role_xyz",
            workspace_path=tmp_path,
            strategy_name="test_strat",
        )
        # 没有 .prompts 文件 → 返回 None
        assert loop is None


class TestRunAgentViaLlm:
    """run_agent_via_llm() 异常路径."""

    def test_unknown_role_raises(self, tmp_path):
        """未知 role 应抛 RuntimeError."""
        from agentnodes.kernel.agent.role_factory import run_agent_via_llm
        with pytest.raises(RuntimeError, match="无法构造 AgentLoop"):
            run_agent_via_llm(
                role="nonexistent_role_xyz",
                workspace_path=tmp_path,
                strategy_name="test_strat",
                task="dummy task",
            )

    def test_known_role_with_mocked_loop_returns_answer(self, tmp_path):
        """Mock AgentLoop.run() → 返回 mock answer JSON.

        选项 B（2026-10-28）：GraphEngine 只做编排，单个 agent turn 仍由
        AgentLoop 执行。所以接缝回到 build_agent_loop —— 这是设计意图，
        不是回退事故。
        """
        from agentnodes.kernel.agent import role_factory as factory
        from agentnodes.kernel.agent.loop import LoopResult

        mock_loop = MagicMock()
        mock_loop.run.return_value = LoopResult(
            answer='{"action": "search_external", "hypothesis": "test"}',
            iterations=2,
            tool_calls_made=1,
            finished_reason="stop",
        )

        with patch.object(factory, "build_agent_loop", return_value=mock_loop):
            result = factory.run_agent_via_llm(
                role="researcher",
                workspace_path=tmp_path,
                strategy_name="test_strat",
                task="find alpha",
                previous_outputs=[{"prior": "result"}],
            )

        assert json.loads(result) == {"action": "search_external", "hypothesis": "test"}
        mock_loop.run.assert_called_once()
        # 任务拼装契约：context → 历史输出 → 当前任务
        full_task = mock_loop.run.call_args.args[0]
        assert "## 之前 Agent 输出 (来自上一阶段)" in full_task
        assert "## 当前任务" in full_task
        assert full_task.index("第 1 阶段输出") < full_task.index("## 当前任务")

    def test_known_role_with_loop_error_returns_error_json(self, tmp_path):
        """AgentLoop.run() 返回 error → 应序列化成 JSON error."""
        from agentnodes.kernel.agent import role_factory as factory
        from agentnodes.kernel.agent.loop import LoopResult

        mock_loop = MagicMock()
        mock_loop.run.return_value = LoopResult(
            answer="",
            iterations=3,
            tool_calls_made=0,
            finished_reason="error",
            error="LLM 调用超时",
        )

        with patch.object(factory, "build_agent_loop", return_value=mock_loop):
            result = factory.run_agent_via_llm(
                role="factor_analyst",
                workspace_path=tmp_path,
                strategy_name="test_strat",
                task="compute IC",
            )

        body = json.loads(result)
        assert "error" in body
        assert "LLM 调用超时" in body["error"]
        assert body["iterations"] == 3
        # 三字段契约：多一个键都是破坏
        assert set(body) == {"error", "iterations", "tool_calls_made"}


# TestSpawnAgentFallback 已随 apps/study/stub_agents.py 移除
# （2026-11-05 批次 3）：stub 输出是研究角色专属（researcher/
# factor_analyst/strategist…），不属于 agent 框架。真 LLM 路径
# （run_agent_via_llm / should_use_real_llm）仍由本文件其余用例覆盖。

