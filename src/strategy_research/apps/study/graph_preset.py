"""research preset（B1-b）—— phases 引擎分支的 GraphEngine 阶段图形态。

设计：docs/stage-b1b-research-preset-design-20261027.md。

要点：
- **直译而非重写**：novelty 门 / 回测前存证 / parse 重试执行直接 import
  phase_engine 的同名函数，阶段序、参数、emit 时点与 _run_phases_branch
  逐行对照——行为等价由构造保证。
- abort（novelty 拒绝）是正常业务终态：数据标记 + 条件边 → END，
  不用 GraphInterrupt；parse 重试留在节点内部（同阶段重入，非阶段间跳转）。
- 域事件（study_phase）仍走 runner._emit，与旧路径同源同序；引擎的
  node.started/completed 默认不接（sink=None），避免双重事件。
- 落位在 study 域（非 runtimes）：preset 闭包需要 BranchContext，
  依赖方向必须是 域 → 平台（GraphEngine），绝不反向。
"""
from __future__ import annotations

import json
from typing import Callable

from strategy_research.kernel.runtimes import (
    END,
    INTERRUPT_KEY,
    GraphEngine,
    GraphInterrupt,
)

from .phase_engine import (
    BranchContext,
    RoundOutcome,
    _capture_round_claims,
    _novelty_gate,
    _run_execution_with_parse_retry,
)

EventSink = Callable[[str, dict], None]


def build_research_engine(
    ctx: BranchContext, *, sink: EventSink | None = None
) -> GraphEngine:
    """构造研究轮次阶段图：researcher → novelty →(条件)[gate]→ claims → execution → evaluation。

    ``study.hitl=True`` 时在 novelty 与 claims 之间注册 HITL 审批门
    （构建期条件：关门时图里没有 gate 节点，形状与历史逐字节一致）。
    门语义与契约照抄 langgraph_engine._make_novelty_gate_node，见
    docs/stage-hitl-gate-graph-design-20261027.md。
    """
    engine = GraphEngine(sink=sink)
    engine.add_node("researcher", _researcher_node(ctx))
    engine.add_node("novelty", _novelty_node(ctx))
    engine.add_node("claims", _claims_node(ctx))
    engine.add_node("execution", _execution_node(ctx))
    engine.add_node("evaluation", _evaluation_node(ctx))
    engine.add_edge("researcher", "novelty")
    hitl = bool(getattr(ctx.study, "hitl", False))
    if hitl:
        engine.add_node("gate", _novelty_gate_node(ctx))
    engine.add_conditional_edge(
        "novelty", _novelty_router,
        {"proceed": "gate" if hitl else "claims", "dup": END},
    )
    if hitl:
        engine.add_edge("gate", "claims")
    engine.add_edge("claims", "execution")
    engine.add_edge("execution", "evaluation")
    engine.add_edge("evaluation", END)
    return engine


def run_phases_via_graph(
    ctx: BranchContext, *, sink: EventSink | None = None
) -> RoundOutcome | dict:
    """图形态的 phases 分支：输出契约与 `_run_phases_branch` 完全一致。

    返回 RoundOutcome（正常路径）或 abort dict（novelty 拒绝，
    与旧分支同形：{"round", "run_name", "aborted", "reason"}），
    或 HITL 暂停 dict（{"paused_for_approval", "interrupt_id", ...}，
    与 langgraph 分支同形，runner._handle_hitl_pause 直接消费）。
    """
    engine = build_research_engine(ctx, sink=sink)
    state = engine.run({})

    # 中断早退（同 _run_langgraph_branch 对 paused/aborted 的早退）：
    # novelty_gate → 审批暂停；其他中断（当前唯一来源 max_hops）→ abort
    # 语义跳轮（runner 预算入账 + continue），绝不落到 RoundOutcome
    # 构造去 KeyError 崩轮。
    interrupt = state.get(INTERRUPT_KEY)
    if isinstance(interrupt, dict):
        if interrupt.get("reason") == "novelty_gate":
            return _paused_result(ctx, interrupt.get("data") or {})
        return {
            "round": ctx.round_num,
            "run_name": ctx.run_name,
            "aborted": True,
            "reason": f"interrupt_{interrupt.get('reason') or 'unknown'}",
        }

    if state.get("novelty_ok") is False:
        return {
            "round": ctx.round_num,
            "run_name": ctx.run_name,
            "aborted": True,
            "reason": "novelty_rejected",
        }

    return _outcome_from_state(ctx, state)


