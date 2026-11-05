"""Phase B — MCP 工具真实现测试。

Phase B 把以下 7 个 stub 工具接入真实现:
- start_research_goal → GoalStore.replace_goal
- get_research_goal → GoalStore.get_current_goal
- run_backtest → core.backtest.run_backtest_script
- validate_run → core.validation.runner.run_validation
- compute_factor → core.compute_factor.compute_factor + DuckDB
- search_memory → PersistentMemory.find_relevant
- add_memory → PersistentMemory.add
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def server():
    from strategy_research.interfaces.mcp.server import MCPServer
    s = MCPServer()
    s.register_default_tools()
    return s


def _extract_body(result: dict) -> dict:
    """call_tool 返回两种格式:
    - 成功: {"content": [{"type": "text", "text": "<JSON str>"}]}
    - 错误: {"error": "<msg>"}  (handler 抛异常时)

    我们的 handler 内置 try/except, 直接返回 JSON str, 所以通常都是 content。
    """
    if "content" in result:
        text = result["content"][0]["text"]
        return json.loads(text)
    elif "error" in result:
        return {"status": "error", "error": result["error"]}
    else:
        raise AssertionError(f"Unexpected call_tool result: {result}")


@pytest.fixture
def isolated_workspace(tmp_path, monkeypatch):
    """tmp_path 充当 workspace, 避免污染真实 cwd."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ============================================================
# start_research_goal / get_research_goal (真接 GoalStore)
# ============================================================



# ============================================================
# run_backtest / validate_run (真接 backtest + validation)
# ============================================================

class TestBacktestToolsReal:
    """MCP backtest 工具接入真 backtest 引擎。"""

    def test_run_backtest_requires_params(self, server):
        """缺参数时应返回 error, 不崩溃。"""
        result = server.call_tool("run_backtest", {})
        body = _extract_body(result)
        assert body["status"] == "error"

    def test_run_backtest_nonexistent_workspace(self, server):
        """workspace 不存在时 backtest 应报错 (不崩溃)。"""
        result = server.call_tool("run_backtest", {
            "workspace": "/nonexistent/path/that/does/not/exist",
            "strategy": "nonexistent_strat",
        })
        body = _extract_body(result)
        assert body["status"] == "error"
        assert "error" in body

    def test_validate_run_requires_run_dir(self, server):
        result = server.call_tool("validate_run", {})
        body = _extract_body(result)
        assert body["status"] == "error"

    def test_validate_run_nonexistent_run_dir(self, server):
        result = server.call_tool("validate_run", {
            "run_dir": "/nonexistent/run_dir",
        })
        body = _extract_body(result)
        assert body["status"] == "error"


# ============================================================
# compute_factor (真接 compute_factor + DuckDB)
# ============================================================

class TestFactorToolReal:
    """MCP compute_factor 工具接入真 compute_factor DSL + DuckDB。"""

    def test_compute_factor_requires_params(self, server):
        result = server.call_tool("compute_factor", {})
        body = _extract_body(result)
        assert body["status"] == "error"

    def test_compute_factor_nonexistent_workspace(self, server):
        result = server.call_tool("compute_factor", {
            "expression": "ts_return(close, 20)",
            "workspace": "/nonexistent",
            "asset": "000001.SZ",
        })
        body = _extract_body(result)
        assert body["status"] == "error"


# ============================================================
# search_memory / add_memory (真接 PersistentMemory)
# ============================================================

class TestMemoryToolsReal:
    """MCP memory 工具接入真 PersistentMemory (替代空列表 stub)。"""

    def test_add_memory_returns_path(self, server, tmp_path, monkeypatch):
        # 重定向 memory 目录
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))

        result = server.call_tool("add_memory", {
            "title": "MCP test memory",
            "content": "This is a test memory entry created via MCP tool.",
            "memory_type": "project",
        })
        body = _extract_body(result)
        assert body["status"] == "ok"
        assert "path" in body
        assert body["title"] == "MCP test memory"
        # 验证文件确实被创建
        assert Path(body["path"]).exists()

    def test_search_memory_finds_added(self, server, tmp_path, monkeypatch):
        """search_memory 应能检索到 add_memory 新增的条目。"""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))

        # 1. add
        add_result = server.call_tool("add_memory", {
            "title": "quantnodes-specific-tag-xyz",
            "content": "This memory contains the keyword quantnodes and xyz unique tokens.",
            "memory_type": "project",
        })
        add_body = json.loads(add_result["content"][0]["text"])
        if add_body["status"] != "ok":
            pytest.skip("add_memory 失败")

        # 2. search
        result = server.call_tool("search_memory", {
            "query": "quantnodes",
        })
        body = _extract_body(result)
        assert body["query"] == "quantnodes"
        assert body["n_results"] >= 1
        titles = [r["title"] for r in body["results"]]
        assert any("xyz" in t for t in titles)

    def test_search_memory_empty_query(self, server, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        result = server.call_tool("search_memory", {"query": ""})
        body = _extract_body(result)
        # 空 query 应返回 0 结果
        assert body["n_results"] == 0

    def test_add_memory_rejects_empty_title(self, server, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        result = server.call_tool("add_memory", {
            "title": "",
            "content": "Some content",
            "memory_type": "project",
        })
        body = _extract_body(result)
        # PersistentMemory.add 拒绝空 name
        assert body["status"] == "error"


# ============================================================
# 回归: stub 行为已被替换
# ============================================================

class TestStubRemoved:
    """确保旧 stub 行为不再出现 (返回假数据)。"""

    def test_search_memory_no_longer_returns_empty_stub(self, server, tmp_path, monkeypatch):
        """旧 stub search_memory 永远返回 n_results=0 + 空列表."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        # 先 add 一个, 确认能搜到
        server.call_tool("add_memory", {
            "title": "stub-removal-test",
            "content": "stubremoval unique-token-alpha",
            "memory_type": "project",
        })
        result = server.call_tool("search_memory", {"query": "stubremoval"})
        body = _extract_body(result)
        assert body["n_results"] >= 1, "新实现应能找到刚 add 的记忆"

    def test_run_backtest_no_longer_returns_submitted_stub(self, server):
        """旧 stub run_backtest 返回 status='ok' + 'submitted'."""
        result = server.call_tool("run_backtest", {
            "workspace": "/nonexistent",
            "strategy": "nonexistent",
        })
        body = _extract_body(result)
        # 真接实现 → 报错
        assert body["status"] == "error"
        assert "submitted" not in json.dumps(body)


# ============================================================
# add_goal_evidence / update_research_goal_status (Goal CRUD)
# ============================================================



