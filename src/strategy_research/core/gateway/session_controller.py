"""SessionController（阶段 1a 最小版）—— 准入 + 签发身份 + 驱动引擎。

P-4 身份：turn_id = {session_id}#{n}（per-session 单调）；
run_id = {turn_id}:r0（turn:run=1:N，1a 每 turn 一次运行）。

生命周期事件：turn.admitted → run.started → [节点事件] → run.settled。
失败路径：异常捕获后 run.settled{ok:False} + run.failed 事件——引擎崩溃
不打断控制器，队列里下一个 turn 照常。

驱动方式：run_pending() 清空 EntryPoint 所有 mailbox（E1 由测试/脚本驱动；
A2 换成后台消费者常驻）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from strategy_research.core.orchestration import EntryPoint, WorkRequest
from strategy_research.core.runtimes.graph_engine import GraphEngine

EventSink = Callable[[str, dict], None]


@dataclass(frozen=True)
class RunResult:
    turn_id: str
    run_id: str
    ok: bool
    answer: str = ""
    error: str | None = None


class SessionController:
    def __init__(
        self,
        entry_point: EntryPoint,
        *,
        client: Any,
        system: str | None = None,
        sink: EventSink | None = None,
        engine_factory: Callable[[], GraphEngine] | None = None,
        registry: Any | None = None,
        stream: bool = False,
        max_iterations: int = 1,
        role: str = "chat",
        workspace: Any | None = None,
        extra_context: dict[str, Any] | None = None,
        context_hub: Any | None = None,
        context_policy: Any | None = None,
        permission_evaluator: Any | None = None,
        permission_gateway: Any | None = None,
        compact_config: Any | None = None,
        fence: Any | None = None,
    ) -> None:
        self._ep = entry_point
        self._client = client
        self._system = system  # 显式 system：跳过装配核（等价于 override）
        self._sink = sink or (lambda kind, data: None)
        self._engine_factory = engine_factory  # None = 默认图（react 或 llm）
        self._registry = registry
        self._stream = stream
        self._max_iterations = max_iterations
        # B1-a/b：与 AgentLoop 路径共享的装配输入（同一渲染核、同一上下文源）
        self._role = role
        self._workspace = workspace
        self._extra_context = extra_context
        self._context_hub = context_hub
        # B5 W2：仲裁策略（None = 不保护的默认策略；只在有预算时生效）。
        self._context_policy = context_policy
        # B1-d：权限闸门（evaluator 规则 + gateway ASK 审批）。不注入 =
        # 无权限检查（测试/脚本路径）。生产 chat 必须注入，否则工具裸跑。
        self._permission_evaluator = permission_evaluator
        self._permission_gateway = permission_gateway
        # B1-c：mid-loop 压缩配置（None = 不压缩）。压缩器在构造引擎时
        # 按当前 session 生成，复用 core/agent/compaction_runtime 的同一
        # 实现（与 AgentLoop 路径语义一致）。
        self._compact_config = compact_config
        # R1：写栅栏（INV-4/INV-5）。None = 不设防（直构测试路径，与
        # permission_gateway 同型先例）；生产由 Gateway 注入唯一实例。
        self._fence = fence
        self._current_session_id: str = ""  # per-turn, set by _execute
        self._turn_counters: dict[str, int] = {}  # session_id -> 已发 turn 数

    # ── 驱动 ─────────────────────────────────────────────

    def run_pending(self, kinds: Any = None) -> list[RunResult]:
        """清空 mailbox：逐 turn 准入 → 执行 → settle。

        kinds：可选的 WorkKind 集合（只领自己负责的工单）。默认 None = 全部，
        保留既有测试/脚本行为。不匹配的工单留在队列（例如 chat 消费者
        不碰 AUTOMATION 工单）。
        """
        results: list[RunResult] = []
        for session_key, mailbox in list(self._ep.iter_mailboxes()):
            while True:
                request = mailbox.poll_due(kinds)
                if request is None:
                    break
                results.append(self._execute(request))
        return results

    # ── 单个 turn ────────────────────────────────────────

    def _execute(self, request: WorkRequest) -> RunResult:
        session_id = self._ep.session_id_for(request.session_key) or ""
        self._turn_counters[session_id] = self._turn_counters.get(session_id, 0) + 1
        turn_id = f"{session_id}#{self._turn_counters[session_id]}"
        run_id = f"{turn_id}:r0"

        self._sink("turn.admitted", {"turn_id": turn_id, "session_id": session_id})
        self._sink("run.started", {"run_id": run_id, "kind": request.kind.value})
        # R1 fence ①：认领即超越（INV-5）——后到的 run 使先到的 token 失效。
        token = (
            self._fence.claim(session_id, run_id)
            if self._fence is not None
            else None
        )

        # _build_engine keeps its zero-arg contract (tests replace it with
        # a lambda); the current session reaches it via this attribute.
        self._current_session_id = session_id
        engine = self._build_engine()
        # P5-1：payload 可携带已格式化历史（旧路径 _convert_messages_to_history 的
        # 输出同构）。顺序：历史在前、当前输入在后——调用方须在 emit 本轮事件
        # **之前**读取历史，否则当前轮会重复注入。
        history = request.payload.get("history")
        messages: list[dict] = []
        if isinstance(history, list):
            messages.extend(
                m for m in history
                if isinstance(m, dict) and m.get("role") and "content" in m
            )
        messages.append({"role": "user", "content": _payload_text(request)})
        # B1-b：pre-run 上下文（goal/memory）经 ContextHub 注入——与
        # AgentLoop 路径同一契约（块在前、当前输入在后）。
        messages = self._inject_context(messages, session_id)
        state = {
            "messages": messages,
            "session_id": session_id,
            "turn_id": turn_id,
            "run_id": run_id,
        }
        try:
            final = engine.run(state)
            answer = str(final.get("answer", ""))
        except Exception as exc:  # noqa: BLE001 —— 引擎崩溃不拖垮控制器
            if token is not None:
                # 本 run 已死：释放以免挡住同 session 的下一个 turn。
                self._fence.release(token)
            self._sink("run.failed", {"run_id": run_id, "error": str(exc)})
            self._sink("run.settled", {"run_id": run_id, "ok": False})
            return RunResult(turn_id, run_id, ok=False, error=str(exc))

        # R1 fence ②：settled 事件本身就是「产物成立」的声明，必须先过
        # 栅栏（INV-4）。被超越 → 陈旧产物不得落库，也不 release（栅栏
        # 归超越者持有）。
        if token is not None:
            verdict = self._fence.validate(token)
            if not verdict.ok:
                self._sink("run.fenced", {
                    "run_id": run_id,
                    "session_id": session_id,
                    "reason": verdict.reason,
                })
                self._sink("run.settled", {
                    "run_id": run_id, "ok": False, "fenced": True,
                })
                return RunResult(
                    turn_id, run_id, ok=False, error=f"fenced: {verdict.reason}",
                )
            # R1 fence ③：正常收尾释放（仅 active 可释放，不误伤后继者）。
            self._fence.release(token)

        # R2.1a：答案进事件流（「一切事实经事件流」）。fenced 的 run 在
        # 上方提前 return，陈旧答案不会出现在事件流里。消费方：
        # chat bridge 据此发 assistant_message（消费者模型下请求线程
        # 看不到 RunResult，事件流是答案的唯一通道）。
        self._sink("run.answer", {"run_id": run_id, "content": answer})

        self._sink(
            "run.settled",
            {"run_id": run_id, "ok": True, "answer_chars": len(answer)},
        )
        return RunResult(turn_id, run_id, ok=True, answer=answer)

    def _build_engine(self) -> GraphEngine:
        """正式构造路径（不再 monkey-patch）。engine_factory 优先。

        Zero-argument by contract (tests inject ``lambda: engine``); the
        per-turn session id arrives via ``_current_session_id``.

        P5-1b：有 registry 时用 react 节点（无工具调用时退化为单次 LLM 调用，
        有工具时自动进入循环）；否则退化为纯 llm 节点。

        B1-a：系统提示经 ``assemble_loop_parts`` 统一渲染——与
        ``build_chat_agent_loop`` 同一实现，保证 chat 两条路径的 prompt
        逐字节一致。

        B1-d：react 路径注入 ``ctx_factory``（ToolContext 按 tool_call 构造）
        ——权限 evaluator/gateway、workspace、session_id 全部经它传递。
        不注入 = 工具裸跑（仅测试路径）。
        """
        if self._engine_factory is not None:
            return self._engine_factory()

        system = self._resolve_system_prompt()
        engine = GraphEngine(sink=self._sink)
        if self._registry is not None:
            from strategy_research.core.agent.tool_context import make_ctx_factory
            from strategy_research.core.runtimes.nodes.react import (
                ReactConfig,
                make_react_node,
            )

            ctx_factory = None
            if (
                self._permission_evaluator is not None
                or self._permission_gateway is not None
                or self._workspace is not None
                # B5 B-c：生产 chat（hub 已接线）的工具调用必须归属会话，
                # 否则 todowrite 等落到共享 "default" 桶。仅带 session_id
                # 的 ToolContext 不改变权限行为——enforce_permission_sync
                # 在无 evaluator 时直接返回（tools.py:878-880）。
                or self._context_hub is not None
            ):
                ctx_factory = make_ctx_factory(
                    workspace=self._workspace,
                    session_id=self._current_session_id or None,
                    permission_evaluator=self._permission_evaluator,
                    permission_gateway=self._permission_gateway,
                    emit_event=lambda kind, data: self._sink(kind, data),
                )

            engine.add_node(
                "react",
                make_react_node(
                    self._client, self._registry, sink=self._sink,
                    config=ReactConfig(
                        system=system,
                        stream=self._stream,
                        max_iterations=self._max_iterations,
                        ctx_factory=ctx_factory,
                        compactor=self._build_compactor(),
                    ),
                ),
            )
        else:
            from strategy_research.core.runtimes.nodes.llm import make_llm_node

            engine.add_node(
                "llm",
                make_llm_node(
                    self._client, system=system,
                    stream=self._stream, sink=self._sink,
                ),
            )
        return engine

    def _model_limits(self) -> tuple[int | None, int | None]:
        """(context_window, max_output) from the client's LLMConfig (B5 R-3).

        The client exposes no such attributes -- the values live on
        ``client.config`` (LLMConfig, config.py:164). Reading them off the
        client silently yielded None, which sent ``make_compactor`` to its
        8_000 fallback: chat was compressing at 8k tokens instead of 80% of
        the model window, while AgentLoop (which reads LLMConfig) was not.
        """
        cfg = getattr(self._client, "config", None)
        return (
            getattr(cfg, "model_context_tokens", None),
            getattr(cfg, "model_max_output_tokens", None),
        )

    def _build_compactor(self):
        """B1-c: mid-loop compactor, or None when compaction is disabled.

        Reuses ``core/agent/compaction_runtime.make_compactor`` — the same
        implementation the AgentLoop mixin uses, so compression semantics
        cannot drift between the two executors.
        """
        if self._compact_config is None:
            return None
        try:
            from strategy_research.core.agent.compaction_runtime import (
                make_compactor,
            )

            ctx_tokens, max_out = self._model_limits()
            return make_compactor(
                compact_config=self._compact_config,
                model_context_tokens=ctx_tokens,
                model_max_output_tokens=max_out,
                llm_client=self._client,
                session_id=self._current_session_id or None,
            )
        except Exception as exc:  # noqa: BLE001 — 压缩不可用不阻断 chat
            self._sink("context.compactor_init_failed", {"error": str(exc)})
            return None

    def _resolve_system_prompt(self) -> str | None:
        """Render the system prompt via the shared assembly core.

        ``self._system`` is an explicit override (wins, matching
        ``assemble_loop_parts(system_prompt_override=...)``).

        Without an explicit system AND without a registry there is nothing
        to assemble from — return None so the llm-node path stays
        prompt-free (existing contract: ``test_session_controller``
        asserts no system message is invented). The assembly core is
        engaged only for the react path (registry present), where the
        AgentLoop chat path would also have rendered a role prompt.
        """
        if self._system is not None:
            return self._system
        if self._registry is None:
            return None
        try:
            from strategy_research.core.agent.loop_assembly import (
                assemble_loop_parts,
            )

            assembly = assemble_loop_parts(
                role=self._role,
                registry=self._registry,
                extra_context=self._extra_context,
                workspace=self._workspace,
            )
            return assembly.system_prompt
        except Exception as exc:  # noqa: BLE001 — 渲染失败不阻断 chat
            self._sink("system_prompt.render_failed", {"error": str(exc)})
            return None

    def _context_budget_tokens(self) -> int | None:
        """Token budget for context arbitration (B5 W2 / R-2).

        Same source as the compaction threshold (``_model_limits``) so the
        two cannot fight: arbitration trims *what goes in*, compaction
        summarises *what is already there*. Unknown window -> None ->
        no arbitration (safe degradation, per the design's §4.2).
        """
        ctx_tokens, _ = self._model_limits()
        if not ctx_tokens:
            return None
        return max(1, int(ctx_tokens * 0.8))

    def _inject_context(self, messages: list[dict], session_id: str) -> list[dict]:
        """B1-b: prepend hub-collected context items (goal/memory) to the turn.

        Uses the same ContextHub contract as the AgentLoop path (P5-4):
        items are rendered as a single leading user block, matching the
        legacy ``full_task = blocks + "\\n\\n" + task`` shape. No hub
        registered → passthrough (test/sync paths).

        B5 W2 (R-2): passes the token budget and policy so arbitration can
        run. Both default to "nothing" when the model window is unknown, so
        this is byte-identical to W0 behaviour on that path.
        """
        if self._context_hub is None or not messages:
            return messages
        try:
            from strategy_research.core.context.protocol import ContextQuery

            last = messages[-1]
            if last.get("role") != "user":
                return messages
            query = ContextQuery(
                task_text=str(last.get("content") or ""),
                session_id=session_id,
            )
            budget = self._context_budget_tokens()
            if budget is None:
                # No model window -> no arbitration -> call build() exactly
                # as W0 did. Keeps duck-typed hubs (tests/scripts) working.
                bundle = self._context_hub.build(query)
            else:
                try:
                    bundle = self._context_hub.build(
                        query,
                        budget_tokens=budget,
                        policy=self._context_policy,
                    )
                except TypeError:
                    # Hub predates the W2 keyword args.
                    bundle = self._context_hub.build(query)
            if bundle.accounting.get("over_budget"):
                self._sink("context.over_budget", {
                    "total_tokens": bundle.accounting.get("total_tokens"),
                    "dropped": len(bundle.dropped),
                })
            if not bundle.items:
                return messages
            prefix = "\n\n".join(item.content for item in bundle.items)
            return messages[:-1] + [
                {**last, "content": prefix + "\n\n" + str(last.get("content") or "")},
            ]
        except Exception as exc:  # noqa: BLE001 — 上下文失败不阻断 chat
            self._sink("context.inject_failed", {"error": str(exc)})
            return messages


def _payload_text(request: WorkRequest) -> str:
    """1a 约定：payload["text"] 即用户输入（chat 类）。"""
    text = request.payload.get("text")
    return text if isinstance(text, str) else ""
