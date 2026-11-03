"""Tests for workflow node dispatch (node_types.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research.kernel.swarm.runtime import AgentStatus
from strategy_research.kernel.workflow.definition import WorkflowNode
from strategy_research.kernel.workflow.node_types import (
    NODE_METADATA,
    NodeContext,
    NodeDispatchError,
    NodeExecutors,
    dispatch_node,
)


class FakeLoop:
    """Scripted loop factory: returns canned answers per role."""

    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> str:
        self.calls.append(kwargs)
        role = kwargs.get("role", "")
        if role in self.answers:
            return self.answers[role]
        return kwargs.get("task", "默认执行完成")

    def factory(self):
        return self


def make_ctx(workspace, **overrides) -> NodeContext:
    kwargs = dict(
        workspace=workspace, strategy_name="demo", objective="研究目标",
        loop_factory=lambda **kw: FakeLoop({})(**kw),
    )
    kwargs.update(overrides)
    return NodeContext(**kwargs)


# ── Metadata registry ─────────────────────────────────────────


class TestMetadata:
    def test_six_types_registered(self):
        assert set(NODE_METADATA.keys()) == {
            "llm_agent", "planner", "evaluator", "approval", "python", "tool",
        }

    def test_metadata_fields(self):
        meta = NODE_METADATA["planner"]
        assert meta.label
        assert meta.description
        assert isinstance(meta.config_schema, dict)


# ── llm_agent node ────────────────────────────────────────────


class TestLLMAgent:
    def test_success_envelope(self, tmp_path: Path):
        fake = FakeLoop({"researcher": "研究结论 A"})
        node = WorkflowNode(id="n1", type="llm_agent", config={"role": "researcher"})
        result = dispatch_node(node, make_ctx(tmp_path, loop_factory=fake))
        assert result.status == AgentStatus.SUCCESS
        assert result.summary == "研究结论 A"
        assert result.artifacts == {}

    def test_prompt_text_appended(self, tmp_path: Path):
        fake = FakeLoop({})
        node = WorkflowNode(id="n1", type="llm_agent",
                            config={"role": "researcher", "prompt_text": "附加指令"})
        dispatch_node(node, make_ctx(tmp_path, loop_factory=fake))
        assert "附加指令" in fake.calls[0]["task"]

    def test_tools_override_passed(self, tmp_path: Path):
        fake = FakeLoop({})
        node = WorkflowNode(id="n1", type="llm_agent",
                            config={"role": "researcher", "tools": ["read", "websearch"]})
        dispatch_node(node, make_ctx(tmp_path, loop_factory=fake))
        assert fake.calls[0].get("tools_override") == ["read", "websearch"]

    def test_upstream_context_injected(self, tmp_path: Path):
        from strategy_research.kernel.swarm.runtime import AgentResult
        fake = FakeLoop({})
        ctx = make_ctx(tmp_path, loop_factory=fake)
        ctx.upstream = {"prev": AgentResult(agent_id="prev", status=AgentStatus.SUCCESS,
                                            summary="上游摘要")}
        dispatch_node(WorkflowNode(id="n1", type="llm_agent", config={"role": "researcher"}), ctx)
        assert "上游摘要" in fake.calls[0]["context"]

    def test_loop_failure_returns_error_envelope(self, tmp_path: Path):
        def boom(**kwargs):
            raise RuntimeError("llm down")
        node = WorkflowNode(id="n1", type="llm_agent", config={"role": "researcher"})
        result = dispatch_node(node, make_ctx(tmp_path, loop_factory=boom))
        assert result.status == AgentStatus.ERROR
        assert "llm down" in (result.error or "")

    def test_summary_truncated(self, tmp_path: Path):
        long_text = "x" * 1000
        fake = FakeLoop({"researcher": long_text})
        node = WorkflowNode(id="n1", type="llm_agent", config={"role": "researcher"})
        result = dispatch_node(node, make_ctx(tmp_path, loop_factory=fake))
        assert len(result.summary) <= 301


# ── planner node ──────────────────────────────────────────────


class TestPlanner:
    PLAN = json.dumps({"plan": [
        {"id": "step_001", "title": "假设", "description": "提出假设并验证", "type": "llm_agent",
         "tools": ["read"], "depends_on": []},
        {"id": "step_002", "title": "回测", "description": "运行回测验证假设", "type": "llm_agent",
         "tools": ["run_backtest"], "depends_on": ["step_001"]},
    ]})

    def test_parses_plan_artifacts(self, tmp_path: Path):
        fake = FakeLoop({"planner": self.PLAN})
        node = WorkflowNode(id="p", type="planner", config={})
        result = dispatch_node(node, make_ctx(tmp_path, loop_factory=fake))
        assert result.status == AgentStatus.SUCCESS
        plan = result.artifacts["plan"]
        assert len(plan) == 2
        assert plan[0]["id"] == "step_001"
        assert plan[1]["depends_on"] == ["step_001"]

    def test_invalid_output_falls_back_to_pipeline(self, tmp_path: Path):
        fake = FakeLoop({"planner": "我不是JSON"})
        node = WorkflowNode(id="p", type="planner", config={})
        result = dispatch_node(node, make_ctx(tmp_path, loop_factory=fake))
        assert result.status == AgentStatus.SUCCESS
        plan = result.artifacts["plan"]
        assert 3 <= len(plan) <= 8
        assert "兜底" in result.summary

    def test_empty_plan_falls_back(self, tmp_path: Path):
        fake = FakeLoop({"planner": '{"plan": []}'})
        node = WorkflowNode(id="p", type="planner", config={})
        result = dispatch_node(node, make_ctx(tmp_path, loop_factory=fake))
        assert len(result.artifacts["plan"]) >= 3

    def test_unknown_dep_references_dropped(self, tmp_path: Path):
        fake = FakeLoop({"planner": json.dumps({"plan": [
            {"id": "s1", "title": "t", "description": "描述内容", "type": "llm_agent",
             "tools": [], "depends_on": ["ghost", "s1"]},
        ]})})
        node = WorkflowNode(id="p", type="planner", config={})
        result = dispatch_node(node, make_ctx(tmp_path, loop_factory=fake))
        plan = result.artifacts["plan"]
        assert plan[0]["depends_on"] == []

    def test_fallback_respects_max_steps(self, tmp_path: Path):
        fake = FakeLoop({"planner": "bad"})
        ctx = make_ctx(tmp_path, loop_factory=fake)
        ctx.params = {"planner": {"max_steps": 3}, "summary": {"max_chars": 300}}
        node = WorkflowNode(id="p", type="planner", config={})
        result = dispatch_node(node, ctx)
        assert len(result.artifacts["plan"]) == 3


# ── evaluator node ────────────────────────────────────────────


class TestEvaluator:
    def test_decision_parsed(self, tmp_path: Path):
        fake = FakeLoop({"evaluator": json.dumps(
            {"verdict": "replan", "reason": "数据不足", "findings": ["缺数据"]})})
        node = WorkflowNode(id="e", type="evaluator", config={})
        result = dispatch_node(node, make_ctx(tmp_path, loop_factory=fake))
        assert result.status == AgentStatus.SUCCESS
        decision = result.artifacts["decision"]
        assert decision["verdict"] == "replan"
        assert decision["findings"] == ["缺数据"]

    def test_invalid_verdict_forced_to_continue(self, tmp_path: Path):
        fake = FakeLoop({"evaluator": json.dumps({"verdict": "banana"})})
        node = WorkflowNode(id="e", type="evaluator", config={})
        result = dispatch_node(node, make_ctx(tmp_path, loop_factory=fake))
        assert result.artifacts["decision"]["verdict"] == "continue"

    def test_parse_failure_defaults_continue(self, tmp_path: Path):
        fake = FakeLoop({"evaluator": "无法解析"})
        node = WorkflowNode(id="e", type="evaluator", config={})
        result = dispatch_node(node, make_ctx(tmp_path, loop_factory=fake))
        assert result.artifacts["decision"]["verdict"] == "continue"

    def test_loop_failure_rule_layer_continue(self, tmp_path: Path):
        def boom(**kwargs):
            raise RuntimeError("down")
        node = WorkflowNode(id="e", type="evaluator", config={})
        result = dispatch_node(node, make_ctx(tmp_path, loop_factory=boom))
        assert result.status == AgentStatus.SUCCESS
        assert result.artifacts["decision"]["verdict"] == "continue"


# ── python / tool nodes ───────────────────────────────────────


class TestPythonTool:
    def test_python_calls_registered_function(self, tmp_path: Path):
        NodeExecutors.reset()
        NodeExecutors.register("my_func", lambda **kw: {"summary": "计算结果 42", "value": 42})
        node = WorkflowNode(id="f", type="python", config={"function": "my_func"})
        result = dispatch_node(node, make_ctx(tmp_path))
        assert result.status == AgentStatus.SUCCESS
        assert "计算结果 42" in result.summary
        assert result.artifacts["value"] == 42

    def test_python_missing_function_raises(self, tmp_path: Path):
        NodeExecutors.reset()
        node = WorkflowNode(id="f", type="python", config={"function": "nope"})
        with pytest.raises(NodeDispatchError, match="no python executor"):
            dispatch_node(node, make_ctx(tmp_path))

    def test_tool_missing_raises(self, tmp_path: Path):
        NodeExecutors.reset()
        node = WorkflowNode(id="t", type="tool", config={"tool": "ghost_tool"})
        with pytest.raises(NodeDispatchError, match="no tool executor"):
            dispatch_node(node, make_ctx(tmp_path))

    def test_tool_wrapper_available(self):
        from strategy_research.kernel.workflow.node_types import register_builtin_tool_executors
        count = register_builtin_tool_executors()
        assert count >= 4
        assert NodeExecutors.get("run_backtest") is not None
        assert NodeExecutors.get("check_data") is not None

    def test_unknown_node_type_raises(self, tmp_path: Path):
        node = WorkflowNode(id="x", type="nope", config={})
        with pytest.raises(NodeDispatchError, match="unsupported type"):
            dispatch_node(node, make_ctx(tmp_path))
