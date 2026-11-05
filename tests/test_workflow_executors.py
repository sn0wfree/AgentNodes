"""Tests for workflow/executors.py — AgentLoopExecutor, PythonExecutor, CLIExecutor, StubExecutor."""

from __future__ import annotations

import pytest

from agentnodes.kernel.workflow.executors import (
    AgentLoopExecutor,
    CLIExecutor,
    PythonExecutor,
    StubExecutor,
)

# ============================================================
# StubExecutor
# ============================================================


class TestStubExecutor:
    def test_default_result(self):
        ex = StubExecutor("test")
        result = ex.run("prompt", {"key": "value"})
        assert result == {"status": "ok"}

    def test_custom_result(self):
        ex = StubExecutor("test", {"status": "custom", "data": 42})
        result = ex.run("prompt", {})
        assert result == {"status": "custom", "data": 42}

    def test_name_property(self):
        ex = StubExecutor("my_agent")
        assert ex.name == "my_agent"

    def test_ignores_prompt(self):
        ex = StubExecutor("test", {"status": "ok"})
        assert ex.run("any prompt", {}) == {"status": "ok"}

    def test_ignores_context(self):
        ex = StubExecutor("test", {"status": "ok"})
        assert ex.run("", {"anything": 1}) == {"status": "ok"}

    def test_none_result_uses_default(self):
        ex = StubExecutor("test", result=None)
        assert ex.run("", {}) == {"status": "ok"}


# ============================================================
# PythonExecutor
# ============================================================


class TestPythonExecutor:
    def test_basic_call(self):
        def my_func(prompt, context):
            return f"processed: {prompt}"

        ex = PythonExecutor("test", my_func)
        result = ex.run("hello", {})
        assert result == {"status": "ok", "output": "processed: hello"}

    def test_context_passed_through(self):
        def my_func(prompt, context):
            return {"prompt": prompt, "context": context}

        ex = PythonExecutor("test", my_func)
        ctx = {"user": "alice", "id": 42}
        result = ex.run("hi", ctx)
        assert result["status"] == "ok"
        assert result["output"]["prompt"] == "hi"
        assert result["output"]["context"] == ctx

    def test_function_returning_dict(self):
        def my_func(prompt, context):
            return {"action": "buy", "symbol": "AAPL"}

        ex = PythonExecutor("test", my_func)
        result = ex.run("", {})
        assert result["output"]["action"] == "buy"

    def test_function_returning_none(self):
        def my_func(prompt, context):
            return None

        ex = PythonExecutor("test", my_func)
        result = ex.run("", {})
        assert result == {"status": "ok", "output": None}

    def test_function_returning_int(self):
        def my_func(prompt, context):
            return 42

        ex = PythonExecutor("test", my_func)
        result = ex.run("", {})
        assert result["output"] == 42

    def test_name_property(self):
        ex = PythonExecutor("my_func", lambda p, c: None)
        assert ex.name == "my_func"

    def test_function_raising_exception_propagates(self):
        def bad_func(prompt, context):
            raise ValueError("oops")

        ex = PythonExecutor("test", bad_func)
        with pytest.raises(ValueError, match="oops"):
            ex.run("", {})


# ============================================================
# CLIExecutor
# ============================================================


