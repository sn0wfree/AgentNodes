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
#
# **2026-11-06（W4 批次 2）为什么改口径**：旧实现把 objective 原文
# （strip + lower）直接喂进哈希。后果是每条用户输入都得到一个独一无二的
# 签名，而 ``assignment.report`` 过去根本不带签名 —— 于是 holdout 抽样
# 永远匹配不到任何历史轨迹，"RSI 闭环"在生产上恒为 ``empty_holdout``。
# 即使把签名写进 report，逐字哈希也会让下一条输入继续对不上。
#
# 评审（簇二 / 附录 C）要求**确定性降维**：签名 = role + 排序后的 toolset
# + 少量离散意图桶。objective 只参与"分桶"，不参与哈希原文。桶表是模块
# 常量的封闭集合，无 LLM、无随机、无时间 —— 同输入必得同输出，跨进程
# 跨版本稳定。
#
# 版本前缀 ``v2|`` 让新旧签名天然分离：旧事件里的 16 位旧签名不会被新
# 查询误采（宁可"抽不到"也不要把语义不同的样本混进验证集）。

#: 签名口径版本。改分桶规则或字段集时必须递增——否则同一签名会同时
#: 代表两种任务集合，holdout 的"同一任务"前提被破坏。
TASK_SIGNATURE_VERSION = "v2"

#: 意图桶（封闭集合，顺序固定 → 遍历结果确定）。
OTHER_INTENT = "other"
KNOWN_INTENTS: tuple[str, ...] = (
    "research",
    "implement",
    "evaluate",
    "operate",
    OTHER_INTENT,
)

#: 意图 → 关键词表（子串匹配，大小写不敏感）。
#:
#: **为什么是关键词而不是分类器**：评审明确要求确定性、无 LLM。关键词表
#: 是可审计的——"为什么这条落到 research 桶"能逐字复核。分类器做不到。
#: 词表可以随时扩充（对应框架 §0.5「退役/阈值可调」的同类精神：口径是
#: 显式常量，不是散落的魔法值）。
INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "research": (
        "研究", "调研", "分析", "报告", "综述",
        "research", "investigate", "survey", "analyz", "analys", "report",
    ),
    "implement": (
        "实现", "开发", "编写", "修复", "重构", "编码",
        "implement", "build", "fix", "code", "refactor", "write",
    ),
    "evaluate": (
        "评估", "评测", "回测", "验证", "测试",
        "evaluate", "benchmark", "backtest", "validate", "test",
    ),
    "operate": (
        "运行", "执行", "部署", "启动", "跑一下",
        "run", "execute", "deploy", "launch", "start",
    ),
}

#: 每个意图桶的代表文本——从签名反查意图后，用它重建 objective，
#: 让 ``EvolutionAgent.propose()`` 内部重算出的签名与抽样签名一致。
#: 必须是本桶关键词表里的词（自检见 ``_intent_probes_sane``）。
INTENT_PROBES: dict[str, str] = {
    "research": "research",
    "implement": "implement",
    "evaluate": "evaluate",
    "operate": "run",
    OTHER_INTENT: "misc",
}


def classify_intent(objective: str) -> str:
    """把 objective 归到 ``KNOWN_INTENTS`` 里的一个桶（确定性）。

    匹配顺序 = ``KNOWN_INTENTS`` 顺序：先命中先返回。多个桶同时命中时
    由顺序裁决——顺序是模块常量，不是 dict 迭代顺序，故稳定。
    """
    text = (objective or "").strip().lower()
    if text:
        for intent in KNOWN_INTENTS:
            if intent == OTHER_INTENT:
                continue
            for keyword in INTENT_KEYWORDS.get(intent, ()):
                if keyword in text:
                    return intent
    return OTHER_INTENT


def signature_for_intent(
    intent: str, role: str, tool_set: Iterable[str]
) -> str:
    """按 (版本, role, toolset, intent) 计算 16 位签名。

    role / toolset 也做归一化（strip + lower + 排序去空），因为"同一个
    工具集的不同书写方式"不该产生两个签名。
    """
    bucket = intent if intent in KNOWN_INTENTS else OTHER_INTENT
    normalized_role = (role or "").strip().lower()
    tools = ",".join(
        sorted({str(t).strip().lower() for t in tool_set if str(t).strip()})
    )
    h = hashlib.sha256()
    h.update(f"{TASK_SIGNATURE_VERSION}|".encode("utf-8"))
    h.update(f"{normalized_role}|".encode("utf-8"))
    h.update(f"{bucket}|".encode("utf-8"))
    h.update(tools.encode("utf-8"))
    return h.hexdigest()[:16]


