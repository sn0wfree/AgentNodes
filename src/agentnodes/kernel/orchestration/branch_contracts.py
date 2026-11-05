"""契约独立定形（决策 8）：RoundOutcome / BranchContext 的中立协议。

依赖方向约定：上层（A2 控制器 / C2 Director / 回放工具）只允许 import
本模块的结构化协议，**不允许 import core/study/phase_engine**——
引擎内部字段（runner/study/paths）不进入跨层契约。

phase_engine 的两个 dataclass 在**结构上满足**这里的 Protocol
（同名同型字段即可，无需继承——zero 改动引擎代码）。
assert_* 助手用于边界处 fail-loud 校验（形状漂移立刻暴露）。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RoundOutcomeLike(Protocol):
    """引擎分支产出契约（9 字段，phase_engine.RoundOutcome 同构）。"""

    metrics: dict
    verdict: str
    eval_result: dict
    agent_outputs: dict
    strategist_output: dict
    hypothesis: str
    predicted_affected: list
    captured_predictions: dict
    backtest_error: str | None


@runtime_checkable
class BranchContextLike(Protocol):
    """分支输入契约的**稳定跨层子集**。

    phase_engine.BranchContext 还有引擎内部字段（runner/paths/graph…），
    那些不进跨层契约；这里只钉住上层可能读取的 7 个稳定字段。
    """

    round_num: int
    previous_summary: dict | None
    directive_text: str | None
    strategy: str
    session: str
    sid: str
    metric_targets: list


ROUND_OUTCOME_REQUIRED: tuple[str, ...] = (
    "metrics", "verdict", "eval_result", "agent_outputs",
    "strategist_output", "hypothesis", "predicted_affected",
    "captured_predictions", "backtest_error",
)

BRANCH_CONTEXT_STABLE: tuple[str, ...] = (
    "round_num", "previous_summary", "directive_text",
    "strategy", "session", "sid", "metric_targets",
)


def assert_round_outcome_shape(obj: Any) -> None:
    """fail-loud：缺字段/类型漂移立刻暴露（沿用 261a88f 修复的立场）。"""
    missing = [name for name in ROUND_OUTCOME_REQUIRED if not hasattr(obj, name)]
    if missing:
        raise TypeError(f"RoundOutcome 形状漂移：缺少 {missing}")
    if not isinstance(obj.verdict, str):
        raise TypeError(f"RoundOutcome.verdict 需要 str，得到 {type(obj.verdict)}")
    if not isinstance(obj.metrics, dict):
        raise TypeError(f"RoundOutcome.metrics 需要 dict，得到 {type(obj.metrics)}")


def assert_branch_context_shape(obj: Any) -> None:
    """fail-loud：稳定子集字段校验（引擎内部字段不检查）。"""
    missing = [name for name in BRANCH_CONTEXT_STABLE if not hasattr(obj, name)]
    if missing:
        raise TypeError(f"BranchContext 形状漂移：缺少 {missing}")
    if not isinstance(obj.round_num, int):
        raise TypeError(f"BranchContext.round_num 需要 int，得到 {type(obj.round_num)}")
