# -*- coding: utf-8 -*-
"""RSI 宿主适配器（L1）——把 L1 资产绑定成已就绪的 cycle runner。

WP-8 3b.2 的注入契约要求 L2 ``cycle_driver.run_once`` 不 import L1，故
halt 护栏与闭环装配由宿主注入。此前两个 L1 宿主（CLI 命令 /
goal_settled_consumer）逐字复制 ``halt_check`` 与
``make_loop_builder(build_rsi_loop)``——契约知识摊回每个调用方。本模块
把它收成单一 host adapter（候选 6 深化，架构复审 2026-11-20）。

**不跨层**：本模块在 L1，import L1 的 halt / rsi_wiring 与 L2 的
cycle_driver 都是合法的 L1→L2 下行；L2 不知道本模块存在。
"""
from __future__ import annotations

from typing import Any, Callable

__all__ = ["RsiHost", "build_rsi_host"]


class RsiHost:
    """已绑定的 RSI cycle runner（宿主侧单一注入面）。"""

    def __init__(
        self,
        run_once: Callable[..., Any],
        halt_check: Callable[[], tuple[bool, str]],
        loop_builder: Callable[..., Any],
    ) -> None:
        self._run_once = run_once
        self._halt_check = halt_check
        self._loop_builder = loop_builder

    def run_cycle(self, *, session_id: str | None = None, **kw: Any) -> Any:
        """跑一轮 RSI——注入契约已绑定，调用方只传业务参数。"""
        return self._run_once(
            session_id=session_id,
            halt_check=self._halt_check,
            loop_builder=self._loop_builder,
            **kw,
        )


def _halt_check() -> tuple[bool, str]:
    """门 1 护栏：L1 kill switch → callable（单一实现，两宿主共用）。"""
    from agentnodes.interfaces.cli.halt import HALT_REASON, is_halted

    if is_halted():
        return True, HALT_REASON
    return False, ""


def build_rsi_host() -> RsiHost:
    """构造已绑定的宿主 runner（生产唯一入口）。"""
    from agentnodes.apps.evolution.cycle_driver import make_loop_builder, run_once
    from agentnodes.interfaces.rsi_wiring import build_rsi_loop

    return RsiHost(
        run_once=run_once,
        halt_check=_halt_check,
        loop_builder=make_loop_builder(build_rsi_loop),
    )
