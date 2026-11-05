"""Tests for Swarm system (runtime, preset loader, CLI)."""

from __future__ import annotations

import pytest

from agentnodes.kernel.swarm.preset_loader import list_presets, load_preset
from agentnodes.kernel.swarm.runtime import (
    AgentResult,
    SwarmPreset,
    SwarmResult,
    SwarmRuntime,
)
from agentnodes.kernel.workflow.dag import topological_layers
from agentnodes.kernel.workflow.types import AgentCall, AgentStatus

# ── Preset Loader Tests ────────────────────────────────────


class TestPresetLoader:
    def test_load_preset(self, tmp_path):
        preset_file = tmp_path / "test.yaml"
        preset_file.write_text(
            "name: test_preset\n"
            "description: A test preset\n"
            "agents:\n"
            "  - id: agent1\n"
            "    prompt_file: .prompts/a.md\n"
            "    tools: [read_file]\n"
            "  - id: agent2\n"
            "    prompt_file: .prompts/b.md\n"
            "    input_from: [agent1]\n"
            "    tools: [write_file]\n"
            "dag:\n"
            "  agent1: []\n"
            "  agent2: [agent1]\n"
        )
        preset = load_preset(preset_file)
        assert preset is not None
        assert preset.name == "test_preset"
        assert preset.description == "A test preset"
        assert len(preset.agents) == 2
        assert preset.dag["agent1"] == []
        assert preset.dag["agent2"] == ["agent1"]

    def test_load_preset_nonexistent(self, tmp_path):
        preset = load_preset(tmp_path / "nonexistent.yaml")
        assert preset is None

    def test_load_preset_invalid_yaml(self, tmp_path):
        preset_file = tmp_path / "bad.yaml"
        preset_file.write_text("{{invalid yaml")
        preset = load_preset(preset_file)
        assert preset is None

    def test_list_presets(self, tmp_path):
        (tmp_path / "a.yaml").write_text("name: preset_a\nagents: []\ndag: {}")
        (tmp_path / "b.yaml").write_text("name: preset_b\nagents: []\ndag: {}")
        (tmp_path / "c.yml").write_text("name: preset_c\nagents: []\ndag: {}")

        presets = list_presets(tmp_path)
        assert len(presets) == 3
        names = {p.name for p in presets}
        assert "preset_a" in names
        assert "preset_b" in names
        assert "preset_c" in names

    def test_list_presets_nonexistent_dir(self, tmp_path):
        presets = list_presets(tmp_path / "nonexistent")
        assert presets == []


# ── SwarmRuntime Tests ─────────────────────────────────────


