"""Apps layer (L2) - research-domain IP (study/goal/alpha_zoo/backtest...).

依赖方向：apps → kernel → foundation（见 tests/test_layer_matrix.py）。

**能力注册（ADR-003 端口化）**：apps 是被 kernel 依赖的一侧，因此由
**apps 主动注册**进 kernel 的 CapabilityRegistry——import 方向仍是
apps → kernel，H1 不受影响。

注册是 import 副作用，幂等：kernel 侧 ``get_default_registry()`` 保证
只构造一次，``register_capabilities`` 在已注册时静默跳过。
"""

from __future__ import annotations

#: 上次完成注册时的 registry 代数（见 ``kernel.agent.capability.registry``）。
#: import 是幂等的，但注册必须能响应"registry 被换掉了"——否则
#: ``reset_default_registry()`` 之后新表永远填不上。
_registered_generation: int = -1


def _register_capabilities(force: bool = False) -> None:
    """Register business capabilities on the kernel's default registry.

    延迟到实际 import 本包时执行，且**幂等**：

    - 同一代数重复调用直接返回（``_registered_generation`` 相等）
    - 逐 kind ``register`` 并吞掉 ``CapabilityAlreadyRegistered``
      （并发/xdist worker、重复 import、测试 reset 后的再注册都会
      走到这里；注册表本身不需要"只准注册一次"的强约束，因为它
      的语义是"这个 kind 有没有实现"，不是"注册动作是否重复"）

    失败不影响 apps 自身的可用性——能力注册是 kernel 侧的需要，
    不该让业务包 import 失败。

    :param force: 忽略代数检查强制重扫（测试/装配根显式调用）
    """
    global _registered_generation
    try:
        from agentnodes.kernel.agent.capability import get_default_registry
        from agentnodes.kernel.agent.capability.registry import (
            registry_generation,
        )

        generation = registry_generation()
        if not force and generation == _registered_generation:
            return

        registry = get_default_registry()
        from .capabilities import CAPABILITIES

        for kind, factory in CAPABILITIES.items():
            if kind in registry:  # 幂等：已注册则跳过
                continue
            registry.register(kind, factory)
        _registered_generation = generation
    except Exception:  # noqa: BLE001 —— 注册失败不阻断业务包 import
        import logging

        logging.getLogger(__name__).debug(
            "capability registration skipped", exc_info=True,
        )


def _populate(registry) -> None:
    """填充器：kernel 新建默认表时把业务能力填进去。

    与 ``_register_capabilities()`` 的差别只在**触发方式**：后者是 import
    副作用（模块已缓存时不会重跑），本函数由 kernel 在 ``get_default_registry()``
    新建表时主动调用。两者填的是同一份 ``CAPABILITIES``。
    """
    from .capabilities import CAPABILITIES

    for kind, factory in CAPABILITIES.items():
        if kind in registry:
            continue
        registry.register(kind, factory)


_register_capabilities()

# 向 kernel 登记填充器（apps → kernel，方向合法）。这样
# ``reset_default_registry()`` 之后新建的表也能被填满，而不是永远空着。
try:
    from agentnodes.kernel.agent.capability.registry import (
        register_populator,
    )

    register_populator(_populate)
except Exception:  # noqa: BLE001
    import logging

    logging.getLogger(__name__).debug(
        "capability populator not registered", exc_info=True,
    )
