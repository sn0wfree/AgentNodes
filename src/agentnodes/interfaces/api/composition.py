# -*- coding: utf-8 -*-
"""API 资源图的组合根（L1）——构造 / 定位 / 生命周期规则的单一Owner。

M12（批 4 · 架构复审候选 2）：此前规则分散在 container.py（构造+close）、
dependencies.py（fallback）、router cache（自建 container，无 close）。
本模块把「构造一份资源图 → 在哪里找到它 → 何时释放」收为一个深模块：
外部只需 ``build_resources`` / ``register`` / ``resources_from_request`` /
``close_all`` 四个行为，内部保留现有 AppContainer 构造（薄转调，零 wiring 变化）。

边界：不引入 DI framework；不镜像资源字段（getattr 按约定取）；不碰 Gateway
singleton（独立控制面）。旧名（container.py 的函数）保留薄转发，下版本删除。
"""
from __future__ import annotations

import atexit
import logging
from typing import Any

from fastapi import Request

logger = logging.getLogger(__name__)

__all__ = [
    "build_resources",
    "register",
    "unregister",
    "resources_from_request",
    "close_resources",
    "close_all",
]

# 进程级资源图注册表：key = container 实例 id，value = container。
# 注意：不能用 db_path 作 key——测试间清 router cache 后会按同一 db_path
# 重建 container，按路径幂等会把新实例误判为已关闭（句柄泄漏）。
_REGISTRY: dict[int, Any] = {}
_CLOSED: set[int] = set()


def build_resources(**kwargs: Any) -> Any:
    """按现有生产 wiring 构造资源图（薄转调 build_container，零行为变化）。"""
    from agentnodes.interfaces.api.container import build_container

    return build_container(**kwargs)


def register(container: Any) -> None:
    """登记资源图（幂等：同实例只登记一次）。"""
    key = id(container)
    if key not in _REGISTRY:
        _REGISTRY[key] = container
        atexit.register(close_resources, container)  # 进程退出兜底关闭


def unregister(container: Any) -> None:
    """从注册表移除（测试 reset 用），不自动 close。"""
    key = id(container)
    _REGISTRY.pop(key, None)
    _CLOSED.discard(key)


def resources_from_request(request: Request) -> Any | None:
    """FastAPI 请求侧定位：优先 app.state._container，无则 None（由
    dependencies.py 的现有 fallback 处理——本批不删 fallback）。"""
    return getattr(request.app.state, "_container", None)


def close_resources(container: Any) -> None:
    """释放一份资源图的全部句柄（薄转调 close_container + 幂等标记）。"""
    from agentnodes.interfaces.api.container import close_container

    key = id(container)
    if key in _CLOSED:
        return
    _CLOSED.add(key)
    close_container(container)


def close_all() -> None:
    """释放所有已登记资源图（测试 reset / 进程退出统一入口）。"""
    for container in list(_REGISTRY.values()):
        close_resources(container)
    _REGISTRY.clear()
