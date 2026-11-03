"""Tests for AgentExecutor + exec_registry (unified engine, Phase 3)."""
from __future__ import annotations

from pathlib import Path

import pytest

from strategy_research.core.agent import exec_registry
from strategy_research.core.agent.builtin_plugins import BUILTIN_PLUGINS
from strategy_research.core.agent.dag_config import AgentNodeConfig
from strategy_research.core.agent.executor import (
    AgentExecutor,
    first_two_sentences,
)
from strategy_research.core.agent.registry import AgentPluginRegistry
from strategy_research.core.agent.loop import AgentLoop
from strategy_research.foundation.llm import LLMConfig, LLMResponse

PLUGINS = {p.id: p for p in BUILTIN_PLUGINS}
_orig_loop_init = AgentLoop.__init__


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "strategies" / "foo").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def executor() -> AgentExecutor:
    return AgentExecutor(
        AgentPluginRegistry(BUILTIN_PLUGINS),
        llm_config=LLMConfig(api_key="sk-test"),
    )


# ── exec_registry ────────────────────────────────────────────────────


class TestExecRegistry:
    def test_builtins_registered(self):
        assert exec_registry.get_python_executor("run_backtest_script") is not None
        assert exec_registry.get_evaluator("decide") is not None

    def test_register_and_get_roundtrip(self):
        def fn(**kw):
            return {"ok": True}
        exec_registry.register_python_executor("_t_py", fn)
        exec_registry.register_evaluator("_t_ev", fn)
        assert exec_registry.get_python_executor("_t_py") is fn
        assert exec_registry.get_evaluator("_t_ev") is fn

    def test_extract_metrics_from_upstream(self):
        up = {
            "a": '{"metrics": {"calmar": 0.7}}',
            "b": "not json",
        }
        assert exec_registry.extract_metrics_from_upstream(up) == {
            "calmar": 0.7,
        }
        assert exec_registry.extract_metrics_from_upstream(None) == {}


# ── python executor ──────────────────────────────────────────────────


class TestPythonExec:
    def test_python_plugin_executes_registered_fn(self, workspace, executor):
        exec_registry.register_python_executor(
            "_py_hello", lambda workspace_path, upstream=None, **kw: {
                "hello": "world", "up": bool(upstream),
            },
        )
        from strategy_research.core.agent.plugin import AgentPlugin
        plugin = AgentPlugin(
            id="py_hello", name="PyHello", category="tool", description="",
            executor_type="python", python_function="_py_hello",
        )
        r = executor.execute(plugin, "t", workspace,
                             upstream_outputs={"r": "out"})
        assert r.success
        assert '"hello": "world"' in r.output
        assert '"up": true' in r.output

    def test_python_plugin_unknown_fn_is_error(self, workspace, executor):
        from strategy_research.core.agent.plugin import AgentPlugin
        plugin = AgentPlugin(
            id="py_missing", name="M", category="tool", description="",
            executor_type="python", python_function="_nope_",
        )
        r = executor.execute(plugin, "t", workspace)
        assert not r.success
        assert "_nope_" in (r.error or "")


# ── evaluator ────────────────────────────────────────────────────────


class TestEvaluator:
    def test_evaluator_gets_metrics_from_upstream(self, workspace, executor):
        calls = {}

        def fake_decide(metrics=None, **kw):
            calls["metrics"] = metrics
            return {"verdict": "keep"}

        exec_registry.register_evaluator("_ev_decide", fake_decide)
        from strategy_research.core.agent.plugin import AgentPlugin
        plugin = AgentPlugin(
            id="ev_decide", name="E", category="tool", description="",
            executor_type="evaluator", python_function="_ev_decide",
        )
        up = {"backtest": '{"metrics": {"calmar": 1.2}}'}
        r = executor.execute(plugin, "t", workspace, upstream_outputs=up)
        assert r.success
        assert calls["metrics"] == {"calmar": 1.2}
        assert '"verdict": "keep"' in r.output

    def test_evaluator_context_forwarded(self, workspace, executor):
        seen = {}

        def fake_decide(metrics=None, **kw):
            seen.update(kw)
            return {}

        exec_registry.register_evaluator("_ev_ctx", fake_decide)
        from strategy_research.core.agent.plugin import AgentPlugin
        plugin = AgentPlugin(
            id="ev_ctx", name="E", category="tool", description="",
            executor_type="evaluator", python_function="_ev_ctx",
        )
        executor.execute(
            plugin, "t", workspace,
            context={"llm_verdict": "keep", "stagnation_count": 2,
                     "executor_type": "evaluator"},
        )
        assert seen.get("llm_verdict") == "keep"
        assert seen.get("stagnation_count") == 2
        assert "executor_type" not in seen


# ── LLM path (mock AgentLoop client) ────────────────────────────────


