"""装配期 harness 解析的统一入口（施工方案 §4.7）。

三个装配点（PromptBuilderFactory / ToolRegistry / CompactConfig）都需要
"我在这个 task_signature 下应该用哪版 prompt / 工具集 / 压缩配置"。
把这段逻辑集中在一处，而不是三处各写一遍：

1. **查找路径唯一**：只在装配期问一次，回答来自 ``genome_version.tier1_active``
2. **失败即降级**：RSI 未启用、业务包缺失、解析异常 → 返回空配置，
   装配照常进行。RSI 是增强，不是前置条件
3. **不进 AgentLoop**：本模块的调用点全部在装配期（构造/首次获取），
   迭代路径上不该出现它（施工方案 §4.7 末条）

**为什么这个模块在 kernel 而非 apps**：它是**调用方**（三个 kernel 装配点）
的公共代码。真正的数据来自 apps.evolution，但通过 CapabilityRegistry
解析，不直接 import——L3→L2 保持为 0。
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class HarnessConfig(Protocol):
    """装配期需要的三个字段（``apps.evolution.EffectiveHarnessConfig`` 满足它）。"""

    prompt_version: dict[str, Any]
    tool_set: frozenset[str] | None
    compaction_config: dict[str, Any]

    def is_empty(self) -> bool: ...


class _EmptyHarness:
    prompt_version: dict[str, Any] = {}
    tool_set: frozenset[str] | None = None
    compaction_config: dict[str, Any] = {}

    def is_empty(self) -> bool:
        return True


_EMPTY = _EmptyHarness()


def resolve_harness(task_signature: str) -> HarnessConfig:
    """Return the effective harness config for ``task_signature``.

    永不抛出：任何失败都降级为空配置（= 不启用 RSI 的默认行为）。
    这是刻意的——装配期抛错会让整个框架起不来，而 RSI 只是增强。
    """
    if not task_signature:
        return _EMPTY
    try:
        from strategy_research.kernel.agent.capability import resolve

        resolver = resolve("evolution.resolve_harness")
        return resolver(task_signature)
    except Exception as exc:  # noqa: BLE001 —— 降级而非崩溃
        logger.debug("harness resolve skipped for %r: %s", task_signature, exc)
        return _EMPTY


def apply_tool_set(
    available: list[str], task_signature: str
) -> list[str]:
    """装配期按生效的 tool_set 收敛工具白名单。

    ``tool_set`` 为 None（未启用 RSI）时原样返回；否则取交集——
    **只做减法**：RSI 不能凭空启用未注册的工具，否则等于绕过权限闸。
    """
    cfg = resolve_harness(task_signature)
    if cfg.tool_set is None:
        return available
    allowed = set(cfg.tool_set)
    filtered = [t for t in available if t in allowed]
    if len(filtered) != len(available):
        logger.info(
            "harness tool_set trimmed %d → %d tools for %r",
            len(available), len(filtered), task_signature,
        )
    return filtered


def apply_compaction_overrides(compact_config: Any, task_signature: str) -> Any:
    """按生效配置对 ``CompactConfig`` 做 ``dataclasses.replace``。

    **在 kernel 侧调用**（不在 ``foundation.compact_config`` 内部）：
    foundation 是 L4、kernel 是 L3，L4→L3 是上行 import，会破层门禁。
    所以注入方向必须是 kernel 主动读 foundation，而不是 foundation
    反向依赖 kernel。

    未启用 RSI 或无可应用字段时原样返回同一个对象（零开销、零语义变化）。
    """
    cfg = resolve_harness(task_signature)
    overrides = dict(getattr(cfg, "compaction_config", {}) or {})
    if not overrides:
        return compact_config
    known = set(getattr(compact_config, "__dataclass_fields__", {}))
    applicable = {k: v for k, v in overrides.items() if k in known}
    if not applicable:
        return compact_config
    from dataclasses import replace

    logger.info(
        "harness compaction overrides applied for %r: %s",
        task_signature, sorted(applicable),
    )
    return replace(compact_config, **applicable)


__all__ = [
    "HarnessConfig",
    "apply_compaction_overrides",
    "apply_tool_set",
    "resolve_harness",
]