def _outcome_from_state(ctx: BranchContext, state: dict) -> RoundOutcome:
    """正常完成路径的共享尾部：state → RoundOutcome（两处调用同形）。"""
    researcher_output = state.get("researcher_output") or {}
    agent_outputs = {
        "researcher": researcher_output,
        **(state.get("exec_agent_outputs") or {}),
        **(state.get("eval_agent_outputs") or {}),
    }
    return RoundOutcome(
        metrics=state["metrics"],
        verdict=state["verdict"],
        eval_result=state["eval_result"],
        agent_outputs=agent_outputs,
        strategist_output=state["strategist_output"],
        hypothesis=state.get("hypothesis", ""),
        predicted_affected=state.get("predicted_affected", []),
        captured_predictions=state.get("captured_predictions", {}),
        backtest_error=state.get("backtest_error"),
    )


def resume_round_via_graph(
    ctx: BranchContext,
    *,
    interrupt_payload: dict,
    sink: EventSink | None = None,
) -> RoundOutcome | dict:
    """HITL approve 后从门后继（claims）续跑——方案 A（重建 + ``start=``）。

    重建契约（hitl-resume-industry-research-20261027.md §5）：暂停点持久化
    后继节点要读的每一个键。claims 只读 researcher_output / predicted_affected
    （execution/evaluation 的输入由 claims/execution 依次产出），故重建
    只需 payload 三键，researcher/novelty/gate 不重跑。审批决策由调用方
    在图外施加（本函数只在 approve 路径被调），gate 保持节点单相位。
    """
    runner = ctx.runner
    runner._emit(ctx.session, "study_phase", {
        "study_id": ctx.sid, "round": ctx.round_num,
        "phase": "graph_resume", "status": "started",
        "decision": "approve",
    })
    engine = build_research_engine(ctx, sink=sink)
    state = engine.run(
        {
            "researcher_output": interrupt_payload.get("researcher_output") or {},
            "predicted_affected": interrupt_payload.get("predicted_affected") or [],
            "hypothesis": interrupt_payload.get("hypothesis") or "",
        },
        start="claims",
    )
    runner._emit(ctx.session, "study_phase", {
        "study_id": ctx.sid, "round": ctx.round_num,
        "phase": "graph_resume", "status": "done",
    })

    interrupt = state.get(INTERRUPT_KEY)
    if isinstance(interrupt, dict):
        return {
            "round": ctx.round_num,
            "run_name": ctx.run_name,
            "aborted": True,
            "reason": f"interrupt_{interrupt.get('reason') or 'unknown'}",
        }
    return _outcome_from_state(ctx, state)


# ── 节点工厂（闭包持 ctx；函数体延迟 import，与旧分支同源）──────


