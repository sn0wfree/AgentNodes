"""Platform shell wrapping + toolset hygiene after eval findings."""
from __future__ import annotations

import sys

from agentnodes.kernel.agent.toolsets import TOOLSETS
from agentnodes.foundation.utils.bg_proc import shell_command


class TestShellCommand:
    def test_wraps_current_platform(self):
        argv = shell_command("echo hi")
        assert isinstance(argv, list) and len(argv) >= 2
        if sys.platform.startswith("win"):
            # Windows 走 shell=True 哨兵（见 bg_proc.shell_command docstring）：
            # 传带引号路径给 cmd /c 会被误解析，改由 CPython 的 COMSPEC 处理。
            assert argv[0] == "__SHELL_TRUE__"
        else:
            assert argv[0] == "bash"

    def test_command_is_last_arg(self):
        assert shell_command("echo hi")[-1] == "echo hi"


class TestToolsetHygieneAfterEval:
    """第 4 批次起量化角色已迁 apps/agent_plugins/，kernel 表只收通用角色。

    原断言验证的是 ``factor_analyst`` / ``backtest_diagnostics`` 两个业务角色
    的后台 shell 卫生；它们已不在 kernel 表里，断言目标随之换成
    **通用角色是否有后台 shell**（coder/general 该有，只读角色该没有）。
    """

    def test_readonly_roles_have_no_bg_shell(self):
        for role in ("minimal", "explorer", "planner", "critic"):
            assert "run_bg_command" not in TOOLSETS[role], role
            assert "shell" not in TOOLSETS[role], role

    def test_execution_roles_have_bg_shell(self):
        for role in ("coder", "general"):
            assert "run_bg_command" in TOOLSETS[role], role
            assert "shell" in TOOLSETS[role], role

    def test_business_roles_absent(self):
        assert "factor_analyst" not in TOOLSETS
        assert "backtest_diagnostics" not in TOOLSETS