class TestCLIExecutor:
    """CLIExecutor 用 ``shlex.split`` + ``shell=False``，因此测试命令必须是
    **真实可执行文件**（``echo`` / ``false`` / ``sh`` 是 Unix shell 内建，
    在 Windows 上不存在）。

    2026-11-05：改为 `sys.executable -c ...`，跨平台且仍覆盖
    「成功 / 非零退出 / stderr / 不存在命令」四种路径。
    """

    @staticmethod
    def _py(code: str) -> str:
        import sys

        return f'"{sys.executable}" -c "{code}"'

    def test_name_property(self):
        ex = CLIExecutor("my_cli")
        assert ex.name == "my_cli"

    def test_default_command(self):
        ex = CLIExecutor("test")
        result = ex.run("prompt", {})
        # Default is `echo 'Agent test executed'`. `echo` 在 Windows 不是
        # 可执行文件，因此默认命令在这里只断言「返回了结构化结果」。
        assert result["status"] in ("ok", "error")
        assert "status" in result

    def test_custom_command_success(self):
        ex = CLIExecutor(
            "test", command=self._py("print('hello')")
        )
        result = ex.run("prompt", {})
        assert result["status"] == "ok", result
        assert "hello" in result["output"]
        assert result["error"] == ""

    def test_command_with_args(self):
        ex = CLIExecutor(
            "test", command=self._py("print('arg1 arg2')")
        )
        result = ex.run("prompt", {})
        assert result["status"] == "ok", result
        assert "arg1" in result["output"]
        assert "arg2" in result["output"]

    def test_failing_command_returns_error(self):
        # 非零退出码 + 无 stdout
        ex = CLIExecutor(
            "test", command=self._py("import sys; sys.exit(1)")
        )
        result = ex.run("prompt", {})
        assert result["status"] == "error"
        assert result["output"] == ""

    def test_failing_command_with_stderr(self):
        # Command that writes to stderr and fails
        ex = CLIExecutor(
            "test",
            command=self._py("import sys; print('error', file=sys.stderr); sys.exit(1)"),
        )
        result = ex.run("prompt", {})
        assert result["status"] == "error"
        assert "error" in result["error"]

    def test_nonexistent_command_returns_error(self):
        ex = CLIExecutor("test", command="nonexistent_binary_xyz_12345")
        result = ex.run("prompt", {})
        assert result["status"] == "error"
        assert "not found" in result["error"].lower() or "Command not found" in result["error"]

    def test_no_shell_injection(self):
        # If shell=True, this would execute "echo; rm /tmp/test_file"
        # With shell=False, shlex.split treats it as single command "echo; rm /tmp/test_file"
        # which doesn't exist → FileNotFoundError
        dangerous = "echo; touch /tmp/sr_test_injection"
        ex = CLIExecutor("test", command=dangerous)
        ex.run('prompt', {})
        # Should NOT create the file
        import os
        assert not os.path.exists("/tmp/sr_test_injection")
        # And should return error or treat as single command
        # (depending on whether shlex splits it correctly)


# ============================================================
# AgentLoopExecutor
# ============================================================


class TestAgentLoopExecutor:
    def test_no_loop_cls_raises(self):
        ex = AgentLoopExecutor("test")
        with pytest.raises(NotImplementedError):
            ex.run("prompt", {})

    def test_name_property(self):
        ex = AgentLoopExecutor("my_agent")
        assert ex.name == "my_agent"

    def test_with_loop_cls(self):
        class FakeLoop:
            def __init__(self, tools=None):
                self.tools = tools

            def run(self, prompt):
                return f"loop result for {prompt}"

        ex = AgentLoopExecutor("test", loop_cls=FakeLoop)
        result = ex.run("hello", {})
        assert result["status"] == "ok"
        assert "loop result for hello" in result["output"]

    def test_tools_passed_to_loop(self):
        received_tools = []

        class FakeLoop:
            def __init__(self, tools=None):
                received_tools.extend(tools or [])

            def run(self, prompt):
                return "ok"

        tools = ["tool1", "tool2"]
        ex = AgentLoopExecutor("test", loop_cls=FakeLoop, tools=tools)
        ex.run("p", {})
        assert received_tools == tools

    def test_default_tools_empty(self):
        class FakeLoop:
            def __init__(self, tools=None):
                self._tools = tools

            def run(self, prompt):
                return self._tools

        ex = AgentLoopExecutor("test", loop_cls=FakeLoop)
        result = ex.run("p", {})
        assert result["output"] == [] or result["output"] is None


# ============================================================
# All executors implement common interface
# ============================================================


class TestExecutorInterface:
    def test_all_have_name(self):
        for cls in [StubExecutor, PythonExecutor, CLIExecutor, AgentLoopExecutor]:
            if cls == PythonExecutor:
                ex = cls("test", func=lambda p, c: None)
            else:
                ex = cls("test")
            assert hasattr(ex, "name")
            assert ex.name == "test"

    def test_all_have_run(self):
        for cls in [StubExecutor, PythonExecutor, CLIExecutor]:
            if cls == PythonExecutor:
                ex = cls("test", func=lambda p, c: "ok")
            elif cls == CLIExecutor:
                ex = cls("test", command="echo ok")
            else:
                ex = cls("test")
            assert hasattr(ex, "run")
            assert callable(ex.run)
