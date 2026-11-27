# -*- coding: utf-8 -*-
"""上下文 7 段用量拆解（R-014 H1 · 单一事实源）。

ZCode 的用量弹层把上下文分成 7 段（messages / system_prompt /
meta_user_context / skills / tool_prompt / system_tool_schemas /
mcp_tool_schemas）。AgentNodes 此前没有任何分段记账，且
``meta_user_context`` / ``mcp_tool_schemas`` 连概念都没有——本模块
**新增这两个概念的归属规则**，并按 7 段估算 token。

**归属规则**（唯一定义点，前端只消费事件）：

| 段 | 归属 |
|---|---|
| messages | 对话历史（system 之后、末条 user 之前的消息） |
| system_prompt | 首条 system 消息 |
| meta_user_context | 末条 user 中由 ContextBundle 注入的前缀（用户上下文） |
| skills | ContextBundle 中 kind=skill 的条目 |
| tool_prompt | tools 中每个 tool 的 description（自然语言提示） |
| system_tool_schemas | tools 的参数 schema（非 MCP） |
| mcp_tool_schemas | 来自 MCP 的 tools schema（name 前缀 ``mcp__`` 或 meta.mcp） |

**为什么是估算**：provider 的 usage 只给总量，不给分段。分段用
``estimate_tokens_cjk``（CJK 1 token/字，其它 3 字符/token）估算，
**各段之和 ≈ 总量**，用于呈现构成，不用于计费。中文段不再被 chars/3 低估。

**纯函数**：不依赖 AgentLoop / DB / 事件总线，便于单测。
"""
from __future__ import annotations

from typing import Any

import json as _json
import unicodedata as _unicodedata

#: 7 段的 key 与展示顺序（前端按此顺序着色）。
BREAKDOWN_KEYS: tuple[str, ...] = (
    "messages",
    "system_prompt",
    "meta_user_context",
    "skills",
    "tool_prompt",
    "system_tool_schemas",
    "mcp_tool_schemas",
)

#: MCP 工具识别前缀（新增概念的归属规则）。
MCP_TOOL_PREFIX = "mcp__"


def _is_cjk(ch: str) -> bool:
    """中日韩表意文字（CJK Unified Ideographs 及扩展 A/B）。

    这些字符在常见 tokenizer（含 MiniMax / GPT 系）里通常 1 字 ≈ 1 token，
    而英文/数字约 3-4 字符 ≈ 1 token。统一 chars/3 会把中文严重低估。
    """
    cp = ord(ch)
    return (
        0x4E00 <= cp <= 0x9FFF    # CJK Unified Ideographs
        or 0x3400 <= cp <= 0x4DBF  # Extension A
        or 0x20000 <= cp <= 0x2A6DF  # Extension B
    )


def estimate_tokens_cjk(messages: list[dict[str, Any]]) -> int:
    """7 段专用估算：CJK 1 token/字，非 CJK 3 字符/token。

    **不替换公共 `estimate_tokens`**——compact / context builder / loop metrics
    等既有消费者依赖 chars/3 的旧口径与阈值，改公共函数会破坏它们。
    这里只用于 breakdown 的分段展示，让中文段占比可信。
    """
    total = 0.0
    for msg in messages:
        content = msg.get("content") or ""
        if isinstance(content, str):
            for ch in content:
                total += 1.0 if _is_cjk(ch) else 1.0 / 3.0
        for tc in msg.get("tool_calls") or []:
            if isinstance(tc, dict):
                fn = tc.get("function") or {}
                blob = _json.dumps(fn.get("arguments", ""), ensure_ascii=False)
                for ch in blob:
                    total += 1.0 if _is_cjk(ch) else 1.0 / 3.0
        if msg.get("role") == "tool":
            total += 100 / 3.0  # 与公共函数一致的 tool role 固定开销
    return max(1, int(total))


def estimate_tokens_text_cjk(text: str) -> int:
    """纯文本版 CJK 感知估算（用于 tools 的 description / schema blob）。"""
    if not text:
        return 1
    total = sum(1.0 if _is_cjk(ch) else 1.0 / 3.0 for ch in text)
    return max(1, int(total))


