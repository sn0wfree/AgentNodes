"""ReActSkeleton — 配置驱动的循环骨架（批次 11 · B3 阶段 1）。

状态机（llmwikify UnifiedAgentLoop 的 AgentNodes 形态）::

    PRECHECK → [REASON → DECIDE① → ACT → DECIDE② → OBSERVE] → FINALIZE

与 ``AgentLoop._run_loop_core`` 的关系：**影子引擎**。默认路径
（legacy）不经过本模块任何一行；``engine="skeleton"`` 或
``AGENTNODES_SKELETON=1`` 显式启用。等价性由
``tests/test_skeleton_equivalence.py`` 的双引擎对照测试钉住。

## 消费协议（全部沿用既有对象，无新抽象）

- **state**：鸭子类型——``iteration`` / ``messages`` / ``stop_reason``
  属性即可（AgentNodes 用 ``LoopContext``，llmwikify 用 ``UnifiedContext``，
  骨架不关心）。handlers 负责按需变异。
- **deciders**（批次 10）：三个闸位 ``precheck`` / ``after_reason`` /
  ``after_act``，各为 ``list[Decider]``，按序判定，首个非 ``PROCEED``
  按 ``Decision`` 分派（CONTINUE→下一轮 / STOP→finalize / EXIT→return）
- **handlers**：``StepHandler``——REASON/ACT 位（绑定 AgentLoop 即复用
  其 strategy steps，``bind_agent_loop`` 既有协议）
- **hooks**：``before_run / before_iteration / after_iteration /
  after_run / on_error``（duck-typed，全部可缺省）

## 与 legacy 的已知差异（阶段 1 有意收窄）

- **async-only**：``run_stream`` 是 async 迭代器；sync 桥在阶段 2
- **no-progress 交互**：触发 EXIT；``_check_no_progress`` 的用户询问
  收编在阶段 2（handlers 可注入覆盖）
- **steering inbox / injectors**：REASON handler 内部处理（绑定 loop
  即复用），骨架本体不感知
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Callable

from .deciders import Decision
from ._core_types import StepResult

logger = logging.getLogger(__name__)


# ── Run spec ─────────────────────────────────────────────────────────


@dataclass
class SkeletonRunSpec:
    """一次骨架运行的输入（B3 阶段 2 由 LoopSpec 取代/收拢）。"""

    task: str
    context: str | None = None
    history: list[dict[str, Any]] | None = None
    max_iterations: int = 8
    session_id: str = ""
    #: no-progress 用户批准续跑（批次 10 的 approved_continuation）
    progress_approved: bool = False


@dataclass
class SkeletonState:
    """骨架内部最小状态（**鸭子协议**——adapters 可换成 LoopContext）。

    骨架只读写字段：``iteration / stop_reason / error / t0``。
    ``messages`` / ``response`` 由 handlers 管理（骨架不触碰）。
    """

    iteration: int = 0
    t0: float = 0.0
    stop_reason: str | None = None
    error: str | None = None
    #: handlers 的自由挂载点（message 列表、response 等）
    extra: dict[str, Any] = field(default_factory=dict)


# ── 骨架 ─────────────────────────────────────────────────────────────


class ReActSkeleton:
    """配置驱动的循环骨架——**只做控制流，不做策略**。

    REASON/ACT 是注入的 handler；停止判定是注入的 deciders；
    生命周期事件是注入的 hooks。骨架不知道预算怎么算、工具怎么执行、
    goal 怎么注入——那些全在 handler/decider/hook 里。
    """

    def __init__(
        self,
        *,
        reasoner: Any,
        actor: Any,
        deciders: dict[str, list[Any]],
        hooks: Any | None = None,
        finalize: Callable[[Any, Any], Any] | None = None,
        prepare: Callable[[SkeletonRunSpec], tuple[Any, Any]] | None = None,
        on_iteration_start: Callable[[Any, int], None] | None = None,
    ) -> None:
        """
        :param reasoner: ``StepHandler``——REASON 位（compaction + LLM 调用）；
            ``handle(spec, state)``，产出 = response（None = 终止信号）
        :param actor: ``StepHandler``——ACT 位（工具执行）；
            ``handle(response, state)``
        :param deciders: ``{闸位: [Decider]}``，闸位 = precheck /
            after_reason / after_act；按序判定，首个非 PROCEED 生效
        :param hooks: ``before_run/before_iteration/after_iteration/
            after_run/on_error``（duck-typed，全部可缺省）
        :param finalize: 终局处理 ``(state, result)``——metrics/git/trace
        :param prepare: 运行准备，收 spec，返回 ``(state, result)``；
            None = 骨架自建空 state + SimpleResult
        :param on_iteration_start: 每轮开头动作（wrap-up nudge 等）
        """
        self._reasoner = reasoner
        self._actor = actor
        self._deciders = deciders
        self._hooks = hooks
        self._finalize = finalize
        self._prepare = prepare
        self._on_iteration_start = on_iteration_start
        self._last_result: Any = None

    # ── 闸位判定 ─────────────────────────────────────────────────────

    async def _run_gate(
        self,
        gate: str,
        state: Any,
        payload: Any = None,
    ) -> tuple[Decision, str | None]:
        """按序跑一个闸位的 deciders，首个非 PROCEED 生效。"""
        for decider in self._deciders.get(gate, ()):
            r = decider.handle(payload, state)
            if not isinstance(r, StepResult):
                r = await r
            decision, reason = r.output
            if decision is not Decision.PROCEED:
                logger.debug("gate %s: %s (%s)", gate, decision, reason)
                return decision, reason
        return Decision.PROCEED, None

    # ── hooks 转发 ───────────────────────────────────────────────────

    async def _fire(self, name: str, *args: Any) -> None:
        fn = getattr(self._hooks, name, None) if self._hooks else None
        if fn is not None:
            await fn(*args)

    # ── 状态机 ───────────────────────────────────────────────────────

    async def run_stream(
        self, spec: SkeletonRunSpec
    ) -> AsyncIterator[dict[str, Any]]:
        state, result = self._prepare_state(spec)
        await self._fire("before_run", spec)

        try:
            for iteration in range(1, max(1, spec.max_iterations) + 1):
                state.iteration = iteration
                result.iterations = iteration
                if self._on_iteration_start is not None:
                    self._on_iteration_start(state, iteration)
                await self._fire("before_iteration", state, iteration)

                # ── PRECHECK（上限/预算/熔断等进入 ACT 前的闸）──
                decision, reason = await self._run_gate("precheck", state)
                if decision is Decision.STOP:
                    state.stop_reason = reason or "precheck"
                    result.stop_reason = state.stop_reason
                    break
                if decision is Decision.EXIT:
                    result.finished_reason = "exit"
                    result.stop_reason = reason
                    self._last_result = result  # run_to_completion 需要
                    await self._fire("after_run", state, result)
                    return

                # ── REASON（compaction + inbox + LLM 调用）──
                r = await self._maybe_handle(self._reasoner, spec, state)
                if not r.success:
                    result.error = r.error
                    state.stop_reason = "error"
                    await self._fire("on_error", state, r.error)
                    yield {"type": "error", "message": r.error}
                    break
                response = r.output

                # ── DECIDE① after_reason ──
                decision, reason = await self._run_gate(
                    "after_reason", state, payload=response
                )
                if decision is Decision.STOP:
                    state.stop_reason = reason or "final"
                    result.stop_reason = state.stop_reason
                    # LoopResult 用 answer 而非 final_content
                    if hasattr(result, "answer"):
                        result.answer = getattr(response, "content", "") or ""
                    result.final_content = (
                        getattr(response, "content", "") or ""
                    )
                    break
                if decision is Decision.CONTINUE:
                    await self._fire("after_iteration", state, iteration)
                    continue

                # ── pre_act 闸（熔断——在 ACT 前，批次 10 修正后的闸位）──
                decision, reason = await self._run_gate(
                    "pre_act", state, payload=response
                )
                if decision is Decision.CONTINUE:
                    await self._fire("after_iteration", state, iteration)
                    continue

                # ── ACT（工具执行）──
                r = await self._maybe_handle(self._actor, response, state)
                if not r.success:
                    result.error = r.error
                    state.stop_reason = "error"
                    await self._fire("on_error", state, r.error)
                    yield {"type": "error", "message": r.error}
                    break

                # ── DECIDE② after_act ──
                #
                # **record 先行**（批次 11 修）：NoProgressDecider 的窗口
                # 需要本轮 tool hashes 入窗后才能判——legacy 由
                # ``_check_no_progress`` 内部 extend 完成，骨架必须显式调用
                # decider 的 ``record``（首轮实测：漏了这步导致骨架永远
                # 判不出 no-progress，一路跑到 max_iterations）。
                tool_hashes = (r.output or {}).get("tool_hashes") or []
                for decider in self._deciders.get("after_act", ()):
                    record_fn = getattr(decider, "record", None)
                    if record_fn is not None:
                        for h in tool_hashes:
                            record_fn(state, h)

                decision, reason = await self._run_gate(
                    "after_act", state, payload=r.output
                )
                if decision is Decision.EXIT:
                    result.finished_reason = "exit"
                    result.stop_reason = reason
                    self._last_result = result  # run_to_completion 需要
                    await self._fire("after_run", state, result)
                    return
                if decision is Decision.STOP:
                    state.stop_reason = reason or "after_act"
                    result.stop_reason = state.stop_reason
                    break

                # ── OBSERVE（下一轮）──
                await self._fire("after_iteration", state, iteration)

            else:
                # for 耗尽 = 上限（PRECHECK 每轮先判，这里兜底）
                state.stop_reason = state.stop_reason or "max_iterations"
                result.stop_reason = result.stop_reason or state.stop_reason

        except Exception as exc:  # noqa: BLE001 — 骨架级兜底
            result.error = str(exc)
            state.stop_reason = "error"
            logger.exception("skeleton run failed")
            await self._fire("on_error", state, exc)
            yield {"type": "error", "message": str(exc)}

        # ── FINALIZE ──
        if self._finalize is not None:
            rv = self._finalize(state, result)
            if hasattr(rv, "__await__"):
                await rv
        self._last_result = result

        await self._fire("after_run", state, result)
        yield {
            "type": "done",
            "content": getattr(result, "answer", "") or getattr(result, "final_content", "") or "",
            "stop_reason": state.stop_reason or "completed",
            "error": result.error,
            "iterations": state.iteration,
        }

    async def run_to_completion(self, spec: SkeletonRunSpec) -> Any:
        """drain run_stream，返回 prepare 提供的 result 对象。"""
        self._last_result = None
        async for event in self.run_stream(spec):
            if event.get("type") == "error":
                logger.debug("skeleton event: %s", event.get("message"))
        return self._last_result

    # ── 内部 ─────────────────────────────────────────────────────────

    @staticmethod
    async def _maybe_handle(handler: Any, inp: Any, state: Any) -> StepResult:
        r = handler.handle(inp, state)
        if isinstance(r, StepResult):
            return r
        return await r

    def _prepare_state(
        self, spec: SkeletonRunSpec
    ) -> tuple[Any, Any]:
        if self._prepare is not None:
            return self._prepare(spec)
        return SkeletonState(), SimpleResult()


class SimpleResult:
    """prepare 未注入时的最小 result（骨架自测用）。"""

    def __init__(self) -> None:
        self.final_content = ""
        self.stop_reason: str | None = None
        self.finished_reason: str | None = None
        self.iterations = 0


__all__ = ["ReActSkeleton", "SimpleResult", "SkeletonRunSpec", "SkeletonState"]
