"""活跃基因组版本链的持有位（kernel 侧）。

**为什么需要这个模块**：``apps.evolution.skill_memory.resolve()`` 的
``source`` 参数决定它读到什么——不传 ``source`` 时它返回空配置
（"未启用 RSI"的默认路径）。装配根把 ``GenomeVersionStore`` 建出来之后，
resolver 必须能找到**同一个实例**，否则版本链是死的：

    validation_gate.activate() → 升版
    skill_memory.resolve()     → 读到空配置（因为没人给它 source）

这正是本轮接线的第二个断点——第一版只把 store 挂到 Gateway 上，
resolver 依旧拿不到它，链路看着通了、实际空转。

**方向**：apps 主动 import kernel（L2→L3，合法）。kernel 不认识 apps。
"""

from __future__ import annotations

import threading
from typing import Any

_lock = threading.RLock()
_active: Any | None = None


def set_active_genome(store: Any | None) -> None:
    """登记当前进程的基因组版本链（装配根/测试调用）。"""
    global _active
    with _lock:
        _active = store


def get_active_genome() -> Any | None:
    """返回当前登记的版本链；未登记时为 None。"""
    with _lock:
        return _active


def reset_active_genome() -> None:
    """清空登记（测试用）。"""
    set_active_genome(None)


__all__ = ["get_active_genome", "reset_active_genome", "set_active_genome"]
