# -*- coding: utf-8 -*-
"""上下文用量细拆（R-014 H1 · 单一事实源 · §9 复核细粒度化）。

ZCode 的用量弹层把上下文分段呈现。AgentNodes 后端把发给 LLM 的请求
**正交细拆**到 10 个细粒度段（每段独立来源、互不重叠、之和 ≈ 总量）；
前端按 chat-ui-config.json 的 ``contextUsage.groups`` 配置聚合成展示行
——未来调整分组/改名只改配置，不改代码。

**归属规则**（唯一定义点，前端只消费事件）：

| 段 | 归属 |
|---|---|
| system_prompt | 首条 system 消息 |
| user_text | 用户手写文本（扣除末条 user 的注入前缀，含当前轮） |
| assistant_text | 助手文本回复 |
| tool_calls | 助手发起的工具调用参数（JSON） |
| tool_results | 工具返回值（tool 角色消息，含固定 100 字符开销） |
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

#: 细粒度段（§9 复核批 · 用户裁决「后端细拆、前端配置聚合」）。
#:
#: 后端只负责把发给 LLM 的请求**正交拆到最细**——每段是独立来源、
#: 互不重叠、之和 ≈ 请求总量；前端按 chat-ui-config.json 的
#: ``contextUsage.groups`` 聚合成展示行（未来改分组只改配置不改代码）。
#:
#: | 段 | 来源 |
#: |---|---|
#: | system_prompt | 首条 system 消息 |
#: | user_text | 用户手写文本（扣除末条 user 的注入前缀，**含当前轮**） |
#: | assistant_text | 助手文本回复 |
#: | tool_calls | 助手发起的工具调用参数（JSON） |
#: | tool_results | 工具返回值（tool 角色消息，含固定 100 字符开销） |
#: | meta_user_context | ContextBundle 注入项（非 skill：goal/todo/memory/…） |
#: | skills | ContextBundle 注入项（kind=skill） |
#: | tool_prompt | 工具定义的 description（非 MCP） |
#: | system_tool_schemas | 工具定义参数 schema（非 MCP） |
#: | mcp_tool_schemas | MCP 工具定义（description + schema） |
BREAKDOWN_KEYS: tuple[str, ...] = (
    "system_prompt",
    "user_text",
    "assistant_text",
    "tool_calls",
    "tool_results",
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
    """按细粒度段估算上下文 token 构成（前端配置聚合，见 BREAKDOWN_KEYS）。

    :param messages: 发给 LLM 的 messages（含注入后的末条 user）
    :param tools: 工具定义列表（OpenAI function schema 形状）
    :param context_items: 注入的 ContextBundle.items（每条有 kind/content）
    :returns: ``{段key: token数}``，含全部细粒度 key（无数据为 0）

    归属是**角色驱动**而非位置切片（旧版 ``messages[1:-1]`` 的两个缺陷）：
    - 末条 user（当前轮）的手写文本计入 user_text（扣除注入前缀——
      前缀已按 items 记入 meta_user_context / skills，不重复）；
    - tool_calls / tool_result 按角色归工具段，与迭代序号无关，
      修复「迭代 ≥2 时末条是 tool_result 导致口径漂移」。
    """
    result = {key: 0 for key in BREAKDOWN_KEYS}
    if not messages:
        return result

    # system_prompt：首条 system
    if messages[0].get("role") == "system":
        result["system_prompt"] = estimate_tokens_cjk(
            [{"role": "system", "content": str(messages[0].get("content") or "")}]
        )

    # 注入前缀长度：末条 user 的 content 前缀是系统注入的 ContextBundle
    # （session_controller: content = prefix + "\n\n" + 用户输入），prefix 已按
    # items 记入 meta_user_context / skills，从 user_text 中扣除避免重复计数。
    # 无 items 时的兜底：末条 user 超长则视前 2000 字符为注入（保守估计）。
    injected_len = 0
    if context_items:
        contents = [str(getattr(item, "content", "") or "") for item in context_items]
        if contents:
            injected_len = len("\n\n".join(contents)) + 2
    elif len(messages) > 1 and messages[-1].get("role") == "user":
        last_content = str(messages[-1].get("content") or "")
        if len(last_content) > 2000:
            injected_len = 2000
            result["meta_user_context"] = estimate_tokens_cjk(
                [{"role": "user", "content": last_content[:2000]}]
            )

    last_user_idx = max(
        (i for i, m in enumerate(messages) if m.get("role") == "user"),
        default=None,
    )

    # messages[1:] 逐条按角色归属（位置无关 → 迭代间口径一致）
    for i, m in enumerate(messages[1:], start=1):
        role = m.get("role")
        if role == "tool":
            result["tool_results"] += estimate_tokens_cjk([m])
            continue
        # 助手工具调用：参数 JSON 归 tool_calls；文本部分仍归 assistant_text
        for tc in m.get("tool_calls") or []:
            if isinstance(tc, dict):
                fn = tc.get("function") or {}
                blob = _json.dumps(fn.get("arguments", ""), ensure_ascii=False)
                result["tool_calls"] += estimate_tokens_cjk(
                    [{"role": "assistant", "content": blob}]
                )
        content = m.get("content")
        if not (isinstance(content, str) and content):
            continue
        if i == last_user_idx and injected_len:
            # 末条 user：扣除注入前缀，只把用户手写部分计入 user_text
            content = content[injected_len:]
        if not content:
            continue
        bucket = "user_text" if role == "user" else "assistant_text"
        result[bucket] += estimate_tokens_cjk([{"role": role, "content": content}])

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