def _researcher_node(ctx: BranchContext) -> Callable[[dict], dict]:
    runner, study, session = ctx.runner, ctx.study, ctx.session

    def node(state: dict) -> dict:
        from strategy_research.apps.autoresearch import run_researcher_phase

        from .runner import SR_AGENT_MAX_ITER

        runner._emit(session, "study_phase", {
            "study_id": ctx.sid, "round": ctx.round_num,
            "phase": "researcher", "status": "started",
        })
        result = run_researcher_phase(
            ctx.path, ctx.strategy, ctx.current_state, ctx.run_dir,
            session_id=session, run_name=ctx.run_name,
            behavior=study.behavior, max_retries=3,
            max_iterations=SR_AGENT_MAX_ITER,
            directives=ctx.directive_text,
            lazy_detection_interval=study.lazy_detection_interval,
            keep_recent=study.keep_recent, round_num=ctx.round_num,
            runs_dir=ctx.runs_dir,
            loop_strategy=runner._loop_strategy,
            on_event=runner._make_agent_on_event(session),
        )
        runner._emit(session, "study_phase", {
            "study_id": ctx.sid, "round": ctx.round_num,
            "phase": "researcher", "status": "done",
        })
        researcher_output = result["researcher_output"]
        if not isinstance(researcher_output, dict):
            researcher_output = {}
        return {**state, "researcher_output": researcher_output}

    return node


def _novelty_node(ctx: BranchContext) -> Callable[[dict], dict]:
    runner = ctx.runner

    def node(state: dict) -> dict:
        researcher_output = state.get("researcher_output") or {}
        hypothesis = researcher_output.get("hypothesis", "")
        predicted_affected = researcher_output.get("predicted_affected") or [
            t["name"] for t in ctx.metric_targets
        ]
        ok = _novelty_gate(runner, ctx.round_num, hypothesis, predicted_affected)
        return {
            **state,
            "novelty_ok": ok,
            "hypothesis": hypothesis,
            "predicted_affected": predicted_affected,
        }

    return node


def _novelty_router(state: dict) -> str:
    return "proceed" if state.get("novelty_ok") else "dup"


def _novelty_gate_node(ctx: BranchContext) -> Callable[[dict], dict]:
    """HITL novelty 审批门（暂停段）：novelty 去重通过后、回测花钱前中断。

    payload 五字段 + 门前 ``study_phase(novelty_gate, interrupted)`` 事件
    照抄 langgraph_engine._make_novelty_gate_node:242-283；恢复段（resume）
    另立文档。空假设防御性放行：正常流里空假设已被 novelty 去重门路由
    dup/END（旧引擎门自带 no_hypothesis 检查是因为它的门在去重之前）。
    """
    runner = ctx.runner

    def node(state: dict) -> dict:
        hypothesis = state.get("hypothesis") or ""
        if not hypothesis:
            return state
        runner._emit(ctx.session, "study_phase", {
            "study_id": ctx.sid, "round": ctx.round_num,
            "phase": "novelty_gate", "status": "interrupted",
            "hypothesis": hypothesis[:200],
        })
        raise GraphInterrupt("novelty_gate", {
            "type": "novelty_gate",
            "study_id": ctx.sid,
            "round_num": ctx.round_num,
            "hypothesis": hypothesis,
            "predicted_affected": state.get("predicted_affected", []),
            # 暂停契约（hitl-resume-industry-research-20261027.md §5）：
            # 后继 claims 节点读 researcher_output（存证要 predictions），
            # 恢复时不得要求 researcher 重跑——暂停点持久化后继要读的
            # 每一个键。
            "researcher_output": state.get("researcher_output") or {},
            "message": f"Round {ctx.round_num}: 请审批 researcher 假设",
        })

    return node


