"""入口 adapter（阶段 3a）—— 把各入口的请求翻译成 WorkRequest。

职责边界（契约 §1）：
- 渠道 adapter 只做：协议字段剥除 → WorkRequest 构造 → 提交 → 拿回执。
- **不做**任何裁决（准入/排队/预算都在 Gateway 内）。
- **不认识** SessionController（消费由 Gateway 侧驱动）。

阶段 4：灰度门已删除——所有入口恒走统一通道（WorkRequest → EntryPoint →
mailbox）。旧路径（asyncio.Queue / 直接调 service）不再存在。
"""
from __future__ import annotations

from strategy_research.kernel.orchestration import (
    EntryPoint,
    Receipt,
    SourceKind,
    WorkKind,
    WorkRequest,
)


def research_graph_enabled() -> bool:
    """[阶段 4 已退役] phases 分支恒走 GraphEngine 阶段图。

    保留函数仅为兼容既有 import 与门禁测试；恒返回 True，
    环境变量不再生效。删除条件：确认无外部调用方（阶段 5）。
    """
    return True


def api_chat_request(
    session_id: str,
    content: str,
    *,
    history: list[dict] | None = None,
) -> WorkRequest:
    """把 API chat 请求翻译成 WorkRequest。

    session_key 用 `chat:<session_id>`——同一物理会话的请求进同一 mailbox
    （session=串行域）。协议字段（HTTP headers 等）在此剥除，不进入 payload。

    ``history``：已格式化的对话历史（与旧路径 `_convert_messages_to_history`
    输出同构），供引擎构造完整 messages。仅入 payload，不进事件。
    """
    payload: dict[str, object] = {"text": content}
    if history:
        payload["history"] = history
    return WorkRequest(
        kind=WorkKind.CHAT,
        source=SourceKind.API_CHAT,
        session_key=f"chat:{session_id}",
        payload=payload,
    )


def cli_request(session_key: str, text: str) -> WorkRequest:
    """CLI 请求：来源 CLI → 默认 followup（五行表）。"""
    key = session_key if ":" in session_key else f"chat:{session_key}"
    return WorkRequest(
        kind=WorkKind.CHAT,
        source=SourceKind.CLI,
        session_key=key,
        payload={"text": text},
    )


def mcp_request(session_key: str, payload: dict) -> WorkRequest:
    """MCP 请求：来源 MCP → 默认 followup（保守排队）。"""
    key = session_key if ":" in session_key else f"mcp:{session_key}"
    return WorkRequest(
        kind=WorkKind.CHAT,
        source=SourceKind.MCP,
        session_key=key,
        payload=dict(payload),
    )


def scheduler_request(session_key: str, payload: dict) -> WorkRequest:
    """定时任务请求：来源 scheduler → 默认 interrupt。"""
    key = session_key if ":" in session_key else f"automation:{session_key}"
    return WorkRequest(
        kind=WorkKind.AUTOMATION,
        source=SourceKind.SCHEDULER,
        session_key=key,
        payload=dict(payload),
    )


def submit_via_entry(entry: EntryPoint, request: WorkRequest) -> Receipt:
    """统一提交点：所有 adapter 都经由这里（便于审计/打点）。"""
    return entry.submit(request)
