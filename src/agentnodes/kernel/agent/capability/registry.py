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

**W1 能力自检（2026-11-06 · 理念三）**：kind 可以声明 ``requires``
（它运行时依赖的其他 kind）。注册时若依赖不全，**整组不注册**并记入
跳过集 + emit ``capability.skipped``——绝不注册一个「包装存在、调用必败」
的壳（扫描 §4 的 26 个幽灵工具就是这么来的）。

依赖晚到时（新 kind 注册成功）会对跳过集做一次提升重算——但只在
``_skipped`` 域内迭代，不会因动态注册陷入无限循环。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

CapabilityFactory = Callable[["CapabilityRegistry"], Any]

#: kind 声明形态：``(factory, requires)`` 元组，或裸 factory（requires 视为空）。
CapabilityEntry = Any


def _normalize(value: CapabilityEntry) -> tuple[CapabilityFactory, frozenset[str]]:
    """把注册值规范化为 ``(factory, requires)``。

    兼容两种形态（W1 迁移期）：
      - 裸 callable        → ``(factory, frozenset())``
      - ``(factory, reqs)`` → 原样（reqs 可为 set / frozenset / list / tuple）
    """
    if isinstance(value, tuple) and len(value) == 2:
        factory, reqs = value
        return factory, frozenset(reqs or ())
    return value, frozenset()


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
        self._requires: dict[str, frozenset[str]] = {}
        #: kind → (factory, 缺失的依赖集)。自检拒绝注册时的完整快照——
        #: 必须连 factory 一起存，否则依赖晚到后无法真正补注册。
        self._skipped: dict[str, tuple[CapabilityFactory, frozenset[str]]] = {}
        #: 装配期收集的声明（两阶段装配的第一阶段产物）
        self._pending: dict[str, tuple[CapabilityFactory, frozenset[str]]] = {}

    # ── 注册 ────────────────────────────────────────────────────────

    def register(
        self,
        kind: str,
        factory: CapabilityFactory,
        *,
        requires: Iterable[str] = (),
    ) -> bool:
        """注册一个 capability factory。

        ``requires`` 是本 kind **运行时**依赖的其他 kind 清单（W1 能力自检）。
        依赖不全时**不注册**、记入跳过集、emit ``capability.skipped`` 并返回
        ``False``——这是降级而非错误，刻意不抛异常（缺业务能力不该阻断启动）。

        重复注册抛 ``CapabilityAlreadyRegistered``（fail-fast）——
        静默覆盖会让"谁最后注册谁生效"变成隐蔽的加载顺序依赖。

        :return: True = 已注册；False = 因依赖缺失被跳过（已记入跳过集）
        """
        if not kind or not isinstance(kind, str):
            raise ValueError(f"kind must be a non-empty str, got {kind!r}")
        if not callable(factory):
            raise ValueError(f"factory for {kind!r} must be callable")
        if kind in self._factories:
            raise CapabilityAlreadyRegistered(kind)

        reqs = frozenset(requires or ())
        missing = reqs - set(self._factories)
        if missing:
            self._skipped[kind] = (factory, missing)
            self._emit_skipped(kind, missing)
            return False

        self._factories[kind] = factory
        self._requires[kind] = reqs
        logger.debug("capability registered: %s", kind)
        self._promote_skipped()
        return True

    def register_many(self, mapping: dict[str, CapabilityEntry]) -> None:
        """两阶段批量注册：**先收集全清单、再算可注册集、最后提交**。

        与逐条 ``register`` 的差别是**顺序无关性**：声明之间的依赖无论谁先谁后，
        结果都相同。逐条注册会让 skip 判定依赖 import 顺序（``register`` 的
        docstring 自己警告过的「隐蔽的加载顺序依赖」）。

        重复 kind 仍 fail-fast（先校验后写入）。依赖缺失的 kind 记入跳过集 +
        emit ``capability.skipped``，不影响其余 kind 的注册。
        """
        normalized = {k: _normalize(v) for k, v in mapping.items()}

        # 阶段 1 · 校验重复（fail-fast，整体不生效）
        declared = set(normalized) | set(self._pending)
        dupes = [k for k in normalized if k in self._factories]
        if dupes:
            raise CapabilityAlreadyRegistered(dupes[0])

        # 阶段 2 · 求可注册闭包（循环至不动点：一个 kind 的依赖可能是同批另一个）
        available = set(self._factories) | set(self._pending)
        ready: dict[str, tuple[CapabilityFactory, frozenset[str]]] = {}
        remaining = dict(normalized)
        while remaining:
            progressed = False
            for kind, (factory, reqs) in list(remaining.items()):
                if reqs <= available:
                    ready[kind] = (factory, reqs)
                    available.add(kind)
                    remaining.pop(kind)
                    progressed = True
            if not progressed:
                break  # 剩余的都缺依赖（或互相成环）→ 全部跳过

        # 阶段 3 · 提交
        for kind, (factory, reqs) in ready.items():
            self._factories[kind] = factory
            self._requires[kind] = reqs
            logger.debug("capability registered: %s", kind)
        for kind, (factory, reqs) in remaining.items():
            missing = frozenset(reqs - set(self._factories))
            self._skipped[kind] = (factory, missing)
            self._emit_skipped(kind, missing, reason="missing_dependencies")
        if declared:
            self._promote_skipped()

    def declare(self, mapping: dict[str, CapabilityEntry]) -> None:
        """第 0 阶段：把声明收集进 pending（供后续 ``register_many`` 合并）。

        两阶段装配的显式入口：调用方可以在所有业务包 import 完成后一次性
        ``register_many``，从而让依赖判定完全与 import 顺序解耦。
        非强制——``register_many`` 自身已是顺序无关的。
        """
        for kind, value in mapping.items():
            self._pending[kind] = _normalize(value)

    # ── 自检 ────────────────────────────────────────────────────────

    def skipped(self) -> dict[str, frozenset[str]]:
        """因依赖不全而未注册的 kind → 缺失依赖集（审计/测试用）。"""
        return {k: v[1] for k, v in self._skipped.items()}

    def requires_of(self, kind: str) -> frozenset[str]:
        """已注册 kind 声明的依赖集（未注册返回空）。"""
        return self._requires.get(kind, frozenset())

    def verify_dependencies(self) -> dict[str, list[str]]:
        """全表一致性检查：已注册 kind 的依赖是否真的都在表内。

        正常路径下恒为空（``register`` 已经拦过）。非空说明有人绕过
        ``register`` 直接改 ``_factories``——返回 ``{kind: [缺失依赖]}``。
        """
        broken: dict[str, list[str]] = {}
        for kind, reqs in self._requires.items():
            missing = reqs - set(self._factories)
            if missing:
                broken[kind] = sorted(missing)
        return broken

    def _promote_skipped(self) -> None:
        """依赖晚到时提升之前被跳过的 kind——**真正补注册**。

        只遍历 ``_skipped`` 快照（有限集），每个 kind 成功注册后从跳过集移除，
        因此不会因为动态注册陷入循环。

        修复记录（2026-11-06，W1 自测）：首版只从跳过集移除、没有把 factory
        写回 ``_factories``——``kinds`` 里看不到它，``resolve`` 仍抛
        ``CapabilityNotFound``。跳过集必须连 factory 一起存。
        """
        if not self._skipped:
            return
        progressed = True
        while progressed:
            progressed = False
            for kind, (factory, missing) in list(self._skipped.items()):
                if kind in self._factories:
                    self._skipped.pop(kind, None)
                    continue
                if missing <= set(self._factories):
                    self._skipped.pop(kind, None)
                    self._factories[kind] = factory
                    self._requires[kind] = frozenset(
                        self._pending.get(kind, (None, frozenset()))[1]
                    ) or frozenset()
                    progressed = True
                    logger.info(
                        "capability promoted after dependency arrived: %s", kind,
                    )
                    self._emit("capability.promoted", {"kind": kind})

    def _emit_skipped(
        self, kind: str, missing: frozenset[str], *, reason: str = "missing_dependencies"
    ) -> None:
        """emit ``capability.skipped`` —— 理念三「绝不允许静默」。

        import 期事件基础设施通常未接线，所以：有 sink 走 sink，否则
        **至少 WARNING 级日志**（不是 debug——静默是缺陷本身）。
        """
        payload = {"kind": kind, "missing": sorted(missing), "reason": reason}
        logger.warning(
            "capability skipped (not registered): %s missing=%s",
            kind, sorted(missing),
        )
        self._emit("capability.skipped", payload)

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        for fn in tuple(_skip_sinks):
            try:
                fn(event, dict(payload))
            except Exception:  # noqa: BLE001 —— 通知失败不得影响注册
                logger.debug("capability sink failed", exc_info=True)

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

#: 跳过/提升事件的出口（``(event, payload) -> None``）。
#:
#: import 期没有事件 sink，所以默认**没有** sink——此时
#: ``capability.skipped`` 退化为 WARNING 日志（见 ``_emit_skipped``）。
#: 装配根（L1）可以在接线后登记一个真正的 sink，让跳过可观测。
_skip_sinks: list[Any] = []


def register_skip_sink(fn: Any) -> None:
    """登记 ``capability.skipped`` / ``capability.promoted`` 的出口（幂等）。

    装配根在事件基础设施就绪后调用；未登记时事件退化为 WARNING 日志。
    """
    if fn not in _skip_sinks:
        _skip_sinks.append(fn)


def reset_skip_sinks() -> None:
    """清空 sink 登记（测试隔离用）。"""
    _skip_sinks.clear()


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
    "register_skip_sink",
    "registry_generation",
    "reset_default_registry",
    "reset_skip_sinks",
    "set_default_registry",
]
