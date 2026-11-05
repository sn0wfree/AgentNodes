"""Tests for ShellExecTool — bash command execution for the agent.

Covers:
- Basic command execution (echo, ls)
- Exit code handling
- stderr capture
- Timeout enforcement
- Blocked commands safety
- Missing command/workspace parameter errors
- Truncation of large output
- Registry registration and gating via allow_shell_tools
"""
from __future__ import annotations

import json
import sys

import pytest

from agentnodes.kernel.agent.tools import ToolContext

# A1 (2026-10-28): these suites drive POSIX commands (pwd/ls/sh/sleep/
# python3) through the shell tool. On Windows there is no POSIX shell, so
# they fail for environmental reasons, not product defects. The
# platform-independent suites (Safety / Parameters / Registration /
# Schema) keep running everywhere.
_posix_shell_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX shell commands (pwd/ls/sh/sleep) unavailable on Windows",
)

# ── Test fixtures ──────────────────────────────────────────────────


@pytest.fixture
def tool():
    from agentnodes.kernel.agent.builtin_tools.shell_tools import ShellExecTool
    return ShellExecTool()


@pytest.fixture
def tmp_workspace(tmp_path):
    """A temporary workspace directory."""
    sub = tmp_path / "workspace"
    sub.mkdir()
    return sub


# ── Basic execution ────────────────────────────────────────────────


@_posix_shell_only
class TestShellExecBasic:
    def test_echo_returns_stdout(self, tool, tmp_workspace):
        result = json.loads(tool.execute(ctx=ToolContext(workspace=str(tmp_workspace)), command="echo hello"))
        assert result["status"] == "ok"
        assert result["stdout"].strip() == "hello"
        assert result["exit_code"] == 0
        assert "elapsed_seconds" in result
        assert result["command"] == "echo hello"

    def test_pwd_runs_in_workspace(self, tool, tmp_workspace):
        result = json.loads(tool.execute(ctx=ToolContext(workspace=str(tmp_workspace)), command="pwd"))
        assert result["status"] == "ok"
        # pwd should be the workspace directory
        assert tmp_workspace.name in result["stdout"] or str(tmp_workspace) in result["stdout"]

    def test_ls_lists_workspace(self, tool, tmp_workspace):
        (tmp_workspace / "file1.txt").write_text("a")
        (tmp_workspace / "file2.txt").write_text("b")
        result = json.loads(tool.execute(ctx=ToolContext(workspace=str(tmp_workspace)), command="ls"))
        assert result["status"] == "ok"
        assert "file1.txt" in result["stdout"]
        assert "file2.txt" in result["stdout"]

    def test_python_c_prints_stdout(self, tool, tmp_workspace):
        """Python invocation through run_command (the chat fix scenario)."""
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=str(tmp_workspace)),
            command="python3 -c 'print(6*7)'",
        ))
        assert result["status"] == "ok"
        assert result["stdout"].strip() == "42"
        assert result["exit_code"] == 0


# ── Exit codes ─────────────────────────────────────────────────────


@_posix_shell_only
class TestShellExecExitCodes:
    def test_nonzero_exit_code_reported(self, tool, tmp_workspace):
        result = json.loads(tool.execute(ctx=ToolContext(workspace=str(tmp_workspace)), command="exit 7"))
        assert result["status"] == "ok"  # status reflects execution, not exit code
        assert result["exit_code"] == 7

    def test_stderr_captured(self, tool, tmp_workspace):
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=str(tmp_workspace)),
            command="sh -c 'echo to_stdout; echo to_stderr 1>&2'",
        ))
        assert result["status"] == "ok"
        assert "to_stdout" in result["stdout"]
        assert "to_stderr" in result["stderr"]


# ── Timeout ────────────────────────────────────────────────────────


