"""引擎与 AgentLoop 的绑定层（批次 11 · B3 阶段 1）。

把 AgentLoop 的既有能力（strategy steps / 预算 / trace / git）包装成
引擎消费的 handler/hook——**沿用 ``bind_agent_loop`` 既有协议**，
不复制任何逻辑。

**async-first**：handlers 是 ``async def handle``（StepHandler 协议允许
返回 Awaitable），内部直接 ``await loop._call_step``——与生产路径的
``arun`` 一致。等价性证明见 ``tests/test_loop_engine_equivalence.py``。
"""

from __future__ import annotations

import logging
from typing import Any

from agentnodes.kernel.agent._core_types import StepHandler, StepResult
from agentnodes.kernel.agent.loop.core import _make_strategy_ctx
from agentnodes.kernel.agent.strategy.loop_context import LoopContext

logger = logging.getLogger(__name__)

__all__ = [
    "LoopActor",
    "LoopFinalize",
    "LoopHooks",
    "LoopIterationStart",
    "LoopPrepare",
    "LoopReasoner",
]


class LoopReasoner(StepHandler):
    """REASON 位：compaction + inbox drain + injectors + LLM 调用 + 预算记账。

    复用 ``strategy.compaction`` 与 ``strategy.llm_call`` 两个 step
    （经 ``_call_step`` 的既有隔离）。产出 = LLM response
    （``None`` = 终止信号，交给 StopSignalDecider / 引擎）。

    **批次 13（S3-a）**：injectors 的两个 order 分支与预算记账都收在这里
    ——翻默认后实测漏掉它们（见 ``docs/design/batch13-*.md`` §1.1/§1.6）：

    - ``order == 0``：per-iteration（TodosInjector 等）
    - ``order >= 100``：post-response（``GoalContinuationInjector``）——
      返回 True = 追加了续跑提示，须让本轮走 CONTINUE
    - 预算：``_record_budget_usage`` 原只在 legacy 的 ``_run_loop_core``
      调用，导致 ``budget_max_tokens`` / ``budget_max_cost_usd`` 在默认引擎
      完全不生效
    """

    def __init__(self, loop: Any) -> None:
        self._loop = loop

    async def handle(self, input: Any, ctx: LoopContext) -> StepResult:
        loop = self._loop

        # compaction step（含 _emit_compaction / _emit_iter_start）
        comp_ctx = _make_strategy_ctx(loop,
            ctx.messages, None, ctx.result, ctx.iteration, ctx.hook_ctx,
        )
        # B2-① T2：compaction 迁新形状——产物走 output（替换态不入 ctx）。
        # 失败吞没（现状语义）：ctx.messages 保持原值，继续 llm_call。
        comp_r = await loop._call_step(loop._strategy.compaction, comp_ctx)
        if comp_r.success:
            ctx.messages = comp_r.output["messages"]

        # inbox drain + per-iteration injectors（order==0）
        loop._drain_inbox(ctx.messages)
        for injector in getattr(loop, "_injectors", []):
            if getattr(injector, "order", 0) == 0:
                try:
                    injector.inject_per_iteration(loop, ctx.messages)
                except Exception:  # noqa: BLE001
                    pass  # injectors must not break the loop

        # LLM call step（内部发 before_iteration / on_error）
        llm_ctx = _make_strategy_ctx(loop,
            ctx.messages, None, ctx.result, ctx.iteration, ctx.hook_ctx,
        )
        step_r = await loop._call_step(loop._strategy.llm_call, llm_ctx)
        if not step_r.success:
            # 崩溃信号回填旧通道（B2-① T1）：_call_step 不再写 ctx；
            # 业务写（"error"）优先，崩溃文本与旧 catch 一致。
            # 空响应走 ok(None)（非失败），不进本分支。
            llm_ctx.should_stop = True
            llm_ctx.stop_reason = llm_ctx.stop_reason or "step_llm_call"

        # 状态回写（引擎的 ctx 就是 LoopContext，steps 已变异它）
        ctx.response = step_r.output
        # **批次 V2 修：必须回写 messages**。
        # ``_make_strategy_ctx`` 做 ``messages=list(messages)``（浅拷贝），
        # 而 ``DefaultLLMCallStep`` 往**那个副本**里 append assistant 消息。
        # 不回写的话：assistant 消息丢在副本里，后续 tool 结果却进原列表 ——
        # 于是发给 provider 的消息序列是 [system, user, tool]，
        # 缺少带 tool_calls 的 assistant 轮次。MiniMax 严格校验直接 400：
        #   "tool result's tool id(...) not found (2013)"
        # 多数 provider 不校验，所以这个 bug 之前一直没暴露。
        ctx.messages = llm_ctx.messages
        if llm_ctx.should_stop:
            ctx.should_stop = True
            ctx.stop_reason = llm_ctx.stop_reason
        if step_r.output is None:
            ctx.should_stop = True
            ctx.stop_reason = ctx.stop_reason or "llm_no_response"
            return StepResult.ok(step_r.output)

        # ── 预算记账（批次 13 S3-a）────────────────────────────────
        # 顺序与 legacy 一致：记账 → 判预算（legacy: 1050-1064）。
        # 记账必须在 after_reason 闸位之前完成，否则 BudgetDecider 读到的
        # tokens_used 恒为 0（这正是翻默认后预算失效的根因）。
        loop._record_budget_usage(llm_ctx.response, ctx.t0 or 0.0)
        # 同步进引擎的 ctx——after_reason 的 BudgetDecider 读的是
        # ``ctx.tokens_used``（BudgetStopStep._resolve），它不会自己去看
        # loop 的私有字段。
        ctx.tokens_used = int(getattr(loop, "_budget_tokens", 0) or 0)
        ctx.cost_usd = float(getattr(loop, "_budget_cost_usd", 0.0) or 0.0)

        # ── post-response injectors（order>=100，批次 13 S3-a）──────
        # GoalContinuationInjector 是默认成员；返回 True = 已追加续跑提示。
        # **只在无 tool_calls 时调用**——与 legacy 一致（loop.py:1066 的
        # ``if not response.has_tool_calls():`` 分支内）。
        if not llm_ctx.response.has_tool_calls():
            for injector in getattr(loop, "_injectors", []):
                if getattr(injector, "order", 0) >= 100:
                    try:
                        if injector.inject_post_response(
                            loop, llm_ctx.response, ctx.messages,
                            ctx.result, ctx.iteration,
                        ):
                            ctx.metadata["injector_continued"] = True
                            break
                    except Exception:  # noqa: BLE001
                        pass  # injectors must not break the loop

        return StepResult.ok(llm_ctx.response)