class TestLLMExec:
    def _patch_loop(self, chat_fn=None):
        """Patch AgentLoop.__init__ to use a mock chat and skip streaming."""
        import strategy_research.core.agent.loop as loop_mod
        orig = loop_mod.AgentLoop.__init__
        _patch_captured: dict = {}
        def patched(self, *a, **kw):
            orig(self, *a, **kw)
            self._stream_mode = False
            self.stream_mode = False
            _patch_captured["max_iterations"] = kw.get("max_iterations")
            if chat_fn is not None:
                self.client.chat = chat_fn
        loop_mod.AgentLoop.__init__ = patched
        return orig, _patch_captured

    def _unpatch_loop(self, orig):
        import strategy_research.core.agent.loop as loop_mod
        loop_mod.AgentLoop.__init__ = orig

    def test_llm_success_and_summary(self, workspace, executor):
        plugin = PLUGINS["researcher"]
        captured: dict = {}

        def fake_chat(messages, **kwargs):
            captured["kwargs"] = kwargs
            captured["user_msg"] = messages[-1]["content"]
            return LLMResponse(
                content='{"hypothesis": "h1", "action": "discover"}',
                tool_calls=[], finish_reason="stop",
            )

        orig, patch_cap = self._patch_loop(fake_chat)
        try:
            r = executor.execute(
                plugin, "研究动量因子", workspace,
                # strategy_name is a loop kwarg now — filtered from the
                # prompt by _NON_PROMPT_KEYS; use a real context key.
                context={"market": "a_share"},
                upstream_outputs={"x": "prior"},
            )
        finally:
            self._unpatch_loop(orig)

        assert r.success
        assert "hypothesis" in r.output
        assert r.metrics["finished_reason"] == "stop"
        assert r.metrics["iterations"] == 1
        # tools present in kwargs
        assert captured["kwargs"].get("tools") is not None
        # User message contains upstream + context + task sections.
        user_msg = captured["user_msg"]
        assert "## Upstream Agent Outputs" in user_msg
        assert "### x" in user_msg
        assert "## Current Context" in user_msg
        assert "研究动量因子" in user_msg

    def test_llm_no_prompt_file_is_error(self, workspace, executor):
        from strategy_research.core.agent.plugin import AgentPlugin
        plugin = AgentPlugin(
            id="nope_prompt", name="N", category="research",
            description="", prompt_file="",
        )
        r = executor.execute(plugin, "t", workspace)
        assert not r.success
        assert "no prompt_file" in (r.error or "")

    def test_llm_error_result_maps_to_error(self, workspace, executor):
        plugin = PLUGINS["researcher"]

        def fail_chat(messages, **kwargs):
            from strategy_research.foundation.llm.errors import LLMError
            raise LLMError("quota exceeded")

        orig, _ = self._patch_loop(fail_chat)
        try:
            r = executor.execute(plugin, "t", workspace)
        finally:
            self._unpatch_loop(orig)
        assert not r.success
        assert r.status == "error"

    def test_node_overrides_tools_and_iterations(self, workspace, executor):
        plugin = PLUGINS["researcher"]
        captured: dict = {}

        def capture_chat(messages, **kwargs):
            captured.update(kwargs)
            return LLMResponse(content="ok", tool_calls=[], finish_reason="stop")

        orig, patch_cap = self._patch_loop(capture_chat)
        try:
            node = AgentNodeConfig(
                id="researcher", tools_override=["read"], max_iterations=3,
            )
            executor.execute(plugin, "t", workspace, node=node)
        finally:
            self._unpatch_loop(orig)
        # tools arg to client.chat is the tool definitions list;
        # when allowed_tools=["read"], only the read tool def appears.
        tools_defs = captured.get("tools") or []
        tool_names = [
            t.get("function", {}).get("name") for t in tools_defs
        ]
        assert "read" in tool_names
        # max_iterations forwarded to AgentLoop constructor
        assert patch_cap.get("max_iterations") == 3


# ── build_task_text ──────────────────────────────────────────────────


class TestBuildTaskText:
    def test_sections_order(self, executor):
        plugin = PLUGINS["researcher"]
        text = executor.build_task_text(
            plugin, "DO IT",
            context={
                "market": "a_share",           # real context key → rendered
                "strategy_name": "foo",        # loop kwarg → filtered
                "session_id": "s1",            # loop kwarg → filtered
            },
            upstream_outputs={"dq": "clean"},
            previous_outputs=[{"k": 1}],
        )
        assert text.index("## Current Context") < text.index(
            "## Upstream Agent Outputs")
        assert text.index("## Upstream Agent Outputs") < text.index(
            "## 之前 Agent 输出")
        assert text.index("## 之前 Agent 输出") < text.index("## 当前任务")
        # loop-kwargs / non-prompt keys excluded from the prompt
        assert "session_id" not in text
        assert "strategy_name" not in text
        assert "a_share" in text

    def test_task_only(self, executor):
        plugin = PLUGINS["researcher"]
        text = executor.build_task_text(plugin, "ONLY", None, None, None)
        assert text == "## 当前任务\nONLY"


# ── first_two_sentences ──────────────────────────────────────────────


class TestSummary:
    def test_english(self):
        assert first_two_sentences("One. Two. Three.") == "One. Two."

    def test_chinese(self):
        assert first_two_sentences("一。二。三。") == "一。二。"

    def test_empty(self):
        assert first_two_sentences("") == ""