def intent_for_signature(
    task_signature: str, *, role: str = "", tool_set: Iterable[str] = ()
) -> str | None:
    """反查签名对应的意图桶；不属于该 (role, toolset) 空间时返回 None。

    用途：RSI 驱动只能从事件流里拿到签名（report 不带 objective），但要
    调 ``EvolutionAgent.propose()`` 又必须给出能重算出同一签名的
    objective。桶表是封闭的 → 有限枚举即可精确反查，无需存原文。
    """
    tools = tuple(tool_set)
    for intent in KNOWN_INTENTS:
        if signature_for_intent(intent, role, tools) == task_signature:
            return intent
    return None


def objective_for_intent(intent: str) -> str:
    """意图桶的代表 objective（用于重算签名，不用于展示）。"""
    return INTENT_PROBES.get(intent, INTENT_PROBES[OTHER_INTENT])


def compute_task_signature(
    objective: str, role: str, tool_set: Iterable[str]
) -> str:
    """稳定签名（附录 C，**确定性降维**口径）。

    = ``sha256(v2 | role | intent(objective) | sorted(tool_set))[:16]``

    同一 ``(意图桶, role, tool_set)`` 多次调用结果一致；objective 的措辞、
    长度、语序不影响签名，只有它命中的意图桶影响。这是让 holdout 能真正
    抽到历史轨迹的前提（见本段上方长注释）。
    """
    return signature_for_intent(
        classify_intent(objective), role, tool_set
    )


# ── EventStore 适配器 ────────────────────────────────────────────────


