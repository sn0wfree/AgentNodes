"""H 档补测 —— `core/mcp/server.py` 的 stdio 服务循环与工具校验分支。

call_tool 返回 ``{"content": [{"type": "text", "text": <JSON 字符串>}]}``，
所以断言用 ``_payload()`` helper 解析 content 文本。

本轮还修了 validate_run 的 ``from .validation`` 相对导入 bug（解析到
不存在的 core/mcp/validation，MCP 客户端调用永远报 No module named）。
"""
from __future__ import annotations

import json

import pytest

from agentnodes.interfaces.mcp.server import MCPServer, MCPTool


def _payload(server, tool: str, args: dict) -> dict:
    """call_tool → content[0].text → JSON dict。"""
    out = server.call_tool(tool, args)
    return json.loads(out["content"][0]["text"])


def _resp_line(out: str, req_id: int | None = None) -> dict:
    """从 serve_stdio 的多行输出里取出目标响应行。"""
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if req_id is None:
            if "result" in obj or "error" in obj:
                return obj
            continue
        if obj.get("id") == req_id:
            return obj
    raise AssertionError(f"no response line for id={req_id} in: {out!r}")


def _serve(server, lines: list[str], capsys) -> str:
    """驱动 serve_stdio 的 stdin 循环，用 capsys 捕获 stdout。"""
    import sys as _sys

    class FakeStdin:
        def __iter__(self):
            return iter(lines)

    real_stdin = _sys.stdin
    _sys.stdin = FakeStdin()
    try:
        server.serve_stdio()
    finally:
        _sys.stdin = real_stdin
    return capsys.readouterr().out


# ── serve_stdio（传输层） ───────────────────────────────────


def test_stdio_lists_tools_on_tools_list(capsys):
    server = MCPServer()
    server.register(MCPTool(name="t1", description="d",
                            parameters={"type": "object", "properties": {}},
                            handler=lambda **kw: "x"))
    out = _serve(server, [json.dumps({"method": "tools/list", "id": 1})], capsys)
    resp = _resp_line(out, req_id=1)
    assert resp["result"]["tools"][0]["name"] == "t1"


def test_stdio_call_tool_on_tools_call(capsys):
    server = MCPServer()
    server.register(MCPTool(name="adder", description="d",
                            parameters={"type": "object", "properties": {}},
                            handler=lambda a=0, b=0: str(int(a) + int(b))))
    req = json.dumps({"method": "tools/call", "id": 2,
                      "params": {"name": "adder", "arguments": {"a": 2, "b": 3}}})
    out = _serve(server, [req], capsys)
    resp = _resp_line(out, req_id=2)
    assert resp["result"]["content"][0]["text"] == "5"


def test_stdio_unknown_method(capsys):
    server = MCPServer()
    out = _serve(server, [json.dumps({"method": "no/such", "id": 3})], capsys)
    resp = _resp_line(out, req_id=3)
    assert "Unknown method" in resp["result"]["error"]


def test_stdio_bad_json_prints_error(capsys):
    server = MCPServer()
    out = _serve(server, ["{not json"], capsys)
    assert "Invalid JSON" in _resp_line(out)["error"]


def test_stdio_skips_blank_lines_and_omits_id_when_absent(capsys):
    server = MCPServer()
    out = _serve(server, ["", json.dumps({"method": "tools/list"})], capsys)
    lines = [json.loads(x) for x in out.splitlines() if x.strip()]
    # serve_stdio 的契约：无 req_id 时不回响应行（源码 879 行判空），只有首行 announce
    assert len(lines) == 1
    assert "tools" in lines[0]


def test_stdio_announces_server_and_tools_first(capsys):
    server = MCPServer(name="my-srv")
    out = _serve(server, [json.dumps({"method": "tools/list"})], capsys)
    first = json.loads(out.splitlines()[0])
    assert first["server"] == "my-srv"
    assert isinstance(first["tools"], list)


def test_stdio_id_echoed_on_tool_error(capsys):
    """工具抛异常 → call_tool 返回 error dict，仍带请求 id 回显。"""
    server = MCPServer()
    server.register(MCPTool(name="boom", description="d",
                            parameters={"type": "object", "properties": {}},
                            handler=lambda **kw: (_ for _ in ()).throw(
                                ValueError("handler died"))))
    req = json.dumps({"method": "tools/call", "id": 9,
                      "params": {"name": "boom", "arguments": {}}})
    out = _serve(server, [req], capsys)
    resp = _resp_line(out, req_id=9)
    assert resp["id"] == 9
    assert "ValueError" in resp["result"]["error"]


# ── update_research_goal_status 校验分支 ────────────────────


@pytest.fixture
def server():
    s = MCPServer()
    s.register_default_tools()
    return s








# ── add_goal_evidence 校验分支 ──────────────────────────────






# ── validate_run 校验分支 ───────────────────────────────────






