"""EvolutionAgent 的 5 个工具（施工方案 §4.3）。

**为什么工具是独立函数而不是 Agent 的方法**：``CapabilityRegistry`` 注册的
是"能力"，不是"对象状态"。把工具做成纯函数（显式收 source 参数），则：

- 可以在装配期被 register / resolve，符合阶段二的端口化契约
- 可以在测试里单独替换，不需要构造整个 EvolutionAgent
- 无法隐藏副作用：函数签名里只有输入和返回值，没有 self 可以偷偷写东西

**只读契约（H5/H3）**：除 ``propose_genome_update`` 外全部只读；
``propose_genome_update`` 只构造 ``GenomeProposal`` 对象返回，
**不写库、不生效**——落库与生效分别由调用方和 validation_gate 负责。
"""

from __future__ import annotations

from typing import Any

from .genome_proposals import GenomeProposal
from .holdout import HoldoutSet, compute_task_signature, sample


# ── 只读工具 ─────────────────────────────────────────────────────────


def read_journal(source: Any, *, limit: int = 50) -> list[dict[str, Any]]:
    """读研究账本（最近的条目）。只读。"""
    return list(source.read_journal(limit=limit))


def read_scoreboard(source: Any) -> dict[str, Any]:
    """读 Lever 统计。只读。"""
    return dict(source.read_scoreboard())


def read_validation(source: Any, *, limit: int = 50) -> list[dict[str, Any]]:
    """读历史验证结果。只读。"""
    return list(source.read_validation(limit=limit))


def read_holdout(
    source: Any,
    task_signature: str,
    seed: int,
    *,
    limit: int = 20,
) -> HoldoutSet:
    """取只读 holdout 集（H5）。

    接受两种 source，避免调用方为了"走工具"而改变数据源形态：

    - ``HoldoutSource``（有 ``read_holdout``）：直接委托——这是
      ``EvolutionAgent`` 持有并传入的形态
    - ``ReportSource``（有 ``iter_assignment_reports``）：走
      ``holdout.sample()`` 从事件流抽样

    ``sample`` 本身没有写方法；这里也不包装出任何写路径。
    """
    if hasattr(source, "read_holdout"):
        return source.read_holdout(task_signature, seed)
    return sample(task_signature, seed, source=source, limit=limit)


# ── 提案工具（唯一的"写"口，但只构造对象） ─────────────────────────


def propose_genome_update(
    target_component: str,
    change: dict[str, Any],
    *,
    evidence_refs: list[str] | None = None,
    now: str = "",
) -> GenomeProposal:
    """构造一份 draft 提案。**不落库、不生效。**

    Tier 0 目标会在 ``GenomeProposal.new()`` 里被拒（H3 第一层）。
    """
    return GenomeProposal.new(
        target_component,
        change,
        evidence_refs=evidence_refs,
        now=now,
    )


# ── 工具名 → 实现 ────────────────────────────────────────────────────

TOOL_IMPLS: dict[str, Any] = {
    "read_journal": read_journal,
    "read_scoreboard": read_scoreboard,
    "read_validation": read_validation,
    "read_holdout": read_holdout,
    "propose_genome_update": propose_genome_update,
}


def build_task_signature(objective: str, role: str, tool_set: set[str] | frozenset[str]) -> str:
    """便利包装（保持与 ``holdout.compute_task_signature`` 同一实现）。"""
    return compute_task_signature(objective, role, tool_set)


__all__ = [
    "TOOL_IMPLS",
    "build_task_signature",
    "propose_genome_update",
    "read_holdout",
    "read_journal",
    "read_scoreboard",
    "read_validation",
]