class EventStoreReportSource:
    """把 ``EventStore`` / ``PlatformStore`` 适配成 ``ReportSource``（只读）。

    ``replay`` 需要 session_id。若调用方没给 ``session_ids``，构造时
    自动从 store 枚举（``iter_session_ids``）——**不能**依赖某个已知
    session：RSI 要的是全量历史轨迹，不是当前会话那一条。
    """

    def __init__(self, event_store: Any, session_ids: Iterable[str] = ()) -> None:
        self._store = event_store
        ids = list(session_ids)
        if not ids:
            # 自动枚举：PlatformStore 提供 iter_session_ids；没有该方法的
            # 替身（测试）就保持空集，调用方显式传。
            iter_ids = getattr(event_store, "iter_session_ids", None)
            if callable(iter_ids):
                try:
                    ids = [sid for sid in iter_ids() if sid]
                except Exception:  # noqa: BLE001 —— 枚举失败退化为空
                    ids = []
        self._sessions = ids

    def iter_assignment_reports(self) -> Iterable[dict[str, Any]]:
        for sid in self._sessions:
            # PlatformStore 用 ``kinds=``；EventStore 用 ``types=``。
            # 统一走 kinds 并回退，避免适配器绑定单一实现。
            try:
                rows = self._store.replay(sid, kinds=["assignment.report"])
            except TypeError:
                rows = self._store.replay(sid, types=["assignment.report"])
            for ev in rows:
                data = dict(ev.data or {})
                data.setdefault("id", getattr(ev, "event_id", None) or getattr(ev, "seq", ""))
                # HoldoutSet 的防污染比较用 ts（ISO8601）；事件自带 time_created。
                if "ts" not in data:
                    ts = (
                        getattr(ev, "ts", None)
                        or getattr(ev, "time_created", None)
                        or getattr(ev, "created_at", None)
                    )
                    if ts is not None:
                        data["ts"] = str(ts)
                yield data

    # ── EvolutionAgent 的另外三个只读源 ──────────────────────────
    #
    # EvolutionAgent 需要 journal / scoreboard / validation 三个源，形态
    # 与 ReportSource 不同。它们都可以从同一份事件流派生——不引入第二个
    # 数据通道，因为"一切事实经事件流"（H6）。缺哪个事件就返回空，
    # 调用方（policy）自己决定空证据意味着什么。

    def read_journal(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """最近的研究账本条目（来自 ``run.*`` / ``study.*`` 事件）。"""
        kinds = ("run.answer", "run.settled", "run.failed", "study.round_done")
        out: list[dict[str, Any]] = []
        for sid in self._sessions:
            for ev in self._iter_any(sid, kinds):
                out.append({
                    "kind": getattr(ev, "kind", ""),
                    "run_id": getattr(ev, "run_id", ""),
                    "data": dict(getattr(ev, "data", None) or {}),
                })
        return out[-limit:] if limit else out

    def read_scoreboard(self) -> dict[str, Any]:
        """按 status 统计 ``assignment.report``——Lever 统计的事件流版本。"""
        counts: dict[str, int] = {}
        for r in self.iter_assignment_reports():
            status = str(r.get("status") or "")
            if status:
                counts[status] = counts.get(status, 0) + 1
        total = sum(counts.values())
        return {
            "counts": counts,
            "total": total,
            "ready_rate": (counts.get("ready", 0) / total) if total else 0.0,
        }

    def read_validation(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """历史验证结果（``genome.*`` 事件）。"""
        kinds = ("genome.rollback", "genome.activated", "rsi.validated")
        out: list[dict[str, Any]] = []
        for sid in self._sessions:
            for ev in self._iter_any(sid, kinds):
                out.append(dict(getattr(ev, "data", None) or {}))
        return out[-limit:] if limit else out

    def _iter_any(self, session_id: str, kinds: tuple[str, ...]) -> Iterable[Any]:
        """按多种事件类型重放（两种 store API 都兼容）。"""
        try:
            return self._store.replay(session_id, kinds=list(kinds))
        except TypeError:
            return self._store.replay(session_id, types=list(kinds))


class StaticGenomeVersion:
    """固定 ``activated_at`` 的基因组版本（测试/简化路径）。"""

    def __init__(self, activated_at: str) -> None:
        self._activated_at = activated_at

    def activated_at(self) -> str:
        return self._activated_at


# ── 持久化（施工方案 §4.2，走 ⑥ 存储原语） ──────────────────────────
#
# ``foundation.storage.holdout.HoldoutStore`` 只做纯 dict 的读写——它是 L4，
# 不能 import L2 的 ``HoldoutSet``。类型还原（dict → HoldoutSet）由本模块
# 提供 decoder，方向正确：apps 拉 foundation，而非反过来。


def decode_holdout(raw: dict[str, Any]) -> HoldoutSet:
    """把 ``HoldoutStore.load_raw()`` 的 dict 还原成 ``HoldoutSet``。"""
    trajs = tuple(
        Trajectory(
            event_id=str(d.get("event_id", "")),
            assignment_id=str(d.get("assignment_id", "")),
            ticket_id=str(d.get("ticket_id", "")),
            run_id=str(d.get("run_id", "")),
            status=str(d.get("status", "")),
            ts=str(d.get("ts", "")),
            task_signature=str(d.get("task_signature", "")),
            payload=dict(d.get("payload") or {}),
        )
        for d in raw.get("trajectories", [])
    )
    return HoldoutSet(
        trajectories=trajs,
        seed=int(raw.get("seed", 0)),
        task_signature=str(raw.get("task_signature", "")),
    )


def persist_holdout(
    holdout: HoldoutSet,
    workspace: str | Any,
    *,
    channel: Any | None = None,
    overwrite: bool = False,
    session_id: str = "",
) -> Any:
    """把 holdout 集落盘（施工方案 §4.2「走 ⑥ 存储原语」）。

    返回 ``HoldoutPointer``；同一 ``(task_signature, seed)`` 已存在时抛
    ``HoldoutAlreadyPersisted``（write-once，H5 的持久化侧保证）。
    """
    from agentnodes.foundation.storage.holdout import HoldoutStore

    return HoldoutStore(workspace, channel=channel).persist(
        holdout, overwrite=overwrite, session_id=session_id
    )


def load_persisted_holdout(
    workspace: str | Any, task_signature: str, seed: int
) -> HoldoutSet:
    """从磁盘读回 holdout 集（带类型还原）。"""
    from agentnodes.foundation.storage.holdout import HoldoutStore

    raw = HoldoutStore(workspace).load_raw(task_signature, seed)
    return decode_holdout(raw)


__all__ = [
    "EventStoreReportSource",
    "HOLDOUT_STATUSES",
    "HoldoutSet",
    "GenomeVersionSource",
    "INTENT_KEYWORDS",
    "INTENT_PROBES",
    "KNOWN_INTENTS",
    "OTHER_INTENT",
    "ReportSource",
    "StaticGenomeVersion",
    "TASK_SIGNATURE_VERSION",
    "Trajectory",
    "classify_intent",
    "compute_task_signature",
    "decode_holdout",
    "intent_for_signature",
    "load_persisted_holdout",
    "objective_for_intent",
    "persist_holdout",
    "sample",
    "signature_for_intent",
]
