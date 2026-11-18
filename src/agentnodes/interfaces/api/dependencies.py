"""FastAPI dependency providers (container-backed).

All providers resolve from the active :class:`AppContainer` when one is
attached (``create_app`` attaches it at startup); they fall back to
lazy direct construction + ``app.state`` caching when no container is
present (unit tests, scripts).

Test override::

    app.dependency_overrides[get_session_service] = lambda: fake_service
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Request

if TYPE_CHECKING:
    from agentnodes.kernel.session.state.event_store import EventStore
    from agentnodes.kernel.session.state.memory_manager import UnifiedMemoryManager
    from agentnodes.kernel.session.projector import Projector
    from agentnodes.kernel.session.service import SessionService
    from agentnodes.kernel.session.store import SessionStore

# ── Cache keys on app.state (fallback when no container is attached) ─

_STATE_KEY_SESSION_STORE = "_session_store"
_STATE_KEY_EVENT_STORE = "_event_store"
_STATE_KEY_SESSION_SERVICE = "_session_service"
_STATE_KEY_MEMORY_MANAGER = "_memory_manager"
_STATE_KEY_PROJECTOR = "_projector"


def _resolve_db_path() -> Path:
    """Return the SQLite DB path for the active workspace (fallback)."""
    from agentnodes.kernel.session.state.memory_manager import resolve_session_db_path

    return resolve_session_db_path()


def get_db_path(request: Request) -> Path:
    """FastAPI dependency: the active unified session DB path."""
    container = getattr(request.app.state, "_container", None)
    if container is not None:
        return container.db_path
    workspace = getattr(request.app.state, "workspace_path", None)
    if workspace is not None:
        # **2026-11-09**：文件名不再硬编码（原先在此重复拼了一遍
        # ``.quantnodes_agentnodes_session.db``，与 session_paths 的常量分叉）。
        from agentnodes.foundation.storage.session_paths import (
            session_db_filename,
        )

        return Path(workspace) / session_db_filename()
    return _resolve_db_path()


def get_projector(request: Request) -> "Projector":
    """Return the app-scoped projector shared by fallback providers."""
    container = getattr(request.app.state, "_container", None)
    if container is not None:
        return container.projector
    projector = getattr(request.app.state, _STATE_KEY_PROJECTOR, None)
    if projector is None:
        from agentnodes.kernel.session.projector import Projector

        projector = Projector(get_db_path(request))
        setattr(request.app.state, _STATE_KEY_PROJECTOR, projector)
    return projector


def get_session_store(request: Request) -> "SessionStore":
    """FastAPI dependency: shared SessionStore."""
    container = getattr(request.app.state, "_container", None)
    if container is not None:
        return container.session_store
    from agentnodes.kernel.session.store import SessionStore

    store = getattr(request.app.state, _STATE_KEY_SESSION_STORE, None)
    if store is None:
        store = SessionStore(
            db_path=get_db_path(request),
            projector=get_projector(request),
        )
        setattr(request.app.state, _STATE_KEY_SESSION_STORE, store)
    return store


def get_event_store(request: Request) -> "EventStore":
    """FastAPI dependency: shared EventStore (event_log + SSE + projector)."""
    container = getattr(request.app.state, "_container", None)
    if container is not None:
        return container.event_store
    from agentnodes.kernel.session.state.event_store import EventStore

    store = getattr(request.app.state, _STATE_KEY_EVENT_STORE, None)
    if store is None:
        # A2: single SSE track — the events endpoint subscribes to this
        # store directly, so no bridge is needed.
        store = EventStore(
            db_path=get_db_path(request),
            flush_to_messages=True,
            projection_sink=get_projector(request),
        )
        setattr(request.app.state, _STATE_KEY_EVENT_STORE, store)
    return store


def get_memory_manager(request: Request) -> "UnifiedMemoryManager":
    """Return the app-scoped MemoryManager wired to the same projector."""
    container = getattr(request.app.state, "_container", None)
    if container is not None:
        return container.memory_manager
    manager = getattr(request.app.state, _STATE_KEY_MEMORY_MANAGER, None)
    if manager is None:
        from agentnodes.kernel.session.state.memory_manager import UnifiedMemoryManager

        manager = UnifiedMemoryManager(
            db_path=get_db_path(request),
            projection_sink=get_projector(request),
        )
        setattr(request.app.state, _STATE_KEY_MEMORY_MANAGER, manager)
    return manager


def get_session_service(request: Request) -> "SessionService":
    """FastAPI dependency: shared SessionService.

    Constructed once per process by the container; all routers share it.
    """
    container = getattr(request.app.state, "_container", None)
    if container is not None:
        return container.session_service
    from agentnodes.kernel.session.service import SessionService

    svc = getattr(request.app.state, _STATE_KEY_SESSION_SERVICE, None)
    if svc is None:
        from agentnodes.interfaces.api.routers.chat import _get_permission_gateway

        svc = SessionService(
            store=get_session_store(request),
            event_bus=get_event_store(request),
            # **W5**：注入带 SSE 推送钩子的进程级审批通道——与
            # ``/api/chat/permission/respond`` 端点共用同一个实例，
            # 于是用户点的审批能真正递到正在等它的工具调用上。
            permission_gateway=_get_permission_gateway(),
        )
        setattr(request.app.state, _STATE_KEY_SESSION_SERVICE, svc)
    return svc


# ── Reset (testing utility) ─────────────────────────────────────────


def reset_app_state(app: Any) -> None:
    """Clear all cached services from ``app.state``.

    Useful between test cases to ensure fresh state.
    """
    keys = (
        _STATE_KEY_SESSION_SERVICE,
        _STATE_KEY_MEMORY_MANAGER,
        _STATE_KEY_EVENT_STORE,
        _STATE_KEY_SESSION_STORE,
        _STATE_KEY_PROJECTOR,
    )
    closed: set[int] = set()
    for key in keys:
        service = getattr(app.state, key, None)
        close = getattr(service, "close", None)
        if callable(close) and id(service) not in closed:
            close()
            closed.add(id(service))
        if hasattr(app.state, key):
            delattr(app.state, key)


__all__ = [
    "get_db_path",
    "get_event_store",
    "get_memory_manager",
    "get_projector",
    "get_session_service",
    "get_session_store",
    "reset_app_state",
]
