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
    from agentnodes.kernel.agent.event_store import EventStore
    from .session.service import SessionService
    from .session.store import SessionStore

# ── Cache keys on app.state (fallback when no container is attached) ─

_STATE_KEY_SESSION_STORE = "_session_store"
_STATE_KEY_EVENT_STORE = "_event_store"
_STATE_KEY_SESSION_SERVICE = "_session_service"


def _resolve_db_path() -> Path:
    """Return the SQLite DB path for the active workspace (fallback)."""
    from agentnodes.kernel.agent.memory_manager import resolve_session_db_path

    return resolve_session_db_path()


def get_db_path(request: Request) -> Path:
    """FastAPI dependency: the active unified session DB path."""
    container = getattr(request.app.state, "_container", None)
    if container is not None:
        return container.db_path
    workspace = getattr(request.app.state, "workspace_path", None)
    if workspace is not None:
        return Path(workspace) / ".quantnodes_agentnodes_session.db"
    return _resolve_db_path()


def get_session_store(request: Request) -> "SessionStore":
    """FastAPI dependency: shared SessionStore."""
    container = getattr(request.app.state, "_container", None)
    if container is not None:
        return container.session_store
    from .session.store import SessionStore

    store = getattr(request.app.state, _STATE_KEY_SESSION_STORE, None)
    if store is None:
        store = SessionStore(db_path=get_db_path(request))
        setattr(request.app.state, _STATE_KEY_SESSION_STORE, store)
    return store


def get_event_store(request: Request) -> "EventStore":
    """FastAPI dependency: shared EventStore (event_log + SSE + projector)."""
    container = getattr(request.app.state, "_container", None)
    if container is not None:
        return container.event_store
    from agentnodes.kernel.agent.event_store import EventStore

    store = getattr(request.app.state, _STATE_KEY_EVENT_STORE, None)
    if store is None:
        # A2: single SSE track — the events endpoint subscribes to this
        # store directly, so no bridge is needed.
        store = EventStore(db_path=get_db_path(request), flush_to_messages=True)
        setattr(request.app.state, _STATE_KEY_EVENT_STORE, store)
    return store


def get_session_service(request: Request) -> "SessionService":
    """FastAPI dependency: shared SessionService.

    Constructed once per process by the container; all routers share it.
    """
    container = getattr(request.app.state, "_container", None)
    if container is not None:
        return container.session_service
    from .session.service import SessionService

    svc = getattr(request.app.state, _STATE_KEY_SESSION_SERVICE, None)
    if svc is None:
        svc = SessionService(store=get_session_store(request), event_bus=get_event_store(request))
        setattr(request.app.state, _STATE_KEY_SESSION_SERVICE, svc)
    return svc


# ── Reset (testing utility) ─────────────────────────────────────────


def reset_app_state(app: Any) -> None:
    """Clear all cached services from ``app.state``.

    Useful between test cases to ensure fresh state.
    """
    for key in (_STATE_KEY_SESSION_STORE, _STATE_KEY_EVENT_STORE,
                _STATE_KEY_SESSION_SERVICE):
        if hasattr(app.state, key):
            delattr(app.state, key)


__all__ = [
    "get_db_path",
    "get_event_store",
    "get_session_service",
    "get_session_store",
    "reset_app_state",
]
