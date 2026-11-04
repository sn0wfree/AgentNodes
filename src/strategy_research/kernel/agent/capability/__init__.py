"""Capability port — kernel 侧访问 apps 能力的唯一合法接缝（ADR-003）。

用法：
    from strategy_research.kernel.agent.capability import resolve, register

    # 业务侧（apps/*/__init__.py 的 register_capabilities）
    register("db.connection", lambda reg: _make_connection_factory())

    # kernel 侧
    factory = resolve("db.connection")
    conn = factory(workspace)
"""

from __future__ import annotations

from .registry import (
    CapabilityAlreadyRegistered,
    CapabilityFactory,
    CapabilityNotFound,
    CapabilityRegistry,
    get_default_registry,
    reset_default_registry,
    set_default_registry,
)


def register(kind: str, factory: CapabilityFactory) -> None:
    """Shortcut: register on the process-wide default registry."""
    get_default_registry().register(kind, factory)


def resolve(kind: str):
    """Shortcut: resolve from the process-wide default registry."""
    return get_default_registry().resolve(kind)


def list_kinds() -> list[str]:
    """Shortcut: list kinds on the process-wide default registry."""
    return get_default_registry().list_kinds()


__all__ = [
    "CapabilityAlreadyRegistered",
    "CapabilityFactory",
    "CapabilityNotFound",
    "CapabilityRegistry",
    "get_default_registry",
    "list_kinds",
    "register",
    "reset_default_registry",
    "resolve",
    "set_default_registry",
]