class LoopActor(StepHandler):
    """ACT 位：工具执行（复用 ``strategy.tool_execution``）。"""

    def __init__(self, loop: Any) -> None:
        self._loop = loop

    async def handle(self, input: Any, ctx: LoopContext) -> StepResult:
        loop = self._loop
        tool_ctx = _make_strategy_ctx(loop,
            ctx.messages, input, ctx.result, ctx.iteration, ctx.hook_ctx,
        )
        step_r = await loop._call_step(loop._strategy.tool_execution, tool_ctx)
        check = getattr(loop, "_check_attempt_timeout", None)
        if callable(check):
            check(ctx.iteration, ctx.result)
        if not step_r.success:
            # 崩溃信号回填旧通道（B2-① T1）——tool_execution 自身不写
            # should_stop，文本与旧 catch 一致。
            tool_ctx.should_stop = True
            tool_ctx.stop_reason = tool_ctx.stop_reason or "step_tool_execution"
        ctx.messages = tool_ctx.messages
        if tool_ctx.should_stop:
            ctx.should_stop = True
            ctx.stop_reason = tool_ctx.stop_reason
        # B2-① T4：直接转发 step 的 output（引擎 after_act 闸读
        # ``r.output["tool_hashes"]``——loop_engine.py:429，形状不变）。
        if step_r.output:
            return StepResult.ok(step_r.output)
        return StepResult.ok({"tool_hashes": [], "tool_result_msgs": []})


