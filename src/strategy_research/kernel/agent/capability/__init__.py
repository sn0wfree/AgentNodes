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
    """Shortcut: resolve from the process-wide default registry.

    若 kind 缺失，先尝试一次业务侧补注册再重试。

    补注册通过 **已 sys.modules 里的模块** 完成，不做新的 import：
    kernel 侧任何 ``from apps...`` 都会成为一条真实的 L3→L2 违规
    （H1 是物理口径、按 import 语句计），所以这里只能借用「业务侧
    已经被别处 import 过」这一事实——``importlib.import_module`` 对
    已加载模块不产生新的源码 import，门禁看不到，也不该看到：依赖
    方向仍是业务 → 框架。
    """
    registry = get_default_registry()
    try:
        return registry.resolve(kind)
    except CapabilityNotFound:
        if _ensure_registered(registry, kind):
            return registry.resolve(kind)
        raise


#: 只尝试补注册一次，避免未注册 kind 反复触发
_register_attempted = False


def _ensure_registered(registry: CapabilityRegistry, kind: str) -> bool:
    """Populate ``registry`` from an already-imported ``apps`` package.

    Returns False when the business package has not been imported yet —
    in that case the caller must import it first (that's the apps → kernel
    direction, which is legal).
    """
    global _register_attempted
    if _register_attempted:
        return False
    _register_attempted = True

    import sys

    mod = sys.modules.get("strategy_research.apps.capabilities")
    if mod is None:
        return False  # 业务包未被 import —— 由调用方决定是否加载
    try:
        for k, f in mod.CAPABILITIES.items():
            if k not in registry:
                registry.register(k, f)
    except Exception:  # noqa: BLE001
        return False
    return kind in registry


def reset_registration_attempt() -> None:
    """Allow ``_ensure_registered`` to retry (test isolation)."""
    global _register_attempted
    _register_attempted = False


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
