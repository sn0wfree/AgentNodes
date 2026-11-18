"""commands 子包内共享的小助手——单点定义，零 re-export。

**来历（2026-11-18，interfaces 复用收敛 B 批）**：``_resolve_console`` 曾在
6 个命令文件（help / show / slash_chat / slash_goal / slash_memory /
slash_session）逐字重复，收敛到这里。消费者一律走具体模块路径：

    from agentnodes.interfaces.cli.commands._shared import _resolve_console

（R-014：不做包级转发，避免 monkeypatch 目标分裂。）
"""

from typing import Any

from rich.console import Console

from agentnodes.interfaces.cli.theme import get_console


def _resolve_console(console: Any = None) -> Console:
    """传入 console 原样返回；否则回退进程级单例（见 ``theme.get_console``）。"""
    return console or get_console()
