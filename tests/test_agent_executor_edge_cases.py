"""Edge-case unit tests for AgentExecutor + helpers.

Covers:
- AgentExecutionResult serialization (.to_dict, .success property)
- first_two_sentences() summary extraction (English, CJK, edge cases)
- exec_registry.extract_metrics_from_upstream() edge cases
- AgentExecutor.build_task_text() section ordering and key filtering
- _exec_python() non-dict return path
- _exec_evaluator() metrics extraction + context-key filtering
- _exec_llm() missing-prompt-file and empty-prompt error paths
- Top-level execute() exception → AgentExecutionResult(error=...) fallback
- AgentExecutor.__init__ with explicit registry injection
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from strategy_research.kernel.agent import exec_registry
from strategy_research.kernel.agent.dag_config import AgentNodeConfig
from strategy_research.kernel.agent.executor import (
    AgentExecutionResult,
    AgentExecutor,
    first_two_sentences,
)
from strategy_research.kernel.agent.plugin import AgentPlugin
from strategy_research.kernel.agent.registry import AgentPluginRegistry


# ── AgentExecutionResult ────────────────────────────────────────────


class TestAgentExecutionResult:
    def test_success_property_true(self):
        r = AgentExecutionResult(agent_id="x", status="success")
        assert r.success is True

    def test_success_property_false_for_error(self):
        r = AgentExecutionResult(agent_id="x", status="error", error="oops")
        assert r.success is False

    def test_success_property_false_for_skipped(self):
        r = AgentExecutionResult(agent_id="x", status="skipped")
        assert r.success is False

    def test_success_property_false_for_pending(self):
        r = AgentExecutionResult(agent_id="x")
        assert r.success is False

    def test_to_dict_contains_core_fields(self):
        r = AgentExecutionResult(
            agent_id="researcher",
            status="success",
            output='{"hypothesis": "x"}',
            elapsed_s=1.23,
            summary="first two sentences",
            metrics={"iterations": 2},
        )
        d = r.to_dict()
        for key in ("agent_id", "status", "output", "error",
                    "elapsed_s", "summary", "metrics"):
            assert key in d
        assert d["agent_id"] == "researcher"
        assert d["metrics"]["iterations"] == 2
        # to_dict omits artifacts by design (kept for future expansion)
        assert "artifacts" not in d

    def test_default_factories_are_independent(self):
        """Two results should not share metrics/artifacts dicts."""
        a = AgentExecutionResult(agent_id="a")
        b = AgentExecutionResult(agent_id="b")
        a.metrics["x"] = 1
        a.artifacts["y"] = 2
        assert "x" not in b.metrics
        assert "y" not in b.artifacts


# ── first_two_sentences ──────────────────────────────────────────────


class TestFirstTwoSentences:
    def test_empty_string(self):
        assert first_two_sentences("") == ""

    def test_none_input(self):
        assert first_two_sentences(None) == ""  # type: ignore[arg-type]

    def test_english_two_sentences(self):
        result = first_two_sentences("First sentence. Second. Third.")
        assert result.startswith("First sentence.")
        assert "Second." in result

    def test_cjk_two_sentences(self):
        # Chinese sentences without trailing space.
        result = first_two_sentences("第一句。第二句。第三句。")
        assert "第一句。" in result
        assert "第二句。" in result

    def test_single_long_sentence(self):
        # No sentence terminators → returns first 400 chars.
        text = "x" * 500
        assert len(first_two_sentences(text)) == 400

    def test_truncation_to_400(self):
        # Two sentences, second is huge → combined still ≤400.
        text = "Hi. " + "y" * 500 + ". Done."
        assert len(first_two_sentences(text)) <= 400

    def test_one_cjk_terminator(self):
        # Single CJK sentence — falls through to the char-slice path.
        assert "你好" in first_two_sentences("你好世界。")


# ── extract_metrics_from_upstream ────────────────────────────────────


class TestExtractMetricsFromUpstream:
    def test_empty_upstream(self):
        assert exec_registry.extract_metrics_from_upstream(None) == {}
        assert exec_registry.extract_metrics_from_upstream({}) == {}

    def test_finds_metrics_field(self):
        upstream = {"a": '{"metrics": {"calmar": 1.2}}'}
        assert exec_registry.extract_metrics_from_upstream(upstream) == {
            "calmar": 1.2,
        }

    def test_ignores_non_json_strings(self):
        upstream = {"a": "not json", "b": '{"metrics": {"sharpe": 0.8}}'}
        assert exec_registry.extract_metrics_from_upstream(upstream) == {
            "sharpe": 0.8,
        }

    def test_ignores_json_without_metrics(self):
        upstream = {"a": '{"answer": "x", "status": "ok"}'}
        assert exec_registry.extract_metrics_from_upstream(upstream) == {}

    def test_ignores_non_dict_metrics(self):
        upstream = {"a": '{"metrics": [1, 2, 3]}'}
        assert exec_registry.extract_metrics_from_upstream(upstream) == {}

    def test_uses_first_match(self):
        # When multiple upstream entries contain metrics, first wins.
        upstream = {
            "first": '{"metrics": {"calmar": 1.0}}',
            "second": '{"metrics": {"calmar": 2.0}}',
        }
        result = exec_registry.extract_metrics_from_upstream(upstream)
        assert result["calmar"] == 1.0

    def test_skips_non_string_values(self):
        """Non-string upstream values are silently skipped, not parsed."""
        upstream = {"a": {"metrics": {"calmar": 1.0}}, "b": '{"metrics": {"x": 2}}'}
        # Implementation iterates `(upstream or {}).values()` and only
        # processes strings. Non-string entries (e.g. dicts) are
        # skipped without raising; only string entries are parsed.
        result = exec_registry.extract_metrics_from_upstream(upstream)
        assert result.get("calmar") is None
        assert result.get("x") == 2  # came from the string entry


# ── AgentExecutor.__init__ ──────────────────────────────────────────


class TestAgentExecutorInit:
    def test_uses_default_registry_when_none(self):
        e = AgentExecutor()
        # We can call a method that depends on the registry.
        assert e._registry is not None

    def test_uses_injected_registry(self):
        custom = AgentPluginRegistry()
        custom.register(AgentPlugin(
            id="x", name="X", category="research", description="",
        ))
        e = AgentExecutor(registry=custom)
        assert e._registry is custom

    def test_default_llm_config_is_none(self):
        e = AgentExecutor()
        assert e._llm_config is None

    def test_accepts_llm_config_kwarg(self):
        cfg = MagicMock(name="LLMConfig")
        e = AgentExecutor(llm_config=cfg)
        assert e._llm_config is cfg


# ── AgentExecutor.build_task_text ───────────────────────────────────


class TestBuildTaskText:
    def test_task_only(self):
        e = AgentExecutor()
        plugin = AgentPlugin(
            id="x", name="X", category="research", description="",
        )
        out = e.build_task_text(plugin, "just a task", None, None, None)
        assert out == "## 当前任务\njust a task"

    def test_context_filters_non_prompt_keys(self):
        """Keys in _NON_PROMPT_KEYS must not leak into Current Context."""
        e = AgentExecutor()
        plugin = AgentPlugin(
            id="x", name="X", category="research", description="",
        )
        ctx = {
            "hypothesis": "momentum",        # ← rendered
            "strategy_name": "foo",          # ← filtered (loop kwarg since
                                             #   f558086 passed it to AgentLoop)
            "session_id": "s-1",            # ← filtered (loop kwarg)
            "executor_type": "llm",          # ← filtered (meta)
            "python_function": "fn",         # ← filtered (meta)
            "behavior": "static",            # ← filtered (meta)
        }
        out = e.build_task_text(plugin, "task", ctx, None, None)
        assert "hypothesis" in out and "momentum" in out
        # loop kwargs / meta keys must be absent
        for forbidden in ("strategy_name", "session_id", "executor_type",
                          "python_function", '"behavior"'):
            assert forbidden not in out, (
                f"non-prompt key leaked: {forbidden!r}"
            )

    def test_upstream_outputs_rendered_with_agent_id_headers(self):
        e = AgentExecutor()
        plugin = AgentPlugin(
            id="x", name="X", category="research", description="",
        )
        upstream = {"researcher": "hypothesis text"}
        out = e.build_task_text(plugin, "task", None, upstream, None)
        assert "## Upstream Agent Outputs" in out
        assert "### researcher" in out
        assert "hypothesis text" in out

    def test_previous_outputs_dict_serialized_as_json(self):
        e = AgentExecutor()
        plugin = AgentPlugin(
            id="x", name="X", category="research", description="",
        )
        prevs = [{"k": 1}, {"k": 2}]
        out = e.build_task_text(plugin, "task", None, None, prevs)
        assert "## 之前 Agent 输出" in out
        assert '"k": 1' in out
        assert '"k": 2' in out

    def test_previous_outputs_non_dict_serialized_as_plain_text(self):
        e = AgentExecutor()
        plugin = AgentPlugin(
            id="x", name="X", category="research", description="",
        )
        out = e.build_task_text(plugin, "task", None, None, ["raw text"])
        assert "## 之前 Agent 输出" in out
        assert "raw text" in out

    def test_sections_appear_in_canonical_order(self):
        """Context → Upstream → Previous → Task (required by LLM)."""
        e = AgentExecutor()
        plugin = AgentPlugin(
            id="x", name="X", category="research", description="",
        )
        out = e.build_task_text(
            plugin, "TASK",
            context={"k": "v"},
            upstream_outputs={"a": "1"},
            previous_outputs=[{"x": 1}],
        )
        ctx_pos = out.index("## Current Context")
        up_pos = out.index("## Upstream Agent Outputs")
        prev_pos = out.index("## 之前 Agent 输出")
        task_pos = out.index("## 当前任务")
        assert ctx_pos < up_pos < prev_pos < task_pos


# ── _exec_python ────────────────────────────────────────────────────


class TestExecPython:
    def test_python_plugin_dispatches_to_registry(self, tmp_path: Path):
        exec_registry.register_python_executor(
            "_t_py_dict", lambda workspace_path, upstream=None, **kw: {
                "result": "ok",
            },
        )
        plugin = AgentPlugin(
            id="py_dict", name="PyDict", category="tool", description="",
            executor_type="python", python_function="_t_py_dict",
        )
        e = AgentExecutor()
        r = e.execute(plugin, "t", tmp_path)
        assert r.success
        assert '"result": "ok"' in r.output

    def test_python_plugin_str_return_value(self, tmp_path: Path):
        """Non-dict return values are stringified (not json.dumps'd)."""
        exec_registry.register_python_executor(
            "_t_py_str", lambda workspace_path, upstream=None, **kw:
            "plain string",
        )
        plugin = AgentPlugin(
            id="py_str", name="PyStr", category="tool", description="",
            executor_type="python", python_function="_t_py_str",
        )
        e = AgentExecutor()
        r = e.execute(plugin, "t", tmp_path)
        assert r.success
        assert r.output == "plain string"

    def test_python_plugin_forwards_context_kwargs(self, tmp_path: Path):
        received = {}

        def capture(**kwargs):
            received.update(kwargs)
            return {"ok": True}

        exec_registry.register_python_executor("_t_py_ctx", capture)
        plugin = AgentPlugin(
            id="py_ctx", name="PyCtx", category="tool", description="",
            executor_type="python", python_function="_t_py_ctx",
        )
        e = AgentExecutor()
        e.execute(
            plugin, "t", tmp_path,
            context={"strategy_name": "foo", "action": "discover",
                     "description": "desc", "run_dir": "/tmp/r"},
        )
        assert received["strategy_name"] == "foo"
        assert received["action"] == "discover"
        assert received["description"] == "desc"
        assert received["run_dir"] == "/tmp/r"

    def test_python_plugin_unknown_function_returns_error(self, tmp_path: Path):
        plugin = AgentPlugin(
            id="py_missing", name="M", category="tool", description="",
            executor_type="python", python_function="_nope_",
        )
        e = AgentExecutor()
        r = e.execute(plugin, "t", tmp_path)
        assert not r.success
        assert "_nope_" in (r.error or "")


# ── _exec_evaluator ─────────────────────────────────────────────────


class TestExecEvaluator:
    def test_evaluator_extracts_metrics_from_upstream(self, tmp_path: Path):
        seen = {}

        def fake_eval(metrics=None, **kw):
            seen["metrics"] = metrics
            seen["ctx"] = kw
            return {"verdict": "keep", "reason": "ok"}

        exec_registry.register_evaluator("_t_ev", fake_eval)
        plugin = AgentPlugin(
            id="ev", name="E", category="tool", description="",
            executor_type="evaluator", python_function="_t_ev",
        )
        e = AgentExecutor()
        r = e.execute(
            plugin, "t", tmp_path,
            upstream_outputs={"backtest": '{"metrics": {"calmar": 0.7}}'},
            context={"llm_verdict": "keep"},
        )
        assert r.success
        assert seen["metrics"] == {"calmar": 0.7}
        assert seen["ctx"]["llm_verdict"] == "keep"
        assert '"verdict": "keep"' in r.output

    def test_evaluator_strips_meta_keys_from_context(self, tmp_path: Path):
        seen = {}

        def fake_eval(metrics=None, **kw):
            seen.update(kw)
            return {}

        exec_registry.register_evaluator("_t_ev_meta", fake_eval)
        plugin = AgentPlugin(
            id="ev_meta", name="M", category="tool", description="",
            executor_type="evaluator", python_function="_t_ev_meta",
        )
        e = AgentExecutor()
        e.execute(
            plugin, "t", tmp_path,
            context={
                "executor_type": "evaluator",
                "python_function": "_t_ev_meta",
                "tools": "list",
                "input_from": ["a"],
                "timeout": 60,
                "stagnation_count": 2,
            },
        )
        for forbidden in ("executor_type", "python_function",
                          "tools", "input_from", "timeout"):
            assert forbidden not in seen, f"meta key leaked: {forbidden!r}"
        assert seen["stagnation_count"] == 2

    def test_evaluator_str_return(self, tmp_path: Path):
        exec_registry.register_evaluator(
            "_t_ev_str", lambda metrics=None, **kw: "keep",
        )
        plugin = AgentPlugin(
            id="ev_str", name="S", category="tool", description="",
            executor_type="evaluator", python_function="_t_ev_str",
        )
        e = AgentExecutor()
        r = e.execute(plugin, "t", tmp_path)
        assert r.success
        assert r.output == "keep"

    def test_evaluator_unknown_function_returns_error(self, tmp_path: Path):
        plugin = AgentPlugin(
            id="ev_missing", name="M", category="tool", description="",
            executor_type="evaluator", python_function="_nope_",
        )
        e = AgentExecutor()
        r = e.execute(plugin, "t", tmp_path)
        assert not r.success
        assert "_nope_" in (r.error or "")


# ── LLM path error branches ─────────────────────────────────────────


class TestExecLLMErrors:
    def test_missing_prompt_file_returns_error(self, tmp_path: Path):
        plugin = AgentPlugin(
            id="no_prompt", name="N", category="research",
            description="", prompt_file="",
        )
        e = AgentExecutor(llm_config=MagicMock(api_key="sk-test"))
        r = e.execute(plugin, "t", tmp_path)
        assert not r.success
        assert "no prompt_file" in (r.error or "")

    def test_plugin_falls_through_to_llm_when_executor_type_unknown(self, tmp_path: Path):
        """Unknown executor_type → falls through to LLM path (the else branch).

        The LLM path then errors out on missing prompt_file.
        """
        plugin = AgentPlugin(
            id="weird", name="W", category="research",
            description="", prompt_file="",
            executor_type="llm",
        )
        e = AgentExecutor(llm_config=MagicMock(api_key="sk-test"))
        r = e.execute(plugin, "t", tmp_path)
        assert not r.success
        assert "no prompt_file" in (r.error or "")


# ── Top-level exception safety ───────────────────────────────────────


class TestExecuteExceptionSafety:
    def test_unexpected_exception_is_caught(self, tmp_path: Path, monkeypatch):
        """A bug inside dispatch should not propagate — return an error
        AgentExecutionResult instead."""
        plugin = AgentPlugin(
            id="boom", name="Boom", category="research",
            description="", prompt_file="",
            executor_type="llm",
        )
        e = AgentExecutor(llm_config=MagicMock(api_key="sk-test"))
        # Force _exec_llm to raise
        monkeypatch.setattr(
            e, "_exec_llm",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("kaboom")),
        )
        r = e.execute(plugin, "t", tmp_path)
        assert not r.success
        assert "kaboom" in (r.error or "")


