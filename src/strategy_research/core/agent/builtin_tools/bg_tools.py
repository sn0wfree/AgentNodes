"""Background command tool — single entry ``run_bg_command``.

Manages nohup-style background tasks for agents: start / status / wait
/ log / kill via one tool with an ``action`` parameter (design
``docs/study-long-task-background-plan.md`` §5).

The task registry lives in ``core/utils/bg_proc.py`` (shared with
``RunBacktestTool(background=True)`` and ``backtest.run_strategy``), so
a task started anywhere is pollable here by ``task_id`` — and watchdogs
/ round-end harvesters sweep the same registry.

Opt-in tool (mirrors shell tools): register via
``register_bg_tools(registry)``; roles get it through the tool
whitelist in ``role_factory``.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from strategy_research.foundation.utils.bg_proc import (
    BgTaskHandle,
    active_tasks,
    get_task,
    is_stalled,
    kill_bg,
    log_progress,
    log_tail,
    register_task,
    register_thread_task,
    set_task_result,
    unregister_task,
)
from ..tools import EFFECT_FS, EFFECT_NET, BaseTool, ToolContext, ToolRegistry
from .shell_tools import _BLOCKED_COMMANDS

logger = logging.getLogger(__name__)

_STALL_TIMEOUT = 300.0  # seconds without log progress → stalled
_MAX_WAIT_SECONDS = 120  # clamp for the wait observation window

__all__ = [
    "RunBgCommandTool",
    "register_bg_tools",
    "active_tasks",
    "get_task",
    "log_progress",
    "register_task",
    "register_thread_task",
    "set_task_result",
    "unregister_task",
]


def _status_payload(task_id: str, handle: BgTaskHandle) -> dict:
    if not handle.is_alive():
        payload: dict[str, Any] = {
            "task_id": task_id, "state": "done",
            "log": str(handle.log_path),
        }
        if handle.proc is not None:
            payload["exit_code"] = handle.proc.returncode
        if handle.result is not None:
            payload["result"] = handle.result
        return payload
    if is_stalled(handle.log_path, _STALL_TIMEOUT):
        return {
            "task_id": task_id, "state": "stalled",
            "stalled_seconds": int(time.time() - handle.log_path.stat().st_mtime)
            if handle.log_path.exists() else None,
            "log": str(handle.log_path),
            "tail": log_tail(handle.log_path, n=3),
        }
    return {
        "task_id": task_id, "state": "running",
        "log": str(handle.log_path),
        "tail": log_tail(handle.log_path, n=3),
    }


# ── Tool ────────────────────────────────────────────────────────────


class RunBgCommandTool(BaseTool):
    """后台任务管理（单入口，action 分派）。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.0.0
    # 变更: v1.0.0 初始版本（长任务后台化 + 日志轮询）
    #
    # ## 用途
    # 将耗时长命令转为后台执行（nohup 语义），日志持续落盘 run.log；
    # 通过日志推进判定进展（写日志 = 正常，停滞 > 300s = 卡死）。
    # 长任务（长回测/大数据计算/下载）用此工具，避免阻塞当前回合。
    #
    # ## 参数
    # - action: start|status|wait|log|kill (必填)
    # - task_id: 任务 ID (start 之外必填)
    # - command: 要后台启动的命令 (start 必填)
    # - cwd: 工作目录 (start 可选, 默认 workspace)
    # - log: 日志路径 (start 可选, 默认 workspace/bg_tasks/<id>.log)
    # - seconds: 观察窗秒数 (wait, 1..120, 默认 15)
    # - n_lines: 读取行数 (log, 1..200, 默认 20)
    #
    # ## 示例
    # start:  {"action": "start", "command": "python compute.py --full"}
    #   → {"status": "running", "task_id": "bg_xxxx", "log": "..."}
    # wait:  {"action": "wait", "task_id": "bg_xxxx", "seconds": 15}
    #   → {"state": "running"|"stalled"|"done", ...}
    # log:   {"action": "log", "task_id": "bg_xxxx", "n_lines": 20}
    #
    # ## 边界
    # - thread 模式任务 (run_backtest background=True) 无法强制 kill —
    #   kill 仅注销，线程自然结束后清理
    # - 日志停滞判定 300s 为固定窗口；观察窗 wait 内部已 sleep，勿重复等待
    #
    # ## 错误处理范式
    # - 未知 action → {"status": "error", "error": "unknown action: ..."}
    # - 未知 task_id → {"status": "error", "error": "unknown task_id: ..."}
    #   （先 start 或 run_backtest(background=True) 获取）
    # - 危险命令 → {"status": "error", "error": "command blocked for safety"}
    # - 全部错误可安全重试，无部分写入遗留
    #
    # ## 相关工具
    # run_backtest: 回测后台化 (background=True); read_file: 日志任意行段读取;
    # run_command: 前台短命令执行 (≤120s)
    # ─────────────────────────────────────────────
    """

    name = "run_bg_command"
    description = (
        "后台任务管理：start/status/wait/log/kill（长命令转后台 + 日志轮询）。"
    )
    repeatable = True
    category = "后台任务"
    effects = frozenset({EFFECT_FS, EFFECT_NET})

    def execute(
        self,
        ctx: ToolContext,
        action: str,
        task_id: str = "",
        command: str = "",
        cwd: str = "",
        log: str = "",
        seconds: int = 15,
        n_lines: int = 20,
    ) -> str:
        workspace = ctx.workspace
        if workspace is None:
            return json.dumps({
                "status": "error",
                "error": "missing workspace context",
                "fix": "AgentLoop 注入 workspace",
            }, ensure_ascii=False)

        if action not in ("start", "status", "wait", "log", "kill"):
            return json.dumps({
                "status": "error",
                "error": f"unknown action: {action}",
                "expected": "start|status|wait|log|kill",
            }, ensure_ascii=False)

        try:
            if action == "start":
                return self._start(ctx, command, cwd, log)
            handle = get_task(task_id)
            if handle is None:
                return json.dumps({
                    "status": "error",
                    "error": f"unknown task_id: {task_id}",
                    "fix": "先 start 或 run_backtest(background=True) 获取 task_id",
                }, ensure_ascii=False)
            if action == "status":
                return json.dumps(_status_payload(task_id, handle), ensure_ascii=False)
            if action == "wait":
                wait = max(1, min(int(seconds), _MAX_WAIT_SECONDS))
                time.sleep(wait)
                return json.dumps(_status_payload(task_id, handle), ensure_ascii=False)
            if action == "log":
                n = max(1, min(int(n_lines), 200))
                return json.dumps({
                    "task_id": task_id,
                    "log": log_tail(handle.log_path, n=n),
                }, ensure_ascii=False)
            # kill
            if handle.proc is not None:
                kill_bg(handle.proc)
            else:
                # thread-mode tasks can't be force-killed; deregistering
                # makes them invisible (the thread finishes on its own)
                logger.warning(
                    "run_bg_command kill: thread task %s not force-killable; "
                    "deregistered", task_id,
                )
            unregister_task(task_id)
            return json.dumps({
                "status": "ok", "task_id": task_id, "killed": True,
            }, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            logger.exception("run_bg_command %s failed", action)
            return json.dumps({
                "status": "error", "error": f"{exc}",
            }, ensure_ascii=False)

    def _start(self, ctx: ToolContext, command: str, cwd: str, log: str) -> str:
        if not command or not isinstance(command, str):
            return json.dumps({
                "status": "error",
                "error": "missing or empty 'command'",
                "expected": "a valid shell command string",
            }, ensure_ascii=False)
        cmd_lower = command.lower().strip()
        for blocked in _BLOCKED_COMMANDS:
            if blocked in cmd_lower:
                return json.dumps({
                    "status": "error",
                    "error": f"command blocked for safety: contains '{blocked}'",
                }, ensure_ascii=False)

        from strategy_research.foundation.utils.bg_proc import run_bg, shell_command

        ws = Path(str(ctx.workspace)).resolve()
        workdir = Path(cwd).resolve() if cwd else ws
        log_path = Path(log).resolve() if log else \
            ws / "bg_tasks" / f"{uuid.uuid4().hex[:8]}.log"
        proc = run_bg(shell_command(command), log_path, cwd=workdir)
        task_id = register_task(proc, log_path, command)
        return json.dumps({
            "status": "running", "task_id": task_id,
            "log": str(log_path), "pid": proc.pid,
        }, ensure_ascii=False)


def register_bg_tools(registry: ToolRegistry) -> None:
    """Register background-command tools into the given registry."""
    registry.register(RunBgCommandTool())
