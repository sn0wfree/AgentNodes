"""Tests for swarm MCP execution tools: run_swarm, get_swarm_status."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest


@pytest.fixture
def server():
    from strategy_research.interfaces.mcp.server import MCPServer
    s = MCPServer()
    s.register_default_tools()
    return s


def _extract_body(result: dict) -> dict:
    if "content" in result:
        text = result["content"][0]["text"]
        return json.loads(text)
    elif "error" in result:
        return {"status": "error", "error": result["error"]}
    else:
        raise AssertionError(f"Unexpected result: {result}")


# ============================================================
# run_swarm
# ============================================================


class TestRunSwarm:
    def test_requires_preset_name(self, server):
        result = server.call_tool("run_swarm", {})
        body = _extract_body(result)
        assert body["status"] == "error"
        assert "preset_name" in body["error"].lower()

    def test_requires_workspace(self, server):
        result = server.call_tool("run_swarm", {
            "preset_name": "quant_research_team",
        })
        body = _extract_body(result)
        assert body["status"] == "error"
        assert "workspace" in body["error"].lower()

    def test_preset_not_found(self, server, tmp_path):
        result = server.call_tool("run_swarm", {
            "preset_name": "nonexistent_preset_xyz",
            "workspace": str(tmp_path),
        })
        body = _extract_body(result)
        assert body["status"] == "error"
        assert "not found" in body["error"].lower()

    def test_stores_result(self, server, tmp_path):
        """执行完成后 RunStore 应有结果。"""
        # 用 mock 避免真正调用 LLM
        with patch("strategy_research.kernel.swarm.runtime.SwarmRuntime.execute") as mock_exec:
            from strategy_research.kernel.swarm.runtime import AgentResult, SwarmResult
            from strategy_research.core.workflow.types import AgentStatus

            mock_exec.return_value = SwarmResult(
                run_id="test_run_001",
                preset_name="quant_research_team",
                success=True,
                elapsed_s=1.5,
                agent_results={
                    "researcher": AgentResult(
                        agent_id="researcher",
                        status=AgentStatus.SUCCESS,
                        output="test output",
                        elapsed_s=1.0,
                    ),
                },
                final_output="test output",
            )

            result = server.call_tool("run_swarm", {
                "preset_name": "quant_research_team",
                "workspace": str(tmp_path),
                "task": "test task",
            })
            body = _extract_body(result)
            assert body["status"] == "ok"
            assert body["run_id"] == "test_run_001"
            assert body["success"] is True

    def test_get_status_after_run(self, server, tmp_path):
        """run_swarm 后 get_swarm_status 应能找到。"""
        with patch("strategy_research.kernel.swarm.runtime.SwarmRuntime.execute") as mock_exec:
            from strategy_research.kernel.swarm.runtime import SwarmResult

            mock_exec.return_value = SwarmResult(
                run_id="status_test_001",
                preset_name="quant_research_team",
                success=True,
                elapsed_s=1.0,
                agent_results={},
                final_output="done",
            )

            run_result = server.call_tool("run_swarm", {
                "preset_name": "quant_research_team",
                "workspace": str(tmp_path),
            })
            run_body = _extract_body(run_result)
            assert run_body["status"] == "ok"

            # 查询状态
            status_result = server.call_tool("get_swarm_status", {
                "run_id": "status_test_001",
            })
            status_body = _extract_body(status_result)
            assert status_body["status"] == "ok"
            assert status_body["run_status"] == "completed"


# ============================================================
# get_swarm_status
# ============================================================


class TestGetSwarmStatus:
    def test_requires_run_id(self, server):
        result = server.call_tool("get_swarm_status", {})
        body = _extract_body(result)
        assert body["status"] == "error"
        assert "run_id" in body["error"].lower()

    def test_not_found(self, server):
        result = server.call_tool("get_swarm_status", {
            "run_id": "nonexistent_run_id",
        })
        body = _extract_body(result)
        assert body["status"] == "ok"
        assert body["run_status"] == "not_found"

    def test_format(self, server):
        result = server.call_tool("get_swarm_status", {
            "run_id": "test",
        })
        body = _extract_body(result)
        assert "status" in body
        assert "run_status" in body
