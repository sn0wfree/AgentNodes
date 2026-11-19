# -*- coding: utf-8 -*-
"""隔离执行 adapter 平台 seam（4c B2.2 子批 3a · 不接生产）。

把「进程隔离执行一个同步工具」封装为一个可替换的 adapter：Windows 用
Job Object / taskkill 树终止（从原型提炼），POSIX 尚未验证 → 禁启用
（``supported() == False``，调用抛 UnsupportedPlatform）。**本阶段零生产
接线**——只提供 seam 与平台保护，供 3b/3c 接 IPC 与试点。

顶层不 import ctypes.WinDLL——POSIX 必须能安全导入本模块（测试门禁在
POSIX CI 上也要能跑）。Windows 相关 ctypes 只在 build_worker_adapter()
内延迟加载。

设计：docs/design/isolated-execution-b22-design-20261119.md
"""
from __future__ import annotations

import sys
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "UnsupportedPlatform",
    "WorkerAdapter",
    "build_worker_adapter",
]


class UnsupportedPlatform(RuntimeError):
    """未验证/不支持的隔离执行平台——显式拒绝，不静默降级到 in-process。"""


@runtime_checkable
class WorkerAdapter(Protocol):
    """隔离 worker 的最小接口（spawn / 通信由 3b IPC 层填充）。"""

    def supported(self) -> bool:
        """当前平台是否已验证可用。"""
        ...

    def spawn(self, worker_script: str) -> Any:
        """启动隔离 worker，返回平台句柄。"""
        ...

    def kill_and_confirm(self, handle: Any, pids: list[int], timeout_s: float) -> dict[int, str]:
        """终止 worker 及其后代并**逐 pid 确认退出**（确认先于任何释放）。"""
        ...

    def close(self, handle: Any) -> None:
        """释放平台资源兜底。"""
        ...


class _PosixAdapter:
    """POSIX：未验证——显式禁启用（裁决：跨平台生产启用前必须验证）。"""

    def supported(self) -> bool:
        return False

    def spawn(self, worker_script: str) -> Any:
        raise UnsupportedPlatform(
            "POSIX 隔离执行尚未验证——跨平台生产启用前必须完成平台测试"
        )

    def kill_and_confirm(self, handle, pids, timeout_s):
        raise UnsupportedPlatform("POSIX 隔离执行尚未验证")

    def close(self, handle) -> None:
        return None


def build_worker_adapter() -> WorkerAdapter:
    """按当前平台返回 adapter；不支持则给禁启用 adapter（不抛，便于测试）。"""
    if sys.platform == "win32":
        from agentnodes.apps.chat._isolation_win32 import Win32JobAdapter

        return Win32JobAdapter()
    return _PosixAdapter()
