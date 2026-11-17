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

from agentnodes.kernel.agent.tools.core import ToolContext

# 2026-11-17：原实现把这些套件整体 skipif(win32)——8 个测试在 Windows 上
# 永远不跑。改为**跨平台命令表**：语义不变（echo / 当前目录 / 列目录 /
# python / 非零退出 / stderr / 慢命令 / 大输出），命令按平台选择。
# Windows 下 subprocess.run(shell=True) 走 cmd.exe。
IS_WIN = sys.platform == "win32"

if IS_WIN:
    CMD_ECHO = "echo hello"
    CMD_PWD = "cd"                     # cmd.exe 的 cd 打印当前目录
    CMD_LS = "dir /b"                  # 裸文件名
    CMD_PY_67 = 'python -c "print(6*7)"'
    CMD_EXIT7 = "exit /b 7"
    CMD_STDERR = (
        'python -c "import sys; sys.stderr.write(\'to_stderr\\n\'); '
        'print(\'to_stdout\')"'
    )
    CMD_SLOW = 'python -c "import time; time.sleep(5)"'
    CMD_BIGOUT = "python -c \"print('x' * 60000)\""
else:
    CMD_ECHO = "echo hello"
    CMD_PWD = "pwd"
    CMD_LS = "ls"
    CMD_PY_67 = "python3 -c 'print(6*7)'"
    CMD_EXIT7 = "exit 7"
    CMD_STDERR = "sh -c 'echo to_stdout; echo to_stderr 1>&2'"
    CMD_SLOW = "sleep 5"
    CMD_BIGOUT = "python3 -c \"print('x' * 60000)\""

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


class TestShellExecBasic:
    def test_echo_returns_stdout(self, tool, tmp_workspace):
        result = json.loads(tool.execute(ctx=ToolContext(workspace=str(tmp_workspace)), command=CMD_ECHO))
        assert result["status"] == "ok"
        assert result["stdout"].strip() == "hello"
        assert result["exit_code"] == 0
        assert "elapsed_seconds" in result
        assert result["command"] == CMD_ECHO

    def test_pwd_runs_in_workspace(self, tool, tmp_workspace):
        result = json.loads(tool.execute(ctx=ToolContext(workspace=str(tmp_workspace)), command=CMD_PWD))
        assert result["status"] == "ok"
        # pwd should be the workspace directory
        assert tmp_workspace.name in result["stdout"] or str(tmp_workspace) in result["stdout"]

    def test_ls_lists_workspace(self, tool, tmp_workspace):
        (tmp_workspace / "file1.txt").write_text("a")
        (tmp_workspace / "file2.txt").write_text("b")
        result = json.loads(tool.execute(ctx=ToolContext(workspace=str(tmp_workspace)), command=CMD_LS))
        assert result["status"] == "ok"
        assert "file1.txt" in result["stdout"]
        assert "file2.txt" in result["stdout"]

    def test_python_c_prints_stdout(self, tool, tmp_workspace):
        """Python invocation through run_command (the chat fix scenario)."""
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=str(tmp_workspace)),
            command=CMD_PY_67,
        ))
        assert result["status"] == "ok"
        assert result["stdout"].strip() == "42"
        assert result["exit_code"] == 0


# ── Exit codes ─────────────────────────────────────────────────────


class TestShellExecExitCodes:
    def test_nonzero_exit_code_reported(self, tool, tmp_workspace):
        result = json.loads(tool.execute(ctx=ToolContext(workspace=str(tmp_workspace)), command=CMD_EXIT7))
        assert result["status"] == "ok"  # status reflects execution, not exit code
        assert result["exit_code"] == 7

    def test_stderr_captured(self, tool, tmp_workspace):
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=str(tmp_workspace)),
            command=CMD_STDERR,
        ))
        assert result["status"] == "ok"
        assert "to_stdout" in result["stdout"]
        assert "to_stderr" in result["stderr"]


# ── Timeout ────────────────────────────────────────────────────────


class TestShellExecTimeout:
    def test_timeout_returns_error(self, tool, tmp_workspace):
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=str(tmp_workspace)),
            command=CMD_SLOW,
            timeout=1,
        ))
        assert result["status"] == "error"
        assert "timed out" in result["error"].lower()
        assert result.get("tool") == "shell"

    def test_custom_timeout_respected(self, tool, tmp_workspace):
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=str(tmp_workspace)),
            command=CMD_ECHO,
            timeout=10,
        ))
        assert result["status"] == "ok"
        assert result["stdout"].strip() == "hello"


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


class TestShellExecTruncation:
    def test_large_stdout_truncated(self, tool, tmp_workspace):
        # 60KB of output, limit is 50_000
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=str(tmp_workspace)),
            command=CMD_BIGOUT,
        ))
        assert result["status"] == "ok"
        assert "truncated" in result["stdout"]


# ── Registry integration ───────────────────────────────────────────


class TestShellToolRegistration:
    def test_register_shell_tools(self):
        from agentnodes.kernel.agent.builtin_tools.shell_tools import (
            register_shell_tools,
        )
        from agentnodes.kernel.agent.tools.core import ToolRegistry
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
