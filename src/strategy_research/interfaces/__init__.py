"""Interfaces layer (L1) - thin adapters (api / cli / mcp).

依赖方向：interfaces → apps → kernel → foundation（见 tests/test_layer_matrix.py）。

**组装根**：``gateway_wiring`` 是本层唯一的"知道两边"的模块——它把
apps 的实现（TicketSink）与 kernel 的机制（容量闸、基因组版本链）接到
``Gateway`` 上。它在被 import 时向 kernel 注册一个构造后置钩子；
kernel 侧不 import 本层（否则是 L3→L1 上行违规）。
"""

from __future__ import annotations


def _register_wiring() -> None:
    """import 本包即挂上 Gateway 接线钩子。

    失败不影响 interfaces 自身可用——接线是增强，缺少它系统照常跑
    （只是 Ticket/容量闸/基因组三项不生效，且不会有事件提示）。
    因此这里吞异常并只在 debug 级别记录。
    """
    try:
        from . import gateway_wiring  # noqa: F401 —— 触发自注册副作用
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).debug(
            "gateway wiring hook not registered", exc_info=True,
        )


_register_wiring()

