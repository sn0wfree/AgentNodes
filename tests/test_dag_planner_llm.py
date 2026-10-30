"""DAGPlanner LLM 路径测试——_plan_via_llm 全分支（打点 mock，不触网）。

覆盖 docs/coverage-90-plan-20261030.md：`dag_planner.py` 原 57.7%，
缺口集中在 LLM 路径（125-158）与格式化 helpers。全部经
`_llm_is_available`/`_call_llm`/`PromptBuilderFactory` 打点。
"""
from __future__ import annotations

import json

import pytest

from strategy_research.core.agent.plugin import AgentPlugin
from strategy_research.core.agent.registry import AgentPluginRegistry
from strategy_research.core.study.dag_planner import DAGPlanner, PlannerConstraints


def _plugin(pid: str, keywords=()) -> AgentPlugin:
    return AgentPlugin(
        id=pid, name=pid, category="analysis", executor_type="agent",
        description=f"{pid} plugin", keywords=list(keywords),
        requires=[], optional=False,
    )


@pytest.fixture()
def reg() -> AgentPluginRegistry:
    r = AgentPluginRegistry()
    for pid in ("researcher", "strategist", "backtest", "risk_controller"):
        r.register(_plugin(pid))
    return r


class _FakeBuilderFactory:
    """替代 PromptBuilderFactory.get(...).build_system_prompt(...)。"""

    def __init__(self, prompt: str | None):
        self._prompt = prompt

    def build_system_prompt(self, role, ctx):
        return self._prompt


@pytest.fixture()
def llm_planner(reg, monkeypatch):
    """LLM 可用 + prompt 就绪的 planner；_call_llm 由各测试覆写。

    注意：`_plan_via_llm` 内部是局部 import
    `from .agent.prompt_builder import PromptBuilderFactory`——补丁必须
    打在源头模块，不能打 dag_planner 命名空间。
    """
    p = DAGPlanner(registry=reg)
    p._llm_is_available = lambda: True  # type: ignore[method-assign]
    import strategy_research.core.agent.prompt_builder as pb

    monkeypatch.setattr(
        pb, "PromptBuilderFactory",
        type("PBF", (), {"get": classmethod(
            lambda cls, role: _FakeBuilderFactory("planner system prompt"))}),
        raising=False,
    )
    return p


def test_llm_selection_used_when_valid(llm_planner):
    llm_planner._call_llm = lambda s, u: json.dumps(  # type: ignore
        {"selected": ["researcher", "strategist"], "reasoning": "r"})
    result = llm_planner.plan("目标")
    assert "researcher" in result.selected_agents
    assert "strategist" in result.selected_agents


def test_llm_none_prompt_falls_back(llm_planner, monkeypatch):
    """build_system_prompt 返回空 → 回退关键字。"""
    import strategy_research.core.agent.prompt_builder as pb

    monkeypatch.setattr(
        pb, "PromptBuilderFactory",
        type("PBF", (), {"get": classmethod(
            lambda cls, role: _FakeBuilderFactory(""))}),
        raising=False,
    )
    result = llm_planner.plan("研究")
    assert "researcher" in result.selected_agents


def test_llm_garbage_falls_back(llm_planner):
    llm_planner._call_llm = lambda s, u: "完全不是 JSON"  # type: ignore
    result = llm_planner.plan("研究")
    assert "researcher" in result.selected_agents  # 关键字回退


def test_llm_selected_not_list_falls_back(llm_planner):
    llm_planner._call_llm = lambda s, u: '{"selected": "researcher"}'  # type: ignore
    result = llm_planner.plan("研究")
    assert "researcher" in result.selected_agents


def test_llm_empty_selection_is_respected_not_fallback(llm_planner):
    """LLM 明确返回空 selected → 尊重其决策（空计划），不回退关键字。

    锁定实际契约：只有 selected 非列表 / 响应非 dict / 调用失败 /
    prompt 缺失才回退关键字；LLM 的明确空选视为有效决策。
    """
    llm_planner._call_llm = lambda s, u: '{}'  # type: ignore
    result = llm_planner.plan("研究")
    assert result.selected_agents == []


def test_llm_user_prompt_contains_catalog_and_constraints(llm_planner):
    captured: dict[str, str] = {}

    def fake_call(system, user):
        captured["system"] = system
        captured["user"] = user
        return json.dumps({"selected": ["researcher"]})

    llm_planner._call_llm = fake_call  # type: ignore
    llm_planner.plan(
        "动量研究",
        PlannerConstraints(max_agents=5, force_agents=["backtest"],
                           exclude_agents=["strategist"]),
    )
    assert "动量研究" in captured["user"]
    assert "最多选择 5" in captured["user"]
    assert "backtest" in captured["user"]
    assert "排除" in captured["user"]
    assert "planner system prompt" in captured["system"]


def test_format_catalog_and_presets_render(reg, tmp_path):
    p = DAGPlanner(registry=reg, preset_yaml_dir=tmp_path / "missing")
    assert p._format_catalog().count("[required]") == 4
    # 目录不存在 → 占位文本
    assert p._format_presets() == "(no presets available)"
    # 目录存在但无 goal_*.yaml → 空串
    (tmp_path / "empty").mkdir()
    p2 = DAGPlanner(registry=reg, preset_yaml_dir=tmp_path / "empty")
    assert p2._format_presets() == ""
    # 有 goal_*.yaml → 列出 stem（不含无关文件）
    (tmp_path / "goal_momentum.yaml").write_text("x", encoding="utf-8")
    (tmp_path / "unrelated.yaml").write_text("x", encoding="utf-8")
    p3 = DAGPlanner(registry=reg, preset_yaml_dir=tmp_path)
    presets = p3._format_presets()
    assert "momentum" in presets and "unrelated" not in presets


def test_build_config_filters_deps_to_selected(reg):
    p = DAGPlanner(registry=reg)
    cfg = p._build_config(["researcher", "strategist", "backtest"], "目标 X")
    # 标准管线邻接里 strategist 依赖 researcher；backtest 依赖 strategist
    assert cfg.dag["strategist"] == ["researcher"]
    assert set(cfg.dag["backtest"]) <= {"researcher", "strategist"}
    assert "目标 X" in cfg.description
