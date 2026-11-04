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

    延迟到实际 import 本包时执行，且失败不影响 apps 自身的可用性——
    能力注册是 kernel 侧的需要，不该让业务包 import 失败。
    """
    try:
        from strategy_research.kernel.agent.capability import get_default_registry

        registry = get_default_registry()
        if len(registry) == 0:  # 幂等：已有注册就不重复
            from .capabilities import CAPABILITIES

            registry.register_many(dict(CAPABILITIES))
    except Exception:  # noqa: BLE001 —— 注册失败不阻断业务包 import
        import logging

        logging.getLogger(__name__).debug(
            "capability registration skipped", exc_info=True,
        )


_register_capabilities()
