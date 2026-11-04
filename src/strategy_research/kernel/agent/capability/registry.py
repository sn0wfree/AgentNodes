"""CapabilityRegistry — kernel 侧访问 apps 能力的唯一入口。

**为什么存在**：H1（ADR-000 物理口径）规定 kernel 不得 import apps。
但 kernel 的若干通用机制（工具、上下文注入、执行注册表）确实需要研究域
能力（回测 / 因子 / 数据 / 目标）。ADR-003 的处置是「端口化」：业务侧
注册实现，kernel 侧按 kind 解析——依赖方向仍是从业务到框架。

**职责边界**：
- 只做 kind → factory 的映射，不做实例缓存决策之外的任何编排
- 重复注册**fail-fast**（否则后注册者静默覆盖，排障极难）
- ``resolve`` 返回 factory 生产的新实例；需要单例的由 factory 自己备忘
  （Registry 不替调用方承担生命周期，那是 service locator 化的开端）

**注册时机**：业务侧入口装配阶段（``apps/*/__init__.py`` 的
``register_capabilities(registry)``）。kernel 启动时通过 Gateway 的
装配路径拿到已注册的 registry。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

CapabilityFactory = Callable[["CapabilityRegistry"], Any]


class CapabilityNotFound(KeyError):
    """Raised by ``resolve()`` when a kind was never registered."""

    def __init__(self, kind: str, available: list[str] | None = None) -> None:
        self.kind = kind
        self.available = sorted(available or [])
        msg = f"capability not registered: {kind!r}"
        if self.available:
            msg += f" (available: {', '.join(self.available)})"
        super().__init__(msg)


class CapabilityAlreadyRegistered(KeyError):
    """Raised by ``register()`` on a duplicate kind (fail-fast)."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(f"capability already registered: {kind!r}")


class CapabilityRegistry:
    """kind → factory 的注册表。kernel 与 apps 之间唯一的合法接缝。"""

    def __init__(self) -> None:
        self._factories: dict[str, CapabilityFactory] = {}

    # ── 注册 ────────────────────────────────────────────────────────

    def register(self, kind: str, factory: CapabilityFactory) -> None:
        """注册一个 capability factory。

        重复注册抛 ``CapabilityAlreadyRegistered``（fail-fast）——
        静默覆盖会让"谁最后注册谁生效"变成隐蔽的加载顺序依赖。
        """
        if not kind or not isinstance(kind, str):
            raise ValueError(f"kind must be a non-empty str, got {kind!r}")
        if not callable(factory):
            raise ValueError(f"factory for {kind!r} must be callable")
        if kind in self._factories:
            raise CapabilityAlreadyRegistered(kind)
        self._factories[kind] = factory
        logger.debug("capability registered: %s", kind)

    def register_many(self, mapping: dict[str, CapabilityFactory]) -> None:
        """批量注册。任一项冲突则整体不生效（先校验后写入）。"""
        dupes = [k for k in mapping if k in self._factories]
        if dupes:
            raise CapabilityAlreadyRegistered(dupes[0])
        for kind, factory in mapping.items():
            self.register(kind, factory)

    # ── 解析 ────────────────────────────────────────────────────────

    def resolve(self, kind: str) -> Any:
        """解析 capability：调用 factory 并返回其产物。

        未注册抛 ``CapabilityNotFound``（带可用 kind 列表，便于排障）。
        """
        try:
            factory = self._factories[kind]
        except KeyError:
            raise CapabilityNotFound(kind, list(self._factories)) from None
        return factory(self)

    # ── 审计 ────────────────────────────────────────────────────────

    def list_kinds(self) -> list[str]:
        """列出已注册 kind（门禁审计用）。"""
        return sorted(self._factories)

    def has(self, kind: str) -> bool:
        return kind in self._factories

    def __contains__(self, kind: object) -> bool:
        return isinstance(kind, str) and kind in self._factories

    def __len__(self) -> int:
        return len(self._factories)

    def __repr__(self) -> str:
        return f"CapabilityRegistry({', '.join(self.list_kinds())})"


# ── 进程级默认实例 ──────────────────────────────────────────────────
#
# kernel 侧代码多数是模块级函数（工具类、注入器），没有构造参数可注入
# registry。这类调用点用 ``get_default_registry()`` 拿到**业务侧注册好的**
# 那一份。
#
# **注册责任在业务侧**：kernel 绝不 import apps（H1）。业务包在被 import
# 时自行注册（``apps/capabilities.py`` 的 ``register_capabilities`` 由
# ``apps/__init__.py`` 调用）。kernel 只维护一张空表，等别人来填。
#
# 代价：若业务包从未被 import，resolve 会抛 CapabilityNotFound。这是
# 有意的——它把"框架被当成独立库使用"与"业务侧没接上"区分开，比静默
# 降级成空实现更容易排障。

_default: CapabilityRegistry | None = None

#: registry 被重置时递增。业务侧的注册入口可以据此判断"这张表是新的、
#: 需要重新填"，而不必去 import kernel 的内部状态。
_generation: int = 0

#: 填充钩子：业务包在自身 import 时注册一个 ``registry -> None`` 的回调。
#:
#: **为什么需要它**：注册是 import 副作用，而 import 在模块已缓存时不会
#: 重跑。``reset_default_registry()`` 之后新表永远等不到注册，``resolve()``
#: 从此永久失败（本轮的实测缺陷）。钩子让"新建默认表"这件事能主动
#: 触发填充——方向是 apps 注册、kernel 调用，不构成 L3→L2 上行 import。
_populators: list[Any] = []


def register_populator(fn: Any) -> None:
    """业务包登记一个填充器（幂等：同一函数只登记一次）。"""
    if fn not in _populators:
        _populators.append(fn)


def _run_populators(registry: CapabilityRegistry) -> None:
    """依次执行填充器。任一失败只记日志，不影响其他填充器。"""
    for fn in tuple(_populators):
        try:
            fn(registry)
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).debug(
                "capability populator failed", exc_info=True,
            )


def registry_generation() -> int:
    """当前默认 registry 的代数（每次 reset/set 递增）。"""
    return _generation


def get_default_registry() -> CapabilityRegistry:
    """Return the process-wide registry (created on first access).

    新建实例时执行一次已登记的填充器——业务包若已 import 过，注册表
    立刻就有内容；若没 import 过，钩子列表为空，表保持空（调用方
    需先 import 业务包，方向合法）。
    """
    global _default
    if _default is None:
        _default = CapabilityRegistry()
        _run_populators(_default)
    return _default


def set_default_registry(registry: CapabilityRegistry) -> None:
    """Install the process-wide registry (装配期调用一次)."""
    global _default, _generation
    _default = registry
    _generation += 1


def reset_default_registry() -> None:
    """Drop the process-wide registry (test isolation)."""
    global _default, _generation
    _default = None
    _generation += 1


__all__ = [
    "CapabilityAlreadyRegistered",
    "CapabilityFactory",
    "CapabilityNotFound",
    "CapabilityRegistry",
    "get_default_registry",
    "register_populator",
    "registry_generation",
    "reset_default_registry",
    "set_default_registry",
]
