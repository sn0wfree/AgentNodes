# -*- coding: utf-8 -*-
"""API 资源图的组合根（L1）——构造 / 定位 / 生命周期规则的单一Owner。

M12（批 4 · 架构复审候选 2）：此前规则分散在 container.py（构造+close）、
dependencies.py（fallback）、router cache（自建 container，无 close）。
本模块把「构造一份资源图 → 在哪里找到它 → 何时释放」收为一个深模块：
外部只需 ``build_resources`` / ``register`` / ``resources_from_request`` /
``close_all`` 四个行为，内部保留现有 AppContainer 构造（薄转调，零 wiring 变化）。

边界：不引入 DI framework；不镜像资源字段（getattr 按约定取）；不碰 kernel
Gateway singleton（``kernel.gateway.singleton.get_gateway``，独立控制面——
与 PermissionGateway 是两个类，后者自第四轮 C3 起归本模块所有）。
旧名（container.py 的函数）保留薄转发，下版本删除。
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
    "session_service_from",
    "delete_session_lifecycle",
    "get_permission_gateway",
    "clear_service_cache",
    "close_resources",
    "close_all",
]

# 进程级资源图注册表：key = container 实例 id，value = container。
# 注意：不能用 db_path 作 key——测试间清 router cache 后会按同一 db_path
# 重建 container，按路径幂等会把新实例误判为已关闭（句柄泄漏）。
_REGISTRY: dict[int, Any] = {}
_CLOSED: set[int] = set()
# 进程退出兜底：只注册一次无参 close_all（不绑定任何容器——绑定容器的
# 回调会永久持有强引用，导致 close 后对象无法回收；17:06 复审候选 1）。
_ATEXIT_REGISTERED = False


def build_resources(**kwargs: Any) -> Any:
    """按现有生产 wiring 构造资源图（薄转调 build_container，零行为变化）。"""
    from agentnodes.interfaces.api.container import build_container

    return build_container(**kwargs)


def register(container: Any) -> None:
    """登记资源图（幂等：同实例只登记一次）。"""
    global _ATEXIT_REGISTERED
    key = id(container)
    if key not in _REGISTRY:
        _REGISTRY[key] = container
    if not _ATEXIT_REGISTERED:
        # 无参 close_all：进程退出时关闭所有仍登记的资源图；不绑定容器，
        # close 后对象可被回收（weakref 回归测试守护）。
        atexit.register(close_all)
        _ATEXIT_REGISTERED = True


def unregister(container: Any) -> None:
    """从注册表移除（测试 reset 用），不自动 close。"""
    key = id(container)
    _REGISTRY.pop(key, None)
    _CLOSED.discard(key)


def resources_from_request(request: Request) -> Any | None:
    """FastAPI 请求侧定位：优先 app.state._container，无则 None（由
    dependencies.py 的现有 fallback 处理——本批不删 fallback）。"""
    return getattr(request.app.state, "_container", None)


#: 服务取用缓存（第二份 router 缓存归位此处，第二轮候选 4）。key = db_path，
#: value = SessionService。create_app 预置的 container 不在此缓存——它走
#: resources_from_request(request)；此缓存只服务无 request 的脚本/测试 fallback。
_SERVICE_CACHE: dict[str, Any] = {}


def session_service_from(request: Any = None) -> Any:
    """SessionService 的唯一取用点（第二轮候选 4）。

    - ``request`` 给定时：优先从 app.state._container 取（正常请求路径，
      create_app 已 attach）；
    - 无 request / container 时：按 db_path 从缓存取或构造（原 chat.py
      fallback 逐字迁入，含 composition.register 统一 close）。

    ``request=None`` 的 fallback 与 chat.py 原 `_get_session_service` 等价。
    """
    if request is not None:
        container = resources_from_request(request)
        if container is not None:
            return container.session_service

    from agentnodes.interfaces.api.container import build_container
    from agentnodes.kernel.session.db import _get_db_path

    db_path = str(_get_db_path())
    service = _SERVICE_CACHE.get(db_path)
    if service is None:
        container = build_container(db_path=db_path)
        register(container)  # 统一 close 生命周期（原 chat.py fallback 同款）
        service = container.session_service
        _SERVICE_CACHE[db_path] = service
    return service


def clear_service_cache() -> None:
    """清服务缓存（测试 reset 用；不 close——close 仍归 close_all）。"""
    _SERVICE_CACHE.clear()


# ── PermissionGateway 进程单例（第四轮 C3 自 routers/chat.py 迁入）──────
# One process-wide PermissionGateway（mirrors SessionService）。Wired with an
# ``on_request`` hook that pushes a ``permission_request`` SSE event for every
# ASK verdict; the front-end answers via ``POST /api/chat/permission/respond``
# which calls ``gateway.respond(tool_call_id, ...)``。
_permission_gateway_cache: dict[str, Any] = {}


def _safe_payload(args: dict) -> dict:
    """Drop large fields before SSE-serialising the request."""
    out = {}
    for k, v in args.items():
        if k.startswith("__") and k.endswith("__"):
            continue  # internal marker
        s = repr(v)
        out[k] = s[:200] + "…" if len(s) > 200 else v
    return out


def _push_event(session_id: str, event_type: str, data: Any) -> None:
    """A2: emit an SSE-visible event through the single EventStore track.

    The event store persists it (event_log), feeds live subscribers
    (``/api/chat/events`` via ``subscribe()``) and — when configured —
    projects it into messages. Payloads that were already JSON strings on
    the old track are accepted for a mechanical migration; dicts preferred.

    Never raises: SSE visibility must not break the request path.
    """
    import json as _json

    try:
        payload: Any = data
        if isinstance(data, str):
            try:
                payload = _json.loads(data)
            except (ValueError, TypeError):
                payload = {"raw": data}
        if not isinstance(payload, dict):
            payload = {"value": payload}
        session_service_from().event_bus.emit(session_id, event_type, payload)
    except Exception as exc:  # noqa: BLE001 — best effort by design
        logger.warning("[SSE] emit %s failed session=%s: %s", event_type, session_id, exc)


def get_permission_gateway(request: Any = None) -> Any:
    """Lazy-init the process-wide PermissionGateway（第四轮 C3 单一归属）。

    The ``on_request`` hook is wired on first use so we can attach the
    SSE buffer without circular imports. ``request`` 参数保留（与原
    router 签名同形，兼容既有调用方/测试锚），当前不使用。
    """
    from agentnodes.kernel.permission import PermissionGateway

    gateway = _permission_gateway_cache.get("default")
    if gateway is not None:
        return gateway

    def _push_sse(tool_call_id, decision, args):
        try:
            from agentnodes.kernel.events.event_v2 import EventType

            session_id = args.get("__session_id__") or ""
            if not session_id:
                # No session scope = no subscriber can see this.
                # Drop on the floor (and log so operators notice).
                logger.warning(
                    "permission request without session_id; dropping",
                )
                return
            # Tool name is implicit from the EventType subscriber;
            # payload carries call_id + args for the dialog UI.
            payload = {
                "tool_call_id": tool_call_id,
                "tool_name": args.get("__tool_name__", ""),
                "args": _safe_payload(args),
                "pattern": decision.pattern,
                "target": decision.target,
            }
            _push_event(session_id, EventType.PERMISSION_REQUEST, payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("permission SSE push failed: %s", exc)

    gateway = PermissionGateway(
        on_request=_push_sse,
    )
    _permission_gateway_cache["default"] = gateway
    return gateway


def delete_session_lifecycle(session_id: str, request: Any = None) -> bool:
    """删除会话的完整生命周期：软删归档 + 投影缓存失效（第三轮复审候选 S2）。

    **唯一拥有者（L1）**：Web ``DELETE /session/{id}`` 与 CLI ``session delete``
    共用本操作。此前 Web 侧自建归档 SQL + 私有失效函数，CLI 侧只归档不失效，
    两前端语义分叉——CLI 删除后 projector 缓存留着已删会话的投影。

    - 归档：委托 L3 ``reader.delete_session``（归档 SQL 的唯一出处；软删，
      消息与 event_log 保留——投影器/SSE 依赖它们继续可用，``GET`` 仍可读）。
      路径显式取 ``db._get_db_path()``——与 Web 侧原实现同源（tests 经该
      入口重定向 DB）；
    - 失效：丢弃 projector 的内存投影缓存（下次 ``project_incremental`` 全量
      重建——event_log 是唯一事实源）。

    ``request`` 给定时优先从 ``app.state._container`` 取该服务；否则遍历本模块
    的 ``_SERVICE_CACHE``（按 db_path 分容器，删除方不知道归属哪个，故全遍历）。

    返回 True 表示会话行存在且已被归档（SQLite UPDATE 的 rowcount 计匹配行，
    重复删除幂等返回 True）。
    """
    from agentnodes.kernel.session.db import _get_db_path
    from agentnodes.kernel.session.reader import delete_session

    existed = delete_session(session_id, db_path=_get_db_path())
    _invalidate_projection(session_id, request)
    return existed


def _invalidate_projection(session_id: str, request: Any = None) -> None:
    """丢弃已删会话的 projector 内存投影（best-effort，失败不阻断删除）。

    投影缓存挂在进程级 SessionService 持有的 EventStore 上。有 request 时
    走 DI container（create_app 已 attach）；否则遍历进程级服务缓存
    （脚本/测试 fallback——删除方未必知道会话归属哪个 db_path）。
    """
    try:
        if request is not None:
            container = resources_from_request(request)
            if container is not None:
                bus = getattr(container.session_service, "event_bus", None)
                invalidate = getattr(bus, "invalidate", None)
                if callable(invalidate):
                    invalidate(session_id)
                return
        for service in _SERVICE_CACHE.values():
            bus = getattr(service, "event_bus", None)
            invalidate = getattr(bus, "invalidate", None)
            if callable(invalidate):
                invalidate(session_id)
    except Exception as exc:  # noqa: BLE001 —— 失效失败不阻断删除
        logger.debug("projection invalidation skipped: %s", exc)


def close_resources(container: Any) -> None:
    """释放一份资源图的全部句柄（薄转调 close_container + 幂等标记）。"""
    from agentnodes.interfaces.api.container import close_container

    key = id(container)
    if key in _CLOSED:
        return
    _CLOSED.add(key)
    close_container(container)
    # 关闭后释放注册表引用：对象可被 gc 回收（17:06 复审候选 1）；
    # 幂等标记 _CLOSED 保留（防重复 close），不依赖注册表存在性。
    _REGISTRY.pop(key, None)


def close_all() -> None:
    """释放所有已登记资源图（测试 reset / 进程退出统一入口）。"""
    for container in list(_REGISTRY.values()):
        close_resources(container)
    _REGISTRY.clear()