# ── Node overrides end-to-end ───────────────────────────────────────


class TestNodeOverrides:
    def test_node_tools_override_replaces_plugin_tools(self, tmp_path: Path):
        plugin = AgentPlugin(
            id="researcher", name="Researcher", category="research",
            description="",
            prompt_file=".prompts/researcher.md",
            tools=("read", "write", "compute_factor"),
        )
        captured = {}

        from strategy_research.kernel.agent import loop as loop_mod
        orig_init = loop_mod.AgentLoop.__init__

        def patched_init(self, *a, **kw):
            orig_init(self, *a, **kw)
            captured["allowed_tools"] = kw.get("allowed_tools")
            self._stream_mode = False
            self.stream_mode = False
            self.client.chat = lambda messages, **k: MagicMock(
                content="ok", tool_calls=[], finish_reason="stop",
            )

        loop_mod.AgentLoop.__init__ = patched_init
        try:
            e = AgentExecutor(llm_config=MagicMock(api_key="sk-test"))
            node = AgentNodeConfig(
                id="researcher", tools_override=["read"], max_iterations=4,
            )
            e.execute(plugin, "t", tmp_path, node=node)
        finally:
            loop_mod.AgentLoop.__init__ = orig_init
        assert captured["allowed_tools"] == ["read"]