"""Holdout — 只读的验证轨迹集（施工方案 §4.2 / 附录 C）。

**H5 是这里的第一原则**：本模块只暴露 ``sample()``，没有
``append / update / delete``。写保护不是洁癖——它是 RSI 防奖励黑客的
最后一道闸：如果 EvolutionAgent 能往 holdout 里塞自己挑的样本，
"验证通过"就失去了全部意义。

**确定性**：同一 ``(task_signature, seed)`` 必须产出同一集合。
实现上依赖 ``random.Random(seed).shuffle`` 对**已排序**的候选列表操作——
不排序的话，候选顺序取决于 event_log 的返回顺序，跨进程/跨版本会漂。

**防污染**：排除当前 ``genome_version.activated_at`` 之后的轨迹。
那些轨迹是"当前基因组自己产生的"，拿它们验证自己等于自证。
"""

from __future__ import annotations

import hashlib
import logging
import random
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

logger = logging.getLogger(__name__)

#: 可进入 holdout 的 report 状态（施工方案 §4.2 第 1 步）。
HOLDOUT_STATUSES: frozenset[str] = frozenset({"failed", "dropped", "ready"})


# ── 轨迹 ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Trajectory:
    """一条可回放的轨迹（来自 ``assignment.report`` 事件）。"""

    event_id: str
    assignment_id: str
    ticket_id: str
    run_id: str
    status: str
    ts: str
    task_signature: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HoldoutSet:
    """不可变的 holdout 集。构造后无法修改（H5 的实现层保证）。"""

    trajectories: tuple[Trajectory, ...]
    seed: int
    task_signature: str

    def __iter__(self):
        return iter(self.trajectories)

    def __len__(self) -> int:
        return len(self.trajectories)

    def __getitem__(self, idx):
        return self.trajectories[idx]


# ── 数据源协议 ───────────────────────────────────────────────────────


class ReportSource(Protocol):
    """只读的事件源。实现方可以是 EventStore 适配器或测试替身。"""

    def iter_assignment_reports(self) -> Iterable[dict[str, Any]]:
        """Yield ``assignment.report`` 事件的 data dict。"""
        ...


class GenomeVersionSource(Protocol):
    """只读的当前基因组版本。"""

    def activated_at(self) -> str:
        """当前 genome_version 的生效时间（ISO8601）。"""
        ...


# ── 抽样 ─────────────────────────────────────────────────────────────


def sample(
    task_signature: str,
    seed: int,
    *,
    source: ReportSource,
    genome: GenomeVersionSource | None = None,
    limit: int = 20,
) -> HoldoutSet:
    """从只读事件流抽样，返回不可变 ``HoldoutSet``。

    :param task_signature: 任务签名（附录 C 的 ``compute_task_signature``）
    :param seed: **必须显式传**（附录 C：不允许默认值，避免隐式不确定性）
    :param source: 只读事件源
    :param genome: 当前基因组版本；None = 不做生效周期过滤（测试路径）
    :param limit: 最多返回条数
    """
    if seed is None:
        raise ValueError("seed is required (no default — see appendix C)")
    if limit < 0:
        raise ValueError("limit must be >= 0")

    activated_at: str | None = None
    if genome is not None:
        activated_at = genome.activated_at()

    candidates: list[Trajectory] = []
    for raw in source.iter_assignment_reports():
        status = raw.get("status")
        if status not in HOLDOUT_STATUSES:
            continue
        if raw.get("task_signature") != task_signature:
            continue
        ts = str(raw.get("ts") or "")
        # 防污染：只接受当前基因组生效**之前**的轨迹
        if activated_at is not None and ts and ts >= activated_at:
            continue
        candidates.append(
            Trajectory(
                event_id=str(raw.get("id") or raw.get("event_id") or ""),
                assignment_id=str(raw.get("assignment_id") or ""),
                ticket_id=str(raw.get("ticket_id") or ""),
                run_id=str(raw.get("run_id") or ""),
                status=str(status),
                ts=ts,
                task_signature=str(raw.get("task_signature") or ""),
                payload=dict(raw.get("payload") or {}),
            )
        )

    # 确定性：先按稳定 key 排序，再洗牌。否则候选顺序取决于事件源的
    # 返回顺序，跨进程会漂，"同 seed 同结果"的承诺就破了。
    candidates.sort(key=lambda t: (t.ts, t.assignment_id, t.event_id))
    rng = random.Random(seed)
    rng.shuffle(candidates)

    return HoldoutSet(
        trajectories=tuple(candidates[:limit]),
        seed=seed,
        task_signature=task_signature,
    )


# ── task_signature ───────────────────────────────────────────────────


def compute_task_signature(
    objective: str, role: str, tool_set: Iterable[str]
) -> str:
    """稳定哈希：objective 归一化 + role + 排序后的工具集（附录 C）。

    同一 ``(objective, role, tool_set)`` 多次调用结果一致。
    """
    h = hashlib.sha256()
    h.update(objective.strip().lower().encode("utf-8"))
    h.update(role.encode("utf-8"))
    h.update(",".join(sorted(tool_set)).encode("utf-8"))
    return h.hexdigest()[:16]


# ── EventStore 适配器 ────────────────────────────────────────────────


class EventStoreReportSource:
    """把 ``EventStore`` 适配成 ``ReportSource``（只读）。

    ``replay`` 需要 session_id；这里用通配 session 的读法——若 EventStore
    未提供跨 session 扫描，则退化为按已知 session 列表逐个 replay。
    """

    def __init__(self, event_store: Any, session_ids: Iterable[str] = ()) -> None:
        self._store = event_store
        self._sessions = list(session_ids)

    def iter_assignment_reports(self) -> Iterable[dict[str, Any]]:
        for sid in self._sessions:
            for ev in self._store.replay(sid, types=["assignment.report"]):
                data = dict(ev.data or {})
                data.setdefault("id", getattr(ev, "id", ""))
                yield data


class StaticGenomeVersion:
    """固定 ``activated_at`` 的基因组版本（测试/简化路径）。"""

    def __init__(self, activated_at: str) -> None:
        self._activated_at = activated_at

    def activated_at(self) -> str:
        return self._activated_at


__all__ = [
    "EventStoreReportSource",
    "HOLDOUT_STATUSES",
    "HoldoutSet",
    "GenomeVersionSource",
    "ReportSource",
    "StaticGenomeVersion",
    "Trajectory",
    "compute_task_signature",
    "sample",
]
