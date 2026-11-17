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
    """装配期需要的字段（``apps.evolution.EffectiveHarnessConfig`` 满足它）。"""

    prompt_version: dict[str, Any]
    tool_set: frozenset[str] | None
    compaction_config: dict[str, Any]
    skill_set: frozenset[str] | None

    def is_empty(self) -> bool: ...


class _EmptyHarness:
    prompt_version: dict[str, Any] = {}
    tool_set: frozenset[str] | None = None
    compaction_config: dict[str, Any] = {}
    skill_set: frozenset[str] | None = None

    def is_empty(self) -> bool:
        return True


_EMPTY = _EmptyHarness()


def resolve_harness(task_signature: str) -> HarnessConfig:
    """Return the effective harness config for ``task_signature``.

    永不抛出：任何失败都降级为空配置（= 不启用 RSI 的默认行为）。
    这是刻意的——装配期抛错会让整个框架起不来，而 RSI 只是增强。

    **2026-11-05 批次 8**：``evolution`` 已从 apps/（L2）迁到
    kernel/evolution/（L3），与本模块同层。因此这里**直接 import**，
    不再经 ``CapabilityRegistry`` —— 走 registry 是为了跨层倒置依赖，
    同层直接用 import 更简单也更诚实（registry 那层间接会掩盖真实耦合）。
    """
    if not task_signature:
        return _EMPTY
    try:
        from agentnodes.kernel.evolution.skill_memory import resolve

        return resolve(task_signature)
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


def apply_skill_set(
    system_prompt: str | None, task_signature: str
) -> str | None:
    """把生效的 skill 文档附加到 system prompt 末尾（装配期，只加不改）。

    **为什么 skill 要进 prompt**：``templates/.skills/*.md`` 是方法论
    知识（IC/IR 阈值、六维评分、因子衰减等），agent 只有在 prompt 里
    看到它们才会按那套标准做事。此前这些文件**没有任何装配期消费通道**
    ——``skill`` 作为 Tier 1 组件存在于提案白名单里，但提案改了它之后
    运行时行为完全不变（等于白改）。

    **只追加**：原 prompt 逐字保留，skill 内容接在后面。没启用 RSI 或
    ``skill_set`` 为 None 时返回原值（``None`` 也原样返回）。

    **失败即降级**：读不到 skill 文件就返回原 prompt，不让装配崩。
    """
    if not task_signature:
        return system_prompt

    cfg = resolve_harness(task_signature)
    names = getattr(cfg, "skill_set", None)
    if not names:
        return system_prompt

    contents = _load_skills(sorted(names))
    if not contents:
        return system_prompt

    block = "\n\n".join(
        f"## 方法论：{name}\n\n{body}" for name, body in contents
    )
    if system_prompt:
        return f"{system_prompt}\n\n{block}"
    return block


def _load_skills(names: list[str]) -> list[tuple[str, str]]:
    """从 kernel 的 SkillRegistry 读技能正文。

    优先 workspace 侧的 ``.skills/``（用户覆盖），其次随包模板。
    找不到的名字跳过——一个失效的 skill 名不该让整次装配失败。
    """
    try:
        from pathlib import Path

        from agentnodes.kernel.skills import SkillRegistry

        registry = SkillRegistry()
        try:
            import agentnodes as _sr

            bundled = Path(_sr.__file__).parent / "templates" / ".skills"
            registry.load_directory(bundled)
        except Exception:  # noqa: BLE001 —— 模板缺失则什么都读不到
            logger.debug("bundled skills unavailable", exc_info=True)

        out: list[tuple[str, str]] = []
        for name in names:
            skill = registry.get(name)
            if skill is None or not skill.content:
                logger.debug("skill %r not found; skipped", name)
                continue
            out.append((name, skill.content))
        return out
    except Exception as exc:  # noqa: BLE001 —— 装配期不得因 skill 崩溃
        logger.debug("skill load failed for %r: %s", names, exc)
        return []


# ── 装配期统一入口 ───────────────────────────────────────────────────
#
# 三个装配点各自需要一个"装配期问一次"的入口。集中在这里，让调用方
# 只需一次调用即可完成 prompt / tool_set / compaction 三件事，
# 而不是各自去拆 resolve_harness 的返回值。


class AssembledHarness:
    """一次装配期解析的完整结果（四件产物）。"""

    __slots__ = (
        "task_signature", "prompt_override", "tool_set", "compact_config",
        "skill_set",
    )

    def __init__(
        self,
        task_signature: str,
        prompt_override: dict[str, Any],
        tool_set: frozenset[str] | None,
        compact_config: Any,
        skill_set: frozenset[str] | None = None,
    ) -> None:
        self.task_signature = task_signature
        self.prompt_override = prompt_override
        self.tool_set = tool_set
        self.compact_config = compact_config
        self.skill_set = skill_set

    def __repr__(self) -> str:
        return (
            f"AssembledHarness(sig={self.task_signature!r}, "
            f"prompt={'yes' if self.prompt_override else 'no'}, "
            f"tool_set={len(self.tool_set) if self.tool_set is not None else 'all'}, "
            f"skills={len(self.skill_set) if self.skill_set is not None else 'all'}, "
            f"compact={'overridden' if self.compact_config is not None else 'none'})"
        )


def assemble(
    *,
    role: str = "",
    compact_config: Any = None,
    task_signature: str = "",
) -> AssembledHarness:
    """装配期一次解析：prompt 覆盖 / tool_set / compaction 覆盖 / skill 集。

    这是四个装配点的**唯一**入口。``task_signature`` 为空（默认）时
    四项均为空/原样，调用方行为与改动前一致。

    :param role: 若给出，提取该 role 的 prompt 覆盖项
    :param compact_config: 若给出，按覆盖项做 ``replace``
    """
    if not task_signature:
        return AssembledHarness("", {}, None, compact_config, None)

    try:
        cfg = resolve_harness(task_signature)
        prompt_all = dict(getattr(cfg, "prompt_version", {}) or {})
        prompt_override = dict(prompt_all.get(role, {}) or {}) if role else {}
        tool_set = getattr(cfg, "tool_set", None)
        skill_set = getattr(cfg, "skill_set", None)
        compact = (
            apply_compaction_overrides(compact_config, task_signature)
            if compact_config is not None
            else None
        )
        return AssembledHarness(
            task_signature, prompt_override, tool_set, compact, skill_set,
        )
    except Exception as exc:  # noqa: BLE001 —— 装配期不得因 RSI 崩溃
        logger.warning("harness assemble failed for %r: %s", task_signature, exc)
        return AssembledHarness(task_signature, {}, None, compact_config, None)


__all__ = [
    "AssembledHarness",
    "HarnessConfig",
    "apply_compaction_overrides",
    "apply_skill_set",
    "apply_tool_set",
    "assemble",
    "resolve_harness",
]
