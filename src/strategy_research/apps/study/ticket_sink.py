"""SQLite-backed TicketSink — **已废弃，改用 kernel/orchestration/ticket_store.py**。

2026-11-05 批次 3：Ticket/Assignment 迁到 L3，``TicketSink`` 的全部职责
（admit / settle / recover / recover_and_mark）已并入 ``TicketStore``。
本文件保留为**薄转发层**，只为让尚未更新的调用点继续工作；下批次删除。

新代码请直接用::

    from strategy_research.kernel.orchestration.ticket_store import TicketStore
    store = TicketStore(db_path)          # 替代 TicketSink(store_factory=...)
"""

from __future__ import annotations

from typing import Any, Callable

from strategy_research.kernel.orchestration.ticket_store import (
    TicketStore,
)

__all__ = ["TicketSink"]


class TicketSink(TicketStore):
    """兼容层：``store_factory`` 参数映射到 ``db_path``。

    ``store_factory`` 是原设计的注入点（便于测试给 tmp 路径）。新
    ``TicketStore`` 直接接 ``db_path`` —— 两者等价，因为 factory 唯一
    的用途就是返回一个指向特定库的 ``StudyStore``。
    """

    def __init__(
        self,
        store_factory: Callable[[], Any] | None = None,
        *,
        emit: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        db_path = None
        if store_factory is not None:
            try:
                db_path = getattr(store_factory(), "db_path", None)
            except Exception:  # noqa: BLE001 —— factory 挂了就走默认路径
                db_path = None
        super().__init__(db_path, emit=emit)

