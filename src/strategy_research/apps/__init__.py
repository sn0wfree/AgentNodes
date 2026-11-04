"""Apps layer (L2) - research-domain IP (study/goal/alpha_zoo/backtest...).

依赖方向：apps → kernel → foundation（见 tests/test_layer_matrix.py）。

**能力注册（ADR-003 端口化）**：apps 是被 kernel 依赖的一侧，因此由
**apps 主动注册**进 kernel 的 CapabilityRegistry——import 方向仍是
apps → kernel，H1 不受影响。

注册是 import 副作用，幂等：kernel 侧 ``get_default_registry()`` 保证
只构造一次，``register_capabilities`` 在已注册时静默跳过。
"""

from __future__ import annotations


def _register_capabilities() -> None:
    """Register business capabilities on the kernel's default registry.

    延迟到实际 import 本包时执行，且**完全幂等**：

    - ``len(registry) == 0`` 快速路径（首次注册）
    - 逐 kind ``register`` 并吞掉 ``CapabilityAlreadyRegistered``
      （并发/xdist worker、重复 import、测试 reset 后的再注册都会
      走到这里；注册表本身不需要"只准注册一次"的强约束，因为它
      的语义是"这个 kind 有没有实现"，不是"注册动作是否重复"）

    失败不影响 apps 自身的可用性——能力注册是 kernel 侧的需要，
    不该让业务包 import 失败。
    """
    try:
        from strategy_research.kernel.agent.capability import get_default_registry

        registry = get_default_registry()
        from .capabilities import CAPABILITIES

        for kind, factory in CAPABILITIES.items():
            if kind in registry:  # 幂等：已注册则跳过
                continue
            registry.register(kind, factory)
    except Exception:  # noqa: BLE001 —— 注册失败不阻断业务包 import
        import logging

        logging.getLogger(__name__).debug(
            "capability registration skipped", exc_info=True,
        )


_register_capabilities()