def _paused_result(ctx: BranchContext, payload: dict) -> dict:
    """审批暂停：interrupt 落库 + awaiting_approval 事件 + paused dict。

    事件形状照抄 langgraph_engine.py:639-648（phase 用 ``novelty_gate``
    ——前端零消费此事件，审批卡片由 runner 的 ``study_paused`` 驱动）；
    返回 dict 六字段供 ``_handle_hitl_pause`` 消费（``interrupt_id`` /
    ``hypothesis`` 必需，runner.py:421-426 直接读取）。
    """
    runner = ctx.runner
    hypothesis = payload.get("hypothesis", "")
    interrupt = runner.study_store.create_interrupt(
        study_id=ctx.sid,
        round_num=ctx.round_num,
        interrupt_type=payload.get("type", "novelty_gate"),
        payload=json.dumps(payload, ensure_ascii=False),
    )
    runner._emit(ctx.session, "study_phase", {
        "study_id": ctx.sid, "round": ctx.round_num,
        "phase": "novelty_gate", "status": "awaiting_approval",
        "interrupt_id": interrupt.interrupt_id,
        "hypothesis": hypothesis,
        "gate_message": payload.get("message", ""),
    })
    return {
        "round": ctx.round_num,
        "run_name": ctx.run_name,
        "paused_for_approval": True,
        "study_id": ctx.sid,
        "interrupt_id": interrupt.interrupt_id,
        "hypothesis": hypothesis,
    }


def _claims_node(ctx: BranchContext) -> Callable[[dict], dict]:
    runner = ctx.runner

    def node(state: dict) -> dict:
        predictions = _capture_round_claims(
            runner, ctx.round_num, state.get("researcher_output"),
            state.get("predicted_affected", []), ctx.metric_targets,
        )
        return {**state, "captured_predictions": predictions}

    return node


def _execution_node(ctx: BranchContext) -> Callable[[dict], dict]:
    runner, session = ctx.runner, ctx.session

    def node(state: dict) -> dict:
        runner._emit(session, "study_phase", {
            "study_id": ctx.sid, "round": ctx.round_num,
            "phase": "execution", "status": "started",
        })
        exec_result = _run_execution_with_parse_retry(
            runner, ctx.path, ctx.strategy, ctx.current_state,
            state.get("researcher_output") or {}, ctx.run_dir,
            session=session, run_name=ctx.run_name,
            results_tsv=ctx.results_tsv, round_num=ctx.round_num,
        )
        runner._emit(session, "study_phase", {
            "study_id": ctx.sid, "round": ctx.round_num,
            "phase": "execution", "status": "done",
        })
        exec_agent_outputs = {
            k: exec_result.get(k) for k in (
                "data_quality_output", "factor_analyst_output",
                "strategist_output", "portfolio_construction_output",
            )
        }
        return {
            **state,
            "metrics": exec_result["metrics"],
            "strategist_output": exec_result["strategist_output"],
            "backtest_result": exec_result["backtest_result"],
            "backtest_error": exec_result.get("backtest_error"),
            "exec_agent_outputs": exec_agent_outputs,
        }

    return node


def _evaluation_node(ctx: BranchContext) -> Callable[[dict], dict]:
    runner, study = ctx.runner, ctx.study

    def node(state: dict) -> dict:
        from strategy_research.apps.autoresearch import run_evaluation_phase

        from .runner import SR_AGENT_MAX_ITER

        runner._emit(ctx.session, "study_phase", {
            "study_id": ctx.sid, "round": ctx.round_num,
            "phase": "evaluation", "status": "started",
        })
        eval_result = run_evaluation_phase(
            ctx.path, ctx.strategy, state["backtest_result"],
            state["metrics"], ctx.run_dir,
            behavior=study.behavior, max_retries=3,
            max_iterations=SR_AGENT_MAX_ITER,
            loop_strategy=runner._loop_strategy,
            on_event=runner._make_agent_on_event(ctx.session),
        )
        runner._emit(ctx.session, "study_phase", {
            "study_id": ctx.sid, "round": ctx.round_num,
            "phase": "evaluation", "status": "done",
        })
        eval_agent_outputs = {
            k: eval_result.get(k) for k in (
                "risk_controller_output", "attribution_analyst_output",
                "anti_overfit_analyst_output", "backtest_diagnostics_output",
            )
        }
        return {
            **state,
            "verdict": eval_result["verdict"],
            "eval_result": eval_result,
            "eval_agent_outputs": eval_agent_outputs,
        }

    return node


__all__: list[str] = []  # 域内部件：不进包导出面，经 phase_engine 显式接线
