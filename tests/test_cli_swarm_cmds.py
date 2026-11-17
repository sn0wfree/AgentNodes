"""CLI swarm 命令测试（此前覆盖率 6.1%）。

list/inspect 走真实的 37 个内置 preset；run 用 monkeypatch 替身
SwarmRuntime（不真跑 agents）；cancel 用假 run_id 走未找到分支。
"""
from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import pytest

from agentnodes.interfaces.cli.commands.swarm import (
    cmd_swarm_cancel,
    cmd_swarm_inspect,
    cmd_swarm_list,
    cmd_swarm_run,
)


def _args(**kw) -> argparse.Namespace:
    return argparse.Namespace(**kw)


@pytest.fixture
def preset_name():
    from agentnodes.kernel.swarm import list_presets

    from agentnodes.apps import swarm_presets as sp

    presets = list_presets(sp.PRESETS_DIR)
    assert presets, "内置 preset 资产缺失"
    return presets[0].name


class TestSwarmList:
    def test_lists_all_presets(self, capsys):
        assert cmd_swarm_list(_args()) == 0
        out = capsys.readouterr().out
        assert "=== Swarm Presets" in out
        assert "asset_allocation_committee" in out or "agents" in out

    def test_preset_count_matches_assets(self, capsys):
        from agentnodes.kernel.swarm import list_presets

        from agentnodes.apps import swarm_presets as sp

        n = len(list_presets(sp.PRESETS_DIR))
        cmd_swarm_list(_args())
        assert f"共 {n} 个" in capsys.readouterr().out


class TestSwarmInspect:
    def test_inspect_real_preset(self, preset_name, capsys):
        assert cmd_swarm_inspect(_args(name=preset_name)) == 0
        out = capsys.readouterr().out
        assert f"=== {preset_name} ===" in out
        assert "DAG 结构" in out
        assert "Agent 详情" in out

    def test_inspect_missing_returns_1(self, capsys):
        assert cmd_swarm_inspect(_args(name="no-such-preset")) == 1
        assert "不存在" in capsys.readouterr().out


class TestSwarmCancel:
    def test_cancel_unknown_run_returns_1(self, capsys):
        assert cmd_swarm_cancel(_args(run_id="no-such-run")) == 1
        assert "未找到运行中的 swarm" in capsys.readouterr().out


class TestSwarmRun:
    def test_run_with_stubbed_runtime(self, preset_name, capsys, monkeypatch, tmp_path):
        """cmd_swarm_run 的执行路径（SwarmRuntime 替身，不真跑 agents）。"""
        import agentnodes.kernel.swarm as swarm_mod

        class StubResult:
            run_id = "stub-run-1"
            preset_name = preset_name
            elapsed_s = 1.23
            success = True
            agent_results = {
                "planner": SimpleNamespace(status=SimpleNamespace(value="completed"), elapsed_s=0.5),
                "executor": SimpleNamespace(status=SimpleNamespace(value="failed"), elapsed_s=0.7),
            }

        class StubRuntime:
            def __init__(self):
                self.calls = []

            def execute(self, preset, workspace, task):
                self.calls.append((preset.name, workspace, task))
                return StubResult()

        stub = StubRuntime()
        monkeypatch.setattr(swarm_mod, "SwarmRuntime", lambda: stub)

        rc = cmd_swarm_run(_args(name=preset_name, workspace=str(tmp_path), task="做研究"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "=== Swarm 执行完成 ===" in out
        assert "stub-run-1" in out
        assert stub.calls and stub.calls[0][0] == preset_name
        assert stub.calls[0][2] == "做研究"

    def test_run_default_task_fills_preset_name(self, preset_name, capsys, monkeypatch, tmp_path):
        """task 缺省 → 用 preset 名生成任务文案。"""
        import agentnodes.kernel.swarm as swarm_mod

        captured: dict = {}

        class StubRuntime:
            def execute(self, preset, workspace, task):
                captured["task"] = task
                return SimpleNamespace(
                    run_id="r", preset_name=preset.name, elapsed_s=0.0,
                    success=True, agent_results={},
                )

        monkeypatch.setattr(swarm_mod, "SwarmRuntime", lambda: StubRuntime())
        assert cmd_swarm_run(_args(name=preset_name, workspace=str(tmp_path), task=None)) == 0
        assert preset_name in captured["task"]

    def test_run_missing_preset_returns_1(self, capsys, tmp_path):
        assert cmd_swarm_run(_args(name="no-such", workspace=str(tmp_path), task=None)) == 1