@_posix_shell_only
class TestShellExecTimeout:
    def test_timeout_returns_error(self, tool, tmp_workspace):
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=str(tmp_workspace)),
            command="sleep 5",
            timeout=1,
        ))
        assert result["status"] == "error"
        assert "timed out" in result["error"].lower()
        assert result.get("tool") == "shell"

    def test_custom_timeout_respected(self, tool, tmp_workspace):
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=str(tmp_workspace)),
            command="echo done",
            timeout=10,
        ))
        assert result["status"] == "ok"
        assert result["stdout"].strip() == "done"


# ── Blocked commands ───────────────────────────────────────────────


class TestShellExecSafety:
    @pytest.mark.parametrize("cmd", [
        "rm -rf /",
        "rm -rf /*",
        "mkfs /dev/sda",
        "chmod -R 777 /",
    ])
    def test_dangerous_command_blocked(self, tool, tmp_workspace, cmd):
        result = json.loads(tool.execute(ctx=ToolContext(workspace=str(tmp_workspace)), command=cmd))
        assert result["status"] == "error"
        assert "blocked" in result["error"].lower()


# ── Parameter validation ──────────────────────────────────────────


class TestShellExecParameters:
    def test_missing_command(self, tool, tmp_workspace):
        """缺必填参数由框架拦截 (TypeError → loop 重试/兜底)。"""
        with pytest.raises(TypeError):
            tool.execute(ctx=ToolContext(workspace=str(tmp_workspace)))

    def test_empty_command(self, tool, tmp_workspace):
        result = json.loads(tool.execute(ctx=ToolContext(workspace=str(tmp_workspace)), command=""))
        assert result["status"] == "error"

    def test_missing_workspace(self, tool):
        result = json.loads(tool.execute(ctx=ToolContext(), command="echo hi"))
        assert result["status"] == "error"
        assert "workspace" in result["error"].lower()


# ── Output truncation ──────────────────────────────────────────────


@_posix_shell_only
class TestShellExecTruncation:
    def test_large_stdout_truncated(self, tool, tmp_workspace):
        # 60KB of output, limit is 50_000
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=str(tmp_workspace)),
            command="python3 -c \"print('x' * 60000)\"",
        ))
        assert result["status"] == "ok"
        assert "truncated" in result["stdout"]


# ── Registry integration ───────────────────────────────────────────


class TestShellToolRegistration:
    def test_register_shell_tools(self):
        from agentnodes.kernel.agent.builtin_tools.shell_tools import (
            register_shell_tools,
        )
        from agentnodes.kernel.agent.tools import ToolRegistry
        r = ToolRegistry()
        register_shell_tools(r)
        assert "shell" in r
        assert r.get("shell").name == "shell"

    def test_default_registry_includes_shell_tool(self):
        from agentnodes.kernel.agent.builtin_tools import build_default_registry
        r = build_default_registry()
        assert "shell" in r

    def test_chat_loop_removes_shell_tool_when_disabled(self):
        from agentnodes.kernel.agent.builtin_tools import build_default_registry

        # Simulate the gating logic from build_chat_agent_loop
        r = build_default_registry()
        assert "shell" in r  # before gating

        # When allow_shell_tools=False, remove it
        allow_shell_tools = False
        if not allow_shell_tools:
            r._tools.pop("shell", None)
        assert "shell" not in r

    def test_chat_loop_keeps_shell_tool_when_enabled(self):
        from agentnodes.kernel.agent.builtin_tools import build_default_registry

        r = build_default_registry()
        allow_shell_tools = True
        if not allow_shell_tools:
            r._tools.pop("shell", None)
        assert "shell" in r


# ── Tool schema ────────────────────────────────────────────────────


class TestShellToolSchema:
    def test_openai_schema_format(self):
        from agentnodes.kernel.agent.builtin_tools.shell_tools import ShellExecTool
        schema = ShellExecTool().to_openai_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "shell"
        params = schema["function"]["parameters"]
        # v2: workspace 由 ToolContext 注入, 不在 schema 中
        assert "workspace" not in params["properties"]
        assert "command" in params["required"]
        assert "timeout" in params["properties"]

    def test_is_not_readonly(self):
        from agentnodes.kernel.agent.builtin_tools.shell_tools import ShellExecTool
        assert ShellExecTool().is_readonly is False