class LoopPrepare:
    """运行准备：``strategy.pre_run`` 槽位 + 首个 LoopContext + hook_ctx。

    **B2-① T6 接线**：此前直调 ``loop._prepare_run``（绕过 strategy
    槽位——``pre_run`` step 是死代码位）。现改为委托
    ``strategy.pre_run.handle``：**custom strategy 的 pre_run 从「静默
    忽略」变为「真的跑」**（唯一显式行为例外，§3.6）。

    **不走 ``_call_step``**：prepare 在引擎 ``try:`` 之外（loop_engine.py
    300 vs 304），失败契约 = **崩溃**（异常向上抛给调用方）——
    ``_call_step`` 的 catch 会把异常变成 fail-result，那是循环体内步骤的
    语义，prepare 没有 result 对象可降级。

    副作用顺序与接线前逐字一致：pre_run → subagent 计数重置 → hook_ctx →
    ctx 组装 → attach_pause → raw_task → _skeleton_ctx。
    """

    def __init__(self, loop: Any) -> None:
        self._loop = loop

    def __call__(self, spec: Any) -> tuple[LoopContext, Any]:
        loop = self._loop
        # ctx 先建：pre_run step 要读 ctx.task/context/history 跑注入器
        # 与 ContextHub 检索。
        ctx = LoopContext(
            task=spec.task,
            context=spec.context,
            history=spec.history,
        )
        # ★ 接线点：strategy.pre_run 槽位（原 loop._prepare_run 直调已删）
        r = loop._strategy.pre_run.handle(None, ctx)
        if not r.success:
            raise RuntimeError(f"pre_run failed: {r.error}")
        # 批次 12 S1：每轮 run 重置委派计数（legacy 在 _run_loop_core:799
        # 做同一件事；漏了会让 subagent 的 per-turn 上限跨调用累积）
        try:
            loop._subagent_count[0] = 0
        except Exception:  # noqa: BLE001
            pass
        hook_ctx = loop._build_hook_context(0, ctx.messages)
        ctx.hook_ctx = hook_ctx
        # 统一引擎 H1：装上挂起/恢复原语的工厂（循环级 HITL）。
        # decider 侧 ``ctx.pause(reason=..., payload=...)`` 创建挂起点
        # （**不阻塞**），引擎据此 yield pause 事件 → await 恢复 → 重跑该闸位。
        # 详见 design doc §3.5 与 kernel/agent/signals/pause.py。
        from agentnodes.kernel.agent.signals.pause import attach_pause

        attach_pause(ctx)
        # 批次 13 S3-a：保留用户原始 task（``full_task`` 已被 pre-run
        # 上下文前缀污染，guardrail 必须扫原文）。
        ctx.metadata["raw_task"] = spec.task
        # 批次 12 S1：把当前 ctx 挂到 loop 上——NoProgressDecider 的
        # approval gate 是闭包（decider 不持有 loop），需要它取
        # response/result/iteration 喂 `_check_no_progress`
        loop._skeleton_ctx = ctx
        return ctx, ctx.result


class LoopIterationStart:
    """每轮开头动作：``wrap_up_nudge``（legacy ``_run_loop_core:1002-1009``）。

    **批次 13（S3-a）新增**：注入逻辑原先内联在 legacy 循环体里，引擎
    ``LoopEngine`` 预留了 ``on_iteration_start`` 钩子但从未收到实现，
    于是 ``wrap_up_nudge=True`` 在默认引擎是空操作。
    """

    #: 与 legacy 同源（``loop.WRAP_UP_NUDGE_TEXT``）——单一文案，
    #: 避免两引擎给出不同的提醒措辞。
    def __init__(self, loop: Any) -> None:
        self._loop = loop

    def __call__(self, state: Any, iteration: int) -> None:
        loop = self._loop
        # attempt 级超时检查点（顺序⑤ 缺口①，2026-11-18 裁决 3）：
        # 到点即抛 AttemptTimeoutError 中止本轮——**执行器内部**中断，
        # 不是等待方超时。默认引擎（skeleton）的迭代循环经此钩子，故检查
        # 点放"每轮开头动作"里。
        check = getattr(loop, "_check_attempt_timeout", None)
        if callable(check):
            check(iteration)
        if not getattr(loop, "_wrap_up_nudge", False):
            return
        from agentnodes.kernel.agent.loop.core import WRAP_UP_RATIO, WRAP_UP_NUDGE_TEXT

        max_iter = loop._effective_max_iterations()
        ratio = getattr(loop, "_wrap_up_ratio", WRAP_UP_RATIO)
        if iteration != max(1, int(max_iter * ratio)):
            return
        state.messages.append({
            "role": "system",
            "content": WRAP_UP_NUDGE_TEXT,
        })


