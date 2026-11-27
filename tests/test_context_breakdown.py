# -*- coding: utf-8 -*-
"""H1 · 7 段上下文拆解单测（选项 B：含 meta_user_context / mcp_tool_schemas）。"""
from __future__ import annotations

from agentnodes.kernel.context.breakdown import (
    BREAKDOWN_KEYS,
    MCP_TOOL_PREFIX,
    estimate_context_breakdown,
    estimate_tokens_cjk,
    estimate_tokens_text_cjk,
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


# ── CJK 感知估算（§9 复核修复 3）─────────────────────────────────

def test_cjk_estimator_counts_ideographs_as_one_token_each():
    # 4 个汉字 = 4 token（旧 chars/3 只估 1）
    assert estimate_tokens_text_cjk("你好世界") == 4
    # 纯 ASCII：3 字符 ≈ 1 token
    assert estimate_tokens_text_cjk("abcdefghijkl") == 4


def test_cjk_estimator_handles_mixed_text():
    # "你好"=2 token + "hello"(5 chars≈1.67) + "世界"=2 token ≈ 5
    got = estimate_tokens_text_cjk("你好hello世界")
    assert got == 5, got


def test_public_estimate_tokens_unchanged():
    """公共函数不能被 CJK 改动污染 —— compact/loop 仍用 chars/3。"""
    from agentnodes.foundation.utils.token_utils import estimate_tokens, CHARS_PER_TOKEN

    msgs = [{"role": "user", "content": "你好世界"}]  # 4 字符
    assert estimate_tokens(msgs) == max(1, int(4 / CHARS_PER_TOKEN)) == 1


def test_breakdown_uses_cjk_for_chinese_history():
    """中文历史不再被 chars/3 低估：4 轮中文对话 ≈ 字数级 token。"""
    msgs = [
        {"role": "system", "content": "规则" * 50},
        {"role": "user", "content": "上一轮" * 10},       # 30 汉字 = 30 token
        {"role": "assistant", "content": "回答" * 15},     # 30 汉字 = 30 token
        {"role": "user", "content": "当前问题"},
    ]
    r = estimate_context_breakdown(msgs, [])
    # history = 60 汉字 ≈ 60 token（旧 chars/3 只估 20）
    assert r["messages"] == 60, r["messages"]


def test_breakdown_tools_work_with_cjk_description():
    tools = [{
        "name": "search",
        "description": "搜索网页" * 25,  # 100 汉字 = 100 token
        "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
    }]
    r = estimate_context_breakdown([{"role": "user", "content": "hi"}], tools)
    assert r["tool_prompt"] == 100, r["tool_prompt"]
    assert r["system_tool_schemas"] > 0
