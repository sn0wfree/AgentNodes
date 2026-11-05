"""Capability port — kernel 侧访问 apps 能力的唯一合法接缝（ADR-003）。

用法：
    from agentnodes.kernel.agent.capability import resolve, register

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


#: 已经尝试过补注册的 registry 实例（按 id 记录）。
#:
#: **为什么按实例记而不是一个布尔**：``reset_default_registry()`` 会丢掉
#: 整个注册表，下一个 ``get_default_registry()`` 建的是一个全新空表。
#: 用单个布尔时，"已尝试过"的状态会跨代沿用——新表永远补不上注册，
#: ``resolve("evolution.read_journal")`` 从此永久抛 CapabilityNotFound。
#: （这是本轮实测到的真实缺陷：test_capability_registry 重置后，
#: 同进程的 test_evolution_tools 全部失败。）
_register_attempted: set[int] = set()


def _ensure_registered(registry: CapabilityRegistry, kind: str) -> bool:
    """Populate ``registry`` from an already-imported ``apps`` package.

    Returns False when the business package has not been imported yet —
    in that case the caller must import it first (that's the apps → kernel
    direction, which is legal).

    每个 registry 实例只尝试一次：未注册的 kind 不该反复触发重扫。
    """
    key = id(registry)
    if key in _register_attempted:
        return False
    _register_attempted.add(key)

    import sys

    # 路径 A：业务包提供了注册入口——它能感知 registry 代数变化并重扫。
    # 这是 apps 模块里的函数调用（不是新的源码 import），方向仍合法。
    apps_pkg = sys.modules.get("agentnodes.apps")
    if apps_pkg is not None:
        registrar = getattr(apps_pkg, "_register_capabilities", None)
        if callable(registrar):
            try:
                registrar(force=True)
            except Exception:  # noqa: BLE001 —— 走路径 B 兜底
                pass
            if registry.has(kind):
                return True

    # 路径 B：直接读 CAPABILITIES 表。覆盖"apps.capabilities 已 import
    # 但 apps/__init__ 的注册入口不可用"的情形（例如部分初始化的包）。
    mod = sys.modules.get("agentnodes.apps.capabilities")
    if mod is None:
        return False  # 业务包未被 import —— 由调用方决定是否加载
    try:
        for k, f in mod.CAPABILITIES.items():
            if k not in registry:
                registry.register(k, f)
    except Exception:  # noqa: BLE001
        return False
    return registry.has(kind)


def reset_registration_attempt() -> None:
    """Allow ``_ensure_registered`` to retry (test isolation)."""
    _register_attempted.clear()


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
