"""W2 — merged CLI group: serve.

原文件同时承载 ``backtest`` 与 ``check`` 两个合并命令组（把
``run`` / ``evaluate`` / ``reproduce`` / ``validate`` / ``accept`` 重新包装成
``backtest run|rerun|replay`` 与 ``check factor|run|accept``）。

**2026-11-05 做减法批次 4**：``backtest`` / ``check`` 两组的全部目标
（``apps.backtest`` / ``apps.factor_validate`` / ``apps.strategy_acceptance``）
已随回测与验证域整体移除，归档 ``testcase/golden/apps/``。因此本文件只剩
``serve`` —— 它把 Web UI 与 API 两个服务合成一条命令，与业务无关。
"""

from __future__ import annotations

import argparse


def dispatch_serve(args: argparse.Namespace) -> int:
    """serve: webui (default) or api-only."""
    from .server import cmd_api_serve, cmd_webui_serve

    if getattr(args, "api_only", False):
        return cmd_api_serve(args)
    return cmd_webui_serve(args)


__all__ = ["dispatch_serve"]
