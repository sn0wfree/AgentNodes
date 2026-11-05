"""把 golden eval 变成 RSI 可用的验证信号（施工方案 §4.5 的真实 replay）。

**为什么需要这一层**：``ValidationGate`` 的 ``replay`` 签名是
``(proposal, holdout) -> float``——它拿到的是 holdout 轨迹集。但真正的
能力评测要看 agent 在**固定任务集**上的表现，不是历史轨迹的状态统计。

于是这里做一件事：**把 golden task 集当成真实的 holdout**。
``HoldoutSet`` 的每条 ``Trajectory`` 携带任务标识（放在 ``payload`` 里），
replay 据此跑真实 LLM 评测，用 ``scorecard`` 打分。

**为什么不用 assignment.report 的状态权重**：那是"过去发生过什么"的
统计，与提案改动的因果距离太远。一个把 prompt 改坏了的提案，在历史
事件流上可能看起来完全一样（因为历史是改之前产生的）。golden eval
是唯一能回答"这个改动让 agent 变好还是变坏"的信号。

**抽样与成本**：默认每次验证跑 10 条（``DEFAULT_SAMPLE_SIZE``）。
- 固定 seed → 同一提案两次验证得到同一批任务（可比）
- 覆盖分层：按 toolset 分组后轮流取，避免全落在同一类任务上

**只读保证**：评测只跑任务、只读 workspace，不写业务表。
每个任务用独立的临时 workspace，避免互相污染。
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from agentnodes.apps.evolution.genome_proposals import GenomeProposal
from agentnodes.apps.evolution.holdout import HoldoutSet, Trajectory

logger = logging.getLogger(__name__)

#: 每次验证跑多少条 golden task。10 条在"信号够稳"与"成本可控"之间
#: 取的折中：单次验证约 10 次 LLM 调用，一次完整 RSI 周期（含重试）
#: 大约 20-30 次调用。
DEFAULT_SAMPLE_SIZE = 10

#: golden task 任务的 payload key（``Trajectory.payload`` 里）。
TASK_ID_KEY = "golden_task_id"
TASK_PROMPT_KEY = "golden_prompt"
TASK_TOOLSET_KEY = "golden_toolset"


# ── 任务选取 ─────────────────────────────────────────────────────────


def select_tasks(
    tasks: list[Any],
    *,
    seed: int,
    size: int = DEFAULT_SAMPLE_SIZE,
) -> list[Any]:
    """从 golden task 列表里确定性抽取 ``size`` 条。

    分层策略：先按 ``toolset`` 分组，再轮转取——保证抽样覆盖不同角色，
    而不是随机撞上一堆同类任务。同 ``(tasks, seed, size)`` 必得同一结果。
    """
    import random

    if size <= 0 or not tasks:
        return []

    # 稳定排序：输入顺序不该影响抽样结果
    ordered = sorted(tasks, key=lambda t: str(getattr(t, "id", "")))

    by_group: dict[str, list[Any]] = {}
    for t in ordered:
        group = str(getattr(t, "toolset", "") or "_none")
        by_group.setdefault(group, []).append(t)

    # 组内先洗牌（固定 seed），再按组名稳定顺序轮转
    rng = random.Random(seed)
    groups = sorted(by_group)
    for g in groups:
        rng.shuffle(by_group[g])

    picked: list[Any] = []
    idx = 0
    while len(picked) < size:
        added = False
        for g in groups:
            bucket = by_group[g]
            if idx < len(bucket):
                picked.append(bucket[idx])
                added = True
                if len(picked) >= size:
                    break
        if not added:
            break
        idx += 1
    return picked


# ── 评测执行 ─────────────────────────────────────────────────────────


@dataclass
class GoldenEvalResult:
    """一次 golden eval 的完整结果。"""

    metric: float
    tasks_run: int
    per_task: dict[str, float] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": round(self.metric, 6),
            "tasks_run": self.tasks_run,
            "per_task": {k: round(v, 4) for k, v in self.per_task.items()},
            "errors": list(self.errors),
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
        }


def run_golden_eval(
    tasks: list[Any],
    *,
    seed: int,
    size: int = DEFAULT_SAMPLE_SIZE,
    workspace: Path | None = None,
    max_iterations: int = 6,
    runner: Callable[[Any, Path, int], Any] | None = None,
) -> GoldenEvalResult:
    """跑一批 golden task，返回均分。

    :param tasks: ``evals.scorecard.GoldenTask`` 列表
    :param seed: 抽样种子（固定 → 可比）
    :param size: 抽样条数
    :param workspace: 工作区根；None = 每条任务用独立临时目录
    :param runner: 单任务执行器 ``(task, workspace, max_iter) -> RunRecord``；
        None = 用 ``evals.run_evals_llm._run_one``
    :param max_iterations: 单任务最大迭代

    **永不抛出**：评测失败是"无法判断"，返回 ``skipped=True``，
    由 ``ValidationGate`` 的 escalate 路径处理——而不是当成"提案变差"
    记入回滚计数。混杂这两者会让一次网络故障回滚掉健康基因组。
    """
    try:
        from evals.scorecard import score_run
    except ImportError as exc:
        # ``evals`` 是仓库根下的独立包（不是 agentnodes 的子包）。
        # 安装形态下它可能不在 sys.path —— 此时评测降级为"无法验证"，
        # 而不是让 import 崩溃带走整个 RSI 闭环。
        logger.warning("evals.scorecard unavailable: %s", exc)
        return GoldenEvalResult(
            metric=0.0, tasks_run=0, skipped=True,
            skip_reason=f"evals package unavailable: {exc}",
        )

    if runner is None:
        try:
            from evals.run_evals_llm import _run_one

            # ``_run_one`` 的签名是 ``(task, *, workspace, max_iterations)``
            # ——关键字限定。包一层统一成位置参数形式，方便注入替身测试。
            def runner(task: Any, ws: Path, max_iter: int) -> Any:  # noqa: F811
                return _run_one(task, workspace=ws, max_iterations=max_iter)
        except Exception as exc:  # noqa: BLE001
            logger.warning("golden eval runner unavailable: %s", exc)
            return GoldenEvalResult(
                metric=0.0, tasks_run=0, skipped=True,
                skip_reason=f"runner unavailable: {exc}",
            )

    picked = select_tasks(tasks, seed=seed, size=size)
    if not picked:
        return GoldenEvalResult(
            metric=0.0, tasks_run=0, skipped=True, skip_reason="no tasks selected",
        )

    per_task: dict[str, float] = {}
    errors: list[str] = []
    for task in picked:
        tid = str(getattr(task, "id", ""))
        # 每条任务独立 workspace：避免上一条写下的文件影响下一条
        use_ws = workspace
        tmp_ctx = None
        if use_ws is None:
            tmp_ctx = tempfile.TemporaryDirectory(prefix=f"golden_{tid}_")
            use_ws = Path(tmp_ctx.name)
        try:
            record = runner(task, use_ws, max_iterations)
            score = score_run(task, record)
            per_task[tid] = float(score.total)
            if getattr(record, "error", None):
                errors.append(f"{tid}: {record.error}")
        except Exception as exc:  # noqa: BLE001 —— 单任务失败不废整轮
            logger.warning("golden task %s failed: %s", tid, exc)
            per_task[tid] = 0.0
            errors.append(f"{tid}: {type(exc).__name__}: {exc}")
        finally:
            if tmp_ctx is not None:
                try:
                    tmp_ctx.cleanup()
                except Exception:  # noqa: BLE001
                    pass

    if not per_task:
        return GoldenEvalResult(
            metric=0.0, tasks_run=0, skipped=True, skip_reason="all tasks errored",
            errors=errors,
        )

    # 全部任务都异常 = 评测环境坏了，不是提案变差。必须 skipped：
    # 若返回 metric=0.0 且 skipped=False，验证闸会把它当成"退化 100%"，
    # 累积三次就把一个没问题的基因组回滚掉——一次网络故障足以废掉
    # 全部进化成果。（与 validation_gate 里 SlotUnavailable 不计回滚
    # 计数是同一条原则。）
    if errors and len(errors) == len(per_task):
        return GoldenEvalResult(
            metric=0.0, tasks_run=len(per_task), per_task=per_task,
            errors=errors, skipped=True,
            skip_reason="all tasks errored (infrastructure failure)",
        )

    metric = sum(per_task.values()) / len(per_task)
    return GoldenEvalResult(
        metric=metric, tasks_run=len(per_task), per_task=per_task, errors=errors,
    )


# ── 与 ValidationGate 的接口 ─────────────────────────────────────────


def make_golden_replay(
    tasks: list[Any],
    *,
    seed: int = 0,
    size: int = DEFAULT_SAMPLE_SIZE,
    workspace: Path | None = None,
    max_iterations: int = 6,
    runner: Callable[[Any, Path, int], Any] | None = None,
    apply_proposal: Callable[[GenomeProposal], Any] | None = None,
) -> Callable[[GenomeProposal, HoldoutSet], float]:
    """构造可直接作为 ``ValidationGate.replay`` 的可调用对象。

    :param apply_proposal: **关键**——评测前如何把提案应用到执行环境。
        签名 ``(proposal) -> undo_callable | None``；返回值若可调用，
        评测结束后调用它复原。

    **为什么必须有 apply_proposal**：不应用提案的话，候选分与基线分
    完全一样（跑的是同一份配置），验证闸会算出 0% 退化、判为"通过"，
    然后激活一个**从未被真正验证过**的提案。这是整条 RSI 链最容易
    出错的地方：看起来在验证，实际在自证。

    调用方（``rsi_wiring``）的实现是「临时升版到提案 → 跑分 → 回滚」。
    """
    def _replay(proposal: GenomeProposal, holdout: HoldoutSet) -> float:
        undo: Callable[[], Any] | None = None
        if apply_proposal is not None:
            undo = apply_proposal(proposal)
        try:
            result = run_golden_eval(
                tasks, seed=seed, size=size, workspace=workspace,
                max_iterations=max_iterations, runner=runner,
            )
            if result.skipped:
                # 无法验证 → 抛出让 ValidationGate 走 escalate 路径
                # （不计回滚计数：这是基础设施问题，不是模型退化）
                from agentnodes.apps.evolution.isolated_replay import (
                    SlotUnavailable,
                )

                raise SlotUnavailable(f"golden eval skipped: {result.skip_reason}")
            logger.info(
                "golden eval: metric=%.4f over %d tasks (proposal=%s)",
                result.metric, result.tasks_run,
                getattr(proposal, "proposal_id", "?"),
            )
            return result.metric
        finally:
            if undo is not None:
                try:
                    undo()
                except Exception:  # noqa: BLE001 —— 复原失败不得吞掉评测结果
                    logger.warning("proposal undo failed", exc_info=True)

    return _replay


__all__ = [
    "DEFAULT_SAMPLE_SIZE",
    "GoldenEvalResult",
    "make_golden_replay",
    "run_golden_eval",
    "select_tasks",
]
