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
        permission_evaluator: Any | None = None,
        permission_gateway: Any | None = None,
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
        # B1-d：权限闸门（evaluator 规则 + gateway ASK 审批）。不注入 =
        # 无权限检查（测试/脚本路径）。生产 chat 必须注入，否则工具裸跑。
        self._permission_evaluator = permission_evaluator
        self._permission_gateway = permission_gateway
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
            self._sink("run.failed", {"run_id": run_id, "error": str(exc)})
            self._sink("run.settled", {"run_id": run_id, "ok": False})
            return RunResult(turn_id, run_id, ok=False, error=str(exc))

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

    def _inject_context(self, messages: list[dict], session_id: str) -> list[dict]:
        """B1-b: prepend hub-collected context items (goal/memory) to the turn.

        Uses the same ContextHub contract as the AgentLoop path (P5-4):
        items are rendered as a single leading user block, matching the
        legacy ``full_task = blocks + "\\n\\n" + task`` shape. No hub
        registered → passthrough (test/sync paths).
        """
        if self._context_hub is None or not messages:
            return messages
        try:
            from strategy_research.core.context.protocol import ContextQuery

            last = messages[-1]
            if last.get("role") != "user":
                return messages
            bundle = self._context_hub.build(
                ContextQuery(
                    task_text=str(last.get("content") or ""),
                    session_id=session_id,
                ),
            )
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
