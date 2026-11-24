# -*- coding: utf-8 -*-
"""H1 · 7 段上下文拆解单测（选项 B：含 meta_user_context / mcp_tool_schemas）。"""
from __future__ import annotations

from agentnodes.kernel.context.breakdown import (
    BREAKDOWN_KEYS,
    MCP_TOOL_PREFIX,
    estimate_context_breakdown,
)


class _Item:
    def __init__(self, kind: str, content: str):
        self.kind = kind
        self.content = content


def test_all_seven_keys_present():
    result = estimate_context_breakdown([], [])
    assert set(result) == set(BREAKDOWN_KEYS)
    assert len(BREAKDOWN_KEYS) == 7


def test_system_prompt_and_messages():
    msgs = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "上一轮"},
        {"role": "assistant", "content": "回答"},
        {"role": "user", "content": "当前问题"},
    ]
    r = estimate_context_breakdown(msgs, [])
    assert r["system_prompt"] > 0
    assert r["messages"] > 0
    assert r["meta_user_context"] == 0  # 短末条 user 不算注入


def test_context_items_map_to_meta_and_skills():
    items = [
        _Item("skill", "技能内容" * 100),
        _Item("goal", "目标内容" * 100),
        _Item("memory", "记忆内容" * 100),
    ]
    r = estimate_context_breakdown(
        [{"role": "user", "content": "hi"}], [], context_items=items,
    )
    assert r["skills"] > 0
    assert r["meta_user_context"] > 0
    # skills 与 meta 分开归属
    assert r["skills"] != r["meta_user_context"]


def test_tools_split_description_and_schema():
    tools = [{
        "name": "search",
        "description": "搜索网页" * 50,
        "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
    }]
    r = estimate_context_breakdown([{"role": "user", "content": "hi"}], tools)
    assert r["tool_prompt"] > 0
    assert r["system_tool_schemas"] > 0
    assert r["mcp_tool_schemas"] == 0


def test_mcp_tools_go_to_mcp_bucket():
    tools = [
        {
            "name": f"{MCP_TOOL_PREFIX}github_search",
            "description": "MCP 工具" * 50,
            "parameters": {"type": "object"},
        },
        {
            "name": "custom",
            "server": "mcp-server",
            "description": "另一 MCP" * 50,
        },
    ]
    r = estimate_context_breakdown([{"role": "user", "content": "hi"}], tools)
    assert r["mcp_tool_schemas"] > 0
    assert r["tool_prompt"] == 0
    assert r["system_tool_schemas"] == 0


def test_segments_sum_is_reasonable():
    msgs = [
        {"role": "system", "content": "规则" * 100},
        {"role": "user", "content": "历史" * 200},
        {"role": "assistant", "content": "回答" * 200},
        {"role": "user", "content": "问题"},
    ]
    tools = [{"name": "t", "description": "desc" * 100, "parameters": {"a": 1}}]
    r = estimate_context_breakdown(msgs, tools, context_items=[_Item("skill", "s" * 100)])
    total = sum(r.values())
    assert total > 0
    assert all(v >= 0 for v in r.values())
