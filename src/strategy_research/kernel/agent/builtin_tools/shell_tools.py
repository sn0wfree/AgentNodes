"""Shell execution tool — run arbitrary commands in the workspace.

Gated by ``allow_shell_tools``; not registered by default in
``build_default_registry()``.  Must be explicitly opted-in via
``register_shell_tools(registry)``.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time

from strategy_research.kernel.agent.tools import EFFECT_FS, EFFECT_NET, BaseTool, ToolContext, ToolRegistry
from .utils import err_actionable

logger = logging.getLogger(__name__)

# ── Defaults ────────────────────────────────────────────────────────

_DEFAULT_TIMEOUT = 30  # seconds
_MAX_OUTPUT_CHARS = 50_000  # truncate stdout/stderr to keep LLM context small
# All patterns are pre-lowercased so the in-string check against
# command.lower() is case-insensitive.
_BLOCKED_COMMANDS = frozenset({
    "rm -rf /",
    "rm -rf /*",
    "mkfs",
    ":(){ :|:& };:",
    "dd if=/dev/zero of=/dev/sd",
    "chmod -r 777 /",
})


# ── Helper ──────────────────────────────────────────────────────────

def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... (truncated, total {len(text)} chars)"


# ── Tool ────────────────────────────────────────────────────────────


class ShellExecTool(BaseTool):
    """在工作区目录执行 shell 命令。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 迁移 v2 (显式签名 + ToolContext; effects 声明)
    #
    # ## 用途
    # 在工作区目录执行 shell 命令 (pip/环境检查/脚本/git/系统命令)。
    # opt-in 工具 (allow_shell_tools 开启时注册)。
    #
    # ## 参数
    # - command: 要执行的命令 (必填)
    # - timeout: 超时秒数 (默认 30, 上限 120)
    #
    # ## 示例
    # {"command": "python -c 'import pandas; print(pandas.__version__)'"}
    #
    # ## 边界
    # 写工具 (effects: fs + net); 危险命令被拦截; 输出截断。
    #
    # ## 错误处理范式
    # - 缺 command → error + expected 示例
    # - 危险命令 → error + 拦截说明
    # - 超时/退出码非 0 → error + 详情
    #
    # ## 相关工具
    # 无 (系统级能力)
    # ─────────────────────────────────────────────
    """

    name = "shell"
    description = "在工作区目录执行 shell 命令 (opt-in); 返回 stdout/stderr/退出码。"
    repeatable = True
    category = "系统"
    effects = frozenset({EFFECT_FS, EFFECT_NET})

    def execute(
        self,
        ctx: ToolContext,
        command: str,
        timeout: int = 30,
    ) -> str:
        from pathlib import Path

        if ctx.workspace is None:
            return err_actionable(
                "missing workspace context",
                fix="AgentLoop 注入 workspace; 直接调用时传 ctx",
                tool="shell",
            )
        workspace = Path(str(ctx.workspace)).resolve()

        if not command or not isinstance(command, str):
            return err_actionable(
                "missing or empty 'command' parameter",
                expected="a valid shell command string, e.g. 'pip install pandas'",
                fix="pass command='your shell command here'",
                tool="shell",
            )

        # ── Parse timeout ────────────────────────────────────
        timeout = max(1, min(timeout, 120))  # clamp 1..120

        # ── Safety: block obviously destructive commands ──────
        cmd_lower = command.lower().strip()
        for blocked in _BLOCKED_COMMANDS:
            if blocked in cmd_lower:
                return err_actionable(
                    f"command blocked for safety: contains '{blocked}'",
                    received=command,
                    fix="use a less destructive command",
                    tool="shell",
                )

        # ── Execute ──────────────────────────────────────────
        t0 = time.monotonic()
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            elapsed = round(time.monotonic() - t0, 2)
        except subprocess.TimeoutExpired:
            return err_actionable(
                f"command timed out after {timeout}s",
                received=command,
                fix="increase timeout or use a shorter-running command",
                tool="shell",
            )
        except FileNotFoundError:
            return err_actionable(
                "shell not found (sh/bash not in PATH)",
                received=command,
                fix="check that /bin/sh or /bin/bash exists",
                tool="shell",
            )
        except Exception as exc:
            return err_actionable(
                f"command execution failed: {exc}",
                received=command,
                fix="verify the command syntax is correct",
                tool="shell",
            )

        stdout = _truncate(result.stdout)
        stderr = _truncate(result.stderr)

        return json.dumps({
            "status": "ok",
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": result.returncode,
            "elapsed_seconds": elapsed,
            "command": command,
        }, ensure_ascii=False)


# ── Registration ────────────────────────────────────────────────────


def register_shell_tools(registry: ToolRegistry) -> None:
    """Register shell tools into the given registry."""
    registry.register(ShellExecTool())
