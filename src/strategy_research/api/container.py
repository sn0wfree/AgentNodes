"""Lightweight DI container for the strategy-research API.

Single construction point for the long-lived API services:

    container = build_container()
    attach_container(app, container)      # called by api/app.py::create_app

Routers resolve services via :mod:`api.dependencies` (container-backed,
falling back to direct construction when no container is attached, e.g.
unit tests or scripts). ``create_app`` additionally pre-seeds the legacy
``routers/chat.py`` singleton cache with the container's services so both
access paths share the same instances.

The container wires the production event-sourced path: ``SessionStore`` +
``EventStore`` (event_log + SSE push + projector flush) bound to the
unified session DB path (``resolve_session_db_path``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from fastapi import Request

if TYPE_CHECKING:
    from ..core.agent.event_store import EventStore
    from .session.service import SessionService
    from .session.store import SessionStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppContainer:
    """The bag of long-lived services used by API routers.

    Constructed once at app startup; read-only thereafter. New services
    that need cross-router state should be added here.

    Attributes:
        workspace_path: User's workspace directory (for project files).
        db_path: Unified session SQLite database path.
        session_store: SessionStore bound to db_path.
        event_store: EventStore (event_log + SSE push + projector flush).
        session_service: SessionService wired with the event store.
    """

    workspace_path: Path | None
    db_path: Path
    session_store: "SessionStore"
    event_store: "EventStore"
    session_service: "SessionService"


# ── Factory ──────────────────────────────────────────────────────────


def build_container(
    *,
    workspace_path: Path | str | None = None,
    db_path: Path | str | None = None,
    session_store_factory: Callable[[Path], "SessionStore"] | None = None,
    event_store_factory: Callable[[Path], "EventStore"] | None = None,
    session_service_factory: Callable[["SessionStore", "EventStore"], "SessionService"] | None = None,
) -> AppContainer:
    """Construct the application container.

    Args:
        workspace_path: Workspace directory (used for project files).
        db_path: SQLite database path. Defaults to
            ``core.agent.memory_manager.resolve_session_db_path`` (the
            unified session DB).
        *_factory: Optional factory overrides for testing. Each receives
            the standard constructor args and must return a compatible
            instance.

    Returns:
        Fully constructed AppContainer with all services wired.
    """
    from ..core.agent.event_store import EventStore
    from ..core.agent.memory_manager import resolve_session_db_path
    from .session.service import SessionService
    from .session.store import SessionStore

    if db_path is None:
        db_path = resolve_session_db_path()
    db_path = Path(db_path)

    # Construct services in dependency order
    session_store = (session_store_factory or SessionStore)(db_path)
    event_store = (event_store_factory or (lambda p: EventStore(p, flush_to_messages=True)))(db_path)
    # A2: no SSE bridge — /api/chat/events subscribes to the event store
    # directly (single track).
    session_service = (session_service_factory or SessionService)(session_store, event_store)

    return AppContainer(
        workspace_path=Path(workspace_path) if workspace_path else None,
        db_path=db_path,
        session_store=session_store,
        event_store=event_store,
        session_service=session_service,
    )


# ── FastAPI integration ──────────────────────────────────────────────

# Key used on app.state to store the container.
_STATE_KEY_CONTAINER = "_container"


def attach_container(app: Any, container: AppContainer) -> None:
    """Attach a container to ``app.state`` so :func:`get_container` finds it."""
    setattr(app.state, _STATE_KEY_CONTAINER, container)
    logger.info("AppContainer attached to FastAPI app")


def get_container(request: Request) -> AppContainer:
    """FastAPI dependency that returns the active :class:`AppContainer`.

    Raises:
        RuntimeError: if no container is attached. Callers should set one
                      via :func:`attach_container` at app startup.
    """
    container = getattr(request.app.state, _STATE_KEY_CONTAINER, None)
    if container is None:
        raise RuntimeError(
            "AppContainer not attached. Call attach_container(app, build_container(...)) "
            "in your FastAPI lifespan or factory."
        )
    return container


def reset_container(app: Any) -> None:
    """Detach the container (for tests)."""
    if hasattr(app.state, _STATE_KEY_CONTAINER):
        delattr(app.state, _STATE_KEY_CONTAINER)


def close_container(container: AppContainer) -> None:
    """Release the container's SQLite handles.

    每个 service 各自持有一条连接（SessionStore / EventStore）。Windows 上
    未关闭的句柄会让临时目录无法删除（WinError 32，且会跨测试污染后续用例），
    生产里 app shutdown 也需要显式释放。顺序与构造相反；best-effort——
    释放失败不影响其他服务。

    W1-8（2026-11-03）：此前无任何释放契约，测试用 TemporaryDirectory 建库
    后直接泄漏句柄，成为全量套件 55 项级联失败的污染源。
    """
    for attr in ("session_service", "event_store", "session_store"):
        svc = getattr(container, attr, None)
        if svc is None:
            continue
        close = getattr(svc, "close", None)
        if close is None:
            continue
        try:
            close()
        except Exception as exc:  # noqa: BLE001 — 释放尽力而为
            logger.debug("close_container: %s close failed: %s", attr, exc)


# ── Adapter for app.state bulk-assignment ────────────────────────────


def services_from_container(container: AppContainer) -> dict[str, Any]:
    """Return the dict of services a container provides.

    Useful for bulk-assignment to ``app.state`` so legacy accessors
    (``routers/chat.py::_get_session_service``) stay populated:

        for key, svc in services_from_container(container).items():
            setattr(app.state, key, svc)
    """
    return {
        "_session_service": container.session_service,
        "_session_store": container.session_store,
        "_event_store": container.event_store,
    }


__all__ = [
    "AppContainer",
    "attach_container",
    "build_container",
    "close_container",
    "get_container",
    "reset_container",
    "services_from_container",
]