def _tool_is_mcp(tool: Any) -> bool:
    """判定一个 tool 定义是否来自 MCP。

    规则（按优先级）：
    1. name 以 ``mcp__`` 开头（MCP 标准命名）；
    2. tool 对象带 ``mcp`` / ``server`` 标记（自定义适配器）；
    3. 否则不是 MCP。
    """
    if not isinstance(tool, dict):
        return False
    name = str(tool.get("name") or "")
    if name.startswith(MCP_TOOL_PREFIX):
        return True
    for key in ("mcp", "server", "mcp_server"):
        if tool.get(key):
            return True
    return False


def estimate_context_breakdown(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    context_items: list[Any] | None = None,
) -> dict[str, int]:
    """按 7 段估算上下文 token 构成。

    :param messages: 发给 LLM 的 messages（含注入后的末条 user）
    :param tools: 工具定义列表（OpenAI function schema 形状）
    :param context_items: 注入的 ContextBundle.items（每条有 kind/content）
    :returns: ``{段key: token数}``，含全部 7 个 key（无数据为 0）
    """
    result = {key: 0 for key in BREAKDOWN_KEYS}
    if not messages:
        return result

    # system_prompt：首条 system
    if messages[0].get("role") == "system":
        result["system_prompt"] = estimate_tokens_cjk(
            [{"role": "system", "content": str(messages[0].get("content") or "")}]
        )

    # messages：system 之后、末条 user 之前的历史
    history = messages[1:-1] if len(messages) > 1 else []
    if history:
        result["messages"] = estimate_tokens_cjk(history)

    # meta_user_context + skills：从 ContextBundle.items 归属
    for item in context_items or []:
        kind = str(getattr(item, "kind", "") or "")
        content = str(getattr(item, "content", "") or "")
        if kind == "skill":
            result["skills"] += estimate_tokens_cjk([{"role": "user", "content": content}])
        else:
            # 其余注入项（goal/todo/memory/hypothesis/knowledge/artifact/
            # tool_result/history）统一归为「用户上下文注入」——这是新增的
            # meta_user_context 概念：不是用户手写文本，而是系统注入的上下文。
            result["meta_user_context"] += estimate_tokens_cjk(
                [{"role": "user", "content": content}]
            )

    # 若没有 context_items 但末条 user 含注入前缀，按整段估算归入 meta_user_context
    # （调用方应在注入前传 context_items；这里做保守兜底）
    if not context_items and len(messages) > 1:
        last = messages[-1]
        if last.get("role") == "user":
            # 末条 user 超过阈值长度时视作含注入前缀；避免把普通长消息全算进来
            content = str(last.get("content") or "")
            if len(content) > 2000:
                result["meta_user_context"] = estimate_tokens_cjk(
                    [{"role": "user", "content": content[:2000]}]
                )

    # tools：拆分 description / schema，并按 MCP 归属
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") or tool
        if not isinstance(fn, dict):
            continue
        desc = str(fn.get("description") or "")
        if desc:
            if _tool_is_mcp(tool):
                result["mcp_tool_schemas"] += estimate_tokens_cjk(
                    [{"role": "user", "content": desc}]
                )
            else:
                result["tool_prompt"] += estimate_tokens_cjk(
                    [{"role": "user", "content": desc}]
                )
        # schema：去掉 description 后的结构化部分
        schema = {k: v for k, v in fn.items() if k not in ("description", "name")}
        if schema:
            blob = _json.dumps(schema, ensure_ascii=False)
            if _tool_is_mcp(tool):
                result["mcp_tool_schemas"] += estimate_tokens_cjk(
                    [{"role": "user", "content": blob}]
                )
            else:
                result["system_tool_schemas"] += estimate_tokens_cjk(
                    [{"role": "user", "content": blob}]
                )

    return result


__all__ = ["BREAKDOWN_KEYS", "MCP_TOOL_PREFIX", "estimate_context_breakdown"]
