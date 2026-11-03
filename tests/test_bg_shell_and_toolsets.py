"""Platform shell wrapping + toolset hygiene after eval findings."""
from __future__ import annotations

import sys

from strategy_research.kernel.agent.toolsets import TOOLSETS
from strategy_research.foundation.utils.bg_proc import shell_command


class TestShellCommand:
    def test_wraps_current_platform(self):
        argv = shell_command("echo hi")
        assert isinstance(argv, list) and len(argv) >= 2
        if sys.platform.startswith("win"):
            assert argv[0] in ("cmd", "powershell", "pwsh")
        else:
            assert argv[0] == "bash"

    def test_command_is_last_arg(self):
        assert shell_command("echo hi")[-1] == "echo hi"


class TestToolsetHygieneAfterEval:
    def test_factor_analyst_has_no_bg_shell(self):
        assert "run_bg_command" not in TOOLSETS["factor_analyst"]
        assert "compute_factor" in TOOLSETS["factor_analyst"]

    def test_backtest_diagnostics_has_no_bg_shell(self):
        assert "run_bg_command" not in TOOLSETS["backtest_diagnostics"]
        assert "run_backtest" in TOOLSETS["backtest_diagnostics"]