class LoopHooks:
    """生命周期事件转发到 ``_afire_hooks``（签名对齐引擎的 _fire）。"""

    def __init__(self, loop: Any) -> None:
        self._loop = loop

    async def before_run(self, ctx: Any) -> None:
        """legacy :809 在 prepare 后发一次 before_run（hook_ctx 形态）。

        引擎传入的是 LoopContext（prepare 产出），这里取它的 hook_ctx。
        批次 12 S1 补：原为空实现，翻默认后 ``test_arun_hooks_are_awaited``
        暴露缺失（hooks 只有 after_run）。

        **批次 13（S3-a）**：用户输入的 guardrail 扫描也在这一步——legacy
        在 ``_run_loop_core:980-989`` 于 before_run 之后立刻扫描，引擎
        完全没有对应点（翻默认后 ``scan_user_input`` 永不执行）。
        """
        loop = self._loop
        hook_ctx = getattr(ctx, "hook_ctx", None)
        if hook_ctx is None:
            hook_ctx = loop._build_hook_context(
                getattr(ctx, "iteration", 0), getattr(ctx, "messages", []),
            )
        await loop._afire_hooks("before_run", hook_ctx)

        # Phase 5-guardrails v1: observe injection signatures in the user
        # task. Emits only — never alters the message path.
        # ``raw_task`` 由 LoopPrepare 写入（``ctx.task`` 已含 pre-run 前缀）。
        original_task = (getattr(ctx, "metadata", None) or {}).get("raw_task", "")
        try:
            from agentnodes.kernel.agent.guards.guardrails import scan_user_input

            for finding in scan_user_input(
                original_task, source_id=loop.session_id or "",
            ):
                loop._emit("guardrail.finding", finding.to_dict())
                await loop._afire_hooks("on_guardrail_finding", hook_ctx, finding)
        except Exception:  # noqa: BLE001 — guardrails never break the loop
            logger.debug("guardrail scan failed (user input)", exc_info=True)

    async def before_iteration(self, ctx: LoopContext, iteration: int) -> None:
        pass  # llm_call step 内部已发 before_iteration（loop.py:932-934 注释）
        #      适配器再发会重复——legacy 每轮只发一次

    async def after_iteration(self, ctx: LoopContext, iteration: int) -> None:
        hook_ctx = getattr(ctx, "hook_ctx", None)
        if hook_ctx is not None:
            await self._loop._afire_hooks("after_iteration", hook_ctx)

    async def after_run(self, ctx: Any, result: Any) -> None:
        """``after_run`` 只发一次（批次 13 S3-a）。

        两个调用方都会到这一步：``DefaultFinalizationStep.execute``
        （finalization step 内部）与引擎 ``run_stream`` 末尾。两者都在
        引擎路径上，实测 legacy 发 1 次、skeleton 发 2 次。
        用 ctx 上的标记做幂等——比删任一方更稳（删 step 里那次会破坏
        「Step 自触发 hook」的既有协议；删引擎末尾那次会漏掉 EXIT 路径，
        它不走 finalization step）。
        """
        if getattr(ctx, "_after_run_fired", False):
            return
        try:
            ctx._after_run_fired = True
        except Exception:  # noqa: BLE001 — 冻结对象则退回不去重
            pass
        hook_ctx = getattr(ctx, "hook_ctx", None)
        if hook_ctx is None:
            hook_ctx = self._loop._build_hook_context(
                getattr(ctx, "iteration", 0), getattr(ctx, "messages", []),
            )
        await self._loop._afire_hooks("after_run", hook_ctx, result)

    async def on_error(self, ctx: Any, error: Any) -> None:
        logger.warning("skeleton on_error: %s", error)


