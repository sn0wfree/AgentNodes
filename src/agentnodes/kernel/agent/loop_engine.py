"""LoopEngine — 配置驱动的循环引擎（批次 11 · B3 阶段 1）。

状态机（llmwikify UnifiedAgentLoop 的 AgentNodes 形态）::

    PRECHECK → [REASON → DECIDE① → ACT → DECIDE② → OBSERVE] → FINALIZE

与 ``AgentLoop._run_loop_core`` 的关系：**影子引擎**。默认路径
（legacy）不经过本模块任何一行；``engine="skeleton"`` 或
``AGENTNODES_SKELETON=1`` 显式启用。等价性由
``tests/test_loop_engine_equivalence.py`` 的双引擎对照测试钉住。

## 消费协议（全部沿用既有对象，无新抽象）

- **state**：鸭子类型——``iteration`` / ``messages`` / ``stop_reason``
  属性即可（AgentNodes 用 ``LoopContext``，llmwikify 用 ``UnifiedContext``，
  引擎不关心）。handlers 负责按需变异。
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
  即复用），引擎本体不感知
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
class LoopEngineSpec:
    """一次引擎运行的输入（B3 阶段 2 由 LoopSpec 取代/收拢）。"""

    task: str
    context: str | None = None
    history: list[dict[str, Any]] | None = None
    max_iterations: int = 8
    session_id: str = ""
    #: no-progress 用户批准续跑（批次 10 的 approved_continuation）
    progress_approved: bool = False


@dataclass
class SkeletonState:
    """引擎内部最小状态（**鸭子协议**——adapters 可换成 LoopContext）。

    引擎只读写字段：``iteration / stop_reason / error / t0``。
    ``messages`` / ``response`` 由 handlers 管理（引擎不触碰）。
    """

    iteration: int = 0
    t0: float = 0.0
    stop_reason: str | None = None
    error: str | None = None
    #: handlers 的自由挂载点（message 列表、response 等）
    extra: dict[str, Any] = field(default_factory=dict)


# ── 引擎 ─────────────────────────────────────────────────────────────


class LoopEngine:
    """配置驱动的循环引擎——**只做控制流，不做策略**。

    REASON/ACT 是注入的 handler；停止判定是注入的 deciders；
    生命周期事件是注入的 hooks。引擎不知道预算怎么算、工具怎么执行、
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
        prepare: Callable[[LoopEngineSpec], tuple[Any, Any]] | None = None,
        on_iteration_start: Callable[[Any, int], None] | None = None,
        event_source: Callable[[Any], tuple[Any, Any]] | None = None,
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
            None = 引擎自建空 state + SimpleResult
        :param on_iteration_start: 每轮开头动作（wrap-up nudge 等）
        :param event_source: 可选 ``() -> loop``——统一引擎 U3 的
            **双通道流式**：run_stream 开始时经它取 loop，把 loop 侧
            ``_emit`` 的事件（text_delta / thinking_delta 等）压进本
            生成器的 pending 队列，步骤间隙 drain 并 yield。None = 不
            转发（旧行为）。适配见 ``AgentLoop._make_event_bridge``。
        """
        self._reasoner = reasoner
        self._actor = actor
        self._deciders = deciders
        self._hooks = hooks
        self._finalize = finalize
        self._prepare = prepare
        self._on_iteration_start = on_iteration_start
        self._event_source = event_source
        self._last_result: Any = None

    # ── 双通道流式（U3）：loop 侧事件 → run_stream ─────────────────

    def _attach_event_source(self, state: Any) -> None:
        """调 event_source(loop) 拿 pending 队列，挂到 state 上。

        ``event_source(loop) -> (pending: list[dict], detach: callable)``
        ——适配器把 loop 的 ``_on_event`` 临时包一层（原回调照常执行），
        事件同时压进 pending；run_stream 在步骤间隙 drain 并 yield。
        纯内存、无锁：单事件循环内生产（步骤内同步回调）与消费
        （步骤间 await 点）交替，无并发写。
        """
        if self._event_source is None:
            return
        try:
            loop = self._event_source()  # 零参：适配器闭包持有 loop
            pending, detach = loop._make_event_bridge()
            state._event_pending = pending
            state._event_detach = detach
        except Exception:  # noqa: BLE001 — 转发是增强，失败不阻断循环
            logger.debug("event_source attach failed", exc_info=True)

    def _detach_event_source(self, state: Any) -> None:
        detach = getattr(state, "_event_detach", None)
        if detach is not None:
            try:
                detach()
            except Exception:  # noqa: BLE001
                logger.debug("event_source detach failed", exc_info=True)
            state._event_detach = None

    def _drain_pending(self, state: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        pending = getattr(state, "_event_pending", None)
        if pending:
            out.extend(list(pending))
            pending.clear()
        # pause 事件（引擎侧产生，U3 事件桥的同形扩展）
        pauses = getattr(self, "_pause_events", None)
        if pauses:
            out.extend(list(pauses))
            pauses.clear()
        return out

    # ── 闸位判定 ─────────────────────────────────────────────────────

    async def _run_gate(
        self,
        gate: str,
        state: Any,
        payload: Any = None,
    ) -> tuple[Decision, str | None]:
        """按序跑一个闸位的 deciders，首个非 PROCEED 生效。

        **挂起处理（H1）**：decider 调 ``ctx.pause(...)`` 只是**创建挂起点**
        （不阻塞，见 pause.py 的 attach_pause）——本方法在闸位跑完后检查
        ``state._pause_handle``，有则 yield pause 事件 → await 恢复 →
        把恢复值写进 ``ctx.resume_value`` → **重跑该闸位**（decider 读到
        resume_value 决定后续）。恢复值因此进循环，且**不重放**闸位之前的
        副作用（不变量 1：挂起点在闸位判定处，继续而非回退）。
        """
        decision, reason = await self._run_gate_once(gate, state, payload)
        handle = getattr(state, "_pause_handle", None)
        if handle is None:
            return decision, reason

        # 1. 消费挂起点（单次挂起：重跑时工厂会因 resume_value 非空而返回 None）
        try:
            state._pause_handle = None
        except Exception:  # noqa: BLE001
            pass

        # 2. 通知外部（事件总线 + run_stream 事件）
        ev = {
            "type": "pause",
            "reason": getattr(handle, "reason", ""),
            "payload": getattr(handle, "payload", {}) or {},
        }
        self._emit_pause(ev)
        # 挂起点在 await 期间保持可见（外部观察者据此 resume）——
        # 与 ``_pause_handle``（一次性消费）区分。
        try:
            state._pause_active = handle
        except Exception:  # noqa: BLE001
            pass

        # 3. await 恢复（async 等待，不阻塞事件循环）
        value = await self._await_resume(handle)
        try:
            state._pause_active = None
        except Exception:  # noqa: BLE001
            pass
        try:
            state.resume_value = value
        except Exception:  # noqa: BLE001
            pass

        # 4. 重跑该闸位——decider 读到 ctx.resume_value 决定后续
        return await self._run_gate_once(gate, state, payload)

    async def _run_gate_once(
        self,
        gate: str,
        state: Any,
        payload: Any = None,
    ) -> tuple[Decision, str | None]:
        """跑一遍闸位的 deciders，首个非 PROCEED 生效。

        **逐个隔离（B2-①b）**：decider 本体抛异常不再穿透引擎级兜底
        （旧行为：整轮 error 出口、归因只剩 "error"），按
        ``crash_policy`` 合成保守裁决——守护型 STOP / 放行型 PROCEED
        （放行由 TextFinal 终答 / max_iter 兜底，有界）。崩溃经
        ``_emit_decider_error`` 落 trace + 事件总线。
        """
        for decider in self._deciders.get(gate, ()):
            try:
                r = decider.handle(payload, state)
                if not isinstance(r, StepResult):
                    r = await r
            except Exception as exc:  # noqa: BLE001 — 逐 decider 隔离
                dname = getattr(decider, "name", type(decider).__name__)
                logger.warning("decider %s crashed at gate %s: %s", dname, gate, exc)
                self._emit_decider_error(gate, decider, exc)
                policy = getattr(decider, "crash_policy", Decision.STOP)
                r = StepResult.ok((policy, f"decider_{dname}"))
            decision, reason = r.output
            if decision is not Decision.PROCEED:
                logger.debug("gate %s: %s (%s)", gate, decision, reason)
                return decision, reason
        return Decision.PROCEED, None

    def _emit_decider_error(self, gate: str, decider: Any, exc: Exception) -> None:
        """decider 崩溃可观测：trace + 事件总线（与 _emit_pause 同款取 loop）。"""
        loop = None
        if self._event_source is not None:
            try:
                loop = self._event_source()
            except Exception:  # noqa: BLE001
                loop = None
        if loop is None:
            return
        try:
            loop._trace_and_emit("decider_error", {
                "decider": getattr(decider, "name", type(decider).__name__),
                "gate": gate,
                "error": str(exc),
            })
        except Exception:  # noqa: BLE001 — 可观测失败不阻断闸位
            logger.debug("decider_error emit failed", exc_info=True)

    async def _await_resume(self, handle: Any) -> Any:
        """await 挂起恢复（不阻塞事件循环）。

        ``PauseHandle.wait`` 是同步阻塞（threading.Event）——放线程里跑，
        主事件循环继续处理其它请求（这正是「循环级 HITL」的意义：
        挂起期间 agent 仍能响应别的请求）。
        """
        import asyncio as _aio

        timeout = getattr(handle, "timeout_s", None)
        try:
            return await _aio.to_thread(handle.wait, timeout)
        except Exception:  # noqa: BLE001 — 等待失败按取消处理
            logger.debug("pause resume failed", exc_info=True)
            return None

    def _emit_pause(self, ev: dict[str, Any]) -> None:
        """把 pause 事件发到 loop 事件总线（外部可观察挂起态）。

        同时压进 pending 队列——``run_stream`` 的 drain 点会 yield 它
        （U3 的事件桥形状：引擎侧事件 → 生成器输出）。
        """
        loop = None
        if self._event_source is not None:
            try:
                loop = self._event_source()
            except Exception:  # noqa: BLE001
                loop = None
        if loop is not None:
            try:
                loop._emit("agent_paused", dict(ev))
            except Exception:  # noqa: BLE001
                logger.debug("pause event emit failed", exc_info=True)
        # 引擎侧 pending（由 _drain_pending 消费 → run_stream yield）
        pending = getattr(self, "_pause_events", None)
        if pending is None:
            pending = []
            self._pause_events = pending
        pending.append(dict(ev))

    # ── hooks 转发 ───────────────────────────────────────────────────

    async def _fire(self, name: str, *args: Any) -> None:
        fn = getattr(self._hooks, name, None) if self._hooks else None
        if fn is not None:
            await fn(*args)

    # ── 状态机 ───────────────────────────────────────────────────────

    async def run_stream(
        self, spec: LoopEngineSpec
    ) -> AsyncIterator[dict[str, Any]]:
        state, result = self._prepare_state(spec)
        self._attach_event_source(state)
        await self._fire("before_run", state)

        try:
            for iteration in range(1, max(1, spec.max_iterations) + 1):
                state.iteration = iteration
                result.iterations = iteration
                if self._on_iteration_start is not None:
                    self._on_iteration_start(state, iteration)
                await self._fire("before_iteration", state, iteration)

                # ── PRECHECK（上限/预算/熔断等进入 ACT 前的闸）──
                decision, reason = await self._run_gate("precheck", state)
                for ev in self._drain_pending(state):
                    yield ev
                if decision is Decision.STOP:
                    state.stop_reason = reason or "precheck"
                    result.stop_reason = state.stop_reason
                    # legacy 口径映射：budget → "budget"，超时 → "timeout"
                    # decider_* 前缀 = 裁判本体崩溃（B2-①b），不是预算超支——
                    # "decider_budget" 会命中子串，必须排除。
                    if "budget" in (reason or "") and not (reason or "").startswith("decider_"):
                        result.finished_reason = "budget"
                    break
                # precheck 无 EXIT：8 个默认 decider 中只有 NoProgressDecider
                #（after_act）返回 EXIT（deciders.py AST 实测 2026-11-11）。
                # 旧分支（L176-181）不可达，统一批 U1 删除——若未来某 decider
                # 需要 precheck EXIT，必须同步恢复本分支并走 FINALIZE 漏斗。

                # ── REASON（compaction + inbox + LLM 调用）──
                r = await self._maybe_handle(self._reasoner, spec, state)
                for ev in self._drain_pending(state):
                    yield ev
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
                    # 预算类 stop 的 finished_reason 口径与 legacy 一致
                    # （loop.py:1059 → "budget"）；不做这层映射会落成
                    # "max_iter"（for-else 兜底），调用方分不清是跑满还是超支。
                    # decider_* 前缀 = 裁判本体崩溃（B2-①b），不是预算超支。
                    if "budget" in (reason or "") and not (reason or "").startswith("decider_"):
                        result.finished_reason = "budget"
                        state.stop_reason = reason
                        result.stop_reason = reason
                    # Legacy 在文本终答前也发 after_iteration
                    # （_run_loop_core:1097）；批次 13 S3-a 实测引擎缺这一次
                    # （legacy 3 次 vs skeleton 2 次）。
                    await self._fire("after_iteration", state, iteration)
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
                # ``_check_no_progress`` 内部 extend 完成，引擎必须显式调用
                # decider 的 ``record``（首轮实测：漏了这步导致引擎永远
                # 判不出 no-progress，一路跑到 max_iterations）。
                tool_hashes = (r.output or {}).get("tool_hashes") or []
                for decider in self._deciders.get("after_act", ()):
                    record_fn = getattr(decider, "record", None)
                    if record_fn is None:
                        continue
                    for h in tool_hashes:
                        # 逐个防护（B2-①b）：喂窗失败只降级 no-progress 保护
                        # （窗口缺 hash，可观测），不阻断循环——旧行为穿透
                        # 引擎级兜底整轮必断。
                        try:
                            record_fn(state, h)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "decider %s record failed: %s",
                                getattr(decider, "name", type(decider).__name__),
                                exc,
                            )
                            self._emit_decider_error("after_act.record", decider, exc)

                decision, reason = await self._run_gate(
                    "after_act", state, payload=r.output
                )
                for ev in self._drain_pending(state):
                    yield ev
                if decision is Decision.EXIT:
                    # **不覆盖 finished_reason**（批次 12 S1）：approval gate
                    # 已写好精确语义（user_rejected / approval_timeout_rejected）；
                    # 无 gate（无精确语义）时才落通用的 "exit"。
                    if getattr(result, "finished_reason", "stop") == "stop":
                        result.finished_reason = "exit"
                    result.stop_reason = reason
                    # 审批闸（loop_progress.py:227-252）已发过 loop_end /
                    # assistant_message / iter_end 三件套——置标记让
                    # LoopFinalize 跳过三件套但仍跑 finalization + trace
                    # close（统一引擎 U1：一切出口必经 FINALIZE 漏斗；
                    # 幂等模式沿 _after_run_fired 先例，adapters:272）。
                    try:
                        state._ending_emitted = True
                    except Exception:  # noqa: BLE001 — 冻结对象则让 Finalize 自己发
                        pass
                    # 改 break 而非 return（U1）：旧实现提前 return 绕过
                    # LoopFinalize —— trace 不关、trace_path=None、无 done。
                    break
                if decision is Decision.STOP:
                    state.stop_reason = reason or "after_act"
                    result.stop_reason = state.stop_reason
                    break

                # ── OBSERVE（下一轮）──
                await self._fire("after_iteration", state, iteration)
                # 统一引擎 U4 / D2：第四闸 after_observe——**空位预留**，
                # 默认零 decider（_run_gate 遇空列表直接 PROCEED，运行时
                # 零变化）。恢复 llmwikify 原版检查点，判定搬迁留给后续
                # 独立语义批（搬迁会改变判定时点 = 行为变更）。
                decision, reason = await self._run_gate("after_observe", state)
                if decision is not Decision.PROCEED:
                    state.stop_reason = reason or "after_observe"
                    result.stop_reason = state.stop_reason
                    if decision is Decision.EXIT:
                        if getattr(result, "finished_reason", "stop") == "stop":
                            result.finished_reason = "exit"
                    break

            else:
                # for 耗尽 = 上限（PRECHECK 每轮先判，这里兜底）
                # legacy 口径：finished_reason="max_iter"（LoopResult 的枚举值）
                state.stop_reason = state.stop_reason or "max_iterations"
                result.stop_reason = result.stop_reason or state.stop_reason
                result.finished_reason = "max_iter"

        except Exception as exc:  # noqa: BLE001 — 引擎级兜底
            result.error = str(exc)
            state.stop_reason = "error"
            logger.exception("skeleton run failed")
            await self._fire("on_error", state, exc)
            yield {"type": "error", "message": str(exc)}

        # ── FINALIZE ──
        for ev in self._drain_pending(state):
            yield ev
        if self._finalize is not None:
            rv = self._finalize(state, result)
            if hasattr(rv, "__await__"):
                await rv
        for ev in self._drain_pending(state):
            yield ev
        self._last_result = result

        self._detach_event_source(state)
        await self._fire("after_run", state, result)
        yield {
            "type": "done",
            "content": getattr(result, "answer", "") or getattr(result, "final_content", "") or "",
            "stop_reason": state.stop_reason or "completed",
            "error": result.error,
            "iterations": state.iteration,
        }

    async def run_to_completion(self, spec: LoopEngineSpec) -> Any:
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
        self, spec: LoopEngineSpec
    ) -> tuple[Any, Any]:
        if self._prepare is not None:
            return self._prepare(spec)
        return SkeletonState(), SimpleResult()


class SimpleResult:
    """prepare 未注入时的最小 result（引擎自测用）。"""

    def __init__(self) -> None:
        self.final_content = ""
        self.stop_reason: str | None = None
        self.finished_reason: str | None = None
        self.iterations = 0


__all__ = ["LoopEngine", "SimpleResult", "LoopEngineSpec", "SkeletonState"]