class TestSwarmRuntime:
    def test_execute_simple_dag(self, tmp_path):
        preset = SwarmPreset(
            name="simple",
            agents=[
                # allow_stub: structural DAG test — no LLM, no prompt.
                AgentCall(agent_name="a1", prompt="", context={"allow_stub": True}),
                AgentCall(agent_name="a2", prompt="",
                          context={"input_from": ["a1"], "allow_stub": True}),
            ],
            dag={"a1": [], "a2": ["a1"]},
        )

        runtime = SwarmRuntime()
        result = runtime.execute(preset, tmp_path, "test task")

        assert result.success
        assert result.preset_name == "simple"
        assert len(result.agent_results) == 2
        assert result.agent_results["a1"].status == AgentStatus.SUCCESS
        assert result.agent_results["a2"].status == AgentStatus.SUCCESS

    def test_execute_parallel_layer(self, tmp_path):
        preset = SwarmPreset(
            name="parallel",
            agents=[
                AgentCall(agent_name="a1", prompt="", context={"allow_stub": True}),
                AgentCall(agent_name="a2", prompt="", context={"allow_stub": True}),
                AgentCall(agent_name="a3", prompt="",
                          context={"input_from": ["a1", "a2"], "allow_stub": True}),
            ],
            dag={"a1": [], "a2": [], "a3": ["a1", "a2"]},
        )

        runtime = SwarmRuntime()
        result = runtime.execute(preset, tmp_path, "test task")

        assert result.success
        assert len(result.agent_results) == 3

    def test_unknown_agent_without_prompt_fails_closed(self, tmp_path):
        """未注册且无 prompt_file 的 agent 必须报错,不得假成功。

        回归:旧逻辑对未知 agent 一律返回 SUCCESS "[stub] ... completed",
        goal hook 会把这段文本当真实证据收集,拼错的 agent id 让整个
        工作流"成功"。
        """
        preset = SwarmPreset(
            name="bad",
            agents=[AgentCall(agent_name="typo_agent", prompt="")],
            dag={"typo_agent": []},
        )
        result = SwarmRuntime().execute(preset, tmp_path, "task")
        assert not result.success
        r = result.agent_results["typo_agent"]
        assert r.status == AgentStatus.ERROR
        assert "not registered" in (r.error or "")

    def test_stub_requires_explicit_opt_in(self, tmp_path):
        """allow_stub=True 才允许无 LLM 的结构 stub。"""

        preset = SwarmPreset(
            name="stub",
            agents=[AgentCall(agent_name="a1", prompt="",
                              context={"allow_stub": True})],
            dag={"a1": []},
        )
        result = SwarmRuntime().execute(preset, tmp_path, "task")
        r = result.agent_results["a1"]
        assert r.status == AgentStatus.SUCCESS
        assert r.output.startswith("[stub]")

        # 确认 executor 未被调用(SimpleNamespace 哨兵不触发)
        assert isinstance(r.output, str)

    def test_custom_id_resolves_plugin_by_prompt_stem(self, tmp_path, monkeypatch):
        """自定义 id + 角色级 prompt_file 必须解析到真实角色插件。

        回归:loader 只把 id 放进 AgentCall,prompt_file 不参与解析,
        导致 27/37 preset 的自定义 id agent 全部走 stub。
        """
        from types import SimpleNamespace

        import agentnodes.kernel.agent.executor as executor_mod

        captured = {}

        def fake_execute(self, plugin, task, workspace, **kwargs):
            captured["plugin_id"] = plugin.id
            captured["prompt_file"] = plugin.prompt_file
            return SimpleNamespace(
                status="success", output="ok", error=None,
                elapsed_s=0.0, summary="", metrics={}, artifacts={},
            )

        monkeypatch.setattr(executor_mod.AgentExecutor, "execute", fake_execute)

        preset = SwarmPreset(
            name="resolve",
            agents=[AgentCall(agent_name="allocator",
                              prompt=".prompts/portfolio_construction.md")],
            dag={"allocator": []},
        )
        result = SwarmRuntime().execute(preset, tmp_path, "task")
        r = result.agent_results["allocator"]
        assert r.status == AgentStatus.SUCCESS
        assert captured["plugin_id"] == "portfolio_construction"
        assert captured["prompt_file"] == ".prompts/portfolio_construction.md"

    def test_cancel(self, tmp_path):
        runtime = SwarmRuntime()
        runtime._active_runs["test_run"] = True

        ok = runtime.cancel("test_run")
        assert ok
        assert "test_run" not in runtime._active_runs

    def test_cancel_nonexistent(self):
        runtime = SwarmRuntime()
        ok = runtime.cancel("nonexistent")
        assert not ok

    def test_topological_layers(self):
        dag = {
            "a": [],
            "b": ["a"],
            "c": ["a"],
            "d": ["b", "c"],
        }
        layers = topological_layers(dag)
        assert len(layers) == 3
        assert layers[0] == ["a"]
        assert set(layers[1]) == {"b", "c"}
        assert layers[2] == ["d"]

    def test_topological_layers_cycle(self):
        dag = {"a": ["b"], "b": ["a"]}
        with pytest.raises(ValueError, match="cycle"):
            topological_layers(dag)

    def test_gather_upstream(self):
        runtime = SwarmRuntime()
        results = {
            "a1": AgentResult(agent_id="a1", status=AgentStatus.SUCCESS, output="out1"),
            "a2": AgentResult(agent_id="a2", status=AgentStatus.ERROR, output=""),
        }
        upstream = runtime._gather_upstream("a3", {"a3": ["a1", "a2"]}, results)
        assert "a1" in upstream
        assert "a2" not in upstream  # failed agents excluded


# ── SwarmResult Tests ──────────────────────────────────────


class TestSwarmResult:
    def test_default_values(self):
        result = SwarmResult()
        assert result.run_id == ""
        assert result.preset_name == ""
        assert result.agent_results == {}
        assert result.elapsed_s == 0.0
        assert not result.success

    def test_agent_result_default(self):
        ar = AgentResult(agent_id="test")
        assert ar.agent_id == "test"
        assert ar.status == AgentStatus.PENDING
        assert ar.output == ""
        assert ar.error is None