class LoopFinalize:
    """终局：finalization step + git commit + trace close + **收尾事件**。

    批次 12 S1 补：翻默认后 ``test_arun_emits_iter_start_and_end`` 暴露
    ``iter_end`` / ``assistant_message`` / ``loop_end`` 一条未发——
    legacy 这三个事件由 ``_handle_stop`` / ``_handle_max_iter`` 发
    （loop_lifecycle.py:41-68），引擎没调那两个方法。

    **批次 13（S3-a）**：

    - ``max_iter`` 终答文案（legacy ``_handle_max_iter`` 补
      "Reached max_iterations=N without a final answer."；引擎原先留空，
      导致 ``LoopResult.success`` 由 True 变 False）
    - ``after_run`` 只在这里发一次（原先 ``DefaultFinalizationStep`` 发
      一次、引擎末尾再发一次 → 实测 2 次 vs legacy 1 次）
    """

    def __init__(self, loop: Any) -> None:
        self._loop = loop

    async def __call__(self, ctx: LoopContext, result: Any) -> None:
        loop = self._loop

        # ── max_iter 终答文案（批次 13 S3-a）────────────────────────
        # 在发事件与跑 finalization step 之前补，这样 assistant_message /
        # metrics 看到的都是最终答案文本。语义与 legacy _handle_max_iter
        # 逐字一致（含 "Reached max_iterations=N" 前缀）。
        finish = getattr(result, "finished_reason", "stop") or "stop"
        if finish == "max_iter" and not getattr(result, "answer", ""):
            result.answer = (
                f"Reached max_iterations={loop.max_iterations} "
                f"without a final answer."
            )

        # ── 收尾事件（legacy _handle_stop / _handle_max_iter 的等价）──
        # **幂等**（统一引擎 U1）：after_act EXIT 走 FINALIZE 漏斗后，审批闸
        # （loop_progress.py:227-252）已发过三件套——skeleton 在该出口置
        # ``ctx._ending_emitted``，这里见标记则跳过三件套，但仍跑
        # finalization step + git commit + trace close（出口收尾是超集，
        # design doc §3.2；幂等模式沿本文件 ``_after_run_fired`` 先例）。
        iteration = getattr(result, "iterations", 0) or 0
        if not getattr(ctx, "_ending_emitted", False):
            try:
                # **budget_exhausted 补发**（统一引擎 U2）：legacy 在预算
                # 停机 break 前发射（loop.py:1119），skeleton 走
                # BudgetDecider → STOP → 本收尾，此前无发射点——「因预算
                # 停止」在生产主路径上零信号（inventory §6 差异 #2）。
                # 载荷形状与 legacy 一致。
                if finish == "budget":
                    loop._trace_and_emit("budget_exhausted", {
                        "reason": result.stop_reason or finish,
                        "tokens_used": getattr(ctx, "tokens_used", 0) or 0,
                        "cost_usd": round(
                            getattr(ctx, "cost_usd", 0.0) or 0.0, 6),
                        "elapsed": round(
                            getattr(ctx, "t0", 0.0) and
                            (__import__("time").perf_counter() -
                             getattr(ctx, "t0", 0.0)) or 0.0, 3),
                    })
                loop._trace_and_emit("loop_end", {
                    "reason": finish, "iteration": iteration,
                })
                if getattr(result, "answer", ""):
                    loop._emit("assistant_message", {"content": result.answer})
                loop._emit("iter_end", {
                    "iteration": iteration,
                    "finish_reason": finish,
                    "tool_calls_made": getattr(result, "tool_calls_made", 0),
                })
            except Exception:  # noqa: BLE001 — 事件发射不阻断收尾
                logger.debug("skeleton finalize events failed", exc_info=True)

        fin_ctx = _make_strategy_ctx(loop,
            ctx.messages, None, result, result.iterations,
            getattr(ctx, "hook_ctx", None),
        )
        fin_ctx.t0 = getattr(ctx, "t0", 0.0)
        # B2-① T1：失败吞没（现状语义——无人读 fin_ctx.should_stop）；
        # 崩溃时 _call_step 内部已 warning。
        await loop._call_step(loop._strategy.finalization, fin_ctx)
        # ``DefaultFinalizationStep`` 内部已发过一次 after_run（它直接调
        # ``loop._afire_hooks``，绕过 LoopHooks）——在这里打标记，让引擎
        # 末尾那次 ``_fire("after_run")`` 跳过。EXIT 路径不走 finalization
        # step，标记未置位，末尾那次正常发（所以两处都不能删）。
        try:
            ctx._after_run_fired = True
        except Exception:  # noqa: BLE001
            pass
        # git commit + trace close（阶段 2 后半收编为 hook）
        import asyncio as _aio

        task = getattr(ctx, "task", "")
        await _aio.to_thread(loop._git_commit, task, result)
        if loop._trace_writer is not None:
            result.trace_path = str(loop._trace_writer.path)
            loop._trace_writer.close()
